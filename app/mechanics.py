"""Deterministic mechanics: soft attributes, named abilities, checks, deltas, 'Система' commands."""
import difflib, math, random, re
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


def grant_exp(scene: Scene, c: Character, amount: int, msgs: list):
    if amount <= 0:
        return
    c.exp += amount
    msgs.append(f"{c.name}: +{amount} EXP ({c.exp}/{c.exp_next})")
    while c.exp >= c.exp_next:
        c.exp -= c.exp_next
        c.level += 1
        c.exp_next = int(c.exp_next * 1.5)
        c.max_hp += 5; c.hp = c.max_hp
        c.max_mp += 3; c.mp = c.max_mp
        c.attr_points += 2; c.ability_points += 1; c.skill_points = c.ability_points
        msgs.append(f"⟦ УРОВЕНЬ ПОВЫШЕН ⟧ {c.name}: уровень {c.level}! HP/MP восстановлены, +2 очка атрибутов, +1 очко способностей "
                    f"(«Система, распредели Сила +1»).")


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



# ---- ability unlock offers (spend ability_points) ----
ABILITY_CATALOG = [
    {"name": "Удар наёмника", "kind": "active", "description": "Точный рубящий удар: в бою опишите применение — бонус к следующему удару оружием.", "cost": "", "cooldown": "1/сцена"},
    {"name": "Железная стойка", "kind": "passive", "description": "Привычка держать строй: легче выдерживать боль и усталость в долгих стычках.", "cost": "", "cooldown": ""},
    {"name": "Глаз следопыта", "kind": "passive", "description": "Замечаете следы, дым, ловушки и чужое присутствие раньше обычного.", "cost": "", "cooldown": ""},
    {"name": "Рывок", "kind": "active", "description": "Короткий ускоренный рывок к цели или в укрытие.", "cost": "", "cooldown": "1/сцена"},
    {"name": "Твёрдый дух", "kind": "passive", "description": "Сложнее сломать волю страхом, болью или чужой магией давления.", "cost": "", "cooldown": ""},
    {"name": "Чтение потока", "kind": "active", "description": "На миг «видите» структуру маны/Системы вокруг — подсказка от рассказчика.", "cost": "2 MP", "cooldown": ""},
    {"name": "Грозное присутствие", "kind": "active", "description": "Короткая угроза или взгляд, чтобы сбить с толку слабого противника.", "cost": "", "cooldown": "1/сцена"},
    {"name": "Полевая медицина", "kind": "active", "description": "Быстрая перевязка: небольшое исцеление себе или союзнику вне тяжёлого боя.", "cost": "", "cooldown": "1/сцена"},
    {"name": "Ночное зрение", "kind": "passive", "description": "В полумраке ориентируетесь почти как днём.", "cost": "", "cooldown": ""},
    {"name": "Контрудар", "kind": "active", "description": "После чужой атаки вблизи — ответный удар тем же ходом (по решению механики/рассказчика).", "cost": "", "cooldown": "1/сцена"},
]


def ability_offers(scene: Scene, n: int = 3) -> list[dict]:
    """Pick unlockable abilities the player does not already have."""
    p = scene.player
    have = {norm(a.name) for a in p.abilities}
    pool = [x for x in ABILITY_CATALOG if norm(x["name"]) not in have]
    if not pool:
        return []
    rng = random.Random(scene.rng_seed + scene.turn * 17 + p.ability_points * 3 + len(p.abilities) * 11)
    rng.shuffle(pool)
    attrs = p.attrs or {}
    prefer = []
    if attrs.get("Разум", 10) >= 15:
        prefer += ["Чтение потока", "Глаз следопыта"]
    if attrs.get("Сила", 10) >= 13:
        prefer += ["Удар наёмника", "Контрудар"]
    if attrs.get("Ловкость", 10) >= 13:
        prefer += ["Рывок", "Глаз следопыта"]
    if attrs.get("Воля", 10) >= 13:
        prefer += ["Твёрдый дух", "Грозное присутствие"]
    ordered = []
    for name in prefer:
        for x in pool:
            if x["name"] == name and x not in ordered:
                ordered.append(x)
    for x in pool:
        if x not in ordered:
            ordered.append(x)
    return ordered[:n]


def take_ability(scene: Scene, name: str) -> str:
    p = scene.player
    if p.ability_points <= 0:
        return "⟦ СИСТЕМА ⟧ Нет очков способностей. Они приходят с уровнем."
    name = re.sub(r"\s+", " ", (name or "").strip())
    if not name:
        return "⟦ СИСТЕМА ⟧ Укажите способность: «Система, возьми Удар наёмника»."
    pick = None
    nl = norm(name)
    for x in ability_offers(scene, 8) + ABILITY_CATALOG:
        xn = norm(x["name"])
        if xn == nl or nl in xn or xn in nl:
            pick = x
            break
    if not pick:
        names = [x["name"] for x in ABILITY_CATALOG]
        m = difflib.get_close_matches(name, names, n=1, cutoff=0.6)
        if m:
            for x in ABILITY_CATALOG:
                if x["name"] == m[0]:
                    pick = x
                    break
    if not pick:
        return f"⟦ СИСТЕМА ⟧ Нет способности «{name}». Откройте список: «Система, способности»."
    if any(norm(a.name) == norm(pick["name"]) for a in p.abilities):
        return f"⟦ СИСТЕМА ⟧ «{pick['name']}» уже есть."
    p.ability_points -= 1
    p.skill_points = p.ability_points
    p.abilities.append(Ability(
        name=pick["name"], kind=pick.get("kind") or "passive",
        description=pick.get("description") or "", cost=pick.get("cost") or "",
        cooldown=pick.get("cooldown") or "",
    ))
    kind_ru = "активная" if pick.get("kind") == "active" else "пассивная"
    return (
        f"### ⟦ СИСТЕМА · СПОСОБНОСТЬ ⟧\n"
        f"Открыто: **{pick['name']}** ({kind_ru}). "
        f"Очков способностей осталось: {p.ability_points}."
    )


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
    if c.is_player:
        d = c.model_dump()
        return d
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
    }
    return d


def parse_system_command(text: str) -> tuple[str, str] | None:
    t = (text or "").strip().lower().lstrip("*\"«„“(_[ ")
    # Soft meta: open ability list without «Система,» prefix
    meta = re.sub(r"^\*+|\*+$", "", t).strip()
    if re.search(
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
    if any(k in r for k in ("осмотр", "изуч", "inspect", "observe", "разгляд", "рассмотр")): return "inspect", r
    if any(k in r for k in ("возьм", "выуч", "открой способ", "взять способ", "выбрать способ", "unlock", "learn")):
        return "take_ability", r
    if any(k in r for k in ("навык", "скил", "skill", "умени", "способн", "абил")) and "распредел" not in r and "влож" not in r:
        return "abilities", r
    if any(k in r for k in ("инвент", "invent", "сумк", "предмет", "вещи")): return "inventory", r
    if any(k in r for k in ("экип", "надень", "надет", "equip", "вооруж")): return "equip", r
    if any(k in r for k in ("сними", "снят", "unequip")): return "unequip", r
    if any(k in r for k in ("используй", "выпей", "примени", "use")): return "use", r
    if any(k in r for k in ("дай", "отдай", "передай", "give")): return "give", r
    if any(k in r for k in ("бой", "combat", "иниц")): return "combat", r
    if any(k in r for k in ("состоян", "эффект", "condition", "бафф", "дебафф")): return "conditions", r
    if any(k in r for k in ("распредел", "вклад", "очк", "assign")): return "assign", r
    return "help", r


def _bar(cur, mx, n=10):
    f = 0 if mx <= 0 else round(n * cur / mx)
    return "█" * f + "░" * (n - f)


def status_text(c: Character) -> str:
    from . import combat as C
    lines = [f"#### {c.name} — уровень {c.level}",
             f"❤️ HP {c.hp}/{c.max_hp} {_bar(c.hp, c.max_hp)}", f"🔷 MP {c.mp}/{c.max_mp} {_bar(c.mp, c.max_mp)}",
             f"✨ EXP {c.exp}/{c.exp_next} · КБ {C.ac(c)}",
             " · ".join(f"{a} {v} ({mod(v):+d})" for a, v in c.attrs.items())]
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
    if cmd == "status":
        others = [scene.characters[i] for i in scene.present if i != p.id]
        s = "### ⟦ СИСТЕМА · СТАТУС ⟧\n" + status_text(p)
        s += f"\nРежим правил: {scene.rule_mode}. Ход: {scene.turn}."
        if scene.combat.active:
            s += f" Бой: раунд {scene.combat.round}."
        if others:
            s += "\nРядом: " + ", ".join(f"{o.name} (HP {o.hp}/{o.max_hp})" for o in others)
        return s
    if cmd == "abilities":
        lines = ["### ⟦ СИСТЕМА · СПОСОБНОСТИ ⟧"]
        lines.append(f"Очки способностей: **{p.ability_points}** · очки атрибутов: {p.attr_points}")
        if not p.abilities:
            lines.append("Сейчас открытых способностей нет (это не «ближний бой» — только особые умения).")
        else:
            act = [a for a in p.abilities if a.kind == "active"]
            pas = [a for a in p.abilities if a.kind != "active"]
            if act:
                lines.append("Активные:")
                for a in act:
                    extra = ", ".join(x for x in (a.cost and f"цена {a.cost}", a.cooldown and f"кд {a.cooldown}") if x)
                    lines.append(f"- {a.name}" + (f" ({extra})" if extra else "") + (f" — {a.description}" if a.description else ""))
            if pas:
                lines.append("Пассивные:")
                for a in pas:
                    lines.append(f"- {a.name}" + (f" — {a.description}" if a.description else ""))
        if p.ability_points > 0:
            offers = ability_offers(scene, 3)
            lines.append("")
            lines.append("Можно открыть (1 очко за способность):")
            for i, x in enumerate(offers, 1):
                kd = "акт." if x.get("kind") == "active" else "пасс."
                lines.append(f"{i}. **{x['name']}** ({kd}) — {x.get('description') or '—'}")
            tip = offers[0]["name"] if offers else "Название"
            lines.append(f"Взять: «Система, возьми {tip}».")
        else:
            lines.append("Очков способностей нет — они приходят с новым уровнем.")
        return chr(10).join(lines)
    if cmd == "take_ability":
        name = re.sub(
            r"возьм\w*|выуч\w*|открой|открыть|взять|выбрать|способност\w*|навык\w*|unlock|learn",
            "", rest, flags=re.I,
        ).strip(" .,;:—-")
        return take_ability(scene, name)
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
            return f"⟦ СИСТЕМА ⟧ Недостаточно очков: есть {p.attr_points}."
        p.attr_points -= n; p.attrs[attr] += n
        return f"⟦ СИСТЕМА ⟧ {attr} {p.attrs[attr] - n}→{p.attrs[attr]}. Осталось очков: {p.attr_points}."
    if cmd == "train":
        return "⟦ СИСТЕМА ⟧ Очки навыков в «ближний бой» не вкладываются. Смотрите «Система, способности» и «Система, возьми …»."
    return ("### ⟦ СИСТЕМА · ПОМОЩЬ ⟧\n"
            "- «Система, статус» / способности / «возьми …» / инвентарь / состояния / бой\n"
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
    return (f"[СОСТОЯНИЕ] Ход {scene.turn}. {p.name}: ур. {p.level}, HP {p.hp}/{p.max_hp}, MP {p.mp}/{p.max_mp}, "
            f"EXP {p.exp}/{p.exp_next}, КБ {C.ac(p)}. Инвентарь: {inv}. Состояния: {cond}.{combat} "
            f"В сцене: {', '.join(present) or 'никого'}.")
