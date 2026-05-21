import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.migrate_json_to_postgres import *  # noqa: F401,F403
from scripts.migrate_json_to_postgres import main


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
