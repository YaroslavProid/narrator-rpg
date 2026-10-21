import base64, json, struct, zlib
from app.cards import parse_card
from app import store
from tests.test_mechanics import mk


def png_with(key, payload):
    def chunk(t, b): return struct.pack(">I", len(b)) + t + b + struct.pack(">I", zlib.crc32(t + b))
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"tEXt", key + b"\0" + base64.b64encode(payload))
            + chunk(b"IDAT", zlib.compress(b"\0\0\0\0")) + chunk(b"IEND", b""))


def test_v2_png_and_json():
    card = {"spec": "chara_card_v2", "data": {"name": "Мирна", "description": "{{char}} — травница.", "personality": "тихая"}}
    c = parse_card(png_with(b"chara", json.dumps(card).encode()))
    assert c["name"] == "Мирна" and c["description"] == "Мирна — травница."
    assert parse_card(json.dumps(card).encode())["personality"] == "тихая"


def test_text_card():
    c = parse_card("Имя: Борек\nХмурый наёмник.".encode())
    assert c["name"] == "Борек" and "наёмник" in c["description"]


def test_save_load_roundtrip():
    s = mk(); store.save_scene(s)
    s2 = store.load_scene(s.id)
    assert s2.system_prompt == s.system_prompt and s2.player.name == "Ярен"
    assert store.list_scenes()[0]["id"] == s.id


def test_api_create_scene():
    from fastapi.testclient import TestClient
    from app.server import app
    with TestClient(app) as cl:
        r = cl.post("/api/scenes", json={"title": "А", "setting": "мир", "rule_mode": "система",
                                         "player": {"name": "Ярен", "description": "наёмник"},
                                         "npcs": [{"name": "Сильфа"}, {"card": '{"spec":"chara_card_v2","data":{"name":"Борек"}}'}]})
        assert r.status_code == 200, r.text
        g = cl.get(f"/api/scenes/{r.json()['id']}").json()
        assert g["rule_mode"] == "система" and len(g["state"]["present"]) == 3
        assert cl.post("/api/scenes", json={"title": "Б", "setting": "", "rule_mode": "x", "player": {"name": "Я"}}).status_code == 400
