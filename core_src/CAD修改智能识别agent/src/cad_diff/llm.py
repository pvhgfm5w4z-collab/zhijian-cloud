#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
"""
语义摘要层（占 20% 的理解与表达）

- rule_summary：无依赖的规则式中文摘要（默认，离线可用）。
  按 变更类型 / 图层 / 关键实体 聚合，输出人读段落 + 要点。
- llm_summary：若配置 provider=openai 且提供 api_key，则调用 OpenAI 兼容接口，
  把结构化变更清单喂给模型生成更自然的工程变更说明；任何失败自动回退 rule。

若无 API key，默认走 rule，保证"拿得到一份可用的中文说明"。
"""
from __future__ import annotations
import json
from collections import defaultdict


def _coord(b):
    return f"x∈[{b[0]:.0f},{b[2]:.0f}] y∈[{b[1]:.0f},{b[3]:.0f}]"


# ── 喂给 LLM 的清单瘦身 ────────────────────────────────────────────────
# 实测：不瘦身时 1000 处变更的 payload 约 13 万 token，直接撑爆
# gpt-4o-mini 的 128k 上下文；3000 处约 40 万 token，必然失败。
# 三招：剔除渲染字段、坐标降精度、超出上限的只给统计概览。
MAX_ITEMS_DEFAULT = 200
CHAR_BUDGET_DEFAULT = 60000


def slim_changes(changes, max_items=None, char_budget=CHAR_BUDGET_DEFAULT):
    """把变更清单压到可安全送入模型的规模。"""
    max_items = max_items if max_items is not None else MAX_ITEMS_DEFAULT
    kept = []
    for c in changes:
        d = {k: v for k, v in c.items()
             if k in ("kind", "type", "layer", "old", "new")}
        b = c.get("bbox")
        if b:
            # 坐标保留 1 位小数即可；原始值是 16 位浮点，纯属浪费 token
            d["bbox"] = [round(float(x), 1) for x in b]
        kept.append(d)
        if len(kept) >= max_items:
            break

    omitted = len(changes) - len(kept)
    if omitted > 0:
        from collections import Counter
        kinds = Counter(c["kind"] for c in changes[max_items:])
        types = Counter(c["type"] for c in changes[max_items:])
        kept.append({
            "_note": f"以下还有 {omitted} 处未逐条列出",
            "_by_kind": dict(kinds),
            "_by_type": dict(types.most_common(8)),
        })

    s = json.dumps(kept, ensure_ascii=False, indent=2)
    # 兜底：仍超预算就整段截断（宁可少给，也不能把请求打爆）
    if len(s) > char_budget:
        s = s[:char_budget] + "\n…(已截断)"
    return s


def rule_summary(changes, cfg=None) -> str:
    cnt = defaultdict(int)
    for c in changes:
        cnt[c["kind"]] += 1
    lines = []
    # 只报非零的类别——实测 4 类全列出来时，"新增 0 处、修改 0 处、移动 0 处"
    # 占了大半行却毫无信息量。
    parts = [f"{name} {cnt[k]} 处"
             for k, name in (("ADDED", "新增"), ("DELETED", "删除"),
                             ("MODIFIED", "修改"), ("MOVED", "移动")) if cnt[k]]
    lines.append("本次版本共检出变更 %d 处：%s。" % (len(changes), "、".join(parts))
                 if parts else "本次版本未检出变更，两版图纸内容一致。")

    # 删除：按图层聚合
    del_by_layer = defaultdict(int)
    for c in changes:
        if c["kind"] == "DELETED":
            del_by_layer[c["layer"]] += 1
    if del_by_layer:
        top = sorted(del_by_layer.items(), key=lambda kv: -kv[1])[:5]
        total_del = sum(del_by_layer.values())
        # 只有一处时说"主要集中"很滑稽；多图层才有归纳价值
        if total_del == 1:
            lines.append(f"删除了 1 处，位于图层：{top[0][0]}。")
        elif len(del_by_layer) == 1:
            lines.append(f"删除的 {total_del} 处全部位于图层：{top[0][0]}。")
        else:
            lines.append("删除主要集中在图层：" +
                         "、".join(f"{l}({n})" for l, n in top) + "。")

    # 新增：是否含尺寸标注/块
    added = [c for c in changes if c["kind"] == "ADDED"]
    if added:
        new_dims = [c for c in added if c["type"] == "DIMENSION"]
        new_blocks = [c for c in added if c["type"] == "INSERT"]
        if new_dims:
            lines.append(f"新增了 {len(new_dims)} 处尺寸标注，建议核对标注数值与工艺要求。")
        if new_blocks:
            names = {c["new"] or c["type"] for c in new_blocks}
            lines.append(f"新增了 {len(new_blocks)} 个块参照，如：{'; '.join(list(names)[:3])}。")

    # 移动：是否整体位移较大
    moved = [c for c in changes if c["kind"] == "MOVED"]
    if moved:
        n = len(moved)
        # "多为图元重排或整体平移"是猜测，只在数量多时才说得通
        tail = "，多为图元重排或整体平移。" if n >= 3 else "。"
        lines.append(f"有 {n} 处发生位置移动{tail}")

    # 关键明细（前若干条）
    if not changes:
        return "\n".join(lines)
    lines.append("")
    lines.append("【关键明细】")
    shown = 0
    for c in changes:
        if shown >= 12:
            lines.append(f"  …其余 {len(changes) - shown} 处详见 JSON / DXF / PNG。")
            break
        if c["kind"] == "DELETED":
            detail = c.get("old", "-")
        elif c["kind"] == "ADDED":
            detail = c.get("new", "-")
        else:
            detail = f"{c.get('old','-')} → {c.get('new','-')}"
        lines.append(f"  · [{c['kind']}] 图层={c['layer']} 类型={c['type']} "
                     f"位置={_coord(c['bbox'])}")
        lines.append(f"      {detail}")
        shown += 1
    return "\n".join(lines)


SYSTEM_PROMPT = (
    "你是一名资深 CAD 制图审核工程师。下面是一张图纸新旧两版的自动比对结果"
    "(结构化变更清单，含 kind/type/layer/old/new/位置坐标)。请用简洁专业的中文，"
    "为设计/施工/审图人员写一段变更说明：先一句话总览，再按'删除/新增/修改/移动'"
    "分类给要点，并提示需要人工重点核对的地方。不要编造清单里没有的内容。")


def _call_openai(changes, cfg):
    import urllib.request
    L = cfg["llm"]
    url = L["base_url"].rstrip("/") + "/chat/completions"
    payload = {
        "model": L["model"],
        "temperature": L.get("temperature", 0.2),
        "max_tokens": L.get("max_tokens", 1200),
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "变更清单(JSON):\n" + slim_changes(
                changes,
                max_items=L.get("max_items"),
                char_budget=L.get("char_budget", CHAR_BUDGET_DEFAULT))},
        ],
    }
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json",
                  "Authorization": "Bearer " + (L.get("api_key") or "")})
    # 超时默认 20s：摘要只是锦上添花，不该让用户干等。
    # 实测 40s 时失败要等满 41 秒才回退，批量任务下不可接受。
    with urllib.request.urlopen(req, timeout=L.get("timeout", 20)) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"].strip()


def llm_summary(changes, cfg) -> dict:
    """返回 {text, source}。source ∈ {rule, llm, llm_fallback}。"""
    provider = cfg.get("llm", {}).get("provider", "rule")
    # 没有变更就别调模型了：既省钱，也避免模型对着空清单凭空生成内容。
    # 实测 0 变更时仍会发请求，mock 甚至返回了"检出 10 处"这种与事实矛盾的幻觉。
    if not changes:
        return {"text": rule_summary(changes, cfg), "source": "rule"}
    if provider == "openai" and cfg["llm"].get("api_key"):
        try:
            text = _call_openai(changes, cfg)
            return {"text": text, "source": "llm"}
        except Exception as ex:
            print(f"[warn] LLM 调用失败，回退规则摘要: {ex}")
            return {"text": rule_summary(changes, cfg), "source": "llm_fallback"}
    return {"text": rule_summary(changes, cfg), "source": "rule"}
