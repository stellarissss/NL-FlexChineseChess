"""Agent 工具集：定义 + 分发。

工具描述会进入请求前缀，**改动会破坏缓存**，非必要不要动。
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from agent.workspace import Build

MAX_READ_LINES = 260
MAX_TOOL_CHARS = 12000

TOOLS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "列出构建目录下的文件（含目录）。用于快速了解工程结构。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "相对路径，默认根目录"},
                    "depth": {"type": "integer", "description": "递归深度，默认 2"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取文本文件。大文件用 start/end 分页，单次最多 500 行。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "start": {"type": "integer", "description": "起始行，从 1 开始"},
                    "end": {"type": "integer", "description": "结束行（含）"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "写入（覆盖）文件。父目录自动创建。新建文件优先用它。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string", "description": "完整文件内容"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "精确替换文件中的一段文本。old_string 必须与文件内容完全一致且唯一（或用 replace_all）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_string": {"type": "string"},
                    "new_string": {"type": "string"},
                    "replace_all": {"type": "boolean"},
                },
                "required": ["path", "old_string", "new_string"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_file",
            "description": "删除文件（一般不必要；保留旧文件更安全）。",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": "在工程内按正则搜索文本，返回 路径:行号: 内容。",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string", "description": "搜索根目录，默认 ."},
                    "glob": {"type": "string", "description": "如 *.py / *.js"},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_state",
            "description": "查看当前棋种、棋盘、配置键、局面概要与已有棋种列表。传 piece_id 可探测该棋子的实际合法走法（验证规则改动是否真的生效）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "piece_id": {"type": "string", "description": "可选。探测该棋子在开局下的合法走法，如 r_chariot_1"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "patch_config",
            "description": "用 JSON Patch(RFC6902) 修改 games/<game>/configs/<name>.json，最快的改法。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "配置名，如 board / ui_config / rules"},
                    "patch": {"type": "array", "description": "[{\"op\":\"replace\",\"path\":\"/geometry/width\",\"value\":15}]"},
                },
                "required": ["name", "patch"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "validate",
            "description": "校验构建：Python 语法、引擎随机对局自检、JS 语法、HTTP 冒烟。返回 ok/errors。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "spawn_workers",
            "description": "并行派出多个工人同时写互不重叠的文件，显著提速。每个任务要写清目标、负责文件、接口约定。",
            "parameters": {
                "type": "object",
                "properties": {
                    "tasks": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "goal": {"type": "string", "description": "目标与验收点，含必要接口细节"},
                                "files": {"type": "array", "items": {"type": "string"},
                                          "description": "该工人独占的文件路径"},
                                "impacts": {"type": "string",
                                            "description": "该工人要逐条落实的影响面清单（落点→改法），一条不许漏"},
                                "context": {"type": "string", "description": "补充上下文/字段约定"},
                            },
                            "required": ["goal", "files"],
                        },
                    }
                },
                "required": ["tasks"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "report",
            "description": "工人完成时汇报结果（主理人不要调用）。",
            "parameters": {
                "type": "object",
                "properties": {"summary": {"type": "string"}},
                "required": ["summary"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish",
            "description": "交付：写入总结并发布构建（会自动刷新页面）。必须已 validate 通过。",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "description": "给玩家看的一句话结果说明"},
                    "game": {"type": "string",
                             "description": "要激活的棋种 id；新建棋种时填它，改现有棋种可省略"},
                },
                "required": ["summary"],
            },
        },
    },
]


class ToolContext:
    def __init__(self, build: Build, game_id: str, role: str = "orchestrator",
                 allowed_files: Optional[List[str]] = None):
        self.build = build
        self.game_id = game_id
        self.role = role
        self.allowed_files = [f.lstrip("/") for f in allowed_files] if allowed_files else None
        self.finished = False
        self.summary = ""
        self.target_game = ""
        self.reported = ""
        self.validation: Dict[str, Any] = {}
        self.spawn_hook: Optional[Callable] = None
        self.validate_hook: Optional[Callable] = None
        self.finish_hook: Optional[Callable] = None
        self.notes: List[str] = []
        self.writes: int = 0
        self.reads: int = 0

    def guard_write(self, rel: str):
        rel = rel.lstrip("/")
        if self.allowed_files is not None:
            ok = any(rel == f or rel.startswith(f.rstrip("/") + "/") for f in self.allowed_files)
            if not ok:
                raise PermissionError(
                    f"该文件不在你的职责范围：{rel}。你只能写：{', '.join(self.allowed_files)}")


def _clip(s: str, n: int = MAX_TOOL_CHARS) -> str:
    return s if len(s) <= n else s[:n] + f"\n…（已截断，共 {len(s)} 字符）"


async def dispatch(ctx: ToolContext, name: str, args: Dict[str, Any]) -> str:
    try:
        return _clip(await _dispatch(ctx, name, args or {}))
    except Exception as e:
        return f"ERROR: {type(e).__name__}: {e}"


async def _dispatch(ctx: ToolContext, name: str, args: Dict[str, Any]) -> str:
    b = ctx.build

    if name == "list_files":
        rel = args.get("path") or ""
        depth = int(args.get("depth") or 2)
        items = b.list_dir(rel, depth)
        return "\n".join(items) or "(空)"

    if name == "read_file":
        rel = args["path"].lstrip("/")
        p = b.resolve(rel)
        if not p.exists():
            return f"ERROR: 文件不存在: {rel}"
        ctx.reads += 1
        text = p.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        start = max(1, int(args.get("start") or 1))
        end = min(len(lines), int(args.get("end") or (start + MAX_READ_LINES - 1)))
        body = "\n".join(f"{i}\t{lines[i-1]}" for i in range(start, end + 1))
        head = f"{rel}  ({len(lines)} 行, 显示 {start}-{end})"
        return f"{head}\n{body}"

    if name == "write_file":
        rel = args["path"].lstrip("/")
        ctx.guard_write(rel)
        content = args.get("content")
        if content is None:
            return "ERROR: content 不能为空"
        b.write(rel, content)
        ctx.writes += 1
        return f"OK 已写入 {rel}（{len(content)} 字符）"

    if name == "edit_file":
        rel = args["path"].lstrip("/")
        ctx.guard_write(rel)
        old = args.get("old_string") or ""
        new = args.get("new_string") or ""
        if not old:
            return "ERROR: old_string 不能为空"
        p = b.resolve(rel)
        if not p.exists():
            return f"ERROR: 文件不存在: {rel}"
        text = p.read_text(encoding="utf-8", errors="replace")
        cnt = text.count(old)
        if cnt == 0:
            # 给出近似行，帮助模型自我纠正
            first = old.splitlines()[0] if old.splitlines() else old
            near = [f"{i}: {l}" for i, l in enumerate(text.splitlines(), 1) if first[:20] in l][:3]
            hint = ("\n相近位置：\n" + "\n".join(near)) if near else ""
            return f"ERROR: 未找到 old_string（{rel}）。请先 read_file 核对原文。{hint}"
        if cnt > 1 and not args.get("replace_all"):
            return f"ERROR: old_string 出现 {cnt} 次，请提供更长上下文或 replace_all=true"
        text = text.replace(old, new) if args.get("replace_all") else text.replace(old, new, 1)
        b.write(rel, text)
        ctx.writes += 1
        return f"OK 已修改 {rel}"

    if name == "delete_file":
        rel = args["path"].lstrip("/")
        ctx.guard_write(rel)
        b.delete(rel)
        ctx.writes += 1
        return f"OK 已删除 {rel}"

    if name == "grep":
        pattern = args["pattern"]
        root = b.resolve(args.get("path") or "")
        glob = args.get("glob") or "*"
        rx = re.compile(pattern, re.I)
        out = []
        files = root.rglob(glob) if root.is_dir() else [root]
        for f in files:
            if not f.is_file() or any(x in f.parts for x in ("__pycache__", "runtime", "venv", ".git")):
                continue
            if f.suffix not in (".py", ".js", ".json", ".html", ".css", ".md", ".txt"):
                continue
            try:
                for i, line in enumerate(f.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                    if rx.search(line):
                        out.append(f"{f.relative_to(b.root)}:{i}: {line.strip()[:180]}")
                        if len(out) >= 80:
                            raise StopIteration
            except StopIteration:
                break
            except Exception:
                continue
        return "\n".join(out) or "(无匹配)"

    if name == "get_state":
        from core.engine import list_games, load_engine
        info = {
            "game_id": ctx.game_id,
            "games": [g["id"] for g in list_games(b.root)],
            "configs": {k: (list(v.keys())[:12] if isinstance(v, dict) else type(v).__name__)
                        for k, v in _load_configs(b, ctx.game_id).items()},
            "points": "配置在 games/%s/configs/，初始局面在 configs/initial/board_state.json" % ctx.game_id,
        }
        # 探针：piece_id 存在时，用构建里的真实引擎算该棋子的合法走法。
        # 改完规则/走法后必须先探测再 finish，防止「配置写对了但引擎没生效」。
        pid = args.get("piece_id")
        if pid:
            try:
                eng = load_engine(ctx.game_id, b.root, _load_configs(b, ctx.game_id))
                st = eng.new_state()
                pc = next((p for p in st.get("pieces", []) if p.get("id") == pid), None)
                if not pc:
                    info["probe_error"] = f"开局里没有 id={pid} 的棋子，可用棋子: " + \
                        ", ".join(p["id"] for p in st.get("pieces", [])[:32])
                else:
                    acts = eng.legal_actions(st, piece_id=pid)
                    info["probe"] = {
                        "piece": pc.get("name"), "type": pc.get("type"),
                        "position": pc.get("position"),
                        "moves_count": len(acts),
                        "moves": [a.get("to") for a in acts[:60]],
                    }
            except Exception as e:
                info["probe_error"] = f"{type(e).__name__}: {e}"
        return json.dumps(info, ensure_ascii=False)

    if name == "patch_config":
        from core.json_patch_utils import apply_patch
        cname = args["name"]
        ops = args.get("patch") or []
        path = b.root / "games" / ctx.game_id / "configs" / f"{cname}.json"
        if not path.exists():
            return f"ERROR: 配置不存在: {path.relative_to(b.root)}"
        data = json.loads(path.read_text(encoding="utf-8"))
        try:
            new_data = apply_patch(data, ops)
        except Exception as e:
            return f"ERROR: patch 失败: {e}"
        b.write(str(path.relative_to(b.root)),
                json.dumps(new_data, ensure_ascii=False, indent=2))
        ctx.writes += 1
        return f"OK 已应用 {len(ops)} 条 patch 到 {cname}.json"

    if name == "validate":
        if not ctx.validate_hook:
            return "ERROR: 当前上下文不支持校验"
        res = await ctx.validate_hook()
        ctx.validation = res
        ws = res.get("warnings") or []
        if res.get("ok"):
            msg = f"OK 校验通过（{res.get('ms')}ms）{json.dumps(res.get('stats') or {}, ensure_ascii=False)}"
            if ws:
                msg += ("\n⚠ 校验告警（必须逐条核实，确认与本次改动无关或确为有意设计"
                        "才可继续 finish）：\n" + "\n".join(f"- {w}" for w in ws))
            return msg
        msg = "FAILED: 校验未通过，禁止 finish，先按下面逐条修好再重新 validate：\n"
        msg += "\n".join(f"- {e}" for e in res.get("errors") or [])
        if ws:
            msg += "\n另有告警：\n" + "\n".join(f"- {w}" for w in ws)
        return msg

    if name == "spawn_workers":
        if ctx.role != "orchestrator" or not ctx.spawn_hook:
            return "ERROR: 工人不能再派工人"
        tasks = args.get("tasks") or []
        if not tasks:
            return "ERROR: tasks 为空"
        results = await ctx.spawn_hook(tasks)
        return json.dumps(results, ensure_ascii=False)

    if name == "report":
        ctx.reported = args.get("summary") or ""
        return "OK 已汇报"

    if name == "finish":
        if ctx.role != "orchestrator":
            return "ERROR: 只有主理人可以发布"
        ctx.summary = args.get("summary") or ""
        ctx.target_game = (args.get("game") or "").strip()
        ctx.finished = True
        if ctx.finish_hook:
            msg = await ctx.finish_hook()
            return msg
        return "OK 已提交"

    return f"ERROR: 未知工具 {name}"


def _load_configs(b: Build, game_id: str) -> Dict[str, Any]:
    out = {}
    d = b.root / "games" / game_id / "configs"
    if d.is_dir():
        for p in d.glob("*.json"):
            try:
                out[p.stem] = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                out[p.stem] = {}
    init = d / "initial" / "board_state.json"
    if init.exists():
        try:
            out["initial_board_state"] = json.loads(init.read_text(encoding="utf-8"))
        except Exception:
            pass
    return out
