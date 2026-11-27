"""Export runs to JSON + human-readable TXT in the results directory."""
from __future__ import annotations

import json
import time
from pathlib import Path

from .config import ROOT

RESULTS_DIR = ROOT / "results"


def set_results_dir(path: str | None, base: Path = ROOT) -> None:
    global RESULTS_DIR
    if path:
        p = Path(path)
        RESULTS_DIR = p if p.is_absolute() else base / p
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)


def _fmt(v) -> str:
    if v is None:
        return "-"
    if isinstance(v, float) and v.is_integer():
        return f"{int(v):,}"
    if isinstance(v, (int, float)):
        return f"{v:,.2f}"
    return str(v)


def run_to_text(run: dict) -> str:
    L: list[str] = []
    L.append(f"Run:       {run['run_id']}")
    L.append(f"Preset:    {run['label']} ({run['group']})")
    L.append(f"Status:    {run['status']}")
    L.append(f"Started:   {run['started_at']}")
    L.append(f"Finished:  {run['finished_at']}")
    s = run["settings"]
    L.append(f"Delay between commands: {s['delay_between_commands_seconds']}s   parallel clients: {s['parallel_clients']}")
    if run.get("error"):
        L.append(f"Error:     {run['error']}")
    if run.get("summary"):
        L.append("")
        L.append("=" * 100)
        L.append("AGGREGATE (sum across clients)")
        L.append("=" * 100)
        L.append(f"{'#':>2}  {'OPERATION':<10} {'CLIENTS':>7} {'MiB/s (first)':>14} {'MiB/s (last)':>13} {'IOPS (first)':>13} {'IOPS (last)':>12} {'MAX ELAPSED':>12}")
        for r in run["summary"]:
            f, l = r["totals_first_done"], r["totals_last_done"]
            L.append(
                f"{r['command_index']:>2}  {r['operation']:<10} {len(r['clients']):>7} "
                f"{_fmt(f.get('Throughput MiB/s')):>14} {_fmt(l.get('Throughput MiB/s')):>13} "
                f"{_fmt(f.get('IOPS')):>13} {_fmt(l.get('IOPS')):>12} {_fmt(r['max_elapsed_s']):>11}s"
            )
    for c in run["clients"]:
        L.append("")
        L.append("=" * 100)
        L.append(f"CLIENT {c['client']} ({c['host']})  status={c['status']}")
        L.append("=" * 100)
        if c.get("error"):
            L.append(f"error: {c['error']}")
        for i, cmd in enumerate(c["commands"], 1):
            L.append(f"[{i}] $ {cmd['command']}")
            L.append(f"    exit={cmd.get('exit_code')}  duration={cmd.get('duration_s', '-')}s  started={cmd.get('started_at', '-')}")
            if cmd.get("error"):
                L.append(f"    error: {cmd['error']}")
            out = (cmd.get("output") or "").rstrip()
            if out:
                L.extend("    " + ln for ln in out.splitlines())
            L.append("")
    return "\n".join(L) + "\n"


def export_run(run: dict) -> list[str]:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    base = RESULTS_DIR / run["run_id"]
    base.with_suffix(".json").write_text(json.dumps({k: v for k, v in run.items() if k != "files"}, indent=2))
    base.with_suffix(".txt").write_text(run_to_text(run))
    return [base.with_suffix(".json").name, base.with_suffix(".txt").name]


def export_provisioning(results: list[dict], vars_: dict, vms_url: str) -> list[str]:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    name = time.strftime("%Y%m%d-%H%M%S") + "-provisioning"
    base = RESULTS_DIR / name
    doc = {"vms": vms_url, "exported_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "vars": vars_, "steps": results}
    base.with_suffix(".json").write_text(json.dumps(doc, indent=2))
    L = [f"Provisioning / API test report  -  {doc['exported_at']}", f"VMS: {vms_url}", ""]
    passed = sum(1 for r in results if r.get("status") == "passed")
    L.append(f"Passed {passed}/{len(results)}")
    L.append("")
    for r in results:
        L.append(f"[{r.get('status', '-').upper():<7}] {r.get('name')}")
        req = r.get("request")
        if req:
            L.append(f"          {req['method']} {req['url']}  ->  HTTP {r.get('response', {}).get('status', '-')}")
        if r.get("error"):
            L.append(f"          error: {r['error']}")
        for chk in (r.get("verify") or {}).get("checks", []):
            mark = "ok " if chk["passed"] else "BAD"
            L.append(f"          verify {mark} {chk['field']}: expected={chk['expected']!r} actual={chk['actual']!r}")
    base.with_suffix(".txt").write_text("\n".join(L) + "\n")
    return [base.with_suffix(".json").name, base.with_suffix(".txt").name]


def list_files() -> list[dict]:
    if not RESULTS_DIR.exists():
        return []
    files = [p for p in RESULTS_DIR.iterdir() if p.suffix in (".json", ".txt", ".md") and not p.name.startswith(".")]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return [{"name": p.name, "size": p.stat().st_size, "mtime": p.stat().st_mtime} for p in files]


# ------------------------------------------------------------------ logs (Logs tab)
# Every run, onboarding report and console export in RESULTS_DIR, one entry per run id (its .txt/.json/.md).

ARCHIVE_DIRNAME = "archive"


def _log_entry(stem: str, files: list[Path], perf_groups: set[str]) -> dict:
    kind = stem.split("-", 2)[2] if stem.count("-") >= 2 else stem
    entry = {"id": stem, "type": kind, "label": kind, "status": "", "clients": [], "performance": False,
             "files": [{"name": f.name, "size": f.stat().st_size} for f in sorted(files, key=lambda p: p.suffix)],
             "size": sum(f.stat().st_size for f in files), "mtime": max(f.stat().st_mtime for f in files)}
    ts = stem[:15]
    if len(ts) == 15 and ts[8] == "-" and ts.replace("-", "").isdigit():
        entry["time"] = f"{ts[0:4]}-{ts[4:6]}-{ts[6:8]}T{ts[9:11]}:{ts[11:13]}:{ts[13:15]}"
    js = next((f for f in files if f.suffix == ".json"), None)
    if kind == "performance_report":   # PowerPoint performance report (+ its .md summary)
        clients: list[str] = []
        md = next((f for f in files if f.suffix == ".md"), None)
        if md is not None:
            for line in md.read_text(errors="replace").splitlines():
                if line.startswith("- Runs:") and "clients:" in line:
                    clients = [c.strip() for c in line.split("clients:", 1)[1].split(",") if c.strip() and c.strip() != "-"]
        fmts = [n for suf, n in ((".pptx", "PowerPoint"), (".docx", "Word")) if any(f.suffix == suf for f in files)]
        entry.update(type="report", label=f"Performance report ({' + '.join(fmts) or 'summary'})", status="done",
                     clients=clients, performance=True)
    elif kind.startswith("console-"):
        entry.update(type="console", label=f"Console export: {kind[len('console-'):]}")
    elif js is not None:
        try:
            d = json.loads(js.read_text())
        except (OSError, ValueError):
            d = {}
        if "steps" in d and "vms" in d:  # onboarding / API test report
            steps = d.get("steps") or []
            ok = sum(1 for s in steps if s.get("status") in ("passed", "reused"))
            entry.update(type="provisioning", label="Onboarding & API test report",
                         status="done" if steps and ok == len(steps) else "failed",
                         clients=[d.get("vms", "")])
        else:
            entry.update(type=d.get("group", kind), label=d.get("label", kind), status=d.get("status", ""),
                         clients=[c.get("client", "") for c in d.get("clients", [])],
                         performance=d.get("group") in perf_groups)
    return entry


def list_logs(perf_groups: set[str]) -> list[dict]:
    if not RESULTS_DIR.exists():
        return []
    groups: dict[str, list[Path]] = {}
    for p in RESULTS_DIR.iterdir():
        if p.is_file() and p.suffix in (".json", ".txt", ".md", ".pptx", ".docx") and not p.name.startswith("."):
            groups.setdefault(p.stem, []).append(p)
    logs = [_log_entry(stem, files, perf_groups) for stem, files in groups.items()]
    logs.sort(key=lambda e: (e.get("time") or "", e["mtime"]), reverse=True)
    return logs


def _selected(perf_groups: set[str], include_performance: bool) -> list[Path]:
    out: list[Path] = []
    for e in list_logs(perf_groups):
        if e["performance"] and not include_performance:
            continue
        out.extend(RESULTS_DIR / f["name"] for f in e["files"])
    return out


def archive_logs(perf_groups: set[str], include_performance: bool, site_name: str) -> dict:
    """Zip the logs into results/archive/ and remove the originals."""
    import zipfile
    files = _selected(perf_groups, include_performance)
    if not files:
        return {"archive": None, "runs": 0, "files": 0}
    adir = RESULTS_DIR / ARCHIVE_DIRNAME
    adir.mkdir(parents=True, exist_ok=True)
    name = f"logs-{time.strftime('%Y%m%d-%H%M%S')}.zip"
    with zipfile.ZipFile(adir / name, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            z.write(f, f"{site_name}-logs/{f.name}")
    for f in files:
        f.unlink(missing_ok=True)
    return {"archive": name, "runs": len({f.stem for f in files}), "files": len(files)}


def purge_logs(perf_groups: set[str], include_performance: bool) -> dict:
    """Delete the logs for good (archives in results/archive/ are kept)."""
    files = _selected(perf_groups, include_performance)
    for f in files:
        f.unlink(missing_ok=True)
    return {"runs": len({f.stem for f in files}), "files": len(files)}


def list_archives() -> list[dict]:
    adir = RESULTS_DIR / ARCHIVE_DIRNAME
    if not adir.exists():
        return []
    zips = sorted(adir.glob("logs-*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
    return [{"name": p.name, "size": p.stat().st_size, "mtime": p.stat().st_mtime} for p in zips]
