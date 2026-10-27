"""Paths of development — the main progression, spent with ability points.

Core paths: five paths that exist in every world (Воин, Маг, Тень, Дух, Следопыт), five ranks each. Their mechanics
are fixed here; the AI only renames them once to fit the world (flavor_core).

Branches: grow from the story. The narrator marks what the hero SAW (<discover/>) — a lead in the Book of paths.
When the hero STUDIES it (book, mentor, long observation — <learn/>), the AI writes a branch after the turn
(generate_branch): three tiers of abilities rooted in one or two core paths. Code balances every number: power,
MP, costs and the rank of the parent path each tier needs. Tier III also needs a story event (<fulfill/>).

Attributes do not gate paths; they amplify what the abilities do (see combat.cast, mechanics.sync_derived)."""
from __future__ import annotations
import difflib, json, math, re
from . import mechanics as M, store
from .models import ATTRS, Ability, Character, Lead, MagicPath, PathReq, PathTier, Scene
from .prompt import render

ROMAN = {1: "I", 2: "II", 3: "III", 4: "IV", 5: "V"}

# ---------------- one balance scale for everything (power level 1..6) ----------------
DAMAGE = {1: "1d8", 2: "2d6", 3: "2d8", 4: "3d8", 5: "4d8", 6: "5d8"}
HEAL = {1: "1d8", 2: "2d6", 3: "2d8", 4: "3d8", 5: "4d8", 6: "5d8"}
SHIELD = {1: "1d6", 2: "2d4", 3: "2d6", 4: "3d6", 5: "4d6", 6: "5d6"}
POTENCY = {1: 1, 2: 1, 3: 2, 4: 2, 5: 3, 6: 4}         # buff / debuff
CONTROL = {1: 1, 2: 1, 3: 1, 4: 2, 5: 2, 6: 3}         # rounds of stun
MP_COST = {1: 2, 2: 3, 3: 4, 4: 5, 5: 6, 6: 8}
PASSIVE = {"hp": lambda L: 3 * L, "mp": lambda L: 2 * L, "ac": lambda L: 1 if L < 4 else 2 if L < 6 else 3,
           "init": lambda L: L}                          # melee/ranged/spell/heal: ceil(L/2)

CORE_COST = {1: 1, 2: 1, 3: 1, 4: 2, 5: 2}             # ability points per core rank
BRANCH_COST = {1: 1, 2: 2, 3: 2}                       # per branch tier
EFFECTS = ["damage", "heal", "shield", "buff", "debuff", "control", "utility"]


def make_ability(name: str, kind: str, effect: str, level: int, scale: str = "", bonus: str = "",
                 description: str = "", path: str = "", tier: int = 0) -> Ability:
    """Numbers come from the level only: the AI (or this file) picks what an ability does, never how much."""
    L = max(1, min(6, level))
    if kind == "passive":
        key = bonus if bonus in M.BONUS_KEYS else "hp"
        val = PASSIVE[key](L) if key in PASSIVE else math.ceil(L / 2)
        return Ability(name=name[:40], kind="passive", effect="passive", bonus={key: val},
                       description=description[:200], path=path, tier=tier)
    effect = effect if effect in EFFECTS else "utility"
    power = {"damage": DAMAGE, "heal": HEAL, "shield": SHIELD}.get(effect, {}).get(L, "")
    if effect in ("buff", "debuff"):
        power = str(POTENCY[L])
    elif effect == "control":
        power = str(CONTROL[L])
    mp = MP_COST[L] if effect != "utility" else max(1, MP_COST[L] // 2)
    scale = M.resolve_attr(scale) or "" if effect != "utility" else ""
    return Ability(name=name[:40], kind="active", effect=effect, power=power, scale=scale, mp=mp, cost=f"{mp} MP",
                   description=description[:200], path=path, tier=tier)


# id, name, description, ranks: (name, kind, effect, scale, bonus, description)
CORE = [
    ("core-voin", "Путь Воина", "Оружие, стойка и ярость: тот, кто решает спор сталью.", [
        ("Мощный удар", "active", "damage", "Сила", "", "Вкладывает в удар весь вес тела."),
        ("Закалка", "passive", "", "", "hp", "Тело привыкло к боли и ранам."),
        ("Боевой клич", "active", "buff", "Влияние", "", "Клич, от которого рука крепче сжимает оружие."),
        ("Стальная кожа", "passive", "", "", "ac", "Удары соскальзывают, будто по доспеху."),
        ("Сокрушающий натиск", "active", "damage", "Сила", "", "Неостановимая серия ударов."),
    ]),
    ("core-mag", "Путь Мага", "Учёная магия: сырая сила, щиты и чтение потоков. Корень всех магических школ.", [
        ("Магическая стрела", "active", "damage", "Разум", "", "Сгусток чистой силы, бьющий без промаха."),
        ("Чутьё магии", "passive", "", "", "spell", "Заклинания складываются точнее и бьют сильнее."),
        ("Магический щит", "active", "shield", "Разум", "", "Мерцающая преграда принимает удар на себя."),
        ("Поток маны", "passive", "", "", "mp", "Запас силы глубже, чем у обычного мага."),
        ("Волна силы", "active", "damage", "Разум", "", "Сокрушительный выброс накопленной силы."),
    ]),
    ("core-ten", "Путь Тени", "Скрытность, точность и грязные приёмы: бить туда, где не ждут.", [
        ("Удар из тени", "active", "damage", "Ловкость", "", "Внезапный удар в уязвимое место."),
        ("Уклонение", "passive", "", "", "ac", "Тело уходит из-под удара раньше, чем ты об этом думаешь."),
        ("Ослепляющий приём", "active", "control", "Ловкость", "", "Песок в глаза, удар под колено — враг теряет ход."),
        ("Точность убийцы", "passive", "", "", "melee", "Каждый удар находит щель в защите."),
        ("Смертельный удар", "active", "damage", "Ловкость", "", "Один удар, после которого не встают."),
    ]),
    ("core-duh", "Путь Духа", "Вера, воля и связь с тем, что выше: лечение, обереги, изгнание.", [
        ("Исцеление", "active", "heal", "Воля", "", "Затягивает раны прикосновением."),
        ("Свет внутри", "passive", "", "", "heal", "Исцеление даётся легче и действует сильнее."),
        ("Оберег", "active", "shield", "Воля", "", "Незримая защита ложится на союзника."),
        ("Благодать", "passive", "", "", "hp", "Дух держит тело, когда оно должно упасть."),
        ("Свет изгнания", "active", "damage", "Воля", "", "Сила веры обжигает врага."),
    ]),
    ("core-sledopyt", "Путь Следопыта", "Дорога, зверь и выстрел издалека.", [
        ("Меткий выстрел", "active", "damage", "Ловкость", "", "Выстрел, выверенный до волоса."),
        ("Чутьё следопыта", "passive", "", "", "init", "Замечаешь опасность первым."),
        ("Ловчая сеть", "active", "control", "Ловкость", "", "Сеть или аркан сковывают врага."),
        ("Закалка странника", "passive", "", "", "hp", "Дорога сделала тело выносливым."),
        ("Град стрел", "active", "damage", "Ловкость", "", "Стрелы ложатся одна за другой."),
    ]),
]
CORE_IDS = [x[0] for x in CORE]


def build_core() -> list[MagicPath]:
    out = []
    for pid, name, desc, ranks in CORE:
        tiers = []
        for n, (an, kind, eff, scale, bonus, ad) in enumerate(ranks, 1):
            tiers.append(PathTier(tier=n, reqs=[PathReq(kind="points", value=CORE_COST[n])],
                                  abilities=[make_ability(an, kind, eff, n, scale, bonus, ad, pid, n)]))
        out.append(MagicPath(id=pid, name=name, description=desc, core=True, tiers=tiers))
    return out


def ensure_core(scene: Scene) -> bool:
    """Every scene has the five core paths. Old AI-generated paths (before branches existed) are dropped.
    Returns True if something changed."""
    changed = False
    legacy = [p for p in scene.paths if not p.core and not p.parents]
    if legacy:
        ids = {p.id for p in legacy}
        scene.paths = [p for p in scene.paths if p.id not in ids]
        for c in scene.characters.values():
            c.paths = {k: v for k, v in c.paths.items() if k not in ids}
        changed = True
    have = {p.id for p in scene.paths}
    missing = [p for p in build_core() if p.id not in have]
    if missing:
        scene.paths = missing + scene.paths
        changed = True
    return changed


def core_paths(scene: Scene) -> list[MagicPath]:
    return [p for p in scene.paths if p.core]


def branches(scene: Scene) -> list[MagicPath]:
    return [p for p in scene.paths if not p.core]


def get_path(scene: Scene, pid: str) -> MagicPath | None:
    return next((p for p in scene.paths if p.id == pid), None)


# ---------------- AI: renaming core paths for this world ----------------
FLAVOR_SYS = """Ты адаптируешь систему прокачки текстовой RPG к конкретному миру. Пиши по-русски.
Есть пять основных путей, они есть в любом мире, их механика неизменна. Твоя задача — только названия и короткие описания,
чтобы они звучали как часть этого мира.

Правила:
- Сохраняй СУТЬ каждого пути и каждой способности: «Путь Мага» остаётся путём учёной магии, «Исцеление» остаётся лечением,
  урон остаётся уроном, щит — щитом. Меняется только облик: в киберпанке «Путь Мага» может стать «Путём Нейромантa»,
  «Магическая стрела» — «Импульсом»; в мрачном фэнтези можно оставить почти как есть.
- Если мир классическое фэнтези — меняй мало: лучше узнаваемое название, чем вычурное.
- Название пути — 2–4 слова. Название способности — 1–3 слова, без кавычек и номеров. Описание — одна короткая фраза.
- Никаких чисел, кубиков и стоимостей — их задаёт Система.
- Не используй имена конкретных персонажей мира в названиях."""

FLAVOR_SCHEMA = {
    "type": "object", "required": ["paths"],
    "properties": {"paths": {"type": "array", "minItems": 5, "maxItems": 5, "items": {
        "type": "object", "required": ["id", "name", "description", "abilities"],
        "properties": {
            "id": {"type": "string", "enum": CORE_IDS},
            "name": {"type": "string", "maxLength": 40},
            "description": {"type": "string", "maxLength": 160},
            "abilities": {"type": "array", "minItems": 5, "maxItems": 5, "items": {
                "type": "object", "required": ["rank", "name", "description"],
                "properties": {"rank": {"type": "integer", "minimum": 1, "maximum": 5},
                               "name": {"type": "string", "maxLength": 40},
                               "description": {"type": "string", "maxLength": 140}}}}}}}},
}


def flavor_request(scene: Scene) -> str:
    lines = [f"Мир: {scene.title}\n{scene.setting[:1500]}\n\nОсновные пути (id — название — суть; способности по рангам):"]
    for p in core_paths(scene):
        lines.append(f"\n{p.id} — {p.name} — {p.description}")
        for t in p.tiers:
            a = t.abilities[0]
            lines.append(f"  ранг {t.tier}: {a.name} ({M.ability_stats(a) or a.kind}) — {a.description}")
    lines.append("\nВерни все пять путей с теми же id и все пять способностей каждого по рангам.")
    return "\n".join(lines)


def apply_flavor(scene: Scene, data: dict) -> int:
    """Rename core paths and their abilities (also those already learned). Mechanics stay. Returns renamed count."""
    n = 0
    for raw in (data or {}).get("paths") or []:
        p = get_path(scene, str(raw.get("id") or ""))
        if not p or not p.core:
            continue
        name = re.sub(r"\s+", " ", str(raw.get("name") or "")).strip()[:40]
        if len(name) >= 3:
            p.name = name; n += 1
        if str(raw.get("description") or "").strip():
            p.description = str(raw["description"]).strip()[:200]
        for ra in raw.get("abilities") or []:
            t = next((t for t in p.tiers if t.tier == ra.get("rank")), None)
            an = re.sub(r"\s+", " ", str(ra.get("name") or "")).strip(" «»\"")[:40]
            if not t or len(an) < 3:
                continue
            a = t.abilities[0]; old = a.name
            a.name = an
            if str(ra.get("description") or "").strip():
                a.description = str(ra["description"]).strip()[:200]
            for c in scene.characters.values():
                for ca in c.abilities:
                    if ca.path == p.id and ca.tier == t.tier and ca.name == old:
                        ca.name, ca.description = a.name, a.description
    scene.core_flavored = True
    return n


async def flavor_core(llm, scene: Scene, seed: int = -1) -> dict:
    content, st = await llm.complete(render(FLAVOR_SYS, [{"role": "user", "content": flavor_request(scene)}], open_model=""),
                                     2200, stop=["<turn|>", "<|turn>"], temperature=0.6, seed=seed, json_schema=FLAVOR_SCHEMA)
    try:
        data = json.loads(content)
    except ValueError:
        data = {}
    apply_flavor(scene, data)
    return st | {"kind": "core flavor"}


# ---------------- leads: what the hero saw ----------------
def _sim(a: str, b: str) -> float:
    a, b = M.norm(a), M.norm(b)
    if not a or not b:
        return 0.0
    if a in b or b in a:
        return 1.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def _known_branch(scene: Scene, what: str) -> MagicPath | None:
    for p in branches(scene):
        names = [p.name, p.origin] + [a.name for t in p.tiers for a in t.abilities]
        if any(_sim(what, x) >= 0.8 for x in names if x):
            return p
    return None


def discover(scene: Scene, what: str, source: str = "", hint: str = "") -> tuple[bool, str]:
    """The hero saw a spell/technique. It goes to the «Следы» page of the Book of paths."""
    what = re.sub(r"\s+", " ", (what or "")).strip(" .«»\"")[:80]
    if len(what) < 3:
        return False, "Не понятно, что увидел герой."
    known = _known_branch(scene, what)
    if known:
        return False, f"«{what}» — уже известная отрасль «{known.name}» в Книге путей."
    lead = max(scene.leads, key=lambda l: _sim(what, l.what), default=None)
    if lead and _sim(what, lead.what) >= 0.75:
        if source and source not in lead.source:
            lead.source = (lead.source + "; " + source).strip("; ")[:200]
        return False, f"След «{lead.what}» уже есть в Книге путей."
    open_ = [l for l in scene.leads if not l.studied]
    if len(open_) >= 12:
        scene.leads.remove(open_[0])
    n = 1 + max((int(l.id.split("-")[-1]) for l in scene.leads if l.id.split("-")[-1].isdigit()), default=0)
    scene.leads.append(Lead(id=f"lead-{n}", what=what, source=(source or "").strip()[:200],
                            hint=(hint or "").strip()[:160], turn=scene.turn))
    return True, (f"Новый след в Книге путей: «{what}»" + (f" ({source.strip()})" if source.strip() else "")
                  + ". Узнай о нём больше — книга, наставник, долгое наблюдение — и откроется новая отрасль.")


def learn(scene: Scene, what: str, how: str = "") -> tuple[bool, str]:
    """The hero studied something (usually a lead). The branch is written by the AI after the turn."""
    what = re.sub(r"\s+", " ", (what or "")).strip(" .«»\"")[:80]
    if len(what) < 3:
        return False, "Не понятно, что изучил герой."
    lead = max(scene.leads, key=lambda l: _sim(what, l.what), default=None)
    if not lead or _sim(what, lead.what) < 0.5:
        known = _known_branch(scene, what)
        if known:
            return False, f"Отрасль «{known.name}» уже есть в Книге путей."
        n = 1 + max((int(l.id.split("-")[-1]) for l in scene.leads if l.id.split("-")[-1].isdigit()), default=0)
        lead = Lead(id=f"lead-{n}", what=what, turn=scene.turn)
        scene.leads.append(lead)
    if lead.studied:
        return False, f"«{lead.what}» уже изучено" + (f" — отрасль «{get_path(scene, lead.branch).name}»."
                                                     if lead.branch and get_path(scene, lead.branch) else ", отрасль пишется.")
    lead.studied = True; lead.how = (how or "").strip()[:200]
    if lead.id not in scene.pending_branches:
        scene.pending_branches.append(lead.id)
    return True, f"Изучено: «{lead.what}». Система откроет новую отрасль в Книге путей после этого хода."


# ---------------- AI: writing a branch ----------------
BRANCH_SYS = """Ты — архитектор прокачки текстовой RPG. Пиши по-русски. Ты создаёшь ОТРАСЛЬ — узкий путь развития,
который герой открыл, потому что УВИДЕЛ что-то в мире и ИЗУЧИЛ это. Отрасль растёт из одного (редко двух) основных путей.

# Что ты получаешь
- мир и его тон;
- что именно герой увидел, где и как изучил;
- пять основных путей (корни) с их сутью;
- уже существующие отрасли этого мира.

# Что сделать
1. Если увиденное — по сути то же, что одна из существующих отраслей, верни её id в same_as и больше ничего не придумывай
   (остальные поля заполни кратко, они будут проигнорированы). Иначе same_as = "".
2. Название отрасли: 2–4 слова, в духе мира и самого явления («Пламенный клинок», «Школа разрушения», «Культ Пепла»,
   «Нейровзлом»). Не «Путь X» и не просто имя стихии, если можно точнее.
3. Описание: одна фраза — что это за искусство и кто им владеет в этом мире.
4. Корни (parents): какой основной путь естественно ведёт к этому искусству. Магия-разрушение → Маг; святой воин → Воин + Дух;
   отравитель → Тень; укротитель зверей → Следопыт. Второй корень — только для настоящих гибридов. depth — насколько это
   продвинутое искусство: 1 — доступно новичку пути, 2 — нужен опыт, 3 — мастерское.
5. Три ступени, на каждой 1–2 способности. Правила способностей:
   - Ступень I обязательно содержит то самое, что герой увидел (или его ученическую версию) — игрок должен узнать «тот самый огненный шар».
   - Ступени II и III развивают тему: сильнее, шире, неожиданнее. Ступень III — визитная карточка мастера.
   - Хотя бы одна активная способность на отрасль и хотя бы одна пассивная на всю отрасль.
   - effect честно соответствует описанию: damage — наносит урон; heal — лечит; shield — поглощает урон; buff — усиливает
     атаки союзника или себя; debuff — ослабляет атаки врага; control — враг теряет ход (оглушение, паралич, сеть);
     utility — небоевое действие (свет, полёт, взлом, чтение мыслей), его эффект опишет рассказчик.
   - scale — атрибут, который усиливает способность: Сила — грубая мощь и оружие ближнего боя; Ловкость — точность, стрельба,
     быстрые приёмы; Выносливость — то, что держится на теле; Воля — вера, дух, сопротивление, лечение; Разум — учёная магия,
     техника; Влияние — голос, присутствие, команды союзникам.
   - Для пассивной способности выбери bonus: hp — больше здоровья; mp — больше маны; ac — труднее попасть; melee — урон в
     ближнем бою; ranged — урон дальним оружием; spell — сила заклинаний; heal — сила лечения; init — инициатива.
   - Название 1–3 слова, без кавычек. Описание — одна образная фраза о том, как это выглядит в мире.
   - НИКАКИХ чисел, кубиков, маны, длительностей и стоимостей — их назначит Система по единой шкале. Не пиши «наносит 10 урона».
   - Не повторяй способности основных путей и других отраслей.
6. trial — сюжетное условие для ступени III: конкретное событие, которое должно случиться в истории, привязанное к миру и
   к тому, как герой изучил искусство: «Превзойти наставника-пироманта в поединке», «Провести ночь в храме Пепла».
   Не «стать сильнее» и не номер ступени.

# Тон
Подстраивайся под мир: в мрачном фэнтези — мрачно, в киберпанке — технологично, в хорроре — жутко. Без юмора, если мир серьёзен."""


def branch_schema(scene: Scene) -> dict:
    ab = {"type": "object", "required": ["name", "kind", "effect", "scale", "bonus", "description"],
          "properties": {"name": {"type": "string", "maxLength": 40},
                         "kind": {"type": "string", "enum": ["active", "passive"]},
                         "effect": {"type": "string", "enum": EFFECTS},
                         "scale": {"type": "string", "enum": list(ATTRS)},
                         "bonus": {"type": "string", "enum": list(M.BONUS_KEYS)},
                         "description": {"type": "string", "maxLength": 160}}}
    return {
        "type": "object", "required": ["same_as", "name", "description", "parents", "trial", "tiers"],
        "properties": {
            "same_as": {"type": "string", "enum": [""] + [p.id for p in branches(scene)]},
            "name": {"type": "string", "maxLength": 40},
            "description": {"type": "string", "maxLength": 200},
            "parents": {"type": "array", "minItems": 1, "maxItems": 2, "items": {
                "type": "object", "required": ["path", "depth"],
                "properties": {"path": {"type": "string", "enum": CORE_IDS},
                               "depth": {"type": "integer", "minimum": 1, "maximum": 3}}}},
            "trial": {"type": "string", "maxLength": 140},
            "tiers": {"type": "array", "minItems": 3, "maxItems": 3, "items": {
                "type": "object", "required": ["abilities"],
                "properties": {"abilities": {"type": "array", "minItems": 1, "maxItems": 2, "items": ab}}}},
        },
    }


def branch_request(scene: Scene, lead: Lead) -> str:
    lines = [f"# Мир\n{scene.title}\n{scene.setting[:1500]}",
             f"\n# Что герой увидел\n{lead.what}" + (f"\nГде и у кого: {lead.source}" if lead.source else ""),
             f"Как изучил: {lead.how or 'узнал подробнее в ходе истории'}",
             "\n# Основные пути (корни)"]
    for p in core_paths(scene):
        lines.append(f"- {p.id}: {p.name} — {p.description} Способности: " + ", ".join(t.abilities[0].name for t in p.tiers))
    br = branches(scene)
    lines.append("\n# Уже существующие отрасли")
    lines += [f"- {p.id}: {p.name} — {p.description} Способности: " + ", ".join(a.name for t in p.tiers for a in t.abilities)
              for p in br] or ["нет"]
    p = scene.player
    lines.append(f"\n# Герой\n{p.name}, {p.kind or 'без вида'}. Способности: {', '.join(a.name for a in p.abilities) or 'нет'}.")
    lines.append("\nСоздай отрасль по инструкции.")
    return "\n".join(lines)


def balance_branch(scene: Scene, raw: dict, lead: Lead) -> MagicPath | None:
    """Clamp an AI branch onto the scale: tier t needs parent rank depth+t-1, power level depth+t, BRANCH_COST points;
    tier III also needs the trial (a story requirement)."""
    name = re.sub(r"\s+", " ", str(raw.get("name") or "")).strip(" «»\"")[:40]
    if len(name) < 3:
        return None
    parents: dict[str, int] = {}
    for x in raw.get("parents") or []:
        pid = str(x.get("path") or "")
        if pid in CORE_IDS and pid not in parents and len(parents) < 2:
            try:
                parents[pid] = max(1, min(3, int(x.get("depth") or 1)))
            except (TypeError, ValueError):
                parents[pid] = 1
    if not parents:
        parents = {"core-mag": 1}
    base = slug = "b-" + (store.slug(M.translit(name))[:30] or "branch")
    i = 1
    while get_path(scene, slug):
        i += 1; slug = f"{base}-{i}"
    trial = re.sub(r"\s+", " ", str(raw.get("trial") or "")).strip()[:140]
    if len(trial) < 8:
        trial = f"Пройти испытание мастера отрасли «{name}»"
    depth = max(parents.values())
    tiers, seen, has_active, has_passive = [], {M.norm(a.name) for p in scene.paths for t in p.tiers for a in t.abilities}, False, False
    for n, t in enumerate((raw.get("tiers") or [])[:3], 1):
        reqs = [PathReq(kind="rank", path=pid, value=min(5, d + n - 1)) for pid, d in parents.items()]
        reqs.append(PathReq(kind="points", value=BRANCH_COST[n]))
        if n == 3:
            reqs.append(PathReq(kind="story", id=f"{slug}-3", text=trial))
        abil = []
        for a in (t.get("abilities") or [])[:2]:
            an = re.sub(r"\s+", " ", str(a.get("name") or "")).strip(" «»\"")[:40]
            if len(an) < 3 or M.norm(an) in seen:
                continue
            seen.add(M.norm(an))
            kind = "passive" if a.get("kind") == "passive" else "active"
            eff = a.get("effect") if a.get("effect") in EFFECTS else "utility"
            abil.append(make_ability(an, kind, eff, depth + n, str(a.get("scale") or ""), str(a.get("bonus") or ""),
                                     str(a.get("description") or ""), slug, n))
            has_active |= kind == "active"; has_passive |= kind == "passive"
        if not abil:
            return None
        tiers.append(PathTier(tier=n, reqs=reqs, abilities=abil))
    if len(tiers) != 3 or not has_active:
        return None
    return MagicPath(id=slug, name=name, description=str(raw.get("description") or "")[:200], parents=parents,
                     origin=lead.what, tiers=tiers)


async def generate_branch(llm, scene: Scene, lead_id: str, seed: int = -1) -> tuple[str, dict | None]:
    """Write the branch for a studied lead. Returns (message for the player, LLM stats)."""
    lead = next((l for l in scene.leads if l.id == lead_id), None)
    if not lead or lead.branch:
        return "", None
    content, st = await llm.complete(render(BRANCH_SYS, [{"role": "user", "content": branch_request(scene, lead)}], open_model=""),
                                     1800, stop=["<turn|>", "<|turn>"], temperature=0.7, seed=seed,
                                     json_schema=branch_schema(scene))
    st = st | {"kind": "branch"}
    try:
        raw = json.loads(content)
    except ValueError:
        raw = {}
    same = get_path(scene, str(raw.get("same_as") or ""))
    if same and not same.core:
        lead.branch = same.id
        return f"«{lead.what}» — это отрасль «{same.name}», она уже есть в Книге путей.", st
    p = balance_branch(scene, raw, lead)
    if not p:
        lead.studied = False      # let the narrator/player retry later instead of losing the lead
        return f"Система не смогла описать отрасль «{lead.what}» — след остаётся в Книге путей.", st
    scene.paths.append(p)
    lead.branch = p.id
    roots = ", ".join(f"{get_path(scene, pid).name} {ROMAN[r]}" for pid, r in p.parents.items())
    return (f"⟦ КНИГА ПУТЕЙ ⟧ Открыта отрасль «{p.name}» (от: {roots}). {p.description} "
            f"Ступень I: {', '.join(a.name for a in p.tiers[0].abilities)}."), st


# ---------------- requirements & unlocking ----------------
def req_status(scene: Scene, c: Character, r: PathReq) -> tuple[bool, str]:
    if r.kind == "rank":
        pp = get_path(scene, r.path); have = c.paths.get(r.path, 0)
        return have >= r.value, f"{pp.name if pp else r.path} {ROMAN.get(r.value, r.value)} (сейчас {ROMAN.get(have, '—')})"
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


def tier_cost(t: PathTier) -> int:
    return next((r.value for r in t.reqs if r.kind == "points"), 0)


def tier_status(scene: Scene, c: Character, p: MagicPath) -> dict:
    """What the System (and the UI) show for the next tier of `p` for `c`."""
    t = next_tier(c, p)
    if t is None:
        return {"path": p.name, "tier": None, "done": True, "ok": False, "missing": [], "met": [], "abilities": []}
    met, missing = [], []
    for r in t.reqs:
        ok, text = req_status(scene, c, r)
        (met if ok else missing).append(text)
    return {"path": p.name, "tier": t.tier, "done": False, "ok": not missing, "missing": missing, "met": met,
            "cost": tier_cost(t), "abilities": [a.name for a in t.abilities]}


def _stems(s: str) -> list[str]:
    """Word stems, so Russian cases match: «тень», «тени», «Тени» -> «тен»."""
    words = re.findall(r"[а-яёa-z]+", (s or "").lower().replace("ё", "е"))
    return [w[:max(3, len(w) - 2)] if len(w) > 4 else w[:max(3, len(w) - 1)]
            for w in words if w not in ("путь", "пути", "путем", "школа", "школы", "магия", "магии", "для", "мне")]


def find_path(scene: Scene, name: str, c: Character | None = None) -> MagicPath | None:
    """By path name (any case form), or by the name of an ability it gives (next tier of `c` first)."""
    n = M.norm(re.sub(r"^(пут[ьиюё]|школ\w*|магии?|отрасл\w*)\s+", "", (name or "").strip(), flags=re.I))
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
    abil = [(a, p, t) for p in scene.paths for t in p.tiers for a in t.abilities]
    if c is not None:
        abil.sort(key=lambda x: 0 if next_tier(c, x[1]) is x[2] else 1)
    for a, p, _ in abil:
        an = M.norm(a.name)
        if n == an or (len(n) >= 4 and (n in an or an in n)):
            return p
    names = [M.norm(p.name) for p in scene.paths] + [M.norm(a.name) for a, _, _ in abil]
    m = difflib.get_close_matches(n, names, n=1, cutoff=0.6)
    if not m:
        return None
    return next((p for p in scene.paths if M.norm(p.name) == m[0]), None) or \
        next((p for a, p, _ in abil if M.norm(a.name) == m[0]), None)


def unlock_tier(scene: Scene, c: Character, path_name: str) -> tuple[bool, str]:
    """Open the next rank/tier of a path for a party member if every requirement holds; spends ability points."""
    if not M.in_party(scene, c):
        return False, f"{c.name} не в отряде — его путь Система не ведёт."
    if not (path_name or "").strip():
        return False, "Укажите путь или способность: «Система, открой путь Воина» / «Система, возьми Исцеление»."
    p = find_path(scene, path_name, c)
    if not p:
        return False, (f"Нет пути или способности «{path_name}». В Книге путей: "
                       + ", ".join(x.name for x in scene.paths) + ".")
    want = M.norm(path_name)
    ab_tier = next((t for t in p.tiers for a in t.abilities if want and (M.norm(a.name) == want or want in M.norm(a.name))),
                   None)
    if ab_tier is not None and _sim(path_name, p.name) < 0.6:
        nt = next_tier(c, p)
        known = next((a for a in c.abilities if M.norm(a.name) == want or want in M.norm(a.name)), None)
        if known:
            more = (f" Следующий ранг пути «{p.name}» — {ROMAN[nt.tier]}: " + ", ".join(a.name for a in nt.abilities)
                    + f" («Система, открой путь {p.name}»).") if nt else ""
            return False, f"«{known.name}» у {c.name} уже есть — очки не потрачены.{more}"
        if nt and ab_tier.tier != nt.tier:
            return False, (f"«{path_name}» — ранг {ROMAN[ab_tier.tier]} пути «{p.name}»; сначала ранг {ROMAN[nt.tier]}: "
                           + ", ".join(a.name for a in nt.abilities) + ".")
    s = tier_status(scene, c, p)
    if s["done"]:
        return False, f"{c.name} уже прошёл(ла) весь путь «{p.name}»."
    word = "ранг" if p.core else "ступень"
    if not s["ok"]:
        return False, f"{p.name}, {word} {ROMAN[s['tier']]} закрыт(а) для {c.name}. Не хватает: " + "; ".join(s["missing"]) + "."
    t = next_tier(c, p)
    pts = tier_cost(t)
    c.ability_points -= pts
    have = {M.norm(a.name) for a in c.abilities}
    got = []
    for a in t.abilities:
        if M.norm(a.name) not in have:
            c.abilities.append(a.model_copy(deep=True)); got.append(f"{a.name} ({M.ability_stats(a)})")
    c.paths[p.id] = t.tier
    extra = M.sync_derived(c)
    return True, (f"{c.name}: «{p.name}», {word} {ROMAN[t.tier]}. Новое: {', '.join(got) or '—'}. "
                  f"Потрачено очков способностей: {pts}, осталось {c.ability_points}."
                  + (" " + "; ".join(extra) + "." if extra else ""))


def fulfill(scene: Scene, what: str) -> tuple[bool, str]:
    """Narrator says a story condition happened: match it to an open story requirement."""
    reqs = [(r, p, t) for p in scene.paths for t in p.tiers for r in t.reqs if r.kind == "story" and r.id not in scene.flags]
    if not reqs:
        return False, "Открытых сюжетных условий нет."
    w = M.norm(what)

    def score(x):
        r, p = x[0], x[1]
        return difflib.SequenceMatcher(None, w, M.norm(r.text)).ratio() + \
            (0.5 if M.norm(p.name) in w or w in M.norm(r.text) else 0)
    best = max(reqs, key=score)
    if score(best) < 0.45:
        return False, "Не нашла такого условия. Открытые: " + "; ".join(r.text for r, _, _ in reqs[:6]) + "."
    r, p, t = best
    scene.flags[r.id] = scene.turn
    return True, f"Условие выполнено: «{r.text}» — отрасль «{p.name}», ступень {ROMAN[t.tier]}."


def open_story_conditions(scene: Scene, limit: int = 8) -> list[str]:
    return [f"{r.text} (отрасль «{p.name}», ступень {ROMAN[t.tier]})"
            for p in scene.paths for t in p.tiers for r in t.reqs if r.kind == "story" and r.id not in scene.flags][:limit]


def tail_block(scene: Scene) -> str:
    """Per-turn note for the narrator (in the turn tail, not the cached system prompt)."""
    leads = [l.what for l in scene.leads if not l.studied][-5:]
    conds = open_story_conditions(scene, 5)
    parts = []
    if leads:
        parts.append("следы (герой видел, ещё не изучил; <learn/> только когда он действительно изучит): " + "; ".join(leads))
    if conds:
        parts.append("сюжетные условия отраслей (<fulfill/> когда случится): " + "; ".join(conds))
    return ("[КНИГА ПУТЕЙ] " + ". ".join(parts) + ".") if parts else ""


# ---------------- views ----------------
def path_view(scene: Scene, p: MagicPath) -> dict:
    return {"id": p.id, "name": p.name, "description": p.description, "core": p.core, "origin": p.origin,
            "parents": [{"id": pid, "name": (get_path(scene, pid) or p).name, "rank": r} for pid, r in p.parents.items()],
            "tiers": [{"tier": t.tier, "cost": tier_cost(t),
                       "story": next((r.text for r in t.reqs if r.kind == "story"), ""),
                       "abilities": [{"name": a.name, "kind": a.kind, "description": a.description,
                                      "stats": M.ability_stats(a)} for a in t.abilities]} for t in p.tiers]}


def book_info(scene: Scene) -> dict:
    """The Book of paths for the client: paths, leads, and per party member the status of every next tier."""
    members = M.party_ids(scene)
    return {"paths": [path_view(scene, p) for p in scene.paths],
            "leads": [l.model_dump() for l in scene.leads if not l.branch],
            "pending": list(scene.pending_branches),
            "status": {cid: {p.id: tier_status(scene, scene.characters[cid], p) | {"have": scene.characters[cid].paths.get(p.id, 0)}
                             for p in scene.paths} for cid in members},
            "points": {cid: scene.characters[cid].ability_points for cid in members}}


def book_text(scene: Scene, c: Character) -> str:
    lines = [f"### ⟦ СИСТЕМА · КНИГА ПУТЕЙ ⟧ {c.name}", f"Очки способностей: **{c.ability_points}**", "", "**Основные пути**"]

    def row(p: MagicPath):
        have = c.paths.get(p.id, 0); s = tier_status(scene, c, p); word = "ранг" if p.core else "ступень"
        head = f"- **{p.name}** — " + (f"{word} {ROMAN[have]}/{ROMAN[len(p.tiers)]}" if have else "не начат")
        if s["done"]:
            return head + ". Пройден полностью."
        t = next_tier(c, p)
        abil = ", ".join(f"{a.name} ({M.ability_stats(a)})" for a in t.abilities)
        return (head + f". Далее {word} {ROMAN[t.tier]}: {abil} — {tier_cost(t)} очк. "
                + ("✔ можно открыть" if s["ok"] else "Не хватает: " + "; ".join(s["missing"])))
    lines += [row(p) for p in core_paths(scene)]
    br = branches(scene)
    if br:
        lines += ["", "**Отрасли**"]
        for p in br:
            roots = ", ".join(f"{(get_path(scene, pid) or p).name} {ROMAN[r]}" for pid, r in p.parents.items())
            lines.append(row(p) + f" _(корни: {roots})_")
    leads = [l for l in scene.leads if not l.branch]
    if leads:
        lines += ["", "**Следы**"]
        for l in leads:
            state = "изучено — отрасль пишется" if l.studied else (l.hint or "узнай больше: книга, наставник, наблюдение")
            lines.append(f"- {l.what}" + (f" ({l.source})" if l.source else "") + f" — {state}")
    lines.append("")
    lines.append("Открыть: «Система, открой путь …» или «Система, возьми <способность>».")
    return "\n".join(lines)
