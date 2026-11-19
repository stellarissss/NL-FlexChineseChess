"""象棋引擎（v2.0 适配层）。

把原有的 RuleEngine / ChessAI / MechanismEngine 包装进统一的 GameEngine
契约，行为与 v1 完全一致：吃子、将死、困毙、机制（冻结/接管/随机走棋…）。

2026-11-08 架构解耦：回合不再等于「一步 + 一次换手」。
引擎引入「行动点预算 + 显式回合开始/结算事件」三层契约：

  · _begin_turn() 是唯一的掷骰入口。掷出 0 步则该方本回合被架空、
    棋谱留痕后直接把回合交给对方（用户明确要求保留 0 步语义），
    并循环为对方重新掷骰，保证 current_turn 永远指向能行动的一方。
  · apply_action / legal_actions / actable_sides 一律以
    steps_remaining 为门禁；预算不再只是「要不要换手」的旁路开关。
  · _advance_turn() 每走一步消耗一个行动点，点数未尽则不换手、
    不结算回合；点尽才切换归属并触发 _begin_turn()。
"""
from __future__ import annotations

import copy
import random
from typing import Any, Dict, List, Optional

from core.engine import ActionError, GameEngine
from rule_engine import RuleEngine
from ai import ChessAI
from mechanism_engine import MechanismEngineBase


class Engine(GameEngine):
    id = "xiangqi"
    name = "无限制象棋"
    interaction = "move"
    config_files = ["board", "pieces_red", "pieces_black", "rules", "ui_config"]

    def __init__(self, configs: Dict[str, Any]):
        super().__init__(configs)
        self.rule_engine: Optional[RuleEngine] = None
        self.chess_ai: Optional[ChessAI] = None
        self.mech: Optional[MechanismEngineBase] = None
        self.rebuild()

    # ── 引擎装配 ──
    def rebuild(self):
        board = self.configs.get("board", {})
        pr = self.configs.get("pieces_red", {})
        pb = self.configs.get("pieces_black", {})
        rules = self.configs.get("rules", {})
        self.rule_engine = RuleEngine(board, pr, pb, rules)
        self.chess_ai = ChessAI(
            board, pr, pb, rules,
            (rules.get("ai_difficulty") or {}).get("current", "medium"),
        )
        self.mech = MechanismEngineBase(rules)
        self.mech.apply_personality_to_ai(self.chess_ai)

    def new_state(self) -> Dict[str, Any]:
        init = self.configs.get("initial_board_state")
        if init:
            st = copy.deepcopy(init)
        else:
            st = {
                "board": {"width": 9, "height": 10},
                "pieces": [],
                "current_turn": "red",
                "move_history": [],
                "game_status": {"state": "playing", "winner": None,
                                "win_condition": None, "custom_rules_active": []},
            }
        st.setdefault("board", {})
        geom = (self.configs.get("board") or {}).get("geometry") or {}
        st["board"].setdefault("width", int(geom.get("width") or 9))
        st["board"].setdefault("height", int(geom.get("height") or 10))
        st.setdefault("mechanisms", {})
        st.setdefault("move_history", [])
        # 开局掷骰不允许 0 步：否则 new_state() 的 current_turn 会被跳过
        # 逻辑翻面，core/state.meta() 与 _human_side() 用它推断先手方就会错。
        self._begin_turn(st, st.get("current_turn", "red"), allow_zero=False)
        return st

    # ── 行动点预算（回合制解耦的核心）──
    def steps_remaining(self, state: Dict[str, Any], side: str) -> int:
        """side 本回合还能走几步。未启用随机步数时恒为 1。"""
        if not self.mech:
            return 1
        return self.mech.steps_remaining(state, side)

    def actable_sides(self, state: Dict[str, Any]) -> List[str]:
        """真正可行动的一方：预算 > 0 且对局未结束。

        core/state.py 的 GameSession.actable_sides() 优先采用本方法；旧实现
        退化为 [current_turn]，把「回合」写死成单值，任何变步数机制都无法
        正确表达，驱动层与落子门禁口径也不一致（2026-11-08 /chess1 实案）。
        """
        if (state.get("game_status") or {}).get("state") == "ended":
            return []
        cur = state.get("current_turn")
        if not cur:
            return []
        if self.mech and self.mech.random_steps_for_side(cur):
            if self.mech.steps_remaining(state, cur) <= 0:
                return []
        return [cur]

    def _begin_turn(self, state: Dict[str, Any], side: str,
                    allow_zero: bool = True) -> None:
        """唯一的回合开始事件：掷本回合行动点并处理 0 步架空。

        掷出 0 步是用户明确要求的语义（「架空一回合就是正解」）：棋谱留一条
        note，回合直接交给对方并重新掷骰。循环直到找到点数 > 0 的一方，
        保证 state["current_turn"] 永远指向真正能行动的一方——这样
        ai_sides() / 前端 AI 看门狗都不会被卡在空回合上。

        allow_zero=False 仅用于 new_state()：开局重掷而不切回合、不留 note，
        避免初始局面无解且先手方被误判。
        """
        if not (self.mech and self.mech.random_steps_for_side(side)):
            return
        guard = 0
        while guard < 8:
            allowed = self.mech.roll_random_steps(state, side)
            if allowed > 0:
                return
            if not allow_zero:
                guard += 1
                continue
            state.setdefault("move_history", []).append({
                "piece_id": None, "from": None, "to": None, "captured": None,
                "note": f"{side} 本回合掷出 0 步，跳过",
            })
            side = "black" if side == "red" else "red"
            state["current_turn"] = side
            guard += 1
        # 极端兜底：连续 8 次都掷 0（(1/4)^8），强制给当前方 1 步。
        # 宁可破坏一次随机性，也不能让对局永久无人可动。
        state["turn_steps"] = {"side": side, "allowed": 1,
                              "used": 0, "rolled": True}

    # ── 合法动作 ──
    def legal_actions(self, state, piece_id=None, side=None) -> List[dict]:
        cur = state.get("current_turn")
        # 行动点门禁：本回合被 0 步架空 / 点数用尽时，提示点必须一起消失，
        # 否则前端显示可走、服务端却拒绝，玩家会以为「点不动 = 卡住」。
        if cur and self.mech and self.mech.random_steps_for_side(cur):
            if self.mech.steps_remaining(state, cur) <= 0:
                return []
        out: List[dict] = []
        for p in state.get("pieces", []):
            if not p.get("is_alive", True):
                continue
            if piece_id and p["id"] != piece_id:
                continue
            if side and p.get("side") != side:
                continue
            if piece_id and p.get("side") != cur:
                continue
            for pos in self.rule_engine.get_legal_moves(p, state):
                out.append({"type": "move", "piece_id": p["id"], "to": list(pos)})
        return out

    # ── 执行动作 ──
    def apply_action(self, state: Dict[str, Any], action: Dict[str, Any]) -> Dict[str, Any]:
        if state.get("game_status", {}).get("state") == "ended":
            raise ActionError("游戏已结束")

        side = action.get("side") or state.get("current_turn", "red")
        if side != state.get("current_turn"):
            raise ActionError("不是该方回合")

        # 行动点门禁：预算用尽 / 掷出 0 步被架空时禁止落子。
        # 旧实现完全不读 turn_steps，随机步数只影响「是否换手」，
        # 于是 allowed=0 时仍有几十个合法走法能落子，机制形同虚设。
        if self.mech and self.mech.random_steps_for_side(side):
            if self.mech.steps_remaining(state, side) <= 0:
                raise ActionError("本回合行动步数已用尽")

        if action.get("type", "move") != "move":
            raise ActionError("象棋只支持走子动作")
        pid = action.get("piece_id")
        to = list(action.get("to") or [])
        if not pid or len(to) != 2:
            raise ActionError("动作参数不完整")

        piece = next((p for p in state["pieces"] if p["id"] == pid and p.get("is_alive", True)), None)
        if not piece:
            raise ActionError("棋子不存在")
        if piece["side"] != side:
            raise ActionError("不是该方棋子")

        if to not in self.rule_engine.get_legal_moves(piece, state):
            raise ActionError("非法移动")

        from_pos = list(piece["position"])
        target = self.rule_engine._get_piece_at(to, state)
        piece["position"] = to
        if target and not self.rule_engine.is_invulnerable(target):
            target["is_alive"] = False

        state.setdefault("move_history", []).append({
            "piece_id": piece["id"],
            "from": from_pos,
            "to": to,
            "captured": target["id"] if target else None,
        })

        winner = self.rule_engine.is_general_captured(state)
        if winner:
            self._end(state, winner, "general_captured")
        else:
            self._advance_turn(state, side)
        return state

    def _advance_turn(self, state, side):
        """走完一步后的回合结算：消耗一个行动点，点尽才换手。"""
        switch_turn = True
        if self.mech:
            state, info = self.mech.apply_post_move_mechanisms(state, side)
            switch_turn = info.get("switch_turn", True)

        # ① 消耗行动点；还有剩余则保持本方回合，不换手、不结算回合
        if self.mech and self.mech.random_steps_for_side(side):
            if self.mech.consume_step(state, side) > 0:
                return

        if not switch_turn:
            return

        # ② 行动点用尽：切换归属
        nxt = "black" if side == "red" else "red"
        state["current_turn"] = nxt

        guard = 0
        while self.mech and self.mech.should_skip_turn(state, nxt) and guard < 4:
            state, _ = self.mech.apply_pre_turn_mechanisms(state, nxt)
            nxt = "black" if nxt == "red" else "red"
            state["current_turn"] = nxt
            guard += 1

        # ③ 唯一的回合开始事件：掷点数、处理 0 步架空
        self._begin_turn(state, nxt)

        # ④ 将死判定必须用结算后的真实可行动方（_begin_turn 可能跳过它）
        cur = state.get("current_turn")
        if cur and self.rule_engine.is_checkmate(cur, state):
            self._end(state, "red" if cur == "black" else "black", "checkmate")

    @staticmethod
    def _end(state, winner, reason):
        state["game_status"] = {
            "state": "ended",
            "winner": winner,
            "win_condition": reason,
            "custom_rules_active": (state.get("game_status") or {}).get("custom_rules_active", []),
        }

    # ── AI ──
    def ai_action(self, state: Dict[str, Any], side: str) -> Optional[dict]:
        # 掷骰只在 _begin_turn 发生。旧实现在这里对深拷贝快照调
        # roll_random_steps，既无副作用又让预算语义混乱，已删除。
        # 预算为 0 时抛错而不是返回 None：main.py 把 None 当成「AI 无棋可走」
        # 会直接判负结束对局，那才是真正的「卡住」。
        if self.mech and self.mech.steps_remaining(state, side) <= 0:
            raise ActionError("本回合行动步数已用尽，AI 不应手")
        if self.mech and self.mech.is_random_move_required(state, side):
            moves = []
            for p in state.get("pieces", []):
                if not p.get("is_alive", True) or p.get("side") != side:
                    continue
                for pos in self.rule_engine.get_legal_moves(p, state):
                    moves.append({"type": "move", "piece_id": p["id"], "to": list(pos), "side": side})
            return random.choice(moves) if moves else None

        move = self.chess_ai.get_best_move(state)
        if not move:
            return None
        move = dict(move)
        move.setdefault("type", "move")
        move["side"] = side
        move.pop("from", None)
        return move

    def _human_side(self) -> str:
        """玩家执哪一方（与 core/state.py 的 meta.human_side 口径一致）。

        优先 rules.human_side；否则落子类玩家执先手方、走子类默认红方。
        Agent 改造出任何棋种（黑白棋/五子棋/围棋…）时，AI 自动执另一侧，
        不会再出现「元信息说玩家执黑、引擎却让 AI 执黑」的死锁。
        """
        rules = (self.configs or {}).get("rules") or {}
        hs = rules.get("human_side")
        if hs in ("red", "black"):
            return hs
        interaction = getattr(self, "interaction", "move")
        try:
            first = (self.new_state() or {}).get("current_turn") or "red"
        except Exception:
            first = ((self.configs or {}).get("initial_board_state") or {}).get("current_turn") or "red"
        return first if interaction == "place" else "red"

    def should_ai_move(self, state: Dict[str, Any], side: str) -> bool:
        if not self.mech:
            return side != self._human_side()
        if self.mech.is_ai_controlled(state, side):
            return True
        return not self.mech.is_player_controlled(state, side)

    def is_player_controlled(self, state, side) -> bool:
        return bool(self.mech and self.mech.is_player_controlled(state, side))

    def mechanisms_summary(self, state) -> List[str]:
        return self.mech.get_active_mechanisms_summary(state) if self.mech else []

    def meta(self) -> Dict[str, Any]:
        m = super().meta()
        m["name"] = "无限制象棋"
        m["interaction"] = "move"
        return m
