"""TurnEngine: narrator-orchestrated turn loop on a single llama.cpp slot, append-only prompt,
dynamic per-turn budget, long-term memory (archive/summaries/facts/retrieval), rewind/regenerate."""
import asyncio, itertools, json, re, time
from . import config, mechanics as M, prompts as P, store
from . import memory as MEM
from . import combat as C
from .budget import turn_budget
from .models import Character, Event, Scene, Summary
from .prompt import render
from .protocol import (clean_speech, cyr_share, parse_step, parse_tag, split_speech, tag_grammar, visible_text,
                       ParseError)

_seg = itertools.count(1)
STATE_RE = re.compile(r"^\[СОСТОЯНИЕ\] Ход (\d+)\.")


def new_character(scene: Scene, name: str, desc: str = "", **kw) -> Character:
    base = store.slug(M.translit(name)) or "npc"
    cid = base; i = 1
    while cid in scene.characters:
        i += 1; cid = f"{base}-{i}"
    c = Character(id=cid, name=name.strip()[:40], description=desc.strip(), **kw)
    scene.characters[cid] = c
    return c


def create_scene(title: str, setting: str, player: dict, npcs: list[dict], rule_mode: str, seed: int | None = None) -> Scene:
    scene = Scene(id=store.new_scene_id(title), title=title.strip(), setting=setting.strip(), rule_mode=rule_mode,
                  player_id="", rng_seed=seed if seed is not None else int(time.time()))
    scene.settings.length_mult = config.LENGTH_MULT
    pdata = {k: v for k, v in player.items() if k in ("personality", "speech")}
    p = new_character(scene, player["name"], player.get("description", ""), is_player=True, **pdata)
    scene.player_id = p.id
    if player.get("kind"):
        p.kind = str(player["kind"])[:60]
    for a, v in (player.get("attrs") or {}).items():
        a2 = M.resolve_attr(a)
        if a2: p.attrs[a2] = max(3, min(20, int(v)))
    from .models import Ability
    for ab in player.get("abilities") or []:
        if isinstance(ab, str):
            p.abilities.append(Ability(name=ab[:40], kind="passive"))
        elif isinstance(ab, dict) and ab.get("name"):
            kind = ab.get("kind") or "passive"
            if kind not in ("active", "passive"):
                kind = "passive"
            p.abilities.append(Ability(
                name=str(ab["name"])[:40], kind=kind,
                description=str(ab.get("description") or "")[:200],
                cost=str(ab.get("cost") or "")[:40],
                cooldown=str(ab.get("cooldown") or "")[:40],
            ))
    p.reveal.kind = p.reveal.attrs = p.reveal.abilities = True
    for it in player.get("inventory") or []:
        M.apply_delta(scene, p, {"add": it})
    scene.present = [p.id]
    for n in npcs:
        c = new_character(scene, n["name"], n.get("description", ""), personality=n.get("personality", ""),
                          speech=n.get("speech", ""), secrets=n.get("secrets", ""),
                          kind=str(n.get("kind") or "")[:60])
        for ab in n.get("abilities") or []:
            if isinstance(ab, dict) and ab.get("name"):
                kind = ab.get("kind") or "passive"
                if kind not in ("active", "passive"):
                    kind = "passive"
                c.abilities.append(Ability(
                    name=str(ab["name"])[:40], kind=kind,
                    description=str(ab.get("description") or "")[:200],
                    cost=str(ab.get("cost") or "")[:40],
                    cooldown=str(ab.get("cooldown") or "")[:40],
                ))
        if n.get("present", True):
            scene.present.append(c.id)
        store.save_card(c)
    scene.initial_present = list(scene.present)
    from . import magic as G
    G.ensure_core(scene)
    p.ability_points = max(p.ability_points, 1)      # the first step on a path is the player's choice
    M.sync_all(scene)
    scene.system_prompt = P.build_system(scene)
    store.save_scene(scene)
    return scene


class TurnEngine:
    def __init__(self, llm, memory: "MEM.VectorStore | None" = None):
        self.llm = llm
        self.mem = memory
        self.lock = asyncio.Lock()
        self.intent = {}                      # scene id -> this turn's parsed player input (intent.Intent | None)
        self.comp_turn = {}                   # scene id -> companion whose turn this is (after the hero's advice)
        self.reacted = {}                     # scene id -> narrator step at which a free reaction was settled
        self.bg_running = False
        self.bg_cancel = asyncio.Event()   # set when a player turn wants the slot

    # ---------- helpers ----------
    def names(self, scene):
        return [c.name for c in scene.characters.values()]

    def log(self, scene, kind, text, who=None):
        scene.log.append(Event(turn=scene.turn, kind=kind, who=who, text=text, witnesses=list(scene.present)))

    def recent_knows(self, scene, c):
        out = []
        for e in scene.log:
            if e.turn >= scene.archived_turn and c.id in e.witnesses and e.kind != "system":
                who = e.who or ("Рассказчик" if e.kind == "narration" else "")
                out.append((f"{who}: " if who else "") + e.text[:200])
        return out

    knows = recent_knows

    async def sys_msg(self, emit, scene, text, kind="system"):
        await emit({"type": "segment", "id": next(_seg), "kind": kind, "who": "Система", "text": text})

    def state(self, scene):
        from . import magic as G
        in_combat = bool(scene.combat and scene.combat.active)
        chars = {k: M.public_char_dict(v, in_combat=in_combat) for k, v in scene.characters.items()}
        return {"player_id": scene.player_id, "present": scene.present, "rule_mode": scene.rule_mode, "turn": scene.turn,
                "settings": scene.settings.model_dump(), "summaries": len(scene.summaries), "facts": len(scene.facts),
                "archived_turn": scene.archived_turn, "combat": scene.combat.model_dump(),
                "director_log": list(getattr(scene, "director_log", []) or [])[-24:],
                "party": M.party_ids(scene), "party_max": config.PARTY_MAX, "party_info": self.party_info(scene),
                "book": G.book_info(scene), "threat": C.threat_info(scene),
                "characters": chars}

    def party_info(self, scene):
        """Per companion: EXP catch-up multiplier, whether the AI is still filling the card."""
        return {cid: {"filling": cid in scene.pending_fill}
                for cid in scene.party if (c := scene.characters.get(cid))}

    def payload(self, scene):
        """Full scene view for the client (initial load and `reload`)."""
        return {"id": scene.id, "title": scene.title, "setting": scene.setting, "rule_mode": scene.rule_mode,
                "turn": scene.turn, "log": [e.model_dump() for e in scene.log], "state": self.state(scene),
                "stats": scene.stats[-20:],
                "rewind_from": (scene.snapshots[0]["turn"] + 1) if scene.snapshots else None}

    def prompt(self, scene, open_model=None, extra=None):
        pre = []
        mb = MEM.memory_block(scene)
        if mb:
            pre = [{"role": "user", "content": mb}]
        return render(scene.system_prompt, pre + scene.turns[scene.archived_upto:] + (extra or []), open_model)

    def seed(self, scene, n):
        return scene.rng_seed + scene.turn * 1000 + scene.regen * 97 + n

    # ---------- public ----------
    async def handle(self, scene: Scene, text: str | None, emit) -> dict:
        self.bg_cancel.set()                 # ask background to yield the slot
        async with self.lock:
            self.bg_cancel.clear()
            if text is not None:
                cmd = M.parse_system_command(text)
                if cmd:
                    await emit({"type": "player", "text": text})
                    if cmd[0] == "ask":
                        return await self._system_agent(scene, text, emit)
                    if cmd[0] in ("director", "director_audit"):
                        return await self._director(scene, cmd[1], emit, audit=(cmd[0] == "director_audit"))
                    out = M.run_system_command(scene, *cmd)
                    M.sync_all(scene)
                    await self.sys_msg(emit, scene, out)
                    scene.log.append(Event(turn=scene.turn, kind="system", who="Система", text=out, witnesses=[scene.player_id]))
                    store.save_scene(scene)
                    st = {"turn": scene.turn, "command": cmd[0], "llm_calls": [], "wall_s": 0}
                    await emit({"type": "turn_end", "stats": st, "state": self.state(scene)})
                    return st
            return await self._turn(scene, text, emit)

    async def _director(self, scene: Scene, order: str, emit, *, audit: bool = False, chat: bool = False) -> dict:
        """Director LLM. chat=True: separate pane only (no soft-meta, no story feed flood)."""
        from . import director as D
        rest = re.sub(
            r"^(директор|director|режиссёр|режиссер|проверь\s+карточки|проверить\s+карточки|аудит)\s*[,:]?\s*",
            "", (order or "").strip(), flags=re.I,
        ).strip()
        if not rest and not audit:
            rest = (
                "Привет. Коротко представься как Директор и скажи, чем можешь помочь "
                "(совет по сюжету/тону, правки карточек, подсказка рассказчику). "
                "Правок без просьбы не делай."
            )
        try:
            result = await D.run_director(self.llm, scene, rest, audit=audit, seed=self.seed(scene, 77))
        except Exception as e:
            err = f"⟦ ДИРЕКТОР ⟧ ошибка: {type(e).__name__}: {e}"
            if chat:
                await emit({"type": "director_msg", "who": "Директор", "text": err})
            else:
                await self.sys_msg(emit, scene, err)
                scene.log.append(Event(turn=scene.turn, kind="system", who="Директор", text=err, witnesses=[scene.player_id]))
            store.save_scene(scene)
            st = {"turn": scene.turn, "command": "director", "error": str(e), "llm_calls": [], "wall_s": 0}
            await emit({"type": "turn_end", "stats": st, "state": self.state(scene)})
            return st
        reply = (result.get("reply") or result.get("text") or "").strip()
        M.sync_all(scene)                    # patched attributes / abilities change max HP/MP
        if chat:
            # stay in Director pane — do not inject into story transcript
            await emit({"type": "director_msg", "who": "Директор", "text": reply})
        else:
            text = "### ⟦ ДИРЕКТОР ⟧\n" + result["text"]
            await self.sys_msg(emit, scene, text)
            scene.log.append(Event(turn=scene.turn, kind="system", who="Директор", text=text, witnesses=[scene.player_id]))
        store.save_scene(scene)
        if result.get("rebuilt"):
            try:
                await self.llm.warm(self.prompt(scene))
            except Exception:
                pass
        st = {"turn": scene.turn, "command": "director", "llm_calls": [result.get("stats") or {}],
              "wall_s": (result.get("stats") or {}).get("wall_s"), "rebuilt": result.get("rebuilt"),
              "chat": bool(chat)}
        await emit({"type": "turn_end", "stats": st, "state": self.state(scene)})
        return st

    async def fill_party(self, scene, emit=None):
        """A new companion's card is often a sketch (name + two words). After the turn, ask the Director to
        complete it consistently with the story; the player sees the result in the party sheet."""
        if not scene.pending_fill:
            return
        from . import director as D
        async with self.lock:
            while scene.pending_fill:
                cid = scene.pending_fill[0]
                c = scene.characters.get(cid)
                if not c or cid not in scene.party:
                    scene.pending_fill.pop(0)
                    continue
                order = (f"{c.name} только что вступил(а) в отряд героя. Заполни карточку {c.name} полностью и "
                         f"согласованно с описанием и тем, что о нём известно из сцены: вид/класс (kind), внешность и роль "
                         f"(description), характер, манера речи, атрибуты (3–18, по роли), 1–3 способности (активные и "
                         f"пассивные), снаряжение по роли (оружие и броня). Уже заданное не противоречь, только дополняй. "
                         f"Других персонажей не трогай.")
                try:
                    res = await D.run_director(self.llm, scene, order, seed=self.seed(scene, 91))
                    text = f"⟦ ОТРЯД ⟧ Карточка {c.name} дописана.\n" + (res.get("text") or "").strip()
                except Exception as e:
                    text = f"⟦ ОТРЯД ⟧ Не удалось дописать карточку {c.name}: {type(e).__name__}: {e}"
                scene.pending_fill.pop(0)
                c.side = "party"; c.reveal.kind = c.reveal.attrs = c.reveal.abilities = True
                scene.log.append(Event(turn=scene.turn, kind="system", who="Система", text=text, witnesses=[scene.player_id]))
                store.save_scene(scene)
                if emit:
                    try:
                        await emit({"type": "segment", "id": next(_seg), "kind": "system", "who": "Система", "text": text})
                        await emit({"type": "memory", "text": f"карточка {c.name} заполнена", "state": self.state(scene)})
                    except Exception:
                        pass

    async def develop(self, scene, emit=None):
        """After a turn, while the player reads: adapt core path names to the world (once) and write the branches
        of leads the hero studied. Separate prompts evict the narrator cache, so re-warm afterwards."""
        from . import magic as G
        if scene.core_flavored and not scene.pending_branches:
            return
        async with self.lock:
            did = False
            if not scene.core_flavored:
                try:
                    await G.flavor_core(self.llm, scene, seed=self.seed(scene, 53)); did = True
                except Exception as e:
                    print("core flavor error", e)
                    scene.core_flavored = True          # names stay default; never retry every turn
            while scene.pending_branches and not self.bg_cancel.is_set():
                lid = scene.pending_branches[0]
                try:
                    text, _ = await G.generate_branch(self.llm, scene, lid, seed=self.seed(scene, 57)); did = True
                except Exception as e:
                    text = f"⟦ КНИГА ПУТЕЙ ⟧ Не удалось описать отрасль: {type(e).__name__}: {e}"
                    lead = next((l for l in scene.leads if l.id == lid), None)
                    if lead:
                        lead.studied = False
                scene.pending_branches.pop(0)
                if text:
                    scene.log.append(Event(turn=scene.turn, kind="system", who="Система", text=text, witnesses=[scene.player_id]))
                    if emit:
                        try: await emit({"type": "segment", "id": next(_seg), "kind": "system", "who": "Система", "text": text})
                        except Exception: pass
            store.save_scene(scene)
            if emit:
                try: await emit({"type": "memory", "text": "книга путей обновлена", "state": self.state(scene)})
                except Exception: pass
            if did and not self.bg_cancel.is_set():
                try: await self.llm.warm(self.prompt(scene))
                except Exception: pass

    async def _system_agent(self, scene, text, emit) -> dict:
        """'Система, …' questions — LLM answers, code checks any action. No story turn."""
        from . import system_agent as SA
        t0 = time.time(); calls = []
        try:
            res = await SA.ask(self.llm, scene, re.sub(r"^\W*(система|system)\W*", "", text, flags=re.I),
                               seed=self.seed(scene, 63))
            out, calls = res["text"], res["calls"]
            M.sync_all(scene)
        except Exception as e:
            out = f"⟦ СИСТЕМА ⟧ Не удалось ответить: {type(e).__name__}: {e}"
        await self.sys_msg(emit, scene, out)
        scene.log.append(Event(turn=scene.turn, kind="system", who="Система", text=out, witnesses=[scene.player_id]))
        store.save_scene(scene)
        st = {"turn": scene.turn, "command": "ask", "llm_calls": calls, "wall_s": round(time.time() - t0, 2),
              "prompt_n": sum(c.get("prompt_n") or 0 for c in calls), "cache_n": sum(c.get("cache_n") or 0 for c in calls)}
        await emit({"type": "turn_end", "stats": st, "state": self.state(scene)})
        return st

    async def warm(self, scene):
        async with self.lock:
            try:
                return await self.llm.warm(self.prompt(scene))
            except Exception:
                return None

    # ---------- rewind / regenerate ----------
    def snapshot(self, scene, text):
        return {"turn": scene.turn, "input": text, "turns_len": len(scene.turns), "log_len": len(scene.log),
                "characters": {k: v.model_dump() for k, v in scene.characters.items()}, "present": list(scene.present),
                "rng_calls": scene.rng_calls, "summaries": [s.model_dump() for s in scene.summaries],
                "facts": [f.model_dump() for f in scene.facts], "archived_upto": scene.archived_upto,
                "archived_turn": scene.archived_turn, "injected": list(scene.memory_injected),
                "stats_len": len(scene.stats), "combat": scene.combat.model_dump(),
                "party": list(scene.party), "pending_fill": list(scene.pending_fill), "flags": dict(scene.flags),
                "paths": [x.model_dump() for x in scene.paths], "leads": [x.model_dump() for x in scene.leads],
                "pending_branches": list(scene.pending_branches)}

    def rewind(self, scene: Scene, to_turn: int) -> bool:
        """Undo every turn >= to_turn (restore the snapshot taken when turn `to_turn` started)."""
        idx = next((i for i, s in enumerate(scene.snapshots) if s["turn"] == to_turn - 1), None)
        if idx is None:
            return False
        s = scene.snapshots[idx]
        from .models import Character as Ch, Fact, Summary as S, Combat
        scene.turns = scene.turns[:s["turns_len"]]; scene.log = scene.log[:s["log_len"]]
        scene.characters = {k: Ch.model_validate(v) for k, v in s["characters"].items()}
        scene.present = s["present"]; scene.rng_calls = s["rng_calls"]
        scene.summaries = [S.model_validate(x) for x in s["summaries"]]; scene.facts = [Fact.model_validate(x) for x in s["facts"]]
        scene.archived_upto = s["archived_upto"]; scene.archived_turn = s["archived_turn"]
        scene.memory_injected = s["injected"]; scene.stats = scene.stats[:s["stats_len"]]
        scene.combat = Combat.model_validate(s.get("combat") or {})
        scene.party = list(s.get("party") or []); scene.pending_fill = list(s.get("pending_fill") or [])
        scene.flags = dict(s.get("flags") or {})
        if "paths" in s:
            from .models import Lead, MagicPath
            scene.paths = [MagicPath.model_validate(x) for x in s["paths"]]
            scene.leads = [Lead.model_validate(x) for x in s.get("leads") or []]
            scene.pending_branches = list(s.get("pending_branches") or [])
        scene.turn = s["turn"]; scene.snapshots = scene.snapshots[:idx]
        if self.mem:
            try: self.mem.delete_from(scene.id, to_turn)
            except Exception: pass
        store.save_scene(scene)
        return True

    async def rewind_locked(self, scene, to_turn):
        async with self.lock:
            return self.rewind(scene, to_turn)

    async def regenerate(self, scene: Scene, emit) -> dict | None:
        self.bg_cancel.set()
        async with self.lock:
            self.bg_cancel.clear()
            if not scene.snapshots:
                return None
            backup = scene.model_copy(deep=True)
            s = scene.snapshots[-1]
            self.rewind(scene, s["turn"] + 1)
            scene.regen += 1
            await emit({"type": "reload", "scene": self.payload(scene), "busy": True})
            try:
                return await self._turn(scene, s["input"], emit, rollback=False)
            except Exception:
                # keep the old version of the turn instead of losing it
                for f in type(scene).model_fields:
                    setattr(scene, f, getattr(backup, f))
                store.save_scene(scene)
                await self._emit_reload(scene, emit)
                raise

    async def _emit_reload(self, scene, emit):
        try:
            await emit({"type": "reload", "scene": self.payload(scene), "busy": False})
        except Exception:
            pass

    # ---------- memory ----------
    async def retrieve(self, scene, query: str, witness: str, k: int, recent: str = "") -> list[dict]:
        live = {x.split("|")[0] for x in scene.memory_injected if int(x.split("|")[1]) >= scene.archived_turn}
        try:
            return await asyncio.to_thread(MEM.retrieve, self.mem, scene, query, witness, k, recent,
                                           self.names(scene), live)
        except Exception:
            return []

    async def index_turn(self, scene, turn):
        if not self.mem:
            return
        docs = []
        for i, e in enumerate(scene.log):
            if e.turn == turn and e.kind in ("narration", "speech", "player", "roll"):
                txt = (f"{e.who}: {e.text}" if e.kind in ("speech", "player") else e.text)
                docs.append({"id": f"t{turn}-{i}", "text": txt, "turn": turn, "kind": e.kind, "who": e.who,
                             "witnesses": e.witnesses})
        try:
            await asyncio.to_thread(self.mem.add, scene.id, docs)
        except Exception as ex:
            print("memory index error", ex)

    async def turn_tokens(self, scene) -> list[tuple[int, int, int]]:
        """[(index, game_turn, tokens)] for each player turn in the unarchived window."""
        starts = [(i, int(m.group(1))) for i, t in enumerate(scene.turns) if i >= scene.archived_upto and t["role"] == "user"
                  and (m := STATE_RE.match(t["content"]))]
        out = []
        for j, (i, tn) in enumerate(starts):
            end = starts[j + 1][0] if j + 1 < len(starts) else len(scene.turns)
            txt = "".join(t["content"] for t in scene.turns[i:end])
            out.append((i, tn, await self.llm.tokenize(txt) + 12 * (end - i)))
        return out

    def plan_blocks(self, tt, n_blocks: int) -> list[tuple]:
        """Split the oldest part into up to n blocks of ~ARCHIVE_BLOCK tokens.
        Never emit a block smaller than ARCHIVE_MIN (unless forced to a single leftover ≥2 turns)."""
        blocks = []; j = 0; min_tok = config.ARCHIVE_MIN
        while len(blocks) < n_blocks and j < len(tt) - 2:
            acc = 0; k = j
            while k < len(tt) - 2 and acc < config.ARCHIVE_BLOCK:
                acc += tt[k][2]; k += 1
            # grow to min size if we still have room before the keep-tail
            while k < len(tt) - 2 and acc < min_tok:
                acc += tt[k][2]; k += 1
            if acc < min_tok and len(blocks) > 0:
                break   # leftover too small — leave for next archive
            if k == j:
                break
            blocks.append((tt[j][1], tt[k - 1][1], tt[k][0], acc)); j = k
        return blocks

    async def archive_block(self, scene, calls, forced=False, n_blocks: int = 1,
                            cancel: "asyncio.Event | None" = None) -> str | None:
        """Summarise oldest blocks. Prefer ONE multi-block LLM call (single-pass) so the slot
        only re-reads the archiver tail once; apply all results afterwards. Checks `cancel`
        between LLM work so a waiting player turn can reclaim the slot."""
        if cancel and cancel.is_set():
            return None
        tt = await self.turn_tokens(scene)
        blocks = self.plan_blocks(tt, n_blocks)
        if not blocks:
            return None
        anchors = {}
        for e in scene.log:
            if e.kind == "player" and any(e.turn in (b[0], b[1], b[1] + 1) for b in blocks) and e.turn not in anchors:
                anchors[e.turn] = e.text[:120]
        pending = []
        if len(blocks) == 1:
            from_turn, to_turn, end_idx, acc = blocks[0]
            extra = [{"role": "user", "content": MEM.archiver_request(scene, from_turn, to_turn, anchors)}]
            content, st = await self.llm.complete(self.prompt(scene, open_model="", extra=extra), 700,
                                                  stop=["<turn|>"], temperature=0.3, json_schema=MEM.SUMMARY_SCHEMA)
            st["kind"] = "archive" + (" (forced)" if forced else ""); calls.append(st)
            if cancel and cancel.is_set():
                return None
            try:
                data = json.loads(content)
            except Exception:
                data = {"summary": re.sub(r"\s+", " ", content)[:800], "facts": []}
            pending.append((from_turn, to_turn, end_idx, acc, data))
        else:
            # single-pass multi-block: one JSON, one re-read of the tail
            extra = [{"role": "user", "content": MEM.multi_archiver_request(scene, blocks, anchors)}]
            content, st = await self.llm.complete(self.prompt(scene, open_model="", extra=extra),
                                                  min(1400, 350 * len(blocks)), stop=["<turn|>"],
                                                  temperature=0.3, json_schema=MEM.MULTI_SUMMARY_SCHEMA)
            st["kind"] = f"archive×{len(blocks)}" + (" (forced)" if forced else ""); calls.append(st)
            if cancel and cancel.is_set():
                return None
            try:
                raw_blocks = json.loads(content).get("blocks") or []
            except Exception:
                raw_blocks = []
            by_range = {(int(b.get("from_turn", 0)), int(b.get("to_turn", 0))): b for b in raw_blocks}
            for from_turn, to_turn, end_idx, acc in blocks:
                data = by_range.get((from_turn, to_turn))
                if not data:
                    # fuzzy match by midpoint
                    data = next((b for (a, c), b in by_range.items() if a <= from_turn and to_turn <= c), None)
                if not data:
                    data = {"summary": f"События ходов {from_turn}–{to_turn}.", "facts": []}
                pending.append((from_turn, to_turn, end_idx, acc, data))
        msgs = []; new_docs = []; summaries = list(scene.summaries)
        for from_turn, to_turn, end_idx, acc, data in pending:
            wit = sorted({w for e in scene.log if from_turn <= e.turn <= to_turn for w in e.witnesses})
            summ = Summary(layer=1, from_turn=from_turn, to_turn=to_turn,
                           text=(data.get("summary") or "").strip(), witnesses=wit)
            new_facts = MEM.add_facts(scene, data.get("facts") or [], wit, to_turn,
                                      lambda n: M.resolve_character(scene, n))
            summaries.append(summ)
            new_docs += [{"id": f"s{from_turn}-{to_turn}", "text": summ.text, "turn": to_turn,
                          "kind": "summary", "witnesses": wit}]
            new_docs += [{"id": f"f{f.id}", "text": f.text, "turn": f.turn, "kind": "fact",
                          "witnesses": f.known_by} for f in new_facts]
            msgs.append(f"Архив: ходы {from_turn}–{to_turn} сведены в летопись" + (f", фактов +{len(new_facts)}" if new_facts else ""))
        if cancel and cancel.is_set():
            # still apply — summaries are valid; skip compress/re-warm upstream
            scene.summaries = summaries
            scene.archived_upto = pending[-1][2]; scene.archived_turn = pending[-1][1] + 1
            if self.mem:
                try: await asyncio.to_thread(self.mem.add, scene.id, new_docs)
                except Exception as ex: print("memory add error", ex)
            return "; ".join(msgs) + " (прервано, без сжатия слоёв)"
        scene.summaries = await self.compress(scene, summaries, calls)
        scene.archived_upto = pending[-1][2]; scene.archived_turn = pending[-1][1] + 1
        if self.mem:
            try: await asyncio.to_thread(self.mem.add, scene.id, new_docs)
            except Exception as ex: print("memory add error", ex)
        return "; ".join(msgs)

    async def compress(self, scene, summaries: list[Summary], calls) -> list[Summary]:
        layer = 1
        while True:
            items = sorted([s for s in summaries if s.layer == layer], key=lambda s: s.from_turn)
            if len(items) < config.L1_MAX:
                return summaries
            batch = items[: config.L1_MAX // 2]
            extra = [{"role": "user", "content": MEM.compress_request(batch)}]
            content, st = await self.llm.complete(self.prompt(scene, open_model="", extra=extra), 700, stop=["<turn|>"],
                                                  temperature=0.3, json_schema=MEM.COMPRESS_SCHEMA)
            st["kind"] = f"compress L{layer}→L{layer + 1}"; calls.append(st)
            try: text = json.loads(content)["summary"]
            except Exception: text = " ".join(s.text for s in batch)[:1200]
            merged = Summary(layer=layer + 1, from_turn=batch[0].from_turn, to_turn=batch[-1].to_turn, text=text,
                             witnesses=sorted({w for s in batch for w in s.witnesses}))
            summaries = [s for s in summaries if s not in batch] + [merged]
            layer += 1

    async def ensure_budget(self, scene, calls) -> int:
        """Hot-path guard: keep the prompt within CTX_BUDGET - GEN_RESERVE (forced archive if needed)."""
        total = await self.llm.tokenize(self.prompt(scene))
        n = 0
        while total > config.CTX_BUDGET - config.GEN_RESERVE and n < 4:
            if not await self.archive_block(scene, calls, forced=True):
                break
            total = await self.llm.tokenize(self.prompt(scene)); n += 1
        return total

    async def background(self, scene, emit=None):
        """Archive when the unarchived window is too long, then re-warm.
        Holds the turn lock only while mutating scene / calling the LLM, and checks
        `bg_cancel` so a waiting player turn always wins the slot. Re-warm is skipped
        if a player is waiting (their turn will rebuild the cache naturally)."""
        if self.bg_running or not config.MEMORY:
            return
        self.bg_running = True
        try:
            await asyncio.sleep(0.3)
            if self.bg_cancel.is_set() or self.lock.locked():
                return
            async with self.lock:
                if self.bg_cancel.is_set():
                    return
                tt = await self.turn_tokens(scene)
                recent = sum(x[2] for x in tt)
                if recent <= config.RECENT_MAX:
                    return
                t0 = time.time(); calls = []
                if emit:
                    try: await emit({"type": "memory", "text": "🧠 Архивация памяти…"})
                    except Exception: emit = None
                target = config.RECENT_MAX - 2 * config.ARCHIVE_BLOCK
                n = max(1, -(-(recent - target) // config.ARCHIVE_BLOCK))
                m = await self.archive_block(scene, calls, n_blocks=n, cancel=self.bg_cancel)
                msgs = [m] if m else []
                recent = sum(x[2] for x in await self.turn_tokens(scene))
                cancelled = self.bg_cancel.is_set()
                if not cancelled:
                    st = await self.llm.warm(self.prompt(scene)); st["kind"] = "re-warm"; calls.append(st)
                else:
                    calls.append({"kind": "re-warm skipped (player waiting)", "prompt_n": 0, "cache_n": 0})
                entry = {"background": True, "after_turn": scene.turn, "wall_s": round(time.time() - t0, 2),
                         "calls": calls, "msgs": msgs, "recent_tokens": recent, "cancelled": cancelled}
                scene.stats.append(entry)
                store.save_scene(scene)
                if emit:
                    try:
                        note = "🧠 " + ("; ".join(x for x in msgs if x) or "готово")
                        if cancelled:
                            note += " · прервано ходом игрока"
                        else:
                            note += f" · {entry['wall_s']} с"
                        await emit({"type": "memory", "text": note, "stats": entry, "state": self.state(scene)})
                    except Exception:
                        pass
        finally:
            self.bg_running = False

    # ---------- turn ----------
    def active_npcs(self, scene) -> int:
        """NPCs present AND involved recently (spoke or were mentioned in the last 2 turns); the narrator
        rarely sends characters away, so raw `present` overstates the scene size."""
        ids = [i for i in scene.present if i != scene.player_id]
        recent = [e for e in scene.log if e.turn >= scene.turn - 1 and e.kind in ("narration", "speech", "player")]
        if scene.turn <= 1 and not any(e.kind != "player" for e in recent):
            return min(len(ids), 3)   # scene start: assume the starting cast is involved
        text = " ".join(e.text for e in recent).lower(); speakers = {e.who for e in recent if e.kind == "speech"}
        act = 0
        for i in ids:
            c = scene.characters[i]
            stem = c.name.lower()[: max(4, len(c.name) - 2)]
            if c.name in speakers or stem in text:
                act += 1
        return act

    INVITE_RE = re.compile(r"отряд|присоедин|пойд[её]шь со мной|идём со мной|идем со мной|пошли со мной|составь компани")

    def invite_hint(self, scene, text) -> str:
        """In a long story the tag docs sit ~20k tokens back and the model forgets <join>: when the player invites
        someone, remind it right in this turn (only then, so it never nudges NPCs into joining on their own)."""
        if not text or not self.INVITE_RE.search(text.lower()):
            return ""
        low = text.lower()
        for cid in scene.present:
            c = scene.characters.get(cid)
            if not c or c.is_player or cid in scene.party:
                continue
            stem = c.name.lower()[: max(3, len(c.name) - 2)]
            if stem in low:
                if len(M.party_ids(scene)) >= config.PARTY_MAX:
                    return f"[ОТРЯД] Игрок зовёт в отряд: {c.name}. Но отряд полон ({config.PARTY_MAX}) — {c.name} не может вступить."
                return (f"[ОТРЯД] Игрок зовёт в отряд: {c.name}. Реши по характеру персонажа. Если соглашается — "
                        f"ОБЯЗАТЕЛЬНО поставь <join who=\"{c.name}\"/> сразу после ответа, иначе в отряд не попадёт. "
                        f"Если отказывается — тег не ставь.")
        return ""

    def mark_active(self, scene, *ids_or_names):
        for x in ids_or_names:
            if not x: continue
            c = scene.characters.get(x) if x in scene.characters else M.resolve_character(scene, x)
            if c:
                c.last_active_turn = scene.turn

    def prune_inactive(self, scene) -> list[str]:
        """Auto-leave NPCs not spoken/mentioned for N turns. Player never pruned. History/witnesses kept."""
        n = scene.settings.inactive_leave
        if n is None:
            n = config.INACTIVE_LEAVE
        if n <= 0:
            return []
        msgs = []
        if scene.combat.active:
            return []
        for cid in list(scene.present):
            if cid == scene.player_id or cid in scene.party or scene.characters[cid].side == "hostile":
                continue
            c = scene.characters[cid]
            # seed last_active for characters that never got a stamp
            if c.last_active_turn == 0 and cid in scene.initial_present:
                c.last_active_turn = 1
            if scene.turn - c.last_active_turn >= n:
                scene.present.remove(cid)
                msgs.append(f"{c.name} больше не в сцене (давно не появлялся).")
                self.log(scene, "system", msgs[-1], "Система")
        return msgs

    def _signals(self, scene):
        prev = [e for e in scene.log if e.turn == scene.turn]
        introduced = any(e.kind == "system" and e.text.startswith("Новый персонаж") for e in prev)
        last = next((s for s in reversed(scene.stats) if "tier" in s), None)
        combat = bool(last and "бой" in " ".join(last.get("tier_reasons", [])))
        return introduced, (last or {}).get("tier"), combat

    async def _turn(self, scene: Scene, text: str | None, emit, rollback: bool = True) -> dict:
        """Run one turn; on any failure (LLM down, timeout…) restore the pre-turn snapshot so the
        in-memory scene never keeps a half-finished turn (dangling user message, bumped turn counter)."""
        snap = self.snapshot(scene, text)
        scene.snapshots.append(snap)
        if config.SNAPSHOTS > 0 and len(scene.snapshots) > config.SNAPSHOTS:
            del scene.snapshots[: len(scene.snapshots) - config.SNAPSHOTS]
        try:
            return await self._turn_body(scene, text, emit)
        except Exception:
            if rollback:
                self.rewind(scene, snap["turn"] + 1)
                await self._emit_reload(scene, emit)
            raise

    async def _turn_body(self, scene: Scene, text: str | None, emit) -> dict:
        t0 = time.time()
        introduced, last_tier, combat = self._signals(scene)
        scene.turn += 1; scene.turn_exp = 0; scene.turn_exp_by = {}
        calls = []
        M.sync_all(scene)
        # auto-leave inactive NPCs before budgeting
        prune_msgs = self.prune_inactive(scene)
        npcs = self.active_npcs(scene)
        st_ = scene.settings
        budget = turn_budget(text, npcs, introduced, last_tier, combat or scene.combat.active,
                             st_.length_mult, st_.max_speaks)
        from . import intent as I
        comp = (scene.characters.get(scene.combat.awaiting)
                if scene.combat.active and scene.combat.awaiting else None)
        self.comp_turn[scene.id] = comp.id if comp else ""
        if comp and text is not None and not M.parse_system_command(text) and not re.search(r"[*«»\"()]", text):
            text = f"«{text.strip()}»"              # on a companion's turn the hero's words are advice, i.e. speech
        self.intent[scene.id] = I.parse(text)       # what the hero really did: act / speak / only think
        if text is not None:
            await emit({"type": "player", "text": text, "turn": scene.turn})
            self.log(scene, "player", text, scene.player.name)
            self.mark_active(scene, scene.player_id)
            # mention detection for NPCs named in the player's input
            for cid in list(scene.present):
                if cid != scene.player_id and scene.characters[cid].name.lower() in text.lower():
                    self.mark_active(scene, cid)
        else:
            await emit({"type": "continue", "turn": scene.turn})
        transcript = []
        react_note = ""
        free_threat = ""
        self.reacted[scene.id] = 0
        if scene.combat.active and scene.combat.threat and not (text and M.parse_system_command(text)):
            if text is None or text.strip() in C.REACTION_TEXTS:
                react_note = await self.resolve_threat(scene, emit, transcript, text)
            else:
                free_threat = scene.combat.threat
                react_note = self.free_reaction_hint(scene)
            self.intent[scene.id] = None              # the reaction was the hero's whole deed this turn
        for msg in prune_msgs:
            await self.sys_msg(emit, scene, "🚪 " + msg)
        prompt_tokens = await self.ensure_budget(scene, calls) if config.MEMORY else None
        last_narr = " ".join(e.text for e in scene.log[-6:] if e.kind in ("narration", "speech", "player"))[-400:]
        mems = await self.retrieve(scene, (text or "") + " " + last_narr, scene.player_id, config.RETRIEVE_K, last_narr)
        scene.memory_injected += [f"{m['id']}|{scene.turn}" for m in mems]
        mem_lines = [f"(ход {m['turn']}) {m['text'][:220]}" for m in mems]
        # one-shot director hint for narrator (consumed this turn)
        dnote = (getattr(scene, "director_note", None) or "").strip()
        if dnote:
            scene.director_note = ""
        turn_hint = budget.hint
        if dnote:
            turn_hint = (turn_hint + " " if turn_hint else "") + f"[ЗАМЕТКА ДИРЕКТОРА] {dnote}"
        if react_note:
            turn_hint = (turn_hint + " " if turn_hint else "") + react_note
        invite = self.invite_hint(scene, text)
        if invite:
            turn_hint = (turn_hint + " " if turn_hint else "") + invite
        from . import magic as G
        book = G.tail_block(scene)
        if book:
            turn_hint = (turn_hint + " " if turn_hint else "") + book
        cur = C.current(scene)
        foes_near = any(scene.characters[i].side == "hostile" and scene.characters[i].hp > 0 for i in scene.present)
        if ((cur is not None and cur.is_player) or (not scene.combat.active and foes_near)) and comp is None:
            it = self.intent.get(scene.id)
            if it is not None and it.acts:
                turn_hint = (turn_hint + " " if turn_hint else "") + (
                    ("Бой, ход героя. " if scene.combat.active else "Рядом враги. ") +
                    f"Оцени то, что написал игрок: реально ли это сейчас (снаряжение, положение, мир) "
                    f"и насколько трудно. Простой удар оружием — короткий замах и <attack who=\"{scene.player.name}\" target=\"…\"/>. "
                    f"Хитрый приём, манёвр, окружение, финт — <deed attr=\"Атрибут\" difficulty=\"легко|обычно|трудно|"
                    f"очень трудно|невозможно\" effect=\"урон|оглушение|ослабление|усиление|нет\" target=\"…\" "
                    f"how=\"что делает герой, кратко\"/>. Способность — <cast …/>. Исход посчитает Система; "
                    f"не подменяй задуманное другим действием.")
        quiet = self.quiet_npcs(scene)
        if quiet:
            turn_hint = (turn_hint + " " if turn_hint else "") + (
                f"Давно молчат: {', '.join(quiet)}. Если кто-то из них уже ушёл по сюжету — поставь <leave who=\"Имя\"/>.")
        scene.turns.append({"role": "user", "content": P.player_message(scene, M.state_line(scene), text,
                                                                         hint=turn_hint, memories=mem_lines,
                                                                         companion=comp)})
        steps = 0; speaks = 0
        paused0 = (scene.combat.threat, scene.combat.awaiting)
        after_pause = 0
        while steps < budget.max_steps:
            steps += 1
            pause = (scene.combat.threat, scene.combat.awaiting)
            if scene.combat.active and any(pause) and pause != paused0:
                after_pause += 1
                if after_pause > 2:
                    break
            if react_note and not free_threat and steps > 2:   # a reaction turn only describes what the System settled
                break
            if self.reacted.get(scene.id) and steps - self.reacted[scene.id] > 2:
                break
            out, st = await self._narrate_step(scene, emit, calls, budget.step_tokens)
            scene.turns.append({"role": "model", "content": out.raw})
            transcript += out.parts
            tag = out.tag
            if out.error and tag is None:
                tag = await self._retry_tag(scene, out.error, calls)
                if tag is None:
                    scene.turns.append({"role": "user", "content": "[СИСТЕМА: результат] Тег пропущен (ошибка формата). Продолжай повествованием или закончи <end/>."})
                    if st.get("stop_word", "").strip() != "/>":
                        break
                    continue
            if tag is None or tag.name == "end":
                break
            if tag.name == "scale":
                b2 = turn_budget(text, npcs, introduced, last_tier, combat or scene.combat.active,
                                 st_.length_mult, st_.max_speaks,
                                 scale_override=tag.attrs.get("level", "").lower())
                budget = b2
                steps -= 1  # declaring the scale is free
                scene.turns.append({"role": "user", "content": f"[СИСТЕМА: результат] {b2.hint}"})
                continue
            if tag.name == "speak" and speaks >= budget.hard_speaks:
                result = "На этот ход реплик достаточно. Заверши ход коротким повествованием и <end/>."
            else:
                result = await self._execute(scene, tag, emit, calls, transcript)
                if tag.name == "speak":
                    speaks += 1
            scene.turns.append({"role": "user", "content": "[СИСТЕМА: результат] " + result})
        if free_threat and scene.combat.active and scene.combat.threat == free_threat:
            await self.resolve_threat(scene, emit, transcript, text)
        if scene.combat.active:
            cur = C.current(scene)
            if comp is not None and cur is not None and cur.id == comp.id:   # the narrator never acted for the companion
                await self.sys_msg(emit, scene, f"{comp.name} выжидает и держит позицию.", "roll")
                await self.next_turns(scene, emit, transcript)
            elif cur is not None and not M.in_party(scene, cur):            # the turn got stuck on a foe
                await self.emit_foes(scene, emit, transcript, C.run_foes(scene))
        narr_words = sum(len(p["text"].split()) for p in transcript if p["kind"] == "narration")
        hot = [c for c in calls if c["kind"].startswith(("narrate", "speak", "tag"))]
        stats = {"turn": scene.turn, "tier": budget.tier, "tier_reasons": budget.reasons, "budget_speaks": budget.speaks,
                 "steps": steps, "speaks": speaks, "narr_words": narr_words, "wall_s": round(time.time() - t0, 2),
                 "prompt_tokens": prompt_tokens, "memories": len(mems), "llm_calls": calls,
                 "prompt_n": sum(c.get("prompt_n") or 0 for c in calls),
                 "cache_n": sum(c.get("cache_n") or 0 for c in calls),
                 "predicted_n": sum(c.get("predicted_n") or 0 for c in calls),
                 "max_hot_prompt_n": max([c.get("prompt_n") or 0 for c in hot], default=0)}
        scene.stats.append({k: v for k, v in stats.items() if k != "llm_calls"} | {"calls": len(calls)})
        store.save_scene(scene)
        await self.index_turn(scene, scene.turn)
        await emit({"type": "turn_end", "stats": stats, "state": self.state(scene), "memories": mem_lines,
                    "text": "\n".join(("*" + p["text"] + "*") if p["kind"] == "narration" else
                                      (f"{p['who']}: {p['text']}" if p["kind"] == "speech" else f"[{p['text']}]")
                                      for p in transcript)})
        return stats

    async def _narrate_step(self, scene, emit, calls, step_tokens):
        names = self.names(scene)
        for attempt in range(2):
            sid = next(_seg); sent = 0; buf = []

            async def on_delta(d):
                nonlocal sent
                buf.append(d)
                vis = visible_text("".join(buf)).lstrip()
                if len(vis) > sent:
                    await emit({"type": "chunk", "id": sid, "kind": "narration", "delta": vis[sent:]})
                    sent = len(vis)

            prompt = self.prompt(scene, open_model="")
            content, st = await self.llm.complete(prompt, step_tokens, stop=["/>", "<turn|>", "<|turn>"],
                                                  on_delta=on_delta, seed=self.seed(scene, attempt + len(calls)))
            st["kind"] = "narrate" + (" (retry ru)" if attempt else ""); calls.append(st)
            out = parse_step(content, st.get("stop_word", ""))
            share = cyr_share(out.narration, names)
            if attempt == 0 and share is not None and share < config.CYR_MIN and len(out.narration) > 20:
                await emit({"type": "retract", "id": sid})
                continue
            parts = split_speech(out.narration, names) if out.narration else []
            await emit({"type": "replace", "id": sid, "segments": parts})
            for p in parts:
                if p["kind"] == "speech":
                    c = M.resolve_character(scene, p["who"])
                    self.log(scene, "speech", p["text"], c.name if c else p["who"])
                else:
                    self.log(scene, "narration", p["text"])
            out.parts = parts
            return out, st
        return out, st

    async def _retry_tag(self, scene, error, calls):
        """One retry with the error sent back, constrained by a GBNF grammar to a single valid tag."""
        extra = [{"role": "user", "content": f"[СИСТЕМА: ошибка формата] {error}. Повтори только один тег протокола, например "
                                             f"<speak who=\"Имя\" intent=\"…\"/> или <end/>."}]
        names = [scene.characters[i].name for i in scene.present if i != scene.player_id]
        from .models import ATTRS as _ATTRS
        g = tag_grammar(names, list(_ATTRS))
        try:
            content, st = await self.llm.complete(self.prompt(scene, open_model="", extra=extra), 80, stop=["<turn|>"],
                                                  grammar=g, temperature=0.3)
        except Exception:
            content, st = await self.llm.complete(self.prompt(scene, open_model="", extra=extra), 80, stop=["/>", "<turn|>"])
            content += "/>"
        st["kind"] = "tag retry (gbnf)"; calls.append(st)
        try:
            m = re.search(r"<\s*\w+[^<]*$", content)
            raw = (m.group(0) if m else content).strip()
            tag = parse_tag(raw if raw.endswith(">") else raw + "/>")
            scene.turns += extra + [{"role": "model", "content": raw if raw.endswith(">") else raw + "/>"}]
            return tag
        except ParseError:
            return None

    async def _execute(self, scene, tag, emit, calls, transcript) -> str:
        a = tag.attrs
        if tag.name == "speak":
            c = M.resolve_character(scene, a.get("who", ""), only_present=True) or M.resolve_character(scene, a.get("who", ""))
            if c is None:
                if not re.search(r"[А-Яа-яЁё]", a["who"]):
                    return f"Персонажа «{a['who']}» нет в сцене. Говорить могут: " + ", ".join(
                        scene.characters[i].name for i in scene.present if i != scene.player_id) + "."
                c = await self._introduce(scene, a["who"], "", emit, transcript)
            if c.is_player:
                return (f"{c.name} — персонаж игрока; его слова уже даны во вводе игрока, повторять их не нужно. "
                        f"Не описывай заново его действие: покажи реакцию NPC (через speak) или закончи <end/>.")
            if c.id not in scene.present:
                scene.present.append(c.id)
            line = await self._speak(scene, c, a.get("intent", ""), emit, calls)
            transcript.append({"kind": "speech", "who": c.name, "text": line})
            self.log(scene, "speech", line, c.name)
            self.mark_active(scene, c.id)
            return f"{c.name}: «{line}»"
        if tag.name in ("roll", "attack", "cast"):
            actor = (M.resolve_character(scene, a.get("who", "")) if a.get("who") else scene.player) or scene.player
            refused = self.out_of_turn(scene, actor, tag.name) or self.hero_idle(scene, actor, tag.name)
            if refused:
                return refused
        if tag.name == "roll":
            who = (M.resolve_character(scene, a.get("who", "")) if a.get("who") else scene.player) or scene.player
            r = M.check(scene, who, a.get("skill", ""), a.get("dc", 12))
            text = ("🎲 " if scene.rule_mode == "d20" else "⚙️ ") + r["text"]
            msgs = []
            if r["success"]:
                M.award_exp(scene, who, M.share_exp(who, M.check_share(r["dc"])), msgs)
            full = text + ("\n" + "\n".join(msgs) if msgs else "")
            await self.sys_msg(emit, scene, full, "roll")
            transcript.append({"kind": "roll", "text": full})
            scene.log.append(Event(turn=scene.turn, kind="roll", who=who.name, text=r["text"], witnesses=list(scene.present)))
            return r["text"] + " Опиши последствия согласно результату."

        if tag.name == "reveal":
            c = M.resolve_character(scene, a.get("who", ""))
            if not c:
                return f"Нет персонажа «{a.get('who')}» для reveal."
            if c.is_player:
                return "Игрок и так знает себя."
            got = M.apply_reveal(c, a.get("what", "all") or "all")
            kind = (c.kind or "не указан") if c.reveal.kind else "неизвестно"
            parts = [f"Ты узнаёшь о {c.name}: вид — {kind}."]
            if c.reveal.attrs:
                parts.append("Характеристики: " + ", ".join(f"{k} {v}" for k, v in c.attrs.items()) + ".")
            if c.reveal.abilities:
                if c.abilities:
                    parts.append("Способности: " + ", ".join(
                        ("⚡" if x.kind == "active" else "◇") + x.name for x in c.abilities) + ".")
                else:
                    parts.append("Особых способностей не видно.")
            text = " ".join(parts)
            if got:
                text += " (открыто: " + ", ".join(got) + ")"
            await self.sys_msg(emit, scene, "👁 " + text)
            transcript.append({"kind": "system", "text": text})
            self.log(scene, "system", text, "Система")
            return text

        if tag.name == "change":
            c = M.resolve_character(scene, a.get("who", ""))
            if not c:
                return f"Нет персонажа «{a.get('who')}». Изменение отклонено."
            msgs, rej = M.apply_delta(scene, c, a)
            text = "; ".join(msgs) or "без изменений"
            if rej:
                text += " (Система отклонила: " + "; ".join(rej) + ")"
            await self.sys_msg(emit, scene, "⚙️ " + text)
            transcript.append({"kind": "system", "text": text})
            self.log(scene, "system", text, "Система")
            return text
        if tag.name == "introduce":
            hostile = (a.get("side") or "").lower() in ("враг", "враги", "hostile", "enemy", "foe")
            try:
                count = max(1, min(6, int(a.get("count") or 1)))
            except ValueError:
                count = 1
            base = re.sub(r"\s*\d+$", "", a["name"]).strip() or a["name"]   # «Волк 1» + count=3 → «Волк 1…3», not «Волк 1 1»
            names = [a["name"]] if count == 1 else [f"{base} {i}" for i in range(1, count + 1)]
            made = []
            for nm in names:
                c = await self._introduce(scene, nm, a.get("desc", ""), emit, transcript, exact=count > 1)
                if (hostile or a.get("threat")) and not M.in_party(scene, c):
                    C.mark_foe(scene, c, a.get("threat", ""))
                made.append(c)
            if count == 1:
                return f"В сцену входит {made[0].name}: {made[0].description or 'новый персонаж'}."
            return f"В сцену входят: {', '.join(c.name for c in made)}. Обращайся к каждому по имени."
        if tag.name in ("discover", "learn"):
            from . import magic as G
            if tag.name == "discover":
                ok, msg = G.discover(scene, a.get("what", ""), a.get("source", ""), a.get("hint", ""))
            else:
                ok, msg = G.learn(scene, a.get("what", ""), a.get("how", ""))
            if ok:
                await self.sys_msg(emit, scene, "📖 " + msg)
                transcript.append({"kind": "system", "text": msg})
                self.log(scene, "system", "📖 " + msg, "Система")
            return msg + (" Продолжай повествование." if ok else "")
        if tag.name == "cast":
            caster = (M.resolve_character(scene, a.get("who", "")) if a.get("who") else scene.player) or scene.player
            ab = C.find_ability(caster, a.get("spell", ""))
            if not ab:
                have = ", ".join(x.name for x in caster.abilities if x.kind == "active") or "нет"
                return (f"У {caster.name} нет способности «{a.get('spell')}». Активные: {have}. "
                        f"Опиши попытку без результата или выбери другое действие.")
            tgt = M.resolve_character(scene, a.get("target", "")) if a.get("target") else None
            if tgt and tgt.hp <= 0 and ab.effect in ("damage", "debuff", "control"):
                return f"{tgt.name} уже повержен(а). Выбери другую цель или закончи <end/>."
            if tgt and not tgt.is_player:
                for x, y in ((caster, tgt), (tgt, caster)):
                    if ab.effect in ("damage", "debuff", "control") and M.in_party(scene, x) and not M.in_party(scene, y):
                        C.mark_foe(scene, y)
            hp_before = tgt.hp if tgt else 0
            r = C.cast(scene, caster, ab, tgt)
            if r["ok"] and tgt and r["damage"]:
                self._rewards(scene, caster, tgt, hp_before, r["msgs"])
            full = r["text"] + ("\n" + "\n".join(r["msgs"]) if r["msgs"] else "")
            await self.sys_msg(emit, scene, full, "roll")
            transcript.append({"kind": "roll", "text": full})
            self.log(scene, "roll", r["text"], caster.name)
            self.mark_active(scene, caster.id, tgt.id if tgt else None)
            after = await self.next_turns(scene, emit, transcript) if r["ok"] and M.in_party(scene, caster) else ""
            return r["text"] + (" Опиши, как это выглядит и что происходит. " + after if r["ok"] else "")
        if tag.name == "fulfill":
            from . import magic as G
            ok, msg = G.fulfill(scene, a.get("what", ""))
            if ok:
                await self.sys_msg(emit, scene, "⟦ СИСТЕМА ⟧ " + msg)
                transcript.append({"kind": "system", "text": msg})
                self.log(scene, "system", "⟦ СИСТЕМА ⟧ " + msg, "Система")
            return msg
        if tag.name in ("join", "part"):
            c = M.resolve_character(scene, a.get("who", ""))
            if not c:
                return f"Нет персонажа «{a.get('who')}»."
            ok, msg = (M.join_party if tag.name == "join" else M.leave_party)(scene, c)
            if ok:
                await self.sys_msg(emit, scene, "⟦ ОТРЯД ⟧ " + msg)
                transcript.append({"kind": "system", "text": msg})
                self.log(scene, "system", "⟦ ОТРЯД ⟧ " + msg, "Система")
                self.mark_active(scene, c.id)
                return msg + (f" Опиши, как {c.name} присоединяется." if tag.name == "join" else f" Опиши, как {c.name} уходит.")
            return msg + (" Покажи это в повествовании." if tag.name == "join" else "")
        if tag.name == "leave":
            c = M.resolve_character(scene, a.get("who", ""), only_present=True)
            if not c or c.is_player:
                return "Этого персонажа нет в сцене."
            if c.id in scene.party:
                return (f"{c.name} — спутник героя и идёт с ним. Если {c.name} уходит из отряда насовсем — "
                        f"<part who=\"{c.name}\"/>.")
            scene.present.remove(c.id)
            self.log(scene, "system", f"{c.name} уходит.", "Система")
            end = C.leave_combat(scene, c)
            if end:
                await self.sys_msg(emit, scene, end)
            return f"{c.name} покидает сцену." + (" " + end if end else "")
        if tag.name == "attack":
            atk = (M.resolve_character(scene, a.get("who", "")) if a.get("who") else scene.player) or scene.player
            tgt = M.resolve_character(scene, a.get("target", ""))
            if not tgt:
                return f"Нет цели «{a.get('target')}»."
            if tgt.hp <= 0:
                return f"{tgt.name} уже повержен(а). Выбери другую цель или закончи <end/>."
            if atk.hp <= 0:
                return f"{atk.name} без сознания и не может атаковать. Опиши сцену и закончи <end/>."
            if not scene.combat.active and not M.in_party(scene, atk) and C.can_react(scene, tgt):
                # a foe opens the fight with a blow at the hero: it waits for the player's reaction
                note = C.mark_foe(scene, atk)
                foes = [atk.id] + [i for i in scene.present if i != atk.id and scene.characters[i].side == "hostile"
                                   and scene.characters[i].hp > 0]
                C.start_combat(scene, foes, first=atk.id)
                r = C.threaten(scene, atk, tgt)
                full = r["text"] + (f"\n{note}" if note else "")
                await self.sys_msg(emit, scene, full, "roll")
                transcript.append({"kind": "roll", "text": full})
                self.log(scene, "roll", r["text"], atk.name)
                self.mark_active(scene, atk.id, tgt.id)
                return r["text"] + " " + self.threat_line(scene)
            started = not scene.combat.active
            notes = []
            for x, y in ((atk, tgt), (tgt, atk)):      # whoever fights the party is a foe (and gets a real sheet)
                if M.in_party(scene, x) and not M.in_party(scene, y):
                    n = C.mark_foe(scene, y)
                    if n:
                        notes.append(n)
            hp_before = tgt.hp
            r = C.attack(scene, atk, tgt, a.get("weapon", ""))
            r["msgs"] = notes + r["msgs"]
            if r.get("hit"):
                self._rewards(scene, atk, tgt, hp_before, r["msgs"])
            full = r["text"]
            if r["msgs"]:
                full += "\n" + "\n".join(r["msgs"])
            await self.sys_msg(emit, scene, full, "roll")
            transcript.append({"kind": "roll", "text": full})
            self.log(scene, "roll", r["text"], atk.name)
            self.mark_active(scene, atk.id, tgt.id)
            after = (await self.next_turns(scene, emit, transcript)
                     if M.in_party(scene, atk) or (started and scene.combat.active) else "")
            return r["text"] + " Опиши последствия удара. " + after
        if tag.name == "react":
            if not (scene.combat.active and scene.combat.threat):
                return "Сейчас никто не бьёт героя — <react> не нужен. Продолжай повествование."
            foe = scene.characters.get(scene.combat.threat)
            r = C.react_free(scene, a.get("attr", ""), a.get("difficulty", ""), a.get("outcome", ""), a.get("how", ""))
            note = await self.after_reaction(scene, emit, transcript, r, foe)
            self.reacted[scene.id] = self.reacted.get(scene.id) or 1
            return note
        if tag.name == "deed":
            actor = (M.resolve_character(scene, a.get("who", "")) if a.get("who") else scene.player) or scene.player
            if actor.is_player and scene.combat.active and scene.combat.threat:
                return "Сейчас герой защищается от удара — это <react attr=… difficulty=… outcome=… how=…/>, не <deed>."
            gate = self.out_of_turn(scene, actor, "deed") or self.hero_idle(scene, actor, "deed")
            if gate:
                return gate
            if actor.hp <= 0:
                return f"{actor.name} без сознания. Закончи <end/>."
            tgt = M.resolve_character(scene, a.get("target", "")) if a.get("target") else None
            effect = C._pick(C.EFFECTS, a.get("effect", ""), "нет")
            if tgt is not None and tgt.hp <= 0 and effect != "усиление":
                return f"{tgt.name} уже повержен(а). Выбери другую цель или закончи <end/>."
            harmful = effect in ("урон", "оглушение", "ослабление") and tgt is not None and tgt.id != actor.id
            notes = []
            if harmful and M.in_party(scene, actor) and not M.in_party(scene, tgt):
                n = C.mark_foe(scene, tgt)
                if n:
                    notes.append(n)
                if not scene.combat.active:
                    C.start_combat(scene, [actor.id, tgt.id], first=actor.id)
                elif not any(o.id == tgt.id for o in scene.combat.order):
                    C.join_combat(scene, [tgt.id])
            hp0 = tgt.hp if tgt else 0
            r = C.deed(scene, actor, tgt, a.get("attr", ""), a.get("difficulty", ""), effect, a.get("how", ""))
            r["msgs"] = notes + r["msgs"]
            if r["damage"] and tgt is not None:
                self._rewards(scene, actor, tgt, hp0, r["msgs"])
            full = r["text"] + ("\n" + "\n".join(r["msgs"]) if r["msgs"] else "")
            await self.sys_msg(emit, scene, full, "roll")
            transcript.append({"kind": "roll", "text": full})
            self.log(scene, "roll", r["text"], actor.name)
            self.mark_active(scene, actor.id, *( [tgt.id] if tgt else []))
            after = (await self.next_turns(scene, emit, transcript)
                     if scene.combat.active and M.in_party(scene, actor) else "")
            return f"{r['text']} Опиши именно это: {r['brief']}. {after}"
        if tag.name == "combat":
            act = (a.get("action") or "").lower()
            if act in ("start", "begin", "бой"):
                foes = [x.strip() for x in (a.get("foes") or a.get("who") or "").split(",") if x.strip()]
                ids = []
                for f in foes:
                    c = M.resolve_character(scene, f) or await self._introduce(scene, f, "", emit, transcript)
                    if not M.in_party(scene, c):
                        C.mark_foe(scene, c, a.get("threat", ""))
                    ids.append(c.id)
                ids = ids or [i for i in scene.present if scene.characters[i].side == "hostile"]
                if not ids:
                    return "Не с кем сражаться: назови противников в foes=\"Имя1,Имя2\"."
                if scene.combat.active:     # a fight already started by an attack: add newcomers, keep the order
                    text = C.join_combat(scene, ids)
                else:
                    text = C.start_combat(scene, ids)
                await self.sys_msg(emit, scene, text, "roll")
                transcript.append({"kind": "roll", "text": text})
                self.log(scene, "system", text, "Система")
                cur = C.current(scene)
                if cur and not M.in_party(scene, cur):          # foes faster than the party strike first
                    text += " " + await self.emit_foes(scene, emit, transcript, C.run_foes(scene))
                cur = C.current(scene)
                if scene.combat.threat:
                    text += " " + self.threat_line(scene)
                elif cur and scene.combat.active:
                    text += (f" Ход героя — закончи <end/>." if cur.is_player else
                             f" Ход спутника {cur.name} — закончи <end/>, игрок может дать совет.")
                return text
            if act in ("end", "stop", "конец"):
                standing = [scene.characters[o.id].name for o in scene.combat.order
                            if not M.in_party(scene, scene.characters[o.id]) and scene.characters[o.id].hp > 0]
                if scene.combat.active and standing:
                    return (f"Отклонено: на ногах ещё {', '.join(standing)}. Бой кончается, когда враги повержены; "
                            f"если кто-то бежит или сдаётся — <leave who=\"Имя\"/> для каждого.")
                text = C.end_combat(scene)
                await self.sys_msg(emit, scene, text)
                return text
            return C.combat_status(scene)
        if tag.name == "condition":
            c = M.resolve_character(scene, a.get("who", ""))
            if not c:
                return f"Нет персонажа «{a.get('who')}»."
            try:
                turns = max(1, int(a.get("turns") or 2))
            except ValueError:
                turns = 2
            try:
                pot = max(0, int(a.get("potency") or 0))
            except ValueError:
                pot = 0
            text = C.apply_condition(c, a.get("name", ""), turns, pot)
            await self.sys_msg(emit, scene, "⚙️ " + text)
            transcript.append({"kind": "system", "text": text})
            self.log(scene, "system", text, "Система")
            self.mark_active(scene, c.id)
            return text
        return "Неизвестное действие."

    def _rewards(self, scene, atk, tgt, hp_before, msgs):
        """A small share for a hit on a foe; for a defeated foe every party member gets a share of THEIR OWN level,
        larger the stronger the foe is relative to them (catch-up for companions comes from that by itself)."""
        if M.in_party(scene, atk) and not M.in_party(scene, tgt):
            M.award_exp(scene, atk, M.share_exp(atk, M.HIT_SHARE), msgs)
        if hp_before > 0 and tgt.hp == 0 and not M.in_party(scene, tgt):
            msgs.append(f"⟦ ПОБЕДА ⟧ {tgt.name} ({tgt.threat or 'обычный'}, ур. {tgt.level}) повержен(а) — опыт всему отряду.")
            for cid in M.party_ids(scene):
                c = scene.characters[cid]
                gap = tgt.level - c.level
                note = f" (враг сильнее на {gap} ур.)" if gap > 0 else ""
                M.award_exp(scene, c, M.share_exp(c, M.kill_share(c, tgt)), msgs, capped=False, note=note)

    def out_of_turn(self, scene, actor, tag) -> str:
        """In a fight the order is real: foes act by code on their turn; the party acts only on its own turn."""
        if tag == "roll" or not scene.combat.active:
            return ""
        cur = C.current(scene)
        in_order = any(o.id == actor.id for o in scene.combat.order)
        if scene.combat.threat and M.in_party(scene, actor):
            foe = scene.characters.get(scene.combat.threat)
            return (f"Отклонено: {foe.name if foe else 'враг'} замахивается на героя — игрок выбирает реакцию. "
                    f"Опиши замах и закончи <end/>.")
        if not M.in_party(scene, actor) and in_order:
            return ("Отклонено: ходы врагов считает Система по очереди — их действия уже в результатах. "
                    "Опиши происходящее, не ставь за врагов <attack>/<cast>.")
        if (cur and cur.id == actor.id and M.in_party(scene, actor) and not actor.is_player
                and self.comp_turn.get(scene.id) != actor.id):
            return (f"Отклонено: ход {actor.name} начнётся после совета игрока. Покажи, что {actor.name} готовится, "
                    f"и закончи <end/>.")
        if cur and in_order and cur.id != actor.id:
            return (f"Отклонено: сейчас ход {cur.name}, а не {actor.name}. "
                    + ("Закончи ход <end/> — игрок решит, что делает герой." if cur.is_player else
                       f"{cur.name} действует после совета игрока — закончи <end/>." if M.in_party(scene, cur)
                       else "Закончи ход <end/>."))
        return ""

    async def next_turns(self, scene, emit, transcript) -> str:
        """After a party member acted in a fight: pass the turn, let the foes act by code, stop at the hero or a
        companion. Returns the text the narrator gets (and the player sees as rolls)."""
        if not scene.combat.active:
            return ""
        C.step(scene)
        head = await self.emit_foes(scene, emit, transcript, C.run_foes(scene))
        cur = C.current(scene)
        if not scene.combat.active:
            return head + "Бой окончен."
        if scene.combat.threat:
            return head + self.threat_line(scene)
        if cur is None:
            return head
        if cur.is_player:
            return head + f"Теперь ход героя ({cur.name}): закончи описание и <end/> — решает игрок."
        return head + f"Теперь ход спутника {cur.name}: закончи описание и <end/> — игрок может дать {cur.name} совет."

    def threat_line(self, scene) -> str:
        foe = scene.characters.get(scene.combat.threat)
        return (f"Удар {foe.name if foe else 'врага'} ещё не нанесён: опиши замах (без исхода) и закончи <end/> — "
                f"игрок опишет реакцию героя.")

    def free_reaction_hint(self, scene) -> str:
        foe = scene.characters.get(scene.combat.threat)
        w = C.equipped_weapon(foe) if foe else None
        return (f"[РЕАКЦИЯ] {foe.name if foe else 'Враг'} бьёт героя ({w.name if w else 'без оружия'}); игрок сам описал, "
                f"как защищается. Оцени: реально ли это сейчас и насколько трудно. Сначала поставь "
                f"<react attr=\"Атрибут\" difficulty=\"легко|обычно|трудно|очень трудно|невозможно\" "
                f"outcome=\"уклон|смягчить|контратака\" how=\"что делает герой, кратко\"/> — Система бросит и ответит, "
                f"потом опиши исход. уклон — удар мимо целиком; смягчить — урон вдвое меньше; контратака — без урона и "
                f"ответный удар (трудно). Не решай исход сам.")

    async def resolve_threat(self, scene, emit, transcript, text) -> str:
        """Active defence: the hero's reaction to the pending blow is settled by code (a parry strikes back at once),
        then the remaining foes act. Returns the line the narrator has to describe."""
        foe = scene.characters.get(scene.combat.threat)
        r = C.react(scene, C.parse_reaction(text or ""))
        return await self.after_reaction(scene, emit, transcript, r, foe)

    async def after_reaction(self, scene, emit, transcript, r, foe) -> str:
        """Show the reaction, strike back after a successful parry, let the remaining foes act."""
        hero = scene.player
        full = r["text"] + ("\n" + "\n".join(r["msgs"]) if r["msgs"] else "")
        await self.sys_msg(emit, scene, full, "roll")
        transcript.append({"kind": "roll", "text": full})
        self.log(scene, "roll", r["text"], hero.name)
        brief = r["brief"]
        if r["counter"] and foe and foe.hp > 0 and scene.combat.active:
            hp0 = foe.hp
            c = C.attack(scene, hero, foe)
            if c["hit"]:
                self._rewards(scene, hero, foe, hp0, c["msgs"])
            full = "↩ Контратака: " + c["text"] + ("\n" + "\n".join(c["msgs"]) if c["msgs"] else "")
            await self.sys_msg(emit, scene, full, "roll")
            transcript.append({"kind": "roll", "text": full})
            self.log(scene, "roll", c["text"], hero.name)
            brief += "; контратака — " + (f"попадание, урон {c['damage']}" if c["hit"] else "промах")
            if foe.hp == 0:
                brief += f", {foe.name} повержен(а)"
        if foe:
            self.mark_active(scene, hero.id, foe.id)
        after = await self.next_turns(scene, emit, transcript)
        return f"[РЕАКЦИЯ ГЕРОЯ — уже посчитано Системой] {brief}. Опиши именно это, коротко. {after}"

    async def emit_foes(self, scene, emit, transcript, acts) -> str:
        """Show each foe action as a roll; return one plain line for the narrator to describe."""
        for x in acts:
            await self.sys_msg(emit, scene, x["text"], "roll")
            transcript.append({"kind": "roll", "text": x["text"]})
            self.log(scene, "roll", x["text"].split("\n")[0], "Система")
        return ("Враги действуют: " + "; ".join(x["brief"] for x in acts) + ". Опиши именно это, коротко. ") if acts else ""

    def quiet_npcs(self, scene, turns: int = 3) -> list[str]:
        """Neutral NPCs that have been silent for a while — the narrator is asked whether they already left."""
        out = []
        for cid in scene.present:
            c = scene.characters.get(cid)
            if c and not c.is_player and cid not in scene.party and c.side != "hostile" \
                    and scene.turn - max(c.last_active_turn, 1) >= turns:
                out.append(c.name)
        return out[:5]

    def hero_idle(self, scene, actor, tag) -> str:
        """Only the player decides what the hero does: on a turn with no deed from the hero (pure speech, thoughts,
        «Продолжить») the narrator may not roll, attack or cast FOR the hero. NPC actions are untouched."""
        if not actor.is_player:
            return ""
        it = self.intent.get(scene.id, "missing")
        if it == "missing" or (it is not None and it.acts):
            return ""
        what = "только говорит или думает" if it is not None else "ничего не делает (ход «Продолжить»)"
        return (f"Отклонено: в этом ходу герой {what} — проверки, атаки и способности за героя не делай. "
                f"Покажи, как отвечают NPC и что делает мир; решать за героя будет игрок.")

    async def _introduce(self, scene, name, desc, emit, transcript, exact=False):
        # exact: members of a pack («Лесная тварь 2», «Волк 2») must not fuzzy-match an existing «… 1»
        exact = exact or bool(re.search(r"\d+\s*$", name))
        ex = (next((c for c in scene.characters.values() if M.norm(c.name) == M.norm(name)), None) if exact
              else M.resolve_character(scene, name))
        if ex and not ex.is_player:
            if ex.id not in scene.present:
                scene.present.append(ex.id)
            return ex
        c = new_character(scene, name, desc)
        scene.present.append(c.id)
        c.last_active_turn = scene.turn
        store.save_card(c)
        msg = f"Новый персонаж: {c.name}" + (f" — {desc}" if desc else "")
        await self.sys_msg(emit, scene, msg)
        transcript.append({"kind": "system", "text": msg})
        self.log(scene, "system", msg, "Система")
        return c

    async def _speak(self, scene, c, intent, emit, calls) -> str:
        # Latest player line this turn (so NPC hears words, not "silence")
        player_line = ""
        for e in reversed(scene.log):
            if e.turn != scene.turn:
                break
            if e.kind == "player":
                player_line = e.text or ""
                break
        # knowledge = own witnessed summaries/facts + own archived memories + recent witnessed events (bounded)
        old = MEM.character_knowledge(scene, c.id)
        mems = await self.retrieve(scene, intent + " " + " ".join(self.recent_knows(scene, c)[-2:]), c.id, 2)
        knows = old + [f"(помнит, ход {m['turn']}) {m['text'][:200]}" for m in mems] + self.recent_knows(scene, c)[-6:]
        extra = [{"role": "user", "content": P.speak_block(scene, c, intent, knows, player_line)}]
        # prefill "Имя: «" so the model writes the line itself instead of echoing <narrate> history format
        prompt = self.prompt(scene, open_model=f"{c.name}: «", extra=extra)
        line = ""
        for attempt in range(2):
            sid = next(_seg)

            async def on_delta(d):
                await emit({"type": "chunk", "id": sid, "kind": "speech", "who": c.name, "delta": d})

            content, st = await self.llm.complete(prompt, config.SPEAK_TOKENS, stop=["»", "\n", "<", "<turn|>"],
                                                  on_delta=on_delta, seed=self.seed(scene, 100 + len(calls)))
            st["kind"] = f"speak {c.name}" + (" (retry ru)" if attempt else ""); calls.append(st)
            line = clean_speech(content, c.name)
            if not line and attempt == 0:
                await emit({"type": "retract", "id": sid})
                continue
            line = line or "…"
            share = cyr_share(line, self.names(scene))
            if attempt == 0 and share is not None and share < config.CYR_MIN and len(line) > 15:
                await emit({"type": "retract", "id": sid})
                continue
            await emit({"type": "replace", "id": sid, "segments": [{"kind": "speech", "who": c.name, "text": line}]})
            break
        return line
