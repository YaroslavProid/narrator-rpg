"""Turn loop with a scripted fake LLM (no server)."""
import asyncio
from app.engine import TurnEngine
from tests.test_mechanics import mk


class FakeLLM:
    def __init__(self, outs): self.outs = list(outs); self.prompts = []; self.bodies = []

    async def tokenize(self, text): return len(text) // 4

    async def warm(self, prompt): return {"prompt_n": 0, "cache_n": 0}

    async def complete(self, prompt, n_predict, stop=None, on_delta=None, seed=-1, temperature=None, **kw):
        self.prompts.append(prompt); self.bodies.append(kw)
        if not self.outs and "json_schema" in kw:
            schema = kw["json_schema"]
            if schema.get("required") == ["blocks"]:
                return '{"blocks": [{"from_turn": 1, "to_turn": 2, "summary": "Сводка по умолчанию о событиях.", "facts": []}]}', {"prompt_n": 1, "cache_n": 0, "predicted_n": 5}
            return '{"summary": "Сводка по умолчанию о событиях.", "facts": []}', {"prompt_n": 1, "cache_n": 0, "predicted_n": 5}
        text, sw = self.outs.pop(0)
        if on_delta: await on_delta(text)
        return text, {"prompt_n": 1, "cache_n": len(prompt), "predicted_n": 5, "stop_word": sw}


def run(outs, text="Я осматриваюсь", s=None):
    s = s or mk(); llm = FakeLLM(outs); ev = []

    async def emit(m): ev.append(m)
    st = asyncio.run(TurnEngine(llm).handle(s, text, emit))
    return s, llm, ev, st


def test_full_turn_inline_speech_and_roll():
    s, llm, ev, st = run([
        ('<narrate>Дождь стучит по крыше.</narrate>\n<speak who="Silfa" intent="поприветствовать"', "/>"),
        ("Заходи, промок ведь.", "»"),
        ('<narrate>Ты тянешься к ключу.</narrate>\n<roll skill="sleight_of_hand" dc="12"', "/>"),
        ("<narrate>Борек ничего не заметил.</narrate>\n<end", "/>"),
    ])
    end = ev[-1]
    assert end["type"] == "turn_end"
    assert end["text"].startswith("*Дождь стучит по крыше.*\nСильфа: Заходи, промок ведь.\n*Ты тянешься к ключу.*")
    assert "Ловкость" in end["text"]
    # append-only: every later prompt starts with the earlier narrator prompt (minus the open model header)
    assert llm.prompts[2].startswith(llm.prompts[0])
    assert "[РЕПЛИКА]" in llm.prompts[1] and "[РЕПЛИКА]" not in llm.prompts[2]
    assert "[СИСТЕМА: результат] Сильфа: «Заходи, промок ведь.»" in llm.prompts[2]
    # witness: Сильфа's knowledge only contains witnessed events
    assert st["steps"] == 3 and st["speaks"] == 1


def test_system_command_no_llm():
    s, llm, ev, st = run([], "Система, статус")
    assert llm.prompts == [] and "СТАТУС" in ev[1]["text"]


def test_parse_retry_then_fallback():
    s, llm, ev, st = run([
        ('<narrate>Туман.</narrate><fly to="x"', "/>"),
        ("<oops", "/>"),
        ("<narrate>Туман густеет.</narrate>", ""),
    ])
    assert ev[-1]["type"] == "turn_end" and "Туман густеет" in ev[-1]["text"]


def test_english_regenerated():
    s, llm, ev, st = run([
        ("<narrate>The rain is falling on the roof of the old tavern.</narrate>", ""),
        ("<narrate>Дождь падает на крышу старой таверны.</narrate>", ""),
    ])
    assert any(e["type"] == "retract" for e in ev) and "Дождь" in ev[-1]["text"]


def test_introduce_and_witness():
    s, llm, ev, st = run([
        ('<narrate>Дверь распахивается.</narrate><introduce name="Мирна" desc="травница"', "/>"),
        ('<narrate>Она садится.</narrate><speak who="Мирна" intent="представиться"', "/>"),
        ("Я Мирна.", "»"),
        ("<end", "/>"),
    ])
    m = next(c for c in s.characters.values() if c.name == "Мирна")
    assert m.id in s.present
    from app.engine import TurnEngine as T
    knows = T(None).recent_knows(s, m)
    assert not any("Дверь распахивается" in k for k in knows)   # happened before she entered
    assert "Карточка:" in llm.prompts[2]                          # not in fixed block -> inline card


class BrokenLLM(FakeLLM):
    async def complete(self, *a, **kw):
        if not self.outs:
            raise RuntimeError("llama-server недоступен")
        return await super().complete(*a, **kw)


def test_failed_turn_rolls_back():
    s, llm, ev, st = run([("<narrate>Первый ход.</narrate><end", "/>")], "Иду к стойке")
    turns, log, snaps = len(s.turns), len(s.log), len(s.snapshots)
    eng = TurnEngine(BrokenLLM([])); ev2 = []

    async def emit(m): ev2.append(m)
    try:
        asyncio.run(eng.handle(s, "Беру ключ", emit)); assert False, "must raise"
    except RuntimeError:
        pass
    assert s.turn == 1 and len(s.turns) == turns and len(s.log) == log and len(s.snapshots) == snaps
    assert ev2[-1]["type"] == "reload" and ev2[-1]["scene"]["turn"] == 1


def test_failed_regenerate_keeps_old_turn():
    s, llm, ev, st = run([("<narrate>Первый ход.</narrate><end", "/>")], "Иду к стойке")
    eng = TurnEngine(BrokenLLM([])); ev2 = []

    async def emit(m): ev2.append(m)
    try:
        asyncio.run(eng.regenerate(s, emit)); assert False, "must raise"
    except RuntimeError:
        pass
    assert s.turn == 1 and "Первый ход" in eng.prompt(s) and s.regen == 0
    assert ev2[0]["type"] == "reload" and ev2[0]["busy"] and ev2[-1]["busy"] is False


def test_snapshots_capped(monkeypatch):
    from app import config
    monkeypatch.setattr(config, "SNAPSHOTS", 2)
    s = None
    for i in range(4):
        s, *_ = run([(f"<narrate>Ход {i}.</narrate><end", "/>")], "Жду", s)
    assert len(s.snapshots) == 2 and TurnEngine(None).payload(s)["rewind_from"] == 3
