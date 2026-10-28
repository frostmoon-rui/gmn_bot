"""Decode public search redirects before applying the public-page URL checks."""
import base64
import urllib.parse


def decode_result_link(href, *, upgrade_http=False):
    try:
        if href.startswith('//'):
            href = 'https:' + href
        parsed=urllib.parse.urlsplit(href)
        host=(parsed.hostname or '').lower()
        query=urllib.parse.parse_qs(parsed.query)
        if parsed.path=='/url' and (not host or host=='google.com' or host.endswith('.google.com')):
            href=(query.get('q') or query.get('url') or [''])[0]
        elif parsed.path in {'/l/','/l'} and (not host or host=='duckduckgo.com' or host.endswith('.duckduckgo.com')):
            href=(query.get('uddg') or [''])[0]
        elif host in {'bing.com','www.bing.com'} and parsed.path.startswith('/ck/'):
            value=(query.get('u') or [''])[0]
            if value.startswith('a1'):
                value=value[2:]
                href=base64.urlsafe_b64decode(value+'='*(-len(value)%4)).decode('utf-8')
        target=urllib.parse.urlsplit(href)
        if upgrade_http and target.scheme == 'http' and target.hostname and not target.username and target.port in (None,80):
            target = target._replace(scheme='https', netloc=target.netloc.removesuffix(':80'))
            href = urllib.parse.urlunsplit(target)
        if target.scheme=='https' and target.hostname and not target.username:
            return href
    except (ValueError,UnicodeError):
        pass
    return ''
