"""DeepSeek 客户端：工具调用 + 前缀缓存 + 用量计量。

性能关键（实测）
- deepseek-flash 是推理模型，默认先输出 reasoning_content；游戏内实时任务关闭
  推理后单次调用 ~0.6s（开启约 21s）。
- 上下文缓存按「前缀完全一致」命中，命中后 input 计费约为 1/5，且首字延迟更低。
  因此系统提示（架构说明 + 工具文档）必须逐字节稳定，工具返回值要短。
"""
from __future__ import annotations

import asyncio
import json
import random
import re
import time
from typing import Any, Dict, List, Optional

import httpx

from core.ai_config import get_api_key, get_base_url, get_model
from core.meter import METER

DEFAULT_TIMEOUT = 180.0


class LLMError(Exception):
    pass


def _repair_json(raw: str) -> dict:
    """模型偶尔给出带尾逗号/单引号的 JSON，做一次温和修复。"""
    raw = (raw or "").strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        pass
    s = raw
    s = re.sub(r"^```(?:json)?|```$", "", s, flags=re.M).strip()
    s = re.sub(r",\s*([}\]])", r"\1", s)
    s = s.replace("'", '"') if '"' not in s else s
    try:
        return json.loads(s)
    except Exception:
        # 截取第一个 { 到最后一个 }
        i, j = s.find("{"), s.rfind("}")
        if i >= 0 and j > i:
            try:
                return json.loads(re.sub(r",\s*([}\]])", r"\1", s[i:j + 1]))
            except Exception:
                return {}
    return {}


class DeepSeek:
    def __init__(self, api_key: Optional[str] = None):
        self._api_key = api_key
        self.base_url = get_base_url().rstrip("/")
        self.model = get_model()
        self._client: Optional[httpx.AsyncClient] = None

    @property
    def api_key(self) -> str:
        return self._api_key or get_api_key()

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(DEFAULT_TIMEOUT, connect=15.0),
                limits=httpx.Limits(max_connections=24, max_keepalive_connections=12),
            )
        return self._client

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.3,
        max_tokens: int = 8192,
        think: bool = False,
        effort: Optional[str] = None,
        retries: int = 2,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        # 思考模式下 reasoning_content 与 content 共享 max_tokens；8K 容易被
        # 思考吃光导致 content 为空（见 ai_config 注释）。开启思考时抬高下限。
        if think and max_tokens < 16384:
            max_tokens = 16384
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if not think:
            payload["thinking"] = {"type": "disabled"}
        elif effort:
            payload["thinking"] = {"type": "enabled", "effort": effort}

        key = self.api_key
        if not key:
            raise LLMError("未配置 DEEPSEEK_API_KEY")

        last_err: Optional[Exception] = None
        for attempt in range(retries + 1):
            try:
                r = await self._get_client().post(
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                    json=payload,
                    timeout=httpx.Timeout(timeout or DEFAULT_TIMEOUT, connect=10.0),
                )
                if r.status_code == 429 or r.status_code >= 500:
                    raise LLMError(f"HTTP {r.status_code}: {r.text[:200]}")
                if r.status_code != 200:
                    raise LLMError(f"HTTP {r.status_code}: {r.text[:400]}")
                data = r.json()
                usage = data.get("usage") or {}
                METER.add(
                    prompt=int(usage.get("prompt_tokens") or 0),
                    completion=int(usage.get("completion_tokens") or 0),
                    cached=int(usage.get("prompt_cache_hit_tokens")
                               or (usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0),
                )
                return data["choices"][0]["message"]
            except Exception as e:
                last_err = e
                if attempt < retries:
                    await asyncio.sleep(0.8 * (attempt + 1) + random.random() * 0.4)
                    continue
                break
        raise LLMError(str(last_err))

    @staticmethod
    def parse_tool_calls(msg: Dict[str, Any]) -> List[Dict[str, Any]]:
        out = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") or {}
            out.append({
                "id": tc.get("id") or f"call_{int(time.time()*1000)%100000}",
                "name": fn.get("name") or "",
                "args": _repair_json(fn.get("arguments") or "{}"),
            })
        return out


CLIENT = DeepSeek()
