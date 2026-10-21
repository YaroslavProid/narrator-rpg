"""Character card import: Character Card V2/V3 (SillyTavern/Tavern PNG or JSON), plain JSON, pasted text."""
import base64, json, re, struct, zlib


def _png_text_chunks(data: bytes) -> dict:
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("не PNG")
    out = {}; i = 8
    while i + 8 <= len(data):
        ln, typ = struct.unpack(">I4s", data[i:i + 8]); body = data[i + 8:i + 8 + ln]; i += 12 + ln
        if typ == b"tEXt":
            k, _, v = body.partition(b"\0"); out[k.decode("latin-1").lower()] = v.decode("latin-1")
        elif typ == b"zTXt":
            k, _, v = body.partition(b"\0"); out[k.decode("latin-1").lower()] = zlib.decompress(v[1:]).decode("latin-1")
        elif typ == b"iTXt":
            k, _, rest = body.partition(b"\0"); comp = rest[0]; rest = rest[2:]
            _, _, rest = rest.partition(b"\0"); _, _, txt = rest.partition(b"\0")
            out[k.decode("latin-1").lower()] = (zlib.decompress(txt) if comp else txt).decode("utf-8")
        elif typ == b"IEND":
            break
    return out


def card_from_png(data: bytes) -> dict:
    ch = _png_text_chunks(data)
    raw = ch.get("ccv3") or ch.get("chara")
    if not raw:
        raise ValueError("в PNG нет карточки (chara/ccv3)")
    return card_from_json(json.loads(base64.b64decode(raw).decode("utf-8")))


def card_from_json(obj: dict) -> dict:
    d = obj.get("data", obj) if isinstance(obj.get("data"), dict) else obj
    name = d.get("name") or obj.get("name") or "Безымянный"
    desc = d.get("description") or ""
    if isinstance(desc, dict):
        desc = "\n".join(f"{k}: {v}" for k, v in desc.items())
    attrs = d.get("base_attributes") or {}
    if attrs:  # Talemate-style character
        desc = (desc + "\n" + "\n".join(f"{k}: {v}" for k, v in attrs.items())).strip()
    pers = d.get("personality") or ""
    speech = "\n".join(x for x in [d.get("mes_example") or "", d.get("first_mes") or ""] if x)
    for k in ("{{char}}", "{{Char}}", "<BOT>"):
        desc, pers, speech = desc.replace(k, name), pers.replace(k, name), speech.replace(k, name)
    return {"name": name.strip(), "description": desc.strip()[:3000], "personality": pers.strip()[:1000],
            "speech": speech.strip()[:800], "secrets": (d.get("creator_notes") or "")[:0]}


def card_from_text(text: str) -> dict:
    t = text.strip()
    if t.startswith("{"):
        return card_from_json(json.loads(t))
    lines = t.split("\n")
    first = lines[0].strip()
    m = re.match(r"^(?:имя|name)\s*:\s*(.+)$", first, re.I)
    name = (m.group(1) if m else first.split(",")[0].split("—")[0].split(" - ")[0]).strip()[:40]
    body = "\n".join(lines[1:]).strip() if m or len(lines) > 1 else t
    return {"name": name, "description": body, "personality": "", "speech": "", "secrets": ""}


def parse_card(data: bytes, filename: str = "") -> dict:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return card_from_png(data)
    return card_from_text(data.decode("utf-8-sig"))
