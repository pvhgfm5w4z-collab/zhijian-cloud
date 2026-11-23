#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
"""生成合成的“方块”旧/新 DXF，用于验证 MVP。
场景：3x3 方块阵列（WALLS 图层）
  旧版：9 个方块
  新版：删除中心块、新增1块、移动1块(+5,+5)、修改1块(20->28)、新增1圆(柱)
        且整体平移 (+3,+3) 以验证全局配准
"""
import ezdxf

S = 20  # 方块边长
G = 40  # 网格间距


def square(x, y, size=S):
    return [(x, y), (x+size, y), (x+size, y+size), (x, y+size)]


def build(path, is_new):
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    tx, ty = (3, 3) if is_new else (0, 0)
    # 3x3 阵列
    for i in range(3):
        for j in range(3):
            x, y = i*G + tx, j*G + ty
            # 新版：挖掉中心块
            if is_new and (i, j) == (1, 1):
                continue
            # 新版：移动左下角块 (+5,+5)
            if is_new and (i, j) == (0, 0):
                x, y = x+5, y+5
            # 新版：修改右上角块尺寸 20->28
            if is_new and (i, j) == (2, 2):
                msp.add_lwpolyline(square(x, y, 28), close=True,
                                   dxfattribs={"layer": "WALLS"})
                continue
            msp.add_lwpolyline(square(x, y, S), close=True,
                               dxfattribs={"layer": "WALLS"})
    if is_new:
        # 新增一块（右下空白区）
        msp.add_lwpolyline(square(3*G+tx, 0+ty, S), close=True,
                           dxfattribs={"layer": "WALLS"})
        # 新增一个圆（柱）
        msp.add_circle((tx+60, ty+60), 8, dxfattribs={"layer": "COLUMN"})
        # 一段文字标注
        msp.add_text("变更说明", dxfattribs={"layer": "NOTE", "height": 5}
                     ).set_placement((tx+0, ty+130))
    doc.saveas(path)
    print("已生成:", path)


if __name__ == "__main__":
    import os
    d = os.path.dirname(os.path.abspath(__file__))
    build(os.path.join(d, "old_block.dxf"), is_new=False)
    build(os.path.join(d, "new_block.dxf"), is_new=True)
