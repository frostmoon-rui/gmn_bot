"""Compact, allowlisted source attribution and non-sensitive service notices."""
import re

LABELS = {
    'source_summary': 'Sources', 'service_notice': 'Notes', 'other_services': 'other notices',
    'no_hourly': 'Hourly data unavailable', 'no_daily': 'Daily data unavailable',
    'not_covered': 'not covered', 'alerts_not_covered': 'alerts unavailable', 'rate_limited': 'rate limited',
    'timed_out': 'timed out', 'access_failed': 'access/configuration', 'connection_failed': 'connection failed',
    'invalid_data': 'location/data validation', 'no_data': 'no matching data', 'retry_later': 'retry deferred',
    'service_failed': 'temporarily unavailable', 'quality_notice': 'Some invalid or mismatched data excluded',
    'different_date': 'Other local dates excluded', 'source_difference': 'Sources differ; no averaging',
    'coverage_note': 'Missing values are not zero; current, hourly and daily data have separate periods.',
    'wind_scale_note': 'Wind force is not a measured speed', 'scope_current': 'Current conditions',
    'forecast_day': 'Forecast date', 'data_unavailable': 'Not supplied',
    'source_amap': 'Amap', 'source_google': 'Google Weather', 'source_moji': 'Moji',
    'source_model': 'Open-Meteo (model)', 'source_msn': 'MSN', 'source_other': 'Other weather service'}


def provider_name(name, labels):
    text = str(name).lower()
    if 'amap' in text or '\u9ad8\u5fb7' in text:
        key = 'amap'
    elif 'google' in text:
        key = 'google'
    elif 'moji' in text or '\u58a8\u8ff9' in text:
        key = 'moji'
    elif 'meteo' in text:
        key = 'model'
    elif 'msn' in text:
        key = 'msn'
    else:
        key = 'other'
    return labels['source_' + key]


def status_key(status):
    text = str(status).casefold()
    if '404' in text:
        return 'alerts_not_covered' if 'alerts=' in text and not any(f'{k}=' in text for k in ('current', 'days', 'hours')) else 'not_covered'
    if '429' in text:
        return 'rate_limited'
    if re.search(r'\b40[13]\b', text):
        return 'access_failed'
    if 'timeout' in text or 'timed out' in text:
        return 'timed_out'
    if any(word in text for word in ('ssl', 'urlerror', 'connection', 'oserror', 'socket', 'dns')):
        return 'connection_failed'
    if any(word in text for word in ('valueerror', 'typeerror', 'keyerror', 'validation', 'geocode')):
        return 'invalid_data'
    if 'defer' in text or 'cooldown' in text or 'backing off' in text or '\u9000\u907f' in text:
        return 'retry_later'
    if 'no matching' in text or 'no data' in text or '\u65e0\u5339\u914d' in text:
        return 'no_data'
    return 'service_failed'


def footer_lines(data, labels):
    """Exactly two bounded lines. Never render raw error bodies, URLs or custom provider identifiers."""
    labels = dict(LABELS, **labels)
    names = list(dict.fromkeys(provider_name(name, labels) for name in data.get('sources', [])))
    if not names and data.get('source'):
        names = [provider_name(data['source'], labels)]
    sources = labels['source_summary'] + ': ' + (' / '.join(names[:5]) or labels['unknown'])
    notices = []
    for name, status in data.get('provider_status', {}).items():
        if status in {'ok', 'available', 'acquired', 'obtained', 'data obtained', 'data retrieved', '\u5df2\u53d6\u5f97\u8d44\u6599'}:
            continue
        notice = provider_name(name, labels) + ': ' + labels[status_key(status)]
        if notice not in notices:
            notices.append(notice)
    if not data.get('hours'):
        notices.append(labels['no_hourly'])
    if not data.get('days'):
        notices.append(labels['no_daily'])
    if data.get('quality_flags'):
        notices.append(labels['quality_notice'])
    if data.get('excluded_source_dates'):
        notices.append(labels['different_date'])
    if data.get('disagreements'):
        notices.append(labels['source_difference'])
    if len(notices) > 3:
        notices = notices[:2] + ['+' + str(len(notices) - 2) + ' ' + labels['other_services']]
    return [sources, labels['service_notice'] + ': ' + (' · '.join(notices) if notices else labels['coverage_note'])]
