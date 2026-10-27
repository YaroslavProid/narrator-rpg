"""Honest turn order, foes by threat, the companion's turn with advice, consumables, inspection and leaving."""
import asyncio
from app import balance as B, combat as C, mechanics as M
from app.engine import TurnEngine, create_scene
from app.models import Item
from tests.test_engine_fake import FakeLLM


def mk():
    return create_scene("Т", "лес", {"name": "Ярен", "attrs": {"Сила": 14, "Ловкость": 14}},
                        [{"name": "Сильфа", "kind": "лучница", "personality": "резкая, осторожная"}],
                        "система", seed=11)


def ch(s, n):
    return M.resolve_character(s, n)


def run(s, outs, text, eng=None):
    eng = eng or TurnEngine(FakeLLM([]))
    eng.llm.outs = list(outs); eng.llm.prompts = []; ev = []

    async def emit(m): ev.append(m)
    asyncio.run(eng.handle(s, text, emit))
    return eng, ev


def rolls(ev):
    return [m["text"] for m in ev if m["type"] == "segment" and m["kind"] == "roll"]


def test_threat_gives_a_real_sheet_scaled_to_the_hero():
    s = mk(); s.player.level = 4
    eng, ev = run(s, [('<introduce name="Волк" count="2" side="враг" threat="опасный" desc="серый волк"', "/>"), ("<end", "/>")],
                  "*оглядываюсь*")
    w = ch(s, "Волк 1")
    off, hp0, base, *_ = B.THREATS["опасный"]
    assert w.threat == "опасный" and w.level == 4 + off and w.max_hp == round(hp0 * B.level_mult(w.level))
    assert w.hp == w.max_hp and w.attrs["Сила"] == base + 1                     # a beast: a little stronger
    assert w.attrs["Сила"] > w.attrs["Разум"] and any(i.name == "Клыки и когти" and i.equipped for i in w.inventory)
    boss = ch(s, "Сильфа"); boss.attrs["Ловкость"] = 15   # a built character keeps its attributes
    assert C.apply_threat(s, boss, "босс") == "" and boss.attrs["Ловкость"] == 15 and boss.threat == "босс"


def test_foes_act_by_code_and_the_turn_comes_back_to_the_hero():
    s = mk()
    eng, ev = run(s, [('<introduce name="Разбойник" side="враг" desc="с коротким мечом"', "/>"),
                      ('<attack who="Ярен" target="Разбойник"', "/>"), ("<end", "/>")], "*бью разбойника*")
    assert s.combat.active
    lines = rolls(ev)
    assert any(l.startswith("⚔️ Ярен → Разбойник") for l in lines)
    assert any(l.startswith("⚠️ Разбойник бросается на Ярен") for l in lines)   # his blow waits for a reaction
    foe = ch(s, "Разбойник")
    assert s.combat.threat == foe.id and C.current(s) is foe
    eng, ev = run(s, [("<narrate>Клинок свистит.</narrate><end", "/>")], "*уклоняюсь от удара*", eng)
    assert any(l.startswith("🛡 Ярен: уклониться") for l in rolls(ev))
    assert not s.combat.threat and C.current(s).is_player               # and it is the hero's turn again
    assert "[РЕАКЦИЯ ГЕРОЯ" in eng.llm.prompts[0]


def test_out_of_turn_actions_are_refused():
    s = mk(); foe = ch(s, "Сильфа"); M.leave_party(s, foe)
    eng, ev = run(s, [('<introduce name="Бандит" side="враг"', "/>"), ("<end", "/>")], "*жду*")
    b = ch(s, "Бандит")
    C.start_combat(s, [b.id])
    s.combat.current = next(i for i, o in enumerate(s.combat.order) if o.id == b.id)
    eng, ev = run(s, [('<attack who="Бандит" target="Ярен"', "/>"), ("<end", "/>")], "*держу оборону*", eng)
    assert "ходы врагов считает Система" in s.turns[-1]["content"] and len(eng.llm.prompts) == 1   # the turn ends there


def test_companion_turn_asks_for_advice_and_may_refuse():
    s = mk(); sil = ch(s, "Сильфа"); M.join_party(s, sil)
    sil.inventory.append(C.ensure_item_stats(Item(name="Лук", equipped=True)))
    eng, ev = run(s, [('<introduce name="Разбойник" side="враг"', "/>"), ("<end", "/>")], "*смотрю на него*")
    foe = ch(s, "Разбойник"); C.mark_foe(s, foe); foe.hp = foe.max_hp = 999      # he must survive until her turn
    eng, ev = run(s, [('<attack who="Ярен" target="Разбойник"', "/>"), ("<end", "/>")], "*бью разбойника*", eng)
    for _ in range(3):                                                 # run the order until the companion's turn comes
        if s.combat.awaiting == sil.id:
            break
        eng, ev = run(s, [('<attack who="Ярен" target="Разбойник"', "/>"), ("<end", "/>")], "*бью снова*", eng)
    assert s.combat.awaiting == sil.id
    eng, ev = run(s, [('<speak who="Сильфа" intent="отказаться — бред"', "/>"), ("Это бред.", "»"),
                      ('<attack who="Сильфа" target="Разбойник" weapon="лук"', "/>"), ("<end", "/>")],
                  "прыгай в костёр", eng)
    p = eng.llm.prompts[0]
    assert "[ХОД СПУТНИКА]" in p and "Совет героя: «прыгай в костёр»" in p and "отказывается" in p
    assert any(l.startswith("⚔️ Сильфа → Разбойник") for l in rolls(ev))


def test_companion_who_did_nothing_holds_and_the_fight_goes_on():
    s = mk(); sil = ch(s, "Сильфа"); M.join_party(s, sil)
    eng, ev = run(s, [('<introduce name="Разбойник" side="враг"', "/>"), ("<end", "/>")], "*жду*")
    C.start_combat(s, [ch(s, "Разбойник").id])
    s.combat.current = next(i for i, o in enumerate(s.combat.order) if o.id == sil.id); s.combat.awaiting = sil.id
    eng, ev = run(s, [("<narrate>Тишина.</narrate><end", "/>")], None, eng)
    assert any("выжидает" in m.get("text", "") for m in ev if m["type"] == "segment")
    assert C.current(s) is None or C.current(s).id != sil.id


def test_fight_ends_when_foes_fall_even_with_a_companion_standing():
    s = mk(); sil = ch(s, "Сильфа"); M.join_party(s, sil)
    run(s, [('<introduce name="Крыса" side="враг" threat="слабый"', "/>"), ("<end", "/>")], "*жду*")
    rat = ch(s, "Крыса"); C.start_combat(s, [rat.id]); rat.hp = 0
    text, more = C.knockout(s, rat, "")
    assert not s.combat.active and any("БОЙ ОКОНЧЕН" in m for m in more)


def test_inspection_cooldown_and_nothing_new():
    s = mk(); b = ch(s, "Сильфа"); M.leave_party(s, b); b.reveal.kind = b.reveal.attrs = False
    s.player.attrs["Воля"] = 3                                         # fail on purpose
    out = M.inspect_target(s, "осмотри Сильфу")
    if "ничего нового" in out:
        again = M.inspect_target(s, "осмотри Сильфу")
        assert "Попробуй через" in again
    b.reveal.kind = b.reveal.attrs = b.reveal.abilities = True
    assert "уже известно всё" in M.inspect_target(s, "осмотри Сильфу")


def test_consumables_have_effects():
    s = mk(); p = s.player
    for n in ("Малое зелье лечения", "Бомба", "Противоядие", "Эликсир маны"):
        p.inventory.append(C.ensure_item_stats(Item(name=n)))
    assert [i.effect for i in p.inventory[-4:]] == ["heal", "damage", "cure", "mana"]
    assert p.inventory[-4].power == 1
    p.hp = 5
    out = M.run_system_command(s, *M.parse_system_command("Система, используй Малое зелье лечения"))
    assert p.hp > 5 and "HP" in out
    assert "нужно бросить в цель" in M.run_system_command(s, *M.parse_system_command("Система, используй Бомба"))
    foe = ch(s, "Сильфа"); hp0 = foe.hp
    out = M.run_system_command(s, *M.parse_system_command("Система, брось Бомба на Сильфу"))
    assert foe.hp < hp0 and not any(i.name == "Бомба" for i in p.inventory)
    p.conditions = []; C.apply_condition(p, "кровотечение", 3, 2)
    M.run_system_command(s, *M.parse_system_command("Система, используй Противоядие"))
    assert not p.conditions


def test_only_neutral_quiet_npcs_leave_on_their_own():
    s = mk(); sil = ch(s, "Сильфа"); M.leave_party(s, sil)
    s.turn = 6; sil.last_active_turn = 1; s.settings.inactive_leave = 2
    eng = TurnEngine(FakeLLM([]))
    sil.side = "hostile"
    assert eng.prune_inactive(s) == [] and sil.id in s.present          # a foe does not wander off
    sil.side = "neutral"
    assert eng.prune_inactive(s) and sil.id not in s.present


def test_companion_waits_for_advice_and_no_blows_at_the_fallen():
    s = mk(); sil = ch(s, "Сильфа"); M.join_party(s, sil)
    eng, ev = run(s, [('<introduce name="Разбойник" side="враг"', "/>"), ("<end", "/>")], "*смотрю*")
    foe = ch(s, "Разбойник"); C.start_combat(s, [foe.id])
    s.combat.current = next(i for i, o in enumerate(s.combat.order) if o.id == sil.id); s.combat.awaiting = sil.id
    # the hero's turn text arrives while Сильфа's turn is pending -> it is advice, and only now she may act
    eng, ev = run(s, [('<attack who="Сильфа" target="Разбойник"', "/>"), ("<end", "/>")], "стреляй ему в ноги", eng)
    assert any(l.startswith("⚔️ Сильфа → Разбойник") for l in rolls(ev))
    # her action in someone else's turn (not after advice) is refused
    s.combat.active = True; s.combat.current = next(i for i, o in enumerate(s.combat.order) if o.id == sil.id)
    s.combat.awaiting = ""
    eng.comp_turn[s.id] = ""
    assert "после совета" in eng.out_of_turn(s, sil, "attack")
    foe.hp = 0
    s.combat.current = next(i for i, o in enumerate(s.combat.order) if o.id == s.player_id)
    eng, ev = run(s, [('<attack who="Ярен" target="Разбойник"', "/>"), ("<end", "/>")], "*добиваю*", eng)
    assert "уже повержен" in eng.llm.prompts[1]


def test_numbered_newcomer_is_not_mistaken_for_an_existing_one():
    s = mk()
    eng, ev = run(s, [('<introduce name="Волк 1" desc="серый"', "/>"), ('<introduce name="Волк 2" desc="поменьше"', "/>"),
                      ("<end", "/>")], "*смотрю*")
    assert ch(s, "Волк 1") is not ch(s, "Волк 2") and ch(s, "Волк 2").name == "Волк 2"


def threatened(seed=11):
    s = mk(); s.rng_seed = seed
    sil = ch(s, "Сильфа"); M.leave_party(s, sil)
    run(s, [('<introduce name="Разбойник" side="враг" desc="с мечом"', "/>"), ("<end", "/>")], "*жду*")
    foe = ch(s, "Разбойник"); C.start_combat(s, [foe.id], first=foe.id)
    acts = C.run_foes(s)
    return s, foe, acts


def test_reaction_words_and_odds():
    assert C.parse_reaction("*пригибаюсь и ухожу от удара*") == "dodge"
    assert C.parse_reaction("*подставляю щит*") == "block"
    assert C.parse_reaction("*отбиваю клинок и контратакую*") == "parry"
    assert C.parse_reaction("«Ну давай!»") == "take"
    s, foe, acts = threatened()
    assert acts and "удар ещё не нанесён" in acts[-1]["text"] and s.combat.threat == foe.id
    odds = C.reaction_odds(s)
    # Ярен: Ловкость 14, Сила 14; a regular foe swings with Сила 11
    assert odds["dodge"] == 65 and odds["block"] == 75 and odds["parry"] is None   # no weapon -> no parry
    s.player.inventory.append(C.ensure_item_stats(Item(name="Меч", equipped=True)))
    assert C.reaction_odds(s)["parry"] == 50
    assert C.threat_info(s)["foe"] == "Разбойник"


def test_repeating_a_trick_is_read_by_foes():
    s, foe, _ = threatened()
    base = C.reaction_odds(s)["dodge"]
    C.react(s, "dodge")
    s.combat.threat = foe.id
    assert C.reaction_odds(s)["dodge"] == base - 10 and C.reaction_odds(s)["take"] == C.reaction_odds(s)["take"]
    C.react(s, "block")
    s.combat.threat = foe.id
    assert C.reaction_odds(s)["dodge"] == base


def test_parry_strikes_back_and_failure_opens_up():
    s = None
    for seed in range(60):
        s, foe, _ = threatened(seed)
        s.player.inventory.append(C.ensure_item_stats(Item(name="Меч", equipped=True)))
        hp0 = s.player.hp
        r = C.react(s, "parry")
        if r["counter"]:
            assert s.player.hp == hp0 and "контратака" in r["text"]
            break
    else:
        raise AssertionError("no successful parry in 60 seeds")
    for seed in range(60):
        s, foe, _ = threatened(seed)
        s.player.inventory.append(C.ensure_item_stats(Item(name="Меч", equipped=True)))
        r = C.react(s, "parry")
        if not r["counter"]:
            assert "×1,5" in r["text"]
            break


def test_counterattack_and_gates_in_a_turn():
    for seed in range(60):
        s, foe, _ = threatened(seed)
        s.player.inventory.append(C.ensure_item_stats(Item(name="Меч", equipped=True)))
        eng = TurnEngine(FakeLLM([]))
        assert "игрок выбирает реакцию" in eng.out_of_turn(s, s.player, "attack")
        eng, ev = run(s, [("<narrate>Сталь звенит.</narrate><end", "/>")], "*парирую и контратакую*", eng)
        if any(l.startswith("↩ Контратака") for l in rolls(ev)):
            return
    raise AssertionError("no counterattack in 60 seeds")


def test_d20_and_companions_keep_the_plain_roll():
    s = create_scene("Т", "лес", {"name": "Ярен"}, [{"name": "Бандит"}], "d20", seed=3)
    b = ch(s, "Бандит"); C.start_combat(s, [b.id], first=b.id)
    acts = C.run_foes(s)
    assert acts and acts[0]["text"].startswith("⚔️ Бандит") and not s.combat.threat
    s2 = mk(); sil = ch(s2, "Сильфа"); M.join_party(s2, sil)
    s2.player.hp = 0                                  # only the companion can be targeted
    run(s2, [('<introduce name="Волк" side="враг"', "/>"), ("<end", "/>")], "*жду*")
    w = ch(s2, "Волк"); s2.combat.active = False
    C.start_combat(s2, [w.id], first=w.id)
    acts = C.run_foes(s2)
    assert acts[0]["text"].startswith("⚔️ Волк → Сильфа") and not s2.combat.threat


def test_foe_opening_blow_waits_for_reaction_and_fight_cannot_be_ended_early():
    s = mk(); M.leave_party(s, ch(s, "Сильфа"))
    eng, ev = run(s, [('<introduce name="Громила" count="2" side="враг" desc="с дубиной"', "/>"),
                      ('<attack who="Громила 1" target="Ярен"', "/>"),
                      ('<combat action="end"', "/>"), ("<end", "/>")], "*иду по тропе*")
    g1, g2 = ch(s, "Громила 1"), ch(s, "Громила 2")
    assert s.combat.active and s.combat.threat == g1.id and s.player.hp == s.player.max_hp
    assert {g1.id, g2.id} <= {o.id for o in s.combat.order}
    assert "реакцию героя" in eng.llm.prompts[2] and "на ногах ещё" in eng.llm.prompts[3]


def test_a_fleeing_foe_leaves_the_order_and_can_end_the_fight():
    s = mk(); M.leave_party(s, ch(s, "Сильфа"))
    run(s, [('<introduce name="Вор" side="враг"', "/>"), ("<end", "/>")], "*жду*")
    v = ch(s, "Вор"); C.start_combat(s, [v.id])
    eng, ev = run(s, [('<leave who="Вор"', "/>"), ("<end", "/>")], "«Беги, пока цел!»")
    assert not s.combat.active


def test_a_fallen_foe_cannot_strike_or_restart_the_fight():
    s = mk(); M.leave_party(s, ch(s, "Сильфа"))
    run(s, [('<introduce name="Громила" side="враг"', "/>"), ("<end", "/>")], "*жду*")
    g = ch(s, "Громила"); C.mark_foe(s, g); g.hp = 0
    eng, ev = run(s, [('<attack who="Громила" target="Ярен"', "/>"), ("<end", "/>")], "*стою*")
    assert not s.combat.active and "без сознания" in eng.llm.prompts[1]
    s.combat.threat = g.id; s.combat.active = True        # a stale threat from someone who fell meanwhile
    r = C.react(s, "dodge")
    assert r["text"] == "Угроза миновала." and not s.combat.active


def test_own_words_for_a_reaction_go_to_the_narrator_who_judges_and_the_code_rolls():
    s, foe, _ = threatened()
    eng = TurnEngine(FakeLLM([]))
    eng, ev = run(s, [('<react attr="Ловкость" difficulty="легко" outcome="уклон" how="бросает плащ в лицо"', "/>"),
                      ("<narrate>Плащ летит.</narrate><end", "/>")], "*срываю плащ и бросаю ему в лицо*", eng)
    assert "[РЕАКЦИЯ]" in eng.llm.prompts[0] and "<react" in eng.llm.prompts[0]
    line = next(l for l in rolls(ev) if l.startswith("🛡"))
    assert "бросает плащ в лицо" in line and "обычно" in line          # a full dodge is never «легко»
    assert not s.combat.threat and C.current(s).is_player


def test_unjudged_free_reaction_falls_back_to_the_plain_reading():
    s, foe, _ = threatened()
    eng, ev = run(s, [("<narrate>Он отскакивает.</narrate><end", "/>")], "*отскакиваю за телегу*")
    assert any(l.startswith("🛡 Ярен: уклониться") for l in rolls(ev)) and not s.combat.threat


def test_impossible_reaction_fails():
    s, foe, _ = threatened()
    r = C.react_free(s, "Разум", "невозможно", "уклон", "телепортируется")
    assert "так не выйдет" in r["text"] and s.player.hp < s.player.max_hp


def test_free_deed_on_the_heros_turn_applies_its_effect():
    s = mk(); M.leave_party(s, ch(s, "Сильфа"))
    run(s, [('<introduce name="Громила" side="враг"', "/>"), ("<end", "/>")], "*жду*")
    g = ch(s, "Громила"); C.mark_foe(s, g)
    for seed in range(40):
        s.rng_seed = seed; s.combat.active = False; g.conditions = []; s.player.conditions = []
        eng, ev = run(s, [('<deed attr="Ловкость" difficulty="легко" effect="оглушение" target="Громила" how="песок в глаза"', "/>"),
                          ("<end", "/>")], "*швыряю горсть песка ему в глаза*")
        line = next(l for l in rolls(ev) if l.startswith("✦"))
        assert "песок в глаза" in line
        if "успех" in line:
            assert "оглушение" in line and any("пропускает ход" in l for l in rolls(ev))   # he loses his next turn
            break
    else:
        raise AssertionError("the deed never landed")
    assert s.combat.active and "Опиши именно это" in eng.llm.prompts[1]


def test_deed_impossible_and_hero_turn_hint():
    s = mk()
    r = C.deed(s, s.player, ch(s, "Сильфа"), "Сила", "невозможно", "урон", "поднимает гору")
    assert not r["ok"] and "так не выйдет" in r["text"]
    M.leave_party(s, ch(s, "Сильфа"))
    run(s, [('<introduce name="Громила" side="враг"', "/>"), ("<end", "/>")], "*жду*")
    C.start_combat(s, [ch(s, "Громила").id], first=s.player_id)
    eng, ev = run(s, [("<end", "/>")], "*делаю финт и бью щитом*")
    assert "<deed" in eng.llm.prompts[0] and "оцени задуманное" in eng.llm.prompts[0]
