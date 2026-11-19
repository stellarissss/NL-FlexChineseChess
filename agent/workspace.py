"""构建工作区：Agent 在隔离副本里改代码，验收后原子发布。

设计要点
- **构建即产物**：runtime/builds/<id>/ 是完整可运行的应用副本；发布 = 切换
  runtime/active.json 指针，毫秒级生效，随时可回滚。
- **状态隔离**：每个构建自带 runtime/ 目录，校验时的对局不会污染玩家的棋局。
- **核心改动**：若 Agent 修改了 core/ main.py run.py agent/，发布后需要重启进程
  才能生效；games/ 与 static/ 则完全热更新。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from core import paths

COPY_ITEMS = ["run.py", "main.py", "core", "agent", "games", "static", "tools"]
# 核心代码永远以 APP_ROOT 为唯一真源：构建里的 core 只是「可编辑副本」，
# 如果从上一个构建继承，就会把 app 根目录后来修好的补丁悄悄覆盖回去。
CORE_ITEMS = ["run.py", "main.py", "core", "agent", "tools"]
ASSET_ITEMS = ["games", "static"]
IGNORE = shutil.ignore_patterns(
    "runtime", "venv", "__pycache__", "*.pyc", ".git", "node_modules",
    "*.log", ".DS_Store", "_shots",
)
CORE_PREFIXES = ("core/", "agent/", "main.py", "run.py", "tools/")


def _hash_tree(root: Path) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for base in COPY_ITEMS:
        p = root / base
        if not p.exists():
            continue
        if p.is_file():
            out[base] = hashlib.sha1(p.read_bytes()).hexdigest()
            continue
        for f in p.rglob("*"):
            if f.is_file() and "__pycache__" not in f.parts:
                rel = str(f.relative_to(root))
                try:
                    out[rel] = hashlib.sha1(f.read_bytes()).hexdigest()
                except Exception:
                    pass
    return out


class Build:
    def __init__(self, build_id: str, root: Path):
        self.id = build_id
        self.root = root

    # ── 路径守卫 ──
    FORBIDDEN_PREFIXES = ("agent/",)   # Agent 自身运行时，从构建内改等于改裁判

    def resolve(self, rel: str) -> Path:
        rel = (rel or "").lstrip("/")
        target = (self.root / rel).resolve()
        if not str(target).startswith(str(self.root.resolve())):
            raise ValueError(f"路径越界: {rel}")
        if any(part in ("..",) for part in Path(rel).parts):
            raise ValueError(f"非法路径: {rel}")
        if rel.startswith("runtime/") or "/venv/" in f"/{rel}":
            raise ValueError("该路径不可编辑")
        for p in self.FORBIDDEN_PREFIXES:
            if rel == p.rstrip("/") or rel.startswith(p):
                raise ValueError(
                    f"{p} 是 Agent 自身的运行时代码，禁止修改；"
                    f"若确需新工具能力，请在 summary 里说明，由人工评估")
        return target

    def read(self, rel: str) -> str:
        p = self.resolve(rel)
        if not p.exists() or not p.is_file():
            raise FileNotFoundError(rel)
        return p.read_text(encoding="utf-8", errors="replace")

    def write(self, rel: str, content: str):
        p = self.resolve(rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, p)

    def delete(self, rel: str):
        p = self.resolve(rel)
        if p.is_file():
            p.unlink()

    def list_dir(self, rel: str = "", depth: int = 2) -> List[str]:
        base = self.resolve(rel) if rel else self.root
        out: List[str] = []
        base_depth = len(base.parts)
        for p in sorted(base.rglob("*")):
            relp = p.relative_to(base)
            if any(x in relp.parts for x in ("__pycache__", "runtime", "venv", ".git")):
                continue
            if len(relp.parts) > depth:
                continue
            out.append(str(p.relative_to(self.root)) + ("/" if p.is_dir() else ""))
            if len(out) >= 400:
                break
        return out

    def changed_files(self) -> List[str]:
        base = _hash_tree(self.root)
        orig = self.base_hashes
        changed = []
        for k, v in base.items():
            if orig.get(k) != v:
                changed.append(k)
        for k in orig:
            if k not in base:
                changed.append(k + " (deleted)")
        return sorted(changed)

    def needs_restart(self) -> bool:
        """是否需要冷启动（进程级重启）。

        games/ 下的 .py 不再触发冷启动：core/engine.load_engine 已按构建目录
        隔离模块命名空间（裸名 import 的 rule_engine/ai 等在加载时弹出并恢复，
        不会串构建），发布时 session.reload(full=False) 即可干净热替换，
        并有发布后 _opening_sig 自检兜底。游戏改动热更新 = 页面秒级生效，
        不再有「服务重启窗口期打不开」的问题。

        仅 core/ main.py run.py agent/ tools/ 这类进程级核心代码才冷启动。
        """
        for f in self.changed_files():
            rel = f.split(" ")[0]
            if rel.startswith(CORE_PREFIXES):
                return True
        return False

    def touches_rules(self) -> bool:
        """走子规则/棋盘几何/棋子定义是否变了。

        2026-11-07 起**不再触发重开**：它只用于告诉发布编排「需要把新引擎
        引入的结构性 state 字段热并入当前对局」（见 core/state.hot_merge）。
        棋盘重开只允许「某方胜利 / 玩家手动」两条途径。

        注意 **不含** configs/initial/board_state.json：那只是「初始布局」，
        改它不代表规则变了（比如「再给我五个傻」只是多摆几枚子），由
        touches_initial_layout() 单独判定走摆子热并入。
        """
        rule_files = {"board.json", "rules.json", "pieces_red.json",
                      "pieces_black.json", "engine.py"}
        for f in self.changed_files():
            rel = f.split(" ")[0]
            if not rel.startswith("games/"):
                continue
            base = rel.rsplit("/", 1)[-1]
            if rel.endswith(".py") or base in rule_files:
                return True
        return False

    def touches_initial_layout(self) -> bool:
        """初始布局（摆子）是否变了 —— 可以并入当前对局，不必重开。"""
        for f in self.changed_files():
            rel = f.split(" ")[0]
            if not rel.startswith("games/"):
                continue
            if rel.rsplit("/", 1)[-1] == "board_state.json":
                return True
        return False


def _new_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}"


def create_build(base_dir: Path) -> Build:
    """从当前生效的美术/棋种 + 应用根目录的核心代码，复制出一个可编辑构建。

    - games/ static/ 取自 base_dir（当前生效构建），Agent 才能接着上次的成果改；
    - core/ agent/ main.py run.py tools/ 恒取自 APP_ROOT，避免核心代码版本漂移。
    """
    bid = _new_id()
    root = paths.BUILDS_DIR / bid
    root.mkdir(parents=True, exist_ok=True)

    def _copy(src: Path, dst: Path):
        if not src.exists():
            return
        if src.is_dir():
            shutil.copytree(src, dst, ignore=IGNORE, dirs_exist_ok=True)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

    for item in ASSET_ITEMS:
        _copy(Path(base_dir) / item, root / item)
    for item in CORE_ITEMS:
        _copy(paths.APP_ROOT / item, root / item)

    (root / "runtime").mkdir(exist_ok=True)
    b = Build(bid, root)
    b.base_hashes = _hash_tree(root)
    return b


def promote(build: Build, game_id: str, prev_build: str = "") -> Tuple[bool, str]:
    """把构建设为当前生效版本。"""
    info = paths.read_active()
    history = info.get("history") or []
    prev = {"build": info.get("build") or "", "game": info.get("game") or "xiangqi",
            "ts": time.time()}
    if not history or history[-1].get("build") != prev["build"] or history[-1].get("game") != prev["game"]:
        history.append(prev)
    history = history[-20:]
    paths.write_active({
        "build": build.id,
        "game": game_id,
        "ts": time.time(),
        "history": history,
        "version": "2.0.0-beta",
    })
    return True, build.id


def revert_to_pristine() -> None:
    """把生效指针退回「从未发布过任何构建」的出厂态（APP_ROOT 原始代码/配置）。"""
    info = paths.read_active()
    paths.write_active({
        "build": "",
        "game": info.get("game") or "xiangqi",
        "ts": time.time(),
        "history": [],
        "version": "2.0.0-beta",
    })


def rollback_last() -> Tuple[bool, str]:
    info = paths.read_active()
    hist = info.get("history") or []
    if not hist:
        return False, "没有可回滚的版本"
    last = hist.pop()
    paths.write_active({
        "build": last.get("build") or "",
        "game": last.get("game") or "xiangqi",
        "ts": time.time(),
        "history": hist,
        "version": "2.0.0-beta",
    })
    return True, f"已回滚到 {last.get('build') or '初始版本'}"


def gc(keep: int = 12):
    """清理旧构建，保留最近 keep 个。"""
    try:
        dirs = sorted([d for d in paths.BUILDS_DIR.iterdir() if d.is_dir()],
                      key=lambda d: d.stat().st_mtime, reverse=True)
        active = paths.read_active().get("build")
        for d in dirs[keep:]:
            if d.name == active:
                continue
            shutil.rmtree(d, ignore_errors=True)
    except Exception:
        pass
