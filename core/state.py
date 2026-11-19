"""对局会话：配置 + 状态 + 引擎实例的唯一持有者。"""
from __future__ import annotations

import copy
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

from core import paths
from core.engine import GameEngine, load_engine, load_manifest, list_games

_STATE_LOCK = threading.RLock()

DEFAULT_CONFIG_FILES = ["board", "pieces_red", "pieces_black", "rules", "ui_config"]


class GameSession:
    def __init__(self):
        self.build_dir: Path = paths.active_build_dir()
        self.game_id: str = paths.active_game_id()
        self.manifest: Dict[str, Any] = {}
        self.configs: Dict[str, Any] = {}
        self.config_files = list(DEFAULT_CONFIG_FILES)
        self.engine: Optional[GameEngine] = None
        self.state: Dict[str, Any] = {}
        # 局面版本号：每次落盘自增，前端轮询据此判断「有没有被实时机制推进」
        self.state_rev: int = 0
        self.last_activity: float = time.time()
        self.last_tick: float = 0.0
        self.last_save: float = 0.0
        self.reload(full=True)

    # ── 载入 ──
    def reload(self, full: bool = True):
        """重新加载引擎与配置。full=True 时同时重建对局状态（换棋种）。"""
        with _STATE_LOCK:
            self.build_dir = paths.active_build_dir()
            self.game_id = paths.active_game_id()
            self.manifest = load_manifest(self.game_id, self.build_dir)
            self.configs, self.config_files = self._read_configs(self.game_id, self.build_dir)
            self.engine = load_engine(self.game_id, self.build_dir, self.configs)
            if full or not self.state:
                self.state = self._read_state()
            else:
                self.state = self._read_state()
            self.state_rev += 1
            self.last_tick = 0.0
            self.last_activity = time.time()

    def _game_dir(self, game_id: str, build_dir: Path) -> Path:
        return Path(build_dir) / "games" / game_id

    def _read_configs(self, game_id: str, build_dir: Path):
        cfg_dir = self._game_dir(game_id, build_dir) / "configs"
        files = list(DEFAULT_CONFIG_FILES)
        mf = self._game_dir(game_id, build_dir) / "manifest.json"
        if mf.exists():
            try:
                m = json.loads(mf.read_text(encoding="utf-8"))
                if isinstance(m.get("config_files"), list) and m["config_files"]:
                    files = list(m["config_files"])
            except Exception:
                pass

        data: Dict[str, Any] = {}
        for name in files:
            p = cfg_dir / f"{name}.json"
            if p.exists():
                try:
                    data[name] = json.loads(p.read_text(encoding="utf-8"))
                except Exception as e:
                    print(f"[config] 解析失败 {p}: {e}")
        # 初始局面
        init = cfg_dir / "initial" / "board_state.json"
        if init.exists():
            try:
                data["initial_board_state"] = json.loads(init.read_text(encoding="utf-8"))
            except Exception as e:
                print(f"[config] 初始局面解析失败: {e}")
        return data, files

    def _state_path(self) -> Path:
        d = paths.STATE_DIR / self.game_id
        d.mkdir(parents=True, exist_ok=True)
        return d / "board_state.json"

    def _read_state(self) -> Dict[str, Any]:
        p = self._state_path()
        if p.exists():
            try:
                st = json.loads(p.read_text(encoding="utf-8"))
                if self.engine and isinstance(st, dict) and "pieces" in st:
                    return self.sync_piece_labels(st)
            except Exception:
                pass
        st = self.engine.new_state() if self.engine else {}
        st = self.sync_piece_labels(st)
        self._write_state(st)
        return st

    def sync_piece_labels(self, st: Dict[str, Any]) -> Dict[str, Any]:
        """用当前配置里的 label 刷新棋子显示名。

        开局文件的 name 是写死的快照（比如「車」），Agent 把 label 改成「神」之后，
        若只改配置不同步，棋盘上还会显示旧名字 —— 用户会认为「改了没生效」。
        """
        try:
            for p in st.get("pieces") or []:
                side = p.get("side")
                ptype = p.get("type")
                cfg = self.configs.get(f"pieces_{side}") or {}
                entry = (cfg.get("pieces") or {}).get(ptype)
                if not entry:
                    for cp in cfg.get("custom_pieces") or []:
                        if cp.get("type") == ptype:
                            entry = cp
                            break
                label = (entry or {}).get("label")
                if label:
                    p["name"] = label
        except Exception:
            pass
        return st

    def _write_state(self, st: Dict[str, Any]):
        p = self._state_path()
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, p)

    # ── 保存 / 重置 ──
    def save(self, min_interval: float = 0.0):
        """推进版本号并落盘。min_interval 用于实时 tick 的高频写入节流。

        版本号每次都加（前端据此感知变化），落盘可以节流——进程崩了最多丢
        min_interval 秒的实时推进，不影响已确认的落子。
        """
        with _STATE_LOCK:
            now = time.time()
            self.state_rev += 1
            if min_interval and (now - self.last_save) < min_interval:
                return
            self._write_state(self.state)
            self.last_save = now

    def reset(self):
        with _STATE_LOCK:
            self.state = self.engine.new_state() if self.engine else {}
            self.sync_piece_labels(self.state)
            self._write_state(self.state)
            self.state_rev += 1
            return self.state

    # ── 初始布局热并入（Agent 只改摆子时，不重开对局） ──
    @staticmethod
    def _nearest_free(occupied, x: int, y: int, width: int, height: int):
        """从 (x,y) 向外找最近的空格：新棋子按开局位置摆，该格有人了就让位。"""
        from collections import deque
        seen = {(x, y)}
        q = deque([(x, y)])
        while q:
            cx, cy = q.popleft()
            if 0 <= cx < width and 0 <= cy < height and (cx, cy) not in occupied:
                return (cx, cy)
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                n = (cx + dx, cy + dy)
                if n not in seen and -2 <= n[0] < width + 2 and -2 <= n[1] < height + 2:
                    seen.add(n)
                    q.append(n)
        return None

    def adopt_initial_layout(self) -> Dict[str, Any]:
        """把新构建的「初始布局」并入正在进行的对局，而不是整盘重开。

        规则（2026-11-01 作弊语义）：两边都有的存活棋子，位置直接采用新初始
        布局的位置——对战过程中改布局即见即所得；阵亡棋子不回魂且占格；
        新开局才有的棋子按初始位置补进来，该格已被占就就近找空格；
        新开局里没有的棋子撤掉。棋谱/回合/胜负一律保留。

        实现：先纯计划（不动任何对象），noop 判定通过后才统一落位——
        否则提前 return 会以引用语义污染当前局面。

        返回 {"ok": bool, "added": int, "removed": int, "moved": int,
              "repositioned": int, "noop": bool, "reason": str}；
        ok=False 时调用方应退回 reset()（例如旧局面已损坏）。
        """
        with _STATE_LOCK:
            if not self.engine:
                return {"ok": False, "reason": "引擎未就绪"}
            try:
                tpl_state = self.engine.new_state() or {}
            except Exception as e:
                return {"ok": False, "reason": f"新开局生成失败：{e}"}
            cur = self.state if isinstance(self.state, dict) else {}
            if not isinstance(cur.get("pieces"), list) or not cur["pieces"]:
                return {"ok": False, "reason": "当前局面没有棋子列表"}
            tpl_pieces = [p for p in (tpl_state.get("pieces") or [])
                          if isinstance(p, dict) and p.get("id")]
            if not tpl_pieces:
                return {"ok": False, "reason": "新开局没有棋子"}
            tpl = {p["id"]: p for p in tpl_pieces}
            live = {p["id"]: p for p in cur["pieces"] if isinstance(p, dict) and p.get("id")}

            geom = (self.configs.get("board") or {}).get("geometry") or {}
            width = int(geom.get("width") or tpl_state.get("width") or 9)
            height = int(geom.get("height") or tpl_state.get("height") or 10)

            removed = [pid for pid in live if pid not in tpl]

            # ── 阶段 A：纯计划（存活棋子的新位置），不改任何对象 ──
            death_spots: set = set()
            for pid, p in live.items():
                if pid in tpl and p.get("is_alive") is False:
                    pos = p.get("position") or []
                    if len(pos) == 2:
                        death_spots.add((int(pos[0]), int(pos[1])))

            plan: list = []            # (piece, new_pos)
            repositioned = 0
            moved = 0
            occupied = set(death_spots)
            for pid, p in live.items():
                if pid not in tpl or p.get("is_alive") is False:
                    continue
                tpos = tpl[pid].get("position") or []
                old = p.get("position") or []
                if len(tpos) != 2:
                    continue
                nx, ny = int(tpos[0]), int(tpos[1])
                if (nx, ny) in occupied:
                    spot = self._nearest_free(occupied, nx, ny, width, height)
                    if spot is None:
                        return {"ok": False, "reason": "新初始位置被占且无处安放"}
                    nx, ny = spot
                    moved += 1
                elif len(old) == 2 and (nx, ny) != (int(old[0]), int(old[1])):
                    repositioned += 1
                plan.append((p, [nx, ny]))
                occupied.add((nx, ny))

            # ── 阶段 B：新增棋子计划 ──
            added = 0
            new_pieces: list = []
            for pid, p in tpl.items():
                if pid in live:
                    continue
                np_ = copy.deepcopy(p)
                pos = np_.get("position") or [0, 0]
                x, y = int(pos[0]), int(pos[1])
                if (x, y) in occupied or not (0 <= x < width and 0 <= y < height):
                    spot = self._nearest_free(occupied, x, y, width, height)
                    if spot is None:
                        return {"ok": False, "reason": "棋盘已满，新棋子无处安放"}
                    x, y = spot
                    moved += 1
                np_["position"] = [x, y]
                np_["is_alive"] = True
                occupied.add((x, y))
                new_pieces.append(np_)
                added += 1

            if added == 0 and not removed and repositioned == 0 and moved == 0:
                return {"ok": True, "added": 0, "removed": 0, "moved": 0,
                        "repositioned": 0, "noop": True, "reason": ""}

            # ── 阶段 C：落位（此时才写对象）──
            for p, pos in plan:
                p["position"] = pos
            merged = [p for pid, p in live.items() if pid in tpl]
            merged.extend(new_pieces)

            # 兜底自检：并入后必须与开局一一对应，否则宁可重开也不留半个坏局面
            ids = [p.get("id") for p in merged]
            if len(set(ids)) != len(ids) or len(merged) != len(tpl):
                return {"ok": False, "reason": "并入后棋子清单与开局对不上"}

            # 棋谱/回合/胜负原地保留，新开局新增的机制键（如随机目标）补进来
            cur["pieces"] = merged
            for k, v in tpl_state.items():
                if k in ("pieces", "move_history", "current_turn", "game_status"):
                    continue
                if k not in cur:
                    cur[k] = copy.deepcopy(v)

            self.sync_piece_labels(cur)
            self._write_state(cur)
            self.state_rev += 1
            return {"ok": True, "added": added, "removed": len(removed),
                    "moved": moved, "repositioned": repositioned,
                    "noop": False, "reason": ""}

    # ── 发布热并入（2026-11-07 架构决策：任何改动都不重开对局）──
    @staticmethod
    def _merge_decl_list(cur_list: list, new_list: list) -> list:
        """机制声明列表按 side 合并：以新声明为准，但保留当前回合的剩余步数。

        不保留的话，发布会把玩家正在进行的「连走两步」打断（remaining 归零或
        被新声明里缺失的 remaining 字段抹掉 —— 正是 2026-11-07 /chess1 实案）。
        """
        out = []
        cur_by_side = {x.get('side'): x for x in cur_list if isinstance(x, dict)}
        for item in new_list:
            if not isinstance(item, dict):
                out.append(copy.deepcopy(item))
                continue
            merged = copy.deepcopy(item)
            old = cur_by_side.get(item.get('side'))
            if old:
                for prog in ('remaining', 'remaining_moves'):
                    if prog in old:
                        merged[prog] = old[prog]
                lim = merged.get('limit')
                if isinstance(lim, int):
                    for prog in ('remaining', 'remaining_moves'):
                        if isinstance(merged.get(prog), int) and merged[prog] > lim:
                            merged[prog] = lim
            out.append(merged)
        return out

    def hot_merge(self, *, layout_changed: bool, rules_changed: bool) -> Dict[str, Any]:
        """发布统一热并入入口：任何改动都不重开对局。

        重开对局只允许两条途径：某方胜利、玩家手动（/api/restart、
        /api/reset_configs、/api/rpg/reset_battle）。Agent 发布一律走这里。

        - layout_changed：initial/board_state.json 变了才调 adopt_initial_layout()
          做摆子热并入（按作弊语义更新存活棋子位置）。**绝不能**在其它改动下
          调用它，否则玩家已走动的棋子会被瞬间拉回开局位。
        - rules_changed：engine.py / rules.json / pieces_*.json / board.json 变了
          -> 只把新引擎/新配置引入的结构性 state 字段增量并入，绝不动
            棋子位置、棋谱、回合、胜负。

        引擎热替换与标签同步由 session.reload(full=False) 完成（它已从磁盘
        读回当前对局并 sync_piece_labels），本方法只补结构性差异。

        返回摘要 dict 供发布日志展示；任何失败也保住当前对局，不抛异常。
        """
        with _STATE_LOCK:                       # RLock，可安全嵌套 adopt 的锁
            out: Dict[str, Any] = {"ok": True, "layout": None, "config": None}
            cur = self.state if isinstance(self.state, dict) else {}

            # ① 摆子改动：仅 initial 真变了时才动棋子位置
            if layout_changed:
                try:
                    out["layout"] = self.adopt_initial_layout()
                except Exception as e:
                    out["layout"] = {"ok": False, "reason": str(e)}

            # ② 结构字段热并入：对任何发布都做，幂等且不动对局数据
            try:
                out["config"] = self._hot_merge_config(
                    merge_mechanisms=rules_changed)
            except Exception as e:
                out["config"] = {"ok": False, "reason": str(e)}
                out["ok"] = False

            self.sync_piece_labels(cur)
            self._write_state(cur)
            self.state_rev += 1
            return out

    def _hot_merge_config(self, *, merge_mechanisms: bool) -> Dict[str, Any]:
        """把新构建 new_state() 引入的结构性字段并入当前对局。

        铁律：只增补/更新「声明类」字段，绝不覆盖对局数据
        （pieces / current_turn / move_history / game_status / history /
         _metadata 一律不碰）。mechanisms 仅在规则类改动时按新声明合并，
        且保留当前回合进度与 cur 独有的运行时键（如随机目标）。
        """
        if not self.engine:
            return {"ok": False, "reason": "引擎未就绪"}
        cur = self.state if isinstance(self.state, dict) else {}
        try:
            tpl = self.engine.new_state() or {}
        except Exception as e:
            return {"ok": False, "reason": f"新开局生成失败：{e}"}

        protected = {"pieces", "current_turn", "move_history", "game_status",
                     "history", "_metadata"}
        added: list = []
        updated: list = []
        for k, v in tpl.items():
            if k in protected:
                continue
            if k not in cur:
                cur[k] = copy.deepcopy(v)
                added.append(k)
                continue
            if k == "mechanisms" and isinstance(v, dict) and isinstance(cur.get(k), dict):
                if not merge_mechanisms:
                    continue        # 只改摆子：机制保持原样，不打断当前回合
                for mk, mv in v.items():
                    if mk not in cur[k]:
                        cur[k][mk] = copy.deepcopy(mv)
                        updated.append("mechanisms.+" + mk)
                    elif isinstance(mv, list) and isinstance(cur[k][mk], list):
                        cur[k][mk] = self._merge_decl_list(cur[k][mk], mv)
                        updated.append("mechanisms.~" + mk)
                    elif isinstance(mv, dict) and isinstance(cur[k][mk], dict):
                        blended = copy.deepcopy(cur[k][mk])
                        blended.update(copy.deepcopy(mv))
                        cur[k][mk] = blended
                        updated.append("mechanisms.~" + mk)
                    else:
                        cur[k][mk] = copy.deepcopy(mv)
                        updated.append("mechanisms.~" + mk)
                # 注：新声明里取消的机制（如 move_limits=[]）由 _merge_decl_list
                # 按 side 合并时自然清空；此处不做主动删除，避免误删对局里
                # 由运行时注入的机制条目（保留对局数据优先）。
            elif k == "board" and isinstance(v, dict) and isinstance(cur.get(k), dict):
                # 棋盘几何改动要生效；按键合并，保留旧字段，棋子坐标不动
                blended = copy.deepcopy(cur[k])
                blended.update(copy.deepcopy(v))
                cur[k] = blended
                updated.append("board")
        return {"ok": True, "added": added, "updated": updated,
                "protected": sorted(protected)}

    # ── 能力与可行动方（异步 / 实时契约） ──
    def capabilities(self) -> Dict[str, Any]:
        """合并 manifest 声明与引擎自述，供前端与自检消费。

        manifest 里的 capabilities 优先；引擎实现 capabilities() 可兜底。
        未知键原样透传（前端/自检不认识的忽略即可，不做硬校验）。
        """
        caps: Dict[str, Any] = {}
        try:
            fn = getattr(self.engine, "capabilities", None)
            if fn:
                caps.update(fn() or {})
        except Exception:
            pass
        m = (self.manifest or {}).get("capabilities")
        if isinstance(m, dict):
            caps.update(m)
        rt = caps.get("realtime")
        if isinstance(rt, (int, float)):
            rt = {"tick_hz": float(rt)}
        if isinstance(rt, dict):
            try:
                hz = float(rt.get("tick_hz") or 1)
            except Exception:
                hz = 1.0
            rt = dict(rt)
            rt["tick_hz"] = max(0.2, min(hz, 10.0))
        elif rt is not None:
            rt = {"tick_hz": 1.0}
        caps["realtime"] = rt
        caps.setdefault("randomness", "state")
        caps["async_actions"] = bool(caps.get("async_actions"))
        return caps

    def actable_sides(self) -> list:
        """当前可行动的一方（可多个）。引擎没实现时才退化为严格轮流。

        2026-11-08 架构解耦：引擎实现了 actable_sides 就以它的返回值为
        唯一口径，**包括空列表** —— 空表示对局结束或本回合行动点用尽，
        不该被这里的 fallback 又填回 [current_turn]。旧实现把空结果兜回
        单值，正是「回合制写死」在驱动层的最后一处残留：引擎说没人能动，
        驱动层却告诉前端还有人可动，AI 看门狗就会一直空转。
        """
        fn = getattr(self.engine, "actable_sides", None)
        if fn is None:
            cur = self.state.get("current_turn")
            return [cur] if cur else []
        try:
            return [s for s in (fn(self.state) or []) if isinstance(s, str) and s]
        except Exception:
            cur = self.state.get("current_turn")
            return [cur] if cur else []

    def ai_sides(self) -> list:
        """可行动方里由 AI 接手的那几方（前端据此决定是否自动应招）。

        严格轮流的棋种通常返回空：AI 回合由 current_turn 推出即可；
        异步/多方同时可动的棋种必须靠这个字段，否则 AI 侧永远轮不到。
        """
        fn = getattr(self.engine, "should_ai_move", None)
        if not fn:
            return []
        out = []
        for s in self.actable_sides():
            try:
                if fn(self.state, s):
                    out.append(s)
            except Exception:
                continue
        return out

    def touch(self):
        """记录「还有人在看这局」，实时 tick 用它决定是否空转。"""
        self.last_activity = time.time()

    # ── 配置热更新（Agent 走的是构建发布，这里供人工/接口微调） ──
    def patch_config(self, name: str, patch_ops: list) -> Dict[str, Any]:
        from core.json_patch_utils import apply_patch
        with _STATE_LOCK:
            if name not in self.configs:
                raise KeyError(f"配置不存在: {name}")
            new_cfg = apply_patch(copy.deepcopy(self.configs[name]), patch_ops)
            self.configs[name] = new_cfg
            self._save_config(name, new_cfg)
            self.engine = load_engine(self.game_id, self.build_dir, self.configs)
            return new_cfg

    def save_config(self, name: str):
        if name in self.configs:
            self._save_config(name, self.configs[name])

    def _save_config(self, name: str, data: Dict[str, Any]):
        p = self._game_dir(self.game_id, self.build_dir) / "configs" / f"{name}.json"
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, p)

    # ── 元信息 ──
    def meta(self) -> Dict[str, Any]:
        m = dict(self.manifest or {})
        try:
            m.update(self.engine.meta() if self.engine else {})
        except Exception:
            pass
        m.setdefault("id", self.game_id)
        m.setdefault("name", "无限制棋局")
        m["build"] = str(self.build_dir.name)
        m["games"] = list_games(self.build_dir)
        geom = (self.configs.get("board") or {}).get("geometry") or {}
        m.setdefault("width", int(geom.get("width") or 9))
        m.setdefault("height", int(geom.get("height") or 10))

        # 玩家执哪一方：以引擎真实开局方为准（落子类玩家先手；走子类默认红方）
        rules = self.configs.get("rules") or {}
        interaction = getattr(self.engine, "interaction", "move")
        first_side = "red"
        try:
            first_side = (self.engine.new_state() or {}).get("current_turn") or "red"
        except Exception:
            first_side = (self.configs.get("initial_board_state") or {}).get("current_turn") or "red"
        if rules.get("player_controls_both") or rules.get("human_side") == "both":
            m["human_side"] = "both"   # 前端 v2.3.5+ 按 both 放行双侧选子
        else:
            m["human_side"] = rules.get("human_side") or (
                first_side if interaction == "place" else "red")
        m["first_side"] = first_side
        return m


SESSION = GameSession()
