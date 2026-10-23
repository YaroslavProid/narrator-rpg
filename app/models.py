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


class Ability(BaseModel):
    """Named power: active (огненный шар) or passive (ночное зрение). Not 'melee/knowledge'."""
    name: str
    kind: Literal["active", "passive"] = "passive"
    description: str = ""
    cost: str = ""       # e.g. "2 MP", "1/сцену"
    cooldown: str = ""


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
    skill_points: int = 0             # legacy alias; migrated → ability_points
    attrs: dict[str, int] = Field(default_factory=lambda: {a: 10 for a in ATTRS})
    abilities: list[Ability] = []
    skills: dict[str, int] = {}       # legacy; emptied on migrate
    inventory: list[Item] = []
    conditions: list[Condition] = []
    reveal: Reveal = Field(default_factory=Reveal)
    last_active_turn: int = 0

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
        # skill_points → ability_points
        if not data.get("ability_points") and data.get("skill_points"):
            data["ability_points"] = int(data.get("skill_points") or 0)
        # legacy skills dict → soft attr bumps (once), then clear
        legacy = data.get("skills") or {}
        if isinstance(legacy, dict) and legacy and not data.get("abilities"):
            # map known old skill names into attr bumps (≤+1 each)
            from_skill = {
                "Атлетика": "Сила", "Ближний бой": "Сила",
                "Акробатика": "Ловкость", "Скрытность": "Ловкость", "Ловкость рук": "Ловкость",
                "Взлом": "Ловкость", "Стрельба": "Ловкость",
                "Выносливость": "Выносливость",
                "Магия": "Разум", "Знания": "Разум", "Расследование": "Разум",
                "Восприятие": "Воля", "Проницательность": "Воля", "Выживание": "Воля", "Медицина": "Воля",
                "Убеждение": "Влияние", "Обман": "Влияние", "Запугивание": "Влияние", "Выступление": "Влияние",
            }
            bumps: dict[str, int] = {}
            for sk, lvl in legacy.items():
                attr = from_skill.get(sk) or ATTR_MIGRATE.get(sk)
                if not attr or attr not in ATTRS:
                    continue
                try:
                    bumps[attr] = bumps.get(attr, 0) + min(2, max(0, int(lvl)))
                except (TypeError, ValueError):
                    pass
            for attr, b in bumps.items():
                data["attrs"][attr] = max(3, min(20, int(data["attrs"].get(attr, 10)) + min(2, b)))
            data["skills"] = {}
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
    regen: int = 0
    version: int = 4

    @property
    def player(self) -> Character:
        return self.characters[self.player_id]
