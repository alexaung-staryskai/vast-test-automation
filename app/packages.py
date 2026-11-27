"""Required-package check and install/update command building for SSH clients."""
from __future__ import annotations

import re
import shlex

from .config import render

PM_ORDER = ["apt-get", "dnf", "yum", "zypper"]
PM_NAMES = ["apt", "dnf", "yum", "zypper"]

# packages.system entries are checked in the package manager's own database (dpkg / rpm), so library and
# meta packages with no command of their own (nfs-common, rdma-core...) are found too. $1 = package name.
PKGQ = r"""pkgq() {
  case "$PM" in
    apt) dpkg-query -W -f='${db:Status-Abbrev}|${Version}\n' "$1" 2>/dev/null | awk -F'|' 'index($1,"ii")==1 {print $2; f=1} END {exit !f}' || { echo "not installed"; return 1; } ;;
    # --whatprovides: also counts a package that provides the name (curl-minimal provides curl on RHEL 9)
    dnf|yum|zypper) V=$(rpm -q --whatprovides --qf '%{NAME}|%{VERSION}-%{RELEASE}\n' "$1" 2>/dev/null) && echo "$V" | awk -F'|' -v n="$1" '{print ($1 == n ? $2 : $2 " (" $1 ")"); exit}' || { echo "not installed"; return 1; } ;;
    *) echo "no supported package manager"; return 1 ;;
  esac
}"""


def system_entries(pkg_cfg: dict) -> list[dict]:
    """packages.system as entries: a plain package name, or {name, package: {apt: ..., dnf: ...}} when the
    name differs per package manager. Names already listed under packages.required are skipped."""
    seen = {p["name"] for p in pkg_cfg.get("required") or [] if isinstance(p, dict) and p.get("name")}
    out: list[dict] = []
    for e in pkg_cfg.get("system") or []:
        if isinstance(e, str):
            e = {"name": e}
        if not isinstance(e, dict) or not str(e.get("name") or "").strip():
            continue
        e = {"name": str(e["name"]).strip(), "package": e.get("package") or {}, "system": True}
        if e["name"] in seen:
            continue
        seen.add(e["name"])
        out.append(e)
    return out


def entries(pkg_cfg: dict) -> list[dict]:
    """Every package the app checks: packages.required (own check / install commands) + packages.system."""
    return [p for p in pkg_cfg.get("required") or [] if isinstance(p, dict) and p.get("name")] + system_entries(pkg_cfg)


def check_script(pkg_cfg: dict) -> str:
    """One shell script that reports package manager, each package's status and upgradable list.
    Output lines: @@PM|<pm>   @@PKG|<name>|<rc>|<first line>   @@UPG|<line>"""
    lines = [
        'PM=""; for p in ' + " ".join(PM_ORDER) + '; do command -v $p >/dev/null 2>&1 && { PM=$p; break; }; done',
        '[ "$PM" = apt-get ] && PM=apt',
        'echo "@@PM|$PM"',
    ]
    system = system_entries(pkg_cfg)
    if system:
        lines.append(PKGQ)
    for p in system:
        names = " ".join(f"{pm}) N={shlex.quote(_pkg_name(p, pm))};;" for pm in PM_NAMES)
        lines.append(
            f'case "$PM" in {names} *) N={shlex.quote(p["name"])};; esac; '
            f'OUT=$(pkgq "$N" 2>&1 </dev/null); RC=$?; '
            f'echo "@@PKG|{p["name"]}|$RC|$(printf "%s" "$OUT" | head -1 | tr "|" "/")"'
        )
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
    for p in entries(pkg_cfg):
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
    for p in entries(pkg_cfg):
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


# Reinstall / remove commands when package_managers.<pm> doesn't set them (older site configs).
PM_DEFAULTS = {
    "apt": {"reinstall": "apt-get install -y --reinstall ${pkg.names}", "remove": "apt-get remove -y ${pkg.names}"},
    "dnf": {"reinstall": "dnf reinstall -y ${pkg.names}", "remove": "dnf remove -y ${pkg.names}"},
    "yum": {"reinstall": "yum reinstall -y ${pkg.names}", "remove": "yum remove -y ${pkg.names}"},
    "zypper": {"reinstall": "zypper --non-interactive install -f ${pkg.names}",
               "remove": "zypper --non-interactive remove ${pkg.names}"},
}
ACTIONS = ("install", "reinstall", "uninstall")


def entry(pkg_cfg: dict, name: str) -> dict | None:
    return next((p for p in entries(pkg_cfg) if p["name"] == name), None)


def can_uninstall(p: dict) -> bool:
    """Custom-install packages (elbencho, vastnfs) need their own uninstall command; repo packages use the PM."""
    return bool(p.get("uninstall")) or not p.get("install")


def action_command(pkg_cfg: dict, status: dict, name: str, action: str) -> dict | None:
    """The command for one package action on one client, or None when it doesn't apply there
    (install when already installed, uninstall when not installed). Raises ValueError when it can't be built."""
    if action not in ACTIONS:
        raise ValueError(f"Unknown action '{action}' (install, reinstall or uninstall)")
    p = entry(pkg_cfg, name)
    if not p:
        raise ValueError(f"'{name}' is not in packages.required or packages.system")
    installed = bool(status.get("packages", {}).get(name, {}).get("installed"))
    if (action == "install" and installed) or (action == "uninstall" and not installed):
        return None
    if action == "reinstall" and not installed:
        action = "install"
    if p.get("install"):   # custom install script
        if action == "uninstall":
            if not p.get("uninstall"):
                raise ValueError(f"No uninstall command for '{name}' - add one under packages.required → {name} → uninstall")
            return {"run": p["uninstall"], "display": f"[{name}] {p.get('uninstall_label') or p['uninstall']}"}
        cmd = p.get("reinstall", p["install"]) if action == "reinstall" else p["install"]
        return {"run": cmd, "display": f"[{name}] {p.get('install_label') or cmd}"}
    pm = status.get("pm") or ""
    pmc = {**PM_DEFAULTS.get(pm, {}), **((pkg_cfg.get("package_managers") or {}).get(pm) or {})}
    key = "remove" if action == "uninstall" else action
    tpl = pmc.get(key)
    if not tpl:
        raise ValueError(f"No '{key}' command configured for package manager '{pm or 'unknown'}' - run Check packages first")
    return {"run": render(tpl, {"pkg": {"names": _pkg_name(p, pm)}})}
