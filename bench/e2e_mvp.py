"""End-to-end scripted session against the running server + real llama-server.
Usage: python bench/e2e_mvp.py [base_url]   (default http://127.0.0.1:8082)"""
import asyncio, json, re, sys, time, urllib.request, urllib.parse, datetime, pathlib
import websockets

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8082"
OUT = pathlib.Path(__file__).resolve().parent
BOREK_CARD = json.dumps({"spec": "chara_card_v2", "spec_version": "2.0", "data": {
    "name": "Борек", "description": "{{char}} — хмурый наёмник лет сорока со шрамом через бровь. Сидит у камина, на поясе связка ключей.",
    "personality": "немногословный, подозрительный, грубый", "mes_example": "{{char}}: Чего уставился?"}}, ensure_ascii=False)
SCENE = {"title": "E2E Серый Брод", "rule_mode": "d20", "seed": 42,
         "setting": "Мрачное фэнтези. Пограничный городок Серый Брод у болот. Ночь, ливень. Таверна «Утопленник». "
                    "Неделю назад на болотной дороге пропал караван; ходят слухи о контрабандистах.",
         "player": {"name": "Ярен", "description": "Бывший наёмник, ищет пропавший караван брата.",
                    "attrs": {"Сила": 12, "Ловкость": 14, "Телосложение": 12, "Интеллект": 10, "Мудрость": 12, "Харизма": 11},
                    "skills": {"Скрытность": 2, "Ближний бой": 2, "Ловкость рук": 1}, "inventory": ["Короткий меч", "Нож", "Кошель"]},
         "npcs": [{"name": "Сильфа", "description": "Хозяйка таверны, рыжая, острая на язык, торгует слухами. Знает, что караван увели контрабандисты Борека.",
                   "personality": "насмешливая, расчётливая"},
                  {"card": BOREK_CARD}]}
SCRIPT = [("input", "Я вхожу в таверну, стряхиваю воду с плаща и подхожу к стойке. Сильфа, налей чего-нибудь крепкого."),
          ("input", "Спрашиваю Сильфу, что она слышала о пропавшем караване."),
          ("input", "Пока Борек дремлет у камина, пытаюсь незаметно снять у него с пояса связку ключей."),
          ("continue", None),
          ("input", "Система, статус"),
          ("input", "Подсаживаюсь к Бореку, кладу нож на стол и тихо спрашиваю, куда делся караван."),
          ("input", "Система, инвентарь")]


def http(path, body=None):
    req = urllib.request.Request(BASE + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def cyr(t):
    c = len(re.findall(r"[А-Яа-яЁё]", t)); l = len(re.findall(r"[A-Za-z]", t))
    return 1.0 if c + l == 0 else c / (c + l)


async def main():
    sid = http("/api/scenes", SCENE)["id"]
    ws_url = BASE.replace("http", "ws") + f"/ws/{urllib.parse.quote(sid)}"
    turns = []
    async with websockets.connect(ws_url, max_size=None) as ws:
        json.loads(await ws.recv())
        for kind, text in SCRIPT:
            t0 = time.time(); first = None; chunks = 0; segs = []; retracts = 0
            await ws.send(json.dumps({"type": kind, "text": text} if kind == "input" else {"type": "continue"}, ensure_ascii=False))
            while True:
                m = json.loads(await ws.recv())
                if m["type"] == "chunk":
                    chunks += 1; first = first or round(time.time() - t0, 2)
                elif m["type"] == "retract":
                    retracts += 1
                elif m["type"] == "segment":
                    first = first or round(time.time() - t0, 2); segs.append(m)
                elif m["type"] == "error":
                    print("ERROR", m); break
                elif m["type"] == "turn_end":
                    break
            st = m.get("stats", {})
            out = m.get("text") or "\n".join(s["text"] for s in segs)
            turns.append({"kind": kind, "input": text, "output": out, "wall_s": round(time.time() - t0, 2),
                          "first_chunk_s": first, "chunks": chunks, "retracts": retracts, "stats": st,
                          "cyr": round(cyr(re.sub(r"\[[^\]]*\]", "", out)), 3)})
            print(f"--- {kind}: {text}\n{out}\n[{turns[-1]['wall_s']} s, first {first} s, "
                  f"cache {st.get('cache_n')} / read {st.get('prompt_n')}]\n", flush=True)
    final = http(f"/api/scenes/{urllib.parse.quote(sid)}")
    write_md(sid, turns, final)


def write_md(sid, turns, final):
    L = [f"# E2E MVP session ({datetime.datetime.now():%Y-%m-%d %H:%M}, Europe/Sofia)", "",
         f"Scene `{sid}`, rule mode d20, 2 NPC (Сильфа from text, Борек from a V2 JSON card). Gemma 4 26B-A4B on llama-server :11435.", "",
         "## Per-turn metrics", "",
         "| # | action | wall s | first chunk s | LLM calls | cached tok (Σ cache_n) | re-read tok (Σ prompt_n) | generated | speaks | Cyrillic |",
         "|---|---|---|---|---|---|---|---|---|---|"]
    for i, t in enumerate(turns, 1):
        s = t["stats"]; a = (t["input"] or "⏵ Продолжить")[:45]
        L.append(f"| {i} | {a} | {t['wall_s']} | {t['first_chunk_s']} | {len(s.get('llm_calls', []))} | {s.get('cache_n', 0)} | "
                 f"{s.get('prompt_n', 0)} | {s.get('predicted_n', 0)} | {s.get('speaks', '-')} | {t['cyr']} |")
    L += ["", "## Per-call detail", "", "| turn | call | cache_n | prompt_n | prompt ms | gen tok | gen ms | wall s |", "|---|---|---|---|---|---|---|---|"]
    for i, t in enumerate(turns, 1):
        for c in t["stats"].get("llm_calls", []):
            L.append(f"| {i} | {c['kind']} | {c['cache_n']} | {c['prompt_n']} | {c['prompt_ms']} | {c['predicted_n']} | {c['predicted_ms']} | {c['wall_s']} |")
    L += ["", "## Transcript", ""]
    for i, t in enumerate(turns, 1):
        L += [f"### Ход {i}", "", f"> **Игрок:** {t['input'] or '⏵ Продолжить'}", "", t["output"], ""]
    p = final["state"]["characters"][final["state"]["player_id"]]
    L += ["## Final player state", "", f"HP {p['hp']}/{p['max_hp']}, MP {p['mp']}/{p['max_mp']}, EXP {p['exp']}/{p['exp_next']}, "
          f"level {p['level']}, inventory: {', '.join(i['name'] for i in p['inventory'])}", "",
          "Present: " + ", ".join(final["state"]["characters"][c]["name"] for c in final["state"]["present"])]
    (OUT / "e2e-mvp.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    (OUT / "e2e-mvp.json").write_text(json.dumps(turns, ensure_ascii=False, indent=1), encoding="utf-8")


asyncio.run(main())
