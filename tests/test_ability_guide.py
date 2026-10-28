"""balance.py is a guide for AI abilities, not a cage: the AI's numbers and mechanics stay unless they clearly outgrow
their price; several targets, riders, a price in health, recharge and free rules work in a fight."""
from app import balance as B, combat as C, magic as G, mechanics as M
from tests.test_combat_flow import mk, ch, run, rolls


def test_the_ais_numbers_stay_within_reach_of_the_guide():
    f = B.fit("damage", 1, power=15)                      # 12 is the guide, +35% allowed -> 16
    assert f["power"] == 15 and not f["note"]
    f = B.fit("damage", 1, power=7)                       # weaker is always fine
    assert f["power"] == 7
    f = B.fit("damage", 1, power=40)                      # a clear excess is trimmed, not reset to the guide
    assert f["power"] == 16 and f["note"]


def test_the_price_buys_power_and_targets_share_it():
    cheap = B.fit("damage", 2, power=40)["power"]
    dear = B.fit("damage", 2, power=40, mp=12, recharge=2, self_hp=6)["power"]
    assert dear > cheap and dear <= 40
    aoe = B.fit("damage", 2, power=40, targets=3)["power"]
    assert aoe < cheap
    assert B.fit("control", 1, power=5)["power"] == 1 and B.fit("control", 1, power=5, mp=10, recharge=3)["power"] == 2


def test_an_ai_ability_keeps_its_own_mechanics():
    a = G.card_ability({"name": "Пепельный вихрь", "kind": "active", "effect": "damage", "scale": "Разум",
                        "power": 9, "mp": 6, "targets": 3, "rider": "горение", "rider_turns": 2})
    assert (a.power, a.mp, a.targets, a.rider, a.rider_turns) == ("9", 6, 3, "горение", 2)
    odd = G.card_ability({"name": "Обмен местами", "kind": "active", "effect": "utility",
                          "rule": "меняется местами с целью в пределах взгляда"})
    assert odd.rule.startswith("меняется местами") and odd.mp > 0
    ab = G.card_ability({"name": "Шкура медведя", "kind": "passive", "bonus": "hp", "value": 99})
    assert ab.bonus["hp"] == round(B.PASSIVE["hp"](1) * 1.5)


def test_several_targets_riders_price_and_recharge_in_a_fight():
    s = mk(); M.leave_party(s, ch(s, "Сильфа"))
    run(s, [('<introduce name="Волк" count="2" side="враг" threat="слабый"', "/>"), ("<end", "/>")], "*жду*")
    w1, w2 = ch(s, "Волк 1"), ch(s, "Волк 2")
    C.start_combat(s, [w1.id, w2.id], first=s.player_id)
    w1.hp = w1.max_hp = w2.hp = w2.max_hp = 200            # they must live to burn
    p = s.player; p.mp = 40
    a = G.make_ability("Огненная волна", "active", "damage", 2, "Разум",
                       proposal={"power": 12, "mp": 10, "targets": 2, "rider": "горение", "self_hp": 3, "recharge": 2})
    p.abilities.append(a)
    hp0, h1, h2 = p.hp, w1.hp, w2.hp
    r = C.cast(s, p, a, w1, [w2])
    assert r["ok"] and w1.hp < h1 and w2.hp < h2 and p.hp == hp0 - 3
    assert any(x.name == "горение" and x.potency > 0 for x in w1.conditions)
    assert "перезаряжается" in C.cast(s, p, a, w1, [w2])["text"]
    s.turn += 3
    assert C.cast(s, p, a, w1)["ok"] or w1.hp <= 0
    burn = w2.hp
    C.tick_conditions(s)
    assert w2.hp < burn or w2.hp == 0                    # горение hurts every round like кровотечение


def test_a_rule_reaches_the_narrator_and_all_foes_can_be_targeted():
    s = mk(); M.leave_party(s, ch(s, "Сильфа"))
    run(s, [('<introduce name="Крыса" count="3" side="враг" threat="слабый"', "/>"), ("<end", "/>")], "*жду*")
    p = s.player; p.mp = 40
    p.abilities.append(G.make_ability("Звёздный дождь", "active", "damage", 2, "Разум", proposal={"targets": 3}))
    p.abilities.append(G.make_ability("Зеркальный шаг", "active", "utility", 1, "Ловкость",
                                      proposal={"rule": "меняется местами с целью"}))
    rats = [ch(s, f"Крыса {i}") for i in (1, 2, 3)]
    hp = [r.hp for r in rats]
    eng, ev = run(s, [('<cast who="Ярен" spell="Звёздный дождь" target="все враги"', "/>"), ("<end", "/>")],
                  "*обрушиваю звёздный дождь на всех крыс*")
    assert all(r.hp < h for r, h in zip(rats, hp))
    r = C.cast(s, p, p.abilities[-1], rats[0])
    assert "Правило способности: меняется местами с целью" in r["text"]
