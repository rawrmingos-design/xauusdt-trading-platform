"""MT5 integration configuration (PROJECT-MT5-001).

Credentials come exclusively from environment / secret files — never from
the repository. ``mode`` explicitly distinguishes paper/demo/live; there is
NO default that can silently connect to a live account.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from xauusdt.execution.errors import ModeGuardError


class Mt5Mode:
    """Explicit venue mode. NEVER default to live."""

    PAPER = "paper"
    DEMO = "demo"
    LIVE = "live"

    _VALID = {PAPER, DEMO, LIVE}


@dataclass(frozen=True)
class Mt5Settings:
    """MT5 connection settings, read from environment variables.

    Env vars (consistent with the repository's pydantic-settings approach
    but kept dependency-free here):
        MT5_LOGIN           account login (int)
        MT5_PASSWORD        account password (never committed)
        MT5_SERVER          broker server name (e.g. "Exness-MT5Trial")
        MT5_TERMINAL_PATH   path to terminal64.exe (Windows only; may be empty
                            on Linux where MT5 runs via Wine)
        MT5_SYMBOL          symbol to trade (e.g. "XAUUSD"); if empty, the
                            adapter will discover the gold symbol at connect
        MT5_MODE            one of: paper | demo | live  (REQUIRED)
        MT5_LOGIN_FILE      optional file containing the login/password
                            (e.g. /etc/xauusdt/mt5.secret) — preferred over
                            plain env for passwords.
    """

    login: int
    password: str
    server: str
    terminal_path: str = ""
    symbol: str = ""  # empty = auto-discover
    mode: str = Mt5Mode.DEMO
    login_file: str = ""

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Mt5Settings:
        e = env if env is not None else os.environ

        raw_mode = e.get("MT5_MODE", "").strip().lower()
        if raw_mode not in Mt5Mode._VALID:
            raise ModeGuardError(
                f"MT5_MODE must be one of {sorted(Mt5Mode._VALID)!r}, got {raw_mode!r}. "
                "Refusing to guess a mode — a live connection must be explicit."
            )

        login_file = e.get("MT5_LOGIN_FILE", "")
        login = e.get("MT5_LOGIN", "")
        password = e.get("MT5_PASSWORD", "")
        if login_file:
            creds = cls._read_login_file(login_file)
            login = creds.get("login", login)
            password = creds.get("password", password)

        if not login or not password:
            raise ModeGuardError(
                "MT5_LOGIN and MT5_PASSWORD (or MT5_LOGIN_FILE) are required; "
                "credentials must come from environment/secrets, never the repo."
            )
        if not e.get("MT5_SERVER"):
            raise ModeGuardError("MT5_SERVER is required (e.g. Exness-MT5Trial).")

        try:
            login_int = int(login)
        except (TypeError, ValueError) as exc:
            raise ModeGuardError(f"MT5_LOGIN must be an integer, got {login!r}.") from exc

        return cls(
            login=login_int,
            password=password,
            server=e["MT5_SERVER"].strip(),
            terminal_path=e.get("MT5_TERMINAL_PATH", "").strip(),
            symbol=e.get("MT5_SYMBOL", "").strip(),
            mode=raw_mode,
            login_file=login_file,
        )

    @staticmethod
    def _read_login_file(path: str) -> dict[str, str]:
        """Read ``login=...`` / ``password=...`` lines from a secret file."""
        out: dict[str, str] = {}
        try:
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, val = line.partition("=")
                    out[key.strip().lower()] = val.strip()
        except OSError as exc:
            raise ModeGuardError(f"Cannot read MT5_LOGIN_FILE {path!r}: {exc}") from exc
        return out

    @property
    def is_demo(self) -> bool:
        return self.mode == Mt5Mode.DEMO

    @property
    def is_live(self) -> bool:
        return self.mode == Mt5Mode.LIVE

    def ensure_demo_or_paper(self, operation: str = "write") -> None:
        """Raise unless the venue is demo (or paper). Live requires extra gate."""
        if self.mode == Mt5Mode.LIVE:
            raise ModeGuardError(
                f"{operation} on a LIVE account requires an explicit runtime guard; "
                "MT5_MODE=live is not auto-approved."
            )

    def __post_init__(self) -> None:
        if self.mode not in Mt5Mode._VALID:
            raise ModeGuardError(f"Invalid MT5_MODE {self.mode!r}.")
