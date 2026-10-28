"""The System as a conversational agent: answers the player's questions about the rules, the hero's sheet,
the party and the Book of paths, and may request one action. Code checks and performs the action — the agent
never decides on its own whether a requirement is met (that status is computed and handed to it)."""
from __future__ import annotations
import json
from . import balance as B, combat as C, config, magic as G, mechanics as M
from .models import ATTRS, Scene
from .prompt import render, stops

KNOWLEDGE = f"""Ты — Система: безличный интерфейс мира в духе LitRPG. Говоришь кратко, сухо, по делу, обращаешься к игроку на «вы». Отвечаешь по-русски.

Что ты знаешь и объясняешь:
- Проверки: по атрибутам ({', '.join(ATTRS)}). Режим d20 — бросок d20 + мягкий бонус от атрибута против сложности; режим «Система» — шанс: 50% при атрибуте, равном сложности, ±5% за каждый пункт разницы (от 5% до 95%).
- Урон, лечение и щиты — точные числа (меч — 9 урона, «Магическая стрела» — 12), атрибут их усиливает: +5% за каждый пункт выше 10 (Сила 14 — ×1,2). Сила — физический урон в ближнем бою и блок; Ловкость — попадание, уклонение и урон стрелкового оружия; Выносливость — здоровье (+{B.HP_PER_END} HP за пункт выше 10); Разум — учёная магия и мана (+{B.MP_PER_MIND} MP за пункт); Воля — лечение, обереги, вера и сопротивление чарам; Влияние — голос и усиление союзников. Броня снимает своё число с каждого физического удара (не меньше 1 урона); магию броня не держит. Попадание: 65% ± 5% за разницу атакующего атрибута и Ловкости цели (в «Системе»).
- Здоровье: {B.HP_BASE} + {B.HP_LEVEL} за каждый уровень после первого + Выносливость; мана: {B.MP_BASE} + {B.MP_LEVEL} за уровень + Разум.
- Опыт считается долей уровня, поэтому гринда нет: обычный враг своего уровня — 8% уровня, опасный — 15%, элита — 30%, босс — 60%; за каждый уровень, на который враг сильнее, награда ×1,4, слабее — ×0,6. Победа над тем, кто намного сильнее, даёт сразу несколько уровней. Трудные проверки (сложность 15+) — 3–5% уровня, попадание — 1%. Новый уровень: +{B.HP_LEVEL} HP, +{B.MP_LEVEL} MP (и полное восстановление), +2 очка атрибутов, +1 очко способностей.
- Очки атрибутов вкладывает игрок («Система, распредели Сила +1»). Очки способностей тратятся ТОЛЬКО в Книге путей.
- Книга путей. Основные пути есть в любом мире: Воин, Маг, Тень, Дух, Следопыт (в этом мире могут называться иначе), по пять рангов; ранг стоит очки способностей и даёт способность. Отрасли — узкие искусства, растущие из основных путей: герой видит что-то в мире (след в Книге), изучает это в истории (книга, наставник, наблюдение) — и Система открывает отрасль из трёх ступеней. Ступени требуют ранга основного пути и очков; ступень III — ещё и сюжетного события. Ранги и ступени открываются по порядку.
- Отряд: до {config.PARTY_MAX} вместе с героем. Спутник вступает по своей воле, начинает с 1-го уровня; его очки распределяет игрок, снаряжением спутник распоряжается сам. За победы опыт считается от уровня каждого, поэтому отстающий спутник растёт быстрее сам собой. В бою ходы идут по очереди; в ход спутника игрок может дать совет — спутник может отказаться от бессмысленного. В «Системе» защита активная: удар врага по герою ждёт реакции игрока — уклониться (Ловкость, тяжёлая броня мешает), блок (Сила, щит помогает; урон вдвое меньше), парировать (нужно оружие ближнего боя; трудно, при успехе — контратака, при провале урон ×1,5) или принять удар (шанс, что он пройдёт мимо; броня, как всегда, снимает своё). Один и тот же приём подряд враги читают: −10% за повтор. Кнопки — лишь быстрый путь: игрок может описать реакцию или приём своими словами, рассказчик оценит, реально ли это и насколько трудно (легко/обычно/трудно/очень трудно), а Система бросит по атрибуту. Большая выгода никогда не бывает «легко»: увернуться целиком — минимум «обычно», контратака — минимум «трудно».

Что ты можешь сделать (только если игрок прямо просит, и только одно действие за ответ):
- unlock_path — открыть следующий ранг основного пути или ступень отрасли для героя или спутника;
- assign — вложить очки атрибутов.
Код проверит требования и выполнит действие или откажет; поэтому не обещай результат, а говори «запрашиваю».

Чего ты НЕ можешь: выдавать предметы и деньги, лечить, менять уровень или опыт, решать за рассказчика, что случится в сюжете, отмечать сюжетные условия выполненными, открывать отрасли (они открываются только через историю), раскрывать тайны NPC и характеристики неосмотренных противников. На такие просьбы вежливо отказывай и объясняй, как этого добиться в игре.
Если игрок спрашивает о пути, которого нет в Книге (например, «путь берсерка»), скажи, что такой отрасли он пока не открыл, предложи ближайший основной путь и объясни, как её найти: увидеть такое искусство в мире и изучить его.
Требования считает код — смотри блок «Статус путей» и не спорь с ним. Не выдумывай пути, способности и числа, которых нет в данных ниже."""

SCHEMA = {
    "type": "object", "required": ["reply", "action"],
    "properties": {
        "reply": {"type": "string", "minLength": 1, "maxLength": 1500},
        "action": {"type": "object", "required": ["type"], "properties": {
            "type": {"type": "string", "enum": ["none", "unlock_path", "assign"]},
            "who": {"type": "string"}, "path": {"type": "string"}, "ability": {"type": "string"},
            "attr": {"type": "string"}, "amount": {"type": "integer", "minimum": 1, "maximum": 5}}},
    },
}


def sheet(scene: Scene, c) -> str:
    abil = ", ".join(f"{a.name} ({'акт.' if a.kind == 'active' else 'пасс.'}{', ' + M.ability_stats(a, c) if M.ability_stats(a, c) else ''})"
                     for a in c.abilities) or "нет"
    paths = ", ".join(f"{p.name} {G.ROMAN[c.paths[p.id]]}" for p in scene.paths if c.paths.get(p.id)) or "нет"
    return (f"{c.name}{' (герой)' if c.is_player else ' (спутник)'}: ур. {c.level}, HP {c.hp}/{c.max_hp}, MP {c.mp}/{c.max_mp}, "
            f"EXP {c.exp}/{c.exp_next}, {M.weapon_line(c)}, броня {C.armor_value(c)}. Атрибуты: " + ", ".join(f"{k} {v}" for k, v in c.attrs.items())
            + f". Свободные очки: атрибуты {c.attr_points}, способности {c.ability_points}. Способности: {abil}. Пути: {paths}. "
            + "Опись: " + (", ".join(f"{i.name}{' ×' + str(i.qty) if i.qty > 1 else ''}{' [надето]' if i.equipped else ''}"
                                     for i in c.inventory) or "пусто")
            + ". Состояния: " + (", ".join(f"{x.name} ({x.turns_left})" for x in c.conditions) or "нет") + ".")


def context(scene: Scene) -> str:
    party = [scene.characters[i] for i in M.party_ids(scene)]
    parts = ["# Отряд", *[sheet(scene, c) for c in party]]
    parts.append("\n# Книга путей этого мира")
    for p in scene.paths:
        kind = "основной путь" if p.core else "отрасль от " + ", ".join(
            f"{(G.get_path(scene, pid) or p).name} {G.ROMAN[r]}" for pid, r in p.parents.items())
        parts.append(f"## {p.name} ({kind}) — {p.description}")
        for t in p.tiers:
            reqs = "; ".join(G.req_status(scene, scene.player, r)[1].split(" (")[0] for r in t.reqs)
            parts.append(f"- {'ранг' if p.core else 'ступень'} {G.ROMAN[t.tier]} [{reqs}]: " +
                         "; ".join(f"{a.name} ({'акт.' if a.kind == 'active' else 'пасс.'}, {M.ability_stats(a)}) — {a.description}"
                                   for a in t.abilities))
    leads = [l for l in scene.leads if not l.branch]
    if leads:
        parts.append("\n# Следы (видели, но отрасль ещё не открыта)")
        parts += [f"- {l.what}" + (f" ({l.source})" if l.source else "") + (" — изучено, отрасль пишется" if l.studied else " — не изучено")
                  for l in leads]
    parts.append("\n# Статус путей (посчитано кодом — это истина)")
    for c in party:
        for p in scene.paths:
            s = G.tier_status(scene, c, p)
            if s["done"]:
                parts.append(f"- {c.name} / {p.name}: пройден полностью")
            elif s["ok"] or c.paths.get(p.id) or p.core:
                parts.append(f"- {c.name} / {p.name} / {'ранг' if p.core else 'ступень'} {G.ROMAN[s['tier']]}: "
                             + ("МОЖНО ОТКРЫТЬ" if s["ok"] else "закрыто, не хватает: " + "; ".join(s["missing"])))
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
    if t == "assign":
        attr = M.resolve_attr(action.get("attr") or "")
        n = int(action.get("amount") or 1)
        if not attr:
            return "Отказано: неизвестный атрибут."
        if n > who.attr_points:
            return f"Отказано: у {who.name} {who.attr_points} очк. атрибутов."
        who.attr_points -= n; who.attrs[attr] += n
        extra = M.sync_derived(who)
        return (f"✔ {who.name}: {attr} {who.attrs[attr] - n}→{who.attrs[attr]}. Осталось очков: {who.attr_points}."
                + (" " + "; ".join(extra) + "." if extra else ""))
    return None


async def ask(llm, scene: Scene, question: str, seed: int = -1) -> dict:
    """One agent turn. Returns {text, stats}; never advances the story turn."""
    calls = []
    G.ensure_core(scene)
    user = context(scene) + f"\n\n# Вопрос игрока\n{question.strip()}"
    content, st = await llm.complete(render(KNOWLEDGE, [{"role": "user", "content": user}], open_model=""), 700,
                                     stop=stops(), temperature=0.3, seed=seed, json_schema=SCHEMA)
    st["kind"] = "system agent"; calls.append(st)
    try:
        data = json.loads(content)
    except ValueError:
        data = {"reply": content.strip()[:1500] or "Система не смогла сформулировать ответ.", "action": {"type": "none"}}
    reply = (data.get("reply") or "").strip()
    outcome = perform(scene, data.get("action") or {})
    text = "### ⟦ СИСТЕМА ⟧\n" + reply + (f"\n\n{outcome}" if outcome else "")
    return {"text": text, "calls": calls, "action": data.get("action"), "outcome": outcome}
