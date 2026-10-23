"""Combat: initiative, attack/defense, damage dice, conditions. Pure code; narrator only announces intent."""
import re
from . import mechanics as M
from .models import Character, Combat, Combatant, Condition, Item, Scene

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
    if m:
        return int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
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
    return 10 + M.mod(c.attrs.get("Ловкость", 10)) + armor


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
    attr = "Ловкость" if weapon and any(k in weapon.name.lower() for k in ("лук", "арбалет", "пистол", "ружь", "винтов")) else "Сила"
    # Soft weight instead of hard mod + skill level
    return M.soft_weight(c.attrs.get(attr, 10), 10), attr


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
        die = M.rng(scene).randint(1, 20)
        init = die + M.soft_weight(c.attrs.get("Ловкость", 10), 10)
        order.append(Combatant(id=i, initiative=init))
    order.sort(key=lambda x: -x.initiative)
    scene.combat = Combat(active=True, round=1, order=order, current=0)
    lines = [f"⟦ БОЙ · раунд 1 ⟧ Инициатива: " + ", ".join(
        f"{scene.characters[o.id].name} {o.initiative}" for o in order)]
    cur = scene.characters[order[0].id]
    lines.append(f"Ход: {cur.name}." + (" (ты)" if cur.is_player else " Опиши его действие или дождись атаки."))
    return "\n".join(lines)


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
    old = target.hp
    target.hp = max(0, target.hp - dmg)
    text += f"попадание! Урон {dmg_s} → HP {old}→{target.hp}/{target.max_hp}"
    if crit:
        text += " (крит!)"
    if target.hp == 0:
        text += f" — {target.name} без сознания!"
        apply_condition(target, "оглушение", turns=3)
        msgs.append(f"{target.name} выведен из строя.")
    # drop defeated from initiative if unconscious
    if target.hp == 0 and scene.combat.active:
        scene.combat.order = [o for o in scene.combat.order if o.id != target.id or target.is_player]
        if len([o for o in scene.combat.order if scene.characters[o.id].hp > 0 and not scene.characters[o.id].is_player]) == 0:
            msgs.append(end_combat(scene))
    return {"hit": True, "text": text, "msgs": msgs, "damage": dmg}


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
