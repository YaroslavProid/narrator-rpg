"""The world's ruleset: what of the game is the world's own, kept as data in the scene (Scene.rules).

The engine keeps six canonical attributes with fixed roles — the combat formulas rely on them:
  Сила — ближний бой, блок; Ловкость — точность, уклонение, стрельба; Выносливость — здоровье;
  Воля — дух, сопротивление, лечение; Разум — знания, техника, учёная магия; Влияние — голос, убеждение.
A world gives them its own names (labels), adds attributes of its own (checks only), names its mana, adds resources
(Рассудок, Перегрев, Голод…), conditions, the core paths that exist in it, its weapons and money. The narrator and the
UI only ever see the world's names; the code resolves them back to the canonical keys."""
from __future__ import annotations
import re
from . import balance as B
from .models import ATTRS, AttrDef, CondDef, Character, ResDef, Ruleset, Scene, WeaponDef

ROLE_HINT = {"Сила": "грубая сила, ближний бой", "Ловкость": "точность, скрытность, стрельба, уклонение",
             "Выносливость": "здоровье и стойкость", "Воля": "дух, вера, сопротивление чарам, лечение",
             "Разум": "знания, техника, учёная магия", "Влияние": "голос, убеждение, команды"}
CORE_IDS = ["core-voin", "core-mag", "core-ten", "core-duh", "core-sledopyt"]
KINDS = ("dot", "stun", "mark")


def default() -> Ruleset:
    return Ruleset()


def _n(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower().replace("ё", "е"))


# ---------------- attributes ----------------
def label(scene: Scene | None, key: str) -> str:
    if scene is None:
        return key
    for a in scene.rules.attrs + scene.rules.extra_attrs:
        if a.key == key:
            return a.label or a.key
    return key


def attr_keys(scene: Scene) -> list[str]:
    return list(ATTRS) + [a.key for a in scene.rules.extra_attrs]


def attr_labels(scene: Scene) -> list[str]:
    return [label(scene, k) for k in attr_keys(scene)]


def resolve(scene: Scene | None, raw: str) -> str | None:
    """The world's name of an attribute (or its own extra one) -> the key it is stored under."""
    n = _n(raw)
    if not n or scene is None:
        return None
    for a in scene.rules.attrs + scene.rules.extra_attrs:
        if n in (_n(a.label), _n(a.key)):
            return a.key
    for a in scene.rules.attrs + scene.rules.extra_attrs:
        lab = _n(a.label or a.key)
        if len(n) >= 4 and (lab.startswith(n[:5]) or n.startswith(lab[:5])):
            return a.key
    return None


def ensure_attrs(scene: Scene, c: Character):
    for a in scene.rules.extra_attrs:
        c.attrs.setdefault(a.key, 10)


# ---------------- resources ----------------
def res_def(scene: Scene, raw: str) -> ResDef | None:
    n = _n(raw)
    return next((r for r in scene.rules.resources if n in (_n(r.key), _n(r.label))), None)


def ensure_res(scene: Scene, c: Character):
    """Party members carry the world's resources (a hero's Рассудок, a mech's Перегрев)."""
    for r in scene.rules.resources:
        if r.key not in c.res:
            c.res[r.key] = r.max if r.start < 0 else max(0, min(r.max, r.start))
        c.res[r.key] = max(0, min(r.max, c.res[r.key]))


def change_res(scene: Scene, c: Character, r: ResDef, delta: int) -> list[str]:
    ensure_res(scene, c)
    old = c.res[r.key]
    c.res[r.key] = max(0, min(r.max, old + delta))
    if c.res[r.key] == old:
        return []
    msgs = [f"{c.name}: {r.label} {old}→{c.res[r.key]}/{r.max}"]
    if c.res[r.key] == 0 and old > 0:
        msgs.append(f"{c.name}: {r.label} на нуле" + (f" — {r.zero}" if r.zero else "") + ".")
    return msgs


# ---------------- conditions ----------------
def cond_kind(scene: Scene | None, name: str) -> str:
    n = _n(name)
    if scene is not None:
        d = next((x for x in scene.rules.conditions if _n(x.name) == n), None)
        if d:
            return d.kind
    return "dot" if name in B.DOT else ("stun" if n == "оглушение" else "mark")


def is_dot(scene: Scene | None, name: str) -> bool:
    return cond_kind(scene, name) == "dot"


# ---------------- core paths ----------------
def path_on(scene: Scene, pid: str) -> bool:
    return pid not in CORE_IDS or pid in scene.rules.paths


# ---------------- weapons ----------------
def weapon(scene: Scene | None, name: str) -> WeaponDef | None:
    if scene is None:
        return None
    n = (name or "").lower()
    for w in sorted(scene.rules.weapons, key=lambda w: len(w.stem), reverse=True):
        if w.stem and B.stem_at_word_start(n, w.stem.lower()):
            return w
    return None


# ---------------- for the narrator and the UI ----------------
def prompt_block(scene: Scene) -> str:
    r = scene.rules
    lines = ["# Правила мира"]
    lines.append("Атрибуты: " + "; ".join(f"{label(scene, k)} — {hint(scene, k)}" for k in attr_keys(scene)) + ".")
    lines.append(f"Мана (MP) в этом мире называется «{r.mp_label}».")
    if r.resources:
        lines.append("Ресурсы героя: " + "; ".join(
            f"{x.label} 0–{x.max}" + (f" ({x.hint})" if x.hint else "") + (f"; на нуле: {x.zero}" if x.zero else "")
            for x in r.resources) + f". Меняй их тегом <change who=\"Имя\" res=\"{r.resources[0].label} -1\"/>.")
    named = [c for c in r.conditions if c.hint or c.kind != "mark"]
    if named:
        lines.append("Состояния: " + "; ".join(
            f"{c.name}" + {"dot": " (ранит каждый раунд)", "stun": " (теряет ход)", "mark": ""}[c.kind]
            + (f" — {c.hint}" if c.hint else "") for c in named) + ".")
    if r.weapons:
        lines.append("Оружие этого мира: " + ", ".join(f"{w.name} (урон {w.damage}{', дальнее' if w.ranged else ''})"
                                                       for w in r.weapons) + ".")
    if r.currency:
        lines.append(f"Деньги: {r.currency}.")
    if r.notes:
        lines.append("Особые правила: " + r.notes)
    return "\n".join(lines)


def hint(scene: Scene, key: str) -> str:
    d = next((a for a in scene.rules.attrs + scene.rules.extra_attrs if a.key == key), None)
    return (d.hint if d and d.hint else ROLE_HINT.get(key, "")) or "проверки"


def schema(scene: Scene) -> dict:
    """What the character sheet shows, in the world's words (the UI draws from this)."""
    r = scene.rules
    return {"name": r.name, "attrs": [{"key": k, "label": label(scene, k), "hint": hint(scene, k)} for k in attr_keys(scene)],
            "mp_label": r.mp_label, "resources": [x.model_dump() for x in r.resources],
            "conditions": [x.model_dump() for x in r.conditions], "currency": r.currency}


# ---------------- from the world generation ----------------
def from_ai(raw: dict | None, base: Ruleset | None = None) -> Ruleset:
    """Normalise the ruleset the AI proposed for a world; anything missing keeps the default."""
    r = (base or default()).model_copy(deep=True)
    if not isinstance(raw, dict):
        return r
    s = lambda v, n: re.sub(r"\s+", " ", str(v or "")).strip()[:n]
    if s(raw.get("name"), 40):
        r.name = s(raw.get("name"), 40)
    labels = raw.get("attr_labels") if isinstance(raw.get("attr_labels"), dict) else {}
    for a in r.attrs:
        lab = s(labels.get(a.key), 24)
        if lab and lab.lower() not in {x.label.lower() for x in r.attrs if x is not a}:
            a.label = lab
    taken = {_n(a.label) for a in r.attrs}
    for x in (raw.get("extra_attrs") or [])[:2]:
        lab = s(x.get("label") if isinstance(x, dict) else x, 24)
        if lab and _n(lab) not in taken:
            r.extra_attrs.append(AttrDef(key=lab, label=lab, hint=s(x.get("hint") if isinstance(x, dict) else "", 80)))
            taken.add(_n(lab))
    if s(raw.get("mp_label"), 20):
        r.mp_label = s(raw.get("mp_label"), 20)
    for x in (raw.get("resources") or [])[:3]:
        if not isinstance(x, dict) or not s(x.get("label"), 24):
            continue
        mx = max(3, min(100, int(x.get("max") or 10)))
        r.resources.append(ResDef(key=s(x["label"], 24), label=s(x["label"], 24), max=mx,
                                  start=-1, hint=s(x.get("hint"), 100), zero=s(x.get("zero"), 120)))
    have = {_n(c.name) for c in r.conditions}
    for x in (raw.get("conditions") or [])[:5]:
        if not isinstance(x, dict) or not s(x.get("name"), 24):
            continue
        name = s(x["name"], 24).lower()
        kind = x.get("kind") if x.get("kind") in KINDS else "mark"
        if _n(name) in have:
            d = next(c for c in r.conditions if _n(c.name) == _n(name))
            d.hint = d.hint or s(x.get("hint"), 100)
        else:
            r.conditions.append(CondDef(name=name, kind=kind, hint=s(x.get("hint"), 100))); have.add(_n(name))
    paths = [p for p in (raw.get("paths") or []) if p in CORE_IDS]
    if paths:
        r.paths = paths
    for x in (raw.get("weapons") or [])[:5]:
        if not isinstance(x, dict) or not s(x.get("name"), 30):
            continue
        name = s(x["name"], 30)
        stem = re.findall(r"[а-яёa-z]{3,}", name.lower())
        if not stem:
            continue
        dmg = int(x.get("damage") or 8)
        r.weapons.append(WeaponDef(stem=stem[0][:max(3, len(stem[0]) - 2)], name=name,
                                   damage=max(3, min(20, dmg)), ranged=bool(x.get("ranged"))))
    res_names = {_n(x.label) for x in r.resources}
    r.extra_attrs = [a for a in r.extra_attrs if _n(a.label) not in res_names]       # «Рассудок» is a resource, not both
    r.currency = s(raw.get("currency"), 60) or r.currency
    r.notes = s(raw.get("notes"), 400) or r.notes
    return r
