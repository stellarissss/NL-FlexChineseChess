"""普通房间的前端诊断日志（无令牌下也能留下现场）。

令牌内测通道有逐令牌操作日志；但故障往往发生在**普通房间**：用户说「打不开 /
黑屏」，而服务端看到的是 200。没有前端现场就只能靠猜。这里把前端自报的异常、
引导阶段、渲染快照按天落盘，让「用户描述」变成「可核对的时间线」。

工程约束（与 access_log 一致）：
- 绝不影响请求：写盘失败一律吞掉，接口永远返回 200。
- 有节流：同一 IP 每分钟上限 40 条，防止异常风暴把磁盘写满。
- 按天滚动，超过 15 天删除。
- 单条截断到 8KB。
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

from core import paths

MAX_LINE = 8000
PER_IP_PER_MIN = 40
RETENTION_DAYS = 15


class ClientDiag:
    def __init__(self) -> None:
        self.dir: Path = paths.LOG_DIR / "client"
        self.retention_days = int(os.environ.get("CHESS_LOG_RETENTION_DAYS") or RETENTION_DAYS)
        self._lock = threading.Lock()
        self._rate: Dict[str, int] = {}
        self._last_cleanup = 0.0
        self._written = 0
        self._dropped = 0

    # ── 写入 ──
    def write(self, ip: str, entry: Dict[str, Any]) -> bool:
        try:
            minute = int(time.time() // 60)
            bucket = f"{ip}|{minute}"
            with self._lock:
                n = self._rate.get(bucket, 0)
                if n >= PER_IP_PER_MIN:
                    self._dropped += 1
                    return False
                self._rate[bucket] = n + 1
                if len(self._rate) > 5000:      # 极端情况下清表，避免无限增长
                    self._rate.clear()

            body = json.dumps(entry, ensure_ascii=False, separators=(",", ":"))
            if len(body) > MAX_LINE:
                body = body[:MAX_LINE] + '…"}'

            day = datetime.now().strftime("%Y%m%d")
            self.dir.mkdir(parents=True, exist_ok=True)
            with self._lock:
                with open(self.dir / f"{day}.jsonl", "a", encoding="utf-8") as f:
                    f.write(body + "\n")
                self._written += 1
            self._maybe_cleanup()
            return True
        except Exception:
            self._dropped += 1
            return False

    # ── 清理 ──
    def _maybe_cleanup(self) -> None:
        now = time.time()
        if now - self._last_cleanup < 3600:
            return
        self._last_cleanup = now
        self.cleanup()

    def cleanup(self) -> Dict[str, int]:
        removed = 0
        cutoff = (datetime.now() - timedelta(days=self.retention_days)).date()
        try:
            for p in self.dir.glob("*.jsonl"):
                stem = p.stem
                if len(stem) != 8 or not stem.isdigit():
                    continue
                try:
                    d = datetime.strptime(stem, "%Y%m%d").date()
                except ValueError:
                    continue
                if d < cutoff:
                    p.unlink()
                    removed += 1
        except Exception:
            pass
        return {"removed": removed, "kept_days": self.retention_days}

    # ── 自检 ──
    def stats(self) -> Dict[str, Any]:
        files = []
        try:
            for p in sorted(self.dir.glob("*.jsonl"))[-7:]:
                files.append({"name": p.name, "bytes": p.stat().st_size})
        except Exception:
            pass
        return {"dir": str(self.dir), "written": self._written,
                "dropped": self._dropped, "files": files}


CLIENT_DIAG = ClientDiag()
