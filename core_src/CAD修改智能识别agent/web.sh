#!/usr/bin/env bash
# CAD 修改智能识别 · Web 交互界面启动脚本（守护进程版）
#
# 用法：
#   bash web.sh            # 后台常驻启动（默认端口 8000），无窗口、不阻塞终端
#   bash web.sh 8080       # 指定端口
#   bash web.sh stop       # 停止正在运行的服务
#   bash web.sh restart    # 重启
#   bash web.sh status     # 查看运行状态
#
# 进程完全脱离当前终端（setsid + nohup），日志写入 outputs/web.log，
# PID 写入 outputs/web.pid，会话结束/关闭终端后仍在后台运行。
set -e
cd "$(dirname "$0")"
PY=/root/.pyenv/versions/3.11.1/bin/python3.11
# DWG→DXF 转换器若已安装，加入 PATH（转换 .dwg 时需要）
export PATH=/root/miniconda/envs/dwgconv/bin:/usr/bin:$PATH
# 转换器优先级：oda | dwg2dxf | auto（默认 auto = 先 dwg2dxf，失败回退 ODA）
# 想优先用 ODA File Converter：CAD_DIFF_CONVERTER=oda bash web.sh
export CAD_DIFF_CONVERTER="${CAD_DIFF_CONVERTER:-auto}"
# 把 src/ 加入 Python 搜索路径，使 `cad_diff` 包可被 `python -m` 找到
export PYTHONPATH="$(pwd)/src:${PYTHONPATH}"

LOG_FILE="$(pwd)/outputs/web.log"
PID_FILE="$(pwd)/outputs/web.pid"
PORT="${1:-8000}"

is_running() {
  [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null
}

case "$1" in
  stop)
    if is_running; then
      kill "$(cat "$PID_FILE")" 2>/dev/null && echo ">>> 已停止 Web 服务 (PID $(cat "$PID_FILE"))"
      rm -f "$PID_FILE"
    else
      echo ">>> 未检测到运行中的 Web 服务"
    fi
    exit 0
    ;;
  restart)
    if is_running; then kill "$(cat "$PID_FILE")" 2>/dev/null; rm -f "$PID_FILE"; sleep 1; fi
    ;;
  status)
    if is_running; then echo ">>> Web 服务运行中 (PID $(cat "$PID_FILE")) 端口见 $LOG_FILE"; else echo ">>> 未运行"; fi
    exit 0
    ;;
esac

if is_running; then
  echo ">>> 已有 Web 服务在运行 (PID $(cat "$PID_FILE"))，请先 bash web.sh stop"
  exit 1
fi

mkdir -p "$(pwd)/outputs"
echo ">>> 启动 CAD 修改智能识别 Web（后台守护，端口 ${PORT}）"
echo ">>> 日志: ${LOG_FILE}   关闭: bash web.sh stop"
# 完全脱离终端：setsid + nohup，输入来自 /dev/null，输出/错误重定向到日志
setsid nohup "$PY" -m cad_diff.web --port "$PORT" > "$LOG_FILE" 2>&1 < /dev/null &
echo $! > "$PID_FILE"
disown 2>/dev/null || true
sleep 2
if is_running; then
  echo ">>> 启动成功，浏览器打开: http://localhost:${PORT}"
else
  echo ">>> 启动失败，请查看日志: ${LOG_FILE}"
  cat "$LOG_FILE" 2>/dev/null | tail -20
  exit 1
fi
