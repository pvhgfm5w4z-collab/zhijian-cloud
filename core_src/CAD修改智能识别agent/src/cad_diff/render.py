#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
"""
渲染层：整图对比 PNG + 分簇聚焦放大 PNG

- draw_comparison：左旧右新，变更彩色圈注
- draw_zoom：相近变更聚簇，每簇单独放大（大图必备）
颜色取自 config.colors 的 PNG 十六进制。
"""
from __future__ import annotations
import math
from collections import defaultdict
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from . import geometry as G


def _png_color(cfg, kind):
    return cfg["colors"][kind][1]


def draw_recs(ax, recs, color, lw=0.8, alpha=1.0):
    """批量绘制一组记录（背景灰图）。用 LineCollection / PatchCollection 一次性绘制，
    避免逐实体 plot 调用，12k 实体也能秒级出图。"""
    from matplotlib.collections import LineCollection, PatchCollection
    segs = []
    patches = []
    tx, ty, ix, iy = [], [], [], []
    for r in recs:
        for kind, *data in r["render"]:
            if kind == "line":
                s, en = data
                segs.append([s, en])
            elif kind == "circle":
                c, r = data
                patches.append(mpatches.Circle(c, r, fill=False))
            elif kind == "arc":
                c, r, a0, a1 = data
                th = [math.radians(a) for a in range(int(a0), int(a1) + 1)]
                pts = [(c[0] + r * math.cos(t), c[1] + r * math.sin(t)) for t in th]
                if len(pts) >= 2:
                    segs.append(pts)
            elif kind == "poly":
                pts, closed = data
                if len(pts) >= 2:
                    poly = mpatches.Polygon(pts, closed=closed, fill=False)
                    patches.append(poly)
            elif kind == "text":
                p, txt = data
                tx.append(p[0]); ty.append(p[1])
            elif kind == "insert":
                p, _ = data
                ix.append(p[0]); iy.append(p[1])
            elif kind == "hatch":
                b = data[0]
                patches.append(mpatches.Rectangle((b[0], b[1]), b[2] - b[0], b[3] - b[1],
                                                  fill=True, alpha=0.25))
            elif kind == "complex":
                b = data[0]
                patches.append(mpatches.Rectangle((b[0], b[1]), b[2] - b[0], b[3] - b[1],
                                                  fill=False))
    if segs:
        ax.add_collection(LineCollection(segs, colors=color, linewidths=lw, alpha=alpha))
    if patches:
        ax.add_collection(PatchCollection(patches, facecolor="none",
                                          edgecolor=color, linewidths=lw, alpha=alpha))
    if tx:
        ax.scatter(tx, ty, c=color, marker="x", s=12 * lw, alpha=alpha)
    if ix:
        ax.scatter(ix, iy, c=color, marker="+", s=20 * lw, alpha=alpha)


def draw_change(ax, ch, cfg):
    c = _png_color(cfg, ch["kind"])
    sh = ch.get("shape", {"kind": "rect"})
    if sh["kind"] == "rot_rect":
        verts = G.rot_rect_verts(sh["cx"], sh["cy"], sh["hl"], sh["hw"], sh["ang"])
        ax.add_patch(mpatches.Polygon(verts, closed=True, fill=False, edgecolor=c, lw=2))
    elif sh["kind"] == "circle":
        pad = G.adaptive_pad(sh["r"] * 2, sh["r"] * 2)
        ax.add_patch(mpatches.Circle((sh["cx"], sh["cy"]), sh["r"] + pad,
                                     fill=False, edgecolor=c, lw=2))
    elif sh["kind"] == "arc":
        pad = G.adaptive_pad(sh["r"] * 2, sh["r"] * 2)
        th = [math.radians(a) for a in range(int(sh["a0"]), int(sh["a1"]) + 1)]
        ax.plot([sh["cx"] + (sh["r"] + pad) * math.cos(t) for t in th],
                [sh["cy"] + (sh["r"] + pad) * math.sin(t) for t in th], color=c, lw=2)
    else:
        b = ch["bbox"]
        w, h = b[2] - b[0], b[3] - b[1]
        pad = G.adaptive_pad(w, h)
        ax.add_patch(mpatches.Rectangle((b[0] - pad, b[1] - pad), w + 2 * pad, h + 2 * pad,
                                        fill=False, edgecolor=c, lw=2))


def _translated_change(ch, dx, dy):
    """Copy a change marker from its new position back to its old position."""
    old = dict(ch)
    b = ch["bbox"]
    old["bbox"] = [b[0] - dx, b[1] - dy, b[2] - dx, b[3] - dy]
    shape = ch.get("shape")
    if shape:
        old_shape = dict(shape)
        if "cx" in old_shape:
            old_shape["cx"] -= dx
        if "cy" in old_shape:
            old_shape["cy"] -= dy
        old["shape"] = old_shape
    return old


def _comparison_view(records, changes, trim_ratio=0.002):
    """Return a shared view around the drawing, ignoring a few isolated outliers.

    Some DXFs contain stray entities far outside the sheet (for example an
    orphan HATCH). Those entities should not make the useful drawing occupy
    only a small part of the comparison image. Include all actual changes so
    a changed object outside the main drawing is still visible.
    """
    valid = [r["bbox"] for r in records
             if r.get("bbox") and len(r["bbox"]) == 4
             and all(math.isfinite(float(v)) for v in r["bbox"])]
    if not valid:
        return (0.0, 1.0, 0.0, 1.0)

    keep = list(range(len(valid)))
    if len(valid) >= 200:
        centers_x = sorted((b[0] + b[2]) / 2 for b in valid)
        centers_y = sorted((b[1] + b[3]) / 2 for b in valid)
        trim = min(int(len(valid) * trim_ratio), max((len(valid) - 1) // 2, 0))
        x0, x1 = centers_x[trim], centers_x[-trim - 1]
        y0, y1 = centers_y[trim], centers_y[-trim - 1]
        keep = [i for i, b in enumerate(valid)
                if x0 <= (b[0] + b[2]) / 2 <= x1
                and y0 <= (b[1] + b[3]) / 2 <= y1]
        if not keep:
            keep = list(range(len(valid)))

    boxes = [valid[i] for i in keep]
    boxes.extend(ch["bbox"] for ch in changes
                 if ch.get("bbox") and len(ch["bbox"]) == 4
                 and all(math.isfinite(float(v)) for v in ch["bbox"]))
    x0 = min(float(b[0]) for b in boxes); x1 = max(float(b[2]) for b in boxes)
    y0 = min(float(b[1]) for b in boxes); y1 = max(float(b[3]) for b in boxes)
    w, h = max(x1 - x0, 1.0), max(y1 - y0, 1.0)
    pad = max(w, h) * 0.025
    return x0 - pad, x1 + pad, y0 - pad, y1 + pad


def draw_comparison(old, new, changes, out_png, cfg):
    x0, x1, y0, y1 = _comparison_view(old + new, changes)
    data_ratio = (x1 - x0) / max(y1 - y0, 1.0)
    fig_width = 16.0
    fig_height = max(4.5, min(10.0, fig_width / (2.1 * max(data_ratio, 0.1))))
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(fig_width, fig_height))
    draw_recs(ax1, old, "#999999", 0.8)
    draw_recs(ax2, new, "#999999", 0.8)
    for ch in changes:
        if ch["kind"] == "DELETED":
            draw_change(ax1, ch, cfg)
        elif ch["kind"] == "MOVED":
            draw_change(ax1, _translated_change(ch, ch["movement"]["dx"],
                                                ch["movement"]["dy"]), cfg)
            draw_change(ax2, ch, cfg)
        else:
            draw_change(ax2, ch, cfg)
    ax1.set_title("OLD  (red = deleted)")
    ax2.set_title("NEW  (green=added  orange/magenta=modified/moved)")
    for ax in (ax1, ax2):
        ax.set_xlim(x0, x1); ax.set_ylim(y0, y1)
        ax.set_aspect("equal"); ax.grid(True, alpha=0.2)
    cnt = summarize(changes)
    fig.suptitle(
        f"CAD Change Comparison  |  ADDED {cnt['ADDED']}  DELETED "
        f"{cnt['DELETED']}  MODIFIED {cnt['MODIFIED']}  MOVED {cnt['MOVED']}",
        fontsize=12)
    fig.tight_layout()
    fig.savefig(out_png, dpi=cfg["output"]["dpi"])
    plt.close(fig)


def draw_zoom(old, new, changes, out_stem, cfg, margin_ratio=0.5):
    """分簇聚焦：相近变更聚成一簇，每簇单独放大图。返回图片路径列表。"""
    if not changes:
        return []
    allx = [r["bbox"][0] for r in old + new] + [r["bbox"][2] for r in old + new]
    ally = [r["bbox"][1] for r in old + new] + [r["bbox"][3] for r in old + new]
    diag = math.hypot(max(allx) - min(allx), max(ally) - min(ally))
    thresh = max(diag * 0.04, 500.0)
    n = len(changes)
    parent = list(range(n))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]; a = parent[a]
        return a

    def cen(ch):
        b = ch["bbox"]
        return ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)

    def old_bbox(ch):
        b = ch["bbox"]
        m = ch.get("movement") or {"dx": 0, "dy": 0}
        return [b[0] - m["dx"], b[1] - m["dy"], b[2] - m["dx"], b[3] - m["dy"]]

    for i in range(n):
        for j in range(i + 1, n):
            ci, cj = cen(changes[i]), cen(changes[j])
            if math.hypot(ci[0] - cj[0], ci[1] - cj[1]) < thresh:
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[ri] = rj
    groups = defaultdict(list)
    for i in range(n):
        groups[find(i)].append(i)
    outs = []
    multi = len(groups) > 1
    for gi, members in enumerate(sorted(groups.values(), key=lambda g: -len(g)), 1):
        sub = [changes[i] for i in members]
        xs, ys = [], []
        for ch in sub:
            b = ch["bbox"]; xs += [b[0], b[2]]; ys += [b[1], b[3]]
            if ch["kind"] == "MOVED":
                ob = old_bbox(ch); xs += [ob[0], ob[2]]; ys += [ob[1], ob[3]]
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        w, h = max(x1 - x0, 1.0), max(y1 - y0, 1.0)
        mx, my = max(w * margin_ratio, 300.0), max(h * margin_ratio, 300.0)
        x0 -= mx; x1 += mx; y0 -= my; y1 += my
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 6))
        draw_recs(ax1, old, "#cccccc", 0.5)
        draw_recs(ax2, new, "#cccccc", 0.5)
        for ch in sub:
            if ch["kind"] == "DELETED":
                draw_change(ax1, ch, cfg)
            elif ch["kind"] == "MOVED":
                draw_change(ax1, _translated_change(ch, ch["movement"]["dx"],
                                                    ch["movement"]["dy"]), cfg)
                draw_change(ax2, ch, cfg)
            else:
                draw_change(ax2, ch, cfg)
        ax1.set_title(f"OLD zoom#{gi}  (red=deleted)")
        ax2.set_title(f"NEW zoom#{gi}")
        for ax in (ax1, ax2):
            ax.set_xlim(x0, x1); ax.set_ylim(y0, y1)
            ax.set_aspect("equal"); ax.grid(True, alpha=0.2)
        fig.tight_layout()
        out = f"{out_stem}_zoom{gi}.png" if multi else f"{out_stem}_zoom.png"
        fig.savefig(out, dpi=cfg["output"]["zoom_dpi"])
        plt.close(fig)
        outs.append(out)
    return outs


def summarize(changes) -> dict:
    from collections import defaultdict
    cnt = defaultdict(int)
    for c in changes:
        cnt[c["kind"]] += 1
    return cnt
