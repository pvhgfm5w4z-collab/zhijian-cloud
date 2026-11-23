#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
"""
命令行入口

  单文件比对:
    python3.11 -m cad_diff.cli old.dxf new.dxf -o result -c configs/default.yaml

  批量比对(多对，CSV: old,new 每行一对):
    python3.11 -m cad_diff.cli --batch pairs.csv -o outputs

  目录模式(自动配对 *old* / *new*):
    python3.11 -m cad_diff.cli --dir ./samples -o outputs

可关闭某输出: --no-pdf --no-html --no-llm
"""
from __future__ import annotations
import os
import sys
import argparse
import csv

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from .config import load_config, DEFAULT_CONFIG
from . import pipeline


def _apply_cli_overrides(cfg, args):
    if args.no_pdf:
        cfg["output"]["pdf"] = False
    if args.no_html:
        cfg["output"]["html"] = False
    if args.no_llm:
        cfg["llm"]["provider"] = "rule"
    if args.raster:
        cfg["raster"]["enabled"] = True
    if args.registration:
        cfg["registration"]["method"] = args.registration
    if args.pos_tol is not None:
        cfg["tolerances"]["pos_tol"] = args.pos_tol
    if args.geom_eps is not None:
        cfg["tolerances"]["geom_eps"] = args.geom_eps
    return cfg


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="CAD 图纸修改智能识别（矢量 diff + 高亮 + 语义摘要）")
    ap.add_argument("old", nargs="?", help="旧版 DXF/DWG")
    ap.add_argument("new", nargs="?", help="新版 DXF/DWG")
    ap.add_argument("-o", "--out", default="result", help="输出前缀或目录")
    ap.add_argument("-c", "--config", help="YAML 配置文件路径")
    ap.add_argument("--batch", help="批量 CSV（每行 old,new）")
    ap.add_argument("--dir", help="目录模式：自动配对 *old*/*new*")
    ap.add_argument("--registration", choices=["translation", "rigid"])
    ap.add_argument("--pos-tol", type=float, help="空间候选半径")
    ap.add_argument("--geom-eps", type=float, help="几何修改阈值")
    ap.add_argument("--no-pdf", action="store_true")
    ap.add_argument("--no-html", action="store_true")
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--raster", action="store_true", help="额外输出栅格级像素 diff 图")
    ap.add_argument("--print-config", action="store_true", help="打印默认配置并退出")
    args = ap.parse_args(argv)

    if args.print_config:
        import json
        print(json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2))
        return

    cfg = load_config(args.config)
    cfg = _apply_cli_overrides(cfg, args)

    jobs = []
    if args.batch:
        with open(args.batch, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                # 支持逗号或空白分隔的 old,new
                parts = [p.strip() for p in line.replace(",", " ").split()]
                if len(parts) >= 2:
                    jobs.append((parts[0], parts[1]))
        out_dir = args.out
        os.makedirs(out_dir, exist_ok=True)
        for i, (o, n) in enumerate(jobs, 1):
            pre = os.path.join(out_dir, f"pair{i}")
            print(f"\n=== 批量 {i}/{len(jobs)}: {os.path.basename(o)} vs {os.path.basename(n)} ===")
            try:
                pipeline.run(o, n, pre, cfg)
            except Exception as ex:
                print(f"[error] 该对失败: {ex}")
        return
    if args.dir:
        pairs = pipeline.discover_pairs(args.dir)
        if not pairs:
            print("[error] 目录模式未找到 *old*/*new* 配对")
            return
        os.makedirs(args.out, exist_ok=True)
        for i, (o, n) in enumerate(pairs, 1):
            pre = os.path.join(args.out, f"pair{i}")
            print(f"\n=== 目录配对 {i}/{len(pairs)} ===")
            try:
                pipeline.run(o, n, pre, cfg)
            except Exception as ex:
                print(f"[error] 该对失败: {ex}")
        return
    if not (args.old and args.new):
        ap.print_help()
        return
    pipeline.run(args.old, args.new, args.out, cfg)
    print(f"\n输出前缀: {args.out}  (dxf/png/txt/json/pdf/html)")


if __name__ == "__main__":
    main()
