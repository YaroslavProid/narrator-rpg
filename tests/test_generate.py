"""Unit tests for AI scene-generation helpers (no live LLM)."""
import pytest
from app.generate import build_prompt, normalize, parse_model_json, _clamp_attrs, _clamp_skills, _inv
from app.models import ATTRS


def test_build_prompt_includes_briefs_and_mode():
    msgs = build_prompt("Таверна", "болото и долги", "d20", "Ярен", "наёмник",
                        [{"name": "Сильфа", "brief": "хозяйка"}], 0)
    assert msgs[0]["role"] == "system"
    u = msgs[1]["content"]
    assert "d20" in u and "Ярен" in u and "Сильфа" in u and "болото" in u


def test_build_prompt_npc_count_when_empty():
    u = build_prompt("", "киберпанк", "система", "", "", [], 3)[1]["content"]
    assert "3" in u and "система" in u


def test_parse_model_json_fenced():
    raw = '''```json
{"title": "X", "setting": "y"*50, "player": {}, "npcs": []}
```'''
    # setting too short after - just parse
    raw = '''```json
{"a": 1}
```'''
    assert parse_model_json(raw) == {"a": 1}


def test_parse_model_json_bare():
    assert parse_model_json('noise {"b": 2} trail') == {"b": 2}


def test_clamp_attrs_aliases_and_bounds():
    a = _clamp_attrs({"сила": 99, "Ловкость": 2, "zzz": 5})
    assert set(a) == set(ATTRS)
    assert a["Сила"] == 18
    assert a["Ловкость"] == 3


def test_clamp_skills_known_only():
    s = _clamp_skills({"Скрытность": 2, "hacking": 9, "Ближний бой": 1})
    assert "Скрытность" in s and s["Скрытность"] == 2
    assert "Ближний бой" in s
    assert "hacking" not in s


def test_inv_filters_forbidden_and_trims():
    items = _inv(["Нож", "Легендарный меч богов", "", "  Плащ  ", "x"*100])
    assert "Нож" in items and "Плащ" in items
    assert all("легенд" not in i.lower() for i in items)
    assert all(len(i) <= 40 for i in items)


def test_normalize_fills_defaults():
    data = {
        "title": "Ночь у брода",
        "setting": "Дождь стучит по крыше. В таверне пахнет дымом и дешёвым элем. " * 2,
        "player": {
            "name": "Ярен",
            "description": "Бывший наёмник с шрамом на брови и пустым кошелем.",
            "personality": "сдержанный",
            "attrs": {"Сила": 12, "Ловкость": 14},
            "skills": {"Скрытность": 2},
            "inventory": ["Короткий меч", "Нож"],
        },
        "npcs": [
            {"name": "Сильфа", "description": "Хозяйка", "personality": "острая", "secrets": "долг"},
            {"name": "", "description": "skip"},
        ],
    }
    out = normalize(data, "fallback", "prem", "Hero", 2)
    assert out["title"] == "Ночь у брода"
    assert len(out["setting"]) >= 40
    assert out["player"]["name"] == "Ярен"
    assert out["player"]["attrs"]["Сила"] == 12
    assert all(a in out["player"]["attrs"] for a in ATTRS)
    assert out["player"]["inventory"] == ["Короткий меч", "Нож"]
    assert len(out["npcs"]) == 1 and out["npcs"][0]["name"] == "Сильфа"


def test_normalize_empty_inventory_gets_kit():
    out = normalize({"title": "T", "setting": "x" * 50, "player": {"name": "A"}, "npcs": []})
    assert len(out["player"]["inventory"]) >= 3
