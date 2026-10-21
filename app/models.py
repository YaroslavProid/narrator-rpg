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
    version: int = 1

    @property
    def player(self) -> Character:
        return self.characters[self.player_id]
