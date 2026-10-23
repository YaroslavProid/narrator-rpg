"""One-shot LLM generation of world, PC sheet+kit, and NPCs from short Russian briefs.
Not on the play hot path — separate prompt, json_schema, no need for byte-stable cache."""
from __future__ import annotations
import json, re
from . import mechanics as M
from .models import ATTRS

SCENE_GEN_SCHEMA = {
    "type": "object",
    "required": ["title", "setting", "player", "npcs"],
    "properties": {
        "title": {"type": "string", "minLength": 2, "maxLength": 80},
        "setting": {"type": "string", "minLength": 80, "maxLength": 2500},
        "player": {
            "type": "object",
            "required": ["name", "description", "personality", "attrs", "skills", "inventory"],
            "properties": {
                "name": {"type": "string", "minLength": 1, "maxLength": 40},
                "description": {"type": "string", "minLength": 20, "maxLength": 1200},
                "personality": {"type": "string", "maxLength": 500},
                "speech": {"type": "string", "maxLength": 400},
                "attrs": {
                    "type": "object",
                    "properties": {a: {"type": "integer"} for a in ATTRS},
                    "required": ATTRS,
                },
                "skills": {
                    "type": "object",
                    "additionalProperties": {"type": "integer"},
                },
                "inventory": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 2,
                    "maxItems": 12,
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
                    "present": {"type": "boolean"},
                },
            },
        },
    },
}


def build_prompt(title: str, premise: str, rule_mode: str, player_name: str, player_brief: str,
                 npc_briefs: list[dict], npc_count: int) -> list[dict]:
    skills = ", ".join(M.SKILLS)
    attrs = ", ".join(ATTRS)
    briefs = []
    for i, n in enumerate(npc_briefs or [], 1):
        briefs.append(f"{i}. Имя: {n.get('name') or 'придумай'}. Заметка: {n.get('brief') or n.get('description') or '—'}")
    if not briefs and npc_count > 0:
        briefs.append(f"(Нужно придумать ровно {npc_count} NPC, уместных для этого мира.)")
    elif npc_count > len(briefs):
        briefs.append(f"(Дополни до {npc_count} NPC: придумай недостающих.)")
    mode_note = (
        "Режим d20: атрибуты 8–16 (сумма около 72), навыки из списка 1–3 уровня, стартовый набор реалистичен."
        if rule_mode == "d20" else
        "Режим «система» (без кубиков): атрибуты 8–16, навыки из списка; инвентарь отражает роль, не «легендарный» хлам."
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
- setting: 2–4 абзаца лора и стартовой ситуации (где мы, тон, крючок). Без мета-комментариев.
- player: полная карточка. attrs — все из списка [{attrs}]. skills — только из [{skills}], 2–5 штук.
- inventory: 4–8 предметов по роли и миру (оружие/инструменты/мелочь), без легендарных артефактов.
- npcs: карточки с description, personality, speech (манеру), secrets (то, чего игрок не знает).
- Не используй английский. Не спрашивай уточнений — сразу готовый JSON.
"""
    system = (
        "Ты — соавтор настольной текстовой RPG. Отвечаешь только одним JSON-объектом по схеме. "
        "Пишешь по-русски, кратко и конкретно. Не морализируешь."
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _clamp_attrs(raw: dict) -> dict[str, int]:
    out = {a: 10 for a in ATTRS}
    if not isinstance(raw, dict):
        return out
    for k, v in raw.items():
        a = M.resolve_attr(str(k)) or (k if k in ATTRS else None)
        if not a:
            continue
        try:
            out[a] = max(3, min(18, int(v)))
        except (TypeError, ValueError):
            pass
    return out


def _clamp_skills(raw: dict) -> dict[str, int]:
    out = {}
    if not isinstance(raw, dict):
        return out
    for k, v in raw.items():
        s = M.resolve_skill(str(k))
        if not s:
            continue
        try:
            out[s] = max(0, min(5, int(v)))
        except (TypeError, ValueError):
            pass
    return dict(list(out.items())[:8])


def _inv(raw) -> list[str]:
    if not isinstance(raw, list):
        return []
    items = []
    for x in raw:
        t = re.sub(r"\s+", " ", str(x or "").strip())[:40]
        if not t or any(w in t.lower() for w in M.FORBIDDEN_ITEM_WORDS):
            continue
        items.append(t)
        if len(items) >= 10:
            break
    return items


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
    player = {
        "name": (pl.get("name") or player_name or "Герой").strip()[:40],
        "description": (pl.get("description") or player_name or "Персонаж игрока.").strip()[:1200],
        "personality": (pl.get("personality") or "").strip()[:500],
        "speech": (pl.get("speech") or "").strip()[:400],
        "attrs": _clamp_attrs(pl.get("attrs") or {}),
        "skills": _clamp_skills(pl.get("skills") or {}),
        "inventory": _inv(pl.get("inventory") or []),
    }
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
            "present": bool(n.get("present", True)),
        })
    if npc_count > 0 and len(npcs) > npc_count:
        npcs = npcs[:npc_count]
    return {"title": title, "setting": setting, "player": player, "npcs": npcs}


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
    from .prompt import render
    msgs = build_prompt(title, premise, rule_mode, player_name, player_brief, npc_briefs, npc_count)
    # raw completion via chat template for consistency with the rest of the app
    prompt = render(msgs[0]["content"], [{"role": "user", "content": msgs[1]["content"]}], open_model="")
    content, stats = await llm.complete(
        prompt, n_predict=1800, stop=["<turn|>", "<|turn>"],
        temperature=0.75, seed=seed, json_schema=SCENE_GEN_SCHEMA,
    )
    data = parse_model_json(content)
    out = normalize(data, title, premise, player_name, npc_count or len(npc_briefs or []))
    out["_stats"] = {k: stats.get(k) for k in ("wall_s", "prompt_n", "cache_n", "predicted_n")}
    return out
