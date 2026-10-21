"""Dynamic per-turn length budget from scene signals (characters present, input type, novelty, length)."""
import re
from dataclasses import dataclass, asdict

TIERS = {  # tier: (speaks, step_tokens, words, max_steps, hint)
    "камерный": (2, 220, 70, 3, "Масштаб сцены: камерный — коротко, 1–2 реплики."),
    "обычный": (3, 300, 110, 4, "Масштаб сцены: обычный — 2–3 реплики, без лишних описаний."),
    "крупный": (5, 420, 170, 6, "Масштаб сцены: крупный — можно развёрнуто, несколько голосов."),
}
ORDER = ["камерный", "обычный", "крупный"]
COMBAT = r"атак|\bбь[юё]|\bбей|удар[июяь]|выпад|выхватыва|стреля|выстрел|напада|драк|\bбой\b|\bбо[юяе]\b|сраж|заклинан|парир|уклоня|рублю|колю|замахива"
TRAVEL = r"иду в|идём|отправля|еду|скачу|выхожу|вхожу|захожу|прибыва|спускаюсь|поднимаюсь|направля|добира|плыв|въезжа|покидаю|дорог[ау]|путь|переход|новое место"
DIALOG = r"спрашива|говорю|отвечаю|шепчу|шепну|киваю|улыбаюсь|сказ|прошу|«|\"|\?"
CROWD = r"толп|площад|рынок|ярмар|\bпир\b|собрани|армия|отряд"


@dataclass
class Budget:
    tier: str
    speaks: int        # target number of character lines (hint)
    hard_speaks: int   # safety stop, slightly above the target
    step_tokens: int
    max_steps: int
    words: int
    hint: str
    reasons: list

    def dict(self):
        return asdict(self)


def classify(text: str | None, npcs: int, introduced_last: bool, last_tier: str | None, combat_ongoing: bool):
    reasons = []
    t = (text or "").lower()
    if text is None:
        lvl = 1 if npcs >= 2 or combat_ongoing else 0
        reasons.append("продолжить")
        if combat_ongoing: lvl = 2; reasons.append("идёт бой")
        return lvl, reasons
    lvl = 1
    combat = bool(re.search(COMBAT, t)); travel = bool(re.search(TRAVEL, t)); dialog = bool(re.search(DIALOG, t))
    if combat: lvl = 2; reasons.append("бой")
    if travel: lvl = 2; reasons.append("перемещение/новое место")
    if re.search(CROWD, t): lvl = 2; reasons.append("толпа")
    if introduced_last: lvl = max(lvl, 2) if npcs >= 2 else max(lvl, 1); reasons.append("новый персонаж")
    if npcs >= 4: lvl = max(lvl, 2); reasons.append(f"{npcs} NPC")
    if not (combat or travel) and dialog and npcs <= 1 and len(t) < 160:
        lvl = 0; reasons.append("диалог один на один")
    elif not (combat or travel) and dialog and len(t) < 80 and lvl < 2:
        lvl = min(lvl, 1); reasons.append("короткая реплика")
    if len(t) > 300 and lvl < 2: lvl += 1; reasons.append("длинный ввод")
    if npcs == 0 and lvl > 1 and not travel: lvl = 1
    return lvl, reasons


def turn_budget(text, npcs, introduced_last=False, last_tier=None, combat_ongoing=False, mult=1.0,
                max_speaks=None, scale_override: str | None = None) -> Budget:
    lvl, reasons = classify(text, npcs, introduced_last, last_tier, combat_ongoing)
    if scale_override in ORDER:
        o = ORDER.index(scale_override)
        lvl = max(0, min(2, lvl + (1 if o > lvl else -1 if o < lvl else 0))); reasons.append(f"рассказчик: {scale_override}")
    tier = ORDER[lvl]
    sp, tok, words, steps, hint = TIERS[tier]
    mult = max(0.5, min(2.0, mult or 1.0))
    sp = max(1, round(sp * mult)); tok = int(tok * mult); words = int(words * mult); steps = max(2, round(steps * min(mult, 1.5)))
    if npcs <= 1: sp = min(sp, 2 if tier != "крупный" else 3)
    hard = sp + 1 + (1 if tier == "крупный" else 0)
    if max_speaks:
        hard = min(hard, max_speaks); sp = min(sp, max_speaks)
    return Budget(tier, sp, hard, tok, steps, words, hint, reasons)
