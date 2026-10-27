"""Bring an older save up to date on load. `Scene.version` says which steps it still needs; bump VERSION whenever the
save format, the balance scale or the narrator's system prompt changes, and add a step here. Rewind snapshots are
migrated too, so going back in time never brings old numbers back."""
from __future__ import annotations
from . import balance as B, combat as C, magic as G, mechanics as M, prompts as P
from .models import Character, MagicPath, Scene

VERSION = 5


def regear(item) -> None:
    """Re-derive a piece of gear from its name on the current scale (old saves: dice, old armour values, things that
    were mistaken for gear — «кожаная сумка», «ножны»)."""
    if item.effect:
        return
    if B.NOT_GEAR.search(item.name.lower()) and (item.armor or item.damage):
        item.armor, item.damage, item.kind, item.slot, item.equipped = 0, "", "предмет", "", False
        return
    w, a = C.weapon_stem(item.name), C._stem(item.name, B.ARMOR)
    if w and item.kind == "оружие":
        item.damage = str(B.WEAPONS[w][1])
    elif a and item.kind == "броня":
        item.armor, item.slot = B.ARMOR[a]
    elif item.damage:
        item.damage = str(B.to_number(item.damage) or "")
    C.ensure_item_stats(item)


def _v5(paths: list[MagicPath], chars: list[Character]) -> None:
    for c in chars:
        for it in c.inventory:
            regear(it)
        c.conditions = [x for x in c.conditions if x.name not in ("щит", "усиление", "ослабление")]
    G.rebalance(paths, [a for c in chars for a in c.abilities])
    for c in chars:
        alive = c.hp > 0
        ratio = c.hp / c.max_hp if c.max_hp else 1.0
        mratio = c.mp / c.max_mp if c.max_mp else 1.0
        c.max_hp = c.max_mp = 0
        M.sync_derived(c)
        c.hp = max(1 if alive else 0, round(ratio * c.max_hp))
        c.mp = round(mratio * c.max_mp)


def to_v5(scene: Scene) -> None:
    """The numeric economy: numbers instead of dice, HP/MP from formulas, armour as damage reduction."""
    _v5(scene.paths, list(scene.characters.values()))
    for s in scene.snapshots:
        chars = {k: Character.model_validate(v) for k, v in (s.get("characters") or {}).items()}
        paths = [MagicPath.model_validate(x) for x in s.get("paths") or []]
        _v5(paths, list(chars.values()))
        s["characters"] = {k: c.model_dump() for k, c in chars.items()}
        if "paths" in s:
            s["paths"] = [x.model_dump() for x in paths]
        if s.get("combat"):
            s["combat"] = {}                    # a fight snapshotted on the old scale restarts cleanly


STEPS = {5: to_v5}


def migrate(scene: Scene) -> bool:
    """Run the missing steps; returns True if the scene changed (the caller saves it)."""
    changed = G.ensure_core(scene)
    if scene.version < VERSION:
        for v in range(scene.version + 1, VERSION + 1):
            if v in STEPS:
                STEPS[v](scene)
        scene.version = VERSION
        changed = True
    if scene.version >= VERSION and P.PROMPT_MARK not in scene.system_prompt:
        scene.system_prompt = P.build_system(scene)        # the narrator's rules changed since this save
        changed = True
    M.sync_all(scene)
    return changed
