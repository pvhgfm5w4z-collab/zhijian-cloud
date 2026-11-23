#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
"""
CAD 图纸修改智能识别引擎（cad_diff 包）

分层：config(配置) / io_dxf(读写) / parser(解析) / align(配准)
     / diff(比对) / highlight(矢量高亮) / render(对比图) / raster(栅格兜底)
     / report(报告) / llm(语义摘要) / pipeline(编排) / cli(命令行)

设计原则：确定性矢量 diff(占 80% 精度) + 可选 LLM 语义层(占 20% 表达)。
"""
from .config import load_config, DEFAULT_CONFIG
from . import pipeline

__all__ = ["load_config", "DEFAULT_CONFIG", "pipeline"]
__version__ = "2.0.0"
