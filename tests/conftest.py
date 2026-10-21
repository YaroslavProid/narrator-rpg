import sys, pathlib, pytest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def tmp_data(tmp_path, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "DATA", tmp_path)
    yield tmp_path
