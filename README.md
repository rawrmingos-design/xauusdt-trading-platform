# XAUUSDT Trading Platform

Production-grade foundation for XAUUSDT perpetual futures trading.

## Quick Start

```bash
# install
python -m pip install --upgrade uv
uv sync

# run smoke check
PYTHONPATH=src python -m xauusdt.cli

# tests
uv run pytest

# lint
uv run ruff check src tests
uv run ruff format src tests --check

# type check
uv run mypy src
```

## Docker

```bash
docker compose up --build
```

## Project Structure

```text
src/xauusdt/
  exchange/      # exchange adapters
  data/          # market data ingestion and normalization
  features/      # feature engineering
  strategy/      # strategy definitions
  risk/          # risk controls
  execution/     # order management
  storage/       # persistence layer
  monitoring/    # metrics and alerts
```

## Environment

See `.env.example`.

## Constraints

- UTC-only timestamps
- No secrets in repo
- No LLM in runtime trading path
- Modular monolith, not microservices
