"""SSH sessions (browser consoles) and the preset command runner."""
from __future__ import annotations

import asyncio
import re
from collections import deque
import shlex
import time
import uuid
from typing import Any, Callable

import asyncssh
from fastapi import WebSocket

from . import elbencho, packages
from .config import TemplateError, render, render_section
from .vms import now_iso

CYAN, GREEN, RED, YELLOW, MAGENTA, DIM, RESET = (
    "\x1b[1;36m", "\x1b[1;32m", "\x1b[1;31m", "\x1b[33m", "\x1b[1;35m", "\x1b[2m", "\x1b[0m")
BOLD = "\x1b[1m"
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07")
DEFAULT_PROMPT_RE = r"(\?\s*$|:\s*$|\[[yYnN]/[yYnN]\]|\([yYnN]/[yYnN]\)|password|passphrase|press (any key|enter)|continue)"

# Handshakes are done one at a time: simultaneous asyncssh handshakes were seen to stall
# (a connect takes ~0.25s, so this is cheap). Sessions and commands still run in parallel.
_CONNECT_LOCK = asyncio.Lock()
# Output kept per console on the server, replayed when a browser page (re)attaches
CONSOLE_BUFFER_CHARS = 2 * 1024 * 1024


def crlf(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\n", "\r\n")


class _ClientHooks(asyncssh.SSHClient):
    """Tells the session when the SSH connection drops, so the UI can show it and offer Reconnect."""

    def __init__(self, session: "Session"):
        self.session = session
        self.conn = None

    def connection_made(self, conn) -> None:
        self.conn = conn

    def connection_lost(self, exc: Exception | None) -> None:
        # ignore the old connection going away after a Reconnect has replaced it
        if self.conn is not None and self.session.conn is not None and self.session.conn is not self.conn:
            return
        asyncio.get_event_loop().create_task(self.session.on_lost(exc))


class Session:
    def __init__(self, client: dict, ssh_cfg: dict):
        self.id = uuid.uuid4().hex[:12]
        self.client = client
        self.ssh_cfg = ssh_cfg
        self.conn: asyncssh.SSHClientConnection | None = None
        self.shell: asyncssh.SSHClientProcess | None = None
        self.active_proc: asyncssh.SSHClientProcess | None = None  # preset command currently running
        # A session outlives browser pages: any number of pages can watch it (viewers), and closing or
        # reloading a page only detaches it. The shell is read all the time and recent output is kept.
        self.viewers: set[WebSocket] = set()
        self.buffer: deque[str] = deque()
        self.buffer_len = 0
        self.reader_task: asyncio.Task | None = None
        self._conn_lock = asyncio.Lock()
        self.status = "new"          # new | connecting | connected | disconnected | error | closed
        self.busy = False
        self.waiting: str | None = None
        self.term_size = (120, 32)
        self.sudo_nopasswd: bool | None = None
        self.selected = True         # ticked for preset runs (restored after a page reload)
        self.origin: str | None = None  # name of the saved client it was opened from, if any
        self.last_seen = time.monotonic()  # last time a page was attached (for the idle auto-close)

    @property
    def label(self) -> str:
        return f"{self.client['name']} ({self.client['host']})"

    @property
    def use_sudo(self) -> bool:
        mode = self.client.get("use_sudo", self.ssh_cfg.get("use_sudo", "auto"))
        if mode == "auto":
            return self.client["username"] != "root"
        return bool(mode)

    def info(self) -> dict:
        return {"id": self.id, "name": self.client["name"], "host": self.client["host"],
                "username": self.client["username"], "status": self.status, "busy": self.busy,
                "waiting": self.waiting, "selected": self.selected, "origin": self.origin,
                "viewers": len(self.viewers)}

    @property
    def ui_status(self) -> str:
        return {"new": "connecting", "connecting": "connecting"}.get(self.status, self.status)

    async def connect(self) -> None:
        """Connect with retries: handshakes occasionally stall (seen with asyncssh, and common over VPNs)."""
        self.status = "connecting"
        attempts = 1 + int(self.ssh_cfg.get("connect_retries", 2))
        for attempt in range(1, attempts + 1):
            try:
                async with _CONNECT_LOCK:
                    await self._connect_once()
                break
            except (asyncio.TimeoutError, OSError) as e:
                if attempt == attempts or isinstance(e, ConnectionRefusedError):
                    raise
                await self.emit(f"{YELLOW}… {type(e).__name__} on attempt {attempt}/{attempts}, retrying{RESET}\r\n")
        self.status = "connected"

    async def _connect_once(self) -> None:
        self.conn = await asyncio.wait_for(
            asyncssh.connect(
                self.client["host"],
                port=int(self.client.get("port", 22)),
                username=self.client["username"],
                password=self.client.get("password") or None,
                known_hosts=None,  # lab clients: host keys are not verified
                client_factory=lambda: _ClientHooks(self),
                keepalive_interval=30,  # notice dead links (VPN drops) instead of hanging
                # with a password: use only the password; without one: ~/.ssh keys / agent
                **({"client_keys": (), "agent_path": None} if self.client.get("password") else {}),
            ),
            timeout=float(self.ssh_cfg.get("connect_timeout_seconds", 15)),
        )
        self.status = "connected"

    async def on_lost(self, exc: Exception | None) -> None:
        if self.status in ("closed", "disconnected"):  # closed by us
            return
        self.status = "error"
        self.shell = self.conn = None
        reason = f"{type(exc).__name__}: {exc}" if exc else "closed by the remote host"
        await self.emit(f"\r\n{RED}✖ SSH connection lost ({reason}). Use ↻ Reconnect.{RESET}\r\n")
        await self.send({"type": "status", "status": "error", "error": reason})

    async def start_shell(self, cols: int, rows: int) -> None:
        assert self.conn
        self.term_size = (cols, rows)
        self.shell = await self.conn.create_process(
            term_type="xterm-256color", term_size=(cols, rows), encoding="utf-8", errors="replace")

    async def send(self, msg: dict) -> None:
        for ws in list(self.viewers):
            try:
                await ws.send_json(msg)
            except Exception:  # that page is gone
                self.viewers.discard(ws)

    def _remember(self, text: str) -> None:
        self.buffer.append(text)
        self.buffer_len += len(text)
        while self.buffer_len > CONSOLE_BUFFER_CHARS and len(self.buffer) > 1:
            self.buffer_len -= len(self.buffer.popleft())

    def replay(self) -> str:
        return "".join(self.buffer)

    async def emit(self, text: str) -> None:
        self._remember(text)
        await self.send({"type": "output", "data": text})

    async def ensure_connected(self) -> None:
        """Connect and start the interactive shell, unless that is already done (safe to call twice)."""
        async with self._conn_lock:
            if self.status == "connected" and self.shell is not None:
                return
            if self.status != "new":
                await self.emit(f"\r\n{DIM}── reconnecting {time.strftime('%H:%M:%S')} ──{RESET}\r\n")
            await self.emit(f"{DIM}Connecting to {self.client['username']}@{self.client['host']} ...{RESET}\r\n")
            await self.send({"type": "status", "status": "connecting"})
            try:
                await self.connect()
                await self.start_shell(*self.term_size)
            except Exception as e:  # noqa: BLE001 - show any connection error in the console
                self.status = "error"
                self.shell = self.conn = None
                await self.emit(f"{RED}SSH connection failed: {type(e).__name__}: {e}{RESET}\r\n")
                await self.send({"type": "status", "status": "error", "error": str(e)})
                return
            await self.send({"type": "status", "status": "connected"})
            self.reader_task = asyncio.create_task(self._read_shell(self.shell))

    async def _read_shell(self, shell) -> None:
        """Pump the interactive shell's output into the buffer and to every attached page."""
        try:
            while True:
                data = await shell.stdout.read(4096)
                if not data:
                    break
                await self.emit(data)
        except asyncio.CancelledError:
            return
        except Exception:  # noqa: BLE001
            pass
        if self.shell is shell and self.status == "connected":  # the remote shell ended (e.g. "exit")
            self.status = "disconnected"
            await self.emit(f"\r\n{DIM}── shell ended · ↻ Reconnect to open a new one ──{RESET}\r\n")
            await self.send({"type": "status", "status": "disconnected"})

    async def disconnect(self) -> None:
        """Close the SSH connection but keep the console (output stays, Reconnect reopens it)."""
        async with self._conn_lock:
            if self.status in ("disconnected", "closed"):
                return
            self.status = "disconnected"
            await self._drop_connection()
            await self.emit(f"\r\n{DIM}── disconnected {time.strftime('%H:%M:%S')} · output kept · ↻ Reconnect to continue ──{RESET}\r\n")
            await self.send({"type": "status", "status": "disconnected"})
            if self.waiting:
                await self.set_waiting(None)

    async def _drop_connection(self) -> None:
        if self.reader_task:
            self.reader_task.cancel()
            self.reader_task = None
        shell, conn = self.shell, self.conn
        self.shell = self.conn = None
        if shell:
            shell.close()
        if conn:
            conn.close()
            try:
                await asyncio.wait_for(conn.wait_closed(), 5)
            except Exception:
                pass

    async def set_waiting(self, prompt: str | None) -> None:
        self.waiting = prompt
        await self.send({"type": "waiting", "prompt": prompt})

    def mask(self, text: str) -> str:
        pw = self.client.get("password") or ""
        return text.replace(pw, "********") if len(pw) >= 3 else text

    async def close(self) -> None:
        self.status = "closed"
        await self._drop_connection()
        await self.send({"type": "removed"})  # pages showing this console remove it
        for ws in list(self.viewers):
            try:
                await ws.close()
            except Exception:
                pass
        self.viewers.clear()


class SessionManager:
    def __init__(self) -> None:
        self.sessions: dict[str, Session] = {}
        self.runs: dict[str, dict] = {}
        self.pkg_status: dict[str, dict] = {}  # session id -> last package check

    def create(self, client: dict, ssh_cfg: dict) -> Session:
        s = Session(client, ssh_cfg)
        self.sessions[s.id] = s
        return s

    async def remove(self, sid: str) -> None:
        s = self.sessions.pop(sid, None)
        self.pkg_status.pop(sid, None)
        if s:
            await s.close()

    async def attach(self, s: Session, ws: WebSocket) -> None:
        """Show a session in one browser page until that page goes away. The session itself stays:
        reloading or closing the page only detaches it (see reap_idle for the auto-close)."""
        params = ws.query_params
        if s.status == "new":  # the first page sets the initial terminal size
            s.term_size = (int(params.get("cols", 120)), int(params.get("rows", 32)))
        s.viewers.add(ws)
        s.last_seen = time.monotonic()
        try:
            await ws.send_json({"type": "replay", "data": s.replay()})
            await ws.send_json({"type": "status", "status": s.ui_status})
            if s.waiting:
                await ws.send_json({"type": "waiting", "prompt": s.waiting})
            if s.status == "new":
                asyncio.create_task(s.ensure_connected())
            while True:
                msg = await ws.receive_json()
                if msg.get("type") == "input":
                    data = msg.get("data", "")
                    # while a preset command runs, keystrokes answer that command
                    target = s.active_proc or s.shell
                    if target:
                        try:
                            target.stdin.write(data)
                        except (BrokenPipeError, OSError):
                            pass
                    if s.active_proc and s.waiting:
                        await s.set_waiting(None)
                elif msg.get("type") == "resize":
                    s.term_size = (int(msg["cols"]), int(msg["rows"]))
                    for p in (s.shell, s.active_proc):
                        if p:
                            try:
                                p.change_terminal_size(*s.term_size)
                            except Exception:
                                pass
        except Exception:
            pass
        finally:
            s.viewers.discard(ws)
            s.last_seen = time.monotonic()

    async def reap_idle(self, idle_seconds: float) -> list[str]:
        """Close sessions that no page has shown for idle_seconds and that are not running anything."""
        if idle_seconds <= 0:
            return []
        now, gone = time.monotonic(), []
        for s in list(self.sessions.values()):
            if not s.viewers and not s.busy and now - s.last_seen > idle_seconds:
                gone.append(s.label)
                await self.remove(s.id)
        return gone

    # ------------------------------------------------------------------ preset runs

    def start_run(self, cfg: dict, group: dict, session_ids: list[str],
                  ctx_for: Callable[[Session, int], dict], vars_: dict) -> dict:
        """group: {id, label, commands | per_session, results?, sudo?, parse?}
        ctx_for(session, index) returns the template context for that client."""
        sessions = [self.sessions[i] for i in session_ids if i in self.sessions]
        sessions = [s for s in sessions if s.status == "connected"]
        if not sessions:
            raise ValueError("No connected consoles selected")
        busy = [s.label for s in sessions if s.busy]
        if busy:
            raise ValueError(f"Console busy: {', '.join(busy)}")
        ex = cfg["execution"]
        run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + group["id"]
        run = {
            "run_id": run_id,
            "group": group["id"],
            "label": group.get("label", group["id"]),
            "status": "running",
            "started_at": now_iso(),
            "finished_at": None,
            "vars": vars_,
            "settings": {
                "delay_between_commands_seconds": group.get(
                    "delay_between_commands_seconds", ex.get("delay_between_commands_seconds", 0)),
                "command_timeout_seconds": ex.get("command_timeout_seconds", 3600),
                "stop_on_error": ex.get("stop_on_error", True),
                "parallel_clients": ex.get("parallel_clients", True),
                "use_pty": ex.get("use_pty", True),
                "input_prompt_regex": ex.get("input_prompt_regex") or DEFAULT_PROMPT_RE,
                "input_idle_seconds": float(ex.get("input_idle_seconds", 3)),
            },
            "waiting": {},
            "clients": [],
        }
        self.runs[run_id] = run
        for s in sessions:
            s.busy = True
        ctxs = {s.id: ctx_for(s, i) for i, s in enumerate(sessions)}
        asyncio.create_task(self._run(run, group, sessions, ctxs))
        return run

    async def _run(self, run: dict, group: dict, sessions: list[Session], ctxs: dict) -> None:
        from .results import export_run  # avoid import cycle

        try:
            if run["settings"]["parallel_clients"]:
                run["clients"] = list(await asyncio.gather(
                    *(self._safe_client(run, group, s, ctxs[s.id]) for s in sessions)))
            else:
                for s in sessions:
                    run["clients"].append(await self._safe_client(run, group, s, ctxs[s.id]))
            if group.get("results"):
                run["summary"] = elbencho.aggregate(run["clients"])
            run["status"] = "done" if all(c["status"] == "ok" for c in run["clients"]) else "failed"
        except Exception as e:  # noqa: BLE001
            run["status"] = "failed"
            run["error"] = f"{type(e).__name__}: {e}"
        finally:
            run["finished_at"] = now_iso()
            run["waiting"] = {}
            for s in sessions:
                s.busy = False
                s.active_proc = None
                if s.waiting:
                    await s.set_waiting(None)
                s.last_seen = time.monotonic()
            run["files"] = export_run(run)

    async def _safe_client(self, run: dict, group: dict, s: Session, ctx: dict) -> dict:
        """One client's failure must not abort the run for the others."""
        try:
            return await self._run_client(run, group, s, ctx)
        except Exception as e:  # noqa: BLE001
            import traceback
            where = traceback.extract_tb(e.__traceback__)[-1]
            msg = f"{type(e).__name__}: {e} (at {where.name}:{where.lineno})"
            await s.emit(f"{RED}✖ {msg}{RESET}\r\n")
            return {"client": s.client["name"], "host": s.client["host"], "status": "failed",
                    "error": msg, "commands": []}

    async def _run_client(self, run: dict, group: dict, s: Session, ctx: dict) -> dict:
        st = run["settings"]
        rec: dict[str, Any] = {"client": s.client["name"], "host": s.client["host"], "status": "ok", "commands": []}
        cmds = group.get("per_session", {}).get(s.id, group.get("commands", []))
        # config sections that commands use as ${mount.…} / ${elbencho.…}; elbencho refers to mount, so mount first
        mount_err = None
        for section in ("mount", "elbencho"):
            try:
                ctx = {**ctx, section: render_section(ctx.get("cfg", {}).get(section, {}), ctx, section)}
            except TemplateError as e:
                mount_err = (mount_err + "; " if mount_err else "") + f"{section}: {e}"
        use_sudo = s.use_sudo and group.get("sudo", True)
        if use_sudo and s.sudo_nopasswd is None:
            try:
                r = await s.conn.run("sudo -n true", check=False, timeout=15)
                s.sudo_nopasswd = r.exit_status == 0
            except Exception:
                s.sudo_nopasswd = False
        await s.emit(f"\r\n{CYAN}━━ {run['label']} ({len(cmds)} command{'s' if len(cmds) != 1 else ''}) ━━{RESET}\r\n")
        for idx, item in enumerate(cmds, 1):
            tpl = item["run"] if isinstance(item, dict) else item
            display_tpl = item.get("display", tpl) if isinstance(item, dict) else tpl
            if idx > 1 and st["delay_between_commands_seconds"]:
                await s.emit(f"{DIM}… waiting {st['delay_between_commands_seconds']}s{RESET}\r\n")
                await asyncio.sleep(float(st["delay_between_commands_seconds"]))
            try:
                cmd = render(tpl, ctx)
                display = render(display_tpl, ctx)
            except TemplateError as e:
                msg = str(e)
                if "client.vip" in msg:
                    msg += " - run the VIP check / provisioning first, or set mount.vip to a fixed IP"
                elif ("mount." in msg or "elbencho." in msg) and mount_err:
                    msg += f" ({mount_err})"
                rec["commands"].append({"command": tpl, "exit_code": None, "error": msg, "output": ""})
                rec["status"] = "failed"
                await s.emit(f"{RED}✖ {msg}{RESET}\r\n")
                break
            c = await self._exec(run, s, cmd, display, idx, len(cmds), use_sudo)
            if group.get("results"):
                c["elbencho"] = elbencho.parse(c["output"])
            rec["commands"].append(c)
            if c["exit_code"] != 0:
                rec["status"] = "failed"
                if st["stop_on_error"]:
                    await s.emit(f"{RED}Stopping sequence (stop_on_error){RESET}\r\n")
                    break
        if group.get("parse") == "packages" and rec["commands"]:
            rec["packages"] = packages.parse_check(rec["commands"][0]["output"], group["pkg_cfg"])
            self.pkg_status[s.id] = rec["packages"]
        await s.emit(f"{GREEN if rec['status'] == 'ok' else RED}━━ {run['label']}: {rec['status'].upper()} ━━{RESET}\r\n")
        if s.shell:
            try:
                s.shell.stdin.write("\n")  # redraw the interactive prompt
            except (BrokenPipeError, OSError):
                pass
        return rec

    async def _exec(self, run: dict, s: Session, cmd: str, display: str, idx: int, total: int,
                    use_sudo: bool) -> dict:
        assert s.conn
        st = run["settings"]
        timeout = float(st["command_timeout_seconds"])
        prompt_rx = re.compile(st["input_prompt_regex"], re.I)
        # Echo the command the way a shell shows a typed command, so the console reads like a session log
        who = f"{s.client['username']}@{s.client['host']}"
        await s.emit(f"\r\n{YELLOW}[{run['label']} {idx}/{total}]{RESET} {GREEN}{who}{RESET}$ "
                     f"{BOLD}{crlf(s.mask(display))}{RESET}{DIM}{'  (sudo)' if use_sudo else ''}{RESET}\r\n")
        remote = cmd
        if use_sudo:
            remote = f"sudo -S -p '[sudo] password: ' bash -c {shlex.quote(cmd)}"
        started = time.monotonic()
        rec: dict[str, Any] = {"command": display, "started_at": now_iso()}
        chunks: list[str] = []
        proc = None
        try:
            kw: dict = {"encoding": "utf-8", "errors": "replace"}
            if st["use_pty"]:
                kw.update(term_type="xterm-256color", term_size=s.term_size)
            else:
                kw["stderr"] = asyncssh.STDOUT
            proc = await s.conn.create_process(remote, **kw)
            s.active_proc = proc
            if use_sudo and not s.sudo_nopasswd:
                proc.stdin.write((s.client.get("password") or "") + "\n")
            if not st["use_pty"]:
                proc.stdin.write_eof()

            queue: asyncio.Queue = asyncio.Queue()

            async def pump() -> None:
                while True:
                    data = await proc.stdout.read(4096)
                    await queue.put(data)
                    if not data:
                        return

            pump_task = asyncio.create_task(pump())
            deadline = started + timeout
            tail = ""
            try:
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise asyncio.TimeoutError
                    try:
                        data = await asyncio.wait_for(queue.get(), min(st["input_idle_seconds"], remaining))
                    except asyncio.TimeoutError:
                        if time.monotonic() >= deadline:
                            raise
                        lines = [ln.strip() for ln in ANSI_RE.sub("", tail).replace("\r", "\n").split("\n") if ln.strip()]
                        last = lines[-1] if lines else ""
                        if last and not s.waiting and prompt_rx.search(last):
                            run["waiting"][s.client["name"]] = last[-160:]
                            await s.set_waiting(last[-160:])
                            await s.emit(f"\r\n{MAGENTA}▶▶ Waiting for your input - type in this console and press Enter{RESET}\r\n")
                        continue
                    if not data:
                        break
                    if s.waiting:
                        run["waiting"].pop(s.client["name"], None)
                        await s.set_waiting(None)
                    data = s.mask(data)
                    chunks.append(data)
                    tail = (tail + data)[-2000:]
                    await s.emit(crlf(data))
            finally:
                pump_task.cancel()
            result = await proc.wait()
            rec["exit_code"] = result.exit_status if result.exit_status is not None else -1
        except asyncio.TimeoutError:
            rec["exit_code"] = -1
            rec["error"] = f"Timed out after {timeout:.0f}s"
            await s.emit(f"{RED}✖ timed out after {timeout:.0f}s{RESET}\r\n")
            if proc:
                try:
                    proc.terminate()
                except Exception:
                    pass
        except Exception as e:  # noqa: BLE001
            rec["exit_code"] = -1
            rec["error"] = f"{type(e).__name__}: {e}"
            await s.emit(f"{RED}✖ {rec['error']}{RESET}\r\n")
        finally:
            s.active_proc = None
            run["waiting"].pop(s.client["name"], None)
            if s.waiting:
                await s.set_waiting(None)
        rec["duration_s"] = round(time.monotonic() - started, 2)
        rec["output"] = ANSI_RE.sub("", "".join(chunks)).replace("\r\n", "\n")
        color = GREEN if rec["exit_code"] == 0 else RED
        await s.emit(f"{color}  exit={rec['exit_code']}  ({rec['duration_s']}s){RESET}\r\n")
        return rec
