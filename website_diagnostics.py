"""Safe screenshot error categories and optional loopback proxy selection."""
import re
import urllib.request
from food_source_transport import local_proxy
from local_settings import setting

TRANSIENT_NETWORK_ERRORS = {'network-reset', 'network-closed', 'network-changed',
                            'network-timeout', 'proxy-tunnel-failed'}


def screenshot_proxy():
    value = setting('WEBSITE_PROXY', None)
    if value is None:
        proxies = urllib.request.getproxies()
        value = proxies.get('https') or proxies.get('http') or ''
    return local_proxy(value)


def capture_error_code(error):
    message = str(error).lower()
    codes = {'err_connection_reset': 'network-reset', 'err_connection_closed': 'network-closed',
             'err_network_changed': 'network-changed', 'err_timed_out': 'network-timeout',
             'err_connection_timed_out': 'network-timeout',
             'err_tunnel_connection_failed': 'proxy-tunnel-failed',
             'err_proxy_connection_failed': 'proxy-unreachable', 'err_name_not_resolved': 'dns-failed',
             'err_cert_authority_invalid': 'certificate-invalid', 'err_cert_date_invalid': 'certificate-invalid',
             'err_cert_common_name_invalid': 'certificate-invalid'}
    for raw, code in codes.items():
        if re.search(r'\b' + raw + r'\b', message):
            return code
    if "executable doesn't exist" in message or isinstance(error, FileNotFoundError):
        return 'browser-executable-missing'
    if 'sandbox' in message:
        return 'browser-sandbox-failed'
    if 'browser has been closed' in message or 'target closed' in message:
        return 'browser-closed'
    if type(error).__name__ == 'TimeoutError' or isinstance(error, TimeoutError):
        return 'timeout'
    if isinstance(error, PermissionError):
        return 'permission-denied'
    if message == 'invalid-local-proxy':
        return 'invalid-local-proxy'
    return 'capture-failed'
