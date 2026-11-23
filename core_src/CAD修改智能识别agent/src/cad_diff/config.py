#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
"""
配置层（模型配置）

把所有"可调旋钮"集中到一处：阈值、配准方式、图层过滤、配色、输出格式、
LLM provider。命令行/配置文件/YAML 都能覆盖。使用者改这里就能改变引擎行为。
"""
from __future__ import annotations
import os
import copy
import yaml

# ------------------------- 默认配置 -------------------------
DEFAULT_CONFIG = {
    # 几何/位置容差（图纸单位；DWG 多为毫米）
    "tolerances": {
        "pos_tol": 20.0,    # 空间候选匹配的最大中心距离
        "pos_eps": 2.0,     # 超过此位移视为"移动"
        "geom_eps": 0.5,    # 几何差异阈值，超过视为"修改"
    },

    # 配准（默认关闭，避免把整张图的位移当作无变化）
    # method: "none" | "translation" | "rigid"
    #   none        -> 保留原始坐标；估计的平移只辅助寻找对应实体，移动仍计入差异
    #   translation -> 平移配准；用于用户明确需要忽略坐标原点差异的场景
    #   rigid       -> 平移+旋转配准；用于用户明确需要忽略整体坐标变换的场景
    "registration": {
        "method": "none",
        "min_pairs": 8,      # 估算变换所需的最少同源实体对
    },

    # 图层过滤：把 diff 限定在关注的图层，提升精度与性能
    # mode: "all" | "only" | "except"
    #   all   -> 比所有图层
    #   only  -> 只比 focus 列表里的图层
    #   except-> 排除 focus 列表里的图层
    "layers": {
        "mode": "all",
        "focus": [],         # 例如 ["WALLS", "EQ-*"]；支持 * 通配
    },

    # 块（INSERT）处理
    "blocks": {
        "expand": True,      # True=递归展开块内实体参与 diff；False=只比对块引用(位置+块名)
    },

    # 实体类型开关：False 的类型直接跳过（不计入 diff）
    "entity_types": {
        "LINE": True, "CIRCLE": True, "ARC": True,
        "LWPOLYLINE": True, "POLYLINE": True, "TEXT": True, "MTEXT": True,
        "INSERT": True, "DIMENSION": True, "MULTILEADER": True, "LEADER": True,
        "HATCH": True, "SPLINE": False, "SOLID": False, "POINT": False,
        "PROXY_ENTITY": False,
    },

    # 配色：kind -> (DXF 颜色号, PNG 十六进制)
    "colors": {
        "ADDED":   [3, "green"],    # 新增 绿
        "DELETED": [1, "red"],      # 删除 红
        "MODIFIED":[2, "orange"],   # 修改 橙
        "MOVED":   [6, "magenta"],  # 移动 品红
    },

    # 输出：默认全部开启；可单独关
    "output": {
        "dxf": True, "png": True, "zoom": True, "docx": True, "txt": True,
        "json": True, "pdf": True, "html": True, "zip": True, "raster": False,
        "dpi": 130, "zoom_dpi": 150,
    },

    # 栅格级兜底（矢量解析失败或作为补充视图时启用）
    "raster": {
        "enabled": False,           # True=额外渲染像素级 diff
        "min_area_ratio": 0.0008,   # 差异连通域占图幅比例低于此值则忽略（噪声过滤）
        "kernel": 3,                # 形态学闭运算核大小
    },

    # LLM 语义摘要层（占 20% 的理解与表达）
    # provider: "rule" | "openai"
    #   rule   -> 无依赖的规则式中文摘要（默认，离线可用）
    #   openai -> 调 OpenAI 兼容接口；缺 key 或失败自动回退 rule
    "llm": {
        "provider": "rule",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "api_key": "",              # 优先读环境变量 CAD_LLM_API_KEY
        "temperature": 0.2,
        "max_tokens": 1200,
        "language": "zh",
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path: str | None = None) -> dict:
    """加载配置：先取默认，再用 YAML 文件覆盖。path 为 None 时回退读仓库默认配置。"""
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    if path is None:
        # 回退到与包同级的默认配置文件（configs/default.yaml）
        _here = os.path.dirname(os.path.abspath(__file__))
        _default_yaml = os.path.join(_here, "..", "..", "configs", "default.yaml")
        if os.path.exists(_default_yaml):
            path = _default_yaml
    if path and os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            user = yaml.safe_load(f) or {}
        cfg = _deep_merge(cfg, user)
    # LLM key 允许来自环境变量
    env_key = os.environ.get("CAD_LLM_API_KEY")
    if env_key:
        cfg["llm"]["api_key"] = env_key
    return cfg


def layer_matches(pattern: str, name: str) -> bool:
    """通配匹配：'EQ-*' 命中 'EQ-水地源热泵'。"""
    if "*" not in pattern:
        return pattern == name
    import fnmatch
    return fnmatch.fnmatch(name, pattern)


def is_layer_focused(cfg: dict, layer: str) -> bool:
    """依据 layers.mode 判定某图层是否参与 diff。"""
    mode = cfg["layers"]["mode"]
    focus = cfg["layers"]["focus"] or []
    if mode == "all":
        return True
    if mode == "only":
        return any(layer_matches(p, layer) for p in focus)
    if mode == "except":
        return not any(layer_matches(p, layer) for p in focus)
    return True


def type_enabled(cfg: dict, dxftype: str) -> bool:
    return bool(cfg["entity_types"].get(dxftype, False))


if __name__ == "__main__":
    import json
    print(json.dumps(load_config(), ensure_ascii=False, indent=2))
