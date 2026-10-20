"""Separate, high-to-low Gemini Pro/Argon research candidates and slow calls."""
import asyncio
import re

RESEARCH_MODE = 'gemini-research'
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


async def generate_research(bot, prompt, max_tokens=16000):
    models = research_model_queue(bot)
    failures = []
    for model in models:
        print('[Gemini research answer]: Trying', model, 'timeout:', MODEL_TIMEOUT)
        try:
            response = await asyncio.wait_for(bot.client.aio.models.generate_content(
                model=model, contents=prompt, config=bot.gemini_content_config(
                    temperature=0.3, max_output_tokens=max_tokens, tools=[],
                    http_options={'timeout': MODEL_TIMEOUT * 1000})), timeout=MODEL_TIMEOUT)
            text = (response.text or '').strip()
            if not text:
                raise ValueError('Empty Gemini research answer')
            print('[Gemini research answer]: Succeeded', model)
            return text
        except Exception as error:
            code = failure_code(error)
            failures.append(model + ':' + code)
            print('[Gemini research answer unavailable]:', model, code, '; keeping candidate')
    raise RuntimeError('Gemini research candidates unavailable: ' + (', '.join(failures) or 'no Pro/Argon candidates'))
