"""Deterministic mechanics: skills, checks, clamped deltas, levels, 'Система, ...' commands.
Concepts ported from the Talemate scene module 'дракония-малфой' (system-handle-input,
system-update-stats, system-auto-update)."""
import difflib, math, random, re
from .models import ATTRS, Character, Item, Scene

SKILLS = {  # Russian skill -> attribute
    "Атлетика": "Сила", "Ближний бой": "Сила", "Акробатика": "Ловкость", "Скрытность": "Ловкость",
    "Ловкость рук": "Ловкость", "Взлом": "Ловкость", "Стрельба": "Ловкость", "Выносливость": "Телосложение",
    "Магия": "Интеллект", "Знания": "Интеллект", "Расследование": "Интеллект", "Восприятие": "Мудрость",
    "Проницательность": "Мудрость", "Выживание": "Мудрость", "Медицина": "Мудрость", "Убеждение": "Харизма",
    "Обман": "Харизма", "Запугивание": "Харизма", "Выступление": "Харизма",
}
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
}
ATTR_ALIASES = {"сил": "Сила", "лов": "Ловкость", "тел": "Телосложение", "вын": "Телосложение", "инт": "Интеллект",
                "муд": "Мудрость", "хар": "Харизма", "str": "Сила", "dex": "Ловкость", "agi": "Ловкость", "con": "Телосложение",
                "vit": "Телосложение", "int": "Интеллект", "wis": "Мудрость", "cha": "Харизма"}
FORBIDDEN_ITEM_WORDS = ["легендар", "божествен", "артефакт", "бессмерт", "всемогущ", "legendary", "artifact", "divine"]
LIMITS = {"hp_frac": 0.3, "mp_frac": 0.5, "exp_event": 50, "exp_turn": 100, "items_per_event": 2, "item_len": 40}

TRANSLIT = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                    ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s", "t", "u",
                     "f", "h", "ts", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya"]))


def translit(s: str) -> str:
    return "".join(TRANSLIT.get(c, c) for c in s.lower())


def norm(s: str) -> str:
    return re.sub(r"[\s_\-]+", " ", (s or "").strip().lower().strip("\"'«»"))


def resolve_skill(name: str) -> str | None:
    n = norm(name)
    for s in SKILLS:
        if s.lower() == n:
            return s
    if n in SKILL_ALIASES:
        return SKILL_ALIASES[n]
    if n.replace(" ", "_") in SKILL_ALIASES:
        return SKILL_ALIASES[n.replace(" ", "_")]
    pool = {s.lower(): s for s in SKILLS} | SKILL_ALIASES
    m = difflib.get_close_matches(n, list(pool), n=1, cutoff=0.6)
    if m:
        v = pool[m[0]]
        return v
    return None


def resolve_attr(name: str) -> str | None:
    n = norm(name)
    for a in ATTRS:
        if a.lower() == n:
            return a
    for k, v in ATTR_ALIASES.items():
        if n.startswith(k):
            return v
    m = difflib.get_close_matches(n, [a.lower() for a in ATTRS], n=1, cutoff=0.6)
    return next((a for a in ATTRS if m and a.lower() == m[0]), None)


def resolve_character(scene: Scene, name: str, only_present: bool = False) -> Character | None:
    """Alias map + transliteration + fuzzy matching of a model-provided name."""
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
    return (v - 10) // 2


def rng(scene: Scene) -> random.Random:
    r = random.Random(f"{scene.rng_seed}:{scene.rng_calls}")
    scene.rng_calls += 1
    return r


def check(scene: Scene, who: Character, skill_raw: str, dc_raw) -> dict:
    """d20 mode: d20 + mod(attr) + skill vs DC, nat 20 crit / nat 1 fumble.
    'система' mode: no dice, 10 + mod + 2*skill vs DC."""
    skill = resolve_skill(skill_raw) or "Восприятие"
    try:
        dc = int(re.sub(r"[^\d-]", "", str(dc_raw)) or 12)
    except ValueError:
        dc = 12
    dc = max(5, min(30, dc))
    attr = SKILLS[skill]
    m = mod(who.attrs.get(attr, 10)); sk = who.skills.get(skill, 0)
    res = {"who": who.name, "skill": skill, "attr": attr, "mod": m, "skill_lvl": sk, "dc": dc, "mode": scene.rule_mode}
    if scene.rule_mode == "d20":
        d = rng(scene).randint(1, 20)
        total = d + m + sk
        ok = d == 20 or (d != 1 and total >= dc)
        res.update(die=d, total=total, success=ok, crit=d == 20, fumble=d == 1)
        tag = " (критический успех!)" if d == 20 else " (критический провал!)" if d == 1 else ""
        res["text"] = (f"Проверка «{skill}» ({who.name}): 1d20({d}) {m:+d} {('+ навык ' + str(sk)) if sk else ''}= {total} "
                       f"против {dc} — {'успех' if ok else 'провал'}{tag}.").replace("  ", " ")
    else:
        total = 10 + m + 2 * sk
        ok = total >= dc
        res.update(die=None, total=total, success=ok, crit=False, fumble=False)
        res["text"] = f"Проверка «{skill}» ({who.name}, без броска): {attr} {m:+d}, навык {sk} → {total} против {dc} — {'успех' if ok else 'провал'}."
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
        c.attr_points += 2
        msgs.append(f"⟦ УРОВЕНЬ ПОВЫШЕН ⟧ {c.name}: уровень {c.level}! HP/MP восстановлены, +2 очка атрибутов "
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
        else: c.inventory.append(Item(name=a))
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


# ---------------- 'Система, ...' commands ----------------
def parse_system_command(text: str) -> tuple[str, str] | None:
    t = (text or "").strip().lower().lstrip("*\"«„“(_[ ")
    rest = None
    for p in ("система", "system", "/"):
        if t.startswith(p):
            rest = t[len(p):]; break
    if rest is None or (p != "/" and rest and rest[0] not in " ,.:;!?-—*»\""):
        return None
    r = re.sub(r"[,:.!?*»\"]", " ", rest).strip()
    if r == "" or any(k in r for k in ("стат", "status", "характ", "stats", "показат")): return "status", r
    if any(k in r for k in ("навык", "скил", "skill", "умени", "способн")): return "skills", r
    if any(k in r for k in ("инвент", "invent", "сумк", "предмет", "вещи")): return "inventory", r
    if any(k in r for k in ("распредел", "вклад", "очк", "assign")): return "assign", r
    return "help", r


def _bar(cur, mx, n=10):
    f = 0 if mx <= 0 else round(n * cur / mx)
    return "█" * f + "░" * (n - f)


def status_text(c: Character) -> str:
    lines = [f"#### {c.name} — уровень {c.level}",
             f"❤️ HP {c.hp}/{c.max_hp} {_bar(c.hp, c.max_hp)}", f"🔷 MP {c.mp}/{c.max_mp} {_bar(c.mp, c.max_mp)}",
             f"✨ EXP {c.exp}/{c.exp_next}",
             " · ".join(f"{a} {v} ({mod(v):+d})" for a, v in c.attrs.items())]
    if c.attr_points:
        lines.append(f"Свободные очки атрибутов: {c.attr_points}")
    return "\n".join(lines)


def run_system_command(scene: Scene, cmd: str, rest: str) -> str:
    p = scene.player
    if cmd == "status":
        others = [scene.characters[i] for i in scene.present if i != p.id]
        s = "### ⟦ СИСТЕМА · СТАТУС ⟧\n" + status_text(p)
        s += f"\nРежим правил: {scene.rule_mode}. Ход: {scene.turn}."
        if others:
            s += "\nРядом: " + ", ".join(f"{o.name} (HP {o.hp}/{o.max_hp})" for o in others)
        return s
    if cmd == "skills":
        if not p.skills:
            return "### ⟦ СИСТЕМА · НАВЫКИ ⟧\nНавыков пока нет. Проверки идут по атрибутам."
        return "### ⟦ СИСТЕМА · НАВЫКИ ⟧\n" + "\n".join(
            f"- {k} ({SKILLS.get(k, '?')}): уровень {v}" for k, v in sorted(p.skills.items()))
    if cmd == "inventory":
        if not p.inventory:
            return "### ⟦ СИСТЕМА · ИНВЕНТАРЬ ⟧\nПусто."
        return "### ⟦ СИСТЕМА · ИНВЕНТАРЬ ⟧\n" + "\n".join(
            f"- {i.name}" + (f" ×{i.qty}" if i.qty > 1 else "") for i in p.inventory)
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
    return ("### ⟦ СИСТЕМА · ПОМОЩЬ ⟧\n- «Система, статус» — показатели\n- «Система, навыки»\n- «Система, инвентарь»\n"
            "- «Система, распредели Сила +1» — потратить очки атрибутов\n- «Система, помощь»\n"
            "Кнопка «Продолжить» — мир действует без вашего хода.")


def state_line(scene: Scene) -> str:
    p = scene.player
    present = [scene.characters[i].name for i in scene.present if i != p.id]
    inv = ", ".join(i.name for i in p.inventory[:8]) or "пусто"
    return (f"[СОСТОЯНИЕ] Ход {scene.turn}. {p.name}: ур. {p.level}, HP {p.hp}/{p.max_hp}, MP {p.mp}/{p.max_mp}, "
            f"EXP {p.exp}/{p.exp_next}. Инвентарь: {inv}. В сцене: {', '.join(present) or 'никого'}.")
