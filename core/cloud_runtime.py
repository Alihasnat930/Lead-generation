"""One active hosted workspace; fenced snapshots and fail-closed email checkpoints.

The dashboard and optional Actions runners share a single lease. Discovery progress
is copied every 30 seconds; outreach mutations are copied synchronously. If storage
or the lease fails, this process stops accepting work until it is restarted.
"""
import threading
import time
from .config import config

_runtime = None
_start_lock = threading.Lock()


class CloudRuntime:
    def __init__(self, state=None):
        from .supabase_state import SupabaseState
        self.state = state or SupabaseState()
        self.lease_name = 'prospect-workspace'
        self.owner = None
        self.deadline = 0
        self.failure = ''
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.paths = {'campaign_store.db':config.DATABASE_PATH,
                      'outreach_store.db':config.OUTREACH_DATABASE_PATH}

    def start(self):
        self.owner = self.state.acquire_lease(self.lease_name,120)
        if not self.owner:
            raise ValueError('Another cloud instance owns this workspace. Wait two minutes after stopping it, then retry.')
        try:
            for key,path in self.paths.items():
                self.state.download_file(key,path)
            self.deadline = time.monotonic()+90
        except Exception:
            self.state.release_lease(self.lease_name,self.owner)
            raise

    def guard(self):
        if self.failure or time.monotonic() >= self.deadline:
            raise ValueError('Cloud storage connection was lost. Restart the app to restore the latest checkpoint before continuing.')

    def checkpoint(self, path=None):
        with self.lock:
            self.guard()
            try:
                self.state.renew_lease(self.lease_name,self.owner,120)
                self.deadline = time.monotonic()+90
                for key,source in self.paths.items():
                    if path is None or str(path) == str(source):
                        self.state.upload_file(key,source,lease_name=self.lease_name,owner=self.owner)
            except Exception as exc:
                self.failure = type(exc).__name__
                raise ValueError('Cloud checkpoint failed. No further email can be sent until storage is restored.') from None

    def maintain(self):
        while not self.stop.wait(30):
            try:
                self.checkpoint()
            except Exception:
                return

    def close(self):
        self.stop.set()
        with self.lock:
            try:
                if not self.failure:
                    self.checkpoint()
            finally:
                self.state.release_lease(self.lease_name,self.owner)


def start_cloud_runtime():
    global _runtime
    if not config.CLOUD_MODE:
        return None
    with _start_lock:
        if _runtime is None:
            runtime = CloudRuntime()
            runtime.start()
            _runtime = runtime
            threading.Thread(target=runtime.maintain,daemon=True,name='cloud-checkpoints').start()
        _runtime.guard()
        return _runtime


def guard():
    if config.CLOUD_MODE:
        if _runtime is None:
            raise ValueError('Cloud workspace has not been initialized.')
        _runtime.guard()


def checkpoint_outreach(path=None):
    guard()
    if _runtime is not None:
        _runtime.checkpoint(path or config.OUTREACH_DATABASE_PATH)


def checkpoint_campaign():
    guard()
    if _runtime is not None:
        _runtime.checkpoint(config.DATABASE_PATH)
