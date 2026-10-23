# -*- coding: utf-8 -*-
"""Soft-meta «куда вложить очки способностей» opens the abilities list but does not steal Director."""
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
