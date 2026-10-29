"""Resolve model transport without requiring a logged-in desktop proxy manager."""


def select_model_proxy(configured=None, system_proxies=None):
    if configured is not None:
        if not isinstance(configured, str):
            raise ValueError('MODEL_PROXY must be a URL string')
        if configured.strip():
            return configured.strip()
    proxies = system_proxies or {}
    return proxies.get('https') or proxies.get('http') or 'http://127.0.0.1:7890'
