"""Phase 0 measurements for narrator-rpg (stdlib only). Run: python bench/phase0.py"""
import json, re, time, sys, random, urllib.request, os, datetime
BASE = os.environ.get("LLM_URL", "http://localhost:11435")
OUT = os.path.dirname(os.path.abspath(__file__))
N = int(os.environ.get("TRIALS", "10"))
RES = {"started": datetime.datetime.now().isoformat(), "cache": [], "inject": [], "proto": []}

def http(path, body=None, timeout=600):
    req = urllib.request.Request(BASE + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())

def wait_idle():
    waited = 0
    while True:
        try:
            s = http("/slots")
            if not any(x.get("is_processing") for x in s): return waited
        except Exception as e:
            print("slots err", e)
        time.sleep(3); waited += 3
        if waited % 30 == 0: print(f"  ..waiting for busy slot {waited}s", flush=True)
        if waited > 600: raise SystemExit("server busy >10min, aborting")

def cyr_share(t):
    t = re.sub(r"<[^>]*>", " ", t)
    c = len(re.findall(r"[А-Яа-яЁё]", t)); l = len(re.findall(r"[A-Za-z]", t))
    return None if c + l == 0 else round(c / (c + l), 3)

def save():
    json.dump(RES, open(os.path.join(OUT, "results-phase0.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)

# ---------------- 1. cache ----------------
NAMES = ["Сильфа", "Борек", "Мирна", "Тавин", "Огрен", "Лиса", "Варг", "Эльза"]
PLACES = ["Серый Брод", "Тёмный лес", "Каменный мост", "Старая мельница", "Башня мага", "Порт Ветров", "Сухая балка", "Храм Луны"]
def lore_block(i, variant=""):
    r = random.Random(i)
    p = r.choice(PLACES); n = r.choice(NAMES)
    return (f"### Запись {i}: {p} ({i}){variant}\n"
            f"Место {p} номер {i} известно тем, что здесь когда-то жил {n}. Местные рассказывают, что по ночам "
            f"у ворот слышны шаги, а торговцы избегают дороги после заката. Цена хлеба здесь {r.randint(2,9)} медяков, "
            f"а стража берёт пошлину {r.randint(1,5)} серебряных. В подвале таверны хранится {r.randint(10,90)} бочек эля. "
            f"{n} задолжал гильдии {r.randint(50,500)} золотых и скрывается уже {r.randint(2,30)} дней. "
            f"Погода обычно {r.choice(['дождливая','ветреная','морозная','душная'])}, а дороги {r.choice(['размыты','пыльны','заснежены'])}.\n")

SYS = ("Ты — рассказчик текстовой ролевой игры. Пиши только по-русски, кратко, 2-4 предложения. "
       "Повествование в настоящем времени, от второго лица.\n\n# Мир\n")
def build_msgs(nlore, history, middle_variant=None, tail_user=None):
    lore = "".join(lore_block(i, (middle_variant if (middle_variant and i == nlore // 2) else "")) for i in range(nlore))
    msgs = [{"role": "system", "content": SYS + lore}]
    msgs += history
    if tail_user: msgs.append({"role": "user", "content": tail_user})
    return msgs

def render(msgs):
    return http("/apply-template", {"messages": msgs})["prompt"]

def ntok(text):
    return len(http("/tokenize", {"content": text})["tokens"])

def complete(prompt, n_predict=60, **kw):
    wait_idle()
    t = time.time()
    r = http("/completion", {"prompt": prompt, "n_predict": n_predict, "cache_prompt": True, "id_slot": 0,
                             "temperature": 0.7, "seed": 42, **kw})
    tm = r.get("timings", {})
    return r.get("content", ""), {"wall_s": round(time.time() - t, 2), "prompt_n": tm.get("prompt_n"),
            "cache_n": tm.get("cache_n"), "tokens_cached": r.get("tokens_cached"),
            "tokens_evaluated": r.get("tokens_evaluated"), "prompt_ms": round(tm.get("prompt_ms") or 0),
            "predicted_n": tm.get("predicted_n"), "predicted_ms": round(tm.get("predicted_ms") or 0)}

def test_cache():
    print("== 1. cache", flush=True)
    nlore = 120
    while ntok(render(build_msgs(nlore, [], tail_user="x"))) < 16500: nlore += 30
    hist = []
    users = ["Я вхожу в таверну и осматриваюсь.", "Подхожу к стойке и заказываю эль.",
             "Спрашиваю, не видели ли тут Борека.", "Сажусь у окна и слушаю разговоры.",
             "Выхожу на улицу под дождь."]
    def step(label, msgs):
        p = render(msgs); out, m = complete(p)
        m.update(label=label, prompt_tokens_total=ntok(p), out_cyr=cyr_share(out), out=out[:200])
        RES["cache"].append(m); save(); print(" ", label, {k: m[k] for k in ("prompt_tokens_total","prompt_n","cache_n","tokens_cached","wall_s","prompt_ms")}, flush=True)
        return out
    out = step("t0 cold (full prompt)", build_msgs(nlore, hist, tail_user=users[0]))
    out = step("t0b identical repeat", build_msgs(nlore, hist, tail_user=users[0]))
    for k in (1, 2, 3):
        hist += [{"role": "user", "content": users[k-1]}, {"role": "assistant", "content": out.strip()}]
        out = step(f"t{k} append-only", build_msgs(nlore, hist, tail_user=users[k]))
    # [РЕПЛИКА] tail: same user turn pending + narrator reply + replica request
    hist += [{"role": "user", "content": users[3]}, {"role": "assistant", "content": "<narrate>За соседним столом Сильфа поднимает глаза.</narrate>\n<speak who=\"Сильфа\" intent=\"насмешливо спросить про долг\"/>"}]
    rep = ("[РЕПЛИКА] Говорит Сильфа. Карточка: трактирщица, острая на язык, 30 лет. Что она знает: игрок ищет Борека. "
           "Намерение: насмешливо спросить про долг. Ответь одной-тремя фразами от её лица, по-русски, без повествования.")
    line = step("t4 +[РЕПЛИКА] tail", build_msgs(nlore, hist, tail_user=rep))
    hist += [{"role": "user", "content": "[СИСТЕМА: реплика] Сильфа: " + line.strip().split("\n")[0][:200]}]
    out = step("t5 РЕПЛИКА removed, line appended", build_msgs(nlore, hist, tail_user=users[4]))
    step("t6 middle lore block changed", build_msgs(nlore, hist, middle_variant=" [изменено]", tail_user=users[4]))
    step("t7 same as t6 (re-hit)", build_msgs(nlore, hist, middle_variant=" [изменено]", tail_user=users[4]))

# ---------------- chat helper ----------------
def chat(body):
    wait_idle(); t = time.time()
    r = http("/v1/chat/completions", {"temperature": 0.8, "max_tokens": 300, **body})
    return r, round(time.time() - t, 2)

SCENE = ("Ты — рассказчик текстовой ролевой игры по-русски. Мир: портовый город Серый Брод, ночь, дождь. "
         "Игрок — вор Тавин. Броски кубиков делает ТОЛЬКО код; результаты бросков приходят от системы и они окончательны. "
         "Пиши только по-русски, 2-4 предложения, второе лицо.")
JUDGE = ("Игроку выпал ПРОВАЛ проверки (d20=3 против сложности 15) при попытке взобраться по мокрой стене склада. "
         "Ниже повествование рассказчика. Соответствует ли оно провалу (игрок НЕ смог взобраться / сорвался / не добился цели)? "
         "Ответь JSON.\n\nПовествование:\n")

def judge(text):
    r, _ = chat({"messages": [{"role": "user", "content": JUDGE + text}], "temperature": 0, "max_tokens": 40,
                 "response_format": {"type": "json_schema", "json_schema": {"name": "j", "schema": {"type": "object",
                    "properties": {"respects_failure": {"type": "boolean"}}, "required": ["respects_failure"]}}}})
    try: return json.loads(r["choices"][0]["message"]["content"])["respects_failure"]
    except Exception: return None

FAIL_WORDS = re.compile(r"сорв|падаешь|упал|соскальз|не удаётся|не удается|не получается|не смог|неудач|провал|срываешь|скольз", re.I)
SUCC_WORDS = re.compile(r"(взбираешься|забираешься|оказываешься) на (крыш|верх)|добираешься до (крыш|окна)", re.I)

def test_inject():
    print("== 2. result injection", flush=True)
    rollargs = {"skill": "Атлетика", "dc": 15}
    res_text = "Проверка Атлетики: 1d20+0 = 3 против 15 — провал."
    for variant in ("user_label", "tool_role"):
        for i in range(N):
            msgs = [{"role": "system", "content": SCENE},
                    {"role": "user", "content": "Я лезу по мокрой стене склада к окну на втором этаже."}]
            if variant == "user_label":
                msgs += [{"role": "assistant", "content": "<narrate>Ты цепляешься за выступы кладки.</narrate>\n<roll skill=\"Атлетика\" dc=\"15\"/>"},
                         {"role": "user", "content": "[СИСТЕМА: результат] " + res_text + " Продолжи повествование с учётом результата."}]
                body = {"messages": msgs}
            else:
                msgs += [{"role": "assistant", "content": "Ты цепляешься за выступы кладки.",
                          "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "roll", "arguments": json.dumps(rollargs, ensure_ascii=False)}}]},
                         {"role": "tool", "tool_call_id": "c1", "name": "roll", "content": json.dumps({"result": res_text, "d20": 3, "total": 3, "dc": 15, "success": False}, ensure_ascii=False)}]
                body = {"messages": msgs, "tools": [ROLL_TOOL]}
            try:
                r, dt = chat({**body, "seed": 1000 + i})
                txt = r["choices"][0]["message"].get("content") or ""
                err = None
            except Exception as e:
                txt, dt, err = "", None, str(e)[:300]
            j = judge(txt) if txt else None
            m = dict(variant=variant, i=i, latency_s=dt, judge=j, fail_kw=bool(FAIL_WORDS.search(txt)),
                     succ_kw=bool(SUCC_WORDS.search(txt)), cyr=cyr_share(txt), err=err, out=txt[:400])
            RES["inject"].append(m); save(); print(" ", variant, i, j, m["cyr"], dt, txt[:80].replace("\n", " "), flush=True)

# ---------------- 3. protocol ----------------
ROLL_TOOL = {"type": "function", "function": {"name": "roll", "description": "Запросить проверку навыка (бросок d20 делает код).",
    "parameters": {"type": "object", "properties": {"skill": {"type": "string"}, "dc": {"type": "integer"}}, "required": ["skill", "dc"]}}}
TOOLS = [ROLL_TOOL,
    {"type": "function", "function": {"name": "speak", "description": "Передать слово персонажу: он сам произнесёт реплику.",
     "parameters": {"type": "object", "properties": {"who": {"type": "string"}, "intent": {"type": "string"}}, "required": ["who", "intent"]}}},
    {"type": "function", "function": {"name": "end", "description": "Завершить ход и передать управление игроку.", "parameters": {"type": "object", "properties": {}}}}]
PLAYER = "Я незаметно прохожу мимо стражника у двери, сажусь к стойке и спрашиваю трактирщицу Сильфу, где прячется Борек."
TAG_RULES = ("\n\nФормат ответа — ТОЛЬКО теги:\n<narrate>текст повествования</narrate>\n<speak who=\"Имя\" intent=\"что персонаж хочет сказать\"/> — персонаж говорит сам (не пиши его реплику)\n"
             "<roll skill=\"Навык\" dc=\"число\"/> — проверка навыка, если исход не очевиден (кубик бросает код, не ты)\n<end/> — конец хода.\n"
             "После тега действия остановись и жди результата. Персонажи в сцене: Сильфа (трактирщица), стражник.")
TOOL_RULES = ("\n\nПовествование пиши обычным текстом. Для проверок навыков вызывай инструмент roll (кубик бросает код), "
              "чтобы персонаж заговорил — инструмент speak, в конце хода — end. Персонажи в сцене: Сильфа (трактирщица), стражник.")
JSON_RULES = ("\n\nОтвечай JSON: narration (текст повествования) и actions — список действий: "
              "{type:'roll', skill, dc} для проверки (кубик бросает код), {type:'speak', who, intent} чтобы персонаж заговорил сам, {type:'end'}. Персонажи: Сильфа (трактирщица), стражник.")
JSON_SCHEMA = {"type": "object", "properties": {"narration": {"type": "string"}, "actions": {"type": "array", "maxItems": 3, "items": {
    "type": "object", "properties": {"type": {"type": "string", "enum": ["roll", "speak", "end"]}, "skill": {"type": "string"},
    "dc": {"type": "integer"}, "who": {"type": "string"}, "intent": {"type": "string"}}, "required": ["type"]}}},
    "required": ["narration", "actions"]}

TAG_RE = re.compile(r"<(roll|speak|end)\b([^<>]*)$", re.S)
def attrs(s): return dict(re.findall(r'(\w+)\s*=\s*"([^"]*)"', s))
def action_ok(a):
    t = a.get("type")
    if t == "roll": return bool(a.get("skill")) and str(a.get("dc", "")).strip().isdigit()
    if t == "speak": return bool(a.get("who")) and bool(a.get("intent"))
    return t == "end"
def sensible(narr, acts):
    # expect: narration in Russian + stealth roll and/or speak by Сильфа; no invented Сильфа lines inside narration is not checked
    good = [a for a in acts if (a["type"] == "roll" and re.search(r"скрыт|незамет|ловк", a.get("skill", ""), re.I))
            or (a["type"] == "speak" and "Сильф" in a.get("who", ""))]
    return bool(narr.strip()) and bool(good)

def test_proto():
    print("== 3. protocol", flush=True)
    for variant in ("tags", "native_tools", "json_schema"):
        for i in range(N):
            seed = 2000 + i; err = None; narr = ""; acts = []; parsed = False; raw = ""
            try:
                if variant == "tags":
                    r, dt = chat({"messages": [{"role": "system", "content": SCENE + TAG_RULES}, {"role": "user", "content": PLAYER}],
                                  "stop": ["/>"], "seed": seed})
                    raw = r["choices"][0]["message"].get("content") or ""
                    narr = " ".join(re.findall(r"<narrate>(.*?)</narrate>", raw, re.S))
                    m = TAG_RE.search(raw)
                    if m:
                        a = {"type": m.group(1), **attrs(m.group(2))}; acts = [a]
                    parsed = bool(narr) and bool(acts) and action_ok(acts[0]) and \
                        re.sub(r"<narrate>.*?</narrate>", "", raw, flags=re.S).strip().startswith("<")
                elif variant == "native_tools":
                    r, dt = chat({"messages": [{"role": "system", "content": SCENE + TOOL_RULES}, {"role": "user", "content": PLAYER}],
                                  "tools": TOOLS, "seed": seed})
                    msg = r["choices"][0]["message"]; narr = msg.get("content") or ""; raw = narr
                    tcs = msg.get("tool_calls") or []
                    raw += "\nTOOL_CALLS: " + json.dumps(tcs, ensure_ascii=False)
                    for tc in tcs:
                        acts.append({"type": tc["function"]["name"], **json.loads(tc["function"]["arguments"] or "{}")})
                    parsed = bool(acts) and all(action_ok(a) for a in acts)
                else:
                    r, dt = chat({"messages": [{"role": "system", "content": SCENE + JSON_RULES}, {"role": "user", "content": PLAYER}],
                                  "response_format": {"type": "json_schema", "json_schema": {"name": "turn", "schema": JSON_SCHEMA}}, "seed": seed})
                    raw = r["choices"][0]["message"].get("content") or ""
                    j = json.loads(raw); narr = j.get("narration", ""); acts = j.get("actions", [])
                    parsed = bool(acts) and all(action_ok(a) for a in acts)
            except Exception as e:
                err = str(e)[:300]; dt = None
            m = dict(variant=variant, i=i, latency_s=dt, parsed=parsed, sensible=parsed and sensible(narr, acts),
                     n_actions=len(acts), actions=acts, narr_cyr=cyr_share(narr), err=err,
                     completion_tokens=(r.get("usage", {}) if not err else {}).get("completion_tokens"), raw=raw[:600])
            RES["proto"].append(m); save(); print(" ", variant, i, parsed, m["sensible"], dt, acts, flush=True)

if __name__ == "__main__":
    which = sys.argv[1:] or ["cache", "inject", "proto"]
    t0 = time.time()
    if "cache" in which: test_cache()
    if "inject" in which: test_inject()
    if "proto" in which: test_proto()
    RES["total_s"] = round(time.time() - t0); save(); print("done", RES["total_s"], "s")
