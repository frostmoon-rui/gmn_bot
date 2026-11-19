"""Explicit live acceptance: free Gemini and public retrieval; local cards only, no QQ."""
import argparse
import asyncio
from datetime import datetime
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from local_settings import setting
from model_proxy import select_model_proxy


async def main(output):
    proxy = select_model_proxy(setting('MODEL_PROXY', None), urllib.request.getproxies())
    if proxy:
        os.environ['HTTP_PROXY'] = os.environ['HTTPS_PROXY'] = os.environ['ALL_PROXY'] = proxy
    os.environ['NO_PROXY'] = '127.0.0.1,localhost,::1'
    from google import genai
    from google.genai import types
    from bot_extensions import Extensions
    from celestial_research import CelestialResearch, presentation
    import celestial_research
    from weather_delivery import render_report

    catalog = json.loads((Path(__file__).resolve().parents[1] / 'model_catalogs_cache/gemini.json').read_text())
    names = [name for name in catalog['models'] if 'flash' in name or 'lite' in name or name == 'gemini-4-hydrogen']
    client = genai.Client(api_key=setting('GEMINI_API_KEY', ''))
    bot = SimpleNamespace(AVAILABLE_CHAT_MODELS=names, client=client,
        gemini_content_config=lambda **kwargs:types.GenerateContentConfig(
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True), **kwargs))
    ext = Extensions.__new__(Extensions)
    ext.b, ext.search_gate, ext.search_cache = bot, asyncio.Semaphore(2), {}
    service = CelestialResearch(bot, ext.python_search)
    output.mkdir(parents=True, exist_ok=True)
    summaries = []
    original_validator = celestial_research.validate_facts
    traces = []

    def trace_validation(data, pages):
        result = original_validator(data, pages)
        traces.append({'response': data, 'evidence': pages, 'accepted': result})
        (output / 'extraction-trace.json').write_text(json.dumps(traces, ensure_ascii=False, indent=2), encoding='utf-8')
        return result

    celestial_research.validate_facts = trace_validation

    async def run(name, key):
        print('[Live card start]:', key, flush=True)
        report = await asyncio.wait_for(service.report(name, 'zh', known=key), timeout=210)
        path = output / (key.replace(' ', '-') + '.png')
        await asyncio.to_thread(path.write_bytes, await asyncio.to_thread(render_report, report))
        body = report.celestial_data
        reference = presentation('zh')['notice'] in body['basis']
        result = {'object': key, 'card': str(path.resolve()), 'headline': body['headline'],
                  'status': report.celestial_labels['status'], 'reference_fallback': reference,
                  'unknown_fields': sum(value == presentation('zh')['unknown'] for _, value in body['facts']),
                  'facts': body['facts'], 'basis': body['basis'], 'sources': body['source'],
                  'qq_messages_sent': 0, 'paid_key_used': False}
        summaries.append(result)
        (output / (key.replace(' ', '-') + '.json')).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print('[Live card result]:', key, json.dumps({k:result[k] for k in ('headline','reference_fallback','unknown_fields')}, ensure_ascii=False), flush=True)

    try:
        # Run serially so one recovered free model is reused without a burst of requests.
        for name, key in [('Sun', 'sun'), ('Moon', 'moon'), ('M78', 'messier78'), ('Land of Light', 'land of light')]:
            await run(name, key)
    finally:
        celestial_research.validate_facts = original_validator
        (output / 'results.json').write_text(json.dumps(summaries, ensure_ascii=False, indent=2), encoding='utf-8')
        await client.aio.aclose()
        client.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('backup') / ('celestial-live-' + datetime.now().strftime('%Y%m%d-%H%M%S')))
    args = parser.parse_args()
    asyncio.run(main(args.output))
