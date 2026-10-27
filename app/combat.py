"""Combat: initiative, attack/defense, damage dice, conditions. Pure code; narrator only announces intent."""
import re
from . import mechanics as M
from .models import Ability, Character, Combat, Combatant, Condition, Item, Scene

WEAPON_DEFAULTS = {  # name substring -> (damage, kind)
    "меч": ("1d8", "оружие"), "сабл": ("1d8", "оружие"), "кинжал": ("1d4", "оружие"), "нож": ("1d4", "оружие"),
    "топор": ("1d8", "оружие"), "булав": ("1d6", "оружие"), "дубин": ("1d6", "оружие"), "копь": ("1d6", "оружие"),
    "лук": ("1d8", "оружие"), "арбалет": ("1d8", "оружие"), "пистол": ("1d10", "оружие"), "кулак": ("1d3", "оружие"),
}
WEAPON_NAMES = {"меч": "Меч", "сабл": "Сабля", "кинжал": "Кинжал", "нож": "Нож", "топор": "Топор", "булав": "Булава",
                "дубин": "Дубина", "копь": "Копьё", "лук": "Лук", "арбалет": "Арбалет", "пистол": "Пистолет",
                "кулак": "Кулаки"}
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


def amount(scene: Scene, expr: str) -> tuple[int, str]:
    """Damage/healing/shield size: rolled in d20 mode, the fixed average in «Система» mode (1d8 -> 5)."""
    if scene.rule_mode != "система":
        return roll_dice(scene, expr)
    n, sides, bonus = parse_dice(expr)
    if sides == 0:
        return bonus, str(bonus)
    v = int(max(1, n) * (sides + 1) / 2 + 0.5) + bonus
    return v, str(v)


def armor_value(c: Character) -> int:
    return sum(i.armor for i in c.inventory if i.equipped and i.kind == "броня") + M.passive_bonus(c, "ac")


def hit_chance(attr_val: int, buff: int, target: Character) -> float:
    """«Система»: 65% between equals; ±5 percentage points per point of the attacking attribute over the target's
    Ловкость, −5 per point of armour, ±5 per point of buff/debuff."""
    return (0.65 + M.CHANCE_STEP * (attr_val - target.attrs.get("Ловкость", 10))
            - M.CHANCE_STEP * armor_value(target) + M.CHANCE_STEP * buff)


def scaled(scene: Scene, base: int, attr_val: int, extra: int) -> tuple[int, str]:
    """«Система»: the attribute multiplies the effect; passives and buffs add on top. d20: the old flat amplifier."""
    mult = M.power_mult(attr_val)
    v = max(1, round(base * mult) + extra)
    return v, f"{base}×{mult:g}".replace(".", ",") + (f"{extra:+d}" if extra else "") + f"={v}"


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


CONSUMABLES = [   # (name stems, effect) — first match wins
    (("противояд", "антидот"), "cure"),
    (("мана", "маны", "эссенци", "эфир"), "mana"),
    (("зелье силы", "эликсир силы", "стимулятор", "боевой эликсир", "отвар ярости"), "buff"),
    (("зель", "эликсир", "исцел", "целеб", "лечеб", "бинт", "бандаж", "аптеч", "припарк", "мазь", "снадоб"), "heal"),
    (("бомб", "гранат", "огненная склянк", "зажигат", "взрывча", "коктейль молотова"), "damage"),
]
POWER_WORDS = [(("мал", "слаб", "простой"), 1), (("больш", "сильн", "крепк"), 3), (("велик", "высш", "превосход", "легендарн"), 4)]


def classify_consumable(item: Item) -> Item:
    n = item.name.lower()
    for stems, eff in CONSUMABLES:
        if any(st in n for st in stems):
            item.effect = eff
            item.power = next((p for words, p in POWER_WORDS if any(w in n for w in words)), 2)
            if item.kind == "предмет":
                item.kind = "расходник"
            break
    return item


def ensure_item_stats(item: Item) -> Item:
    if not item.effect and not item.damage and not item.armor:
        classify_consumable(item)
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


# threat -> (level offset from the hero, base HP, HP per level, base attribute, natural damage, armour)
THREATS = {
    "слабый":  (-2, 6, 3, 9, "1d4", 0),
    "обычный": (0, 12, 4, 11, "1d6", 0),
    "опасный": (1, 18, 5, 13, "1d8", 1),
    "элита":   (2, 28, 7, 15, "1d10", 2),
    "босс":    (4, 45, 12, 17, "2d8", 3),
}
THREAT_ALIASES = {"слаб": "слабый", "weak": "слабый", "мелк": "слабый", "обыч": "обычный", "normal": "обычный",
                  "опасн": "опасный", "сильн": "опасный", "dangerous": "опасный", "элит": "элита", "elite": "элита",
                  "босс": "босс", "boss": "босс", "владык": "босс", "лорд": "босс"}
BEAST = ("зверь", "волк", "тварь", "чудовищ", "монстр", "паук", "крыс", "медвед", "пёс", "пес", "демон", "нежит", "зомби", "скелет")


def norm_threat(t: str) -> str:
    t = (t or "").lower().strip()
    return next((v for k, v in THREAT_ALIASES.items() if t.startswith(k)), "")


def is_blank(c: Character) -> bool:
    """A foe the narrator just made up: default sheet (all 10, 20 HP, level 1) and no threat assigned yet."""
    return not c.threat and c.level == 1 and c.max_hp == 20 and all(v == 10 for v in c.attrs.values())


def apply_threat(scene: Scene, c: Character, threat: str = "обычный") -> str:
    """Give a foe a sheet matching its threat and the hero's level: level, HP, attributes, a weapon or natural
    attack, armour. Only for blank sheets — characters the player or the Director built are left alone."""
    threat = norm_threat(threat) or "обычный"
    if not is_blank(c):
        c.threat = c.threat or threat
        return ""
    off, hp0, hp_l, base, nat, arm = THREATS[threat]
    lvl = max(1, scene.player.level + off)
    c.threat, c.level = threat, lvl
    c.max_hp = c.hp = hp0 + hp_l * lvl
    grow = lvl // 3
    beast = any(k in (c.kind + " " + c.name + " " + c.description).lower() for k in BEAST)
    for a in c.attrs:
        c.attrs[a] = base + grow + (1 if (beast and a in ("Сила", "Ловкость", "Выносливость")) else 0) \
            - (2 if (beast and a in ("Разум", "Влияние")) else 0)
    if not equipped_weapon(c):
        desc = (c.description + " " + c.kind).lower()
        named = next((k for k in WEAPON_DEFAULTS if k in desc), None)
        w = Item(name=WEAPON_NAMES[named] if named else ("Клыки и когти" if beast else "Оружие"), kind="оружие",
                 damage=WEAPON_DEFAULTS[named][0] if named else nat, equipped=True, slot="рука")
        c.inventory.append(w)
    if arm and not any(i.kind == "броня" and i.equipped for i in c.inventory):
        c.inventory.append(Item(name="Шкура" if beast else "Доспех", kind="броня", armor=arm, equipped=True, slot="тело"))
    return f"{c.name}: {threat}, ур. {lvl}, HP {c.max_hp}"


def mark_foe(scene: Scene, c: Character, threat: str = "") -> str:
    """Someone fights the party: mark hostile and, if the sheet is blank, give it real stats."""
    if M.in_party(scene, c):
        return ""
    c.side = "hostile"
    return apply_threat(scene, c, threat or c.threat or "обычный")


# ---------------- the honest turn order ----------------
def current(scene: Scene) -> Character | None:
    cb = scene.combat
    if not cb.active or not cb.order:
        return None
    cb.current = min(cb.current, len(cb.order) - 1)
    return scene.characters.get(cb.order[cb.current].id)


def check_end(scene: Scene) -> str:
    """The fight ends when no foe stands, or when the whole party is down."""
    cb = scene.combat
    if not cb.active:
        return ""
    foes = [o for o in cb.order if not M.in_party(scene, scene.characters[o.id]) and scene.characters[o.id].hp > 0]
    party = [o for o in cb.order if M.in_party(scene, scene.characters[o.id]) and scene.characters[o.id].hp > 0]
    if not foes:
        return end_combat(scene)
    if not party:
        end_combat(scene)
        return "⟦ БОЙ ОКОНЧЕН ⟧ Отряд пал."
    return ""


def pick_target(scene: Scene, foe: Character) -> Character | None:
    """Foes press the most hurt party member half the time, otherwise anyone standing."""
    alive = [scene.characters[o.id] for o in scene.combat.order
             if M.in_party(scene, scene.characters[o.id]) and scene.characters[o.id].hp > 0]
    if not alive:
        return None
    r = M.rng(scene)
    return min(alive, key=lambda c: c.hp / max(1, c.max_hp)) if r.random() < 0.5 else r.choice(alive)


def foe_turn(scene: Scene, foe: Character) -> dict:
    """A foe acts by code: a damaging ability if it has one and the MP (one time in three), else its weapon."""
    tgt = pick_target(scene, foe)
    if tgt is None:
        return {"text": f"{foe.name} не находит цели.", "msgs": [], "brief": f"{foe.name} не находит цели"}
    if any(x.name == "оглушение" for x in foe.conditions):
        return {"text": f"{foe.name} оглушён и пропускает ход.", "msgs": [], "brief": f"{foe.name} оглушён и пропускает ход"}
    spells = [a for a in foe.abilities if a.kind == "active" and a.effect == "damage" and foe.mp >= ability_mp(a)]
    if spells and M.rng(scene).random() < 0.34:
        r = cast(scene, foe, spells[0], tgt)
        how = f"применяет «{spells[0].name}» против {tgt.name}"
        hit = r.get("damage", 0) > 0
    elif can_react(scene, tgt):                 # a blow at the hero: it waits for the player's reaction
        return threaten(scene, foe, tgt)
    else:
        r = attack(scene, foe, tgt)
        how = f"атакует {tgt.name}"
        hit = r.get("hit")
    brief = f"{foe.name} {how} — " + (f"попадание, урон {r.get('damage', 0)}" if hit else "промах")
    if tgt.hp == 0:
        brief += f", {tgt.name} падает без сознания"
    return {"text": r["text"], "msgs": r["msgs"], "brief": brief}


def step(scene: Scene) -> Character | None:
    """Move to the next living combatant (unconscious ones are skipped); a new round ticks conditions."""
    cb = scene.combat
    if not cb.active or not cb.order:
        return None
    for _ in range(len(cb.order) + 1):
        cb.current += 1
        if cb.current >= len(cb.order):
            cb.current = 0; cb.round += 1
            tick_conditions(scene)
        c = scene.characters.get(cb.order[cb.current].id)
        if c and c.hp > 0:
            return c
    return None


def run_foes(scene: Scene) -> list[str]:
    """After a party member acted: every foe whose turn comes acts, until it is the hero's or a companion's turn
    (then the companion waits for the hero's advice). Returns [{text: shown to the player, brief: for the narrator}]."""
    out = []
    cb = scene.combat
    if cb.threat:                                # a blow at the hero still waits for the reaction
        return out
    guard = 0
    while cb.active and guard < 12:
        guard += 1
        c = current(scene)
        if c is None:
            break
        if c.hp <= 0:
            step(scene); continue
        if M.in_party(scene, c):
            cb.awaiting = "" if c.is_player else c.id
            break
        r = foe_turn(scene, c)
        out.append({"text": r["text"] + ("\n" + "\n".join(r["msgs"]) if r["msgs"] else ""), "brief": r["brief"]})
        if r.get("pause"):
            break
        end = check_end(scene)
        if end:
            out.append({"text": end, "brief": "бой окончен"}); break
        step(scene)
    return out


def start_combat(scene: Scene, ids: list[str], first: str = "") -> str:
    """`first`: whoever struck to start the fight acts first; after that the initiative order runs."""
    ids = [i for i in ids if i in scene.characters and (scene.characters[i].hp > 0 or i == scene.player_id)]
    if scene.player_id not in ids:
        ids = [scene.player_id] + ids
    for cid in scene.party:                      # the whole party fights, not only who started it
        if cid in scene.characters and scene.characters[cid].hp > 0 and cid not in ids:
            ids.append(cid)
    for cid in ids:
        c = scene.characters[cid]
        if not M.in_party(scene, c):
            mark_foe(scene, c)
    for i in ids:
        if i not in scene.present:
            scene.present.append(i)
    order = []
    for i in ids:
        c = scene.characters[i]
        order.append(Combatant(id=i, initiative=initiative(scene, c)))
    order.sort(key=lambda x: -x.initiative)
    scene.combat = Combat(active=True, round=1, order=order, current=0)
    if first:
        scene.combat.current = next((k for k, o in enumerate(order) if o.id == first), 0)
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
        start_combat(scene, [attacker.id, target.id], first=attacker.id)
    elif not any(o.id == target.id for o in scene.combat.order):
        join_combat(scene, [target.id])
    w = None
    if weapon_name:
        w = next((i for i in attacker.inventory if weapon_name.lower() in i.name.lower()), None)
        if w:
            ensure_item_stats(w)
        elif any(k in weapon_name.lower() for k in WEAPON_DEFAULTS):
            # the narrator says «обнажает короткий меч» but the card has no such item: use the weapon anyway
            w = ensure_item_stats(Item(name=weapon_name.strip()[:40]))
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
        buff = cond_potency(attacker, "усиление") - cond_potency(attacker, "ослабление")
        hit, pct = M.chance(scene, hit_chance(attacker.attrs.get(skill, 10), buff, target))
        crit = False
        die = None
        arm = armor_value(target)
        atk_txt = (f"{skill} {attacker.attrs.get(skill, 10)} против Ловкости {target.attrs.get('Ловкость', 10)}"
                   + (f", броня {arm}" if arm else "") + f" — шанс попасть {pct}%")
    if any(c.name == "оглушение" for c in attacker.conditions):
        hit = False
        msgs.append(f"{attacker.name} оглушён и промахивается.")
    text = f"⚔️ {attacker.name} → {target.name} ({wname}): {atk_txt} — "
    if not hit:
        text += "промах!" + (" (крит. провал)" if die == 1 else "")
        return {"hit": False, "text": text, "msgs": msgs, "damage": 0}
    if scene.rule_mode == "система":
        dmg, dmg_s = weapon_damage(scene, attacker, w, skill)
    else:
        dmg, dmg_s = amount(scene, dmg_expr)
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
        cb = scene.combat
        cur = cb.order[cb.current].id if cb.order and cb.current < len(cb.order) else None
        cb.order = [o for o in cb.order if o.id != target.id or M.in_party(scene, target)]
        cb.current = next((k for k, o in enumerate(cb.order) if o.id == cur), min(cb.current, max(0, len(cb.order) - 1)))
        end = check_end(scene)
        if end:
            msgs.append(end)
    return text, msgs


def weapon_damage(scene: Scene, attacker: Character, w: Item | None, skill: str) -> tuple[int, str]:
    """«Система»: the weapon's fixed average, multiplied by the attacking attribute; passives and buffs on top."""
    base, _ = amount(scene, w.damage if w and w.damage else "1d4")
    extra = (M.passive_bonus(attacker, "ranged" if is_ranged(w) else "melee")
             + cond_potency(attacker, "усиление") - cond_potency(attacker, "ослабление"))
    return scaled(scene, base, attacker.attrs.get(skill, 10), extra)


# ---------------- active defence («Система»): a blow at the hero waits for the player's reaction ----------------
REACTIONS = {   # key -> (button label, what the button sends)
    "dodge": ("Уклониться", "*уклоняюсь от удара*"),
    "block": ("Блок", "*блокирую удар*"),
    "parry": ("Парировать", "*парирую и контратакую*"),
    "take": ("Принять на броню", "*принимаю удар на броню*"),
}
REACT_WORDS = {
    "dodge": ("уклон", "увор", "отскак", "отскоч", "пригиба", "откатыва", "отпрыг", "ухожу от", "уйти от", "отступаю"),
    "block": ("блок", "щит", "закрыва", "прикрыва", "подставляю"),
    "parry": ("парир", "контрат", "отбива", "отбить", "встречн", "отвожу"),
    "take": ("принима", "терплю", "стою", "броню"),
}
REACT_TIRE = 0.10        # the same trick twice in a row: foes read it, −10 points per repeat


def can_react(scene: Scene, tgt: Character) -> bool:
    return (scene.rule_mode == "система" and tgt.is_player and tgt.hp > 0
            and not any(x.name == "оглушение" for x in tgt.conditions))


def threaten(scene: Scene, foe: Character, tgt: Character) -> dict:
    """The foe swings at the hero; the blow lands only after the player's reaction."""
    scene.combat.threat = foe.id
    w = equipped_weapon(foe)
    wname = w.name if w else "безоружный удар"
    opts = " · ".join(f"{REACTIONS[k][0]} {p}%" for k, p in reaction_odds(scene).items() if p is not None)
    return {"text": f"⚠️ {foe.name} бросается на {tgt.name} ({wname}) — удар ещё не нанесён. Реакция: {opts}",
            "msgs": [], "pause": True,
            "brief": f"{foe.name} замахивается на {tgt.name} — удар ещё не нанесён, игрок выбирает реакцию"}


def leave_combat(scene: Scene, c: Character) -> str:
    """Someone flees or surrenders: out of the order (the turn pointer stays on whoever had it)."""
    cb = scene.combat
    if not cb.active or not any(o.id == c.id for o in cb.order):
        return ""
    cur = cb.order[cb.current].id if cb.order and cb.current < len(cb.order) else None
    cb.order = [o for o in cb.order if o.id != c.id]
    if cb.threat == c.id:
        cb.threat = ""
    if cur == c.id:
        cb.current -= 1          # the next step() lands on whoever stood after them
        step(scene)
    else:
        cb.current = next((k for k, o in enumerate(cb.order) if o.id == cur), 0)
    return check_end(scene)


def parse_reaction(text: str) -> str:
    """The first reaction named in the player's words; nothing recognisable means the blow is taken on the armour."""
    t = (text or "").lower()
    hits = [(t.find(w), k) for k, ws in REACT_WORDS.items() for w in ws if w in t]
    return min(hits)[1] if hits else "take"


def _foe_attack(foe: Character) -> tuple[Item | None, str, int]:
    w = equipped_weapon(foe)
    if w:
        ensure_item_stats(w)
    skill = "Ловкость" if is_ranged(w) else "Сила"
    return w, skill, foe.attrs.get(skill, 10) + cond_potency(foe, "усиление") - cond_potency(foe, "ослабление")


def reaction_odds(scene: Scene) -> dict:
    """Success chance (percent, 5–95) of each reaction to the pending blow; None when it is impossible.
    Уклониться: Ловкость vs the blow, heavy armour hinders, arrows are harder. Блок: Сила vs the blow, a shield helps;
    halves the damage. Парировать: the weapon's attribute vs the blow, hard; no damage and a counterattack, but a
    failure opens you up (×1,5). Принять на броню: the plain chance the blow misses; armour soaks the damage."""
    cb = scene.combat
    foe, hero = scene.characters.get(cb.threat), scene.player
    if foe is None:
        return {}
    w, skill, a = _foe_attack(foe)
    ranged = is_ranged(w)
    arm = armor_value(hero)
    hw = equipped_weapon(hero)
    shield = any(i.equipped and "щит" in i.name.lower() for i in hero.inventory)
    at = lambda k: hero.attrs.get(k, 10)
    tire = lambda k: REACT_TIRE * cb.react_streak if cb.react_last == k and k != "take" else 0
    raw = {
        "dodge": 0.5 + M.CHANCE_STEP * (at("Ловкость") - a) - M.CHANCE_STEP * max(0, arm - 2) - (0.1 if ranged else 0),
        "block": 0.6 + M.CHANCE_STEP * (at("Сила") - a) + (0.15 if shield else 0) - (0.1 if ranged and not shield else 0),
        "parry": (None if ranged or not hw or is_ranged(hw) else
                  0.35 + M.CHANCE_STEP * (at("Сила") - a)),
        "take": 1 - (0.65 + M.CHANCE_STEP * (a - at("Ловкость")) - M.CHANCE_STEP * arm),
    }
    return {k: (None if p is None else round(100 * max(0.05, min(0.95, p - tire(k))))) for k, p in raw.items()}


def threat_info(scene: Scene) -> dict | None:
    """For the client: who strikes, with what, and the reactions with their chances."""
    foe = scene.characters.get(scene.combat.threat) if scene.combat.active else None
    if foe is None:
        return None
    w = equipped_weapon(foe)
    odds = reaction_odds(scene)
    return {"foe": foe.name, "weapon": w.name if w else "безоружный удар",
            "options": [{"key": k, "label": REACTIONS[k][0], "text": REACTIONS[k][1], "pct": p}
                        for k, p in odds.items() if p is not None]}


def react(scene: Scene, choice: str) -> dict:
    """Resolve the pending blow with the hero's reaction. Returns {text, msgs, brief, counter} — `counter` means a
    successful parry: the hero strikes back at once (the engine resolves it, it does not take the hero's turn)."""
    cb = scene.combat
    foe, hero = scene.characters.get(cb.threat), scene.player
    odds = reaction_odds(scene)
    cb.threat = ""
    if foe is None or foe.hp <= 0 or hero.hp <= 0 or not cb.active:
        end = check_end(scene)
        return {"text": "Угроза миновала.", "msgs": [end] if end else [], "brief": "угроза миновала", "counter": False}
    if odds.get(choice) is None:
        choice = "take"
    pct = odds[choice]
    tired = cb.react_last == choice != "take" and cb.react_streak > 0
    cb.react_streak = cb.react_streak + 1 if cb.react_last == choice else 1
    cb.react_last = choice
    ok = M.rng(scene).random() * 100 < pct
    w, skill, a = _foe_attack(foe)
    wname = w.name if w else "безоружный удар"
    label = REACTIONS[choice][0].lower()
    head = (f"🛡 {hero.name}: {label} — {foe.name} ({wname}), шанс {pct}%"
            + (" (враг ждёт этого приёма)" if tired else "") + " — ")
    if ok:
        how = {"dodge": "удар проходит мимо", "parry": "клинок отведён — контратака!",
               "take": "удар скользит по броне", "block": None}[choice]
        if how:
            brief = {"dodge": f"{hero.name} уклоняется от удара {foe.name}",
                     "parry": f"{hero.name} парирует удар {foe.name} и контратакует",
                     "take": f"удар {foe.name} не пробивает защиту {hero.name}"}[choice]
            how = how[0].upper() + how[1:] + ("" if how.endswith("!") else ".")
            return {"text": head + f"успех! {how}", "msgs": [], "brief": brief, "counter": choice == "parry"}
    dmg, dmg_s = weapon_damage(scene, foe, w, skill)
    if choice == "block" and ok:
        dmg = max(1, dmg // 2); dmg_s += f", блок ½={dmg}"
        verdict, brief = "успех! Удар принят на блок.", f"{hero.name} блокирует удар {foe.name}, урон снижен до {dmg}"
    elif choice == "parry":
        dmg = round(dmg * 1.5); dmg_s += f", раскрылся ×1,5={dmg}"
        verdict, brief = "провал!", f"{hero.name} пытается парировать, но раскрывается — {foe.name} попадает, урон {dmg}"
    elif choice == "take":
        arm = armor_value(hero)
        if arm:
            dmg = max(1, dmg - arm); dmg_s += f" − броня {arm}={dmg}"
        verdict, brief = "провал!", f"{foe.name} попадает по {hero.name}, броня смягчает, урон {dmg}"
    else:
        verdict = "провал!"
        brief = f"{hero.name} пытается {'уклониться' if choice == 'dodge' else 'блокировать'}, но {foe.name} попадает, урон {dmg}"
    dmg, soak = absorb(hero, dmg)
    old = hero.hp
    hero.hp = max(0, hero.hp - dmg)
    text = head + f"{verdict} Урон {dmg_s}{soak} → HP {old}→{hero.hp}/{hero.max_hp}"
    text, msgs = knockout(scene, hero, text)
    if hero.hp == 0:
        brief += f", {hero.name} падает без сознания"
    return {"text": text, "msgs": msgs, "brief": brief, "counter": False}


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
    if scene.rule_mode == "система":
        cv, tv = caster.attrs.get(a.scale or "Разум", 10), target.attrs.get("Воля", 10)
        ok, pct = M.chance(scene, 0.5 + M.CHANCE_STEP * (cv - tv))
        return ok, f"{a.scale or 'Разум'} {cv} против Воли {tv} — шанс {pct}%"
    power = M.amp(caster.attrs.get(a.scale or "Разум", 10))
    defense = 10 + M.amp(target.attrs.get("Воля", 10))
    if scene.rule_mode == "d20":
        die = M.rng(scene).randint(1, 20)
        ok = die == 20 or (die != 1 and die + power >= defense)
        return ok, f"1d20({die}){power:+d}={die + power} против Воли {defense}"


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
            start_combat(scene, [caster.id, target.id], first=caster.id)
        bonus = amp + M.passive_bonus(caster, "spell" if a.scale in ("Разум", "Воля", "Влияние") else
                                      ("ranged" if a.scale == "Ловкость" else "melee"))
        roll, roll_s = amount(scene, a.power or "1d6")
        if scene.rule_mode == "система":
            dmg, roll_s = scaled(scene, roll, caster.attrs.get(a.scale, 10) if a.scale else 10, bonus - amp)
            bonus_s = ""
        else:
            dmg = max(1, roll + bonus); bonus_s = f"{bonus:+d}"
        dmg, soak = absorb(target, dmg)
        old = target.hp; target.hp = max(0, target.hp - dmg)
        text = f"{head}: {roll_s}{bonus_s} ({a.scale or '—'}) → урон {dmg}{soak}, HP {old}→{target.hp}/{target.max_hp}"
        text, more = knockout(scene, target, text); msgs += more
    elif eff == "heal":
        roll, roll_s = amount(scene, a.power or "1d6")
        if scene.rule_mode == "система":
            healed, roll_s = scaled(scene, roll, caster.attrs.get(a.scale, 10) if a.scale else 10, M.passive_bonus(caster, "heal"))
        else:
            healed = max(1, roll + amp + M.passive_bonus(caster, "heal")); roll_s += f"{amp:+d}"
        old = target.hp; target.hp = min(target.max_hp, target.hp + healed)
        text = f"{head}: {roll_s} → +{target.hp - old} HP ({old}→{target.hp}/{target.max_hp})"
    elif eff == "shield":
        roll, roll_s = amount(scene, a.power or "1d4")
        if scene.rule_mode == "система":
            val, roll_s = scaled(scene, roll, caster.attrs.get(a.scale, 10) if a.scale else 10, 0)
        else:
            val = max(1, roll + amp); roll_s += f"{amp:+d}"
        apply_condition(target, "щит", turns=3, potency=val)
        text = f"{head}: щит {roll_s} — поглотит {val} урона (3 раунда)"
    elif eff == "buff":
        val = max(1, int(a.power or 1) + max(0, amp // 2))
        apply_condition(target, "усиление", turns=3, potency=val)
        text = f"{head}: усиление +{val} к атаке и урону (3 раунда)"
    elif eff in ("debuff", "control"):
        if not scene.combat.active and target.side == "hostile":
            start_combat(scene, [caster.id, target.id], first=caster.id)
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
