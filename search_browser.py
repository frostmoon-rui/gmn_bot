"""Bounded, isolated rendering of fixed search engines, without user sessions."""
import time
import re
from urllib.parse import urlsplit


def allowed_request(url, *, navigation=False, method='GET', resource='document'):
    try:
        parsed=urlsplit(url)
        if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port not in (None,443):
            return False
        host=parsed.hostname or ''
        if method != 'GET' or resource in {'image','media','font'}:
            return False
        if navigation:
            return host in {'www.google.com','www.bing.com'} and (
                parsed.path == '/search' or parsed.path.startswith(('/sorry/','/httpservice/')))
        return any(host==domain or host.endswith('.'+domain) for domain in
                   ('google.com','gstatic.com','googleusercontent.com','bing.com','bing.net'))
    except ValueError:
        return False


def allowed_news_request(url, publisher, *, navigation=False, method='GET', resource='document'):
    try:
        parsed=urlsplit(url)
        origin=urlsplit(publisher)
        if origin.scheme != 'https' or not origin.hostname or origin.username or origin.password or origin.port not in (None,443):
            return False
        if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port not in (None,443):
            return False
        host=parsed.hostname or ''
        source=origin.hostname or ''
        same=bool(source) and (host.removeprefix('www.')==source.removeprefix('www.') or host.endswith('.'+source))
        if method=='POST':
            return not navigation and host=='news.google.com' and parsed.path.startswith('/_/DotsSplashUi/data/')
        if method!='GET' or resource in {'image','font','media'}:
            return False
        if navigation:
            return same or (host=='news.google.com' and parsed.path.startswith(('/rss/articles/','/articles/','/read/')))
        return same or any(host==d or host.endswith('.'+d) for d in (
            'google.com','gstatic.com','googleusercontent.com','gtimg.com'))
    except ValueError:
        return False


def render_search(url):
    return _render(url)


def render_news_article(url, publisher):
    return _render(url, publisher=publisher)


def _render(url, publisher=None):
    """Run normal page JavaScript; never click verification or persist cookies."""
    parsed=urlsplit(url)
    search_target=not publisher and allowed_request(url,navigation=True) and parsed.path=='/search'
    news_target=publisher and parsed.hostname=='news.google.com' and parsed.path.startswith('/rss/articles/') and allowed_news_request(url,publisher,navigation=True)
    if not (search_target or news_target):
        raise ValueError('Unsupported browser search target')
    from research_transport import research_proxy
    from video_transport import validate_video_target
    proxy=research_proxy()
    validate_video_target(url,proxy)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {'state':'browser-unavailable','error':'playwright-missing'}
    started=time.monotonic()
    try:
        with sync_playwright() as runtime:
            options={'headless':True,'chromium_sandbox':True,'timeout':10000}
            if proxy:
                options['proxy']={'server':proxy}
            browser=runtime.chromium.launch(**options)
            try:
                context=browser.new_context(locale='zh-CN',service_workers='block',accept_downloads=False)
                validated={parsed.hostname}
                count=0
                def route_request(route):
                    nonlocal count
                    req=route.request
                    count+=1
                    try:
                        policy=(lambda value,**kw:allowed_news_request(value,publisher,**kw)) if publisher else allowed_request
                        permitted=count<=100 and time.monotonic()-started<40 and policy(
                            req.url,navigation=req.is_navigation_request(),method=req.method,resource=req.resource_type)
                        host=urlsplit(req.url).hostname
                        if permitted and host not in validated:
                            validate_video_target(req.url,proxy)
                            validated.add(host)
                        route.continue_() if permitted else route.abort()
                    except Exception:
                        route.abort()
                context.route('**/*',route_request)
                tab=context.new_page()
                response=tab.goto(url,wait_until='domcontentloaded',timeout=20000)
                if response is None or response.status != 200:
                    return {'state':'browser-request-failed','error':'http-'+str(getattr(response,'status','none'))}
                try:
                    if publisher:
                        host=urlsplit(publisher).hostname.removeprefix('www.')
                        tab.wait_for_url(lambda value:(urlsplit(value).hostname or '').removeprefix('www.')==host,timeout=10000)
                    else:
                        tab.wait_for_function("() => document.querySelector('a h3, h2 a, a [role=heading]') || /unusual traffic|reCAPTCHA|异常流量/.test(document.body.innerText)",timeout=10000)
                except Exception:
                    pass
                if publisher:
                    source=urlsplit(publisher).hostname.removeprefix('www.')
                    if (urlsplit(tab.url).hostname or '').removeprefix('www.')!=source:
                        return {'state':'browser-request-failed','error':'publisher-not-reached'}
                    tab.locator('body').wait_for(timeout=5000)
                    text=tab.locator('body').inner_text(timeout=5000)[:40000]
                    title=tab.title()[:300]
                    if re.search(r'安全验证|验证码|captcha|access denied|just a moment',title,re.I):
                        return {'state':'verification-required','error':'publisher-verification'}
                    return {'state':'rendered','text':text,'url':tab.url,'title':title}
                if not allowed_request(tab.url,navigation=True):
                    return {'state':'browser-request-failed','error':'unexpected-navigation'}
                raw=tab.content()
                if len(raw)>1500000:
                    return {'state':'browser-request-failed','error':'response-too-large'}
                return {'state':'rendered','html':raw}
            finally:
                browser.close()
    except Exception as error:
        from website_diagnostics import capture_error_code
        return {'state':'browser-request-failed','error':capture_error_code(error)}
