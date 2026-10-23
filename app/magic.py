"""Magic paths: the AI invents 3-5 paths that fit the world, code forces them onto one balance scale.
Requirements per tier: hero/companion level, governing attribute, ability points, and (tiers II-III always)
a story condition the narrator marks fulfilled with <fulfill what="…"/>. Code decides; the System agent explains."""
from __future__ import annotations
import difflib, json, re
from . import config, mechanics as M, store
from .models import ATTRS, Ability, Character, MagicPath, PathReq, PathTier, Scene
from .prompt import render

# one scale for every world: tier -> (level, attribute minimum, ability points, MP of an active ability)
TIER_RULES = {1: (2, 12, 1, 2), 2: (4, 14, 1, 4), 3: (7, 16, 2, 6)}
MAX_PATHS = 2          # a character follows at most two paths
ROMAN = {1: "I", 2: "II", 3: "III"}

PATHS_SCHEMA = {
    "type": "object", "required": ["paths"],
    "properties": {"paths": {"type": "array", "minItems": 3, "maxItems": 5, "items": {
        "type": "object", "required": ["name", "description", "attr", "tiers"],
        "properties": {
            "name": {"type": "string", "maxLength": 40},
            "description": {"type": "string", "maxLength": 160},
            "attr": {"type": "string", "enum": list(ATTRS)},
            "tiers": {"type": "array", "minItems": 3, "maxItems": 3, "items": {
                "type": "object", "required": ["story", "abilities"],
                "properties": {
                    "story": {"type": "string", "maxLength": 120},
                    "abilities": {"type": "array", "minItems": 1, "maxItems": 2, "items": {
                        "type": "object", "required": ["name", "kind", "description"],
                        "properties": {"name": {"type": "string", "maxLength": 40},
                                       "kind": {"type": "string", "enum": ["active", "passive"]},
                                       "description": {"type": "string", "maxLength": 140}}}},
                }}},
        }}}},
}


def _slug(s: str) -> str:
    return store.slug(s)[:30] or "path"


def salvage_paths(content: str) -> list[dict]:
    """Model output may be cut by the token limit: keep every complete path object before the cut."""
    try:
        return json.loads(content).get("paths") or []
    except ValueError:
        pass
    i = content.find("[", content.find('"paths"'))
    out, dec = [], json.JSONDecoder()
    while i != -1:
        j = content.find("{", i)
        if j == -1:
            break
        try:
            obj, end = dec.raw_decode(content, j)
        except ValueError:
            break
        out.append(obj)
        i = end
    return out


def balance(raw: list[dict]) -> list[MagicPath]:
    """Clamp model output onto TIER_RULES; tiers II-III always get a story condition."""
    out, seen = [], set()
    for p in raw[:5]:
        name = str(p.get("name") or "").strip()[:40]
        if not name or M.norm(name) in seen:
            continue
        seen.add(M.norm(name))
        attr = M.resolve_attr(str(p.get("attr") or "")) or "Разум"
        pid = _slug(name)
        tiers = []
        for n, t in enumerate((p.get("tiers") or [])[:3], 1):
            lvl, amin, pts, mp = TIER_RULES[n]
            reqs = [PathReq(kind="level", value=lvl), PathReq(kind="attr", attr=attr, value=amin),
                    PathReq(kind="points", value=pts)]
            story = re.sub(r"\s+", " ", str(t.get("story") or "")).strip()[:120]
            if len(story) < 8 or re.fullmatch(r"(ступень\s*)?[IVX\d]+\.?", story, flags=re.I):
                story = ""          # the model sometimes writes the tier number instead of an event
            if n > 1 and not story:
                story = ("Найти наставника" if n == 2 else "Пройти испытание") + f" пути «{name}»"
            if story:
                reqs.append(PathReq(kind="story", id=f"{pid}-{n}", text=story))
            abil = []
            for a in (t.get("abilities") or [])[:2]:
                an = str(a.get("name") or "").strip()[:40]
                if not an:
                    continue
                kind = a.get("kind") if a.get("kind") in ("active", "passive") else "passive"
                abil.append(Ability(name=an, kind=kind, description=str(a.get("description") or "")[:200],
                                    cost=f"{mp} MP" if kind == "active" else "", cooldown=""))
            if abil:
                tiers.append(PathTier(tier=n, reqs=reqs, abilities=abil))
        if len(tiers) == 3:
            out.append(MagicPath(id=pid, name=name, description=str(p.get("description") or "")[:300],
                                 attr=attr, tiers=tiers))
    return out


async def ensure_paths(llm, scene: Scene, seed: int = -1) -> list[dict]:
    """Generate this world's paths once (first time anyone asks). Returns LLM call stats (empty if cached)."""
    if scene.paths:
        return []
    sys_ = ("Ты проектируешь пути магии (школы, дисциплины) для текстовой RPG. Пиши по-русски. Пути должны подходить "
            "миру: в фэнтези — стихии и ремёсла, в киберпанке — импланты и сети, в хорроре — запретные культы. "
            "У каждого пути три ступени; на каждой — сюжетное условие (что должно случиться в истории: наставник, "
            "книга, ритуал, испытание — коротко, конкретно, привязано к миру: «Найти мастера рун в руинах форта», а НЕ номер "
            "ступени) и 1–2 способности. Описания — одной короткой фразой. Числа не пиши — их "
            "задаст Система. Способности ступени III заметно сильнее ступени I. Не повторяй уже имеющиеся у героя способности.")
    p = scene.player
    user = (f"Мир: {scene.title}\n{scene.setting[:1500]}\n\nГерой: {p.name}, {p.kind or ''}. {p.description[:300]}\n"
            f"Способности героя: {', '.join(a.name for a in p.abilities) or 'нет'}.\n"
            f"Атрибуты (путь опирается на один из них): {', '.join(ATTRS)}.\nПридумай 4 пути.")
    content, st = await llm.complete(render(sys_, [{"role": "user", "content": user}], open_model=""), 3600,
                                     stop=["<turn|>", "<|turn>"], temperature=0.7, seed=seed, json_schema=PATHS_SCHEMA)
    scene.paths = balance(salvage_paths(content))
    if not scene.paths:
        raise RuntimeError("модель не смогла придумать пути магии")
    from . import prompts
    scene.system_prompt = prompts.build_system(scene)   # the narrator must learn the story conditions
    return [st | {"kind": "magic paths"}]


def _stems(s: str) -> list[str]:
    """Word stems, so Russian cases match: «тень», «тени», «Тени» -> «тен»."""
    words = re.findall(r"[а-яёa-z]+", (s or "").lower().replace("ё", "е"))
    return [w[:max(3, len(w) - 2)] if len(w) > 4 else w[:max(3, len(w) - 1)]
            for w in words if w not in ("путь", "пути", "путем", "школа", "школы", "магия", "магии", "для", "мне")]


def find_path(scene: Scene, name: str) -> MagicPath | None:
    n = M.norm(re.sub(r"^(пут[ьиюё]|школ\w*|магии?)\s+", "", (name or "").strip(), flags=re.I))
    if not n:
        return None
    for p in scene.paths:
        pn = M.norm(p.name)
        if n == pn or n in pn or pn in n or n == p.id:
            return p
    q = _stems(name)
    for p in scene.paths:
        pw = re.findall(r"[а-яёa-z]+", p.name.lower().replace("ё", "е"))
        if q and all(any(w.startswith(st) for w in pw) for st in q):
            return p
    m = difflib.get_close_matches(n, [M.norm(p.name) for p in scene.paths], n=1, cutoff=0.6)
    return next((p for p in scene.paths if M.norm(p.name) == m[0]), None) if m else None


def req_status(scene: Scene, c: Character, r: PathReq) -> tuple[bool, str]:
    if r.kind == "level":
        return c.level >= r.value, f"уровень {r.value} (сейчас {c.level})"
    if r.kind == "attr":
        v = c.attrs.get(r.attr, 10)
        return v >= r.value, f"{r.attr} {r.value} (сейчас {v})"
    if r.kind == "points":
        return c.ability_points >= r.value, f"очков способностей {r.value} (есть {c.ability_points})"
    done = r.id in scene.flags
    return done, f"{r.text} ({'выполнено' if done else 'не выполнено'})"


def next_tier(c: Character, p: MagicPath) -> PathTier | None:
    have = c.paths.get(p.id, 0)
    return next((t for t in p.tiers if t.tier == have + 1), None)


def tier_status(scene: Scene, c: Character, p: MagicPath) -> dict:
    """What the System (and the UI) show for the next tier of `p` for `c`."""
    t = next_tier(c, p)
    if t is None:
        return {"path": p.name, "tier": None, "done": True, "ok": False, "missing": [], "met": []}
    met, missing = [], []
    for r in t.reqs:
        ok, text = req_status(scene, c, r)
        (met if ok else missing).append(text)
    if not c.paths.get(p.id) and len([x for x in c.paths if c.paths[x]]) >= MAX_PATHS:
        missing.append(f"не больше {MAX_PATHS} путей на персонажа")
    return {"path": p.name, "tier": t.tier, "done": False, "ok": not missing, "missing": missing, "met": met,
            "abilities": [a.name for a in t.abilities]}


def unlock_tier(scene: Scene, c: Character, path_name: str) -> tuple[bool, str]:
    """Open the next tier for a party member if every requirement holds; spends ability points."""
    if not M.in_party(scene, c):
        return False, f"{c.name} не в отряде — его путь Система не ведёт."
    p = find_path(scene, path_name)
    if not p:
        return False, f"Пути «{path_name}» в этом мире нет. Известные: {', '.join(x.name for x in scene.paths)}."
    s = tier_status(scene, c, p)
    if s["done"]:
        return False, f"{c.name} уже прошёл(ла) все ступени пути «{p.name}»."
    if not s["ok"]:
        return False, f"{p.name}, ступень {ROMAN[s['tier']]} закрыта для {c.name}. Не хватает: " + "; ".join(s["missing"]) + "."
    t = next_tier(c, p)
    pts = next(r.value for r in t.reqs if r.kind == "points")
    c.ability_points -= pts; c.skill_points = c.ability_points
    have = {M.norm(a.name) for a in c.abilities}
    got = []
    for a in t.abilities:
        if M.norm(a.name) not in have:
            c.abilities.append(a.model_copy()); got.append(a.name)
    c.paths[p.id] = t.tier
    return True, (f"{c.name}: открыт путь «{p.name}», ступень {ROMAN[t.tier]}. "
                  f"Новые способности: {', '.join(got) or '—'}. Потрачено очков способностей: {pts}.")


def fulfill(scene: Scene, what: str) -> tuple[bool, str]:
    """Narrator says a story condition happened: match it to an open story requirement."""
    reqs = [(r, p, t) for p in scene.paths for t in p.tiers for r in t.reqs if r.kind == "story" and r.id not in scene.flags]
    if not reqs:
        return False, "Открытых сюжетных условий нет."
    w = M.norm(what)
    best = max(reqs, key=lambda x: difflib.SequenceMatcher(None, w, M.norm(x[0].text)).ratio() +
               (0.5 if M.norm(x[1].name) in w or w in M.norm(x[0].text) else 0))
    score = difflib.SequenceMatcher(None, w, M.norm(best[0].text)).ratio() + (0.5 if M.norm(best[1].name) in w or w in M.norm(best[0].text) else 0)
    if score < 0.45:
        return False, f"Не нашла такого условия. Открытые: " + "; ".join(r.text for r, _, _ in reqs[:6]) + "."
    r, p, t = best
    scene.flags[r.id] = scene.turn
    return True, f"Условие выполнено: «{r.text}» — путь «{p.name}», ступень {ROMAN[t.tier]}."


def open_story_conditions(scene: Scene, limit: int = 8) -> list[str]:
    return [f"{r.text} (путь «{p.name}», ступень {ROMAN[t.tier]})"
            for p in scene.paths for t in p.tiers for r in t.reqs if r.kind == "story" and r.id not in scene.flags][:limit]


def paths_overview(scene: Scene, c: Character) -> str:
    lines = [f"### ⟦ СИСТЕМА · ПУТИ МАГИИ ⟧ {c.name}"]
    for p in scene.paths:
        have = c.paths.get(p.id, 0)
        s = tier_status(scene, c, p)
        head = f"**{p.name}** ({p.attr}) — " + (f"пройдена ступень {ROMAN[have]}" if have else "не открыт")
        lines.append(head + (f". {p.description}" if p.description and not have else ""))
        if not s["done"]:
            lines.append(f"  Ступень {ROMAN[s['tier']]} даст: {', '.join(s['abilities'])}. "
                         + ("Все требования выполнены — можно открыть." if s["ok"] else "Не хватает: " + "; ".join(s["missing"]) + "."))
    return "\n".join(lines)
