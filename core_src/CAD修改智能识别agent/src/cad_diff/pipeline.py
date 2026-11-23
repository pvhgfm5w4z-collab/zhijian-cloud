#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
"""
管线编排：把各层串成一次完整比对

load -> extract -> diff -> 高亮/渲染/报告/栅格/语义。返回产出文件清单。
"""
from __future__ import annotations
import os
import zipfile
from .config import load_config
from . import io_dxf, parser, diff as D, highlight, render, report, llm, raster, align


def run(old_path, new_path, out_prefix, cfg):
    """跑一次完整比对；out_prefix 为输出文件前缀(不含扩展名)。返回 manifest。"""
    # 确保输出目录存在（out_prefix 的父目录）
    out_dir = os.path.dirname(out_prefix)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    # 输入若是 DWG，先统一转为 DXF 并复用同一路径：
    # 否则高亮/渲染层会各自再触发一次转换，导致同一 dwg 被重复转换而失败。
    if str(old_path).lower().endswith(".dwg"):
        old_path = io_dxf.convert_dwg(old_path)
    if str(new_path).lower().endswith(".dwg"):
        new_path = io_dxf.convert_dwg(new_path)
    old_doc, old_proxy = io_dxf.load_doc(old_path)
    new_doc, new_proxy = io_dxf.load_doc(new_path)
    old_recs, old_meta = parser.extract_entities(old_doc, cfg)
    new_recs, new_meta = parser.extract_entities(new_doc, cfg)

    registration_mode = cfg.get("registration", {}).get("method", "none")
    registration_info = {"method": registration_mode, "applied": registration_mode != "none"}
    if registration_mode == "none":
        tx, ty = align.estimate_translation(old_recs, new_recs)
        registration_info.update({"estimated_translation": [tx, ty], "used_for_matching": True})

    print(f"[info] 旧版实体数={len(old_recs)}  新版实体数={len(new_recs)}")
    changes = D.diff(old_recs, new_recs, cfg, align=True)
    cnt = D.summarize(changes)
    print(f"[info] 变更: 新增 {cnt['ADDED']} | 删除 {cnt['DELETED']} | "
          f"修改 {cnt['MODIFIED']} | 移动 {cnt['MOVED']}")

    out = {"changes": len(changes), "changes_list": changes, "counts": cnt, "files": {}}
    ocfg = cfg["output"]

    if ocfg.get("dxf"):
        p = out_prefix + ".dxf"
        highlight.annotate_dxf(new_path, changes, p, cfg)
        out["files"]["dxf"] = p
    if ocfg.get("png"):
        p = out_prefix + ".png"
        render.draw_comparison(old_recs, new_recs, changes, p, cfg)
        out["files"]["png"] = p
    zooms = []
    if ocfg.get("zoom"):
        zooms = render.draw_zoom(old_recs, new_recs, changes, out_prefix, cfg)
        out["files"]["zoom"] = zooms

    # 语义摘要
    summary_obj = llm.llm_summary(changes, cfg)
    if registration_mode == "none":
        tx, ty = registration_info["estimated_translation"]
        if abs(tx) >= cfg["tolerances"]["pos_eps"] or abs(ty) >= cfg["tolerances"]["pos_eps"]:
            note = (f"坐标配准关闭；检测到整体平移候选 dx={tx:+.3f}、dy={ty:+.3f}。"
                    "该偏移仅用于匹配对应实体，移动仍按旧版与新版原始坐标逐项标注。")
        else:
            note = "坐标配准关闭；按原始坐标比较，超过位移阈值的实体会列为移动。"
        if isinstance(summary_obj, dict):
            summary_obj["text"] = note + ("\n" + summary_obj.get("text", "") if summary_obj.get("text") else "")

    # 栅格级兜底（可选）
    raster_box = []
    if ocfg.get("raster") or cfg["raster"].get("enabled"):
        rp = out_prefix + "_raster.png"
        _, boxes = raster.raster_diff(old_recs, new_recs, rp, cfg)
        if boxes:
            out["files"]["raster"] = rp
            raster_box = boxes

    meta = {
        "old_path": old_path, "new_path": new_path,
        "old_entities": len(old_recs), "new_entities": len(new_recs),
        "old_proxy": old_proxy, "new_proxy": new_proxy,
        "skipped_types": new_meta.get("skipped_types", {}),
        "block_expanded": new_meta.get("block_expanded", 0),
        "raster_boxes": raster_box,
        "registration": registration_mode,
        "registration_info": registration_info,
    }
    if ocfg.get("json"):
        p = out_prefix + ".json"
        report.write_json(changes, p, meta, summary_obj)
        out["files"]["json"] = p
    if ocfg.get("txt"):
        p = out_prefix + ".txt"
        report.write_txt_report(changes, p, summary_obj["text"])
        out["files"]["txt"] = p
    if ocfg.get("docx"):
        p = out_prefix + ".docx"
        imgs = [out["files"].get("png")] + zooms
        report.write_docx(changes, p, cfg, imgs, summary_obj)
        out["files"]["docx"] = p
    if ocfg.get("pdf"):
        p = out_prefix + ".pdf"
        imgs = [out["files"].get("png")] + zooms
        report.write_pdf(changes, p, cfg, imgs, summary_obj)
        out["files"]["pdf"] = p
    if ocfg.get("html"):
        p = out_prefix + ".html"
        imgs = [out["files"].get("png")] + zooms
        report.write_html(changes, p, cfg, imgs, summary_obj)
        out["files"]["html"] = p

    out["summary"] = summary_obj
    out["meta"] = meta

    # 把本次所有产物聚合为一个 zip（便于交付/下载），同时受 output.zip 开关控制
    if ocfg.get("zip", True):
        zip_path = out_prefix + ".zip"
        _pack_zip(zip_path, out["files"], zooms)
        out["files"]["zip"] = zip_path

    return out


def _pack_zip(zip_path, files, zooms):
    """把 manifest.files 中收集到的所有产物打包成一个 zip（扁平文件名，避免路径穿越）。"""
    seen = set()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # 主产物（可能含 list 类型的 zoom 等，已在 zooms 里单独处理）
        for kind, p in files.items():
            if kind == "zoom":
                continue
            if isinstance(p, list):
                for sub in p:
                    _add(zf, sub, seen)
            elif p:
                _add(zf, p, seen)
        # zoom 列表单独补一次（files["zoom"] 是 list，上面的分支已覆盖；此处兜底）
        for z in zooms:
            _add(zf, z, seen)


def _add(zf, path, seen):
    path = str(path)
    if not path or not os.path.exists(path):
        return
    name = os.path.basename(path)
    # 同名冲突则加序号
    base, ext = os.path.splitext(name)
    unique = name
    i = 1
    while unique in seen:
        unique = f"{base}_{i}{ext}"
        i += 1
    seen.add(unique)
    zf.write(path, unique)


def discover_pairs(folder):
    """目录模式：在每个子目录内，按文件名配对 同目录、同名(仅 old/new 不同) 的图纸。
    仅配对 .dwg/.dxf（忽略 .clean.dxf 等中间文件），避免跨数据集/跨格式误配。"""
    pairs = []
    for root, _, fs in os.walk(folder):
        olds, news = {}, {}
        for f in fs:
            low = f.lower()
            if ".clean." in low or not low.endswith((".dxf", ".dwg")):
                continue
            if "old" in low:
                key = low.replace("old", "")
                olds.setdefault(key, []).append(os.path.join(root, f))
            elif "new" in low:
                key = low.replace("new", "")
                news.setdefault(key, []).append(os.path.join(root, f))
        for key in set(olds) & set(news):
            # 取同目录、同名(去掉 old/new 后相同)的一对
            o = sorted(olds[key])[0]
            n = sorted(news[key])[0]
            pairs.append((o, n))
    return pairs
