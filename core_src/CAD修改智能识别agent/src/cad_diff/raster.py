#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
"""
栅格级兜底 diff（OpenCV）

当矢量解析失败 / 解析结果过少 / 需要"像素级不在乎图元类型"的差异时启用：
1) 把旧版、新版按相同图幅与分辨率渲染成灰度图；
2) absdiff + 阈值 + 形态学闭运算去噪；
3) 找连通域轮廓 -> 过滤噪声(面积占比) -> 差异框；
4) 输出标注 PNG + 差异框清单（可并入 JSON 报告）。

优点：对任意格式鲁棒、无需解析图元；缺点：只给区域、不认"是什么"。
"""
from __future__ import annotations
import math
import numpy as np
import cv2
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from . import geometry as G

_LINE = "#333333"


def _extent(old, new):
    xs, ys = [], []
    for r in old + new:
        b = r["bbox"]
        xs += [b[0], b[2]]; ys += [b[1], b[3]]
    if not xs:
        return (0, 1, 0, 1)
    return (min(xs), max(xs), min(ys), max(ys))


def _render_array(recs, extent, dpi=120, scale=1.0):
    """把记录渲染成灰度 uint8 数组（白底黑线），尺寸由 extent/dpi 决定，图与图一致。"""
    x0, x1, y0, y1 = extent
    w = max(x1 - x0, 1.0) * scale
    h = max(y1 - y0, 1.0) * scale
    fig_w = max(2.0, w / 1000.0 * (dpi / 100.0))
    fig_h = max(2.0, h / 1000.0 * (dpi / 100.0))
    fig, ax = plt.subplots(1, 1, figsize=(fig_w, fig_h))
    ax.set_xlim(x0, x0 + w); ax.set_ylim(y0, y0 + h)
    ax.set_aspect("equal"); ax.axis("off")
    from matplotlib.collections import LineCollection, PatchCollection
    segs, patches = [], []
    for r in recs:
        for kind, *data in r["render"]:
            if kind == "line":
                s, en = data
                segs.append([s, en])
            elif kind == "circle":
                c, rr = data
                patches.append(plt.Circle(c, rr, fill=False))
            elif kind == "arc":
                c, rr, a0, a1 = data
                th = [math.radians(a) for a in range(int(a0), int(a1) + 1)]
                pts = [(c[0] + rr * math.cos(t), c[1] + rr * math.sin(t)) for t in th]
                if len(pts) >= 2:
                    segs.append(pts)
            elif kind == "poly":
                pts, closed = data
                if len(pts) >= 2:
                    patches.append(plt.Polygon(pts, closed=closed, fill=False))
            elif kind == "text":
                p, _ = data
                ax.plot(p[0], p[1], "x", color=_LINE, ms=4)
            elif kind == "insert":
                p, _ = data
                ax.plot(p[0], p[1], "+", color=_LINE, ms=6)
            elif kind == "hatch":
                b = data[0]
                patches.append(plt.Rectangle((b[0], b[1]), b[2] - b[0], b[3] - b[1],
                                             fill=True, facecolor="#888888", alpha=0.5))
            elif kind == "complex":
                b = data[0]
                patches.append(plt.Rectangle((b[0], b[1]), b[2] - b[0], b[3] - b[1],
                                             fill=False))
    if segs:
        ax.add_collection(LineCollection(segs, colors=_LINE, linewidths=1.0))
    if patches:
        ax.add_collection(PatchCollection(patches, facecolor="none",
                                          edgecolor=_LINE, linewidths=1.0))
    fig.canvas.draw()
    buf = np.asarray(fig.canvas.buffer_rgba())
    plt.close(fig)
    gray = cv2.cvtColor(buf, cv2.COLOR_RGBA2GRAY)
    return gray


def raster_diff(old, new, out_path, cfg, scale=1.0):
    """执行栅格 diff，输出标注 PNG + 差异框清单。返回 (png_path, boxes)。"""
    extent = _extent(old, new)
    try:
        img_old = _render_array(old, extent, dpi=cfg["raster"].get("dpi", 120), scale=scale)
        img_new = _render_array(new, extent, dpi=cfg["raster"].get("dpi", 120), scale=scale)
    except Exception as ex:
        print(f"[warn] 栅格渲染失败，跳过: {ex}")
        return None, []
    if img_old.shape != img_new.shape:
        img_new = cv2.resize(img_new, (img_old.shape[1], img_old.shape[0]))
    d = cv2.absdiff(img_old, img_new)
    _, bw = cv2.threshold(d, 25, 255, cv2.THRESH_BINARY)
    kernel = int(cfg["raster"].get("kernel", 3))
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (kernel, kernel))
    bw = cv2.morphologyEx(bw, cv2.MORPH_CLOSE, k)
    bw = cv2.morphologyEx(bw, cv2.MORPH_OPEN, k)
    total = bw.shape[0] * bw.shape[1]
    min_area = total * cfg["raster"].get("min_area_ratio", 0.0008)
    contours, _ = cv2.findContours(bw, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes = []
    vis = cv2.cvtColor(img_new, cv2.COLOR_GRAY2BGR)
    for c in contours:
        area = cv2.contourArea(c)
        if area < min_area:
            continue
        x, y, w, h = cv2.boundingRect(c)
        boxes.append({"x": int(x), "y": int(y), "w": int(w), "h": int(h),
                      "area_px": int(area)})
        cv2.rectangle(vis, (x, y), (x + w, y + h), (0, 200, 0), 2)
    cv2.imwrite(out_path, vis)
    return out_path, boxes
