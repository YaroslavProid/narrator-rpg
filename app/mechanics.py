"""Deterministic mechanics: soft attributes, named abilities, checks, deltas, 'Система' commands."""
import difflib, math, random, re
from . import config
from .models import ATTRS, ATTR_MIGRATE, Ability, Character, Item, Reveal, Scene

# Soft-stat thresholds (attr vs baseline 10, or vs DC / opponent):
# |gap| ≤ SOFT_NEGLIGIBLE → weight 0 (narrative / near coin-flip)
# SOFT_NEGLIGIBLE < |gap| ≤ SOFT_MODERATE → ±1
# |gap| > SOFT_MODERATE → ±max(2, |gap|//2)  (colossal gap dominates)
SOFT_NEGLIGIBLE = 2
SOFT_MODERATE = 4

# Legacy skill names → attribute (old saves / old <roll skill="Скрытность">)
LEGACY_SKILL_ATTR = {
    "Атлетика": "Сила", "Ближний бой": "Сила",
    "Акробатика": "Ловкость", "Скрытность": "Ловкость", "Ловкость рук": "Ловкость",
    "Взлом": "Ловкость", "Стрельба": "Ловкость",
    "Выносливость": "Выносливость",
    "Магия": "Разум", "Знания": "Разум", "Расследование": "Разум",
    "Восприятие": "Воля", "Проницательность": "Воля", "Выживание": "Воля", "Медицина": "Воля",
    "Убеждение": "Влияние", "Обман": "Влияние", "Запугивание": "Влияние", "Выступление": "Влияние",
}
# Keep SKILLS alias for any leftover imports (maps to attr name as "skill" label → attr)
SKILLS = {k: v for k, v in LEGACY_SKILL_ATTR.items()}

SKILL_ALIASES = {
    "athletics": "Атлетика", "strength": "Атлетика", "melee": "Ближний бой", "attack": "Ближний бой", "fight": "Ближний бой",
    "combat": "Ближний бой", "acrobatics": "Акробатика", "dexterity": "Акробатика", "stealth": "Скрытность", "sneak": "Скрытность",
    "hide": "Скрытность", "sleight_of_hand": "Ловкость рук", "sleight of hand": "Ловкость рук", "pickpocket": "Ловкость рук",
    "steal": "Ловкость рук", "lockpicking": "Взлом", "lockpick": "Взлом", "ranged": "Стрельба", "archery": "Стрельба",
    "shooting": "Стрельба", "endurance": "Выносливость", "constitution": "Выносливость", "arcana": "Магия", "magic": "Магия",
    "spellcasting": "Магия", "history": "Знания", "knowledge": "Знания", "lore": "Знания", "religion": "Знания",
    "investigation": "Расследование", "perception": "Восприятие", "awareness": "Восприятие", "insight": "Проницательность",
    "survival": "Выживание", "medicine": "Медицина", "heal": "Медицина", "persuasion": "Убеждение", "persuade": "Убеждение",
    "diplomacy": "Убеждение", "charisma": "Убеждение", "deception": "Обман", "lie": "Обман", "bluff": "Обман",
    "intimidation": "Запугивание", "intimidate": "Запугивание", "performance": "Выступление",
    "внимательность": "Восприятие", "наблюдательность": "Восприятие", "ловкость": "Акробатика", "сила": "Атлетика",
    "харизма": "Убеждение", "воровство": "Ловкость рук", "карманная кража": "Ловкость рук", "бой": "Ближний бой",
    "атака": "Ближний бой", "угроза": "Запугивание", "ложь": "Обман", "незаметность": "Скрытность", "стелс": "Скрытность",
    "will": "Воля", "mind": "Разум", "influence": "Влияние", "presence": "Влияние", "spirit": "Воля",
}
ATTR_ALIASES = {
    "сил": "Сила", "лов": "Ловкость", "вын": "Выносливость", "тел": "Выносливость",
    "вол": "Воля", "муд": "Воля", "раз": "Разум", "инт": "Разум", "влия": "Влияние", "хар": "Влияние",
    "str": "Сила", "dex": "Ловкость", "agi": "Ловкость", "con": "Выносливость", "vit": "Выносливость",
    "int": "Разум", "wis": "Воля", "cha": "Влияние", "wil": "Воля",
}
FORBIDDEN_ITEM_WORDS = ["легендар", "божествен", "артефакт", "бессмерт", "всемогущ", "legendary", "artifact", "divine"]
LIMITS = {"hp_frac": 0.3, "mp_frac": 0.5, "exp_event": 50, "exp_turn": 100, "items_per_event": 2, "item_len": 40}

TRANSLIT = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                    ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s", "t", "u",
                     "f", "h", "ts", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya"]))


def translit(s: str) -> str:
    return "".join(TRANSLIT.get(c, c) for c in s.lower())


def norm(s: str) -> str:
    return re.sub(r"[\s_\-]+", " ", (s or "").strip().lower().strip("\"'«»"))


def soft_weight(value: int, baseline: int = 10) -> int:
    """Soft modifier: small gaps barely matter; colossal gaps dominate."""
    d = int(value) - int(baseline)
    ad = abs(d)
    if ad <= SOFT_NEGLIGIBLE:
        return 0
    if ad <= SOFT_MODERATE:
        return 1 if d > 0 else -1
    return max(2, ad // 2) * (1 if d > 0 else -1)


def soft_label(weight: int) -> str:
    if weight == 0:
        return "разрыв ничтожен"
    if abs(weight) == 1:
        return "небольшой перевес"
    return "колоссальный разрыв"


def resolve_attr(name: str) -> str | None:
    n = norm(name)
    if not n:
        return None
    for a in ATTRS:
        if a.lower() == n:
            return a
    if name in ATTR_MIGRATE:
        return ATTR_MIGRATE[name]
    for k, v in ATTR_ALIASES.items():
        if n.startswith(k):
            return v
    m = difflib.get_close_matches(n, [a.lower() for a in ATTRS], n=1, cutoff=0.6)
    return next((a for a in ATTRS if m and a.lower() == m[0]), None)


def resolve_skill(name: str) -> str | None:
    """Legacy: resolve old skill name; returns the skill's canonical Russian name or None."""
    n = norm(name)
    for s in LEGACY_SKILL_ATTR:
        if s.lower() == n:
            return s
    if n in SKILL_ALIASES:
        return SKILL_ALIASES[n]
    if n.replace(" ", "_") in SKILL_ALIASES:
        return SKILL_ALIASES[n.replace(" ", "_")]
    pool = {s.lower(): s for s in LEGACY_SKILL_ATTR} | SKILL_ALIASES
    m = difflib.get_close_matches(n, list(pool), n=1, cutoff=0.6)
    return pool[m[0]] if m else None


def resolve_check_attr(raw: str) -> str:
    """What attribute a <roll skill=...> or check uses (attrs first, then legacy skills)."""
    a = resolve_attr(raw)
    if a:
        return a
    sk = resolve_skill(raw)
    if sk:
        return LEGACY_SKILL_ATTR[sk]
    return "Воля"  # default perception-ish


def resolve_character(scene: Scene, name: str, only_present: bool = False) -> Character | None:
    n = norm(name)
    if not n:
        return None
    pool = {}
    ids = scene.present if only_present else list(scene.characters)
    for cid in ids:
        c = scene.characters[cid]
        keys = [c.name, c.name.split()[0], *c.aliases, translit(c.name), translit(c.name.split()[0])]
        for k in keys:
            pool.setdefault(norm(k), c)
    if n in {"игрок", "player", "герой", "ты", "you"}:
        return scene.player
    if n in pool:
        return pool[n]
    for k, c in pool.items():
        if len(n) >= 3 and (k.startswith(n) or n.startswith(k)):
            return c
    m = difflib.get_close_matches(n, list(pool), n=1, cutoff=0.7) or difflib.get_close_matches(translit(n), list(pool), n=1, cutoff=0.7)
    return pool[m[0]] if m else None


def mod(v: int) -> int:
    """Legacy hard modifier — prefer soft_weight for checks; kept for AC/display."""
    return (v - 10) // 2


def amp(v: int) -> int:
    """Attribute as an amplifier of what a character does: damage, spell power, healing, HP/MP pools.
    10 is the human norm (0); every 2 points above add +1, capped so stats never replace dice."""
    return max(-2, min(5, (int(v) - 10) // 2))


# passive ability bonus keys: hp, mp (pools), ac, melee, ranged (weapon damage), spell (spell power),
# heal (healing power), init (initiative)
BONUS_KEYS = ("hp", "mp", "ac", "melee", "ranged", "spell", "heal", "init")


def passive_bonus(c: Character, key: str) -> int:
    return sum(int((a.bonus or {}).get(key, 0)) for a in c.abilities if a.kind == "passive")


def sync_derived(c: Character) -> list[str]:
    """Max HP/MP follow Выносливость/Разум and passive abilities. The bonus part is stored apart from the base
    (level-ups), so it can be recomputed any time; HP/MP gained this way are granted, lost ones are clamped."""
    msgs = []
    hp_b = 3 * amp(c.attrs.get("Выносливость", 10)) + passive_bonus(c, "hp")
    mp_b = 2 * amp(c.attrs.get("Разум", 10)) + passive_bonus(c, "mp")
    if hp_b != c.hp_bonus:
        d = hp_b - c.hp_bonus
        c.max_hp = max(1, c.max_hp + d); c.hp_bonus = hp_b
        c.hp = max(0, min(c.max_hp, c.hp + max(0, d)))
        msgs.append(f"{c.name}: макс. HP {c.max_hp - d}→{c.max_hp}")
    if mp_b != c.mp_bonus:
        d = mp_b - c.mp_bonus
        c.max_mp = max(0, c.max_mp + d); c.mp_bonus = mp_b
        c.mp = max(0, min(c.max_mp, c.mp + max(0, d)))
        msgs.append(f"{c.name}: макс. MP {c.max_mp - d}→{c.max_mp}")
    return msgs


def sync_all(scene: Scene):
    for c in scene.characters.values():
        sync_derived(c)


def rng(scene: Scene) -> random.Random:
    r = random.Random(f"{scene.rng_seed}:{scene.rng_calls}")
    scene.rng_calls += 1
    return r


def check(scene: Scene, who: Character, skill_raw: str, dc_raw) -> dict:
    """Soft attribute check. Small gaps barely matter; large gaps dominate.
    d20: d20 + soft_weight(attr, 10) vs DC.
    система: soft compare attr vs DC (as target rating), no die unless tied."""
    attr = resolve_check_attr(skill_raw)
    try:
        dc = int(re.sub(r"[^\d-]", "", str(dc_raw)) or 12)
    except ValueError:
        dc = 12
    dc = max(5, min(30, dc))
    aval = who.attrs.get(attr, 10)
    w = soft_weight(aval, 10)
    res = {"who": who.name, "skill": attr, "attr": attr, "mod": w, "skill_lvl": 0, "dc": dc,
           "mode": scene.rule_mode, "attr_val": aval, "soft": soft_label(w)}
    if scene.rule_mode == "d20":
        d = rng(scene).randint(1, 20)
        total = d + w
        ok = d == 20 or (d != 1 and total >= dc)
        res.update(die=d, total=total, success=ok, crit=d == 20, fumble=d == 1)
        tag = " (критический успех!)" if d == 20 else " (критический провал!)" if d == 1 else ""
        soft = f", {res['soft']}" if w != 0 or True else ""
        res["text"] = (f"Проверка «{attr}» ({who.name}): 1d20({d}) {w:+d} = {total} "
                       f"против {dc} — {'успех' if ok else 'провал'}{tag} [{res['soft']}, {attr} {aval}].")
    else:
        # Non-dice: compare character rating to DC as target rating via soft gaps
        gap_w = soft_weight(aval, dc)
        if gap_w == 0:
            ok = aval >= dc  # near-equal: slight lean
        else:
            ok = gap_w > 0
        res.update(die=None, total=aval, success=ok, crit=False, fumble=False, mod=gap_w, soft=soft_label(gap_w))
        res["text"] = (f"Проверка «{attr}» ({who.name}, без броска): {attr} {aval} против {dc} — "
                       f"{'успех' if ok else 'провал'} [{res['soft']}].")
    return res


def _int(v) -> int:
    try:
        return int(re.sub(r"[^\d+-]", "", str(v)) or 0)
    except ValueError:
        return 0


def grant_exp(scene: Scene, c: Character, amount: int, msgs: list, note: str = ""):
    if amount <= 0:
        return
    c.exp += amount
    msgs.append(f"{c.name}: +{amount} EXP{note} ({c.exp}/{c.exp_next})")
    while c.exp >= c.exp_next:
        c.exp -= c.exp_next
        c.level += 1
        c.exp_next = int(c.exp_next * 1.5)
        c.max_hp += 5; c.hp = c.max_hp
        c.max_mp += 3; c.mp = c.max_mp
        c.attr_points += 2; c.ability_points += 1; c.skill_points = c.ability_points
        extra = " Очко способностей тратится в Книге путей." if c.is_player or c.id in scene.party else ""
        msgs.append(f"⟦ УРОВЕНЬ ПОВЫШЕН ⟧ {c.name}: уровень {c.level}! HP/MP восстановлены, +2 очка атрибутов, +1 очко способностей "
                    f"(«Система, распредели {'' if c.is_player else c.name.split()[0] + ' '}Сила +1»).{extra}")


def party_ids(scene: Scene) -> list[str]:
    """The hero first, then companions in joining order."""
    return [scene.player_id] + [i for i in scene.party if i in scene.characters and i != scene.player_id]


def in_party(scene: Scene, c: Character | None) -> bool:
    return bool(c) and (c.is_player or c.id in scene.party)


def catchup_mult(scene: Scene, c: Character) -> float:
    """Companions behind the hero learn faster: base**level_gap (each level costs x1.5 more), capped."""
    if c.is_player:
        return 1.0
    gap = max(0, scene.player.level - c.level)
    return min(config.EXP_CATCHUP_MAX, config.EXP_CATCHUP_BASE ** gap)


def award_exp(scene: Scene, c: Character, base: int, msgs: list, capped: bool = True) -> int:
    """EXP for a party member. `capped`: the per-turn cap (LIMITS exp_turn) applies to the base amount so the
    narrator cannot farm EXP; system-decided rewards (defeated foes) pass capped=False."""
    if base <= 0 or not in_party(scene, c):
        return 0
    if capped:
        got = scene.turn_exp if c.is_player else scene.turn_exp_by.get(c.id, 0)
        base = max(0, min(base, LIMITS["exp_turn"] - got))
        if c.is_player:
            scene.turn_exp = got + base
        else:
            scene.turn_exp_by[c.id] = got + base
    mult = catchup_mult(scene, c)
    amount = round(base * mult)
    grant_exp(scene, c, amount, msgs, f" (×{mult:g} — догоняет героя)" if mult > 1 and amount else "")
    return amount


def kill_exp(foe: Character) -> int:
    return config.EXP_KILL + 5 * max(0, foe.level - 1)


def join_party(scene: Scene, c: Character) -> tuple[bool, str]:
    """A character agreed to join: the full card opens up, they start at level 1, the AI completes the card later."""
    if c.is_player:
        return False, "Герой и так ведёт отряд."
    if c.id in scene.party:
        return False, f"{c.name} уже в отряде."
    if c.hp <= 0:
        return False, f"{c.name} без сознания и не может вступить в отряд."
    if len(party_ids(scene)) >= config.PARTY_MAX:
        names = ", ".join(scene.characters[i].name for i in scene.party)
        return False, (f"Отряд полон: не больше {config.PARTY_MAX} вместе с героем ({names}). "
                       f"{c.name} не может присоединиться, пока кто-то не уйдёт.")
    scene.party.append(c.id)
    c.side = "party"
    c.level, c.exp, c.exp_next = 1, 0, 100
    c.attr_points = 0
    c.ability_points = c.skill_points = 1   # like the hero: a first step on a path of the player's choice
    c.reveal.kind = c.reveal.attrs = c.reveal.abilities = True
    c.last_active_turn = scene.turn
    if c.id not in scene.present:
        scene.present.append(c.id)
    if c.id not in scene.pending_fill:
        scene.pending_fill.append(c.id)
    return True, f"{c.name} вступает в отряд — уровень 1. Отряд: {len(party_ids(scene))}/{config.PARTY_MAX}."


def leave_party(scene: Scene, c: Character | None) -> tuple[bool, str]:
    if not c or c.id not in scene.party:
        return False, f"{c.name if c else 'Этот персонаж'} не состоит в отряде."
    scene.party.remove(c.id)
    c.side = "neutral"
    if c.id in scene.pending_fill:
        scene.pending_fill.remove(c.id)
    return True, f"{c.name} покидает отряд. Отряд: {len(party_ids(scene))}/{config.PARTY_MAX}."


def pick_member(scene: Scene, rest: str) -> tuple[Character, str]:
    """'распредели Сильфе Ловкость +1' -> (Сильфа, 'распредели  Ловкость +1'). Defaults to the hero.
    Matches a word stem so Russian case endings (Сильфе, Сильфы) still work."""
    low = (rest or "").lower()
    for cid in scene.party:
        c = scene.characters.get(cid)
        if not c:
            continue
        for key in {c.name.lower(), c.name.split()[0].lower()}:
            stem = key[:max(3, len(key) - 2)] if len(key) > 4 else key
            m = re.search(r"(?<![а-яёa-z])" + re.escape(stem) + r"[а-яёa-z]*", low)
            if m:
                return c, rest[:m.start()] + rest[m.end():]
    return scene.player, rest


def apply_delta(scene: Scene, c: Character, delta: dict) -> tuple[list[str], list[str]]:
    """Validate and clamp a model-proposed delta. Returns (applied messages, rejected notes)."""
    msgs, rej = [], []
    dhp = _int(delta.get("hp", 0)); cap = max(1, math.ceil(c.max_hp * LIMITS["hp_frac"]))
    if dhp:
        if abs(dhp) > cap:
            rej.append(f"HP {dhp:+d} урезано до {math.copysign(cap, dhp):+.0f}")
            dhp = int(math.copysign(cap, dhp))
        old = c.hp; c.hp = max(0, min(c.max_hp, c.hp + dhp))
        if c.hp != old:
            msgs.append(f"{c.name}: HP {old}→{c.hp}/{c.max_hp}" + (" — без сознания!" if c.hp == 0 else ""))
    dmp = _int(delta.get("mp", 0)); cap = max(1, math.ceil(c.max_mp * LIMITS["mp_frac"]))
    if dmp:
        if abs(dmp) > cap:
            rej.append(f"MP {dmp:+d} урезано"); dmp = int(math.copysign(cap, dmp))
        old = c.mp; c.mp = max(0, min(c.max_mp, c.mp + dmp))
        if c.mp != old:
            msgs.append(f"{c.name}: MP {old}→{c.mp}/{c.max_mp}")
    dexp = _int(delta.get("exp", 0))
    if dexp < 0:
        rej.append("отрицательный опыт отклонён"); dexp = 0
    allowed = max(0, min(LIMITS["exp_event"], LIMITS["exp_turn"] - scene.turn_exp))
    if dexp > allowed:
        rej.append(f"EXP {dexp} урезано до {allowed}"); dexp = allowed
    scene.turn_exp += dexp
    grant_exp(scene, c, dexp, msgs)
    adds = [a.strip() for a in re.split(r"[;,]", delta.get("add", "") or "") if a.strip()]
    for a in adds[:LIMITS["items_per_event"]]:
        if len(a) > LIMITS["item_len"] or any(w in a.lower() for w in FORBIDDEN_ITEM_WORDS):
            rej.append(f"предмет «{a}» отклонён"); continue
        ex = next((i for i in c.inventory if i.name.lower() == a.lower()), None)
        if ex: ex.qty += 1
        else:
            from . import combat as C
            c.inventory.append(C.ensure_item_stats(Item(name=a)))
        msgs.append(f"{c.name}: получен предмет «{a}»")
    if len(adds) > LIMITS["items_per_event"]:
        rej.append("лишние предметы отклонены")
    for r_ in [a.strip() for a in re.split(r"[;,]", delta.get("remove", "") or "") if a.strip()]:
        ex = next((i for i in c.inventory if i.name.lower() == r_.lower()), None) or \
             next((i for i in c.inventory if r_.lower() in i.name.lower()), None)
        if not ex:
            rej.append(f"нет предмета «{r_}»"); continue
        ex.qty -= 1
        if ex.qty <= 0: c.inventory.remove(ex)
        msgs.append(f"{c.name}: потерян предмет «{ex.name}»")
    return msgs, rej


# ---------------- inventory helpers ----------------
def find_item(c: Character, name: str) -> Item | None:
    n = norm(name)
    return next((i for i in c.inventory if norm(i.name) == n), None) or \
           next((i for i in c.inventory if n in norm(i.name)), None)


def equip_item(c: Character, name: str) -> str:
    from . import combat as C
    it = find_item(c, name)
    if not it:
        return f"⟦ СИСТЕМА ⟧ Нет предмета «{name}»."
    C.ensure_item_stats(it)
    if it.kind not in ("оружие", "броня"):
        return f"⟦ СИСТЕМА ⟧ «{it.name}» нельзя экипировать."
    for other in c.inventory:
        if other.equipped and other.slot == (it.slot or ("рука" if it.kind == "оружие" else "тело")):
            other.equipped = False
    it.equipped = True
    extra = f", урон {it.damage}" if it.damage else (f", броня +{it.armor}" if it.armor else "")
    return f"⟦ СИСТЕМА ⟧ Экипировано: {it.name}{extra}."


def unequip_item(c: Character, name: str) -> str:
    it = find_item(c, name)
    if not it or not it.equipped:
        return f"⟦ СИСТЕМА ⟧ «{name}» не экипирован."
    it.equipped = False
    return f"⟦ СИСТЕМА ⟧ Снято: {it.name}."


def use_item(scene: Scene, c: Character, name: str) -> str:
    it = find_item(c, name)
    if not it:
        return f"⟦ СИСТЕМА ⟧ Нет предмета «{name}»."
    n = it.name.lower()
    msgs = []
    if any(k in n for k in ("зелье", "лечение", "бандаж", "аптеч", "Potion", "heal")):
        heal = min(10, c.max_hp - c.hp)
        c.hp += heal; msgs.append(f"{c.name}: +{heal} HP ({c.hp}/{c.max_hp})")
    elif any(k in n for k in ("мана", "эссенц")):
        gain = min(8, c.max_mp - c.mp)
        c.mp += gain; msgs.append(f"{c.name}: +{gain} MP")
    else:
        return f"⟦ СИСТЕМА ⟧ «{it.name}» нельзя использовать напрямую (опишите действие рассказчику)."
    it.qty -= 1
    if it.qty <= 0:
        c.inventory.remove(it)
    return "⟦ СИСТЕМА ⟧ " + "; ".join(msgs)


def give_item(scene: Scene, giver: Character, name: str, receiver: Character) -> str:
    it = find_item(giver, name)
    if not it:
        return f"⟦ СИСТЕМА ⟧ У {giver.name} нет «{name}»."
    it.qty -= 1; it.equipped = False
    got = Item(name=it.name, kind=it.kind, damage=it.damage, armor=it.armor, slot=it.slot)
    if it.qty <= 0:
        giver.inventory.remove(it)
    ex = next((i for i in receiver.inventory if i.name == got.name), None)
    if ex: ex.qty += 1
    else: receiver.inventory.append(got)
    return f"⟦ СИСТЕМА ⟧ {giver.name} отдаёт «{got.name}» → {receiver.name}."



# ---------------- 'Система, ...' commands ----------------

def apply_reveal(c: Character, what: str) -> list[str]:
    """Mark reveal flags on NPC. what: kind|attrs|abilities|all."""
    w = norm(what) or "all"
    do_kind = w in ("all", "всё", "все", "full", "kind") or any(x in w for x in ("тип", "вид", "род"))
    do_attrs = w in ("all", "всё", "все", "full", "attrs", "attr", "stats") or any(
        x in w for x in ("характер", "стат", "атрибут", "показат"))
    do_abil = w in ("all", "всё", "все", "full", "abilities", "ability") or any(
        x in w for x in ("способн", "навык", "абил"))
    if w in ("all", "всё", "все", "full", ""):
        do_kind = do_attrs = do_abil = True
    got = []
    if do_kind and not c.reveal.kind:
        c.reveal.kind = True; got.append("вид")
    elif do_kind:
        c.reveal.kind = True
    if do_attrs and not c.reveal.attrs:
        c.reveal.attrs = True; got.append("характеристики")
    elif do_attrs:
        c.reveal.attrs = True
    if do_abil and not c.reveal.abilities:
        c.reveal.abilities = True; got.append("способности")
    elif do_abil:
        c.reveal.abilities = True
    return got


def inspect_target(scene: Scene, rest: str) -> str:
    """Система, осмотри <имя> — soft Воля check; on success reveal kind (and more if roll is strong)."""
    name = re.sub(r"осмотр\w*|изуч\w*|inspect|observe|разгляд\w*|рассмотр\w*|кого|что", "", rest, flags=re.I).strip(" .,")
    if not name:
        return "⟦ СИСТЕМА ⟧ Укажите цель: «Система, осмотри Сильфу»."
    c = resolve_character(scene, name)
    if not c:
        return f"⟦ СИСТЕМА ⟧ Нет персонажа «{name}»."
    if c.is_player:
        return "⟦ СИСТЕМА ⟧ Себя вы и так знаете — смотрите статус."
    # Soft observation check vs DC 12
    r = check(scene, scene.player, "Воля", 12)
    lines = ["### ⟦ СИСТЕМА · ОСМОТР ⟧", r["text"]]
    if not r["success"]:
        lines.append(f"Ты не замечаешь о {c.name} ничего нового.")
        return "\n".join(lines)
    # Success: always kind; good margin also attrs; crit/colossal also abilities
    got = apply_reveal(c, "kind")
    margin = (r.get("total") or r.get("attr_val") or 10) - r["dc"]
    if r.get("crit") or margin >= 5 or r.get("mod", 0) >= 2:
        got += apply_reveal(c, "attrs")
    if r.get("crit") or margin >= 8:
        got += apply_reveal(c, "abilities")
    elif r["success"] and soft_weight(scene.player.attrs.get("Воля", 10), 10) >= 2:
        got += apply_reveal(c, "attrs")
    kind = c.kind or "неизвестно"
    lines.append(f"Наблюдение за {c.name}:")
    lines.append(f"- вид: {kind if c.reveal.kind else 'неизвестно'}")
    if c.reveal.attrs:
        lines.append("- характеристики: " + ", ".join(f"{a} {c.attrs.get(a, 10)}" for a in ATTRS))
    else:
        lines.append("- характеристики: неизвестно")
    if c.reveal.abilities:
        if c.abilities:
            lines.append("- способности: " + ", ".join(
                ("⚡ " if a.kind == "active" else "◇ ") + a.name for a in c.abilities))
        else:
            lines.append("- способности: не видно особых")
    else:
        lines.append("- способности: неизвестно")
    if got:
        lines.append("Открыто: " + ", ".join(dict.fromkeys(got)) + ".")
    return "\n".join(lines)


def public_char_dict(c: Character, *, in_combat: bool = False) -> dict:
    """Client-facing character view: hide unrevealed NPC stats."""
    if c.is_player or c.side == "party":
        return c.model_dump()
    d = {
        "id": c.id, "name": c.name, "is_player": False,
        "kind": c.kind if c.reveal.kind else "",
        "kind_known": c.reveal.kind,
        "description": c.description if c.reveal.kind else "",
        "level": c.level if c.reveal.attrs else 0,
        "hp": c.hp if (c.reveal.attrs or in_combat) else None,
        "max_hp": c.max_hp if (c.reveal.attrs or in_combat) else None,
        "mp": c.mp if c.reveal.attrs else None,
        "max_mp": c.max_mp if c.reveal.attrs else None,
        "attrs": dict(c.attrs) if c.reveal.attrs else {},
        "abilities": [a.model_dump() for a in c.abilities] if c.reveal.abilities else [],
        "reveal": c.reveal.model_dump(),
        "conditions": [x.model_dump() for x in c.conditions] if (c.reveal.attrs or in_combat) else [],
        "side": c.side,
    }
    return d


def parse_system_command(text: str) -> tuple[str, str] | None:
    t = (text or "").strip().lower().lstrip("*\"«„“(_[ ")
    # Soft meta: open ability list without «Система,» prefix.
    # Never steal Director chat (even if the message mentions abilities).
    meta = re.sub(r"^\*+|\*+$", "", t).strip()
    if re.search(r"(директор|director|режиссёр|режиссер)", meta):
        pass  # fall through to Система,/director parsing — or None for free chat
    elif re.search(
        r"(очк\w*\s+способност|способност\w*\s+очк|список\s+навык|мои\s+(навыки|способности)|"
        r"куда\s+(можно\s+)?(вложить|потратить|распредел).{0,48}(способност|навык|очк)|"
        r"открой.{0,24}(навыки|способности)|смотрю.{0,40}(способност|навык|очк))",
        meta,
    ):
        if not re.search(r"\b(сильф|враг|гоблин|твар)\w*", meta):
            return "abilities", meta
    rest = None; p = ""
    for pref in ("система", "system", "/"):
        if t.startswith(pref):
            rest = t[len(pref):]; p = pref; break
    if rest is None or (p != "/" and rest and rest[0] not in " ,.:;!?-—*»\""):
        return None
    r = re.sub(r"[,:.!?*»\"]", " ", rest).strip()
    if r == "" or any(k in r for k in ("стат", "status", "характ", "stats", "показат")): return "status", r
    if any(k in r for k in ("директор", "director", "режиссёр", "режиссер")): return "director", r
    if any(k in r for k in ("провер", "аудит", "audit", "карточки")) and any(
            k in r for k in ("карточки", "карт", "провер", "аудит", "audit", "несостык")):
        return "director_audit", r
    if re.search(r"\bкниг", r) or re.fullmatch(
            r"((покажи|открой)\s+)?(мои\s+)?(пути(\s+(магии|развития))?|магия|школы магии|магические пути|следы|отрасли|paths)", r):
        return "paths", r        # the Book of paths: computed by code, no LLM
    if "?" in rest or re.match(r"(как|что|почему|зачем|сколько|можно|могу|какие|какой|какая|где|когда|чем|кто|"
                               r"расскажи|объясни|подскажи|хочу|нужно|требуется)\b", r) or re.match(r"покажи\b.*\b(пут|отрасл|ступен|ранг)", r):
        return "ask", r          # a question, even about opening a path, is for the System agent
    if re.search(r"\b(пут[ьиюё]|ступен\w*|ранг\w*|отрасл\w*)\b", r) and re.search(r"\b(откр\w*|изуч\w*|освой\w*|прокач\w*|развей|развить)\b", r):
        return "unlock_path", r
    if re.match(r"(возьм|выуч|освой|прокач)\w*\s+\S", r):
        return "unlock_path", r  # «возьми Огненную стрелу» — the ability of the next rank/tier of some path
    if re.search(r"маги|\bпут[ьиюё]\b|школ|заклин|стихи", r):
        return "ask", r
    if any(k in r for k in ("отпусти", "исключи", "прогони", "dismiss")): return "dismiss", r
    if any(k in r for k in ("отряд", "групп", "party", "спутник")): return "party", r
    if any(k in r for k in ("осмотр", "изуч", "inspect", "observe", "разгляд", "рассмотр")): return "inspect", r
    if any(k in r for k in ("навык", "скил", "skill", "умени", "способн", "абил")) and "распредел" not in r and "влож" not in r:
        return "abilities", r
    if any(k in r for k in ("инвент", "invent", "сумк", "предмет", "вещи")): return "inventory", r
    if any(k in r for k in ("экип", "надень", "надет", "equip", "вооруж")): return "equip", r
    if any(k in r for k in ("сними", "снят", "unequip")): return "unequip", r
    if any(k in r for k in ("используй", "выпей", "примени", "use")): return "use", r
    if re.search(r"(?<![а-яё])(дай|отдай|передай)(?![а-яё])|\bgive\b", r): return "give", r   # not «выдай», «продай»
    if any(k in r for k in ("бой", "combat", "иниц")): return "combat", r
    if any(k in r for k in ("состоян", "эффект", "condition", "бафф", "дебафф")): return "conditions", r
    if any(k in r for k in ("распредел", "вклад", "очк", "assign")): return "assign", r
    if r and not re.fullmatch(r"(помощь|help|справка|команды)", r):
        return "ask", r          # anything the parser does not know is a question for the System agent
    return "help", r


def _bar(cur, mx, n=10):
    f = 0 if mx <= 0 else round(n * cur / mx)
    return "█" * f + "░" * (n - f)


EFFECT_RU = {"damage": "урон", "heal": "лечение", "shield": "щит", "buff": "усиление", "debuff": "ослабление",
             "control": "оглушение", "utility": "особое", "passive": "пассивно"}
BONUS_RU = {"hp": "макс. HP", "mp": "макс. MP", "ac": "КБ", "melee": "урон в ближнем бою", "ranged": "урон дальним оружием",
            "spell": "сила заклинаний", "heal": "сила лечения", "init": "инициатива"}


def ability_stats(a: Ability) -> str:
    """Short mechanical summary: «урон 2d6 +Разум · 3 MP», «КБ +1»."""
    if a.kind == "passive":
        return ", ".join(f"{BONUS_RU.get(k, k)} {v:+d}" for k, v in (a.bonus or {}).items() if v)
    bits = []
    if a.effect and a.effect not in ("utility", "passive"):
        pw = f"{a.power} ход." if a.effect == "control" else (f"+{a.power}" if a.effect in ("buff", "debuff") else a.power)
        bits.append(f"{EFFECT_RU[a.effect]} {pw}".strip() + (f" +{a.scale}" if a.scale else ""))
    elif a.effect == "utility":
        bits.append("особое")
    if a.mp:
        bits.append(f"{a.mp} MP")
    elif a.cost:
        bits.append(a.cost)
    return " · ".join(bits)


def status_text(c: Character) -> str:
    from . import combat as C
    lines = [f"#### {c.name} — уровень {c.level}",
             f"❤️ HP {c.hp}/{c.max_hp} {_bar(c.hp, c.max_hp)}", f"🔷 MP {c.mp}/{c.max_mp} {_bar(c.mp, c.max_mp)}",
             f"✨ EXP {c.exp}/{c.exp_next} · КБ {C.ac(c)}",
             " · ".join(f"{a} {v} ({amp(v):+d})" for a, v in c.attrs.items())]
    if c.conditions:
        lines.append("Состояния: " + ", ".join(f"{x.name}({x.turns_left})" for x in c.conditions))
    if c.attr_points or c.skill_points:
        lines.append(f"Очки: атрибуты {c.attr_points}, способности {c.ability_points}")
    eq = [i for i in c.inventory if i.equipped]
    if eq:
        lines.append("Экипировка: " + ", ".join(i.name for i in eq))
    return "\n".join(lines)


def run_system_command(scene: Scene, cmd: str, rest: str) -> str:
    from . import combat as C
    p = scene.player
    if cmd in ("status", "abilities", "assign", "conditions", "unlock_path", "paths"):
        p, rest = pick_member(scene, rest)   # progression of companions is the hero's call too
    if cmd == "paths":
        from . import magic as G
        return G.book_text(scene, p)
    if cmd == "unlock_path":
        from . import magic as G
        name = re.sub(r"откр\w*|изуч\w*|освой\w*|прокач\w*|разв\w*|возьм\w*|выуч\w*|следующ\w*|ступен\w*|ранг\w*|"
                      r"отрасл\w*|\bпут[ьиюё]\b|\bдля\b", "", rest, flags=re.I).strip(" .,;:—-«»")
        ok, msg = G.unlock_tier(scene, p, name)
        return "### ⟦ СИСТЕМА · ПУТЬ ⟧\n" + ("" if ok else "Отказано. ") + msg
    if cmd == "party":
        lines = [f"### ⟦ СИСТЕМА · ОТРЯД {len(party_ids(scene))}/{config.PARTY_MAX} ⟧"]
        for cid in party_ids(scene):
            c = scene.characters[cid]
            mult = catchup_mult(scene, c)
            lines.append(f"- {c.name}{' (герой)' if c.is_player else ''}: ур. {c.level}, HP {c.hp}/{c.max_hp}, "
                         f"EXP {c.exp}/{c.exp_next}" + (f", опыт ×{mult:g}" if mult > 1 else "")
                         + (f", свободные очки: атрибуты {c.attr_points}, способности {c.ability_points}"
                            if c.attr_points or c.ability_points else ""))
        if len(party_ids(scene)) == 1:
            lines.append("Пока вы один. Предложите кому-нибудь пойти с вами — согласие решает сам персонаж.")
        return chr(10).join(lines)
    if cmd == "dismiss":
        name = re.sub(r"отпусти\w*|исключи\w*|прогони\w*|из\s+отряда|dismiss", "", rest, flags=re.I).strip(" .,;:—-")
        c = resolve_character(scene, name) if name else None
        ok, msg = leave_party(scene, c)
        return "⟦ ОТРЯД ⟧ " + msg
    if cmd == "status":
        others = [scene.characters[i] for i in scene.present if i != p.id]
        if not p.is_player:
            return "### ⟦ СИСТЕМА · СТАТУС ⟧\n" + status_text(p)
        s = "### ⟦ СИСТЕМА · СТАТУС ⟧\n" + status_text(p)
        s += f"\nРежим правил: {scene.rule_mode}. Ход: {scene.turn}."
        if scene.combat.active:
            s += f" Бой: раунд {scene.combat.round}."
        if others:
            s += "\nРядом: " + ", ".join(f"{o.name} (HP {o.hp}/{o.max_hp})" for o in others)
        return s
    if cmd == "abilities":
        lines = ["### ⟦ СИСТЕМА · СПОСОБНОСТИ ⟧" + ("" if p.is_player else f" {p.name}")]
        if not p.abilities:
            lines.append("Открытых способностей пока нет.")
        for a in [a for a in p.abilities if a.kind == "active"] + [a for a in p.abilities if a.kind != "active"]:
            lines.append(f"- {'⚡' if a.kind == 'active' else '◇'} {a.name}" + (f" ({ability_stats(a)})" if ability_stats(a) else "")
                         + (f" — {a.description}" if a.description else ""))
        lines.append(f"Очки способностей: **{p.ability_points}** — тратятся в Книге путей («Система, пути»).")
        return chr(10).join(lines)
    if cmd == "inspect":
        return inspect_target(scene, rest)
    if cmd == "inventory":
        if not p.inventory:
            return "### ⟦ СИСТЕМА · ИНВЕНТАРЬ ⟧\nПусто."
        lines = []
        for i in p.inventory:
            mark = " [экип.]" if i.equipped else ""
            extra = f", урон {i.damage}" if i.damage else (f", броня +{i.armor}" if i.armor else "")
            lines.append(f"- {i.name}" + (f" ×{i.qty}" if i.qty > 1 else "") + mark + extra)
        return "### ⟦ СИСТЕМА · ИНВЕНТАРЬ ⟧\n" + "\n".join(lines)
    if cmd == "equip":
        name = re.sub(r"экип\w*|надень|надет\w*|equip|вооруж\w*", "", rest).strip()
        return equip_item(p, name) if name else "⟦ СИСТЕМА ⟧ Укажите предмет: «Система, экипируй Короткий меч»."
    if cmd == "unequip":
        name = re.sub(r"сними|снят\w*|unequip", "", rest).strip()
        return unequip_item(p, name) if name else "⟦ СИСТЕМА ⟧ Укажите предмет."
    if cmd == "use":
        name = re.sub(r"используй|выпей|примени|use", "", rest).strip()
        return use_item(scene, p, name) if name else "⟦ СИСТЕМА ⟧ Укажите предмет."
    if cmd == "give":
        m = re.search(r"(?:дай|отдай|передай|give)\s+(.+?)\s+(?:персонажу\s+|герою\s+|npc\s+)?([А-Яа-яЁёA-Za-z][\w\- ]+)$", rest)
        if not m:
            return "⟦ СИСТЕМА ⟧ Формат: «Система, дай Нож Сильфе»."
        recv = resolve_character(scene, m.group(2).strip())
        if not recv:
            return f"⟦ СИСТЕМА ⟧ Нет персонажа «{m.group(2)}»."
        return give_item(scene, p, m.group(1).strip(), recv)
    if cmd == "combat":
        return C.combat_status(scene)
    if cmd == "conditions":
        if not p.conditions:
            return "### ⟦ СИСТЕМА · СОСТОЯНИЯ ⟧\nНет активных состояний."
        return "### ⟦ СИСТЕМА · СОСТОЯНИЯ ⟧\n" + "\n".join(
            f"- {x.name}: ещё {x.turns_left} ход." + (f", сила {x.potency}" if x.potency else "") for x in p.conditions)
    if cmd == "assign":
        m = re.search(r"([а-яёa-z]+)\s*\+?\s*(\d+)?", re.sub(r"распредел\w*|очк\w*|вложи|assign", "", rest))
        attr = resolve_attr(m.group(1)) if m else None
        n = int(m.group(2) or 1) if m else 1
        if not attr:
            return "⟦ СИСТЕМА ⟧ Укажите атрибут: " + ", ".join(ATTRS)
        if n > p.attr_points:
            return f"⟦ СИСТЕМА ⟧ Недостаточно очков у {p.name}: есть {p.attr_points}."
        p.attr_points -= n; p.attrs[attr] += n
        extra = sync_derived(p)
        return (f"⟦ СИСТЕМА ⟧ {'' if p.is_player else p.name + ': '}{attr} {p.attrs[attr] - n}→{p.attrs[attr]}. "
                f"Осталось очков: {p.attr_points}." + (" " + "; ".join(extra) + "." if extra else ""))
    return ("### ⟦ СИСТЕМА · ПОМОЩЬ ⟧\n"
            "- «Система, статус» / способности / инвентарь / состояния / бой\n"
            "- «Система, пути» — Книга путей; «Система, открой путь Воина» / «возьми Огненную стрелу»\n"
            "- «Система, осмотри Имя» — разведка противника\n"
            "- «Система, директор …» / «Система, проверь карточки»\n"
            "- «Система, экипируй Короткий меч» / «сними Щит»\n"
            "- «Система, используй Зелье лечения» / «дай Нож Сильфе»\n"
            "- «Система, распредели Сила +1»\n"
            "- «Система, помощь»\n"
            "Кнопка «Продолжить» — мир действует без вашего хода.")


def state_line(scene: Scene) -> str:
    from . import combat as C
    p = scene.player
    present = [scene.characters[i].name for i in scene.present if i != p.id]
    inv = ", ".join(("*" + i.name + "*" if i.equipped else i.name) for i in p.inventory[:8]) or "пусто"
    cond = (", ".join(x.name for x in p.conditions)) or "нет"
    combat = f" Бой р.{scene.combat.round}." if scene.combat.active else ""
    party = [scene.characters[i] for i in scene.party if i in scene.characters]
    combat += (" Отряд героя: " + ", ".join(f"{c.name} (ур. {c.level}, HP {c.hp}/{c.max_hp})" for c in party) + "."
               if party else "")
    # learned abilities live here, not in the cached card: the narrator needs exact names for <cast>
    acts = [f"{c.name}: " + ", ".join(a.name for a in c.abilities if a.kind == "active")
            for c in [p] + party if any(a.kind == "active" for a in c.abilities)]
    skills = (" Активные способности — " + "; ".join(acts) + ".") if acts else ""
    return (f"[СОСТОЯНИЕ] Ход {scene.turn}. {p.name}: ур. {p.level}, HP {p.hp}/{p.max_hp}, MP {p.mp}/{p.max_mp}, "
            f"EXP {p.exp}/{p.exp_next}, КБ {C.ac(p)}. Инвентарь: {inv}. Состояния: {cond}.{combat}{skills} "
            f"В сцене: {', '.join(present) or 'никого'}.")
