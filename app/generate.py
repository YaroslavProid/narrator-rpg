"""One-shot LLM generation of world, PC sheet+kit, and NPCs from short Russian briefs.
Not on the play hot path — separate prompt, json_schema, no need for byte-stable cache."""
from __future__ import annotations
import json, re
from . import mechanics as M
from .models import ATTRS, ATTR_MIGRATE

# what an ability does — and how strong — is the AI's proposal; balance.fit keeps it in reach of the guide
ABILITY_PROPS = {
    "name": {"type": "string", "minLength": 2, "maxLength": 40},
    "kind": {"type": "string", "enum": ["active", "passive"]},
    "effect": {"type": "string", "enum": ["damage", "heal", "shield", "buff", "debuff", "control", "utility"]},
    "scale": {"type": "string", "enum": list(ATTRS)},
    "bonus": {"type": "string", "enum": list(M.BONUS_KEYS)},
    "description": {"type": "string", "maxLength": 200},
    "power": {"type": "integer", "minimum": 0, "maximum": 100},
    "mp": {"type": "integer", "minimum": 0, "maximum": 30},
    "recharge": {"type": "integer", "minimum": 0, "maximum": 5},
    "targets": {"type": "integer", "minimum": 1, "maximum": 3},
    "rider": {"type": "string", "maxLength": 30},
    "self_hp": {"type": "integer", "minimum": 0, "maximum": 30},
    "rule": {"type": "string", "maxLength": 160},
}

WORLD_SCHEMA = {
    "type": "object",
    "required": ["look", "tone", "places", "rules"],
    "properties": {
        "look": {"type": "string", "minLength": 40, "maxLength": 700},
        "tone": {"type": "string", "maxLength": 200},
        "places": {"type": "array", "minItems": 2, "maxItems": 5, "items": {"type": "string", "maxLength": 160}},
        "factions": {"type": "array", "maxItems": 4, "items": {"type": "string", "maxLength": 160}},
        "rules": {"type": "string", "minLength": 20, "maxLength": 500},
    },
}

_S = lambda n: {"type": "string", "maxLength": n}
RULES_SCHEMA = {
    "type": "object",
    "required": ["name", "paths", "mp_label", "attr_labels", "extra_attrs", "resources", "conditions", "weapons", "currency"],
    "properties": {
        "name": _S(40),
        "paths": {"type": "array", "minItems": 2, "maxItems": 5,
                  "items": {"type": "string", "enum": ["core-voin", "core-mag", "core-ten", "core-duh", "core-sledopyt"]}},
        "mp_label": _S(20),
        "attr_labels": {"type": "object", "properties": {a: _S(24) for a in ATTRS}},
        "extra_attrs": {"type": "array", "maxItems": 2, "items": {
            "type": "object", "required": ["label", "hint"], "properties": {"label": _S(24), "hint": _S(80)}}},
        "resources": {"type": "array", "maxItems": 2, "items": {
            "type": "object", "required": ["label", "max", "hint", "zero"],
            "properties": {"label": _S(24), "max": {"type": "integer", "minimum": 3, "maximum": 100},
                           "hint": _S(100), "zero": _S(120)}}},
        "conditions": {"type": "array", "maxItems": 4, "items": {
            "type": "object", "required": ["name", "kind", "hint"],
            "properties": {"name": _S(24), "kind": {"type": "string", "enum": ["dot", "stun", "mark"]}, "hint": _S(100)}}},
        "weapons": {"type": "array", "maxItems": 4, "items": {
            "type": "object", "required": ["name", "damage", "ranged"],
            "properties": {"name": _S(30), "damage": {"type": "integer", "minimum": 3, "maximum": 20},
                           "ranged": {"type": "boolean"}}}},
        "currency": _S(60),
        "notes": _S(400),
    },
}

SCENE_GEN_SCHEMA = {
    "type": "object",
    "required": ["title", "world", "rules", "setting", "player", "npcs"],
    "properties": {
        "title": {"type": "string", "minLength": 2, "maxLength": 80},
        "world": WORLD_SCHEMA,
        "rules": RULES_SCHEMA,
        "setting": {"type": "string", "minLength": 80, "maxLength": 2500},
        "player": {
            "type": "object",
            "required": ["name", "kind", "description", "personality", "attrs", "abilities", "inventory"],
            "properties": {
                "name": {"type": "string", "minLength": 1, "maxLength": 40},
                "description": {"type": "string", "minLength": 20, "maxLength": 1200},
                "personality": {"type": "string", "maxLength": 500},
                "speech": {"type": "string", "maxLength": 400},
                "kind": {"type": "string", "maxLength": 60},
                "attrs": {
                    "type": "object",
                    "properties": {a: {"type": "integer"} for a in ATTRS},
                    "required": ATTRS,
                },
                "abilities": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 8,
                    "items": {
                        "type": "object",
                        "required": ["name", "kind", "effect", "scale", "bonus", "power", "mp", "targets", "rider",
                                     "self_hp", "recharge", "rule"],
                        "properties": ABILITY_PROPS,
                    },
                },
                "inventory": {
                    "type": "array",
                    "minItems": 3,
                    "maxItems": 10,
                    "items": {
                        "type": "object",
                        "required": ["name", "why"],
                        "properties": {
                            "name": {"type": "string", "minLength": 2, "maxLength": 40},
                            "why": {"type": "string", "maxLength": 160},
                        },
                    },
                },
            },
        },
        "npcs": {
            "type": "array",
            "minItems": 0,
            "maxItems": 8,
            "items": {
                "type": "object",
                "required": ["name", "description", "personality"],
                "properties": {
                    "name": {"type": "string", "minLength": 1, "maxLength": 40},
                    "description": {"type": "string", "minLength": 10, "maxLength": 1200},
                    "personality": {"type": "string", "maxLength": 500},
                    "speech": {"type": "string", "maxLength": 400},
                    "secrets": {"type": "string", "maxLength": 400},
                    "kind": {"type": "string", "maxLength": 60},
                    "present": {"type": "boolean"},
                    "abilities": {
                        "type": "array",
                        "maxItems": 5,
                        "items": {
                            "type": "object",
                            "required": ["name", "kind"],
                            "properties": ABILITY_PROPS,
                        },
                    },
                },
            },
        },
    },
}


def build_prompt(title: str, premise: str, rule_mode: str, player_name: str, player_brief: str,
                 npc_briefs: list[dict], npc_count: int) -> list[dict]:
    attrs = ", ".join(ATTRS)
    briefs = []
    for i, n in enumerate(npc_briefs or [], 1):
        briefs.append(f"{i}. Имя: {n.get('name') or 'придумай'}. Заметка: {n.get('brief') or n.get('description') or '—'}")
    if not briefs and npc_count > 0:
        briefs.append(f"(Нужно придумать ровно {npc_count} NPC, уместных для этого мира.)")
    elif npc_count > len(briefs):
        briefs.append(f"(Дополни до {npc_count} NPC: придумай недостающих.)")
    mode_note = (
        "Режим d20: атрибуты 8–16 (сумма около 72), способности — именованные умения, стартовый набор реалистичен."
        if rule_mode == "d20" else
        "Режим «система» (без кубиков): атрибуты 8–16; способности по роли; инвентарь без «легендарного» хлама."
    )
    user = f"""Сгенерируй сцену текстовой RPG целиком по-русски.

Заголовок (можно улучшить): {title or 'придумай краткий'}
Замысел / зерно мира: {premise or 'мрачное фэнтези, таверна на краю мира'}
Режим правил: {rule_mode}. {mode_note}

Игрок — персонаж игрока:
- Имя: {player_name or 'придумай подходящее'}
- Кратко о нём: {player_brief or 'обычный искатель приключений с личной целью'}

NPC (присутствуют в стартовой сцене):
{chr(10).join(briefs) or '(без NPC — пустой список)'}

Требования:
- Замысел — канон. Всё, что в нём сказано (место, эпоха, тон, люди, завязка), обязательно есть в мире и ничему не противоречит; ты только дополняешь.
- world — облик мира, по нему рассказчик будет вести всю игру:
  look — как мир выглядит и звучит: эпоха, технологии или магия, архитектура, одежда, природа, быт;
  tone — тон и жанр в нескольких словах; places — 2–5 мест рядом с завязкой; factions — силы и группы, которые тянут сюжет;
  rules — что в этом мире возможно и невозможно (есть ли магия и какая, оружие, деньги, власть).
- rules — правила игры для этого мира (движок у всех миров общий, ты настраиваешь его под мир):
  paths — какие основные пути развития здесь существуют: core-voin (бой и оружие), core-mag (учёная магия),
  core-ten (скрытность, яды, обман), core-duh (вера, дух, исцеление), core-sledopyt (выживание, стрельба, звери).
  Нет магии — нет core-mag; нет веры с силой — нет core-duh. Оставь 2–5;
  mp_label — как в этом мире зовётся запас сил для особых приёмов (Мана, Ки, Энергия, Выдержка, Заряд);
  attr_labels — можно переименовать шесть атрибутов под мир (Разум → Смекалка, Влияние → Обаяние), не меняя их роли;
  пустые — оставь как есть; extra_attrs — 0–2 своих атрибута мира для проверок (Механика, Хакинг, Вера);
  resources — 0–2 ресурса героя, которых нет в обычной игре и которые важны для жанра (Рассудок в хорроре, Перегрев
  импланта в киберпанке, Голод в выживании): max, на что влияет (hint) и что случится на нуле (zero);
  conditions — 0–4 состояния мира (горение, заражение, паника…): kind dot — ранит каждый раунд, stun — теряет ход,
  mark — только для сюжета; weapons — 0–4 оружия этого мира, которого нет в обычном фэнтези (револьвер, плазменный
  резак), с уроном 3–20 (нож 5, меч 9, револьвер 12, винтовка 15) и ranged; currency — местные деньги;
  notes — особые правила одной-двумя фразами, если есть.
- setting: 2–4 абзаца — лор и стартовая ситуация в этом облике мира. Без мета-комментариев.
- player.kind — кто герой в этом мире одним-двумя словами (сыщица, наёмник, вор-карманник), по его описанию.
- player.attrs — все из списка [{attrs}] (не D&D-скиллы), по прошлому героя.
- player.abilities — 1–3 ИМЕНОВАННЫХ способности из прошлого героя: active (огненный шар, удар щитом) или passive (ночное зрение, нюх на ложь). НЕ пиши «Ближний бой=4» / «Знания» — это атрибуты. Способности не нарушают world.rules (нет магии в мире — нет и заклинаний). Для каждой укажи, что она делает: активной — effect (damage — урон, heal — лечение, shield — защита, buff — усиление союзника, debuff — ослабление врага, control — враг теряет ход, utility — небоевое) и scale (атрибут, который её усиливает); пассивной — bonus (hp, mp, ac, melee, ranged, spell, heal, init). Числа можешь предложить сам (power, mp): ориентир для старта — урон ≈12, лечение ≈10, щит ≈8, усиление ≈2, мана ≈4; сильнее — только за цену (больше маны, recharge — перезарядка в ходах, self_hp — плата здоровьем), несколько целей (targets) — меньше на каждую, rider — доп. состояние (горение, яд, страх). Необычную механику опиши в rule одной фразой. Система подрежет только явный перекос. Дальше герой растёт по Книге путей.
- inventory: 4–8 предметов, у каждого why — откуда он у героя. Снаряжение вытекает из прошлого и рода занятий героя и из эпохи мира (никаких мечей в киберпанке и пистолетов в средневековье); оружие — то, чем герой реально умеет пользоваться; деньги — в местной валюте; 1–2 предмета прямо связаны с завязкой (письмо, долговая расписка, ключ, знак гильдии). Без легендарного.
- npcs: description, personality, speech, secrets, kind (вид/тип: наёмник, дух, торговец…), опционально abilities. Их тайны и цели связаны с завязкой.
- Не используй английский. Сразу готовый JSON.
"""
    system = (
        "Ты — соавтор настольной текстовой RPG. Отвечаешь только одним JSON-объектом по схеме. "
        "Пишешь по-русски, кратко и конкретно. Не морализируешь. "
        "Способности — это силы/умения, проверки — только по атрибутам."
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _clamp_attrs(raw: dict) -> dict[str, int]:
    out = {a: 10 for a in ATTRS}
    if not isinstance(raw, dict):
        return out
    for k, v in raw.items():
        a = M.resolve_attr(str(k)) or ATTR_MIGRATE.get(k) or (k if k in ATTRS else None)
        if not a:
            continue
        try:
            out[a] = max(3, min(18, int(v)))
        except (TypeError, ValueError):
            pass
    return out


def _abilities(raw) -> list[dict]:
    out = []
    if not isinstance(raw, list):
        return out
    for x in raw:
        if not isinstance(x, dict):
            continue
        name = re.sub(r"\s+", " ", str(x.get("name") or "").strip())[:40]
        if not name:
            continue
        # reject old skill-style entries
        if M.resolve_skill(name) and not x.get("kind"):
            continue
        kind = str(x.get("kind") or "passive").lower()
        if kind not in ("active", "passive"):
            kind = "passive"
        out.append({k: v for k, v in {
            "name": name, "kind": kind,
            "effect": str(x.get("effect") or "") if kind == "active" else "",
            "scale": M.resolve_attr(str(x.get("scale") or "")) or "",
            "bonus": str(x.get("bonus") or "") if kind == "passive" else "",
            "description": str(x.get("description") or "").strip()[:200],
        }.items()} | {k: x[k] for k in ("power", "mp", "recharge", "targets", "rider", "self_hp", "rule")
                      if x.get(k) not in (None, "")})
        if len(out) >= 8:
            break
    return out


def _inv(raw) -> tuple[list[str], dict[str, str]]:
    """Item names plus why the hero has each (the reason is shown in the preview and kept for the narrator)."""
    if not isinstance(raw, list):
        return [], {}
    items, why = [], {}
    for x in raw:
        name = x.get("name") if isinstance(x, dict) else x
        t = re.sub(r"\s+", " ", str(name or "").strip().replace(",", " "))[:40].strip()
        if not t or t in items or any(w in t.lower() for w in M.FORBIDDEN_ITEM_WORDS):
            continue
        items.append(t)
        if isinstance(x, dict) and str(x.get("why") or "").strip():
            why[t] = str(x["why"]).strip()[:160]
        if len(items) >= 10:
            break
    return items, why


def _world(raw, premise: str) -> dict:
    w = raw if isinstance(raw, dict) else {}
    lst = lambda v, n: [re.sub(r"\s+", " ", str(x)).strip()[:160] for x in (v if isinstance(v, list) else []) if str(x).strip()][:n]
    return {"look": str(w.get("look") or "").strip()[:700], "tone": str(w.get("tone") or "").strip()[:200],
            "places": lst(w.get("places"), 5), "factions": lst(w.get("factions"), 4),
            "rules": str(w.get("rules") or "").strip()[:500]}


def normalize(data: dict, fallback_title: str = "", fallback_premise: str = "",
              player_name: str = "", npc_count: int = 0) -> dict:
    """Validate and clamp model JSON into create_scene-ready dict."""
    if not isinstance(data, dict):
        raise ValueError("ответ модели не JSON-объект")
    title = (data.get("title") or fallback_title or "Новая сцена").strip()[:80]
    setting = (data.get("setting") or fallback_premise or "").strip()
    if len(setting) < 40:
        setting = (setting + "\n" + (fallback_premise or "Мир ещё не описан подробно.")).strip()
    pl = data.get("player") or {}
    abil = _abilities(pl.get("abilities") or [])
    if not abil:
        abil = [{"name": "Закалённый в боях", "kind": "passive", "description": "Легче переносит боль и страх.", "cost": "", "cooldown": ""}]
    player = {
        "name": (pl.get("name") or player_name or "Герой").strip()[:40],
        "description": (pl.get("description") or player_name or "Персонаж игрока.").strip()[:1200],
        "personality": (pl.get("personality") or "").strip()[:500],
        "speech": (pl.get("speech") or "").strip()[:400],
        "kind": (pl.get("kind") or "искатель приключений").strip()[:60],
        "attrs": _clamp_attrs(pl.get("attrs") or {}),
        "abilities": abil,
    }
    player["inventory"], player["inventory_why"] = _inv(pl.get("inventory") or [])
    if not player["inventory"]:
        player["inventory"] = ["Нож", "Кошель", "Плащ"]
    npcs = []
    for n in data.get("npcs") or []:
        if not isinstance(n, dict):
            continue
        name = (n.get("name") or "").strip()[:40]
        if not name:
            continue
        npcs.append({
            "name": name,
            "description": (n.get("description") or "").strip()[:1200],
            "personality": (n.get("personality") or "").strip()[:500],
            "speech": (n.get("speech") or "").strip()[:400],
            "secrets": (n.get("secrets") or "").strip()[:400],
            "kind": (n.get("kind") or "").strip()[:60],
            "abilities": _abilities(n.get("abilities") or []),
            "present": bool(n.get("present", True)),
        })
    if npc_count > 0 and len(npcs) > npc_count:
        npcs = npcs[:npc_count]
    from . import rules as R
    return {"title": title, "premise": (fallback_premise or "").strip()[:1500], "world": _world(data.get("world"), fallback_premise),
            "rules": R.from_ai(data.get("rules")).model_dump(), "setting": setting, "player": player, "npcs": npcs}


def parse_model_json(content: str) -> dict:
    t = (content or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    a, b = t.find("{"), t.rfind("}")
    if a < 0 or b <= a:
        raise ValueError("в ответе нет JSON")
    return json.loads(t[a:b + 1])


async def generate_scene(llm, *, title: str, premise: str, rule_mode: str,
                         player_name: str, player_brief: str,
                         npc_briefs: list[dict], npc_count: int = 0,
                         seed: int = -1) -> dict:
    """Call llama-server once and return a normalized scene draft."""
    from .prompt import render, stops
    msgs = build_prompt(title, premise, rule_mode, player_name, player_brief, npc_briefs, npc_count)
    # raw completion via chat template for consistency with the rest of the app
    prompt = render(msgs[0]["content"], [{"role": "user", "content": msgs[1]["content"]}], open_model="")
    content, stats = await llm.complete(
        prompt, n_predict=3200, stop=stops(),
        temperature=0.75, seed=seed, json_schema=SCENE_GEN_SCHEMA,
    )
    data = parse_model_json(content)
    out = normalize(data, title, premise, player_name, npc_count or len(npc_briefs or []))
    out["_stats"] = {k: stats.get(k) for k in ("wall_s", "prompt_n", "cache_n", "predicted_n")}
    return out
