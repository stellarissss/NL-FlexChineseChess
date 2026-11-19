"""演示棋种：同时具备「随机 + 异步 + 实时」三种自由度。

设计意图（也是给 Agent 的范例）：
  · 随机：王的「飞雷」落点由 state_rng(state, "wild") 决定——同一局面下
    提示点与执行掷出同一颗骰子，走完一步自然重掷。
  · 异步：红黑双方永远都可行动（actable_sides 返回两方），不强制轮流。
  · 实时：tick() 每秒让「浮」子随机漂移一格，不需要玩家操作。

规则很简陋，只为验证平台链路，不是产品。
"""
from __future__ import annotations

from core.engine import ActionError, GameEngine, state_rng

DIRS = [(1, 0), (-1, 0), (0, 1), (0, -1)]


class Engine(GameEngine):
    id = "demo"
    name = "随机·异步·实时演示"
    interaction = "move"
    config_files = ["board", "ui_config"]

    # ── 初始局面 ──
    def new_state(self):
        return {
            "board": {"width": 9, "height": 10},
            "pieces": [
                {"id": "r_king_1", "type": "king", "name": "王", "side": "red",
                 "position": [4, 9], "is_alive": True, "custom_properties": {}},
                {"id": "b_king_1", "type": "king", "name": "王", "side": "black",
                 "position": [4, 0], "is_alive": True, "custom_properties": {}},
                {"id": "n_drift_1", "type": "drift", "name": "浮", "side": "neutral",
                 "position": [3, 5], "is_alive": True, "custom_properties": {}},
            ],
            "current_turn": "red",
            "move_history": [],
            "game_status": {"state": "playing", "winner": None,
                            "win_condition": None, "custom_rules_active": []},
            "clock": 0.0,
        }

    # ── 工具 ──
    @staticmethod
    def _at(state, x, y):
        for p in state.get("pieces", []):
            if p.get("is_alive", True) and list(p.get("position") or []) == [x, y]:
                return p
        return None

    def _empty_cells(self, state):
        w = int(state["board"]["width"])
        h = int(state["board"]["height"])
        out = []
        for x in range(w):
            for y in range(h):
                if self._at(state, x, y) is None:
                    out.append([x, y])
        return out

    def _wild(self, state, side):
        """随机跳点：由局面内容决定（查询与执行一致）。"""
        cells = self._empty_cells(state)
        if not cells:
            return None
        return state_rng(state, f"wild:{side}").choice(cells)

    # ── 异步：双方永远可行动 ──
    def actable_sides(self, state):
        if (state.get("game_status") or {}).get("state") == "ended":
            return []
        return ["red", "black"]

    # ── 合法动作 ──
    def legal_actions(self, state, piece_id=None, side=None):
        sides = [side] if side else self.actable_sides(state)
        out = []
        for s in sides:
            for p in state.get("pieces", []):
                if not p.get("is_alive", True) or p.get("side") != s:
                    continue
                if piece_id and p["id"] != piece_id:
                    continue
                px, py = p["position"]
                for dx, dy in DIRS:
                    nx, ny = px + dx, py + dy
                    if not (0 <= nx < state["board"]["width"]
                            and 0 <= ny < state["board"]["height"]):
                        continue
                    occ = self._at(state, nx, ny)
                    if occ is not None and occ.get("side") == s:
                        continue
                    out.append({"type": "move", "piece_id": p["id"], "to": [nx, ny]})
                wild = self._wild(state, s)
                if wild and wild not in [a["to"] for a in out if a["piece_id"] == p["id"]]:
                    out.append({"type": "move", "piece_id": p["id"], "to": list(wild)})
        return out

    # ── 执行 ──
    def apply_action(self, state, action):
        if (state.get("game_status") or {}).get("state") == "ended":
            raise ActionError("游戏已结束")
        side = action.get("side")
        if side not in self.actable_sides(state):
            raise ActionError("该方当前不可行动")
        pid = action.get("piece_id")
        to = list(action.get("to") or [])
        if not pid or len(to) != 2:
            raise ActionError("动作参数不完整")
        piece = next((p for p in state["pieces"]
                      if p["id"] == pid and p.get("is_alive", True)), None)
        if not piece or piece["side"] != side:
            raise ActionError("不是该方棋子")
        px, py = piece["position"]
        in_board = (0 <= to[0] < state["board"]["width"]
                    and 0 <= to[1] < state["board"]["height"])
        adj = in_board and any([px + dx, py + dy] == to for dx, dy in DIRS)
        wild = self._wild(state, side)
        if not (adj or (wild and list(wild) == to)):
            raise ActionError("非法移动")
        target = self._at(state, to[0], to[1])
        if target is not None and target["side"] == side:
            raise ActionError("不能吃自己的子")
        piece["position"] = to
        if target is not None:
            target["is_alive"] = False
            if target.get("type") == "king":
                self._end(state, side)
        state.setdefault("move_history", []).append(
            {"piece_id": pid, "from": [px, py], "to": to,
             "captured": target["id"] if target else None})
        return state

    # ── 实时：每秒漂移 ──
    def tick(self, state, now=None):
        drift = next((p for p in state.get("pieces", [])
                      if p.get("is_alive", True) and p.get("type") == "drift"), None)
        if drift is None:
            return False
        x, y = drift["position"]
        cands = []
        for dx, dy in DIRS:
            nx, ny = x + dx, y + dy
            if 0 <= nx < state["board"]["width"] and 0 <= ny < state["board"]["height"]:
                if self._at(state, nx, ny) is None:
                    cands.append([nx, ny])
        if not cands:
            return False
        drift["position"] = state_rng(state, "drift").choice(cands)
        state["clock"] = float(now or 0)
        return True

    @staticmethod
    def _end(state, winner):
        state["game_status"] = {"state": "ended", "winner": winner,
                                "win_condition": "king_captured",
                                "custom_rules_active": []}

    def ai_action(self, state, side):
        acts = self.legal_actions(state, side=side)
        return acts[0] if acts else None

    def should_ai_move(self, state, side):
        return side != "red"

    def is_player_controlled(self, state, side):
        return side == "red"

    def meta(self):
        m = super().meta()
        m["name"] = self.name
        m["interaction"] = "move"
        return m
