
"""Combat, inventory equip/use, conditions, auto-prune."""
import asyncio
from app import combat as C, mechanics as M, config
from app.engine import TurnEngine
from app.models import Item
from tests.test_mechanics import mk
from tests.test_engine_fake import FakeLLM
from tests.test_memory import play


def test_attack_d20_and_damage():
    s = mk(); p, foe = s.player, next(c for c in s.characters.values() if c.name == "Борек")
    p.inventory.append(C.ensure_item_stats(Item(name="Короткий меч")))
    M.equip_item(p, "Короткий меч")
    r = C.attack(s, p, foe, "Короткий меч")
    assert s.combat.active and "→" in r["text"] and foe.hp <= foe.max_hp


def test_combat_initiative_and_end():
    s = mk()
    text = C.start_combat(s, [c.id for c in s.characters.values()])
    assert "Инициатива" in text and s.combat.round == 1
    assert "БОЙ ОКОНЧЕН" in C.end_combat(s) and not s.combat.active


def test_conditions_tick():
    s = mk(); p = s.player
    C.apply_condition(p, "кровотечение", turns=2, potency=3)
    hp0 = p.hp
    C.start_combat(s, [p.id, next(c.id for c in s.characters.values() if not c.is_player)])
    for _ in range(len(s.combat.order)):          # one full round: bleeding ticks once at its end
        C.step(s)
    assert p.hp == hp0 - 3 and any(c.name == "кровотечение" and c.turns_left == 1 for c in p.conditions)


def test_equip_use_give_commands():
    s = mk(); p = s.player
    p.inventory.append(C.ensure_item_stats(Item(name="Зелье лечения", kind="расходник")))
    assert "Экипировано" in M.run_system_command(s, "equip", "экипируй нож")
    assert any(i.equipped for i in p.inventory if "нож" in i.name.lower())
    old = p.hp; p.hp = 10
    assert "HP" in M.run_system_command(s, "use", "используй зелье лечения")
    assert p.hp > 10
    sil = next(c for c in s.characters.values() if c.name == "Сильфа")
    assert "отдаёт" in M.run_system_command(s, "give", "дай нож сильфе")
    assert any(i.name.lower() == "нож" for i in sil.inventory)


def test_auto_prune_inactive(monkeypatch):
    monkeypatch.setattr(config, "INACTIVE_LEAVE", 2)
    s = mk(); eng = TurnEngine(FakeLLM([]))
    # play one turn where only Сильфа speaks
    play(s, eng, [('<narrate>Шум.</narrate><speak who="Сильфа" intent="x"', "/>"), ("Привет.", "»"), ("<end", "/>")], "Жду")
    borek = next(c for c in s.characters.values() if c.name == "Борек")
    # advance turns without mentioning Борек
    for i in range(3):
        play(s, eng, [("<narrate>Тишина.</narrate><end", "/>")], f"Жду ещё {i}")
    assert borek.id not in s.present
    assert borek.id in s.characters  # still in roster
