#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
"""
高亮层：在新版图纸上叠加 CHANGES 图层，按类型画贴合形状的圈注

颜色来自 config.colors（DXF 颜色号 + PNG 十六进制）。标注形状由 geometry.rec_shape
决定：LINE→旋转细框、CIRCLE→圆、ARC→弧、其余→收紧矩形；每处带 A1/A2... 标签。
"""
from __future__ import annotations
import ezdxf
from collections import defaultdict
from . import geometry as G
from .io_dxf import readfile_clean


def annotate_dxf(new_path, changes, out_path, cfg):
    """新建干净文档，复制新版几何（避开 LibreDWG 损坏的 MATERIAL 表），叠加 CHANGES。"""
    src = ezdxf.readfile(readfile_clean(new_path))
    try:
        out_doc = ezdxf.new(dxfversion=src.dxfversion)
    except Exception:
        out_doc = ezdxf.new("R2010")

    existing = {l.dxf.name for l in out_doc.layers}
    for layer in src.layers:
        name = layer.dxf.name
        if name in ("0", "DEFPOINTS"):
            continue
        if name in existing:
            continue
        try:
            out_doc.layers.add(name, color=layer.dxf.color,
                               linetype=layer.dxf.linetype)
        except Exception:
            try:
                out_doc.layers.add(name)
            except Exception:
                pass

    for e in src.modelspace():
        try:
            out_doc.modelspace().add_entity(e.copy())
        except Exception:
            pass

    msp = out_doc.modelspace()
    if "CHANGES" not in [l.dxf.name for l in out_doc.layers]:
        out_doc.layers.add("CHANGES")

    counts = defaultdict(int)
    for ch in changes:
        counts[ch["kind"]] += 1
        col = cfg["colors"][ch["kind"]][0]
        label = f"{ch['kind'][0]}{counts[ch['kind']]}"
        sh = ch.get("shape", {"kind": "rect"})
        if sh["kind"] == "rot_rect":
            verts = G.rot_rect_verts(sh["cx"], sh["cy"], sh["hl"], sh["hw"], sh["ang"])
            msp.add_lwpolyline(verts, close=True,
                               dxfattribs={"layer": "CHANGES", "color": col})
            lx, ly = verts[0]
            hgt = max(8.0, min(sh["hw"] * 2.2, 30.0))
        elif sh["kind"] == "circle":
            pad = G.adaptive_pad(sh["r"] * 2, sh["r"] * 2)
            msp.add_circle((sh["cx"], sh["cy"]), sh["r"] + pad,
                           dxfattribs={"layer": "CHANGES", "color": col})
            lx, ly = sh["cx"] - sh["r"] - pad, sh["cy"] + sh["r"] + pad
            hgt = max(8.0, min(pad * 1.2, 30.0))
        elif sh["kind"] == "arc":
            pad = G.adaptive_pad(sh["r"] * 2, sh["r"] * 2)
            msp.add_arc((sh["cx"], sh["cy"]), sh["r"] + pad, sh["a0"], sh["a1"],
                        dxfattribs={"layer": "CHANGES", "color": col})
            lx, ly = sh["cx"] - sh["r"] - pad, sh["cy"] + sh["r"] + pad
            hgt = max(8.0, min(pad * 1.2, 30.0))
        else:
            b = ch["bbox"]
            w, h = b[2] - b[0], b[3] - b[1]
            pad = G.adaptive_pad(w, h)
            box = [(b[0] - pad, b[1] - pad), (b[2] + pad, b[1] - pad),
                   (b[2] + pad, b[3] + pad), (b[0] - pad, b[3] + pad)]
            msp.add_lwpolyline(box, close=True,
                               dxfattribs={"layer": "CHANGES", "color": col})
            lx, ly = b[0] - pad, b[3] + pad
            hgt = max(8.0, min(pad * 1.2, 30.0))
        msp.add_text(label, dxfattribs={"layer": "CHANGES", "color": col,
                                        "height": hgt}).set_placement((lx, ly + 2))
    out_doc.saveas(out_path)
    return out_path
