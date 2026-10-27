"""Length is a soft wording, not a script: generous ceilings, no sentence counts, no early cut-offs."""
import re
from app import budget as B, config, prompts as P
from app.engine import TurnEngine
from tests.test_engine_fake import FakeLLM
from tests.test_mechanics import mk
from tests.test_memory import play


def test_ceilings_are_generous_and_hints_are_soft():
    for text, npcs, combat in (("«Привет»", 1, False), ("Осматриваю комнату", 2, False), ("Атакую", 3, True)):
        b = B.turn_budget(text, npcs, combat_ongoing=combat)
        assert b.step_tokens >= 900 and b.hard_speaks >= 6 and b.max_steps >= 6
        assert not re.search(r"\d|коротк|предложен|реплик", b.hint), b.hint        # no counts, no «коротко»
    assert B.turn_budget("«Привет»", 1).step_tokens <= B.turn_budget("Атакую", 3, combat_ongoing=True).step_tokens
    assert B.turn_budget("Атакую", 3, max_speaks=2).hard_speaks == 2             # the player's own limit still counts
    assert config.SPEAK_TOKENS >= 300


def test_prompts_carry_the_principle_without_hard_counts():
    s = mk()
    sysp = P.build_system(s)
    assert "Длина — по смыслу" in sysp and "Не раздувай" in sysp
    assert "1–3 предложения" not in sysp and "<narrate>повествование</narrate>" in sysp
    from app.models import Character
    block = P.speak_block(s, s.player, "ответить", [], "«Привет»")
    assert "1–3 фраз" not in block and "ровно столько" in block


def test_several_lines_in_one_turn_are_not_cut_early():
    s = mk(); eng = TurnEngine(FakeLLM([]))
    outs = []
    for topic in ("о погоде", "про долги трактирщика", "о новых постояльцах", "про цены на эль"):
        outs += [(f'<narrate>Шум.</narrate><speak who="Silfa" intent="болтать {topic}"', "/>"), ("Ну и ну.", "»")]
    play(s, eng, outs + [("<narrate>Тишина.</narrate><end", "/>")], "Спрашиваю: «Как дела?»")
    assert s.stats[-1]["speaks"] == 4
    assert "реплик достаточно" not in eng.prompt(s)