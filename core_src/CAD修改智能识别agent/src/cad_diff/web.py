#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
"""
Web 交互界面（FastAPI）

一个本地分析工具界面：上传旧版/新版图纸（DWG/DXF 或 ZIP），选择配置预设，
点「比对」后服务端跑完整管线，把结果（圈注 DXF + 对比图 + 聚焦图 + PDF/HTML
报告 + 变更清单 + 中文摘要）展示在网页里，可在线预览/下载。

限制说明：DWG 需先转 DXF（服务端自动用 dwg2dxf 转）；ZIP 自动解压取内部图纸。
"""
from __future__ import annotations
import os
import io
import sys
import time
import shutil
import tempfile
import zipfile
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent                       # CAD修改智能识别agent/
sys.path.insert(0, str(HERE.parent))           # 使 cad_diff 包可导入

from cad_diff import pipeline, config          # noqa: E402
from cad_diff.io_dxf import convert_dwg        # noqa: E402

WEB_DIR = HERE / "web_assets"
UPLOAD_DIR = ROOT / "outputs" / "web_uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

CONFIG_DIR = ROOT / "configs"
PRESETS = {
    "default": str(CONFIG_DIR / "default.yaml"),
    "tianzheng": str(CONFIG_DIR / "tianzheng.yaml"),
}

app = FastAPI(title="CAD 修改智能识别 · Web")

# 前端静态页由 / 直接返回（内联），其余资源用 StaticFiles
app.mount("/assets", StaticFiles(directory=str(WEB_DIR), html=False), name="assets")


async def _resolve_input(upload: UploadFile, work: Path) -> str:
    """把上传文件落盘并解析为可用路径：DWG→转DXF；ZIP→解压取首个图纸。"""
    # 注意：在 async 端点中必须用 await upload.read()，同步 .read() 会与事件循环冲突导致读取为空/错位
    data = await upload.read()
    if not data:
        raise HTTPException(400, f"文件为空: {upload.filename}")
    suffix = Path(upload.filename or "x.dxf").suffix.lower()
    work.mkdir(parents=True, exist_ok=True)
    raw = work / f"up{suffix}"
    raw.write_bytes(data)

    if suffix == ".zip":
        extract_dir = work / "unzip"
        extract_dir.mkdir(exist_ok=True)
        with zipfile.ZipFile(raw) as z:
            z.extractall(extract_dir)
        dxf = next((p for p in extract_dir.rglob("*.dxf")), None)
        dwg = next((p for p in extract_dir.rglob("*.dwg")), None)
        target = str(dxf) if dxf else (str(dwg) if dwg else None)
        if not target:
            raise HTTPException(400, "ZIP 内未找到 .dxf/.dwg 图纸")
        return target
    if suffix == ".dwg":
        return convert_dwg(str(raw))
    # dxf / 其它当 dxf 处理
    return str(raw)


@app.get("/", response_class=HTMLResponse)
def index():
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(html)


@app.get("/api/presets")
def presets():
    return {"presets": list(PRESETS.keys())}


@app.post("/api/compare")
async def compare(
    old: UploadFile = File(...),
    new: UploadFile = File(...),
    preset: str = Form("default"),
    registration: str = Form(""),
    raster: bool = Form(False),
    no_llm: bool = Form(False),
):
    t0 = time.time()
    cfg_path = PRESETS.get(preset, PRESETS["default"])
    cfg = config.load_config(cfg_path)
    if registration in ("translation", "rigid"):
        cfg["registration"]["method"] = registration
    if raster:
        cfg["raster"]["enabled"] = True
    if no_llm:
        cfg["llm"]["provider"] = "rule"

    job = UPLOAD_DIR / f"job_{int(t0*1000)}"
    job.mkdir(parents=True, exist_ok=True)
    try:
        old_path = await _resolve_input(old, job / "old")
        new_path = await _resolve_input(new, job / "new")
    except HTTPException:
        raise
    except Exception as ex:
        raise HTTPException(400, f"输入解析失败: {ex}")

    out_prefix = str(job / "result")
    try:
        man = pipeline.run(old_path, new_path, out_prefix, cfg)
    except Exception as ex:
        raise HTTPException(500, f"比对失败: {ex}")

    changes = man.get("changes_list", man["changes"])
    summary = man.get("summary", {})

    # 图片转 base64 供前端内联预览
    import base64

    def b64(p):
        if p and os.path.exists(p):
            return "data:image/png;base64," + base64.b64encode(
                open(p, "rb").read()).decode("ascii")
        return None

    images = []
    main_png = man["files"].get("png")
    if main_png:
        images.append({"name": "整图对比", "src": b64(main_png)})
    for z in man["files"].get("zoom", []):
        images.append({"name": Path(z).stem, "src": b64(z)})
    raster_png = man["files"].get("raster")
    if raster_png:
        images.append({"name": "栅格级像素 diff", "src": b64(raster_png)})

    downloads = []
    job_name = job.name
    for kind, p in man["files"].items():
        if isinstance(p, list):
            for sub in p:
                downloads.append({"kind": kind, "name": Path(sub).name, "all": False,
                                  "url": f"/api/file?job={job_name}&name={Path(sub).name}"})
        elif p:
            downloads.append({"kind": kind, "name": Path(p).name, "all": (kind == "zip"),
                              "url": f"/api/file?job={job_name}&name={Path(p).name}"})
    # 把「下载全部(zip)」排到最前
    downloads.sort(key=lambda d: not d["all"])

    return JSONResponse({
        "ok": True,
        "elapsed": round(time.time() - t0, 2),
        "job": job_name,
        "counts": man["counts"],
        "summary": {"text": summary.get("text", ""), "source": summary.get("source", "rule")},
        "meta": {
            "old_entities": man["meta"]["old_entities"],
            "new_entities": man["meta"]["new_entities"],
            "block_expanded": man["meta"].get("block_expanded", 0),
            "old_proxy": man["meta"]["old_proxy"],
            "new_proxy": man["meta"]["new_proxy"],
            "registration": man["meta"]["registration"],
            "registration_info": man["meta"].get("registration_info", {}),
        },
        "changes": [
            {
                "kind": c["kind"], "type": c["type"], "layer": c["layer"],
                "bbox": [round(v, 1) for v in c["bbox"]],
                "old": c.get("old", ""), "new": c.get("new", ""),
                "movement": c.get("movement"),
            } for c in changes
        ],
        "images": [im for im in images if im["src"]],
        "downloads": downloads,
    })


@app.get("/api/jobs")
def list_jobs():
    """列出 web_uploads 下的历史比对任务（文件管理）。"""
    jobs = []
    for d in sorted(UPLOAD_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if not d.is_dir():
            continue
        files = []
        total = 0
        for f in d.rglob("*"):
            if f.is_file():
                total += f.stat().st_size
                files.append(f.name)
        zip_name = "result.zip" if (d / "result.zip").exists() else None
        jobs.append({
            "job": d.name,
            "mtime": int(d.stat().st_mtime),
            "count": len(files),
            "size": total,
            "has_zip": bool(zip_name),
            "zip_url": f"/api/file?job={d.name}&name=result.zip" if zip_name else None,
            "files": files,
        })
    return {"jobs": jobs, "upload_dir": str(UPLOAD_DIR)}


@app.post("/api/clean")
def clean_jobs(keep: int = 5):
    """清理历史比对任务，仅保留最近 keep 个（默认 5）。返回被删除的 job 名。"""
    if keep < 0:
        raise HTTPException(400, "keep 不能为负")
    dirs = [d for d in UPLOAD_DIR.iterdir() if d.is_dir()]
    dirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    removed = []
    for d in dirs[keep:]:
        shutil.rmtree(d, ignore_errors=True)
        removed.append(d.name)
    return {"removed": removed, "remaining": len(dirs) - len(removed)}


@app.get("/api/file")
def get_file(job: str, name: str):
    # 仅允许访问本 job 目录下的产物，防目录穿越（不接受任意路径）
    if not job or not name:
        raise HTTPException(400, "缺少参数")
    safe_name = Path(name).name          # 丢弃任何目录成分
    path = (UPLOAD_DIR / job / safe_name)
    if not str(path).startswith(str(UPLOAD_DIR)) or not path.exists():
        raise HTTPException(404, "文件不存在")
    return FileResponse(str(path))


if __name__ == "__main__":
    import argparse
    import uvicorn
    _ap = argparse.ArgumentParser(description="CAD 修改智能识别 Web")
    _ap.add_argument("--host", default="0.0.0.0")
    _ap.add_argument("--port", type=int, default=8000)
    _a = _ap.parse_args()
    uvicorn.run(app, host=_a.host, port=_a.port)
