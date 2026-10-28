"""The numeric economy («Система»): plain numbers amplified by attributes, gear from names, HP/MP from formulas,
old saves migrated; plus the review fixes (Director notes and the Book, rewind, fight commands, cache re-warm)."""
import asyncio, copy, json
from app import balance as B, combat as C, director as D, magic as G, mechanics as M, migrate as MG
from app.engine import TurnEngine, create_scene
from app.models import Item, Scene
from tests.test_engine_fake import FakeLLM


def mk(mode="система"):
    return create_scene("Тест", "мир", {"name": "Ярен", "attrs": {"Сила": 14, "Разум": 12},
                                        "inventory": ["Короткий меч", "Кольчуга", "Щит", "Кожаная сумка"]},
                        [{"name": "Сильфа", "kind": "лучница"}, {"name": "Борек", "kind": "наёмник"}], mode, seed=5)


def ch(s, name):
    return M.resolve_character(s, name)


def run(s, outs, text, eng=None):
    eng = eng or TurnEngine(FakeLLM([]))
    eng.llm = FakeLLM(outs)
    ev = []

    async def emit(m): ev.append(m)
    asyncio.run(eng.handle(s, text, emit))
    return eng, ev


def test_gear_is_read_from_names_without_false_matches():
    kinds = {n: C.ensure_item_stats(Item(name=n)) for n in
             ["Короткий меч", "Кольчуга", "Деревянный щит", "Защитный амулет", "Кожаная сумка", "Ножны",
              "Серебряная цепочка", "Лукошко ягод", "Боевой топор", "Зелье лечения", "Зелье обмана"]}
    assert kinds["Короткий меч"].damage == "9" and kinds["Кольчуга"].armor == 2
    assert kinds["Деревянный щит"].slot == "щит" and kinds["Боевой топор"].damage == "10"
    for n in ("Защитный амулет", "Кожаная сумка", "Ножны", "Серебряная цепочка", "Лукошко ягод"):
        assert kinds[n].kind == "предмет" and not kinds[n].damage and not kinds[n].armor, n
    assert kinds["Зелье лечения"].effect == "heal" and kinds["Зелье обмана"].effect == "heal"   # a potion, not mana


def test_shield_and_armour_are_worn_together_and_take_damage_off():
    s = mk(); p = s.player
    M.equip_item(p, "Кольчуга"); M.equip_item(p, "Щит")
    assert {i.name for i in p.inventory if i.equipped} >= {"Кольчуга", "Щит"}
    assert C.armor_value(p) == B.ARMOR["кольчуг"][0] + B.ARMOR["щит"][0]
    dmg, note = C.hurt(s, p, 10, True)
    assert dmg == 10 - C.armor_value(p) and "броня" in note
    dmg, _ = C.hurt(s, p, 10, False)                    # magic passes armour
    assert dmg == 10


def test_blow_is_a_plain_number_amplified_by_strength():
    s = mk(); p = s.player; foe = ch(s, "Борек")
    M.equip_item(p, "Короткий меч")
    foe.attrs["Ловкость"] = 3                           # always hit (95%)
    foe.inventory = []
    hits = [C.attack(s, p, foe) for _ in range(10)]
    dmgs = {r["damage"] for r in hits if r["hit"]}
    assert dmgs == {round(9 * B.mult(14))}               # 9 × 1,2 = 11, every time
    assert "9×1,2 (Сила)=11" in next(r["text"] for r in hits if r["hit"])
    assert M.weapon_line(p) == "Короткий меч: урон 11 (9×Сила)"


def test_d20_spreads_the_same_numbers_and_a_trick_hits_like_the_weapon():
    s = mk("d20"); p = s.player; foe = ch(s, "Борек"); C.mark_foe(s, foe)
    M.equip_item(p, "Короткий меч"); foe.inventory = []
    base = round(9 * B.mult(14))
    vals = []
    for _ in range(30):
        foe.hp = foe.max_hp = 999
        r = C.deed(s, p, foe, "Сила", "легко", "урон", "удар с разворота")
        if r["damage"]:
            vals.append(r["damage"])
    assert vals and all(round(base * 0.8) <= v <= round(base * 1.2) for v in vals)


def test_health_and_mana_follow_the_formula_and_level():
    s = mk(); p = s.player
    assert p.max_hp == B.HP_BASE and p.max_mp == B.MP_BASE + 2 * B.MP_PER_MIND
    p.attr_points = 2
    M.run_system_command(s, *M.parse_system_command("Система, распредели Выносливость +2"))
    assert p.max_hp == B.HP_BASE + 2 * B.HP_PER_END
    M.grant_exp(s, p, p.exp_next, [])
    assert p.max_hp == B.HP_BASE + B.HP_LEVEL + 2 * B.HP_PER_END and p.hp == p.max_hp


def test_foe_sheet_from_threat_and_numbers_in_card_abilities():
    s = mk()
    foe = ch(s, "Борек"); foe.side = "hostile"; C.apply_threat(s, foe, "элита")   # the sheet, not the budget
    off, hp0, base, dmg, arm = B.THREATS["элита"]
    assert foe.level == 1 + off and foe.attrs["Сила"] == base and foe.max_hp == round(hp0 * B.level_mult(foe.level))
    w = C.equipped_weapon(foe)
    assert int(w.damage) == dmg and C.armor_value(foe) == arm
    s.player.level = 5; foe2 = ch(s, "Сильфа"); M.leave_party(s, foe2); C.apply_threat(s, foe2, "обычный")
    assert foe2.level == 5 and foe2.max_hp == round(B.THREATS["обычный"][1] * B.level_mult(5))   # grows with the hero
    a = G.card_ability({"name": "Огненный плевок", "kind": "active", "effect": "damage", "scale": "Разум"})
    assert a.power == str(B.DAMAGE[1]) and a.mp == B.MP_COST[1] and not a.path
    assert G.card_ability({"name": "Чуткий сон", "kind": "passive", "bonus": "init"}).bonus == {"init": B.PASSIVE["init"](1)}
    assert G.card_ability({"name": "Знание трав", "kind": "passive"}).effect == ""          # a narrative trait


def test_the_narrator_cannot_hand_out_exp_and_the_state_line_has_no_ac():
    s = mk(); p = s.player
    _, rej = M.apply_delta(s, p, {"exp": "50"})
    assert p.exp == 0 and any("опыт" in r for r in rej)
    line = M.state_line(s)
    assert "КБ" not in line and "броня" in line


def test_director_note_adds_up_and_abilities_set_keeps_the_book():
    s = mk(); p = s.player
    s.director_note = "Пожелание игрока (вне игры): меньше боёв"
    G.unlock_tier(s, p, "Маг")
    D.apply_patches(s, {"reply": "ок", "narrator_note": "добавь дождя",
                        "character_patches": [{"who": "Ярен", "abilities_set": [{"name": "Плащ тени", "kind": "passive"}]}]})
    assert "меньше боёв" in s.director_note and "добавь дождя" in s.director_note
    assert {a.name for a in p.abilities} == {"Магическая стрела", "Плащ тени"} and p.paths["core-mag"] == 1


def test_rewind_restores_the_note_and_the_lull():
    s = mk()
    s.director_note, s.lull = "заметка", 3
    eng, _ = run(s, [("<narrate>Тихо.</narrate><end", "/>")], "*жду*")
    assert s.director_note == ""                          # consumed by the turn
    assert eng.rewind(s, 1) and s.director_note == "заметка" and s.lull == 3


def test_items_in_a_fight_take_the_heros_turn():
    s = mk(); p = s.player; foe = ch(s, "Борек"); C.mark_foe(s, foe)
    p.inventory.append(C.ensure_item_stats(Item(name="Бомба", qty=2)))
    C.start_combat(s, [foe.id], first=foe.id)
    eng, ev = run(s, [], "Система, брось Бомба на Борек")
    assert "Сейчас ход Борек" in next(m["text"] for m in ev if m["type"] == "segment")
    assert next(i for i in p.inventory if i.name == "Бомба").qty == 2      # nothing thrown out of turn
    s.combat.current = next(k for k, o in enumerate(s.combat.order) if o.id == p.id); s.combat.threat = ""
    hp0 = foe.hp
    eng, ev = run(s, [], "Система, брось Бомба на Борек", eng)
    assert foe.hp == hp0 - B.BOMB[2]
    assert C.current(s) is None or not C.current(s).is_player or s.combat.threat or not s.combat.active
    assert "Итог прошлого хода" in s.director_note                          # the narrator describes it next turn


def test_director_and_system_agent_rewarm_the_narrator_prefix():
    s = mk()
    llm = FakeLLM([(json.dumps({"reply": "Привет.", "summary": "", "character_patches": [], "scene_patch": {}}), "")])
    warmed = []

    async def warm(prompt):
        warmed.append(prompt); return {"prompt_n": 0, "cache_n": 0}
    llm.warm = warm
    eng = TurnEngine(llm)

    async def emit(m): pass
    asyncio.run(eng._director(s, "привет", emit, chat=True))
    assert warmed and warmed[-1] == eng.prompt(s)


def _old_save() -> Scene:
    """A version-4 save: dice weapons, old armour, HP 25/25, an ability on the dice scale, a snapshot."""
    s = mk()
    p = s.player
    raw = json.loads(s.model_dump_json())
    raw["version"] = 4
    pc = raw["characters"][p.id]
    pc["max_hp"], pc["hp"], pc["max_mp"], pc["mp"] = 25, 20, 13, 13
    for it in pc["inventory"]:
        if it["name"] == "Короткий меч":
            it["damage"] = "1d8"
        if it["name"] == "Кольчуга":
            it["armor"] = 4
        if it["name"] == "Кожаная сумка":
            it.update(kind="броня", armor=2, slot="тело")
    pc["abilities"] = [{"name": "Магическая стрела", "kind": "active", "effect": "damage", "power": "1d8", "scale": "Разум",
                        "mp": 2, "path": "core-mag", "tier": 1}]
    pc["paths"] = {"core-mag": 1}
    for path in raw["paths"]:
        for t in path["tiers"]:
            for a in t["abilities"]:
                a["power"] = "1d8" if a.get("effect") == "damage" else a.get("power", "")
                a.pop("level", None)
    raw["snapshots"] = [{"turn": 0, "input": "x", "characters": copy.deepcopy(raw["characters"]), "paths": copy.deepcopy(raw["paths"]),
                         "combat": {"active": True}, "turns_len": 0, "log_len": 0, "present": raw["present"], "rng_calls": 0,
                         "summaries": [], "facts": [], "archived_upto": 0, "archived_turn": 0, "injected": [], "stats_len": 0}]
    raw["system_prompt"] = "старые правила"
    return Scene.model_validate(raw)


def test_old_saves_are_migrated_to_numbers():
    s = _old_save(); p = s.player
    assert MG.migrate(s) and s.version == MG.VERSION
    inv = {i.name: i for i in p.inventory}
    assert inv["Короткий меч"].damage == "9" and inv["Кольчуга"].armor == 2
    assert inv["Кожаная сумка"].kind == "предмет" and not inv["Кожаная сумка"].armor
    arrow = next(a for a in p.abilities if a.name == "Магическая стрела")
    assert arrow.power == str(B.DAMAGE[1]) and arrow.mp == B.MP_COST[1]
    assert p.max_hp == B.HP_BASE and p.hp == round(20 / 25 * B.HP_BASE)
    snap = s.snapshots[0]
    assert snap["characters"][p.id]["max_hp"] == B.HP_BASE and snap["combat"] == {}
    assert M.norm("Правила рассказчика") in M.norm(s.system_prompt)
    assert not MG.migrate(s)                              # nothing left to do the second time
