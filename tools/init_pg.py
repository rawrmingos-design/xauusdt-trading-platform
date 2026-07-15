"""Initialize PostgreSQL tables."""

import asyncio
import sys

sys.path.insert(0, "/home/devistopup13/xauusdt-platform/src")

from xauusdt.storage.database import create_tables, init_db


async def main():
    db_url = "postgresql+asyncpg://xauusdt:xauusdt@localhost:5432/xauusdt"
    await init_db(db_url)
    await create_tables()
    print("Tables created in PostgreSQL")


if __name__ == "__main__":
    asyncio.run(main())
