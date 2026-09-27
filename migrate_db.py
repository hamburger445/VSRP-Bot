"""Run once to apply database migrations. Also runs automatically on bot startup."""
import asyncio

from utils.database import close_db, get_db


async def main():
    await get_db()
    print("Database migrations applied successfully.")
    await close_db()


if __name__ == "__main__":
    asyncio.run(main())
