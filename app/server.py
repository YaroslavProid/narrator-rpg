"""FastAPI app: REST for scenes/cards, WebSocket for turns."""
import argparse, asyncio, json, traceback
from contextlib import asynccontextmanager
import httpx
from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
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
    state["mem"] = mem; state["subs"] = {}; state["turn_running"] = False
    yield
    await state["llm"].close()

app = FastAPI(title="Narrator RPG", lifespan=lifespan)
WEB = config.ROOT / "web"
(WEB / "assets").mkdir(exist_ok=True)


@app.middleware("http")
async def revalidate_ui(request, call_next):
    """UI files change with the code; make the browser revalidate them instead of using a stale copy."""
    resp = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/assets/"):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


app.mount("/assets", StaticFiles(directory=WEB / "assets"), name="assets")


def get_scene(sid):
    if sid not in state["scenes"]:
        try:
            sc = store.load_scene(sid)
        except FileNotFoundError:
            raise HTTPException(404, "сцена не найдена")
        from .migrate import migrate
        migrate(sc)                       # older saves: new numbers, new narrator rules
        store.save_scene(sc)
        state["scenes"][sid] = sc
    return state["scenes"][sid]


class AbilityIn(BaseModel):
    name: str
    kind: str = "passive"  # active | passive
    description: str = ""
    cost: str = ""
    cooldown: str = ""


class PlayerIn(BaseModel):
    name: str
    description: str = ""
    personality: str = ""
    speech: str = ""
    kind: str = ""
    attrs: dict[str, int] = {}
    abilities: list[AbilityIn] = []
    skills: dict[str, int] = {}  # legacy ignored
    inventory: list[str] = []


class NpcIn(BaseModel):
    name: str = ""
    description: str = ""
    personality: str = ""
    speech: str = ""
    secrets: str = ""
    kind: str = ""
    abilities: list[AbilityIn] = []
    card: str = ""
    present: bool = True


class SceneIn(BaseModel):
    title: str
    setting: str
    premise: str = ""
    world: dict = {}
    rules: dict = {}
    rule_mode: str = "d20"
    player: PlayerIn
    npcs: list[NpcIn] = []
    seed: int | None = None


class NpcBriefIn(BaseModel):
    name: str = ""
    brief: str = ""


class GenerateIn(BaseModel):
    title: str = ""
    premise: str = ""
    rule_mode: str = "d20"
    player_name: str = ""
    player_brief: str = ""
    npcs: list[NpcBriefIn] = []
    npc_count: int = 0
    seed: int | None = None


@app.get("/")
async def index():
    return FileResponse(WEB / "index.html")


@app.get("/api/scenes")
async def scenes():
    return store.list_scenes()


@app.get("/api/status")
async def status():
    """Is llama-server reachable? Shown as a dot in the UI header."""
    llm = "down"
    try:
        r = await state["llm"].client.get(config.LLM_URL.rstrip("/") + "/health", timeout=3)
        llm = "ok" if r.status_code == 200 else ("loading" if r.status_code == 503 else "error")
    except httpx.HTTPError:
        pass
    return {"llm": llm, "llm_url": config.LLM_URL, "memory": state["mem"] is not None,
            "busy": state["turn_running"]}


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
    sc = create_scene(body.title, body.setting, body.player.model_dump(), npcs, body.rule_mode, body.seed,
                      premise=body.premise, world=body.world, rules=body.rules or None)
    state["scenes"][sc.id] = sc
    return {"id": sc.id}


@app.post("/api/scenes/generate")
async def generate_scene_draft(body: GenerateIn):
    """One-shot LLM draft: world, PC sheet+kit, NPCs. Preview before create."""
    if body.rule_mode not in ("d20", "система"):
        raise HTTPException(400, "rule_mode: d20 | система")
    npc_count = max(0, min(8, int(body.npc_count or 0)))
    briefs = [{"name": n.name, "brief": n.brief} for n in body.npcs if (n.name or n.brief).strip()]
    if not briefs and npc_count == 0:
        npc_count = 2
    from .generate import generate_scene
    try:
        draft = await generate_scene(
            state["llm"],
            title=body.title.strip(),
            premise=body.premise.strip(),
            rule_mode=body.rule_mode,
            player_name=body.player_name.strip(),
            player_brief=body.player_brief.strip(),
            npc_briefs=briefs,
            npc_count=npc_count,
            seed=body.seed if body.seed is not None else -1,
        )
    except Exception as e:
        raise HTTPException(502, f"генерация: {type(e).__name__}: {e}")
    return draft


@app.get("/api/scenes/{sid}")
async def scene_get(sid: str):
    return state["engine"].payload(get_scene(sid))


@app.delete("/api/scenes/{sid}")
async def scene_delete(sid: str):
    get_scene(sid)
    eng: TurnEngine = state["engine"]
    async with eng.lock:
        state["scenes"].pop(sid, None)
        store.delete_scene(sid)
        if state["mem"]:
            await asyncio.to_thread(state["mem"].drop, sid)
    for w in list(state["subs"].pop(sid, ())):
        try: await w.close()
        except Exception: pass
    return {"ok": True}


class SettingsIn(BaseModel):
    """Only the fields that are sent are changed; 0 / null = automatic."""
    length_mult: float | None = None
    max_speaks: int | None = None
    inactive_leave: int | None = None


@app.post("/api/scenes/{sid}/settings")
async def scene_settings(sid: str, body: SettingsIn):
    sc = get_scene(sid)
    sent = body.model_fields_set
    if "length_mult" in sent and body.length_mult is not None:
        sc.settings.length_mult = max(0.5, min(2.0, body.length_mult))
    if "max_speaks" in sent:
        sc.settings.max_speaks = body.max_speaks if body.max_speaks and body.max_speaks > 0 else None
    if "inactive_leave" in sent:
        sc.settings.inactive_leave = None if body.inactive_leave is None else max(0, min(50, body.inactive_leave))
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
    subs = state["subs"].setdefault(sid, set())
    subs.add(websocket)

    async def emit(m):
        """Broadcast to every open tab of this scene. A dropped socket (phone screen locked, tab closed)
        must not abort the running turn: it keeps going, gets saved, and the client reloads on reconnect."""
        data = json.dumps(m, ensure_ascii=False)
        for w in list(state["subs"].get(sid, ())):
            try:
                await w.send_text(data)
            except Exception:
                state["subs"].get(sid, set()).discard(w)

    async def after_turn():
        await eng.fill_party(sc, emit)      # a new companion's card first, then the Book of paths, then memory
        await eng.develop(sc, emit)
        await eng.background(sc, emit)

    await websocket.send_text(json.dumps({"type": "hello", "state": eng.state(sc), "busy": state["turn_running"]},
                                         ensure_ascii=False))
    if sc.turns and not eng.lock.locked():
        asyncio.create_task(eng.warm(sc))  # re-warm prompt cache after load
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
                t = msg.get("type")
                if t not in ("input", "continue", "regenerate", "rewind", "director", "director_audit"):
                    continue  # ping / unknown
                if state["turn_running"]:  # e.g. a second tab; background archiving does not count
                    await websocket.send_text(json.dumps({"type": "error", "text": "рассказчик ещё занят прошлым ходом"},
                                                         ensure_ascii=False))
                    continue
                state["turn_running"] = True
                try:
                    if t == "input":
                        if (msg.get("text") or "").strip():
                            await eng.handle(sc, msg["text"].strip(), emit)
                    elif t == "director":
                        # separate Director chat — never runs soft-meta / story auto-replies
                        txt = (msg.get("text") or "").strip()
                        await eng._director(sc, txt, emit, audit=False, chat=True)
                    elif t == "director_audit":
                        await eng._director(sc, (msg.get("text") or "").strip(), emit, audit=True, chat=True)
                    elif t == "continue":
                        await eng.handle(sc, None, emit)
                    elif t == "regenerate":
                        if await eng.regenerate(sc, emit) is None:
                            await emit({"type": "error", "text": "нечего переписывать"})
                    elif t == "rewind":
                        ok = await eng.rewind_locked(sc, int(msg.get("turn", 0)))
                        await emit({"type": "reload", "scene": eng.payload(sc), "busy": False} if ok
                                   else {"type": "error", "text": "нет точки отката для этого хода"})
                        continue
                finally:
                    state["turn_running"] = False
                asyncio.create_task(after_turn())
            except WebSocketDisconnect:
                raise
            except Exception as e:
                traceback.print_exc()
                await emit({"type": "error", "text": f"{type(e).__name__}: {e}"})
    except WebSocketDisconnect:
        return
    finally:
        state["subs"].get(sid, set()).discard(websocket)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=config.HOST)
    ap.add_argument("--port", type=int, default=config.PORT)
    a = ap.parse_args()
    import uvicorn
    uvicorn.run(app, host=a.host, port=a.port, log_level="info")


if __name__ == "__main__":
    main()
