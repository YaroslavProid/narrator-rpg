"""Magic paths (AI-invented, code-balanced) and the System agent (answers; code checks and performs actions)."""
import asyncio, json
from app import magic as G, mechanics as M
from app.engine import TurnEngine, create_scene
from app.protocol import parse_tag
from tests.test_engine_fake import FakeLLM

RAW = {"paths": [
    {"name": "Путь Тени", "description": "Магия сумерек", "attr": "Ловкость", "tiers": [
        {"story": "", "abilities": [{"name": "Теневой шаг", "kind": "active", "description": "Шаг сквозь тень"}]},
        {"story": "Найти наставника из Ночной гильдии", "abilities": [{"name": "Покров ночи", "kind": "passive", "description": "Сливается с тьмой"}]},
        {"story": "", "abilities": [{"name": "Теневой двойник", "kind": "active", "description": "Двойник отвлекает врага"}]}]},
    {"name": "Пламя", "description": "Огонь очага", "attr": "Разум", "tiers": [
        {"story": "Зажечь огонь в древнем алтаре", "abilities": [{"name": "Искра", "kind": "active", "description": "Поджигает"}]},
        {"story": "", "abilities": [{"name": "Огненная стена", "kind": "active", "description": "Стена огня"}]},
        {"story": "Пережить ночь в горящем храме", "abilities": [{"name": "Феникс", "kind": "passive", "description": "Восстаёт"}]}]},
    {"name": "Корни", "description": "Сила леса", "attr": "Воля", "tiers": [
        {"story": "", "abilities": [{"name": "Лоза", "kind": "active", "description": "Опутывает"}]},
        {"story": "", "abilities": [{"name": "Кора", "kind": "passive", "description": "Кожа как кора"}]},
        {"story": "", "abilities": [{"name": "Древо", "kind": "active", "description": "Призывает древо"}]}]},
]}


def mk():
    s = create_scene("Тест", "Мрачный лес", {"name": "Ярен", "attrs": {"Ловкость": 12}},
                     [{"name": "Сильфа", "kind": "лучница"}], "d20", seed=4)
    return s


def ready():
    s = mk(); s.paths = G.balance(RAW["paths"]); return s


def test_balance_forces_one_scale_and_story_on_higher_tiers():
    paths = G.balance(RAW["paths"])
    assert [p.name for p in paths] == ["Путь Тени", "Пламя", "Корни"]
    t = paths[0].tiers
    lv = [next(r.value for r in x.reqs if r.kind == "level") for x in t]
    assert lv == [2, 4, 7]
    assert next(r for r in t[0].reqs if r.kind == "attr").attr == "Ловкость"
    assert all(any(r.kind == "story" for r in x.reqs) for x in t[1:])           # tiers II-III always have a story condition
    assert not any(r.kind == "story" for r in t[0].reqs)                         # tier I only if the AI gave one
    assert t[0].abilities[0].cost == "2 MP" and t[2].abilities[0].cost == "6 MP"


def test_unlock_requires_everything_and_spends_points():
    s = ready(); p = s.player
    ok, msg = G.unlock_tier(s, p, "тени")
    assert not ok and "уровень 2" in msg and "очков способностей" in msg and "Ловкость" not in msg   # Ловкость 12 is already met
    p.level = 2; p.ability_points = 1
    ok, msg = G.unlock_tier(s, p, "Путь Тени")
    assert ok and p.paths["put-teni"] == 1 and p.ability_points == 0 and any(a.name == "Теневой шаг" for a in p.abilities)
    p.level = 4; p.attrs["Ловкость"] = 14; p.ability_points = 1
    ok, msg = G.unlock_tier(s, p, "тени")
    assert not ok and "наставника" in msg                                       # the story condition is not met yet


def test_fulfill_matches_story_condition_and_opens_the_way():
    s = ready(); p = s.player; p.level = 4; p.attrs["Ловкость"] = 14; p.ability_points = 2; p.paths["put-teni"] = 1
    ok, msg = G.fulfill(s, "герой нашёл наставника Ночной гильдии")
    assert ok and "put-teni-2" in s.flags
    assert G.unlock_tier(s, p, "тени")[0] and p.paths["put-teni"] == 2
    assert not G.fulfill(s, "съел пирожок")[0]


def test_max_two_paths_per_character():
    s = ready(); p = s.player; p.level = 3; p.attrs.update({"Ловкость": 14, "Воля": 14, "Разум": 14}); p.ability_points = 5
    s.flags["plamya-1"] = 1
    assert G.unlock_tier(s, p, "тени")[0] and G.unlock_tier(s, p, "пламя")[0]
    ok, msg = G.unlock_tier(s, p, "корни")
    assert not ok and "не больше 2 путей" in msg


def test_routing_questions_go_to_agent_commands_stay_instant():
    assert M.parse_system_command("Система, статус")[0] == "status"
    assert M.parse_system_command("Система, инвентарь")[0] == "inventory"
    assert M.parse_system_command("Система, что нужно для пути огня?")[0] == "ask"
    assert M.parse_system_command("Система, хочу изучать магию")[0] == "ask"
    assert M.parse_system_command("Система, пути магии")[0] == "paths"
    assert M.parse_system_command("Система, открой путь Тени")[0] == "unlock_path"
    assert M.parse_system_command("Система, помощь")[0] == "help"
    assert parse_tag('<fulfill what="нашёл наставника"/>').name == "fulfill"


def run(s, outs, text):
    llm = FakeLLM(outs); ev = []

    async def emit(m): ev.append(m)
    eng = TurnEngine(llm)
    asyncio.run(eng.handle(s, text, emit))
    return eng, llm, ev


def test_agent_generates_paths_once_answers_and_cannot_cheat():
    s = mk(); turn = s.turn
    reply = {"reply": "Путь Тени требует уровня 2 и Ловкости 12. Запрашиваю открытие.",
             "action": {"type": "unlock_path", "path": "Путь Тени"}}
    eng, llm, ev = run(s, [(json.dumps(RAW, ensure_ascii=False), ""), (json.dumps(reply, ensure_ascii=False), "")],
                       "Система, хочу открыть путь тени, что для этого нужно?")
    assert len(s.paths) == 3 and s.turn == turn                    # no story turn spent
    note = next(m["text"] for m in ev if m["type"] == "segment")
    assert "Отказано" in note and "уровень 2" in note               # the model asked, the code refused
    assert "Теневой шаг" not in [a.name for a in s.player.abilities]
    assert "Статус путей" in llm.prompts[1] and "закрыта, не хватает" in llm.prompts[1]
    assert "<fulfill" in s.system_prompt and "наставника" in s.system_prompt   # the narrator learns the conditions
    # second question: paths are not regenerated
    eng, llm, ev = run(s, [(json.dumps({"reply": "Ок.", "action": {"type": "none"}}), "")], "Система, сколько у меня очков?")
    assert len(llm.prompts) == 1


def test_agent_unlocks_when_requirements_hold_and_works_for_companion():
    s = ready(); sil = M.resolve_character(s, "Сильфа"); M.join_party(s, sil)
    sil.level = 2; sil.attrs["Ловкость"] = 13; sil.ability_points = 1
    reply = {"reply": "Запрашиваю.", "action": {"type": "unlock_path", "who": "Сильфа", "path": "тень"}}
    eng, llm, ev = run(s, [(json.dumps(reply, ensure_ascii=False), "")], "Система, открой Сильфе путь тени?")
    assert sil.paths.get("put-teni") == 1 and any(a.name == "Теневой шаг" for a in sil.abilities)
    st = ev[-1]["state"]["magic"]["status"][sil.id]
    assert next(x for x in st if x["id"] == "put-teni")["have"] == 1


def test_fulfill_tag_in_a_turn():
    s = ready()
    eng, llm, ev = run(s, [('<narrate>Старик кивает: он учил когда-то Ночную гильдию.</narrate><fulfill what="найти наставника из Ночной гильдии"', "/>"),
                           ("<end", "/>")], "Прошу старика научить меня")
    assert "put-teni-2" in s.flags and any("Условие выполнено" in m.get("text", "") for m in ev if m["type"] == "segment")


def test_salvage_truncated_output_and_tier_number_as_story():
    full = json.dumps(RAW, ensure_ascii=False, indent=2)
    cut = full[: full.index('"name": "Корни"') + 30]                    # cut inside the third path
    assert [p["name"] for p in G.salvage_paths(cut)] == ["Путь Тени", "Пламя"]
    raw = json.loads(json.dumps(RAW["paths"][2:], ensure_ascii=False)); raw[0]["tiers"][0]["story"] = "I"
    assert not any(r.kind == "story" for r in G.balance(raw)[0].tiers[0].reqs)


def test_give_does_not_catch_vydai():
    assert M.parse_system_command("Система, выдай мне 100 золотых")[0] == "ask"
    assert M.parse_system_command("Система, дай Нож Сильфе")[0] == "give"
