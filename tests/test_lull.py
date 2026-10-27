"""«Затишье»: a soft reminder after a run of quiet turns; one line in the current prompt only."""
from app import config
from app.engine import TurnEngine
from app.models import Scene
from tests.test_engine_fake import FakeLLM
from tests.test_mechanics import mk
from tests.test_memory import play
from tests.test_review_fixes import with_foe

QUIET = [("<narrate>Тихо.</narrate><end", "/>")]


def quiet_turn(s, eng, text="*жду*"):
    n = len(eng.llm.prompts)
    play(s, eng, QUIET, text)
    return "\n".join(eng.llm.prompts[n:])


def test_counter_increments_on_quiet_turns_and_resets_on_events():
    s = mk(); eng = TurnEngine(FakeLLM([]))
    for i in range(1, 4):
        quiet_turn(s, eng)
        assert s.lull == i
    play(s, eng, [('<narrate>Входит человек.</narrate><introduce name="Гонец" desc="курьер"', "/>"), ("<end", "/>")], "*жду*")
    assert s.lull == 0                                            # a new NPC is an event
    quiet_turn(s, eng); quiet_turn(s, eng)
    assert s.lull == 2
    quiet_turn(s, eng, "*иду в Каменный город*")
    assert s.lull == 0                                            # travel too
    quiet_turn(s, eng)
    play(s, eng, [('<change who="Ярен" hp="-3"', "/>"), ("<end", "/>")], "*жду*")
    assert s.lull == 0                                            # a wound too


def test_reminder_fires_at_the_threshold_once_and_is_not_stored(monkeypatch):
    monkeypatch.setattr(config, "LULL_TURNS", 3)
    s = mk(); eng = TurnEngine(FakeLLM([]))
    for _ in range(3):
        assert "[ЗАТИШЬЕ]" not in quiet_turn(s, eng)
    assert s.lull == 3
    p = quiet_turn(s, eng)
    assert "[ЗАТИШЬЕ] Несколько ходов прошло спокойно" in p and "ничего не меняй" in p
    assert s.lull == 0 and s.stats[-1]["lull_fired"] is True     # reset right after firing: no nagging
    assert eng.ephemeral.get(s.id) is None
    assert "[ЗАТИШЬЕ]" not in "".join(t["content"] for t in s.turns) and "[ЗАТИШЬЕ]" not in eng.prompt(s)
    assert "[ЗАТИШЬЕ]" not in quiet_turn(s, eng) and s.lull == 1


def test_combat_has_its_own_wording(monkeypatch):
    monkeypatch.setattr(config, "LULL_COMBAT_TURNS", 2)
    s, sil, foe = with_foe(party=False)
    s.lull = 2
    eng = TurnEngine(FakeLLM([]))
    play(s, eng, QUIET, "*осматриваюсь, держу оборону*")
    p = "\n".join(eng.llm.prompts)
    assert "Бой тянется без перемен" in p and "Несколько ходов прошло спокойно" not in p
    assert s.lull == 0 and "[ЗАТИШЬЕ]" not in "".join(t["content"] for t in s.turns)


def test_combat_turns_without_change_count_and_a_fall_resets(monkeypatch):
    s, sil, foe = with_foe(party=False)
    eng = TurnEngine(FakeLLM([]))
    play(s, eng, QUIET, "*держу оборону*")
    assert s.lull == 1 and s.combat.active
    foe.hp = 5
    play(s, eng, [('<change who="Разбойник" hp="-5"', "/>"), ("<end", "/>")], "*держу оборону*")
    assert foe.hp == 0 and s.lull == 0


def test_it_can_be_switched_off_and_old_scenes_load(monkeypatch):
    monkeypatch.setattr(config, "LULL_TURNS", 0)
    s = mk(); eng = TurnEngine(FakeLLM([])); s.lull = 99
    assert "[ЗАТИШЬЕ]" not in quiet_turn(s, eng)
    data = s.model_dump(); data.pop("lull")
    assert Scene.model_validate(data).lull == 0