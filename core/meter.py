"""Token 计量：统一记录每次 LLM 调用的输入/输出/缓存命中。"""
from __future__ import annotations

import datetime
import json
import threading
import time
from pathlib import Path
from typing import Any, Dict

from core import paths

_LOCK = threading.Lock()
_FILE = paths.STATE_DIR / "token_stats.json"


# ── DeepSeek-V4.1-Flash（模型名 deepseek-flash）官方价目 ──
# 来源 api-docs.deepseek.com 模型 & 价格页，2026-09-10 生效，单位：元 / 百万 tokens。
#   输入（缓存未命中）高峰 2.0 / 空闲 1.0
#   输入（缓存命中）  高峰 0.04 / 空闲 0.02
#   输出              高峰 8.0 / 空闲 4.0
# 高峰时段：北京时间周一至周五 9:00–12:00、14:00–18:00（不含法定节假日）；
# 其余时段（含周末与节假日全天）均为空闲时段。法定节假日按工作日计会让估算
# 略偏高——对成本预估来说偏保守是安全方向，故不做节假日表。
_PEAK_PRICE = {"miss": 2.0, "hit": 0.04, "out": 8.0}
_OFF_PEAK_PRICE = {"miss": 1.0, "hit": 0.02, "out": 4.0}


def _is_peak(ts: float | None = None) -> bool:
    """是否高峰时段。显式按北京时间（UTC+8）算，不依赖服务器时区。"""
    dt = datetime.datetime.fromtimestamp(ts if ts is not None else time.time(),
                                         tz=datetime.timezone(datetime.timedelta(hours=8)))
    if dt.weekday() >= 5:
        return False
    h = dt.hour + dt.minute / 60.0
    return (9.0 <= h < 12.0) or (14.0 <= h < 18.0)


class TokenMeter:
    def __init__(self):
        self.data: Dict[str, Any] = {
            "total_tokens": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "cached_tokens": 0,
            "calls": 0,
            "today": 0,
            "day": time.strftime("%Y-%m-%d"),
            "cost": 0.0,
        }
        self._load()

    def _load(self):
        try:
            if _FILE.exists():
                d = json.loads(_FILE.read_text(encoding="utf-8"))
                if isinstance(d, dict):
                    self.data.update(d)
        except Exception:
            pass
        self._roll_day()

    def _roll_day(self):
        today = time.strftime("%Y-%m-%d")
        if self.data.get("day") != today:
            self.data["day"] = today
            self.data["today"] = 0

    def _flush(self):
        try:
            _FILE.write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    def add(self, prompt: int = 0, completion: int = 0, cached: int = 0):
        with _LOCK:
            self._roll_day()
            now = time.time()
            self.data["prompt_tokens"] += int(prompt)
            self.data["completion_tokens"] += int(completion)
            self.data["total_tokens"] += int(prompt) + int(completion)
            self.data["cached_tokens"] += int(cached)
            self.data["today"] += int(prompt) + int(completion)
            self.data["calls"] += 1
            # 按 DeepSeek-V4.1-Flash 官方价目实时计价（元），高峰/空闲按调用时刻判定
            p = _PEAK_PRICE if _is_peak(now) else _OFF_PEAK_PRICE
            cost = ((int(prompt) - int(cached)) / 1e6 * p["miss"]
                    + int(cached) / 1e6 * p["hit"]
                    + int(completion) / 1e6 * p["out"])
            self.data["cost"] = round(self.data.get("cost", 0.0) + cost, 6)
            self.data["cost_currency"] = "CNY"
            self._flush()

    def snapshot(self) -> Dict[str, Any]:
        with _LOCK:
            self._roll_day()
            d = dict(self.data)
            d["cache_hit_rate"] = round(d["cached_tokens"] / max(1, d["prompt_tokens"]), 4)
            d["cost_currency"] = "CNY"
            d["peak"] = _is_peak()
            return d


METER = TokenMeter()
