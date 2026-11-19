"""NL-FlexChineseChess · 免鉴权单用户版 FastAPI 入口。

这是面向本地 / 自建服务器的精简发行版：
- 去掉了原线上多实例部署的「大厅主页 + 访客令牌 + IP 独占座位 + 门禁页」机制；
- 保留全部核心：棋局引擎、活动规则/机制、AI Agent 现场改棋规、事件流与诊断。

设计约定（Single Source of Truth）：
- 代码（可被 Agent 热改写）位于应用根目录；Agent 在 staging 构建目录修改，
  经 validate 验收后通过 active.json 指针热发布，即时生效。
- 对局状态位于 runtime/state/，与代码分离，发布不丢局面。
- DeepSeek 密钥只从环境变量 DEEPSEEK_API_KEY 或 config.json 读取，仓库不含任何密钥。
"""
from __future__ import annotations

import asyncio
import json
import mimetypes
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

APP_ROOT = Path(__file__).resolve().parent
for _p in (APP_ROOT, APP_ROOT / "core"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               PlainTextResponse, StreamingResponse)
from pydantic import BaseModel

from core import paths
from core.client_diag import CLIENT_DIAG
from core.engine import ActionError
from core.meter import METER
from core.state import SESSION

APP_VERSION = "1.0.0"

app = FastAPI(title="NL-FlexChineseChess", version=APP_VERSION)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)


def _now_iso() -> str:
    from datetime import datetime
    d = datetime.now()
    return d.strftime("%Y-%m-%dT%H:%M:%S.") + f"{d.microsecond // 1000:03d}"


async def _json_body(request: Request):
    """只读小体积 JSON 请求体用于诊断（Starlette 会缓存，下游仍可解析）。"""
    if "json" not in (request.headers.get("content-type") or ""):
        return None
    try:
        raw = await request.body()
        if not raw:
            return None
        if len(raw) > 8192:
            return {"_truncated_bytes": len(raw)}
        return json.loads(raw)
    except Exception as e:
        return {"_parse_error": str(e)}


@app.middleware("http")
async def diag_middleware(request: Request, call_next):
    """纯诊断中间件：无令牌、无门禁，只记录异常与耗时到前端诊断日志。"""
    if request.method == "OPTIONS":
        return await call_next(request)
    ip = request.client.host if request.client else ""
    t0 = time.perf_counter()
    try:
        return await call_next(request)
    except Exception as e:
        import traceback
        await asyncio.to_thread(CLIENT_DIAG.write, ip, {
            "t": _now_iso(), "kind": "crash", "method": request.method,
            "path": request.url.path, "error": f"{type(e).__name__}: {e}",
            "trace": traceback.format_exc()[-3000:],
        })
        raise


@app.on_event("startup")
async def _on_startup():
    asyncio.create_task(_realtime_loop())


# ═══════════════════════════════════════════════════════════
# 实时机制主循环
#
# 引擎实现 tick(state, now) 并在 manifest 声明 capabilities.realtime 后，
# 这里按 tick_hz 驱动它（冷却、计时、自动移动…）。两道门控避免空转：
#   1) 只有声明了实时能力的引擎才驱动；
#   2) 最近 15 分钟无人访问就不推进（轮询 /api/state 会保持活跃）。
# 落盘按 2s 节流：实时推进丢两秒无所谓，别把磁盘写爆。
# ═══════════════════════════════════════════════════════════

IDLE_TICK_WINDOW = 900.0
SAVE_THROTTLE = 2.0


def _tick_once() -> bool:
    """驱动一次实时推进；返回是否真的改了局面。"""
    caps = SESSION.capabilities()
    rt = caps.get("realtime")
    if not rt:
        return False
    if (SESSION.state.get("game_status") or {}).get("state") == "ended":
        return False
    now = time.time()
    if now - SESSION.last_activity > IDLE_TICK_WINDOW:
        return False
    try:
        hz = float((rt or {}).get("tick_hz") or 1.0)
    except Exception:
        hz = 1.0
    interval = 1.0 / max(0.2, min(hz, 10.0))
    if now - SESSION.last_tick < interval:
        return False
    SESSION.last_tick = now
    fn = getattr(SESSION.engine, "tick", None)
    if not fn:
        return False
    try:
        try:
            changed = bool(fn(SESSION.state, now))
        except TypeError:
            changed = bool(fn(SESSION.state))
    except Exception:
        import traceback
        traceback.print_exc()
        return False
    if changed:
        SESSION.save(min_interval=SAVE_THROTTLE)
    return changed


async def _realtime_loop():
    while True:
        try:
            await asyncio.sleep(0.25)
            _tick_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            import traceback
            traceback.print_exc()
            await asyncio.sleep(1.0)


def _active_root() -> Path:
    return SESSION.build_dir


@app.post("/api/client_log")
async def client_log(request: Request):
    """前端诊断上报：无令牌、无门禁，写入本地诊断日志，失败也不影响请求。"""
    ip = request.client.host if request.client else ""
    try:
        payload = await request.json()
        if not isinstance(payload, dict):
            payload = {"value": payload}
    except Exception:
        raw = await request.body()
        payload = {"raw": raw[:600].decode("utf-8", "replace")}
    payload.pop("t", None)
    entry = {"t": _now_iso(), "kind": "client", "ip": ip,
             "game": SESSION.game_id, "build": str(SESSION.build_dir.name)}
    entry.update(payload)
    await asyncio.to_thread(CLIENT_DIAG.write, ip, entry)
    return {"success": True}


# ═══════════════════════════════════════════════════════════
# 静态资源与核心棋局 API（与线上服务完全一致的真源）
# ═══════════════════════════════════════════════════════════

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    p = _active_root() / "static" / "index.html"
    if not p.exists():
        p = APP_ROOT / "static" / "index.html"
    html = p.read_text(encoding="utf-8")
    html = html.replace("{{VERSION}}", APP_VERSION)
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@app.get("/index.html", response_class=HTMLResponse)
async def index_alias(request: Request):
    return await index(request)


@app.get("/static/{path:path}")
async def static_file(path: str):
    # 入口页只走 "/"（那里会注入版本号）。/static/index.html 直接请求会绕过
    # 版本注入，且算出来的 api-base 可能不对，因此不单独提供。
    if path.lower().endswith((".html", ".htm")):
        return PlainTextResponse("not found", status_code=404)
    root = (_active_root() / "static").resolve()
    target = (root / path).resolve()
    if not str(target).startswith(str(root)) or not target.exists():
        target = (APP_ROOT / "static" / path).resolve()
    if not target.exists() or not target.is_file():
        return PlainTextResponse("not found", status_code=404)
    ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
    return FileResponse(target, media_type=ctype,
                        headers={"Cache-Control": "no-store, max-age=0"})


@app.get("/api/game/web/{name}")
async def game_web(name: str):
    """游戏自带的前端扩展（js/css），由 Agent 生成。"""
    base = (_active_root() / "games" / SESSION.game_id / "web").resolve()
    target = (base / name).resolve()
    if not str(target).startswith(str(base)) or not target.exists():
        return PlainTextResponse("", status_code=200,
                                 headers={"Content-Type": "text/plain; charset=utf-8"})
    ctype = mimetypes.guess_type(str(target))[0] or "text/plain"
    return FileResponse(target, media_type=ctype,
                        headers={"Cache-Control": "no-store"})


# ═══════════════════════════════════════════════════════════
# 数据模型
# ═══════════════════════════════════════════════════════════

class MoveRequest(BaseModel):
    piece_id: Optional[str] = None
    to: Optional[List[int]] = None
    type: str = "move"
    # 异步/多方同时可动时，客户端可显式声明是哪一方在动；缺省按当前回合
    side: Optional[str] = None


class DifficultyRequest(BaseModel):
    difficulty: str


class StopMechanismRequest(BaseModel):
    mechanism_type: str
    side: str


class SetApiKey(BaseModel):
    api_key: str


class AgentCommand(BaseModel):
    command: str


# ═══════════════════════════════════════════════════════════
# 棋局 API
# ═══════════════════════════════════════════════════════════

@app.get("/api/game/meta")
async def game_meta():
    return {"success": True, "meta": SESSION.meta(), "version": APP_VERSION,
            "interaction": getattr(SESSION.engine, "interaction", "move"),
            "capabilities": SESSION.capabilities(),
            "actable_sides": SESSION.actable_sides(),
            "ai_sides": SESSION.ai_sides(), "rev": SESSION.state_rev}


@app.get("/api/config/all")
async def get_all_configs():
    SESSION.touch()
    data = dict(SESSION.configs)
    data["board_state"] = SESSION.state
    data["capabilities"] = SESSION.capabilities()
    data["actable_sides"] = SESSION.actable_sides()
    data["ai_sides"] = SESSION.ai_sides()
    data["rev"] = SESSION.state_rev
    return data


@app.get("/api/config/{name}")
async def get_config(name: str):
    if name not in SESSION.configs:
        return JSONResponse({"error": "无效的配置名"}, status_code=400)
    return SESSION.configs[name]


@app.get("/api/state")
async def get_state():
    SESSION.touch()
    return {"success": True, "state": SESSION.state,
            "mechanisms": _mechanisms_summary(),
            "rev": SESSION.state_rev,
            "actable_sides": SESSION.actable_sides(),
            "ai_sides": SESSION.ai_sides(),
            "capabilities": SESSION.capabilities()}


@app.get("/api/check_status")
async def get_check_status():
    """将军状态：被将军的一方 -> True。只做提示，不做走子拦截。

    自由度模式下送将禁手已关闭，走子不受将军约束；
    此接口供前端把被将军一方的将/帅标红，解将后自动恢复。
    """
    SESSION.touch()
    in_check: Dict[str, bool] = {}
    fn = getattr(getattr(SESSION, "engine", None), "rule_engine", None)
    fn = getattr(fn, "is_in_check", None)
    if fn:
        for side in sorted({p.get("side") for p in (SESSION.state.get("pieces") or [])
                            if p.get("side")}):
            try:
                in_check[side] = bool(fn(side, SESSION.state))
            except Exception:
                in_check[side] = False
    return {"success": True, "in_check": in_check, "rev": SESSION.state_rev}



def _mechanisms_summary() -> List[str]:
    fn = getattr(SESSION.engine, "mechanisms_summary", None)
    try:
        return fn(SESSION.state) if fn else []
    except Exception:
        return []


@app.post("/api/move")
async def make_move(req: MoveRequest):
    eng = SESSION.engine
    st = SESSION.state
    SESSION.touch()
    turn = st.get("current_turn", "red")

    if (st.get("game_status") or {}).get("state") == "ended":
        return {"success": False, "message": "游戏已结束"}

    # 谁在动：异步规则下可行动方可能不止一个，优先采用客户端声明且被引擎认可的一方
    sides = SESSION.actable_sides()
    side = req.side if req.side in sides else (turn if turn in sides else (sides[0] if sides else turn))

    is_player = getattr(eng, "is_player_controlled", None)
    if is_player and not is_player(st, side):
        if getattr(eng, "should_ai_move", lambda s, x: False)(st, side):
            return {"success": False, "message": "本回合由 AI 接管中，请等待 AI 走棋",
                    "ai_controlled": True, "side": side}

    if req.to is None:
        return {"success": False, "message": "缺少落点坐标 to"}
    action = {"type": req.type or "move", "piece_id": req.piece_id,
              "to": list(req.to), "side": side}
    try:
        eng.apply_action(st, action)
    except ActionError as e:
        return {"success": False, "message": str(e),
                "stale": bool(SESSION.capabilities().get("realtime")
                              or SESSION.capabilities().get("randomness") == "live"),
                "rev": SESSION.state_rev}
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"success": False, "message": f"移动失败: {e}"}

    SESSION.save()
    return {"success": True, "board_state": st, "mechanisms": _mechanisms_summary(),
            "rev": SESSION.state_rev, "actable_sides": SESSION.actable_sides(),
            "ai_sides": SESSION.ai_sides()}


@app.post("/api/ai_move")
async def ai_move():
    import asyncio
    import copy
    eng = SESSION.engine
    st = SESSION.state
    SESSION.touch()
    turn = st.get("current_turn", "black")
    sides = SESSION.actable_sides()
    ai_sides = SESSION.ai_sides()
    # 异步棋种里 AI 可能不是「当前回合」那一方：优先挑真正由 AI 控制的可行动方
    side = ai_sides[0] if ai_sides else (turn if turn in sides else (sides[0] if sides else turn))

    if (st.get("game_status") or {}).get("state") == "ended":
        return {"success": False, "message": "游戏已结束"}

    # 行动点门禁：预算用尽 / 本回合被 0 步架空时不该驱动 AI 落子。
    # 否则 ai_action 返回 None 会被下面误判为「AI 无棋可走」而直接判负
    # 结束对局 —— 正是 2026-11-08 /chess1「卡住」的最后一步。
    budget = getattr(eng, "steps_remaining", None)
    if budget:
        try:
            if budget(st, side) <= 0:
                return {"success": False, "message": "本回合行动步数已用尽",
                        "rev": SESSION.state_rev,
                        "actable_sides": SESSION.actable_sides(),
                        "ai_sides": SESSION.ai_sides()}
        except Exception:
            pass

    should = getattr(eng, "should_ai_move", None)
    if should and not should(st, side):
        # 带上状态字段：前端据此把「回合已交接」对齐成正常状态，而不是卡住
        return {"success": False, "message": "不是AI回合",
                "rev": SESSION.state_rev,
                "actable_sides": SESSION.actable_sides(),
                "ai_sides": SESSION.ai_sides()}

    # AI 搜索用局面快照：实时 tick 可能在思考期间推进 state，直接传引用会让
    # 线程里的搜索与事件循环里的 tick 打架。
    snap = copy.deepcopy(st)
    try:
        # AI 搜索是 CPU 密集操作（Agent 改写的游戏可能深度更深、盘面更大），
        # 必须放线程池执行——否则会阻塞 uvicorn 事件循环，
        # AI 思考期间全站所有请求（页面/静态/其他用户）全部挂起。
        action = await asyncio.to_thread(eng.ai_action, snap, side)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"success": False, "message": f"AI 思考失败: {e}"}

    if not action:
        st["game_status"] = {"state": "ended", "winner": "red" if side == "black" else "black",
                             "win_condition": "stalemate",
                             "custom_rules_active": (st.get("game_status") or {}).get("custom_rules_active", [])}
        SESSION.save()
        return {"success": True, "board_state": st, "message": "AI无棋可走",
                "rev": SESSION.state_rev}

    action = dict(action)
    action.setdefault("side", side)
    try:
        eng.apply_action(st, action)
    except ActionError as e:
        return {"success": False, "message": str(e), "rev": SESSION.state_rev}

    SESSION.save()
    return {"success": True, "board_state": st, "ai_move": action,
            "is_random": False, "mechanisms": _mechanisms_summary(),
            "rev": SESSION.state_rev, "actable_sides": SESSION.actable_sides(),
            "ai_sides": SESSION.ai_sides()}


@app.post("/api/valid_moves")
async def valid_moves(req: MoveRequest):
    SESSION.touch()
    kwargs = {"piece_id": req.piece_id}
    if req.side:
        kwargs["side"] = req.side
    acts = SESSION.engine.legal_actions(SESSION.state, **kwargs)
    moves = [a["to"] for a in acts if a.get("to")]

    # 吃子落点：目标格站着敌方活子。前端据此把提示点画成红色并罩住被吃的棋子；
    # 引擎自己给了 capture 标记的，以引擎为准（自定义规则里「吃不掉也算吃」的也认）。
    side = req.side
    if not side and req.piece_id:
        for p in (SESSION.state.get("pieces") or []):
            if p.get("id") == req.piece_id:
                side = p.get("side")
                break
    occupied = set()
    try:
        for p in (SESSION.state.get("pieces") or []):
            if p.get("is_alive") is False:
                continue
            pos = p.get("position") or []
            if len(pos) == 2 and (not side or p.get("side") != side):
                occupied.add((int(pos[0]), int(pos[1])))
    except Exception:
        occupied = set()
    flagged = {tuple(a["to"]) for a in acts
               if a.get("to") and (a.get("capture") or a.get("captured"))}
    captures = [m for m in moves
                if (int(m[0]), int(m[1])) in occupied or (int(m[0]), int(m[1])) in flagged]
    return {"success": True, "moves": moves, "captures": captures,
            "rev": SESSION.state_rev}


@app.post("/api/undo")
async def undo():
    st = SESSION.state
    hist = st.get("move_history") or []
    if not hist:
        return {"success": False, "message": "没有可悔的棋"}

    steps = min(2, len(hist))
    for _ in range(steps):
        last = hist.pop()
        if last.get("from") is None:
            # 落子类游戏：撤销即移除该子
            for p in st["pieces"]:
                if p["id"] == last.get("piece_id"):
                    p["is_alive"] = False
                    break
        else:
            for p in st["pieces"]:
                if p["id"] == last.get("piece_id"):
                    p["position"] = list(last["from"])
                    break
        if last.get("captured"):
            for p in st["pieces"]:
                if p["id"] == last["captured"]:
                    p["is_alive"] = True
                    break

    st["move_history"] = hist
    # 回合归还给被撤销那一手的一方；空局则回到起始方
    if hist:
        lastp = next((p for p in st["pieces"] if p["id"] == hist[-1].get("piece_id")), None)
        st["current_turn"] = ("black" if lastp and lastp.get("side") == "red" else "red")
    else:
        st["current_turn"] = SESSION.engine.new_state().get("current_turn", "red")
    st["game_status"] = {"state": "playing", "winner": None, "win_condition": None,
                         "custom_rules_active": (st.get("game_status") or {}).get("custom_rules_active", [])}
    SESSION.save()
    return {"success": True, "board_state": st, "message": f"已悔{steps}步"}


@app.post("/api/restart")
async def restart():
    SESSION.reset()
    return {"success": True, "message": "游戏已重新开始", "board_state": SESSION.state}


@app.post("/api/reset_configs")
async def reset_configs():
    """恢复初始状态：退回出厂构建（APP_ROOT 原始代码与配置）并重开一局。

    与 /api/restart（只重开对局、保留 AI 改造）不同，这里会撤销全部 AI 改造、
    清空回滚历史，棋盘回到默认象棋。
    """
    from agent.workspace import gc, revert_to_pristine
    revert_to_pristine()
    SESSION.reload(full=True)
    SESSION.reset()
    gc()
    return {"success": True, "message": "已恢复初始状态（默认规则与棋子）",
            "board_state": SESSION.state}


@app.post("/api/difficulty")
async def set_difficulty(req: DifficultyRequest):
    if req.difficulty not in ["easy", "medium", "hard"]:
        return {"success": False, "message": "无效的难度"}
    rules = SESSION.configs.get("rules")
    if isinstance(rules, dict):
        rules.setdefault("ai_difficulty", {})["current"] = req.difficulty
        ai = getattr(SESSION.engine, "chess_ai", None)
        if ai:
            ai.set_difficulty(req.difficulty)
        try:
            SESSION.save_config("rules")
        except Exception:
            pass
    return {"success": True, "message": f"难度已设置为{req.difficulty}"}


@app.get("/api/mechanisms")
async def mechanisms():
    return {"success": True, "mechanisms": _mechanisms_summary(),
            "raw": SESSION.state.get("mechanisms", {})}


@app.post("/api/stop_mechanism")
async def stop_mechanism(req: StopMechanismRequest):
    st = SESSION.state
    mech = st.get("mechanisms", {}) or {}
    if req.mechanism_type in mech and isinstance(mech[req.mechanism_type], list):
        if req.mechanism_type == "player_control":
            mech[req.mechanism_type] = [i for i in mech[req.mechanism_type] if i.get("side") != req.side]
        else:
            mech[req.mechanism_type] = [
                i for i in mech[req.mechanism_type]
                if not (i.get("side") == req.side and i.get("remaining", 0) != 0)
            ]
        st["mechanisms"] = mech
        SESSION.save()
    return {"success": True, "message": "机制已截停",
            "mechanisms": _mechanisms_summary(), "board_state": st}


@app.get("/api/token_stats")
async def token_stats():
    d = METER.snapshot()
    return {
        "total_tokens": d.get("total_tokens", 0),
        "today_tokens": d.get("today", 0),
        "total_calls": d.get("calls", 0),
        # DeepSeek-V4.1-Flash 官方价目（分高峰/空闲），单位：元（CNY）
        "estimated_cost_cny": round(d.get("cost", 0.0), 6),
        "cost_currency": "CNY",
        "peak_now": bool(d.get("peak")),
        "cached_tokens": d.get("cached_tokens", 0),
        "cache_hit_rate": d.get("cache_hit_rate", 0.0),
        "prompt_tokens": d.get("prompt_tokens", 0),
        "completion_tokens": d.get("completion_tokens", 0),
    }


@app.post("/api/apikey")
async def set_api_key(req: SetApiKey):
    # 服务端已托管密钥时，禁止访客覆盖，避免把线上对局改坏
    from core.ai_config import get_api_key as _gk
    if _gk():
        return {"success": False, "message": "服务器已托管密钥，无需设置"}
    os.environ["DEEPSEEK_API_KEY"] = req.api_key.strip()
    from core.ai_config import reload_config
    reload_config()
    return {"success": True, "message": "API密钥已设置"}


@app.get("/api/apikey/status")
async def api_key_status():
    from core.ai_config import get_api_key
    return {"has_key": bool(get_api_key())}


@app.get("/api/logs")
async def logs(count: int = 20):
    from agent.runtime import AGENT
    return {"logs": AGENT.recent_logs(count)}


@app.get("/api/thinking_status")
async def thinking_status():
    from agent.runtime import AGENT
    return AGENT.status()


@app.post("/api/clear_logs")
async def clear_logs():
    from agent.runtime import AGENT
    AGENT.clear_logs()
    return {"success": True, "message": "日志已清空"}


# ═══════════════════════════════════════════════════════════
# Agent API
# ═══════════════════════════════════════════════════════════

@app.post("/api/agent/command")
async def agent_command(req: AgentCommand):
    from agent.runtime import AGENT
    try:
        return await AGENT.start(req.command, SESSION)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"success": False, "type": "error", "message": f"Agent 启动失败: {e}"}


@app.get("/api/agent/status")
async def agent_status(since: int = 0, job_id: str = ""):
    from agent.runtime import AGENT
    st = AGENT.status(since)
    # 发布代码改动会重启进程，内存状态归零；此时用落盘结果把前端接上
    if st.get("state") == "idle" and job_id:
        rec = AGENT.restore(job_id)
        if rec:
            st.update({
                "state": rec.get("state") or "done",
                "job_id": rec.get("job_id"),
                "summary": rec.get("summary") or "",
                "error": rec.get("error") or "",
                "game": rec.get("game") or st.get("game"),
                "phase": rec.get("phase") or "",
                "finished_at": rec.get("finished_at") or 0,
                "validation": rec.get("validation") or {},
                "restored": True,
            })
            st["events"] = [e for e in (rec.get("events") or []) if e.get("seq", 0) > since]
    return st


@app.get("/api/agent/events")
async def agent_events(request: Request):
    from agent.runtime import AGENT

    async def gen():
        last = 0
        while True:
            if await request.is_disconnected():
                break
            st = AGENT.status(last)
            for ev in st.get("events", []):
                last = max(last, ev.get("seq", 0))
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
            if st.get("state") in ("done", "error", "cancelled"):
                yield "event: end\ndata: {}\n\n"
                break
            await asyncio.sleep(0.4)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/agent/cancel")
async def agent_cancel():
    from agent.runtime import AGENT
    AGENT.cancel()
    return {"success": True}


@app.post("/api/agent/undo")
async def agent_undo():
    from agent.runtime import AGENT
    ok, msg = AGENT.rollback_last()
    if ok:
        SESSION.reload(full=False)
    return {"success": ok, "message": msg}


@app.post("/api/undo_config")
async def undo_config():
    """兼容旧按钮：回滚到上一个构建版本。"""
    from agent.runtime import AGENT
    ok, msg = AGENT.rollback_last()
    if ok:
        SESSION.reload(full=False)
    return {"success": ok, "message": msg if ok else "没有可撤回的修改"}


@app.post("/api/command")
async def legacy_command(req: AgentCommand):
    """兼容旧调用：等价于 Agent 改造。"""
    from agent.runtime import AGENT
    return await AGENT.start(req.command, SESSION)


@app.get("/api/games")
async def games():
    from core.engine import list_games
    return {"success": True, "games": list_games(SESSION.build_dir),
            "active": SESSION.game_id}


@app.post("/api/games/{game_id}/activate")
async def activate_game(game_id: str):
    """直接切换棋种（Agent 也会用它做快速切换）。"""
    from core.engine import load_manifest
    gdir = SESSION.build_dir / "games" / game_id
    if not (gdir / "engine.py").exists():
        return {"success": False, "message": "棋种不存在"}
    m = load_manifest(game_id, SESSION.build_dir)
    bid = "" if SESSION.build_dir == paths.APP_ROOT else SESSION.build_dir.name
    paths.write_active({"build": bid, "game": game_id, "ts": time.time()})
    SESSION.reload(full=True)
    return {"success": True, "message": f"已切换到 {m.get('name', game_id)}", "meta": SESSION.meta()}


# ═══════════════════════════════════════════════════════════
# 兼容：RPG 代理路由
# ═══════════════════════════════════════════════════════════

class RpgApplyPatchReq(BaseModel):
    patch: list
    target: str


@app.post("/api/rpg/apply_patch")
async def rpg_apply_patch(req: RpgApplyPatchReq):
    try:
        cfg = SESSION.patch_config(req.target, req.patch)
        return {"success": True, "target": req.target, "configs": cfg}
    except Exception as e:
        return {"success": False, "message": f"应用 patch 失败: {e}"}


@app.post("/api/rpg/reset_battle")
async def rpg_reset_battle():
    SESSION.reset()
    return {"success": True, "message": "战斗已重置", "board_state": SESSION.state}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("CHESS_PORT", "9401"))
    host = os.environ.get("CHESS_HOST", "0.0.0.0")
    uvicorn.run(app, host=host, port=port, log_level="info")