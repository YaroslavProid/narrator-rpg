"""Combat: initiative, honest turn order, attacks, abilities, free-form deeds, active defence, conditions.
Pure code; the narrator only announces intent. Every number comes from balance.py: damage is a plain number
amplified by the attribute, armour takes a flat amount off physical blows."""
import re
from . import balance as B, mechanics as M
from .models import Ability, Character, Combat, Combatant, Condition, Item, Scene


# ---------------- gear ----------------
def _stem(name: str, table: dict) -> str | None:
    n = (name or "").lower()
    if B.NOT_GEAR.search(n):
        return None
    return next((k for k in sorted(table, key=len, reverse=True) if B.stem_at_word_start(n, k)), None)


def weapon_stem(name: str) -> str | None:
    return _stem(name, B.WEAPONS)


def is_ranged(weapon: Item | None) -> bool:
    if not weapon:
        return False
    if weapon.ranged:
        return True
    k = weapon_stem(weapon.name)
    return bool(k and B.WEAPONS[k][2])


def weapon_damage_base(weapon: Item | None) -> int:
    if not weapon:
        return B.UNARMED
    return B.to_number(weapon.damage) or B.UNARMED


CONSUMABLES = [   # (name stems at a word start, effect) — first match wins
    (("противояд", "антидот"), "cure"),
    (("зелье маны", "эликсир маны", "мана", "маны", "эссенци", "эфир"), "mana"),
    (("зелье силы", "эликсир силы", "стимулятор", "боевой эликсир", "отвар ярости"), "buff"),
    (("бомб", "гранат", "огненная склянк", "зажигат", "взрывча", "коктейль молотова"), "damage"),
    (("зель", "эликсир", "исцел", "целеб", "лечеб", "бинт", "бандаж", "аптеч", "припарк", "мазь", "снадоб", "аптечк",
      "склянк", "микстур", "тоник", "настойк", "бальзам", "отвар", "лекарств", "пилюл", "таблетк", "стимпак"), "heal"),
]
POWER_WORDS = [(("мал", "слаб", "простой"), 1), (("больш", "сильн", "крепк"), 3), (("велик", "высш", "превосход"), 4)]


def classify_consumable(item: Item) -> Item:
    n = item.name.lower()
    for stems, eff in CONSUMABLES:
        if eff == "heal" and re.search(r"(?<![а-яё])(яд|отрав|кислот)", n):
            continue                         # «склянка с ядом» is not a medicine
        if any(B.stem_at_word_start(n, st) for st in stems):
            item.effect = eff
            item.power = next((p for words, p in POWER_WORDS if any(B.stem_at_word_start(n, w) for w in words)), 2)
            if item.kind == "предмет":
                item.kind = "расходник"
            break
    return item


def ensure_item_stats(item: Item, scene: Scene | None = None) -> Item:
    """Weapons get a damage number, armour an armour value and a slot, consumables an effect — from the name.
    A legacy dice string («1d8») becomes a number. Things that only look like gear («ножны», «кожаная сумка») stay items."""
    if item.damage and not re.fullmatch(r"\d+", item.damage.strip()):
        item.damage = str(B.to_number(item.damage) or "")
    if not item.effect and not item.damage and not item.armor:
        classify_consumable(item)
    if item.effect:
        return item
    from . import rules as R
    rw = R.weapon(scene, item.name)
    if rw and not item.damage and not item.armor:
        item.damage, item.kind, item.ranged = str(rw.damage), "оружие", rw.ranged
    w = weapon_stem(item.name)
    if w and not item.damage and not item.armor:
        item.damage, item.kind, item.ranged = str(B.WEAPONS[w][1]), "оружие", B.WEAPONS[w][2]
    a = _stem(item.name, B.ARMOR)
    if a and not item.armor and not item.damage:
        item.armor, item.kind, item.slot = B.ARMOR[a][0], "броня", B.ARMOR[a][1]
    if item.kind == "оружие" and not item.slot:
        item.slot = "рука"
    if item.kind == "броня" and not item.slot:
        item.slot = "тело"
    return item


def equipped_weapon(c: Character) -> Item | None:
    w = next((i for i in c.inventory if i.equipped and i.kind == "оружие"), None)
    if w:
        return w
    return next((i for i in c.inventory if i.kind == "оружие"), None)


def armor_value(c: Character) -> int:
    return sum(i.armor for i in c.inventory if i.equipped and i.kind == "броня") + M.passive_bonus(c, "ac")


def ac(c: Character) -> int:
    """d20 only: armour class from agility (armour itself takes damage off instead)."""
    return 10 + M.mod(c.attrs.get("Ловкость", 10))


def cond_potency(c: Character, name: str) -> int:
    return max((x.potency for x in c.conditions if x.name == name), default=0)


def buff_sign(c: Character) -> int:
    return (1 if cond_potency(c, "усиление") else 0) - (1 if cond_potency(c, "ослабление") else 0)


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
    return dmg - soak, f", щит −{soak}"


def _num(v: float) -> str:
    return f"{v:g}".replace(".", ",")


def spread(scene: Scene, dmg: int, crit: bool = False) -> tuple[int, str]:
    """d20: ±20% around the number and ×2 on a natural 20. «Система»: the number is exact."""
    if scene.rule_mode == "система":
        return dmg, ""
    f = 1 + M.rng(scene).uniform(-B.D20_SPREAD, B.D20_SPREAD)
    v = max(1, round(dmg * f * (2 if crit else 1)))
    return v, (f" ×{_num(round(f, 2))}" + (" ×2 крит" if crit else "") + f"={v}")


def _mults(attr: str, attr_val: int, level: int) -> tuple[float, str]:
    m, lv = B.mult(attr_val), B.level_mult(level)
    return m * lv, (f"×{_num(m)} ({attr})" if m != 1 else "") + (f"×{_num(round(lv, 2))} (ур. {level})" if lv != 1 else "")


def physical(scene: Scene, attacker: Character, weapon: Item | None, base: int | None = None, crit: bool = False) -> tuple[int, str]:
    """Weapon damage: the weapon's number × the attribute (Сила, Ловкость for ranged) × the level + passives + buff."""
    ranged = is_ranged(weapon)
    attr = "Ловкость" if ranged else "Сила"
    base = weapon_damage_base(weapon) if base is None else base
    m, ms = _mults(attr, attacker.attrs.get(attr, 10), attacker.level)
    extra = (M.passive_bonus(attacker, "ranged" if ranged else "melee")
             + cond_potency(attacker, "усиление") - cond_potency(attacker, "ослабление"))
    v = max(1, round(base * m) + extra)
    text = f"{base}{ms}" + (f"{extra:+d}" if extra else "")
    v, s = spread(scene, v, crit)
    return v, text + (f"={v}" if not s and (m != 1 or extra) else s)


def magical(scene: Scene, caster: Character, a: Ability, bonus_key: str, levelled: bool = True) -> tuple[int, str]:
    """An ability's number × its attribute × the caster's level (damage, healing, shields) + the matching passive."""
    base = B.to_number(a.power) or 1
    m, ms = _mults(a.scale, caster.attrs.get(a.scale, 10) if a.scale else 10, caster.level if levelled else 1)
    extra = M.passive_bonus(caster, bonus_key) if bonus_key else 0
    v = max(1, round(base * m) + extra)
    text = f"{base}{ms}" + (f"{extra:+d}" if extra else "")
    v, s = spread(scene, v)
    return v, text + (f"={v}" if not s and (m != 1 or extra) else s)


def hurt(scene: Scene, target: Character, dmg: int, physical_blow: bool) -> tuple[int, str]:
    """Armour (physical only) and a shield take their part; returns the damage that lands and a note."""
    note = ""
    if physical_blow:
        arm = armor_value(target)
        if arm:
            dmg = max(1, dmg - arm); note += f", броня −{arm}"
    dmg, soak = absorb(target, dmg)
    old = target.hp
    target.hp = max(0, target.hp - dmg)
    return dmg, f"{note}{soak} → урон {dmg}, HP {old}→{target.hp}/{target.max_hp}"


# ---------------- foes ----------------
THREATS = B.THREATS
THREAT_ALIASES = {"слаб": "слабый", "weak": "слабый", "мелк": "слабый", "обыч": "обычный", "normal": "обычный",
                  "опасн": "опасный", "сильн": "опасный", "dangerous": "опасный", "элит": "элита", "elite": "элита",
                  "босс": "босс", "boss": "босс", "владык": "босс", "лорд": "босс"}
BEAST = ("зверь", "волк", "тварь", "чудовищ", "монстр", "паук", "крыс", "медвед", "пёс", "пес", "демон", "нежит", "зомби", "скелет")


def norm_threat(t: str) -> str:
    t = (t or "").lower().strip()
    return next((v for k, v in THREAT_ALIASES.items() if t.startswith(k)), "")


def is_blank(c: Character) -> bool:
    """A foe the narrator just made up: default sheet (all 10, level 1) and no threat assigned yet."""
    return not c.threat and c.level == 1 and all(v == 10 for v in c.attrs.values())


def apply_threat(scene: Scene, c: Character, threat: str = "обычный") -> str:
    """Give a foe a sheet matching its threat and the hero's level: level, attributes, a weapon or natural
    attack, armour; HP follows from the threat (mechanics.sync_derived). Only for blank sheets — characters the
    player or the Director built keep their attributes."""
    threat = norm_threat(threat) or "обычный"
    if not is_blank(c):
        c.threat = c.threat or threat
        M.sync_derived(c)
        return ""
    off, _, base, dmg, arm = THREATS[threat]
    lvl = max(1, scene.player.level + off)
    c.threat, c.level = threat, lvl
    beast = any(k in (c.kind + " " + c.name + " " + c.description).lower() for k in BEAST)
    for a in c.attrs:
        c.attrs[a] = base + (1 if (beast and a in ("Сила", "Ловкость", "Выносливость")) else 0) \
            - (2 if (beast and a in ("Разум", "Влияние")) else 0)
    for i in c.inventory:
        ensure_item_stats(i)
    if not equipped_weapon(c):
        desc = (c.description + " " + c.kind).lower()
        named = next((k for k in B.WEAPONS if B.stem_at_word_start(desc, k)), None)
        w = Item(name=B.WEAPONS[named][0] if named else ("Клыки и когти" if beast else "Оружие"), kind="оружие",
                 damage=str(dmg), equipped=True, slot="рука")      # the threat sets the blow; the level grows it
        c.inventory.append(w)
    if arm and not any(i.kind == "броня" and i.equipped for i in c.inventory):
        c.inventory.append(Item(name="Шкура" if beast else "Доспех", kind="броня", armor=arm, equipped=True, slot="тело"))
    c.max_hp = 0
    M.sync_derived(c)
    c.hp = c.max_hp
    return f"{c.name}: {threat}, ур. {lvl}, HP {c.max_hp}"


THREAT_WEIGHT = {"слабый": 0.5, "обычный": 1, "опасный": 2, "элита": 3, "босс": 5}
THREAT_ORDER = ["слабый", "обычный", "опасный", "элита", "босс"]


def encounter_room(scene: Scene) -> float:
    """How much threat may still stand against the party (balance.ENCOUNTER_PER_MEMBER per party member)."""
    standing = sum(THREAT_WEIGHT.get(c.threat or "обычный", 1) for cid in scene.present
                   if (c := scene.characters.get(cid)) and c.side == "hostile" and c.hp > 0)
    return B.ENCOUNTER_PER_MEMBER * len(M.party_ids(scene)) - standing


def fit_threat(scene: Scene, threat: str) -> str:
    """The strongest threat not above the asked one that still fits the room; '' when even «слабый» does not."""
    t = norm_threat(threat) or "обычный"
    room = encounter_room(scene)
    for k in reversed(THREAT_ORDER[:THREAT_ORDER.index(t) + 1]):
        if THREAT_WEIGHT[k] <= room + 1e-9:
            return k
    return ""


def mark_foe(scene: Scene, c: Character, threat: str = "") -> str:
    """Someone fights the party: mark hostile and, if the sheet is blank, give it real stats."""
    if M.in_party(scene, c):
        return ""
    want = norm_threat(threat or c.threat) or "обычный"
    if c.side != "hostile" and is_blank(c) and want != "босс":
        want = fit_threat(scene, want) or "слабый"     # a new foe joins only as strong as the party can stand
    c.side = "hostile"
    return apply_threat(scene, c, want)


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
        for cid in M.party_ids(scene):
            c = scene.characters[cid]
            c.conditions = [x for x in c.conditions if x.name != "оглушение"]
            c.hp = max(c.hp, round(c.max_hp * B.DEFEAT_HP))
        return ("⟦ БОЙ ОКОНЧЕН ⟧ Отряд пал. Враги решают судьбу героя (плен, грабёж, уходят — добивать не обязательно); "
                f"опиши это. Герой приходит в себя с четвертью сил.")
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
    if consume_stun(foe):
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
    cb.awaiting = ""                  # a new turn: nobody waits for advice until run_foes stops on a companion
    for _ in range(len(cb.order) + 1):
        cb.current += 1
        if cb.current >= len(cb.order):
            cb.current = 0; cb.round += 1
            tick = tick_conditions(scene)
            cb = scene.combat         # a fall at the round's end may have finished the fight (a fresh Combat)
            if tick:
                cb.notes += tick.split("\n")
            if not cb.active or not cb.order:
                return None
        cb.current = min(cb.current, len(cb.order) - 1)
        c = scene.characters.get(cb.order[cb.current].id)
        if c and c.hp > 0:
            return c
    return None


def drain_notes(scene: Scene) -> list[dict]:
    """Round-end events step() collected (bleeding, falls, conditions wearing off), shown like a foe's action."""
    cb = scene.combat
    notes, cb.notes = [n for n in cb.notes if n], []
    return [{"text": "\n".join(notes), "brief": "; ".join(notes)}] if notes else []


def consume_stun(c: Character) -> bool:
    """A stun is spent on the stunned one's own turn, not per round: True when this turn is lost."""
    cond = next((x for x in c.conditions if x.name == "оглушение"), None)
    if cond is None:
        return False
    cond.turns_left -= 1
    if cond.turns_left <= 0:
        c.conditions.remove(cond)
    return True


def _waits_turn(scene: Scene, c: Character) -> bool:
    """A living combatant's stun waits for their own turn instead of ticking at the end of the round."""
    cb = scene.combat
    return cb.active and c.hp > 0 and any(o.id == c.id for o in cb.order)


def run_foes(scene: Scene) -> list[dict]:
    """After a party member acted: every foe whose turn comes acts, until it is the hero's or a companion's turn
    (then the companion waits for the hero's advice). Returns [{text: shown to the player, brief: for the narrator}]."""
    out = drain_notes(scene)
    if scene.combat.threat:                      # a blow at the hero still waits for the reaction
        return out
    guard = 0
    while scene.combat.active and guard < 12:
        cb = scene.combat
        guard += 1
        c = current(scene)
        if c is None:
            break
        if c.hp <= 0:
            step(scene); out += drain_notes(scene); continue
        if M.in_party(scene, c):
            if consume_stun(c):
                out.append({"text": f"{c.name} оглушён и пропускает ход.", "brief": f"{c.name} оглушён и пропускает ход"})
                step(scene); out += drain_notes(scene); continue
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
        out += drain_notes(scene)
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
    order = [Combatant(id=i, initiative=initiative(scene, scene.characters[i])) for i in ids]
    order.sort(key=lambda x: -x.initiative)
    scene.combat = Combat(active=True, round=1, order=order, current=0)
    if first:
        scene.combat.current = next((k for k, o in enumerate(order) if o.id == first), 0)
    lines = [f"⟦ БОЙ · раунд 1 ⟧ Инициатива: " + ", ".join(
        f"{scene.characters[o.id].name} {o.initiative}" for o in order)]
    cur = scene.characters[order[scene.combat.current].id]
    lines.append(f"Ход: {cur.name}." + (" (ты)" if cur.is_player else ""))
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


def clear_fallen(scene: Scene) -> list[str]:
    """Fallen foes are bodies in the background, no longer «present»."""
    gone = [cid for cid in scene.present if (c := scene.characters.get(cid)) and not c.is_player
            and c.side == "hostile" and c.hp <= 0 and cid not in scene.party]
    scene.present = [cid for cid in scene.present if cid not in gone]
    return gone


def end_combat(scene: Scene) -> str:
    clear_fallen(scene)
    for c in scene.characters.values():          # a fight's effects end with it (a downed ally is not stunned for later)
        c.conditions = [x for x in c.conditions if x.name not in ("оглушение", "щит", "усиление", "ослабление")]
    scene.combat = Combat()
    return "⟦ БОЙ ОКОНЧЕН ⟧"


def tick_conditions(scene: Scene) -> str:
    from . import rules as R
    msgs, fallen = [], []
    for c in scene.characters.values():
        keep = []
        for cond in c.conditions:
            if R.is_dot(scene, cond.name) and cond.potency > 0 and c.hp > 0:
                old = c.hp; c.hp = max(0, c.hp - cond.potency)
                msgs.append(f"{c.name}: {cond.name} −{cond.potency} HP ({old}→{c.hp})")
                if c.hp == 0:
                    fallen.append(c)
            if cond.name == "оглушение" and _waits_turn(scene, c):
                keep.append(cond)         # spent on their own turn (consume_stun)
                continue
            cond.turns_left -= 1
            if cond.turns_left > 0:
                keep.append(cond)
            else:
                msgs.append(f"{c.name}: состояние «{cond.name}» прошло")
        c.conditions = keep
    for c in fallen:                      # bled out: the same fall as from a blow (out of the order, end of the fight)
        msgs.append(f"{c.name} истекает кровью и падает без сознания.")
        _, more = knockout(scene, c, "")
        msgs += more
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


def _roll_hit(scene: Scene, attacker: Character, target: Character, attr: str) -> tuple[bool, bool, str]:
    """(hit, crit, text). «Система»: a chance from the attribute against the target's Ловкость. d20: d20 vs AC."""
    av = attacker.attrs.get(attr, 10)
    if scene.rule_mode == "система":
        hit, pct = M.chance(scene, B.hit_chance(av, target.attrs.get("Ловкость", 10), buff_sign(attacker)))
        return hit, False, f"{attr} {av} против Ловкости {target.attrs.get('Ловкость', 10)} — шанс {pct}%"
    bonus = M.soft_weight(av, 10) + 2 * buff_sign(attacker)
    die = M.rng(scene).randint(1, 20)
    total, tac = die + bonus, ac(target)
    return die == 20 or (die != 1 and total >= tac), die == 20, f"1d20({die}){bonus:+d}={total} против КБ {tac}"


def attack(scene: Scene, attacker: Character, target: Character, weapon_name: str = "") -> dict:
    """Resolve one weapon attack. Auto-starts combat if needed. Returns {hit, text, msgs, damage}."""
    if not scene.combat.active:
        start_combat(scene, [attacker.id, target.id], first=attacker.id)
    elif not any(o.id == target.id for o in scene.combat.order):
        join_combat(scene, [target.id])
    w = None
    if weapon_name:
        w = next((i for i in attacker.inventory if weapon_name.lower() in i.name.lower()), None)
        if w is None and weapon_stem(weapon_name):
            # the narrator says «обнажает короткий меч» but the card has no such item: use the weapon anyway
            w = Item(name=weapon_name.strip()[:40])
    w = w or equipped_weapon(attacker)
    if w:
        ensure_item_stats(w)
    attr = "Ловкость" if is_ranged(w) else "Сила"
    wname = w.name if w else "безоружный удар"
    msgs = []
    hit, crit, atk_txt = _roll_hit(scene, attacker, target, attr)
    if any(c.name == "оглушение" for c in attacker.conditions):
        hit = False
        msgs.append(f"{attacker.name} оглушён и промахивается.")
    text = f"⚔️ {attacker.name} → {target.name} ({wname}): {atk_txt} — "
    if not hit:
        return {"hit": False, "text": text + "промах!", "msgs": msgs, "damage": 0}
    dmg, dmg_s = physical(scene, attacker, w, crit=crit)
    dmg, land = hurt(scene, target, dmg, True)
    text += f"попадание! {dmg_s}{land}"
    text, more = knockout(scene, target, text)
    return {"hit": True, "text": text, "msgs": msgs + more, "damage": dmg}


def knockout(scene: Scene, target: Character, text: str) -> tuple[str, list[str]]:
    msgs = []
    if target.hp == 0:
        text += f" — {target.name} без сознания!"
        if not M.in_party(scene, target):
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


# ---------------- active defence («Система»): a blow at the hero waits for the player's reaction ----------------
REACTIONS = {   # key -> (button label, what the button sends)
    "dodge": ("Уклониться", "*уклоняюсь от удара*"),
    "block": ("Блок", "*блокирую удар*"),
    "parry": ("Парировать", "*парирую и контратакую*"),
    "take": ("Принять на броню", "*принимаю удар на броню*"),
}
REACTION_TEXTS = {t for _, t in REACTIONS.values()}
REACT_WORDS = {
    "dodge": ("уклон", "увор", "отскак", "отскоч", "пригиба", "откатыва", "отпрыг", "ухожу от", "уйти от", "отступаю",
              "бегу", "бежа", "убега", "прыга", "прыгн", "уворач", "увёрт", "уверт", "увернус", "кувырк", "ныря", "нырн",
              "перекат", "отшатыв", "шаг в сторон", "в сторону", "отхожу", "падаю на"),
    "block": ("блок", "щит", "закрыва", "прикрыва", "подставляю", "защищ"),
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
    scene.combat.awaiting = ""            # the hero's reaction is next, not a companion's advice
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


STRIKE_WORDS = ("руб", "бью", "бьём", "удар", "колю", "режу", "стреля", "мета", "кида", "швыря", "атаку", "выпад",
                "вонза", "пронза", "пинаю", "толка")


def weapon_named(c: Character, text: str) -> str:
    """The weapon the player named («ножом», «из арбалета»), by the stem of its first word; '' if none."""
    t = (text or "").lower()
    for i in c.inventory:
        if i.kind == "оружие":
            for w in re.findall(r"[а-яё]{3,}", i.name.lower()):
                if re.search(r"(?<![а-яё])" + w[:4], t):
                    return i.name
    return ""


BLOW_WORDS = STRIKE_WORDS + ("атак", "напада", "нападу", "добива", "добью", "стрел", "выстрел", "дерусь", "бьюсь",
                             "замахива", "бросаюсь на", "кидаюсь на", "режу", "колю", "душу", "толкаю", "сбиваю")


def wants_blow(text: str) -> bool:
    """The player's own words ask the hero to hit someone (a blow, a shot, a throw)."""
    t = (text or "").lower()
    return any(w in t for w in BLOW_WORDS)


def strikes_instead(text: str) -> bool:
    """The player attacks instead of defending: no defensive word, but a blow."""
    t = (text or "").lower()
    return not any(w in t for ws in REACT_WORDS.values() for w in ws) and any(w in t for w in STRIKE_WORDS)


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
    return w, skill, foe.attrs.get(skill, 10)


def reaction_odds(scene: Scene) -> dict:
    """Success chance (percent, 5–95) of each reaction to the pending blow; None when it is impossible.
    Уклониться: Ловкость vs the blow, heavy armour hinders, arrows are harder. Блок: Сила vs the blow, a shield helps;
    halves the damage. Парировать: the weapon's attribute vs the blow, hard; no damage and a counterattack, but a
    failure opens you up (×1,5). Принять на броню: the plain chance the blow misses; armour takes its part as always."""
    cb = scene.combat
    foe, hero = scene.characters.get(cb.threat), scene.player
    if foe is None:
        return {}
    w, skill, a = _foe_attack(foe)
    a += 2 * buff_sign(foe)
    ranged = is_ranged(w)
    arm = armor_value(hero)
    hw = equipped_weapon(hero)
    shield = any(i.equipped and i.slot == "щит" for i in hero.inventory)
    at = lambda k: hero.attrs.get(k, 10)
    tire = lambda k: REACT_TIRE * cb.react_streak if cb.react_last == k and k != "take" else 0
    raw = {
        "dodge": 0.5 + B.STEP * (at("Ловкость") - a) - B.STEP * max(0, arm - 2) - (0.1 if ranged else 0),
        "block": 0.6 + B.STEP * (at("Сила") - a) + (0.15 if shield else 0) - (0.1 if ranged and not shield else 0),
        "parry": (None if ranged or not hw or is_ranged(hw) else 0.35 + B.STEP * (at("Сила") - a)),
        "take": 1 - B.hit_chance(a, at("Ловкость")),
    }
    return {k: (None if p is None else round(100 * B.clamp_chance(p - tire(k)))) for k, p in raw.items()}


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
    w, _, _ = _foe_attack(foe)
    wname = w.name if w else "безоружный удар"
    label = REACTIONS[choice][0].lower()
    head = (f"🛡 {hero.name}: {label} — {foe.name} ({wname}), шанс {pct}%"
            + (" (враг ждёт этого приёма)" if tired else "") + " — ")
    return _land(scene, foe, hero, choice, ok, head)


def _land(scene: Scene, foe: Character, hero: Character, choice: str, ok: bool, head: str, how: str = "") -> dict:
    """What the blow does after the reaction: dodge — all or nothing; block — half on success; parry — nothing and a
    counterattack, or ×1,5 on failure; take — the plain blow. Armour always takes its part.
    `how` — the player's own words for a free reaction."""
    w, _, _ = _foe_attack(foe)
    how_free = how
    if ok and choice != "block":
        how = {"dodge": "удар проходит мимо", "parry": "клинок отведён — контратака!", "take": "удар скользит мимо"}[choice]
        brief = {"dodge": f"{hero.name} уклоняется от удара {foe.name}",
                 "parry": f"{hero.name} парирует удар {foe.name} и контратакует",
                 "take": f"удар {foe.name} не достигает {hero.name}"}[choice]
        if how_free:
            brief = f"{hero.name}: {how_free} — удаётся, удар {foe.name} не достигает цели" + (
                ", и герой контратакует" if choice == "parry" else "")
        how = how[0].upper() + how[1:] + ("" if how.endswith("!") else ".")
        return {"text": head + f"успех! {how}", "msgs": [], "brief": brief, "counter": choice == "parry"}
    dmg, dmg_s = physical(scene, foe, w)
    if choice == "block" and ok:
        dmg = max(1, dmg // 2); dmg_s += f", блок ½={dmg}"
        verdict = "успех! Удар ослаблен." if how_free else "успех! Удар принят на блок."
    elif choice == "parry":
        dmg = round(dmg * 1.5); dmg_s += f", раскрылся ×1,5={dmg}"
        verdict = "провал!"
    else:
        verdict = "провал!"
    dmg, land = hurt(scene, hero, dmg, True)
    if how_free:
        brief = (f"{hero.name}: {how_free} — " + (f"удар {foe.name} ослаблен, урон {dmg}" if choice == "block" and ok
                                              else f"не выходит, {foe.name} попадает, урон {dmg}"))
    elif choice == "block" and ok:
        brief = f"{hero.name} блокирует удар {foe.name}, урон снижен до {dmg}"
    elif choice == "parry":
        brief = f"{hero.name} пытается парировать, но раскрывается — {foe.name} попадает, урон {dmg}"
    elif choice == "take":
        brief = f"{foe.name} попадает по {hero.name}, урон {dmg}"
    else:
        brief = f"{hero.name} пытается {'уклониться' if choice == 'dodge' else 'блокировать'}, но {foe.name} попадает, урон {dmg}"
    text = head + f"{verdict} {dmg_s}{land}"
    text, msgs = knockout(scene, hero, text)
    if hero.hp == 0:
        brief += f", {hero.name} падает без сознания"
    return {"text": text, "msgs": msgs, "brief": brief, "counter": False}


# ---------------- free-form deeds: the narrator judges what the player wrote, the code rolls ----------------
DIFFICULTY = B.DIFFICULTY
OUTCOMES = {"уклон": "avoid", "избеж": "avoid", "avoid": "avoid", "dodge": "avoid", "мимо": "avoid",
            "смягч": "reduce", "ослаб": "reduce", "блок": "reduce", "reduce": "reduce", "block": "reduce",
            "контр": "counter", "counter": "counter", "парир": "counter", "parry": "counter"}
OUTCOMES.update({"размен": "trade", "trade": "trade", "нет": "none", "none": "none", "не защищ": "none"})
OUTCOME_KIND = {"avoid": "dodge", "reduce": "block", "counter": "parry"}
MIN_DIFF = {"avoid": "обычно", "counter": "трудно"}     # a big payoff is never «легко»
EFFECTS = {"урон": "урон", "damage": "урон", "оглуш": "оглушение", "stun": "оглушение", "сбит": "оглушение",
           "ослаб": "ослабление", "weaken": "ослабление", "обезоруж": "ослабление", "ослеп": "ослабление",
           "помех": "ослабление", "усил": "усиление", "buff": "усиление", "преимущ": "усиление", "нет": "нет",
           "none": "нет"}
DEED_POTENCY = 3          # a successful trick: ±3 damage and ±10% to hit for 2 rounds


def norm_difficulty(d: str) -> str:
    d = (d or "").lower().strip()
    if d.startswith(("невозм", "impossible")):
        return "невозможно"
    if "очень" in d or d.startswith(("very", "крайне")):
        return "очень трудно"
    if d.startswith(("трудн", "сложн", "hard")):
        return "трудно"
    if d.startswith(("легк", "лёгк", "прост", "easy")):
        return "легко"
    return "обычно"


def pick(table: dict, raw: str, default: str) -> str:
    r = (raw or "").lower().strip()
    return next((v for k, v in table.items() if r.startswith(k) or k in r), default)


def harder(a: str, b: str) -> str:
    order = list(DIFFICULTY)
    return a if order.index(a) >= order.index(b) else b


def react_free(scene: Scene, attr_raw: str, difficulty: str, outcome: str, how: str = "") -> dict:
    """The player described their own reaction; the narrator judged it (attribute, difficulty, what it achieves),
    the code rolls: attribute vs difficulty + the foe's attacking attribute over 10."""
    cb = scene.combat
    foe, hero = scene.characters.get(cb.threat), scene.player
    cb.threat = ""
    if foe is None or foe.hp <= 0 or hero.hp <= 0 or not cb.active:
        end = check_end(scene)
        return {"text": "Угроза миновала.", "msgs": [end] if end else [], "brief": "угроза миновала", "counter": False}
    outcome = pick(OUTCOMES, outcome, "avoid")
    difficulty = norm_difficulty(difficulty)
    how = (how or "своя реакция").strip()[:80]
    _, _, a = _foe_attack(foe)
    if outcome in ("trade", "none"):
        cb.react_last, cb.react_streak = "", 0
        if outcome == "none":
            ok, pct = M.chance(scene, 1 - B.hit_chance(a, hero.attrs.get("Ловкость", 10)))
            head = f"🛡 {hero.name}: {how} — не защищается, шанс что удар пройдёт мимо {pct}% — "
            return _land(scene, foe, hero, "take", ok, head, how)
        head = f"🛡 {hero.name}: {how} — размен ударами, защиты нет — "
        r = _land(scene, foe, hero, "take", False, head, how)
        r["counter"] = hero.hp > 0                     # the hero's own blow comes right after (the engine rolls it)
        r["brief"] += "; герой бьёт в ответ"
        return r
    if difficulty == "невозможно":
        head = f"🛡 {hero.name}: {how} — так не выйдет — "
        return _land(scene, foe, hero, OUTCOME_KIND[outcome] if outcome != "counter" else "dodge", False, head, how)
    difficulty = harder(difficulty, MIN_DIFF.get(outcome, "легко"))
    attr = M.resolve_check_attr(attr_raw, scene)
    dc = DIFFICULTY[difficulty] + (a - 10)
    ok, pct = M.chance(scene, B.check_chance(hero.attrs.get(attr, 10), dc))
    cb.react_last, cb.react_streak = "", 0             # an inventive answer resets the foes' read
    from . import rules as R
    head = f"🛡 {hero.name}: {how} — {R.label(scene, attr)} {hero.attrs.get(attr, 10)}, {difficulty}, против {foe.name}: шанс {pct}% — "
    return _land(scene, foe, hero, OUTCOME_KIND[outcome], ok, head, how)


def escape(scene: Scene, attr_raw: str, difficulty: str, kind: str = "", how: str = "") -> dict:
    """The hero runs or yields; the narrator judged how hard it is, the code rolls.
    Surrender: no roll, the fight ends and the foes decide what comes next. Flight: attribute vs difficulty plus the
    most agile standing foe's edge (ranged foes and heavy armour make it harder); success ends the fight and leaves the
    foes behind, failure loses the turn and a blow that was waiting lands. Returns {ok, ended, text, msgs, brief}."""
    cb = scene.combat
    hero = scene.player
    foe = scene.characters.get(cb.threat) if cb.threat else None
    cb.threat = ""
    foes = [scene.characters[o.id] for o in cb.order
            if o.id in scene.characters and not M.in_party(scene, scene.characters[o.id])
            and scene.characters[o.id].hp > 0]
    how = (how or "").strip()[:80]
    k = (kind or "").lower()
    if any(w in k for w in ("сда", "surrender", "капитул")):
        how = how or "сдаётся"
        end_combat(scene)
        names = ", ".join(f.name for f in foes) or "враги"
        return {"ok": True, "ended": True, "msgs": [], "text": f"🏳 {hero.name}: {how} — бой окончен.",
                "brief": f"{hero.name} сдаётся ({how}): бой окончен, {names} решают его судьбу — опиши это"}
    how = how or "бросается бежать"
    difficulty = norm_difficulty(difficulty)
    if difficulty == "невозможно" or not foes:
        ok, text = False, f"🏃 {hero.name}: {how} — отступать некуда."
    else:
        edge = max(f.attrs.get("Ловкость", 10) for f in foes) - 10
        if any(is_ranged(equipped_weapon(f)) for f in foes):
            edge += 2
        dc = DIFFICULTY[difficulty] + edge + max(0, armor_value(hero) - 2)
        r = M.check(scene, hero, attr_raw or "Ловкость", dc)
        ok = r["success"]
        text = f"🏃 {hero.name}: {how} — " + r["text"].split(": ", 1)[-1]
    if ok:
        gone = {f.id for f in foes}
        scene.present = [i for i in scene.present if i not in gone]
        end_combat(scene)
        names = ", ".join(f.name for f in foes)
        return {"ok": True, "ended": True, "msgs": [], "text": text + " Бой окончен: враги остались позади.",
                "brief": f"{hero.name} вырывается из боя ({how}); {names} остались позади — опиши бегство"}
    msgs = []
    brief = f"{hero.name} пытается скрыться ({how}), но не уйти от погони"
    if foe is not None and foe.hp > 0 and hero.hp > 0:      # the waiting blow lands: the hero gave up his guard
        land = _land(scene, foe, hero, "take", False, "", how)
        text += "\n" + land["text"]; msgs += land["msgs"]; brief = land["brief"]
    return {"ok": False, "ended": not scene.combat.active, "text": text, "msgs": msgs, "brief": brief}


def deed(scene: Scene, actor: Character, target: Character | None, attr_raw: str, difficulty: str, effect: str,
         how: str = "") -> dict:
    """A free-form deed on the actor's turn (a feint, sand in the eyes, a shield bash, kicking a brazier over...):
    the narrator judged attribute, difficulty and effect; the code rolls the check and applies the effect.
    A damaging trick hits like the actor's weapon. Returns {ok, text, msgs, brief, damage}."""
    difficulty = norm_difficulty(difficulty)
    effect = pick(EFFECTS, effect, "нет")
    how = (how or "задуманный приём").strip()[:80]
    if difficulty == "невозможно":
        return {"ok": False, "text": f"✦ {actor.name}: {how} — так не выйдет.", "msgs": [], "damage": 0,
                "brief": f"{actor.name}: {how} — не выходит, это невозможно"}
    if effect == "урон":
        difficulty = harder(difficulty, "обычно")          # a plain blow is <attack>; a trick that hurts is harder
    resist = 0
    if target is not None and target.id != actor.id and effect != "усиление":
        resist = target.attrs.get("Воля" if M.resolve_check_attr(attr_raw, scene) in ("Влияние", "Разум") else "Ловкость", 10) - 10
    r = M.check(scene, actor, attr_raw, DIFFICULTY[difficulty] + resist)
    text = f"✦ {actor.name}: {how} — " + r["text"].split(": ", 1)[-1]
    msgs, dmg = [], 0
    if not r["success"]:
        return {"ok": False, "text": text, "msgs": msgs, "damage": 0, "brief": f"{actor.name}: {how} — не удаётся"}
    if effect == "урон" and target is not None:
        dmg, dmg_s = physical(scene, actor, equipped_weapon(actor))
        dmg, land = hurt(scene, target, dmg, True)
        text += f" {dmg_s}{land}"
        text, more = knockout(scene, target, text)
        msgs += more
        brief = f"{actor.name}: {how} — удаётся, урон {dmg}" + (f", {target.name} падает" if target.hp == 0 else "")
    elif effect == "оглушение" and target is not None:
        msgs.append(apply_condition(target, "оглушение", 1))
        brief = f"{actor.name}: {how} — удаётся, {target.name} теряет ход"
    elif effect == "ослабление" and target is not None:
        msgs.append(apply_condition(target, "ослабление", 2, DEED_POTENCY))
        brief = f"{actor.name}: {how} — удаётся, {target.name} ослаблен(а) на 2 хода"
    elif effect == "усиление":
        msgs.append(apply_condition(actor, "усиление", 2, DEED_POTENCY))
        brief = f"{actor.name}: {how} — удаётся, следующие действия сильнее"
    else:
        brief = f"{actor.name}: {how} — удаётся"
    return {"ok": True, "text": text, "msgs": msgs, "damage": dmg, "brief": brief}


# ---------------- abilities ----------------
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
    """Debuffs and control: the caster's attribute against the target's Воля."""
    cv, tv = caster.attrs.get(a.scale or "Разум", 10), target.attrs.get("Воля", 10)
    if scene.rule_mode == "система":
        ok, pct = M.chance(scene, B.CHECK_BASE + B.STEP * (cv - tv))
        return ok, f"{a.scale or 'Разум'} {cv} против Воли {tv} — шанс {pct}%"
    die = M.rng(scene).randint(1, 20)
    power, defense = M.soft_weight(cv, 10), 10 + M.soft_weight(tv, 10)
    return die == 20 or (die != 1 and die + power >= defense), f"1d20({die}){power:+d}={die + power} против Воли {defense}"


def is_physical(a: Ability) -> bool:
    """A technique powered by the body (Сила/Ловкость) is a blow: armour takes its part. Spells pass armour."""
    return a.scale in ("Сила", "Ловкость")


def cast(scene: Scene, caster: Character, a: Ability, target: Character | None,
         extra: list[Character] | None = None) -> dict:
    """Use an active ability: code spends MP (and the health it costs), applies its number amplified by the attribute
    to up to `a.targets` targets, puts its rider condition and starts its recharge. Its `rule` — a mechanic in words —
    goes to the narrator. Abilities without numbers only cost MP; the narrator describes their effect."""
    if a.kind != "active":
        return {"ok": False, "text": f"«{a.name}» — пассивная способность, она действует сама.", "msgs": [], "damage": 0}
    mp = ability_mp(a)
    wait = caster.ready_at.get(a.name, 0) - scene.turn
    if wait > 0:
        return {"ok": False, "text": f"✨ {caster.name}: «{a.name}» перезаряжается — ещё {wait} ход.", "msgs": [], "damage": 0}
    if caster.mp < mp:
        return {"ok": False, "text": f"✨ {caster.name}: «{a.name}» — не хватает MP ({caster.mp}/{mp}). Не сработало.",
                "msgs": [], "damage": 0}
    if a.self_hp and caster.hp <= a.self_hp:
        return {"ok": False, "text": f"✨ {caster.name}: «{a.name}» требует {a.self_hp} HP — сил не хватит.", "msgs": [], "damage": 0}
    if any(x.name == "оглушение" for x in caster.conditions):
        return {"ok": False, "text": f"✨ {caster.name} оглушён и не может применить «{a.name}».", "msgs": [], "damage": 0}
    eff = a.effect or "utility"
    if eff in ("heal", "shield", "buff") and target is None:
        target = caster
    if eff in ("damage", "debuff", "control") and target is None:
        return {"ok": False, "text": f"✨ «{a.name}»: нужна цель.", "msgs": [], "damage": 0}
    targets = [t for t in [target] + list(extra or []) if t is not None]
    seen = set(); targets = [t for t in targets if not (t.id in seen or seen.add(t.id))][:max(1, a.targets)]
    caster.mp -= mp
    paid = ""
    if a.self_hp:
        caster.hp -= a.self_hp; paid = f", −{a.self_hp} HP"
    if a.recharge:
        caster.ready_at[a.name] = scene.turn + a.recharge + 1
    names = ", ".join(t.name for t in targets if t is not caster)
    head = f"✨ {caster.name}: «{a.name}»" + (f" → {names}" if names else "") + (f" (−{mp} MP{paid})" if mp or paid else "")
    msgs, dmg, parts = [], 0, []
    if eff in ("damage", "debuff", "control") and not scene.combat.active and \
            (eff == "damage" or any(t.side == "hostile" for t in targets)):
        start_combat(scene, [caster.id] + [t.id for t in targets], first=caster.id)
    for t in targets:
        part, d, more = _apply(scene, caster, a, eff, t)
        parts.append(part); dmg += d; msgs += more
    text = head + ": " + "; ".join(parts) if parts else head + ": применено."
    if a.rule:
        text += f" Правило способности: {a.rule}."
    return {"ok": True, "text": text, "msgs": msgs, "damage": dmg}


def _rider(scene: Scene, a: Ability, t: Character, v: int) -> str:
    from . import rules as R
    if not a.rider or t.hp <= 0:
        return ""
    if R.cond_kind(scene, a.rider) == "stun":
        apply_condition(t, "оглушение", turns=a.rider_turns or 1)
        return f", оглушение ({a.rider_turns or 1} р.)"
    pot = max(1, v // 5) if R.is_dot(scene, a.rider) else 0
    apply_condition(t, a.rider, turns=a.rider_turns or 2, potency=pot)
    return f", {a.rider} ({a.rider_turns or 2} р." + (f", −{pot} HP за раунд" if pot else "") + ")"


def _apply(scene: Scene, caster: Character, a: Ability, eff: str, target: Character) -> tuple[str, int, list[str]]:
    """One target of an ability: (text, damage dealt, messages)."""
    who = f"{target.name}: " if a.targets > 1 else ""
    if eff == "damage":
        phys = is_physical(a)
        key = ("ranged" if a.scale == "Ловкость" else "melee") if phys else "spell"
        v, s = magical(scene, caster, a, key)
        if phys:
            v += cond_potency(caster, "усиление") - cond_potency(caster, "ослабление")
        d, land = hurt(scene, target, max(1, v), phys)
        text, more = knockout(scene, target, f"{who}{s}{land}" + _rider(scene, a, target, v))
        return text, d, more
    if eff == "heal":
        v, s = magical(scene, caster, a, "heal")
        old = target.hp; target.hp = min(target.max_hp, target.hp + v)
        return f"{who}лечение {s} → +{target.hp - old} HP ({old}→{target.hp}/{target.max_hp})", 0, []
    if eff == "shield":
        v, s = magical(scene, caster, a, "")
        apply_condition(target, "щит", turns=3, potency=v)
        return f"{who}щит {s} — поглотит {v} урона (3 раунда)", 0, []
    if eff == "buff":
        v, _ = magical(scene, caster, a, "", levelled=False)
        apply_condition(target, "усиление", turns=3, potency=v)
        return f"{who}усиление +{v} к урону и +{round(B.BUFF_HIT * 100)}% к попаданию (3 раунда)", 0, []
    if eff in ("debuff", "control"):
        ok, roll_s = resist(scene, caster, a, target)
        if not ok:
            return f"{who}{roll_s} — {target.name} сопротивляется", 0, []
        if eff == "debuff":
            v, _ = magical(scene, caster, a, "", levelled=False)
            apply_condition(target, "ослабление", turns=2, potency=v)
            return (f"{who}{roll_s} — ослабление −{v} к урону и −{round(B.BUFF_HIT * 100)}% к попаданию (2 раунда)"
                    + _rider(scene, a, target, v)), 0, []
        turns = max(1, B.to_number(a.power) or 1)
        apply_condition(target, "оглушение", turns=turns)
        return f"{who}{roll_s} — {target.name} оглушён ({turns} раунд.)" + _rider(scene, a, target, 0), 0, []
    n = B.to_number(a.power)
    return (f"{who}эффект описывает рассказчик" + (f" (сила {n})" if n else "") + _rider(scene, a, target, n)), 0, []


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
