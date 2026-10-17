#!/usr/bin/env bash
# =============================================================================
#  InvestBuddy / InfoData — dev 模式一键启动（Linux / macOS）
#
#  与仓库根目录的 start-dev.bat 逻辑**一一对齐**，改一处请同时改另一处：
#    后端 :${INFO_DATA_API_PORT:-8000}   uvicorn app.main:app
#    前端 :${INFO_DATA_WEB_PORT:-5173}   npm run dev
#
#  Python 解释器解析顺序（首个命中即用）：
#    1) 环境变量 INFO_DATA_PYTHON（必须是存在的文件，否则警告并跳过）
#    2) 仓库根 .python-path（一行，填解释器绝对路径；已 gitignore，机器私有）
#    3) backend/.venv/bin/python
#    4) PATH 中的 python3，再退 python
#    一个都找不到就打印修法并退出 —— **不静默失败**。
#
#  用法：
#      ./scripts/start-dev.sh            # 起后端 + 前端
#      ./scripts/start-dev.sh backend    # 只起后端
#      ./scripts/start-dev.sh frontend   # 只起前端
#
#  停止：Ctrl+C；后台模式（nohup ./scripts/start-dev.sh &）用 kill 收尾。
# =============================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"

if [ ! -f "$BACKEND/app/main.py" ]; then
  echo "[ERROR] 找不到 $BACKEND/app/main.py" >&2
  echo "        本脚本必须位于仓库的 scripts/ 目录下，且 backend/ 完整。" >&2
  exit 1
fi

# ---------------------------- 解析 Python 解释器 ----------------------------
PY=""
if [ -n "${INFO_DATA_PYTHON:-}" ]; then
  if [ -x "${INFO_DATA_PYTHON}" ]; then
    PY="${INFO_DATA_PYTHON}"
  else
    echo "[WARN] INFO_DATA_PYTHON 指向的文件不存在或不可执行，已忽略：${INFO_DATA_PYTHON}"
  fi
fi

if [ -z "$PY" ] && [ -f "$ROOT/.python-path" ]; then
  # 只取第一行并去掉回车（Windows 上写过的文件常带 CRLF）
  candidate="$(head -n 1 "$ROOT/.python-path" | tr -d '\r' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
  if [ -n "$candidate" ] && [ -x "$candidate" ]; then
    PY="$candidate"
  elif [ -n "$candidate" ]; then
    echo "[WARN] .python-path 里写的解释器不可执行，已忽略：$candidate"
  fi
fi

if [ -z "$PY" ] && [ -x "$BACKEND/.venv/bin/python" ]; then
  PY="$BACKEND/.venv/bin/python"
fi

if [ -z "$PY" ] && command -v python3 >/dev/null 2>&1; then
  PY="$(command -v python3)"
fi

if [ -z "$PY" ] && command -v python >/dev/null 2>&1; then
  PY="$(command -v python)"
fi

if [ -z "$PY" ]; then
  cat >&2 <<'EOM'

[ERROR] 没有可用的 Python 解释器。任选一种修法：
          1) 安装 Python 3.12+ 并确保 python3 在 PATH 中
          2) 建虚拟环境： cd backend && python3 -m venv .venv
          3) 设环境变量： export INFO_DATA_PYTHON=/绝对路径/python3
          4) 在仓库根写 .python-path 文件，内容为解释器绝对路径（一行）

EOM
  exit 1
fi
echo "[OK] Python  = $PY"

# 版本硬要求：项目用到 `str | None` 等 3.10+ 语法，低于 3.10 会在 import 期崩
PYVER="$("$PY" -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null || echo "unknown")"
case "$PYVER" in
  3.1[0-9]|3.[2-9][0-9]) : ;;
  *)
    echo "[ERROR] Python 版本 $PYVER 过低，本项目要求 3.10+（建议 3.12）。" >&2
    exit 1
    ;;
esac

# ---------------------------- 解析端口 ----------------------------
API_PORT="${INFO_DATA_API_PORT:-8000}"
WEB_PORT="${INFO_DATA_WEB_PORT:-5173}"
echo "[OK] Ports   = backend :$API_PORT  frontend :$WEB_PORT"

# 端口占用检查：**统一用 Python socket 探测**，不依赖 ss / lsof。
#
# 为什么不用 ss / lsof（2026-10-17 实测踩过）：最小化安装的 Linux 容器、
# 以及 Windows 的 Git Bash 里**两者都可能不存在**。原实现「工具缺失 ⇒ 当作未占用」，
# 结果在端口已被占用时仍去启动，产生**重复实例**：uvicorn 绑定失败后退出（还留下
# 一段看似成功的启动日志），vite 则自动换端口跑到 5174 —— 排查时极具误导性。
# Python 是启动本项目的必备条件，用它探端口零额外依赖、跨平台一致。
port_busy() {
  "$PY" - "$1" <<'PYEOF' >/dev/null 2>&1
import socket, sys
port = int(sys.argv[1])
s = socket.socket()
s.settimeout(0.4)
try:
    # connect 成功即有人在监听（不发送任何数据，对服务无副作用）
    s.connect(("127.0.0.1", port))
    sys.exit(0)
except OSError:
    sys.exit(1)
finally:
    s.close()
PYEOF
}

TARGET="${1:-all}"
case "$TARGET" in
  all|backend|frontend) : ;;
  *) echo "[ERROR] 未知参数：$TARGET（可选 all / backend / frontend）" >&2; exit 1 ;;
esac

PIDS=()
cleanup() {
  echo
  echo "[STOP] 收到中断，正在停止本次启动的进程…"
  for p in "${PIDS[@]:-}"; do
    [ -n "$p" ] && kill "$p" 2>/dev/null || true
  done
  wait 2>/dev/null || true
  exit 0
}
trap cleanup INT TERM

# ---------------------------- 后端 ----------------------------
if [ "$TARGET" = "all" ] || [ "$TARGET" = "backend" ]; then
  if port_busy "$API_PORT"; then
    echo "[SKIP] 后端已在 :$API_PORT 监听"
  else
    echo "[START] 后端 API …  http://127.0.0.1:$API_PORT/docs"
    # NO_PROXY：国内数据源直连，避免本机 HTTP 代理干扰采集
    ( cd "$BACKEND" && NO_PROXY='*' no_proxy='*' \
        "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port "$API_PORT" ) &
    PIDS+=("$!")
  fi
fi

# ---------------------------- 前端 ----------------------------
if [ "$TARGET" = "all" ] || [ "$TARGET" = "frontend" ]; then
  if ! command -v npm >/dev/null 2>&1; then
    echo "[WARN] 未找到 npm，跳过前端。装 Node.js 18+ 后重试。"
  elif port_busy "$WEB_PORT"; then
    echo "[SKIP] 前端已在 :$WEB_PORT 监听"
  else
    if [ ! -d "$FRONTEND/node_modules" ]; then
      echo "[INFO] 首次运行：安装前端依赖（npm install）…"
      ( cd "$FRONTEND" && npm install ) || { echo "[ERROR] npm install 失败" >&2; exit 1; }
    fi
    echo "[START] 前端 dev server …  http://127.0.0.1:$WEB_PORT"
    ( cd "$FRONTEND" && INFO_DATA_WEB_PORT="$WEB_PORT" INFO_DATA_API_PORT="$API_PORT" npm run dev ) &
    PIDS+=("$!")
  fi
fi

if [ ${#PIDS[@]} -eq 0 ]; then
  echo "两个服务都已在运行，无需启动。"
  exit 0
fi

echo
echo "已启动。打开 http://127.0.0.1:$WEB_PORT （Ctrl+C 停止）"
wait
