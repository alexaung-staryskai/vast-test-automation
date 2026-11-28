"""Performance report as a PowerPoint deck (python-pptx), built from the site's elbencho run files.

It uses the same template as the tester manual (app/assets/report-template.pptx: the VAST layouts, Moderat titles,
theme colours, VAST logo, the green note box and black code box). Numbers are the same as the Performance results table in tab ②: summed across clients, throughput converted from
elbencho's MiB/s to decimal units (1 GB/s = 1000 MB/s). Charts are native PowerPoint charts, so they stay editable.
The deck is written to the results folder next to a short .md summary, and shows up in the 🗒 Logs tab.
"""
from __future__ import annotations

import copy
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.dml import MSO_LINE
from pptx.enum.chart import XL_CHART_TYPE, XL_LABEL_POSITION, XL_LEGEND_POSITION
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Inches, Pt

UNITS = {"MB": 1e6, "GB": 1e9, "TB": 1e12}            # decimal throughput units, as in the app
MIB = 1048576

PERF_OPS = ("READ", "WRITE")                # the report shows only the read / write phases
PERF_TOOLS = ("elbencho", "vperfcheck.sh", "fio", "vdbench", "iozone", "ior", "mdtest", "warp", "dd")   # benchmark commands worth showing

TEMPLATE = Path(__file__).parent / "assets" / "report-template.pptx"

# colours of the manual's template
NAVY = RGBColor(0x10, 0x16, 0x2D)          # titles, text
TEAL = RGBColor(0x1A, 0x9F, 0xC0)          # title accent
ORANGE = RGBColor(0xE9, 0x71, 0x32)        # accent2: the manual's numbered circles
GREEN = RGBColor(0x4E, 0xA7, 0x2E)         # accent6: note boxes
MUTED = RGBColor(0x5B, 0x67, 0x7A)
LINE = RGBColor(0xC9, 0xD6, 0xE3)          # card borders in the manual
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
LIGHT_TEAL = RGBColor(0x9C, 0xD9, 0xE8)
SERIES = [TEAL, ORANGE, NAVY, GREEN, RGBColor(0x0F, 0x9E, 0xD5), RGBColor(0xA0, 0x2B, 0x93)]
TITLE_FONT = "Moderat"                     # the template's title font (body text inherits the theme font)


# ------------------------------------------------------------------ data

def _num(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _window(w) -> tuple | None:
    """vperfcheck summary rows carry their own [start, end] (ISO); elbencho presets get it from the commands."""
    if isinstance(w, (list, tuple)) and len(w) == 2:
        a, b = _when(w[0]), _when(w[1])
        if a and b:
            return (a, b)
    return None


def _when(iso: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(str(iso))
    except (TypeError, ValueError):
        return None


_LAT_UNIT = {"ns": 0.001, "us": 1.0, "µs": 1.0, "ms": 1000.0, "s": 1e6}


def parse_latency(metrics: dict) -> dict | None:
    """elbencho --lat --latpercent: 'IO latency : [ min=440us avg=1.48ms max=27.1ms ]' and
    'IO lat % us : [ 1%<=576 50%<=1216 75%<=1920 99%<=3808 99.9%<=4928 ]'. Returns microseconds:
    {min, avg, max, p1, p50, p75, p99, p99.9} (whatever is present)."""
    import re
    out: dict[str, float] = {}
    raw = str((metrics.get("IO latency") or {}).get("raw") or "")
    for k in ("min", "avg", "max"):
        m = re.search(rf"\b{k}=([\d.]+)\s*(ns|us|µs|ms|s)\b", raw)
        if m:
            out[k] = float(m.group(1)) * _LAT_UNIT[m.group(2)]
    raw = str((metrics.get("IO lat % us") or {}).get("raw") or "")
    for pct, v in re.findall(r"([\d.]+)%<=([\d.]+)", raw):
        out[f"p{pct}"] = float(v)
    return out or None


def _combine_latency(per_client: dict[str, dict], weights: dict[str, float | None]) -> dict | None:
    """Across clients: average weighted by each client's IOPS; percentiles and max = the worst client (conservative)."""
    lats = {c: l for c, l in per_client.items() if l}
    if not lats:
        return None
    out: dict[str, float] = {}
    w = {c: (weights.get(c) or 1.0) for c in lats}
    if all("avg" in l for l in lats.values()):
        out["avg"] = sum(l["avg"] * w[c] for c, l in lats.items()) / sum(w.values())
    keys = {k for l in lats.values() for k in l} - {"avg"}
    for k in keys:
        vals = [l[k] for l in lats.values() if k in l]
        out[k] = min(vals) if k == "min" else max(vals)
    return out


def rows_for(run: dict) -> list[dict]:
    """One row per elbencho phase, summed across clients (same as perfRows() in the UI), with per-client values,
    latency (if elbencho ran with --lat --latpercent) and the time window the benchmark command ran in."""
    per_client: dict[tuple[int, str], dict[str, float | None]] = {}
    per_client_lat: dict[tuple[int, str], dict[str, dict | None]] = {}
    per_client_iops: dict[tuple[int, str], dict[str, float | None]] = {}
    windows: dict[int, list] = {}
    for c in run.get("clients") or []:
        for i, cmd in enumerate(c.get("commands") or []):
            st, du = _when(cmd.get("started_at")), _num(cmd.get("duration_s"))
            if st and du is not None and cmd.get("elbencho"):
                w = windows.setdefault(i + 1, [st, st])
                w[0] = min(w[0], st)
                w[1] = max(w[1], st + timedelta(seconds=du))
            for op in cmd.get("elbencho") or []:
                m = op.get("metrics") or {}
                key, name = (i + 1, str(op.get("operation"))), c.get("client", "?")
                per_client.setdefault(key, {})[name] = _num((m.get("Throughput MiB/s") or {}).get("last_done"))
                per_client_iops.setdefault(key, {})[name] = _num((m.get("IOPS") or {}).get("last_done"))
                per_client_lat.setdefault(key, {})[name] = parse_latency(m)
    out = []
    for r in run.get("summary") or []:
        first, last = r.get("totals_first_done") or {}, r.get("totals_last_done") or {}
        out.append({
            "run_id": run.get("run_id"), "label": run.get("label") or run.get("group"), "status": run.get("status"),
            "started": _when(run.get("started_at")), "index": r.get("command_index"), "operation": str(r.get("operation")),
            "clients": list(r.get("clients") or []), "command": r.get("command") or "",
            "tp_first": _num(first.get("Throughput MiB/s")), "tp_last": _num(last.get("Throughput MiB/s")),
            "iops_first": _num(first.get("IOPS")), "iops_last": _num(last.get("IOPS")),
            "ops_last": _num(last.get("Files/s", last.get("Dirs/s"))), "total_mib": _num(last.get("Total MiB")),
            "elapsed": _num(r.get("max_elapsed_s")),
            "per_client": per_client.get((r.get("command_index"), str(r.get("operation"))), {}),
            "lat_clients": per_client_lat.get((r.get("command_index"), str(r.get("operation"))), {}),
            "lat": _combine_latency(per_client_lat.get((r.get("command_index"), str(r.get("operation"))), {}),
                                    per_client_iops.get((r.get("command_index"), str(r.get("operation"))), {})) or r.get("lat"),
            "window": tuple(windows[r.get("command_index")]) if r.get("command_index") in windows else _window(r.get("window")),
            "profile": r.get("profile") or "",   # vperfcheck: the profile (file-read-bw, s3-read-iops, ...)
        })
    return out


def run_windows(runs: list[dict]) -> dict[str, tuple[datetime, datetime]]:
    """run_id -> (start, end) of its benchmark commands (for the VMS metrics query)."""
    out = {}
    for run in runs:
        ws = [r["window"] for r in rows_for(run) if r["window"] and r["operation"].upper() in PERF_OPS]
        if ws:
            out[run.get("run_id")] = (min(w[0] for w in ws), max(w[1] for w in ws))
    return out


def perf_command(cmd: str) -> str:
    """Only the benchmark part of a preset's command line (drops mount checks, mkdir, cd … around it)."""
    import re
    parts = [p.strip() for p in re.split(r"\s*(?:&&|\|\||;|\n)\s*", str(cmd or "")) if p.strip()]
    keep = [p for p in parts if re.sub(r"^(?:sudo\s+|exec\s+|time\s+)+", "", p).split(" ", 1)[0].rsplit("/", 1)[-1] in PERF_TOOLS]
    return " && ".join(keep)


def _tp(mib_s: float | None, unit: str) -> float | None:
    return None if mib_s is None else mib_s * MIB / UNITS[unit]


def _fmt(v: float | None, d: int = 2) -> str:
    if v is None:
        return "-"
    return f"{v:,.{d}f}"


def _cat(r: dict) -> str:
    t = r["started"].strftime("%m-%d %H:%M") if r["started"] else ""
    return f"{r.get('profile') or r['operation'].title()} · {t}"


# ------------------------------------------------------------------ template helpers (as in the manual's builder)

R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


class Deck:
    LEFT, TOP, RIGHT, BOTTOM = Inches(0.84), Inches(2.0), Inches(12.5), Inches(6.75)   # content area of the manual

    def __init__(self):
        self.prs = Presentation(str(TEMPLATE))
        self.lay = {l.name: l for l in self.prs.slide_layouts}
        self.tpl = list(self.prs.slides)            # the template's own slides, removed before saving
        def find(i, name):
            return next(sh for sh in self.tpl[i].shapes if sh.name == name)
        self.logo = find(0, "Graphic 3")
        self.slnum = find(0, "Slide Number Placeholder 4")._element
        self.note_el = find(1, "Rectangle 12")._element
        self.code_el = find(2, "Rectangle 5")._element
        self._ids = 2000

    # -- slides
    def cover(self, title: str, sub: str | None = None, meta: str | None = None):
        s = self.prs.slides.add_slide(self.lay["13_Cover"])
        tf = s.placeholders[0].text_frame
        tf.text = title
        if sub:
            r = tf.add_paragraph().add_run(); r.text = sub; r.font.size = Pt(28); r.font.bold = False
        if meta:
            r = tf.add_paragraph().add_run(); r.text = meta; r.font.size = Pt(14); r.font.bold = False
            r.font.color.rgb = MUTED
        for ph in list(s.placeholders):
            if ph.placeholder_format.idx != 0:
                ph._element.getparent().remove(ph._element)
        self._copy_picture(s, self.logo)
        s.shapes._spTree.append(copy.deepcopy(self.slnum))
        return s

    def content(self, title: str, sub: str):
        s = self.prs.slides.add_slide(self.lay["Single Content Light"])
        s.placeholders[14].text_frame.text = title
        s.placeholders[15].text_frame.text = sub
        body = s.placeholders[13]
        body._element.getparent().remove(body._element)
        return s

    def save(self, out: Path) -> int:
        lst = self.prs.slides._sldIdLst
        for sid in list(lst)[:len(self.tpl)]:
            self.prs.part.drop_rel(sid.rId); lst.remove(sid)
        self.prs.save(str(out))
        return len(lst)

    # -- template shapes
    def _copy_picture(self, slide, src):
        el = copy.deepcopy(src._element)
        for node in el.iter():
            for attr in (f"{{{R_NS}}}embed", f"{{{R_NS}}}link"):
                rid = node.get(attr)
                if rid:
                    part = src.part.related_part(rid)
                    node.set(attr, slide.part.relate_to(part, f"{R_NS}/image"))
        slide.shapes._spTree.append(el)

    def _add(self, slide, el, x, y, w, h, name):
        el = copy.deepcopy(el)
        self._ids += 1
        cnv = el.find(".//" + qn("p:cNvPr")); cnv.set("id", str(self._ids)); cnv.set("name", name)
        for ext in cnv.findall(qn("a:extLst")):
            cnv.remove(ext)
        xfrm = el.find(".//" + qn("a:xfrm"))
        xfrm.find(qn("a:off")).set("x", str(int(x))); xfrm.find(qn("a:off")).set("y", str(int(y)))
        xfrm.find(qn("a:ext")).set("cx", str(int(w))); xfrm.find(qn("a:ext")).set("cy", str(int(h)))
        slide.shapes._spTree.append(el)
        return slide.shapes[-1]

    def _fill(self, shape, paras, size=11, mono=False):
        tf = shape.text_frame
        tf.clear()
        for i, para in enumerate(paras):
            p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            p.alignment = PP_ALIGN.LEFT
            ppr = p._p.get_or_add_pPr()
            for k in ("indent", "marL"):
                ppr.set(k, "0")
            for t, bold in (para if isinstance(para, list) else [(para, False)]):
                r = p.add_run(); r.text = t; r.font.size = Pt(size); r.font.bold = bold
                if mono:
                    r.font.name = "Courier New"
        tf.margin_left = tf.margin_right = Inches(0.1)

    def note(self, slide, lines, x, y, w, h, title="Note:"):
        sh = self._add(slide, self.note_el, x, y, w, h, "Note")
        self._fill(sh, [[(title, True)]] + lines, size=11)
        return sh

    def code(self, slide, lines, x, y, w, h, size=10):
        sh = self._add(slide, self.code_el, x, y, w, h, "Code")
        self._fill(sh, lines, size=size, mono=True)
        sh.text_frame.vertical_anchor = MSO_ANCHOR.TOP
        sh.text_frame.margin_top = Inches(0.1)
        return sh


def _text(slide, x, y, w, h, paras, size=12, color=None, align=PP_ALIGN.LEFT, name="Text"):
    """paras: list of str or list of (text, bold) / (text, {size,bold,color,font}) runs. Font: the template's."""
    tb = slide.shapes.add_textbox(x, y, w, h)
    tb.name = name
    tf = tb.text_frame
    tf.word_wrap = True
    for k in ("margin_left", "margin_right", "margin_top", "margin_bottom"):
        setattr(tf, k, 0)
    for i, para in enumerate(paras if isinstance(paras, list) else [paras]):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.space_after = Pt(3)
        p.alignment = align
        for t, opt in (para if isinstance(para, list) else [(para, False)]):
            opt = {"bold": opt} if isinstance(opt, bool) else opt
            r = p.add_run(); r.text = str(t)
            r.font.size = Pt(opt.get("size", size)); r.font.bold = opt.get("bold", False)
            c = opt.get("color", color)
            if c is not None:
                r.font.color.rgb = c
            if opt.get("font"):
                r.font.name = opt["font"]
    return tb


def _box(slide, x, y, w, h, name):
    """White card with the manual's light border."""
    b = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, x, y, w, h)
    b.name = name
    b.adjustments[0] = 0.06
    b.fill.solid(); b.fill.fore_color.rgb = WHITE
    b.line.color.rgb = LINE; b.line.width = Pt(1)
    b.shadow.inherit = False
    return b


def _style_chart(chart, legend=True, number_format="0.00", axis_title: str | None = None):
    chart.font.size = Pt(11)
    chart.font.color.rgb = NAVY
    chart.has_legend = legend
    if legend:
        chart.legend.position = XL_LEGEND_POSITION.TOP
        chart.legend.include_in_layout = False
        chart.legend.font.size = Pt(11)
    va = chart.value_axis
    va.has_major_gridlines = True
    va.major_gridlines.format.line.color.rgb = LINE
    va.format.line.fill.background()
    va.tick_labels.font.size = Pt(10)
    va.tick_labels.font.color.rgb = MUTED
    va.tick_labels.number_format = number_format
    va.tick_labels.number_format_is_linked = False
    if axis_title:
        va.has_title = True
        va.axis_title.text_frame.text = axis_title
        f = va.axis_title.text_frame.paragraphs[0].runs[0].font
        f.size, f.bold, f.color.rgb = Pt(10), False, MUTED
    ca = chart.category_axis
    ca.tick_labels.font.size = Pt(10)
    ca.format.line.color.rgb = LINE
    ca.has_major_gridlines = False


def _labels(series, fmt: str):
    dl = series.data_labels
    dl.show_value = True
    dl.number_format = fmt
    dl.number_format_is_linked = False
    dl.font.size = Pt(10)
    dl.font.color.rgb = NAVY
    dl.position = XL_LABEL_POSITION.OUTSIDE_END


def _chart(deck, slide, kind, cd, name, h=None):
    L, T, R = deck.LEFT, deck.TOP, deck.RIGHT
    gf = slide.shapes.add_chart(kind, L, T - Inches(0.1), R - L, h or (deck.BOTTOM - T - Inches(0.2)), cd)
    gf.name = name
    return gf.chart


def _ms(us: float | None, d: int = 2) -> str:
    return "-" if us is None else f"{us / 1000:,.{d}f} ms"


def _table(slide, x, y, w, head, rows, widths, num_from=1, bold_cols=(), size=11, row_h=0.36, name="Table"):
    """A table styled like the manual's (template table style); numbers right-aligned from column num_from."""
    gf = slide.shapes.add_table(len(rows) + 1, len(head), x, y, w, Inches(row_h * (len(rows) + 1)))
    gf.name = name
    tbl = gf.table
    scale = w / sum(Inches(v) for v in widths)
    for i, v in enumerate(widths):
        tbl.columns[i].width = Emu(int(Inches(v) * scale))
    for j, h in enumerate(head):
        tbl.cell(0, j).text = h
    for i, row in enumerate(rows, start=1):
        for j, v in enumerate(row):
            tbl.cell(i, j).text = str(v)
    for i in range(len(rows) + 1):
        tbl.rows[i].height = Inches(row_h)
        for j in range(len(head)):
            c = tbl.cell(i, j)
            c.margin_left = c.margin_right = Inches(0.08); c.margin_top = c.margin_bottom = Inches(0.03)
            c.vertical_anchor = MSO_ANCHOR.MIDDLE
            for p in c.text_frame.paragraphs:
                p.alignment = PP_ALIGN.RIGHT if j >= num_from else PP_ALIGN.LEFT
                for run in p.runs:
                    run.font.size = Pt(size)
                    run.font.bold = i == 0 or j in bold_cols
    return tbl


def _arrow(slide, x, y, w, h, name):
    a = slide.shapes.add_shape(MSO_SHAPE.RIGHT_ARROW, x, y, w, h)
    a.name = name
    a.fill.solid(); a.fill.fore_color.rgb = RGBColor(0x9A, 0xA8, 0xB8); a.line.fill.background(); a.shadow.inherit = False
    return a


def _mount_summary(opts: str) -> list[str]:
    """'proto=rdma,port=20049,vers=3,nconnect=8,remoteports=a-b' -> ['NFSv3 over RDMA', 'nconnect=8', 'multipath: a-b']"""
    o = {}
    for part in str(opts or "").split(","):
        k, _, v = part.partition("=")
        o[k.strip()] = v.strip()
    out = []
    if o:
        out.append(f"NFSv{o.get('vers', '3')} over {'RDMA' if o.get('proto') == 'rdma' else (o.get('proto') or 'TCP').upper()}")
        if o.get("nconnect"):
            out.append(f"nconnect={o['nconnect']}")
        if o.get("remoteports"):
            out.append(f"multipath across VIPs {o['remoteports']}")
    return out


RED = RGBColor(0xE8, 0x44, 0x5A)       # fabric A, as in the usual VAST network drawings
BLUE = RGBColor(0x2B, 0xB7, 0xE3)      # fabric B
DBOX_MODELS = {"ceres_v2": "Ceres", "ceres": "Ceres", "mavericks": "Mavericks", "maverick": "Maverick"}


def _line(slide, x1, y1, x2, y2, color, name, w=1.75, dashed=False):
    c = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, int(x1), int(y1), int(x2), int(y2))
    c.line.color.rgb = color
    c.line.width = Pt(w)
    if dashed:
        c.line.dash_style = MSO_LINE.DASH
    c.name = name
    return c


def _device(slide, x, y, w, h, text, name, fill=NAVY):
    """A rack-unit style box (dark, like the hardware in the drawing) with a small white label."""
    b = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, int(x), int(y), int(w), int(h))
    b.name = name
    b.adjustments[0] = 0.12
    b.fill.solid(); b.fill.fore_color.rgb = fill
    b.line.color.rgb = RGBColor(0x5B, 0x67, 0x7A); b.line.width = Pt(0.75)
    b.shadow.inherit = False
    tf = b.text_frame
    for k in ("margin_left", "margin_right", "margin_top", "margin_bottom"):
        setattr(tf, k, Inches(0.04))
    tf.text = text
    p = tf.paragraphs[0]; p.alignment = PP_ALIGN.CENTER
    for r in p.runs:
        r.font.size = Pt(9 if h < Inches(0.34) else 10); r.font.bold = True; r.font.color.rgb = WHITE
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    return b


def topology_slide(deck, hw: dict, clients: list[dict], labels: dict | None = None):
    """Physical network diagram from the VMS inventory. Split design: clients -> 2 front-end switches -> CNodes ->
    2 back-end switches -> DBoxes. Converged design (EBoxes): clients -> 2 switches -> EBoxes."""
    labels = labels or {}
    L, R = deck.LEFT, deck.RIGHT
    conv = hw.get("design") == "converged"
    s = deck.content("Cluster Network Architecture",
                     ("Converged design - EBoxes, " if conv else "Split design - CNodes and DBoxes on separate fabrics, ")
                     + "dual fabric (A / B), from the VMS inventory")
    DW = Inches(8.1)                              # diagram width; labels on the right
    DX = L
    LX = DX + DW + Inches(0.35); LW = R - LX
    def lab(y, title, sub="", h=Inches(0.55)):
        _text(s, LX, y, LW, h, [[(title, {"bold": True, "size": 12, "color": NAVY})]] + ([[(sub, {"size": 10, "color": MUTED})]] if sub else []),
              size=11, name=f"Label {title[:20]}")
    gb = lambda sp: f"{sp}G" if sp else ""   # noqa: E731
    # legend (bottom right)
    gy = Inches(7.05)          # below the diagram, right of the footer logo
    _line(s, LX, gy, LX + Inches(0.4), gy, RED, "Legend A", 2.5)
    _text(s, LX + Inches(0.5), gy - Inches(0.1), Inches(1.1), Inches(0.25), [[("Fabric A", {"size": 10})]], name="Legend A text")
    _line(s, LX + Inches(1.5), gy, LX + Inches(1.9), gy, BLUE, "Legend B", 2.5)
    _text(s, LX + Inches(2.0), gy - Inches(0.1), Inches(1.1), Inches(0.25), [[("Fabric B", {"size": 10})]], name="Legend B text")
    if not conv:
        _line(s, LX, gy - Inches(0.27), LX + Inches(0.4), gy - Inches(0.27), MUTED, "Legend back-end", 2, dashed=True)
        _text(s, LX + Inches(0.5), gy - Inches(0.37), LW - Inches(0.5), Inches(0.25),
              [[("dashed = back-end (CNodes ↔ DBoxes)", {"size": 10})]], name="Legend back-end text")

    # row 1: clients
    shown = clients[:6]
    n_cl = max(1, len(shown))
    cw = min(Inches(1.7), int((DW - Inches(0.3) * (n_cl - 1)) / n_cl))
    gap = (DW - cw * n_cl) / max(1, n_cl - 1) if n_cl > 1 else 0
    y_cl, h_cl = Inches(1.95), Inches(0.38)
    cxs = []
    for i, c in enumerate(shown or [{"name": "client"}]):
        x = DX + (i * (cw + gap) if n_cl > 1 else (DW - cw) / 2)
        _device(s, x, y_cl, cw, h_cl, c.get("name", ""), f"Client {i + 1}")
        cxs.append(x + cw / 2)
    more = f" (+{len(clients) - 6} more)" if len(clients) > 6 else ""
    lab(y_cl, f"Test clients ×{len(clients) or 1}{more}", "each client on both fabrics")

    # row 2: (front-end) switches A / B
    sw_w, sw_h = Inches(2.5), Inches(0.36)
    y_fs = Inches(2.75) if not conv else Inches(2.9)
    ax, bx = DX + DW * 0.25 - sw_w / 2, DX + DW * 0.75 - sw_w / 2
    fs_name = labels.get("front_switch") or (f"{gb(hw.get('fe_speed'))} switch".strip() if hw.get("fe_speed") else "Switch")
    _device(s, ax, y_fs, sw_w, sw_h, f"{fs_name} A", "Switch A", fill=RGBColor(0x2A, 0x33, 0x47))
    _device(s, bx, y_fs, sw_w, sw_h, f"{fs_name} B", "Switch B", fill=RGBColor(0x2A, 0x33, 0x47))
    for i, cx in enumerate(cxs):
        _line(s, cx - Inches(0.12), y_cl + h_cl, ax + sw_w / 2, y_fs, RED, f"Client {i + 1} to A")
        _line(s, cx + Inches(0.12), y_cl + h_cl, bx + sw_w / 2, y_fs, BLUE, f"Client {i + 1} to B")
    lab(y_fs - Inches(0.05), f"{fs_name} ×2" + ("" if conv else " (front-end)"),
        "client and VIP traffic" if not conv else "one converged network: clients, CNodes and DNodes")

    # row 3: CNodes (split) or EBoxes (converged), stacked
    n_nodes = (hw.get("eboxes") if conv else hw.get("cnodes")) or 1
    stack_n = min(n_nodes, 8)
    nh = min(Inches(0.3), int(Inches(1.35) / stack_n))
    nw = Inches(3.0)
    nx = DX + (DW - nw) / 2
    y_n = (y_fs + sw_h + Inches(0.5))
    ys = []
    for i in range(stack_n):
        y = y_n + i * (nh + Inches(0.05))
        txt = ("EBox" if conv else "CNode") + f" {i + 1}"
        if i == stack_n - 1 and n_nodes > stack_n:
            txt = f"… {n_nodes - stack_n + 1} more"
        _device(s, nx, y, nw, nh, txt, f"{'EBox' if conv else 'CNode'} {i + 1}")
        ys.append(y + nh / 2)
    # front-end links: switch A -> left side of each node, switch B -> right side (elbows)
    ayx, byx = ax + sw_w / 2, bx + sw_w / 2
    _line(s, ayx, y_fs + sw_h, ayx, ys[-1], RED, "A trunk")
    _line(s, byx, y_fs + sw_h, byx, ys[-1], BLUE, "B trunk")
    for i, y in enumerate(ys):
        _line(s, ayx, y, nx, y, RED, f"A to node {i + 1}")
        _line(s, byx, y, nx + nw, y, BLUE, f"B to node {i + 1}")
    if hw.get("fe_speed"):
        _text(s, ayx - Inches(1.05), y_fs + sw_h + Inches(0.08), Inches(1.0), Inches(0.3), [[(gb(hw["fe_speed"]), {"size": 10, "bold": True})]],
              align=PP_ALIGN.RIGHT, name="FE speed A")
        _text(s, byx + Inches(0.05), y_fs + sw_h + Inches(0.08), Inches(1.0), Inches(0.3), [[(gb(hw["fe_speed"]), {"size": 10, "bold": True})]],
              name="FE speed B")
    if conv:
        lab(y_n, f"VAST EBox ×{n_nodes}", f"CNode + DNode in one box · {hw.get('fe_ports') or 2}× {gb(hw.get('fe_speed'))} per box".strip(" ·"))
        return s
    ports = " · ".join(x for x in [f"{hw['fe_ports']}× {gb(hw.get('fe_speed'))} front-end" if hw.get("fe_ports") else "",
                                   f"{hw['be_ports']}× {gb(hw.get('be_speed'))} back-end" if hw.get("be_ports") else ""] if x)
    lab(y_n, f"VAST CNode ×{n_nodes}", (f"{hw['cboxes']} CBox{'es' if hw.get('cboxes') != 1 else ''} · " if hw.get("cboxes") else "") + ports)

    # row 4: back-end switches
    y_bs = ys[-1] + nh / 2 + Inches(0.55)
    bs_name = labels.get("back_switch") or (f"{gb(hw.get('be_speed'))} switch".strip() if hw.get("be_speed") else "Switch")
    _device(s, ax, y_bs, sw_w, sw_h, f"{bs_name} A", "Back-end switch A", fill=RGBColor(0x2A, 0x33, 0x47))
    _device(s, bx, y_bs, sw_w, sw_h, f"{bs_name} B", "Back-end switch B", fill=RGBColor(0x2A, 0x33, 0x47))
    # CNode back-end links: from the node edges, a little outside the front-end trunks
    lx_, rx_ = nx - Inches(0.25), nx + nw + Inches(0.25)
    for i, y in enumerate(ys):
        _line(s, nx, y + Inches(0.06), lx_, y + Inches(0.06), RED, f"Node {i + 1} back A", dashed=True)
        _line(s, nx + nw, y + Inches(0.06), rx_, y + Inches(0.06), BLUE, f"Node {i + 1} back B", dashed=True)
    _line(s, lx_, ys[0] + Inches(0.06), lx_, y_bs, RED, "Back A trunk", dashed=True)
    _line(s, rx_, ys[0] + Inches(0.06), rx_, y_bs, BLUE, "Back B trunk", dashed=True)
    lab(y_bs - Inches(0.05), f"{bs_name} ×2 (back-end)", "NVMe-oF between CNodes and DBoxes")

    # row 5: DBoxes
    n_db = hw.get("dboxes") or 1
    show_db = min(n_db, 4)
    dw, dh = Inches(2.6), Inches(0.42)
    tot = show_db * dw + (show_db - 1) * Inches(0.3)
    y_db = y_bs + sw_h + Inches(0.45)
    for i in range(show_db):
        x = DX + (DW - tot) / 2 + i * (dw + Inches(0.3))
        model = labels.get("dbox_model") or DBOX_MODELS.get(hw.get("dbox_model", ""), str(hw.get("dbox_model") or "").replace("_", " ").title())
        _device(s, x, y_db, dw, dh, (f"VAST {model}" if model else "DBox") + (f" {i + 1}" if n_db > 1 else ""), f"DBox {i + 1}")
        _line(s, ax + sw_w / 2, y_bs + sw_h, ax + sw_w / 2, y_db + dh / 2, RED, f"A to DBox {i + 1}", dashed=True) if i == 0 else None
        _line(s, bx + sw_w / 2, y_bs + sw_h, bx + sw_w / 2, y_db + dh / 2, BLUE, f"B to DBox {i + 1}", dashed=True) if i == 0 else None
        _line(s, ax + sw_w / 2, y_db + dh / 2, x, y_db + dh / 2, RED, f"A into DBox {i + 1}", dashed=True)
        _line(s, bx + sw_w / 2, y_db + dh / 2, x + dw, y_db + dh / 2, BLUE, f"B into DBox {i + 1}", dashed=True)
    model = labels.get("dbox_model") or DBOX_MODELS.get(hw.get("dbox_model", ""), str(hw.get("dbox_model") or "DBox"))
    lab(y_db - Inches(0.05), f"VAST {model} ×{n_db}" if model else f"DBox ×{n_db}",
        " · ".join(x for x in [f"{hw['dnodes']} DNodes" if hw.get("dnodes") else "",
                               f"{hw['d_ports']}× {gb(hw.get('d_speed'))} per DNode" if hw.get("d_ports") else ""] if x))
    return s


def expected_checks(expected: dict | None, best: dict, tp_rows: list[dict], lat_any: dict, unit: str) -> list[tuple]:
    """Targets vs measured: (metric, expected text, actual text, ratio, met, expected value, actual value, chartable)."""
    exp = {k: v for k, v in (expected or {}).items() if v not in (None, "")}
    checks: list[tuple] = []
    def _f(k):
        try:
            return float(exp[k])
        except (KeyError, TypeError, ValueError):
            return None
    for op, key in (("READ", "read_gbps"), ("WRITE", "write_gbps")):
        e, r = _f(key), best.get(op)
        if e and r:
            a = r["tp_last"] * MIB / 1e9
            checks.append((f"{op.title()} throughput", f"{_fmt(e * 1e9 / UNITS[unit])} {unit}/s", f"{_fmt(_tp(r['tp_last'], unit))} {unit}/s",
                           a / e, a >= e, e * 1e9 / UNITS[unit], _tp(r["tp_last"], unit), True))
    for op, key in (("READ", "read_iops"), ("WRITE", "write_iops")):
        e = _f(key)
        r = max((x for x in tp_rows if x["operation"] == op and x["iops_last"]), key=lambda x: x["iops_last"], default=None)
        if e and r:
            checks.append((f"{op.title()} IOPS", f"{e:,.0f}", f"{r['iops_last']:,.0f}", r["iops_last"] / e, r["iops_last"] >= e,
                           e, r["iops_last"], False))
    e = _f("p99_ms")
    if e and (lat_any or {}).get("p99") is not None:
        a = lat_any["p99"] / 1000
        checks.append(("P99 latency (lower is better)", f"≤ {e:,.2f} ms", f"{a:,.2f} ms", e / a if a else 0, a <= e, e, a, False))
    return checks


def cluster_compare(tp_rows: list[dict], cluster: dict | None) -> list[dict]:
    """Per run: what the cluster served (VMS 10 s samples) - whole-run average (comparable with the clients),
    sustained (>= 50% of peak) and peak bandwidth, IOPS and latency."""
    cl_runs = (cluster or {}).get("runs") or {}
    out = []
    def _ts(v):
        try:
            return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        except ValueError:
            return None
    for r in tp_rows:
        smp = cl_runs.get(r["run_id"]) or []
        k = "rd" if r["operation"] == "READ" else "wr"
        if not [x for x in smp if x.get(f"{k}_bw") is not None]:
            continue
        w = r.get("window")
        # 10-second samples whose interval lies inside the benchmark command's run time (timestamps = interval end)
        inside = [x for x in smp if w and _ts(x["t"]) and w[0] + timedelta(seconds=5) < _ts(x["t"]) <= w[1] + timedelta(seconds=5)] or smp
        bw = [x[f"{k}_bw"] or 0 for x in inside]
        whole = sum(bw) / len(bw)
        top = max(bw)
        steady = [x for x in inside if (x.get(f"{k}_bw") or 0) >= 0.5 * top] or inside
        sustained = sum(x[f"{k}_bw"] or 0 for x in steady) / len(steady)
        iops = [x.get(f"{k}_iops") for x in steady if x.get(f"{k}_iops") is not None]
        lat = [x.get(f"{k}_lat") for x in steady if x.get(f"{k}_lat") is not None]
        out.append({"r": r, "whole": whole, "sustained": sustained, "peak": top, "n": len(inside),
                    "iops": sum(iops) / len(iops) if iops else None, "lat": sum(lat) / len(lat) if lat else None})
    return out


def prepare(runs: list[dict]) -> dict:
    """The data both reports use: read/write rows (sorted), best runs, peak IOPS, latency, clients, period."""
    rows = [r for run in runs for r in rows_for(run) if r["operation"].upper() in PERF_OPS]
    rows.sort(key=lambda r: (r["started"] or datetime.min, r["index"] or 0))
    tp_rows = [r for r in rows if r["tp_last"] is not None]
    if not tp_rows:
        raise ValueError("The selected runs have no elbencho throughput results - run Elbencho Write / Read first.")
    starts = [r["started"] for r in rows if r["started"]]
    period = ""
    if starts:
        a, b = min(starts), max(starts)
        period = a.strftime("%d %b %Y %H:%M") + ("" if a == b else " – " + (b.strftime("%H:%M") if a.date() == b.date()
                                                                            else b.strftime("%d %b %Y %H:%M")))
    best = {op: max((r for r in tp_rows if r["operation"] == op), key=lambda r: r["tp_last"], default=None) for op in ("READ", "WRITE")}
    lat_read = (best["READ"] or {}).get("lat") or {}
    return {"rows": rows, "tp_rows": tp_rows, "period": period, "best": best,
            "all_clients": sorted({c for r in rows for c in r["clients"]}),
            "peak_iops": max((r for r in rows if r["iops_last"]), key=lambda r: r["iops_last"], default=None),
            "lat_any": lat_read or next((r["lat"] for r in tp_rows if r.get("lat")), None) or {}}


# ------------------------------------------------------------------ the deck

def build(runs: list[dict], out: Path, *, site: str, unit: str = "GB", vms: str = "", tenant: dict | None = None,
          clients: list[dict] | None = None, mount: dict | None = None, version: str = "",
          expected: dict | None = None, cluster: dict | None = None, arch: dict | None = None,
          labels: dict | None = None) -> dict:
    """runs: elbencho run dicts (the .json files). Writes out (.pptx) and out.with_suffix('.md'). Returns a summary.
    expected: targets {read_gbps, write_gbps, read_iops, write_iops, p99_ms, basis} for Estimated vs Actual.
    cluster: {"info": {name, version, cnodes, dboxes}, "runs": {run_id: [sample, ...]}} from the VMS analytics API,
             sample = {t, rd_bw, wr_bw (bytes/s), rd_iops, wr_iops, rd_lat, wr_lat (µs)}.
    arch: {vip_pool: {name, ips}, view_path, protocols} for the architecture page."""
    unit = unit if unit in UNITS else "GB"
    tenant = tenant or {}
    # only the read / write phases of the benchmark (no MKDIRS / delete phases, no other commands)
    rows = [r for run in runs for r in rows_for(run) if r["operation"].upper() in PERF_OPS]
    rows.sort(key=lambda r: (r["started"] or datetime.min, r["index"] or 0))
    tp_rows = [r for r in rows if r["tp_last"] is not None]
    if not tp_rows:
        raise ValueError("The selected runs have no elbencho throughput results - run Elbencho Write / Read first.")
    starts = [r["started"] for r in rows if r["started"]]
    period = ""
    if starts:
        a, b = min(starts), max(starts)
        period = a.strftime("%d %b %Y %H:%M") + ("" if a == b else " – " + (b.strftime("%H:%M") if a.date() == b.date()
                                                                            else b.strftime("%d %b %Y %H:%M")))
    all_clients = sorted({c for r in rows for c in r["clients"]})
    best = {op: max((r for r in tp_rows if r["operation"] == op), key=lambda r: r["tp_last"], default=None)
            for op in ("READ", "WRITE")}
    peak_iops = max((r for r in rows if r["iops_last"]), key=lambda r: r["iops_last"], default=None)
    nr, nc = len(runs), len(all_clients)
    counts = f"{nr} run{'s' if nr != 1 else ''} · {nc} client{'s' if nc != 1 else ''}"

    deck = Deck()
    L, T, R, B = deck.LEFT, deck.TOP, deck.RIGHT, deck.BOTTOM
    W = R - L

    # 1. cover
    meta = "   ·   ".join(x for x in [f"Tenant {tenant.get('tenant_name')}" if tenant.get("tenant_name") else "",
                                      period, counts] if x)
    s = deck.cover("Performance Report", site, meta)
    s.notes_slide.notes_text_frame.text = (f"Generated {datetime.now().strftime('%d %b %Y %H:%M')} by VAST Test Bench"
                                           + (f" v{version}" if version else "") + (f" · VMS {vms}" if vms else ""))

    # page 2: physical network architecture from the VMS inventory (split or converged)
    arch = arch or {}
    info = (cluster or {}).get("info") or {}
    hw = info.get("hw") or {}
    if hw.get("cnodes") or hw.get("eboxes"):
        used_cl = [c for c in (clients or []) if not all_clients or c.get("name") in all_clients] or [{"name": c} for c in all_clients]
        s = topology_slide(deck, hw, used_cl, labels)
        s.notes_slide.notes_text_frame.text = (
            "Drawn from the VMS inventory when the report was generated: GET /cboxes/, /cnodes/, /dboxes/, /dnodes/, /eboxes/ "
            "(box counts and DBox model) and /nics/ (port type and link rate per node). Switch names come from the report "
            "window; the VMS does not list the switches. Red = fabric A, blue = fabric B.")

    # architecture: clients -> network / mount -> VIP pool -> VAST cluster (tenant, view)
    s = deck.content("Test Data Path", "How the test clients reach the tenant's data - mount, VIP pool, cluster, view")
    used = [c for c in (clients or []) if not all_clients or c.get("name") in all_clients][:8] or \
        [{"name": c} for c in all_clients[:8]]
    bh, bg = Inches(0.62), Inches(0.16)
    colh = len(used) * bh + (len(used) - 1) * bg
    cy0 = T + Inches(0.35) + max(0, (Inches(3.9) - colh) // 2)
    _text(s, L, T - Inches(0.05), Inches(2.6), Inches(0.3), [[(f"Test clients ({len(used)})", True)]], size=12, color=NAVY,
          name="Clients title")
    for i, c in enumerate(used):
        y = cy0 + i * (bh + bg)
        _box(s, L, y, Inches(2.6), bh, f"Client {i + 1}")
        _text(s, L + Inches(0.15), y + Inches(0.08), Inches(2.35), bh - Inches(0.1),
              [[(c.get("name", ""), True)], [(f"{c.get('host', '')}", {"size": 10, "color": MUTED})]], size=12, color=NAVY,
              name=f"Client {i + 1} text")
    mid = cy0 + colh // 2
    ax = L + Inches(2.75)
    _arrow(s, ax, mid - Inches(0.2), Inches(1.55), Inches(0.4), "Arrow clients to VIPs")
    net = _mount_summary((mount or {}).get("options", "")) or ["NFS"]
    _text(s, ax - Inches(0.05), mid - Inches(1.25), Inches(1.7), Inches(1.0), [[(x, {"size": 10})] for x in net[:2]], size=10,
          color=NAVY, align=PP_ALIGN.CENTER, name="Network label")
    if len(net) > 2:
        _text(s, ax - Inches(0.05), mid + Inches(0.3), Inches(1.7), Inches(0.8), [[(net[2], {"size": 10})]], size=10, color=MUTED,
              align=PP_ALIGN.CENTER, name="Multipath label")
    vp = arch.get("vip_pool") or {}
    ips = list(vp.get("ips") or [])
    vx = ax + Inches(1.7)
    _box(s, vx, mid - Inches(0.95), Inches(2.3), Inches(1.9), "VIP pool box")
    _text(s, vx + Inches(0.18), mid - Inches(0.8), Inches(2.0), Inches(1.7),
          [[("VIP pool", True)], [(vp.get("name") or "-", {"size": 11})],
           [(f"{ips[0]} – {ips[-1]}" if ips else "-", {"size": 11, "color": TEAL})],
           [(f"{len(ips)} virtual IPs" if ips else "", {"size": 10, "color": MUTED})]], size=13, color=NAVY, name="VIP pool text")
    ax2 = vx + Inches(2.4)
    _arrow(s, ax2, mid - Inches(0.2), Inches(0.75), Inches(0.4), "Arrow VIPs to cluster")
    kx = ax2 + Inches(0.9)
    kw = R - kx
    lines = [[("VAST cluster", {"bold": True, "size": 15, "color": NAVY})]]
    if info.get("name"):
        lines.append([(info["name"], {"size": 12, "color": TEAL})])
    if info.get("version"):
        lines.append([("VAST OS ", {"color": MUTED}), (".".join(str(info["version"]).split(".")[:4]), {})])
    pl = lambda n, one, many: f"{n} {one if n == 1 else many}"  # noqa: E731
    hw = " · ".join(x for x in [pl(info["cnodes"], "CNode", "CNodes") if info.get("cnodes") else "",
                                pl(info["dboxes"], "DBox", "DBoxes") if info.get("dboxes") else ""] if x)
    if hw:
        lines.append([(hw, {})])
    lines.append("")
    lines.append([("Tenant ", {"color": MUTED}), (tenant.get("tenant_name") or "-", {"bold": True})])
    vpath = arch.get("view_path") or tenant.get("view_path")
    if vpath:
        prot = arch.get("protocols") or tenant.get("view_protocols")
        lines.append([("View ", {"color": MUTED}), (str(vpath), {}),
                      (f"  ({' + '.join(prot) if isinstance(prot, list) else prot})" if prot else "", {"color": MUTED})])
    for k, lbl in (("qos_read_limit", "QoS read limit"), ("qos_write_limit", "QoS write limit"), ("quota_hard_limit", "Quota")):
        if tenant.get(k):
            lines.append([(f"{lbl} ", {"color": MUTED}), (str(tenant[k]), {})])
    kh = min(Inches(4.5), Inches(0.5) + Inches(0.3) * len(lines))
    ky = max(T, mid - kh // 2)
    _box(s, kx, ky, kw, kh, "Cluster box")
    _text(s, kx + Inches(0.25), ky + Inches(0.2), kw - Inches(0.45), kh - Inches(0.3), lines, size=12, name="Cluster text")
    s.notes_slide.notes_text_frame.text = ("Cluster details are read from the VMS when the report is generated "
                                           "(GET /clusters/, /cnodes/, /dboxes/).")

    # 2. highlights
    s = deck.content("Performance Highlights", f"Peak results · {counts} · summed across clients, all threads done")
    cards = []
    for op, lbl in (("READ", "Peak read"), ("WRITE", "Peak write")):
        r = best[op]
        if r:
            cards.append((lbl, _fmt(_tp(r["tp_last"], unit)), f"{unit}/s", f"{r['label']} · {len(r['clients'])} client"
                          f"{'s' if len(r['clients']) != 1 else ''}"))
    if peak_iops:
        cards.append(("Peak IOPS", f"{peak_iops['iops_last']:,.0f}", "", f"{peak_iops['operation'].title()} · {peak_iops['label']}"))
    lat_read = (best["READ"] or {}).get("lat") or {}
    lat_any = lat_read or next((r["lat"] for r in tp_rows if r.get("lat")), None) or {}
    if lat_any.get("p99") is not None:
        op_l = "read" if lat_read else next(r["operation"].lower() for r in tp_rows if r.get("lat"))
        cards.append((f"P99 latency ({op_l})", f"{lat_any['p99'] / 1000:,.2f}", "ms",
                      f"avg {_ms(lat_any.get('avg'))} · worst client"))
    else:
        cards.append(("Runs · clients", f"{nr} · {nc}", "", ", ".join(all_clients)[:70]))
    gap = Inches(0.3)
    cw = int((W - gap * (len(cards) - 1)) / len(cards))
    for i, (lbl, val, u, sub) in enumerate(cards):
        x = L + i * (cw + gap)
        _box(s, x, T, cw, Inches(2.3), f"Card {i + 1}")
        circ = s.shapes.add_shape(MSO_SHAPE.OVAL, x + Inches(0.25), T + Inches(0.28), Inches(0.3), Inches(0.3))
        circ.name = f"Card {i + 1} number"
        circ.fill.solid(); circ.fill.fore_color.rgb = ORANGE; circ.line.fill.background(); circ.shadow.inherit = False
        circ.text_frame.text = str(i + 1)
        for k in ("margin_left", "margin_right", "margin_top", "margin_bottom"):
            setattr(circ.text_frame, k, 0)
        cp = circ.text_frame.paragraphs[0]; cp.alignment = PP_ALIGN.CENTER
        cp.runs[0].font.size = Pt(11); cp.runs[0].font.bold = True; cp.runs[0].font.color.rgb = WHITE
        _text(s, x + Inches(0.68), T + Inches(0.3), cw - Inches(0.85), Inches(0.35), [[(lbl, True)]], size=13, color=NAVY,
              name=f"Card {i + 1} label")
        _text(s, x + Inches(0.25), T + Inches(0.85), cw - Inches(0.4), Inches(0.8),
              [[(val, {"size": 36, "bold": True, "color": NAVY, "font": TITLE_FONT}),
                ((" " + u) if u else "", {"size": 16, "color": TEAL, "font": TITLE_FONT})]], name=f"Card {i + 1} value")
        _text(s, x + Inches(0.25), T + Inches(1.7), cw - Inches(0.4), Inches(0.5), sub, size=11, color=MUTED,
              name=f"Card {i + 1} note")
    deck.note(s, [f"Throughput and IOPS are summed across the clients that ran the test together, measured when the last "
                  f"thread finished (\"last done\").",
                  f"Decimal units: 1 {unit}/s = {({'MB': '1,000,000 bytes/s', 'GB': '1,000 MB/s', 'TB': '1,000 GB/s'})[unit]}"
                  " - elbencho reports MiB/s, converted here."],
              L, T + Inches(2.65), W, Inches(1.05), title="How to read these numbers:")

    # estimated vs actual (targets typed in the report window, saved per site)
    checks = expected_checks(expected, best, tp_rows, lat_any, unit)
    if checks:
        s = deck.content("Estimated vs Actual Performance", "Expected results (sizing / POC success criteria) against what the test measured")
        tp_checks = [c for c in checks if c[7]]
        tw = W
        if tp_checks:
            cd = CategoryChartData()
            cd.categories = [c[0] for c in tp_checks]
            cd.add_series("Expected", [round(c[5], 3) for c in tp_checks])
            cd.add_series("Actual", [round(c[6], 3) for c in tp_checks])
            gf = s.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, L, T - Inches(0.1), Inches(5.2), Inches(4.3), cd)
            gf.name = "Expected vs actual chart"
            ch = gf.chart
            _style_chart(ch, axis_title=f"{unit}/s")
            ch.plots[0].gap_width, ch.plots[0].overlap = 70, -10
            for i, ser in enumerate(ch.plots[0].series):
                ser.format.fill.solid(); ser.format.fill.fore_color.rgb = LIGHT_TEAL if i == 0 else TEAL
                _labels(ser, "0.00")
            tw = W - Inches(5.5)
        tx = R - tw
        rows_t = [(c[0], c[1], c[2], f"{c[3] * 100:,.0f}%", "✓ Met" if c[4] else ("≈ Close" if c[3] >= 0.9 else "✗ Below"))
                  for c in checks]
        _table(s, tx, T, tw, ["Metric", "Expected", "Actual", "% of target", "Result"], rows_t,
               [2.4, 1.4, 1.4, 1.1, 1.0], num_from=1, bold_cols=(2,), size=11, row_h=0.42)
        met = sum(1 for c in checks if c[4])
        basis = str((expected or {}).get("basis") or "").strip()
        deck.note(s, [f"{met} of {len(checks)} targets met. Actual = best run, summed across clients, all threads done; "
                      "P99 = the worst client's P99 IO latency."] + ([f"Expected values: {basis}"] if basis else []),
                  tx, T + Inches(0.42 * (len(checks) + 1)) + Inches(0.3), tw, Inches(1.25), title="Summary:")

    # 3. throughput per run - chart, then time taken and latency per run
    s = deck.content("Throughput per Run", f"{unit}/s summed across clients · dark: all threads done · light: first thread done")
    cd = CategoryChartData()
    cd.categories = [_cat(r) for r in tp_rows]
    cd.add_series("All threads done (last done)", [round(_tp(r["tp_last"], unit), 3) for r in tp_rows])
    cd.add_series("First thread done", [round(_tp(r["tp_first"], unit) or 0, 3) for r in tp_rows])
    n_t = min(len(tp_rows), 6)
    tbl_h = Inches(0.34 * (n_t + 1))
    ch = _chart(deck, s, XL_CHART_TYPE.COLUMN_CLUSTERED, cd, "Throughput chart", h=B - T - tbl_h - Inches(0.25))
    _style_chart(ch, axis_title=f"{unit}/s")
    ch.plots[0].gap_width, ch.plots[0].overlap = 60, -10
    for i, ser in enumerate(ch.plots[0].series):
        ser.format.fill.solid(); ser.format.fill.fore_color.rgb = TEAL if i == 0 else LIGHT_TEAL
        _labels(ser, "0.00")
    _table(s, L, B - tbl_h, W, ["Run", f"{unit}/s (all done)", "Time taken", "IOPS", "Avg latency", "P99 latency", "P99.9 latency"],
           [(_cat(r) + f" · {r['label']}", _fmt(_tp(r["tp_last"], unit)), f"{r['elapsed']:,.1f} s" if r["elapsed"] is not None else "-",
             _fmt(r["iops_last"], 0), _ms((r["lat"] or {}).get("avg")), _ms((r["lat"] or {}).get("p99")),
             _ms((r["lat"] or {}).get("p99.9"))) for r in tp_rows[:n_t]],
           [3.2, 1.4, 1.2, 1.1, 1.2, 1.2, 1.3], num_from=1, bold_cols=(1, 5), size=10, row_h=0.34, name="Run table")
    s.notes_slide.notes_text_frame.text = ("Quote the dark bars: throughput when every thread had finished. Time taken = the "
                                           "slowest client's elapsed time. Latency = IO latency from elbencho --lat --latpercent; "
                                           "P99 / P99.9 are the worst client's values.")

    # cluster performance: what the VMS measured during each run
    cmp_rows = cluster_compare(tp_rows, cluster)
    if cmp_rows:
        s = deck.content("Cluster Performance", "What the VAST cluster served during each run (VMS analytics) next to what the clients reported")
        cd = CategoryChartData()
        cd.categories = [_cat(x["r"]) for x in cmp_rows]
        cd.add_series("Clients reported", [round(_tp(x["r"]["tp_last"], unit), 3) for x in cmp_rows])
        cd.add_series("Cluster avg (whole run)", [round(x["whole"] / UNITS[unit], 3) for x in cmp_rows])
        cd.add_series("Cluster sustained", [round(x["sustained"] / UNITS[unit], 3) for x in cmp_rows])
        shown = cmp_rows[:6]
        tbl_h = Inches(0.34 * (len(shown) + 1))
        flags = [x for x in cmp_rows if x["sustained"] > 1.3 * x["whole"]]
        gaps = [x for x in cmp_rows if abs(x["whole"] / UNITS[unit] / _tp(x["r"]["tp_last"], unit) - 1) > 0.15]
        note_w = Inches(3.9) if (flags or gaps) else 0
        gf = s.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, L, T - Inches(0.1), W - note_w - (Inches(0.25) if note_w else 0),
                                B - T - tbl_h - Inches(0.2), cd)
        gf.name = "Cluster chart"
        ch = gf.chart
        _style_chart(ch, axis_title=f"{unit}/s")
        ch.plots[0].gap_width, ch.plots[0].overlap = 60, -10
        for i, ser in enumerate(ch.plots[0].series):
            ser.format.fill.solid(); ser.format.fill.fore_color.rgb = (TEAL, NAVY, LIGHT_TEAL)[i]
            _labels(ser, "0.00")
        ty = B - tbl_h
        _table(s, L, ty, W, ["Run", f"Clients {unit}/s", f"Cluster avg {unit}/s", f"Sustained {unit}/s", f"Peak {unit}/s",
                             "Cluster IOPS", "Cluster latency", "Difference"],
               [(_cat(x["r"]) + f" · {x['r']['label']}", _fmt(_tp(x["r"]["tp_last"], unit)), _fmt(x["whole"] / UNITS[unit]),
                 _fmt(x["sustained"] / UNITS[unit]), _fmt(x["peak"] / UNITS[unit]), _fmt(x["iops"], 0), _ms(x["lat"]),
                 f"{(x['whole'] / UNITS[unit] / _tp(x['r']['tp_last'], unit) - 1) * 100:+.1f}%") for x in shown],
               [3.0, 1.2, 1.3, 1.2, 1.1, 1.1, 1.2, 1.0], num_from=1, bold_cols=(2,), size=10, row_h=0.34, name="Cluster table")
        if note_w:
            lines = []
            for x in cmp_rows:
                d = x["whole"] / UNITS[unit] / _tp(x["r"]["tp_last"], unit) - 1
                if x in gaps:
                    lines.append(f"{_cat(x['r'])}: clients and cluster differ by {d * 100:+.0f}% - check other load on the "
                                 "cluster or client-side caching.")
                elif x in flags:
                    lines.append(f"{_cat(x['r'])}: slow start or tail - sustained {_fmt(x['sustained'] / UNITS[unit])} {unit}/s, "
                                 f"{_fmt(x['whole'] / UNITS[unit])} {unit}/s over the whole run.")
            deck.note(s, lines[:4], R - note_w, T, note_w, B - T - tbl_h - Inches(0.3), title="Worth a look:")
        s.notes_slide.notes_text_frame.text = (
            "Cluster figures come from the VMS analytics API (GET /monitors/ad_hoc_query/, object_type=cluster, "
            "ProtoMetrics ProtoCommon rd_bw / wr_bw / iops / latency, 10-second samples) for the time each benchmark command ran. "
            "Cluster avg = all samples in that time (comparable with the clients' figure, which also covers the whole run). "
            "Sustained = samples at 50% of the run's peak or more (ramp-up and tail left out). Cluster latency is measured "
            "inside the cluster, so it is lower than the client-side latency. Other load on the cluster is included.")

    # latency per run (elbencho --lat --latpercent)
    lat_rows = [r for r in tp_rows if r.get("lat")]
    if lat_rows:
        s = deck.content("Latency", "IO latency per run · percentiles are the worst client's values · milliseconds")
        cd = CategoryChartData()
        cd.categories = [_cat(r) for r in lat_rows]
        for key, nm in (("p50", "P50 (median)"), ("p99", "P99"), ("p99.9", "P99.9")):
            cd.add_series(nm, [round((r["lat"].get(key) or 0) / 1000, 3) for r in lat_rows])
        per = [(r, c, l) for r in lat_rows for c, l in sorted(r["lat_clients"].items()) if l]
        n_t = min(len(per), 6)
        tbl_h = Inches(0.32 * (n_t + 1))
        ch = _chart(deck, s, XL_CHART_TYPE.COLUMN_CLUSTERED, cd, "Latency chart", h=B - T - tbl_h - Inches(0.25))
        _style_chart(ch, number_format="0.00", axis_title="ms")
        ch.plots[0].gap_width, ch.plots[0].overlap = 60, -10
        for i, ser in enumerate(ch.plots[0].series):
            ser.format.fill.solid(); ser.format.fill.fore_color.rgb = (LIGHT_TEAL, TEAL, ORANGE)[i]
            _labels(ser, "0.00")
        _table(s, L, B - tbl_h, W, ["Run · client", "Min", "Avg", "P50", "P99", "P99.9", "Max"],
               [(f"{_cat(r)} · {c}", _ms(l.get("min")), _ms(l.get("avg")), _ms(l.get("p50")), _ms(l.get("p99")),
                 _ms(l.get("p99.9")), _ms(l.get("max"))) for r, c, l in per[:n_t]],
               [3.4, 1.1, 1.1, 1.1, 1.1, 1.1, 1.1], num_from=1, bold_cols=(4,), size=10, row_h=0.32, name="Latency table")
        s.notes_slide.notes_text_frame.text = ("Client-side IO latency per block (elbencho --lat --latpercent --latpercent9s). "
                                               "Chart: P50 / P99 / P99.9 of the worst client per run. Table: each client.")

    # 4. IOPS per run
    io_rows = [r for r in tp_rows if r["iops_last"]]
    if io_rows:
        s = deck.content("IOPS per Run", "Summed across clients · all threads done")
        cd = CategoryChartData()
        cd.categories = [_cat(r) for r in io_rows]
        cd.add_series("IOPS (last done)", [r["iops_last"] for r in io_rows])
        ch = _chart(deck, s, XL_CHART_TYPE.COLUMN_CLUSTERED, cd, "IOPS chart")
        _style_chart(ch, legend=False, number_format="#,##0", axis_title="IOPS")
        ch.has_title = False
        ch.plots[0].gap_width = 80
        ser = ch.plots[0].series[0]
        ser.format.fill.solid(); ser.format.fill.fore_color.rgb = ORANGE
        _labels(ser, "#,##0")
        bs = sorted({str(r["command"]).split(" -b ")[1].split()[0] for r in io_rows if " -b " in str(r["command"])})
        s.notes_slide.notes_text_frame.text = "IOPS summed across clients when all threads finished." + (
            f" Block size: {', '.join(bs)}." if bs else "")

    # 5. per-client throughput
    pc_rows = [r for r in tp_rows if r["per_client"]]
    names = sorted({c for r in pc_rows for c in r["per_client"]})
    if pc_rows and names:
        s = deck.content("Throughput per Client", f"{unit}/s each client reached in the same run · all threads done")
        cd = CategoryChartData()
        cd.categories = [_cat(r) for r in pc_rows]
        for c in names:
            cd.add_series(c, [round(_tp(r["per_client"].get(c), unit) or 0, 3) for r in pc_rows])
        ch = _chart(deck, s, XL_CHART_TYPE.COLUMN_CLUSTERED, cd, "Per-client chart")
        _style_chart(ch, axis_title=f"{unit}/s")
        ch.plots[0].gap_width, ch.plots[0].overlap = 70, -5
        for i, ser in enumerate(ch.plots[0].series):
            ser.format.fill.solid(); ser.format.fill.fore_color.rgb = SERIES[i % len(SERIES)]
            _labels(ser, "0.00")
        s.notes_slide.notes_text_frame.text = ("Similar bars mean the load was spread evenly; one short bar points at "
                                               "that client or its network path.")

    # 6. results table (paged), styled like the manual's troubleshooting table
    head = ["Run", "Operation", "Clients", f"{unit}/s last", f"{unit}/s first", "IOPS last", "Files·dirs/s",
            f"Data ({unit})", "Elapsed s"]
    widths = [2.5, 1.15, 2.45, 1.15, 1.15, 1.15, 1.2, 1.1, 1.15]
    per_page = 9
    for page in range(0, len(rows), per_page):
        chunk = rows[page:page + per_page]
        s = deck.content("All Results" + (f" ({page // per_page + 1})" if len(rows) > per_page else ""),
                         "Read and write results, summed across clients")
        tbl = s.shapes.add_table(len(chunk) + 1, len(head), L, T, W, Inches(0.42 * (len(chunk) + 1))).table
        scale = W / sum(Inches(w) for w in widths)
        for i, w in enumerate(widths):
            tbl.columns[i].width = Emu(int(Inches(w) * scale))
        for j, h in enumerate(head):
            tbl.cell(0, j).text = h
        for i, r in enumerate(chunk, start=1):
            t = r["started"].strftime("%d %b %H:%M") if r["started"] else ""
            vals = [f"{r['label']} · {t}", r["operation"].title(), ", ".join(r["clients"]), _fmt(_tp(r["tp_last"], unit)),
                    _fmt(_tp(r["tp_first"], unit)), _fmt(r["iops_last"], 0), _fmt(r["ops_last"], 0),
                    _fmt(r["total_mib"] * MIB / UNITS[unit] if r["total_mib"] is not None else None, 1), _fmt(r["elapsed"], 1)]
            for j, v in enumerate(vals):
                tbl.cell(i, j).text = v
        for i in range(len(chunk) + 1):
            for j in range(len(head)):
                c = tbl.cell(i, j)
                c.margin_left = c.margin_right = Inches(0.08); c.margin_top = c.margin_bottom = Inches(0.04)
                c.vertical_anchor = MSO_ANCHOR.MIDDLE
                for p in c.text_frame.paragraphs:
                    p.alignment = PP_ALIGN.RIGHT if j >= 3 else PP_ALIGN.LEFT
                    for run in p.runs:
                        run.font.size = Pt(12)
                        run.font.bold = i == 0 or j in (0, 3)

    # 7. test setup
    s = deck.content("Test Setup", "Tenant, clients, mount and the benchmark commands that produced these results")
    colw = int((W - Inches(0.4)) / 2)
    left = [[("Tenant", {"bold": True, "size": 14, "color": NAVY})]]
    for k, lbl in (("tenant_name", "Tenant"), ("view_path", "View path"), ("view_protocols", "Protocols"),
                   ("qos_read_limit", "QoS read limit"), ("qos_write_limit", "QoS write limit"),
                   ("quota_hard_limit", "Quota hard limit")):
        v = tenant.get(k)
        if v not in (None, "", []):
            left.append([(f"{lbl}:  ", {"color": MUTED}), (" + ".join(v) if isinstance(v, list) else str(v), {})])
    if vms:
        left.append([("VMS:  ", {"color": MUTED}), (vms, {})])
    left.append("")
    left.append([("Clients", {"bold": True, "size": 14, "color": NAVY})])
    for c in clients or []:
        if not all_clients or c.get("name") in all_clients:
            left.append([(f"{c.get('name')}:  ", {"color": MUTED}), (f"{c.get('username', '')}@{c.get('host', '')}", {})])
    if mount and (mount.get("mount_point") or mount.get("options")):
        left.append("")
        left.append([("Mount", {"bold": True, "size": 14, "color": NAVY})])
        if mount.get("mount_point"):
            left.append([("Mount point:  ", {"color": MUTED}), (str(mount["mount_point"]), {})])
        if mount.get("options"):
            left.append([("Options:  ", {"color": MUTED}), (str(mount["options"]), {"size": 10})])
    _text(s, L, T, colw, B - T, left, size=12, name="Setup")
    cmds: list[tuple[str, str]] = []          # (run label, benchmark command), one per distinct command
    for r in rows:
        c = perf_command(r["command"])
        if c and c not in [x[1] for x in cmds]:
            cmds.append((r["label"], c))
    code = []
    for i, (lbl, cmd) in enumerate(cmds[:6]):
        code += ([""] if i else []) + [f"# {lbl}", cmd[:300]]
    import re
    tools = sorted({re.sub(r"^(?:sudo\s+|exec\s+|time\s+)+", "", c).split(" ", 1)[0].rsplit("/", 1)[-1] for _, c in cmds})
    _text(s, L + colw + Inches(0.4), T, colw, Inches(0.3),
          [[(f"{tools[0]} commands" if len(tools) == 1 else "Benchmark commands", {"bold": True, "size": 14, "color": NAVY})]],
          name="Commands title")
    code = code or ["(no command recorded)"]
    deck.code(s, code, L + colw + Inches(0.4), T + Inches(0.4), colw,
              min(B - T - Inches(0.5), Inches(0.3) + Inches(0.33) * sum(1 + len(x) // 70 for x in code)), size=10)

    out.parent.mkdir(parents=True, exist_ok=True)
    deck.prs.core_properties.title = f"Performance Report - {site}"
    deck.prs.core_properties.author = "VAST Test Bench"
    n_slides = deck.save(out)

    # companion summary for the Logs reading pane
    md = [f"# Performance report - {site}", "",
          f"- Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}" + (f" (VAST Test Bench v{version})" if version else ""),
          f"- Runs: {nr} · clients: {', '.join(all_clients) or '-'}", f"- Period: {period or '-'}",
          f"- VMS: {vms or '-'} · tenant: {tenant.get('tenant_name') or '-'}",
          "- Contents: elbencho read and write results only",
          f"- PowerPoint: `{out.name}` ({n_slides} slides) - download it with the ⤓ pptx button", "",
          f"| Run | Operation | Clients | {unit}/s last | {unit}/s first | IOPS last | Time taken s | P99 latency |",
          "|---|---|---|---:|---:|---:|---:|---:|"]
    for r in rows:
        t = r["started"].strftime("%Y-%m-%d %H:%M") if r["started"] else ""
        md.append(f"| {r['label']} · {t} | {r['operation']}{' · ' + r['profile'] if r.get('profile') else ''} | {', '.join(r['clients'])} | {_fmt(_tp(r['tp_last'], unit))} | "
                  f"{_fmt(_tp(r['tp_first'], unit))} | {_fmt(r['iops_last'], 0)} | {_fmt(r['elapsed'], 1)} | {_ms((r['lat'] or {}).get('p99'))} |")
    out.with_suffix(".md").write_text("\n".join(md) + "\n")
    return {"file": out.name, "slides": n_slides, "runs": nr, "rows": len(rows),
            "clients": all_clients, "summary": out.with_suffix(".md").name}


def report_path(results_dir: Path) -> Path:
    return results_dir / f"{time.strftime('%Y%m%d-%H%M%S')}-performance_report.pptx"
