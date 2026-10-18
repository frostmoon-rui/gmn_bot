"""On-demand tools, low-interruption preferences and opt-in reminders; no live bot import."""
import asyncio
import base64
import html
import json
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from bot_care import crisis_intent

TZ = timezone(timedelta(hours=8))
HELP = {'general': '🐾 Lucky Cat · Quick guide\n'
            'Mention me in groups; speak directly in private. Use cat help to open this guide.\n'
            'Chat: ask a question | generate image:description\n'
            'Video: reply to a card and mention me with summarize video, or summarize video:URL\n'
            'Weather: set location:city | weather today | weather tomorrow\n'
            'Care: comfort me | keep me company | grounding exercise | mental health support | '
            'first aid:situation\n'
            'Tools: remind me 10 minutes rest | my reminders | cancel reminder ID\n'
            'Preferences: call me Rain | do not disturb on/off | clear context\n'
            'More: help search / video / maps / food map / identity / admin | bot status',
 'video': '🎬 Video information summary\n'
          'Reply to a shared card and mention me with summarize video, or use summarize video:URL. '
          'You can also ask about your most recent card from the last 30 minutes.\n'
          'Public information is fetched in the background; MiMo 2.6 Flash returns 2–3 sentences '
          'within 3 minutes. Use view sources to check references.\n'
          'This summarizes public metadata rather than watching the audiovisual content. Bilibili '
          'gets detailed metadata where available; other sites depend on readable public pages. '
          'Native QQ video usually needs the original public page URL. Sharing alone does not '
          'trigger a reply.',
 'search': '🔎 Search (allowlisted users)\n'
           'search:question → Python retrieval + MiMo 2.6 Flash summary\n'
           'ask assistant:question → Python retrieval + MiMo 2.6 Pro summary\n'
           'The conversation model decides when ordinary questions need current evidence or an '
           'encyclopedia lookup. Wiki is queried as needed.\n'
           'Replies default to 2–4 sentences. Use view sources for references. Background jobs '
           'allow up to 5 minutes; models summarize retrieved material without AI browsing tools.',
 'maps': '🗺️ Amap (mention me in groups)\n'
         'map:full place | search places:city|keyword\n'
         'search nearby:full place|keyword|radius in meters\n'
         'driving route / walking route / cycling route / transit route:origin -> destination\n'
         'custom map:name|place1;place2 | my maps | view map:name\n'
         'amap weather:city | road traffic:city|road\n'
         'More: help custom maps / advanced maps\n'
         'Abroad: Google map:place | Google route:origin -> destination\n'
         'air quality:city | local timezone:city (Google)\n'
         'set location:country city (a province alone uses its capital as a reference while '
         'retaining the original record)\n'
         'weather today | weather tomorrow; reports do not disclose your bound location.\n'
         'Amap, Google, Moji and Open-Meteo supply parallel weather data; MSN needs a configured '
         'connector.',
 'custom maps': '📍 Create a map\n'
                'custom map:weekend trip|Beijing Palace Museum;Beijing Temple of Heaven;Beijing '
                'Summer Palace\n'
                'Full places are resolved automatically. Saved maps return an image with up to 10 '
                'points.\n'
                'my maps | view map:weekend trip | view map:weekend trip|connect\n'
                'add map point:weekend trip|Beijing Beihai Park | remove map point:weekend trip|B\n'
                'delete map:weekend trip|confirm (a local recovery copy is retained)\n'
                'You can only view or edit your own maps in this chat. Group results are visible '
                'to that group. Member locations are never collected automatically. Connecting '
                'lines are illustrative, not road navigation.',
 'advanced maps': '🧭 Map tools\n'
                  'geocode:full address | reverse geocode:longitude,latitude\n'
                  'convert coordinates:gps|longitude,latitude (also baidu/gcj02)\n'
                  'administrative area:city | IP location:public IPv4 (city reference only)\n'
                  'input suggestions:city|keyword | place details:POI ID\n'
                  'polygon '
                  'search:longitude,latitude;longitude,latitude;longitude,latitude|keyword\n'
                  'measure distance:origin -> destination (driving distance)\n'
                  'nearby traffic:full place|radius in meters | rectangle traffic:bottom-left '
                  'coordinates|top-right coordinates\n'
                  'transit stop:city|stop name | transit line:city|line\n'
                  'transit stop ID:ID | transit line ID:ID (not live arrivals)\n'
                  'Coordinates default to GCJ-02, longitude first; do not mix Google/GPS '
                  'coordinates. Provider permissions and quotas apply. Paid services are not '
                  'enabled automatically.',
 'food map': '🍜 Cat Food Map (run locally first)\n'
             'Open http://127.0.0.1:8788 on the host.\n'
             'submit food:city|full venue name|full address|category|reason\n'
             'Optionally append |original HTTPS recommendation URL. Approved submissions become '
             'public.\n'
             'Member submissions require review; superadmin submissions are included directly.\n'
             'Admins: pending food | approve food:ID | remove food:ID\n'
             'Location facts from the public CacheTide blog map retain individual original '
             'sources. QQ IDs, private addresses and personal weather bindings are not published. '
             'This is a local address.',
 'identity': '🌱 Names and care\n'
             'call me Rain | use they/them for me | my gender identity is nonbinary\n'
             'identity settings:gender identity|pronouns|name | view my identity | clear my '
             'identity\n'
             'Your latest explicit statement takes priority; HRT, clothes and nicknames do not '
             'determine identity.\n'
             'comfort me | keep me company | grounding exercise | mental health support | first '
             'aid:situation\n'
             'Care shortcuts do not add notebook records, start private chats or notify others. '
             'The chat platform may retain messages.',
 'admin': '🔧 Administration\n'
          'Group admins/superadmins: normal mode (default) | quiet mode | active mode | clear '
          'context\n'
          'Superadmins: switch model to Gemini/GPT/Claude/DeepSeek\n'
          'allowlist add/remove QQ | blocklist add/remove QQ\n'
          'important allowlist add/remove QQ | view important allowlist\n'
          'organize notebook | organize portraits | person record:QQ information\n'
          'Allowlisted users: view notebook | retract | add/remove trigger word\n'
          'CacheTide is a superadmin and should be addressed as CacheTide; only the original admin '
          'can grant new admin permissions.\n'
          'earthquake warning / latest earthquake distinguish warning references from post-event '
          'reports; no reliable countdown is provided.\n'
          'Important weather is sent at 22:30; do not disturb on pauses personal notifications. '
          'Update announcements are not posted to groups.'}


def cq_escape(text, parameter=False):
    value = str(text).replace('&', '&amp;').replace('[', '&#91;').replace(']', '&#93;')
    return value.replace(',', '&#44;') if parameter else value


def message_text(data):
    """Preserve real OneBot at/reply/image segments without letting text forge CQ commands."""
    message = data.get('message')
    if isinstance(message, list):
        parts = []
        for segment in message:
            if not isinstance(segment, dict):
                continue
            kind, info = segment.get('type'), segment.get('data', {})
            if not isinstance(info, dict):
                continue
            if kind == 'text':
                parts.append(cq_escape(info.get('text', '')))
            elif kind in {'at', 'reply', 'image'}:
                attrs = ','.join(str(k) + '=' + cq_escape(v, True) for k, v in info.items())
                parts.append('[CQ:' + kind + (',' + attrs if attrs else '') + ']')
        return ''.join(parts).strip()
    return str(data.get('raw_message') or message or '').strip()


def event_text(data):
    raw = message_text(data)
    cleaned = html.unescape(re.sub(r'\[CQ:[^\]]+\]', '', raw))
    cleaned = re.sub(r'[\u200b-\u200f\ufeff]', '', cleaned).strip()
    # Do not strip arbitrary member names or treat mentions of others as addressing the bot.
    cleaned = re.sub(r'^@(?:Lucky Cat|Lucky|Yuyuko)(?:\s|/)+', '', cleaned).strip()
    mentioned = bool(re.search(r'\[CQ:at,qq=' + re.escape(str(data.get('self_id'))) + r'(?:,|\])', raw))
    mentioned = mentioned or bool(re.match(r'^@(?:Lucky Cat|Lucky|Yuyuko)(?:\s|/|$)', raw))
    return raw, cleaned, mentioned


def safe_segments(text):
    """Send model output as text, allowing only validated supported Base64 images."""
    segments = []
    offset = 0
    for match in re.finditer(r'\[CQ:image,file=base64://([A-Za-z0-9+/=]+)\]', text):
        try:
            blob = base64.b64decode(match[1], validate=True)
        except ValueError:
            continue
        if not blob.startswith((b'\x89PNG\r\n\x1a\n', b'GIF87a', b'GIF89a', b'\xff\xd8\xff', b'RIFF')):
            continue
        if match.start() > offset:
            segments.append({'type': 'text', 'data': {'text': text[offset:match.start()]}})
        segments.append({'type': 'image', 'data': {'file': 'base64://' + match[1]}})
        offset = match.end()
    if offset < len(text):
        segments.append({'type': 'text', 'data': {'text': text[offset:]}})
    return segments


def reply_belongs_to(message, event):
    if not isinstance(message, dict):
        return False
    if event.get('message_type') == 'group':
        return message.get('group_id') is not None and str(message['group_id']) == str(event.get('group_id'))
    peer = message.get('peer_user_id') or message.get('user_id') or (message.get('sender') or {}).get('user_id')
    return message.get('message_type') == 'private' and str(peer) == str(event.get('user_id'))


def parse_reminder(text, now=None):
    now = now or datetime.now(TZ)
    match = re.fullmatch(r'remind me\s+(\d+)\s*(minutes?|hours?|days?)\s+(.+)', text, re.S | re.I)
    if match:
        amount = int(match[1])
        if amount <= 0:
            raise ValueError('Reminder delay must be greater than 0.')
        seconds = amount * {'minute': 60, 'hour': 3600, 'day': 86400}[match[2].lower().rstrip('s')]
        if seconds > 366 * 86400:
            raise ValueError('Reminders can be scheduled up to 366 days ahead.')
        return now + timedelta(seconds=seconds), match[3].strip()
    match = re.fullmatch(r'remind me\s+(today|tomorrow|\d{4}-\d{2}-\d{2})\s+(\d{1,2}[:：]\d{2})\s+(.+)', text, re.S | re.I)
    if not match:
        raise ValueError('Usage: remind me 10 minutes rest; or remind me tomorrow 08:30 go out (Beijing time).')
    try:
        date_text = match[1].lower()
        day = now.date() + timedelta(days=date_text == 'tomorrow') if date_text in {'today', 'tomorrow'} else datetime.strptime(date_text, '%Y-%m-%d').date()
        hour, minute = map(int, match[2].replace('：', ':').split(':'))
        due = datetime(day.year, day.month, day.day, hour, minute, tzinfo=TZ)
    except ValueError:
        raise ValueError('Invalid date or time; check and try again.') from None
    if not now < due <= now + timedelta(days=366):
        raise ValueError('Choose a time within the next 366 days (Beijing time).')
    return due, match[3].strip()


class Features:
    def __init__(self, bot):
        self.b = bot
        self.started = time.monotonic()
        self.prefs = bot.load_json_data('bot_preferences.json', {'groups': {}, 'users': {}})
        self.prefs.setdefault('groups', {})
        self.prefs.setdefault('users', {})
        self.reminders = bot.load_json_data('reminders.json', [])
        self.last_passive = {}
        self.repeat_states = {}

    def mode(self, group):
        return self.prefs['groups'].get(str(group), {}).get('mode', 'normal')

    def active(self, group):
        return self.mode(group) in {'normal', 'active'}

    def allow_passive(self, group, feature='random', consume=True):
        if not self.active(group):
            return False
        now = time.monotonic()
        key = (str(group), feature)
        interval = {'random': 300, 'repeat': 60, 'notice': 120}.get(feature, 300)
        if now - self.last_passive.get(key, float('-inf')) < interval:
            return False
        if consume:
            self.mark_passive(group, feature)
        return True

    def mark_passive(self, group, feature):
        self.last_passive[(str(group), feature)] = time.monotonic()
        if feature == 'repeat' and str(group) in self.repeat_states:
            self.repeat_states[str(group)]['sent'] = True

    def observe_repeat(self, group, user, text, directed=False):
        """Repeat once after two different members send the same short text within 90 seconds."""
        key, now = str(group), time.monotonic()
        if directed or not text or len(text) > 200 or re.search(r'https?://|\[CQ:|[\r\n]', text):
            self.repeat_states.pop(key, None)
            return False
        if re.match(r'^(?:remind me|set location|notebook|search|ask assistant|summarize video|help|manual)', text):
            self.repeat_states.pop(key, None)
            return False
        if crisis_intent(text):
            self.repeat_states.pop(key, None)
            return False
        previous = self.repeat_states.get(key)
        if not previous or previous['text'] != text or now-previous['updated'] > 90:
            previous = {'text': text, 'users': set(), 'updated': now, 'sent': False}
            self.repeat_states[key] = previous
        previous['users'].add(user)
        previous['updated'] = now
        return len(previous['users']) >= 2 and not previous['sent'] and self.allow_passive(group, 'repeat', consume=False)

    def notifications_enabled(self, user):
        return not self.prefs['users'].get(str(user), {}).get('dnd', False)

    async def command(self, text, kind, group, user, addressed, admin=False):
        if not addressed and text != 'cat help':
            return None
        help_match = re.fullmatch(r'(?:help|manual|cat help|kitty help|butler help)(?:\s+(.+))?', text, re.I)
        if help_match:
            return HELP.get((help_match[1] or 'general').strip().lower(), 'Options: help search / maps / identity / admin')
        if text in {'bot status', 'cat status'}:
            provider = self.b.ACTIVE_MODEL_PROVIDER
            name = {'gpt': self.b.GPT_MODEL_NAME, 'claude': self.b.CLAUDE_MODEL_NAME,
                    'deepseek': self.b.DEEPSEEK_MODEL_NAME}.get(provider)
            if not name:
                name = next(iter(self.b.AVAILABLE_CHAT_MODELS), 'No models discovered')
            return f'Online for {int((time.monotonic()-self.started)/60)} minutes | {provider}: {name}\n' + \
                f'Stickers: {len(self.b.stolen_emojis)} | Group mode: ' + {'normal': 'normal (light interaction)', 'active': 'active', 'quiet': 'quiet'}.get(self.mode(group), 'quiet') + \
                '\nStatus does not indicate API quota or guarantee model availability.'
        if text in {'quiet mode', 'active mode', 'normal mode'}:
            if kind != 'group':
                return 'Use this in the group you want to configure.'
            if not admin:
                return 'Only group admins or superadmins can change the group mode.'
            self.prefs['groups'].setdefault(str(group), {})['mode'] = {'quiet mode': 'quiet', 'active mode': 'active', 'normal mode': 'normal'}[text]
            self.repeat_states.pop(str(group), None)
            await self.b.save_json_data_async('bot_preferences.json', self.prefs)
            print('[Group preferences]:', group, text)
            return 'Enabled ' + text + ('. Repeating and random interjections are paused; direct requests still receive replies.' if text == 'quiet mode' else '. Repeating and occasional replies are enabled; random interjections are at least 5 minutes apart, counted separately for each group.')
        if text in {'do not disturb on', 'do not disturb off'}:
            self.prefs['users'].setdefault(str(user), {})['dnd'] = text == 'do not disturb on'
            await self.b.save_json_data_async('bot_preferences.json', self.prefs)
            return 'Your automatic weather and earthquake notifications are paused. Direct queries and your scheduled reminders remain available.' if text == 'do not disturb on' else 'Your previous automatic weather and earthquake notifications are restored.'
        if text == 'clear context':
            if kind == 'group' and not admin:
                return 'Only group admins or superadmins can clear group context. You can clear your own private chat.'
            session = f'group_{group}' if kind == 'group' else f'private_{user}'
            for attr in ('chat_histories', 'gpt_chat_histories', 'claude_chat_histories', 'deepseek_chat_histories'):
                getattr(self.b, attr).pop(session, None)
            if kind == 'group':
                self.b.group_unread_buffers.pop(group, None)
            return 'Short-term context for this chat is cleared. Notebook and identity records are retained.'
        if text.startswith('remind me'):
            try:
                due, content = parse_reminder(text)
            except ValueError as error:
                return str(error)
            if not content or len(content) > 300:
                return 'Reminder content must contain 1–300 characters.'
            if sum(r.get('user') == user and r.get('status') == 'pending' for r in self.reminders) >= 20:
                return 'You already have 20 pending reminders. Cancel unwanted reminders first.'
            identifier = uuid.uuid4().hex[:8]
            record = {'id': identifier, 'user': user, 'kind': kind, 'group': group,
                      'due': due.timestamp(), 'content': content, 'status': 'pending'}
            self.reminders.append(record)
            await self.b.save_json_data_async('reminders.json', self.reminders)
            print('[Reminder created]:', identifier, due.isoformat())
            return f'Reminder scheduled {identifier}: {due:%m-%d %H:%M} (Beijing time).\nUse "cancel reminder {identifier}" to cancel.'
        if text == 'my reminders':
            rows = [f"{r['id']} | {datetime.fromtimestamp(r['due'], TZ):%m-%d %H:%M} | {r['content']}" + (' (delivery status unknown)' if r['status'] == 'uncertain' else '')
                    for r in self.reminders if r.get('user') == user and r.get('status') in {'pending', 'uncertain'}
                    and (kind == 'private' or (r.get('kind') == kind and r.get('group') == group))]
            return 'Pending reminders (Beijing time):\n' + '\n'.join(rows) if rows else 'You have no pending reminders here.'
        match = re.fullmatch(r'cancel reminder\s+([0-9a-fA-F]{8})', text)
        if match:
            record = next((r for r in self.reminders if r.get('id') == match[1].lower() and
                           r.get('user') == user and r.get('status') in {'pending', 'uncertain'}), None)
            if not record:
                return 'No pending reminder belonging to you was found.'
            record['status'] = 'cancelled'
            await self.b.save_json_data_async('reminders.json', self.reminders)
            return 'This reminder was cancelled.'
        return None

    async def deliver_due(self, now=None):
        now = time.time() if now is None else now
        for record in list(self.reminders):
            if record.get('status') != 'pending' or record.get('due', now+1) > now or record.get('retry_at', 0) > now:
                continue
            if now - record['due'] > 86400:
                record['status'] = 'expired'
                print('[Reminder expired]:', record['id'], 'Offline for more than a day; record retained for review')
            else:
                ws = self.b.active_ws
                if ws is None:
                    continue
                try:
                    if record['user'] in self.b.BLACKLIST_QQ:
                        continue
                    segments = ([{'type': 'at', 'data': {'qq': str(record['user'])}}] if record['kind'] == 'group' else [])
                    segments.append({'type': 'text', 'data': {'text': 'Reminder: ' + record['content']}})
                    await ws.send(json.dumps({'action': 'send_group_msg' if record['kind'] == 'group' else 'send_private_msg',
                        'params': {'group_id' if record['kind'] == 'group' else 'user_id': record['group'] if record['kind'] == 'group' else record['user'], 'message': segments}}))
                    record['status'] = 'delivered'
                    print('[Reminder delivered]:', record['id'])
                except asyncio.TimeoutError:
                    record['status'] = 'uncertain'
                    print('[Reminder receipt unknown]:', record['id'], 'No automatic resend; check with my reminders')
                except Exception as error:
                    record['retry_at'] = now + 60
                    print('[Reminder awaiting retry]:', record['id'], type(error).__name__)
            await self.b.save_json_data_async('reminders.json', self.reminders)

    async def reminder_loop(self):
        while True:
            try:
                await self.deliver_due()
            except Exception as error:
                print('[Reminder loop error]:', type(error).__name__)
            await asyncio.sleep(15)
