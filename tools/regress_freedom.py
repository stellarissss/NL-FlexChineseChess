#!/usr/bin/env python3
"""自由度契约回归：随机 / 异步 / 实时三种玩法必须被放行，歧义玩法必须被拦。

用法：python tools/regress_freedom.py
退出码：0 = 全部符合预期；1 = 有偏差

三个夹具（tools/regress/fixtures/）：
  demo      随机(state_rng) + 异步(双方可动) + 实时(tick) → 必须 ok 且零错误
  livedemo  查询每次重掷、执行只做静态校验            → 必须 ok（活随机可放行）
  badapply  查询给出执行时拒绝的动作                  → 必须 !ok（查询/执行分歧）

这个脚本守的是「判定分级」本身：有人把规则改回「一有随机就驳回」时，demo 会先红。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tools" / "regress" / "fixtures"

# 夹具名 → (期望 ok, 期望至少一条警告)
EXPECT = {
    "demo": (True, False),
    "livedemo": (True, True),
    "badapply": (False, False),
}
PLIES = 24


def _build_tmp(tmp: Path):
    """搭一个最小构建：core/ + tools/selftest.py + 夹具里的 games/。"""
    shutil.copytree(ROOT / "core", tmp / "core",
                    ignore=shutil.ignore_patterns("__pycache__"))
    (tmp / "tools").mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "tools" / "selftest.py", tmp / "tools" / "selftest.py")
    for d in FIXTURES.iterdir():
        if d.is_dir():
            shutil.copytree(d, tmp / "games" / d.name)


def main() -> int:
    failed = []
    tmp = Path(tempfile.mkdtemp(prefix="chess-freedom-"))
    try:
        _build_tmp(tmp)
        for name, (want_ok, want_warn) in EXPECT.items():
            r = subprocess.run(
                [sys.executable, "tools/selftest.py", str(tmp), name, str(PLIES)],
                cwd=str(tmp), capture_output=True, text=True, timeout=180,
            )
            lines = (r.stdout or "").strip().splitlines()
            if not lines:
                failed.append(f"{name}: 自检无输出 ({(r.stderr or '')[-200:]})")
                continue
            try:
                d = json.loads(lines[-1])
            except Exception as e:
                failed.append(f"{name}: 输出非 JSON ({e})")
                continue
            ok = bool(d.get("ok"))
            warns = d.get("warnings") or []
            errs = d.get("errors") or []
            stats = d.get("stats") or {}
            if ok != want_ok:
                failed.append(f"{name}: ok={ok}，期望 {want_ok}；errors={errs[:2]}")
            elif want_warn and not warns:
                failed.append(f"{name}: 期望给出「活随机」警告，实际没有")
            print(f"[{'PASS' if ok == want_ok else 'FAIL'}] {name:9s} ok={ok} "
                  f"warn={len(warns)} err={len(errs)} "
                  f"applied={stats.get('applied')} tick={stats.get('tick_changes')}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if failed:
        print("\n不符合预期：")
        for f in failed:
            print(" -", f)
        return 1
    print("\n全部符合预期：随机/异步/实时放行，查询与执行分歧仍被拦下。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
