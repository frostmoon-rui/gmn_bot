from local_settings import setting
"""Backend-only configuration; read real values from ignored local_settings.json or environment variables."""
import os

JS_KEY = setting('AMAP_JS_KEY', '').strip()
JS_SECURITY_CODE = setting('AMAP_JS_SECURITY_CODE', '').strip()
