"""vperfcheck: VAST's profile-driven elbencho wrapper (docs.vastdata.com/docs/vperfcheck-automated-performance-testing).

One client (the coordinator) runs vperfcheck.sh. It starts `elbencho --service` on every benchmark client over
passwordless SSH and runs them together (elbencho's distributed mode), so the results are one aggregate per profile.

This module builds the command lines, sets up / tests / removes the SSH trust from the coordinator to the clients,
writes the S3 credentials file, and turns vperfcheck's results.json (elbencho JSON records) into a run summary in
the same shape as the elbencho presets (so Logs, Chart and the reports read it).

SSH trust is additive and marked, so it never breaks the SSH access that already works:
  * a dedicated key ~/.ssh/vast_test_bench_vperfcheck on the coordinator (existing keys are not touched),
  * its public key appended once to ~/.ssh/authorized_keys on each client (tagged; removable),
  * a marked block in the coordinator's ~/.ssh/config for these hosts only: that key, accept-new host keys
    (a changed host key is still refused), and User / Port only where they differ. A backup is kept.
"""
from __future__ import annotations

import ipaddress
import json
import re
import shlex
from datetime import datetime, timedelta
from typing import Any, AsyncIterator

DEFAULT_URL = "http://vast-vperfsanity.s3.amazonaws.com/download/vperfcheck-latest-stable.tar.gz"
FILE_PROFILES = ["file-read-bw", "file-read-iops", "file-read-lat", "file-write-bw", "file-write-iops", "file-write-lat"]
S3_PROFILES = ["s3-read-bw", "s3-read-iops", "s3-read-lat", "s3-write-bw"]
KEY_NAME = "vast_test_bench_vperfcheck"          # ~/.ssh/<KEY_NAME> on the coordinator
KEY_TAG = "vast-test-bench-vperfcheck"            # comment of that key in authorized_keys (what Remove deletes)
CFG_BEGIN = "# >>> vast-test-bench vperfcheck: managed by VAST Test Bench (Remove SSH trust deletes it) >>>"
CFG_END = "# <<< vast-test-bench vperfcheck <<<"
S3_ENV = "~/.vast-test-bench/vperfcheck-s3.env"   # written just before a run, deleted as soon as the run reads it
SIZE_RE = re.compile(r"^\d+(\.\d+)?[kmgtpKMGTP]?[iI]?[bB]?$")
DUR_RE = re.compile(r"^\d+[smh]?$")

DEFAULTS: dict[str, Any] = {
    "url": DEFAULT_URL,
    "install_dir": "${mount.mount_point}/vperfcheck",
    "dir": "${mount.mount_point}/vperfcheck-data",
    "file_tests": ["file-read-bw", "file-read-iops"],
    "prep": "auto",
    "time": "1m",
    "threads": "",
    "noloop": False,
    "lat": False,
    "verbose": True,
    "elbencho_args": "--nolive",
    "stop_services_after": True,
    "timeout_seconds": 14400,
    "s3": {"bucket": "${vars.bucket_name}", "prefix": "vperfcheck", "endpoint": "auto",
           "tests": ["s3-read-bw", "s3-read-iops"]},
}
ADVANCED = ("block", "filesize", "iodepth", "direct", "rand", "autosize", "autotime")


def q(v: Any) -> str:
    return shlex.quote(str(v))


def merged(raw: dict | None) -> dict:
    """The vperfcheck config section with defaults filled in (not rendered)."""
    raw = raw if isinstance(raw, dict) else {}
    out = {**DEFAULTS, **{k: v for k, v in raw.items() if k != "s3"}}
    out["s3"] = {**DEFAULTS["s3"], **(raw.get("s3") if isinstance(raw.get("s3"), dict) else {})}
    return out


def bracket_endpoint(ips: list[str], scheme: str = "http") -> str:
    """Tenant VIPs as vperfcheck's bracket form: http://10.0.0.[1-4,7] (one entry per /24, comma separated)."""
    groups: dict[str, list[int]] = {}
    for ip in ips:
        try:
            a = ipaddress.IPv4Address(str(ip))
        except ValueError:
            continue
        p = str(a).rsplit(".", 1)
        groups.setdefault(p[0], []).append(int(p[1]))
    parts = []
    for prefix, octs in groups.items():
        octs = sorted(set(octs))
        if len(octs) == 1:
            parts.append(f"{scheme}://{prefix}.{octs[0]}")
            continue
        ranges, start = [], octs[0]
        for prev, cur in zip(octs, octs[1:] + [None]):
            if cur != (prev + 1 if prev is not None else None):
                ranges.append(f"{start}-{prev}" if start != prev else f"{start}")
                start = cur
        parts.append(f"{scheme}://{prefix}.[{','.join(ranges)}]")
    return ",".join(parts)


# ------------------------------------------------------------------ command lines

def check_size(v: str, what: str) -> str:
    v = str(v).strip()
    if not SIZE_RE.match(v):
        raise ValueError(f"{what}: '{v}' is not a size like 100g or 1t")
    return v


def run_args(opts: dict, hosts: list[str], resdir: str, stop_after: bool) -> list[str]:
    """vperfcheck.sh arguments for one run. opts: mode, dir (path or s3://bucket/prefix), prep {mode, size},
    tests, time, threads, noloop, lat, verbose, dryrun, advanced {...}, passthrough."""
    args = ["./vperfcheck.sh", "--dir", opts["target"]]
    prep = opts.get("prep") or {}
    pm = prep.get("mode", "auto")
    if pm == "auto":
        args += ["--prep", "auto"]
    elif pm == "size":
        args += ["--prep", check_size(prep.get("size", ""), "Dataset size")]
    elif pm != "keep":
        raise ValueError(f"Unknown dataset option '{pm}' (keep, auto or size)")
    tests = [t for t in (opts.get("tests") or []) if t]
    for t in tests:
        if not re.fullmatch(r"[A-Za-z0-9._*-]+", t):
            raise ValueError(f"Profile name '{t}' is not valid")
    if tests:
        args += ["--test", ",".join(tests)]
    t = str(opts.get("time") or "").strip()
    if t:
        if not DUR_RE.match(t):
            raise ValueError(f"Time '{t}' must look like 30s, 1m or 2h")
        args += ["--time", t]
    th = str(opts.get("threads") or "").strip()
    if th:
        if not th.isdigit() or int(th) < 1:
            raise ValueError(f"Threads '{th}' must be a whole number")
        args += ["--threads", th]
    if opts.get("noloop"):
        args.append("--noloop")
    if opts.get("lat"):
        args.append("--lat")
    adv = opts.get("advanced") or {}
    for k in ADVANCED:
        v = str(adv.get(k) or "").strip()
        if not v:
            continue
        if k in ("block", "filesize", "autosize"):
            check_size(v, k)
        elif k == "autotime" and not DUR_RE.match(v):
            raise ValueError(f"autotime '{v}' must look like 30s, 1m or 2h")
        elif k in ("iodepth",) and not v.isdigit():
            raise ValueError(f"iodepth '{v}' must be a whole number")
        elif k in ("direct", "rand") and v not in ("0", "1"):
            raise ValueError(f"{k} must be 0 or 1")
        args += [f"--{k}", v]
    if opts.get("verbose"):
        args.append("--verbose")
    if opts.get("dryrun"):
        args.append("--dryrun")
    args += ["--resdir", resdir]
    if stop_after and hosts and not opts.get("dryrun"):
        args.append("--stop")
    args += hosts
    # after "--" straight to elbencho: the configured elbencho_args (--nolive keeps the logs readable) + the typed ones
    passthrough = (str(opts.get("elbencho_args") or "") + " " + str(opts.get("passthrough") or "")).strip()
    if passthrough:
        args += ["--", *shlex.split(passthrough)]
    return args


def run_command(install_dir: str, args: list[str], s3: bool) -> str:
    """Shell command for the coordinator. In S3 mode the credentials file is read, then deleted at once."""
    env = f". {S3_ENV} && rm -f {S3_ENV} && " if s3 else ""
    return f"cd {q(install_dir)} && {env}" + " ".join(q(a) for a in args)


def s3_env_file(access_key: str, secret_key: str, endpoint: str) -> str:
    return "".join(f"export {k}={q(v)}\n" for k, v in (("AWS_ACCESS_KEY_ID", access_key),
                                                       ("AWS_SECRET_ACCESS_KEY", secret_key),
                                                       ("AWS_ENDPOINT_URL_S3", endpoint)) if v)


def s3_list_cmd(first_endpoint: str, target: str) -> str:
    """How many objects are under the prefix (0 or 1 is enough), after reading the credentials file."""
    return (f". {S3_ENV} && rm -f {S3_ENV} && aws --no-verify-ssl --endpoint-url {q(first_endpoint)} s3 ls --recursive "
            f"{q(target)} 2>&1 | grep -v InsecureRequestWarning | head -3")


def write_s3_env_cmd() -> str:
    """Reads the file body on stdin (never on the command line), mode 600."""
    return f"umask 077 && mkdir -p ~/.vast-test-bench && cat > {S3_ENV} && echo ok"


def install_commands(url: str, install_dir: str, user: str, mount_point: str) -> list[dict]:
    d = q(install_dir)
    mcheck = ""
    if mount_point and str(install_dir).startswith(str(mount_point).rstrip("/") + "/"):
        mcheck = (f'mountpoint -q {q(mount_point)} || {{ echo "{mount_point} is not mounted - run Mount NFS first '
                  f'(vperfcheck goes on the shared VAST view so every client sees it)"; exit 1; }}; ')
    return [
        {"run": f"{mcheck}mkdir -p {d} && chown {q(user)} {d} && ls -ld {d}",
         "display": f"create {install_dir} (owned by {user})"},
        {"run": (f'T=$(mktemp) && {{ curl -fsSL -o "$T" {q(url)} || wget -qO "$T" {q(url)}; }} '
                 f'&& tar -xzf "$T" -C {d} --strip-components=1 && rm -f "$T" && chmod +x {d}/vperfcheck.sh '
                 f'&& echo "vperfcheck $(cat {d}/.version 2>/dev/null) is in {install_dir}" '
                 f'&& echo "profiles: $(ls {d}/profiles | tr \'\\n\' \' \')"'),
         "display": f"download {url} and unpack into {install_dir}", "sudo": False},
    ]


def prepare_dir_command(target: str, user: str, mount_point: str, shared: bool = False) -> dict:
    """shared: the benchmark clients log in as different users, so each client's elbencho service (running as that
    user) must be able to write there too."""
    d = q(target)
    mcheck = ""
    if mount_point and str(target).startswith(str(mount_point).rstrip("/") + "/"):
        mcheck = f'mountpoint -q {q(mount_point)} || {{ echo "{mount_point} is not mounted - run Mount NFS first"; exit 1; }}; '
    return {"run": f"{mcheck}mkdir -p {d} && chown {q(user)} {d}" + (f" && chmod 777 {d}" if shared else "") + f" && ls -ld {d}",
            "display": f"create {target} (owned by {user}" + (", writable by every client's user)" if shared else ")")}


# ------------------------------------------------------------------ results

def parse_records(text: str) -> list[dict]:
    """elbencho --jsonfile: JSON objects one after another (sometimes with blank lines between them)."""
    dec, i, out = json.JSONDecoder(), 0, []
    while i < len(text):
        while i < len(text) and text[i].isspace():
            i += 1
        if i >= len(text):
            break
        try:
            obj, i = dec.raw_decode(text, i)
        except ValueError:
            break
        if isinstance(obj, dict):
            out.append(obj)
    return out


def _f(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _lat(done: dict) -> dict | None:
    """latency.IO -> {min, avg, max, p50, p99, ...} in µs (the same keys perf_report.parse_latency returns)."""
    io = ((done or {}).get("latency") or {}).get("IO") or {}
    out = {k: _f(io.get(f"{k}_us")) for k in ("min", "avg", "max")}
    out.update({f"p{k}": _f(v) for k, v in (io.get("percentiles_us") or {}).items()})
    out = {k: v for k, v in out.items() if v is not None}
    return out or None


def _totals(done: dict) -> dict:
    done = done or {}
    bps, byts = _f(done.get("bytes/s")), _f(done.get("bytes"))
    out = {"Throughput MiB/s": bps / 1048576 if bps is not None else None, "IOPS": _f(done.get("iops")),
           "Total MiB": byts / 1048576 if byts is not None else None,
           "Elapsed time": (_f(done.get("elapsed_time_ms")) or 0) / 1000 if done.get("elapsed_time_ms") is not None else None}
    if bps is None:   # metadata phases (mkdirs, delete): entries per second
        out["Files/s"] = _f(done.get("entries/s"))
    return {k: v for k, v in out.items() if v is not None}


def summary(records: list[dict], hosts: list[str], command: str) -> list[dict]:
    """One row per elbencho phase, in the same shape as elbencho.aggregate() (plus profile, window, latency, hosts)."""
    # auto-sizing writes one "prepare" record per doubling: keep the last of each run of the same profile + phase
    keep = [r for i, r in enumerate(records)
            if not (i + 1 < len(records) and (records[i + 1].get("label"), records[i + 1].get("phase_type"))
                    == (r.get("label"), r.get("phase_type")))]
    rows = []
    for i, r in enumerate(keep, 1):
        cfg = r.get("config") or {}
        n = int(_f(cfg.get("hosts")) or len(hosts) or 1)
        first, last = r.get("first_done") or {}, r.get("last_done") or {}
        start = None
        try:
            start = datetime.strptime(str(r.get("iso_start_date")), "%Y-%m-%dT%H:%M:%S.%f%z")
        except ValueError:
            pass
        el = _f(last.get("elapsed_time_ms"))
        rows.append({
            "command_index": i, "command": command, "operation": str(r.get("phase_type") or "?").upper(),
            "profile": r.get("label") or "", "clients": hosts[:n] if hosts else [f"{n} host(s)"],
            "threads": _f(cfg.get("threads")), "block_size": _f(cfg.get("block_size")),
            "totals_first_done": _totals(first), "totals_last_done": _totals(last),
            "max_elapsed_s": el / 1000 if el is not None else None,
            "lat": _lat(last) or _lat(first),
            "window": [start.isoformat(), (start + timedelta(milliseconds=el or 0)).isoformat()] if start else None,
        })
    return rows


# ------------------------------------------------------------------ SSH trust (coordinator -> clients)

async def _sh(s, cmd: str, input: str | None = None, timeout: float = 60) -> tuple[int, str]:
    """Run a short command on a session's SSH connection (no PTY, no sudo). Returns (exit code, output)."""
    if not s.conn:
        return 255, "not connected"
    try:
        r = await s.conn.run(cmd, input=input, check=False, timeout=timeout)
    except Exception as e:  # noqa: BLE001
        return 255, f"{type(e).__name__}: {e}"
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    return (r.exit_status if r.exit_status is not None else 255), s.mask(out)


def config_block(coord_user: str, targets: list[dict]) -> str:
    """targets: [{host, username, port}]. User / Port only where they differ from what ssh would use anyway."""
    lines, seen = [CFG_BEGIN], set()
    for t in targets:
        h = str(t["host"])
        if h in seen or not re.fullmatch(r"[A-Za-z0-9._:\-\[\]%]+", h):
            continue
        seen.add(h)
        lines += [f"Host {h}", f"    IdentityFile ~/.ssh/{KEY_NAME}", "    StrictHostKeyChecking accept-new"]
        if t.get("username") and t["username"] != coord_user:
            lines.append(f"    User {t['username']}")
        if int(t.get("port") or 22) != 22:
            lines.append(f"    Port {int(t['port'])}")
    lines.append(CFG_END)
    return "\n".join(lines) + "\n"


KEYGEN = f"""command -v ssh-keygen >/dev/null || {{ echo "ssh-keygen not found - install openssh-client"; exit 1; }}
umask 077; mkdir -p ~/.ssh; K=~/.ssh/{KEY_NAME}
if [ -f "$K" ] && [ -f "$K.pub" ]; then echo "@@STATE existing"; else
  rm -f "$K" "$K.pub"; ssh-keygen -q -t ed25519 -N '' -C "{KEY_TAG}@$(hostname)" -f "$K" >/dev/null && echo "@@STATE created" || exit 1
fi
echo "@@PUB $(cat "$K.pub")"
"""
AUTHORIZE = """umask 077; mkdir -p ~/.ssh; IFS= read -r KEY; F=~/.ssh/authorized_keys
if [ -f "$F" ] && grep -qxF "$KEY" "$F"; then echo "already authorized"; exit 0; fi
if [ -s "$F" ] && [ -n "$(tail -c1 "$F")" ]; then echo >> "$F"; fi
printf '%s\\n' "$KEY" >> "$F" && echo "key added to ~/.ssh/authorized_keys"
"""
# the marked block goes first so it wins for these hosts; the rest of the file is kept as it is
WRITE_CONFIG = f"""umask 077; mkdir -p ~/.ssh; C=~/.ssh/config; B=$(cat)
[ -e "$C" ] || : > "$C"
cp -p "$C" "$C.vast-test-bench.bak"
{{ printf '%s\\n\\n' "$B"; awk -v b={q(CFG_BEGIN)} -v e={q(CFG_END)} '$0==b{{s=1;next}} $0==e{{s=0;d=1;next}} d&&/^$/{{d=0;next}} {{d=0}} !s' "$C"; }} > "$C.vtb-tmp" \\
  && cat "$C.vtb-tmp" > "$C" && rm -f "$C.vtb-tmp" && echo "~/.ssh/config updated (backup: ~/.ssh/config.vast-test-bench.bak)"
"""
REMOVE_CONFIG = f"""C=~/.ssh/config
if [ -f "$C" ] && grep -qxF {q(CFG_BEGIN)} "$C"; then
  cp -p "$C" "$C.vast-test-bench.bak"
  awk -v b={q(CFG_BEGIN)} -v e={q(CFG_END)} '$0==b{{s=1;next}} $0==e{{s=0;d=1;next}} d&&/^$/{{d=0;next}} {{d=0}} !s' "$C" > "$C.vtb-tmp" \\
    && cat "$C.vtb-tmp" > "$C" && rm -f "$C.vtb-tmp" && echo "block removed from ~/.ssh/config"
else echo "no block in ~/.ssh/config"; fi
rm -f ~/.ssh/{KEY_NAME} ~/.ssh/{KEY_NAME}.pub && echo "key ~/.ssh/{KEY_NAME} removed"
"""
UNAUTHORIZE = f"""F=~/.ssh/authorized_keys
if [ -f "$F" ] && grep -q {q(KEY_TAG)} "$F"; then
  grep -v {q(KEY_TAG)} "$F" > "$F.vtb-tmp"; cat "$F.vtb-tmp" > "$F"; rm -f "$F.vtb-tmp"; echo "key removed from ~/.ssh/authorized_keys"
else echo "no VAST Test Bench key in ~/.ssh/authorized_keys"; fi
"""


READ_BLOCK = (f"C=~/.ssh/config; [ -f \"$C\" ] && awk -v b={q(CFG_BEGIN)} -v e={q(CFG_END)} "
              "'$0==b{s=1;next} $0==e{s=0} s' \"$C\"; true")


def parse_block(text: str) -> list[dict]:
    """The Host stanzas of our marked ~/.ssh/config block: [{host, username?, port?}]."""
    out: list[dict] = []
    cur: dict | None = None
    for ln in (text or "").splitlines():
        t = ln.strip()
        if t.startswith("Host "):
            cur = {"host": t[5:].strip()}
            out.append(cur)
        elif cur is not None and t.startswith("User "):
            cur["username"] = t[5:].strip()
        elif cur is not None and t.startswith("Port ") and t[5:].strip().isdigit():
            cur["port"] = int(t[5:].strip())
    return out


def ssh_test_cmd(host: str) -> str:
    return (f"ssh -n -o BatchMode=yes -o ConnectTimeout=8 -o StrictHostKeyChecking=accept-new -o LogLevel=ERROR {q(host)} "
            f"'echo \"$(id -un)@$(hostname)\"'")


async def trust_test(coord, targets: list) -> AsyncIterator[dict]:
    """From the coordinator: ssh (no password, no prompts) to every target, the way vperfcheck does."""
    for t in targets:
        step = f"test:{t.id}"
        yield {"step": step, "label": f"{coord.client['name']} → {t.client['name']} ({t.client['host']})", "status": "running"}
        rc, out = await _sh(coord, ssh_test_cmd(t.client["host"]), timeout=30)
        last = out.splitlines()[-1] if out else ""
        yield {"step": step, "status": "ok" if rc == 0 else "failed",
               "detail": f"logged in as {last}" if rc == 0 else (out[-400:] or f"exit {rc}")}


async def trust_setup(coord, targets: list) -> AsyncIterator[dict]:
    yield {"step": "key", "label": f"SSH key on {coord.client['name']} (~/.ssh/{KEY_NAME})", "status": "running"}
    rc, out = await _sh(coord, KEYGEN)
    pub = next((ln[6:].strip() for ln in out.splitlines() if ln.startswith("@@PUB ")), "")
    if rc != 0 or not pub.startswith("ssh-"):
        yield {"step": "key", "status": "failed", "detail": out[-400:] or f"exit {rc}"}
        return
    state = "created" if "@@STATE created" in out else "already there - reused"
    yield {"step": "key", "status": "ok", "detail": f"{state}: {pub.split()[0]} …{pub.split()[1][-12:]}"}
    for t in targets:
        step = f"auth:{t.id}"
        yield {"step": step, "label": f"Authorize the key on {t.client['name']} (~/.ssh/authorized_keys)", "status": "running"}
        rc, out = await _sh(t, AUTHORIZE, input=pub + "\n")
        yield {"step": step, "status": "ok" if rc == 0 else "failed", "detail": out[-400:]}
    # add these hosts to the block (hosts set up earlier stay in it)
    new = [{"host": t.client["host"], "username": t.client.get("username"), "port": t.client.get("port")} for t in targets]
    _, old = await _sh(coord, READ_BLOCK)
    keep = [e for e in parse_block(old) if e["host"] not in {n["host"] for n in new}]
    block = config_block(coord.client.get("username", ""), keep + new)
    yield {"step": "config", "label": f"~/.ssh/config on {coord.client['name']} (marked block for these hosts)", "status": "running"}
    rc, out = await _sh(coord, WRITE_CONFIG, input=block)
    if rc == 0 and keep:
        out += f"\nalso still set up: {', '.join(e['host'] for e in keep)}"
    yield {"step": "config", "status": "ok" if rc == 0 else "failed", "detail": out[-400:]}
    if rc != 0:
        return
    async for ev in trust_test(coord, targets):
        yield ev


async def trust_remove(coord, targets: list) -> AsyncIterator[dict]:
    for t in targets:
        step = f"unauth:{t.id}"
        yield {"step": step, "label": f"Remove the key from {t.client['name']}", "status": "running"}
        rc, out = await _sh(t, UNAUTHORIZE)
        yield {"step": step, "status": "ok" if rc == 0 else "failed", "detail": out[-400:]}
    # only the ticked hosts leave the block; the key and the block go once no host is left
    _, old = await _sh(coord, READ_BLOCK)
    gone = {t.client["host"] for t in targets}
    rest = [e for e in parse_block(old) if e["host"] not in gone]
    if rest:
        yield {"step": "config", "label": f"~/.ssh/config on {coord.client['name']}: take out these hosts", "status": "running"}
        rc, out = await _sh(coord, WRITE_CONFIG, input=config_block(coord.client.get("username", ""), rest))
        out += f"\nstill set up (and the key kept) for: {', '.join(e['host'] for e in rest)}"
    else:
        yield {"step": "config", "label": f"Remove the key and ~/.ssh/config block on {coord.client['name']}", "status": "running"}
        rc, out = await _sh(coord, REMOVE_CONFIG)
    yield {"step": "config", "status": "ok" if rc == 0 else "failed", "detail": out[-400:]}
