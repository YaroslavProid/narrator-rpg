"""Chat template rendering (byte-exact, deterministic) for the model llama-server runs.

The engine talks to llama-server's raw /completion and keeps the prompt prefix byte-stable for the cache, so it renders
the chat template itself. Pick the model family with NARRATOR_TEMPLATE (gemma4 by default): gemma4, gemma3, chatml
(Qwen 2.5, many fine-tunes), qwen3 (chatml with thinking switched off), llama3. Every LLM call stops on the template's
own turn markers: use stops(...) instead of writing them by hand."""
from . import config

TEMPLATES = {
    # Gemma 4: a system turn; the model turn opens with an empty thought channel (reasoning off)
    "gemma4": {"sys": ("<|turn>system\n", "<turn|>\n"), "user": ("<|turn>user\n", "<turn|>\n"),
               "model": ("<|turn>model\n<|channel>thought\n<channel|>", "<turn|>\n"), "stops": ["<turn|>", "<|turn>"]},
    # Gemma 2/3: no system role — the system text opens the first user turn
    "gemma3": {"sys": None, "user": ("<start_of_turn>user\n", "<end_of_turn>\n"),
               "model": ("<start_of_turn>model\n", "<end_of_turn>\n"), "stops": ["<end_of_turn>", "<start_of_turn>"]},
    "chatml": {"sys": ("<|im_start|>system\n", "<|im_end|>\n"), "user": ("<|im_start|>user\n", "<|im_end|>\n"),
               "model": ("<|im_start|>assistant\n", "<|im_end|>\n"), "stops": ["<|im_end|>", "<|im_start|>"]},
    "qwen3": {"sys": ("<|im_start|>system\n", "<|im_end|>\n"), "user": ("<|im_start|>user\n", "<|im_end|>\n"),
              "model": ("<|im_start|>assistant\n<think>\n\n</think>\n\n", "<|im_end|>\n"), "stops": ["<|im_end|>", "<|im_start|>"]},
    "llama3": {"bos": "<|begin_of_text|>",
               "sys": ("<|start_header_id|>system<|end_header_id|>\n\n", "<|eot_id|>"),
               "user": ("<|start_header_id|>user<|end_header_id|>\n\n", "<|eot_id|>"),
               "model": ("<|start_header_id|>assistant<|end_header_id|>\n\n", "<|eot_id|>"),
               "stops": ["<|eot_id|>", "<|start_header_id|>"]},
}
NAME = config.CHAT_TEMPLATE if config.CHAT_TEMPLATE in TEMPLATES else "gemma4"
T = TEMPLATES[NAME]
STOPS = list(T["stops"])


def stops(*extra: str) -> list[str]:
    """Stop strings for a call: the call's own (e.g. "/>") plus the template's turn markers."""
    return list(extra) + STOPS


def render(system: str, turns: list, open_model: str | None = None) -> str:
    parts = [T.get("bos", "")]
    first_user_prefix = ""
    if T["sys"]:
        parts += [T["sys"][0], system, T["sys"][1]]
    else:
        first_user_prefix = system + "\n\n"
    for t in turns:
        if t["role"] == "user":
            parts += [T["user"][0], first_user_prefix, t["content"], T["user"][1]]
            first_user_prefix = ""
        else:
            parts += [T["model"][0], t["content"], T["model"][1]]
    if first_user_prefix:                      # no user turn yet: the system text still has to reach the model
        parts += [T["user"][0], first_user_prefix.rstrip(), T["user"][1]]
    if open_model is not None:
        parts += [T["model"][0], open_model]
    return "".join(parts)
