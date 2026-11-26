"""Performance report as a PowerPoint deck (python-pptx), built from the site's elbencho run files.

It uses the same template as the tester manual (app/assets/report-template.pptx: the VAST layouts, Moderat titles,
theme colours, VAST logo, the green note box and black code box). Numbers are the same as the Performance results table in tab ②: summed across clients, throughput converted from
elbencho's MiB/s to decimal units (1 GB/s = 1000 MB/s). Charts are native PowerPoint charts, so they stay editable.
The deck is written to the results folder next to a short .md summary, and shows up in the 🗒 Logs tab.
"""
from __future__ import annotations

import copy
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LABEL_POSITION, XL_LEGEND_POSITION
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Inches, Pt

UNITS = {"MB": 1e6, "GB": 1e9, "TB": 1e12}            # decimal throughput units, as in the app
MIB = 1048576

PERF_OPS = ("READ", "WRITE")                # the report shows only the read / write phases
PERF_TOOLS = ("elbencho", "fio", "vdbench", "iozone", "ior", "mdtest", "warp", "dd")   # benchmark commands worth showing

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


def _when(iso: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(str(iso))
    except (TypeError, ValueError):
        return None


def rows_for(run: dict) -> list[dict]:
    """One row per elbencho phase, summed across clients (same as perfRows() in the UI), with per-client values."""
    per_client: dict[tuple[int, str], dict[str, float | None]] = {}
    for c in run.get("clients") or []:
        for i, cmd in enumerate(c.get("commands") or []):
            for op in cmd.get("elbencho") or []:
                m = op.get("metrics") or {}
                tp = (m.get("Throughput MiB/s") or {}).get("last_done")
                per_client.setdefault((i + 1, str(op.get("operation"))), {})[c.get("client", "?")] = _num(tp)
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
        })
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
    return f"{r['operation'].title()} · {t}"


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


# ------------------------------------------------------------------ the deck

def build(runs: list[dict], out: Path, *, site: str, unit: str = "GB", vms: str = "", tenant: dict | None = None,
          clients: list[dict] | None = None, mount: dict | None = None, version: str = "") -> dict:
    """runs: elbencho run dicts (the .json files). Writes out (.pptx) and out.with_suffix('.md'). Returns a summary."""
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

    # 3. throughput per run
    s = deck.content("Throughput per Run", f"{unit}/s summed across clients · dark: all threads done · light: first thread done")
    cd = CategoryChartData()
    cd.categories = [_cat(r) for r in tp_rows]
    cd.add_series("All threads done (last done)", [round(_tp(r["tp_last"], unit), 3) for r in tp_rows])
    cd.add_series("First thread done", [round(_tp(r["tp_first"], unit) or 0, 3) for r in tp_rows])
    ch = _chart(deck, s, XL_CHART_TYPE.COLUMN_CLUSTERED, cd, "Throughput chart")
    _style_chart(ch, axis_title=f"{unit}/s")
    ch.plots[0].gap_width, ch.plots[0].overlap = 60, -10
    for i, ser in enumerate(ch.plots[0].series):
        ser.format.fill.solid(); ser.format.fill.fore_color.rgb = TEAL if i == 0 else LIGHT_TEAL
        _labels(ser, "0.00")
    s.notes_slide.notes_text_frame.text = "Quote the dark bars: throughput when every thread had finished."

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
          f"| Run | Operation | Clients | {unit}/s last | {unit}/s first | IOPS last | Elapsed s |",
          "|---|---|---|---:|---:|---:|---:|"]
    for r in rows:
        t = r["started"].strftime("%Y-%m-%d %H:%M") if r["started"] else ""
        md.append(f"| {r['label']} · {t} | {r['operation']} | {', '.join(r['clients'])} | {_fmt(_tp(r['tp_last'], unit))} | "
                  f"{_fmt(_tp(r['tp_first'], unit))} | {_fmt(r['iops_last'], 0)} | {_fmt(r['elapsed'], 1)} |")
    out.with_suffix(".md").write_text("\n".join(md) + "\n")
    return {"file": out.name, "slides": n_slides, "runs": nr, "rows": len(rows),
            "clients": all_clients, "summary": out.with_suffix(".md").name}


def report_path(results_dir: Path) -> Path:
    return results_dir / f"{time.strftime('%Y%m%d-%H%M%S')}-performance_report.pptx"
