"""Long scripted session (Phase 2): grows the prompt past ~20k tokens so archiving/summaries/facts trigger,
then checks recall of early facts, hot-path cache behaviour, dynamic budget tiers, regenerate and rewind.
Usage: python bench/e2e_long.py [base_url]  ->  bench/e2e-long.md / .json"""
import asyncio, json, re, sys, time, urllib.request, urllib.parse, datetime, pathlib
import websockets

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8082"
import os
OUT = pathlib.Path(__file__).resolve().parent
TAG = os.environ.get("E2E_TAG", "e2e-long")
SCENE = {"title": "Long Серый Брод", "rule_mode": "d20", "seed": 7,
         "setting": "Мрачное фэнтези. Пограничный городок Серый Брод у болот. Ночь, ливень. Таверна «Утопленник». "
                    "Неделю назад на болотной дороге пропал караван; ходят слухи о контрабандистах с Чёрной Топи.",
         "player": {"name": "Ярен", "description": "Бывший наёмник, ищет пропавший караван брата.",
                    "attrs": {"Сила": 12, "Ловкость": 14, "Телосложение": 12, "Интеллект": 10, "Мудрость": 12, "Харизма": 11},
                    "skills": {"Скрытность": 2, "Ближний бой": 2, "Ловкость рук": 1, "Восприятие": 1},
                    "inventory": ["Короткий меч", "Нож", "Кошель", "Серебряный ключ брата"]},
         "npcs": [{"name": "Сильфа", "description": "Хозяйка таверны, рыжая, острая на язык, торгует слухами.", "personality": "насмешливая, расчётливая"},
                  {"name": "Борек", "description": "Хмурый наёмник у камина со шрамом через бровь. Связан с контрабандистами.", "personality": "грубый, подозрительный"}]}
I, C = "input", "continue"
SCRIPT = [
    (I, "Вхожу в таверну «Утопленник», стряхиваю воду с плаща и сажусь у стойки. Сильфа, налей эля."),
    (I, "Шепчу Сильфе: «Мой брат Тарен перед исчезновением сказал, что пароль контрабандистов — „Чёрная цапля“. Это правда?»"),
    (I, "Пока все отвлеклись на шум у двери, прячу серебряный ключ брата под третьей половицей у камина."),
    (I, "Плачу Сильфе пять серебряных за комнату на ночь и спрашиваю, кто такой Борек."),
    (I, "Подхожу к Бореку и спрашиваю: «Ты ходил с караваном Тарена?»"),
    (C, None),
    (I, "Отвечаю Бореку: «Я просто ищу брата. Мне не нужны неприятности.»"),
    (I, "Поднимаюсь в свою комнату, запираю дверь и осматриваю её."),
    (I, "Ложусь спать, положив меч рядом с кроватью."),
    (C, None),
    (I, "Утром спускаюсь вниз, завтракаю и спрашиваю Сильфу, какая дорога ведёт к Чёрной Топи."),
    (I, "Отправляюсь по болотной дороге к Чёрной Топи, внимательно глядя по сторонам."),
    (I, "Осматриваю следы колёс на размокшей дороге — может, это следы каравана?"),
    (C, None),
    (I, "Из тумана выходят двое бандитов. Выхватываю меч и атакую ближайшего!"),
    (I, "Продолжаю бой: бью второго бандита и пытаюсь уйти от его удара."),
    (I, "Обыскиваю тела бандитов."),
    (I, "Иду дальше вглубь топи, к огням, которые мерцают между деревьями."),
    (I, "Подкрадываюсь к лагерю у огней и наблюдаю, кто там."),
    (I, "Выхожу к костру, поднимаю руки и говорю: «Я пришёл с миром. Мне нужен главный.»"),
    (I, "Говорю главарю: «Я ищу караван брата. Что вы о нём знаете?»"),
    (C, None),
    (I, "Предлагаю главарю сделку: мой меч в обмен на сведения о брате."),
    (I, "Осматриваю лагерь, пока главарь думает, и ищу следы каравана."),
    (I, "Возвращаюсь по дороге обратно в Серый Брод, пока не стемнело."),
    (I, "На полпути к городу сворачиваю к старой мельнице, где, говорят, прячут товар."),
    (I, "Осторожно вхожу в мельницу и осматриваю жернова и мешки."),
    (I, "Нахожу в мешке с зерном обрывок письма. Читаю его при свете фонаря."),
    (C, None),
    (I, "Слышу шаги наверху. Прячусь за мешками и жду."),
    (I, "Наверху появляется незнакомец в плаще. Окликаю его: «Кто ты такой?»"),
    (I, "Спрашиваю незнакомца, что он знает о Чёрной Топи и пропавших людях."),
    (I, "Предлагаю незнакомцу пойти со мной в город, там безопаснее."),
    (I, "Выходим из мельницы и идём по дороге в Серый Брод под дождём."),
    (C, None),
    (I, "У городских ворот стражники требуют пошлину. Плачу и прохожу."),
    (I, "Иду через рыночную площадь, где толпа слушает глашатая."),
    (I, "Прислушиваюсь к словам глашатая о награде за контрабандистов."),
    (I, "Ухожу с площади и направляюсь к таверне."),
    (I, "Вхожу в таверну и сажусь у стойки. Сильфа, налей чего-нибудь горячего."),
    (I, "Тихо спрашиваю Сильфу: «Помнишь, какой пароль контрабандистов я тебе называл в первый вечер?»"),
    (I, "Иду к камину и проверяю свой тайник — там ли ещё то, что я спрятал в первую ночь?"),
    (I, "Вспоминаю вслух: «Тарен... как же мне найти тебя, брат?» — и спрашиваю Борека, знал ли он Тарена."),
    (C, None),
    (I, "Говорю Сильфе: «Завтра я снова пойду в топь. Если не вернусь — отдай ключ кому-нибудь честному.»"),
    (I, "Система, статус"),
]
RECALL = {}  # filled below: 1-based index in SCRIPT -> patterns


for _i, (_k, _t) in enumerate(SCRIPT, 1):
    if _t and "пароль контрабандистов я тебе" in _t: RECALL[_i] = [r"цапл"]
    if _t and "проверяю свой тайник" in _t: RECALL[_i] = [r"половиц|под пол|доск", r"ключ"]
    if _t and "знал ли он Тарена" in _t: RECALL[_i] = [r"Тарен"]


def http(path, body=None):
    req = urllib.request.Request(BASE + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def cyr(t):
    c = len(re.findall(r"[А-Яа-яЁё]", t)); l = len(re.findall(r"[A-Za-z]", t))
    return 1.0 if c + l == 0 else c / (c + l)


async def recv_turn(ws, t0):
    first = None; mem = []
    while True:
        m = json.loads(await ws.recv())
        if m["type"] in ("chunk", "segment") and first is None:
            first = round(time.time() - t0, 2)
        if m["type"] == "error":
            print("ERROR", m, flush=True); return m, first
        if m["type"] == "turn_end":
            return m, first


async def wait_background(ws):
    """After a turn the server may archive in the background; wait for it like a reading player would."""
    try:
        m = json.loads(await asyncio.wait_for(ws.recv(), 2.0))
    except asyncio.TimeoutError:
        return None
    if m.get("type") == "memory":
        t0 = time.time()
        m2 = json.loads(await asyncio.wait_for(ws.recv(), 600))
        return {"text": m2.get("text"), "stats": m2.get("stats"), "wait_s": round(time.time() - t0, 2)}
    return None


async def main():
    sid = http("/api/scenes", SCENE)["id"]
    url = BASE.replace("http", "ws") + f"/ws/{urllib.parse.quote(sid)}"
    turns = []; bgs = []
    async with websockets.connect(url, max_size=None) as ws:
        json.loads(await ws.recv())
        for n, (kind, text) in enumerate(SCRIPT, 1):
            t0 = time.time()
            await ws.send(json.dumps({"type": kind, "text": text} if kind == I else {"type": "continue"}, ensure_ascii=False))
            m, first = await recv_turn(ws, t0)
            st = m.get("stats", {})
            out = m.get("text") or ""
            if not out and m.get("type") == "turn_end":
                out = "(команда Системы)"
            rec = {"n": n, "kind": kind, "input": text, "output": out, "wall_s": round(time.time() - t0, 2), "first_s": first,
                   "stats": st, "memories": m.get("memories"), "cyr": round(cyr(re.sub(r"\[[^\]]*\]", "", out)), 3)}
            if n in RECALL:
                rec["recall"] = {p: bool(re.search(p, out, re.I)) for p in RECALL[n]}
            turns.append(rec)
            print(f"#{n} [{st.get('tier')}] {rec['wall_s']}s ptok={st.get('prompt_tokens')} read={st.get('prompt_n')} "
                  f"maxhot={st.get('max_hot_prompt_n')} sp={st.get('speaks')} w={st.get('narr_words')} mem={len(m.get('memories') or [])}"
                  + (f" recall={rec['recall']}" if 'recall' in rec else ""), flush=True)
            bg = await wait_background(ws)
            if bg:
                bg["after"] = n; bgs.append(bg); print("   BG", bg["text"], flush=True)
        # regenerate the last LLM turn, then rewind it
        t0 = time.time(); await ws.send(json.dumps({"type": "regenerate"}))
        m = json.loads(await ws.recv())
        m, first = await recv_turn(ws, t0)
        regen = {"wall_s": round(time.time() - t0, 2), "first_s": first, "stats": m.get("stats"), "output": m.get("text")}
        print("REGEN", regen["wall_s"], m.get("stats", {}).get("prompt_n"), flush=True)
        await wait_background(ws)
        last_turn = m.get("stats", {}).get("turn")
        t0 = time.time(); await ws.send(json.dumps({"type": "rewind", "turn": last_turn}))
        rw = json.loads(await ws.recv()); rewind = {"reply": rw.get("type"), "wall_s": round(time.time() - t0, 2)}
        t0 = time.time(); await ws.send(json.dumps({"type": "continue"}))
        m, first = await recv_turn(ws, t0)
        after_rw = {"wall_s": round(time.time() - t0, 2), "stats": m.get("stats"), "output": m.get("text")}
        print("AFTER REWIND", after_rw["wall_s"], m.get("stats", {}).get("prompt_n"), flush=True)
        await wait_background(ws)
    final = http(f"/api/scenes/{urllib.parse.quote(sid)}")
    sc = json.loads((OUT.parent / "data" / "scenes" / f"{sid}.json").read_text(encoding="utf-8"))
    res = {"sid": sid, "turns": turns, "background": bgs, "regen": regen, "rewind": rewind, "after_rewind": after_rw,
           "summaries": sc["summaries"], "facts": sc["facts"], "final_state": final["state"]}
    (OUT / f"{TAG}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    write_md(res)


def write_md(r):
    T = r["turns"]; llm = [t for t in T if t["stats"].get("llm_calls")]
    L = [f"# E2E long session — Phase 2 memory ({datetime.datetime.now():%Y-%m-%d %H:%M}, Europe/Sofia)", "",
         f"Scene `{r['sid']}`, d20, 2 starting NPC, {len(T)} scripted inputs + regenerate + rewind. Gemma 4 26B-A4B, llama-server :11435.", "",
         "## Per-turn", "",
         "| # | input | tier (reasons) | wall s | 1st chunk s | prompt tok | Σ cached | Σ re-read | max hot re-read | NPC lines | narr words | mem | Cyr |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for t in T:
        s = t["stats"]
        L.append(f"| {t['n']} | {(t['input'] or '⏵ Продолжить')[:40]} | {s.get('tier', '-')} ({', '.join(s.get('tier_reasons', []))}) | "
                 f"{t['wall_s']} | {t['first_s']} | {s.get('prompt_tokens', '-')} | {s.get('cache_n', 0)} | {s.get('prompt_n', 0)} | "
                 f"{s.get('max_hot_prompt_n', 0)} | {s.get('speaks', '-')} | {s.get('narr_words', '-')} | {len(t.get('memories') or [])} | {t['cyr']} |")
    walls = sorted(t["wall_s"] for t in llm); firsts = sorted(t["first_s"] for t in llm if t["first_s"])
    hot = [t["stats"].get("max_hot_prompt_n", 0) for t in llm]
    tiers = {}
    for t in llm:
        tiers.setdefault(t["stats"]["tier"], []).append(t)
    L += ["", "## Summary", "",
          f"- LLM turns: {len(llm)}; wall median {walls[len(walls)//2]} s, max {walls[-1]} s; first chunk median {firsts[len(firsts)//2]} s.",
          f"- Max prompt size: {max(t['stats'].get('prompt_tokens') or 0 for t in llm)} tokens; max hot-path single-call re-read: {max(hot)} tokens.",
          f"- Calls with re-read > 2000 tokens in the hot path: {sum(1 for c in [c for t in llm for c in t['stats']['llm_calls']] if (c.get('prompt_n') or 0) > 2000 and c['kind'] != 'archive (forced)')}.",
          "- Tiers: " + "; ".join(f"**{k}** ×{len(v)}: avg NPC lines {sum(x['stats']['speaks'] for x in v)/len(v):.1f}, "
                                 f"avg narration words {sum(x['stats']['narr_words'] for x in v)/len(v):.0f}" for k, v in tiers.items()), ""]
    L += ["## Background archiving", "", "| after # | result | wall s | calls (kind: re-read/cached) |", "|---|---|---|---|"]
    for b in r["background"]:
        s = b.get("stats") or {}
        L.append(f"| {b['after']} | {b['text']} | {s.get('wall_s')} | " + ", ".join(f"{c['kind']}: {c.get('prompt_n')}/{c.get('cache_n')}" for c in s.get("calls", [])) + " |")
    L += ["", "## Recall of early facts", ""]
    for t in T:
        if "recall" in t:
            L += [f"- #{t['n']} «{t['input']}» → {t['recall']}", f"  - retrieved: {t.get('memories')}", f"  - output: {t['output'][:600]}"]
    L += ["", "## Summaries", ""] + [f"- L{s['layer']} turns {s['from_turn']}–{s['to_turn']}: {s['text']}" for s in r["summaries"]]
    L += ["", "## Facts", ""] + [f"- #{f['id']} {f['text']} (known_by {f['known_by']})" for f in r["facts"]]
    rg = r["regen"]; ar = r["after_rewind"]
    L += ["", "## Regenerate / rewind", "",
          f"- Regenerate last turn: {rg['wall_s']} s, re-read {rg['stats'].get('prompt_n') if rg.get('stats') else '-'} tok.",
          f"- Rewind reply: {r['rewind']}; next Продолжить: {ar['wall_s']} s, re-read {ar['stats'].get('prompt_n') if ar.get('stats') else '-'} tok.", "",
          "## Transcript", ""]
    for t in T:
        L += [f"### {t['n']}. {t['input'] or '⏵ Продолжить'}", "", t["output"], ""]
    (OUT / f"{TAG}.md").write_text("\n".join(L) + "\n", encoding="utf-8")


asyncio.run(main())
