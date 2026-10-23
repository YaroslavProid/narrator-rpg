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
    for a, v in (player.get("attrs") or {}).items():
        a2 = M.resolve_attr(a)
        if a2: p.attrs[a2] = max(3, min(20, int(v)))
    for s, v in (player.get("skills") or {}).items():
        s2 = M.resolve_skill(s)
        if s2: p.skills[s2] = max(0, min(5, int(v)))
    for it in player.get("inventory") or []:
        M.apply_delta(scene, p, {"add": it})
    scene.present = [p.id]
    for n in npcs:
        c = new_character(scene, n["name"], n.get("description", ""), personality=n.get("personality", ""),
                          speech=n.get("speech", ""), secrets=n.get("secrets", ""))
        if n.get("present", True):
            scene.present.append(c.id)
        store.save_card(c)
    scene.initial_present = list(scene.present)
    scene.system_prompt = P.build_system(scene)
    store.save_scene(scene)
    return scene


class TurnEngine:
    def __init__(self, llm, memory: "MEM.VectorStore | None" = None):
        self.llm = llm
        self.mem = memory
        self.lock = asyncio.Lock()
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
        return {"player_id": scene.player_id, "present": scene.present, "rule_mode": scene.rule_mode, "turn": scene.turn,
                "settings": scene.settings.model_dump(), "summaries": len(scene.summaries), "facts": len(scene.facts),
                "archived_turn": scene.archived_turn, "combat": scene.combat.model_dump(),
                "characters": {k: v.model_dump() for k, v in scene.characters.items()}}

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
                    out = M.run_system_command(scene, *cmd)
                    await emit({"type": "player", "text": text})
                    await self.sys_msg(emit, scene, out)
                    scene.log.append(Event(turn=scene.turn, kind="system", who="Система", text=out, witnesses=[scene.player_id]))
                    store.save_scene(scene)
                    st = {"turn": scene.turn, "command": cmd[0], "llm_calls": [], "wall_s": 0}
                    await emit({"type": "turn_end", "stats": st, "state": self.state(scene)})
                    return st
            return await self._turn(scene, text, emit)

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
                "stats_len": len(scene.stats), "combat": scene.combat.model_dump()}

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
            s = scene.snapshots[-1]
            self.rewind(scene, s["turn"] + 1)
            scene.regen += 1
            await emit({"type": "reload"})
            return await self._turn(scene, s["input"], emit)

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
        # force a single undersized block only when forced and nothing else fits
        if forced and not blocks:
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
            msgs.append(f"Архив: ходы {from_turn}–{to_turn} ({acc} ток.) → сводка, фактов +{len(new_facts)}")
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
        for cid in list(scene.present):
            if cid == scene.player_id:
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

    async def _turn(self, scene: Scene, text: str | None, emit) -> dict:
        t0 = time.time()
        scene.snapshots.append(self.snapshot(scene, text))
        introduced, last_tier, combat = self._signals(scene)
        scene.turn += 1; scene.turn_exp = 0
        calls = []
        # auto-leave inactive NPCs before budgeting
        prune_msgs = self.prune_inactive(scene)
        npcs = self.active_npcs(scene)
        st_ = scene.settings
        budget = turn_budget(text, npcs, introduced, last_tier, combat or scene.combat.active,
                             st_.length_mult, st_.max_speaks)
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
        for msg in prune_msgs:
            await self.sys_msg(emit, scene, "🚪 " + msg)
        prompt_tokens = await self.ensure_budget(scene, calls) if config.MEMORY else None
        last_narr = " ".join(e.text for e in scene.log[-6:] if e.kind in ("narration", "speech", "player"))[-400:]
        mems = await self.retrieve(scene, (text or "") + " " + last_narr, scene.player_id, config.RETRIEVE_K, last_narr)
        scene.memory_injected += [f"{m['id']}|{scene.turn}" for m in mems]
        mem_lines = [f"(ход {m['turn']}) {m['text'][:220]}" for m in mems]
        scene.turns.append({"role": "user", "content": P.player_message(scene, M.state_line(scene), text,
                                                                         hint=budget.hint, memories=mem_lines)})
        transcript = []
        steps = 0; speaks = 0
        while steps < budget.max_steps:
            steps += 1
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
                b2 = turn_budget(text, npcs, introduced, last_tier, combat, st_.length_mult, st_.max_speaks,
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
        g = tag_grammar(names, list(M.SKILLS))
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
        if tag.name == "roll":
            who = (M.resolve_character(scene, a.get("who", "")) if a.get("who") else scene.player) or scene.player
            r = M.check(scene, who, a.get("skill", ""), a.get("dc", 12))
            text = ("🎲 " if scene.rule_mode == "d20" else "⚙️ ") + r["text"]
            msgs = []
            if r["success"] and who.is_player:
                gain = 5 + max(0, r["dc"] - 10)
                M.grant_exp(scene, who, min(M.LIMITS["exp_turn"] - scene.turn_exp, gain), msgs)
                scene.turn_exp += gain
            full = text + ("\n" + "\n".join(msgs) if msgs else "")
            await self.sys_msg(emit, scene, full, "roll")
            transcript.append({"kind": "roll", "text": full})
            scene.log.append(Event(turn=scene.turn, kind="roll", who=who.name, text=r["text"], witnesses=list(scene.present)))
            return r["text"] + " Опиши последствия согласно результату."
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
            c = await self._introduce(scene, a["name"], a.get("desc", ""), emit, transcript)
            return f"В сцену входит {c.name}: {c.description or 'новый персонаж'}."
        if tag.name == "leave":
            c = M.resolve_character(scene, a.get("who", ""), only_present=True)
            if not c or c.is_player:
                return "Этого персонажа нет в сцене."
            scene.present.remove(c.id)
            self.log(scene, "system", f"{c.name} уходит.", "Система")
            return f"{c.name} покидает сцену."
        if tag.name == "attack":
            atk = (M.resolve_character(scene, a.get("who", "")) if a.get("who") else scene.player) or scene.player
            tgt = M.resolve_character(scene, a.get("target", ""))
            if not tgt:
                return f"Нет цели «{a.get('target')}»."
            if scene.rule_mode == "система" and not scene.combat.active:
                # still allow attack; combat.attack uses no-dice path
                pass
            r = C.attack(scene, atk, tgt, a.get("weapon", ""))
            full = r["text"]
            if r["msgs"]:
                full += "\n" + "\n".join(r["msgs"])
            if scene.combat.active:
                adv = C.advance_turn(scene)
                if adv:
                    full += "\n" + adv
            await self.sys_msg(emit, scene, full, "roll")
            transcript.append({"kind": "roll", "text": full})
            self.log(scene, "roll", r["text"], atk.name)
            self.mark_active(scene, atk.id, tgt.id)
            return r["text"] + " Опиши последствия удара."
        if tag.name == "combat":
            act = (a.get("action") or "").lower()
            if act in ("start", "begin", "бой"):
                foes = [x.strip() for x in (a.get("foes") or a.get("who") or "").split(",") if x.strip()]
                ids = []
                for f in foes:
                    c = M.resolve_character(scene, f) or await self._introduce(scene, f, "", emit, transcript)
                    ids.append(c.id)
                text = C.start_combat(scene, ids or [i for i in scene.present if i != scene.player_id][:4])
                await self.sys_msg(emit, scene, text, "roll")
                transcript.append({"kind": "roll", "text": text})
                self.log(scene, "system", text, "Система")
                return text
            if act in ("end", "stop", "конец"):
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

    async def _introduce(self, scene, name, desc, emit, transcript):
        ex = M.resolve_character(scene, name)
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
        # knowledge = own witnessed summaries/facts + own archived memories + recent witnessed events (bounded)
        old = MEM.character_knowledge(scene, c.id)
        mems = await self.retrieve(scene, intent + " " + " ".join(self.recent_knows(scene, c)[-2:]), c.id, 2)
        knows = old + [f"(помнит, ход {m['turn']}) {m['text'][:200]}" for m in mems] + self.recent_knows(scene, c)[-6:]
        extra = [{"role": "user", "content": P.speak_block(scene, c, intent, knows)}]
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
