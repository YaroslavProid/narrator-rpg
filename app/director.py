"""Director: chat + optional card/scene patches (Talemate-style).

Not on the play hot path — separate prompt + json_schema. Reply is always required;
patches only when needed. After apply, rebuild system_prompt if cards/setting changed.
"""
from __future__ import annotations
import json, re
from . import mechanics as M
from . import prompts as P
from .models import ATTRS, Ability, Character, Item, Scene
from .prompt import render

DIRECTOR_SCHEMA = {
    "type": "object",
    "required": ["reply", "summary"],
    "properties": {
        "reply": {"type": "string", "minLength": 1, "maxLength": 1200},
        "summary": {"type": "string", "minLength": 3, "maxLength": 400},
        "narrator_note": {"type": "string", "maxLength": 400},
        "character_patches": {
            "type": "array",
            "maxItems": 8,
            "items": {
                "type": "object",
                "required": ["who"],
                "properties": {
                    "who": {"type": "string"},
                    "description": {"type": "string"},
                    "personality": {"type": "string"},
                    "speech": {"type": "string"},
                    "secrets": {"type": "string"},
                    "kind": {"type": "string"},
                    "attrs": {
                        "type": "object",
                        "additionalProperties": {"type": "integer"},
                    },
                    "abilities_set": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["name", "kind"],
                            "properties": {
                                "name": {"type": "string"},
                                "kind": {"type": "string", "enum": ["active", "passive"]},
                                "description": {"type": "string"},
                                "cost": {"type": "string"},
                                "cooldown": {"type": "string"},
                            },
                        },
                    },
                    "abilities_add": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["name", "kind"],
                            "properties": {
                                "name": {"type": "string"},
                                "kind": {"type": "string", "enum": ["active", "passive"]},
                                "description": {"type": "string"},
                                "cost": {"type": "string"},
                                "cooldown": {"type": "string"},
                            },
                        },
                    },
                    "inventory_add": {"type": "array", "items": {"type": "string"}, "maxItems": 8},
                    "inventory_remove": {"type": "array", "items": {"type": "string"}, "maxItems": 8},
                    "reveal": {
                        "type": "object",
                        "properties": {
                            "kind": {"type": "boolean"},
                            "attrs": {"type": "boolean"},
                            "abilities": {"type": "boolean"},
                        },
                    },
                },
            },
        },
        "scene_patch": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "setting": {"type": "string"},
                "present_add": {"type": "array", "items": {"type": "string"}},
                "present_remove": {"type": "array", "items": {"type": "string"}},
            },
        },
    },
}


def _card_snapshot(c: Character) -> str:
    inv = ", ".join(("*" + i.name + "*" if i.equipped else i.name) + (f"×{i.qty}" if i.qty > 1 else "")
                    for i in c.inventory) or "пусто"
    abs_ = ", ".join(f"{a.name}({'акт' if a.kind == 'active' else 'пас'})" for a in c.abilities) or "нет"
    attrs = ", ".join(f"{k}={v}" for k, v in c.attrs.items())
    return (
        f"### {c.name} (id={c.id})"
        + (" [ИГРОК]" if c.is_player else "")
        + f"\nвид: {c.kind or '—'}\n"
        f"описание: {(c.description or '—')[:400]}\n"
        f"характер: {(c.personality or '—')[:200]}\n"
        f"речь: {(c.speech or '—')[:150]}\n"
        f"тайны: {(c.secrets or '—')[:150]}\n"
        f"атрибуты: {attrs}\n"
        f"способности: {abs_}\n"
        f"инвентарь: {inv}\n"
        f"reveal: kind={c.reveal.kind} attrs={c.reveal.attrs} abilities={c.reveal.abilities}"
    )


def scene_brief(scene: Scene) -> str:
    present = [scene.characters[i].name for i in scene.present]
    cards = "\n\n".join(_card_snapshot(c) for c in scene.characters.values())
    return (
        f"Сцена: {scene.title}\nРежим: {scene.rule_mode}\nХод: {scene.turn}\n"
        f"Присутствуют: {', '.join(present) or '—'}\n"
        f"Сеттинг (сокр.): {scene.setting[:800]}\n\n"
        f"Карточки:\n{cards}"
    )


def incomplete_hints(scene: Scene) -> list[str]:
    """Heuristic mismatches the Director should fix (e.g. archer with empty inventory)."""
    hints = []
    role_gear = [
        (r"лучник|лучниц|стрелк|арбалет|лучн", ["лук", "арбалет", "стрел", "колчан"]),
        (r"мечник|воин|наёмник|рыцар|страж", ["меч", "клинок", "топор", "щит", "копье", "копьё"]),
        (r"маг|волшеб|чародей|колдун", ["посох", "гримуар", "книга", "жезл", "свиток"]),
        (r"вор|разбойник|убийц", ["кинжал", "нож", "отмыч", "капюшон"]),
        (r"жрец|священ|целител|лекар", ["символ", "зелье", "травы", "посох"]),
        (r"охотник|следопыт", ["лук", "нож", "капкан", "колчан"]),
    ]
    for c in scene.characters.values():
        blob = " ".join([c.kind or "", c.description or "", c.personality or ""]).lower()
        inv_blob = " ".join(i.name.lower() for i in c.inventory)
        for pat, keywords in role_gear:
            if re.search(pat, blob) and not any(k in inv_blob for k in keywords):
                hints.append(
                    f"{c.name}: по роли/описанию похож на «{pat}», но в инвентаре нет подходящего снаряжения "
                    f"(сейчас: {inv_blob or 'пусто'})."
                )
                break
        if not (c.description or "").strip() and not c.is_player:
            hints.append(f"{c.name}: пустое описание.")
        if not c.kind and not c.is_player:
            hints.append(f"{c.name}: не указан вид/тип.")
    return hints


def build_director_prompt(scene: Scene, order: str, *, audit: bool = False) -> list[dict]:
    hints = incomplete_hints(scene)
    hint_block = ""
    if hints:
        hint_block = (
            "Замеченные несостыковки (чини только если уместно или тебя просят):\n- "
            + "\n- ".join(hints) + "\n\n"
        )
    if audit and not (order or "").strip():
        order = (
            "Проверь карточки и сцену: дополни пустой инвентарь по роли, уточни вид/описание/способности, "
            "убери противоречия. Кратко расскажи игроку, что нашла. Не меняй сюжет без нужды."
        )
    chat = scene.director_log[-8:] if getattr(scene, "director_log", None) else []
    chat_block = ""
    if chat:
        chat_block = "Недавний разговор с игроком:\n" + "\n".join(f"- {x}" for x in chat) + "\n\n"
    recent = []
    for e in scene.log[-12:]:
        if e.kind in ("player", "speech", "narration", "system"):
            who = e.who or ("Рассказчик" if e.kind == "narration" else e.kind)
            recent.append(f"{who}: {(e.text or '')[:180]}")
    recent_block = ""
    if recent:
        recent_block = "Хвост сцены (для контекста):\n" + "\n".join(recent) + "\n\n"
    attrs = ", ".join(ATTRS)
    msg = (order or "").strip() or "просто поздоровайся и кратко скажи, чем можешь помочь"
    user = "\n".join([
        "Ты — Директор текстовой RPG (как в Talemate): собеседник и режиссёр, не авто-редактор.",
        "",
        "С тобой можно просто поговорить: спросить совет по сюжету, тону, персонажам, сложности;",
        "попросить вмешаться в ответы рассказчика; попросить поменять карточку.",
        "Ты НЕ обязан каждый раз что-то править. Если запрос — разговор или совет, ответь в reply",
        "и оставь character_patches/scene_patch пустыми.",
        "Правки карточек/сцены — только когда игрок просит ИЛИ при аудите с явной дырой.",
        "",
        scene_brief(scene),
        "",
        f"{hint_block}{chat_block}{recent_block}Сообщение игрока: {msg}",
        "",
        "Ответ — один JSON:",
        "- reply (обязательно): живой ответ игроку по-русски, 2–8 предложений. Можно советовать, спорить, предлагать варианты.",
        "- summary: очень кратко для лога (что ответила / что правила).",
        "- narrator_note (опционально): одноразовая мягкая подсказка рассказчику на СЛЕДУЮЩИЙ ход (тон, акцент, чего избегать). Пусто, если не нужно.",
        "- character_patches / scene_patch: только при реальных правках; иначе [] и {}.",
        f"- abilities_set заменяет список; abilities_add добавляет; inventory_add/remove; attrs из [{attrs}] (3–18).",
        "- Не удаляй персонажа игрока из сцены. Не пиши код.",
    ])
    system = (
        "Ты Директор: сначала общаешься с игроком (reply), правки — по необходимости. "
        "Отвечаешь одним JSON. По-русски, по делу, без обязательных правок."
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def parse_director_json(content: str) -> dict:
    t = (content or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    a, b = t.find("{"), t.rfind("}")
    if a < 0 or b <= a:
        raise ValueError("в ответе директора нет JSON")
    return json.loads(t[a:b + 1])


def _ability_from(raw: dict) -> Ability | None:
    if not isinstance(raw, dict):
        return None
    name = re.sub(r"\s+", " ", str(raw.get("name") or "").strip())[:40]
    if not name:
        return None
    kind = str(raw.get("kind") or "passive").lower()
    if kind not in ("active", "passive"):
        kind = "passive"
    return Ability(
        name=name, kind=kind,
        description=str(raw.get("description") or "").strip()[:200],
        cost=str(raw.get("cost") or "").strip()[:40],
        cooldown=str(raw.get("cooldown") or "").strip()[:40],
    )


def _add_item(c: Character, name: str, msgs: list[str]):
    from . import combat as C
    t = re.sub(r"\s+", " ", (name or "").strip())[:40]
    if not t or any(w in t.lower() for w in M.FORBIDDEN_ITEM_WORDS):
        msgs.append(f"отклонён предмет «{name}»")
        return
    ex = next((i for i in c.inventory if i.name.lower() == t.lower()), None)
    if ex:
        ex.qty += 1
    else:
        c.inventory.append(C.ensure_item_stats(Item(name=t)))
    msgs.append(f"{c.name}: +«{t}»")


def apply_patches(scene: Scene, data: dict) -> tuple[str, list[str], bool, str]:
    """Apply validated director JSON. Returns (summary, actions, rebuilt_prompt, reply)."""
    if not isinstance(data, dict):
        raise ValueError("патч директора не объект")
    actions: list[str] = []
    need_rebuild = False

    for patch in data.get("character_patches") or []:
        if not isinstance(patch, dict):
            continue
        who = str(patch.get("who") or "").strip()
        c = M.resolve_character(scene, who)
        if not c:
            actions.append(f"нет персонажа «{who}» — пропуск")
            continue
        changed = False
        for field, maxlen in (
            ("description", 1200), ("personality", 500), ("speech", 400),
            ("secrets", 400), ("kind", 60),
        ):
            if field in patch and patch[field] is not None:
                val = str(patch[field]).strip()[:maxlen]
                if val != getattr(c, field):
                    setattr(c, field, val)
                    actions.append(f"{c.name}: {field} обновлён")
                    changed = True
                    need_rebuild = True
        if isinstance(patch.get("attrs"), dict):
            for k, v in patch["attrs"].items():
                a = M.resolve_attr(str(k))
                if not a:
                    continue
                try:
                    nv = max(3, min(18, int(v)))
                except (TypeError, ValueError):
                    continue
                if c.attrs.get(a) != nv:
                    c.attrs[a] = nv
                    actions.append(f"{c.name}: {a}→{nv}")
                    changed = True
                    need_rebuild = True
        if "abilities_set" in patch and isinstance(patch["abilities_set"], list):
            new_abs = [ab for x in patch["abilities_set"] if (ab := _ability_from(x))]
            c.abilities = new_abs[:8]
            actions.append(f"{c.name}: способности заменены ({len(c.abilities)})")
            changed = True
            need_rebuild = True
        if isinstance(patch.get("abilities_add"), list):
            for x in patch["abilities_add"]:
                ab = _ability_from(x)
                if not ab:
                    continue
                if any(a.name.lower() == ab.name.lower() for a in c.abilities):
                    continue
                c.abilities.append(ab)
                actions.append(f"{c.name}: +способность «{ab.name}»")
                changed = True
                need_rebuild = True
            c.abilities = c.abilities[:8]
        for name in patch.get("inventory_add") or []:
            before = len(actions)
            _add_item(c, str(name), actions)
            if len(actions) > before:
                changed = True
        for name in patch.get("inventory_remove") or []:
            it = M.find_item(c, str(name))
            if not it:
                actions.append(f"{c.name}: нет «{name}» для удаления")
                continue
            c.inventory.remove(it)
            actions.append(f"{c.name}: −«{it.name}»")
            changed = True
        if isinstance(patch.get("reveal"), dict) and not c.is_player:
            got = []
            rev = patch["reveal"]
            for key in ("kind", "attrs", "abilities"):
                if key in rev and bool(rev[key]) != getattr(c.reveal, key):
                    setattr(c.reveal, key, bool(rev[key]))
                    got.append(key)
            if got:
                actions.append(f"{c.name}: reveal {','.join(got)}")
                changed = True
        if changed:
            from . import store
            try:
                store.save_card(c)
            except Exception:
                pass

    sp = data.get("scene_patch") or {}
    if isinstance(sp, dict):
        if sp.get("title"):
            title = str(sp["title"]).strip()[:80]
            if title and title != scene.title:
                scene.title = title
                actions.append(f"сцена: заголовок → {title}")
                need_rebuild = True
        if sp.get("setting") is not None:
            setting = str(sp["setting"]).strip()[:4000]
            if setting and setting != scene.setting:
                scene.setting = setting
                actions.append("сцена: сеттинг обновлён")
                need_rebuild = True
        for name in sp.get("present_add") or []:
            c = M.resolve_character(scene, str(name))
            if not c:
                actions.append(f"present_add: нет «{name}»")
                continue
            if c.id not in scene.present:
                scene.present.append(c.id)
                actions.append(f"в сцене: +{c.name}")
                need_rebuild = True
        for name in sp.get("present_remove") or []:
            c = M.resolve_character(scene, str(name))
            if not c:
                continue
            if c.is_player:
                actions.append("нельзя убрать игрока из сцены")
                continue
            if c.id in scene.present:
                scene.present.remove(c.id)
                actions.append(f"в сцене: −{c.name}")
                need_rebuild = True

    if need_rebuild:
        scene.system_prompt = P.build_system(scene)

    reply = str(data.get("reply") or "").strip()[:1200]
    note = str(data.get("narrator_note") or "").strip()[:400]
    if note:
        scene.director_note = note
        actions.append("заметка рассказчику на следующий ход")
    summary = str(data.get("summary") or "").strip()[:400] or (
        "Директор внёс правки." if actions else (reply[:120] if reply else "без правок")
    )
    return summary, actions, need_rebuild, reply


async def run_director(llm, scene: Scene, order: str, *, audit: bool = False, seed: int = -1) -> dict:
    """Call LLM once; chat reply + optional patches."""
    msgs = build_director_prompt(scene, order, audit=audit)
    prompt = render(msgs[0]["content"], [{"role": "user", "content": msgs[1]["content"]}], open_model="")
    content, stats = await llm.complete(
        prompt, n_predict=1600, stop=["<turn|>", "<|turn>"],
        temperature=0.55, seed=seed, json_schema=DIRECTOR_SCHEMA,
    )
    data = parse_director_json(content)
    summary, actions, rebuilt, reply = apply_patches(scene, data)
    if not reply:
        reply = summary
    player_line = (order or "").strip() or ("(аудит карточек)" if audit else "(открыл Директора)")
    scene.director_log = (getattr(scene, "director_log", None) or [])[-16:]
    scene.director_log.append(f"Игрок: {player_line[:240]}")
    scene.director_log.append(f"Директор: {reply[:400]}")
    parts = ["🎬 " + reply]
    if actions:
        parts.append("\n".join(f"· {a}" for a in actions[:20]))
    elif summary and summary not in reply:
        parts.append("· " + summary)
    if rebuilt:
        parts.append("(системный промпт пересобран — кэш префикса обновится на следующем ходе)")
    return {
        "summary": summary,
        "reply": reply,
        "actions": actions,
        "rebuilt": rebuilt,
        "text": "\n".join(parts),
        "stats": {k: stats.get(k) for k in ("wall_s", "prompt_n", "cache_n", "predicted_n")},
    }
