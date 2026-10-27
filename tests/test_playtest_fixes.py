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
    assert s.player.hp == 0 and any(x.name == "оглушение" for x in s.player.conditions)
    s.player.hp = 5; s.player.conditions = []
    p = ch(s, "Пёс"); C.start_combat(s, [p.id])
    s.player.hp = 0
    text, more = C.knockout(s, s.player, "")
    assert not s.combat.active and any("Отряд пал" in m for m in more) and s.player.hp == 1


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
    assert s.player.hp == 5
    msgs, rej = M.apply_delta(s, s.player, {"hp": "3->"})
    assert s.player.hp == 5 and rej and "непонятное значение" in rej[0]


def test_a_reaction_settled_after_the_narrator_is_described_next_turn():
    s, foe, _ = threatened()
    eng, ev = run(s, [("<narrate>Шорох.</narrate><end", "/>")], "*отскакиваю за телегу*")
    assert "Итог прошлого хода" in s.director_note
