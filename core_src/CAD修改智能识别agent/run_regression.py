#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
"""
回归测试：把所有样例跑一遍，与期望的变更统计对比。

每个用例记录 (新增, 删除, 修改, 移动) 的期望值——这是改动引擎后
判断"有没有破坏原有行为"的唯一依据，尤其守住两条容易互相打架的规则：

  · 原地改内容（改尺寸值 / 改文字 / 改字高）→ 必须判 MODIFIED
  · 挪了位置的增删（远处删一处、别处加一处）→ 必须判 DELETED + ADDED

放宽配对阈值能让第一条通过，但很容易把第二条误配成"修改"；
收紧则相反。两个方向必须同时守住，所以两类用例都留在这里。

用法：
    python3.11 run_regression.py              # 全部用例（自动用 DWG 或 DXF）
    python3.11 run_regression.py --keep       # 保留 outputs/regression 产物
"""
from __future__ import annotations
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
OUT = os.path.join(ROOT, "outputs", "regression")

# (名称, 目录, 旧文件, 新文件, (期望新增, 期望删除, 期望修改, 期望移动))
# 注意：期望值来自当前引擎的实测输出，是"行为基线"，不是绝对真理。
# 如果你有意改变判定规则，请同步更新这里并说明原因。
CASES = [
    ("主动轴3(删除10处)",      "samples/active_shaft3", "old.dwg",          "new.dwg",          (0, 10, 0, 0)),
    ("主动轴2(删除10处)",      "samples/active_shaft2", "old.dwg",          "new.dwg",          (0, 10, 0, 0)),
    ("主动轴1(删除1处)",       "samples/active_shaft",  "old.dwg",          "new.dwg",          (0,  1, 0, 0)),
    ("暖通平面图(删除14处)",   "samples/hvac",          "old.dwg",          "new.dwg",          (0, 14, 0, 0)),
    ("实测图纸(新增2处)",      "samples/real",          "old.dwg",          "new.dwg",          (2,  0, 0, 0)),
    ("图块样例(混合)",         "samples/blocks",        "old_block.dxf",    "new_block.dxf",    (3,  1, 1, 1)),
    # ── 判定规则专项用例（见文件头的两条规则）────────────────────
    ("用例1 原位改内容应判修改", "samples/cases/case1_原位改内容与尺寸值",
                                                     "old.dxf",          "new.dxf",          (0,  0, 3, 0)),
    ("用例2 远距挪动应判增删",   "samples/cases/case2_远距挪动改字",
                                                     "old.dxf",          "new.dxf",          (1,  1, 0, 0)),
    ("用例3 仅改字高应判修改",   "samples/cases/case3_仅改字高",
                                                     "old.dxf",          "new.dxf",          (0,  0, 1, 0)),
]

LINE = re.compile(r"变更:\s*新增\s*(\d+)\s*\|\s*删除\s*(\d+)\s*\|\s*修改\s*(\d+)\s*\|\s*移动\s*(\d+)")


def run_one(name: str, d: str, old: str, new: str, prefix: str) -> tuple | None:
    op = os.path.join(ROOT, d, old)
    np_ = os.path.join(ROOT, d, new)
    for f in (op, np_):
        if not os.path.exists(f):
            print(f"  [跳过] {name}: 缺少 {f}")
            return None
    r = subprocess.run(
        [PY, os.path.join(ROOT, "cad_diff_cli.py"), op, np_, "-o", prefix],
        cwd=ROOT, capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": os.path.join(ROOT, "src")},
    )
    m = LINE.search(r.stdout + r.stderr)
    if not m:
        print(f"  [失败] {name}: 未解析到变更统计")
        print((r.stdout + r.stderr)[-500:])
        return None
    return tuple(int(x) for x in m.groups())


def main() -> int:
    shutil.rmtree(OUT, ignore_errors=True)
    os.makedirs(OUT, exist_ok=True)
    print("=" * 68)
    print("CAD 修改识别 · 回归测试")
    print("=" * 68)
    passed = failed = skipped = 0
    for name, d, old, new, want in CASES:
        got = run_one(name, d, old, new, os.path.join(OUT, d.replace("/", "__")))
        if got is None:
            skipped += 1
            continue
        if got == want:
            print(f"  [通过] {name:<28} 新增{got[0]} 删除{got[1]} 修改{got[2]} 移动{got[3]}")
            passed += 1
        else:
            print(f"  [不符] {name:<28} 期望{want}  实际{got}")
            failed += 1
    print("-" * 68)
    print(f"通过 {passed} | 不符 {failed} | 跳过 {skipped}")
    if "--keep" not in sys.argv:
        shutil.rmtree(OUT, ignore_errors=True)
        print(f"（产物已清理；加 --keep 可保留 outputs/regression）")
    print("=" * 68)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
