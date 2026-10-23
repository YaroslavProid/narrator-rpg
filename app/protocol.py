"""Text-tag protocol: <narrate>, <speak who intent/>, <roll skill dc/>, <change/>, <introduce/>, <leave/>, <end/>."""
import re
from dataclasses import dataclass, field

TAG_RE = re.compile(r"<\s*(\w+)((?:\s+[\w-]+\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s/>]+))*)\s*/?\s*>?\s*$", re.S)
ATTR_RE = re.compile(r"([\w-]+)\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s/>]+))")
ACTION_TAGS = {"speak", "roll", "change", "introduce", "leave", "end", "scale", "attack", "combat", "condition",
               "reveal", "join", "part", "fulfill", "discover", "learn", "cast"}
REQUIRED = {"speak": ["who"], "roll": ["skill"], "change": ["who"], "introduce": ["name"], "leave": ["who"], "end": [], "scale": ["level"], "attack": ["target"], "combat": ["action"], "condition": ["who", "name"],
            "reveal": ["who"], "join": ["who"], "part": ["who"], "fulfill": ["what"],
            "discover": ["what"], "learn": ["what"], "cast": ["spell"]}


class ParseError(Exception):
    pass


@dataclass
class Tag:
    name: str
    attrs: dict = field(default_factory=dict)
    raw: str = ""


@dataclass
class StepOutput:
    narration: str
    tag: Tag | None
    raw: str           # content to store in history (incl. closing "/>" for an action)
    error: str | None = None


BROKEN_NARR = re.compile(r"</?\s*narr\w*(?:\s*>|[^\w\s<>]{0,2})")


def visible_text(raw: str) -> str:
    """Narration visible so far in a (possibly partial) stream: tags removed, partial tag held back."""
    s = re.sub(r"</?\s*narrate\s*>", "", raw)
    s = BROKEN_NARR.sub("", s)
    s = re.sub(r"<\s*/?\s*\w+(?:\s+[^<>]*)?/?>", "", s)
    i = s.rfind("<")
    if i != -1:
        s = s[:i]
    return s


def parse_attrs(s: str) -> dict:
    return {m.group(1).lower(): (m.group(2) if m.group(2) is not None else m.group(3) if m.group(3) is not None else m.group(4)).strip()
            for m in ATTR_RE.finditer(s)}


def parse_tag(text: str) -> Tag:
    t = text.strip()
    m = TAG_RE.match(t)
    if not m:
        raise ParseError(f"не удалось разобрать тег: {t[:80]}")
    name = m.group(1).lower()
    if name not in ACTION_TAGS:
        raise ParseError(f"неизвестный тег <{name}>; допустимы: {', '.join(sorted(ACTION_TAGS))}")
    attrs = parse_attrs(m.group(2) or "")
    for r in REQUIRED[name]:
        if not attrs.get(r):
            raise ParseError(f"у тега <{name}> нет атрибута {r}")
    return Tag(name, attrs, t)


def parse_step(content: str, stop_word: str = "") -> StepOutput:
    """Parse one narrator generation that stopped on '/>' (or ended naturally)."""
    raw = content
    stopped_on_tag = stop_word.strip() == "/>"
    # find the trailing action tag: last '<' not part of narrate open/close
    idx = None
    for m in re.finditer(r"<\s*(/?)\s*(\w+)", content):
        nm = m.group(2).lower()
        if nm == "narrate":
            continue
        idx = m.start()
    tag = None; err = None
    narr_src = content
    if idx is not None:
        tail = content[idx:]
        narr_src = content[:idx]
        closed = stopped_on_tag or tail.rstrip().endswith("/>") or tail.rstrip().endswith(">")
        if closed:
            try:
                tag = parse_tag(tail if tail.rstrip().endswith(">") else tail + "/>")
            except ParseError as e:
                err = str(e)
        elif re.match(r"<\s*(\w+)", tail) and re.match(r"<\s*(\w+)", tail).group(1).lower() in ACTION_TAGS:
            err = "тег не закрыт"
        else:
            narr_src = content  # stray '<' in prose
        if stopped_on_tag:
            raw = content + "/>"
    narration = re.sub(r"</?\s*narrate\s*>", "", narr_src)
    narration = BROKEN_NARR.sub("", narration)
    narration = re.sub(r"<[^>]*>", "", narration)
    narration = re.sub(r"[ \t]+\n", "\n", narration).strip()
    # canonical history text: a glitch the model produced (e.g. "</narr[") must not be copied back into the
    # append-only history, or it self-reinforces. Diverging only inside the last model turn is a cheap rollback.
    if err is None:
        raw = (f"<narrate>{narration}</narrate>" if narration else "") + \
              (("\n" if narration else "") + canonical_tag(tag) if tag else "")
    return StepOutput(narration, tag, raw, err)


def canonical_tag(tag: "Tag") -> str:
    attrs = "".join(f' {k}="{v.replace(chr(34), "")}"' for k, v in tag.attrs.items())
    return f"<{tag.name}{attrs}/>"


def split_speech(text: str, names: list[str]) -> list[dict]:
    """Split narration into narration/speech parts on lines 'Имя: ...' with a known name."""
    parts = []
    lower = {n.lower(): n for n in names}
    for line in text.split("\n"):
        m = re.match(r"^\s*\**([^\s:*][^:*\n]{0,30}?)\**\s*:\s*(.+)$", line)
        if m and m.group(1).strip().lower() in lower:
            parts.append({"kind": "speech", "who": lower[m.group(1).strip().lower()], "text": m.group(2).strip()})
        elif line.strip():
            if parts and parts[-1]["kind"] == "narration":
                parts[-1]["text"] += "\n" + line.strip()
            else:
                parts.append({"kind": "narration", "who": None, "text": line.strip()})
    return parts


def clean_speech(text: str, name: str) -> str:
    t = text.strip()
    t = re.sub(r"<[^>]*>?", "", t)
    for p in (name + ":", name.split()[0] + ":"):
        if t.lower().startswith(p.lower()):
            t = t[len(p):].strip()
    t = t.strip().strip("*").strip()
    if len(t) >= 2 and t[0] in "«\"„“" and t[-1] in "»\"“”" and t.count("«") <= 1:
        t = t[1:-1].strip()
    return t.split("\n\n")[0].strip()


def cyr_share(text: str, whitelist=()) -> float | None:
    t = re.sub(r"<[^>]*>", " ", text)
    for w in whitelist:
        if w:
            t = t.replace(w, " ")
    c = len(re.findall(r"[А-Яа-яЁё]", t)); l = len(re.findall(r"[A-Za-z]", t))
    return None if c + l == 0 else c / (c + l)


def tag_grammar(names: list[str], skills: list[str]) -> str:
    """GBNF for the format-error retry: exactly one valid action tag."""
    q = lambda xs: " | ".join('"' + x.replace('"', '') + '"' for x in xs) or '"?"'
    return "\n".join([
        'root ::= [ \\n]* ( speak | roll | attack | end )',
        'speak ::= "<speak who=\\"" name "\\" intent=\\"" txt "\\"/>"',
        'roll ::= "<roll skill=\\"" skill "\\" dc=\\"" dc "\\"/>"',
        'attack ::= "<attack who=\\"" name "\\" target=\\"" name "\\" weapon=\\"" txt "\\"/>" | "<attack target=\\"" name "\\"/>"',
        'end ::= "<end/>"',
        f"name ::= {q(names)}",
        f"skill ::= {q(skills)}",
        'dc ::= [1-2] [0-9] | [5-9]',
        'txt ::= [^"<>\\n]{3,120}',
    ])
