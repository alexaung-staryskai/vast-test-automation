"""Read / validate / save the raw config file for the Configuration tab.
Saves keep comments and ordering (ruamel round-trip) and back up the previous file."""
from __future__ import annotations

import io
import shutil
import time
from pathlib import Path
from typing import Any

import yaml
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

REQUIRED = {"vms": dict, "vars": dict, "provisioning": dict, "clients": list, "command_groups": list}


def _rt() -> YAML:
    y = YAML()
    y.preserve_quotes = True
    y.width = 4096
    y.indent(mapping=2, sequence=4, offset=2)
    return y


def read_raw(path: Path) -> dict:
    text = path.read_text() if path.exists() else ""
    data = yaml.safe_load(text) or {}
    return {"path": str(path), "text": text, "data": data, "mtime": path.stat().st_mtime if path.exists() else None}


def validate(data: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["Top level must be a mapping"]
    for key, typ in REQUIRED.items():
        if key not in data:
            errors.append(f"Missing section: {key}")
        elif not isinstance(data[key], typ):
            errors.append(f"Section '{key}' must be a {'list' if typ is list else 'mapping'}")
    if isinstance(data.get("vms"), dict) and not data["vms"].get("url"):
        errors.append("vms.url is required")
    ids = [s.get("id") for s in (data.get("provisioning") or {}).get("steps", []) if isinstance(s, dict)]
    if any(not i for i in ids):
        errors.append("Every provisioning step needs an id")
    dup = {i for i in ids if ids.count(i) > 1}
    if dup:
        errors.append(f"Duplicate step ids: {', '.join(sorted(map(str, dup)))}")
    for c in data.get("clients") or []:
        if not isinstance(c, dict) or not c.get("name") or not c.get("host"):
            errors.append("Every client needs name and host")
            break
    vip = data.get("vip_pool") or {}
    if vip:
        try:
            if int(vip.get("ip_count", 5)) < 5:
                errors.append("vip_pool.ip_count must be at least 5")
        except (TypeError, ValueError):
            errors.append("vip_pool.ip_count must be a number")
        for r in vip.get("ranges") or []:
            if not isinstance(r, dict) or not r.get("start") or not r.get("end"):
                errors.append("Each vip_pool.ranges entry needs start and end")
                break
    return errors


def _backup(path: Path) -> str | None:
    if not path.exists():
        return None
    bdir = path.parent / "config-backups"
    bdir.mkdir(exist_ok=True)
    dest = bdir / f"{path.stem}-{time.strftime('%Y%m%d-%H%M%S')}{path.suffix}"
    shutil.copy2(path, dest)
    return str(dest)


def save_text(path: Path, text: str) -> dict:
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as e:
        return {"ok": False, "errors": [f"YAML syntax error: {e}"]}
    errors = validate(data)
    if errors:
        return {"ok": False, "errors": errors}
    backup = _backup(path)
    path.write_text(text)
    return {"ok": True, "backup": backup}


def _detach_inner_comment(parent: CommentedMap, key: Any) -> None:
    slot = parent.ca.items.get(key)
    if not slot or len(slot) < 4 or not slot[3]:
        return
    texts = [t.value.strip() for t in slot[3] if getattr(t, "value", "").strip()]
    slot[3] = None
    if texts and not slot[2]:
        parent.yaml_add_eol_comment(" ".join(texts), key)


def _merge(node: Any, new: Any) -> Any:
    """Apply `new` onto the round-trip node, keeping comments where the structure is unchanged."""
    if isinstance(node, CommentedMap) and isinstance(new, dict):
        for k in [k for k in node if k not in new]:
            del node[k]
        for k, v in new.items():
            old = node.get(k)
            node[k] = _merge(old, v) if k in node else v
            if isinstance(old, (CommentedMap, CommentedSeq)) and not isinstance(node[k], (CommentedMap, CommentedSeq)) \
                    or (isinstance(node[k], (list, dict)) and not node[k]):
                # A block collection became a scalar or an empty [] / {}: comment lines that sat between
                # the key and its first item would be written in between "key:" and the flow value,
                # which is invalid YAML. Keep them as an end-of-line comment instead.
                _detach_inner_comment(node, k)
        return node
    if isinstance(node, CommentedSeq) and isinstance(new, list):
        if len(node) == len(new):
            for i, v in enumerate(new):
                node[i] = _merge(node[i], v)
            return node
        if len(new) > len(node):  # items added at the end
            for i in range(len(node)):
                node[i] = _merge(node[i], new[i])
            node.extend(new[len(node):])
            return node
        return new  # items removed: replace (comments inside the list are lost)
    return new


def save_data(path: Path, data: dict) -> dict:
    errors = validate(data)
    if errors:
        return {"ok": False, "errors": errors}
    y = _rt()
    doc = y.load(path.read_text()) if path.exists() else CommentedMap()
    doc = _merge(doc, data)
    buf = io.StringIO()
    y.dump(doc, buf)
    text = buf.getvalue()
    # Never write a file that would not load back as exactly what was asked for.
    try:
        reread = yaml.safe_load(text) or {}
    except yaml.YAMLError as e:
        return {"ok": False, "errors": [f"Internal error: the updated config would not be valid YAML ({e}). Nothing was written."]}
    if reread != data:
        return {"ok": False, "errors": ["Internal error: the updated config did not round-trip. Nothing was written."]}
    backup = _backup(path)
    path.write_text(text)
    return {"ok": True, "backup": backup}
