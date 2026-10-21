from app.protocol import parse_step, parse_tag, split_speech, visible_text, clean_speech, cyr_share, ParseError
import pytest


def test_narrate_then_speak():
    o = parse_step('<narrate>Дождь хлещет.</narrate>\n<speak who="Сильфа" intent="насмешливо отказать"', "/>")
    assert o.narration == "Дождь хлещет."
    assert o.tag.name == "speak" and o.tag.attrs == {"who": "Сильфа", "intent": "насмешливо отказать"}
    assert o.raw.endswith("/>")


def test_roll_and_end():
    assert parse_step('<roll skill="stealth" dc="12"', "/>").tag.attrs["dc"] == "12"
    o = parse_step("<narrate>Тишина.</narrate>\n<end", "/>")
    assert o.tag.name == "end" and o.narration == "Тишина."


def test_natural_stop_without_tag():
    o = parse_step("<narrate>Просто текст.</narrate>", "")
    assert o.tag is None and o.error is None and o.narration == "Просто текст."


def test_bad_tag_errors():
    o = parse_step('<narrate>а</narrate><fly to="луна"', "/>")
    assert o.error and "неизвестный" in o.error
    with pytest.raises(ParseError):
        parse_tag('<speak intent="x"/>')


def test_unquoted_and_single_quotes():
    t = parse_tag("<roll skill='Восприятие' dc=15/>")
    assert t.attrs == {"skill": "Восприятие", "dc": "15"}


def test_visible_text_holds_partial_tag():
    assert visible_text("<narrate>Дождь") == "Дождь"
    assert visible_text("<narrate>Дождь.</narrate>\n<spe") == "Дождь.\n"
    assert visible_text('<narrate>А</narrate><speak who="x"/><narrate>Б') == "АБ"


def test_split_speech():
    p = split_speech("Он входит.\nСильфа: Привет.\nТишина.", ["Сильфа"])
    assert [x["kind"] for x in p] == ["narration", "speech", "narration"] and p[1]["who"] == "Сильфа"


def test_clean_speech_and_cyr():
    assert clean_speech("Сильфа: «Долги сами себя не отдают.»", "Сильфа") == "Долги сами себя не отдают."
    assert cyr_share("Hello world") == 0
    assert cyr_share("Привет, Sylpha", ["Sylpha"]) == 1
