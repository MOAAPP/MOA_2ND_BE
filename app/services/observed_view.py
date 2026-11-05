"""Read-only per-request view of the canonical synthetic dataset plus confirmed local SMS.

The source ZIP is immutable. A day beyond its observation snapshot can include
only user-confirmed post-snapshot items, never future_scenario/oracle rows.
An incomplete day MUST NOT be marked observation_complete for ML.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.services.dataset import Dataset
from app.services.stage2_store import read_user

KST = ZoneInfo("Asia/Seoul")

def for_user_date(ds: Dataset, user_id: str, as_of: date | None) -> Dataset:
    cutoff = date.fromisoformat(ds.meta["snapshot_date"])
    day = as_of or cutoff
    if day <= cutoff:
        return ds
    if user_id not in ds.profiles:
        raise KeyError(user_id)
    if day > datetime.now(KST).date():
        raise ValueError("future_day_cannot_be_treated_as_observed")
    if (day - cutoff).days > 365:
        raise ValueError("post_snapshot_window_exceeded")

    base = ds.get_user(user_id)
    already = {t["id"] for t in base.transactions}
    extra: list[dict] = []
    for item in read_user(user_id)["transactions"]:
        row = item.get("finance_row")
        raw_date = item.get("effective_date")
        if not isinstance(row, dict) or not isinstance(raw_date, str):
            continue
        try:
            effective = date.fromisoformat(raw_date)
        except ValueError:
            continue
        if not cutoff < effective <= day or row.get("id") in already:
            continue
        # Past source snapshots already include earlier transfers; never replay.
        if row.get("status") != "confirmed_user" or row.get("user_id") != user_id:
            continue
        entry = {**row, "date": raw_date, "source": "confirmed_user_local"}
        extra.append(entry)
        already.add(entry["id"])

    view = object.__new__(Dataset)
    view.raw = ds.raw
    view.meta = {**ds.meta, "snapshot_date": day.isoformat(),
                 "canonical_snapshot_date": cutoff.isoformat(),
                 "post_snapshot_incomplete": True}
    view.profiles = ds.profiles
    view.by_user = {**ds.by_user,
                    user_id: replace(base, transactions=base.transactions + tuple(extra))}
    return view
