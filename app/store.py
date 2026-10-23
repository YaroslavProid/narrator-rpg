"""JSON save/load under data/."""
import json, re, time
from . import config
from .models import Character, Scene


def slug(s: str) -> str:
    from .mechanics import translit
    s = translit(s)
    s = re.sub(r"[^\w\-]+", "-", s.strip().lower(), flags=re.U).strip("-")
    return s[:40] or "scene"


def scenes_dir():
    d = config.DATA / "scenes"; d.mkdir(parents=True, exist_ok=True); return d


def save_scene(scene: Scene):
    p = scenes_dir() / f"{scene.id}.json"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(scene.model_dump_json(indent=1), encoding="utf-8")
    tmp.replace(p)


def load_scene(scene_id: str) -> Scene:
    if not re.fullmatch(r"[\w\-]+", scene_id):
        raise FileNotFoundError(scene_id)
    return Scene.model_validate_json((scenes_dir() / f"{scene_id}.json").read_text(encoding="utf-8"))


def list_scenes() -> list[dict]:
    out = []
    for p in sorted(scenes_dir().glob("*.json"), key=lambda p: -p.stat().st_mtime):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            out.append({"id": d["id"], "title": d["title"], "rule_mode": d["rule_mode"], "turn": d["turn"],
                        "updated": time.strftime("%Y-%m-%d %H:%M", time.localtime(p.stat().st_mtime))})
        except Exception:
            continue
    return out


def new_scene_id(title: str) -> str:
    base = slug(title); i = 1; sid = base
    while (scenes_dir() / f"{sid}.json").exists():
        i += 1; sid = f"{base}-{i}"
    return sid


def save_card(c: Character):
    d = config.DATA / "characters"; d.mkdir(parents=True, exist_ok=True)
    (d / f"{slug(c.name)}.json").write_text(c.model_dump_json(indent=1), encoding="utf-8")


def delete_scene(scene_id: str):
    if not re.fullmatch(r"[\w\-]+", scene_id):
        raise FileNotFoundError(scene_id)
    (scenes_dir() / f"{scene_id}.json").unlink(missing_ok=True)
