"""Free-first scoped Gemini planning and direct Argon calls; retain catalog helpers."""
import asyncio
import re

RESEARCH_MODE = 'gemini-research'
DIRECT_ARGON_MODE = 'gemini-argon-direct'
MODEL_TIMEOUT = 600
EXCLUDE = ('image', 'audio', 'tts', 'transcribe', 'live', 'embedding', 'robotics',
           'omni', 'customtools', 'deep-research', 'antigravity')


def is_research_model(name):
    value = str(name).removeprefix('models/').lower()
    return (value.startswith('gemini-') and bool(re.search(r'-(?:pro|argon)(?:-|$)', value))
            and not any(word in value for word in EXCLUDE))


def research_model_score(name):
    value = name.lower()
    version = re.search(r'^gemini-(\d+)(?:\.(\d+))?(?:-|$)', value)
    major, minor = (int(version[1]), int(version[2] or 0)) if version else (0, 0)
    return major, minor, 'argon' in value, 'preview' not in value, value.endswith('-latest'), value


def rank_research_models(names):
    names = {str(name).removeprefix('models/') for name in names if is_research_model(name)}
    return sorted(names, key=research_model_score, reverse=True)


def research_model_queue(bot):
    names = list(getattr(bot, 'AVAILABLE_RESEARCH_MODELS', []))
    if not names:
        names = list(getattr(bot, 'AVAILABLE_ORGANIZER_MODELS', [])) + list(getattr(bot, 'AVAILABLE_CHAT_MODELS', []))
    return rank_research_models(names)


def failure_code(error):
    if isinstance(error, (asyncio.TimeoutError, TimeoutError)):
        return 'timeout'
    code = getattr(error, 'code', None)
    return str(code) if isinstance(code, int) else type(error).__name__


async def generate_research(bot, prompt, max_tokens=8192, *, purpose='argon-answer', validator=None, json_mode=False):
    if purpose not in {'research-plan', 'argon-answer'}:
        raise ValueError('Unsupported Gemini fallback purpose')
    models = ['gemini-4-argon', 'gemini-3.8-flash'] if purpose == 'research-plan' else ['gemini-4-argon']
    config = {'temperature': 0.1 if json_mode else 0.3, 'max_output_tokens': max_tokens, 'tools': [],
              'http_options': {'timeout': MODEL_TIMEOUT * 1000, 'retry_options': {'attempts': 1}}}
    if json_mode:
        config['response_mime_type'] = 'application/json'
    failures = []
    for tier in ('free', 'paid'):
        if tier == 'paid' and not getattr(bot, 'RESEARCH_PAID_ENABLED', False):
            break
        for model in models:
            print('[Scoped Gemini]: Trying', tier, purpose, model)
            try:
                if tier == 'free':
                    response = await asyncio.wait_for(bot.client.aio.models.generate_content(
                        model=model, contents=prompt, config=bot.gemini_content_config(**config)), timeout=MODEL_TIMEOUT)
                else:
                    from paid_gemini import generate_paid
                    response = await generate_paid(bot, model, prompt, config, purpose)
                text = (response.text or '').strip()
                if not text:
                    raise ValueError('Empty Gemini answer')
                result = validator(text) if validator else text
                print('[Scoped Gemini]: Succeeded', tier, model)
                return result
            except Exception as error:
                code = failure_code(error)
                failures.append(tier + '/' + model + ':' + code)
                print('[Scoped Gemini unavailable]:', tier, model, code)
    raise RuntimeError('Gemini research candidates unavailable: ' + ', '.join(failures))
