#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Agent 核心：标准工作流程（SOP）+ 双模式规划器 + 版本管理 + 归档 + 台账自主管理

SOP（Agent 工作流程，两种规划器执行同一套）：
  1. plan              解析目标，制定工作计划
  2. version_register  图纸版本登记（图号 + 版本演进）
  3. compare           调用比对引擎
  4. report            生成差异报告（markdown）
  5. archive           产物结构化归档
  6. ledger_update     台账自主管理：项目信息 × 变更清单 → 台账条目
  7. review            自主检查：风险分级、数据质量、待办生成
  8. finish            总结输出

规划器：
  - LLMPlanner  ：OpenAI 兼容 function calling（demo/llm_config.json 配置后启用）
  - RulePlanner ：无 key 时的内置规划器，严格按 SOP 顺序执行，保证演示可跑
"""
from __future__ import annotations
import os
import re
import json
import shutil
import datetime
import hashlib

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECTS_DIR = os.path.join(BASE_DIR, "projects")
os.makedirs(PROJECTS_DIR, exist_ok=True)

LLM_CONFIG_PATH = os.path.join(BASE_DIR, "llm_config.json")

SOP_STEPS = [
    ("plan", "解析目标，制定工作计划"),
    ("version_register", "登记图纸版本，建立版本演进链"),
    ("compare", "调用比对引擎，识别新旧版差异"),
    ("report", "生成结构化差异报告"),
    ("archive", "归档本次任务全部产物"),
    ("ledger_update", "综合项目信息与变更清单，自主更新变更台账"),
    ("review", "自主检查：风险分级与待办生成"),
    ("finish", "汇总输出"),
]

RISK_KEYWORDS_HIGH = ["梁", "柱", "板", "墙", "基础", "桩", "预应力", "钢构", "承重"]
RISK_KEYWORDS_MID = ["DIMENSION", "HATCH", "LWPOLYLINE", "POLYLINE", "INSERT", "CIRCLE", "ARC"]


# ── 项目 / 版本 / 台账 存储 ─────────────────────────────────────────────
def _proj_dir(project: str) -> str:
    safe = re.sub(r"[^\w一-龥-]", "_", project)
    d = os.path.join(PROJECTS_DIR, safe)
    for sub in ("drawings", "reports", "archive"):
        os.makedirs(os.path.join(d, sub), exist_ok=True)
    return d


def _load_json(path, default):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default


def _save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def create_project(name: str, stage: str = "施工阶段", contract: str = "固定单价合同", note: str = ""):
    d = _proj_dir(name)
    meta = _load_json(os.path.join(d, "project.json"), {})
    meta.update({
        "name": name, "stage": stage, "contract": contract, "note": note,
        "created": meta.get("created") or datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })
    _save_json(os.path.join(d, "project.json"), meta)
    if not os.path.exists(os.path.join(d, "ledger.json")):
        _save_json(os.path.join(d, "ledger.json"), {"items": [], "seq": 0})
    return meta


def get_project(name: str):
    d = _proj_dir(name)
    return _load_json(os.path.join(d, "project.json"), None)


def list_projects():
    out = []
    for p in sorted(os.listdir(PROJECTS_DIR)):
        meta = _load_json(os.path.join(PROJECTS_DIR, p, "project.json"), None)
        if meta:
            ledger = _load_json(os.path.join(PROJECTS_DIR, p, "ledger.json"), {"items": []})
            meta["ledger_count"] = len(ledger["items"])
            out.append(meta)
    return out


def _version_group_key(drawing_no: str, sample_key: str = "") -> str:
    return f"{drawing_no}::{sample_key}" if sample_key else drawing_no


def _file_hash(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalize_version_statuses(reg: dict) -> bool:
    changed = False
    for group in reg.get("drawings", {}).values():
        versions = group.get("versions", [])
        ids = [v.get("version") for v in versions]
        field_version = group.get("field_version")
        if field_version not in ids:
            field_version = next((v.get("version") for v in versions if v.get("status") == "现场使用"), None)
        if field_version not in ids and versions:
            field_version = versions[0].get("version")
        if group.get("field_version") != field_version:
            group["field_version"] = field_version
            changed = True
        for index, version in enumerate(versions):
            if version.get("status"):
                continue
            if version.get("version") == field_version:
                version["status"] = "现场使用"
            elif index == len(versions) - 1:
                version["status"] = "已收到"
            else:
                version["status"] = "已被新版替代"
            changed = True
    return changed


def register_version(project: str, drawing_no: str, file_path: str, label: str, source: str = "Agent登记",
                     sample_key: str = "", sample_label: str = "", status: str = ""):
    """按图号和样例来源分别登记；同一文件重复运行时复用已登记版本。"""
    d = _proj_dir(project)
    reg_path = os.path.join(d, "versions.json")
    reg = _load_json(reg_path, {"drawings": {}})
    statuses_changed = _normalize_version_statuses(reg)
    group_key = _version_group_key(drawing_no, sample_key)
    g = reg["drawings"].setdefault(group_key, {
        "drawing_no": drawing_no, "sample_key": sample_key,
        "sample_label": sample_label, "versions": [],
    })
    file_hash = _file_hash(file_path)
    for existing in g["versions"]:
        if existing.get("content_hash") == file_hash:
            if statuses_changed:
                _save_json(reg_path, reg)
            return {"drawing_no": drawing_no, "sample_key": sample_key,
                    "version": existing["version"], "file": existing["file"],
                    "total_versions": len(g["versions"]),
                    "chain": [v["version"] for v in g["versions"]], "reused": True}
    n = len(g["versions"]) + 1
    ext = os.path.splitext(file_path)[1].lower()
    file_stem = re.sub(r"[^\w一-龥-]", "_", f"{drawing_no}_{sample_key}" if sample_key else drawing_no)
    dst = os.path.join(d, "drawings", f"{file_stem}_v{n}{ext}")
    shutil.copyfile(file_path, dst)
    ver = {
        "version": f"v{n}", "label": label or f"第{n}版", "file": os.path.basename(dst),
        "registered": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "source": source,
        "content_hash": file_hash, "status": status or ("现场使用" if n == 1 else "已收到"),
    }
    g["versions"].append(ver)
    if n == 1:
        g["field_version"] = ver["version"]
    _save_json(reg_path, reg)
    return {"drawing_no": drawing_no, "sample_key": sample_key, "version": ver["version"], "file": ver["file"],
            "total_versions": n, "chain": [v["version"] for v in g["versions"]], "reused": False}


def list_versions(project: str):
    d = _proj_dir(project)
    path = os.path.join(d, "versions.json")
    reg = _load_json(path, {"drawings": {}})
    if _normalize_version_statuses(reg):
        _save_json(path, reg)
    return reg


def set_version_status(project: str, drawing_no: str, sample_key: str, version: str, status: str):
    allowed = {"已收到", "已批准", "现场使用", "已被新版替代"}
    if status not in allowed:
        return False
    d = _proj_dir(project)
    path = os.path.join(d, "versions.json")
    reg = _load_json(path, {"drawings": {}})
    _normalize_version_statuses(reg)
    group = reg["drawings"].get(_version_group_key(drawing_no, sample_key))
    if not group:
        return False
    target = next((v for v in group.get("versions", []) if v.get("version") == version), None)
    if not target:
        return False
    if status == "已被新版替代":
        return False
    if status == "已批准" and target.get("status") not in {"已收到", "已批准"}:
        return False
    if status == "已收到" and target.get("status") != "已收到":
        return False
    if status == "现场使用" and target.get("status") not in {"已批准", "现场使用", "已被新版替代"}:
        return False
    if status == "现场使用":
        current = group.get("field_version")
        for entry in group.get("versions", []):
            if entry.get("version") == current and current != version:
                entry["status"] = "已被新版替代"
        group["field_version"] = version
    target["status"] = status
    _save_json(path, reg)
    return True


def get_version_file(project: str, drawing_no: str, version: str, sample_key: str = ""):
    d = _proj_dir(project)
    reg = _load_json(os.path.join(d, "versions.json"), {"drawings": {}})
    for v in reg["drawings"].get(_version_group_key(drawing_no, sample_key), {}).get("versions", []):
        if v["version"] == version:
            return os.path.join(d, "drawings", v["file"])
    return None


# ── 归档 ────────────────────────────────────────────────────────────────
def archive_job(project: str, job_id: str, job_dir: str, drawing_no: str, old_ver: str, new_ver: str,
                sample_key: str = "", sample_label: str = ""):
    """把一次比对任务的产物结构化归档到项目下，并写归档索引。"""
    d = _proj_dir(project)
    arc_dir = os.path.join(d, "archive", job_id)
    source_hashes = {
        role: _file_hash(os.path.join(job_dir, f"{role}.dxf"))
        for role in ("old", "new")
    }
    source_fingerprint = hashlib.sha256(json.dumps(
        [drawing_no, sample_key, old_ver, new_ver, source_hashes], ensure_ascii=False, sort_keys=True
    ).encode("utf-8")).hexdigest()
    idx_path = os.path.join(d, "archive", "index.json")
    idx = _load_json(idx_path, {"archives": []})
    existing = next((a for a in idx["archives"]
                     if a.get("source_fingerprint") == source_fingerprint), None)
    archived_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if existing:
        runs = existing.setdefault("runs", [{"job_id": existing["job_id"], "archived": existing.get("archived", "")}])
        if not any(run.get("job_id") == job_id for run in runs):
            runs.append({"job_id": job_id, "archived": archived_at})
        existing["run_count"] = len(runs)
        existing["last_run"] = archived_at
        _save_json(idx_path, idx)
        return {"archive_dir": existing["dir"], "files": existing["files"],
                "total_archives": len(idx["archives"]), "reused": True,
                "run_count": existing["run_count"], "archive_job_id": existing["job_id"]}

    os.makedirs(arc_dir, exist_ok=True)
    kept = []
    for f in os.listdir(job_dir):
        if f.startswith(("result", "工程签证单")):
            shutil.copyfile(os.path.join(job_dir, f), os.path.join(arc_dir, f))
            kept.append(f)
        elif f in ("old.dxf", "new.dxf"):
            version = old_ver if f == "old.dxf" else new_ver
            source_name = f"{drawing_no}_{sample_key or 'default'}_{version}.dxf"
            shutil.copyfile(os.path.join(job_dir, f), os.path.join(arc_dir, source_name))
            kept.append(source_name)
    entry = {
        "job_id": job_id, "drawing_no": drawing_no,
        "sample_key": sample_key, "sample_label": sample_label,
        "source_hashes": source_hashes, "source_fingerprint": source_fingerprint,
        "from_version": old_ver, "to_version": new_ver,
        "archived": archived_at, "last_run": archived_at,
        "runs": [{"job_id": job_id, "archived": archived_at}], "run_count": 1,
        "files": kept, "dir": arc_dir,
    }
    idx["archives"] = [a for a in idx["archives"] if a.get("job_id") != job_id]
    idx["archives"].append(entry)
    _save_json(idx_path, idx)
    return {"archive_dir": arc_dir, "files": kept, "total_archives": len(idx["archives"]),
            "reused": False, "run_count": 1, "archive_job_id": job_id}


def list_archive(project: str):
    d = _proj_dir(project)
    return _load_json(os.path.join(d, "archive", "index.json"), {"archives": []})


# ── 台账自主管理 ─────────────────────────────────────────────────────────
def _classify(change: dict) -> str:
    t, layer = change.get("type", ""), change.get("layer", "")
    text = f"{t} {layer} {change.get('old', '')} {change.get('new', '')}"
    if any(k in text for k in RISK_KEYWORDS_HIGH):
        return "结构类变更"
    if t == "DIMENSION" or "标注" in text:
        return "标注/尺寸类变更"
    if t in ("HATCH", "LWPOLYLINE", "POLYLINE"):
        return "几何/区域类变更"
    if t in ("TEXT", "MTEXT"):
        return "文字说明类变更"
    return "其他变更"


def _risk_level(change: dict, estimate: dict) -> str:
    return _quality_safety_assessment(change)[0]


def _quality_safety_assessment(change: dict) -> tuple[str, list[str]]:
    """质量安全检查分级不使用金额，仅依据变更对象、实质变化和可核验性。"""
    kind, etype = change.get("kind", ""), change.get("type", "")
    text = f"{change.get('old', '')} {change.get('new', '')} {change.get('layer', '')}"
    structural = _classify(change) == "结构类变更" or any(k in text for k in RISK_KEYWORDS_HIGH)
    if structural:
        return "高", ["涉及结构/承重构件", "核对设计变更通知及审批手续", "复核现场实体、支撑和临边防护"]
    if etype in RISK_KEYWORDS_MID or kind in ("ADDED", "DELETED"):
        checks = ["涉及实体增删、几何范围或尺寸变化"]
        if etype == "DIMENSION" or kind == "MODIFIED":
            checks.append("复核尺寸、标高、轴线及施工放样")
        if kind in ("ADDED", "DELETED"):
            checks.append("现场核对施工范围、成品质量和安全措施")
        return "中", checks
    return "低", ["未识别到结构或实体安全影响", "核对图纸标注与现场记录一致"]


def _make_todos(change: dict, estimate: dict, project_meta: dict) -> list:
    todos = []
    kind, etype = change["kind"], change.get("type", "")
    if kind == "MODIFIED" and etype == "DIMENSION":
        todos.append("复核该尺寸对应构件的几何工程量并更新算量")
    if kind in ("ADDED", "DELETED") and etype in ("HATCH", "LWPOLYLINE", "POLYLINE"):
        todos.append("现场确认实际施工范围与图纸变更范围一致")
    if _classify(change) == "结构类变更":
        todos.append("结构类变更：须取得设计院正式变更通知单并履行审批流程")
    # 仅对需要另行组价/复核的条目保留触发项；合同层面的说明由签证单汇总展示一次。
    if project_meta.get("contract", "").startswith("固定单价") and (
        estimate.get("price") in (None, "", "-") or estimate.get("amount") is None
    ):
        todos.append("固定单价合同：核对变更是否属于清单缺项，缺项部分需另行组价确认")
    if not todos:
        todos.append("按一单一算原则编制本变更结算单并纳入台账跟踪")
    return todos


def update_ledger(project: str, job_id: str, drawing_no: str, old_ver: str, new_ver: str,
                  changes: list, estimates: list, project_meta: dict,
                  sample_key: str = "", sample_label: str = ""):
    """台账自主管理：把一批变更写入台账，自动编号、归类、风险分级、生成待办。"""
    d = _proj_dir(project)
    path = os.path.join(d, "ledger.json")
    ledger = _load_json(path, {"items": [], "seq": 0})
    est_by_idx = {e["idx"]: e for e in estimates}
    new_items = []
    existing_keys = {item.get("change_key") for item in ledger["items"] if item.get("change_key")}
    existing_keys.update(item['change_key'] for item in ledger.get('deleted_items', []) if item.get('change_key'))
    occurrences = {}
    for c in changes:
        est = est_by_idx.get(c["idx"], {"delta": "-", "unit": "-", "price": "-", "amount": 0.0, "note": ""})
        content = est.get("content") or (c.get("new") or c.get("old") or "")[:60]
        semantic = (c.get("kind", ""), c.get("type", ""), c.get("layer", ""), content)
        occurrence = occurrences.get(semantic, 0)
        occurrences[semantic] = occurrence + 1
        change_payload = {"kind": semantic[0], "type": semantic[1], "layer": semantic[2],
                          "content": semantic[3], "occurrence": occurrence}
        change_key = hashlib.sha256(json.dumps(
            [drawing_no, sample_key, old_ver, new_ver, change_payload], ensure_ascii=False, sort_keys=True
        ).encode("utf-8")).hexdigest()
        if change_key in existing_keys:
            continue
        ledger["seq"] += 1
        item = {
            "id": f"CL-{ledger['seq']:04d}",
            "created": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "source": {"job_id": job_id, "drawing_no": drawing_no,
                       "sample_key": sample_key, "sample_label": sample_label,
                       "from_version": old_ver, "to_version": new_ver},
            "change_key": change_key,
            "kind": c["kind"], "kind_cn": c["kind_cn"], "type": c["type"], "layer": c["layer"],
            "content": content,
            "category": _classify(c),
            "estimate": dict(est),
            "change": dict(c),
            "risk": _risk_level(c, est),
            "risk_type": "质量安全检查风险",
            "risk_checks": _quality_safety_assessment(c)[1],
            "status": "待确认",
            "todos": _make_todos(c, est, project_meta),
            "agent_note": "",
        }
        new_items.append(item)
        existing_keys.add(change_key)
    ledger["items"].extend(new_items)
    _save_json(path, ledger)
    return {"added": len(new_items), "skipped_duplicates": len(changes) - len(new_items),
            "total": len(ledger["items"]),
            "items": [i["id"] for i in new_items]}


def get_ledger(project: str, status: str = "", risk: str = ""):
    d = _proj_dir(project)
    ledger = _load_json(os.path.join(d, "ledger.json"), {"items": [], "seq": 0})
    items = ledger["items"]
    for item in items:
        if not item.get("risk_checks"):
            level, checks = _quality_safety_assessment({
                "kind": item.get("kind", ""), "type": item.get("type", ""),
                "layer": item.get("layer", ""), "old": item.get("content", ""),
                "new": item.get("content", "")})
            item["risk"], item["risk_type"], item["risk_checks"] = level, "质量安全检查风险", checks
    risk_rank = {"低": 1, "中": 2, "高": 3}
    status_order = ["待确认", "已确认", "已签证", "已索赔"]
    # 一张签证单对应一次 Agent 来源任务；同一来源的所有变更共用一条状态流转。
    groups = {}
    for item in items:
        source = item.get("source", {})
        key_parts = [source.get("job_id", ""), source.get("drawing_no", ""),
                     source.get("sample_key", ""), source.get("from_version", ""),
                     source.get("to_version", "")]
        visa_id = "VISA-" + hashlib.sha1("|".join(key_parts).encode("utf-8")).hexdigest()[:10].upper()
        item["visa_id"] = visa_id
        groups.setdefault(visa_id, []).append(item)

    meta = get_project(project) or {}
    contract_fixed = meta.get("contract", "").startswith("固定单价")
    summaries = []
    for visa_id, details in groups.items():
        statuses = [i.get("status", "待确认") for i in details]
        # 兼容历史逐条状态数据：汇总状态取最前阶段，展示仍保持整单语义。
        summary_status = min(statuses, key=lambda x: status_order.index(x) if x in status_order else 0)
        summary_risk = max((i.get("risk", "低") for i in details), key=lambda x: risk_rank.get(x, 0))
        source = details[0].get("source", {})
        fixed_triggers = [i.get("id") for i in details if any("固定单价合同" in t for t in i.get("todos", []))]
        summaries.append({
            "id": visa_id,
            "title": f"签证单 {visa_id}",
            "source": source,
            "status": summary_status,
            "risk": summary_risk,
            "amount": round(sum(i.get("estimate", {}).get("amount", 0) or 0 for i in details), 2),
            "detail_count": len(details),
            "fixed_unit_price_notice": contract_fixed,
            "fixed_unit_price_trigger_ids": fixed_triggers,
            "items": details,
        })
    if status:
        summaries = [s for s in summaries if s["status"] == status]
    if risk:
        summaries = [s for s in summaries if s["risk"] == risk]
    visible_items = [i for s in summaries for i in s["items"]]
    total_amount = sum(s["amount"] for s in summaries)
    stats = {
        "total": len(summaries),
        "detail_total": len(visible_items),
        "by_status": {},
        "by_risk": {},
        "amount_sum": round(total_amount, 2),
    }
    for s in summaries:
        stats["by_status"][s["status"]] = stats["by_status"].get(s["status"], 0) + 1
        stats["by_risk"][s["risk"]] = stats["by_risk"].get(s["risk"], 0) + 1
    fixed_notice = contract_fixed and bool(summaries)
    return {"items": visible_items, "summaries": summaries, "stats": stats,
            "contract": meta.get("contract", ""),
            "fixed_unit_price_notice": fixed_notice}


def set_ledger_status(project: str, item_id: str, status: str):
    transitions = {
        "待确认": {"待确认", "已确认"},
        "已确认": {"已确认", "已签证"},
        "已签证": {"已签证", "已索赔"},
        "已索赔": {"已索赔"},
    }
    d = _proj_dir(project)
    path = os.path.join(d, "ledger.json")
    ledger = _load_json(path, {"items": [], "seq": 0})
    target_items = []
    for i in ledger["items"]:
        source = i.get("source", {})
        key_parts = [source.get("job_id", ""), source.get("drawing_no", ""), source.get("sample_key", ""),
                     source.get("from_version", ""), source.get("to_version", "")]
        visa_id = "VISA-" + hashlib.sha1("|".join(key_parts).encode("utf-8")).hexdigest()[:10].upper()
        if i.get("id") == item_id or visa_id == item_id:
            target_items.append(i)
    if target_items:
        current = min((i.get("status", "待确认") for i in target_items),
                      key=lambda x: ["待确认", "已确认", "已签证", "已索赔"].index(x) if x in {"待确认", "已确认", "已签证", "已索赔"} else 0)
        if status not in transitions.get(current, set()):
            return False
        for i in target_items:
            i["status"] = status
        _save_json(path, ledger)
        return True
    return False


# ── 差异报告（markdown） ────────────────────────────────────────────────
def generate_report(project: str, job_id: str, drawing_no: str, old_ver: str, new_ver: str,
                    counts: dict, changes: list, job_dir: str, sample_label: str = ""):
    lines = [
        f"# 图纸差异比对报告",
        f"",
        f"- 项目：{project}",
        f"- 图纸：{drawing_no}（{old_ver} → {new_ver}）",
        f"- 来源样例：{sample_label}" if sample_label else "- 来源样例：历史任务未记录",
        f"- 任务：{job_id}",
        f"- 生成时间：{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 生成方式：Agent 自动工作流（引擎确定性矢量 diff）",
        f"",
        f"## 变更统计",
        f"",
        f"| 新增 | 删除 | 修改 | 移动 | 合计 |",
        f"|---|---|---|---|---|",
        f"| {counts.get('ADDED', 0)} | {counts.get('DELETED', 0)} | {counts.get('MODIFIED', 0)} | {counts.get('MOVED', 0)} | {sum(counts.values())} |",
        f"",
        f"## 变更明细",
        f"",
        f"| # | 类型 | 实体 | 图层 | 旧版 | 新版 |",
        f"|---|---|---|---|---|---|",
    ]
    kind_cn = {"ADDED": "新增", "DELETED": "删除", "MODIFIED": "修改", "MOVED": "移动"}
    for i, c in enumerate(changes, 1):
        old = (c.get("old") or "—")[:38]
        new = (c.get("new") or "—")[:38]
        lines.append(f"| {i} | {kind_cn.get(c['kind'], c['kind'])} | {c.get('type', '')} | {c.get('layer', '')} | {old} | {new} |")
    lines += ["", "## 附注", "",
              "本报告由 Agent 自动工作流生成，变更内容由确定性矢量比对引擎识别，",
              "所有条目可在台账中逐条追溯至图纸区域截图与版本记录。"]
    report = "\n".join(lines)
    d = _proj_dir(project)
    rp = os.path.join(d, "reports", f"diff_report_{job_id}.md")
    with open(rp, "w", encoding="utf-8") as f:
        f.write(report)
    return {"report_path": rp, "report_file": os.path.basename(rp), "lines": len(lines)}


# ── LLM 规划器 ──────────────────────────────────────────────────────────
def load_llm_config():
    cfg = _load_json(LLM_CONFIG_PATH, {})
    if cfg.get("api_key"):
        return cfg
    env_key = os.environ.get("CAD_LLM_API_KEY")
    if env_key:
        cfg["api_key"] = env_key
        cfg.setdefault("base_url", "https://api.deepseek.com")
        cfg.setdefault("model", "deepseek-chat")
        return cfg
    return None


AGENT_SYSTEM_PROMPT = """你是"图纸变更索赔 Agent"，负责按标准工作流程处理新到图纸：
以现场当前使用版本为旧版，登记新到图纸为新版 → 比对 → 生成差异报告 → 归档 → 更新变更台账 → 自主检查 → 总结。

纪律：
1. 金额、数量只能引用工具返回值，禁止自己推算或编造数字。
2. 每一步先说明意图再调用工具；工具失败要换参数重试或报告阻塞。
3. 台账风险按质量安全检查逻辑生成；金额、单价和量差只用于计价，不参与风险分级。
4. 不得因为存在更早版本就将其当作现场基准；任务上下文中的现场基准版本优先。
5. 全部步骤完成后调用 finish 工具，附一段面向商务人员的中文总结。"""


def run_agent_llm(goal: str, context: dict, tools_dispatch, emit):
    """OpenAI 兼容 function calling 规划器。tools_dispatch(name, args) -> dict"""
    from openai import OpenAI
    cfg = load_llm_config()
    client = OpenAI(api_key=cfg["api_key"], base_url=cfg.get("base_url", "https://api.deepseek.com"))
    model = cfg.get("model", "deepseek-chat")

    tool_schemas = [
        {"type": "function", "function": {"name": "version_register", "description": "登记图纸版本（旧版/新版都要登记），返回版本号",
         "parameters": {"type": "object", "properties": {"file_role": {"type": "string", "enum": ["old", "new"]}, "label": {"type": "string"}}, "required": ["file_role"]}}},
        {"type": "function", "function": {"name": "compare", "description": "对已登记的两个版本执行差异比对",
         "parameters": {"type": "object", "properties": {}, "required": []}}},
        {"type": "function", "function": {"name": "report", "description": "生成 markdown 差异报告",
         "parameters": {"type": "object", "properties": {}, "required": []}}},
        {"type": "function", "function": {"name": "archive", "description": "归档本次任务产物",
         "parameters": {"type": "object", "properties": {}, "required": []}}},
        {"type": "function", "function": {"name": "ledger_update", "description": "把全部变更写入变更台账（自动归类/风险分级/待办）",
         "parameters": {"type": "object", "properties": {}, "required": []}}},
        {"type": "function", "function": {"name": "review", "description": "自主检查：统计高质量安全检查风险条目、评估数据质量、输出检查结论",
         "parameters": {"type": "object", "properties": {}, "required": []}}},
        {"type": "function", "function": {"name": "finish", "description": "结束工作流并输出总结",
         "parameters": {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]}}},
    ]
    messages = [
        {"role": "system", "content": AGENT_SYSTEM_PROMPT},
        {"role": "user", "content": f"目标：{goal}\n\n任务上下文：{json.dumps(context, ensure_ascii=False)}"},
    ]
    for _round in range(12):
        resp = client.chat.completions.create(model=model, messages=messages, tools=tool_schemas)
        msg = resp.choices[0].message
        if not msg.tool_calls:
            messages.append({"role": "assistant", "content": msg.content or ""})
            continue
        messages.append(msg)
        for call in msg.tool_calls:
            name = call.function.name
            try:
                args = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            emit("step", {"tool": name, "args": args, "thought": (msg.content or "").strip()[:200], "planner": "llm"})
            try:
                result = tools_dispatch(name, args)
                ok = True
            except Exception as e:
                result, ok = {"error": str(e)}, False
            emit("result", {"tool": name, "ok": ok, "result": _brief(result)})
            messages.append({"role": "tool", "tool_call_id": call.id,
                             "content": json.dumps(_brief(result), ensure_ascii=False)})
            if name == "finish":
                return {"planner": "llm", "summary": args.get("summary", "")}
    return {"planner": "llm", "summary": "已达最大轮次，工作流中止"}


def _brief(result, n=800):
    s = json.dumps(result, ensure_ascii=False, default=str)
    return s[:n] if len(s) <= n else s[:n] + "…(截断)"


# ── 规则规划器（无 key 兜底，执行同一套 SOP） ─────────────────────────────
def run_agent_rule(goal: str, context: dict, tools_dispatch, emit):
    seq = [
        ("version_register", {"file_role": "old", "label": "底版"}),
        ("version_register", {"file_role": "new", "label": "新版（变更）"}),
        ("compare", {}),
        ("report", {}),
        ("archive", {}),
        ("ledger_update", {}),
        ("review", {}),
        ("finish", {}),
    ]
    for name, args in seq:
        emit("step", {"tool": name, "args": args, "planner": "rule",
                      "thought": dict(SOP_STEPS).get(name if name != "finish" else "finish", "")})
        result = tools_dispatch(name, args)
        emit("result", {"tool": name, "ok": "error" not in result, "result": _brief(result)})
        if "error" in result:
            return {"planner": "rule", "summary": f"步骤 {name} 失败：{result['error']}"}
    source_note = (f"（复用工具模式比对和计价结果，本次 {context.get('source_price_total', 0):.2f} 元）"
                   if context.get("source_reused") else "")
    summary = (f"工作流完成：{context.get('drawing_no')} 新版已登记、比对、归档{source_note}，"
               f"台账新增 {context.get('_ledger_added', '?')} 条，请人工复核高风险条目。")
    return {"planner": "rule", "summary": summary}
