#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════
# NL-FlexChineseChess 一键启动脚本（本地 / 自建服务器通用）
#
#   用法：
#     cp env.example .env      # 填入 DEEPSEEK_API_KEY
#     ./start.sh
#
#   或直接传环境变量：
#     DEEPSEEK_API_KEY=sk-xxx ./start.sh
#
#   端口 / 监听地址：CHESS_PORT（默认 9401）、CHESS_HOST（默认 0.0.0.0）
# ═══════════════════════════════════════════════════════════
set -euo pipefail
cd "$(dirname "$0")"

# 1) 加载 .env（若存在）——只导出变量，不回显密钥
if [ -f .env ]; then
    set -a
    # shellcheck disable=SC1091
    source .env
    set +a
fi

# 2) 选择 Python（优先 python3）
PY_BIN="${PYTHON_BIN:-python3}"
if ! command -v "$PY_BIN" >/dev/null 2>&1; then
    echo "找不到 Python，请安装 Python 3.10+ 或设置 PYTHON_BIN。" >&2
    exit 1
fi

# 3) 虚拟环境（可复用）
VENV_DIR="${VENV_DIR:-.venv}"
if [ ! -f "$VENV_DIR/bin/python" ]; then
    echo "[setup] 创建虚拟环境 $VENV_DIR ..."
    "$PY_BIN" -m venv "$VENV_DIR"
fi
PY="$VENV_DIR/bin/python"

# 4) 依赖
echo "[setup] 安装依赖 ..."
"$PY" -m pip install -q --upgrade pip
"$PY" -m pip install -q -r requirements.txt

# 5) 密钥自检（缺失时给出清晰指引，但不阻断——服务本身可起，Agent 调用会报错）
if [ -z "${DEEPSEEK_API_KEY:-}" ] && [ ! -f config.json ]; then
    echo "[warn] 未检测到 DEEPSEEK_API_KEY，也没有 config.json。" >&2
    echo "       请将 env.example 复制为 .env 并填入密钥，或导出 DEEPSEEK_API_KEY。" >&2
fi

PORT="${CHESS_PORT:-9401}"
HOST="${CHESS_HOST:-0.0.0.0}"
echo "[run] NL-FlexChineseChess 启动于 http://${HOST}:${PORT}"
exec "$PY" run.py
