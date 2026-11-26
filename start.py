#!/usr/bin/env python3
"""VAST Test Bench launcher - the same on Windows, macOS and Linux.

    run.bat / run.sh / run.command call this. You can also run it directly:
        python start.py [SITE_FOLDER] [--port N] [--no-browser]

It only uses the Python standard library. It
  1. checks the Python version (3.10 or newer),
  2. creates the virtual environment .venv on the first start,
  3. installs requirements.txt (again only when that file changes),
  4. picks the port: --port, else $PORT, else the site's app.port, else 8080,
  5. if the app already runs on that port, just opens the browser,
  6. starts the app on 127.0.0.1 and opens it in the browser.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import venv
import webbrowser
from pathlib import Path

MIN_PY = (3, 10)
ROOT = Path(__file__).resolve().parent
RUN_DIR = ROOT / ".run"          # app-<port>.json: pid of the running app, so stop can find it
VENV = ROOT / ".venv"
REQS = ROOT / "requirements.txt"
STAMP = VENV / ".requirements.sha256"
RESTART_EXIT_CODE = 75           # app/main.py exits with this to be started again
HOST = "127.0.0.1"   # the app holds credentials and shell access: local only
IS_WIN = os.name == "nt"


def say(msg: str) -> None:
    print(f"[VAST Test Bench] {msg}", flush=True)


def die(msg: str, code: int = 1) -> None:
    print(f"\n[VAST Test Bench] ERROR: {msg}\n", file=sys.stderr, flush=True)
    sys.exit(code)


def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if IS_WIN else "bin/python")


def _has_pip(py: Path) -> bool:
    try:  # also catches a .venv copied from another machine / made by another Python
        return subprocess.run([str(py), "-m", "pip", "--version"], capture_output=True).returncode == 0
    except OSError:
        return False


def ensure_venv() -> Path:
    py = venv_python()
    if py.exists():
        if _has_pip(py):
            return py
        say("The existing .venv does not work on this machine - recreating it")
    say(f"Creating the Python environment in {VENV.name}{os.sep} (first start only) ...")
    try:
        venv.EnvBuilder(with_pip=True, clear=True).create(VENV)
        if _has_pip(py):
            return py
    except (Exception, SystemExit):  # noqa: BLE001 - Debian/Ubuntu's venv exits when python3-venv is missing
        pass
    # No ensurepip (Debian/Ubuntu without the python3-venv package): make the venv without pip and
    # install pip with the official bootstrap script - no sudo needed (internet is needed anyway for the packages).
    say("This Python has no 'ensurepip' (e.g. Ubuntu without python3-venv) - installing pip into .venv instead ...")
    try:
        venv.EnvBuilder(with_pip=False, clear=True).create(VENV)
        getpip = VENV / "get-pip.py"
        with urllib.request.urlopen("https://bootstrap.pypa.io/get-pip.py", timeout=60) as r:
            getpip.write_bytes(r.read())
        subprocess.run([str(py), str(getpip), "-q", "--disable-pip-version-check"], check=True)
        getpip.unlink(missing_ok=True)
        if _has_pip(py):
            return py
        raise RuntimeError("pip is still missing")
    except (Exception, SystemExit) as e:  # noqa: BLE001
        hint = ""
        if sys.platform.startswith("linux"):
            hint = ("\nInstall the venv module, then start again:"
                    "\n  Debian/Ubuntu:   sudo apt install python3-venv"
                    "\n  RHEL/Rocky/Alma: sudo dnf install python3")
        die(f"Could not create the Python environment in {VENV}: {e}{hint}")
    return py


def ensure_requirements(py: Path) -> None:
    want = hashlib.sha256(REQS.read_bytes()).hexdigest()
    if STAMP.exists() and STAMP.read_text().strip() == want:
        return
    say("Installing the required Python packages (first start or requirements.txt changed) ...")
    r = subprocess.run([str(py), "-m", "pip", "install", "--disable-pip-version-check", "-q", "-r", str(REQS)])
    if r.returncode != 0:
        die("Installing the Python packages failed (see the pip output above).\n"
            "Check the internet / proxy connection (for a proxy: set HTTPS_PROXY), then start again.")
    STAMP.write_text(want)


def site_port(py: Path, site: Path | None) -> int | None:
    if not site:
        return None
    code = ("import sys, yaml; c = yaml.safe_load(open(sys.argv[1], encoding='utf-8')) or {}; "
            "print((c.get('app') or {}).get('port', ''))")
    r = subprocess.run([str(py), "-c", code, str(site / "config.yaml")], capture_output=True, text=True)
    return int(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip().isdigit() else None


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((HOST, port)) == 0


def is_our_app(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2) as r:
            return b"VAST Test Bench" in r.read(4096)
    except Exception:  # noqa: BLE001
        return False


def open_when_ready(url: str, proc: subprocess.Popen, no_browser: bool) -> None:
    for _ in range(120):
        if proc.poll() is not None:
            return
        if is_our_app(url):
            say(f"Ready: {url}   (press Ctrl+C in this window to stop)")
            if not no_browser:
                webbrowser.open(url)
            return
        time.sleep(0.5)


def _pid_alive(pid: int) -> bool:
    if IS_WIN:
        r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True)
        return str(pid) in r.stdout
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _end(pid: int) -> None:
    if IS_WIN:   # /T also ends child processes
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
    else:
        import signal
        os.kill(pid, signal.SIGTERM)


def stop(port: int | None) -> None:
    """Stop the app(s) started by this launcher: the one on --port, or all of them."""
    import json
    files = sorted(RUN_DIR.glob(f"app-{port}.json" if port else "app-*.json"))
    if not files:
        say(f"No running VAST Test Bench found{f' on port {port}' if port else ''} (nothing to stop).")
        return
    for f in files:
        try:
            info = json.loads(f.read_text())
        except (OSError, ValueError):
            f.unlink(missing_ok=True)
            continue
        pid, p = int(info["pid"]), int(info["port"])
        url = f"http://{HOST}:{p}"
        if not _pid_alive(pid):
            say(f"Port {p}: not running any more (cleaned up).")
            f.unlink(missing_ok=True)
            continue
        if not is_our_app(url):   # don't end a process that isn't the app (e.g. a re-used pid)
            say(f"Port {p}: pid {pid} is not answering as VAST Test Bench - left alone. Stop it by hand if needed.")
            continue
        say(f"Stopping VAST Test Bench on {url} (pid {pid}) - open consoles are closed ...")
        (RUN_DIR / f"app-{p}.stopping").write_text("")   # tells the run window this was a normal stop
        _end(pid)
        for _ in range(40):
            if not port_in_use(p):
                break
            time.sleep(0.25)
        f.unlink(missing_ok=True)
        say(f"Stopped ({url})." if not port_in_use(p) else f"Port {p} is still in use - check it by hand.")


def main() -> None:
    if sys.version_info < MIN_PY:
        die(f"Python {MIN_PY[0]}.{MIN_PY[1]} or newer is needed (this is {sys.version.split()[0]}).\n"
            "Install it from https://www.python.org/downloads/ and start again.")
    ap = argparse.ArgumentParser(description="Start VAST Test Bench")
    ap.add_argument("site", nargs="?", help="open this test site folder (a folder with config.yaml)")
    ap.add_argument("--port", type=int, help="port (default: $PORT, the site's app.port, or 8080)")
    ap.add_argument("--no-browser", action="store_true", help="do not open the browser")
    ap.add_argument("--stop", action="store_true", help="stop the running app (the one on --port, or all)")
    a = ap.parse_args()
    if a.stop:
        return stop(a.port)

    site = None
    if a.site:
        site = Path(a.site).expanduser().resolve()
        if site.is_file() and site.name == "config.yaml":
            site = site.parent
        if not (site / "config.yaml").is_file():
            die(f"Not a test site folder (no config.yaml): {site}")

    py = ensure_venv()
    ensure_requirements(py)

    env_port = os.environ.get("PORT", "")
    port = a.port or (int(env_port) if env_port.isdigit() else None) or site_port(py, site) or 8080
    url = f"http://{HOST}:{port}"

    if port_in_use(port):
        if is_our_app(url):
            say(f"Already running at {url} - opening it.")
            if not a.no_browser:
                webbrowser.open(url)
            return
        die(f"Port {port} is used by another program. Start on another port, e.g.:  run --port 8081")

    env = dict(os.environ, PYTHONUTF8="1", VAST_LAUNCHER="start.py", VAST_APP_PORT=str(port))
    if site:
        env["VAST_TEST_SITE"] = str(site)
    say(f"Starting on {url}" + (f"  (test site: {site})" if site else ""))
    cmd = [str(py), "-m", "uvicorn", "app.main:app", "--host", HOST, "--port", str(port)]
    proc = subprocess.Popen(cmd, cwd=ROOT, env=env)
    import json
    RUN_DIR.mkdir(exist_ok=True)
    pidfile = RUN_DIR / f"app-{port}.json"
    pidfile.write_text(json.dumps({"pid": proc.pid, "port": port, "site": str(site or ""), "started": time.time()}))
    threading.Thread(target=open_when_ready, args=(url, proc, a.no_browser), daemon=True).start()
    code = 0
    try:
        code = proc.wait()
        while code == RESTART_EXIT_CODE:   # the app asked to be restarted (🔄 Restart app, Windows)
            say("Restarting ...")
            reopen = RUN_DIR / f"app-{port}.site"
            if reopen.exists():
                env["VAST_TEST_SITE"] = reopen.read_text().strip()
                reopen.unlink(missing_ok=True)
                if not env["VAST_TEST_SITE"]:
                    env.pop("VAST_TEST_SITE")
            proc = subprocess.Popen(cmd, cwd=ROOT, env=env)
            pidfile.write_text(json.dumps({"pid": proc.pid, "port": port, "site": env.get("VAST_TEST_SITE", ""),
                                           "started": time.time()}))
            code = proc.wait()
    except KeyboardInterrupt:
        say("Stopping ...")
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
    finally:
        pidfile.unlink(missing_ok=True)
    marker = RUN_DIR / f"app-{port}.stopping"
    if marker.exists():   # ended by the stop script: a normal stop, not an error
        marker.unlink(missing_ok=True)
        say("Stopped.")
        code = 0
    sys.exit(code)


if __name__ == "__main__":
    main()
