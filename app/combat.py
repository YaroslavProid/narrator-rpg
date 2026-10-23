"""Combat: initiative, attack/defense, damage dice, conditions. Pure code; narrator only announces intent."""
import re
from . import mechanics as M
from .models import Ability, Character, Combat, Combatant, Condition, Item, Scene

WEAPON_DEFAULTS = {  # name substring -> (damage, kind)
    "меч": ("1d8", "оружие"), "сабл": ("1d8", "оружие"), "кинжал": ("1d4", "оружие"), "нож": ("1d4", "оружие"),
    "топор": ("1d8", "оружие"), "булав": ("1d6", "оружие"), "дубин": ("1d6", "оружие"), "копь": ("1d6", "оружие"),
    "лук": ("1d8", "оружие"), "арбалет": ("1d8", "оружие"), "пистол": ("1d10", "оружие"), "кулак": ("1d3", "оружие"),
}
ARMOR_DEFAULTS = {"кольчуг": 4, "латы": 6, "кожан": 2, "щит": 2, "броня": 3}


def parse_dice(expr: str) -> tuple[int, int, int]:
    """'1d8+2' → (n, sides, bonus). Bare int → (0, 0, int)."""
    e = (expr or "").strip().lower().replace(" ", "")
    m = re.fullmatch(r"(\d+)d(\d+)([+-]\d+)?", e)
    if m and int(m.group(2)) > 0:
        return min(20, int(m.group(1))), int(m.group(2)), int(m.group(3) or 0)
    try:
        return 0, 0, int(e)
    except ValueError:
        return 1, 4, 0


def roll_dice(scene: Scene, expr: str) -> tuple[int, str]:
    n, sides, bonus = parse_dice(expr)
    if sides == 0:
        return bonus, str(bonus)
    rolls = [M.rng(scene).randint(1, sides) for _ in range(max(1, n))]
    total = sum(rolls) + bonus
    body = "+".join(map(str, rolls)) + (f"{bonus:+d}" if bonus else "")
    return total, f"{n}d{sides}({body})={total}"


def ac(c: Character) -> int:
    armor = sum(i.armor for i in c.inventory if i.equipped and i.kind == "броня")
    return 10 + M.mod(c.attrs.get("Ловкость", 10)) + armor + M.passive_bonus(c, "ac")


def cond_potency(c: Character, name: str) -> int:
    return max((x.potency for x in c.conditions if x.name == name), default=0)


def initiative(scene: Scene, c: Character) -> int:
    return M.rng(scene).randint(1, 20) + M.soft_weight(c.attrs.get("Ловкость", 10), 10) + M.passive_bonus(c, "init")


def absorb(target: Character, dmg: int) -> tuple[int, str]:
    """A shield (spell/ward) soaks damage first and wears off when spent."""
    sh = next((x for x in target.conditions if x.name == "щит" and x.potency > 0), None)
    if not sh or dmg <= 0:
        return dmg, ""
    soak = min(sh.potency, dmg); sh.potency -= soak
    if sh.potency <= 0:
        target.conditions.remove(sh)
    return dmg - soak, f" (щит поглотил {soak})"


def equipped_weapon(c: Character) -> Item | None:
    w = next((i for i in c.inventory if i.equipped and i.kind == "оружие"), None)
    if w:
        return w
    for i in c.inventory:
        if i.kind == "оружие" or any(k in i.name.lower() for k in WEAPON_DEFAULTS):
            return i
    return None


def ensure_item_stats(item: Item) -> Item:
    n = item.name.lower()
    if not item.damage:
        for k, (dmg, kind) in WEAPON_DEFAULTS.items():
            if k in n:
                item.damage, item.kind = dmg, kind; break
    if not item.armor:
        for k, a in ARMOR_DEFAULTS.items():
            if k in n:
                item.armor, item.kind = a, "броня"; break
    if item.kind == "оружие" and not item.slot:
        item.slot = "рука"
    if item.kind == "броня" and not item.slot:
        item.slot = "тело"
    return item


def attack_bonus(c: Character, weapon: Item | None) -> tuple[int, str]:
    attr = "Ловкость" if is_ranged(weapon) else "Сила"
    # Soft weight instead of hard mod + skill level; buffs/debuffs from spells shift it
    return M.soft_weight(c.attrs.get(attr, 10), 10) + cond_potency(c, "усиление") - cond_potency(c, "ослабление"), attr


def is_ranged(weapon: Item | None) -> bool:
    return bool(weapon) and any(k in weapon.name.lower() for k in ("лук", "арбалет", "пистол", "ружь", "винтов"))


def damage_bonus(c: Character, weapon: Item | None) -> int:
    """Strength (melee) / Agility (ranged) amplify weapon damage, plus passives and buffs."""
    if is_ranged(weapon):
        return M.amp(c.attrs.get("Ловкость", 10)) + M.passive_bonus(c, "ranged") + cond_potency(c, "усиление")
    return M.amp(c.attrs.get("Сила", 10)) + M.passive_bonus(c, "melee") + cond_potency(c, "усиление")


def start_combat(scene: Scene, ids: list[str]) -> str:
    ids = [i for i in ids if i in scene.characters]
    if scene.player_id not in ids:
        ids = [scene.player_id] + ids
    for i in ids:
        if i not in scene.present:
            scene.present.append(i)
    order = []
    for i in ids:
        c = scene.characters[i]
        order.append(Combatant(id=i, initiative=initiative(scene, c)))
    order.sort(key=lambda x: -x.initiative)
    scene.combat = Combat(active=True, round=1, order=order, current=0)
    lines = [f"⟦ БОЙ · раунд 1 ⟧ Инициатива: " + ", ".join(
        f"{scene.characters[o.id].name} {o.initiative}" for o in order)]
    cur = scene.characters[order[0].id]
    lines.append(f"Ход: {cur.name}." + (" (ты)" if cur.is_player else " Опиши его действие или дождись атаки."))
    return "\n".join(lines)


def join_combat(scene: Scene, ids: list[str]) -> str:
    """Combat is already running: roll initiative only for newcomers and slot them in without resetting turns."""
    cb = scene.combat
    have = {o.id for o in cb.order}
    added = []
    for i in ids:
        if i in have or i not in scene.characters:
            continue
        c = scene.characters[i]
        if i not in scene.present:
            scene.present.append(i)
        cb.order.append(Combatant(id=i, initiative=initiative(scene, c)))
        added.append(f"{c.name} {cb.order[-1].initiative}")
    cur = cb.order[cb.current].id if cb.order else None
    cb.order.sort(key=lambda x: -x.initiative)
    cb.current = next((k for k, o in enumerate(cb.order) if o.id == cur), 0)
    if not added:
        return combat_status(scene)
    return f"⟦ БОЙ · раунд {cb.round} ⟧ В бой вступают: {', '.join(added)}. Ход: {scene.characters[cb.order[cb.current].id].name}."


def end_combat(scene: Scene) -> str:
    scene.combat = Combat()
    return "⟦ БОЙ ОКОНЧЕН ⟧"


def advance_turn(scene: Scene) -> str:
    cb = scene.combat
    if not cb.active or not cb.order:
        return ""
    cb.order[cb.current].acted = True
    nxt = (cb.current + 1) % len(cb.order)
    if nxt == 0:
        cb.round += 1
        for o in cb.order:
            o.acted = False
        tick = tick_conditions(scene)
        cb.current = 0
        who = scene.characters[cb.order[0].id]
        return f"Раунд {cb.round}. Ход: {who.name}." + (("\n" + tick) if tick else "")
    cb.current = nxt
    who = scene.characters[cb.order[nxt].id]
    return f"Ход: {who.name}." + (" (ты)" if who.is_player else "")


def tick_conditions(scene: Scene) -> str:
    msgs = []
    for c in scene.characters.values():
        keep = []
        for cond in c.conditions:
            if cond.name == "кровотечение" and cond.potency > 0 and c.hp > 0:
                old = c.hp; c.hp = max(0, c.hp - cond.potency)
                msgs.append(f"{c.name}: кровотечение −{cond.potency} HP ({old}→{c.hp})")
            cond.turns_left -= 1
            if cond.turns_left > 0:
                keep.append(cond)
            else:
                msgs.append(f"{c.name}: состояние «{cond.name}» прошло")
        c.conditions = keep
    return "\n".join(msgs)


def apply_condition(c: Character, name: str, turns: int = 2, potency: int = 0) -> str:
    name = name.strip().lower()
    aliases = {"bleed": "кровотечение", "bleeding": "кровотечение", "stun": "оглушение", "stunned": "оглушение",
               "poison": "отравление", "poisoned": "отравление", "fear": "испуг", "frightened": "испуг"}
    name = aliases.get(name, name)
    for cond in c.conditions:
        if cond.name == name:
            cond.turns_left = max(cond.turns_left, turns)
            cond.potency = max(cond.potency, potency)
            return f"{c.name}: продлено «{name}» ({cond.turns_left} ход.)"
    c.conditions.append(Condition(name=name, turns_left=turns, potency=potency))
    return f"{c.name}: состояние «{name}» ({turns} ход.)"


def attack(scene: Scene, attacker: Character, target: Character, weapon_name: str = "") -> dict:
    """Resolve one attack. Auto-starts combat if needed. Returns result dict with text."""
    if not scene.combat.active:
        start_combat(scene, [attacker.id, target.id])
    w = None
    if weapon_name:
        w = next((i for i in attacker.inventory if weapon_name.lower() in i.name.lower()), None)
        if w:
            ensure_item_stats(w)
    w = w or equipped_weapon(attacker)
    if w:
        ensure_item_stats(w)
    dmg_expr = (w.damage if w and w.damage else "1d4")
    bonus, skill = attack_bonus(attacker, w)
    wname = w.name if w else "безоружный удар"
    target_ac = ac(target)
    msgs = []
    if scene.rule_mode == "d20":
        die = M.rng(scene).randint(1, 20)
        total = die + bonus
        hit = die == 20 or (die != 1 and total >= target_ac)
        crit = die == 20
        atk_txt = f"1d20({die}){bonus:+d}={total} против КБ {target_ac}"
    else:
        total = 10 + bonus
        hit = total >= target_ac
        crit = False
        die = None
        atk_txt = f"{skill} {bonus:+d} → {total} против КБ {target_ac}"
    if any(c.name == "оглушение" for c in attacker.conditions):
        hit = False
        msgs.append(f"{attacker.name} оглушён и промахивается.")
    text = f"⚔️ {attacker.name} → {target.name} ({wname}): {atk_txt} — "
    if not hit:
        text += "промах!" + (" (крит. провал)" if die == 1 else "")
        return {"hit": False, "text": text, "msgs": msgs, "damage": 0}
    dmg, dmg_s = roll_dice(scene, dmg_expr)
    if crit:
        dmg2, dmg_s2 = roll_dice(scene, dmg_expr)
        dmg += dmg2; dmg_s = f"крит {dmg_s}+{dmg_s2}"
    db = damage_bonus(attacker, w)
    if db:
        dmg = max(1, dmg + db); dmg_s += f"{db:+d}={dmg}"
    dmg, soak = absorb(target, dmg)
    old = target.hp
    target.hp = max(0, target.hp - dmg)
    text += f"попадание! Урон {dmg_s}{soak} → HP {old}→{target.hp}/{target.max_hp}"
    if crit:
        text += " (крит!)"
    text, more = knockout(scene, target, text)
    msgs += more
    return {"hit": True, "text": text, "msgs": msgs, "damage": dmg}


def knockout(scene: Scene, target: Character, text: str) -> tuple[str, list[str]]:
    msgs = []
    if target.hp == 0:
        text += f" — {target.name} без сознания!"
        apply_condition(target, "оглушение", turns=3)
        msgs.append(f"{target.name} выведен из строя.")
    # drop defeated from initiative if unconscious
    if target.hp == 0 and scene.combat.active:
        scene.combat.order = [o for o in scene.combat.order if o.id != target.id or target.is_player]
        if len([o for o in scene.combat.order if scene.characters[o.id].hp > 0 and not scene.characters[o.id].is_player]) == 0:
            msgs.append(end_combat(scene))
    return text, msgs


def find_ability(c: Character, name: str) -> Ability | None:
    n = M.norm(name)
    if not n:
        return None
    return (next((a for a in c.abilities if M.norm(a.name) == n), None)
            or next((a for a in c.abilities if n in M.norm(a.name) or M.norm(a.name) in n), None))


def ability_mp(a: Ability) -> int:
    if a.mp:
        return a.mp
    m = re.search(r"(\d+)\s*(mp|мп|ман)", (a.cost or "").lower())
    return int(m.group(1)) if m else 0


def resist(scene: Scene, caster: Character, a: Ability, target: Character) -> tuple[bool, str]:
    """Debuffs and control: caster's power vs the target's Воля."""
    power = M.amp(caster.attrs.get(a.scale or "Разум", 10))
    defense = 10 + M.amp(target.attrs.get("Воля", 10))
    if scene.rule_mode == "d20":
        die = M.rng(scene).randint(1, 20)
        ok = die == 20 or (die != 1 and die + power >= defense)
        return ok, f"1d20({die}){power:+d}={die + power} против Воли {defense}"
    return 10 + power >= defense, f"{a.scale or 'Разум'} {power:+d} против Воли {defense}"


def cast(scene: Scene, caster: Character, a: Ability, target: Character | None) -> dict:
    """Use an active ability: code spends MP, rolls its power and adds the attribute amplifier.
    Abilities without mechanics (from cards) only cost MP; the narrator describes their effect."""
    if a.kind != "active":
        return {"ok": False, "text": f"«{a.name}» — пассивная способность, она действует сама.", "msgs": [], "damage": 0}
    mp = ability_mp(a)
    if caster.mp < mp:
        return {"ok": False, "text": f"✨ {caster.name}: «{a.name}» — не хватает MP ({caster.mp}/{mp}). Не сработало.",
                "msgs": [], "damage": 0}
    if any(x.name == "оглушение" for x in caster.conditions):
        return {"ok": False, "text": f"✨ {caster.name} оглушён и не может применить «{a.name}».", "msgs": [], "damage": 0}
    caster.mp -= mp
    eff = a.effect or "utility"
    if eff in ("heal", "shield", "buff") and target is None:
        target = caster
    if eff in ("damage", "debuff", "control") and target is None:
        caster.mp += mp
        return {"ok": False, "text": f"✨ «{a.name}»: нужна цель.", "msgs": [], "damage": 0}
    amp = M.amp(caster.attrs.get(a.scale, 10)) if a.scale else 0
    head = f"✨ {caster.name}: «{a.name}»" + (f" → {target.name}" if target and target is not caster else "") + (f" (−{mp} MP)" if mp else "")
    msgs, dmg = [], 0
    if eff == "damage":
        if not scene.combat.active:
            start_combat(scene, [caster.id, target.id])
        bonus = amp + M.passive_bonus(caster, "spell" if a.scale in ("Разум", "Воля", "Влияние") else
                                      ("ranged" if a.scale == "Ловкость" else "melee"))
        roll, roll_s = roll_dice(scene, a.power or "1d6")
        dmg = max(1, roll + bonus)
        dmg, soak = absorb(target, dmg)
        old = target.hp; target.hp = max(0, target.hp - dmg)
        text = f"{head}: {roll_s}{bonus:+d} ({a.scale or '—'}) → урон {dmg}{soak}, HP {old}→{target.hp}/{target.max_hp}"
        text, more = knockout(scene, target, text); msgs += more
    elif eff == "heal":
        roll, roll_s = roll_dice(scene, a.power or "1d6")
        amount = max(1, roll + amp + M.passive_bonus(caster, "heal"))
        old = target.hp; target.hp = min(target.max_hp, target.hp + amount)
        text = f"{head}: {roll_s}{amp:+d} → +{target.hp - old} HP ({old}→{target.hp}/{target.max_hp})"
    elif eff == "shield":
        roll, roll_s = roll_dice(scene, a.power or "1d4")
        val = max(1, roll + amp)
        apply_condition(target, "щит", turns=3, potency=val)
        text = f"{head}: щит {roll_s}{amp:+d} = поглотит {val} урона (3 раунда)"
    elif eff == "buff":
        val = max(1, int(a.power or 1) + max(0, amp // 2))
        apply_condition(target, "усиление", turns=3, potency=val)
        text = f"{head}: усиление +{val} к атаке и урону (3 раунда)"
    elif eff in ("debuff", "control"):
        if not scene.combat.active and target.side == "hostile":
            start_combat(scene, [caster.id, target.id])
        ok, roll_s = resist(scene, caster, a, target)
        if not ok:
            text = f"{head}: {roll_s} — {target.name} сопротивляется."
        elif eff == "debuff":
            val = max(1, int(a.power or 1))
            apply_condition(target, "ослабление", turns=2, potency=val)
            text = f"{head}: {roll_s} — ослабление −{val} к атаке и урону (2 раунда)"
        else:
            turns = max(1, int(a.power or 1))
            apply_condition(target, "оглушение", turns=turns)
            text = f"{head}: {roll_s} — {target.name} оглушён ({turns} раунд.)"
    else:
        text = f"{head}: применено. Эффект описывает рассказчик."
    return {"ok": True, "text": text, "msgs": msgs, "damage": dmg}


def combat_status(scene: Scene) -> str:
    if not scene.combat.active:
        return "⟦ БОЙ ⟧ Сейчас боя нет. Рассказчик начнёт его тегом <attack> или <combat action=\"start\"/>."
    cb = scene.combat
    lines = [f"### ⟦ БОЙ · раунд {cb.round} ⟧"]
    for i, o in enumerate(cb.order):
        c = scene.characters[o.id]
        mark = "→ " if i == cb.current else "  "
        cond = (", ".join(x.name for x in c.conditions)) or "—"
        lines.append(f"{mark}{c.name}: HP {c.hp}/{c.max_hp}, иниц. {o.initiative}, сост. {cond}")
    return "\n".join(lines)
