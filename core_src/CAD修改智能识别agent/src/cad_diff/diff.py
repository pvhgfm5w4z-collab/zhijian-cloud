#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
"""
Diff 层：空间候选匹配 + 一对一贪心 + 变更分类

候选：RTree 只取邻近实体（POS_TOL 半径），避免 O(n²)。
评分：score = 位置距离 + 2×几何距离；同类型/同层/几何兼容才入选。
分类：匹配后按 pos/geom 容差判 ADDED / DELETED / MODIFIED / MOVED。
"""
from __future__ import annotations
import math
from collections import defaultdict
from rtree import index as rtree_index
from .geometry import geom_dist, pos_dist, rec_shape


def diff(old, new, cfg, align=True):
    tol = cfg["tolerances"]
    POS_TOL, POS_EPS, GEOM_EPS = tol["pos_tol"], tol["pos_eps"], tol["geom_eps"]

    raw_old = old
    method = cfg.get("registration", {}).get("method", "none")
    match_old = old
    if align and method == "none":
        # Estimate a common offset only to associate the same entities when a
        # whole drawing moved. Classification below always uses raw coordinates.
        from .align import estimate_translation, apply_translation
        tx, ty = estimate_translation(raw_old, new)
        if tx or ty:
            match_old = apply_translation(raw_old, tx, ty)
    elif align:
        from .align import register
        match_old, _tf = register(old, new, cfg)

    idx = rtree_index.Index()
    for i, r in enumerate(match_old):
        b = r["bbox"]
        idx.insert(i, (b[0], b[1], b[2], b[3]))

    matched_old = set(); matched_new = set()
    pairs = []

    # DXF handles persist when an existing entity is edited or moved. Use
    # that source identity before spatial matching so a far move of one member
    # of a repeated pattern does not become a deletion plus an addition.
    old_ids = defaultdict(list); new_ids = defaultdict(list)
    for i, r in enumerate(raw_old):
        if r.get("source_id") is not None:
            old_ids[r["source_id"]].append(i)
    for j, r in enumerate(new):
        if r.get("source_id") is not None:
            new_ids[r["source_id"]].append(j)
    for source_id in old_ids.keys() & new_ids.keys():
        if len(old_ids[source_id]) != 1 or len(new_ids[source_id]) != 1:
            continue
        i, j = old_ids[source_id][0], new_ids[source_id][0]
        if raw_old[i]["type"] != new[j]["type"] or raw_old[i]["layer"] != new[j]["layer"]:
            continue
        pairs.append((i, j))
        matched_old.add(i); matched_new.add(j)

    for j, nr in enumerate(new):
        if j in matched_new:
            continue
        b = nr["bbox"]
        cands = list(idx.intersection((b[0] - POS_TOL, b[1] - POS_TOL,
                                       b[2] + POS_TOL, b[3] + POS_TOL)))
        best = None; best_score = math.inf
        for i in cands:
            if i in matched_old:
                continue
            or_ = match_old[i]
            if or_["type"] != nr["type"] or or_["layer"] != nr["layer"]:
                continue
            gd = geom_dist(or_, nr)
            if gd == math.inf:
                continue
            score = pos_dist(or_, nr) + 2.0 * gd
            if score < best_score:
                best_score = score; best = i
        if best is not None and best_score < POS_TOL * 2:
            pairs.append((best, j)); matched_old.add(best); matched_new.add(j)

    # If a uniquely identifiable entity moved farther than the spatial search
    # radius, retain its identity so it is reported as MOVED instead of as an
    # unrelated deletion plus addition.
    old_left = defaultdict(list)
    new_left = defaultdict(list)
    for i, r in enumerate(raw_old):
        if i not in matched_old:
            old_left[(r["type"], r["layer"], r["geom"])].append(i)
    for j, r in enumerate(new):
        if j not in matched_new:
            new_left[(r["type"], r["layer"], r["geom"])].append(j)
    for key in old_left.keys() & new_left.keys():
        if len(old_left[key]) == 1 and len(new_left[key]) == 1:
            i, j = old_left[key][0], new_left[key][0]
            pairs.append((i, j))
            matched_old.add(i)
            matched_new.add(j)

    changes = []
    for i, j in pairs:
        or_ = raw_old[i]
        match_or, nr = match_old[i], new[j]
        pd_raw = pos_dist(or_, nr)
        gd = geom_dist(match_or, nr)
        movement = None
        if pd_raw >= POS_EPS:
            movement = {
                "dx": nr["center"][0] - or_["center"][0],
                "dy": nr["center"][1] - or_["center"][1],
            }
        if gd < GEOM_EPS and pd_raw < POS_EPS:
            continue  # 未变化
        elif gd < GEOM_EPS and pd_raw >= POS_EPS:
            kind = "MOVED"
        else:
            kind = "MODIFIED"
        change = {"kind": kind, "bbox": nr["bbox"], "layer": nr["layer"],
                  "type": or_["type"], "old": or_["summary"],
                  "new": nr["summary"], "shape": rec_shape(nr)}
        if movement is not None:
            change["movement"] = movement
        changes.append(change)

    for i, r in enumerate(raw_old):
        if i not in matched_old:
            changes.append({"kind": "DELETED", "bbox": r["bbox"], "layer": r["layer"],
                            "type": r["type"], "old": r["summary"],
                            "shape": rec_shape(r)})
    for j, r in enumerate(new):
        if j not in matched_new:
            changes.append({"kind": "ADDED", "bbox": r["bbox"], "layer": r["layer"],
                            "type": r["type"], "new": r["summary"],
                            "shape": rec_shape(r)})
    return changes


def summarize(changes) -> dict:
    cnt = defaultdict(int)
    for c in changes:
        cnt[c["kind"]] += 1
    return cnt
