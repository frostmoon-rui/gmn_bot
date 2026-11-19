"""Read-only album metadata diagnostics; no photos, video bytes or chat messages."""
import asyncio
from collections import Counter
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from local_settings import setting
from album_backup import media_variant
from tools.backup_albums import call, pages
import websockets


async def inspect():
    group = str(setting('ALBUM_BACKUP_GROUP_ID', ''))
    if not group.isdecimal():
        raise ValueError('Album group is not configured')
    counts, shapes, hosts = Counter(), Counter(), Counter()
    async with websockets.connect('ws://127.0.0.1:3001', open_timeout=5, close_timeout=2) as ws:
        async for albums in pages(ws, 'get_qun_album_list', {'group_id': group, 'attach_info': ''},
                                  'album_list', 'has_more', 'attach_info'):
            for album in albums:
                counts['albums'] += 1
                async for rows in pages(ws, 'get_group_album_media_list',
                        {'group_id': group, 'album_id': album['album_id'], 'attach_info': ''},
                        'media_list', 'next_has_more', 'next_attach_info'):
                    for row in rows:
                        kind, identity, variant = media_variant(row)
                        counts[kind] += 1
                        shapes[','.join(sorted(row.keys()))] += 1
                        for field in ('video', 'video_info'):
                            if isinstance(row.get(field), dict):
                                shapes[field + ':' + ','.join(sorted(row[field].keys()))] += 1
                        if variant and isinstance(variant.get('url'), str):
                            hosts[urlsplit(variant['url']).hostname or 'invalid'] += 1
                        else:
                            counts['missing_download_url'] += 1
                        if sum(counts[k] for k in ('image', 'video', 'unknown')) >= 20000:
                            raise ValueError('Metadata inspection limit reached')
    report = {'counts': dict(counts), 'schemas': dict(shapes), 'cdn_hosts': dict(hosts),
              'media_bytes_downloaded': 0, 'qq_messages_sent': 0}
    print(json.dumps(report, indent=2))
    return report


if __name__ == '__main__':
    asyncio.run(asyncio.wait_for(inspect(), timeout=120))
