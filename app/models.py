"""Pydantic models; they are exactly what is saved in data/scenes/<id>.json."""
from typing import Literal
from pydantic import BaseModel, Field

ATTRS = ["Сила", "Ловкость", "Телосложение", "Интеллект", "Мудрость", "Харизма"]


class Condition(BaseModel):
    name: str
    turns_left: int = 1
    potency: int = 0          # e.g. bleed damage per turn


class Item(BaseModel):
    name: str
    qty: int = 1
    kind: str = "предмет"     # оружие | броня | расходник | предмет
    damage: str = ""          # "1d8", "1d4+1" for weapons
    armor: int = 0            # AC bonus when equipped
    equipped: bool = False
    slot: str = ""            # рука | тело | …


class Character(BaseModel):
    id: str
    name: str
    description: str = ""
    personality: str = ""
    speech: str = ""
    secrets: str = ""
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
    skill_points: int = 0
    attrs: dict[str, int] = Field(default_factory=lambda: {a: 10 for a in ATTRS})
    skills: dict[str, int] = {}
    inventory: list[Item] = []
    conditions: list[Condition] = []
    last_active_turn: int = 0   # last turn spoken / mentioned / acted


class Combatant(BaseModel):
    id: str
    initiative: int = 0
    acted: bool = False


class Combat(BaseModel):
    active: bool = False
    round: int = 0
    order: list[Combatant] = []
    current: int = 0


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
    inactive_leave: int | None = None   # None → config.INACTIVE_LEAVE


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
    regen: int = 0
    version: int = 3

    @property
    def player(self) -> Character:
        return self.characters[self.player_id]
