"""The world's ruleset as data: names of attributes, own attributes and resources, conditions, core paths, weapons."""
from app import combat as C, magic as G, mechanics as M, rules as R
from app.engine import create_scene
from app.models import Item
from tests.test_combat_flow import run, rolls

NOIR = {"name": "нуар 1890-х", "attr_labels": {"Разум": "Смекалка", "Влияние": "Обаяние"},
        "extra_attrs": [{"label": "Механика", "hint": "паровые машины и замки"}],
        "mp_label": "Выдержка",
        "resources": [{"label": "Рассудок", "max": 10, "hint": "ужас и бессонница", "zero": "срыв: герой бежит прочь"}],
        "conditions": [{"name": "страх", "kind": "mark", "hint": "дрожат руки"}, {"name": "ожог паром", "kind": "dot"}],
        "paths": ["core-voin", "core-ten", "core-sledopyt"],
        "weapons": [{"name": "Револьвер «Веблей»", "damage": 12, "ranged": True}, {"name": "Трость со шпагой", "damage": 8}],
        "currency": "фунты и шиллинги"}


def noir():
    return create_scene("Сальм", "Дождливый порт.", {"name": "Ада", "inventory": ["Револьвер «Веблей»", "Трость со шпагой"]},
                        [{"name": "Кросс", "kind": "судья"}], "система", seed=4, rules=NOIR)


def test_attribute_names_and_own_attributes():
    s = noir()
    assert R.resolve(s, "Смекалка") == "Разум" and R.resolve(s, "механика") == "Механика"
    r = M.check(s, s.player, "Смекалка", 12)
    assert "Смекалка" in r["text"] and r["attr"] == "Разум"
    assert M.check(s, s.player, "Механика", 12)["attr"] == "Механика"
    s.player.attr_points = 1
    out = M.run_system_command(s, *M.parse_system_command("Система, распредели Механику +1"))
    assert s.player.attrs["Механика"] == 11, out
    sp = s.system_prompt
    assert "Смекалка" in sp and "Рассудок 0–10" in sp and "фунты" in sp and "Выдержка" in sp


def test_resources_change_and_hit_zero():
    s = noir(); p = s.player
    eng, ev = run(s, [('<change who="Ада" res="Рассудок -4"', "/>"), ('<change who="Ада" рассудок="-9"', "/>"),
                      ("<end", "/>")], "*смотрю в глаза утопленнику*")
    assert p.res["Рассудок"] == 0 and any("на нуле — срыв" in t["content"] for t in s.turns)
    assert "Рассудок 0/10" in M.state_line(s)


def test_a_world_without_magic_has_no_mage_path():
    s = noir(); G.ensure_core(s)
    ids = {p.id for p in G.core_paths(s)}
    assert ids == {"core-voin", "core-ten", "core-sledopyt"}


def test_world_weapons_and_conditions():
    s = noir(); p = s.player
    rev = next(i for i in p.inventory if i.name.startswith("Револьвер"))
    assert rev.damage == "12" and rev.ranged and C.is_ranged(rev)
    assert R.is_dot(s, "ожог паром") and not R.is_dot(s, "страх")
    p.conditions = []; C.apply_condition(p, "ожог паром", 2, 3)
    hp = p.hp; C.tick_conditions(s)
    assert p.hp == hp - 3


def test_the_ui_gets_the_schema_and_old_scenes_keep_defaults():
    s = noir()
    from app.engine import TurnEngine
    st = TurnEngine(None).state(s)
    assert [a["label"] for a in st["rules"]["attrs"]][:6] == ["Сила", "Ловкость", "Выносливость", "Воля", "Смекалка", "Обаяние"]
    assert st["rules"]["resources"][0]["label"] == "Рассудок" and st["characters"][s.player_id]["res"]["Рассудок"] == 10
    plain = create_scene("Т", "мир", {"name": "Ярен"}, [], "d20", seed=1)
    assert plain.rules.mp_label == "Мана" and len(plain.rules.paths) == 5 and not plain.rules.resources
