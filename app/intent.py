"""What the player actually did this turn: act, speak (aloud / whisper / shout) or only think.
Markup as in SillyTavern-like front-ends: *действие или мысли*, «речь» or "речь", (мысли) / ((мысли)).
A starred aside that reads as a thought («*интересно, он врёт?*», «*думаю, пора уходить*») is a thought.
Unmarked text is speech when an action declares speaking («*говорю шёпотом* …»), thoughts when it declares
thinking, otherwise an action. The narrator also gets the raw input and decides doubtful cases by meaning."""
from __future__ import annotations
import re
from dataclasses import dataclass, field

SPEAK = re.compile(r"говор|скаж|сказа|шеп|шёп|крич|крикн|\bору\b|спрашива|спрош|отвеча|бормоч|\bзову\b|оклика|"
                   r"обращаюсь|произнош|вслух|рявка|цеж|шипл|окликн|отвеча", re.I)
WHISPER = re.compile(r"шеп|шёп|тихо|вполголоса|едва слышно|на ухо", re.I)
SHOUT = re.compile(r"крич|крикн|\bору\b|громко|во весь голос|рявка|орать", re.I)
THINK = re.compile(r"дума|мысл|про себя|размышля|прикидыва|мелькает", re.I)
# a starred aside that is the hero's inner voice, not a deed
THOUGHT_STAR = re.compile(r"^(я\s+)?(думаю|подумал[аи]?|мысленно|про\s+себя|интересно|надеюсь|наверное|похоже|кажется|"
                          r"хм+|ну\s+и|чёрт|черт|а\s+что\s+если|что\s+если|неужели|может\s+быть|может,|только\s+бы|"
                          r"лишь\s+бы|главное|ладно|так-?так|странно|почему|зачем|кто\s+же|что\s+же)\b", re.I)
# a starred aside that only says HOW the hero speaks/thinks is not a physical action
MANNER_ONLY = re.compile(r"^(я\s+)?(тихо\s+|громко\s+)?(говорю|шепчу|шепчу\s+\S+|говорю\s+шёпотом|говорю\s+шепотом|"
                         r"кричу|думаю|думаю\s+про\s+себя|про\s+себя|шёпотом|шепотом|вслух)\b[^.!?]{0,20}$", re.I)


@dataclass
class Intent:
    actions: list[str] = field(default_factory=list)
    speech: list[str] = field(default_factory=list)
    thoughts: list[str] = field(default_factory=list)
    manner: str = "вслух"          # вслух | шёпотом | громко

    @property
    def acts(self) -> bool:
        """True when the hero physically does something this turn (checks/attacks for the hero allowed)."""
        return bool(self.actions)

    def spoken(self) -> str:
        return " ".join(self.speech).strip()


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip(" \t\n-—–,.;:") if s else ""


def _words(s: str) -> str:
    """Spoken words: keep the punctuation, drop a leading dialogue dash."""
    return re.sub(r"\s+", " ", s).strip().lstrip("—–-").strip() if s else ""


def _tail_after(t: str, openers: tuple[str, ...]) -> tuple[str, str]:
    """An unclosed «quote / "quote / (thought: everything after the first opener belongs to it."""
    idx = [t.find(o) for o in openers if o in t]
    if not idx:
        return t, ""
    i = min(idx)
    return t[:i], t[i + 1:]


_CONJ_TAIL = re.compile(r"(?:^|[\s,;])(?:и|а|но|я|потом|затем|тут|тогда|вдруг|сразу|при этом)\s*$", re.I)


def _strip_speech_tag(rest: str) -> str:
    """«Я подхожу к стражнику и говорю:» -> the deed «Я подхожу к стражнику»; «и кричу:» -> nothing."""
    ms = list(SPEAK.finditer(rest))
    if not ms:
        return rest
    pre = rest[:ms[-1].start()]
    while True:
        new = _CONJ_TAIL.sub("", pre)
        if new == pre:
            break
        pre = new
    return pre


def parse(text: str | None) -> Intent | None:
    if text is None:
        return None
    t = re.sub(r"\*{2,}([^*]+?)\*{2,}", r"*\1*", text.strip())      # **bold** is the same starred action
    it = Intent()
    acts = [m.group(1) for m in re.finditer(r"\*([^*]+)\*", t)]
    t = re.sub(r"\*[^*]+\*", " ", t)
    if "*" in t:                                  # an unclosed *action runs up to the next marker or the end
        i = t.index("*")
        m = re.match(r"[^«\"„(]*", t[i + 1:])
        acts.append(m.group(0))
        t = t[:i] + " " + t[i + 1:][m.end():]
    quoted = [m.group(1) or m.group(2) or m.group(3) for m in re.finditer(r"«([^»]+)»|\"([^\"]+)\"|„([^“”]+)[“”]", t)]
    t = re.sub(r"«[^»]+»|\"[^\"]+\"|„[^“”]+[“”]", " ", t)
    t, open_speech = _tail_after(t, ("«", "\"", "„"))      # an unclosed «quote is speech to the end
    if open_speech.strip(" »“”\"„"):
        quoted.append(open_speech.strip(" »“”\"„"))
    thoughts = [m.group(1) or m.group(2) for m in re.finditer(r"\(\(([^)]+)\)\)|\(([^)]+)\)", t)]
    t = re.sub(r"\(\([^)]+\)\)|\([^)]+\)", " ", t)
    t, open_thought = _tail_after(t, ("(",))
    if open_thought.strip(" ()"):
        thoughts.append(open_thought.strip(" ()"))
    t = re.sub(r"[»“”)]", " ", t)
    rest_raw = re.sub(r"\s+", " ", t).strip()
    rest = _clean(t)

    declares_speech = any(SPEAK.search(a) for a in acts)
    declares_thought = any(THINK.search(a) and not SPEAK.search(a) for a in acts)
    for a in acts:
        if WHISPER.search(a):
            it.manner = "шёпотом"
        elif SHOUT.search(a):
            it.manner = "громко"
        has_rest = bool(rest and re.search(r"\w", rest))
        if MANNER_ONLY.match(_clean(a)) and (has_rest or len(_clean(a).split()) <= 2):
            continue                      # «*я говорю шепотом*» + words after it — manner, not a deed
        if THOUGHT_STAR.match(_clean(a)) and not SPEAK.search(a):
            it.thoughts.append(_clean(a)); continue    # «*думаю, пора уходить*» — the thought itself
        if re.search(r"\w", a):
            it.actions.append(_clean(a))
    it.speech += [_words(q) for q in quoted if _words(q)]
    it.thoughts += [_clean(x) for x in thoughts if _clean(x)]
    if rest and re.search(r"\w", rest):
        if quoted and not declares_speech:
            # words in quotes are the speech; the text around them is the deed or a speech tag («и кричу:»)
            if WHISPER.search(rest):
                it.manner = "шёпотом"
            elif SHOUT.search(rest):
                it.manner = "громко"
            deed = _clean(_strip_speech_tag(rest))
            if deed and re.search(r"\w", deed):
                it.actions.append(deed)
        elif declares_thought and not declares_speech:
            it.thoughts.append(rest)
        elif (declares_speech or quoted or rest_raw.startswith(("—", "–", "-")) or rest_raw.endswith("?")):
            it.speech.append(_words(rest_raw))
        else:
            it.actions.append(rest)
    return it


def for_narrator(it: Intent | None, raw: str | None = None) -> str:
    """The [ИГРОК] block: the raw input plus deed, words and thoughts kept apart so the narrator does not mix them up."""
    if it is None:
        return "[ПРОДОЛЖИТЬ] Игрок ничего не делает. Мир и персонажи действуют сами: продвинь сцену на шаг."
    lines = ["[ИГРОК]"]
    if raw and raw.strip():
        lines.append("Как написал игрок: " + raw.strip()[:600]
                     + " (разбор ниже — подсказка; *…* бывает и действием, и мыслью — решай по смыслу)")
    if it.actions:
        lines.append("Действие героя: " + "; ".join(it.actions))
    if it.speech:
        who = {"шёпотом": "шёпотом — слышат только те, кто вплотную рядом (спутники)",
               "громко": "громко — слышат все вокруг"}.get(it.manner, "вслух")
        lines.append(f"Речь героя ({who}): «" + " ".join(it.speech) + "»")
    if it.thoughts:
        lines.append("Мысли героя (никто не слышит, NPC на них не реагируют): " + " ".join(it.thoughts))
    if not it.actions:
        lines.append("Герой ничего не делает руками — только " + ("говорит" if it.speech else "думает")
                     + ". Не превращай это в действие и не делай проверок за героя.")
    return "\n".join(lines)


def hint(it: Intent | None) -> str:
    """Short per-turn reminder (in the turn tail, so the cached prompt prefix stays intact)."""
    if it is None:
        return "Герой бездействует: без проверок и атак за него; NPC и мир действуют сами."
    parts = []
    if it.speech:
        parts.append("на речь героя отвечают репликами (<speak>), проверка на слова не нужна")
    if it.thoughts:
        parts.append("мысли героя никто не слышит")
    if it.actions:
        parts.append("проверка (<roll>) — только если действие рискованное и исход неочевиден")
    return ("Как читать ввод: " + "; ".join(parts) + ".") if parts else ""
