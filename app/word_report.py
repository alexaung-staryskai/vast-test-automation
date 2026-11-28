"""Performance report as a Word document (python-docx), built on a template the user uploads.

Same data as the PowerPoint report (perf_report): elbencho read / write results summed across clients, latency,
the cluster's own numbers from the VMS and expected-vs-actual targets. The layout follows the usual VAST benchmark
write-ups: document history, overview (objective / method / test tool), lab environment table, network architecture,
result summary, results per run with charts, execution commands in code boxes, observations and a disclaimer.

Template (optional, uploaded in ⚙ Configuration -> Report template): its page setup, headers / footers (logo) and styles
(Title, Heading 1, Heading 2, List Bullet, Table Grid) are used. Text placeholders anywhere in it, headers included:
{{title}} {{site}} {{tenant}} {{date}} {{author}} {{vms}} {{period}}. A paragraph that holds only {{content}} marks where
the report goes - text before / after it (e.g. a cover page) is kept. Without {{content}} the template's body is replaced.
"""
from __future__ import annotations

import copy
import io
import re
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

import docx
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Emu, Inches, Pt, RGBColor

from . import perf_report as pr

PLACEHOLDERS = ("title", "site", "tenant", "date", "author", "vms", "period")
TOKEN_RE = re.compile(r"\{\{\s*(" + "|".join(PLACEHOLDERS + ("content",)) + r")\s*\}\}")
NAVY, TEAL, ORANGE, LIGHT_TEAL, GREEN = "#10162D", "#1A9FC0", "#E97132", "#9CD9E8", "#4EA72E"
RED, BLUE, MUTED, GRID = "#E8445A", "#2BB7E3", "#5B677A", "#C9D6E3"
MAX_TEMPLATE_BYTES = 20 * 1024 * 1024


# ------------------------------------------------------------------ template

def _docx_bytes(data: bytes) -> bytes:
    """Accept .docx and .dotx: a Word template (.dotx) only differs in the main part's content type."""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        ct = z.read("[Content_Types].xml").decode("utf-8")
        z.getinfo("word/document.xml")
    except (zipfile.BadZipFile, KeyError, UnicodeDecodeError):
        raise ValueError("Not a Word file - upload a .docx (or .dotx) template")
    if "wordprocessingml.template.main+xml" not in ct:
        return data
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as w:
        for item in z.infolist():
            blob = z.read(item.filename)
            if item.filename == "[Content_Types].xml":
                blob = ct.replace("wordprocessingml.template.main+xml", "wordprocessingml.document.main+xml").encode("utf-8")
            w.writestr(item, blob)
    return out.getvalue()


def check_template(data: bytes) -> dict:
    """Validate an uploaded template. Returns what the report will use from it."""
    if len(data) > MAX_TEMPLATE_BYTES:
        raise ValueError("The template is larger than 20 MB")
    try:
        d = docx.Document(io.BytesIO(_docx_bytes(data)))
    except ValueError:
        raise
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"Word can't read this file: {type(e).__name__}")
    found = set()
    for p in _all_paragraphs(d):
        found |= {m.group(1) for m in TOKEN_RE.finditer(p.text)}
    styles = {s.name for s in d.styles}
    sec = d.sections[0]
    return {"placeholders": sorted(found - {"content"}), "content_marker": "content" in found,
            "styles": [n for n in ("Title", "Heading 1", "Heading 2", "List Bullet", "Table Grid") if n in styles],
            "missing_styles": [n for n in ("Title", "Heading 1", "Heading 2") if n not in styles],
            "page": f"{sec.page_width / 914400:.2f} × {sec.page_height / 914400:.2f} in" if sec.page_width else "",
            "header_images": sum(len(s.header._element.findall(".//" + qn("a:blip"))) for s in d.sections)}


def _all_paragraphs(d):
    for p in d.paragraphs:
        yield p
    for t in d.tables:
        for row in t.rows:
            for c in row.cells:
                yield from c.paragraphs
    for s in d.sections:
        for part in (s.header, s.footer, s.first_page_header, s.first_page_footer, s.even_page_header, s.even_page_footer):
            try:
                yield from part.paragraphs
                for t in part.tables:
                    for row in t.rows:
                        for c in row.cells:
                            yield from c.paragraphs
            except Exception:  # noqa: BLE001
                continue


def _fill_placeholders(d, values: dict) -> None:
    """Replace {{token}} in every paragraph (body, tables, headers, footers). A token split over several runs is
    rewritten into the paragraph's first run, keeping that run's formatting."""
    for p in _all_paragraphs(d):
        text = p.text
        if "{{" not in text or not TOKEN_RE.search(text):
            continue
        new = TOKEN_RE.sub(lambda m: values.get(m.group(1), m.group(0)) if m.group(1) != "content" else m.group(0), text)
        if new == text:
            continue
        runs = p.runs
        if not runs:
            continue
        if any(TOKEN_RE.search(r.text) for r in runs) and sum(bool(TOKEN_RE.search(r.text)) for r in runs) == len(TOKEN_RE.findall(text)):
            for r in runs:   # tokens sit inside single runs: replace in place, formatting untouched
                if TOKEN_RE.search(r.text):
                    r.text = TOKEN_RE.sub(lambda m: values.get(m.group(1), m.group(0)) if m.group(1) != "content" else m.group(0), r.text)
        else:
            runs[0].text = new
            for r in runs[1:]:
                r.text = ""


def default_template() -> docx.Document:
    """Built-in template: US Letter, 1 in margins, Calibri, navy / teal headings."""
    d = docx.Document()
    sec = d.sections[0]
    sec.page_width, sec.page_height = Inches(8.5), Inches(11)
    for m in ("left_margin", "right_margin", "top_margin", "bottom_margin"):
        setattr(sec, m, Inches(1))
    st = d.styles
    st["Normal"].font.name = "Calibri"
    st["Normal"].font.size = Pt(11)
    for name, size, color in (("Title", 26, NAVY), ("Heading 1", 16, NAVY), ("Heading 2", 13, TEAL)):
        f = st[name].font
        f.name, f.size, f.bold = "Calibri", Pt(size), True
        f.color.rgb = RGBColor.from_string(color[1:])
    hp = sec.header.paragraphs[0]
    hp.text = "{{title}}"
    hp.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    hp.runs[0].font.size, hp.runs[0].font.color.rgb = Pt(9), RGBColor.from_string(MUTED[1:])
    fp = sec.footer.paragraphs[0]
    fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _page_field(fp)
    return d


def starter_template() -> bytes:
    """A template to edit in Word and upload: shows the placeholders and where the report goes."""
    d = default_template()
    d.add_paragraph("{{title}}", style="Title")
    p = d.add_paragraph()
    p.add_run("{{site}} · tenant {{tenant}} · {{date}}").italic = True
    d.add_paragraph("Prepared by {{author}}")
    d.add_paragraph()
    d.add_paragraph("How to use this template", style="Heading 1")
    for t in ("Change fonts, colours, the header / footer (add your logo) and the heading styles - the report uses them.",
              "Placeholders - type the name in double curly braces, e.g. title: title, site, tenant, date, author, vms, "
              "period. They work in the body, header and footer (see the cover above and the header).",
              "The paragraph on the next page that holds only the content placeholder is where the report is inserted. "
              "Text before it (this cover page) and after it (an appendix) is kept.",
              "Delete this 'How to use' section, then upload the file in VAST Test Bench: ⚙ Configuration → Report template."):
        d.add_paragraph(t, style="List Bullet")
    br = d.add_paragraph().add_run()
    br.add_break(WD_BREAK.PAGE)
    d.add_paragraph("{{content}}")
    out = io.BytesIO()
    d.save(out)
    return out.getvalue()


def _page_field(paragraph) -> None:
    run = paragraph.add_run()
    for kind, text in (("begin", None), (None, "PAGE"), ("end", None)):
        if kind:
            el = OxmlElement("w:fldChar"); el.set(qn("w:fldCharType"), kind)
        else:
            el = OxmlElement("w:instrText"); el.set(qn("xml:space"), "preserve"); el.text = text
        run._r.append(el)
    run.font.size = Pt(9)


# ------------------------------------------------------------------ charts (matplotlib)

def _plt():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 9, "axes.edgecolor": GRID, "axes.labelcolor": MUTED,
                         "xtick.color": NAVY, "ytick.color": MUTED, "axes.spines.top": False, "axes.spines.right": False})
    return plt


def _png(fig) -> io.BytesIO:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=200, bbox_inches="tight", facecolor="white")
    import matplotlib.pyplot as plt
    plt.close(fig)
    buf.seek(0)
    return buf


def bar_chart(categories: list[str], series: list[tuple[str, list[float], str]], ylabel: str, fmt: str = "{:,.2f}",
              width_in: float = 6.5, height_in: float = 3.0) -> io.BytesIO:
    plt = _plt()
    fig, ax = plt.subplots(figsize=(width_in, height_in))
    n = len(series)
    w = 0.8 / max(1, n)
    many = len(categories) * n > 12
    for i, (name, vals, color) in enumerate(series):
        xs = [x + (i - (n - 1) / 2) * w for x in range(len(categories))]
        bars = ax.bar(xs, vals, w * 0.92, label=name, color=color)
        if many and i > 0:
            continue   # crowded: value labels on the first series only
        for b, v in zip(bars, vals):
            if v:
                ax.annotate(fmt.format(v), (b.get_x() + b.get_width() / 2, b.get_height()), ha="center", va="bottom",
                            fontsize=6 if many else 7, color=NAVY, xytext=(0, 2), textcoords="offset points")
    ax.set_xticks(range(len(categories)))
    if len(categories) > 4:
        ax.set_xticklabels(categories, fontsize=7, rotation=30, ha="right")
    else:
        ax.set_xticklabels(categories, fontsize=8)
    ax.set_ylabel(ylabel)
    ax.yaxis.grid(True, color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)
    ax.margins(y=0.15)
    if n > 1:
        ax.legend(frameon=False, fontsize=8, ncol=n, loc="upper center", bbox_to_anchor=(0.5, 1.12))
    return _png(fig)


def topology_png(hw: dict, clients: list[dict], labels: dict | None = None) -> io.BytesIO:
    """Network diagram: split (clients -> front-end switches -> CNodes -> back-end switches -> DBoxes) or
    converged (clients -> switches -> EBoxes). Red = fabric A, blue = fabric B, dashed = back-end."""
    plt = _plt()
    from matplotlib.patches import FancyBboxPatch
    labels = labels or {}
    conv = hw.get("design") == "converged"
    fig, ax = plt.subplots(figsize=(7.2, 5.6 if not conv else 4.2))
    ax.set_xlim(0, 10); ax.axis("off")
    H = 10 if not conv else 7.5
    ax.set_ylim(0, H)
    gb = lambda sp: f"{sp}G" if sp else ""   # noqa: E731
    def box(x, y, w, h, text, fill=NAVY, fs=7.5):
        ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.02,rounding_size=0.08", fc=fill, ec="#5B677A", lw=0.6))
        ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", color="white", fontsize=fs, fontweight="bold")
    def line(x1, y1, x2, y2, c, dashed=False):
        ax.plot([x1, x2], [y1, y2], color=c, lw=1.4, ls=(0, (4, 2)) if dashed else "-", solid_capstyle="butt", zorder=0)
    # clients (top)
    shown = clients[:6] or [{"name": "client"}]
    n = len(shown)
    cw = min(1.7, (9.0 - 0.3 * (n - 1)) / n)
    y_cl = H - 0.9
    xs = [0.5 + (9.0 - cw) / 2] if n == 1 else [0.5 + i * (cw + (9.0 - cw * n) / (n - 1)) for i in range(n)]
    for x, c in zip(xs, shown):
        box(x, y_cl, cw, 0.55, c.get("name", ""), fs=7)
    # front-end switches
    sw_w, sw_h = 2.8, 0.5
    ax_c, bx_c = 2.75, 7.25
    y_fs = H - 2.4
    fs_name = labels.get("front_switch") or (f"{gb(hw.get('fe_speed'))} switch".strip() if hw.get("fe_speed") else "Switch")
    box(ax_c - sw_w / 2, y_fs, sw_w, sw_h, f"{fs_name} A", "#2A3347")
    box(bx_c - sw_w / 2, y_fs, sw_w, sw_h, f"{fs_name} B", "#2A3347")
    for x in xs:
        line(x + cw / 2 - 0.12, y_cl, ax_c, y_fs + sw_h, RED)
        line(x + cw / 2 + 0.12, y_cl, bx_c, y_fs + sw_h, BLUE)
    # CNodes / EBoxes
    nn = (hw.get("eboxes") if conv else hw.get("cnodes")) or 1
    k = min(nn, 8)
    nh = min(0.42, 2.0 / k)
    nw, nx = 3.4, 5 - 1.7
    top = y_fs - 0.75
    ys = [top - i * (nh + 0.08) - nh for i in range(k)]
    for i, y in enumerate(ys):
        txt = ("EBox" if conv else "CNode") + f" {i + 1}"
        if i == k - 1 and nn > k:
            txt = f"… {nn - k + 1} more"
        box(nx, y, nw, nh, txt, fs=7)
    line(ax_c, y_fs, ax_c, ys[-1] + nh / 2, RED); line(bx_c, y_fs, bx_c, ys[-1] + nh / 2, BLUE)
    for y in ys:
        line(ax_c, y + nh / 2, nx, y + nh / 2, RED); line(bx_c, y + nh / 2, nx + nw, y + nh / 2, BLUE)
    if hw.get("fe_speed"):
        ax.text(ax_c - 0.1, y_fs - 0.25, gb(hw["fe_speed"]), ha="right", fontsize=7.5, fontweight="bold", color=NAVY)
        ax.text(bx_c + 0.1, y_fs - 0.25, gb(hw["fe_speed"]), ha="left", fontsize=7.5, fontweight="bold", color=NAVY)
    if not conv:
        y_bs = ys[-1] - 1.0
        bs_name = labels.get("back_switch") or (f"{gb(hw.get('be_speed'))} switch".strip() if hw.get("be_speed") else "Switch")
        box(ax_c - sw_w / 2, y_bs, sw_w, sw_h, f"{bs_name} A", "#2A3347")
        box(bx_c - sw_w / 2, y_bs, sw_w, sw_h, f"{bs_name} B", "#2A3347")
        lx, rx = nx - 0.3, nx + nw + 0.3
        for y in ys:
            line(nx, y + nh / 2 - 0.08, lx, y + nh / 2 - 0.08, RED, True); line(nx + nw, y + nh / 2 - 0.08, rx, y + nh / 2 - 0.08, BLUE, True)
        line(lx, ys[0] + nh / 2 - 0.08, lx, y_bs + sw_h, RED, True); line(rx, ys[0] + nh / 2 - 0.08, rx, y_bs + sw_h, BLUE, True)
        nd = hw.get("dboxes") or 1
        sd = min(nd, 4)
        dw, dh = 2.6, 0.55
        tot = sd * dw + (sd - 1) * 0.3
        y_db = y_bs - 1.0
        model = labels.get("dbox_model") or pr.DBOX_MODELS.get(hw.get("dbox_model", ""), str(hw.get("dbox_model") or "").replace("_", " ").title())
        for i in range(sd):
            x = 5 - tot / 2 + i * (dw + 0.3)
            box(x, y_db, dw, dh, (f"VAST {model}" if model else "DBox") + (f" {i + 1}" if nd > 1 else ""))
            if i == 0:
                line(ax_c, y_bs, ax_c, y_db + dh / 2, RED, True); line(bx_c, y_bs, bx_c, y_db + dh / 2, BLUE, True)
            line(ax_c, y_db + dh / 2, x, y_db + dh / 2, RED, True); line(bx_c, y_db + dh / 2, x + dw, y_db + dh / 2, BLUE, True)
    # legend just below the lowest box, then crop the drawing to its contents (same scale on both axes)
    ymin = (y_db if not conv else ys[-1]) - 0.75
    ly = ymin + 0.3
    line(0.2, ly, 0.7, ly, RED); ax.text(0.8, ly, "Fabric A", va="center", fontsize=7, color=NAVY)
    line(2.0, ly, 2.5, ly, BLUE); ax.text(2.6, ly, "Fabric B", va="center", fontsize=7, color=NAVY)
    if not conv:
        line(3.8, ly, 4.3, ly, MUTED, True); ax.text(4.4, ly, "back-end (CNodes ↔ DBoxes)", va="center", fontsize=7, color=NAVY)
    ax.set_ylim(ymin, H)
    fig.set_size_inches(7.2, 7.2 * (H - ymin) / 10)
    return _png(fig)


# ------------------------------------------------------------------ document helpers

class Writer:
    def __init__(self, d):
        self.d = d
        self.styles = {s.name for s in d.styles}
        sec = d.sections[-1]
        self.width = (sec.page_width or Inches(8.5)) - (sec.left_margin or Inches(1)) - (sec.right_margin or Inches(1))

    def st(self, *names):
        return next((n for n in names if n in self.styles), None)

    def h(self, text, level=1):
        style = self.st(f"Heading {level}")
        p = self.d.add_paragraph(text, style=style) if style else self.d.add_paragraph()
        if not style:
            r = p.add_run(text); r.bold = True; r.font.size = Pt(16 if level == 1 else 13)
        return p

    def p(self, *parts, italic=False, size=None, align=None, color=None):
        """parts: str or (text, {"bold": True, ...})"""
        para = self.d.add_paragraph()
        for part in parts:
            t, o = (part, {}) if isinstance(part, str) else part
            r = para.add_run(t)
            r.bold = o.get("bold"); r.italic = o.get("italic", italic)
            if size or o.get("size"):
                r.font.size = Pt(o.get("size", size))
            if color or o.get("color"):
                r.font.color.rgb = RGBColor.from_string((o.get("color") or color)[1:])
        if align:
            para.alignment = align
        return para

    def bullet(self, *parts):
        style = self.st("List Bullet", "List Paragraph")
        para = self.d.add_paragraph(style=style) if style else self.d.add_paragraph()
        if not style or style == "List Paragraph":
            para.add_run("• ")
        for part in parts:
            t, o = (part, {}) if isinstance(part, str) else part
            r = para.add_run(t); r.bold = o.get("bold")
        return para

    def _shade(self, cell, fill):
        tcPr = cell._tc.get_or_add_tcPr()
        shd = OxmlElement("w:shd"); shd.set(qn("w:val"), "clear"); shd.set(qn("w:color"), "auto"); shd.set(qn("w:fill"), fill)
        tcPr.append(shd)

    def _borders(self, table):
        """Thin borders on every cell - works whatever table styles the template has."""
        tblPr = table._tbl.tblPr
        b = OxmlElement("w:tblBorders")
        for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
            e = OxmlElement(f"w:{edge}"); e.set(qn("w:val"), "single"); e.set(qn("w:sz"), "4"); e.set(qn("w:color"), "A6B4C4")
            b.append(e)
        tblPr.append(b)

    def table(self, head, rows, widths=None, num_from=1, bold_cols=(), size=9):
        t = self.d.add_table(rows=len(rows) + 1, cols=len(head))
        style = self.st("Table Grid")
        if style:
            t.style = style
        else:
            self._borders(t)
        t.alignment = WD_TABLE_ALIGNMENT.CENTER
        if widths:
            tot = sum(widths)
            for i, w in enumerate(widths):
                for c in t.columns[i].cells:
                    c.width = Emu(int(self.width * w / tot))
        for i, row in enumerate([head] + [list(r) for r in rows]):
            for j, v in enumerate(row):
                c = t.cell(i, j)
                c.text = ""
                para = c.paragraphs[0]
                r = para.add_run(str(v))
                r.font.size = Pt(size)
                r.bold = i == 0 or j in bold_cols
                para.alignment = WD_ALIGN_PARAGRAPH.RIGHT if (i > 0 and j >= num_from) else WD_ALIGN_PARAGRAPH.LEFT
                if i == 0:
                    self._shade(c, "DCE6F0")
        self.d.add_paragraph()
        return t

    def code(self, lines: list[str]):
        """A 1×1 table with monospace text - the usual 'execution command' box."""
        t = self.d.add_table(rows=1, cols=1)
        style = self.st("Table Grid")
        if style:
            t.style = style
        else:
            self._borders(t)
        c = t.cell(0, 0)
        self._shade(c, "F2F2F2")
        c.text = ""
        for i, ln in enumerate(lines):
            para = c.paragraphs[0] if i == 0 else c.add_paragraph()
            r = para.add_run(ln)
            r.font.name = "Courier New"
            r._element.rPr.rFonts.set(qn("w:eastAsia"), "Courier New")
            r.font.size = Pt(8)
        self.d.add_paragraph()

    def image(self, png: io.BytesIO, width_in: float = 6.5, caption: str | None = None):
        if self.d.paragraphs:   # keep the line introducing the picture on the same page as the picture
            self.d.paragraphs[-1].paragraph_format.keep_with_next = True
        w = min(Inches(width_in), self.width)
        self.d.add_picture(png, width=w)
        self.d.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
        if caption:
            self.p(caption, italic=True, size=9, align=WD_ALIGN_PARAGRAPH.CENTER, color=MUTED)


def _elbencho_params(cmd: str) -> dict:
    """-b block, -t threads, -n dirs, -N files, -s size from an elbencho command line."""
    out = {}
    for flag, key in (("-b", "block"), ("-t", "threads"), ("-n", "dirs"), ("-N", "files"), ("-s", "size")):
        m = re.search(rf"(?:^|\s){re.escape(flag)}\s+(\S+)", cmd or "")
        if m:
            out[key] = m.group(1)
    for flag in ("--direct", "--lat", "--rand"):
        out[flag] = flag in (cmd or "")
    return out


def _mib(v):
    return "-" if v is None else f"{v:,.0f}"


def _gbs(mib):
    return "-" if mib is None else f"{mib * pr.MIB / 1e9:,.1f}"


# ------------------------------------------------------------------ the document

def build(runs: list[dict], out: Path, *, site: str, unit: str = "GB", vms: str = "", tenant: dict | None = None,
          clients: list[dict] | None = None, mount: dict | None = None, version: str = "", expected: dict | None = None,
          cluster: dict | None = None, arch: dict | None = None, labels: dict | None = None,
          template: bytes | None = None) -> dict:
    """Write the Word report to `out` (.docx). template: the uploaded .docx/.dotx bytes, or None for the built-in one."""
    tenant, arch, labels = tenant or {}, arch or {}, labels or {}
    data = pr.prepare(runs)
    rows, tp_rows, best, lat_any = data["rows"], data["tp_rows"], data["best"], data["lat_any"]
    all_clients, period = data["all_clients"], data["period"]
    info = (cluster or {}).get("info") or {}
    hw = info.get("hw") or {}
    used_cl = [c for c in (clients or []) if not all_clients or c.get("name") in all_clients] or [{"name": c} for c in all_clients]
    now = datetime.now()
    author = str(labels.get("author") or "").strip() or "VAST Test Bench"
    title = f"Performance Report – {site}"
    values = {"title": title, "site": site, "tenant": str(tenant.get("tenant_name") or "-"), "date": now.strftime("%d %b %Y"),
              "author": author, "vms": vms or "-", "period": period or "-"}

    d = docx.Document(io.BytesIO(_docx_bytes(template))) if template else default_template()
    _fill_placeholders(d, values)
    body = d.element.body
    marker = next((p for p in d.paragraphs if p.text.strip() in ("{{content}}", "{{ content }}")), None)
    if marker is None:   # replace the template's body, keep its section settings (page size, headers, footers)
        for el in list(body):
            if el.tag != qn("w:sectPr"):
                body.remove(el)
    existing = set(id(el) for el in body)    # everything not in here afterwards is the report
    w = Writer(d)

    # ---- title
    if not marker:
        if w.st("Title"):
            d.add_paragraph(title, style=w.st("Title"))
        else:
            w.p((title, {"bold": True, "size": 24}))
        w.p((" · ".join(x for x in [f"Tenant {values['tenant']}", period, f"VMS {vms}" if vms else ""] if x), {"italic": True}), color=MUTED)
    # ---- document history
    w.h("Document History", 1)
    w.table(["Date", "Version", "Author", "History"],
            [(now.strftime("%d-%m-%Y"), "V1.0", author,
              f"Generated by VAST Test Bench{' v' + version if version else ''} from {len(runs)} run{'s' if len(runs) != 1 else ''}")],
            widths=[1.2, 0.8, 2.0, 3.5], num_from=99)

    # ---- overview
    w.h("Overview", 1)
    proto = ", ".join(pr._mount_summary((mount or {}).get("options", ""))[:1]) or "NFS"
    w.p(("Objective: ", {"bold": True}), f"Measure the read and write throughput, IOPS and latency of tenant "
        f"{values['tenant']} on the VAST cluster {info.get('name') or vms or ''} from {len(all_clients)} test client"
        f"{'s' if len(all_clients) != 1 else ''}.")
    w.p(("Method: ", {"bold": True}), f"Run elbencho on all clients at the same time over {proto}, sum the results across "
        "clients, and compare them with the throughput the cluster itself recorded (VMS analytics) and with the expected figures.")
    w.p(("Test tool: ", {"bold": True}), "https://github.com/breuner/elbencho")
    w.p("Throughput is reported in MiB/s as elbencho prints it and in GB/s (GB/s = MiB/s × 1.048576 / 1000). "
        "\"First Done\" is measured when the fastest thread finishes (all threads still active); \"Last Done\" when the "
        "last thread finishes. Latency is the IO latency measured by elbencho on the clients.", size=10)

    # ---- environment
    w.h("Test Environment", 1)
    w.p(("Lab environment", {"bold": True}))
    if hw.get("design") == "converged":
        vast_cfg = f"EBox x {hw.get('eboxes')}"
    else:
        model = labels.get("dbox_model") or pr.DBOX_MODELS.get(hw.get("dbox_model", ""), str(hw.get("dbox_model") or "DBox"))
        vast_cfg = " / ".join(x for x in [f"CNode x {hw.get('cnodes') or info.get('cnodes') or '?'}",
                                          f"{model} DBox x {hw.get('dboxes') or info.get('dboxes') or '?'}"] if x)
    code_v = ".".join(str(info.get("version") or "").split(".")[:4]) or "-"
    client_cfg = "; ".join(f"{c.get('name')} ({c.get('host', '')})" for c in used_cl[:8])
    mopt = pr._mount_summary((mount or {}).get("options", ""))
    w.table(["VAST Config", "Code", "Client", "Client Config"],
            [(vast_cfg, code_v, f"{len(all_clients)} Host{'s' if len(all_clients) != 1 else ''}",
              client_cfg + (f". {', '.join(mopt[:2])}" if mopt else ""))], widths=[1.6, 0.9, 0.9, 3.6], num_from=99)
    if hw.get("cnodes") or hw.get("eboxes"):
        w.h("Cluster network architecture", 2)
        ports = []
        if hw.get("fe_ports"):
            ports.append(f"{hw['fe_ports']} × {hw.get('fe_speed')}G front-end ports per {'EBox' if hw.get('design') == 'converged' else 'CNode'}")
        if hw.get("be_ports") and hw.get("design") != "converged":
            ports.append(f"{hw['be_ports']} × {hw.get('be_speed')}G back-end ports per CNode")
        if hw.get("d_ports"):
            ports.append(f"{hw['d_ports']} × {hw.get('d_speed')}G ports per DNode ({hw.get('dnodes')} DNodes)")
        w.p(("Split design" if hw.get("design") != "converged" else "Converged design (EBoxes)", {"bold": True}),
            " - from the VMS inventory: " + "; ".join(ports) + "." if ports else "")
        w.image(topology_png(hw, used_cl, labels), 6.3, "Red = fabric A, blue = fabric B, dashed = back-end network")
    w.h("Data path", 2)
    vp = arch.get("vip_pool") or {}
    ips = list(vp.get("ips") or [])
    for parts in ([("Mount: ", {"bold": True}), ", ".join(mopt) or "-"],
                  [("VIP pool: ", {"bold": True}), f"{vp.get('name') or '-'}" + (f", {ips[0]} – {ips[-1]} ({len(ips)} IPs)" if ips else "")],
                  [("Tenant / view: ", {"bold": True}), f"{values['tenant']}, {arch.get('view_path') or tenant.get('view_path') or '-'}"],
                  [("QoS / quota: ", {"bold": True}), " · ".join(f"{lbl} {tenant[k]}" for k, lbl in
                   (("qos_read_limit", "read limit"), ("qos_write_limit", "write limit"), ("quota_hard_limit", "quota")) if tenant.get(k)) or "-"]):
        w.bullet(*parts)
    if mount and mount.get("options"):
        w.p("Sample mount command:", size=10)
        target = f"{ips[0] if ips else '<VIP>'}:{arch.get('view_path') or tenant.get('view_path') or '/<view>'}"
        w.code([f"sudo mount -t nfs -o {mount['options']} \\", f"  {target} {mount.get('mount_point') or '/mnt/vast'}"])

    # ---- result summary
    w.h("Result Summary", 1)
    summ = []
    for op in ("READ", "WRITE"):
        r = best[op]
        if r:
            p = _elbencho_params(r["command"])
            summ.append((op.title(), f"{p.get('block', '-')} block, {p.get('threads', '-')} threads", ", ".join(r["clients"]),
                         _mib(r["tp_first"]), _mib(r["tp_last"]), _gbs(r["tp_last"]), f"{r['elapsed']:,.1f} s" if r["elapsed"] else "-",
                         pr._ms((r.get("lat") or {}).get("p99"))))
    w.table(["Operation", "Best case", "Clients", "First Done MiB/s", "Last Done MiB/s", "GB/s", "Time taken", "P99 latency"],
            summ, widths=[0.8, 1.4, 1.5, 1.0, 1.0, 0.7, 0.8, 0.8], num_from=3, bold_cols=(5,))
    checks = pr.expected_checks(expected, best, tp_rows, lat_any, unit)
    if checks:
        w.h("Estimated vs actual performance", 2)
        w.table(["Metric", "Expected", "Actual", "% of target", "Result"],
                [(c[0], c[1], c[2], f"{c[3] * 100:,.0f}%", "Met" if c[4] else ("Close" if c[3] >= 0.9 else "Below")) for c in checks],
                widths=[2.2, 1.3, 1.3, 1.0, 0.9], num_from=1, bold_cols=(2,))
        tpc = [c for c in checks if c[7]]
        if tpc:
            w.image(bar_chart([c[0] for c in tpc], [("Expected", [c[5] for c in tpc], LIGHT_TEAL), ("Actual", [c[6] for c in tpc], TEAL)],
                              f"{unit}/s", height_in=2.6), 5.0)
        basis = str((expected or {}).get("basis") or "").strip()
        met = sum(1 for c in checks if c[4])
        w.p(("Note: ", {"bold": True}), f"{met} of {len(checks)} targets met." + (f" Expected values: {basis}." if basis else ""), size=10)

    # ---- results
    w.h("Results", 1)
    w.h("Throughput per run", 2)
    res = []
    for r in tp_rows:
        p = _elbencho_params(r["command"])
        files = "-"
        if p.get("threads") and p.get("files"):
            files = f"{p['threads']} × {p.get('dirs', '1')} × {p['files']} × {p.get('size', '?')}"
        res.append((r["started"].strftime("%d %b %H:%M") if r["started"] else "-", r["operation"].upper(), p.get("block", "-"),
                    p.get("threads", "-"), files, _mib(r["tp_first"]), _mib(r["tp_last"]), _gbs(r["tp_last"]),
                    f"{r['elapsed']:,.1f}s" if r["elapsed"] is not None else "-"))
    w.table(["Run", "Op", "Block", "Threads", "Files per client", "First Done MiB/s", "Last Done MiB/s", "GB/s", "Elapsed"],
            res, widths=[1.0, 0.6, 0.6, 0.6, 1.3, 1.0, 1.0, 0.6, 0.7], num_from=5, bold_cols=(7,), size=8)
    w.p(("Note: ", {"bold": True}), "summed across clients; files per client = threads × directories × files × size.", size=9)
    cats = [pr._cat(r) for r in tp_rows]
    w.image(bar_chart(cats, [("All threads done (Last Done)", [round(pr._tp(r["tp_last"], unit), 3) for r in tp_rows], TEAL),
                             ("First thread done", [round(pr._tp(r["tp_first"], unit) or 0, 3) for r in tp_rows], LIGHT_TEAL)],
                      f"{unit}/s"), 6.3, f"Throughput per run ({unit}/s, decimal)")
    pcn = sorted({c for r in tp_rows for c in r["per_client"]})
    if pcn:
        w.h("Throughput per client", 2)
        w.table(["Run"] + pcn + ["Total"],
                [(pr._cat(r),) + tuple(_mib(r["per_client"].get(c)) for c in pcn) + (_mib(r["tp_last"]),) for r in tp_rows],
                widths=[1.6] + [1.2] * len(pcn) + [1.0], num_from=1, bold_cols=(len(pcn) + 1,))
        w.p(("Note: ", {"bold": True}), "MiB/s when all threads finished (Last Done).", size=9)

    lat_rows = [r for r in tp_rows if r.get("lat")]
    if lat_rows:
        w.h("Latency", 2)
        per = [(r, c, l) for r in lat_rows for c, l in sorted(r["lat_clients"].items()) if l]
        w.table(["Run · client", "Min", "Avg", "P50", "P99", "P99.9", "Max"],
                [(f"{pr._cat(r)} · {c}", pr._ms(l.get("min")), pr._ms(l.get("avg")), pr._ms(l.get("p50")), pr._ms(l.get("p99")),
                  pr._ms(l.get("p99.9")), pr._ms(l.get("max"))) for r, c, l in per], widths=[2.4] + [0.8] * 6, num_from=1, bold_cols=(4,))
        w.image(bar_chart([pr._cat(r) for r in lat_rows],
                          [("P50", [round((r["lat"].get("p50") or 0) / 1000, 3) for r in lat_rows], LIGHT_TEAL),
                           ("P99", [round((r["lat"].get("p99") or 0) / 1000, 3) for r in lat_rows], TEAL),
                           ("P99.9", [round((r["lat"].get("p99.9") or 0) / 1000, 3) for r in lat_rows], ORANGE)], "ms",
                          height_in=2.6), 5.5, "IO latency per run (worst client), milliseconds")

    cmp_rows = pr.cluster_compare(tp_rows, cluster)
    if cmp_rows:
        w.h("Cluster performance (VMS)", 2)
        w.p("What the VAST cluster served during each run, from the VMS analytics API (10-second samples for the time the "
            "benchmark command ran), next to what the clients reported.", size=10)
        w.table(["Run", "Clients GB/s", "Cluster avg GB/s", "Sustained GB/s", "Peak GB/s", "Cluster IOPS", "Cluster latency", "Difference"],
                [(pr._cat(x["r"]), _gbs(x["r"]["tp_last"]), f"{x['whole'] / 1e9:,.1f}", f"{x['sustained'] / 1e9:,.1f}",
                  f"{x['peak'] / 1e9:,.1f}", pr._fmt(x["iops"], 0), pr._ms(x["lat"]),
                  f"{(x['whole'] / (x['r']['tp_last'] * pr.MIB) - 1) * 100:+.1f}%") for x in cmp_rows],
                widths=[1.5, 0.9, 1.0, 1.0, 0.8, 0.9, 0.9, 0.8], num_from=1, bold_cols=(2,), size=8)
        w.image(bar_chart([pr._cat(x["r"]) for x in cmp_rows],
                          [("Clients reported", [round(pr._tp(x["r"]["tp_last"], unit), 3) for x in cmp_rows], TEAL),
                           ("Cluster avg (whole run)", [round(x["whole"] / pr.UNITS[unit], 3) for x in cmp_rows], NAVY),
                           ("Cluster sustained", [round(x["sustained"] / pr.UNITS[unit], 3) for x in cmp_rows], LIGHT_TEAL)],
                          f"{unit}/s", height_in=2.8), 6.3)
        w.p(("Note: ", {"bold": True}), "Cluster avg covers the whole run like the clients' figure; sustained leaves out samples "
            "below 50% of the run's peak (ramp-up and tail). Cluster latency is measured inside the cluster.", size=9)

    w.h("Execution commands", 2)
    seen = []
    for r in rows:
        c = pr.perf_command(r["command"])
        if c and c not in [x[1] for x in seen]:
            seen.append((r["label"], c))
    for lbl, c in seen[:8]:
        w.p(f"{lbl}:", size=10)
        w.code([c])

    # ---- observations
    w.h("Observations", 1)
    for op in ("READ", "WRITE"):
        r = best[op]
        if r:
            w.bullet(f"We observed {op.lower()} throughput of {_mib(r['tp_last'])} MiB/s ({_gbs(r['tp_last'])} GB/s) "
                     f"from {len(r['clients'])} client{'s' if len(r['clients']) != 1 else ''} when all threads finished, "
                     f"in {r['elapsed']:,.1f} s." if r["elapsed"] else ".")
            if r["tp_first"] and r["tp_last"] and abs(r["tp_first"] / r["tp_last"] - 1) > 0.05:
                w.bullet(f"First Done ({_mib(r['tp_first'])} MiB/s) and Last Done ({_mib(r['tp_last'])} MiB/s) differ by "
                         f"{(r['tp_first'] / r['tp_last'] - 1) * 100:+.1f}% - some threads finished later than others.")
            vals = [v for v in r["per_client"].values() if v]
            if len(vals) > 1:
                w.bullet(f"Per client the {op.lower()} throughput ranged from {_mib(min(vals))} to {_mib(max(vals))} MiB/s "
                         f"({(max(vals) / min(vals) - 1) * 100:.1f}% spread).")
    if lat_any.get("p99") is not None:
        w.bullet(f"P99 IO latency was {pr._ms(lat_any['p99'])} (average {pr._ms(lat_any.get('avg'))}) on the worst client.")
    for x in cmp_rows:
        dlt = x["whole"] / (x["r"]["tp_last"] * pr.MIB) - 1
        if abs(dlt) > 0.15:
            w.bullet(f"{pr._cat(x['r'])}: the cluster recorded {dlt * 100:+.0f}% compared with the clients - check other load "
                     "on the cluster or client-side caching.")
        elif x["sustained"] > 1.3 * x["whole"]:
            w.bullet(f"{pr._cat(x['r'])}: the cluster sustained {x['sustained'] / 1e9:,.1f} GB/s once the run was under way "
                     f"({x['whole'] / 1e9:,.1f} GB/s over the whole run), so the run had a slow start or tail.")
    for c in checks:
        w.bullet(f"{c[0]}: {c[2]} against an expected {c[1]} ({c[3] * 100:.0f}% of target).")

    w.p(("Disclaimer: ", {"bold": True}), "These figures are synthetic benchmark results from the configuration and commands "
        "documented here. Application performance depends on workload, client configuration and network conditions.", size=9)
    w.p("---------- End of Document ----------", align=WD_ALIGN_PARAGRAPH.CENTER, color=MUTED, size=9)

    if marker is not None:   # move the report to where {{content}} is, then drop the marker
        new = [el for el in list(body) if id(el) not in existing and el.tag != qn("w:sectPr")]
        anchor = marker._p
        for el in new:
            anchor.addprevious(el)
        anchor.getparent().remove(anchor)
    # the template's own body images are no longer used: drop them so the report doesn't carry them
    from docx.opc.constants import RELATIONSHIP_TYPE as RT
    xml = d.element.xml
    for rid, rel in list(d.part.rels.items()):
        if rel.reltype == RT.IMAGE and f'"{rid}"' not in xml:
            d.part.drop_rel(rid)
    d.core_properties.title = title
    d.core_properties.author = author
    out.parent.mkdir(parents=True, exist_ok=True)
    d.save(str(out))
    return {"file": out.name, "runs": len(runs), "rows": len(rows), "template": "uploaded" if template else "built-in",
            "content_marker": marker is not None}


def write_summary(runs: list[dict], out: Path, *, site: str, unit: str = "GB", vms: str = "", tenant: dict | None = None,
                  clients: list[dict] | None = None, version: str = "", **_ignored) -> None:
    """The .md summary shown in the Logs tab (written by the PowerPoint report too; used when only Word is made)."""
    data = pr.prepare(runs)
    tenant = tenant or {}
    md = [f"# Performance report - {site}", "",
          f"- Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}" + (f" (VAST Test Bench v{version})" if version else ""),
          f"- Runs: {len(runs)} · clients: {', '.join(data['all_clients']) or '-'}", f"- Period: {data['period'] or '-'}",
          f"- VMS: {vms or '-'} · tenant: {tenant.get('tenant_name') or '-'}", "- Contents: elbencho read and write results only",
          f"- Word: `{out.with_suffix('.docx').name}` - download it with the ⤓ docx button", "",
          f"| Run | Operation | Clients | {unit}/s last | {unit}/s first | IOPS last | Time taken s | P99 latency |",
          "|---|---|---|---:|---:|---:|---:|---:|"]
    for r in data["rows"]:
        t = r["started"].strftime("%Y-%m-%d %H:%M") if r["started"] else ""
        md.append(f"| {r['label']} · {t} | {r['operation']}{' · ' + r['profile'] if r.get('profile') else ''} | {', '.join(r['clients'])} | {pr._fmt(pr._tp(r['tp_last'], unit))} | "
                  f"{pr._fmt(pr._tp(r['tp_first'], unit))} | {pr._fmt(r['iops_last'], 0)} | {pr._fmt(r['elapsed'], 1)} | "
                  f"{pr._ms((r['lat'] or {}).get('p99'))} |")
    out.write_text("\n".join(md) + "\n")
