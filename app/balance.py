"""Every number of the game economy in one place. Tune the game here.

Damage, healing and shields are plain numbers: «Меч — 9 урона», «Огненный шар — 22 урона». Attributes amplify them:
  Сила         — физический урон в ближнем бою (×), блок
  Ловкость     — точность, уклонение, урон стрелкового оружия (×), инициатива
  Выносливость — здоровье (+4 HP за пункт выше 10)
  Разум        — учёная магия (×), запас маны (+3 MP за пункт)
  Воля         — лечение, обереги, вера (×), сопротивление чарам
  Влияние      — голос: усиление союзников, социальные проверки
The multiplier is +5% per attribute point above 10 (−5% below), from ×0,7 to ×2. Armour takes a flat amount off every
physical blow. Every level adds 10% to what a character deals (damage, healing, shields) and to a foe's health, so a
fight against an equal stays about as long at level 10 as at level 1; attribute points on top make the hero stronger.
In «Система» everything is exact; d20 adds a ±20% spread and a ×2 critical on a natural 20."""
from __future__ import annotations
import re

# ---------------- attributes ----------------
STEP = 0.05                      # every attribute point: ±5% of power and ±5 percentage points of a chance
MULT_MIN, MULT_MAX = 0.7, 2.0


def mult(attr_val: int) -> float:
    return max(MULT_MIN, min(MULT_MAX, 1 + STEP * (int(attr_val) - 10)))


def scaled(base: int, attr_val: int) -> int:
    return max(1, round(base * mult(attr_val)))


LEVEL_STEP = 0.10                # +10% per level above the first


def level_mult(level: int) -> float:
    return 1 + LEVEL_STEP * (max(1, int(level)) - 1)


# ---------------- health and mana ----------------
HP_BASE, HP_LEVEL, HP_PER_END = 40, 8, 4         # hero/companions/neutral NPCs: 40 + 8 per level + 4 per Выносливость over 10
MP_BASE, MP_LEVEL, MP_PER_MIND = 20, 4, 3        # 20 + 4 per level + 3 per Разум over 10

# ---------------- weapons and armour ----------------
# name stem -> (canonical name, damage, ranged). Matched at the START of a word («меч», «мечом», not «смечный»).
WEAPONS = {
    "кулак": ("Кулаки", 3, False), "кинжал": ("Кинжал", 5, False), "нож": ("Нож", 5, False), "серп": ("Серп", 5, False),
    "дубин": ("Дубина", 7, False), "посох": ("Посох", 7, False), "кистен": ("Кистень", 7, False), "вилы": ("Вилы", 7, False),
    "копь": ("Копьё", 8, False), "копьё": ("Копьё", 8, False), "булав": ("Булава", 8, False), "меч": ("Меч", 9, False),
    "сабл": ("Сабля", 9, False), "шпаг": ("Шпага", 9, False), "рапир": ("Рапира", 9, False), "тесак": ("Тесак", 9, False),
    "молот": ("Молот", 10, False), "цеп": ("Цеп", 9, False), "топор": ("Топор", 10, False), "секир": ("Секира", 12, False),
    "алебард": ("Алебарда", 12, False), "клеймор": ("Клеймор", 12, False), "двуруч": ("Двуручный меч", 12, False),
    "лук": ("Лук", 8, True), "арбалет": ("Арбалет", 10, True), "праща": ("Праща", 5, True),
    "пистол": ("Пистолет", 12, True), "револьвер": ("Револьвер", 12, True), "винтовк": ("Винтовка", 15, True),
    "ружь": ("Ружьё", 14, True), "автомат": ("Автомат", 16, True),
}
UNARMED = 3
# name stem -> (armour, slot). Armour takes this much off every physical blow (never below 1 damage).
ARMOR = {"кожан": (1, "тело"), "стёган": (1, "тело"), "стеган": (1, "тело"), "кольчуг": (2, "тело"),
         "бронежилет": (3, "тело"), "кирас": (3, "тело"), "латы": (4, "тело"), "латн": (4, "тело"),
         "доспех": (2, "тело"), "броня": (2, "тело"), "щит": (1, "щит"), "шлем": (1, "голова")}
# a word that, after an armour/weapon stem, means the thing is NOT armour/weapon («кожаная сумка», «ножны»)
NOT_GEAR = re.compile(r"сумк|мешоч|мешок|кошел|пояс|ремен|ножн|флакон|фляг|перчатк|сапог|ботин|плащ|книг|свиток|амулет|"
                      r"кольц|цепочк|украшен|лукошк|вилк", re.I)

# ---------------- abilities (power level 1..6): a GUIDE, not a cage ----------------
# What an ordinary ability of that level does. The AI proposes its own numbers and mechanics (several targets, an extra
# condition, a recharge, a price in health, a rule of its own); fit() keeps what is within reach of the guide — the price
# pays for power — and only trims a clear excess. Weaker than the guide is always fine.
DAMAGE = {1: 12, 2: 16, 3: 22, 4: 30, 5: 40, 6: 52}
HEAL = {1: 10, 2: 14, 3: 20, 4: 26, 5: 34, 6: 44}
SHIELD = {1: 8, 2: 12, 3: 16, 4: 22, 5: 30, 6: 38}
POTENCY = {1: 2, 2: 3, 3: 4, 4: 5, 5: 6, 6: 8}       # buff / debuff: ± this much damage and ±10% to hit
CONTROL = {1: 1, 2: 1, 3: 1, 4: 2, 5: 2, 6: 3}       # rounds of stun
MP_COST = {1: 4, 2: 6, 3: 8, 4: 10, 5: 13, 6: 16}
BUFF_HIT = 0.10                                       # an active buff (or debuff) moves the chance to hit by this much
# passives by level: hp/mp pools, armour, flat damage/healing bonuses, initiative
PASSIVE = {"hp": lambda L: 6 * L, "mp": lambda L: 4 * L, "ac": lambda L: 1 if L < 4 else 2,
           "melee": lambda L: L + 1, "ranged": lambda L: L + 1, "spell": lambda L: L + 1, "heal": lambda L: L + 1,
           "init": lambda L: L}

TOLERANCE = 0.35                 # up to +35% over what the price allows is kept as the AI wrote it
TARGET_SHARE = {1: 1.0, 2: 0.65, 3: 0.5}        # several targets: each takes this share of the power
RIDER_SHARE = 0.85               # an extra condition on top (горение, яд, страх…) costs 15% of the power
MP_MAX_MULT = 2.5                # mana up to 2.5× the guide
RECHARGE_MAX = 5                 # turns
DOT = ("кровотечение", "горение", "яд", "отравление", "кислота", "гниение")   # conditions that hurt every round
GUIDE = {"damage": DAMAGE, "heal": HEAL, "shield": SHIELD, "buff": POTENCY, "debuff": POTENCY, "control": CONTROL}


def guide(effect: str, level: int) -> int:
    t = GUIDE.get(effect)
    return t[max(1, min(6, level))] if t else 0


def guide_mp(effect: str, level: int) -> int:
    m = MP_COST[max(1, min(6, level))]
    return m if effect != "utility" else max(1, m // 2)


def price_mult(effect: str, level: int, mp: int, recharge: int = 0, self_hp: int = 0) -> float:
    """How much stronger than the guide the price lets an ability be: mana over the guide buys power at half rate
    (less mana — less power), every turn of recharge +15%, health paid +50% of its share of a level's damage."""
    gm = guide_mp(effect, level)
    m = 1 + 0.5 * (mp / gm - 1) if gm and mp else 1.0
    m += 0.15 * max(0, min(RECHARGE_MAX, recharge))
    if self_hp:
        m += 0.5 * self_hp / max(1, guide("damage", level))
    return max(0.5, min(2.0, m))


def fit(effect: str, level: int, power: int = 0, mp: int = 0, recharge: int = 0, targets: int = 1,
        self_hp: int = 0, rider: bool = False) -> dict:
    """Take the AI's proposal and keep it unless it clearly outgrows its price. Returns the numbers to use and a note
    (empty when nothing was changed)."""
    L = max(1, min(6, level))
    gm = guide_mp(effect, L)
    mp = max(1, min(round(gm * MP_MAX_MULT), int(mp))) if mp and mp > 0 else gm
    recharge = max(0, min(RECHARGE_MAX, int(recharge or 0)))
    targets = max(1, min(3, int(targets or 1)))
    self_hp = max(0, min(guide("damage", L), int(self_hp or 0)))
    pm = price_mult(effect, L, mp, recharge, self_hp)
    note = ""
    if effect == "control":                  # rounds of stun: the price buys at most one more round
        cap = CONTROL[L] + (1 if pm >= 1.4 else 0) - (1 if targets > 1 else 0) - (1 if rider else 0)
        cap = max(1, cap)
        power = int(power or 0) or cap
        if power > cap:
            note, power = f"оглушение урезано до {cap} р.", cap
    elif effect in GUIDE:
        allowed = guide(effect, L) * pm * TARGET_SHARE[targets] * (RIDER_SHARE if rider else 1)
        top = max(1, round(allowed * (1 + TOLERANCE)))
        power = int(power or 0) or max(1, round(allowed))
        if power > top:
            note, power = f"сила урезана до {top} (цена позволяет до {top})", top
    else:
        power = max(0, int(power or 0))
    return {"power": power, "mp": mp, "recharge": recharge, "targets": targets, "self_hp": self_hp, "note": note}


def fit_passive(key: str, level: int, value: int = 0) -> int:
    g = PASSIVE[key](max(1, min(6, level)))
    return max(1, min(round(g * 1.5) if g > 1 else 2, int(value))) if value and value > 0 else g


# ---------------- consumables (power 1..4) ----------------
POTION_HEAL = {1: 15, 2: 25, 3: 40, 4: 60}
POTION_MANA = {1: 8, 2: 14, 3: 22, 4: 30}
BOMB = {1: 12, 2: 18, 3: 26, 4: 36}
POTION_BUFF = {1: 2, 2: 3, 3: 4, 4: 6}

# ---------------- foes ----------------
# threat -> (level offset from the hero, HP at level 1, attributes, weapon damage at level 1, armour).
# HP and damage grow with the foe's level (level_mult); attributes stay. Tuned so that against a hero of the same level
# with a sword and Сила 14: обычный falls in ~3 of the hero's turns and needs ~9 to bring him down, опасный ~5 vs ~6,
# элита is a fight for the party, босс needs the party and its abilities.
THREATS = {
    "слабый":  (-1, 12, 9, 5, 0),
    "обычный": (0, 24, 11, 8, 0),
    "опасный": (1, 38, 12, 11, 1),
    "элита":   (2, 60, 13, 14, 2),
    "босс":    (3, 110, 14, 18, 3),
}

ENCOUNTER_PER_MEMBER = 1.5      # threat a fight may hold per party member: слабый 0.5, обычный 1, опасный 2, элита 3, босс 5
DEFEAT_HP = 0.25                 # a fallen party comes to with this share of health
REST_SHARE = 0.05                # out of a fight every input gives back this share of health and mana

# ---------------- chances («Система») ----------------
HIT_BASE = 0.65                  # equal attacker attribute and target Ловкость
CHECK_BASE = 0.50                # attribute equal to the difficulty
CHANCE_MIN, CHANCE_MAX = 0.05, 0.95
DIFFICULTY = {"легко": 10, "обычно": 13, "трудно": 16, "очень трудно": 19}
D20_SPREAD = 0.20                # d20: damage varies ±20%; a natural 20 doubles it


def clamp_chance(p: float) -> float:
    return max(CHANCE_MIN, min(CHANCE_MAX, p))


def hit_chance(attack_attr: int, target_agility: int, buff: int = 0) -> float:
    """buff: +1 for an active «усиление» on the attacker, −1 for «ослабление» (the size of the effect is in damage)."""
    return clamp_chance(HIT_BASE + STEP * (attack_attr - target_agility) + BUFF_HIT * buff)


def check_chance(attr_val: int, dc: int) -> float:
    return clamp_chance(CHECK_BASE + STEP * (attr_val - dc))


# ---------------- legacy dice -> numbers (old saves) ----------------
OLD_POWER = {"1d8": 1, "2d6": 2, "2d8": 3, "3d8": 4, "4d8": 5, "5d8": 6}          # damage / heal
OLD_SHIELD = {"1d6": 1, "2d4": 2, "2d6": 3, "3d6": 4, "4d6": 5, "5d6": 6}


def to_number(expr: str) -> int:
    """'9' -> 9; a legacy dice string '1d8' -> twice its average (the new scale is about ×2 the old one)."""
    s = (expr or "").strip().lower().replace(" ", "")
    if re.fullmatch(r"\d+", s):
        return int(s)
    m = re.fullmatch(r"(\d+)d(\d+)([+-]\d+)?", s)
    if m:
        n, sides, b = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
        return max(1, round(2 * (n * (sides + 1) / 2 + b)))
    return 0


def stem_at_word_start(text: str, stem: str) -> bool:
    return re.search(r"(?<![а-яёa-z])" + re.escape(stem), text.lower()) is not None
