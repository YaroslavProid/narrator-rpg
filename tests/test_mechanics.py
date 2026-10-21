from app import mechanics as M
from app.engine import create_scene


def mk(mode="d20"):
    return create_scene("Тест", "мир", {"name": "Ярен", "attrs": {"Ловкость": 14}, "skills": {"stealth": 2},
                                       "inventory": ["Нож"]},
                        [{"name": "Сильфа", "description": "трактирщица"}, {"name": "Борек"}], mode, seed=7)


def test_skill_resolution():
    assert M.resolve_skill("stealth") == "Скрытность"
    assert M.resolve_skill("Sleight of Hand") == "Ловкость рук"
    assert M.resolve_skill("Восприятия") == "Восприятие"
    assert M.resolve_skill("persuasion") == "Убеждение"
    assert M.resolve_skill("zzzz") is None


def test_name_aliases():
    s = mk()
    assert M.resolve_character(s, "Silfa").name == "Сильфа"
    assert M.resolve_character(s, "сильфу").name == "Сильфа"
    assert M.resolve_character(s, "Borek").name == "Борек"
    assert M.resolve_character(s, "игрок").is_player
    assert M.resolve_character(s, "Гэндальф") is None


def test_check_d20_deterministic():
    s1, s2 = mk(), mk()
    r1 = M.check(s1, s1.player, "stealth", "15"); r2 = M.check(s2, s2.player, "stealth", "15")
    assert r1 == r2 and r1["skill"] == "Скрытность" and r1["mod"] == 2 and r1["skill_lvl"] == 2
    assert r1["total"] == r1["die"] + 4 and 1 <= r1["die"] <= 20


def test_check_system_mode_no_dice():
    s = mk("система")
    r = M.check(s, s.player, "Скрытность", 13)
    assert r["die"] is None and r["total"] == 10 + 2 + 4 and r["success"]


def test_dc_clamped():
    s = mk(); assert M.check(s, s.player, "Восприятие", "99")["dc"] == 30


def test_clamp_hp_mp_exp():
    s = mk(); p = s.player
    msgs, rej = M.apply_delta(s, p, {"hp": "-50"})
    assert p.hp == 20 - 6 and rej                       # 30% of 20
    M.apply_delta(s, p, {"hp": "+100"}); assert p.hp == 20
    M.apply_delta(s, p, {"mp": "-9"}); assert p.mp == 5   # 50% cap
    _, rej = M.apply_delta(s, p, {"exp": "-5"}); assert p.exp == 0 and rej
    M.apply_delta(s, p, {"exp": "500"}); assert p.exp == 50
    M.apply_delta(s, p, {"exp": "80"}); assert s.turn_exp == 100 and p.level == 2 and p.exp == 0


def test_level_up():
    s = mk(); p = s.player; msgs = []
    M.grant_exp(s, p, 120, msgs)
    assert p.level == 2 and p.exp == 20 and p.exp_next == 150 and p.attr_points == 2
    assert any("УРОВЕНЬ" in m for m in msgs)


def test_items():
    s = mk(); p = s.player
    M.apply_delta(s, p, {"add": "Ключ, Легендарный меч богов"})
    names = [i.name for i in p.inventory]
    assert "Ключ" in names and not any("Легендар" in n for n in names)
    _, rej = M.apply_delta(s, p, {"remove": "Корона"}); assert rej
    M.apply_delta(s, p, {"remove": "нож"}); assert "Нож" not in [i.name for i in p.inventory]


def test_rule_mode_locked_in_model():
    s = mk("система"); assert s.rule_mode == "система" and "без бросков" in s.system_prompt
