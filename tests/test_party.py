"""Hero's party: joining (limit 4 with the hero), level 1 start, full card, progression by the player,
catch-up EXP, kill EXP for everyone, hostile list, AI completion of the card."""
import asyncio, json
from app import config, mechanics as M
from app.engine import TurnEngine, create_scene
from app.protocol import parse_tag
from tests.test_engine_fake import FakeLLM


def mk():
    return create_scene(
        "Тест", "мир", {"name": "Ярен", "attrs": {"Ловкость": 14}},
        [{"name": "Сильфа", "kind": "хозяйка таверны"}, {"name": "Борек", "kind": "наёмник"},
         {"name": "Мирна", "kind": "травница"}, {"name": "Гвоздь", "kind": "главарь"}],
        "d20", seed=3,
    )


def ch(s, name):
    return M.resolve_character(s, name)


def test_join_starts_at_level_one_with_full_card():
    s = mk(); sil = ch(s, "Сильфа"); sil.level = 4; sil.exp = 70
    ok, msg = M.join_party(s, sil)
    assert ok and s.party == [sil.id] and sil.side == "party" and sil.level == 1 and sil.exp == 0
    assert sil.reveal.attrs and sil.reveal.abilities and sil.id in s.pending_fill
    d = M.public_char_dict(sil)
    assert "inventory" in d and d["attrs"]          # companions are shown in full, like the hero


def test_party_limit_is_four_with_the_hero():
    s = mk()
    for n in ("Сильфа", "Борек", "Мирна"):
        assert M.join_party(s, ch(s, n))[0]
    ok, msg = M.join_party(s, ch(s, "Гвоздь"))
    assert not ok and "полон" in msg and len(M.party_ids(s)) == config.PARTY_MAX
    assert M.leave_party(s, ch(s, "Борек"))[0] and M.join_party(s, ch(s, "Гвоздь"))[0]


def test_kill_reward_is_a_share_of_own_level_and_catches_up():
    s = mk(); sil = ch(s, "Сильфа"); M.join_party(s, sil)
    foe = ch(s, "Гвоздь"); foe.level = 5; foe.threat = "обычный"
    s.player.level, s.player.exp_next = 5, 506
    hero = M.share_exp(s.player, M.kill_share(s.player, foe))
    comp = M.share_exp(sil, M.kill_share(sil, foe))
    assert hero == round(506 * 0.08)                              # same level: 8% of the hero's level
    assert comp == round(100 * 0.08 * 1.4 ** 4)                   # 4 levels above the companion: x1.4^4
    assert comp / sil.exp_next > hero / s.player.exp_next         # the one behind grows faster by itself
    assert M.award_exp(s, ch(s, "Борек"), 10, []) == 0            # outsiders get nothing


def test_beating_something_far_stronger_gives_several_levels():
    s = mk(); p = s.player
    boss = ch(s, "Гвоздь"); boss.threat = "босс"; boss.level = p.level + 5
    msgs = []
    M.award_exp(s, p, M.share_exp(p, M.kill_share(p, boss)), msgs, capped=False)
    assert p.level >= 3, msgs


def test_individual_exp_cap_per_character():
    s = mk(); sil = ch(s, "Сильфа"); M.join_party(s, sil)
    got = M.award_exp(s, sil, 150, [])
    assert got == round(sil.exp_next * M.TURN_CAP) and M.award_exp(s, sil, 10, []) == 0
    assert M.award_exp(s, s.player, 10, []) == 10           # the hero has his own cap


def test_player_levels_up_companion():
    s = mk(); sil = ch(s, "Сильфа"); M.join_party(s, sil)
    M.grant_exp(s, sil, 100, [])
    assert sil.level == 2 and sil.attr_points == 2 and sil.ability_points == 2   # 1 on joining + 1 per level
    lov = sil.attrs["Ловкость"]
    out = M.run_system_command(s, *M.parse_system_command("Система, распредели Сильфе Ловкость +1"))
    assert sil.attrs["Ловкость"] == lov + 1 and sil.attr_points == 1 and "Сильфа" in out
    out = M.run_system_command(s, *M.parse_system_command("Система, открой Сильфе путь Следопыта"))
    assert sil.paths.get("core-sledopyt") == 1 and sil.ability_points == 1, out
    assert any(a.name == "Меткий выстрел" for a in sil.abilities)
    assert s.player.ability_points == 1 and not s.player.paths


def test_party_and_dismiss_commands():
    s = mk(); M.join_party(s, ch(s, "Сильфа"))
    assert M.parse_system_command("Система, отряд")[0] == "party"
    assert "Сильфа" in M.run_system_command(s, "party", "")
    out = M.run_system_command(s, *M.parse_system_command("Система, отпусти Сильфу"))
    assert "покидает" in out and s.party == []


def test_new_tags_parse():
    assert parse_tag('<join who="Сильфа"/>').name == "join"
    assert parse_tag('<part who="Сильфа"/>').name == "part"
    assert parse_tag('<reveal who="Борек" what="all"/>').name == "reveal"


def run_turn(s, outs, text="Иду дальше"):
    llm = FakeLLM(outs); ev = []

    async def emit(m): ev.append(m)
    eng = TurnEngine(llm)
    asyncio.run(eng.handle(s, text, emit))
    return eng, llm, ev


def test_join_tag_in_a_turn_then_ai_fills_card():
    s = mk()
    eng, llm, ev = run_turn(s, [
        ('<narrate>Сильфа снимает фартук.</narrate>\n<join who="Сильфа"', "/>"),
        ("<narrate>Она берёт лук.</narrate><end", "/>"),
    ], "Сильфа, пойдёшь со мной?")
    sil = ch(s, "Сильфа")
    assert sil.id in s.party and s.pending_fill == [sil.id]
    assert any(m["type"] == "segment" and "ОТРЯД" in m["text"] for m in ev)
    patch = {"reply": "Готово.", "summary": "Карточка Сильфы дополнена",
             "character_patches": [{"who": "Сильфа", "kind": "лучница", "attrs": {"Ловкость": 15},
                                    "inventory_add": ["Охотничий лук"]}]}
    llm.outs.append((json.dumps(patch, ensure_ascii=False), ""))
    out = []

    async def emit(m): out.append(m)
    asyncio.run(eng.fill_party(s, emit))
    assert s.pending_fill == [] and sil.kind == "лучница" and sil.attrs["Ловкость"] == 15
    assert any(i.name == "Охотничий лук" for i in sil.inventory)
    assert out[-1]["state"]["party"] == [s.player_id, sil.id]


def test_companion_stays_in_scene():
    s = mk(); sil = ch(s, "Сильфа"); M.join_party(s, sil)
    s.settings.inactive_leave = 1; s.turn = 10; sil.last_active_turn = 0
    TurnEngine(None).prune_inactive(s)
    assert sil.id in s.present
    eng, llm, ev = run_turn(s, [('<narrate>Ночь.</narrate><leave who="Сильфа"', "/>"), ("<end", "/>")])
    assert sil.id in s.present and "<part" in llm.prompts[1]


def test_introduce_pack_of_foes():
    s = mk()
    eng, llm, ev = run_turn(s, [
        ('<narrate>Из тумана выходят твари.</narrate><introduce name="Лесная тварь" count="3" side="враг" threat="слабый" desc="серая шерсть"', "/>"),
        ("<end", "/>"),
    ])
    foes = [c for c in s.characters.values() if c.name.startswith("Лесная тварь ")]
    assert [c.name for c in foes] == ["Лесная тварь 1", "Лесная тварь 2", "Лесная тварь 3"]
    assert all(c.side == "hostile" and c.id in s.present for c in foes)
    assert M.resolve_character(s, "Лесная тварь 2") is foes[1]


def test_kill_gives_exp_to_whole_party():
    s = mk(); sil = ch(s, "Сильфа"); M.join_party(s, sil)
    tw = ch(s, "Гвоздь"); tw.hp = 1; tw.max_hp = 1
    for seed in range(40):                               # find a seed where the hero's blow lands
        s.rng_seed = seed; s.rng_calls = 0; tw.hp = 1; s.player.exp = sil.exp = 0; s.combat.active = False
        eng, llm, ev = run_turn(s, [('<attack who="Ярен" target="Гвоздь"', "/>"), ("<end", "/>")], "Бью Гвоздя")
        if tw.hp == 0:
            break
    assert tw.hp == 0 and tw.side == "hostile"
    share = M.share_exp(sil, M.kill_share(sil, tw))
    assert s.player.exp >= share and sil.exp == share     # same level: the same share of the level


def test_pack_name_with_number_and_side_visible():
    s = mk()
    eng, llm, ev = run_turn(s, [('<introduce name="Волк 1" count="2" side="враг"', "/>"), ("<end", "/>")])
    names = sorted(c.name for c in s.characters.values() if c.name.startswith("Волк"))
    assert names == ["Волк 1", "Волк 2"]
    st = eng.state(s)
    assert {st["characters"][c.id]["side"] for c in s.characters.values() if c.name.startswith("Волк")} == {"hostile"}


def test_combat_start_during_fight_adds_without_reset():
    from app import combat as C
    s = mk()
    C.start_combat(s, [ch(s, "Борек").id]); s.combat.round = 2
    before = [o.id for o in s.combat.order]
    C.join_combat(s, [ch(s, "Гвоздь").id])
    assert s.combat.round == 2 and set(before) < {o.id for o in s.combat.order}


def test_invitation_reminds_narrator_about_join_tag():
    s = mk()
    eng, llm, ev = run_turn(s, [("<narrate>Сильфа думает.</narrate><end", "/>")], "Сильфа, пойдёшь со мной? Предлагаю присоединиться к моему отряду.")
    assert '<join who="Сильфа"/>' in llm.prompts[0]
    eng, llm, ev = run_turn(s, [("<narrate>Тихо.</narrate><end", "/>")], "Осматриваю зал.")
    assert "[ОТРЯД]" not in llm.prompts[0].split("Действие героя: Осматриваю зал")[1]   # no nudge without an invitation
