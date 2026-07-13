"""Bitget exchange custom exceptions."""


class BitgetError(Exception):
    """Base exception for Bitget client errors."""


class BitgetAuthError(BitgetError):
    """Authentication/authorization failure."""


class BitgetRateLimitError(BitgetError):
    """Rate limit exceeded."""


class BitgetServerError(BitgetError):
    """Server-side error (5xx)."""


class BitgetRequestError(BitgetError):
    """Client-side request error (4xx, excluding auth/rate-limit)."""
