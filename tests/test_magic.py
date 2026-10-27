"""Book of paths: five core paths in every world, branches opened through the story (seen → studied → AI writes,
code balances), abilities with mechanics amplified by attributes, the System agent (answers; code acts)."""
import asyncio, json
from app import combat as C, magic as G, mechanics as M
from app.engine import TurnEngine, create_scene
from app.models import Lead, MagicPath
from tests.test_engine_fake import FakeLLM


def mk():
    return create_scene("Тест", "Мрачный город магов", {"name": "Ярен", "attrs": {"Ловкость": 12, "Разум": 14}},
                        [{"name": "Сильфа", "kind": "лучница"}, {"name": "Борек", "kind": "наёмник"}], "d20", seed=4)


BRANCH = {"same_as": "", "name": "Школа разрушения", "description": "Боевая магия огня городских магов.",
          "parents": [{"path": "core-mag", "depth": 2}], "trial": "Превзойти пироманта гильдии в поединке",
          "tiers": [
              {"abilities": [{"name": "Огненный шар", "kind": "active", "effect": "damage", "scale": "Разум", "bonus": "spell",
                              "description": "Ком пламени, наносит 50 урона"}]},
              {"abilities": [{"name": "Жар в крови", "kind": "passive", "effect": "utility", "scale": "Разум", "bonus": "spell",
                              "description": "Огонь слушается"},
                             {"name": "Стена огня", "kind": "active", "effect": "control", "scale": "Разум", "bonus": "hp",
                              "description": "Пламя отрезает путь"}]},
              {"abilities": [{"name": "Огненная буря", "kind": "active", "effect": "damage", "scale": "Разум", "bonus": "spell",
                              "description": "Небо горит"}]}]}


def run(s, outs, text, eng=None):
    llm = FakeLLM(outs); ev = []

    async def emit(m): ev.append(m)
    eng = eng or TurnEngine(llm)
    eng.llm = llm
    asyncio.run(eng.handle(s, text, emit))
    return eng, llm, ev


def test_core_paths_exist_in_every_world_and_cost_points():
    s = mk(); p = s.player
    assert [x.id for x in G.core_paths(s)] == G.CORE_IDS and all(len(x.tiers) == 5 for x in G.core_paths(s))
    assert p.ability_points == 1                                       # the first step is the player's choice
    out = M.run_system_command(s, *M.parse_system_command("Система, открой путь Мага"))
    assert p.paths["core-mag"] == 1 and p.ability_points == 0, out
    arrow = next(a for a in p.abilities if a.name == "Магическая стрела")
    assert arrow.effect == "damage" and arrow.power == "1d8" and arrow.mp == 2 and arrow.scale == "Разум"
    out = M.run_system_command(s, *M.parse_system_command("Система, открой путь Мага"))
    assert "Не хватает" in out and p.paths["core-mag"] == 1
    p.ability_points = 1
    assert "Чутьё магии" in M.run_system_command(s, *M.parse_system_command("Система, возьми Чутьё магии"))
    assert p.paths["core-mag"] == 2 and M.passive_bonus(p, "spell") == 1


def test_parse_book_commands():
    assert M.parse_system_command("Система, пути")[0] == "paths"
    assert M.parse_system_command("Система, книга путей")[0] == "paths"
    assert M.parse_system_command("Система покажи пути развития пути берсерка")[0] == "ask"
    assert M.parse_system_command("Система, возьми Исцеление")[0] == "unlock_path"
    assert M.parse_system_command("Система, изучи Сильфу")[0] == "inspect"
    assert M.parse_system_command("Система, способности")[0] == "abilities"


def test_attributes_amplify_pools_and_passives():
    s = mk(); p = s.player
    base = p.max_hp
    p.attrs["Выносливость"] = 16; M.sync_derived(p)
    assert p.max_hp == base + 9
    p.ability_points = 2; G.unlock_tier(s, p, "Воин"); G.unlock_tier(s, p, "Воин")
    assert p.max_hp == base + 9 + 6 and p.hp == p.max_hp               # «Закалка»: hp +6
    p.attrs["Выносливость"] = 10; M.sync_derived(p)
    assert p.max_hp == base + 6 and p.hp <= p.max_hp
    assert p.max_mp == 10 + 4                                          # Разум 14 → +2 amp → +4 MP


def test_cast_spends_mp_rolls_power_and_amplifies():
    s = mk(); p = s.player; borek = M.resolve_character(s, "Борек")
    G.unlock_tier(s, p, "Маг")
    arrow = C.find_ability(p, "стрела")
    C.mark_foe(s, borek)                       # a blank foe gets its real sheet when the fight starts
    mp0, hp0 = p.mp, borek.hp
    r = C.cast(s, p, arrow, borek)
    assert r["ok"] and p.mp == mp0 - 2 and borek.hp == hp0 - r["damage"] and r["damage"] >= 1 + 2   # 1d8 + Разум(+2)
    assert s.combat.active
    p.mp = 1
    r = C.cast(s, p, arrow, borek)
    assert not r["ok"] and "не хватает MP" in r["text"] and p.mp == 1


def test_shield_absorbs_and_control_is_resisted_by_will():
    s = mk(); p = s.player; borek = M.resolve_character(s, "Борек")
    p.ability_points = 3
    for _ in range(3):
        G.unlock_tier(s, p, "Маг")
    r = C.cast(s, p, C.find_ability(p, "щит"), None)                   # shield defaults to self
    soak = next(x.potency for x in p.conditions if x.name == "щит")
    assert r["ok"] and soak >= 1
    left, note = C.absorb(p, soak + 3)
    assert left == 3 and "поглотил" in note and not any(x.name == "щит" for x in p.conditions)
    s.player.ability_points = 1; G.unlock_tier(s, p, "Тень")
    s.player.ability_points = 2; G.unlock_tier(s, p, "Тень"); G.unlock_tier(s, p, "Тень")
    borek.attrs["Воля"] = 20
    p.mp = 50
    texts = [C.cast(s, p, C.find_ability(p, "Ослепляющий"), borek)["text"] for _ in range(6)]
    assert any("сопротивляется" in t for t in texts)                  # Воля 20 resists most attempts


def test_discover_learn_and_branch_written_after_turn():
    s = mk(); s.core_flavored = True
    eng, llm, ev = run(s, [('<narrate>Маг у ворот швыряет ком пламени.</narrate><discover what="огненный шар" source="маг у ворот"', "/>"),
                           ("<end", "/>")], "Смотрю на мага")
    assert [l.what for l in s.leads] == ["огненный шар"] and not s.leads[0].studied
    assert "огненный шар" in G.tail_block(s)
    # seeing it again does not duplicate
    assert not G.discover(s, "Огненный шар", "снова")[0] and len(s.leads) == 1
    eng, llm, ev = run(s, [('<narrate>Ты неделю читаешь трактат.</narrate><learn what="огненного шара" how="трактат пироманта"', "/>"),
                           ("<end", "/>")], "Читаю трактат", eng)
    assert s.leads[0].studied and s.pending_branches == ["lead-1"]
    llm = FakeLLM([(json.dumps(BRANCH, ensure_ascii=False), "")]); eng.llm = llm
    asyncio.run(eng.develop(s))
    b = G.get_path(s, s.leads[0].branch)
    assert b and b.name == "Школа разрушения" and b.parents == {"core-mag": 2} and not s.pending_branches
    assert "Огненный шар" in llm.prompts[0] or "огненный шар" in llm.prompts[0]
    # code sets every number: tier I needs Маг II, 1 point; tier III also needs the trial
    t1, t2, t3 = b.tiers
    assert [(r.kind, r.path, r.value) for r in t1.reqs if r.kind == "rank"] == [("rank", "core-mag", 2)]
    assert G.tier_cost(t1) == 1 and G.tier_cost(t3) == 2 and any(r.kind == "story" for r in t3.reqs)
    fire = t1.abilities[0]
    assert fire.power == G.DAMAGE[3] and fire.mp == G.MP_COST[3] and "50" in fire.description  # text kept, numbers ignored
    assert t2.abilities[0].kind == "passive" and t2.abilities[0].bonus == {"spell": 2}
    p = s.player; p.ability_points = 5
    ok, msg = G.unlock_tier(s, p, "Огненный шар")
    assert not ok and "Путь Мага II" in msg
    G.unlock_tier(s, p, "Маг"); G.unlock_tier(s, p, "Маг")
    ok, msg = G.unlock_tier(s, p, "Огненный шар")
    assert ok and p.paths[b.id] == 1 and any(a.name == "Огненный шар" for a in p.abilities)
    assert "Школа разрушения" in M.run_system_command(s, "paths", "пути")


def test_branch_same_as_existing_links_lead():
    s = mk(); s.core_flavored = True
    s.leads = [Lead(id="lead-1", what="огненный шар", studied=True), Lead(id="lead-2", what="пламенный ком", studied=True)]
    b = G.balance_branch(s, BRANCH, s.leads[0]); s.paths.append(b); s.leads[0].branch = b.id
    raw = dict(BRANCH, same_as=b.id)
    llm = FakeLLM([(json.dumps(raw, ensure_ascii=False), "")])
    msg, _ = asyncio.run(G.generate_branch(llm, s, "lead-2"))
    assert s.leads[1].branch == b.id and len(G.branches(s)) == 1 and "уже есть" in msg


def test_fulfill_trial_and_cast_tag_in_a_turn():
    s = mk(); s.core_flavored = True
    b = G.balance_branch(s, BRANCH, Lead(id="lead-1", what="огненный шар")); s.paths.append(b)
    borek = M.resolve_character(s, "Борек")
    G.unlock_tier(s, s.player, "Маг")
    hp0 = borek.hp
    eng, llm, ev = run(s, [('<narrate>Пиромант гильдии повержен в честном поединке.</narrate><fulfill what="превзойти пироманта гильдии"', "/>"),
                           ('<narrate>Ты вскидываешь руку.</narrate><cast spell="Магическая стрела" target="Борек"', "/>"),
                           ("<end", "/>")], "Бью Борека стрелой")
    assert f"{b.id}-3" in s.flags
    assert borek.hp < hp0 and borek.side == "hostile"
    assert any("Магическая стрела" in m.get("text", "") for m in ev if m["type"] == "segment")
    assert "Магическая стрела" in s.turns[-6]["content"] or any("Активные способности" in t["content"] for t in s.turns)


def test_agent_explains_missing_branch_and_cannot_cheat():
    s = mk(); turn = s.turn; s.player.ability_points = 0
    reply = {"reply": "Отрасли берсерка в вашей Книге пока нет. Ближе всего Путь Воина. Запрашиваю открытие.",
             "action": {"type": "unlock_path", "path": "Путь Воина"}}
    eng, llm, ev = run(s, [(json.dumps(reply, ensure_ascii=False), "")], "Система покажи пути развития пути берсерка")
    note = next(m["text"] for m in ev if m["type"] == "segment")
    assert "Отказано" in note and "очков способностей 1" in note and s.turn == turn
    assert "Книга путей этого мира" in llm.prompts[0] and "Путь Воина" in llm.prompts[0]
    assert not s.player.paths


def test_flavor_renames_core_and_learned_abilities():
    s = mk(); p = s.player
    G.unlock_tier(s, p, "Маг")
    data = {"paths": [{"id": "core-mag", "name": "Путь Нейроманта", "description": "Взлом сетей разумом.",
                       "abilities": [{"rank": 1, "name": "Импульс", "description": "Разряд в чужой мозг."}]}]}
    G.apply_flavor(s, data)
    assert G.get_path(s, "core-mag").name == "Путь Нейроманта" and s.core_flavored
    a = next(a for a in p.abilities if a.path == "core-mag")
    assert a.name == "Импульс" and a.effect == "damage" and a.power == "1d8"


def test_rewind_restores_book_and_legacy_paths_dropped():
    s = mk()
    eng, llm, ev = run(s, [('<narrate>Ведьма шепчет, и тени оживают.</narrate><discover what="оживление теней"', "/>"),
                           ("<end", "/>")], "Смотрю")
    assert s.leads
    assert eng.rewind(s, 1) and not s.leads
    s.paths.append(MagicPath(id="put-teni", name="Путь Тени (старый)")); s.player.paths["put-teni"] = 1
    assert G.ensure_core(s) and not G.get_path(s, "put-teni") and "put-teni" not in s.player.paths
    assert "book" in eng.state(s) and eng.state(s)["book"]["status"][s.player_id]["core-voin"]["tier"] == 1
