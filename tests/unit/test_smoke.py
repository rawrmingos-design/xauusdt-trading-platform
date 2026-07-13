"""Smoke tests for xauusdt package imports."""


def test_package_importable() -> None:
    import xauusdt  # noqa: F401


def test_config_importable() -> None:
    from xauusdt.config import Settings  # noqa: F401


def test_logging_importable() -> None:
    from xauusdt.logging import configure_logging  # noqa: F401
