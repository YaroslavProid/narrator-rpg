"""Director: one-shot LLM that patches character cards and scene fields when asked.

Not on the play hot path — separate prompt + json_schema. After apply, rebuild system_prompt
(invalidates prompt cache for the fixed prefix; accepted trade-off).
"""
from __future__ import annotations
import json, re
from . import mechanics as M
from . import prompts as P
from .models import ATTRS, Ability, Character, Item, Scene
from .prompt import render

DIRECTOR_SCHEMA = {
    "type": "object",
    "required": ["summary", "character_patches", "scene_patch"],
    "properties": {
        "summary": {"type": "string", "minLength": 5, "maxLength": 400},
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
        hint_block = "Замеченные несостыковки (исправь, если уместно):\n- " + "\n- ".join(hints) + "\n\n"
    if audit and not (order or "").strip():
        order = (
            "Проверь все карточки и сцену: дополни пустой инвентарь по роли, уточни вид/описание/способности, "
            "убери противоречия. Не меняй сюжет без нужды."
        )
    attrs = ", ".join(ATTRS)
    user = f"""Ты — Директор текстовой RPG. По запросу игрока (или аудиту) внеси точечные правки карточек и сцены.

{scene_brief(scene)}

{hint_block}Запрос: {order.strip() or 'исправь несостыковки карточек'}

Правила ответа — один JSON по схеме:
- character_patches: правки по имени who (как в карточках). Меняй только нужные поля.
- abilities_set заменяет весь список способностей; abilities_add — добавляет.
- inventory_add / inventory_remove — предметы (без легендарного хлама).
- attrs — только из [{attrs}], значения 3–18; небольшие сдвиги.
- scene_patch: title/setting осторожно; present_add/present_remove — имена.
- Не удаляй персонажа игрока из сцены. Не пиши код. Только по-русски.
- summary: 1–2 предложения, что сделал.
Если правок не нужно — пустые массивы/объект и summary «правок не требуется».
"""
    system = (
        "Ты Директор: правишь карточки и сеттинг для согласованности. "
        "Отвечаешь одним JSON. Кратко, по делу, по-русски."
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


def apply_patches(scene: Scene, data: dict) -> tuple[str, list[str], bool]:
    """Apply validated director JSON. Returns (summary_text, action_lines, rebuilt_prompt)."""
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

    summary = str(data.get("summary") or "").strip()[:400] or (
        "Директор внёс правки." if actions else "Правок не требуется."
    )
    if not actions:
        actions.append("изменений нет")
    return summary, actions, need_rebuild


async def run_director(llm, scene: Scene, order: str, *, audit: bool = False, seed: int = -1) -> dict:
    """Call LLM once and apply patches. Returns dict with summary, actions, rebuilt, stats."""
    msgs = build_director_prompt(scene, order, audit=audit)
    prompt = render(msgs[0]["content"], [{"role": "user", "content": msgs[1]["content"]}], open_model="")
    content, stats = await llm.complete(
        prompt, n_predict=1400, stop=["<turn|>", "<|turn>"],
        temperature=0.4, seed=seed, json_schema=DIRECTOR_SCHEMA,
    )
    data = parse_director_json(content)
    summary, actions, rebuilt = apply_patches(scene, data)
    line = "🎬 " + summary
    if actions and actions != ["изменений нет"]:
        line += "\n" + "\n".join(f"· {a}" for a in actions[:20])
    if rebuilt:
        line += "\n(системный промпт пересобран — кэш префикса сбросится на следующем ходе)"
    # keep short log on scene
    scene.director_log = (getattr(scene, "director_log", None) or [])[-19:] + [summary]
    return {
        "summary": summary,
        "actions": actions,
        "rebuilt": rebuilt,
        "text": line,
        "stats": {k: stats.get(k) for k in ("wall_s", "prompt_n", "cache_n", "predicted_n")},
    }
