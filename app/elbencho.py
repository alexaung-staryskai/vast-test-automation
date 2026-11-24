"""Parse elbencho's result table and aggregate it across clients.

elbencho prints a table like:

    OPERATION   RESULT TYPE         FIRST DONE   LAST DONE
    =========== ================    ==========   =========
    WRITE       Elapsed time     :    20.432s     20.556s
                IOPS             :       1998        1992
                Throughput MiB/s :       1998        1992
                Total MiB        :      40832       40960
"""
from __future__ import annotations

import re
from typing import Any

LINE_RE = re.compile(r"^(?P<op>[A-Z][A-Z0-9_]*)?\s+(?P<metric>[A-Za-z%][^:]*?)\s*:\s*(?P<rest>\S.*)$")
DUR_RE = re.compile(r"^(?:(?P<h>\d+(?:\.\d+)?)h)?(?:(?P<m>\d+(?:\.\d+)?)m(?!s))?(?:(?P<s>\d+(?:\.\d+)?)s)?(?:(?P<ms>\d+(?:\.\d+)?)ms)?$")

SUMMABLE = ("Throughput MiB/s", "IOPS", "Files/s", "Dirs/s", "Total MiB", "Files total", "Dirs total")


def to_number(token: str) -> float | None:
    t = token.replace(",", "")
    try:
        return float(t)
    except ValueError:
        pass
    m = DUR_RE.match(t)
    if m and any(m.groupdict().values()):
        g = {k: float(v) if v else 0.0 for k, v in m.groupdict().items()}
        return g["h"] * 3600 + g["m"] * 60 + g["s"] + g["ms"] / 1000
    return None


def parse(output: str) -> list[dict]:
    """Return [{operation, metrics: {name: {first_done, last_done, raw}}}] for each table block."""
    ops: list[dict] = []
    current: dict | None = None
    in_table = False
    for raw_line in output.splitlines():
        line = raw_line.rstrip()
        if line.startswith("OPERATION") and "RESULT" in line:
            in_table = True
            current = None
            continue
        if not in_table or not line or line.startswith("==="):
            continue
        if line.startswith("---"):
            current = None
            continue
        m = LINE_RE.match(line)
        if not m:
            continue
        if m.group("op"):
            current = {"operation": m.group("op"), "metrics": {}}
            ops.append(current)
        if current is None:
            continue
        rest = m.group("rest").strip()
        tokens = rest.split()
        entry: dict[str, Any] = {"raw": rest}
        if 1 <= len(tokens) <= 2 and all(to_number(t) is not None for t in tokens):
            entry["first_done"] = to_number(tokens[0])
            entry["last_done"] = to_number(tokens[-1]) if len(tokens) == 2 else None
        current["metrics"][m.group("metric").strip()] = entry
    return ops


def aggregate(clients: list[dict]) -> list[dict]:
    """Sum throughput/IOPS per (command, operation) across clients."""
    rows: dict[tuple[int, str], dict] = {}
    for c in clients:
        for idx, cmd in enumerate(c.get("commands", [])):
            for op in cmd.get("elbencho") or []:
                key = (idx, op["operation"])
                row = rows.setdefault(
                    key,
                    {
                        "command_index": idx + 1,
                        "command": cmd["command"],
                        "operation": op["operation"],
                        "clients": [],
                        "totals_first_done": {},
                        "totals_last_done": {},
                        "max_elapsed_s": None,
                    },
                )
                row["clients"].append(c["client"])
                for name in SUMMABLE:
                    mt = op["metrics"].get(name)
                    if not mt:
                        continue
                    for col, dest in (("first_done", "totals_first_done"), ("last_done", "totals_last_done")):
                        if mt.get(col) is not None:
                            row[dest][name] = round(row[dest].get(name, 0) + mt[col], 2)
                el = op["metrics"].get("Elapsed time", {}).get("last_done")
                if el is not None:
                    row["max_elapsed_s"] = max(row["max_elapsed_s"] or 0, el)
    return sorted(rows.values(), key=lambda r: r["command_index"])
