import asyncio
import logging
from app.bot_runner import main

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
