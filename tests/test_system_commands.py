from app import mechanics as M
from tests.test_mechanics import mk


def test_parse():
    assert M.parse_system_command("Система, статус") == ("status", "статус")
    assert M.parse_system_command("система")[0] == "status"
    assert M.parse_system_command("Система: инвентарь")[0] == "inventory"
    assert M.parse_system_command("*Система, навыки*")[0] == "skills"
    assert M.parse_system_command("/help")[0] == "help"
    assert M.parse_system_command("Система, распредели Сила +1")[0] == "assign"
    assert M.parse_system_command("Системный сбой в голове") is None
    assert M.parse_system_command("Я иду в таверну") is None


def test_outputs():
    s = mk(); p = s.player
    st = M.run_system_command(s, "status", "")
    assert "СТАТУС" in st and "HP 20/20" in st and "Сильфа" in st
    assert "Скрытность" in M.run_system_command(s, "skills", "")
    assert "Нож" in M.run_system_command(s, "inventory", "")
    assert "статус" in M.run_system_command(s, "help", "")


def test_assign_points():
    s = mk(); p = s.player
    assert "Недостаточно" in M.run_system_command(s, "assign", "распредели сила +1")
    p.attr_points = 2
    out = M.run_system_command(s, "assign", "распредели сила +1")
    assert p.attrs["Сила"] == 11 and p.attr_points == 1, out
