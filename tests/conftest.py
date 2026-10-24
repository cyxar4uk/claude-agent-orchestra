from __future__ import annotations

import pytest

from orchestra.backends import MockBackend
from orchestra.engine import Engine
from orchestra.settings import Settings
from orchestra.spec import load_config
from orchestra.store import Store


@pytest.fixture()
def settings(tmp_path) -> Settings:
    # Explicit kwargs win over any environment / .env, so tests never touch real config.
    return Settings(
        _env_file=None,
        backend="mock",
        workspace=str(tmp_path),
        data_dir=str(tmp_path / "data"),
        api_token="",
    )


@pytest.fixture()
def config():
    return load_config(None)


@pytest.fixture()
def backend() -> MockBackend:
    return MockBackend()


@pytest.fixture()
def engine(config, settings, backend) -> Engine:
    return Engine(config, settings, Store(":memory:"), backend)
