#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
"""
对齐层：按配置配准旧版坐标系，默认不改变坐标

- translation：中位数估计全局平移（哈希分桶 O(n+m)，覆盖绝大多数场景）
- rigid：先估计整体旋转角（线角度直方图互相关），旋转旧版后再做平移配准。
         适合"新版相对旧版整体旋转/平移"的版本（如不同导出朝向）。

同源实体对 = (type, layer, geom) 完全相同（即"同一图元、可能整体变换了"）。
"""
from __future__ import annotations
import math
from collections import defaultdict
from statistics import median
import numpy as np
from .geometry import pos_dist


def estimate_translation(old, new):
    """哈希分桶：每个新实体只在同指纹桶里取位置最近的旧实体，估平移中位数。"""
    buckets = defaultdict(list)
    for or_ in old:
        buckets[(or_["type"], or_["layer"], or_["geom"])].append(or_)
    dxs, dys = [], []
    for nr in new:
        cands = buckets.get((nr["type"], nr["layer"], nr["geom"]))
        if not cands:
            continue
        best = min(cands, key=lambda or_: pos_dist(or_, nr))
        dxs.append(nr["center"][0] - best["center"][0])
        dys.append(nr["center"][1] - best["center"][1])
    if len(dxs) >= 3:
        return (median(dxs), median(dys))
    return (0.0, 0.0)


def _angle_hist(recs, bins=180):
    hist = np.zeros(bins)
    for r in recs:
        if r["type"] == "LINE":
            dx = r["attrs"].get("dx", 0.0); dy = r["attrs"].get("dy", 0.0)
            if dx == 0 and dy == 0:
                continue
            ang = math.degrees(math.atan2(dy, dx)) % 180
            hist[int(round(ang)) % bins] += 1
    return hist


def estimate_rotation(old, new, bins=180):
    """线角度直方图互相关，估计旧版需旋转的度数(关于原点)以对齐新版。"""
    ho = _angle_hist(old, bins)
    hn = _angle_hist(new, bins)
    if ho.sum() < 3 or hn.sum() < 3:
        return 0.0
    best_d, best_v = 0, -1
    for d in range(bins):
        # 把旧版直方图整体平移 d 后与新版比对（重叠量）
        v = sum(min(ho[(k - d) % bins], hn[k]) for k in range(bins))
        if v > best_v:
            best_v, best_d = v, d
    # 在峰值附近做 0.5° 精修
    for d in np.arange(max(0, best_d - 1), min(bins, best_d + 1), 0.5):
        v = sum(min(ho[int((k - d) % bins)], hn[k]) for k in range(bins))
        if v > best_v:
            best_v, best_d = v, d
    return float(best_d)


def rotate_point(p, deg):
    a = math.radians(deg)
    return (p[0] * math.cos(a) - p[1] * math.sin(a),
            p[0] * math.sin(a) + p[1] * math.cos(a))


def rotate_recs(recs, deg):
    from . import geometry as G
    out = []
    for r in recs:
        b = r["bbox"]
        corners = [(b[0], b[1]), (b[2], b[1]), (b[2], b[3]), (b[0], b[3])]
        wc = [rotate_point(c, deg) for c in corners]
        xs = [c[0] for c in wc]; ys = [c[1] for c in wc]
        nr = dict(r)
        nb = (min(xs), min(ys), max(xs), max(ys))
        nr["bbox"] = nb
        nr["center"] = ((nb[0] + nb[2]) / 2.0, (nb[1] + nb[3]) / 2.0)
        # 同步更新几何属性，使指纹随之旋转
        a = dict(nr.get("attrs", {}))
        t = nr["type"]
        if t == "LINE":
            dx, dy = a.get("dx", 0.0), a.get("dy", 0.0)
            a["dx"], a["dy"] = rotate_point((dx, dy), deg)
        elif t == "ARC":
            a["a0"] = (a.get("a0", 0.0) + deg) % 360
            a["a1"] = (a.get("a1", 0.0) + deg) % 360
        elif t in ("LWPOLYLINE", "POLYLINE"):
            a["bw"] = nb[2] - nb[0]; a["bh"] = nb[3] - nb[1]
        elif t == "HATCH":
            a["bw"] = nb[2] - nb[0]; a["bh"] = nb[3] - nb[1]
        nr["attrs"] = a
        nr["geom"] = G.geom_signature(nr)
        nr["summary"] = G.describe(nr)
        out.append(nr)
    return out


def match_count(old, new):
    """同源实体对数量（(type,layer,geom) 完全相同且位置最近）——配准质量的廉价指标。"""
    buckets = defaultdict(list)
    for or_ in old:
        buckets[(or_["type"], or_["layer"], or_["geom"])].append(or_)
    n = 0
    for nr in new:
        cands = buckets.get((nr["type"], nr["layer"], nr["geom"]))
        if not cands:
            continue
        best = min(cands, key=lambda or_: pos_dist(or_, nr))
        if pos_dist(best, nr) < 1e6:
            n += 1
    return n


def register(old, new, cfg):
    """按 config.registration.method 选择配准方式，返回(配准后旧版, 变换信息)。"""
    method = cfg["registration"].get("method", "none")
    info = {"method": method, "rotation": 0.0, "translation": (0.0, 0.0)}
    if method == "none":
        info["applied"] = False
        return old, info
    if method == "rigid":
        deg = estimate_rotation(old, new)
        base = match_count(old, new)
        rot = match_count(rotate_recs(old, deg), new) if abs(deg) > 0.01 else base
        # 仅当旋转明显提升配对时才采用，否则保持平移（避免对未旋转图引入误差）
        if rot > base + 3:
            old = rotate_recs(old, deg)
            info["rotation"] = deg
    tx, ty = estimate_translation(old, new)
    info["translation"] = (tx, ty)
    info["applied"] = True
    old = apply_translation(old, tx, ty)
    return old, info


def apply_translation(recs, tx, ty):
    out = []
    for r in recs:
        b = r["bbox"]
        nr = dict(r)
        nr["bbox"] = (b[0] + tx, b[1] + ty, b[2] + tx, b[3] + ty)
        nr["center"] = (r["center"][0] + tx, r["center"][1] + ty)
        out.append(nr)
    return out
