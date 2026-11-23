#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
"""
解析层：把模型空间实体抽取成可比较的记录表

拓展能力（相对 MVP）：
- INSERT 块内实体递归展开（blocks.expand=True 时，子图元参与 diff）
- HATCH 填充边界提取（纳入存在性/位置比对，关闭"填充删除漏检"）
- 图层过滤 / 实体类型开关（来自 config）
- PROXY_ENTITY 跳过 + 在 load 时向上层报告
"""
from __future__ import annotations
import math
import hashlib
from ezdxf.bbox import extents
from ezdxf.entities.boundary_paths import BoundaryPaths, flatten_to_polyline_path
from ezdxf.path import make_path
from . import geometry as G
from .config import is_layer_focused, type_enabled


def _finalize(rec: dict) -> dict:
    b = rec["bbox"]
    rec["center"] = ((b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0)
    rec["geom"] = G.geom_signature(rec)
    rec["summary"] = G.describe(rec)
    return rec


def _visible_len(txt: str) -> float:
    """MTEXT/TEXT 的可见字符数（去掉格式控制码），用于估算文字横向占位。

    不这么算的话，'Ra3.2' 和一大段说明文字的包围盒会一样大，
    RTree 取候选、圈注框大小都会失真。
    """
    s = G.clean_mtext(txt or "")
    if not s:
        return 1.0
    # %%c / %%d / %%p 各自只渲染成一个可见符号（φ / ° / ±）
    import re as _re
    s = _re.sub(r"%%[cCdDpP]", "X", s)
    return float(max(len(s), 1))


def _transform_point(p, tx, ty, rot_deg, sx, sy):
    x, y = p[0] * sx, p[1] * sy
    a = math.radians(rot_deg)
    rx = x * math.cos(a) - y * math.sin(a)
    ry = x * math.sin(a) + y * math.cos(a)
    return (rx + tx, ry + ty)


def _xy(v):
    """兼容 Vec3(.x/.y) 与 (x,y,z) 元组两种表示。"""
    try:
        return (float(v.x), float(v.y))
    except Exception:
        return (float(v[0]), float(v[1]))


def _boundary_points(path):
    """将任意 HATCH 边界展平成点列（弧/椭圆/样条也参与面积计算）。"""
    raw = list(getattr(path, "vertices", []) or [])
    if raw:
        # PolylinePath 的第三个值是 bulge；没有 bulge 时保留原始点，避免不必要的近似。
        has_bulge = any(len(v) > 2 and abs(float(v[2] or 0.0)) > 1e-12 for v in raw)
        if not has_bulge:
            return [_xy(v) for v in raw]
        try:
            wrapper = BoundaryPaths([path])
            wrapper.polyline_to_edge_paths(just_with_bulge=True)
            path = wrapper.paths[0]
        except Exception:
            pass
    try:
        # 相对误差随图纸尺度缩放；flatten_to_polyline_path 会同时处理
        # LineEdge、ArcEdge、EllipseEdge、SplineEdge。
        rough = []
        for edge in getattr(path, "edges", []) or []:
            for name in ("start", "end", "center", "major_axis"):
                v = getattr(edge, name, None)
                if v is not None:
                    rough.append(_xy(v))
            rough.extend(_xy(v) for v in (getattr(edge, "control_points", []) or []))
            rough.extend(_xy(v) for v in (getattr(edge, "fit_points", []) or []))
        scale = 1.0
        if len(rough) > 1:
            rb = G.bbox_of_points(rough)
            scale = max(rb[2] - rb[0], rb[3] - rb[1], 1.0)
        flat = flatten_to_polyline_path(path, max(1e-4, scale * 1e-5), segments=32)
        return [_xy(v) for v in (flat.vertices or [])]
    except Exception:
        # 损坏的样条仍保留控制点/端点，至少能进入变更指纹而不是静默丢失。
        pts = []
        for edge in getattr(path, "edges", []) or []:
            for name in ("start", "end", "control_points", "fit_points"):
                value = getattr(edge, name, None)
                if isinstance(value, (list, tuple)):
                    pts.extend(_xy(v) for v in value)
                elif value is not None:
                    pts.append(_xy(value))
        return pts


def _polygon_area(points):
    if len(points) < 3:
        return 0.0
    return 0.5 * sum(
        points[i][0] * points[(i + 1) % len(points)][1]
        - points[(i + 1) % len(points)][0] * points[i][1]
        for i in range(len(points))
    )


def _polyline_perimeter(points):
    if len(points) < 2:
        return 0.0
    return sum(
        math.hypot(points[(i + 1) % len(points)][0] - points[i][0],
                   points[(i + 1) % len(points)][1] - points[i][1])
        for i in range(len(points))
    )


def _canonical_path(points, bbox):
    """Normalize a closed boundary so translation and start-point order do not matter."""
    if len(points) > 1 and points[0] == points[-1]:
        points = points[:-1]
    if not points:
        return ()
    bw = max(abs(bbox[2] - bbox[0]), 1.0)
    bh = max(abs(bbox[3] - bbox[1]), 1.0)
    # shortcut: 1e-5 normalized quantization filters CAD round-off; use exact curves for survey-grade tolerance.
    seq = [(round((x - bbox[0]) / bw, 5), round((y - bbox[1]) / bh, 5))
           for x, y in points]
    candidates = []
    for values in (seq, list(reversed(seq))):
        first = min(values)
        for i, value in enumerate(values):
            if value == first:
                candidates.append(tuple(values[i:] + values[:i]))
    return min(candidates) if candidates else tuple(seq)


def _shape_fingerprint(paths, bbox):
    normalized = sorted(_canonical_path(points, bbox) for points in paths if points)
    if not normalized:
        return ""
    return hashlib.sha1(repr(normalized).encode("ascii")).hexdigest()[:16]


def _polyline_points(e):
    """读取多段线真实曲线边界，包含 bulge 圆弧段。"""
    if e.dxftype() == "LWPOLYLINE":
        raw = list(e.get_points())
        points = [(float(v[0]), float(v[1])) for v in raw]
        has_bulge = any(len(v) > 4 and abs(float(v[4] or 0.0)) > 1e-12 for v in raw)
    else:
        vertices_value = getattr(e, "vertices", [])
        vertices = list(vertices_value() if callable(vertices_value) else vertices_value)
        points = [(float(v.dxf.location.x), float(v.dxf.location.y)) for v in vertices]
        has_bulge = any(abs(float(getattr(v.dxf, "bulge", 0.0) or 0.0)) > 1e-12
                        for v in vertices)
    if not has_bulge or len(points) < 2:
        return points
    try:
        path = make_path(e)
        rough = G.bbox_of_points(points)
        scale = max(rough[2] - rough[0], rough[3] - rough[1], 1.0)
        flat = list(path.flattening(max(1e-4, scale * 1e-5), segments=32))
        if len(flat) >= 2:
            return [_xy(v) for v in flat]
    except Exception:
        pass
    return points


def _hatch_geometry(e):
    """返回 HATCH 的包围盒、边界点数和边界面积（图纸单位²）。"""
    pts = []
    path_points = []
    signed_areas = []
    perimeters = []
    try:
        for path in e.paths:
            path_pts = _boundary_points(path)
            if len(path_pts) >= 2:
                pts.extend(path_pts)
            if len(path_pts) >= 3:
                path_points.append(path_pts)
                signed_areas.append(_polygon_area(path_pts))
                perimeters.append(_polyline_perimeter(path_pts))
    except Exception:
        pass
    if len(pts) < 2:
        return None
    # DXF 外边界通常逆时针、孔洞顺时针；保留符号后求净面积。
    area = abs(sum(signed_areas)) if signed_areas else 0.0
    bbox = G.bbox_of_points(pts)
    return bbox, len(pts), area, sum(perimeters), _shape_fingerprint(path_points, bbox)


def _hatch_bbox(e):
    """从 HATCH 边界路径提取包围盒，同时返回边界面积。"""
    geometry = _hatch_geometry(e)
    if not geometry:
        return None
    bbox, n, area, perimeter, shape = geometry
    return (bbox, n, area, perimeter, shape)


def _extract_primitive(e, layer, cfg, block=None, source_id=None):
    """抽取单个"原始图元"(LINE/CIRCLE/ARC/POLY/TEXT/MTEXT/HATCH)为记录列表(多段线可能含子对象)。"""
    t = e.dxftype()
    if not type_enabled(cfg, t):
        return []
    if t == "LINE":
        s = (e.dxf.start.x, e.dxf.start.y); en = (e.dxf.end.x, e.dxf.end.y)
        bbox = G.bbox_of_points([s, en]); dx, dy = en[0] - s[0], en[1] - s[1]
        rec = {"type": t, "layer": layer, "bbox": bbox, "block": block,
               "attrs": {"dx": dx, "dy": dy}, "render": [("line", s, en)]}
    elif t == "CIRCLE":
        c = (e.dxf.center.x, e.dxf.center.y); r = e.dxf.radius
        bbox = (c[0] - r, c[1] - r, c[0] + r, c[1] + r)
        rec = {"type": t, "layer": layer, "bbox": bbox, "block": block,
               "attrs": {"r": r, "area": math.pi * r * r,
                         "area_m2": math.pi * r * r / 1e6},
               "render": [("circle", c, r)]}
    elif t == "ARC":
        c = (e.dxf.center.x, e.dxf.center.y); r = e.dxf.radius
        bbox = (c[0] - r, c[1] - r, c[0] + r, c[1] + r)
        rec = {"type": t, "layer": layer, "bbox": bbox, "block": block,
               "attrs": {"r": r, "a0": e.dxf.start_angle, "a1": e.dxf.end_angle},
               "render": [("arc", c, r, e.dxf.start_angle, e.dxf.end_angle)]}
    elif t in ("LWPOLYLINE", "POLYLINE"):
        pts = _polyline_points(e)
        if not pts:
            return []
        bbox = G.bbox_of_points(pts)
        closed = bool(getattr(e, "closed", getattr(e, "is_closed", False)))
        area = abs(_polygon_area(pts)) if closed and len(pts) >= 3 else 0.0
        rec = {"type": t, "layer": layer, "bbox": bbox, "block": block,
               "attrs": {"n": len(pts), "bw": bbox[2] - bbox[0], "bh": bbox[3] - bbox[1],
                         "area": area, "area_m2": area / 1e6,
                         "perimeter": _polyline_perimeter(pts) if closed else 0.0,
                         "shape": _shape_fingerprint([pts], bbox)},
               "render": [("poly", pts, closed)]}
    elif t in ("TEXT", "MTEXT"):
        ins = (e.dxf.insert.x, e.dxf.insert.y)
        txt = getattr(e.dxf, "text", "") or ""
        # MTEXT 的字高属性是 char_height，TEXT 才是 height。
        # 之前统一按 height 取，导致 MTEXT 恒取默认值 2.5 —— 字高改动检测不到，
        # 且包围盒与实际文字大小完全脱节。
        h = getattr(e.dxf, "char_height", None) or getattr(e.dxf, "height", None) or 2.5
        # 包围盒按字高 + 文字长度估算横向占位，否则长短文字的框一样大，
        # RTree 取候选和圈注都会失真。
        vis = _visible_len(txt)
        w = max(h * 0.75 * vis, h)
        bbox = (ins[0] - h * 0.3, ins[1] - h * 0.3, ins[0] + w, ins[1] + h * 1.2)
        rec = {"type": t, "layer": layer, "bbox": bbox, "block": block,
               "attrs": {"text": txt, "h": h}, "render": [("text", ins, txt)]}
    elif t == "HATCH":
        res = _hatch_bbox(e)
        if not res:
            return []
        bbox, n, area, perimeter, shape = res
        pat = getattr(e.dxf, "pattern_name", "") or ("solid" if e.solid_fill else "-")
        rec = {"type": t, "layer": layer, "bbox": bbox, "block": block,
               "attrs": {"pattern": pat, "n": n,
                         "bw": bbox[2] - bbox[0], "bh": bbox[3] - bbox[1],
                         "area": area, "perimeter": perimeter,
                         "area_m2": area / 1e6, "shape": shape,
                         "area_src": "boundary"},
               "render": [("hatch", bbox)]}
    else:
        return []
    if source_id is None and block is None:
        handle = getattr(e.dxf, "handle", None)
        if handle:
            source_id = ("entity", str(handle))
    if source_id is not None:
        rec["source_id"] = source_id
    return [_finalize(rec)]


def _dim_measurement(e):
    """计算尺寸标注的真实测量值。

    DXF 里 DIMENSION 的 text 常写作 '%%C<>' —— '<>' 是 AutoCAD 的"运行时
    测量"占位符，真实数值并不落盘，而是打开图纸时按标注几何算出来的。
    所以"标注测量值丢失"不是转换器的问题，而是必须自己重算。
    ezdxf 的 get_measurement() 已实现各类型（线性/半径/角度/坐标）的解算。
    """
    try:
        v = e.get_measurement()
        if v is None:
            return None
        v = float(v)
        if not math.isfinite(v):
            return None
        return round(v, 2)
    except Exception:
        return None


def _mleader_text(e) -> str:
    """多重引线文字：优先 ezdxf 的 mtext context，回退展开图元。

    注意 ezdxf 1.4 中 has_mtext_content 是**属性**而非方法。
    部分图纸的文字存在二进制 context data 里，两种转换器都解不出，
    此时返回空串（上层会标注"文字不可读"而不是瞎猜）。
    """
    try:
        if e.has_mtext_content:
            t = e.get_mtext_content() or ""
            if t.strip():
                return t
    except Exception:
        pass
    try:
        t = e.context.mtext.default_content or ""
        if t.strip():
            return t
    except Exception:
        pass
    try:
        for ve in e.virtual_entities():
            if ve.dxftype() in ("MTEXT", "TEXT"):
                t = getattr(ve.dxf, "text", "") or ""
                if t.strip():
                    return t
    except Exception:
        pass
    return ""


def _extract_complex(e, layer, cfg, block=None):
    """复杂类型兜底：DIMENSION/MULTILEADER/LEADER 用 virtual_entities 展开求包围盒。"""
    t = e.dxftype()
    if not type_enabled(cfg, t):
        return []
    try:
        ves = list(e.virtual_entities())
        b = extents(ves)
    except Exception:
        try:
            b = e.bbox()
        except Exception:
            return []
    if b is None:
        return []
    bbox = (b.extmin.x, b.extmin.y, b.extmax.x, b.extmax.y)
    attrs = {}
    try:
        if t == "DIMENSION":
            # 真实测量值优先（由标注几何解算），失败才回退到"最长展开线"估算
            m = _dim_measurement(e)
            if m is not None:
                attrs["measure"] = m
                attrs["measure_src"] = "ezdxf"
            else:
                meas = 0.0
                for ve in ves:
                    if ve.dxftype() == "LINE":
                        L = math.hypot(ve.dxf.end.x - ve.dxf.start.x,
                                       ve.dxf.end.y - ve.dxf.start.y)
                        meas = max(meas, L)
                if meas > 0:
                    attrs["measure"] = round(meas, 2)
                    attrs["measure_src"] = "estimate"
            attrs["dimtype"] = int(getattr(e.dxf, "dimtype", 0) or 0)
            attrs["dim_text"] = getattr(e.dxf, "text", "") or ""
            # 收紧包围盒：剔除明显过长的延伸线，仅保留尺寸线/箭头/数值紧凑范围
            lines = [(ve.dxf.start, ve.dxf.end) for ve in ves if ve.dxftype() == "LINE"]
            if len(lines) >= 3:
                lengths = sorted(math.hypot(en.x - st.x, en.y - st.y) for st, en in lines)
                thr = lengths[int(len(lengths) * 0.75)]
                pts = []
                for st, en in lines:
                    if math.hypot(en.x - st.x, en.y - st.y) <= thr * 1.5:
                        pts += [(st.x, st.y), (en.x, en.y)]
                for ve in ves:
                    if ve.dxftype() in ("MTEXT", "TEXT"):
                        try:
                            bb = ve.bbox()
                            if bb:
                                pts += [(bb.extmin.x, bb.extmin.y), (bb.extmax.x, bb.extmax.y)]
                        except Exception:
                            pass
                if len(pts) >= 2:
                    bbox = G.bbox_of_points(pts)
        elif t == "MULTILEADER":
            tx = _mleader_text(e)
            if tx:
                attrs["text"] = tx
        elif t == "LEADER":
            for ve in ves:
                if ve.dxftype() in ("MTEXT", "TEXT"):
                    tx = getattr(ve.dxf, "text", "") or ""
                    if tx:
                        attrs["text"] = tx
                        break
    except Exception:
        pass
    rec = {"type": t, "layer": layer, "bbox": bbox, "block": block,
           "attrs": attrs, "render": [("complex", bbox)]}
    handle = getattr(e.dxf, "handle", None)
    if handle and block is None:
        rec["source_id"] = ("entity", str(handle))
    return [_finalize(rec)]


def _expand_insert(doc, e, cfg, tx=0.0, ty=0.0, rot=0.0, sx=1.0, sy=1.0, depth=0):
    """递归展开 INSERT：把块定义内图元变换到世界坐标后抽取。
    depth 防止块自引用死循环。"""
    out = []
    if depth > 6:
        return out
    name = e.dxf.name
    layout = doc.blocks.get(name)
    if layout is None:
        return out
    # 累积变换：本层插入点的旋转/缩放叠加到上层
    itx, ity = e.dxf.insert.x, e.dxf.insert.y
    irot = getattr(e.dxf, "rotation", 0.0) or 0.0
    isx = getattr(e.dxf, "xscale", 1.0) or 1.0
    isy = getattr(e.dxf, "yscale", 1.0) or 1.0
    # 世界插入点 = 上层变换(本层 insert)
    wx, wy = _transform_point((itx, ity), tx, ty, rot, sx, sy)
    # 合成旋转/缩放（仅近似：块缩放作用于子图元，旋转叠加）
    srot = rot + irot
    ssx, ssy = sx * isx, sy * isy
    for sub in layout:
        st = sub.dxftype()
        if st == "INSERT":
            out += _expand_insert(doc, sub, cfg, wx, wy, srot, ssx, ssy, depth + 1)
        else:
            # 子图元几何按合成变换投影到世界
            prims = _extract_primitive(sub, sub.dxf.layer, cfg, block=name)
            for rec in prims:
                b = rec["bbox"]
                corners = [(b[0], b[1]), (b[2], b[1]), (b[2], b[3]), (b[0], b[3])]
                wcorners = [_transform_point(c, wx, wy, srot, ssx, ssy) for c in corners]
                rec["bbox"] = G.bbox_of_points(wcorners)
                rec["layer"] = sub.dxf.layer if sub.dxf.layer != "0" else e.dxf.layer
                # render 也投影（用于绘制）
                rec["render"] = _project_render(rec["render"], wx, wy, srot, ssx, ssy)
                out.append(_finalize(rec))
    return out


def _project_render(render, tx, ty, rot_deg, sx, sy):
    a = math.radians(rot_deg)
    ca, sa = math.cos(a), math.sin(a)

    def tf(p):
        x, y = p[0] * sx, p[1] * sy
        return (x * ca - y * sa + tx, x * sa + y * ca + ty)
    out = []
    for kind, *data in render:
        if kind == "line":
            s, en = data
            out.append(("line", tf(s), tf(en)))
        elif kind == "circle":
            c, r = data
            out.append(("circle", tf(c), r * max(abs(sx), abs(sy))))
        elif kind == "arc":
            c, r, a0, a1 = data
            out.append(("arc", tf(c), r * max(abs(sx), abs(sy)), a0 + rot_deg, a1 + rot_deg))
        elif kind == "poly":
            pts, closed = data
            out.append(("poly", [tf(p) for p in pts], closed))
        elif kind == "text":
            p, txt = data
            out.append(("text", tf(p), txt))
        else:
            out.append((kind, *data))
    return out


def extract_entities(doc, cfg) -> tuple[list, dict]:
    """抽取模型空间全部实体；返回 (记录表, 元信息)。
    元信息含 proxy 命中、被跳过的类型统计，供上层报告。"""
    msp = doc.modelspace()
    recs = []
    meta = {"proxy": False, "skipped_types": {}, "block_expanded": 0}
    for e in msp:
        t = e.dxftype()
        if t == "PROXY_ENTITY":
            meta["proxy"] = True
            continue
        layer = e.dxf.layer
        if not is_layer_focused(cfg, layer):
            continue
        if not type_enabled(cfg, t):
            meta["skipped_types"][t] = meta["skipped_types"].get(t, 0) + 1
            continue
        if t == "INSERT":
            if cfg["blocks"]["expand"]:
                sub = _expand_insert(doc, e, cfg)
                meta["block_expanded"] += len(sub)
                recs += sub
            else:
                ins = (e.dxf.insert.x, e.dxf.insert.y)
                rec = {"type": t, "layer": layer, "block": None,
                       "bbox": (ins[0] - 5, ins[1] - 5, ins[0] + 5, ins[1] + 5),
                       "attrs": {"name": e.dxf.name},
                       "render": [("insert", ins, e.dxf.name)]}
                handle = getattr(e.dxf, "handle", None)
                if handle:
                    rec["source_id"] = ("entity", str(handle))
                recs.append(_finalize(rec))
        elif t in ("LINE", "CIRCLE", "ARC", "LWPOLYLINE", "POLYLINE",
                   "TEXT", "MTEXT", "HATCH"):
            recs += _extract_primitive(e, layer, cfg)
        else:
            recs += _extract_complex(e, layer, cfg)
    return recs, meta
