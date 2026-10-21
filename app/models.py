"""Pydantic models; they are exactly what is saved in data/scenes/<id>.json."""
from typing import Literal
from pydantic import BaseModel, Field

ATTRS = ["Сила", "Ловкость", "Телосложение", "Интеллект", "Мудрость", "Харизма"]


class Item(BaseModel):
    name: str
    qty: int = 1
    kind: str = "предмет"


class Character(BaseModel):
    id: str
    name: str
    description: str = ""
    personality: str = ""
    speech: str = ""          # manner of speech / example lines
    secrets: str = ""         # private notes, only shown to the character itself
    aliases: list[str] = []
    is_player: bool = False
    level: int = 1
    hp: int = 20
    max_hp: int = 20
    mp: int = 10
    max_mp: int = 10
    exp: int = 0
    exp_next: int = 100
    attr_points: int = 0
    attrs: dict[str, int] = Field(default_factory=lambda: {a: 10 for a in ATTRS})
    skills: dict[str, int] = {}
    inventory: list[Item] = []


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
    length_mult: float = 1.0          # scales the dynamic per-turn budget
    max_speaks: int | None = None     # optional user ceiling on character lines per turn


class Scene(BaseModel):
    id: str
    title: str
    setting: str = ""
    rule_mode: Literal["d20", "система"] = "d20"
    player_id: str
    characters: dict[str, Character] = {}
    present: list[str] = []
    initial_present: list[str] = []   # who was in the fixed block at creation
    system_prompt: str = ""           # rendered once at creation; byte-stable for the cache
    turns: list[dict] = []            # append-only raw prompt history [{role, content}]
    log: list[Event] = []
    turn: int = 0
    turn_exp: int = 0
    rng_seed: int = 0
    rng_calls: int = 0
    stats: list[dict] = []            # per-turn latency/cache metrics
    settings: SceneSettings = SceneSettings()
    summaries: list[Summary] = []     # layered history (layer 1 = archived blocks, 2+ = compressed)
    facts: list[Fact] = []
    archived_upto: int = 0            # index into turns: everything before is archived (summarised)
    archived_turn: int = 0            # game turns < this are archived
    memory_injected: list[str] = []   # chroma ids already shown in the unarchived window
    snapshots: list[dict] = []        # per-turn restore points for rewind/regenerate
    regen: int = 0
    version: int = 2

    @property
    def player(self) -> Character:
        return self.characters[self.player_id]
