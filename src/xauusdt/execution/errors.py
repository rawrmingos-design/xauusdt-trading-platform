"""Execution layer exceptions (PROJECT-MT5-001).

Follow the repository convention (see ``exchange/exceptions.py``): a small
hierarchy rooted at a base exception, with specific subclasses for the
failure modes an adapter must handle. Never swallowed silently — callers
catch these explicitly.
"""

from __future__ import annotations


class ExecutionError(Exception):
    """Base exception for all execution-layer failures."""


class VenueUnavailableError(ExecutionError):
    """Venue/terminal is not reachable (disconnected, not initialized, crash)."""


class InitializeError(VenueUnavailableError):
    """MT5 initialize() failed or timed out."""


class DisconnectedError(VenueUnavailableError):
    """Terminal was connected but is now disconnected."""


class SymbolUnavailableError(ExecutionError):
    """Requested symbol does not exist on this venue/account."""


class MarketUnavailableError(ExecutionError):
    """Market for the symbol is closed / not tradable right now."""


class OrderValidationError(ExecutionError):
    """Order intent failed domain validation (bad volume, side, price...)."""


class OrderCheckRejectedError(ExecutionError):
    """Venue order_check() rejected the request (margin, stop distance...)."""


class OrderSendRejectedError(ExecutionError):
    """Venue rejected the order submission (retcode != done/placed)."""


class OrderTimeoutError(ExecutionError):
    """Order submission timed out — outcome unknown, must reconcile."""


class MalformedResponseError(ExecutionError):
    """Venue returned an unexpected/malformed payload."""


class ReconciliationError(ExecutionError):
    """Local state and venue truth could not be reconciled."""


class ModeGuardError(ExecutionError):
    """Operation blocked by the explicit paper/demo/live mode guard."""
