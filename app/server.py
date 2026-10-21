"""FastAPI app: REST for scenes/cards, WebSocket for turns."""
import argparse, asyncio, json
from contextlib import asynccontextmanager
from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel
from . import config, store
from .cards import parse_card
from .engine import TurnEngine, create_scene
from .llm import LLM

state = {}


@asynccontextmanager
async def lifespan(app):
    mem = None
    if config.MEMORY and (config.EMBED_DIR / "model_quantized.onnx").exists():
        from .memory import Embedder, VectorStore
        mem = VectorStore(Embedder())
    elif config.MEMORY:
        print("WARNING: embedding model missing in", config.EMBED_DIR, "- vector memory disabled")
    state["llm"] = LLM(); state["engine"] = TurnEngine(state["llm"], mem); state["scenes"] = {}
    yield
    await state["llm"].close()

app = FastAPI(title="Narrator RPG", lifespan=lifespan)
WEB = config.ROOT / "web"


def get_scene(sid):
    if sid not in state["scenes"]:
        try:
            state["scenes"][sid] = store.load_scene(sid)
        except FileNotFoundError:
            raise HTTPException(404, "сцена не найдена")
    return state["scenes"][sid]


class PlayerIn(BaseModel):
    name: str
    description: str = ""
    attrs: dict[str, int] = {}
    skills: dict[str, int] = {}
    inventory: list[str] = []


class NpcIn(BaseModel):
    name: str = ""
    description: str = ""
    personality: str = ""
    speech: str = ""
    secrets: str = ""
    card: str = ""        # raw pasted card (JSON V2/V3 or text); overrides fields
    present: bool = True


class SceneIn(BaseModel):
    title: str
    setting: str
    rule_mode: str = "d20"
    player: PlayerIn
    npcs: list[NpcIn] = []
    seed: int | None = None


@app.get("/")
async def index():
    return FileResponse(WEB / "index.html")


@app.get("/api/scenes")
async def scenes():
    return store.list_scenes()


@app.post("/api/scenes")
async def new_scene(body: SceneIn):
    if body.rule_mode not in ("d20", "система"):
        raise HTTPException(400, "rule_mode: d20 | система")
    npcs = []
    for n in body.npcs:
        d = n.model_dump()
        if n.card.strip():
            try:
                d |= {k: v for k, v in parse_card(n.card.encode("utf-8")).items() if v}
            except Exception as e:
                raise HTTPException(400, f"карточка: {e}")
        if not d["name"].strip():
            raise HTTPException(400, "у NPC нет имени")
        npcs.append(d)
    sc = create_scene(body.title, body.setting, body.player.model_dump(), npcs, body.rule_mode, body.seed)
    state["scenes"][sc.id] = sc
    return {"id": sc.id}


@app.get("/api/scenes/{sid}")
async def scene_get(sid: str):
    sc = get_scene(sid)
    return {"id": sc.id, "title": sc.title, "setting": sc.setting, "rule_mode": sc.rule_mode,
            "log": [e.model_dump() for e in sc.log], "state": state["engine"].state(sc), "stats": sc.stats[-20:]}


class SettingsIn(BaseModel):
    length_mult: float = 1.0
    max_speaks: int | None = None


@app.post("/api/scenes/{sid}/settings")
async def scene_settings(sid: str, body: SettingsIn):
    sc = get_scene(sid)
    sc.settings.length_mult = max(0.5, min(2.0, body.length_mult))
    sc.settings.max_speaks = body.max_speaks if body.max_speaks and body.max_speaks > 0 else None
    store.save_scene(sc)
    return sc.settings.model_dump()


@app.post("/api/cards/parse")
async def card_parse(file: UploadFile = File(...)):
    try:
        return parse_card(await file.read(), file.filename or "")
    except Exception as e:
        raise HTTPException(400, f"не удалось прочитать карточку: {e}")


@app.websocket("/ws/{sid}")
async def ws(websocket: WebSocket, sid: str):
    await websocket.accept()
    try:
        sc = get_scene(sid)
    except HTTPException:
        await websocket.send_json({"type": "error", "text": "сцена не найдена"}); await websocket.close(); return
    eng: TurnEngine = state["engine"]

    async def emit(m):
        await websocket.send_text(json.dumps(m, ensure_ascii=False))

    await emit({"type": "hello", "state": eng.state(sc)})
    if sc.turns and not eng.lock.locked():
        asyncio.create_task(eng.warm(sc))  # re-warm prompt cache after load
    try:
        while True:
            msg = json.loads(await websocket.receive_text())
            try:
                t = msg.get("type")
                if t == "input" and (msg.get("text") or "").strip():
                    await eng.handle(sc, msg["text"].strip(), emit)
                elif t == "continue":
                    await eng.handle(sc, None, emit)
                elif t == "regenerate":
                    if await eng.regenerate(sc, emit) is None:
                        await emit({"type": "error", "text": "нечего переписывать"})
                elif t == "rewind":
                    ok = await eng.rewind_locked(sc, int(msg.get("turn", 0)))
                    await emit({"type": "reload"} if ok else {"type": "error", "text": "нет точки отката"})
                    continue
                else:
                    continue
                asyncio.create_task(eng.background(sc, emit))
            except WebSocketDisconnect:
                raise
            except Exception as e:
                await emit({"type": "error", "text": f"{type(e).__name__}: {e}"})
    except WebSocketDisconnect:
        return


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=config.HOST)
    ap.add_argument("--port", type=int, default=config.PORT)
    a = ap.parse_args()
    import uvicorn
    uvicorn.run(app, host=a.host, port=a.port, log_level="info")


if __name__ == "__main__":
    main()
