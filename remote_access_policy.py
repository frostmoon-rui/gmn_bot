"""Keep remote support off the proxy while retaining a default proxy route."""
from copy import deepcopy

PROCESS_NAMES = (
    'AweSun.exe', 'awesun_guard.exe', 'p2ptelemetry.exe',
    'SunloginClient.exe', 'SunloginClient_Service.exe', 'SunloginService.exe',
    'SunloginRemote.exe', 'SunloginClient_Desktop.exe',
)
DOMAINS = ('oray.com', 'oray.net', 'orayimg.com', 'sunlogin.com',
           'sunlogin.net', 'aweray.com', 'awesun.com')


def apply_policy(config):
    result = deepcopy(config)
    defaults = [r for r in result.get('rules', []) if isinstance(r, str)
                and r.upper().startswith('MATCH,')]
    # This host used global mode. Preserve its selected GLOBAL policy as the
    # catch-all; do not retain unrelated subscription rules ahead of that route.
    fallback = 'MATCH,GLOBAL' if result.get('mode') == 'global' else (
        defaults[-1] if defaults else 'MATCH,GLOBAL')
    result['mode'] = 'rule'
    result['find-process-mode'] = 'always'
    result['rules'] = [f'PROCESS-NAME,{name},DIRECT' for name in PROCESS_NAMES] + [
        f'DOMAIN-SUFFIX,{domain},DIRECT' for domain in DOMAINS] + [fallback]
    dns = result.setdefault('dns', {})
    filters = dns.setdefault('fake-ip-filter', [])
    if not isinstance(filters, list):
        raise ValueError('Unsupported fake-ip-filter format; no config written')
    if dns.get('fake-ip-filter-mode', 'blacklist') != 'blacklist':
        raise ValueError('Whitelist DNS filters require explicit review')
    for domain in DOMAINS:
        value = '+.' + domain
        if value not in filters:
            filters.append(value)
    return result


def script_policy():
    """Clash Verge global script, reapplied when profiles are regenerated."""
    import json
    return '''// Keep remote support direct; other traffic keeps the selected GLOBAL policy.
function main(config) {
  const processes = %s;
  const domains = %s;
  config.mode = "rule";
  config["find-process-mode"] = "always";
  config.rules = processes.map(p => `PROCESS-NAME,${p},DIRECT`)
    .concat(domains.map(d => `DOMAIN-SUFFIX,${d},DIRECT`), ["MATCH,GLOBAL"]);
  config.dns = config.dns || {};
  if (config.dns["fake-ip-filter-mode"] && config.dns["fake-ip-filter-mode"] !== "blacklist")
    throw new Error("Remote access policy requires blacklist DNS filters");
  config.dns["fake-ip-filter"] = [...new Set((config.dns["fake-ip-filter"] || [])
    .concat(domains.map(d => "+." + d)))];
  return config;
}
''' % (json.dumps(PROCESS_NAMES), json.dumps(DOMAINS))
