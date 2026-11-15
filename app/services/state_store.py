"""Durable JSON documents: transactional SQLite locally, private Blob CAS on Vercel.

No synthetic source data is modified. Private blobs are never exposed as public URLs.
Pure mutations may be retried; network/model/Discord side effects must run outside them.
"""
from __future__ import annotations
import copy
import json
import os
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4
from urllib.parse import quote
import httpx
from app.services.stage2_store import runtime_dir

class StateConflict(RuntimeError):
    pass

class StateUnavailable(RuntimeError):
    pass

def storage_kind():
    if os.getenv('BLOB_READ_WRITE_TOKEN') or os.getenv('BLOB_STORE_ID'):
        return 'private_blob'
    return 'sqlite'

def _key(key):
    if not re.fullmatch(r'[A-Za-z0-9_./-]{1,190}', key) or '..' in key:
        raise ValueError('invalid_state_key')
    return 'moa-learning-v1/' + key

_SQLITE_INIT = threading.RLock()

@contextmanager
def _db():
    root = runtime_dir()
    root.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(root / 'learning-state.sqlite3', timeout=15)
    try:
        # The home loads preferences, history and quiz concurrently on a fresh DB.
        # Serialize schema/WAL initialization and always close connections, including
        # failed compare-and-swap transactions, rather than waiting for Python GC.
        with _SQLITE_INIT:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('CREATE TABLE IF NOT EXISTS documents (key TEXT PRIMARY KEY, value TEXT NOT NULL, version INTEGER NOT NULL)')
        with db:
            yield db
    finally:
        db.close()

def _blob_auth():
    token = os.getenv('BLOB_READ_WRITE_TOKEN', '')
    store = os.getenv('BLOB_STORE_ID', '').removeprefix('store_')
    if token:
        store = token.removeprefix('vercel_blob_rw_').split('_', 1)[0]
    else:
        from vercel.oidc import get_vercel_oidc_token_sync
        token = get_vercel_oidc_token_sync()
    if not token or not store:
        raise StateUnavailable('storage_credentials_missing')
    return token, store

def read(key):
    name = _key(key)
    if storage_kind() == 'sqlite':
        if os.getenv('VERCEL'):
            raise StateUnavailable('durable_storage_required')
        with _db() as db:
            row = db.execute('SELECT value,version FROM documents WHERE key=?', (name,)).fetchone()
        return (json.loads(row[0]), str(row[1])) if row else ({}, None)
    token, store = _blob_auth()
    url = f'https://{store.lower()}.private.blob.vercel-storage.com/{quote(name, safe="/")}'
    try:
        r = httpx.get(url, params={'cache': '0'}, headers={'Authorization': 'Bearer '+token, 'Cache-Control': 'no-cache', 'Accept-Encoding': 'identity'}, timeout=12)
        if r.status_code == 404:
            return {}, None
        r.raise_for_status()
        return r.json(), r.headers.get('etag')
    except (httpx.HTTPError, ValueError) as exc:
        raise StateUnavailable('storage_read_failed') from exc

def write(key, value, expected):
    name = _key(key)
    raw = json.dumps(value, ensure_ascii=False, separators=(',', ':'))
    if storage_kind() == 'sqlite':
        if os.getenv('VERCEL'):
            raise StateUnavailable('durable_storage_required')
        with _db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT version FROM documents WHERE key=?', (name,)).fetchone()
            actual = str(row[0]) if row else None
            if actual != expected:
                raise StateConflict('state_changed')
            version = (row[0] if row else 0) + 1
            db.execute('INSERT INTO documents VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,version=excluded.version', (name, raw, version))
        return str(version)
    token, store = _blob_auth()
    headers = {'Authorization': 'Bearer '+token, 'x-api-version': '12', 'x-vercel-blob-access': 'private',
               'x-vercel-blob-store-id': store, 'x-add-random-suffix': '0',
               'x-allow-overwrite': '1' if expected else '0', 'x-content-type': 'application/json', 'x-cache-control-max-age': '0'}
    if expected:
        headers['x-if-match'] = expected
    try:
        r = httpx.put('https://vercel.com/api/blob', params={'pathname': name}, headers=headers, content=raw.encode(), timeout=15)
        if r.status_code in (409, 412):
            __import__('logging').getLogger(__name__).warning('learning_store_cas_conflict status=%s weak_etag=%s',r.status_code,str(expected).startswith('W/'))
            raise StateConflict('state_changed')
        r.raise_for_status()
        return r.json().get('etag')
    except (httpx.HTTPError, ValueError) as exc:
        raise StateUnavailable('storage_write_failed') from exc

def mutate(key, fn):
    for attempt in range(5):
        document, version = read(key)
        changed = copy.deepcopy(document)
        result = fn(changed)
        if changed == document:
            return result
        try:
            write(key, changed, version)
            return result
        except StateConflict:
            time.sleep(0.05 * (2 ** attempt))
            continue
    raise StateConflict('state_busy_retry')

def get(key):
    return read(key)[0]

def put(key, value):
    return mutate(key, lambda doc: (doc.clear(), doc.update(value), value)[-1])
