"""初始布局热并入（adopt_initial_layout）验证：改摆子不该把正在下的棋清掉。

跑法（服务器上）：
  cd /opt/chess-demo && CHESS_RUNTIME_DIR=/tmp/ctest-rt python tools/test_adopt_layout.py <build_id>
"""
import copy
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

build_id = sys.argv[1] if len(sys.argv) > 1 else ""
rt = Path(os.environ.get("CHESS_RUNTIME_DIR") or "/tmp/ctest-rt")
rt.mkdir(parents=True, exist_ok=True)
(rt / "active.json").write_text(json.dumps({"build": build_id, "game": "xiangqi", "ts": 0}))

from core.state import GameSession  # noqa: E402
from agent.workspace import Build    # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("  PASS " if cond else "  FAIL ") + name + (f"  {extra}" if extra else ""))
    if not cond:
        fails.append(name)


s = GameSession()
s.reset()
eng = s.engine
st = s.state

# ── 1. 走成中局，并制造阵亡棋子 ──
for _ in range(30):
    side = st.get("current_turn")
    a = eng.ai_action(st, side)
    if not a:
        break
    a = dict(a)
    a.setdefault("side", side)
    try:
        eng.apply_action(st, a)
    except Exception:
        break
s.save()

dead_before = sorted(p["id"] for p in st["pieces"] if p.get("is_alive") is False)
alive_pos_before = {p["id"]: list(p["position"]) for p in st["pieces"] if p.get("is_alive") is not False}
print(f"中局：棋谱 {len(st.get('move_history') or [])} 步，阵亡 {len(dead_before)} 子 {dead_before}")

# ── 2. 模拟 Agent「再给我五个傻」：只往初始布局里加 10 枚 ──
init = copy.deepcopy(s.configs["initial_board_state"])
occupied_xy = {(p["position"][0], p["position"][1]) for p in st["pieces"] if p.get("is_alive") is not False}
free = [(x, y) for y in range(10) for x in range(9) if (x, y) not in occupied_xy]
for i in range(5):
    init["pieces"].append({"id": f"r_silly_{i+1}", "type": "soldier", "side": "red",
                           "position": list(free[i]), "name": "傻"})
    init["pieces"].append({"id": f"b_silly_{i+1}", "type": "soldier", "side": "black",
                           "position": list(free[i + 5]), "name": "傻"})
s.configs["initial_board_state"] = init

res = s.adopt_initial_layout()
print("adopt:", res)
check("并入成功", res.get("ok") is True, str(res))
check("新增 10 枚", res.get("added") == 10, f"added={res.get('added')}")
check("未撤子", res.get("removed") == 0)
check("棋谱保留", len(s.state.get("move_history") or []) == len(st.get("move_history") or []))
check("回合保留", s.state.get("current_turn") == st.get("current_turn"))

pos_after = {p["id"]: list(p["position"]) for p in s.state["pieces"] if p.get("is_alive") is not False}
moved = [pid for pid, pos in alive_pos_before.items() if pos_after.get(pid) != pos]
check("既有棋子位置一个没动", not moved, str(moved[:5]))

dead_after = sorted(p["id"] for p in s.state["pieces"] if p.get("is_alive") is False)
check("阵亡的子没有被复活", dead_after == dead_before, f"{dead_before} -> {dead_after}")

alive_xy = [(p["position"][0], p["position"][1]) for p in s.state["pieces"] if p.get("is_alive") is not False]
check("没有两子叠在同一格", len(alive_xy) == len(set(alive_xy)))
check("棋子总数与开局一致", len(s.state["pieces"]) == len(init["pieces"]),
      f"{len(s.state['pieces'])} vs {len(init['pieces'])}")

# ── 3. 从初始布局里撤掉 2 枚（AI 减子）：局中部该跟着减 ──
init2 = copy.deepcopy(init)
init2["pieces"] = [p for p in init2["pieces"] if p["id"] not in ("r_silly_1", "b_silly_1")]
s.configs["initial_board_state"] = init2
res2 = s.adopt_initial_layout()
print("adopt(减子):", res2)
check("减子并入成功", res2.get("ok") is True and res2.get("removed") == 2, str(res2))
check("减子后总数正确", len(s.state["pieces"]) == len(init2["pieces"]))

# ── 4. 新棋子落点被占：必须就近安置而不是叠上去 ──
init3 = copy.deepcopy(init2)
occupant = next(p for p in s.state["pieces"] if p.get("is_alive") is not False and p["id"] != "r_king_1")
init3["pieces"].append({"id": "r_silly_9", "type": "soldier", "side": "red",
                        "position": list(occupant["position"]), "name": "傻"})
s.configs["initial_board_state"] = init3
res3 = s.adopt_initial_layout()
print("adopt(占位冲突):", res3)
alive_xy = [(p["position"][0], p["position"][1]) for p in s.state["pieces"] if p.get("is_alive") is not False]
check("冲突时成功并入", res3.get("ok") is True, str(res3))
check("冲突时就近安置", res3.get("moved", 0) >= 1, f"moved={res3.get('moved')}")
check("冲突后仍无叠子", len(alive_xy) == len(set(alive_xy)))

# ── 5. 「改摆子」与「改规则」必须分开判定 ──
b = Build.__new__(Build)
for files, want_rules, want_layout, label in [
    (["games/xiangqi/configs/initial/board_state.json"], False, True, "只改初始布局"),
    (["games/xiangqi/configs/rules.json"], True, False, "改规则"),
    (["games/xiangqi/engine.py"], True, False, "改引擎"),
    (["games/xiangqi/configs/pieces_red.json"], True, False, "改棋子定义"),
]:
    b.changed_files = (lambda fs: (lambda: fs))(files)
    check(f"{label}：判定正确", b.touches_rules() is want_rules
          and b.touches_initial_layout() is want_layout,
          f"rules={b.touches_rules()} layout={b.touches_initial_layout()}")

print("\n结果：", "全部通过" if not fails else f"{len(fails)} 项失败 -> {fails}")
sys.exit(1 if fails else 0)
