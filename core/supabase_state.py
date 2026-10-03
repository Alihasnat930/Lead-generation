"""Consistent SQLite snapshots and database-enforced exclusive workspace leases."""
from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
from pathlib import Path
import uuid
import sqlite3
import tempfile
from contextlib import closing
import hashlib
import zlib

from .supabase_client import get_client


def _now():
    return datetime.now(timezone.utc)


def _parse_ts(value):
    if not value:
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


class SupabaseState:
    def __init__(self):
        self.client = get_client()
        self._digests = {}

    def acquire_lease(self, name, ttl_seconds):
        owner = uuid.uuid4().hex
        result = self.client.rpc('acquire_workspace_lease', {
            'lease_name':name,'lease_owner':owner,'ttl_seconds':int(ttl_seconds)}).execute()
        return owner if result.data is True else None

    def renew_lease(self, name, owner, ttl_seconds=120):
        result = self.client.rpc('renew_workspace_lease', {
            'lease_name':name,'lease_owner':owner,'ttl_seconds':int(ttl_seconds)}).execute()
        if result.data is not True:
            raise ValueError('Cloud workspace lease was lost. Restart the app to reload current data.')

    def release_lease(self, name, owner):
        if not owner:
            return
        (
            self.client.table("job_leases")
            .delete()
            .eq("name", name)
            .eq("owner", owner)
            .execute()
        )

    def download_file(self, state_key, destination):
        record = (
            self.client.table("cloud_state")
            .select("payload_base64")
            .eq("name", state_key)
            .limit(1)
            .execute()
        )
        row = (record.data or [None])[0]
        if not row:
            return False
        payload = row.get("payload_base64") or ""
        if not payload:
            return False
        compressed = payload.startswith('zlib:')
        raw = base64.b64decode((payload[5:] if compressed else payload).encode("ascii"), validate=True)
        if compressed:
            raw = zlib.decompress(raw)
        if not raw.startswith(b'SQLite format 3\x00'):
            raise ValueError('Cloud snapshot is not a valid SQLite database.')
        path = Path(destination)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Called only before any connection/worker starts under the workspace lease.
        temporary = path.with_suffix('.restore.tmp')
        temporary.write_bytes(raw)
        try:
            with closing(sqlite3.connect(temporary)) as db:
                if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                    raise ValueError('Cloud snapshot integrity check failed.')
            for suffix in ('-wal','-shm'):
                sidecar = Path(str(path)+suffix)
                sidecar.unlink(missing_ok=True)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
        return True

    def upload_file(self, state_key, source, *, lease_name, owner):
        path = Path(source)
        if not path.exists():
            return
        # sqlite3.backup includes committed WAL pages while writers remain active.
        with tempfile.TemporaryDirectory() as temp:
            backup_path = Path(temp)/'checkpoint.db'
            with closing(sqlite3.connect(path)) as src, closing(sqlite3.connect(backup_path)) as dst:
                src.backup(dst)
            raw = backup_path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if self._digests.get(state_key) == digest:
            return
        payload = 'zlib:'+base64.b64encode(zlib.compress(raw,level=6)).decode('ascii')
        result = self.client.rpc('save_workspace_snapshot', {
            'lease_name':lease_name,'lease_owner':owner,'state_name':state_key,
            'payload':payload,'byte_count':len(raw)}).execute()
        if result.data is not True:
            raise ValueError('Cloud checkpoint rejected: this process no longer owns the workspace.')
        self._digests[state_key] = digest
