"""Review fixes: stale companion wait, round-end falls, per-turn stun, escape/surrender, intent hardening,
hero deeds on a companion's turn, short persisted hints, travel scale."""
from app import budget as B, combat as C, intent as I, mechanics as M, prompts as P, protocol
from app.engine import TurnEngine
from tests.test_combat_flow import ch, mk, rolls, run, threatened
from tests.test_engine_fake import FakeLLM


def with_foe(party=True, first=""):
    s = mk(); sil = ch(s, "Сильфа")
    if party:
        M.join_party(s, sil)
    else:
        M.leave_party(s, sil)
    run(s, [('<introduce name="Разбойник" side="враг" desc="с мечом"', "/>"), ("<end", "/>")], "*жду*")
    foe = ch(s, "Разбойник")
    C.start_combat(s, [foe.id], first=first or s.player_id)
    return s, sil, foe


def set_turn(s, who):
    s.combat.current = next(i for i, o in enumerate(s.combat.order) if o.id == who.id)


# ---- 1. a stale «waiting for the companion» must not swallow the hero's reaction ----
def test_threat_and_step_clear_the_companion_wait():
    s, sil, foe = with_foe()
    s.combat.awaiting = sil.id
    C.threaten(s, foe, s.player)
    assert s.combat.awaiting == ""
    s.combat.threat = ""; s.combat.awaiting = sil.id
    C.step(s)
    assert s.combat.awaiting == ""


def test_free_reaction_after_a_companion_turn_is_not_taken_for_advice():
    s, sil, foe = with_foe(first="")
    set_turn(s, foe)
    s.combat.threat = foe.id; s.combat.awaiting = sil.id          # stale value left by the companion's turn
    eng, ev = run(s, [("<narrate>Он бьёт.</narrate><end", "/>")], "*кувыркаюсь за бочку*")
    p = eng.llm.prompts[0]
    assert "[РЕАКЦИЯ]" in p and "[ХОД СПУТНИКА]" not in p
    assert next(m for m in ev if m["type"] == "player")["text"] == "*кувыркаюсь за бочку*"


# ---- 2. round-end events are shown, a bleeding fall ends the fight; a stun is spent on its owner's turn ----
def test_bleeding_fall_at_round_end_is_reported_and_ends_the_fight():
    s, sil, foe = with_foe(party=False)
    foe.hp = 3; C.apply_condition(foe, "кровотечение", 3, 5)
    set_turn(s, s.player if s.combat.order[-1].id == s.player_id else foe)
    s.combat.current = len(s.combat.order) - 1
    C.step(s)                                                       # the round wraps: the tick runs
    assert foe.hp == 0 and not s.combat.active
    out = C.drain_notes(s)
    assert out and "истекает кровью" in out[0]["text"] and "БОЙ ОКОНЧЕН" in out[0]["text"]
    assert C.drain_notes(s) == []


def test_bleeding_hero_falls_but_stays_in_the_order():
    s, sil, foe = with_foe()
    s.player.hp = 2; C.apply_condition(s.player, "кровотечение", 3, 4)
    s.combat.current = len(s.combat.order) - 1
    C.step(s)
    assert s.player.hp == 0 and s.combat.active and any(o.id == s.player_id for o in s.combat.order)
    assert any("кровью" in n for n in s.combat.notes) or C.drain_notes(s)


def test_stun_is_spent_on_the_stunned_ones_own_turn():
    s, sil, foe = with_foe(party=False)
    s.combat.order.sort(key=lambda o: 0 if o.id == foe.id else 1)       # foe first, hero second
    set_turn(s, s.player)
    C.apply_condition(foe, "оглушение", 1)
    C.step(s)                                                        # round wraps, tick runs; foe's turn is next
    assert any(c.name == "оглушение" for c in foe.conditions)        # not eaten by the round tick
    out = C.run_foes(s)
    assert any("пропускает ход" in x["text"] for x in out)
    assert not any(c.name == "оглушение" for c in foe.conditions)    # applied exactly once
    assert not s.combat.threat and C.current(s).is_player


# ---- 3. the step of a free reaction ----
def test_react_remembers_the_narrator_step():
    s, foe, _ = threatened()
    eng, ev = run(s, [('<roll skill="Ловкость" dc="10"', "/>"),
                      ('<react attr="Ловкость" difficulty="обычно" outcome="уклон" how="кувырок"', "/>"),
                      ("<end", "/>")], "*кувыркаюсь вбок*")
    assert eng.reacted[s.id] == 2
    assert not s.combat.threat


# ---- 4. escape / surrender ----
def test_surrender_ends_the_fight_without_a_roll():
    s, sil, foe = with_foe(party=False)
    r = C.escape(s, "", "", "сдача", "поднимает руки")
    assert r["ended"] and not s.combat.active
    assert foe.side == "hostile" and foe.id in s.present and "решают его судьбу" in r["brief"]


def test_flight_succeeds_or_fails_by_the_code_roll():
    won = lost = False
    for seed in range(80):
        s, sil, foe = with_foe(party=False); s.rng_seed = seed
        r = C.escape(s, "Ловкость", "легко", "бегство", "бросается к двери")
        if r["ok"]:
            won = True
            assert not s.combat.active and foe.id not in s.present      # the foe stays behind
        else:
            lost = True
            assert s.combat.active and not r["ended"]                   # the turn is lost, the fight goes on
        if won and lost:
            break
    assert won and lost


def test_failed_flight_under_a_pending_blow_takes_the_hit():
    for seed in range(80):
        s, foe, _ = threatened(seed)
        hp0 = s.player.hp
        r = C.escape(s, "Ловкость", "очень трудно", "бегство", "в дверь")
        if not r["ok"]:
            assert s.player.hp < hp0 and not s.combat.threat and "попадает" in r["brief"]
            break
    else:
        raise AssertionError("flight never failed")
    s, foe, _ = threatened()
    r = C.escape(s, "Ловкость", "невозможно", "бегство", "сквозь стену")
    assert not r["ok"] and "отступать некуда" in r["text"] and s.player.hp < s.player.max_hp


def test_escape_tag_in_the_engine():
    assert "escape" in protocol.ACTION_TAGS
    s, sil, foe = with_foe(party=False)
    eng, ev = run(s, [('<escape kind="сдача" how="бросаю меч"', "/>"), ("<end", "/>")], "*бросаю меч и сдаюсь*")
    assert any(l.startswith("🏳") for l in rolls(ev)) and not s.combat.active
    assert "бой окончен" in eng.llm.prompts[1].lower()
    # outside a fight and on a turn of pure speech it is refused
    eng, ev = run(s, [('<escape kind="бегство"', "/>"), ("<end", "/>")], "*бегу*")
    assert "Боя нет" in eng.llm.prompts[1]
    s, sil, foe = with_foe(party=False)
    eng, ev = run(s, [('<escape kind="бегство"', "/>"), ("<end", "/>")], "«Давай поговорим»")
    assert "Отклонено" in eng.llm.prompts[1] and not any(l.startswith("🏃") for l in rolls(ev))


def test_escape_on_the_heros_turn_costs_the_turn():
    for seed in range(60):
        s, sil, foe = with_foe(party=False); s.rng_seed = seed
        eng, ev = run(s, [('<escape kind="бегство" attr="Ловкость" difficulty="трудно" how="к двери"', "/>"),
                          ("<end", "/>")], "*бегу к двери*")
        assert any(l.startswith("🏃") for l in rolls(ev))
        if s.combat.active:                                        # failed: the foes answered and it is the hero's turn
            assert "Опиши именно это" in eng.llm.prompts[1]
            break
    else:
        raise AssertionError("never failed")


# ---- 5. intent parsing and reaction words ----
def test_intent_is_robust_to_sloppy_markup():
    assert I.parse("— Назад!").speech == ["Назад!"]
    it = I.parse("Привет, как дела?")
    assert it.speech == ["Привет, как дела?"] and not it.acts
    it = I.parse("**бью мечом**")
    assert it.actions == ["бью мечом"]                                # no junk '* *' action
    it = I.parse("«Стой!")                                            # unclosed quote: still speech
    assert it.speech == ["Стой!"] and not it.acts
    it = I.parse("*бью волка")                                        # unclosed star: still an action
    assert it.actions == ["бью волка"] and not it.speech
    it = I.parse("*бросаю бомбу* и кричу: \"Ложись!\"")
    assert it.actions == ["бросаю бомбу"] and it.speech == ["Ложись!"] and it.manner == "громко"
    it = I.parse("Я подхожу к стражнику и говорю: «Привет»")
    assert it.actions == ["Я подхожу к стражнику"] and it.speech == ["Привет"]
    assert I.parse("Я бью волка").acts and I.parse("(думаю: ловушка").thoughts


def test_flight_and_dodge_words_are_not_taking_the_blow():
    for t in ("бегу к двери", "прыгаю в сторону", "отскакиваю", "уворачиваюсь", "*кувыркаюсь за бочку*", "ныряю под стол"):
        assert C.parse_reaction(t) == "dodge", t
    assert C.parse_reaction("закрываюсь щитом") == "block" and C.parse_reaction("стою") == "take"


# ---- 6. the hero's deed on a companion's turn is not silently dropped ----
def test_hero_deed_on_a_companions_turn_is_noted():
    s, sil, foe = with_foe()
    set_turn(s, sil); s.combat.awaiting = sil.id
    eng, ev = run(s, [('<attack who="Сильфа" target="Разбойник"', "/>"), ("<end", "/>")], "*бью разбойника*")
    assert any(m["type"] == "segment" and "не выполняется" in m["text"] for m in ev)
    assert "Герой заявил действие: «бью разбойника»" in eng.llm.prompts[0]
    assert "не считай его выполненным" in eng.llm.prompts[0]
    assert "Совет героя" in P.companion_turn(sil, "стреляй")


# ---- 7. long hints are not persisted ----
def test_history_keeps_only_short_hints():
    s, sil, foe = with_foe(party=False)
    run(s, [("<end", "/>")], "*делаю финт и бью щитом*")
    hist = "".join(t["content"] for t in s.turns if t["role"] == "user")
    assert "Бой, ход героя: оцени задуманное" in hist and "effect=" not in hist and "очень трудно" not in hist
    s, foe, _ = threatened()
    run(s, [("<end", "/>")], "*срываю плащ и бросаю ему в лицо*")
    hist = "".join(t["content"] for t in s.turns if t["role"] == "user")
    assert "[РЕАКЦИЯ]" in hist and "ответный удар" not in hist
    sysp = s.system_prompt
    assert "<escape" in sysp and "смягчить — урон вдвое меньше" in sysp and "<deed" in sysp


# ---- 8. travel keeps the big scene ----
def test_travel_deed_bumps_the_scale():
    lvl, why = B.classify("*иду в Каменный город*", 1, False, None, False)
    assert lvl == 2 and "перемещение/новое место" in why
    assert B.classify("«Привет»", 1, False, None, False)[0] == 0