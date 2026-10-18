"""Review or quarantine non-GIF stickers above 1 MiB; never starts the bot."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sticker_store import StickerStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='Back up and remove matching originals from the library')
    args = parser.parse_args()
    store = StickerStore(Path(__file__).resolve().parents[1])
    try:
        print(json.dumps(store.cleanup_large_non_gif(apply=args.apply), indent=2))
    finally:
        store.close()


if __name__ == '__main__':
    main()
