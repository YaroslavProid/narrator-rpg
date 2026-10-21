# narrator-rpg

Private single-player Russian text RPG with an LLM narrator (llama-server, Gemma 4 26B-A4B) — see SPEC.md.
License: AGPL-3.0 (see LICENSE). Concepts of the «Система» module ported from the Talemate scene «дракония-малфой».

## Setup
    py -3.12 -m venv .venv
    .venv\Scripts\python.exe -m pip install -r requirements.txt

## Run (Phase 1 MVP)
    run-narrator.cmd            # http://0.0.0.0:8082 (same port as Talemate - don't run both)
    run-narrator.cmd 8090       # other port;  2nd arg = host, e.g. run-narrator.cmd 8090 127.0.0.1
Env: `LLM_URL` (default http://localhost:11435), `NARRATOR_PORT`, `NARRATOR_MAX_STEPS` (4),
`NARRATOR_STEP_TOKENS` (350), `NARRATOR_SPEAK_TOKENS` (120), `NARRATOR_CYR_MIN` (0.7), `NARRATOR_DATA` (data/).
Memory (Phase 2): `NARRATOR_MEMORY` (1), `NARRATOR_CTX_BUDGET` (30000), `NARRATOR_GEN_RESERVE` (3500),
`NARRATOR_RECENT_MAX` (18000 tokens of unarchived history), `NARRATOR_ARCHIVE_BLOCK` (3000), `NARRATOR_L1_MAX` (8),
`NARRATOR_RETRIEVE_K` (4), `NARRATOR_LENGTH_MULT` (1.0, default per-scene length multiplier), `NARRATOR_EMBED_DIR`.

Embedding model (CPU, ~120 MB, not in git):

    mkdir models\multilingual-e5-small
    curl -L -o models\multilingual-e5-small\model_quantized.onnx https://huggingface.co/Xenova/multilingual-e5-small/resolve/main/onnx/model_quantized.onnx
    curl -L -o models\multilingual-e5-small\tokenizer.json https://huggingface.co/Xenova/multilingual-e5-small/resolve/main/tokenizer.json

## Layout
    app/llm.py        llama-server /completion client (stream, cache_prompt, /slots wait, cache metrics)
    app/prompt.py     byte-exact Gemma 4 template rendering
    app/protocol.py   tag protocol parser (<narrate>, <speak/>, <roll/>, <change/>, <introduce/>, <leave/>, <end/>), Cyrillic check
    app/mechanics.py  stats, checks (d20 / «система»), clamped deltas, levels, skill & name aliases, «Система, …» commands
    app/prompts.py    Russian system prompt (rendered once per scene), player message, [РЕПЛИКА] tail
    app/engine.py     turn loop (dynamic budget, character agents, witnesses, introduce, GBNF retry), memory
                      orchestration (archive/compress/re-warm, retrieval, context budget), rewind/regenerate
    app/budget.py     dynamic per-turn budget: tier камерный/обычный/крупный from active NPCs, input type, novelty, length
    app/memory.py     e5-small ONNX embedder, Chroma store (data/chroma, witness metadata), memory block, facts, archiver prompts
    app/cards.py      Character Card V2/V3 PNG/JSON + text import
    app/store.py      JSON saves: data/scenes/<id>.json, data/characters/<name>.json
    app/server.py     FastAPI + WebSocket /ws/<scene>
    web/index.html    mobile-first SPA, no build
    tests/            pytest (parser, mechanics/clamping, Система commands, cards/store/API, fake-LLM turn loop)
    bench/e2e_mvp.py  scripted session vs real Gemma -> bench/e2e-mvp.md
    bench/e2e_long.py 46-input session (prompt >20k, archiving, recall, regenerate/rewind) -> bench/e2e-long.md

## Tests
    .venv\Scripts\python.exe -m pytest -q
    .venv\Scripts\python.exe bench\e2e_mvp.py http://127.0.0.1:8082   # server must be running
