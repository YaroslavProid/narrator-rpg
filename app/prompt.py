"""Gemma 4 chat template rendering (byte-exact, deterministic)."""
SYS_OPEN = "<|turn>system\n"
END = "<turn|>\n"
MODEL_OPEN = "<|turn>model\n<|channel>thought\n<channel|>"


def render(system: str, turns: list, open_model: str | None = None) -> str:
    parts = [SYS_OPEN, system, END]
    for t in turns:
        if t["role"] == "user":
            parts += ["<|turn>user\n", t["content"], END]
        else:
            parts += [MODEL_OPEN, t["content"], END]
    if open_model is not None:
        parts += [MODEL_OPEN, open_model]
    return "".join(parts)
