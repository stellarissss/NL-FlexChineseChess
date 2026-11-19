"""全局路径与运行时布局。

v2.0 的关键设计：**代码（可被 Agent 改写）与运行时状态分离**。
- 代码位于 app 根目录，由 Agent 在 staging 构建目录中修改，验收后热发布。
- 对局状态位于 runtime/state/，不会因代码发布而丢失。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent
# 多实例支持：CHESS_RUNTIME_DIR 可指向独立运行时目录（构建/对局状态/日志互不干扰），
# 多个进程共用同一份代码，仅状态隔离，例如 /chess、/chess1、/chess2 三局并行。
RUNTIME_DIR = Path(os.environ.get("CHESS_RUNTIME_DIR") or (APP_ROOT / "runtime"))
BUILDS_DIR = RUNTIME_DIR / "builds"
STATE_DIR = RUNTIME_DIR / "state"
LOG_DIR = RUNTIME_DIR / "logs"
ACTIVE_POINTER = RUNTIME_DIR / "active.json"

for _d in (RUNTIME_DIR, BUILDS_DIR, STATE_DIR, LOG_DIR):
    _d.mkdir(parents=True, exist_ok=True)


def read_active() -> dict:
    """读取当前生效的构建指针。"""
    try:
        return json.loads(ACTIVE_POINTER.read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_active(data: dict) -> None:
    tmp = ACTIVE_POINTER.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, ACTIVE_POINTER)


def active_build_dir() -> Path:
    """返回当前生效构建的根目录；未发布过则为 app 根目录。"""
    info = read_active()
    bid = info.get("build")
    if bid:
        p = BUILDS_DIR / bid
        if p.is_dir():
            return p
    return APP_ROOT


def active_game_id() -> str:
    info = read_active()
    return info.get("game") or "xiangqi"
