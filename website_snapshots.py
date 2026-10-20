"""On-demand captures of fixed public website routes, never owner sessions."""
import io
import time
import sys
from urllib.parse import urlsplit

from PIL import Image
from website_diagnostics import screenshot_proxy, capture_error_code, TRANSIENT_NETWORK_ERRORS

ORIGIN = 'https://frostmoonrui.com'
PAGES = {'home': '', 'about': 'about/', 'friends': 'links/',
         'resources': 'resources/', 'food': 'food-map/', 'projects': 'projects/'}
LANGUAGES = {'zh', 'en', 'ru', 'ja'}


class SnapshotError(RuntimeError):
    def __init__(self, message, code='capture-failed', stage='capture'):
        super().__init__(message)
        self.code, self.stage = code, stage


def page_url(page='home', language='en'):
    if page not in PAGES or language not in LANGUAGES:
        raise SnapshotError('Unknown public website page or language')
    return f'{ORIGIN}/{language}/{PAGES[page]}'


def request_allowed(url, navigation=False, target=None):
    """Deny arbitrary navigation, local addresses and owner API/session endpoints."""
    try:
        u = urlsplit(url)
        if u.scheme != 'https' or u.username or u.password or u.port not in {None, 443}:
            return False
        if navigation:
            return url == target
        if u.hostname == 'frostmoonrui.com':
            return (url == target or u.path.startswith(('/assets/', '/data/', '/_AMapService/'))
                    or u.path in {'/favicon.jpg', '/api/map-config', '/api/community/approved', '/food-map/index.json'})
        return any(u.hostname == domain or u.hostname.endswith('.'+domain)
                   for domain in ('amap.com', 'autonavi.com', 'amap.com.cn', 'amapcdn.com'))
    except (ValueError, AttributeError):
        return False


def capture(page='home', language='en', theme='dark', browser_path=''):
    """Use a fresh browser/context per request; no persistent profile, cookies or cache."""
    target = page_url(page, language)
    if theme not in {'light', 'dark'}:
        raise SnapshotError('Unknown website theme')
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise SnapshotError('Install screenshot support: python -m pip install playwright; python -m playwright install chromium',
                            code='playwright-missing', stage='initialization') from error
    print('[Website screenshot] Starting:', page, language, theme)
    started = time.monotonic()
    stage = 'proxy-configuration'
    try:
        proxy = screenshot_proxy()
        print('[Website screenshot] Runtime:', sys.version.split()[0], 'browser:', 'custom' if browser_path else 'bundled',
              'proxy:', 'local HTTP' if proxy else 'direct')
        stage = 'playwright-start'
        with sync_playwright() as runtime:
            stage = 'browser-launch'
            options = {'headless': True, 'chromium_sandbox': True, 'timeout': 10000}
            if proxy:
                options['proxy'] = {'server': proxy}
            if browser_path:
                options['executable_path'] = browser_path
            browser = runtime.chromium.launch(**options)
            try:
                stage = 'browser-context'
                context = browser.new_context(viewport={'width': 1280, 'height': 960},
                                              color_scheme=theme, reduced_motion='reduce',
                                              service_workers='block', accept_downloads=False)
                def route_request(route):
                    request = route.request
                    if request.method == 'GET' and request_allowed(
                            request.url, request.is_navigation_request(), target):
                        route.continue_()
                    else:
                        route.abort()
                context.route('**/*', route_request)
                tab = context.new_page()
                tab.set_default_timeout(10000)
                stage = 'navigation'
                for attempt in range(2):
                    try:
                        response = tab.goto(target, wait_until='domcontentloaded', timeout=20000)
                        break
                    except Exception as error:
                        code = capture_error_code(error)
                        if attempt or code not in TRANSIENT_NETWORK_ERRORS:
                            raise
                        print('[Website screenshot] Retrying navigation once:', code)
                if response is None or response.status != 200 or tab.url != target:
                    status = getattr(response, 'status', None)
                    code = 'http-' + str(status) if isinstance(status, int) and status != 200 else 'unexpected-navigation'
                    raise SnapshotError('The requested public page is unavailable', code=code, stage=stage)
                stage = 'page-ready'
                tab.locator('main').wait_for(state='visible')
                stage = 'page-load'
                tab.wait_for_load_state('load', timeout=10000)
                stage = 'fonts-ready'
                tab.wait_for_function("() => document.fonts.status === 'loaded'", timeout=5000)
                if page == 'friends':
                    stage = 'friends-ready'
                    tab.wait_for_function("() => document.documentElement.dataset.communityReady === 'true'", timeout=12000)
                if page == 'food':
                    stage = 'map-ready'
                    tab.locator('#load-map').click()
                    tab.wait_for_function("() => !document.getElementById('load-map').disabled", timeout=20000)
                    tab.locator('#map-canvas').scroll_into_view_if_needed()
                # Static viewport capture avoids unbounded long pages and hidden lazy images.
                stage = 'screenshot'
                raw = tab.screenshot(type='png', full_page=False, animations='disabled', timeout=10000)
                stage = 'image-validation'
                if len(raw) > 8*1024*1024:
                    raise SnapshotError('Website screenshot exceeded the image size limit', code='image-too-large', stage=stage)
                with Image.open(io.BytesIO(raw)) as image:
                    if image.format != 'PNG':
                        raise SnapshotError('Unexpected screenshot format', code='invalid-image-format', stage=stage)
                    image.verify()
                print('[Website screenshot] Completed:', page, f'{time.monotonic()-started:.1f}s')
                return {'url': target, 'image': raw}
            finally:
                try:
                    browser.close()
                except Exception as error:
                    print('[Website screenshot] Cleanup warning:', capture_error_code(error))
    except SnapshotError as error:
        print('[Website screenshot] Failed:', error.stage, error.code)
        raise
    except Exception as error:
        code = capture_error_code(error)
        print('[Website screenshot] Failed:', stage, code)
        raise SnapshotError('Website capture failed (' + stage + '/' + code + '); the public page is still available as a link.',
                            code=code, stage=stage) from error
