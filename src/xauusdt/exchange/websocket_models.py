"""Pydantic models for Bitget Futures WebSocket messages."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class WsPingMessage(BaseModel):
    """Client sends a ping frame to keep connection alive."""

    type: Literal["ping"] = "ping"
    arg: dict[str, Any]


class WsPongMessage(BaseModel):
    """Exchange responds with pong frame."""

    type: Literal["pong"] = "pong"
    arg: dict[str, Any]


class WsSubscribeMessage(BaseModel):
    """Client subscribes to a channel."""

    type: Literal["subscribe"] = "subscribe"
    arg: dict[str, Any] = Field(default_factory=dict)


class WsUnsubscribeMessage(BaseModel):
    """Client unsubscribes from a channel."""

    type: Literal["unsubscribe"] = "unsubscribe"
    arg: dict[str, Any] = Field(default_factory=dict)


class WsCandleStickSnapshot(BaseModel):
    """WebSocket candlestick snapshot — initial full state for a candle interval."""

    arg: dict[str, Any]
    action: str
    data: list[dict[str, Any]]


class WsCandleStickUpdate(BaseModel):
    """WebSocket candlestick update — real-time candle change."""

    arg: dict[str, Any]
    action: str
    data: list[dict[str, Any]]


class WsAuthMessage(BaseModel):
    """Authentication message for private channels (not used for public channels)."""

    type: Literal["login"] = "login"
    arg: dict[str, Any] = Field(default_factory=dict)


class WsResponse(BaseModel):
    """Generic WebSocket response that may contain success or error."""

    action: str
    arg: dict[str, Any]
    code: int | None = None
    msg: str | None = None
    data: list[dict[str, Any]] | None = None


class WsError(BaseModel):
    """WebSocket error response from the exchange."""

    action: str
    arg: dict[str, Any]
    code: int
    msg: str
