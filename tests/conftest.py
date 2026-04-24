import json
from pathlib import Path

_CONFIG_PATH = Path(__file__).parent.parent / "config.json"
_created_by_tests = False


def pytest_configure(config):
    global _created_by_tests
    if not _CONFIG_PATH.exists():
        _CONFIG_PATH.write_text(json.dumps({}), encoding="utf-8")
        _created_by_tests = True


def pytest_unconfigure(config):
    if _created_by_tests and _CONFIG_PATH.exists():
        _CONFIG_PATH.unlink()
