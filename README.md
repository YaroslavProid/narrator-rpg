# narrator-rpg

Private single-player Russian text RPG with an LLM narrator (llama-server, Gemma 4 26B-A4B) — see SPEC.md.
License: AGPL-3.0 (see LICENSE).

## Setup
    py -3.12 -m venv .venv
    .venv\Scripts\python.exe bench\phase0.py      # Phase 0 measurements (needs llama-server on :11435)
