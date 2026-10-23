"""The System as a conversational agent: answers the player's questions about the rules, the hero's sheet,
the party and the magic paths, and may request one action. Code checks and performs the action — the agent
never decides on its own whether a requirement is met (that status is computed and handed to it)."""
from __future__ import annotations
import json
from . import combat as C, config, magic as G, mechanics as M
from .models import ATTRS, Scene
from .prompt import render

KNOWLEDGE = f"""Ты — Система: безличный интерфейс мира в духе LitRPG. Говоришь кратко, сухо, по делу, обращаешься к игроку на «вы». Отвечаешь по-русски.

Что ты знаешь и объясняешь:
- Проверки: по атрибутам ({', '.join(ATTRS)}). Режим d20 — бросок d20 + мягкий бонус от атрибута против сложности; режим «Система» — сравнение атрибута со сложностью без броска.
- Опыт: за свои удачные проверки и попадания; за поверженного врага — всем членам отряда поровну. Новый уровень: +5 HP, +3 MP, +2 очка атрибутов, +1 очко способностей.
- Очки атрибутов вкладывает игрок («Система, распредели Сила +1»), очки способностей — на способности из списка предложений или на ступени путей магии.
- Отряд: до {config.PARTY_MAX} вместе с героем. Спутник вступает по своей воле, начинает с 1-го уровня; его очки распределяет игрок, снаряжением спутник распоряжается сам. Отстающие спутники получают опыт с множителем 1,5 за каждый уровень разницы с героем (не больше ×{config.EXP_CATCHUP_MAX:g}).
- Пути магии мира: у каждого пути три ступени. Требования ступени: уровень, главный атрибут пути, очки способностей и сюжетное условие (наставник, книга, ритуал — его выполнение отмечает история). Не больше {G.MAX_PATHS} путей на персонажа. Ступени открываются по порядку.

Что ты можешь сделать (только если игрок прямо просит, и только одно действие за ответ):
- unlock_path — открыть следующую ступень пути для героя или спутника;
- take_ability — открыть способность из списка предложений;
- assign — вложить очки атрибутов.
Код проверит требования и выполнит действие или откажет; поэтому не обещай результат, а говори «запрашиваю».

Чего ты НЕ можешь: выдавать предметы и деньги, лечить, менять уровень или опыт, решать за рассказчика, что случится в сюжете, отмечать сюжетные условия выполненными, раскрывать тайны NPC и характеристики неосмотренных противников. На такие просьбы вежливо отказывай и объясняй, как этого добиться в игре.
Требования считает код — смотри блок «Статус путей» и не спорь с ним. Не выдумывай пути, способности и числа, которых нет в данных ниже."""

SCHEMA = {
    "type": "object", "required": ["reply", "action"],
    "properties": {
        "reply": {"type": "string", "minLength": 1, "maxLength": 1500},
        "action": {"type": "object", "required": ["type"], "properties": {
            "type": {"type": "string", "enum": ["none", "unlock_path", "take_ability", "assign"]},
            "who": {"type": "string"}, "path": {"type": "string"}, "ability": {"type": "string"},
            "attr": {"type": "string"}, "amount": {"type": "integer", "minimum": 1, "maximum": 5}}},
    },
}


def sheet(scene: Scene, c) -> str:
    abil = ", ".join(f"{a.name} ({'акт.' if a.kind == 'active' else 'пасс.'}{', ' + a.cost if a.cost else ''})" for a in c.abilities) or "нет"
    paths = ", ".join(f"{p.name} {G.ROMAN[c.paths[p.id]]}" for p in scene.paths if c.paths.get(p.id)) or "нет"
    return (f"{c.name}{' (герой)' if c.is_player else ' (спутник)'}: ур. {c.level}, HP {c.hp}/{c.max_hp}, MP {c.mp}/{c.max_mp}, "
            f"EXP {c.exp}/{c.exp_next}, КБ {C.ac(c)}. Атрибуты: " + ", ".join(f"{k} {v}" for k, v in c.attrs.items())
            + f". Свободные очки: атрибуты {c.attr_points}, способности {c.ability_points}. Способности: {abil}. Пути: {paths}. "
            + "Опись: " + (", ".join(f"{i.name}{' ×' + str(i.qty) if i.qty > 1 else ''}{' [надето]' if i.equipped else ''}"
                                     for i in c.inventory) or "пусто")
            + ". Состояния: " + (", ".join(f"{x.name} ({x.turns_left})" for x in c.conditions) or "нет") + ".")


def context(scene: Scene) -> str:
    party = [scene.characters[i] for i in M.party_ids(scene)]
    parts = ["# Отряд", *[sheet(scene, c) for c in party]]
    offers = M.ability_offers(scene, 3) if scene.player.ability_points > 0 else []
    if offers:
        parts.append("Способности к открытию у героя: " + "; ".join(f"{x['name']} — {x.get('description') or ''}" for x in offers))
    parts.append("\n# Пути магии этого мира")
    for p in scene.paths:
        parts.append(f"## {p.name} (атрибут {p.attr}) — {p.description}")
        for t in p.tiers:
            reqs = "; ".join(r.text if r.kind == "story" else
                             (f"уровень {r.value}" if r.kind == "level" else f"{r.attr} {r.value}" if r.kind == "attr"
                              else f"очков способностей {r.value}") for r in t.reqs)
            parts.append(f"- ступень {G.ROMAN[t.tier]}: {reqs}. Даёт: " +
                         "; ".join(f"{a.name} ({'акт.' if a.kind == 'active' else 'пасс.'}{', ' + a.cost if a.cost else ''}) — {a.description}" for a in t.abilities))
    parts.append("\n# Статус путей (посчитано кодом — это истина)")
    for c in party:
        for p in scene.paths:
            s = G.tier_status(scene, c, p)
            if s["done"]:
                parts.append(f"- {c.name} / {p.name}: все ступени пройдены")
            else:
                parts.append(f"- {c.name} / {p.name} / ступень {G.ROMAN[s['tier']]}: "
                             + ("МОЖНО ОТКРЫТЬ" if s["ok"] else "закрыта, не хватает: " + "; ".join(s["missing"])))
    tail = [f"{e.who + ': ' if e.who else ''}{e.text[:220]}" for e in scene.log[-8:] if e.kind in ("narration", "speech", "player")]
    if tail:
        parts.append("\n# Последние события\n" + "\n".join(tail))
    return "\n".join(parts)


def perform(scene: Scene, action: dict) -> str | None:
    """Run the requested action through the same checks as the deterministic commands. Returns the outcome."""
    t = (action or {}).get("type") or "none"
    if t == "none":
        return None
    who = M.resolve_character(scene, action.get("who") or "") if action.get("who") else scene.player
    if who is None or not M.in_party(scene, who):
        return f"Отказано: «{action.get('who')}» не состоит в отряде."
    if t == "unlock_path":
        ok, msg = G.unlock_tier(scene, who, action.get("path") or "")
        return ("✔ " if ok else "Отказано: ") + msg
    if t == "take_ability":
        return M.take_ability(scene, action.get("ability") or "", who)
    if t == "assign":
        attr = M.resolve_attr(action.get("attr") or "")
        n = int(action.get("amount") or 1)
        if not attr:
            return "Отказано: неизвестный атрибут."
        if n > who.attr_points:
            return f"Отказано: у {who.name} {who.attr_points} очк. атрибутов."
        who.attr_points -= n; who.attrs[attr] += n
        return f"✔ {who.name}: {attr} {who.attrs[attr] - n}→{who.attrs[attr]}. Осталось очков: {who.attr_points}."
    return None


async def ask(llm, scene: Scene, question: str, seed: int = -1) -> dict:
    """One agent turn. Returns {text, stats}; never advances the story turn."""
    calls = await G.ensure_paths(llm, scene, seed=seed)
    user = context(scene) + f"\n\n# Вопрос игрока\n{question.strip()}"
    content, st = await llm.complete(render(KNOWLEDGE, [{"role": "user", "content": user}], open_model=""), 700,
                                     stop=["<turn|>", "<|turn>"], temperature=0.3, seed=seed, json_schema=SCHEMA)
    st["kind"] = "system agent"; calls.append(st)
    try:
        data = json.loads(content)
    except ValueError:
        data = {"reply": content.strip()[:1500] or "Система не смогла сформулировать ответ.", "action": {"type": "none"}}
    reply = (data.get("reply") or "").strip()
    outcome = perform(scene, data.get("action") or {})
    text = "### ⟦ СИСТЕМА ⟧\n" + reply + (f"\n\n{outcome}" if outcome else "")
    return {"text": text, "calls": calls, "action": data.get("action"), "outcome": outcome}
