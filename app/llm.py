"""Thin async client for llama-server raw /completion with streaming and cache metrics."""
import asyncio, json, time
import httpx
from . import config


class LLM:
    def __init__(self, url: str = None):
        self.url = (url or config.LLM_URL).rstrip("/")
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(600, connect=10))

    async def wait_idle(self, max_wait: float = 900):
        waited = 0.0
        while waited < max_wait:
            try:
                r = await self.client.get(self.url + "/slots")
                if not any(s.get("is_processing") for s in r.json()):
                    return waited
            except Exception:
                pass
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
                "top_p": 0.95, "min_p": 0.05, "repeat_penalty": 1.05, "seed": seed,
                "stop": stop or [], **extra}
        t0 = time.time(); out = []; timings = {}; stop_word = ""; stop_type = ""
        async with self.client.stream("POST", self.url + "/completion", json=body) as r:
            r.raise_for_status()
            async for line in r.aiter_lines():
                if not line.startswith("data:"):
                    continue
                d = json.loads(line[5:].strip())
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
