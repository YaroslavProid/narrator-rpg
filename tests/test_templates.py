"""The chat template is the model's, picked by NARRATOR_TEMPLATE; Gemma 4 stays the default byte for byte."""
import importlib
from app import config, prompt


def render_with(name, *a, **k):
    old = config.CHAT_TEMPLATE
    config.CHAT_TEMPLATE = name
    try:
        mod = importlib.reload(prompt)
        return mod.render(*a, **k), mod.stops("/>")
    finally:
        config.CHAT_TEMPLATE = old
        importlib.reload(prompt)


TURNS = [{"role": "user", "content": "привет"}, {"role": "model", "content": "<narrate>Тишина.</narrate>"}]


def test_gemma4_default_is_unchanged():
    out, st = render_with("gemma4", "СИС", TURNS, open_model="")
    assert out == ("<|turn>system\nСИС<turn|>\n<|turn>user\nпривет<turn|>\n<|turn>model\n<|channel>thought\n<channel|>"
                   "<narrate>Тишина.</narrate><turn|>\n<|turn>model\n<|channel>thought\n<channel|>")
    assert st == ["/>", "<turn|>", "<|turn>"]


def test_chatml_qwen3_llama3_and_gemma3():
    out, st = render_with("chatml", "СИС", TURNS, open_model="")
    assert out.startswith("<|im_start|>system\nСИС<|im_end|>\n<|im_start|>user\nпривет") and out.endswith("<|im_start|>assistant\n")
    assert "<|im_end|>" in st
    assert render_with("qwen3", "С", TURNS, open_model="")[0].endswith("<think>\n\n</think>\n\n")
    assert render_with("llama3", "С", TURNS)[0].startswith("<|begin_of_text|><|start_header_id|>system")
    g3, st3 = render_with("gemma3", "СИС", TURNS)
    assert g3.startswith("<start_of_turn>user\nСИС\n\nпривет<end_of_turn>") and "<end_of_turn>" in st3
    assert render_with("unknown", "С", TURNS)[1][-2:] == ["<turn|>", "<|turn>"]      # falls back to gemma4
