#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
"""
几何工具（解析 / diff / 高亮 共用）

- 实体指纹 geom_signature：与位置无关，用于判断"是不是同一个实体(可能移动了)"
- geom_dist / pos_dist：几何距离与位置距离
- rec_shape：从实体抽取贴合几何本形的高亮形状（线→旋转细框、圆、弧、收紧矩形）
"""
from __future__ import annotations
import math
import re


def bbox_of_points(pts):
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def clean_mtext(s: str) -> str:
    """粗略去除 MTEXT 格式控制码，保留可见文字。"""
    s = s or ""
    s = re.sub(r"\{\\[a-zA-Z]+\b[^;}]*;", "", s)
    s = s.replace("}", "").replace("{", "")
    s = re.sub(r"\\[a-zA-Z]+", "", s)
    return s.strip()


def clean_dimtext(s: str) -> str:
    """尺寸标注文字清洗：去 MTEXT 控制码 + 还原 AutoCAD 特殊符号。"""
    s = clean_mtext(s or "")
    for code, rep in (("%%c", "φ"), ("%%C", "φ"), ("%%d", "°"), ("%%D", "°"),
                      ("%%p", "±"), ("%%P", "±"), ("%%%", "%")):
        s = s.replace(code, rep)
    return s.strip()


def describe(rec: dict) -> str:
    """生成实体的人读内容摘要（TXT 报告 / 删除项说明 / LLM 上下文）。"""
    t = rec["type"]; a = rec.get("attrs", {}); b = rec["bbox"]
    bw = b[2] - b[0]; bh = b[3] - b[1]
    if t == "LINE":
        dx, dy = a.get("dx", 0), a.get("dy", 0)
        return f"线条 长度≈{math.hypot(dx, dy):.1f} (Δx={dx:.1f}, Δy={dy:.1f})"
    if t == "CIRCLE":
        area = a.get("area")
        area_text = f" 面积≈{a.get('area_m2', area / 1e6):.3f}m²" if area is not None else ""
        return f"圆 半径={a.get('r'):.2f}{area_text}"
    if t == "ARC":
        return f"圆弧 半径={a.get('r'):.2f} 角度{a.get('a0')}°~{a.get('a1')}°"
    if t in ("LWPOLYLINE", "POLYLINE"):
        area = a.get("area", 0.0)
        area_text = f" 面积≈{a.get('area_m2', area / 1e6):.3f}m²" if area else ""
        return f"多段线 {a.get('n')}点 范围≈{bw:.1f}×{bh:.1f}{area_text}"
    if t in ("TEXT", "MTEXT"):
        # 带上字高：只改字号时文字内容一样，不显示字高报告会变成"文字 'A' → 文字 'A'"，
        # 读者根本看不出改了什么。
        return f"文字 {clean_mtext(a.get('text'))!r} 字高={a.get('h', 0):.2f}"
    if t == "INSERT":
        return f"块参照 {a.get('name')}"
    if t == "HATCH":
        area = a.get("area")
        area_m2 = a.get("area_m2", area / 1e6 if area is not None else None)
        area_text = f" 面积≈{area_m2:.3f}m²" if area_m2 is not None else ""
        perimeter = a.get("perimeter")
        perimeter_text = f" 周长≈{perimeter:.2f}" if perimeter is not None else ""
        return f"填充 范围≈{bw:.1f}×{bh:.1f}{area_text}{perimeter_text} 图案={a.get('pattern', '-')}"
    if t == "DIMENSION":
        m = a.get("measure")
        ov = clean_dimtext(a.get("dim_text", ""))
        if m is not None:
            tag = "" if a.get("measure_src") == "ezdxf" else "(估算)"
            base = f"尺寸标注 测量值={m}{tag}"
        else:
            base = f"尺寸标注 范围≈{bw:.1f}×{bh:.1f} (测量值不可算)"
        if ov and ov != "<>":
            base += f" 标注文字={ov!r}"
        return base
    if t == "MULTILEADER":
        txt = clean_mtext(a.get("text", ""))
        return f"多重引线 文字={txt!r}" if txt else "多重引线 (文字存于二进制context, 不可读)"
    if t == "LEADER":
        return "引线"
    return f"{t}"


def geom_signature(rec: dict) -> tuple:
    """与位置无关的几何指纹（块内实体用 块名+局部指纹，使同一图元在不同插入点匹配）。"""
    t = rec["type"]; a = rec.get("attrs", {})
    blk = rec.get("block")
    if blk:
        suffix = ("BLOCK", blk)
    else:
        suffix = ()
    if t == "LINE":
        L = math.hypot(a["dx"], a["dy"])
        ang = math.degrees(math.atan2(a["dy"], a["dx"])) % 180
        return ("LINE", round(L, 2), round(ang, 1)) + suffix
    if t == "CIRCLE":
        return ("CIRCLE", round(a["r"], 3)) + suffix
    if t == "ARC":
        return ("ARC", round(a["r"], 3), round(a["a0"], 1), round(a["a1"], 1)) + suffix
    if t in ("LWPOLYLINE", "POLYLINE"):
        return ("POLY", a["n"], round(a["bw"], 2), round(a["bh"], 2),
                round(a.get("area", 0.0), 2), round(a.get("perimeter", 0.0), 2),
                a.get("shape", "")) + suffix
    if t in ("TEXT", "MTEXT"):
        return ("TEXT", hash(a["text"]), round(a["h"], 2)) + suffix
    if t == "INSERT":
        return ("INSERT", a["name"])
    if t == "HATCH":
        return ("HATCH", round(a["bw"], 2), round(a["bh"], 2),
                round(a.get("area", 0.0), 2),
                round(a.get("perimeter", 0.0), 2), a.get("shape", "")) + suffix
    if t == "DIMENSION" and a.get("measure") is not None:
        # 测量值进指纹：尺寸从 189 改成 200 但位置几乎不动时，也能被判为变更
        return ("DIM", int(a.get("dimtype", 0) or 0), round(float(a["measure"]), 2),
                hash(clean_dimtext(a.get("dim_text", "")))) + suffix
    if t == "MULTILEADER":
        tx = clean_mtext(a.get("text", ""))
        if tx:
            return ("MLEADER", hash(tx)) + suffix
    bw = rec["bbox"][2] - rec["bbox"][0]
    bh = rec["bbox"][3] - rec["bbox"][1]
    return (t, round(bw, 1), round(bh, 1)) + suffix


# 「内容被改写」的几何罚值。不能太小（会把远处的增删误配成修改），
# 也不能是 inf（那样同位置的改动只能报成一删一增）。
# 配对判据是 pos_dist + 2*geom_dist < pos_tol*2，取 10 即要求中心距 <20，
# 与 pos_tol 量级一致：挪了窝的各归增删，原地改字的判为修改。
TEXT_EDIT_PENALTY = 10.0


def geom_dist(a: dict, b: dict) -> float:
    """几何距离；类型/关键属性不兼容返回 inf。"""
    sa, sb = a["geom"], b["geom"]
    if sa[0] != sb[0]:
        return math.inf
    if sa[0] == "LINE":
        return abs(sa[1] - sb[1]) + 0.5 * abs(sa[2] - sb[2])
    if sa[0] == "CIRCLE":
        return abs(sa[1] - sb[1])
    if sa[0] == "ARC":
        return abs(sa[1] - sb[1]) + 0.3 * (abs(sa[2] - sb[2]) + abs(sa[3] - sb[3]))
    if sa[0] == "POLY":
        bw = max(abs(sa[2]), abs(sb[2]), 1.0)
        bh = max(abs(sa[3]), abs(sb[3]), 1.0)
        aa = max(abs(sa[4]), abs(sb[4]), 1.0)
        pp = max(abs(sa[5]), abs(sb[5]), 1.0)
        d = min(20.0, 100.0 * (abs(sa[2] - sb[2]) / bw + abs(sa[3] - sb[3]) / bh))
        d += min(20.0, 500.0 * abs(sa[4] - sb[4]) / aa)
        d += min(8.0, 50.0 * abs(sa[5] - sb[5]) / pp)
        if len(sa) >= 7 and len(sb) >= 7 and sa[6] != sb[6]:
            d += 1.0
        return min(d, 20.0)
    if sa[0] == "HATCH":
        # 以相对尺度计算匹配分数：边界外框相同而内部填充变大时，
        # 仍能先配成同一实体，再由 GEOM_EPS 判为 MODIFIED。
        bw = max(abs(sa[1]), abs(sb[1]), 1.0)
        bh = max(abs(sa[2]), abs(sb[2]), 1.0)
        aa = max(abs(sa[3]), abs(sb[3]), 1.0)
        pp = max(abs(sa[4]), abs(sb[4]), 1.0)
        bbox_delta = min(20.0, 100.0 * (abs(sa[1] - sb[1]) / bw + abs(sa[2] - sb[2]) / bh))
        area_delta = min(20.0, 500.0 * abs(sa[3] - sb[3]) / aa)
        perimeter_delta = min(8.0, 50.0 * abs(sa[4] - sb[4]) / pp)
        shape_delta = 1.0 if len(sa) >= 6 and len(sb) >= 6 and sa[5] != sb[5] else 0.0
        return min(20.0, bbox_delta + area_delta + perimeter_delta + shape_delta)
    if sa[0] == "TEXT":
        # 文字被改写时不能返回 inf——那会让"删一处 + 加一处"代替"改一处"，
        # 用户看到的是同坐标的一删一增，非常费解。改为重罚但允许配对：
        # 罚 10 意味着 pos_dist 需 <20 才配得上，远处的增删仍各归各类。
        d = abs(sa[2] - sb[2])       # 字高差（只改字号也应算变更）
        if sa[1] != sb[1]:
            d += TEXT_EDIT_PENALTY   # 文字内容被改写
        return d
    if sa[0] == "INSERT":
        return 0.0 if sa[1] == sb[1] else math.inf   # 块名不同即不同图元
    if sa[0] == "DIM":
        if sa[1] != sb[1]:
            return math.inf          # 标注类型不同（线性/半径/角度）视为不兼容
        d = abs(sa[2] - sb[2])       # 测量值差
        if sa[3] != sb[3]:
            d += TEXT_EDIT_PENALTY   # 标注文字被人工改写：重罚但仍允许配成"修改"
        return d
    if sa[0] == "MLEADER":
        return 0.0 if sa[1] == sb[1] else TEXT_EDIT_PENALTY
    if len(sa) >= 3:
        return abs(sa[1] - sb[1]) + abs(sa[2] - sb[2])
    return 0.0


def pos_dist(a: dict, b: dict) -> float:
    return math.hypot(a["center"][0] - b["center"][0],
                     a["center"][1] - b["center"][1])


def adaptive_pad(w, h):
    size = max(w, h, 1.0)
    return max(3.0, min(0.08 * size, 25.0))


def rot_rect_verts(cx, cy, hl, hw, ang):
    a = math.radians(ang)
    ux, uy = math.cos(a), math.sin(a)
    vx, vy = -math.sin(a), math.cos(a)
    return [(cx + hl * ux - hw * vx, cy + hl * uy - hw * vy),
            (cx + hl * ux + hw * vx, cy + hl * uy + hw * vy),
            (cx - hl * ux + hw * vx, cy - hl * uy + hw * vy),
            (cx - hl * ux - hw * vx, cy - hl * uy - hw * vy)]


def rec_shape(rec: dict) -> dict:
    """从 rec 抽取高亮形状（DXF / PNG 共用），优先贴合几何本形。"""
    t = rec["type"]; r = rec.get("render", [])
    if t == "LINE":
        for kind, *data in r:
            if kind == "line":
                s, en = data
                cx = (s[0] + en[0]) / 2.0; cy = (s[1] + en[1]) / 2.0
                L = math.hypot(en[0] - s[0], en[1] - s[1])
                ang = math.degrees(math.atan2(en[1] - s[1], en[0] - s[0]))
                hw = max(3.0, min(0.08 * max(L, 1.0), 20.0))
                return {"kind": "rot_rect", "cx": cx, "cy": cy,
                        "hl": L / 2.0 + hw, "hw": hw, "ang": ang}
    if t == "CIRCLE":
        for kind, *data in r:
            if kind == "circle":
                c, r0 = data
                return {"kind": "circle", "cx": c[0], "cy": c[1], "r": r0}
    if t == "ARC":
        for kind, *data in r:
            if kind == "arc":
                c, r0, a0, a1 = data
                return {"kind": "arc", "cx": c[0], "cy": c[1], "r": r0, "a0": a0, "a1": a1}
    return {"kind": "rect"}
