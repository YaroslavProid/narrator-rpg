"""Regressions from the playtest logs (sessions A and B): loops, double damage, falls, peaceful NPCs as foes."""
from app import combat as C, mechanics as M
from tests.test_combat_flow import mk, ch, run, rolls, threatened

WINDUP = "Убийца делает стремительный выпад кинжалом, целясь прямо в горло; клинка почти не видно за движением руки."


def test_a_foe_attack_from_the_narrator_ends_the_turn_instead_of_looping():
    s = mk(); M.leave_party(s, ch(s, "Сильфа"))
    run(s, [('<introduce name="Убийца" side="враг"', "/>"), ("<end", "/>")], "*жду*")
    u = ch(s, "Убийца"); C.start_combat(s, [u.id], first=s.player_id)
    outs = [(f"<narrate>{WINDUP}</narrate><attack who=\"Убийца\" target=\"Ярен\"", "/>")] * 6
    eng, ev = run(s, outs, "«Кто ты?»")
    assert len(eng.llm.prompts) == 1                      # refused once -> the turn is over, no six copies


def test_the_same_paragraph_twice_is_taken_back():
    s = mk()
    eng, ev = run(s, [(f"<narrate>{WINDUP}</narrate><speak who=\"Сильфа\" intent=\"крикнуть\"", "/>"), ("Берегись!", "»"),
                      (f"<narrate>{WINDUP}</narrate><end", "/>")], "*жду*")
    assert any(m["type"] == "retract" for m in ev)
    assert sum(1 for m in ev if m["type"] == "replace" and any(WINDUP[:30] in p["text"] for p in m["segments"])) == 1


def test_no_manual_damage_in_a_fight():
    s, foe, _ = threatened()
    hp = s.player.hp
    eng, ev = run(s, [("<narrate>Удар.</narrate><change who=\"Ярен\" hp=\"-6\"", "/>"), ("<end", "/>")], "*блокирую удар*")
    assert "урон в бою считает только Система" in s.turns[-1]["content"] or "урон в бою" in "".join(eng.llm.prompts)
    lost = hp - s.player.hp
    assert lost <= 8                                       # only the System's blow, never +6 on top


def test_a_fall_through_change_is_a_real_fall_and_a_wiped_party_does_not_softlock():
    s = mk(); M.leave_party(s, ch(s, "Сильфа"))
    run(s, [('<introduce name="Пёс" side="враг"', "/>"), ("<end", "/>")], "*жду*")
    s.player.hp = 3
    run(s, [('<change who="Ярен" hp="-5"', "/>"), ("<end", "/>")], "*падаю со скалы*")   # out of combat: allowed
    assert s.player.hp == 0 and not s.player.conditions      # down at 0 HP; no stun left to linger after healing
    s.player.hp = 5; s.player.conditions = []
    p = ch(s, "Пёс"); C.start_combat(s, [p.id])
    s.player.hp = 0
    text, more = C.knockout(s, s.player, "")
    assert not s.combat.active and any("Отряд пал" in m for m in more) and s.player.hp == round(s.player.max_hp * 0.25)


def test_a_threat_without_side_does_not_make_an_enemy():
    s = mk()
    run(s, [('<introduce name="Слуга" desc="испуганный юноша" threat="слабый"', "/>"), ("<end", "/>")], "*оглядываюсь*")
    sl = ch(s, "Слуга")
    assert sl.side != "hostile" and sl.threat == "слабый"


def test_threat_hp_does_not_jump_on_sync():
    s = mk()
    run(s, [('<introduce name="Пёс" side="враг" threat="обычный"', "/>"), ("<end", "/>")], "*жду*")
    p = ch(s, "Пёс"); hp = p.max_hp
    M.sync_all(s)
    assert p.max_hp == hp


def test_a_kick_after_a_reaction_is_refused_clearly():
    s, foe, _ = threatened()
    eng, ev = run(s, [('<react attr="Ловкость" difficulty="обычно" outcome="уклон" how="уход вбок"', "/>"),
                      ('<deed attr="Ловкость" difficulty="легко" effect="ослабление" target="Разбойник" how="пинок"', "/>"),
                      ("<end", "/>")], "*ухожу вбок и пинаю его по колену*")
    assert any("ответный удар не состоялся" in t["content"].lower() for t in s.turns if t["role"] == "user")
    assert not any(l.startswith("✦") for l in rolls(ev))


def test_a_foe_outside_the_order_joins_instead_of_striking_out_of_turn():
    s, foe, _ = threatened()
    C.react(s, "dodge"); s.combat.threat = ""
    run(s, [('<introduce name="Нож" desc="худой бандит с ножом"', "/>"), ("<end", "/>")], "*оглядываюсь*")
    n = ch(s, "Нож")
    eng, ev = run(s, [('<attack who="Нож" target="Ярен"', "/>"), ("<end", "/>")], "*держу меч*")
    assert not any(l.startswith("⚔️ Нож") for l in rolls(ev))           # no blow outside the order
    assert any(o.id == n.id for o in s.combat.order) and n.side == "hostile"


# ---- session C ----
def test_an_ooc_note_is_a_wish_not_a_turn_and_a_pending_blow_keeps_waiting():
    s, foe, _ = threatened()
    hp, turn = s.player.hp, s.turn
    eng, ev = run(s, [("<narrate>Понял.</narrate><end", "/>")], "(OOC: можно покороче, без лишних монстров)")
    assert s.combat.threat == foe.id and s.player.hp == hp and s.turn == turn and not eng.llm.prompts
    assert "без лишних монстров" in s.director_note


def test_a_blow_instead_of_a_defence_trades_blows_even_without_the_narrator():
    s, foe, _ = threatened()
    eng, ev = run(s, [("<narrate>Звон.</narrate><end", "/>")], "*рублю его мечом*")
    assert any("размен" in l for l in rolls(ev))


def test_words_only_under_a_blow_mean_no_defence_without_the_narrator_inventing_one():
    s, foe, _ = threatened()
    eng, ev = run(s, [("<narrate>Удар.</narrate><end", "/>")], "«Бруно, помоги!»")
    assert any("принять на броню" in l for l in rolls(ev)) and not s.combat.threat


def test_trading_blows_lands_the_foes_blow_and_strikes_back():
    s, foe, _ = threatened()
    s.player.inventory.append(C.ensure_item_stats(__import__("app.models", fromlist=["Item"]).Item(name="Нож", equipped=True)))
    hp = s.player.hp
    eng, ev = run(s, [('<react attr="Ловкость" difficulty="обычно" outcome="размен" how="метает нож навстречу"', "/>"),
                      ("<narrate>Сталь.</narrate><end", "/>")], "*отступаю за камень и метаю в него нож*")
    lines = rolls(ev)
    assert any("размен" in l for l in lines) and any(l.startswith("↩ Контратака") for l in lines)
    assert s.player.hp < hp


def test_change_with_an_arrow_and_garbage():
    s = mk(); s.player.hp = 0
    run(s, [('<change who="Ярен" hp="0->5"', "/>"), ("<end", "/>")], "*пью зелье*")
    assert s.player.hp >= 5                          # +5 by the arrow, then a breath of rest out of a fight
    hp = s.player.hp
    msgs, rej = M.apply_delta(s, s.player, {"hp": "3->"})
    assert s.player.hp == hp and rej and "непонятное значение" in rej[0]


def test_a_reaction_settled_after_the_narrator_is_described_next_turn():
    s, foe, _ = threatened()
    eng, ev = run(s, [("<narrate>Шорох.</narrate><end", "/>")], "*отскакиваю за телегу*")
    assert "Итог прошлого хода" in s.director_note


# ---- round 2 ----
def fight(s):
    M.leave_party(s, ch(s, "Сильфа"))
    run(s, [('<introduce name="Мародёр" side="враг"', "/>"), ("<end", "/>")], "*жду*")
    m = ch(s, "Мародёр"); C.start_combat(s, [m.id], first=s.player_id)
    return m


def test_combat_start_in_a_running_fight_is_silent_and_the_heros_blow_still_lands():
    s = mk(); m = fight(s)
    para = "Ты вскидываешь арбалет, ловишь мародёра в прицел и задерживаешь дыхание перед выстрелом."
    eng, ev = run(s, [(f'<narrate>{para}</narrate><combat action="start" foes="Мародёр"', "/>"),
                      (f'<narrate>{para}</narrate><attack who="Ярен" target="Мародёр"', "/>"), ("<end", "/>")],
                  "*стреляю в мародёра*")
    assert any(l.startswith("⚔️ Ярен → Мародёр") for l in rolls(ev))           # the repeat did not swallow the blow
    assert not any(l.startswith("⟦ БОЙ") for l in rolls(ev))                   # no status block again


def test_the_same_line_twice_is_refused():
    s = mk()
    outs = [(f'<speak who="Сильфа" intent="закричать Гони!{"!" * i}"', "/>") if k == 0 else ("Гони!", "»")
            for i in range(4) for k in (0, 1)] + [("<end", "/>")]      # near-identical lines, not the same tag
    eng, ev = run(s, outs, "*жду*")
    said = [t for t in s.turns if t["role"] == "user" and t["content"].startswith("[СИСТЕМА: результат] Сильфа:")]
    assert len(said) == 1 and any("уже сказал" in t["content"] for t in s.turns)


def test_words_on_the_heros_turn_pass_it():
    s = mk(); m = fight(s)
    eng, ev = run(s, [('<speak who="Мародёр" intent="зарычать"', "/>"), ("Р-р!", "»"), ("<end", "/>")], "«Не подходи!»")
    lines = rolls(ev)
    assert any("выжидает" in l for l in lines) and any(l.startswith("⚠️ Мародёр") for l in lines)


def test_fallen_foes_leave_the_scene_after_the_fight():
    s = mk(); m = fight(s); m.hp = 0
    C.knockout(s, m, "")
    assert not s.combat.active and m.id not in s.present


def test_doors_and_rumours_are_not_items():
    s = mk(); p = s.player; n = len(p.inventory)
    msgs, rej = M.apply_delta(s, p, {"add": "Открытая дверь склада, Гроссбух налогов"})
    assert [i.name for i in p.inventory[n:]] == ["Гроссбух налогов"] and "не предмет" in rej[0]


def test_taking_a_known_ability_spends_nothing():
    from app import magic as G
    s = mk(); p = s.player; p.ability_points = 3
    G.ensure_core(s)
    path = s.paths[0]; first = path.tiers[0].abilities[0].name
    ok, msg = G.unlock_tier(s, p, path.name)
    assert ok, msg
    pts = p.ability_points
    ok, msg = G.unlock_tier(s, p, first)
    assert not ok and "уже есть" in msg and p.ability_points == pts


def test_a_reaction_with_no_blow_forbids_describing_a_hit():
    s = mk()
    eng, ev = run(s, [('<react attr="Ловкость" difficulty="обычно" outcome="уклон" how="пригнуться"', "/>"), ("<end", "/>")],
                  "*пригибаюсь за стол*")
    assert "удара не было" in s.turns[-1]["content"] or any("удара не было" in t["content"] for t in s.turns)


def test_a_blow_under_a_pending_blow_is_a_trade_whatever_the_narrator_says():
    s, foe, _ = threatened()
    eng, ev = run(s, [('<react attr="Ловкость" difficulty="обычно" outcome="уклон" how="сместиться"', "/>"),
                      ("<end", "/>")], "*наношу удар ножом*")
    assert any("размен" in l for l in rolls(ev)) and not any("сместиться" in l for l in rolls(ev))


def test_the_counter_in_a_trade_uses_the_named_weapon():
    from app.models import Item
    s, foe, _ = threatened()
    s.player.inventory += [C.ensure_item_stats(Item(name="Легкий арбалет", equipped=True)),
                           C.ensure_item_stats(Item(name="Охотничий нож"))]
    eng, ev = run(s, [("<narrate>Сталь.</narrate><end", "/>")], "*бью его ножом*")
    assert any(l.startswith("↩ Контратака") and "Охотничий нож" in l for l in rolls(ev))


# ---- round 3 ----
def test_the_same_tag_twice_ends_the_turn_and_missing_abilities_are_refusals():
    s = mk(); M.leave_party(s, ch(s, "Сильфа"))
    outs = [('<cast who="Ярен" spell="Небесный огонь" target="Сильфа"', "/>")] * 6 + [("<end", "/>")]
    eng, ev = run(s, outs, "*колдую*")
    assert len(eng.llm.prompts) <= 2


def test_one_fighting_action_per_input_and_no_blow_without_the_players_words():
    s = mk(); m = fight(s)
    eng, ev = run(s, [('<attack who="Ярен" target="Мародёр"', "/>"), ('<attack who="Ярен" target="Мародёр"', "/>"),
                      ('<deed who="Ярен" attr="Ловкость" difficulty="легко" effect="урон" target="Мародёр" how="пинок"', "/>"),
                      ("<end", "/>")], "*бью мародёра*")
    assert sum(1 for l in rolls(ev) if l.startswith(("⚔️ Ярен", "✦ Ярен"))) == 1
    s2 = mk(); fight(s2)
    eng, ev = run(s2, [('<attack who="Ярен" target="Мародёр"', "/>"), ("<end", "/>")],
                  "*поднимаю руки и предлагаю ему золото*")
    assert not any(l.startswith("⚔️ Ярен") for l in rolls(ev)) and "не писал, что герой нападает" in s2.turns[-2]["content"]


def test_a_deed_named_like_an_ability_is_cast_as_that_ability():
    from app import magic as G
    s = mk(); m = fight(s); p = s.player; p.mp = 30
    p.abilities.append(G.make_ability("Тяжёлый удар", "active", "damage", 1, "Сила"))
    eng, ev = run(s, [('<deed attr="Сила" difficulty="обычно" effect="урон" target="Мародёр" how="тяжёлый удар"', "/>"),
                      ("<end", "/>")], "*наношу Тяжёлый удар по мародёру*")
    assert any("«Тяжёлый удар»" in l for l in rolls(ev)) and p.mp < 30


def test_an_encounter_the_party_can_stand():
    s = mk(); M.leave_party(s, ch(s, "Сильфа"))
    eng, ev = run(s, [('<introduce name="Гоблин" count="5" side="враг" threat="обычный"', "/>"), ("<end", "/>")], "*иду*")
    gob = [c for c in s.characters.values() if c.name.startswith("Гоблин") and c.side == "hostile"]
    weight = sum(C.THREAT_WEIGHT[c.threat] for c in gob)
    assert [c.threat for c in gob] == ["обычный", "слабый"] and weight <= 1.5      # one regular, one weak, no more
    assert any("не выстоять" in t["content"] for t in s.turns)


def test_rest_out_of_a_fight_and_atomic_exchange_and_worn_kit_and_vials():
    from app.engine import create_scene
    s = create_scene("Т", "мир", {"name": "Ада", "inventory": ["Короткий меч", "Кожаная броня", "Склянка с микстурой",
                                                              "Склянка с ядом"]}, [], "система", seed=2)
    p = s.player
    assert all(i.equipped for i in p.inventory if i.kind in ("оружие", "броня"))
    vial = {i.name: i.effect for i in p.inventory}
    assert vial["Склянка с микстурой"] == "heal" and vial["Склянка с ядом"] != "heal"
    p.hp = 10
    run(s, [("<narrate>Тихо.</narrate><end", "/>")], "*сижу у костра*")
    assert p.hp > 10
    n = len(p.inventory)
    msgs, rej = M.apply_delta(s, p, {"add": "Ключ", "remove": "Золотой слиток"})
    assert len(p.inventory) == n and "обмен отклонён" in rej[0]


def test_a_resource_is_not_also_an_attribute():
    from app import rules as R
    r = R.from_ai({"extra_attrs": [{"label": "Рассудок"}], "resources": [{"label": "Рассудок", "max": 10}]})
    assert not r.extra_attrs and r.resources[0].label == "Рассудок"


def test_the_fight_tier_does_not_stick_after_a_fight():
    s = mk(); m = fight(s); m.hp = 0
    C.knockout(s, m, "")
    run(s, [("<narrate>Тихо.</narrate><end", "/>")], "«Ну и денёк.»")
    assert "бой" not in " ".join(s.stats[-1]["tier_reasons"])


def test_peaceful_newcomers_who_turn_hostile_also_fit_the_budget():
    s = mk(); M.leave_party(s, ch(s, "Сильфа"))
    run(s, [('<introduce name="Гоблин" count="3" desc="у костра"', "/>"), ("<end", "/>")], "*смотрю*")
    gob = [ch(s, f"Гоблин {i}") for i in (1, 2, 3)]
    C.start_combat(s, [g.id for g in gob])
    assert sum(C.THREAT_WEIGHT[g.threat] for g in gob) <= 2.0          # обычный + слабый + слабый at most
