"""Pydantic models; they are exactly what is saved in data/scenes/<id>.json."""
from typing import Any, Literal
from pydantic import BaseModel, Field, field_validator, model_validator

# Fiction-agnostic attributes (not D&D ability-score labels as the only fiction).
# Broad capabilities: melee/physical, agility/stealth, stamina, will/perception,
# mind/knowledge/magic affinity, social presence — checks use these, not a skill list.
ATTRS = ["Сила", "Ловкость", "Выносливость", "Воля", "Разум", "Влияние"]

# Old D&D-ish names → new
ATTR_MIGRATE = {
    "Телосложение": "Выносливость",
    "Интеллект": "Разум",
    "Мудрость": "Воля",
    "Харизма": "Влияние",
}


class Condition(BaseModel):
    name: str
    turns_left: int = 1
    potency: int = 0


class Item(BaseModel):
    name: str
    qty: int = 1
    kind: str = "предмет"
    damage: str = ""
    armor: int = 0
    equipped: bool = False
    slot: str = ""
    effect: str = ""        # consumables: heal | mana | cure | damage | buff (set by combat.ensure_item_stats)
    power: int = 0          # 1..4 on the same scale as abilities


class Ability(BaseModel):
    """Named power: active (огненный шар) or passive (ночное зрение). Not 'melee/knowledge'."""
    name: str
    kind: Literal["active", "passive"] = "passive"
    description: str = ""
    cost: str = ""       # e.g. "2 MP", "1/сцену"
    cooldown: str = ""
    # mechanics (abilities from paths; free-form ones from cards leave them empty = narrative only)
    effect: Literal["", "damage", "heal", "shield", "buff", "debuff", "control", "utility", "passive"] = ""
    power: str = ""      # dice for damage/heal/shield ("2d6"), potency for buff/debuff, turns for control
    scale: str = ""      # attribute that amplifies it
    mp: int = 0
    bonus: dict[str, int] = {}   # passive: hp, mp, ac, melee, ranged, spell, heal, init
    path: str = ""       # path id it comes from
    tier: int = 0


class PathReq(BaseModel):
    """One requirement of a path tier. Numbers are checked by code; `story` ones are marked fulfilled
    by the narrator (<fulfill/>) when the event happens in the story."""
    kind: Literal["level", "attr", "points", "story", "rank"]
    attr: str = ""
    path: str = ""          # rank: parent path id
    value: int = 0
    id: str = ""            # story requirement id (scene.flags key)
    text: str = ""          # story requirement wording: «Найти наставника Тени»


class PathTier(BaseModel):
    tier: int
    reqs: list[PathReq] = []
    abilities: list[Ability] = []


class MagicPath(BaseModel):
    """A development path. core: one of the five paths present in every world (fixed mechanics, names flavoured
    by AI). Otherwise a branch: AI-generated after the hero studied something seen in the story; grows from
    `parents` (core path id -> rank needed for its first tier)."""
    id: str
    name: str
    description: str = ""
    attr: str = "Разум"     # legacy (old generated paths)
    core: bool = False
    parents: dict[str, int] = {}
    origin: str = ""        # what the hero saw that led here
    tiers: list[PathTier] = []


class Lead(BaseModel):
    """Something the hero saw (a spell, a technique) that may lead to a new branch once studied."""
    id: str
    what: str
    source: str = ""
    hint: str = ""          # how one could learn more
    turn: int = 0
    studied: bool = False
    how: str = ""           # how it was studied (book, mentor…)
    branch: str = ""        # branch path id once generated


class Reveal(BaseModel):
    """What the player has learned about this character (via осмотр / tags)."""
    kind: bool = False
    attrs: bool = False
    abilities: bool = False


class Character(BaseModel):
    id: str
    name: str
    description: str = ""
    personality: str = ""
    speech: str = ""
    secrets: str = ""
    aliases: list[str] = []
    is_player: bool = False
    kind: str = ""                    # type/вид: «наёмник», «демон»…
    level: int = 1
    hp: int = 20
    max_hp: int = 20
    mp: int = 10
    max_mp: int = 10
    exp: int = 0
    exp_next: int = 100
    attr_points: int = 0
    ability_points: int = 0
    hp_bonus: int = 0                 # part of max_hp that comes from attributes and passives (recomputed)
    mp_bonus: int = 0
    attrs: dict[str, int] = Field(default_factory=lambda: {a: 10 for a in ATTRS})
    abilities: list[Ability] = []
    inventory: list[Item] = []
    conditions: list[Condition] = []
    reveal: Reveal = Field(default_factory=Reveal)
    last_active_turn: int = 0
    side: Literal["neutral", "party", "hostile"] = "neutral"   # hero's party / foe / everyone else
    threat: str = ""                  # foes: слабый | обычный | опасный | элита | босс (sets stats and the EXP reward)
    inspect_turn: int = -100          # last failed «осмотри»: another try only a few turns later
    paths: dict[str, int] = {}        # path id -> highest rank/tier opened

    @model_validator(mode="before")
    @classmethod
    def _migrate(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        attrs = dict(data.get("attrs") or {})
        migrated = {}
        for k, v in attrs.items():
            nk = ATTR_MIGRATE.get(k, k)
            try:
                migrated[nk] = int(v)
            except (TypeError, ValueError):
                continue
        # ensure all ATTRS present
        data["attrs"] = {a: migrated.get(a, 10) for a in ATTRS}
        for a, v in migrated.items():
            if a in ATTRS:
                data["attrs"][a] = max(3, min(20, v))
        if "reveal" not in data or data["reveal"] is None:
            data["reveal"] = {}
        if data.get("is_player"):
            # player always "knows" self
            data["reveal"] = {"kind": True, "attrs": True, "abilities": True}
        return data


class Combatant(BaseModel):
    id: str
    initiative: int = 0
    acted: bool = False


class Combat(BaseModel):
    active: bool = False
    round: int = 0
    order: list[Combatant] = []
    current: int = 0
    awaiting: str = ""      # companion id whose turn waits for the hero's advice (or «пусть решает сама»)


class Event(BaseModel):
    turn: int
    kind: Literal["player", "narration", "speech", "system", "roll"]
    who: str | None = None
    text: str
    witnesses: list[str] = []


class Summary(BaseModel):
    layer: int
    from_turn: int
    to_turn: int
    text: str
    witnesses: list[str] = []


class Fact(BaseModel):
    id: int
    text: str
    known_by: list[str] = []
    turn: int = 0


class SceneSettings(BaseModel):
    length_mult: float = 1.0
    max_speaks: int | None = None
    inactive_leave: int | None = None


class Scene(BaseModel):
    id: str
    title: str
    setting: str = ""
    rule_mode: Literal["d20", "система"] = "d20"
    player_id: str
    characters: dict[str, Character] = {}
    present: list[str] = []
    initial_present: list[str] = []
    system_prompt: str = ""
    turns: list[dict] = []
    log: list[Event] = []
    turn: int = 0
    turn_exp: int = 0
    rng_seed: int = 0
    rng_calls: int = 0
    stats: list[dict] = []
    settings: SceneSettings = SceneSettings()
    summaries: list[Summary] = []
    facts: list[Fact] = []
    archived_upto: int = 0
    archived_turn: int = 0
    memory_injected: list[str] = []
    snapshots: list[dict] = []
    combat: Combat = Combat()
    director_log: list[str] = []
    party: list[str] = []            # companion ids (the hero is implicit), at most PARTY_MAX-1
    pending_fill: list[str] = []     # companions whose card the AI still has to complete
    turn_exp_by: dict[str, int] = {}  # EXP granted this turn per character (cap), reset each turn
    paths: list[MagicPath] = []      # core paths (always) + branches opened in this story
    core_flavored: bool = False      # core path names already adapted to this world by the AI
    leads: list[Lead] = []           # seen but not yet opened: the «Следы» page of the Book of paths
    pending_branches: list[str] = [] # studied leads whose branch the AI still has to write
    flags: dict[str, int] = {}       # fulfilled story requirements: id -> turn
    director_note: str = ""  # one-shot hint for narrator (from Director chat)
    regen: int = 0
    version: int = 4

    @property
    def player(self) -> Character:
        return self.characters[self.player_id]
