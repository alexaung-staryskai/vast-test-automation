"""Required-package check and install/update command building for SSH clients."""
from __future__ import annotations

import re
import shlex

from .config import render

PM_ORDER = ["apt-get", "dnf", "yum", "zypper"]


def check_script(pkg_cfg: dict) -> str:
    """One shell script that reports package manager, each package's status and upgradable list.
    Output lines: @@PM|<pm>   @@PKG|<name>|<rc>|<first line>   @@UPG|<line>"""
    lines = [
        'PM=""; for p in ' + " ".join(PM_ORDER) + '; do command -v $p >/dev/null 2>&1 && { PM=$p; break; }; done',
        '[ "$PM" = apt-get ] && PM=apt',
        'echo "@@PM|$PM"',
    ]
    for p in pkg_cfg.get("required", []):
        check = p.get("check") or f"command -v {p['name']}"
        lines.append(
            f'OUT=$(bash -c {shlex.quote(check)} 2>&1 </dev/null); RC=$?; '
            f'echo "@@PKG|{p["name"]}|$RC|$(printf "%s" "$OUT" | head -1 | tr "|" "/")"'
        )
    cases = []
    for pm, c in (pkg_cfg.get("package_managers") or {}).items():
        if c.get("upgradable"):
            cases.append(f'{pm}) ({c["upgradable"]}) 2>/dev/null </dev/null | sed "s/^/@@UPG|/" ;;')
    if cases:
        lines.append('case "$PM" in ' + " ".join(cases) + " esac")
    lines.append("true")
    return "\n".join(lines)


def _pkg_name(p: dict, pm: str) -> str:
    return (p.get("package") or {}).get(pm, p["name"])


def parse_check(output: str, pkg_cfg: dict) -> dict:
    pm = ""
    pkgs: dict[str, dict] = {}
    upg_lines: list[str] = []
    for raw in output.splitlines():
        line = raw.strip("\r")
        if line.startswith("@@PM|"):
            pm = line[5:].strip()
        elif line.startswith("@@PKG|"):
            parts = line.split("|", 3)
            if len(parts) == 4:
                name, rc, ver = parts[1], parts[2], parts[3].strip()
                pkgs[name] = {"installed": rc == "0", "version": ver if rc == "0" else "", "detail": ver}
        elif line.startswith("@@UPG|"):
            upg_lines.append(line[6:])
    for p in pkg_cfg.get("required", []):
        st = pkgs.setdefault(p["name"], {"installed": False, "version": "", "detail": "not checked"})
        st["custom_install"] = bool(p.get("install"))
        st["update_available"] = False
        if st["installed"] and not p.get("install") and pm:
            name = re.escape(_pkg_name(p, pm))
            rx = re.compile(rf"(^|[\s|]){name}([./\s|:]|$)")
            st["update_available"] = any(rx.search(ln) for ln in upg_lines)
    return {"pm": pm, "packages": pkgs}


def build_commands(pkg_cfg: dict, status: dict, mode: str, only: list[str] | None = None) -> list[dict]:
    """Commands to install missing (mode=missing) or update (mode=update) packages on one client."""
    pm = status.get("pm") or ""
    pmc = (pkg_cfg.get("package_managers") or {}).get(pm, {})
    repo: list[str] = []
    cmds: list[dict] = []
    for p in pkg_cfg.get("required", []):
        name = p["name"]
        if only and name not in only:
            continue
        st = status.get("packages", {}).get(name, {})
        if mode == "missing" and st.get("installed"):
            continue
        if mode == "update" and not st.get("installed"):
            continue
        if p.get("install"):
            cmd = p.get("update", p["install"]) if mode == "update" else p["install"]
            # long install scripts can set install_label so the console shows one readable line
            cmds.append({"run": cmd, "display": f"[{name}] {p.get('install_label') or cmd}"})
        elif mode == "missing" or st.get("update_available"):
            repo.append(_pkg_name(p, pm))
    if repo:
        tpl = pmc.get("install" if mode == "missing" else "update")
        if not tpl:
            raise ValueError(f"No '{'install' if mode == 'missing' else 'update'}' command configured for "
                             f"package manager '{pm or 'unknown'}'")
        cmd = render(tpl, {"pkg": {"names": " ".join(repo)}})
        cmds.insert(0, {"run": cmd})
    return cmds
