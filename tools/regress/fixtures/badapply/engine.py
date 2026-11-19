"""反例 1：legal_actions 用全局 random（活随机），apply 却按严格规则拒绝。

预期：自检报「硬错误」——查询给出的动作执行时被拒（提示点点了必报错）。
"""
from __future__ import annotations

import random

from core.engine import ActionError, GameEngine

DIRS = [(1, 0), (-1, 0), (0, 1), (0, -1)]


class Engine(GameEngine):
    id = "badapply"
    name = "反例：查询与执行不一致"
    interaction = "move"
    config_files = ["board", "ui_config"]

    def new_state(self):
        return {
            "board": {"width": 9, "height": 10},
            "pieces": [
                {"id": "r_king_1", "type": "king", "name": "王", "side": "red",
                 "position": [4, 9], "is_alive": True, "custom_properties": {}},
                {"id": "b_king_1", "type": "king", "name": "王", "side": "black",
                 "position": [4, 0], "is_alive": True, "custom_properties": {}},
            ],
            "current_turn": "red",
            "move_history": [],
            "game_status": {"state": "playing", "winner": None,
                            "win_condition": None, "custom_rules_active": []},
        }

    @staticmethod
    def _at(state, x, y):
        for p in state.get("pieces", []):
            if p.get("is_alive", True) and list(p.get("position") or []) == [x, y]:
                return p
        return None

    def legal_actions(self, state, piece_id=None, side=None):
        s = side or state.get("current_turn")
        out = []
        for p in state.get("pieces", []):
            if not p.get("is_alive", True) or p.get("side") != s:
                continue
            px, py = p["position"]
            for dx, dy in DIRS:
                nx, ny = px + dx, py + dy
                if 0 <= nx < 9 and 0 <= ny < 10:
                    out.append({"type": "move", "piece_id": p["id"], "to": [nx, ny]})
            # 每次查询都用全局 random 抽 4 个远点——查询与执行不可能一致
            for _ in range(4):
                out.append({"type": "move", "piece_id": p["id"],
                            "to": [random.randrange(9), random.randrange(10)]})
        return out

    def apply_action(self, state, action):
        side = action.get("side") or state.get("current_turn")
        pid = action.get("piece_id")
        to = list(action.get("to") or [])
        piece = next((p for p in state["pieces"] if p["id"] == pid), None)
        if not piece:
            raise ActionError("棋子不存在")
        px, py = piece["position"]
        if any([px + dx, py + dy] == to for dx, dy in DIRS):
            piece["position"] = to
            state["current_turn"] = "black" if side == "red" else "red"
            state.setdefault("move_history", []).append(
                {"piece_id": pid, "from": [px, py], "to": to, "captured": None})
            return state
        raise ActionError("非法移动")
