"""Player input: deed / speech (aloud, whisper, shout) / thoughts, and what the narrator may do with it;
«Система» combat: hit chance + fixed damage."""
import asyncio
from app import balance as B, combat as C, intent as I, mechanics as M
from app.engine import TurnEngine, create_scene
from app.models import Item
from tests.test_engine_fake import FakeLLM


def test_whispered_plan_is_speech_not_an_action():
    it = I.parse("*я говорю шепотом* видимо он пришел на шум, попробуем проследить за ним?")
    assert not it.acts and it.manner == "шёпотом" and it.speech == ["видимо он пришел на шум, попробуем проследить за ним?"]


def test_markup_kinds():
    it = I.parse("(странно, он врёт) *киваю* «Ладно, верю.»")
    assert it.actions == ["киваю"] and it.speech == ["Ладно, верю."] and it.thoughts == ["странно, он врёт"]
    assert I.parse("— Сколько стоит комната?").speech == ["Сколько стоит комната?"]
    assert I.parse("*думаю про себя* стоит ли ему доверять").thoughts == ["стоит ли ему доверять"]
    assert I.parse("*кричу* Сильфа, беги!").manner == "громко"
    assert I.parse("Захожу в таверну и ищу Сильфу").acts


def test_narrator_gets_parts_apart():
    block = I.for_narrator(I.parse("(он опасен) *говорю шёпотом* уходим"))
    assert "Речь героя (шёпотом" in block and "Мысли героя (никто не слышит" in block and "не делай проверок" in block


def mk(mode="d20"):
    return create_scene("Т", "мир", {"name": "Ярен", "attrs": {"Ловкость": 14, "Сила": 14}},
                        [{"name": "Сильфа"}, {"name": "Разбойник"}], mode, seed=2)


def run(s, outs, text):
    llm = FakeLLM(outs); ev = []

    async def emit(m): ev.append(m)
    asyncio.run(TurnEngine(llm).handle(s, text, emit))
    return llm, ev


def test_no_hero_check_on_pure_speech_or_continue():
    s = mk()
    llm, ev = run(s, [('<narrate>Ты шепчешь.</narrate><roll skill="Ловкость" dc="14"', "/>"), ("<end", "/>")],
                  "*говорю шепотом* проследим за ним?")
    assert not any(m["type"] == "segment" and m["kind"] == "roll" for m in ev)
    assert "Отклонено" in llm.prompts[1] and "только говорит" in llm.prompts[1]
    llm, ev = run(s, [('<narrate>Ветка хрустит.</narrate><roll skill="Ловкость" dc="16"', "/>"), ("<end", "/>")], None)
    assert not any(m["type"] == "segment" and m["kind"] == "roll" for m in ev)
    # an NPC may still act on a quiet turn
    llm, ev = run(s, [('<attack who="Разбойник" target="Ярен" weapon="короткий меч"', "/>"), ("<end", "/>")], None)
    hit = next(m["text"] for m in ev if m["type"] == "segment" and m["kind"] == "roll")
    assert "Разбойник → Ярен (короткий меч)" in hit           # named weapon used though the card has none


def test_hero_check_allowed_on_a_deed():
    s = mk()
    llm, ev = run(s, [('<narrate>Ты крадёшься.</narrate><roll skill="Ловкость" dc="14"', "/>"), ("<end", "/>")],
                  "*крадусь за ним вдоль кустов*")
    assert any(m["type"] == "segment" and m["kind"] == "roll" for m in ev)


def test_npc_does_not_hear_whisper_or_thoughts():
    from app import prompts as P
    s = mk(); sil = M.resolve_character(s, "Сильфа"); bandit = M.resolve_character(s, "Разбойник")
    M.join_party(s, sil)
    line = "(он врёт) *шепчу* уходим отсюда"
    assert "уходим отсюда" in P.speak_block(s, sil, "", [], line)           # companion beside the hero hears
    far = P.speak_block(s, bandit, "", [], line)
    assert "уходим" not in far and "шепчет" in far                          # others only notice whispering
    assert "он врёт" not in P.speak_block(s, sil, "", [], line)             # nobody hears thoughts


def test_system_mode_fixed_damage_and_hit_chance():
    s = mk("система"); p = s.player; foe = M.resolve_character(s, "Разбойник")
    p.inventory.append(C.ensure_item_stats(Item(name="Меч", equipped=True)))
    foe.inventory = []
    dmgs, hits = set(), 0
    for _ in range(200):
        foe.hp = foe.max_hp = 999
        r = C.attack(s, p, foe)
        hits += r["hit"]
        if r["hit"]:
            dmgs.add(r["damage"]); assert "шанс" in r["text"]
    expect = round(B.WEAPONS["меч"][1] * B.mult(p.attrs["Сила"]))
    assert dmgs == {expect}                       # always the same number: the sword's 9 × Strength
    assert 40 < hits < 200                        # but a blow can miss


def test_d20_mode_spreads_the_same_numbers():
    s = mk("d20")
    vals = {C.spread(s, 20)[0] for _ in range(60)}
    assert len(vals) > 3 and min(vals) >= 16 and max(vals) <= 24      # ±20% around the number


def test_starred_thoughts_like_in_sillytavern():
    it = I.parse("*интересно, он врёт?* «Где ты был вчера?»")
    assert it.thoughts == ["интересно, он врёт?"] and not it.actions and it.speech == ["Где ты был вчера?"]
    it = I.parse("*думаю, пора уходить* *встаю из-за стола*")
    assert it.thoughts == ["думаю, пора уходить"] and it.actions == ["встаю из-за стола"]
    it = I.parse("*говорю шёпотом* уходим")
    assert it.speech == ["уходим"] and it.manner == "шёпотом" and not it.thoughts


def test_the_narrator_sees_the_raw_input():
    raw = "*хм, а если это ловушка* \"Открывай\""
    block = I.for_narrator(I.parse(raw), raw)
    assert "Как написал игрок: " + raw in block
