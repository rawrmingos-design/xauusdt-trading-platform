"""Database migration: Rename legacy symbol to canonical symbol."""

import argparse
import asyncio
import sys

sys.path.insert(0, "/home/devistopup13/xauusdt-platform/src")

from sqlalchemy import text

from xauusdt.storage.database import get_session, init_db


async def migrate_symbol(db_url: str, old_sym: str, new_sym: str, dry_run: bool = False):
    """Rename a symbol in the database."""
    await init_db(db_url)

    async for session in get_session():
        # Check counts
        old_count = await session.execute(
            text("SELECT COUNT(*) FROM candles WHERE symbol = :sym"), {"sym": old_sym}
        )
        old_val = old_count.scalar()

        new_count = await session.execute(
            text("SELECT COUNT(*) FROM candles WHERE symbol = :sym"), {"sym": new_sym}
        )
        new_val = new_count.scalar()

        print(f"Current rows with {old_sym}: {old_val}")
        print(f"Current rows with {new_sym}: {new_val}")

        if old_val == 0:
            print("No legacy rows found. Migration not needed.")
            await session.close()
            return

        if dry_run:
            print(f"[Dry Run] Would update {old_val} rows from '{old_sym}' to '{new_sym}'")
        else:
            print(f"Updating {old_val} rows...")
            result = await session.execute(
                text("UPDATE candles SET symbol = :new_sym WHERE symbol = :old_sym"),
                {"old_sym": old_sym, "new_sym": new_sym},
            )
            await session.commit()
            print(f"Updated {result.rowcount} rows successfully.")

        await session.close()
        break


def main():
    parser = argparse.ArgumentParser(description="Migrate legacy symbol names in DB")
    parser.add_argument(
        "--db-url", default="postgresql+asyncpg://xauusdt:xauusdt@localhost:5432/xauusdt"
    )
    parser.add_argument("--old", default="XAUUSDT_UMCBL")
    parser.add_argument("--new", default="XAU-USDT-SWAP")
    parser.add_argument(
        "--execute", action="store_true", help="Actually run the update (default is dry-run)"
    )
    args = parser.parse_args()

    asyncio.run(migrate_symbol(args.db_url, args.old, args.new, dry_run=not args.execute))


if __name__ == "__main__":
    main()
