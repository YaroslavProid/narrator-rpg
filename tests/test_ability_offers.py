# -*- coding: utf-8 -*-
"""Ability offers: reshuffle on level-up, locks survive; soft-meta does not steal Director."""
from app import mechanics as M
from app.engine import create_scene


def mk():
    return create_scene(
        "Тест", "мир",
        {"name": "Ярен", "attrs": {"Ловкость": 14, "Сила": 13}},
        [{"name": "Сильфа", "description": "лучница", "kind": "лучница"}],
        "d20", seed=9,
    )


def test_soft_meta_skips_director_chat():
    # would match soft-meta abilities, but director keyword prevents steal
    assert M.parse_system_command("Система, директор подскажи про способности и очки")[0] == "director"
    # bare "директор, ..." is not a Система-command and must NOT become abilities
    assert M.parse_system_command("директор, куда вложить очки способностей") is None


def test_soft_meta_abilities_still_works():
    assert M.parse_system_command("*я смотрю куда я могу вложить очки способностей*")[0] == "abilities"


def test_offers_stable_mid_level_then_reshuffle_on_level():
    s = mk()
    p = s.player
    p.ability_points = 1
    a = [x["name"] for x in M.ability_offers(s, 3)]
    b = [x["name"] for x in M.ability_offers(s, 3)]
    assert a == b and len(a) == 3
    # lock first
    assert "Зафиксировано" in M.lock_ability_offer(s, a[0])
    assert a[0] in p.ability_locks
    locked = a[0]
    # level up → reshuffle, locked stays
    msgs = []
    M.grant_exp(s, p, p.exp_next - p.exp, msgs)
    assert p.level >= 2
    assert any("перемешан" in m or "УРОВЕНЬ" in m for m in msgs)
    offers2 = [x["name"] for x in M.ability_offers(s, 3)]
    assert locked in offers2
    assert locked in p.ability_locks
    # unlocked slots may differ from original set
    assert len(offers2) == 3


def test_unlock_and_take_clears_lock():
    s = mk()
    p = s.player
    p.ability_points = 1
    offers = M.ability_offers(s, 3)
    name = offers[0]["name"]
    M.lock_ability_offer(s, name)
    assert name in p.ability_locks
    M.unlock_ability_offer(s, name)
    assert name not in p.ability_locks
    M.lock_ability_offer(s, name)
    out = M.take_ability(s, name)
    assert "Открыто" in out or "открыт" in out.lower() or name in out
    assert name not in p.ability_locks
    assert any(a.name == name for a in p.abilities)
