import asyncio, json, math, re
import pytest
from app import config, memory as MEM
from app.budget import turn_budget
from app.engine import TurnEngine
from app.models import Summary
from tests.test_mechanics import mk
from tests.test_engine_fake import FakeLLM


# ---------------- dynamic budget ----------------
def test_budget_tiers():
    # from what the hero did (deed / speech) and the fight — no keyword guessing
    one = turn_budget("«Ты боишься?»", npcs=1)
    norm = turn_budget("Осматриваю комнату и ищу следы", npcs=2)
    big = turn_budget("Выхватываю меч и атакую бандита", npcs=3, combat_ongoing=True)
    crowd = turn_budget("Иду через площадь", npcs=4)
    assert (one.tier, norm.tier, big.tier, crowd.tier) == ("камерный", "обычный", "крупный", "крупный")
    assert one.speaks < norm.speaks < big.speaks and one.step_tokens < big.step_tokens
    assert big.hard_speaks > big.speaks                      # soft bound slightly above
    assert turn_budget(None, 2, combat_ongoing=True).tier == "крупный"
    assert turn_budget(None, 1).tier == "камерный"


def test_budget_settings_and_scale_tag():
    b = turn_budget("Атакую", npcs=3, mult=2.0, combat_ongoing=True)
    assert b.speaks >= 8 or b.speaks == 10 or b.step_tokens == 840
    assert turn_budget("Атакую", npcs=3, max_speaks=2).hard_speaks == 2
    assert turn_budget("Осматриваюсь", npcs=2, scale_override="крупный").tier == "крупный"


# ---------------- memory blocks / facts ----------------
def test_memory_block_and_character_knowledge():
    s = mk(); sil = next(c for c in s.characters.values() if c.name == "Сильфа")
    assert MEM.memory_block(s) is None
    s.summaries = [Summary(layer=1, from_turn=1, to_turn=4, text="Ярен спрятал ключ.", witnesses=[s.player_id]),
                   Summary(layer=1, from_turn=5, to_turn=8, text="Сильфа назвала пароль.", witnesses=[s.player_id, sil.id])]
    MEM.add_facts(s, [{"text": "Пароль — Чёрная цапля", "known_by": ["Сильфа", "Ярен"]},
                      {"text": "Пароль — чёрная цапля!", "known_by": []},      # duplicate
                      {"text": "Ключ под третьей половицей", "known_by": ["Ярен"]}],
                  [s.player_id, sil.id], 8, lambda n: __import__("app.mechanics", fromlist=["x"]).resolve_character(s, n))
    assert len(s.facts) == 2
    mb = MEM.memory_block(s)
    assert "Ходы 1–4" in mb and "#1 Пароль" in mb and "знают: Сильфа, Ярен" in mb
    k = MEM.character_knowledge(s, sil.id)
    assert any("пароль" in x for x in k) and not any("ключ" in x.lower() for x in k)   # witness filter


# ---------------- archive / budget / rewind with a fake LLM ----------------
def summary_json(t):
    return json.dumps({"summary": t, "facts": [{"text": "Пароль контрабандистов — Чёрная цапля", "known_by": ["Сильфа"]}]},
                      ensure_ascii=False)


def play(s, eng, outs, text):
    eng.llm.outs += outs
    ev = []

    async def emit(m): ev.append(m)
    asyncio.run(eng.handle(s, text, emit))
    return ev


def test_archive_and_forced_budget(monkeypatch):
    monkeypatch.setattr(config, "ARCHIVE_BLOCK", 200); monkeypatch.setattr(config, "RECENT_MAX", 600)
    s = mk(); eng = TurnEngine(FakeLLM([]))
    for i in range(6):
        play(s, eng, [("<narrate>" + "Дождь льёт. " * 40 + "</narrate><end", "/>")], f"Жду {i}")
    eng.llm.outs += [(summary_json("Ярен ждал в таверне."), ""), (summary_json("Ещё ожидание."), ""),
                     (summary_json("Снова."), "")]
    asyncio.run(eng.background(s))
    assert s.summaries and s.archived_turn > 1 and s.archived_upto > 0
    assert s.facts and s.facts[0].known_by == [next(c.id for c in s.characters.values() if c.name == "Сильфа")]
    assert any(b.get("json_schema") for b in eng.llm.bodies)
    p = eng.prompt(s)
    assert "[ПАМЯТЬ]" in p and "Жду 0" not in p and "Жду 5" in p
    bg = [x for x in s.stats if x.get("background")][-1]
    assert bg["calls"][-1]["kind"] == "re-warm"
    # forced (hot-path) archive when over the context budget
    for i in range(4):
        play(s, eng, [("<narrate>" + "Ветер воет. " * 40 + "</narrate><end", "/>")], f"Ещё {i}")
    monkeypatch.setattr(config, "CTX_BUDGET", 900); monkeypatch.setattr(config, "GEN_RESERVE", 100)
    eng.llm.outs += [(summary_json("Принудительно."), "")] * 4
    calls = []
    total = asyncio.run(eng.ensure_budget(s, calls))
    assert any(c["kind"] == "archive (forced)" for c in calls)


def test_layer_compression(monkeypatch):
    monkeypatch.setattr(config, "L1_MAX", 4)
    s = mk(); eng = TurnEngine(FakeLLM([(json.dumps({"summary": "Сжатая сводка ходов 1-4 про таверну и долги."}), "")]))
    items = [Summary(layer=1, from_turn=i, to_turn=i, text=f"сводка {i}", witnesses=[s.player_id]) for i in range(1, 5)]
    out = asyncio.run(eng.compress(s, items, []))
    assert [x.layer for x in out].count(2) == 1 and len(out) == 3


def test_rewind_and_regenerate():
    s = mk(); eng = TurnEngine(FakeLLM([]))
    play(s, eng, [("<narrate>Первый ход.</narrate><end", "/>")], "Иду к стойке")
    play(s, eng, [('<narrate>Ты берёшь нож.</narrate><change who="Ярен" add="Ключ"', "/>"), ("<end", "/>")], "Беру ключ")
    assert any(i.name == "Ключ" for i in s.player.inventory) and s.turn == 2
    assert eng.rewind(s, 2)
    assert s.turn == 1 and not any(i.name == "Ключ" for i in s.player.inventory)
    assert "Беру ключ" not in eng.prompt(s) and len(s.snapshots) == 1
    ev = []

    async def emit(m): ev.append(m)
    eng.llm.outs += [("<narrate>Другой вариант первого хода.</narrate><end", "/>")]
    asyncio.run(eng.regenerate(s, emit))
    assert s.turn == 1 and s.regen == 1 and "Другой вариант" in eng.prompt(s) and "Первый ход" not in eng.prompt(s)
    assert ev[0]["type"] == "reload"


def test_speak_soft_cap():
    s = mk(); eng = TurnEngine(FakeLLM([])); s.settings.max_speaks = 1
    outs = []
    for _ in range(6):
        outs += [('<narrate>Шум.</narrate><speak who="Сильфа" intent="болтать"', "/>"), ("Ну и ну.", "»")]
    ev = play(s, eng, outs[:3] + outs[4:6] + [("<end", "/>")], "Спрашиваю: «Как дела?»")
    assert s.stats[-1]["speaks"] == 1
    assert "реплик достаточно" in eng.prompt(s)


def test_grammar_retry():
    s = mk(); eng = TurnEngine(FakeLLM([('<narrate>Туман.</narrate><fly to="x"', "/>"),
                                         ('<speak who="Сильфа" intent="позвать"', ""), ("Эй!", "»"), ("<end", "/>")]))
    play(s, eng, [], "Жду")
    assert "grammar" in eng.llm.bodies[1] and 'name ::= "Сильфа" | "Борек"' in eng.llm.bodies[1]["grammar"]
    assert "Сильфа: «Эй!»" in eng.prompt(s)


# ---------------- vector store (chroma) ----------------
class HashEmb:
    def embed(self, texts, kind="passage"):
        out = []
        for t in texts:
            v = [0.0] * 64
            for w in re.findall(r"\w+", t.lower()):
                v[hash(w[:5]) % 64] += 1
            n = math.sqrt(sum(x * x for x in v)) or 1
            out.append([x / n for x in v])
        return out


def test_vector_store_witness_filter(tmp_path):
    pytest.importorskip("chromadb")
    vs = MEM.VectorStore(HashEmb(), tmp_path / "chroma")
    vs.add("sc", [{"id": "a", "text": "Сильфа шепчет пароль чёрная цапля", "turn": 1, "kind": "speech", "witnesses": ["p", "sil"]},
                  {"id": "b", "text": "Борек прячет пароль чёрная цапля в сапоге", "turn": 2, "kind": "narration", "witnesses": ["bor"]},
                  {"id": "c", "text": "Дождь над болотом", "turn": 9, "kind": "narration", "witnesses": ["p"]}])
    r = vs.query("sc", "какой пароль цапля", 3, witness="p", before_turn=5, max_dist=2)
    assert [x["id"] for x in r] == ["a"]
    assert {x["id"] for x in vs.query("sc", "пароль", 3, witness="bor", max_dist=2)} == {"b"}
    vs.delete_from("sc", 2)
    assert {x["id"] for x in vs.query("sc", "пароль", 3, max_dist=2)} == {"a"}


def test_real_embedder_ranks_russian():
    if not (config.EMBED_DIR / "model_quantized.onnx").exists():
        pytest.skip("embedding model not downloaded")
    e = MEM.Embedder()
    q, a, b = e.embed(["какой пароль у контрабандистов?"], "query")[0], *e.embed(
        ["Сильфа сказала, что пароль контрабандистов — «Чёрная цапля».", "Дождь стучит по крыше таверны."])
    dot = lambda x, y: sum(i * j for i, j in zip(x, y))
    assert dot(q, a) > dot(q, b)


def test_budget_regressions():
    assert turn_budget("Ложусь спать, положив меч рядом с кроватью.", npcs=1).tier != "крупный"
    assert turn_budget("Пока все отвлеклись, прячу ключ под половицей.", npcs=2).tier == "обычный"


def test_active_npcs_and_fuzzy_fact_dedup():
    s = mk(); eng = TurnEngine(FakeLLM([]))
    play(s, eng, [('<narrate>Сильфа молчит.</narrate><speak who="Сильфа" intent="x"', "/>"), ("Ну.", "»"), ("<end", "/>")], "Жду")
    assert eng.active_npcs(s) == 1    # Сильфа spoke, Борек not mentioned
    s.turn += 2   # two quiet turns later nobody is "active"
    from app.engine import new_character
    assert eng.active_npcs(s) == 0
    MEM.add_facts(s, [{"text": "Серебряный ключ брата спрятан под третьей половицей у камина", "known_by": []}], [s.player_id], 1, lambda n: None)
    MEM.add_facts(s, [{"text": "Серебряный ключ брата был спрятан под третьей половицей у камина в таверне.", "known_by": []}], [s.player_id], 2, lambda n: None)
    assert len(s.facts) == 1


def test_batched_archive_single_prefix(monkeypatch):
    monkeypatch.setattr(config, "ARCHIVE_BLOCK", 400)
    monkeypatch.setattr(config, "ARCHIVE_MIN", 200)
    s = mk(); eng = TurnEngine(FakeLLM([]))
    for i in range(10):
        play(s, eng, [("<narrate>" + "Туман. " * 50 + "</narrate><end", "/>")], f"Шаг {i}")
    n0 = len(eng.llm.prompts)
    calls = []
    # FakeLLM returns single-block JSON by default; multi-schema needs blocks list
    eng.llm.outs.append((json.dumps({
        "blocks": [
            {"from_turn": 1, "to_turn": 3, "summary": "Сводка А про туман и ожидание в таверне.", "facts": []},
            {"from_turn": 4, "to_turn": 6, "summary": "Сводка Б: герой готовится к выходу.", "facts": []},
            {"from_turn": 7, "to_turn": 8, "summary": "Сводка В: дорога и дождь.", "facts": []},
        ]}, ensure_ascii=False), ""))
    msg = asyncio.run(eng.archive_block(s, calls, n_blocks=3))
    arch = eng.llm.prompts[n0:]
    assert msg and msg.count("Архив") >= 2 and len(s.summaries) >= 2
    assert sum(1 for c in calls if str(c.get("kind", "")).startswith("archive")) == 1  # single-pass
    assert "[АРХИВАТОР]" in arch[0] and "НЕСКОЛЬКО" in arch[0]


def test_hybrid_retrieve_prefers_facts():
    s = mk()
    MEM.add_facts(s, [{"text": "Пароль контрабандистов — Чёрная цапля", "known_by": ["Сильфа", "Ярен"]}],
                  [s.player_id, next(c.id for c in s.characters.values() if c.name == "Сильфа")], 2,
                  lambda n: __import__("app.mechanics", fromlist=["x"]).resolve_character(s, n))
    s.archived_turn = 5
    hits = MEM.retrieve(None, s, "какой был пароль цапля?", s.player_id, 3)
    assert hits and "цапл" in hits[0]["text"].lower() and hits[0]["kind"] == "fact"


def test_background_skips_rewarm_when_cancelled(monkeypatch):
    monkeypatch.setattr(config, "ARCHIVE_BLOCK", 200)
    monkeypatch.setattr(config, "ARCHIVE_MIN", 100)
    monkeypatch.setattr(config, "RECENT_MAX", 400)
    s = mk(); eng = TurnEngine(FakeLLM([]))
    for i in range(6):
        play(s, eng, [("<narrate>" + "Дождь. " * 40 + "</narrate><end", "/>")], f"Жду {i}")
    eng.bg_cancel.set()
    asyncio.run(eng.background(s))
    # cancel before lock → nothing runs, or skipped re-warm
    assert eng.bg_running is False


def test_plan_blocks_respects_min():
    eng = TurnEngine(FakeLLM([]))
    tt = [(0, 1, 500), (1, 2, 500), (2, 3, 500), (3, 4, 500), (4, 5, 500), (5, 6, 500)]
    # with ARCHIVE_MIN default 1500 and BLOCK 3000, one fat block
    blocks = eng.plan_blocks(tt, 3)
    assert blocks and all(b[3] >= config.ARCHIVE_MIN or len(blocks) == 1 for b in blocks)
