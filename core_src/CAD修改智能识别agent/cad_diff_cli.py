#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
"""
CAD 修改智能识别 —— 兼容入口（shim）

引擎已重构为 src/cad_diff/ 包。本文件仅做向后兼容：保持旧命令
    python3.11 cad_diff_cli.py <旧版.dxf> <新版.dxf> -o result
可用，内部转发到新引擎的 CLI。

新用法推荐（功能更全）：
    python3.11 -m cad_diff.cli <旧> <新> -o result -c configs/default.yaml
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "src"))

from cad_diff.cli import main  # noqa: E402

if __name__ == "__main__":
    main()
