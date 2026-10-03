"""One-command local startup; only standard-library imports before dependency setup."""
import argparse
from datetime import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
LOGS = DATA / "logs"
RUNTIME = DATA / "runtime"
STATE = RUNTIME / "server.json"
MODULES = ("streamlit", "gspread", "google.auth", "requests", "dotenv", "pandas",
           "bs4", "tldextract", "ddgs")


def say(message):
    print(message, flush=True)


class InstanceLock:
    """The OS releases this lock even if the launcher crashes."""
    def __enter__(self):
        self.file = (RUNTIME / "launcher.lock").open("a+b")
        self.file.seek(0, 2)
        if self.file.tell() == 0:
            self.file.write(b"0")
            self.file.flush()
        self.file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            return False
        return True

    def __exit__(self, *_):
        self.file.close()


def healthy(url):
    try:
        with urlopen(url + "/_stcore/health", timeout=2) as response:
            return response.status == 200 and response.read(32).strip() == b"ok"
    except (OSError, URLError):
        return False


def reuse_existing(no_browser):
    try:
        state = json.loads(STATE.read_text(encoding="utf-8"))
        port = int(state["port"])
        url = f"http://127.0.0.1:{port}"
        if not 1 <= port <= 65535 or not healthy(url):
            raise ValueError("Server is still starting")
    except (OSError, ValueError, KeyError):
        say("Another launcher is already starting Prospect Studio. Keep its window open.")
        return 0
    say(f"Prospect Studio is already running: {url}")
    if not no_browser:
        webbrowser.open(url)
    return 0


def ensure_dependencies(repair=False):
    requirements = ROOT / "requirements.txt"
    fingerprint = hashlib.sha256(requirements.read_bytes() + sys.version.encode()
                                 + sys.executable.encode()).hexdigest()
    stamp = Path(sys.prefix) / ".prospect-studio-requirements.sha256"
    try:
        ready = all(importlib.util.find_spec(name) is not None for name in MODULES)
        ready = ready and stamp.read_text(encoding="utf-8").strip() == fingerprint
    except (OSError, ModuleNotFoundError):
        ready = False
    if not ready or repair:
        say("Checking/installing Python packages (internet is needed for missing packages)...")
        command = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
                   "-r", str(requirements)]
        if repair:
            command.append("--force-reinstall")
        subprocess.run(command, cwd=ROOT, check=True)
        subprocess.run([sys.executable, "-m", "pip", "check"], cwd=ROOT, check=True)
        stamp.write_text(fingerprint, encoding="utf-8")
    # Import the actual app services, including optional CRM dependencies, without sending anything.
    subprocess.run([sys.executable, "-c",
                    "from core.campaigns import launch; from core.sheets import append_new_qualified; "
                    "from core.store import Store; Store(); print('Application and database checks passed.')"],
                   cwd=ROOT, check=True)


def serve(port, no_browser):
    url = f"http://127.0.0.1:{port}"
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            raise RuntimeError(f"Port {port} is occupied. Close the old server or run "
                               f"run_app.bat --port {port + 1}.") from None
    log_path = LOGS / "server.log"
    say(f"Starting dashboard: {url}")
    say(f"Server log: {log_path}")
    process = None
    try:
        with log_path.open("a", encoding="utf-8") as log:
            log.write(f"\n--- Launcher started {datetime.now().isoformat(timespec='seconds')} ---\n")
            log.flush()
            process = subprocess.Popen(
                [sys.executable, "-m", "streamlit", "run", str(ROOT / "app.py"),
                 "--server.address=127.0.0.1", f"--server.port={port}", "--server.headless=true"],
                cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0)
            STATE.write_text(json.dumps({"launcher_pid": os.getpid(), "server_pid": process.pid,
                                         "port": port}), encoding="utf-8")
            deadline = time.monotonic() + 60
            while not healthy(url):
                if process.poll() is not None:
                    raise RuntimeError(f"Dashboard exited during startup. Read {log_path}.")
                if time.monotonic() >= deadline:
                    raise RuntimeError(f"Dashboard did not become ready within 60 seconds. Read {log_path}.")
                time.sleep(0.3)
            say("Ready. Campaigns start their own background workers from the dashboard.")
            subprocess.Popen([sys.executable, str(ROOT / 'outreach_worker.py')], cwd=ROOT,
                             stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                             creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0) if os.name=='nt' else 0,
                             start_new_session=os.name!='nt')
            say('Sync service started. Configure automatic email and WhatsApp sending on the Schedule page.')
            say("Keep this window open. Ctrl+C stops the dashboard; pause campaigns in the app first if needed.")
            if not no_browser:
                webbrowser.open(url)
            return process.wait()
    except KeyboardInterrupt:
        say("Stopping the dashboard. Campaign workers keep their saved progress.")
        return 0
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        STATE.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description="Set up and launch Prospect Studio locally.")
    parser.add_argument("--check", action="store_true", help="Install/check dependencies and database, then exit.")
    parser.add_argument("--repair", action="store_true", help="Reinstall requirements before starting.")
    parser.add_argument("--no-browser", action="store_true", help="Do not open a browser tab.")
    parser.add_argument("--port", type=int, default=8501, help="Local dashboard port (default: 8501).")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    if sys.version_info < (3, 11):
        raise RuntimeError("Python 3.11 or newer is required.")
    if sys.prefix == sys.base_prefix:
        raise RuntimeError("Use run_app.bat to create and use the project's .venv environment.")
    os.chdir(ROOT)
    for directory in (LOGS, RUNTIME, DATA / "samples", ROOT / "credentials"):
        directory.mkdir(parents=True, exist_ok=True)
    with InstanceLock() as acquired:
        if not acquired:
            if args.check or args.repair:
                raise RuntimeError("Another launcher is active. Stop it before running --check or --repair.")
            return reuse_existing(args.no_browser)
        ensure_dependencies(args.repair)
        if args.check:
            say("Setup is ready. Run run_app.bat to open Prospect Studio.")
            return 0
        return serve(args.port, args.no_browser)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        say(f"Startup error: {exc}")
        raise SystemExit(1)
