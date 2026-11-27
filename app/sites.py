"""Test sites: one self-contained folder per customer/test, so a run can be handed over and replicated.

    sites/<name>/
      site.yaml        name, description, created_at
      config.yaml      everything the app needs (VMS, vars, VIP plan, clients, mount, packages, presets)
      tenants.yaml     onboarded tenants
      results/         exported runs (JSON + TXT)
      config-backups/  previous versions of config.yaml

Any folder with a config.yaml can be opened, so a colleague can copy or unzip a site anywhere.
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .config import ROOT

SITES_DIR = Path(os.environ.get("VAST_SITES_DIR", ROOT / "sites"))
LAST_FILE = SITES_DIR / ".last-site"
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{1,63}$")
EXAMPLE = ROOT / "config.example.yaml"
TEMPLATES_DIR = ROOT / "templates"   # ready-made configurations, e.g. templates/lab-example.yaml


def list_templates() -> list[dict]:
    """Templates are config files; '# label:' and '# description:' comment lines describe them."""
    out = []
    for f in sorted(TEMPLATES_DIR.glob("*.yaml")):
        meta = {"label": f.stem, "description": ""}
        for line in f.read_text().splitlines()[:5]:
            for key in ("label", "description"):
                if line.startswith(f"# {key}:"):
                    meta[key] = line.split(":", 1)[1].strip()
        out.append({"id": f"template:{f.stem}", **meta})
    return out


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


# Tenant statuses meaning "no longer on the VMS": "deleted" (🧹 Cleanup in tab ①) and
# "vms record deleted" (🗑 Delete site - VMS objects removed, local records and test results kept).
VMS_DELETED = "vms record deleted"
GONE = ("deleted", VMS_DELETED)


def mark_vms_deleted(path: Path) -> None:
    """Delete site keeps the folder (test results) and only records that its VMS objects are gone."""
    f = path / "site.yaml"
    meta = (yaml.safe_load(f.read_text()) or {}) if f.is_file() else {}
    meta.setdefault("name", path.name)
    meta.update(status=VMS_DELETED, vms_deleted_at=_now())
    f.write_text(yaml.safe_dump(meta, sort_keys=False))


def slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", name.strip()).strip("-")


def is_site(path: Path) -> bool:
    return (path / "config.yaml").is_file()


def info(path: Path) -> dict:
    meta = {}
    if (path / "site.yaml").is_file():
        meta = yaml.safe_load((path / "site.yaml").read_text()) or {}
    cfg = {}
    try:
        cfg = yaml.safe_load((path / "config.yaml").read_text()) or {}
    except (OSError, yaml.YAMLError):
        pass
    app_cfg = cfg.get("app") or {}
    tfile = path / str(app_cfg.get("tenants_file", "tenants.yaml"))
    tenants = []
    if tfile.is_file():
        try:
            tenants = (yaml.safe_load(tfile.read_text()) or {}).get("tenants") or []
        except yaml.YAMLError:
            pass
    rdir = path / str(app_cfg.get("results_dir", "results"))
    results = [p for p in rdir.glob("*.json")] if rdir.is_dir() else []
    mtimes = [p.stat().st_mtime for p in [path / "config.yaml", path / "site.yaml", tfile, *results] if p.exists()]
    return {
        "name": meta.get("name", path.name),
        "description": meta.get("description", ""),
        "created_at": meta.get("created_at"),
        "path": str(path.resolve()),
        "vms": (cfg.get("vms") or {}).get("url"),
        # "vms record deleted" after 🗑 Delete site - until a new tenant is onboarded in the site
        "status": meta.get("status") if not any(isinstance(t, dict) and t.get("status") not in GONE for t in tenants) else None,
        "vms_deleted_at": meta.get("vms_deleted_at"),
        "tenants": sum(1 for t in tenants if isinstance(t, dict) and t.get("status") not in GONE),
        # tenants still on the VMS - deleting the site only removes local records, so the UI reminds the user
        "active_tenants": [
            {"name": t.get("name"), "vms": t.get("vms"), "tenant_id": t.get("tenant_id"),
             "vip_range": (t.get("vip_pool") or {}).get("range"),
             "objects": [o.get("step") for o in t.get("objects") or [] if isinstance(o, dict)]}
            for t in tenants if isinstance(t, dict) and t.get("status") not in GONE],
        # tenants already deleted from the VMS - "Check VMS for leftovers" looks for what the VMS left behind
        "deleted_tenants": [t.get("name") for t in tenants if isinstance(t, dict) and t.get("status") in GONE and t.get("name")],
        "results": len(results),
        "backups": len(list((path / "config-backups").glob("*.yaml"))) if (path / "config-backups").is_dir() else 0,
        "copied_from": meta.get("copied_from"),
        "has_tenants_file": tfile.is_file(),
        "updated": max(mtimes) if mtimes else None,
    }


def list_sites() -> list[dict]:
    SITES_DIR.mkdir(parents=True, exist_ok=True)
    out = [info(d) for d in SITES_DIR.iterdir() if d.is_dir() and is_site(d)]
    return sorted(out, key=lambda s: s["updated"] or 0, reverse=True)


def create(name: str, description: str = "", template: str | None = None) -> Path:
    name = name.strip()
    if not NAME_RE.match(name):
        raise ValueError("Use 2-64 characters: letters, digits, space, dot, dash or underscore")
    path = SITES_DIR / slug(name)
    if path.exists():
        raise ValueError(f"A test site folder named '{path.name}' already exists - open it instead")
    src = EXAMPLE
    if template and template.startswith("template:"):  # a ready-made configuration
        src = TEMPLATES_DIR / f"{template.split(':', 1)[1]}.yaml"
        if src.parent != TEMPLATES_DIR or not src.is_file():
            raise ValueError(f"Unknown template: {template}")
    elif template:  # copy the configuration of an existing site (not its tenants or results)
        tp = Path(template)
        src = tp / "config.yaml" if tp.is_dir() else tp
        if not src.is_file():
            raise ValueError(f"Template not found: {template}")
    path.mkdir(parents=True)
    shutil.copy(src, path / "config.yaml")  # new file date = creation time
    (path / "results").mkdir()
    (path / "site.yaml").write_text(yaml.safe_dump(
        {"name": name, "description": description, "created_at": _now(),
         "copied_from": (f"templates/{src.name}" if template and template.startswith("template:")
                         else str(src) if template else "config.example.yaml")}, sort_keys=False))
    return path


def update_meta(path: Path, name: str, description: str) -> None:
    """Rename (display name only - the folder keeps its name) and describe a site."""
    if not NAME_RE.match(name.strip()):
        raise ValueError("Use 2-64 characters: letters, digits, space, dot, dash or underscore")
    f = path / "site.yaml"
    meta = (yaml.safe_load(f.read_text()) or {}) if f.is_file() else {}
    meta.update(name=name.strip(), description=description.strip(), updated_at=_now())
    meta.setdefault("created_at", _now())
    f.write_text(yaml.safe_dump(meta, sort_keys=False))


TRASH_DIR = SITES_DIR / ".trash"


def trash(path_str: str) -> Path:
    """'Delete' a site by moving its folder to sites/.trash/ (recoverable). Only folders directly in sites/."""
    path = Path(path_str).resolve()
    if path.parent != SITES_DIR.resolve() or not is_site(path):
        raise ValueError("Only test sites inside the sites folder can be deleted here")
    if last() == str(path):
        LAST_FILE.unlink(missing_ok=True)
    TRASH_DIR.mkdir(parents=True, exist_ok=True)
    dest = TRASH_DIR / f"{path.name}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    shutil.move(str(path), str(dest))
    return dest


def resolve(path_or_name: str) -> Path:
    p = Path(path_or_name).expanduser()
    if not p.is_absolute():
        cand = SITES_DIR / path_or_name
        p = cand if cand.exists() else (ROOT / path_or_name)
    if p.is_file() and p.name == "config.yaml":
        p = p.parent
    if not is_site(p):
        raise ValueError(f"No config.yaml in {p} - not a test site folder")
    return p.resolve()


def remember(path: Path) -> None:
    try:
        SITES_DIR.mkdir(parents=True, exist_ok=True)
        LAST_FILE.write_text(str(path))
    except OSError:
        pass


def last() -> str | None:
    try:
        p = LAST_FILE.read_text().strip()
        return p if p and is_site(Path(p)) else None
    except OSError:
        return None


def export_zip(path: Path, include_results: bool = True) -> bytes:
    """Zip the site for hand-over. Hidden files (run state with generated secrets) and backups are left out."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(path.rglob("*")):
            rel = f.relative_to(path)
            if not f.is_file() or any(part.startswith(".") for part in rel.parts):
                continue
            if rel.parts[0] == "config-backups" or (not include_results and rel.parts[0] == "results"):
                continue
            z.write(f, Path(path.name) / rel)
    return buf.getvalue()


# ---- site configuration export / import (no logs) ----
MANIFEST = "vast-test-bench-site.json"
SECRET_FILES = (".client-secrets.json", ".tenant-secrets.json")
STATE_FILE = ".provision_state.json"          # onboarding state (created object ids, generated password)
MAX_IMPORT_BYTES = 40 * 1024 * 1024
REPORT_TEMPLATE = "report-template.docx"        # Word report template uploaded in ⚙ Configuration


def _site_files(path: Path) -> tuple[str, str]:
    """(tenants file, results dir) names from the site's config.yaml."""
    try:
        app_cfg = (yaml.safe_load((path / "config.yaml").read_text()) or {}).get("app") or {}
    except (OSError, yaml.YAMLError):
        app_cfg = {}
    return str(app_cfg.get("tenants_file", "tenants.yaml")), str(app_cfg.get("results_dir", "results"))


def export_config_zip(path: Path, include_secrets: bool = False, app_version: str = "") -> tuple[bytes, list[str]]:
    """Everything that configures the site - no logs / results, no config backups:
    config.yaml (VMS connection, onboarding settings, VIP pool, clients, presets, custom commands, report targets),
    site.yaml (name, description, status), the tenants file and the Word report template. include_secrets adds the saved passwords
    (.client-secrets.json, .tenant-secrets.json) and the onboarding state."""
    tfile, rdir = _site_files(path)
    files = [("config.yaml", path / "config.yaml"), ("site.yaml", path / "site.yaml")]
    if "/" not in tfile and ".." not in tfile:
        files.append((tfile, path / tfile))
    files.append((REPORT_TEMPLATE, path / REPORT_TEMPLATE))     # uploaded Word report template
    if include_secrets:
        files += [(n, path / n) for n in SECRET_FILES]
        files.append((f"results/{STATE_FILE}", path / rdir / STATE_FILE))
    files = [(n, f) for n, f in files if f.is_file()]
    meta = info(path)
    manifest = {"format": 1, "kind": "vast-test-bench-site", "app_version": app_version, "exported_at": _now(),
                "site": {"name": meta["name"], "description": meta["description"], "vms": meta["vms"]},
                "files": [n for n, _ in files], "includes_secrets": include_secrets, "tenants_file": tfile}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{path.name}/{MANIFEST}", json.dumps(manifest, indent=2))
        for n, f in files:
            if n == "site.yaml":   # no local absolute paths (user name, folders) in a zip that gets shared
                try:
                    m = yaml.safe_load(f.read_text()) or {}
                    cf = str(m.get("copied_from") or "")
                    if cf.startswith(("/", "~")) or re.match(r"^[A-Za-z]:[\\/]", cf):
                        parts = Path(cf).parts
                        m["copied_from"] = "/".join(parts[-3:]) if "sites" in parts[-3:-1] else Path(cf).name
                    z.writestr(f"{path.name}/{n}", yaml.safe_dump(m, sort_keys=False))
                    continue
                except (OSError, yaml.YAMLError):
                    pass
            z.write(f, f"{path.name}/{n}")
    return buf.getvalue(), [n for n, _ in files]


def import_zip(data: bytes, filename: str = "") -> dict:
    """Create a new site from a site zip (this app's configuration export, or a full / 'without results' download).
    Only configuration is taken: logs, results and backups in the zip are skipped. Never overwrites a site."""
    import posixpath
    from . import config_store
    if len(data) > MAX_IMPORT_BYTES:
        raise ValueError("The file is too large for a site configuration (over 40 MB) - export it without logs")
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ValueError("Not a zip file - choose a site .zip exported from VAST Test Bench")
    names = [n for n in z.namelist() if not n.endswith("/")]
    for n in names:
        parts = n.replace("\\", "/").split("/")
        if n.startswith("/") or ".." in parts or ":" in parts[0]:
            raise ValueError(f"Unsafe path in the zip: {n}")
    cfgs = [n for n in names if posixpath.basename(n) == "config.yaml" and n.count("/") <= 1]
    if len(cfgs) != 1:
        raise ValueError("The zip must contain one site (a config.yaml at the top or in one folder)")
    root = posixpath.dirname(cfgs[0])
    pre = f"{root}/" if root else ""
    rel = {n[len(pre):]: n for n in names if n.startswith(pre)}
    def read(n: str) -> bytes:
        info_ = z.getinfo(rel[n])
        if info_.file_size > MAX_IMPORT_BYTES:
            raise ValueError(f"{n} is too large")
        return z.read(rel[n])
    try:
        cfg_text = read("config.yaml").decode("utf-8")
        cfg = yaml.safe_load(cfg_text) or {}
    except (UnicodeDecodeError, yaml.YAMLError) as e:
        raise ValueError(f"config.yaml can't be read: {e}")
    errors = config_store.validate(cfg)
    if errors:
        raise ValueError("config.yaml is not valid: " + "; ".join(errors[:3]))
    meta: dict = {}
    if "site.yaml" in rel:
        try:
            meta = yaml.safe_load(read("site.yaml").decode("utf-8")) or {}
        except (UnicodeDecodeError, yaml.YAMLError):
            meta = {}
    manifest: dict = {}
    if MANIFEST in rel:
        try:
            manifest = json.loads(read(MANIFEST))
        except ValueError:
            manifest = {}
    app_cfg = cfg.get("app") or {}
    tfile = str(app_cfg.get("tenants_file", "tenants.yaml"))
    rdir = str(app_cfg.get("results_dir", "results"))
    wanted = {"config.yaml": "config.yaml", "site.yaml": "site.yaml"}
    if "/" not in tfile and ".." not in tfile:
        wanted[tfile] = tfile
    for n in SECRET_FILES:
        wanted[n] = n
    wanted[REPORT_TEMPLATE] = REPORT_TEMPLATE
    wanted[f"results/{STATE_FILE}"] = f"{rdir}/{STATE_FILE}" if "/" not in rdir and ".." not in rdir else None
    skipped = sorted(n for n in rel if n not in wanted and n != MANIFEST)
    # a unique display name and folder
    base = str(meta.get("name") or manifest.get("site", {}).get("name") or root or Path(filename).stem or "Imported site").strip()
    base = re.sub(r"[^A-Za-z0-9 ._-]+", "-", base)[:58].strip(" .-_") or "Imported site"
    if not NAME_RE.match(base):
        base = "Imported site"
    taken = {s["name"] for s in list_sites()}
    name, i = base, 2
    while name in taken or (SITES_DIR / slug(name)).exists():
        name, i = f"{base}-{i}", i + 1
    path = SITES_DIR / slug(name)
    path.mkdir(parents=True)
    written = []
    try:
        (path / "results").mkdir(exist_ok=True)
        for src, dst in wanted.items():
            if src not in rel or dst is None or src == "site.yaml":
                continue
            target = path / dst
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(read(src))
            if posixpath.basename(dst).startswith("."):
                target.chmod(0o600)                       # passwords / keys / onboarding state: owner only
            written.append(src)
        meta.update(name=name, imported_at=_now(), imported_from=filename or "zip")
        meta.setdefault("description", "")
        meta.setdefault("created_at", _now())
        (path / "site.yaml").write_text(yaml.safe_dump(meta, sort_keys=False))
        written.append("site.yaml")
    except Exception:
        shutil.rmtree(path, ignore_errors=True)
        raise
    return {"path": str(path.resolve()), "name": name, "files": sorted(written), "skipped": skipped,
            "renamed": name != base, "includes_secrets": any(n in written for n in SECRET_FILES),
            "from_version": manifest.get("app_version")}


# ---- app-wide delete log (sites/.delete-log.jsonl): survives the site folder moving to the trash ----
AUDIT_FILE = SITES_DIR / ".delete-log.jsonl"


def audit(**entry) -> None:
    try:
        SITES_DIR.mkdir(parents=True, exist_ok=True)
        with AUDIT_FILE.open("a") as f:
            f.write(json.dumps({"time": _now(), **entry}, default=str) + "\n")
    except OSError:
        pass


def read_audit(limit: int = 1000) -> list[dict]:
    try:
        lines = AUDIT_FILE.read_text().splitlines()
    except OSError:
        return []
    out = []
    for ln in lines[-limit:]:
        try:
            out.append(json.loads(ln))
        except ValueError:
            pass
    return out[::-1]
