"""Thin async client for llama-server raw /completion with streaming and cache metrics."""
import asyncio, json, time
import httpx
from . import config


class LLM:
    def __init__(self, url: str = None):
        self.url = (url or config.LLM_URL).rstrip("/")
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(600, connect=10))

    async def wait_idle(self, max_wait: float = 900):
        """Wait until no slot is processing. Fails fast when llama-server is unreachable, and skips the
        check when /slots is disabled (--no-slots) instead of silently spinning for max_wait."""
        waited = 0.0; conn_errors = 0
        while waited < max_wait:
            try:
                r = await self.client.get(self.url + "/slots")
            except httpx.TransportError as e:
                conn_errors += 1
                if conn_errors >= 3:
                    raise RuntimeError(f"llama-server недоступен по {self.url} ({type(e).__name__}). "
                                       f"Запущен ли он? Проверь LLM_URL.") from e
            else:
                conn_errors = 0
                slots = r.json() if r.status_code == 200 else None
                if not isinstance(slots, list):
                    return waited
                if not any(isinstance(s, dict) and s.get("is_processing") for s in slots):
                    return waited
            await asyncio.sleep(2); waited += 2
        raise RuntimeError("llama-server busy too long")

    async def tokenize(self, text: str) -> int:
        r = await self.client.post(self.url + "/tokenize", json={"content": text})
        return len(r.json()["tokens"])

    async def complete(self, prompt: str, n_predict: int, stop=None, on_delta=None, seed=-1,
                       temperature=None, **extra):
        """Stream a completion. Returns (content, stats). stats has cache_n/prompt_n/predicted_n/wall_s."""
        await self.wait_idle()
        body = {"prompt": prompt, "n_predict": n_predict, "stream": True, "cache_prompt": True,
                "id_slot": 0, "temperature": config.TEMPERATURE if temperature is None else temperature,
                "top_p": config.TOP_P, "min_p": config.MIN_P, "repeat_penalty": config.REPEAT_PENALTY, "seed": seed,
                "stop": stop or [], **extra}
        t0 = time.time(); out = []; timings = {}; stop_word = ""; stop_type = ""
        async with self.client.stream("POST", self.url + "/completion", json=body) as r:
            r.raise_for_status()
            async for line in r.aiter_lines():
                if not line.startswith("data:"):
                    continue
                try:
                    d = json.loads(line[5:].strip())
                except ValueError:
                    continue
                if "error" in d and not d.get("content"):
                    raise RuntimeError(f"llama-server: {d['error']}")
                c = d.get("content", "")
                if c:
                    out.append(c)
                    if on_delta:
                        await on_delta(c)
                if d.get("stop"):
                    timings = d.get("timings", {}) or {}
                    stop_word = d.get("stopping_word", "") or ""
                    stop_type = d.get("stop_type", "") or ""
        stats = {"wall_s": round(time.time() - t0, 2), "prompt_n": timings.get("prompt_n"),
                 "cache_n": timings.get("cache_n"), "predicted_n": timings.get("predicted_n"),
                 "prompt_ms": round(timings.get("prompt_ms") or 0), "predicted_ms": round(timings.get("predicted_ms") or 0),
                 "stop_word": stop_word, "stop_type": stop_type}
        return "".join(out), stats

    async def warm(self, prompt: str):
        _, st = await self.complete(prompt, n_predict=0)
        return st

    async def close(self):
        await self.client.aclose()
