"""Dynamic per-turn length budget from scene signals (characters present, input type, novelty, length)."""
import re
from dataclasses import dataclass, asdict

# Length is steered by the wording of the hint only (a soft nudge about the weight of the scene). The numbers are
# safety ceilings against runaway generation and loops, not targets:
# tier: (speaks: informational, step_tokens ceiling, max_steps ceiling, hard_speaks ceiling, hint)
TIERS = {
    "камерный": (2, 900, 6, 6, "Сцена лёгкая — не раздувай."),
    "обычный": (3, 1000, 8, 8, "Сцена обычная — столько, сколько нужно, без лишнего."),
    "крупный": (5, 1200, 10, 10, "Сцена насыщенная — можно развернуться."),
}
ORDER = ["камерный", "обычный", "крупный"]
TRAVEL = (r"иду в|идём|отправля|еду|скачу|выхожу|вхожу|захожу|прибыва|спускаюсь|поднимаюсь|направля|добира|плыв|въезжа|"
          r"покидаю|дорог[ау]|путь|переход|новое место")     # a deed that moves the hero to another place: bigger scene


@dataclass
class Budget:
    tier: str
    speaks: int        # typical number of character lines (informational only)
    hard_speaks: int   # safety ceiling against a speak loop (or the player's own limit from the settings)
    step_tokens: int   # n_predict ceiling for one narrator step (anti-runaway, not a length target)
    max_steps: int     # loop guard
    hint: str
    reasons: list

    def dict(self):
        return asdict(self)


def classify(text: str | None, npcs: int, introduced_last: bool, last_tier: str | None, combat_ongoing: bool):
    """Scene size from the parsed input (deed / speech / thoughts), the fight and how many NPCs are involved."""
    from . import intent as I
    reasons = []
    if text is None:
        lvl = 1 if npcs >= 2 or combat_ongoing else 0
        reasons.append("продолжить")
        if combat_ongoing:
            lvl = 2; reasons.append("идёт бой")
        return lvl, reasons
    it = I.parse(text)
    lvl = 1
    if combat_ongoing:
        lvl = 2; reasons.append("бой")
    if introduced_last:
        lvl = max(lvl, 2 if npcs >= 2 else 1); reasons.append("новый персонаж")
    if npcs >= 4:
        lvl = max(lvl, 2); reasons.append(f"{npcs} NPC")
    if it.acts and re.search(TRAVEL, " ".join(it.actions).lower()):
        lvl = 2; reasons.append("перемещение/новое место")
    if not it.acts and not combat_ongoing:
        lvl = 0 if npcs <= 1 else min(lvl, 1); reasons.append("только речь/мысли")
    if it.acts and len(" ".join(it.actions)) > 220 and lvl < 2:
        lvl += 1; reasons.append("развёрнутое действие")
    return lvl, reasons


def turn_budget(text, npcs, introduced_last=False, last_tier=None, combat_ongoing=False, mult=1.0,
                max_speaks=None, scale_override: str | None = None) -> Budget:
    lvl, reasons = classify(text, npcs, introduced_last, last_tier, combat_ongoing)
    if scale_override in ORDER:
        o = ORDER.index(scale_override)
        lvl = max(0, min(2, lvl + (1 if o > lvl else -1 if o < lvl else 0))); reasons.append(f"рассказчик: {scale_override}")
    tier = ORDER[lvl]
    sp, tok, steps, hard, hint = TIERS[tier]
    mult = max(0.5, min(2.0, mult or 1.0))
    sp = max(1, round(sp * mult)); tok = int(tok * max(1.0, mult)); steps = max(4, round(steps * max(1.0, min(mult, 1.5))))
    hard = max(sp + 1, round(hard * max(1.0, mult)))
    if max_speaks:
        hard = min(hard, max_speaks); sp = min(sp, max_speaks)
    return Budget(tier, sp, hard, tok, steps, hint, reasons)
