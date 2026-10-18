"""Import place facts using the blog's public JSON protocol; do not copy reviews, photos, companions or ratings."""
import argparse
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from food_map import FoodStore

URL = 'https://cachetide.top/food-map/index.json'


def convert(document):
    if not isinstance(document, dict) or document.get('schemaVersion') != 1 or not isinstance(document.get('spots'), list):
        raise ValueError('Unexpected blog export format; import stopped')
    result = []
    for spot in document['spots']:
        if spot.get('coordinateSystem', document.get('coordinateSystem', 'GCJ-02')) != 'GCJ-02':
            raise ValueError('Blog coordinates are not GCJ-02; do not mix coordinate systems')
        article = spot.get('articleUrl') or 'https://cachetide.top/food-map'
        if urllib.parse.urlsplit(article).hostname != 'cachetide.top' or not article.startswith('https://'):
            article = 'https://cachetide.top/food-map'
        result.append({'name': spot.get('name', ''), 'city': spot.get('city', ''), 'address': spot.get('address', ''),
                       'location': f'{float(spot["lng"]):.6f},{float(spot["lat"]):.6f}',
                       'category': spot.get('category') or 'Food', 'source_url': article,
                       'source_name': 'CacheTide food map',
                       'note': 'Place record from CacheTide’s public food map; see the original article for reviews and details.'})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='Explicitly fetch and add verified public places to the local directory')
    args = parser.parse_args()
    if not args.apply:
        print('No --apply: no network requests or directory writes.')
        return 0
    request = urllib.request.Request(URL, headers={'User-Agent': 'GmnBot/1.0 attributed-food-map-import'})
    with urllib.request.urlopen(request, timeout=15) as response:
        raw = response.read(1024*1024+1)
    if len(raw) > 1024*1024:
        raise ValueError('Blog JSON too large; reading stopped')
    entries = convert(json.loads(raw))
    store = FoodStore(Path(__file__).resolve().parents[1])
    count = duplicate = invalid = 0
    for entry in entries:
        try:
            store.add(entry, 'attributed-blog-import', approved=True)
            count += 1
        except ValueError as error:
            if 'submitted already' in str(error):
                duplicate += 1
            else:
                invalid += 1
                print('[Blog import skipped]', type(error).__name__)
    print('[Blog import finished]', 'found', len(entries), 'added', count, 'existing', duplicate, 'invalid', invalid,
          '; copied place facts only, retaining each original source URL')
    return int(invalid > 0)


if __name__ == '__main__':
    raise SystemExit(main())
