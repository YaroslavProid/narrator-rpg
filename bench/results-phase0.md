# Phase 0 results (2026-10-21, Gemma 4 26B-A4B IQ4_XS, llama-server :11435, -c 36864 -np 1 --jinja)

Script: `bench/phase0.py` (stdlib only). Raw data: `results-phase0.json`. Total run 558 s.

## 1. Prompt cache (/completion, cache_prompt, id_slot 0, ~17.4k-token prefix, n_predict 60)

| step | prompt tok | processed (prompt_n) | cached (cache_n) | prompt ms | wall s |
|---|---|---|---|---|---|
| t0 cold (full prompt) | 17431 | 11288 | 6144 | 42497 | 45.45 |
| t0b identical repeat | 17431 | 1 | 17431 | 53 | 3.0 |
| t1 append-only | 17513 | 86 | 17428 | 646 | 3.61 |
| t2 append-only | 17596 | 87 | 17510 | 650 | 3.52 |
| t3 append-only | 17671 | 79 | 17593 | 562 | 3.5 |
| t4 +[РЕПЛИКА] tail | 17805 | 138 | 17668 | 888 | 3.83 |
| t5 РЕПЛИКА removed, line appended | 17810 | 96 | 17715 | 659 | 3.62 |
| t6 middle lore block changed | 17815 | 17816 | 0 | 52542 | 55.5 |
| t7 same as t6 (re-hit) | 17815 | 1 | 17815 | 53 | 3.0 |

PP speed ≈ 340 tok/s cold (17.8k tok ≈ 52 s). Append-only and the РЕПЛИКА tail re-read only the new tail (80-140 tok, <1 s). Removing the РЕПЛИКА block costs only the re-read from its start (cache rolls back to the common prefix). A change in the middle block forced a **full** re-read (cache_n=0, not just from the changed point) — likely SWA/no partial-reuse on Gemma 4; treat any non-tail edit as a full 50 s re-read. t0 had 6144 tokens cached from an earlier run (chunk-aligned reuse).

## 2. Roll result injection (d20=3 vs DC15 failure, 10 trials each, judged by the model with json_schema + keyword check)

| variant | judge: respects failure | failure keywords | success keywords | Cyrillic share | mean latency s |
|---|---|---|---|---|---|
| user_label | 10/10 | 10/10 | 0/10 | 1.000 | 3.10 |
| tool_role | 10/10 | 9/10 | 0/10 | 1.000 | 3.08 |

Both work on Gemma 4 (unlike the earlier 14B). user_label outputs echoed the `<narrate>` wrapper (follows history format), tool_role outputs are more varied (user_label was nearly identical across seeds).

## 3. Protocol (10 trials each, Russian scene: sneak past guard + ask Сильфа)

| variant | parse OK | sensible action | all action args in Russian | narration Cyrillic | mean latency s |
|---|---|---|---|---|---|
| tags | 10/10 | 10/10 | 4/10 | 1.000 | 3.44 |
| native_tools | 2/10 | 2/10 | 0/2 | 0.905 | 3.90 |
| json_schema | 10/10 | 10/10 | 0/10 | 1.000 | 4.45 |

- tags: stop on `/>` works; 6× roll (skill always English `stealth`), 4× speak Сильфа with Russian intent. One action per stop, as designed.
- native_tools: only 2/10 returned tool_calls; 8/10 leaked raw Gemma tool syntax into content (`roll{dc:12,skill:<|"|>stealth<|"|>}`, backtick calls) — server-side parser does not reliably catch Gemma 4 calls. Unusable as-is.
- json_schema: 10/10 valid (grammar-forced), always roll+speak, but names transliterated to Latin (`Silfa`) 8/10 and some English intents.

## 4. Russian adherence

Narration Cyrillic share ≈1.0 everywhere. Leakage is only in structured fields: skill names (English in tags/json/native) and character names/intents in json_schema. Mitigate with enum of Russian skill names / present-character names (GBNF/json_schema enum) and alias mapping in code.

## Recommendation

- **Protocol: text tags + stop `/>`** as primary (10/10 parse, fastest, streams). Validate attrs; map skill to an allowed Russian list (accept English aliases). Retry path: json_schema/GBNF with `enum` for skills and present names. Do not enable native tool-calling.
- **Result injection: labeled user message `[СИСТЕМА: результат] …`** (10/10, keeps raw /completion append-only and matches tags). tool role also works, so it's no longer a risk.
- **Cache:** keep everything strictly append-only; РЕПЛИКА tail is cheap (~1 s); any edit before the tail = full ~50 s re-read at 17k → summaries/archiving must only happen in background with re-warm.
