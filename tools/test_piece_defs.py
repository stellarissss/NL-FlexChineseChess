"""棋子定义完整性校验的回归测试。

背景（2026-10-31 /chess1「把帅改成神」）：Agent 整文件重写 pieces_red.json 时
只留下被改的 general / advisor，其余五种红方棋子定义被抹掉。棋盘上 16 枚红子
还在，但一步也走不了；开局签名按「兵种合计」统计时被黑方同类棋子掩盖，校验放行。

用法：/opt/chess-demo/venv/bin/python tools/test_piece_defs.py
"""
import copy
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.workspace import Build
from agent.validate import check_piece_definitions

MOVES_RAY = {"kind": "ray", "dir": [1, 0], "max": -1, "screens": 0, "land": "any", "sym": "rotate4"}
MOVES_JUMP = {"kind": "jump", "to": [0, 1], "block": [], "land": "any", "sym": "rotate4"}

FULL_RED = {
    "_metadata": {"version": "3.0"},
    "side": "red",
    "pieces": {
        "general": {"label": "帥", "is_king": True, "moves": [MOVES_JUMP]},
        "advisor": {"label": "仕", "moves": [MOVES_JUMP]},
        "elephant": {"label": "相", "moves": [MOVES_JUMP]},
        "horse": {"label": "馬", "moves": [MOVES_JUMP]},
        "chariot": {"label": "車", "moves": [MOVES_RAY]},
        "cannon": {"label": "炮", "moves": [MOVES_RAY]},
        "soldier": {"label": "兵", "moves": [MOVES_JUMP]},
    },
    "custom_pieces": [],
}

TRUNCATED_RED = {
    "_metadata": {"version": "3.1"},
    "side": "red",
    "pieces": {
        "general": {"label": "神", "is_king": True, "invulnerable": True,
                    "moves": [{"kind": "ray", "dir": [1, 0], "max": 99,
                               "screens": 0, "land": "any", "sym": "rotate8"}]},
        "advisor": {"label": "仕", "moves": [MOVES_JUMP]},
    },
}

BOARD_STATE = {
    "pieces": [
        {"id": "r_general", "type": "general", "side": "red", "position": [4, 9], "is_alive": True},
        {"id": "r_advisor_1", "type": "advisor", "side": "red", "position": [3, 9], "is_alive": True},
        {"id": "r_chariot_1", "type": "chariot", "side": "red", "position": [0, 9], "is_alive": True},
        {"id": "r_horse_1", "type": "horse", "side": "red", "position": [1, 9], "is_alive": True},
        {"id": "r_elephant_1", "type": "elephant", "side": "red", "position": [2, 9], "is_alive": True},
        {"id": "r_cannon_1", "type": "cannon", "side": "red", "position": [1, 7], "is_alive": True},
        {"id": "r_soldier_1", "type": "soldier", "side": "red", "position": [0, 6], "is_alive": True},
        {"id": "b_general", "type": "general", "side": "black", "position": [4, 0], "is_alive": True},
        {"id": "b_chariot_1", "type": "chariot", "side": "black", "position": [0, 0], "is_alive": True},
    ]
}

passed = 0
failed = []


def check(name, cond, detail=""):
    global passed
    if cond:
        passed += 1
        print(f"  ok  {name}")
    else:
        failed.append(name)
        print(f"  FAIL {name} {detail}")


def build_dir(tmp: Path, red: dict, black: dict = None, state: dict = None,
              drop_black: bool = False) -> Build:
    root = tmp / "build"
    cfg = root / "games" / "xiangqi" / "configs"
    (cfg / "initial").mkdir(parents=True, exist_ok=True)
    (cfg / "pieces_red.json").write_text(json.dumps(red, ensure_ascii=False), encoding="utf-8")
    if not drop_black:
        b = black if black is not None else copy.deepcopy(FULL_RED)
        b["side"] = "black"
        (cfg / "pieces_black.json").write_text(json.dumps(b, ensure_ascii=False), encoding="utf-8")
    (cfg / "initial" / "board_state.json").write_text(
        json.dumps(state if state is not None else BOARD_STATE, ensure_ascii=False), encoding="utf-8")
    return Build("test", root)


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)

        # 1. 完整配置 → 无错误
        errs = check_piece_definitions(build_dir(tmp / "a", copy.deepcopy(FULL_RED)), "xiangqi")
        check("完整配置无报错", errs == [], errs)

        # 2. 被截断的配置（本次事故现场）→ 五种棋子逐个报缺失
        errs = check_piece_definitions(build_dir(tmp / "b", copy.deepcopy(TRUNCATED_RED)), "xiangqi")
        check("截断配置报错", bool(errs), errs)
        for t in ("chariot", "horse", "elephant", "cannon", "soldier"):
            check(f"报出缺失兵种 {t}", any(t in e for e in errs), errs)
        check("不误报 general/advisor", not any("「general」" in e or "「advisor」" in e for e in errs), errs)

        # 3. 定义了但 moves 为空 → 报「废子」
        empty = copy.deepcopy(FULL_RED)
        empty["pieces"]["cannon"]["moves"] = []
        errs = check_piece_definitions(build_dir(tmp / "c", empty), "xiangqi")
        check("moves 为空报错", any("cannon" in e and "moves" in e for e in errs), errs)

        # 4. 只靠 custom_pieces 定义也算数
        custom = copy.deepcopy(TRUNCATED_RED)
        custom["custom_pieces"] = [
            {"type": t, "label": t, "moves": [MOVES_JUMP]}
            for t in ("chariot", "horse", "elephant", "cannon", "soldier")
        ]
        errs = check_piece_definitions(build_dir(tmp / "d", custom), "xiangqi")
        check("custom_pieces 兜底通过", errs == [], errs)

        # 5. 黑方配置缺失但棋盘上有黑子 → 报错（不静默）
        errs = check_piece_definitions(
            build_dir(tmp / "e", copy.deepcopy(FULL_RED), drop_black=True), "xiangqi")
        check("黑方配置缺失报错", any("pieces_black.json" in e for e in errs), errs)

        # 6. 非棋子定义型棋种（没有 pieces_*.json）→ 直接跳过
        root = tmp / "f" / "build"
        (root / "games" / "weiqi" / "configs" / "initial").mkdir(parents=True)
        (root / "games" / "weiqi" / "configs" / "initial" / "board_state.json").write_text(
            json.dumps(BOARD_STATE, ensure_ascii=False), encoding="utf-8")
        errs = check_piece_definitions(Build("test", root), "weiqi")
        check("非棋子型棋种跳过", errs == [], errs)

        # 7. 阵亡棋子不算「棋盘上还有」
        dead = copy.deepcopy(BOARD_STATE)
        for p in dead["pieces"]:
            if p["type"] == "cannon":
                p["is_alive"] = False
        errs = check_piece_definitions(
            build_dir(tmp / "g", copy.deepcopy(TRUNCATED_RED), state=dead), "xiangqi")
        check("阵亡棋子不参与检查", not any("cannon" in e for e in errs), errs)

    print(f"\n{passed} passed, {len(failed)} failed")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
