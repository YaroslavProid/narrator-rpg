from app import mechanics as M
from app.models import ATTRS
from app.engine import create_scene


def mk(mode="d20"):
    return create_scene(
        "Тест", "мир",
        {"name": "Ярен", "attrs": {"Ловкость": 14, "Воля": 12},
         "abilities": [{"name": "Ночное зрение", "kind": "passive", "description": "Видит в темноте"}],
         "inventory": ["Нож"]},
        [{"name": "Сильфа", "description": "трактирщица", "kind": "хозяйка таверны"},
         {"name": "Борек", "kind": "наёмник"}],
        mode, seed=7,
    )


def test_attrs_are_generic():
    assert "Выносливость" in ATTRS and "Влияние" in ATTRS
    assert "Телосложение" not in ATTRS
    assert "Харизма" not in ATTRS


def test_legacy_skill_resolves_to_attr():
    assert M.resolve_check_attr("stealth") == "Ловкость"
    assert M.resolve_check_attr("Скрытность") == "Ловкость"
    assert M.resolve_check_attr("Воля") == "Воля"
    assert M.resolve_check_attr("persuasion") == "Влияние"


def test_soft_weight_thresholds():
    assert M.soft_weight(10) == 0
    assert M.soft_weight(12) == 0          # gap 2 → negligible
    assert M.soft_weight(13) == 1          # gap 3 → moderate
    assert M.soft_weight(14) == 1
    assert M.soft_weight(15) == 2          # gap 5 → colossal starts
    assert M.soft_weight(18) == 4
    assert M.soft_weight(8) == 0
    assert M.soft_weight(7) == -1
    assert M.soft_weight(5) == -2


def test_name_aliases():
    s = mk()
    assert M.resolve_character(s, "Silfa").name == "Сильфа"
    assert M.resolve_character(s, "игрок").is_player


def test_check_d20_soft_deterministic():
    s1, s2 = mk(), mk()
    r1 = M.check(s1, s1.player, "stealth", "15")
    r2 = M.check(s2, s2.player, "stealth", "15")
    assert r1 == r2
    assert r1["attr"] == "Ловкость"
    assert r1["mod"] == M.soft_weight(14)  # +1
    assert r1["total"] == r1["die"] + r1["mod"]


def test_check_system_mode_soft():
    s = mk("система")
    # attr 14 vs dc 13 → moderate success; vs dc 20 → fail
    r = M.check(s, s.player, "Ловкость", 13)
    assert r["die"] is None and r["success"]
    r2 = M.check(s, s.player, "Ловкость", 20)
    assert not r2["success"]


def test_dc_clamped():
    s = mk()
    assert M.check(s, s.player, "Воля", "99")["dc"] == 30


def test_abilities_on_create():
    s = mk()
    assert any(a.name == "Ночное зрение" for a in s.player.abilities)
    assert s.characters["silfa"].kind == "хозяйка таверны"


def test_inspect_reveals():
    s = mk()
    sil = M.resolve_character(s, "Сильфа")
    assert not sil.reveal.kind
    out = M.run_system_command(s, "inspect", "осмотри Сильфу")
    assert "ОСМОТР" in out
    # may succeed or fail depending on soft roll; either way no crash
    assert sil.name == "Сильфа"


def test_public_char_hides_stats():
    s = mk()
    sil = M.resolve_character(s, "Сильфа")
    d = M.public_char_dict(sil)
    assert d["attrs"] == {} and d["abilities"] == []
    M.apply_reveal(sil, "all")
    d2 = M.public_char_dict(sil)
    assert d2["attrs"] and d2["kind_known"]


def test_clamp_hp_mp_exp():
    s = mk(); p = s.player
    msgs, rej = M.apply_delta(s, p, {"hp": "-50"})
    assert p.hp == 20 - 6 and rej
    M.apply_delta(s, p, {"hp": "+100"}); assert p.hp == 20
    M.apply_delta(s, p, {"mp": "-9"}); assert p.mp == 5
    _, rej = M.apply_delta(s, p, {"exp": "-5"}); assert p.exp == 0 and rej
    M.apply_delta(s, p, {"exp": "500"}); assert p.exp == 50
    M.apply_delta(s, p, {"exp": "80"}); assert s.turn_exp == 100 and p.level == 2 and p.exp == 0


def test_level_up_ability_points():
    s = mk(); p = s.player; msgs = []
    M.grant_exp(s, p, 120, msgs)
    assert p.level == 2 and p.attr_points == 2 and p.ability_points == 2      # 1 at the start + 1 per level
    assert any("УРОВЕНЬ" in m for m in msgs)


def test_items():
    s = mk(); p = s.player
    M.apply_delta(s, p, {"add": "Ключ, Легендарный меч богов"})
    names = [i.name for i in p.inventory]
    assert "Ключ" in names and not any("Легендар" in n for n in names)


def test_migrate_old_attrs():
    from app.models import Character
    c = Character.model_validate({
        "id": "x", "name": "Старый",
        "attrs": {"Сила": 12, "Ловкость": 11, "Телосложение": 14, "Интеллект": 10, "Мудрость": 13, "Харизма": 9},
        "skills": {"Скрытность": 2, "Ближний бой": 1},
    })
    assert c.attrs["Выносливость"] >= 14
    assert c.attrs["Разум"] == 10
    assert c.attrs["Воля"] >= 13
    assert c.attrs["Влияние"] == 9
    assert c.skills == {}


def test_rule_mode_locked_in_model():
    s = mk("система")
    assert s.rule_mode == "система" and "без бросков" in s.system_prompt
