#!/usr/bin/env python3
"""构建自检：引擎级随机对局 + 状态不变量 + 能力契约校验。

用法： python tools/selftest.py <build_root> [game_id] [plies]
输出： 单行 JSON {ok, errors, warnings, stats}

判定分级（关键：不因为「随机 / 异步 / 实时」驳回构建）

  硬错误（打回）
    · legal_actions 返回的动作，在同一时刻 apply_action 拒绝 —— 查询与执行
      对「合法」有分歧，玩家点到提示点必然报「非法移动」。这是唯一的硬契约。
    · 随机对局中连续非法动作、结构/坐标错误、轮到无人能走且 ai_action 不代走。
  警告（放行）
    · 同一局面两次查询结果不同（活随机）：允许，但要求前端重同步 + apply 只按
      静态规则校验；建议 manifest 声明 capabilities.randomness="live"。
    · 声明了实时却整局 tick 都没推进过状态；开局签名在多种子下抖动。
"""
from __future__ import annotations

import copy
import json
import random
import sys
import traceback
from pathlib import Path

MAX_PLIES_DEFAULT = 60
OPENING_SAMPLES = 3          # 开局签名取多个随机种子，避免随机设计下单次抖动误报
PROBE_PER_SIDE = 8           # 每方抽多少个动作做「可执行性探针」


def _norm(acts) -> list:
    """动作归一化，用于集合比较。"""
    out = []
    for a in acts or []:
        if not isinstance(a, dict):
            out.append(("?", repr(a), ()))
            continue
        out.append((a.get("type"), a.get("piece_id"), tuple(a.get("to") or ())))
    return sorted(out)


def _sample(seq: list, n: int) -> list:
    """均匀抽样（保持顺序稳定），用于控制大棋盘上的探针成本。"""
    seq = list(seq)
    if len(seq) <= n:
        return seq
    step = len(seq) / float(n)
    return [seq[int(i * step)] for i in range(n)]


def _caps(manifest: dict) -> dict:
    c = manifest.get("capabilities")
    if not isinstance(c, dict):
        c = {}
    rt = c.get("realtime")
    hz = 1.0
    if isinstance(rt, dict):
        try:
            hz = float(rt.get("tick_hz") or 1)
        except Exception:
            hz = 1.0
    elif isinstance(rt, (int, float)):
        hz = float(rt)
    return {
        "randomness": str(c.get("randomness") or "state").lower(),
        "realtime": bool(rt),
        "tick_hz": max(0.2, min(hz, 10.0)),
        "async_actions": bool(c.get("async_actions")),
        "declared": bool(c),
    }


def _actable(eng, state) -> list:
    """引擎声明的可行动方；未实现则退化为严格轮流。"""
    fn = getattr(eng, "actable_sides", None)
    sides = []
    if fn:
        try:
            sides = [s for s in (fn(state) or []) if isinstance(s, str) and s]
        except Exception:
            sides = []
    if not sides:
        cur = state.get("current_turn")
        sides = [cur] if cur else []
    return sides


def _where(state, action) -> str:
    pid = action.get("piece_id")
    to = list(action.get("to") or [])
    ptype = next((p.get("type") for p in state.get("pieces", [])
                  if p.get("id") == pid), "?")
    return f"{pid}({ptype}) → ({to[0] if to else '?'},{to[1] if len(to) > 1 else '?'})"


def _probe(eng, state, sides, caps) -> list:
    """可执行性探针：同一时刻查到的动作，必须能被 apply 接受。"""
    errs = []
    for side in sides:
        try:
            acts = eng.legal_actions(state, side=side)
        except Exception as e:
            errs.append(f"legal_actions(side={side}) 崩溃: {e}")
            continue
        if not isinstance(acts, list):
            errs.append(f"legal_actions(side={side}) 未返回 list（{type(acts).__name__}）")
            continue
        for a in _sample(acts, PROBE_PER_SIDE):
            if not isinstance(a, dict):
                errs.append(f"legal_actions 返回了非 dict 元素: {a!r}")
                break
            cp = copy.deepcopy(state)
            act = dict(a)
            act.setdefault("side", side)
            try:
                eng.apply_action(cp, act)
            except Exception as e:
                still = False
                try:
                    still = any(x.get("piece_id") == a.get("piece_id")
                                and list(x.get("to") or []) == list(a.get("to") or [])
                                for x in eng.legal_actions(state, side=side))
                except Exception:
                    pass
                if still:
                    errs.append(
                        f"{_where(state, a)} 在 legal_actions 里、apply_action 却拒绝（{e}）。"
                        "查询与执行对「合法」的判定不一致——这是唯一的硬契约。"
                        "常见原因：apply 忽略了动作里的 to 自己重选、apply 先改 state 再校验、"
                        "或 apply 重新掷骰（随机只能「选择」，不能「过滤」）。")
                else:
                    errs.append(
                        f"{_where(state, a)} 已从 legal_actions 消失，apply 也拒绝（{e}）："
                        "查询结果按当前规则就不该出现（活随机会这样，需保证 apply 只做静态校验）。")
                if len(errs) >= 4:
                    return errs
    return errs


def _probe_turn_mechanism(eng, state, configs) -> list:
    """行为探针：声明「每回合连走 N 步」就必须真的连走 N 步。

    静态校验（语法/配置/开局签名/可执行性）看不出「机制条目字段名写错
    → _find_active_mechanism 判未激活 → 回合照常切换」这类静默失效。
    2026-11-07 /chess1 实案：AI 写了 moves_per_turn=2 和 move_limits，
    validate 全绿，玩家走一步立刻换边。这里实走一步验证才算数。
    """
    errs = []
    rules = configs.get("rules") or {}
    try:
        n = int(rules.get("moves_per_turn") or 1)
    except (TypeError, ValueError):
        n = 1
    limits = ((state.get("mechanisms") or {}).get("move_limits")) or []
    if not isinstance(limits, list) or not limits:
        return errs
    for it in limits:
        if not isinstance(it, dict):
            continue
        try:
            lim = max(n, int(it.get("limit") or 1))
        except (TypeError, ValueError):
            lim = n
        if lim < 2:
            continue
        side = it.get("side")
        if side not in ("red", "black"):
            continue
        st = copy.deepcopy(state)
        st["current_turn"] = side
        try:
            acts = [a for a in eng.legal_actions(st, side=side)
                    if isinstance(a, dict) and a.get("type") == "move"]
        except Exception:
            continue
        if not acts:
            continue
        applied = False
        for cand in acts[:3]:
            a = dict(cand)
            a["side"] = side
            trial = copy.deepcopy(st)
            try:
                trial = eng.apply_action(trial, a)
            except Exception:
                continue
            st = trial
            applied = True
            break
        if not applied:
            continue
        if (st.get("game_status") or {}).get("state") == "ended":
            continue  # 恰好吃将结束，不判失败
        if st.get("current_turn") != side:
            errs.append(
                f"声明「{side} 每回合连走 {lim} 步」，但实走第 1 步就换边到 "
                f"{st.get('current_turn')}：机制条目未激活。检查 item 是否带 "
                f"remaining 字段（_find_active_mechanism 按它判激活），"
                f"以及 apply_post_move_mechanisms 是否真的把 switch_turn 置 False。")
            break
    return errs

def _probe_turn_rhythm(eng, state, configs) -> list:
    '''回合节奏探针：行动点门禁 / 换手结算 / 0 步架空（2026-11-08 /chess1 实案）。

    静态校验看不出「行动点预算只影响换手、不参与落子门禁」，也看不出
    「current_turn 与预算不同步 → 玩家被架空 / 对局突然结束」。这里用固定
    掷骰序列实走一局，逐条验证回合节奏不变量；任何一条不满足就打回构建。
    '''
    errs = []
    rs = (configs.get("rules") or {}).get("random_steps") or {}
    if not rs.get("enabled"):
        return errs
    try:
        lo, hi = int(rs.get("min", 0)), int(rs.get("max", 3))
    except (TypeError, ValueError):
        lo, hi = 0, 3

    # ① 0 步预算必须是硬门禁：提示点消失 + apply_action 拒绝
    st0 = copy.deepcopy(state)
    st0["game_status"] = {"state": "playing", "winner": None,
                          "win_condition": None, "custom_rules_active": []}
    side0 = st0.get("current_turn") or "red"
    st0["turn_steps"] = {"side": side0, "allowed": 0, "used": 0, "rolled": True}
    try:
        acts = eng.legal_actions(st0, side=side0) or []
        if acts:
            errs.append(f"掷出 0 步时 legal_actions({side0}) 仍返回 {len(acts)} 个走法："
                        "行动点预算没有成为落子门禁（前端显示可走、服务端却拒绝）")
            cand = dict(acts[0]); cand["side"] = side0
            try:
                eng.apply_action(copy.deepcopy(st0), cand)
                errs.append("掷出 0 步仍能 apply_action 落子：0 步架空语义未生效")
            except Exception:
                pass
    except Exception as e:
        errs.append(f"掷出 0 步时 legal_actions 异常: {e}")

    # ② 固定掷骰序列实走一局，检查回合节奏不变量
    seq = [1, 3, 0, 2, 1, 3, 0, 1, 2, 1]
    idx = {"i": 0}
    orig = eng.mech.roll_random_steps

    def fake_roll(bs, side):
        v = seq[min(idx["i"], len(seq) - 1)]
        idx["i"] += 1
        v = max(lo, min(hi, v))
        bs["turn_steps"] = {"side": side, "allowed": v, "used": 0, "rolled": True}
        return v

    try:
        eng.mech.roll_random_steps = fake_roll
        st = eng.new_state()
        st["game_status"] = {"state": "playing", "winner": None,
                             "win_condition": None, "custom_rules_active": []}
        before_sig, same = None, 0
        for ply in range(24):
            if (st.get("game_status") or {}).get("state") == "ended":
                break
            cur = st.get("current_turn")
            ts = st.get("turn_steps") or {}
            if ts.get("side") != cur:
                errs.append(f"第 {ply} 步：current_turn={cur} 但 turn_steps.side="
                            f"{ts.get('side')}，回合归属与预算不同步")
                break
            left = eng.steps_remaining(st, cur) if hasattr(eng, "steps_remaining") else 1
            if left <= 0:
                errs.append(f"第 {ply} 步：current_turn={cur} 却剩余 0 个行动点，"
                            "current_turn 停在了不可行动方（回合调度器失效）")
                break
            acts = [a for a in (eng.legal_actions(st, side=cur) or [])
                    if isinstance(a, dict) and a.get("type") == "move"]
            if not acts:
                break
            a = dict(acts[0]); a["side"] = cur
            try:
                eng.apply_action(st, a)
            except Exception as e:
                errs.append(f"第 {ply} 步合法走法被 apply_action 拒绝: {e}")
                break
            sig = (st.get("current_turn"), ts.get("used"),
                   len(st.get("move_history") or []))
            same = same + 1 if sig == before_sig else 0
            before_sig = sig
            if same >= 2:
                errs.append(f"第 {ply} 步：局面连续 3 次未推进，出现回合死锁")
                break
        notes = [m.get("note") for m in (st.get("move_history") or [])
                 if isinstance(m, dict) and m.get("note")]
        if not any("掷出 0 步" in (n or "") for n in notes):
            errs.append("固定序列里包含 0 步，但棋谱没有任何 0 步跳过记录")
    finally:
        eng.mech.roll_random_steps = orig
    return errs

def main() -> int:
    root = Path(sys.argv[1]).resolve()
    game_id = sys.argv[2] if len(sys.argv) > 2 else "xiangqi"
    plies = int(sys.argv[3]) if len(sys.argv) > 3 else MAX_PLIES_DEFAULT

    sys.path.insert(0, str(root))
    errors, warnings = [], []
    stats = {}

    try:
        from core.engine import load_engine, load_manifest
    except Exception as e:
        print(json.dumps({"ok": False, "errors": [f"导入 core.engine 失败: {e}"],
                          "warnings": [], "stats": {}}, ensure_ascii=False))
        return 0

    gdir = root / "games" / game_id
    manifest = load_manifest(game_id, root)
    caps = _caps(manifest)
    stats["capabilities"] = caps
    files = manifest.get("config_files") or ["board", "pieces_red", "pieces_black", "rules", "ui_config"]
    configs = {}
    for name in files:
        p = gdir / "configs" / f"{name}.json"
        if p.exists():
            try:
                configs[name] = json.loads(p.read_text(encoding="utf-8"))
            except Exception as e:
                errors.append(f"配置 {name}.json 解析失败: {e}")
    init = gdir / "configs" / "initial" / "board_state.json"
    if init.exists():
        try:
            configs["initial_board_state"] = json.loads(init.read_text(encoding="utf-8"))
        except Exception as e:
            errors.append(f"初始局面解析失败: {e}")

    if errors:
        print(json.dumps({"ok": False, "errors": errors, "warnings": warnings, "stats": stats},
                         ensure_ascii=False))
        return 0

    try:
        eng = load_engine(game_id, root, configs)
    except Exception:
        errors.append("引擎加载失败:\n" + traceback.format_exc(limit=4))
        print(json.dumps({"ok": False, "errors": errors, "warnings": warnings, "stats": stats},
                         ensure_ascii=False))
        return 0

    try:
        state = eng.new_state()
    except Exception:
        errors.append("new_state() 失败:\n" + traceback.format_exc(limit=4))
        print(json.dumps({"ok": False, "errors": errors, "warnings": warnings, "stats": stats},
                         ensure_ascii=False))
        return 0

    # ── 机制行为探针：声明连走 N 步就实走一步验证（静态校验抓不到）──
    if not errors:
        errors.extend(_probe_turn_mechanism(eng, state, configs))
    # ── 回合节奏探针：行动点门禁 / 换手结算 / 0 步架空（静态校验抓不到）──
    if not errors:
        errors.extend(_probe_turn_rhythm(eng, state, configs))

    # ── 结构不变量 ──
    if not isinstance(state, dict) or "pieces" not in state:
        errors.append("new_state() 必须返回含 pieces 的 dict")
    else:
        w = int(state.get("board", {}).get("width") or manifest.get("width") or 0)
        h = int(state.get("board", {}).get("height") or manifest.get("height") or 0)
        stats["initial_pieces"] = len(state["pieces"])
        stats["board"] = [w, h]
        if w and h:
            for p in state["pieces"]:
                pos = p.get("position") or []
                if len(pos) != 2 or not (0 <= pos[0] < w and 0 <= pos[1] < h):
                    errors.append(f"棋子 {p.get('id')} 坐标越界: {pos}")
                    break

    # ── 开局签名：每种棋子开局有多少合法走法（多种子聚合） ──
    # 这是「配置改了但引擎没生效」的照妖镜。随机设计下每个种子结果可能不同，
    # 取多次采样的最小值——某种子下为 0，说明存在「这种棋子完全动不了」的局面。
    # 同时按「阵营 + 兵种」记账：单看兵种合计会被另一方的同名棋子掩盖，
    # 出现过「红方整类棋子被抹掉定义、合计仍然不为 0」的漏检。
    opening, opening_runs, type_counts = {}, [], {}
    opening_side, runs_side, counts_side = {}, [], {}
    if not errors:
        try:
            for i in range(OPENING_SAMPLES):
                st_i = state if i == 0 else eng.new_state()
                id2type = {p.get("id"): p.get("type") for p in st_i.get("pieces", [])}
                id2side = {p.get("id"): p.get("side") for p in st_i.get("pieces", [])}
                if i == 0:
                    for p in st_i.get("pieces", []):
                        if p.get("is_alive", True):
                            t = p.get("type")
                            type_counts[t] = type_counts.get(t, 0) + 1
                            sd = p.get("side")
                            if sd:
                                d = counts_side.setdefault(sd, {})
                                d[t] = d.get(t, 0) + 1
                sides = sorted({p.get("side") for p in st_i.get("pieces", [])
                                if p.get("is_alive", True) and p.get("side")})
                run, run_side = {}, {}
                for side in sides:
                    for a in eng.legal_actions(st_i, side=side):
                        t = id2type.get(a.get("piece_id"))
                        if not t:
                            continue
                        run[t] = run.get(t, 0) + 1
                        sd = id2side.get(a.get("piece_id")) or side
                        d = run_side.setdefault(sd, {})
                        d[t] = d.get(t, 0) + 1
                opening_runs.append(run)
                runs_side.append(run_side)
            for t in type_counts:
                vals = [r.get(t, 0) for r in opening_runs]
                opening[t] = min(vals) if vals else 0
            for sd, tcnt in counts_side.items():
                for t in tcnt:
                    vals = [(r.get(sd) or {}).get(t, 0) for r in runs_side]
                    opening_side.setdefault(sd, {})[t] = min(vals) if vals else 0
            diffs = [t for t in opening if len({r.get(t, 0) for r in opening_runs}) > 1]
            if diffs:
                warnings.append(
                    f"开局签名在 {OPENING_SAMPLES} 个随机种子间抖动：{', '.join(diffs[:6])}"
                    "（随机布局/随机走法的正常表现，已取最小值统计）。")
        except Exception:
            errors.append("开局走法统计失败:\n" + traceback.format_exc(limit=4))
    stats["opening"] = opening
    stats["opening_runs"] = opening_runs
    stats["piece_counts"] = type_counts
    stats["opening_side"] = opening_side
    stats["piece_counts_side"] = counts_side

    # ── 可执行性探针（唯一的硬契约）──
    if not errors:
        sides0 = _actable(eng, state)
        if not sides0:
            errors.append("开局无任何可行动方（actable_sides 与 current_turn 都为空）")
        else:
            errors += _probe(eng, state, sides0, caps)

    # ── 稳定性：同一局面两次查询是否一致 ──
    # 不一致 = 活随机。允许运行（前端会重同步），只做提示，不驳回。
    if not errors:
        try:
            sides0 = _actable(eng, state)
            first = []
            for s in sides0:
                first += _norm(eng.legal_actions(state, side=s))
            again = []
            for s in sides0:
                again += _norm(eng.legal_actions(state, side=s))
            if first != again:
                unstable = len(set(first) ^ set(again))
                if caps["randomness"] == "live":
                    warnings.append(
                        f"活随机（已按 capabilities.randomness=live 声明放行）：同一局面两次查询"
                        f"有 {unstable} 个着法不同。前端会自动重同步，注意 apply_action 只做静态校验。")
                else:
                    warnings.append(
                        f"检测到活随机：同一局面两次查询有 {unstable} 个着法不同。本次放行。"
                        "若想让提示点完全稳定，改用 core.engine.state_rng(state, key) 取随机源"
                        "（种子随局面走，查询多少次都一样）；若确实需要每次重掷，"
                        "请在 manifest.json 声明 \"capabilities\": {\"randomness\": \"live\"}，"
                        "前端会开启重同步。")
        except Exception:
            errors.append("稳定性检查崩溃:\n" + traceback.format_exc(limit=4))

    # ── 随机对局（按可行动方驱动，支持异步 / 实时） ──
    applied, rejected, ai_used = 0, 0, 0
    tick_calls, tick_changes = 0, 0
    reject_detail = ""
    if not errors:
        try:
            span = 1.0 / max(0.2, caps["tick_hz"])
            sim_now = 0.0
            for ply in range(plies):
                if (state.get("game_status") or {}).get("state") == "ended":
                    break

                if caps["realtime"] and hasattr(eng, "tick"):
                    sim_now += span
                    tick_calls += 1
                    try:
                        try:
                            changed = bool(eng.tick(state, sim_now))
                        except TypeError:
                            changed = bool(eng.tick(state))
                        if changed:
                            tick_changes += 1
                    except Exception:
                        errors.append("tick() 崩溃:\n" + traceback.format_exc(limit=4))
                        break
                    if (state.get("game_status") or {}).get("state") == "ended":
                        break

                sides = _actable(eng, state)
                pool = []
                for s in sides:
                    for a in eng.legal_actions(state, side=s):
                        if isinstance(a, dict):
                            pool.append((s, a))
                if not pool:
                    # 「0 走法」本身可能是代走类设计；真正的坏改是「卡死」：
                    # 轮到的一方既没有可走的子，ai_action 也不代走，对局永远停在这。
                    if (state.get("game_status") or {}).get("state") == "ended":
                        break
                    sides_ai = []
                    for s in sides:
                        try:
                            cand = eng.ai_action(state, s) if hasattr(eng, "ai_action") else None
                        except Exception:
                            cand = None
                        if cand:
                            sides_ai.append((s, cand))
                    if not sides_ai:
                        errors.append(
                            f"对局卡死：可行动方 {sides or '无'} 无任何合法动作，"
                            "ai_action 也不代走（该方棋子全部动不了）。若某类棋子设计为"
                            "「玩家不可操作」，必须让 ai_action 在轮到该方时代走。")
                        break
                    s, a = random.choice(sides_ai)
                    a = dict(a)
                    a.setdefault("side", s)
                    try:
                        eng.apply_action(state, a)
                        ai_used += 1
                        applied += 1
                    except Exception as e:
                        errors.append(f"ai_action 给出的动作被 apply 拒绝（{e}）："
                                      f"{_where(state, a)}")
                        break
                    continue

                side, a = random.choice(pool)
                a = dict(a)
                a.setdefault("side", side)
                snapshot = copy.deepcopy(state)
                try:
                    eng.apply_action(state, a)
                    applied += 1
                except Exception as e:
                    rejected += 1
                    # 回滚到动作前：apply 半途抛错可能已污染 state，不回滚会让
                    # 后续着法连坐全非法，把「一次违约」放大成「连续非法动作」。
                    state.clear()
                    state.update(snapshot)
                    if not reject_detail:
                        still = sure = False
                        try:
                            still = any(x.get("piece_id") == a.get("piece_id")
                                        and list(x.get("to") or []) == list(a.get("to") or [])
                                        for x in eng.legal_actions(state, side=side))
                            sure = True
                        except Exception:
                            pass
                        if not sure:
                            reject_detail = f"legal_actions 崩溃后无法复核：{_where(state, a)}（{e}）"
                        elif still:
                            reject_detail = (
                                f"{_where(state, a)} 在 legal_actions 里、apply_action 却拒绝（{e}）。"
                                "查询与执行判定不一致：apply 不得重掷随机、不得忽略动作里的 to、"
                                "不得先改 state 再校验。")
                        else:
                            reject_detail = (
                                f"legal_actions 返回了 apply_action 不认的动作：{_where(state, a)}（{e}）。"
                                "活随机下每次查询结果会变，apply 必须按静态规则接受任一「同一时刻"
                                "查到」的动作。")
                    if rejected > 3:
                        errors.append(f"连续非法动作（{rejected} 次，均已回滚）：{reject_detail}")
                        break
            # AI 一步（若实现）
            if plies > 0 and not errors and (state.get("game_status") or {}).get("state") != "ended":
                sides = _actable(eng, state)
                side = sides[0] if sides else state.get("current_turn")
                ai = eng.ai_action(state, side) if hasattr(eng, "ai_action") else None
                if ai:
                    ai = dict(ai)
                    ai.setdefault("side", side)
                    eng.apply_action(state, ai)
                    ai_used = 1
        except Exception:
            errors.append("随机对局崩溃:\n" + traceback.format_exc(limit=6))

    stats.update({"applied": applied, "rejected": rejected, "ai_used": ai_used,
                  "history": len(state.get("move_history") or []),
                  "status": (state.get("game_status") or {}).get("state"),
                  "tick_calls": tick_calls, "tick_changes": tick_changes})
    if plies > 0 and applied == 0 and not errors and stats.get("initial_pieces", 0) > 0:
        warnings.append("随机对局未产生任何有效动作，请确认 legal_actions 是否为空")
    if caps["realtime"] and tick_calls and not tick_changes:
        warnings.append(
            f"声明了实时（realtime.tick_hz={caps['tick_hz']:g}）但 {tick_calls} 次 tick 都没有"
            "推进状态：确认 tick(state, now) 真的实现了计时/冷却/自动移动，"
            "否则前端轮询只会空转。")

    print(json.dumps({"ok": not errors, "errors": errors[:8], "warnings": warnings[:8],
                      "stats": stats}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
