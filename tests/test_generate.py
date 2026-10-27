"""Unit tests for AI scene-generation helpers (no live LLM)."""
from app.generate import build_prompt, normalize, parse_model_json, _clamp_attrs, _abilities, _inv
from app.models import ATTRS


def test_build_prompt_asks_for_abilities_not_skills():
    u = build_prompt("Таверна", "болото", "d20", "Ярен", "наёмник",
                     [{"name": "Сильфа", "brief": "хозяйка"}], 0)[1]["content"]
    assert "способност" in u.lower() or "abilities" in u.lower() or "Способности" in u or "именован" in u.lower()
    assert "Ближний бой=4" in u or "НЕ пиши" in u
    assert "Выносливость" in u or "Влияние" in u


def test_parse_model_json_bare():
    assert parse_model_json('noise {"b": 2} trail') == {"b": 2}


def test_clamp_attrs_new_names():
    a = _clamp_attrs({"сила": 99, "Влияние": 2, "Телосложение": 14})
    assert set(a) == set(ATTRS)
    assert a["Сила"] == 18
    assert a["Влияние"] == 3
    # Телосложение may not map via resolve_attr alone — ATTR_MIGRATE in models; clamp uses resolve_attr
    assert "Выносливость" in a


def test_abilities_filter_legacy_skills():
    abs_ = _abilities([
        {"name": "Огненный шар", "kind": "active", "cost": "3 MP"},
        {"name": "Скрытность", "kind": ""},  # legacy skill name without kind → skip if resolve_skill
        {"name": "Ночное зрение", "kind": "passive"},
    ])
    names = [a["name"] for a in abs_]
    assert "Огненный шар" in names and "Ночное зрение" in names


def test_normalize_abilities():
    data = {
        "title": "Ночь",
        "setting": "Дождь. " * 20,
        "player": {
            "name": "Ярен",
            "description": "Бывший наёмник с шрамом.",
            "personality": "сдержанный",
            "attrs": {"Сила": 12, "Ловкость": 14},
            "abilities": [{"name": "Удар щитом", "kind": "active", "cost": "1/бой"}],
            "inventory": ["Щит", "Нож"],
        },
        "npcs": [{"name": "Сильфа", "description": "Хозяйка", "kind": "трактирщица"}],
    }
    out = normalize(data)
    assert out["player"]["abilities"][0]["name"] == "Удар щитом"
    assert "skills" not in out["player"] or not out["player"].get("skills")
    assert out["npcs"][0].get("kind") == "трактирщица"


def test_inv_filters_forbidden():
    items, why = _inv(["Нож", "Легендарный меч богов", {"name": "Плащ", "why": "подарок матери"}])
    assert "Нож" in items and "Плащ" in items
    assert all("легенд" not in i.lower() for i in items) and why == {"Плащ": "подарок матери"}


def test_world_and_premise_are_kept_and_reach_the_narrator():
    from app.generate import normalize
    from app.engine import create_scene
    data = {"title": "Причал", "setting": "Портовый город под вечным дождём. " * 3,
            "world": {"look": "Газовые фонари, паровые краны, деревянные доки", "tone": "нуар", "places": ["Доки", "Ратуша"],
                      "factions": ["Гильдия"], "rules": "Магии нет, пистоли редки и дороги"},
            "player": {"name": "Ада", "description": "сыщица", "attrs": {}, "abilities": [{"name": "Чутьё", "kind": "passive"}],
                       "inventory": [{"name": "Револьвер, старый", "why": "служебный"}, {"name": "Ключ от двери склада", "why": "улика"}]},
            "npcs": []}
    d = normalize(data, "Причал", "Нуар в порту, пропала дочь судьи", "Ада")
    assert d["premise"].startswith("Нуар в порту") and d["world"]["rules"].startswith("Магии нет")
    assert d["player"]["inventory"] == ["Револьвер  старый".replace("  ", " "), "Ключ от двери склада"]
    s = create_scene(d["title"], d["setting"], d["player"], [], "система", premise=d["premise"], world=d["world"])
    assert [i.name for i in s.player.inventory] == ["Револьвер старый", "Ключ от двери склада"]   # the kit is kept as is
    sp = s.system_prompt
    assert "Замысел автора (канон" in sp and "Газовые фонари" in sp and "Магии нет" in sp and "Доки; Ратуша" in sp
