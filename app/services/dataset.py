"""Read-only versioned dataset loader. No database, no mutation, no oracle leakage."""
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from zipfile import ZipFile, BadZipFile
from collections import defaultdict
from app.core.settings import DATASET_PATH


class DataProblem(Exception):
    pass


@dataclass(frozen=True)
class UserData:
    user_id: str
    profile: dict
    accounts: tuple[dict, ...]
    transactions: tuple[dict, ...]
    schedules: tuple[dict, ...]
    balance_checks: tuple[dict, ...]
    daily: tuple[dict, ...]
    pending_card_liabilities: tuple[dict, ...]
    investments: tuple[dict, ...]
    portfolio_snapshots: tuple[dict, ...]
    purchase_intents: tuple[dict, ...]


class Dataset:
    def __init__(self, source: Path):
        if not source.is_file():
            raise DataProblem(f'dataset_missing: {source} (set MOA_DATASET_PATH)')
        try:
            if source.suffix.lower() == '.zip':
                with ZipFile(source) as z:
                    names = [name for name in z.namelist() if name.endswith('.json') and 'manifest' not in name.lower()]
                    if len(names) != 1:
                        raise DataProblem('dataset_zip_requires_single_financial_json')
                    self.raw = json.loads(z.read(names[0]))
            else:
                self.raw = json.loads(source.read_text(encoding='utf-8'))
        except (BadZipFile, OSError, ValueError, UnicodeError) as exc:
            raise DataProblem(f'dataset_unreadable: {exc}') from exc
        self.meta = self.raw.get('metadata', {})
        if not self.meta.get('synthetic') or not self.meta.get('snapshot_date'):
            raise DataProblem('dataset_must_declare_synthetic_and_snapshot_date')
        profiles = self.raw.get('profiles', [])
        ids = [p.get('user_id') for p in profiles]
        if len(ids) != len(set(ids)) or not ids:
            raise DataProblem('duplicate_or_missing_user_ids')
        self.profiles = {p['user_id']: p for p in profiles}
        self.by_user = {}
        collections = {
            'accounts': 'accounts', 'transactions': 'transactions', 'schedules': 'schedules',
            'balance_checks': 'balance_checks', 'daily': 'daily',
            'pending_card_liabilities': 'pending_card_liabilities', 'investments': 'investments',
            'portfolio_snapshots': 'virtual_portfolio_snapshots', 'purchase_intents': 'purchase_intent_screenshots',
        }
        partitions = {}
        for attr, key in collections.items():
            grouped = defaultdict(list)
            seen = set()
            for row in self.raw.get(key, []):
                user_id = row.get('user_id')
                if user_id not in self.profiles:
                    raise DataProblem(f'orphan_user_in_{key}: {user_id}')
                rid = row.get('id')
                if rid is not None:
                    if rid in seen:
                        raise DataProblem(f'duplicate_id_in_{key}: {rid}')
                    seen.add(rid)
                grouped[user_id].append(row)
            partitions[attr] = grouped
        for user_id, profile in self.profiles.items():
            payload = {k: tuple(partitions[k][user_id]) for k in collections}
            self.by_user[user_id] = UserData(user_id, profile, **payload)

    def get_user(self, user_id: str):
        if user_id not in self.by_user:
            raise KeyError(user_id)
        return self.by_user[user_id]


@lru_cache(maxsize=4)
def _load_cached(path: str, mtime_ns: int, size: int):
    return Dataset(Path(path))


def load_dataset(path: Path | None = None) -> Dataset:
    source = (path or DATASET_PATH).resolve()
    if not source.is_file():
        raise DataProblem(f'dataset_missing: {source}')
    st = source.stat()
    return _load_cached(str(source), st.st_mtime_ns, st.st_size)