"""游戏引擎契约与加载器。

任何棋类 / 桌游都实现 GameEngine 的三个核心方法：
    new_state()                  → 初始局面
    legal_actions(state, ...)    → 合法动作列表
    apply_action(state, action)  → 落子并推进回合（原地修改并返回 state）
可选项（全部有缺省实现，不实现即退化为「严格轮流、无随机、无时钟」）：
    ai_action(state, side)       → 电脑走一步（返回 action 或 None）
    status(state)                → 结束判定
    meta()                       → 前端渲染所需的元信息
    actable_sides(state)         → 当前可行动的一方（异步/多方同时可动）
    tick(state, now)             → 推进时钟（实时机制），返回 state 是否变化
    capabilities()               → 能力声明（见下）

三种自由度与约束（自检按此判定，不再「一有随机就驳回」）：
    · 随机：用 state_rng(state, key) 取随机源。它的种子由 state 内容决定，
      同一局面下提示点、送将过滤、apply 校验掷出同一颗骰子；局面一变自然重掷。
      直接在走法计算里调 random 属于「活随机」——允许运行，但要求 apply_action
      只按静态规则校验、绝不重掷（否则查询到的动作执行时会被自己拒绝）。
    · 异步：实现 actable_sides() 返回可行动方列表，apply_action 按它校验，
      不要写死 side == current_turn。
    · 实时：实现 tick(state, now) 并在 manifest.json 声明
      "capabilities": {"realtime": {"tick_hz": 1}}，主循环会驱动它。
    真正的硬约束只有一条：legal_actions 返回的动作，apply_action 在同一时刻
    必须接受（查询与执行不能对「合法」有分歧）。

状态（state）是前后端共享的唯一数据结构，字段约定见 core/engine.py 顶部说明，
前端 static/app.js 直接消费，不要随意改名。
"""
from __future__ import annotations

import importlib.util
import json
import random
import sys
import threading
import zlib
from pathlib import Path
from typing import Any, Dict, List, Optional


# ─────────────────────────────────────────────────────────────
# 局面随机源：随机的种子来自 state 本身
#
# 前端链路是「选中 → 查提示点 → 点击 → 服务端 apply 再校验」，同一步棋会被
# 查询多次。若每次查询都调全局 random，玩家会点到提示点上却报「非法移动」。
# 用 state_rng 后：同一局面所有查询掷出同一结果（提示点/执行一致），
# 而局面一变（走子、换回合）骰子就重掷——随机性一点没少，只是采样点绑在局面上。
#
# 用法（在 legal_actions / apply_action 里都可以）：
#     rng = state_rng(state, "teleport")     # key 区分不同随机用途
#     dest = rng.choice(candidates)
# ─────────────────────────────────────────────────────────────
_RNG_CACHE: Dict[tuple, int] = {}
_RNG_CACHE_MAX = 512


def _rng_seed(state: Dict[str, Any], key: str) -> int:
    sig_cache: Dict[tuple, int] = _RNG_CACHE
    try:
        payload = json.dumps(state, sort_keys=True, ensure_ascii=False, default=str)
    except Exception:
        payload = repr(state)
    sig = (zlib.crc32(payload.encode("utf-8", "replace")),
           zlib.crc32(str(key).encode("utf-8", "replace")))
    seed = sig_cache.get(sig)
    if seed is None:
        seed = (sig[0] << 32) ^ sig[1]
        if len(sig_cache) >= _RNG_CACHE_MAX:
            sig_cache.clear()
        sig_cache[sig] = seed
    return seed


def state_rng(state: Dict[str, Any], key: str = "") -> random.Random:
    """由 (state 内容, key) 决定种子的随机源：同一局面 → 同一串结果。

    每次调用返回全新的 Random 实例（互不推进），所以「查询多少次」都不会
    改变结果；只有 state 内容变了，下一次拿到的才是新骰子。
    """
    return random.Random(_rng_seed(state, key))

# ─────────────────────────────────────────────────────────────
# 状态结构约定（前端依赖，务必保持）
# {
#   "board": {"width": 9, "height": 10},
#   "pieces": [
#       {"id": "r_ju_1", "type": "ju", "name": "車", "side": "red",
#        "position": [0, 9], "is_alive": true, "custom_properties": {}}
#   ],
#   "current_turn": "red",
#   "move_history": [{"piece_id": "...", "from": [x,y], "to": [x,y], "captured": null}],
#   "game_status": {"state": "playing", "winner": null, "win_condition": null,
#                   "custom_rules_active": []}
# }
# 动作（action）：
#   {"type": "move",  "piece_id": "r_ju_1", "to": [x, y]}
#   {"type": "place", "to": [x, y], "side": "red", "piece_type": "stone", "name": ""}
#   {"type": "pass",  "side": "red"}
# ─────────────────────────────────────────────────────────────


class ActionError(Exception):
    """非法动作。message 会直接展示给玩家。"""


class GameEngine:
    id: str = "generic"
    name: str = "未命名棋局"
    interaction: str = "move"          # move | place | custom
    config_files: List[str] = ["board", "ui_config"]

    def __init__(self, configs: Dict[str, Any]):
        self.configs = configs

    # ── 必须实现 ──
    def new_state(self) -> Dict[str, Any]:
        raise NotImplementedError

    def legal_actions(self, state: Dict[str, Any], piece_id: Optional[str] = None,
                      side: Optional[str] = None) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def apply_action(self, state: Dict[str, Any], action: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError

    # ── 可选实现 ──
    def ai_action(self, state: Dict[str, Any], side: str) -> Optional[Dict[str, Any]]:
        return None

    def status(self, state: Dict[str, Any]) -> Dict[str, Any]:
        return state.get("game_status") or {"state": "playing", "winner": None}

    def actable_sides(self, state: Dict[str, Any]) -> List[str]:
        """当前允许行动的一方（可多个 → 异步/连动/反应着）。

        缺省是严格轮流。实现异步规则时重写它，并让 apply_action 按它校验
        （不要写死 side == current_turn），自检与服务端都以此为准。
        """
        cur = state.get("current_turn")
        return [cur] if cur else []

    def tick(self, state: Dict[str, Any], now: Optional[float] = None) -> bool:
        """推进时钟：实时机制（冷却、计时、自动移动）在这里落地。

        返回 True 表示 state 被改动，服务端会据此持久化并向客户端推送。
        实现后请在 manifest.json 声明 "capabilities": {"realtime": {"tick_hz": 1}}，
        否则主循环不会驱动它（避免无谓空转）。
        """
        return False

    def capabilities(self) -> Dict[str, Any]:
        """引擎自述能力（manifest 里的 capabilities 优先，这里给缺省值）。"""
        return {"randomness": "state", "async_actions": False, "realtime": None}

    def meta(self) -> Dict[str, Any]:
        geom = (self.configs.get("board") or {}).get("geometry") or {}
        return {
            "id": self.id,
            "name": self.name,
            "interaction": self.interaction,
            "width": int(geom.get("width") or 9),
            "height": int(geom.get("height") or 10),
            "sides": ["red", "black"],
        }


class GridEngine(GameEngine):
    """网格棋类的通用基类：提供坐标、占位、直线计数等纯工具方法。

    它不含任何具体棋种的胜负逻辑——那是子类（Agent 生成）的职责。
    """

    interaction = "place"

    # ── 网格工具 ──
    @property
    def width(self) -> int:
        return int(((self.configs.get("board") or {}).get("geometry") or {}).get("width") or 15)

    @property
    def height(self) -> int:
        return int(((self.configs.get("board") or {}).get("geometry") or {}).get("height") or 15)

    def in_bounds(self, x: int, y: int) -> bool:
        return 0 <= x < self.width and 0 <= y < self.height

    @staticmethod
    def piece_at(state: Dict[str, Any], x: int, y: int) -> Optional[dict]:
        for p in state.get("pieces", []):
            if p.get("is_alive", True) and list(p.get("position") or []) == [x, y]:
                return p
        return None

    def empty(self, state: Dict[str, Any], x: int, y: int) -> bool:
        return self.piece_at(state, x, y) is None

    @staticmethod
    def next_side(state: Dict[str, Any], sides=("red", "black")) -> str:
        cur = state.get("current_turn") or sides[0]
        return sides[1] if cur == sides[0] else sides[0]

    def line_length(self, state: Dict[str, Any], x: int, y: int, dx: int, dy: int,
                    side: str) -> int:
        """从 (x,y) 沿 (dx,dy) 双向统计同色连子数量（含自身）。"""
        n = 1
        for sign in (1, -1):
            cx, cy = x + dx * sign, y + dy * sign
            while self.in_bounds(cx, cy):
                p = self.piece_at(state, cx, cy)
                if not p or p.get("side") != side:
                    break
                n += 1
                cx += dx * sign
                cy += dy * sign
        return n

    def new_piece(self, state: Dict[str, Any], side: str, x: int, y: int,
                  piece_type: str = "stone", name: str = "") -> dict:
        n = sum(1 for p in state.get("pieces", []) if p.get("side") == side) + 1
        return {
            "id": f"{side}_{piece_type}_{n}",
            "type": piece_type,
            "name": name,
            "side": side,
            "position": [x, y],
            "is_alive": True,
            "custom_properties": {},
        }


# ─────────────────────────────────────────────────────────────
# 动态加载：按构建目录隔离模块命名空间，避免热发布后拿到旧模块
# ─────────────────────────────────────────────────────────────

_LOAD_LOCK = threading.RLock()


def _shadowed_names(game_dir: Path) -> set:
    """build 内会被裸名 import 的模块名（engine/rule_engine/ai…）。

    只取非标准库名字，避免误伤 sys.modules 里的 json / copy 等。
    """
    stdlib = getattr(sys, "stdlib_module_names", frozenset())
    return {p.stem for p in game_dir.glob("*.py") if p.stem not in stdlib}


def load_engine(game_id: str, build_dir: Path, configs: Dict[str, Any]) -> GameEngine:
    """加载引擎，并保证「用的是这个构建目录里的代码」。

    坑：engine.py 里写的是 `from rule_engine import RuleEngine` 这类裸名导入，
    走的是 sys.modules 全局缓存。热发布时若不清缓存，新构建的 rule_engine.py
    永远不会被加载 —— 配置是新的、代码是旧的，改规则「看起来生效实际没生效」。
    """
    engine_path = Path(build_dir) / "games" / game_id / "engine.py"
    if not engine_path.exists():
        raise FileNotFoundError(f"未找到引擎：{engine_path}")

    game_dir = engine_path.parent
    mod_name = f"game_engine_{game_id}_{abs(hash(str(build_dir))) & 0xFFFFFF}"

    with _LOAD_LOCK:
        for stale in [m for m in sys.modules if m.startswith(f"game_engine_{game_id}_")]:
            sys.modules.pop(stale, None)

        local = _shadowed_names(game_dir)
        saved = {n: sys.modules.pop(n) for n in list(local) if n in sys.modules}
        inserted = False
        if str(game_dir) not in sys.path:
            sys.path.insert(0, str(game_dir))
            inserted = True
        try:
            spec = importlib.util.spec_from_file_location(mod_name, engine_path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[mod_name] = module
            spec.loader.exec_module(module)  # type: ignore[union-attr]
        finally:
            if inserted:
                try:
                    sys.path.remove(str(game_dir))
                except ValueError:
                    pass
            # 本次加载出来的裸名模块不要留在全局，防止污染其它构建
            for name in local:
                if name in sys.modules and name not in saved:
                    sys.modules.pop(name, None)
            sys.modules.update(saved)

    cls = getattr(module, "Engine", None)
    if cls is None:
        raise AttributeError("engine.py 必须导出名为 Engine 的类")
    return cls(configs)


def load_manifest(game_id: str, build_dir: Path) -> Dict[str, Any]:
    p = Path(build_dir) / "games" / game_id / "manifest.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"id": game_id, "name": game_id}


def list_games(build_dir: Path) -> List[Dict[str, Any]]:
    out = []
    root = Path(build_dir) / "games"
    if not root.is_dir():
        return out
    for d in sorted(root.iterdir()):
        if not (d / "engine.py").exists():
            continue
        m = load_manifest(d.name, build_dir)
        out.append({"id": d.name, "name": m.get("name", d.name),
                    "description": m.get("description", "")})
    return out
