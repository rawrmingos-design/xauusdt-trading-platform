"""Tests for MT5 config / mode guard (PROJECT-MT5-001).

Credentials must never be hardcoded; mode must be explicit; live must never
be a silent default.
"""

from __future__ import annotations

import pytest

from xauusdt.execution.errors import ModeGuardError
from xauusdt.execution.mt5.config import Mt5Mode, Mt5Settings


def _env(**overrides):
    base = {
        "MT5_LOGIN": "12345",
        "MT5_PASSWORD": "secret",
        "MT5_SERVER": "Exness-MT5Trial",
        "MT5_MODE": "demo",
    }
    base.update(overrides)
    return base


def test_from_env_demo_ok():
    s = Mt5Settings.from_env(_env())
    assert s.login == 12345
    assert s.mode == Mt5Mode.DEMO
    assert s.is_demo
    assert not s.is_live


def test_from_env_requires_mode():
    env = _env()
    del env["MT5_MODE"]
    with pytest.raises(ModeGuardError):
        Mt5Settings.from_env(env)


def test_from_env_rejects_unknown_mode():
    with pytest.raises(ModeGuardError):
        Mt5Settings.from_env(_env(MT5_MODE="production"))


def test_no_default_to_live():
    """Constructing with no mode must fail via from_env, never pick live."""
    # Constructor default is demo (safe), not live.
    s = Mt5Settings(login=1, password="x", server="s")
    assert s.mode == Mt5Mode.DEMO
    assert not s.is_live
    # But from_env without MT5_MODE refuses to guess.
    env = _env()
    del env["MT5_MODE"]
    with pytest.raises(ModeGuardError):
        Mt5Settings.from_env(env)


def test_requires_credentials():
    env = _env()
    del env["MT5_PASSWORD"]
    with pytest.raises(ModeGuardError):
        Mt5Settings.from_env(env)
    env = _env()
    del env["MT5_LOGIN"]
    with pytest.raises(ModeGuardError):
        Mt5Settings.from_env(env)


def test_requires_server():
    env = _env()
    del env["MT5_SERVER"]
    with pytest.raises(ModeGuardError):
        Mt5Settings.from_env(env)


def test_login_must_be_int():
    with pytest.raises(ModeGuardError):
        Mt5Settings.from_env(_env(MT5_LOGIN="abc"))


def test_login_file_reads_credentials(tmp_path):
    secret = tmp_path / "mt5.secret"
    secret.write_text("# secret\nlogin=999\npassword=filepass\n")
    s = Mt5Settings.from_env(_env(MT5_LOGIN_FILE=str(secret), MT5_LOGIN="", MT5_PASSWORD=""))
    assert s.login == 999
    assert s.password == "filepass"


def test_ensure_demo_or_paper_allows_demo():
    s = Mt5Settings.from_env(_env(MT5_MODE="demo"))
    s.ensure_demo_or_paper("write")  # no raise


def test_ensure_demo_or_paper_blocks_live():
    s = Mt5Settings.from_env(_env(MT5_MODE="live"))
    with pytest.raises(ModeGuardError):
        s.ensure_demo_or_paper("write")
