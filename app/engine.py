"""TurnEngine: narrator-orchestrated turn loop on a single llama.cpp slot, append-only prompt."""
import asyncio, itertools, re, time
from . import config, mechanics as M, prompts as P, store
from .models import Character, Event, Scene
from .prompt import render
from .protocol import clean_speech, cyr_share, parse_step, parse_tag, split_speech, visible_text, ParseError

Emit = callable
_seg = itertools.count(1)


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
    def __init__(self, llm):
        self.llm = llm
        self.lock = asyncio.Lock()

    # ---------- helpers ----------
    def names(self, scene):
        return [c.name for c in scene.characters.values()]

    def witnesses(self, scene):
        return list(scene.present)

    def log(self, scene, kind, text, who=None):
        scene.log.append(Event(turn=scene.turn, kind=kind, who=who, text=text, witnesses=self.witnesses(scene)))

    def knows(self, scene, c):
        out = []
        for e in scene.log:
            if c.id in e.witnesses and e.kind != "system":
                who = e.who or ("Рассказчик" if e.kind == "narration" else "")
                out.append((f"{who}: " if who else "") + e.text[:200])
        return out

    async def sys_msg(self, emit, scene, text, kind="system"):
        await emit({"type": "segment", "id": next(_seg), "kind": kind, "who": "Система", "text": text})

    # ---------- public ----------
    async def handle(self, scene: Scene, text: str | None, emit) -> dict:
        async with self.lock:
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

    def state(self, scene):
        return {"player_id": scene.player_id, "present": scene.present, "rule_mode": scene.rule_mode, "turn": scene.turn,
                "characters": {k: v.model_dump() for k, v in scene.characters.items()}}

    def prompt(self, scene, open_model=None, extra=None):
        turns = scene.turns + (extra or [])
        return render(scene.system_prompt, turns, open_model)

    async def warm(self, scene):
        async with self.lock:
            try:
                return await self.llm.warm(self.prompt(scene))
            except Exception:
                return None

    async def _turn(self, scene: Scene, text: str | None, emit) -> dict:
        t0 = time.time()
        scene.turn += 1; scene.turn_exp = 0
        calls = []
        if text is not None:
            await emit({"type": "player", "text": text})
            self.log(scene, "player", text, scene.player.name)
        else:
            await emit({"type": "continue"})
        scene.turns.append({"role": "user", "content": P.player_message(scene, M.state_line(scene), text)})
        transcript = []
        steps = 0; speaks = 0
        while steps < config.MAX_STEPS:
            steps += 1
            out, st = await self._narrate_step(scene, emit, calls)
            scene.turns.append({"role": "model", "content": out.raw})
            for part in out.parts:
                transcript.append(part)
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
            result = await self._execute(scene, tag, emit, calls, transcript)
            if tag.name == "speak":
                speaks += 1
            scene.turns.append({"role": "user", "content": "[СИСТЕМА: результат] " + result})
        stats = {"turn": scene.turn, "steps": steps, "speaks": speaks, "wall_s": round(time.time() - t0, 2),
                 "llm_calls": calls,
                 "prompt_n": sum(c.get("prompt_n") or 0 for c in calls),
                 "cache_n": sum(c.get("cache_n") or 0 for c in calls),
                 "predicted_n": sum(c.get("predicted_n") or 0 for c in calls)}
        scene.stats.append({k: v for k, v in stats.items() if k != "llm_calls"} | {"calls": len(calls)})
        store.save_scene(scene)
        await emit({"type": "turn_end", "stats": stats, "state": self.state(scene),
                    "text": "\n".join(("*" + p["text"] + "*") if p["kind"] == "narration" else
                                      (f"{p['who']}: {p['text']}" if p["kind"] == "speech" else f"[{p['text']}]")
                                      for p in transcript)})
        return stats

    async def _narrate_step(self, scene, emit, calls):
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
            content, st = await self.llm.complete(prompt, config.STEP_TOKENS, stop=["/>", "<turn|>", "<|turn>"],
                                                  on_delta=on_delta, seed=scene.rng_seed + scene.turn * 10 + attempt + len(calls))
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
        extra = [{"role": "user", "content": f"[СИСТЕМА: ошибка формата] {error}. Повтори только один тег протокола, например "
                                             f"<speak who=\"Имя\" intent=\"…\"/> или <end/>."}]
        content, st = await self.llm.complete(self.prompt(scene, open_model="", extra=extra), 80, stop=["/>", "<turn|>"])
        st["kind"] = "tag retry"; calls.append(st)
        try:
            m = re.search(r"<\s*\w+[^<]*$", content)
            tag = parse_tag((m.group(0) if m else content) + ("/>" if st.get("stop_word", "").strip() == "/>" else ""))
            scene.turns += extra + [{"role": "model", "content": content + "/>"}]
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
            return f"{c.name}: «{line}»"
        if tag.name == "roll":
            who = M.resolve_character(scene, a.get("who", "")) if a.get("who") else scene.player
            who = who or scene.player
            r = M.check(scene, who, a.get("skill", ""), a.get("dc", 12))
            text = "🎲 " + r["text"] if scene.rule_mode == "d20" else "⚙️ " + r["text"]
            msgs = []
            if r["success"] and who.is_player:
                M.grant_exp(scene, who, min(M.LIMITS["exp_turn"] - scene.turn_exp, 5 + max(0, r["dc"] - 10)), msgs)
                scene.turn_exp += 5 + max(0, r["dc"] - 10)
            full = text + ("\n" + "\n".join(msgs) if msgs else "")
            await self.sys_msg(emit, scene, full, "roll")
            transcript.append({"kind": "roll", "text": full})
            scene.log.append(Event(turn=scene.turn, kind="roll", who=who.name, text=r["text"], witnesses=self.witnesses(scene)))
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
        return "Неизвестное действие."

    async def _introduce(self, scene, name, desc, emit, transcript):
        ex = M.resolve_character(scene, name)
        if ex and not ex.is_player:
            if ex.id not in scene.present:
                scene.present.append(ex.id)
            return ex
        c = new_character(scene, name, desc)
        scene.present.append(c.id)
        store.save_card(c)
        msg = f"Новый персонаж: {c.name}" + (f" — {desc}" if desc else "")
        await self.sys_msg(emit, scene, msg)
        transcript.append({"kind": "system", "text": msg})
        self.log(scene, "system", msg, "Система")
        return c

    async def _speak(self, scene, c, intent, emit, calls) -> str:
        extra = [{"role": "user", "content": P.speak_block(scene, c, intent, self.knows(scene, c))}]
        # prefill "Имя: «" so the model writes the line itself instead of echoing <narrate> history format
        prompt = self.prompt(scene, open_model=f"{c.name}: «", extra=extra)
        line = ""
        for attempt in range(2):
            sid = next(_seg); buf = []

            async def on_delta(d):
                buf.append(d)
                await emit({"type": "chunk", "id": sid, "kind": "speech", "who": c.name, "delta": d})

            content, st = await self.llm.complete(prompt, config.SPEAK_TOKENS, stop=["»", "\n", "<", "<turn|>"],
                                                  on_delta=on_delta, seed=scene.rng_seed + scene.turn * 100 + len(calls))
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
