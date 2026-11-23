#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
"""
IO 层：DWG→DXF 转换 + DXF 清洗修复 + 读取

- convert_dwg: dwg2dxf(LibreDWG) / ODAFileConverter 多工具回退
- clean_tmp:   修复 LibreDWG 导出 DXF 的格式瑕疵（XDATA 空行 + MATERIAL 表损坏）
- load_doc:    读取为 ezdxf 文档，并探测 PROXY_ENTITY（天正自定义对象）
"""
from __future__ import annotations
import os
import shutil
import subprocess
import ezdxf


def _run_dwg2dxf(exe: str, dwg_path: str, dxf_path: str) -> tuple[bool, str]:
    """libredwg：dwg2dxf -o out.dxf in.dwg"""
    try:
        r = subprocess.run([exe, "-o", dxf_path, dwg_path],
                           check=False, capture_output=True, timeout=600)
    except Exception as ex:
        return False, str(ex)
    if os.path.exists(dxf_path) and os.path.getsize(dxf_path) > 0:
        return True, ""
    err = (r.stderr or b"").decode("utf-8", "ignore").strip().splitlines()
    return False, (err[-1] if err else f"exit={r.returncode}")


def _has_x_server() -> bool:
    """是否真有 X 服务在跑。

    不能只看 DISPLAY 环境变量——服务器 / Docker / CI 里 DISPLAY 常残留为
    ":0"，但根本没有 X 服务，Qt 仍会在加载 xcb 插件时以退出码 134 崩溃。
    以 /tmp/.X11-unix 下是否存在 socket 为准。
    """
    try:
        return bool(os.listdir("/tmp/.X11-unix"))
    except OSError:
        return False


def _oda_cmd(exe: str, xvfb: bool = False) -> list:
    """ODA 是 Qt 程序，无显示器时加载不了 xcb 插件（退出码 134 Aborted）。
    无 X 服务时自动套一层 Xvfb 虚拟显示，让它在纯命令行服务器上也能跑。"""
    if xvfb:
        return ["xvfb-run", "-a", "--server-args=-screen 0 1024x768x24", exe]
    return [exe]


def _run_oda(exe: str, dwg_path: str, dxf_path: str) -> tuple[bool, str]:
    """ODA File Converter：目录→目录 转换，再取回同名 .dxf。

      ODAFileConverter <输入目录> <输出目录> <版本> <类型> <递归> <审计> [过滤]
      例：ODAFileConverter ./in ./out ACAD2018 DXF 0 1 xxx.dwg
    """
    import tempfile
    in_dir = os.path.dirname(os.path.abspath(dwg_path))
    base = os.path.splitext(os.path.basename(dwg_path))[0]
    out_dir = tempfile.mkdtemp(prefix="oda_out_")
    try:
        # 无 X 服务就直接上 Xvfb；若已有 X 服务但直连仍失败，再补一次 Xvfb 重试
        xvfb = not _has_x_server() and bool(shutil.which("xvfb-run"))
        attempts = [xvfb]
        if not xvfb and shutil.which("xvfb-run"):
            attempts.append(True)
        last = ""
        for use_xvfb in attempts:
            os.makedirs(out_dir, exist_ok=True)
            r = subprocess.run(_oda_cmd(exe, use_xvfb) +
                               [in_dir, out_dir, "ACAD2018", "DXF", "0", "1",
                                os.path.basename(dwg_path)],
                               check=False, capture_output=True, timeout=600)
            src = os.path.join(out_dir, base + ".dxf")
            if os.path.exists(src) and os.path.getsize(src) > 0:
                shutil.move(src, dxf_path)
                return True, ""
            err = (r.stderr or b"").decode("utf-8", "ignore").strip().splitlines()
            last = (err[-1] if err else f"exit={r.returncode}")
            shutil.rmtree(out_dir, ignore_errors=True)
        return False, last
    except Exception as ex:
        return False, str(ex)
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)


# 转换器：命令名 -> (缓存后缀, 执行函数)
CONVERTERS = {
    "dwg2dxf": ("_converted.dxf", _run_dwg2dxf),
    "ODAFileConverter": ("_oda.dxf", _run_oda),
}


def convert_dwg(dwg_path: str) -> str:
    """DWG 必须先转 DXF（二进制专有格式无法直接解析）。

    转换器优先级可用环境变量 CAD_DIFF_CONVERTER 指定：
      oda      -> 优先 ODA File Converter，失败回退 LibreDWG
      dwg2dxf  -> 只用 LibreDWG
      auto     -> 默认：先 LibreDWG，失败再回退 ODA

    关于选哪个：ODA 的兼容性强于 LibreDWG（高版本 DWG / 天正 / 代理实体），
    但**实测对普通 DWG 两者产出的实体构成完全一致**（278 实体，各类数量相同）。
    另外别指望靠换转换器拿到标注测量值——DXF 里只存 `<>` 占位符，真实值不落盘，
    两种转换器都一样；测量值由 parser 用 ezdxf get_measurement() 另行重算。

    健壮性设计：
    1) 幂等：已转换过且文件非空则直接复用（按转换器分别缓存，互不覆盖）。
    2) 容错：libredwg 常输出 ERROR/warning 且非零退出，但仍写出可用 dxf，
       因此只要产物非空即视为成功。
    """
    base = os.path.splitext(dwg_path)[0]
    prefer = os.environ.get("CAD_DIFF_CONVERTER", "auto").strip().lower()
    if prefer == "oda":
        order = ["ODAFileConverter", "dwg2dxf"]
    elif prefer == "dwg2dxf":
        order = ["dwg2dxf"]
    else:
        order = ["dwg2dxf", "ODAFileConverter"]

    tried = []
    for tool in order:
        suffix, runner = CONVERTERS[tool]
        dxf_path = base + suffix
        # 1) 幂等复用（按转换器分别缓存）
        if os.path.exists(dxf_path) and os.path.getsize(dxf_path) > 0:
            return dxf_path
        exe = shutil.which(tool)
        if not exe:
            tried.append(f"{tool}(未安装)")
            continue
        ok, msg = runner(exe, dwg_path, dxf_path)
        if ok:
            return dxf_path
        tried.append(f"{tool}({msg or '未生成 dxf'})")

    raise RuntimeError(
        "DWG 转换失败：" + "; ".join(tried) +
        "。请先转成 DXF，或安装 ODA File Converter / LibreDWG 后加入 PATH；"
        "若已装 ODA 且想优先使用，可设环境变量 CAD_DIFF_CONVERTER=oda。")


def clean_tmp(path: str) -> str:
    """LibreDWG 导出的 DXF 常带两类瑕疵，这里做清洗后存临时文件：
    1) 状态机删除"组码位置"的 bug 空行（天正 XDATA 常见，否则 ezdxf 报
       Invalid group code）。只删组码位的空行、保留值位的空行（全删会配对错位）。
    2) 移除缺少值的 HEADER 变量（例如某些版本导出的空 $CMATERIAL）。
    3) 移除损坏的 MATERIAL 表（否则 ezdxf 保存崩溃；按 ENDTAB 界定）。"""
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.read().split("\n")
    # 1) 状态机空行清洗
    fixed, expect_code = [], True
    for line in lines:
        if expect_code and line.strip() == "":
            continue
        fixed.append(line)
        expect_code = not expect_code
    # HEADER 变量以组码 9 开始；若下一组码立刻又是 9，说明该变量缺少值。
    # 删除该变量后再交给 ezdxf，避免 HeaderSection.load_tags 访问 group[1] 越界。
    i = 0
    in_header = False
    while i + 1 < len(fixed):
        code, value = fixed[i].strip(), fixed[i + 1].strip()
        if (code == "0" and value == "SECTION" and i + 3 < len(fixed)
                and fixed[i + 2].strip() == "2" and fixed[i + 3].strip() == "HEADER"):
            in_header = True
            i += 4
            continue
        if in_header and code == "0" and value == "ENDSEC":
            in_header = False
            i += 2
            continue
        if in_header and code == "9":
            next_pair = i + 2
            if next_pair + 1 >= len(fixed) or fixed[next_pair].strip() == "9":
                del fixed[i:i + 2]
                continue
        i += 2
    # 3) MATERIAL 表清洗
    out, i, n = [], 0, len(fixed)
    while i < n:
        if (fixed[i].strip() == "0" and i + 3 < n
                and fixed[i + 1].strip() == "TABLE"
                and fixed[i + 2].strip() == "2"
                and fixed[i + 3].strip() == "MATERIAL"):
            i += 4
            while i < n and fixed[i].strip() != "ENDTAB":
                i += 1
            i += 1
            continue
        out.append(fixed[i]); i += 1
    tmp = path + ".clean.dxf"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    return tmp


def load_doc(path: str) -> tuple[ezdxf.document.Document, bool]:
    """读取文档并探测 PROXY_ENTITY（真·天正自定义对象，代理图形缺失时漏检）。
    返回 (doc, has_proxy)。"""
    if path.lower().endswith(".dwg"):
        path = convert_dwg(path)
    doc = ezdxf.readfile(clean_tmp(path))
    has_proxy = False
    try:
        for e in doc.modelspace():
            if e.dxftype() == "PROXY_ENTITY":
                has_proxy = True
                break
    except Exception:
        pass
    return doc, has_proxy


def readfile_clean(path: str) -> str:
    """仅做清洗并返回干净 DXF 临时路径（供高亮层复制几何用）。"""
    if path.lower().endswith(".dwg"):
        path = convert_dwg(path)
    return clean_tmp(path)
