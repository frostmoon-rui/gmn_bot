"""Location normalization outside the QQ main loop; geocoding only, without sending messages."""
import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace
from bot_extensions import Extensions
from bot_runtime import JsonStore

store = JsonStore(Path(__file__).resolve().parent)


def load(path, default):
    return store.load(path, default)


async def save(path, data):
    await asyncio.to_thread(store.save, path, data)


async def main():
    bot = SimpleNamespace(load_json_data=load, save_json_data_async=save,
        regions_data=load('regions.json', {}), REGIONS_FILE='regions.json')
    await Extensions(bot).migrate_locations()


if __name__ == '__main__':
    asyncio.run(main())
