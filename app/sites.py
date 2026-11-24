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
        "tenants": sum(1 for t in tenants if isinstance(t, dict) and t.get("status") != "deleted"),
        # tenants still on the VMS - deleting the site only removes local records, so the UI reminds the user
        "active_tenants": [
            {"name": t.get("name"), "vms": t.get("vms"), "tenant_id": t.get("tenant_id"),
             "vip_range": (t.get("vip_pool") or {}).get("range"),
             "objects": [o.get("step") for o in t.get("objects") or [] if isinstance(o, dict)]}
            for t in tenants if isinstance(t, dict) and t.get("status") != "deleted"],
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
