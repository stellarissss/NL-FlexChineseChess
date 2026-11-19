"""Agent 运行时：主理人 + 并行工人 + 构建发布 + 事件流。"""
from __future__ import annotations

import asyncio
import copy
import json
import os
import time
import traceback
from typing import Any, Dict, List, Optional

from agent import prompts
from agent.llm import CLIENT, LLMError
from agent.tools import TOOLS, ToolContext, dispatch
from agent.validate import run_validation
from agent.workspace import (CORE_ITEMS, Build, create_build, gc, promote,
                             rollback_last)
from core import paths
from core.ai_config import (get_agent_think, get_agent_effort,
                            get_first_turn_think, get_first_turn_effort)
from core.meter import METER

# 全链路思考开关：默认关闭（其余轮 / 工人保持 0.6s 级快答）。
# 设 DEEPSEEK_AGENT_THINK=low|high|max 并重启服务可为整条流水线开思考。
AGENT_THINK = get_agent_think()
AGENT_EFFORT = get_agent_effort()

# 首轮（理解指令）分级思考：默认 low，独立开关，与上面解耦。
# 只作用于主循环 step==0；语义消歧 + 几何后果预演在这一步完成。
FIRST_TURN_THINK = get_first_turn_think()
FIRST_TURN_EFFORT = get_first_turn_effort()

HARD_BUDGET = 360.0          # 玩家指令的硬上限（秒）
RESERVE_FOR_DELIVERY = 22.0  # 留给校验与发布
MAX_ORCH_STEPS = 80          # 步数护栏：历史实测时间预算才是真瓶颈（56 步 ~110s 就耗尽），
                             # 放宽步数、仍由 HARD_BUDGET 兜底，避免「还有时间却没步数」
MAX_WORKER_STEPS = 12
MAX_WORKERS = 4

READ_ONLY_TOOLS = {"list_files", "read_file", "grep", "get_state"}

RESULT_FILE = paths.RUNTIME_DIR / "last_job.json"


class PublishError(RuntimeError):
    """发布后自检不通过 —— 已自动回滚，需要 Agent 继续修。"""


class AgentRuntime:
    def __init__(self):
        self.state: str = "idle"          # idle|running|done|error|cancelled
        self.job_id: str = ""
        self.events: List[Dict[str, Any]] = []
        self._seq = 0
        self.command: str = ""
        self.started_at: float = 0.0
        self.finished_at: float = 0.0
        self.summary: str = ""
        self.error: str = ""
        self.phase: str = ""
        self.validation: Dict[str, Any] = {}
        self.build: Optional[Build] = None
        self.game_id: str = "xiangqi"
        self.task: Optional[asyncio.Task] = None
        self.logs: List[Dict[str, Any]] = []
        self.steps: int = 0
        self.deadline: float = 0.0

    # ── 事件 ──
    def emit(self, kind: str, text: str, detail: str = ""):
        self._seq += 1
        ev = {"seq": self._seq, "ts": time.time(), "kind": kind, "text": text, "detail": detail}
        self.events.append(ev)
        if len(self.events) > 800:
            self.events = self.events[-500:]
        self.phase = text
        self.logs.append(ev)
        if len(self.logs) > 400:
            self.logs = self.logs[-300:]
        print(f"[agent] {kind}: {text}", flush=True)

    def status(self, since: int = 0) -> Dict[str, Any]:
        now = time.time()
        elapsed = (now - self.started_at) if self.started_at else 0
        remaining = max(0, HARD_BUDGET - elapsed) if self.state == "running" else 0
        return {
            "state": self.state,
            "job_id": self.job_id,
            "phase": self.phase,
            "elapsed": round(elapsed, 1),
            "remaining": round(remaining, 1),
            "steps": self.steps,
            "summary": self.summary,
            "error": self.error,
            "finished_at": self.finished_at,
            "validation": self.validation,
            "game": self.game_id,
            "events": [e for e in self.events if e["seq"] > since],
            "token": METER.snapshot(),
        }

    def recent_logs(self, count: int = 20) -> List[Dict[str, Any]]:
        return self.logs[-count:]

    def clear_logs(self):
        self.logs.clear()

    # ── 结果持久化：代码改动会冷启动进程，内存里的 job 状态会丢 ──
    def persist(self):
        try:
            data = {
                "job_id": self.job_id,
                "state": self.state,
                "summary": self.summary,
                "error": self.error,
                "game": self.game_id,
                "phase": self.phase,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "events": self.events[-120:],
                "validation": self.validation or {},
            }
            tmp = RESULT_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, RESULT_FILE)
        except Exception:
            pass

    @staticmethod
    def restore(job_id: str = "") -> Optional[Dict[str, Any]]:
        try:
            d = json.loads(RESULT_FILE.read_text(encoding="utf-8"))
            if job_id and str(d.get("job_id")) != str(job_id):
                return None
            return d
        except Exception:
            return None

    # ── 启动 ──
    async def start(self, command: str, session) -> Dict[str, Any]:
        if self.state == "running":
            return {"success": False, "type": "busy", "message": "上一条改造还在进行中",
                    "status": self.status(0)}
        command = (command or "").strip()
        if not command:
            return {"success": False, "type": "rejected", "message": "指令为空"}

        self.state = "running"
        self.job_id = str(int(time.time() * 1000))
        self.events.clear()
        self._seq = 0
        self.command = command
        self.summary = ""
        self.error = ""
        self.validation = {}
        self.steps = 0
        self.started_at = time.time()
        self.finished_at = 0.0
        self.deadline = self.started_at + HARD_BUDGET
        self.game_id = session.game_id
        self.emit("start", f"收到指令：{command}")

        self.task = asyncio.create_task(self._run(command, session))
        return {"success": True, "type": "started", "job_id": self.job_id,
                "budget": HARD_BUDGET}

    def cancel(self):
        if self.task and not self.task.done():
            self.task.cancel()
            self.state = "cancelled"
            self.emit("error", "已取消")

    # ── 主流程 ──
    async def _run(self, command: str, session):
        t0 = time.time()
        try:
            base = session.build_dir
            self.emit("build", "复制当前版本，准备隔离工作区", base.name)
            self.build = create_build(base)
            ctx = ToolContext(self.build, self.game_id, role="orchestrator")
            ctx.spawn_hook = lambda tasks: self._spawn(tasks)
            ctx.validate_hook = lambda: self._validate(self.build, self._guess_game())
            ctx.finish_hook = lambda: self._finish(ctx, session)

            meta = session.meta()
            changed = self.build.changed_files()
            hint = prompts.PLANNER_HINT.format(
                command=command,
                game_id=self.game_id,
                game_name=meta.get("name", self.game_id),
                w=meta.get("width", 9), h=meta.get("height", 10),
                interaction=meta.get("interaction", "move"),
                games=", ".join(g["id"] for g in meta.get("games", [])),
                changed=f"{len(changed)} 个文件" if changed else "无",
            )

            messages: List[Dict[str, Any]] = [
                {"role": "system", "content": prompts.system_prompt()},
                {"role": "user", "content": hint},
            ]

            for step in range(MAX_ORCH_STEPS):
                if time.time() > self.deadline - RESERVE_FOR_DELIVERY:
                    self.emit("warn", "接近时间上限，进入收口")
                    break
                if ctx.finished:
                    break
                self.steps = step + 1
                if step == 0 and FIRST_TURN_THINK:
                    self.emit("info", "正在理解指令 · 语义消歧与后果预演（首轮深度思考）")
                msg = await self._chat(messages, "主理人", first=(step == 0))
                calls = CLIENT.parse_tool_calls(msg)
                _am: Dict[str, Any] = {
                    "role": "assistant",
                    "content": msg.get("content") or "",
                    "tool_calls": msg.get("tool_calls") or [],
                }
                # DeepSeek thinking 模式：多轮工具调用必须原样回传
                # reasoning_content，否则网关返回 HTTP 400。
                if msg.get("reasoning_content"):
                    _am["reasoning_content"] = msg["reasoning_content"]
                messages.append(_am)

                if not calls:
                    txt = (msg.get("content") or "").strip()
                    self.emit("info", f"模型未调用工具：{txt[:80] or '(空)'}")
                    if ctx.validation.get("ok"):
                        self.emit("info", "模型给出结论，自动交付")
                        ctx.finished = True
                        ctx.summary = txt[:160] or "已按要求完成改造"
                        if ctx.finish_hook:
                            await ctx.finish_hook()
                        break
                    messages.append({"role": "user", "content":
                                     "不要只解释。请立即用工具实施改动，然后 validate() 并 finish()。"})
                    continue

                # 只读工具并发执行，写操作串行，保证依赖顺序
                if all(c["name"] in READ_ONLY_TOOLS for c in calls):
                    for c in calls:
                        self.emit("tool", self._label(c), self._snippet(c))
                    results = await asyncio.gather(*[
                        self._run_tool(ctx, c) for c in calls])
                    for c, r in zip(calls, results):
                        if r.startswith("ERROR"):
                            self.emit("warn", f"{c['name']} 失败", r[:160])
                        messages.append({"role": "tool", "tool_call_id": c["id"], "content": r})
                else:
                    for c in calls:
                        self.emit("tool", self._label(c), self._snippet(c))
                        r = await self._run_tool(ctx, c)
                        messages.append({"role": "tool", "tool_call_id": c["id"], "content": r})
                        if ctx.finished:
                            break

                # 预算护栏：长时间只读不写，强制推向实施
                if ctx.writes == 0 and step >= 3 and step % 2 == 1:
                    messages.append({"role": "user", "content":
                                     "停止继续探索。现在就动手：用 spawn_workers 并行派工，"
                                     "或直接 write_file 写文件。契约已在系统说明里，不需要再读。"})

            if not ctx.finished:
                over_time = time.time() > self.deadline - RESERVE_FOR_DELIVERY
                if over_time:
                    self.emit("warn", "时间到，尝试交付已完成部分")
                else:
                    self.emit("warn", f"步数用尽（{MAX_ORCH_STEPS} 步），交付已完成部分")
                changed = self.build.changed_files()
                if changed:
                    res = await self._validate(self.build, self.game_id)
                    if res.get("ok"):
                        self.game_id = _pick_game(self.build, self.game_id)
                        head = "已按时间预算交付" if over_time else "已交付当前进度"
                        detail = ctx.summary.strip() or (
                            f"{head}（{len(changed)} 个文件）："
                            + "、".join(changed[:4]) + ("…" if len(changed) > 4 else "")
                            + "；校验通过，已发布生效。")
                        await self._publish(session, detail)
                        self.state = "done"
                        self.finished_at = time.time()
                        return
                self.state = "error"
                self.error = "未能在预算内完成可交付的改动"
                self.emit("error", self.error)
                self.finished_at = time.time()
                return

            self.state = "done" if not self.error else "error"
            self.finished_at = time.time()
            self.emit("done", self.summary or "改造完成",
                      f"耗时 {self.finished_at - self.started_at:.1f}s")
        except asyncio.CancelledError:
            self.state = "cancelled"
            self.emit("error", "任务已取消")
        except Exception as e:
            traceback.print_exc()
            self.state = "error"
            self.error = f"{type(e).__name__}: {e}"
            self.emit("error", "执行失败", self.error)
        finally:
            self.finished_at = self.finished_at or time.time()
            self.persist()
            gc()

    async def _chat(self, messages: List[Dict[str, Any]], who: str,
                    first: bool = False) -> Dict[str, Any]:
        # 分级推理：仅「第一轮·理解指令」开思考做语义消歧与后果预演；
        # 后续执行轮与全部工人轮保持快答，控制总耗时。
        think = FIRST_TURN_THINK if first else AGENT_THINK
        effort = FIRST_TURN_EFFORT if first else AGENT_EFFORT
        remain = self.deadline - time.time() - RESERVE_FOR_DELIVERY
        if remain < 8:
            raise LLMError("时间预算不足")
        try:
            return await CLIENT.chat(messages, TOOLS, temperature=0.25, max_tokens=8192,
                                     think=think, effort=effort,
                                     timeout=min(120.0, remain))
        except LLMError as e:
            remain = self.deadline - time.time() - RESERVE_FOR_DELIVERY
            if remain < 12:
                raise
            self.emit("warn", f"{who}调用失败，重试一次", str(e)[:200])
            return await CLIENT.chat(messages, TOOLS, temperature=0.2, max_tokens=8192,
                                     think=think, effort=effort,
                                     retries=1, timeout=min(120.0, remain))

    async def _run_tool(self, ctx: ToolContext, call: Dict[str, Any]) -> str:
        name = call["name"]
        if name == "spawn_workers":
            self.emit("spawn", f"派出 {len(call['args'].get('tasks') or [])} 个工人并行施工")
        if name == "validate":
            self.emit("check", "开始校验构建")
        if name == "finish":
            self.emit("check", "校验并发布")
        r = await dispatch(ctx, name, call.get("args") or {})
        if name in ("validate", "finish"):
            ok = not r.startswith("FAILED") and not r.startswith("ERROR")
            self.emit("check", "校验通过" if ok else "校验未通过", r[:400])
        return r

    # ── 工人 ──
    async def _spawn(self, tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        sem = asyncio.Semaphore(MAX_WORKERS)

        async def one(t: Dict[str, Any]) -> Dict[str, Any]:
            async with sem:
                return await self._worker(t)

        results = await asyncio.gather(*[one(t) for t in tasks[:MAX_WORKERS]],
                                       return_exceptions=True)
        out = []
        for t, r in zip(tasks, results):
            if isinstance(r, Exception):
                out.append({"id": t.get("id"), "ok": False, "summary": f"{type(r).__name__}: {r}"})
            else:
                out.append(r)
        return out

    async def _worker(self, task: Dict[str, Any]) -> Dict[str, Any]:
        wid = task.get("id") or f"w{int(time.time()*1000)%10000}"
        files = task.get("files") or []
        goal = task.get("goal") or ""
        self.emit("worker", f"工人 {wid} 开始：{goal[:60]}")
        ctx = ToolContext(self.build, self.game_id, role="worker", allowed_files=files)
        messages: List[Dict[str, Any]] = [
            # 工人用精简系统提示词（含引擎契约与自由度契约），不再吃全量主提示词：
            # 每个工人每次调用省 ~6K tokens，且独立稳定前缀仍可命中上下文缓存。
            {"role": "system", "content": prompts.worker_system()},
            {"role": "user", "content": prompts.worker_task_prompt(
                goal, files, task.get("context", ""), task.get("impacts", ""))},
        ]
        deadline = min(time.time() + 160, self.deadline - 12)
        try:
            for step in range(MAX_WORKER_STEPS):
                if time.time() > deadline or ctx.reported:
                    break
                left = max(10.0, min(150.0, deadline - time.time()))
                msg = await CLIENT.chat(messages, TOOLS, temperature=0.25,
                                        max_tokens=8192, think=AGENT_THINK,
                                        effort=AGENT_EFFORT, timeout=left)
                calls = CLIENT.parse_tool_calls(msg)
                _am = {"role": "assistant", "content": msg.get("content") or "",
                       "tool_calls": msg.get("tool_calls") or []}
                if msg.get("reasoning_content"):
                    _am["reasoning_content"] = msg["reasoning_content"]
                messages.append(_am)
                if not calls:
                    break
                for c in calls:
                    r = await dispatch(ctx, c["name"], c.get("args") or {})
                    messages.append({"role": "tool", "tool_call_id": c["id"], "content": r})
                    if ctx.reported:
                        break
            summary = ctx.reported or "（未汇报，已按任务写入文件）"
            self.emit("worker", f"工人 {wid} 完成", summary[:160])
            return {"id": wid, "ok": True, "summary": summary, "files": files}
        except Exception as e:
            self.emit("warn", f"工人 {wid} 失败", str(e)[:160])
            return {"id": wid, "ok": False, "summary": f"{type(e).__name__}: {e}", "files": files}

    def _guess_game(self) -> str:
        # 按改动分布推断本次要校验的棋种（新建棋种时尤其重要）
        try:
            counts = {}
            for f in self.build.changed_files():
                parts = f.split("/")
                if len(parts) > 2 and parts[0] == "games":
                    counts[parts[1]] = counts.get(parts[1], 0) + 1
            if counts:
                return max(counts, key=counts.get)
        except Exception:
            pass
        return self.game_id

    # ── 校验 / 发布 ──
    async def _validate(self, build: Build, game_id: str) -> Dict[str, Any]:
        res = await run_validation(build, game_id, deep=True, deadline=self.deadline)
        self.validation = res
        return res

    async def _finish(self, ctx: ToolContext, session) -> str:
        if ctx.target_game:
            if not (self.build.root / "games" / ctx.target_game / "engine.py").exists():
                ctx.finished = False
                return f"ERROR: 棋种 {ctx.target_game} 不存在（缺少 games/{ctx.target_game}/engine.py）"
            self.game_id = ctx.target_game
        res = await self._validate(self.build, self.game_id)
        if not res.get("ok"):
            ctx.finished = False
            return "FAILED: 校验未通过，先修复：\n" + "\n".join(f"- {e}" for e in res.get("errors") or [])
        target = ctx.summary or "已按要求完成改造"
        try:
            await self._publish(session, target)
        except PublishError as e:
            ctx.finished = False
            return f"FAILED: {e}"
        return "OK 已发布，页面将自动刷新"

    async def _publish(self, session, summary: str):
        need_restart = False
        try:
            need_restart = self.build.needs_restart()
        except Exception:
            pass
        reset_rules = False
        try:
            reset_rules = self.build.touches_rules()
        except Exception:
            pass
        layout_changed = False
        try:
            layout_changed = self.build.touches_initial_layout()
        except Exception:
            pass

        stats = (self.validation or {}).get("stats") or {}
        expect = stats.get("opening") or {}
        expect_cnt = stats.get("piece_counts") or {}

        promote(self.build, self.game_id)
        # 发布前留一份对局快照：自检失败回滚时原样恢复，绝不重开
        state_before = copy.deepcopy(session.state)
        merged = hot_publish(session, rules_changed=reset_rules,
                             layout_changed=layout_changed, emit=self.emit)
        self.summary = summary
        self.validation = self.validation or {}

        # 发布后自检：同一进程里热重载出来的行为必须与干净子进程里的自检一致。
        # 不一致说明「配置是新的、代码还是旧的」，宁可回滚也不能把坏版本留给玩家。
        if not need_restart and expect:
            got = _opening_sig(session)
            got_cnt: Dict[str, int] = {}
            for p in session.state.get("pieces", []):
                got_cnt[p.get("type")] = got_cnt.get(p.get("type"), 0) + 1
            dead = [t for t, v in expect.items()
                    if v > 0 and expect_cnt.get(t, 0) > 0
                    and got_cnt.get(t, 0) > 0 and got.get(t, 0) == 0]
            if dead:
                rollback_last()
                session.reload(full=False)
                # 回滚只换回旧版本，当前对局原样保留（绝不重开）
                session.state = copy.deepcopy(state_before)
                session._write_state(session.state)
                session.state_rev += 1
                self.emit("error", "发布后自检失败，已自动回滚（当前对局保留）",
                          "、".join(dead) + " 在线上完全不能动")
                raise PublishError(
                    "发布后自检发现规则没有真正生效：" + "、".join(dead)
                    + " 在线上完全不能动，已自动回滚。请检查引擎里对应走法分支是否被正确修改"
                      "（不要只改 JSON 就以为生效）。")

        self.emit("publish", "已发布新版本", summary)
        # 裁判文件被动过要在结果里亮出来，不允许静默
        touched_judge = [f for f in self.build.changed_files()
                         if f.startswith("tools/")]
        if touched_judge:
            self.emit("warn", "自检脚本被本次改动修改（修 harness 自身 bug 属合法，"
                              "放宽断言属违规）", "、".join(touched_judge))
            self.summary = (self.summary or "") + "（注意：tools/ 自检脚本被修改）"
        if need_restart:
            self.persist()          # 重启会清空内存状态，先把结果落盘给前端
            self.emit("info", "代码有改动，正在重启服务（约 1 秒）")
            _apply_core_changes(self.build)
            _schedule_restart()

    # ── 回滚 ──
    def rollback_last(self):
        ok, msg = rollback_last()
        return ok, msg

    # ── 展示辅助 ──
    @staticmethod
    def _label(c: Dict[str, Any]) -> str:
        a = c.get("args") or {}
        n = c["name"]
        if n == "write_file":
            return f"写入 {a.get('path')}"
        if n == "edit_file":
            return f"修改 {a.get('path')}"
        if n == "read_file":
            return f"查看 {a.get('path')}"
        if n == "patch_config":
            return f"改配置 {a.get('name')}"
        if n == "spawn_workers":
            return f"派出 {len(a.get('tasks') or [])} 个工人"
        return n

    @staticmethod
    def _snippet(c: Dict[str, Any]) -> str:
        a = c.get("args") or {}
        s = a.get("goal") or a.get("path") or a.get("name") or ""
        return str(s)[:120]


def _opening_sig(session) -> Dict[str, int]:
    """当前进程内的引擎，开局每种棋子有多少走法（与自检同口径）。"""
    try:
        eng = session.engine
        st = eng.new_state()
        id2type = {p.get("id"): p.get("type") for p in st.get("pieces", [])}
        out: Dict[str, int] = {}
        for side in ("red", "black"):
            for a in eng.legal_actions(st, side=side):
                t = id2type.get(a.get("piece_id"))
                if t:
                    out[t] = out.get(t, 0) + 1
        return out
    except Exception:
        return {}


def _pick_game(build: Build, current: str) -> str:
    """若 Agent 新建了棋种但没显式切换，保持当前棋种，避免误切。"""
    return current


AGENT = AgentRuntime()


# ── 核心代码热替换（Agent 改了 core/ main.py 等才走这里） ──



def hot_publish(session, rules_changed: bool, layout_changed: bool,
                emit) -> Dict[str, Any]:
    """发布编排（模块级，可独立测试）：任何改动都热并入当前对局，绝不自动重开。

    2026-11-07 架构决策：重开对局只允许「某方胜利 / 玩家手动」两条途径
    （main.py 的 /api/restart、/api/reset_configs、/api/rpg/reset_battle）。
    Agent 发布一律走这里。session.reload(full=False) 已从磁盘读回棋谱/回合/
    棋子并同步标签；hot_merge 只补结构性差异：
      · 摆子(initial)改动 -> 按作弊语义更新存活棋子位置；
      · 规则/引擎/名字改动 -> 只并入新字段，绝不拉回玩家已走动的棋子。
    """
    session.reload(full=False)
    merged = session.hot_merge(layout_changed=layout_changed,
                               rules_changed=rules_changed)
    lay = merged.get("layout") or {}
    cfg = merged.get("config") or {}

    if layout_changed and not lay.get("ok"):
        emit("warn", "初始布局有变动但未能全量并入（%s），当前对局已原样保留"
             % (lay.get("reason") or "未知原因"))
    elif layout_changed and lay.get("noop"):
        emit("info", "初始布局无摆子差异，已热并入当前对局（棋谱与回合保留）")
    elif layout_changed:
        bits = []
        if lay.get("added"):
            bits.append(f"新增 {lay['added']} 枚")
        if lay.get("removed"):
            bits.append(f"撤掉 {lay['removed']} 枚")
        if lay.get("moved"):
            bits.append(f"{lay['moved']} 枚原位置被占、已就近安放")
        if lay.get("repositioned"):
            bits.append(f"{lay['repositioned']} 枚棋子已换位（对局中立即生效）")
        emit("info", "初始布局有变动，已并入当前对局（" + "、".join(bits)
             + "，其余棋子与棋谱保留）")
    if rules_changed:
        emit("info", "规则/配置有变动，已热并入当前对局"
                     "（棋谱、回合、棋子位置与胜负一律保留，不重开）")
    if cfg.get("added"):
        emit("info", "新版本引入的机制字段已并入当前对局："
             + "、".join(cfg["added"]))
    return merged


def _apply_core_changes(build: Build):
    import shutil
    from pathlib import Path
    # 先备份当前核心代码，重启失败时可人工回退
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = paths.RUNTIME_DIR / "core_backup" / stamp
    try:
        for item in CORE_ITEMS:
            src = paths.APP_ROOT / item
            if src.exists():
                dst = backup / item
                if src.is_dir():
                    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
                else:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)
    except Exception as e:
        print(f"[agent] 核心备份失败: {e}", flush=True)
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", "runtime")
    for item in CORE_ITEMS:
        src = build.root / item
        if not src.exists():
            continue
        dst = paths.APP_ROOT / item
        try:
            if src.is_dir():
                shutil.copytree(src, dst, dirs_exist_ok=True, ignore=ignore)
            else:
                shutil.copy2(src, dst)
        except Exception as e:
            print(f"[agent] 核心代码同步失败 {item}: {e}", flush=True)


def _schedule_restart():
    """延迟重启；若玩家刚好又下了指令，等任务跑完再重启，避免把新任务杀掉。"""
    import asyncio as _aio

    def _try():
        if AGENT.state == "running":
            _aio.get_event_loop().call_later(2.0, _try)
        else:
            _restart_process()
    _aio.get_event_loop().call_later(1.2, _try)


def _restart_process():
    import os
    import sys
    print("[agent] 重启进程以加载核心改动", flush=True)
    try:
        os.execv(sys.executable, [sys.executable, str(paths.APP_ROOT / "run.py")])
    except Exception as e:
        print(f"[agent] 重启失败: {e}", flush=True)
