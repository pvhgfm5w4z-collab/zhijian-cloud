#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
图纸变更索赔 Agent —— 产品流程 Demo

复用核心比对引擎（core_src/CAD修改智能识别agent），在外层串起产品闭环：
  上传新旧图纸 → 引擎比对 → 变更清单(人工勾选确认) → 演示规则量差估算/组价 → 生成签证单(docx)

启动：
  python demo_app.py  →  http://localhost:8321
"""
from __future__ import annotations
import io
import math
import os
import re
import sys
import json
import hashlib
import time
import shutil
import zipfile
import datetime
import tempfile
import logging
from decimal import Decimal, ROUND_HALF_UP

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENGINE_DIR = os.path.join(BASE_DIR, "core_src", "CAD修改智能识别agent")
# 在加载 cad_diff/matplotlib 之前指定项目内缓存目录，避免 Windows 用户目录锁冲突。
RUNTIME_DIR = os.path.join(BASE_DIR, ".runtime")
os.makedirs(os.path.join(RUNTIME_DIR, "mpl"), exist_ok=True)
os.makedirs(os.path.join(RUNTIME_DIR, "cache"), exist_ok=True)
os.environ.setdefault("MPLCONFIGDIR", os.path.join(RUNTIME_DIR, "mpl"))
os.environ.setdefault("XDG_CACHE_HOME", os.path.join(RUNTIME_DIR, "cache"))
sys.path.insert(0, os.path.join(ENGINE_DIR, "src"))

from cad_diff.config import load_config  # noqa: E402
from cad_diff import pipeline  # noqa: E402

from fastapi import FastAPI, UploadFile, File, Form, Body  # noqa: E402
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse  # noqa: E402
import uvicorn  # noqa: E402

# Large AutoCAD files may contain unsupported proxy objects. ezdxf reports each
# skipped object at warning level; keep the service console readable while the
# parser continues and the normal comparison result is returned.
logging.getLogger("ezdxf").setLevel(logging.ERROR)

JOBS_DIR = os.path.join(BASE_DIR, "jobs")
os.makedirs(JOBS_DIR, exist_ok=True)
JOBS: dict = {}


def _job_meta_path(jid: str) -> str:
    return os.path.join(JOBS_DIR, jid, "job.json")


def _persist_job(jid: str):
    """Persist the small, JSON-safe part of a tool task for Agent handoff."""
    job = JOBS.get(jid)
    if not job:
        return
    data = {k: v for k, v in job.items() if k not in ("dir", "old_path", "new_path")}
    data["dir_name"] = os.path.basename(job["dir"])
    data["old_file"] = os.path.basename(job.get("old_path", ""))
    data["new_file"] = os.path.basename(job.get("new_path", ""))
    with open(_job_meta_path(jid), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _load_persisted_jobs():
    for name in os.listdir(JOBS_DIR):
        path = _job_meta_path(name)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            job_dir = os.path.join(JOBS_DIR, data.get("dir_name") or name)
            old_path = os.path.join(job_dir, data.get("old_file", "old.dxf"))
            new_path = os.path.join(job_dir, data.get("new_file", "new.dxf"))
            if os.path.isdir(job_dir) and os.path.exists(old_path) and os.path.exists(new_path):
                data.update({"dir": job_dir, "old_path": old_path, "new_path": new_path})
                JOBS[name] = data
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue

SAMPLES = {
    "blocks": ("合成样例 blocks（9→11 实体，四类变更全中）",
               os.path.join(ENGINE_DIR, "samples", "blocks", "old_block.dxf"),
               os.path.join(ENGINE_DIR, "samples", "blocks", "new_block.dxf")),
    "real": ("真实图纸 real（方块图，新增中心线）",
             os.path.join(ENGINE_DIR, "samples", "real", "old.dxf"),
             os.path.join(ENGINE_DIR, "samples", "real", "new.dxf")),
    "hvac": ("真实图纸 hvac（天正暖通 1.24 万实体）",
             os.path.join(ENGINE_DIR, "samples", "hvac", "old.dxf"),
             os.path.join(ENGINE_DIR, "samples", "hvac", "new.dxf")),
    "shaft": ("真实图纸 active_shaft（主动轴，删除标注）",
              os.path.join(ENGINE_DIR, "samples", "active_shaft", "old.dxf"),
              os.path.join(ENGINE_DIR, "samples", "active_shaft", "new.dxf")),
    "yanhe_gate": ("盐河闸水电站（v1 → v2，演示图纸）",
                   os.path.join(BASE_DIR, "projects", "演示项目一期", "drawings", "盐河闸水电站v1.dxf"),
                   os.path.join(BASE_DIR, "projects", "演示项目一期", "drawings", "盐河闸水电站v2.dxf")),
    "yanhe_gate_demo": ("盐河闸水电站（演示版：新增/删除/修改/移动）",
                        os.path.join(BASE_DIR, "projects", "演示项目一期", "drawings", "盐河闸水电站_演示旧版.dxf"),
                        os.path.join(BASE_DIR, "projects", "演示项目一期", "drawings", "盐河闸水电站_演示新版.dxf")),
}

# Public distribution exposes only bundled, reproducible samples.
SAMPLES = {k: v for k, v in SAMPLES.items() if all(os.path.isfile(p) for p in v[1:])}

KIND_CN = {"ADDED": "新增", "DELETED": "删除", "MODIFIED": "修改", "MOVED": "移动"}
KIND_COLOR = {"ADDED": "#1D9E75", "DELETED": "#E24B4A", "MODIFIED": "#E8A33D", "MOVED": "#D4537E"}

# 图纸图元本身不能直接等同于工程材料。这里仅做“推荐”，并把依据返回给前端，
# 最终由用户选择合同清单项并确认工程量。
MATERIAL_RULES = [
    ("钢筋", ("钢筋", "REBAR", "RB", "GJ", "HRB", "HPB", "%%C", "Φ", "φ", "@")),
    ("混凝土", ("混凝土", "砼", "CONC", "C20", "C25", "C30", "C35", "C40")),
    ("砌体", ("砌体", "砌块", "砖", "BLOCK", "MASON")),
    ("管线", ("管道", "管线", "PIPE", "HVAC", "DRAIN", "给水", "排水")),
]


def _material_hint(change: dict) -> dict:
    text = " ".join(str(change.get(k, "")) for k in ("type", "layer", "old", "new"))
    upper = text.upper()
    scores = []
    for label, words in MATERIAL_RULES:
        hits = [w for w in words if (w.upper() in upper if w.isascii() else w in text)]
        scores.append((len(hits), label, hits))
    scores.sort(reverse=True, key=lambda x: x[0])
    best = scores[0] if scores else (0, "待确认", [])
    if best[0] >= 2:
        confidence = "高"
        label = best[1]
        evidence = "、".join(best[2])
    elif best[0] == 1:
        confidence = "中"
        label = best[1]
        evidence = "、".join(best[2])
    elif change.get("type") in ("HATCH", "LWPOLYLINE", "POLYLINE"):
        confidence = "低"
        label = "结构/面层待确认"
        evidence = "闭合区域或填充图元，缺少材料文字"
    else:
        confidence = "低"
        label = "待确认"
        evidence = "未找到材料、图层或构件标识"
    return {"label": label, "confidence": confidence, "evidence": evidence}

# ── 首版默认单价（工具/Agent 页面均可覆盖；后续可接合同清单） ────────────
DEMO_PRICE = {
    "length_per_m": 12.0,    # 长度类变更（如管线/线条延长），元/m
    "area_per_m2": 85.0,     # 面积类变更（如填充面层），元/m²
    "item_per_unit": 350.0,  # 构件/块参照类（如门窗樘、设备台），元/个
}


def _demo_config():
    cfg = load_config(os.path.join(ENGINE_DIR, "configs", "default.yaml"))
    cfg["output"].update({
        "dxf": True, "png": True, "zoom": True, "txt": False,
        "json": False, "pdf": False, "html": False, "raster": False, "zip": False,
    })
    return cfg


def _serialize_changes(changes):
    out = []
    for i, c in enumerate(changes):
        material = _material_hint(c)
        out.append({
            "idx": i,
            "kind": c["kind"],
            "kind_cn": KIND_CN.get(c["kind"], c["kind"]),
            "color": KIND_COLOR.get(c["kind"], "#666"),
            "type": c.get("type", "-"),
            "layer": c.get("layer", "-"),
            "bbox": [round(float(x), 1) for x in c.get("bbox", [0, 0, 0, 0])],
            "old": c.get("old", ""),
            "new": c.get("new", ""),
            "movement": c.get("movement"),
            "material": material["label"],
            "material_confidence": material["confidence"],
            "material_evidence": material["evidence"],
        })
    return out


def _run_compare(jid: str, old_path: str, new_path: str):
    job_dir = os.path.join(JOBS_DIR, jid)
    os.makedirs(job_dir, exist_ok=True)
    old_dst = os.path.join(job_dir, "old" + os.path.splitext(old_path)[1].lower())
    new_dst = os.path.join(job_dir, "new" + os.path.splitext(new_path)[1].lower())
    shutil.copyfile(old_path, old_dst)
    shutil.copyfile(new_path, new_dst)

    cfg = _demo_config()
    manifest = pipeline.run(old_dst, new_dst, os.path.join(job_dir, "result"), cfg)
    changes = manifest["changes_list"]
    summary_obj = manifest.get("summary") or {}
    if isinstance(summary_obj, dict):
        summary_text = summary_obj.get("text") or summary_obj.get("summary") or ""
        summary_points = summary_obj.get("points") or summary_obj.get("bullets") or []
    else:
        summary_text, summary_points = str(summary_obj), []

    JOBS[jid] = {
        "dir": job_dir,
        "old_path": old_dst,
        "new_path": new_dst,
        "counts": dict(manifest["counts"]),
        "changes": _serialize_changes(changes),
        "summary_text": summary_text,
        "summary_points": summary_points,
        "has_png": os.path.exists(os.path.join(job_dir, "result.png")),
        "zooms": sorted(f for f in os.listdir(job_dir) if f.startswith("result_zoom") and f.endswith(".png")),
        "created": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    _persist_job(jid)
    return JOBS[jid]


_load_persisted_jobs()


# ── 演示规则量差估算 ─────────────────────────────────────────────────────
_NUM = r"([-+]?\d+(?:\.\d+)?)"


def _measure(summary: str):
    m = re.search(r"测量值=" + _NUM, summary or "")
    return float(m.group(1)) if m else None


def _line_len(summary: str):
    m = re.search(r"长度≈" + _NUM, summary or "")
    return float(m.group(1)) if m else None


def _bbox_area_m2(bbox):
    w = abs(bbox[2] - bbox[0]) / 1000.0
    h = abs(bbox[3] - bbox[1]) / 1000.0
    return w * h


def _span(summary: str):
    m = re.search(r"范围≈" + _NUM + r"[×x]" + _NUM, summary or "")
    return (float(m.group(1)), float(m.group(2))) if m else None


def _summary_area_m2(summary: str):
    """读取 HATCH 实际边界面积；兼容旧摘要中的图纸单位平方值。"""
    m = re.search(r"面积≈\s*" + _NUM + r"\s*(m²|平方米)?", summary or "")
    if not m:
        return None
    value = float(m.group(1))
    return value if m.group(2) else value / 1e6


def _round_half_up(value, places=2):
    """按十进制四舍五入，避免二进制浮点和银行家舍入影响签证金额。"""
    quantum = Decimal("1").scaleb(-places)
    return float(Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP))


def _price_table(prices=None):
    """读取旧版兜底单价，非法值回退到演示默认值。"""
    table = dict(DEMO_PRICE)
    if not isinstance(prices, dict):
        return table
    for key in table:
        try:
            value = float(prices.get(key, table[key]))
            if math.isfinite(value) and value >= 0:
                table[key] = value
        except (TypeError, ValueError):
            pass
    return table


def _job_visa_total(job: dict) -> float:
    """Return the signed total from persisted detail rows, repairing stale totals."""
    items = job.get("visa_items") or []
    total = _round_half_up(sum(
        float(item.get("amount") or 0)
        for item in items
        if isinstance(item, dict) and _finite_number(item.get("amount")) is not None
    ), 2)
    if items:
        job["visa_total"] = total
    else:
        try:
            total = _round_half_up(float(job.get("visa_total") or 0), 2)
        except (TypeError, ValueError):
            total = 0.0
    return total


def _finite_number(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _find_boq_item(boq_items, code):
    """在页面传回的合同清单中找一条合法清单项。"""
    if not code or not isinstance(boq_items, list):
        return None
    for item in boq_items:
        if not isinstance(item, dict) or str(item.get("code", "")).strip() != str(code).strip():
            continue
        price = _finite_number(item.get("unit_price"))
        if price is None or price < 0:
            return None
        name = str(item.get("name", "")).strip()
        unit = str(item.get("unit", "")).strip()
        if not name or not unit:
            return None
        return {"code": str(item.get("code")).strip(), "name": name, "unit": unit, "unit_price": price}
    return None


def _area_delta_m2(change):
    """从可解析的截面/闭合区域摘要得到面积差（图纸单位按 mm 转 m²）。"""
    etype = change.get("type")
    if etype in ("HATCH", "LWPOLYLINE", "POLYLINE", "CIRCLE"):
        old_area = _summary_area_m2(change.get("old", ""))
        new_area = _summary_area_m2(change.get("new", ""))
        if old_area is not None and new_area is not None:
            return new_area - old_area
        if change.get("kind") in ("ADDED", "DELETED"):
            area = new_area if change.get("kind") == "ADDED" else old_area
            if area is not None:
                return area if change.get("kind") == "ADDED" else -area
    old_span, new_span = _span(change.get("old", "")), _span(change.get("new", ""))
    if etype in ("HATCH", "LWPOLYLINE", "POLYLINE") and old_span and new_span:
        return (new_span[0] * new_span[1] - old_span[0] * old_span[1]) / 1e6
    if change.get("kind") in ("ADDED", "DELETED") and etype in ("HATCH", "LWPOLYLINE", "POLYLINE"):
        sign = 1 if change.get("kind") == "ADDED" else -1
        return sign * _bbox_area_m2(change.get("bbox", [0, 0, 0, 0]))
    return None


def _line_boundary_area_delta_m2(change):
    """Estimate the swept area of a moved boundary line (drawing units are mm).

    This is only used for concrete/masonry boundary lines.  A line move changes
    the enclosed area by approximately line length × perpendicular displacement.
    Added/deleted isolated lines are deliberately left unresolved because they
    need neighbouring segments to reconstruct a closed polygon.
    """
    if change.get("type") != "LINE" or change.get("kind") != "MODIFIED":
        return None
    material = str(change.get("material", "")).lower()
    layer = str(change.get("layer", "")).lower()
    if not any(token in material or token in layer for token in ("混凝土", "concrete", "砼", "砌体", "砌块", "mason", "block")):
        return None
    movement = change.get("movement") or {}
    dx, dy = _finite_number(movement.get("dx")), _finite_number(movement.get("dy"))
    old_len, new_len = _line_len(change.get("old")), _line_len(change.get("new"))
    if dx is None or dy is None or old_len is None or new_len is None:
        return None
    # The parser reports Δx/Δy in each line summary; use the dominant axis to
    # identify the line direction and therefore the perpendicular displacement.
    old_axis = re.search(r"Δx=" + _NUM + r", Δy=" + _NUM, change.get("old", ""))
    if not old_axis:
        return None
    vx, vy = abs(float(old_axis.group(1))), abs(float(old_axis.group(2)))
    perpendicular = abs(dx) if vy >= vx else abs(dy)
    return ((old_len + new_len) / 2.0) * perpendicular / 1e6


def _unit_is(unit, *values):
    return str(unit or "").strip().lower() in {str(value).lower() for value in values}


def _auto_boq_delta(change, unit):
    """按图纸几何和清单单位自动给出可直接计价的工程量差。"""
    kind, etype = change.get("kind"), change.get("type")
    if _unit_is(unit, "m", "米") and etype == "LINE":
        if kind == "MODIFIED":
            old_length, new_length = _line_len(change.get("old")), _line_len(change.get("new"))
            return (new_length - old_length) / 1000.0 if old_length is not None and new_length is not None else None
        if kind in ("ADDED", "DELETED"):
            length = _line_len(change.get("new") or change.get("old"))
            if length is not None:
                return (1 if kind == "ADDED" else -1) * length / 1000.0
    if _unit_is(unit, "m²", "m2", "平方米"):
        return _area_delta_m2(change)
    if _unit_is(unit, "个", "套", "件", "台", "樘") and kind in ("ADDED", "DELETED") and etype == "INSERT":
        return 1 if kind == "ADDED" else -1
    return None


def estimate_change(c, prices=None, boq_item=None, manual_delta=None, thickness_m=None):
    """按合同清单项组价，优先使用图纸自动量差，人工输入可覆盖。"""
    prices = _price_table(prices)
    kind, etype = c["kind"], c["type"]
    material = c.get("material", "待确认")
    base = {
        "idx": c["idx"], "kind_cn": c["kind_cn"], "type": etype, "layer": c["layer"],
        "content": "", "delta": "-", "unit": "-", "price": "-", "amount": 0.0,
        "note": "", "boq_code": "", "boq_name": "", "material": material,
        "needs_review": True, "area_delta_m2": None, "thickness_m": None,
        "calculation_method": "",
    }

    # 清单项映射是正式索赔金额的入口；能从图纸解析的量差自动计算，人工输入可覆盖。
    if boq_item:
        code = boq_item["code"]
        name = boq_item["name"]
        unit = boq_item["unit"]
        price = boq_item["unit_price"]
        base.update({"boq_code": code, "boq_name": name, "unit": unit, "price": price,
                     "content": f"[{code}] {name}（材料推荐：{material}）"})
        thickness = _finite_number(thickness_m)
        area_delta = _area_delta_m2(c) if _unit_is(unit, "m³", "m3", "立方米", "m²", "m2", "平方米") else None
        derived_delta = area_delta * thickness if _unit_is(unit, "m³", "m3", "立方米") and area_delta is not None and thickness is not None and thickness > 0 else None
        manual = _finite_number(manual_delta)
        auto_delta = _auto_boq_delta(c, unit)
        delta = derived_delta if derived_delta is not None else manual if manual is not None else auto_delta
        calculation_method = (
            "area_times_thickness" if derived_delta is not None
            else "manual" if manual is not None
            else "auto" if auto_delta is not None
            else ""
        )
        if delta is not None:
            base.update({
                "delta": f"{delta:+.6f}",
                "amount": _round_half_up(delta * price, 2),
                "note": ((f"截面面积差 {area_delta:+.3f} m² × 厚度 {thickness:.3f} m = 体积差 {delta:+.3f} m³，按合同清单项单价组价"
                          if derived_delta is not None else
                          "工程量差由人工确认，按合同清单项单价组价" if manual is not None else
                          "按图纸几何量差自动计算，按合同清单项单价组价")
                        + ("；清单单价为 0，请复核" if price == 0 else "")),
                "needs_review": price == 0,
                "area_delta_m2": area_delta,
                "thickness_m": thickness if derived_delta is not None else None,
                "calculation_method": calculation_method,
            })
        else:
            base.update({"note": (
                "已识别面积差，请填写厚度（m）后自动换算体积"
                if _unit_is(unit, "m³", "m3", "立方米") and area_delta is not None
                else "该变更是线段，仅解析到长度；厚度不能单独换算体积，请填写体积工程量或改用闭合面/HATCH"
                if _unit_is(unit, "m³", "m3", "立方米") and etype == "LINE"
                else "已匹配合同清单项，但未解析出工程量差，请人工填写工程量"
            )})
        return base

    if kind == "MODIFIED" and etype == "DIMENSION":
        o, n = _measure(c["old"]), _measure(c["new"])
        if o is not None and n is not None:
            d = _round_half_up(n - o, 2)
            base.update({
                "content": f"尺寸标注 {o} → {n}（{c['layer']}）",
                "delta": f"{d:+.2f}", "unit": "mm",
                "note": "未匹配合同清单项，仅作候选提示；请确认材料、工程量和单价",
            })
        else:
            base.update({"content": f"尺寸标注修改（{c['layer']}）", "note": "测量值不可解析，建议人工核量"})
        return base
    if kind == "MODIFIED" and etype == "LINE":
        o, n = _line_len(c["old"]), _line_len(c["new"])
        if o is not None and n is not None:
            dm = (n - o) / 1000.0
            amount = _round_half_up(dm * prices["length_per_m"], 2)
            base.update({"content": f"线条长度 {o:.1f} → {n:.1f} mm", "delta": f"{dm:+.6f}", "unit": "m", "price": prices["length_per_m"], "amount": amount, "note": "未匹配合同清单项，按长度类兜底单价估算；请人工复核"})
        else:
            base.update({"content": "线条几何修改", "note": "长度不可解析，建议人工核量"})
        return base
    if kind in ("ADDED", "DELETED") and etype == "LINE":
        length = _line_len(c.get("new") or c.get("old"))
        if length is not None:
            sign = 1 if kind == "ADDED" else -1
            delta_m = sign * length / 1000.0
            amount = _round_half_up(delta_m * prices["length_per_m"], 2)
            base.update({"content": f"{'新增' if sign > 0 else '删除'}线条长度 {length:.1f} mm", "delta": f"{delta_m:+.6f}", "unit": "m", "price": prices["length_per_m"], "amount": amount, "note": "未匹配合同清单项，按长度类兜底单价估算；需确认该线段代表实际工程量"})
        else:
            base.update({"content": f"{base['kind_cn']}LINE（{c['layer']}）", "note": "长度不可解析，建议人工核量"})
        return base
    if kind == "MODIFIED" and etype in ("HATCH", "LWPOLYLINE", "POLYLINE", "CIRCLE"):
        da = _area_delta_m2(c)
        if da is not None:
            amount = _round_half_up(da * prices["area_per_m2"], 2)
            base.update({
                "content": f"闭合区域实际面积变化 {da:+.3f} m²",
                "delta": f"{da:+.6f}", "unit": "m²",
                "price": prices["area_per_m2"], "amount": amount,
                "note": "未匹配合同清单项，按闭合边界实际面积差和面积类兜底单价估算；请人工复核",
            })
            return base
        o, n = _span(c["old"]), _span(c["new"])
        if o and n:
            oa, na = o[0] * o[1] / 1e6, n[0] * n[1] / 1e6
            da = na - oa
            amount = _round_half_up(da * prices["area_per_m2"], 2)
            base.update({
                "content": f"区域范围 {o[0]:.1f}×{o[1]:.1f} → {n[0]:.1f}×{n[1]:.1f} mm",
                "delta": f"{da:+.6f}", "unit": "m²",
                "price": prices["area_per_m2"], "amount": amount,
                "note": "未匹配合同清单项，按闭合边界实际面积差和面积类兜底单价估算；请人工复核",
            })
        else:
            base.update({"content": f"区域几何修改（{etype}，{c['layer']}）", "note": "范围不可解析，建议人工核量"})
        return base
    if kind in ("ADDED", "DELETED") and etype in ("HATCH", "LWPOLYLINE", "POLYLINE", "CIRCLE"):
        area = _area_delta_m2(c)
        if area is None:
            sign = 1 if kind == "ADDED" else -1
            area = sign * _bbox_area_m2(c["bbox"])
        sign = 1 if area >= 0 else -1
        amount = _round_half_up(area * prices["area_per_m2"], 2)
        base.update({
            "content": f"{'新增' if sign > 0 else '删除'}填充/闭合区域（{etype}，{c['layer']}）",
            "delta": f"{area:+.6f}", "unit": "m²",
            "price": prices["area_per_m2"], "amount": amount,
            "note": "未匹配合同清单项，按闭合边界实际面积和面积类兜底单价估算；请人工复核",
        })
        return base
    if kind in ("ADDED", "DELETED") and etype == "INSERT":
        sign = 1 if kind == "ADDED" else -1
        amount = _round_half_up(sign * prices["item_per_unit"], 2)
        base.update({
            "content": f"{'新增' if sign > 0 else '删除'}{c['new'] or c['old']}",
            "delta": f"{sign:+d}", "unit": "个",
            "price": prices["item_per_unit"], "amount": amount,
            "note": "未匹配合同清单项，按构件类兜底单价估算；请人工复核",
        })
        return base
    if kind == "MOVED":
        base.update({"content": f"{etype} 位置移动（{c['layer']}）", "note": "位置调整，原则上无工程量增减，提示复核是否引起附带量差"})
        return base
    base.update({
        "content": f"{c['kind_cn']}{etype}（{c['layer']}）：{(c['new'] or c['old'])[:40]}",
        "note": "演示规则未覆盖此类实体，建议人工核量",
    })
    return base


def gen_visa_docx(jid: str, items: list, project: str, part: str) -> str:
    from docx import Document
    from docx.shared import Pt
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    doc = Document()
    st = doc.styles["Normal"]
    st.font.name = "SimSun"
    st.font.size = Pt(10.5)
    try:
        from docx.oxml.ns import qn
        st.element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")
    except Exception:
        pass

    h = doc.add_heading("工程签证单（AI 辅助生成草稿，需人工复核）", level=1)
    h.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p = doc.add_paragraph()
    p.add_run(f"项目名称：{project}        签证部位：{part or '—'}\n").bold = True
    p.add_run(f"签证编号：VISA-{jid}    日期：{datetime.date.today().isoformat()}\n")
    p.add_run("变更依据：新版图纸与旧版图纸比对差异（详见本次页面中的比对结果与变更清单）\n")
    p.add_run("说明：已匹配合同清单项的条目按页面确认工程量差组价；未匹配条目仅作兜底估算，正式申报前须由造价员按合同清单和现场实测复核。")

    total = _round_half_up(sum(i["amount"] for i in items), 2)
    table = doc.add_table(rows=1, cols=8)
    table.style = "Table Grid"
    hdr = table.rows[0].cells
    for j, t in enumerate(["序号", "变更类型", "合同清单项", "变更内容", "工程量增减", "单位", "本次单价(元)", "估算合价(元)"]):
        hdr[j].text = t
    for n, i in enumerate(items, 1):
        row = table.add_row().cells
        row[0].text = str(n)
        row[1].text = i["kind_cn"]
        row[2].text = (f"{i.get('boq_code', '')} {i.get('boq_name', '')}").strip() or "待匹配"
        row[3].text = i["content"] + (f"（{i['note']}）" if i["note"] else "")
        row[4].text = str(i["delta"])
        row[5].text = i["unit"]
        row[6].text = str(i["price"])
        row[7].text = f"{i['amount']:.2f}"
    doc.add_paragraph(f"估算合计：{total:.2f} 元（大写金额由造价员复核后填写）").runs[0].bold = True
    doc.add_paragraph("\n施工单位（签章）：              监理单位（签章）：              建设单位（签章）：")

    path = os.path.join(JOBS[jid]["dir"], f"工程签证单_{jid}_{time.time_ns()}.docx")
    doc.save(path)
    return path


# ── FastAPI ──────────────────────────────────────────────────────────────
app = FastAPI(title="图纸变更索赔 Agent Demo")


@app.get("/assets/zhijian-cloud-logo.png")
def brand_logo():
    return FileResponse(os.path.join(BASE_DIR, "assets", "zhijian-cloud-logo.png"), media_type="image/png")

@app.get("/assets/logo_cloud_compare.png")
def compare_logo():
    return FileResponse(os.path.join(BASE_DIR, "logo_cloud_compare.png"), media_type="image/png")

@app.get("/assets/{name}")
def menu_icon(name: str):
    if name in {'agent-overview.css', 'agent-overview.js', 'agent-workspace.css'}:
        return FileResponse(os.path.join(BASE_DIR, 'assets', name))
    if name not in {"menu-icon-1.png", "menu-icon-2.png", "menu-icon-3.png"}:
        return JSONResponse({"error": "资源不存在"}, status_code=404)
    return FileResponse(os.path.join(BASE_DIR, name), media_type="image/png")




def _tool_page(page: str):
    html_path = os.path.join(BASE_DIR, "demo_page.html")
    with open(html_path, "r", encoding="utf-8") as f:
        # Target the body tag: CSS selectors also contain data-page="compare".
        return f.read().replace('<body data-page="compare">', f'<body data-page="{page}">', 1)


@app.get("/", response_class=HTMLResponse)
def index():
    return _tool_page("compare")


@app.get("/tool/compare", response_class=HTMLResponse)
def compare_page():
    return _tool_page("compare")


@app.get("/tool/contract", response_class=HTMLResponse)
def contract_page():
    return _tool_page("contract")


@app.get("/tool/visa", response_class=HTMLResponse)
def visa_page():
    return _tool_page("visa")


@app.get("/api/samples")
def list_samples():
    return [{"key": k, "label": v[0]} for k, v in SAMPLES.items()]


@app.post("/api/compare")
async def compare(sample: str = Form(""), old_file: UploadFile = File(None), new_file: UploadFile = File(None)):
    jid = datetime.datetime.now().strftime("%H%M%S") + f"_{int(time.time() * 1000) % 10000:04d}"
    try:
        if sample:
            if sample not in SAMPLES:
                return JSONResponse({"error": "未知样例"}, status_code=400)
            _, old_p, new_p = SAMPLES[sample]
        else:
            if not old_file or not new_file:
                return JSONResponse({"error": "请同时上传旧版与新版图纸"}, status_code=400)
            for upload in (old_file, new_file):
                if not (upload.filename or "").lower().endswith(".dxf"):
                    return JSONResponse({"error": "只支持 DXF 格式图纸"}, status_code=400)
            tmp_dir = os.path.join(JOBS_DIR, "upload_" + jid)
            os.makedirs(tmp_dir, exist_ok=True)
            old_p = os.path.join(tmp_dir, "old_" + old_file.filename)
            new_p = os.path.join(tmp_dir, "new_" + new_file.filename)
            with open(old_p, "wb") as f:
                f.write(await old_file.read())
            with open(new_p, "wb") as f:
                f.write(await new_file.read())
        job = _run_compare(jid, old_p, new_p)
        job.update({
            "sample_key": sample,
            "sample_label": SAMPLES[sample][0] if sample in SAMPLES else "工具模式上传任务",
            "source": "tool",
        })
        _persist_job(jid)
        return {
            "job_id": jid, "counts": job["counts"], "changes": job["changes"],
            "old_file": os.path.basename(job.get("old_path", old_p)),
            "new_file": os.path.basename(job.get("new_path", new_p)),
            "summary_text": job["summary_text"], "summary_points": job["summary_points"],
            "has_png": job["has_png"], "zooms": job["zooms"], "created": job["created"],
        }
    except Exception as e:
        return JSONResponse({"error": f"比对失败：{e}"}, status_code=500)


@app.get("/api/jobs/{jid}")
def api_job(jid: str):
    job = JOBS.get(jid)
    if not job:
        return JSONResponse({"error": "任务不存在"}, status_code=404)
    if job.get("visa_items"):
        _job_visa_total(job)
        _persist_job(jid)
    result = {k: v for k, v in job.items() if k not in ("dir", "old_path", "new_path")}
    result["job_id"] = jid
    result["old_file"] = os.path.basename(job.get("old_path", ""))
    result["new_file"] = os.path.basename(job.get("new_path", ""))
    return result


@app.get("/api/jobs/{jid}/boq")
def get_boq(jid: str):
    if jid not in JOBS: return JSONResponse({"error": "任务不存在"}, status_code=404)
    return {"items": JOBS[jid].get("boq_items", []), "mappings": JOBS[jid].get("mappings", {})}

@app.get("/api/jobs/{jid}/quantities")
def job_quantities(jid: str):
    if jid not in JOBS: return JSONResponse({"error": "任务不存在"}, status_code=404)
    result = {}
    for c in JOBS[jid]["changes"]:
        unit = "m" if c.get("type") == "LINE" else "m²" if c.get("type") in ("HATCH", "LWPOLYLINE", "POLYLINE", "CIRCLE") else "个" if c.get("type") == "INSERT" else None
        value = _auto_boq_delta(c, unit) if unit else None
        if c.get("kind") == "MOVED" and unit: value = 0
        result[str(c["idx"])] = {"value": value, "unit": unit}
    return result

@app.post("/api/jobs/{jid}/boq")
def save_boq(jid: str, payload: dict = Body(...)):
    if jid not in JOBS: return JSONResponse({"error": "任务不存在"}, status_code=404)
    JOBS[jid]["boq_items"] = payload.get("items") or []
    _persist_job(jid)
    return {"items": JOBS[jid]["boq_items"]}

@app.post("/api/jobs/{jid}/mappings")
def save_mappings(jid: str, payload: dict = Body(...)):
    if jid not in JOBS: return JSONResponse({"error": "任务不存在"}, status_code=404)
    JOBS[jid].setdefault("mappings", {}).update(payload.get("mappings") or {})
    _persist_job(jid)
    return {"mappings": JOBS[jid]["mappings"]}

@app.post("/api/jobs/{jid}/hatch-volume")
def hatch_volume(jid: str, payload: dict = Body(...)):
    if jid not in JOBS: return JSONResponse({"error": "任务不存在"}, status_code=404)
    change = next((c for c in JOBS[jid]["changes"] if str(c["idx"]) == str(payload.get("idx")) and c.get("type") == "HATCH"), None)
    thickness = _finite_number(payload.get("thickness_m"))
    if change is None or thickness is None or thickness <= 0:
        return JSONResponse({"error": "请选择填充图元并输入大于 0 的厚度"}, status_code=400)
    JOBS[jid].setdefault("mappings", {}).setdefault(str(change["idx"]), {})["thickness_m"] = thickness
    _persist_job(jid)
    area = _area_delta_m2(change)
    return {"area_delta_m2": area, "volume_delta_m3": area * thickness if area is not None else None}

@app.get("/api/jobs/{jid}/image/{name}")
def job_image(jid: str, name: str):
    if jid not in JOBS or not re.fullmatch(r"result(_zoom\d*)?\.png", name):
        return JSONResponse({"error": "not found"}, status_code=404)
    path = os.path.join(JOBS[jid]["dir"], name)
    if not os.path.exists(path):
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(path)


@app.post("/api/jobs/{jid}/visa")
def make_visa(jid: str, payload: dict = Body(...)):
    if jid not in JOBS:
        return JSONResponse({"error": "任务不存在"}, status_code=404)
    selected = payload.get("indices", [])
    project = payload.get("project", "演示项目")
    part = payload.get("part", "")
    prices = _price_table(payload.get("prices"))
    boq_items = payload.get("boq_items") or []
    mappings = payload.get("mappings") or {}
    thicknesses = payload.get("thicknesses") or {}
    changes = JOBS[jid]["changes"]
    items = []
    for raw_idx in selected:
        try:
            idx = int(raw_idx)
        except (TypeError, ValueError):
            continue
        if idx < 0 or idx >= len(changes):
            continue
        mapping = mappings.get(str(idx), mappings.get(idx, {})) if isinstance(mappings, dict) else {}
        mapping = mapping if isinstance(mapping, dict) else {}
        code = mapping.get("boq_code") or mapping.get("code")
        boq_item = _find_boq_item(boq_items, code)
        thickness_m = mapping.get("thickness_m")
        if thickness_m is None and isinstance(thicknesses, dict):
            thickness_m = thicknesses.get(str(idx), thicknesses.get(idx))
        items.append(estimate_change(changes[idx], prices, boq_item,
                                     mapping.get("delta_qty"), thickness_m))
    if not items:
        return JSONResponse({"error": "未勾选任何变更条目"}, status_code=400)
    path = gen_visa_docx(jid, items, project, part)
    total = _round_half_up(sum(i["amount"] for i in items), 2)
    review_required = any(i.get("needs_review") or i.get("price") == "-" for i in items)
    JOBS[jid].update({
        "visa_items": items,
        "visa_total": total,
        "visa_prices": prices,
        "visa_review_required": review_required,
        "visa_project": project,
        "visa_part": part,
    })
    _persist_job(jid)
    return {
        "items": items,
        "total": total,
        "prices": prices,
        "review_required": review_required,
        "download": f"/api/jobs/{jid}/visa.docx?version={os.path.basename(path).rsplit('_', 1)[-1][:-5]}",
        "file": os.path.basename(path),
    }


@app.get("/api/jobs/{jid}/visa.docx")
def download_visa(jid: str, version: str = ""):
    if jid not in JOBS:
        return JSONResponse({"error": "任务不存在"}, status_code=404)
    if version:
        if not re.fullmatch(r"\d+", version):
            return JSONResponse({"error": "签证版本无效"}, status_code=400)
        name = f"工程签证单_{jid}_{version}.docx"
        path = os.path.join(JOBS[jid]["dir"], name)
        if not os.path.isfile(path):
            return JSONResponse({"error": "签证版本不存在，请重新生成"}, status_code=404)
        return FileResponse(path, filename=name, headers={"Cache-Control": "no-store"})
    job = JOBS[jid]
    if job.get("visa_items"):
        path = gen_visa_docx(jid, job["visa_items"], job.get("visa_project", ""), job.get("visa_part", ""))
        return FileResponse(path, filename=os.path.basename(path), headers={"Cache-Control": "no-store"})
    for f in sorted(os.listdir(JOBS[jid]["dir"]), reverse=True):
        if f.startswith("工程签证单") and f.endswith(".docx"):
            return FileResponse(os.path.join(JOBS[jid]["dir"], f), filename=f, headers={"Cache-Control": "no-store"})
    return JSONResponse({"error": "签证单尚未生成"}, status_code=404)


# ── Agent 模式 ───────────────────────────────────────────────────────────
import agent_core as AG


@app.get("/agent", response_class=HTMLResponse)
def agent_page():
    with open(os.path.join(BASE_DIR, "agent_page.html"), "r", encoding="utf-8") as f:
        return f.read()


@app.get("/api/agent/llm_status")
def llm_status():
    cfg = AG.load_llm_config()
    return {
        "llm_enabled": bool(cfg),
        "base_url": (cfg or {}).get("base_url", ""),
        "model": (cfg or {}).get("model", ""),
        "planner": "LLM function calling" if cfg else "内置规则规划器",
    }


@app.get("/api/projects")
def api_list_projects():
    return AG.list_projects()


@app.get("/api/agent/overview")
def api_agent_overview():
    projects = []
    for meta in AG.list_projects():
        name = meta['name']
        ledger = AG.get_ledger(name)
        versions = AG.list_versions(name)
        archives = AG.list_archive(name)['archives']
        version_rows = [v for g in versions['drawings'].values() for v in g.get('versions', [])]
        reports_dir = os.path.join(AG._proj_dir(name), 'reports')
        reports = [dict(name=f, modified=datetime.datetime.fromtimestamp(os.path.getmtime(os.path.join(reports_dir, f))).strftime('%Y-%m-%d %H:%M:%S'))
                   for f in os.listdir(reports_dir) if f.startswith('diff_report_') and f.endswith('.md')]
        statuses = ledger['stats']['by_status']
        count = len(ledger['summaries'])
        progress = round(sum({'待确认': 0, '已确认': 1, '已签证': 2, '已索赔': 3}.get(s['status'], 0)
                             for s in ledger['summaries']) / (3 * count) * 100) if count else 0
        latest = max(version_rows, key=lambda v: v.get('registered', ''), default={})
        projects.append({**meta, 'stats': ledger['stats'], 'progress': progress,
                         'pending': statuses.get('待确认', 0),
                         'in_progress': statuses.get('已确认', 0) + statuses.get('已签证', 0),
                         'completed': statuses.get('已索赔', 0),
                         'version_count': len(version_rows), 'latest_version': latest.get('version', '—'),
                         'version_updated': latest.get('registered', ''),
                         'archive_count': len(archives), 'archive_files': sum(len(a.get('files', [])) for a in archives),
                         'report_count': len(reports),
                         'reports_today': sum(r['modified'][:10] == datetime.datetime.now().strftime('%Y-%m-%d') for r in reports),
                         'reports_month': sum(r['modified'][:7] == datetime.datetime.now().strftime('%Y-%m') for r in reports),
                         'updated': max([meta.get('created', '')] + [v.get('registered', '') for v in version_rows]
                                        + [a.get('last_run', a.get('archived', '')) for a in archives]
                                        + [i.get('created', '') for i in ledger['items']]),
                         'workflow': [bool(version_rows), bool(archives), bool(count), bool(reports)]})
    return {'projects': projects}


@app.post("/api/projects")
def api_create_project(payload: dict = Body(...)):
    name = (payload.get("name") or "").strip()
    if not name:
        return JSONResponse({"error": "项目名不能为空"}, status_code=400)
    return AG.create_project(name, payload.get("stage", "施工阶段"),
                             payload.get("contract", "固定单价合同"), payload.get("note", ""))


@app.get("/api/projects/{project}/versions")
def api_versions(project: str):
    return AG.list_versions(project)


@app.get("/api/projects/{project}/ledger")
def api_ledger(project: str, status: str = "", risk: str = ""):
    ledger = AG.get_ledger(project, status, risk)
    for summary in ledger["summaries"]:
        _restore_ledger_geometry(summary["items"])
    project_items = AG.get_ledger(project)['items']
    ledger['boq_options'] = _project_boq_options(project_items)
    for item in ledger['items']:
        item['pricing_boq'] = _ledger_boq(item, project_items)
    return ledger


def _project_boq_options(project_items):
    candidates = []
    job_ids = {i.get('source', {}).get('job_id') for i in project_items}
    estimates = [i.get('estimate', {}) for i in project_items]
    for jid in sorted(j for j in job_ids if j):
        job = JOBS.get(jid, {})
        candidates.extend(job.get('boq_items', []))
        estimates.extend(job.get('visa_items', []))
    candidates.extend(dict(code=e.get('boq_code'), name=e.get('boq_name'),
                           unit=e.get('unit'), unit_price=e.get('price')) for e in estimates)
    options = {}
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        boq = _find_boq_item([candidate], candidate.get('code'))
        if not boq or not _unit_is(boq['unit'], 'm³', 'm3', '立方米'):
            continue
        key = hashlib.sha256(json.dumps(boq, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()
        options[key] = {'key': key, **boq}
    return list(options.values())


def _ledger_boq(item, project_items):
    def from_estimate(e):
        code = e.get('boq_code')
        return _find_boq_item([dict(code=code, name=e.get('boq_name'),
                                   unit=e.get('unit'), unit_price=e.get('price'))], code)

    own = from_estimate(item.get('estimate', {}))
    if own:
        return own
    source = item.get('source', {})
    candidates = []
    seen_jobs = set()
    for row in project_items:
        src = row.get('source', {})
        if (src.get('drawing_no'), src.get('sample_key', '')) != (source.get('drawing_no'), source.get('sample_key', '')):
            continue
        boq = from_estimate(row.get('estimate', {}))
        if boq and row.get('layer') == item.get('layer') and row.get('type') == item.get('type'):
            candidates.append(boq)
        jid = src.get('job_id')
        if jid in seen_jobs:
            continue
        seen_jobs.add(jid)
        job = JOBS.get(jid, {})
        changes = {c['idx']: c for c in job.get('changes', [])}
        for estimate in job.get('visa_items', []):
            change = changes.get(estimate['idx'], {})
            boq = from_estimate(estimate)
            if boq and change.get('layer') == item.get('layer') and change.get('type') == item.get('type'):
                candidates.append(boq)
    # Only inherit an unambiguous confirmed mapping within this drawing chain.
    unique = {(b['code'], b['name'], b['unit'], b['unit_price']): b for b in candidates}
    return next(iter(unique.values())) if len(unique) == 1 else None


def _contract_estimate(change, source, project_items, previous=None):
    previous = previous or {}
    boq = _ledger_boq(dict(source=source, layer=change.get('layer'), type=change.get('type'),
                           estimate=previous), project_items)
    if boq:
        return estimate_change(change, boq_item=boq, thickness_m=previous.get('thickness_m'))
    estimate = estimate_change(change)
    estimate.update(delta='-', unit='-', price='-', amount=0.0, boq_code='', boq_name='',
                    needs_review=True, note='未匹配已确认的合同清单，暂不计价',
                    content=(change.get('new') or change.get('old') or change.get('kind_cn', '变更'))[:100])
    return estimate


def _restore_ledger_geometry(items):
    """Recover older ledger rows only when the persisted task order matches exactly."""
    if not items:
        return
    job = JOBS.get(items[0].get("source", {}).get("job_id"), {})
    changes = job.get("changes", [])
    estimates = {e['idx']: e for e in job.get('visa_items', [])}
    selected = [c for c in changes if c['idx'] in estimates] if estimates else changes
    matched = len(selected) == len(items) and all(
        all(i.get(k) == c.get(k) for k in ('kind', 'type', 'layer'))
        for i, c in zip(items, selected))
    for index, item in enumerate(items):
        if not item.get('change') and matched:
            item['change'] = selected[index]
            original = estimates.get(selected[index]['idx'], {})
            item['estimate'] = {**original, **item['estimate']}
        if item.get('change'):
            item['area_delta_m2'] = _area_delta_m2(item['change'])


@app.post("/api/projects/{project}/ledger/price")
def api_ledger_price(project: str, payload: dict = Body(...)):
    path = os.path.join(AG._proj_dir(project), 'ledger.json')
    ledger = AG._load_json(path, {'items': [], 'seq': 0})
    item = next((i for i in ledger['items'] if i['id'] == payload.get('item_id')), None)
    if not item:
        return JSONResponse({'error': '台账条目不存在'}, status_code=404)
    if item.get('status') != '待确认':
        return JSONResponse({'error': '仅待确认条目可修改计价'}, status_code=400)
    peers = [i for i in ledger['items'] if i.get('source') == item.get('source')]
    _restore_ledger_geometry(peers)
    thickness = _finite_number(payload.get('thickness_m'))
    boq = _ledger_boq(item, ledger['items'])
    if payload.get('boq_choice'):
        boq = next((b for b in _project_boq_options(ledger['items'])
                    if b['key'] == payload['boq_choice']), None)
        if not boq:
            return JSONResponse({'error': '所选清单不属于本项目或已变化，请刷新后重新选择'}, status_code=400)
    area = _area_delta_m2(item['change']) if item.get('change') else None
    if thickness is None or thickness <= 0 or area is None:
        return JSONResponse({'error': '请填写正数厚度（m）；条目须能解析面积差'}, status_code=400)
    if not boq or not _unit_is(boq['unit'], 'm³', 'm3', '立方米'):
        return JSONResponse({'error': '请先选择本项目已有的体积计价清单'}, status_code=400)
    estimate = estimate_change(item['change'], boq_item=boq, thickness_m=thickness)
    item['estimate'] = estimate
    item['content'] = estimate['content']
    item['todos'] = AG._make_todos(item['change'], estimate, AG.get_project(project) or {})
    AG._save_json(path, ledger)
    return {'ok': True, 'estimate': estimate}


@app.delete("/api/projects/{project}/ledger/visas/{visa_id}")
def api_ledger_delete(project: str, visa_id: str):
    path = os.path.join(AG._proj_dir(project), 'ledger.json')
    ledger = AG._load_json(path, {'items': [], 'seq': 0})
    summary = next((s for s in AG.get_ledger(project)['summaries'] if s['id'] == visa_id), None)
    if summary is None:
        return JSONResponse({'error': '签证单不存在或已删除'}, status_code=404)
    ids = {i['id'] for i in summary['items']}
    deleted_at = datetime.datetime.now().isoformat(timespec='seconds')
    ledger.setdefault('deleted_items', []).extend({**i, 'deleted_at': deleted_at} for i in ledger['items'] if i['id'] in ids)
    ledger['items'] = [i for i in ledger['items'] if i['id'] not in ids]
    _delete_visa_files(project, summary['source'].get('job_id', ''), ledger['items'])
    AG._save_json(path, ledger)
    return {'ok': True}


def _delete_visa_files(project, job_id, remaining_items):
    if not job_id:
        return

    def remove_generated(root, target):
        root = os.path.realpath(root)
        target = os.path.realpath(target)
        if os.path.commonpath([root, target]) != root or target == root:
            raise ValueError('文件路径超出生成目录')
        if os.path.isdir(target):
            shutil.rmtree(target)
        elif os.path.isfile(target):
            os.remove(target)

    live_jobs = {i.get('source', {}).get('job_id') for i in remaining_items}
    if job_id in live_jobs:
        return
    directory = AG._proj_dir(project)
    remove_generated(os.path.join(directory, 'reports'), os.path.join(directory, 'reports', f'diff_report_{job_id}.md'))
    index_path = os.path.join(directory, 'archive', 'index.json')
    index = AG._load_json(index_path, {'archives': []})
    kept = []
    for archive in index['archives']:
        runs = {archive.get('job_id')} | {r.get('job_id') for r in archive.get('runs', [])}
        if job_id in runs and not (runs & live_jobs):
            remove_generated(os.path.join(directory, 'archive'), os.path.join(directory, 'archive', archive['job_id']))
        else:
            kept.append(archive)
    index['archives'] = kept
    if os.path.isfile(index_path):
        AG._save_json(index_path, index)
    for other in AG.list_projects():
        if other['name'] != project:
            other_items = AG.get_ledger(other['name'])['items']
            if any(i.get('source', {}).get('job_id') == job_id for i in other_items):
                return
            for archive in AG.list_archive(other['name'])['archives']:
                if archive.get('job_id') == job_id or any(r.get('job_id') == job_id for r in archive.get('runs', [])):
                    return
    remove_generated(JOBS_DIR, os.path.join(JOBS_DIR, job_id))
    JOBS.pop(job_id, None)


@app.post("/api/projects/{project}/ledger/status")
def api_ledger_status(project: str, payload: dict = Body(...)):
    ok = AG.set_ledger_status(project, payload.get("item_id", ""), payload.get("status", ""))
    if not ok:
        return JSONResponse({"error": "状态需按 待确认 → 已确认 → 已签证 → 已索赔 顺序推进"}, status_code=400)
    return {"ok": True}


@app.post("/api/projects/{project}/versions/status")
def api_version_status(project: str, payload: dict = Body(...)):
    ok = AG.set_version_status(project, payload.get("drawing_no", ""), payload.get("sample_key", ""),
                               payload.get("version", ""), payload.get("status", ""))
    if not ok:
        return JSONResponse({"error": "版本不存在或状态不允许；新版本须先批准，再设为现场使用"}, status_code=400)
    return {"ok": True}


@app.get("/api/projects/{project}/archive")
def api_archive(project: str):
    return AG.list_archive(project)


@app.get("/api/projects/{project}/archive/{job_id}/file/{name}")
def api_archive_file(project: str, job_id: str, name: str):
    path = os.path.join(AG._proj_dir(project), "archive", job_id, name)
    if not os.path.exists(path):
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(path, filename=name)


@app.get("/api/projects/{project}/reports/{name}")
def api_report(project: str, name: str):
    path = os.path.join(AG._proj_dir(project), "reports", name)
    if not os.path.exists(path):
        return JSONResponse({"error": "not found"}, status_code=404)
    with open(path, "r", encoding="utf-8") as f:
        return {"name": name, "content": f.read()}


def _run_agent_workflow(payload: dict, uploaded_new_path: str = "", uploaded_name: str = ""):
    project = (payload.get("project") or "").strip()
    drawing_no = (payload.get("drawing_no") or "图纸-01").strip()
    sample = payload.get("sample", "")
    source_job_id = (payload.get("source_job_id") or "").strip()
    source_job = JOBS.get(source_job_id) if source_job_id else None
    if source_job_id and not source_job:
        return JSONResponse({"error": f"工具模式任务 {source_job_id} 不存在或已重启服务"}, status_code=400)
    if source_job and not source_job.get("visa_items"):
        return JSONResponse({"error": "请先在工具模式完成量差估算并确认签证计价，再交给 Agent"}, status_code=400)
    if source_job:
        sample = source_job.get("sample_key") or ""
        sample_label = source_job.get("sample_label") or "工具模式上传任务"
        sample_old_path = source_job.get("old_path", "")
        sample_new_path = source_job.get("new_path", "")
    else:
        sample_label, sample_old_path, sample_new_path = SAMPLES.get(sample, ("", "", ""))
    if not project:
        return JSONResponse({"error": "请先创建/选择项目"}, status_code=400)
    if not source_job and sample and sample not in SAMPLES and not uploaded_new_path and not payload.get("target_version"):
        return JSONResponse({"error": "该图纸来源没有内置新版，请上传新收到的 DXF"}, status_code=400)
    if not AG.get_project(project):
        AG.create_project(project, payload.get("stage", "施工阶段"), payload.get("contract", "固定单价合同"))
    meta = AG.get_project(project)
    prices = _price_table(payload.get("prices"))
    group_key = AG._version_group_key(drawing_no, sample)
    group = AG.list_versions(project).get("drawings", {}).get(group_key, {})
    baseline_version = (group.get("field_version") or "").strip()
    if payload.get("baseline_version") and payload["baseline_version"] != baseline_version:
        return JSONResponse({"error": "现场使用版本已变化，请刷新后重试"}, status_code=400)
    baseline_path = AG.get_version_file(project, drawing_no, baseline_version, sample) if baseline_version else sample_old_path
    if source_job:
        if baseline_version and AG._file_hash(baseline_path) != AG._file_hash(sample_old_path):
            return JSONResponse({"error": "工具任务旧图与当前现场使用版本不一致，请使用现场版本重新比对"}, status_code=400)
        baseline_path = sample_old_path
        new_path = sample_new_path
    else:
        new_path = uploaded_new_path or sample_new_path
    if baseline_version and not baseline_path:
        return JSONResponse({"error": f"现场使用版本 {baseline_version} 不存在，请先在版本管理中选择有效版本"}, status_code=400)
    target_version = payload.get("target_version")
    if target_version:
        versions = group.get("versions", [])
        target = next((v for v in versions if v.get("version") == target_version), None)
        if source_job or uploaded_new_path or not baseline_version or not target or target.get("status") not in {"已收到", "已批准"}:
            return JSONResponse({"error": "请选择已收到或已批准的版本，与当前现场使用版本比对"}, status_code=400)
        new_path = AG.get_version_file(project, drawing_no, target_version, sample)
    elif not source_job and not uploaded_new_path and baseline_version:
        versions = group.get("versions", [])
        baseline_index = next((i for i, v in enumerate(versions) if v.get("version") == baseline_version), -1)
        candidates = [(i, v) for i, v in enumerate(versions)
                      if i > baseline_index and v.get("status") in ("已收到", "已批准")]
        if candidates:
            target = candidates[-1][1]
            new_path = AG.get_version_file(project, drawing_no, target["version"], sample) or sample_new_path
    if not baseline_path or not os.path.isfile(baseline_path):
        return JSONResponse({"error": "该图号尚无现场基准，请先在工具模式上传旧图和新图，完成量差估算并确认签证计价后再交给 Agent"}, status_code=400)
    if not new_path or not os.path.isfile(new_path):
        return JSONResponse({"error": "请上传新收到的 DXF 图纸"}, status_code=400)
    if AG._file_hash(baseline_path) == AG._file_hash(new_path):
        return JSONResponse({"error": "新图与现场使用版本内容相同，没有新的差异可比对"}, status_code=400)

    run_ctx = {"project": project, "drawing_no": drawing_no, "sample": sample,
               "sample_label": sample_label,
               "baseline_version": baseline_version or "待登记",
               "incoming_file": uploaded_name or "内置样例新版",
               "stage": meta.get("stage"), "contract": meta.get("contract"),
               "prices": prices,
               "source_job_id": source_job_id,
               "source_reused": bool(source_job_id)}
    events = []

    def emit(kind, data):
        events.append({"t": datetime.datetime.now().strftime("%H:%M:%S"), "kind": kind, **data})

    def dispatch(name, args):
        if name == "version_register":
            role = args.get("file_role", "new")
            existing_version = baseline_version if role == "old" else target_version
            if existing_version:
                run_ctx[f"ver_{role}"] = existing_version
                return {"version": existing_version, "reused": True}
            path = baseline_path if role == "old" else new_path
            label = args.get("label", "")
            if role == "new" and uploaded_name:
                label = f"收到新版：{uploaded_name}"
            r = AG.register_version(project, drawing_no, path, label,
                                    sample_key=sample, sample_label=sample_label,
                                    status="现场使用" if role == "old" and not baseline_version else "已收到")
            run_ctx[f"ver_{role}"] = r["version"]
            return r
        if name == "compare":
            if source_job:
                jid = source_job_id
                job = source_job
                reused = True
            else:
                jid = datetime.datetime.now().strftime("%H%M%S") + f"_{int(time.time() * 1000) % 10000:04d}"
                job = _run_compare(jid, baseline_path, new_path)
                reused = False
            run_ctx["job_id"] = jid
            run_ctx["counts"] = job["counts"]
            run_ctx["changes_n"] = len(job["changes"])
            return {"job_id": jid, "counts": job["counts"], "changes": len(job["changes"]), "reused": reused}
        if name == "report":
            job = JOBS[run_ctx["job_id"]]
            return AG.generate_report(project, run_ctx["job_id"], drawing_no,
                                      run_ctx.get("ver_old", "v1"), run_ctx.get("ver_new", "v2"),
                                      run_ctx["counts"], job["changes"], job["dir"], sample_label)
        if name == "archive":
            job = JOBS[run_ctx["job_id"]]
            return AG.archive_job(project, run_ctx["job_id"], job["dir"], drawing_no,
                                  run_ctx.get("ver_old", "v1"), run_ctx.get("ver_new", "v2"),
                                  sample, sample_label)
        if name == "ledger_update":
            job = JOBS[run_ctx["job_id"]]
            if source_job:
                estimates = source_job["visa_items"]
                selected = {item["idx"] for item in estimates}
                changes = [change for change in job["changes"] if change["idx"] in selected]
                # Re-sum detail rows so a stale/missing persisted total cannot display 0.
                run_ctx["source_price_total"] = _job_visa_total(source_job)
                _persist_job(source_job_id)
            else:
                changes = job["changes"]
                project_items = AG.get_ledger(project)['items']
                source = dict(drawing_no=drawing_no, sample_key=sample)
                estimates = [_contract_estimate(c, source, project_items) for c in changes]
            r = AG.update_ledger(project, run_ctx["job_id"], drawing_no,
                                 run_ctx.get("ver_old", "v1"), run_ctx.get("ver_new", "v2"),
                                 changes, estimates, meta, sample, sample_label)
            run_ctx["_ledger_added"] = r["added"]
            return r
        if name == "review":
            ledger = AG.get_ledger(project)
            high = [i for i in ledger["items"] if i["risk"] == "高" and i["status"] == "待确认"]
            mid = [i for i in ledger["items"] if i["risk"] == "中" and i["status"] == "待确认"]
            findings = []
            if high:
                findings.append(f"{len(high)} 条高风险（结构类）变更待确认，建议优先走设计院变更审批")
            if mid:
                findings.append(f"{len(mid)} 条中风险变更待确认，建议本周内完成现场收方")
            if run_ctx.get("changes_n", 0) == 0:
                findings.append("本次比对未发现变更，台账无需新增，已留档备查")
            if not findings:
                findings.append("全部条目为低风险，按常规一单一算流程处理")
            return {"high_risk_pending": len(high), "mid_risk_pending": len(mid), "findings": findings}
        if name == "finish":
            return {"done": True}
        return {"error": f"未知工具 {name}"}

    sop = [{"no": i + 1, "key": k, "name": n} for i, (k, n) in enumerate(AG.SOP_STEPS)]
    emit("plan", {"goal": f"处理 {drawing_no} 的新图纸（现场基准 {run_ctx['baseline_version']}，样例 {sample}），完成标准工作流程", "sop": sop})

    cfg = AG.load_llm_config()
    try:
        if cfg:
            outcome = AG.run_agent_llm(f"以现场使用版本为基准处理 {drawing_no} 新到图纸，完成标准工作流程", run_ctx, dispatch, emit)
        else:
            outcome = AG.run_agent_rule(f"以现场使用版本为基准处理 {drawing_no} 新到图纸", run_ctx, dispatch, emit)
    except Exception as e:
        emit("error", {"message": str(e)})
        outcome = {"planner": "error", "summary": f"Agent 运行异常：{e}"}

    ledger = api_ledger(project)
    return {
        "planner": outcome.get("planner"), "summary": outcome.get("summary"),
        "events": events, "sop": sop, "run_ctx": {k: v for k, v in run_ctx.items() if not k.startswith("_")},
        "ledger": ledger,
    }


@app.post("/api/agent/run")
def api_agent_run(payload: dict = Body(...)):
    return _run_agent_workflow(payload)


@app.post("/api/agent/run_upload")
def api_agent_run_upload(project: str = Form(...), drawing_no: str = Form("图纸-01"),
                         sample: str = Form(""), baseline_version: str = Form(""),
                         incoming_file: UploadFile = File(...), stage: str = Form("施工阶段"),
                         contract: str = Form("固定单价合同"),
                         length_price: float = Form(12.0), area_price: float = Form(85.0),
                         item_price: float = Form(350.0)):
    extension = os.path.splitext(incoming_file.filename or "")[1].lower()
    if extension != ".dxf":
        return JSONResponse({"error": "Agent 新版本上传目前仅支持 DXF 文件"}, status_code=400)
    with tempfile.NamedTemporaryFile(prefix="agent_incoming_", suffix=".dxf", dir=JOBS_DIR, delete=False) as temp:
        shutil.copyfileobj(incoming_file.file, temp)
        temp_path = temp.name
    try:
        payload = {"project": project, "drawing_no": drawing_no, "sample": sample,
                   "baseline_version": baseline_version, "stage": stage, "contract": contract,
                   "prices": {"length_per_m": length_price, "area_per_m2": area_price,
                              "item_per_unit": item_price}}
        return _run_agent_workflow(payload, temp_path, incoming_file.filename or "新图纸.dxf")
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


if __name__ == "__main__":
    print("工具模式服务启动中：http://127.0.0.1:8321/tool/compare", flush=True)
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8321")), log_level="info")


