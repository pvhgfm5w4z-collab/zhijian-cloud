#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
"""
报告层：把变更清单落地为多种格式

- TXT：人读变更说明（分类统计 + 每处坐标/内容，删除项含"原本内容"）
- JSON：结构化清单（可接 LLM / 下游系统）
- DOCX：python-docx 生成（Word，闭环交付，中文字体）
- PDF：reportlab 生成（摘要 + 统计 + 明细表 + 对比图/聚焦图），中文用思源/Noto 字体避免黑块
- HTML：自包含网页（base64 内嵌图片 + 明细表 + 语义摘要）
"""
from __future__ import annotations
import os
import json
import base64
from collections import defaultdict
from . import llm as LLM


# ------------------------- 中文字体（PDF 关键） -------------------------
# reportlab 默认 Helvetica 不含中文字形，会渲染成黑块；需注册 CJK TTF/TTC。
_CJK_FONT_NAME = None


def _discover_cjk_font():
    """在常见路径里找一个支持中文的字体文件，返回 (字体名, 路径)。"""
    candidates = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
        "/opt/sandbox-prefetched/blobs/plugin/pl_lHTdrkkqJ5HV/latest/scripts/assets/fonts/SourceHanSansSC-Subset.otf",
    ]
    import glob
    # 再补一轮模糊搜索，兜底
    candidates += glob.glob("/usr/share/fonts/**/*CJK*.ttc", recursive=True)
    candidates += glob.glob("/usr/share/fonts/**/*SourceHan*.otf", recursive=True)
    candidates += glob.glob("/usr/share/fonts/**/*wqy*.ttf", recursive=True)
    seen = set()
    for p in candidates:
        if p in seen:
            continue
        seen.add(p)
        if os.path.exists(p):
            return p
    return None


def _register_cjk():
    """注册中文字体到 reportlab（只做一次）。返回字体名或空串（空串=用默认西文字体，会黑块）。

    优先用 reportlab 内置 CID 中文字体（STSong-Light，无需外部文件，最稳）；
    若该环境不含 CID 字体，则回退到系统 TrueType/OpenType 中文字体。
    """
    global _CJK_FONT_NAME
    if _CJK_FONT_NAME is not None:
        return _CJK_FONT_NAME
    from reportlab.pdfbase import pdfmetrics
    # 1) reportlab 内置 CID 中文字体（宋体），无需外部文件，解决中文黑块
    try:
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
        _CJK_FONT_NAME = "STSong-Light"
        return _CJK_FONT_NAME
    except Exception:
        pass
    # 2) 回退：系统 TTF/TTC 中文字体（仅支持 TrueType 轮廓，CFF 轮廓会失败）
    from reportlab.pdfbase.ttfonts import TTFont
    path = _discover_cjk_font()
    if not path:
        print("[warn] 未找到中文字体，PDF 中文可能显示为方块；请安装 fonts-noto-cjk 或思源黑体。")
        _CJK_FONT_NAME = ""
        return _CJK_FONT_NAME
    name = "CJKFont"
    try:
        if path.lower().endswith(".ttc"):
            pdfmetrics.registerFont(TTFont(name, path, subfontIndex=0))
        else:
            pdfmetrics.registerFont(TTFont(name, path))
        _CJK_FONT_NAME = name
    except Exception as ex:
        print(f"[warn] 中文字体注册失败: {ex}；PDF 中文可能显示为方块。")
        _CJK_FONT_NAME = ""
    return _CJK_FONT_NAME


def write_txt_report(changes, out_path, summary=None):
    cnt = defaultdict(int)
    for c in changes:
        cnt[c["kind"]] += 1
    lines = ["CAD 图纸修改识别报告 (TXT)", "=" * 56,
             f"汇总: 新增 {cnt['ADDED']} | 删除 {cnt['DELETED']} | "
             f"修改 {cnt['MODIFIED']} | 移动 {cnt['MOVED']}", ""]
    if summary:
        lines += ["【智能摘要】", summary, ""]
    del_by_type = defaultdict(int)
    for c in changes:
        if c["kind"] == "DELETED":
            del_by_type[c["type"]] += 1
    if del_by_type:
        lines.append("【删除内容分类】")
        for t, n in sorted(del_by_type.items(), key=lambda kv: -kv[1]):
            lines.append(f"  - {t}: {n} 个")
        lines.append("")
    for kind in ("DELETED", "MODIFIED", "MOVED", "ADDED"):
        items = [c for c in changes if c["kind"] == kind]
        if not items:
            continue
        title = {"DELETED": "删除明细", "MODIFIED": "修改明细",
                 "MOVED": "移动明细", "ADDED": "新增明细"}[kind]
        lines.append(f"--- {title} ({len(items)}) ---")
        for i, c in enumerate(items, 1):
            b = c["bbox"]
            pos = f"x∈[{b[0]:.1f}, {b[2]:.1f}]  y∈[{b[1]:.1f}, {b[3]:.1f}]"
            lines.append(f"  [{i}] 类型={c['type']}  图层={c['layer']}")
            lines.append(f"      坐标: {pos}")
            if kind == "DELETED":
                lines.append(f"      原本内容: {c.get('old', '-')}")
            elif kind == "ADDED":
                lines.append(f"      新增内容: {c.get('new', '-')}")
            else:
                lines.append(f"      原本内容: {c.get('old', '-')}")
                lines.append(f"      现在内容: {c.get('new', '-')}")
            if c.get("movement"):
                m = c["movement"]
                lines.append(f"      实体位移: dx={m['dx']:+.3f}, dy={m['dy']:+.3f}（图纸单位）")
        lines.append("")
    lines.append("说明:")
    lines.append("  - 坐标为图纸单位(与 DXF 一致)。")
    lines.append("  - DIMENSION 测量值由 ezdxf 依标注定义点实时重算（DXF 只存 <> 占位符，")
    lines.append("    真实数值不落盘，与用哪个转换器无关）；极少数无法重算的才退回包围盒估算。")
    lines.append("  - MULTILEADER 文字存于二进制 context data，当前无法读出，如实标注为不可读。")
    # 用真实输出前缀，别硬编码 result——用户 -o 指定别的名字时会对不上号
    stem = os.path.splitext(os.path.basename(out_path))[0]
    lines.append(f"  - 详见同目录 {stem}.dxf (CHANGES 图层圈注) / {stem}.png (对比图)。")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return out_path


def write_json(changes, out_path, meta=None, summary=None):
    payload = {"summary_counts": _counts(changes),
               "meta": meta or {}, "summary": summary, "changes": changes}
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return out_path


def _counts(changes):
    cnt = defaultdict(int)
    for c in changes:
        cnt[c["kind"]] += 1
    return cnt


# ------------------------- PDF -------------------------
def write_pdf(changes, out_path, cfg, images, summary_obj):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                    TableStyle, Image as RLImage)

    cjk = _register_cjk()
    doc = SimpleDocTemplate(out_path, pagesize=A4,
                            topMargin=1.5 * cm, bottomMargin=1.5 * cm)
    ss = getSampleStyleSheet()
    # 克隆为带中文字体的样式（关键：解决中文黑块）
    base = cjk or "Helvetica"
    title_st = ParagraphStyle("CJKTitle", parent=ss["Title"], fontName=base)
    h2_st = ParagraphStyle("CJKH2", parent=ss["Heading2"], fontName=base)
    h3_st = ParagraphStyle("CJKH3", parent=ss["Heading3"], fontName=base)
    body_st = ParagraphStyle("CJKBody", parent=ss["BodyText"], fontName=base)
    story = []
    story.append(Paragraph("CAD 图纸修改智能识别报告", title_st))
    cnt = _counts(changes)
    story.append(Paragraph(
        f"新增 <b>{cnt['ADDED']}</b> &nbsp;|&nbsp; 删除 <b>{cnt['DELETED']}</b> "
        f"&nbsp;|&nbsp; 修改 <b>{cnt['MODIFIED']}</b> &nbsp;|&nbsp; 移动 "
        f"<b>{cnt['MOVED']}</b>", h2_st))

    src = summary_obj.get("source", "rule") if summary_obj else "rule"
    stxt = summary_obj.get("text", "") if summary_obj else ""
    if stxt:
        story.append(Paragraph(f"智能摘要（来源：{src}）", h3_st))
        for ln in stxt.split("\n"):
            if ln.strip():
                story.append(Paragraph(ln.replace("&", "&amp;"), body_st))
        story.append(Spacer(1, 0.3 * cm))

    # 图片
    for img in images:
        if os.path.exists(img):
            try:
                story.append(RLImage(img, width=17 * cm,
                                     height=17 * cm * _ratio(img)))
                story.append(Spacer(1, 0.2 * cm))
            except Exception:
                pass

    # 明细表（表头/内容均用中文字体，单元格用 Paragraph 以支持 CJK）
    story.append(Paragraph("变更明细", h3_st))
    head = ["#", "类型", "图层", "变更", "坐标(X/Y范围)", "内容"]
    rows = [head]
    for i, c in enumerate(changes, 1):
        b = c["bbox"]
        if c["kind"] == "DELETED":
            content = c.get("old", "-")
        elif c["kind"] == "ADDED":
            content = c.get("new", "-")
        else:
            content = f"{c.get('old','-')} → {c.get('new','-')}"
        if c.get("movement"):
            m = c["movement"]
            content += f"（位移 dx={m['dx']:+.3f}, dy={m['dy']:+.3f}）"
        coord = f"[{b[0]:.0f},{b[2]:.0f}]×[{b[1]:.0f},{b[3]:.0f}]"
        rows.append([str(i), c["type"], c["layer"], c["kind"],
                    coord, _clip(content, 40)])
    # 单元格用 Paragraph 包裹，确保 CJK 正确渲染
    data = []
    for r_i, row in enumerate(rows):
        if r_i == 0:
            data.append(row)
        else:
            data.append([Paragraph(str(x), body_st) for x in row])
    tbl = Table(data, colWidths=[1*cm, 2.4*cm, 2.6*cm, 2*cm, 3.4*cm, 5.6*cm],
                repeatRows=1)
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2c3e50")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), base),
        ("FONTSIZE", (0, 0), (-1, -1), 7.5),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.whitesmoke]),
    ]))
    story.append(tbl)
    doc.build(story)
    return out_path


def write_docx(changes, out_path, cfg, images, summary_obj):
    """生成 Word 报告（.docx）。中文显式设东亚字体，避免 Word 打开字体异常。"""
    from docx import Document
    from docx.shared import Pt, Inches, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn

    # 优先使用系统中文字体名；若客户机无此字体，Word 会自动回退到默认中文字体
    EAST_ASIAN = "微软雅黑"   # Word 里常见中文字体，缺失时回退

    doc = Document()
    # 默认 Normal 样式设中文字体（影响正文）
    normal = doc.styles["Normal"]
    normal.font.name = EAST_ASIAN
    normal.font.size = Pt(10.5)
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), EAST_ASIAN)

    def set_cjk(run, bold=False, size=10.5, color=None):
        run.font.name = EAST_ASIAN
        run.font.size = Pt(size)
        run.bold = bold
        if color:
            run.font.color.rgb = color
        rpr = run._element.get_or_add_rPr()
        rfonts = rpr.find(qn("w:rFonts"))
        if rfonts is None:
            rfonts = rpr.makeelement(qn("w:rFonts"), {})
            rpr.append(rfonts)
        rfonts.set(qn("w:eastAsia"), EAST_ASIAN)
        rfonts.set(qn("w:ascii"), EAST_ASIAN)
        rfonts.set(qn("w:hAnsi"), EAST_ASIAN)

    title = doc.add_heading("", level=0)
    trun = title.add_run("CAD 图纸修改智能识别报告")
    set_cjk(trun, bold=True, size=18)

    cnt = _counts(changes)
    stat = doc.add_paragraph()
    srun = stat.add_run(
        f"新增 {cnt['ADDED']}  |  删除 {cnt['DELETED']}  |  修改 {cnt['MODIFIED']}  |  移动 {cnt['MOVED']}")
    set_cjk(srun, bold=True, size=12, color=RGBColor(0x2c, 0x3e, 0x50))

    src = summary_obj.get("source", "rule") if summary_obj else "rule"
    stxt = summary_obj.get("text", "") if summary_obj else ""
    if stxt:
        h = doc.add_heading("", level=2)
        set_cjk(h.add_run(f"智能摘要（来源：{src}）"), bold=True, size=13)
        for ln in stxt.split("\n"):
            if ln.strip():
                p = doc.add_paragraph()
                set_cjk(p.add_run(ln))

    # 图片
    for img in images:
        if os.path.exists(img):
            try:
                doc.add_picture(img, width=Inches(6.3))
                doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
            except Exception:
                pass

    # 明细表
    h = doc.add_heading("", level=2)
    set_cjk(h.add_run(f"变更明细（{len(changes)} 处）"), bold=True, size=13)
    table = doc.add_table(rows=1, cols=6)
    table.style = "Light Grid Accent 1"
    hdr = ["#", "类型", "图层", "变更", "坐标(X/Y范围)", "内容"]
    for i, hcell in enumerate(table.rows[0].cells):
        set_cjk(hcell.paragraphs[0].add_run(hdr[i]), bold=True)
    for i, c in enumerate(changes, 1):
        b = c["bbox"]
        if c["kind"] == "DELETED":
            content = c.get("old", "-")
        elif c["kind"] == "ADDED":
            content = c.get("new", "-")
        else:
            content = f"{c.get('old','-')} → {c.get('new','-')}"
        if c.get("movement"):
            m = c["movement"]
            content += f"（位移 dx={m['dx']:+.3f}, dy={m['dy']:+.3f}）"
        coord = f"[{b[0]:.0f},{b[2]:.0f}]×[{b[1]:.0f},{b[3]:.0f}]"
        cells = table.add_row().cells
        vals = [str(i), c["type"], c["layer"], c["kind"], coord, _clip(content, 60)]
        for j, v in enumerate(vals):
            set_cjk(cells[j].paragraphs[0].add_run(v))
    doc.save(out_path)
    return out_path
    try:
        from PIL import Image as PILImage
        w, h = PILImage.open(img).size
        return h / w if w else 1.0
    except Exception:
        return 0.6


def _clip(s, n):
    s = str(s).replace("\n", " ")
    return s if len(s) <= n else s[:n] + "…"


# ------------------------- HTML -------------------------
def write_html(changes, out_path, cfg, images, summary_obj):
    cnt = _counts(changes)
    src = summary_obj.get("source", "rule") if summary_obj else "rule"
    stxt = summary_obj.get("text", "") if summary_obj else ""
    imgs_html = ""
    for img in images:
        if os.path.exists(img):
            with open(img, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("ascii")
            imgs_html += (f'<figure><img src="data:image/png;base64,{b64}" '
                          f'alt="{os.path.basename(img)}">'
                          f'<figcaption>{os.path.basename(img)}</figcaption></figure>')

    rows = ""
    for i, c in enumerate(changes, 1):
        b = c["bbox"]
        if c["kind"] == "DELETED":
            content = c.get("old", "-")
        elif c["kind"] == "ADDED":
            content = c.get("new", "-")
        else:
            content = f"{c.get('old','-')} → {c.get('new','-')}"
        if c.get("movement"):
            m = c["movement"]
            content += f"（位移 dx={m['dx']:+.3f}, dy={m['dy']:+.3f}）"
        rows += (f"<tr><td>{i}</td><td>{c['type']}</td><td>{c['layer']}</td>"
                 f"<td class='{c['kind']}'>{c['kind']}</td>"
                 f"<td>[{b[0]:.0f},{b[2]:.0f}]×[{b[1]:.0f},{b[3]:.0f}]</td>"
                 f"<td>{_clip(content, 80)}</td></tr>")

    stxt_html = "".join(f"<p>{ln}</p>" for ln in stxt.split("\n") if ln.strip())
    html = f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>CAD 图纸修改识别报告</title>
<style>
 body{{font-family:-apple-system,'Segoe UI',sans-serif;margin:24px;color:#222}}
 h1{{margin-bottom:4px}} .stat{{font-size:18px;margin:8px 0}}
 .stat b{{color:#2c3e50}} figure{{display:inline-block;margin:10px}}
 figure img{{max-width:720px;border:1px solid #ddd}} figcaption{{font-size:12px;color:#666}}
 table{{border-collapse:collapse;width:100%;font-size:13px;margin-top:10px}}
 th,td{{border:1px solid #ccc;padding:5px 7px;text-align:left;vertical-align:top}}
 th{{background:#2c3e50;color:#fff}}
 .ADDED{{color:green;font-weight:bold}} .DELETED{{color:red;font-weight:bold}}
 .MODIFIED{{color:orange;font-weight:bold}} .MOVED{{color:magenta;font-weight:bold}}
 .summary{{background:#f7f9fb;border-left:4px solid #2c3e50;padding:10px 14px}}
</style></head><body>
<h1>CAD 图纸修改智能识别报告</h1>
<div class="stat">新增 <b>{cnt['ADDED']}</b> &nbsp;|&nbsp; 删除 <b>{cnt['DELETED']}</b>
 &nbsp;|&nbsp; 修改 <b>{cnt['MODIFIED']}</b> &nbsp;|&nbsp; 移动 <b>{cnt['MOVED']}</b></div>
<div class="summary"><h3>智能摘要（来源：{src}）</h3>{stxt_html}</div>
<h3>对比图 / 聚焦放大图</h3>{imgs_html}
<h3>变更明细（{len(changes)} 处）</h3>
<table><thead><tr><th>#</th><th>类型</th><th>图层</th><th>变更</th>
<th>坐标(X/Y范围)</th><th>内容</th></tr></thead><tbody>{rows}</tbody></table>
</body></html>"""
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    return out_path
