"""Collect a bounded, deduplicated public Bilibili comment sample without login."""
import html
import re
import time
from collections import deque
from urllib.parse import urlencode


def comment_text(value):
    if not isinstance(value, str):
        return ''
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', value))).strip()[:240]


def comment_nodes(data):
    top = data.get('top') or {}
    if isinstance(top, dict):
        if isinstance(top.get('content'), dict):
            yield top
        else:
            for value in list(top.values())[:10]:
                if isinstance(value, dict):
                    yield value
                elif isinstance(value, list):
                    yield from value[:10]
    elif isinstance(top, list):
        yield from top[:10]
    for name in ('top_replies', 'hots', 'replies'):
        values = data.get(name)
        if isinstance(values, list):
            yield from values[:50]


def fetch_comments(api_json, aid, max_comments=60, max_requests=4, time_budget=15):
    if not str(aid).isdigit():
        raise ValueError('Invalid public video identifier')
    max_comments = max(1, min(120, int(max_comments)))
    max_requests = max(1, min(6, int(max_requests)))
    deadline = time.monotonic() + max(1, min(30, time_budget))
    queue = deque([('main', 2, 1), ('main', 0, 1)])
    comments, texts, seen_pages, queued_threads = [], set(), {}, set()
    stats = {'reported_total': None, 'collected': 0, 'requests': 0, 'thread_requests': 0,
             'nested_collected': 0, 'stop_reason': 'page-end', 'request_failed': False}
    while queue and stats['requests'] < max_requests and len(comments) < max_comments:
        if time.monotonic() >= deadline:
            stats['stop_reason'] = 'time-budget'
            break
        kind, section, page_number = queue.popleft()
        params = {'type': 1, 'oid': str(aid), 'pn': page_number, 'ps': 20}
        if kind == 'thread':
            params['root'] = section
            endpoint = 'https://api.bilibili.com/x/v2/reply/reply?'
            stats['thread_requests'] += 1
        else:
            params['sort'] = section
            endpoint = 'https://api.bilibili.com/x/v2/reply?'
        stats['requests'] += 1
        try:
            data = api_json(endpoint + urlencode(params))
            if not isinstance(data, dict):
                raise ValueError('Unexpected public comment response')
        except Exception:
            stats.update(request_failed=True, stop_reason='request-failed')
            break
        page = data.get('page') if isinstance(data.get('page'), dict) else {}
        total = page.get('count')
        if kind == 'main' and isinstance(total, int) and not isinstance(total, bool) and total >= 0:
            stats['reported_total'] = max(stats['reported_total'] or 0, total)
        new_roots = 0
        roots = data.get('replies') if isinstance(data.get('replies'), list) else []
        seen = seen_pages.setdefault((kind, section), set())
        threads = []
        for root in roots[:50]:
            if not isinstance(root, dict):
                continue
            content = root.get('content') or {}
            text = comment_text(content.get('message')) if isinstance(content, dict) else ''
            identifier = str(root.get('rpid') or root.get('rpid_str') or '')
            key = identifier or text
            if key and key not in seen:
                seen.add(key)
                new_roots += 1
            count = root.get('rcount')
            inline = root.get('replies') if isinstance(root.get('replies'), list) else []
            if (kind == 'main' and identifier.isdigit() and isinstance(count, int)
                    and count > len(inline) and identifier not in queued_threads):
                threads.append((count, identifier))
        nodes = roots if kind == 'thread' else comment_nodes(data)
        pending = deque((node, int(kind == 'thread')) for node in nodes)
        visited = 0
        while pending and len(comments) < max_comments and visited < 600:
            node, depth = pending.popleft()
            visited += 1
            if not isinstance(node, dict):
                continue
            content = node.get('content')
            text = comment_text(content.get('message')) if isinstance(content, dict) else ''
            normalized = text.casefold()
            if text and normalized not in texts:
                comments.append(text)
                texts.add(normalized)
                stats['nested_collected'] += int(depth > 0)
            children = node.get('replies')
            if depth == 0 and isinstance(children, list):
                pending.extend((child, 1) for child in children[:12])
        for _, identifier in sorted(threads, reverse=True)[:2]:
            queued_threads.add(identifier)
            queue.append(('thread', identifier, 1))
        size = page.get('size')
        size = size if isinstance(size, int) and not isinstance(size, bool) and size > 0 else 20
        more = total > page_number * size if isinstance(total, int) else len(roots) >= size
        if more and new_roots:
            queue.append((kind, section, page_number + 1))
        elif more and roots:
            stats['stop_reason'] = 'repeated-public-sample'
    if len(comments) >= max_comments:
        stats['stop_reason'] = 'sample-limit'
    elif queue and stats['requests'] >= max_requests:
        stats['stop_reason'] = 'request-limit'
    elif stats['stop_reason'] == 'page-end' and (stats['reported_total'] or 0) > len(comments):
        stats['stop_reason'] = 'public-sample-limited'
    stats['collected'] = len(comments)
    return comments, stats
