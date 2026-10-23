"""Director patch apply (no live LLM)."""
from app import director as D
from app.engine import create_scene
from app.models import Ability


def mk():
    return create_scene(
        "Тест", "мир у болот",
        {"name": "Ярен", "attrs": {"Ловкость": 14},
         "abilities": [{"name": "Ночное зрение", "kind": "passive"}],
         "inventory": ["Нож"]},
        [{"name": "Сильфа", "description": "лучница из лесной стражи", "kind": "лучница",
          "personality": "сдержанная"}],
        "d20", seed=3,
    )


def test_incomplete_hints_archer_empty_inv():
    s = mk()
    hints = D.incomplete_hints(s)
    assert any("Сильфа" in h for h in hints)


def test_apply_inventory_and_kind():
    s = mk()
    sil = s.characters[[k for k in s.characters if "silfa" in k or s.characters[k].name == "Сильфа"][0]]
    assert not sil.inventory
    summary, actions, rebuilt = D.apply_patches(s, {
        "summary": "Выдала Сильфе лук.",
        "character_patches": [{
            "who": "Сильфа",
            "inventory_add": ["Короткий лук", "Колчан со стрелами"],
            "abilities_add": [{"name": "Меткий выстрел", "kind": "active", "cost": "1/ход"}],
            "kind": "лучница стражи",
        }],
        "scene_patch": {},
    })
    assert any("лук" in a.lower() or "Лук" in a for a in actions)
    assert any(i.name.lower().find("лук") >= 0 for i in sil.inventory)
    assert any(a.name == "Меткий выстрел" for a in sil.abilities)
    assert sil.kind == "лучница стражи"
    assert rebuilt  # kind/abilities touch prompt cards


def test_apply_rejects_legendary_and_player_leave():
    s = mk()
    summary, actions, _ = D.apply_patches(s, {
        "summary": "попытка",
        "character_patches": [{
            "who": "Ярен",
            "inventory_add": ["Легендарный меч богов", "Плащ"],
        }],
        "scene_patch": {"present_remove": ["Ярен"]},
    })
    inv = [i.name for i in s.player.inventory]
    assert "Плащ" in inv
    assert not any("Легендар" in n for n in inv)
    assert any("нельзя убрать игрока" in a for a in actions)
    assert s.player_id in s.present


def test_attr_clamp():
    s = mk()
    D.apply_patches(s, {
        "summary": "статы",
        "character_patches": [{"who": "Ярен", "attrs": {"Сила": 99, "Ловкость": 1}}],
        "scene_patch": {},
    })
    assert s.player.attrs["Сила"] == 18
    assert s.player.attrs["Ловкость"] == 3


def test_parse_director_command():
    from app import mechanics as M
    assert M.parse_system_command("Система, директор дай лук Сильфе")[0] == "director"
    assert M.parse_system_command("Система, проверь карточки")[0] == "director_audit"
