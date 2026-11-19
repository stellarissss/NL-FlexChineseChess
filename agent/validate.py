"""构建校验：语法 → 引擎自检 → 静态标记 → HTTP 冒烟。"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.workspace import Build


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _run(cmd: List[str], cwd: Path, timeout: float = 90) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout)


def check_python(build: Build) -> List[str]:
    errs = []
    for p in build.root.rglob("*.py"):
        if "__pycache__" in p.parts or "runtime" in p.parts:
            continue
        r = _run([sys.executable, "-m", "py_compile", str(p)], build.root, timeout=30)
        if r.returncode != 0:
            msg = (r.stderr or "").strip().splitlines()
            errs.append(f"{p.relative_to(build.root)}: " + (msg[-1] if msg else "语法错误"))
    return errs


def check_configs(build: Build, game_id: str) -> List[str]:
    """棋盘配置的常识检查：单位错误会让棋盘糊成一片。"""
    errs: List[str] = []
    gdir = build.root / "games" / game_id / "configs"
    bp = gdir / "board.json"
    if not bp.exists():
        return errs
    try:
        b = json.loads(bp.read_text(encoding="utf-8"))
    except Exception as e:
        return [f"board.json 解析失败: {e}"]
    geo = b.get("geometry") or {}
    app = b.get("appearance") or {}
    w, h = int(geo.get("width") or 0), int(geo.get("height") or 0)
    if not (2 <= w <= 40 and 2 <= h <= 40):
        errs.append(f"棋盘尺寸异常: {w}×{h}（应在 2~40 之间）")
    lt = float((app.get("grid") or {}).get("line_thickness") or 0)
    if lt > 0.25:
        errs.append(f"grid.line_thickness={lt} 过大：单位是「格」，常用 0.02~0.05，"
                    f"1 表示一整格，会糊满棋盘")
    pt = float((app.get("palace") or {}).get("line_thickness") or 0)
    if pt > 0.25:
        errs.append(f"palace.line_thickness={pt} 过大：单位是「格」，常用 0.02~0.05")
    border = (app.get("decorations") or {}).get("border") or {}
    if border.get("enabled") and float(border.get("thickness") or 0) > 0.25:
        errs.append(f"decorations.border.thickness={border.get('thickness')} 过大：单位是「格」，常用 0.05~0.15")
    bg, lc = app.get("background_color"), app.get("line_color")
    if bg and lc and str(bg).lower() == str(lc).lower():
        errs.append("棋盘底色与线条颜色相同，网格会看不见")
    errs += _check_unknown_keys(b)
    return errs


# 前端真正消费的 appearance 字段；写别的字段不会报错但也不会生效（静默失效），
# 所以在校验期直接拦下，逼 Agent 改用 games/<id>/web/extension.js。
APPEARANCE_SCHEMA: Dict[str, Any] = {
    "background_color": None,
    "line_color": None,
    "palace_line_color": None,
    "grid": {"line_thickness", "show_horizontal", "show_vertical", "river_gap"},
    "palace": {"enabled", "show_diagonals", "line_thickness"},
    "river": {"enabled", "text", "text_size", "gap_ratio"},
    "layout": {"viewbox_padding_left", "viewbox_padding_right",
               "viewbox_padding_top", "viewbox_padding_bottom", "board_size"},
    "decorations": {"border", "star_points", "custom_lines", "background_pattern"},
    "border": {"enabled", "thickness", "color"},
}


CAPABILITY_KEYS = {"randomness", "async_actions", "realtime"}


def check_piece_definitions(build: Build, game_id: str) -> List[str]:
    """棋子定义完整性：初始局面里有的兵种，本方配置里必须有走法。

    典型事故（2026-10-31 /chess1「把帅改成神」）：Agent 整文件重写
    pieces_red.json 时只留下被改的 general、advisor，其余五种棋子定义被抹掉。
    棋盘上 16 枚红子还在，但一步也走不了；而开局签名按「兵种合计」统计时，
    红方的 0 被黑方同类棋子的走法盖住，校验看不出异常，一路发布上线。

    这里按「阵营 + 兵种」逐项核对：缺定义 = 硬错误；moves 为空 = 提示性错误。
    """
    errs: List[str] = []
    gdir = build.root / "games" / game_id / "configs"
    side_files = {s: gdir / f"pieces_{s}.json" for s in ("red", "black")}
    if not any(p.exists() for p in side_files.values()):
        return errs                      # 不是「按棋子定义走法」的棋种，跳过
    init = gdir / "initial" / "board_state.json"
    if not init.exists():
        init = gdir / "board_state.json"
    if not init.exists():
        return errs
    try:
        state = json.loads(init.read_text(encoding="utf-8"))
    except Exception as e:
        return [f"初始局面 {init.name} 解析失败: {e}"]

    by_side: Dict[str, set] = {}
    for p in (state.get("pieces") or []):
        if not isinstance(p, dict) or p.get("is_alive") is False:
            continue
        side, ptype = p.get("side"), p.get("type")
        if side and ptype:
            by_side.setdefault(str(side), set()).add(str(ptype))

    for side, types in sorted(by_side.items()):
        path = side_files.get(side)
        if path is None:
            continue
        if not path.exists():
            errs.append(f"{side} 方在初始局面里有棋子，但没有 {path.name}")
            continue
        try:
            conf = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            errs.append(f"{path.name} 解析失败: {e}")
            continue
        defs = conf.get("pieces") or {}
        customs = {}
        for c in (conf.get("custom_pieces") or []):
            if isinstance(c, dict) and c.get("type"):
                customs[str(c["type"])] = c
        for ptype in sorted(types):
            d = defs.get(ptype) or customs.get(ptype)
            if not isinstance(d, dict):
                errs.append(
                    f"{side} 方棋子「{ptype}」在 {path.name} 里没有定义："
                    f"棋盘上还摆着这种棋子，但它一步也走不了。"
                    f"整文件重写 pieces_*.json 时必须保留其它兵种的 moves 定义。"
                )
                continue
            mv = d.get("moves")
            if not isinstance(mv, list) or not mv:
                errs.append(f"{side} 方棋子「{ptype}」的 moves 为空，开局即是废子")
    return errs


def check_rules_contract(build: Build, game_id: str) -> List[str]:
    """rules.json 平台契约：human_side 只允许 "red"/"black"。

    典型事故（2026-11-01 /chess1「让我来控制对方」）：Agent 为表达「玩家控制
    双方」写了 human_side: "both"。引擎侧 _both_controlled() 认识它，但前端
    _isSidePlayerControlled 按 `side === playerSide` 单值比较，"both" 永远不
    等于 red/black —— 玩家点任何棋子都选不中，AI 又被 both 短路不走子，
    整盘死锁且控制台零报错。

    「控制双方」的正确写法是 rules.player_controls_both: true（引擎已内置）。
    """
    errs: List[str] = []
    p = build.root / "games" / game_id / "configs" / "rules.json"
    if not p.exists():
        return errs
    try:
        rules = json.loads(p.read_text(encoding="utf-8")) or {}
    except Exception as e:
        return [f"rules.json 解析失败: {e}"]
    hs = rules.get("human_side")
    if hs is not None and hs not in ("red", "black", "both"):
        errs.append(
            f'rules.human_side="{hs}" 不是合法值（允许 "red"/"black"/"both"）。'
            f'要表达「玩家控制双方」可写 human_side: "both" 或 rules.player_controls_both: true；'
            f'其它值会让前端无法识别任何一方、玩家点不动棋子（死锁）。')
    return errs


def check_mechanism_contract(build: Build, game_id: str) -> List[str]:
    """机制条目契约 + 作用域一致性（2026-11-07 /chess1 实案）。

    两类静默失效，静态校验原先完全看不见：
      · AI 注入 move_limits 条目时只写 remaining_moves，而机制引擎
        _find_active_mechanism 按 item["remaining"] != 0 判激活 →
        条目永远「未激活」，回合照常切换，功能 0% 生效、控制台零报错。
      · 指令「让我一回合动两次」（单方）被实现成红黑双方都注入，
        作用域放大一倍，等于需求没实现。
    """
    errs: List[str] = []
    gdir = build.root / "games" / game_id
    ip = gdir / "configs" / "initial" / "board_state.json"
    rp = gdir / "configs" / "rules.json"
    if not ip.exists():
        return errs
    try:
        init = json.loads(ip.read_text(encoding="utf-8"))
    except Exception as e:
        return [f"initial/board_state.json 解析失败: {e}"]
    limits = ((init.get("mechanisms") or {}).get("move_limits")) or []
    if not isinstance(limits, list) or not limits:
        return errs

    # 机制引擎是否已兼容 remaining_moves（避免误伤已正确改造的构建）。
    # 只看 _find_active_mechanism 的函数体：文件其它地方（post_move 记账）
    # 本来就含这个词，用整文件判断会误放行「筛选器没改」的坏构建。
    mech_py = gdir / "mechanism_engine.py"
    compat = False
    if mech_py.exists():
        try:
            src = mech_py.read_text(encoding="utf-8")
            i = src.find("def _find_active_mechanism")
            if i >= 0:
                j = src.find("\n    def ", i + 10)
                body = src[i:(j if j > 0 else i + 1200)]
                compat = "remaining_moves" in body
        except Exception:
            compat = False
    for it in limits:
        if not isinstance(it, dict) or "limit" not in it:
            continue
        if "remaining" not in it and not compat:
            errs.append(
                f'move_limits 条目(side={it.get("side")}) 缺 remaining 字段：'
                f'机制引擎 _find_active_mechanism 按它判激活，只写 remaining_moves '
                f'会让机制永远不触发（配置在、校验过、玩家一步就换边）。'
                f'补 remaining，或让机制引擎兼容 remaining_moves。')
            break

    # 作用域一致性：rules.moves_per_turn_sides 与初始局面里的 side 集合必须一致
    if rp.exists():
        try:
            rules = json.loads(rp.read_text(encoding="utf-8"))
        except Exception:
            rules = {}
        declared = rules.get("moves_per_turn_sides")
        if isinstance(declared, list) and declared:
            actual = sorted({x.get("side") for x in limits
                             if isinstance(x, dict) and x.get("side")})
            if actual and sorted(declared) != actual:
                errs.append(
                    f'作用域不一致：rules.moves_per_turn_sides={sorted(declared)}，'
                    f'但初始局面 move_limits 作用于 {actual}。'
                    f'「让我 / 我方」是单方需求，别把双方都写进去。')
    return errs


def check_turn_rhythm_contract(build: Build, game_id: str) -> List[str]:
    """回合节奏契约（2026-11-08 /chess1 实案）。

    三类静默失效，普通语法/配置校验完全看不见：
      · 随机步数预算只影响「是否换手」，不参与 apply_action 落子门禁 →
        allowed=0 时仍有几十个合法走法能落子，0 步架空形同虚设。
      · 掷骰散落在 roll / consume / ai_action 多处，current_turn 与预算
        不同步 → 玩家被连续架空、AI 一回合无限连走、对局突然结束。
      · _find_active_mechanism 被回退成只认 remaining → 老机制的
        remaining_moves 条目永远判未激活（2026-11-07 已修，禁止回退）。
    """
    errs: List[str] = []
    gdir = build.root / "games" / game_id
    ep = gdir / "engine.py"
    mp = gdir / "mechanism_engine.py"
    rp = gdir / "configs" / "rules.json"
    if not ep.exists():
        return errs
    try:
        esrc = ep.read_text(encoding="utf-8")
    except Exception:
        return errs

    # ① remaining_moves 兼容禁止回退（无论当前有没有 move_limits 条目）
    if mp.exists():
        try:
            import re
            msrc = mp.read_text(encoding="utf-8")
            i = msrc.find("def _find_active_mechanism")
            if i >= 0:
                j = msrc.find("\n    def ", i + 10)
                body = msrc[i:(j if j > 0 else i + 1200)]
                code = "\n".join(ln.split("#")[0] for ln in body.splitlines())
                # 必须是真的取值表达式：只在 docstring / 注释里提到不算兼容，
                # 否则「把兼容代码删了、说明文字还在」也能骗过校验。
                uses = re.search(
                    r"""\.get\(\s*['"]remaining_moves['"]|\[\s*['"]remaining_moves['"]\s*\]""",
                    code)
                if not uses:
                    errs.append(
                        "mechanism_engine._find_active_mechanism 未兼容 remaining_moves："
                        "move_limits 按 remaining_moves 记账时会永远判未激活"
                        "（2026-11-07 /chess1 实案，禁止把兼容逻辑改回去）。")
        except Exception:
            pass

    # ② 启用随机/变步数时，引擎必须实现回合调度契约
    enabled = False
    if rp.exists():
        try:
            r = json.loads(rp.read_text(encoding="utf-8"))
            enabled = (bool((r.get("random_steps") or {}).get("enabled"))
                       or int(r.get("moves_per_turn") or 1) > 1)
        except Exception:
            enabled = False
    if not enabled:
        return errs

    if "def _begin_turn" not in esrc:
        errs.append("启用变步数机制但引擎没有 _begin_turn 回合开始事件：掷骰会散落在"
                    "多处，current_turn 与行动点预算必然不同步。")
    if "def actable_sides" not in esrc:
        errs.append("启用变步数机制但引擎没有实现 actable_sides()：驱动层会退化成"
                    "[current_turn] 单值，AI 连走与 0 步架空无法表达。")
    def _code_only(block: str) -> str:
        # 只看代码行：注释/说明里提到函数名不算真的调用（否则正常构建被误拦）
        return "\n".join(ln.split("#")[0] for ln in block.splitlines())

    i = esrc.find("def apply_action")
    if i >= 0:
        j = esrc.find("\n    def ", i + 10)
        body = esrc[i:(j if j > 0 else i + 4000)]
        code = _code_only(body)
        if "steps_remaining" not in code and "turn_steps" not in code:
            errs.append("apply_action 里没有任何行动点门禁：随机步数只会影响"
                        "「是否换手」，掷出 0 步仍能落子，机制形同虚设。")
    i = esrc.find("def ai_action")
    if i >= 0:
        j = esrc.find("\n    def ", i + 10)
        body = esrc[i:(j if j > 0 else i + 2000)]
        if "roll_random_steps" in _code_only(body):
            errs.append("ai_action 里仍在调用 roll_random_steps：掷骰必须只发生在"
                        "_begin_turn，否则预算语义会被反复重置。")
    return errs

def check_manifest(build: Build, game_id: str) -> List[str]:
    """能力声明体检：格式写错不会拦发布，但要提醒——静默失效最难查。

    随机 / 异步 / 实时本身**不是**错误（自检只对「查询与执行不一致」硬卡），
    这里只保证声明的写法能被主循环与前端正确消费。
    """
    p = build.root / "games" / game_id / "manifest.json"
    if not p.exists():
        return []
    try:
        m = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []
    caps = m.get("capabilities")
    if caps is None:
        return []
    if not isinstance(caps, dict):
        return ["manifest.capabilities 必须是对象，"
                "例如 {\"randomness\": \"live\", \"async_actions\": true, "
                "\"realtime\": {\"tick_hz\": 1}}"]
    warns: List[str] = []
    unknown = [k for k in caps if k not in CAPABILITY_KEYS]
    if unknown:
        warns.append(f"manifest.capabilities 里的 {', '.join(unknown)} 不会被识别；"
                     f"可用：{', '.join(sorted(CAPABILITY_KEYS))}")
    rnd = caps.get("randomness")
    if rnd not in (None, "state", "frozen", "live"):
        warns.append(f"capabilities.randomness={rnd!r} 无效，可选 state（默认）/ live")
    rt = caps.get("realtime")
    if rt is not None and not isinstance(rt, (dict, bool, int, float)):
        warns.append("capabilities.realtime 应为 {\"tick_hz\": 1} 或 true")
    return warns


def _check_unknown_keys(board: Dict[str, Any]) -> List[str]:
    errs: List[str] = []
    app = board.get("appearance") or {}
    if not isinstance(app, dict):
        return errs
    for key, sub in app.items():
        if key not in APPEARANCE_SCHEMA:
            errs.append(
                f"appearance.{key} 不是核心支持的字段，前端会忽略它（改了也没效果）。"
                f"可用字段：{', '.join(k for k in APPEARANCE_SCHEMA if k != 'border')}；"
                f"需要额外视觉/动画请写 games/<id>/web/extension.js"
            )
            continue
        allowed = APPEARANCE_SCHEMA[key]
        if not isinstance(allowed, set) or not isinstance(sub, dict):
            continue
        for sk in sub:
            if sk not in allowed:
                errs.append(
                    f"appearance.{key}.{sk} 不是核心支持的字段（前端忽略）。"
                    f"{key} 下可用：{', '.join(sorted(allowed))}；"
                    f"新装饰请用 games/<id>/web/extension.js"
                )
    dec = app.get("decorations") or {}
    if isinstance(dec, dict):
        border = dec.get("border")
        if isinstance(border, dict):
            for sk in border:
                if sk not in APPEARANCE_SCHEMA["border"]:
                    errs.append(
                        f"decorations.border.{sk} 不是受支持字段（前端忽略）。"
                        f"可用：{', '.join(sorted(APPEARANCE_SCHEMA['border']))}"
                    )
    return errs


def check_engine(build: Build, game_id: str) -> Dict[str, Any]:
    try:
        r = _run([sys.executable, "tools/selftest.py", str(build.root), game_id],
                 build.root, timeout=90)
        out = (r.stdout or "").strip().splitlines()
        if not out:
            return {"ok": False, "errors": [f"自检无输出: {(r.stderr or '')[-400:]}"], "warnings": []}
        return json.loads(out[-1])
    except Exception as e:
        return {"ok": False, "errors": [f"自检执行失败: {e}"], "warnings": []}


def opening_baseline(build: Build, game_id: str,
                     timeout: float = 40) -> Optional[Dict[str, Any]]:
    """当前生效版本（发布前）的开局签名。

    用于回答一个很实际的问题：「这次改动是不是把某类棋子改成了废子」。
    只有同一棋种的上一版本来做基线，才不会误伤「这个棋种本来就有不能动的棋子」。
    """
    try:
        from core import paths
        active = Path(paths.active_build_dir())
        if active.resolve() == build.root.resolve():
            return None
        if not (active / "games" / game_id / "engine.py").exists():
            return None
        r = _run([sys.executable, "tools/selftest.py", str(active), game_id, "0"],
                 build.root, timeout=timeout)
        out = (r.stdout or "").strip().splitlines()
        if not out:
            return None
        d = json.loads(out[-1])
        if not d.get("ok"):
            return None
        s = d.get("stats") or {}
        return {"opening": s.get("opening") or {},
                "counts": s.get("piece_counts") or {},
                "opening_side": s.get("opening_side") or {},
                "counts_side": s.get("piece_counts_side") or {}}
    except Exception:
        return None


def intended_disabled_types(build: "Build", game_id: str) -> set:
    """读取玩家显式允许的「废子」兵种清单，保留自由度。

    rules.json 里声明 intended_disabled_piece_types: ["soldier"] 表示
    「让这个兵种完全不能动」是有意设计，此时归零降级为 warning 而非硬错误。
    未声明 = 无意改瘫 = 硬拦。
    """
    try:
        p = build.root / "games" / game_id / "configs" / "rules.json"
        d = json.loads(p.read_text(encoding="utf-8"))
        v = d.get("intended_disabled_piece_types") or []
        return {str(x) for x in v} if isinstance(v, list) else set()
    except Exception:
        return set()


def compare_opening(base: Optional[Dict[str, Any]],
                    stats: Dict[str, Any],
                    intended_disabled: Optional[set] = None) -> Dict[str, List[str]]:
    """旧版能动、新版完全不能动 → **发布硬错误**（2026-11-09 起）。

    2026-10-31 补：**按「阵营 + 兵种」比对**。旧实现只比对兵种合计，红方棋子
    被抹掉定义时，黑方同名棋子仍有走法，合计不为 0，异常被完全掩盖。
    2026-11-09 升级：此前这类归零只是 warning，`ok = not errors` 不拦发布，
    且 tools.py 只把 errors 回灌给 Agent，导致「体检写着病危、医院直接办出院」
    （/chess1「长江黄河」实案：红黑兵卒开局走法 5→0 仍被发布）。
    现改为：未显式声明豁免的归零 → errors（硬拦，Agent 必须修）。
    口径仍是**相对回归**：base（上一生效版本）里该兵种本来就为 0 的会跳过，
    因此不会把「历史遗留的坏构建」永久锁死，玩家仍可改回来。
    """
    errors: List[str] = []
    warns: List[str] = []
    intended = intended_disabled or set()
    out = {"errors": errors, "warnings": warns}
    if not base:
        return out

    def emit(msg: str, ptype: str):
        if ptype in intended:
            warns.append("[已声明豁免] " + msg)
        else:
            errors.append(msg)

    b_side = base.get("opening_side") or {}
    n_side = stats.get("opening_side") or {}
    b_cnt_side = base.get("counts_side") or {}
    n_cnt_side = stats.get("piece_counts_side") or {}
    if b_side and n_side and b_cnt_side and n_cnt_side:
        for side in sorted(b_side):
            for ptype, bmoves in (b_side.get(side) or {}).items():
                if bmoves <= 0:
                    continue
                if (b_cnt_side.get(side) or {}).get(ptype, 0) <= 0:
                    continue
                if (n_cnt_side.get(side) or {}).get(ptype, 0) <= 0:
                    continue
                if (n_side.get(side) or {}).get(ptype, 0) == 0:
                    emit(
                        f"{side} 方棋子「{ptype}」开局走法从 {bmoves} 变成了 0，"
                        f"而它还在棋盘上 —— 这次改动把它改瘫了，禁止发布。"
                        f"常见原因：在 rule_engine.py 里把某整行/整列/整片区域设成"
                        f"「不可停留」却没给替代落点（贴着该区域的兵种会被集体钉死），"
                        f"或 pieces_{side}.json 的 moves/sym 写错、整文件重写丢了定义。"
                        f"请给棋子一条合法的替代落点后重新 validate；若确实就是要"
                        f"「让 {ptype} 不能动」，在 rules.json 写 "
                        f'intended_disabled_piece_types: ["{ptype}"] 显式声明。',
                        ptype)
        return out

    b_open = base.get("opening") or {}
    b_cnt = base.get("counts") or {}
    n_open = stats.get("opening") or {}
    n_cnt = stats.get("piece_counts") or {}

    for ptype, bmoves in b_open.items():
        if bmoves <= 0:
            continue
        if b_cnt.get(ptype, 0) <= 0 or n_cnt.get(ptype, 0) <= 0:
            continue                      # 该棋子被删掉或改名了，不算「改坏」
        if n_open.get(ptype, 0) == 0:
            emit(
                f"棋子「{ptype}」开局走法从 {bmoves} 变成了 0 —— 这次改动把它改瘫了，"
                f"禁止发布。请检查落点过滤 / pieces_*.json 的 moves/sym / 引擎走法分支，"
                f"用 get_state 逐兵种核对后重新 validate。若确实要让 {ptype} 完全不动，"
                f'在 rules.json 写 intended_disabled_piece_types: ["{ptype}"] 显式声明。',
                ptype)
    return out


NODE_CANDIDATES = ["/opt/chess-demo/tools/node/bin/node", "node"]


def _node() -> Optional[str]:
    for c in NODE_CANDIDATES:
        p = shutil.which(c) if not c.startswith("/") else (c if Path(c).exists() else None)
        if p:
            try:
                v = subprocess.run([p, "-v"], capture_output=True, text=True, timeout=10).stdout.strip()
                major = int(v.lstrip("v").split(".")[0])
                if major >= 16:
                    return p
            except Exception:
                continue
    return None


def check_js(build: Build, files: List[str]) -> List[str]:
    node = _node()
    if not node:
        return []
    errs = []
    for rel in files:
        if not rel.endswith(".js") or rel.endswith("(deleted)"):
            continue
        p = build.root / rel
        if not p.exists():
            continue
        r = subprocess.run([node, "--check", str(p)], capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            errs.append(f"{rel}: " + (r.stderr or "").strip()[:300])
    return errs


async def check_http(build: Build) -> Dict[str, Any]:
    """真正启动一次服务，走关键接口。"""
    import httpx

    port = _free_port()
    env = dict(os.environ)
    env["CHESS_PORT"] = str(port)
    # 校验子进程必须与线上实例隔离：runtime 指向构建外临时目录，
    # 否则共用线上 active.json / state，校验进程会读写线上状态（改指针、动对局）。
    # 临时目录不能放构建根目录里：changed_files 按整树 diff，会把运行产物
    # 当成本次改动一起发布出去。
    import tempfile
    tmpdir = tempfile.mkdtemp(prefix="chess-validate-")
    env["CHESS_RUNTIME_DIR"] = tmpdir.rstrip("/") + "/runtime"
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "main.py", cwd=str(build.root), env=env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    errs: List[str] = []
    try:
        async with httpx.AsyncClient(timeout=8.0) as cli:
            ok = False
            for _ in range(60):
                await asyncio.sleep(0.25)
                try:
                    r = await cli.get(f"http://127.0.0.1:{port}/api/game/meta")
                    if r.status_code == 200:
                        ok = True
                        break
                except Exception:
                    continue
            if not ok:
                errs.append("服务启动超时（HTTP 未就绪）")
            else:
                r = await cli.get(f"http://127.0.0.1:{port}/")
                if r.status_code != 200 or "<html" not in r.text.lower():
                    errs.append(f"首页异常: HTTP {r.status_code}")
                r = await cli.get(f"http://127.0.0.1:{port}/api/config/all")
                if r.status_code != 200:
                    errs.append(f"/api/config/all 异常: HTTP {r.status_code}")
                r = await cli.post(f"http://127.0.0.1:{port}/api/valid_moves",
                                   json={"piece_id": None, "to": [0, 0]})
                if r.status_code != 200:
                    errs.append(f"/api/valid_moves 异常: HTTP {r.status_code}")
                # 状态通道：前端轮询、实时推进、异步可行动方都走它，必须可用
                r = await cli.get(f"http://127.0.0.1:{port}/api/state")
                if r.status_code != 200:
                    errs.append(f"/api/state 异常: HTTP {r.status_code}")
                else:
                    try:
                        d = r.json() or {}
                        if "state" not in d or "rev" not in d:
                            errs.append("/api/state 缺少 state/rev 字段")
                        elif not isinstance(d.get("actable_sides"), list):
                            errs.append("/api/state 缺少 actable_sides 列表")
                    except Exception as e:
                        errs.append(f"/api/state 返回非 JSON: {e}")
    finally:
        try:
            proc.terminate()
            await asyncio.wait_for(proc.wait(), timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        shutil.rmtree(tmpdir, ignore_errors=True)
    return {"ok": not errs, "errors": errs}


async def run_validation(build: Build, game_id: str, deep: bool = True,
                         deadline: Optional[float] = None) -> Dict[str, Any]:
    t0 = time.time()
    errors: List[str] = []
    warnings: List[str] = []

    errors += check_python(build)
    errors += check_configs(build, game_id)
    errors += check_piece_definitions(build, game_id)
    errors += check_rules_contract(build, game_id)
    errors += check_mechanism_contract(build, game_id)
    errors += check_turn_rhythm_contract(build, game_id)
    warnings += check_manifest(build, game_id)
    if not errors:
        eng = await asyncio.to_thread(check_engine, build, game_id)
        errors += eng.get("errors") or []
        warnings += eng.get("warnings") or []
        stats = eng.get("stats") or {}
        if not errors:
            base = await asyncio.to_thread(opening_baseline, build, game_id)
            cmp = compare_opening(base, stats,
                                  intended_disabled_types(build, game_id))
            errors += cmp["errors"]
            warnings += cmp["warnings"]
            stats["has_baseline"] = bool(base)
    else:
        stats = {}

    changed = build.changed_files()
    errors += check_js(build, changed)

    http_info = {}
    if not errors and deep:
        remain = (deadline - time.time()) if deadline else 60
        if remain > 12:
            http_info = await check_http(build)
            errors += http_info.get("errors") or []

    return {
        "ok": not errors,
        "errors": errors[:12],
        "warnings": warnings[:6],
        "stats": stats,
        "ms": int((time.time() - t0) * 1000),
    }
