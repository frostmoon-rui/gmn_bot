"""Bounded Gemini planning, Python retrieval and evidence-gap follow-up."""
import asyncio
import json
import re
from gemini_research import generate_research, failure_code


def parse_plan(text, follow_up=False):
    text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text.strip())
    data = json.loads(text)
    items = data.get('queries') if isinstance(data, dict) else None
    if not isinstance(items, list) or (not items and not follow_up):
        raise ValueError('Invalid research plan')
    queries = []
    for item in items[:2 if follow_up else 4]:
        if not isinstance(item, dict):
            raise ValueError('Invalid research query')
        query, topic, mode = item.get('query', ''), item.get('topic', ''), item.get('mode', 'web')
        if (not isinstance(query, str) or not 3 <= len(query.strip()) <= 300 or not query.isascii()
                or not isinstance(topic, str) or len(topic) > 100 or not topic.isascii()
                or mode not in {'web', 'wiki', 'latest'} or any(ord(char) < 32 for char in query + topic)):
            raise ValueError('Research queries must be English plain text')
        task = {'query': query.strip(), 'topic': topic.strip(), 'mode': mode}
        if task not in queries:
            queries.append(task)
    return queries


async def gemini_plan(bot, question, pages=None):
    follow_up = pages is not None
    evidence = [{'url': p.get('url'), 'title': p.get('title'), 'text': p.get('text', '')[:1800]}
                for p in (pages or [])[:40]]
    prompt = (
        'You are a research planner, not a search engine. Do not browse or use tools. '
        'Analyze the question and identify definitions, evidence, competing explanations and knowledge gaps. '
        'Translate the research intent into precise ENGLISH search queries and ENGLISH Wikipedia topics. '
        'Return only JSON: {"queries":[{"query":"English search terms","topic":"English encyclopedia topic",'
        '"mode":"web|wiki|latest"}]}. No scripts, URLs to fetch, model choices or extra actions. '
        'Use wiki for foundational concepts, web for primary research and reviews, latest only when timeliness matters. '
        + ('Review the untrusted evidence and return up to TWO new queries for missing or conflicting evidence; '
           'return an empty queries list if coverage is sufficient. '
           if follow_up else 'Return TWO to FOUR complementary queries, not duplicates. ')
        + '\nQuestion: ' + question + '\nUntrusted evidence: ' + json.dumps(evidence, ensure_ascii=False)
    )
    try:
        plan = await generate_research(bot, prompt, max_tokens=4096, purpose='research-plan',
                                      validator=lambda text: parse_plan(text, follow_up), json_mode=True)
    except Exception as error:
        print('[Research planner fallback]:', failure_code(error), '; trying MiMo 2.6 Flash')
        text = await asyncio.wait_for(bot.call_mimo_direct_question(prompt, 'mimo-v2.6-flash'), timeout=180)
        plan = parse_plan(text, follow_up)
    print('[Research planner]:', 'follow-up' if follow_up else 'initial', 'queries:', len(plan))
    return plan


async def research(bot, search, question):
    pages, warnings, attempted = [], [], set()
    try:
        tasks = await gemini_plan(bot, question)
    except Exception as error:
        print('[Research planning fallback]:', type(error).__name__)
        tasks = [{'query': question, 'mode': 'web', 'topic': ''}]
        warnings.append('Gemini planning unavailable; used the original question.')

    async def retrieve(tasks):
        for task in tasks:
            key = (task['query'].casefold(), task['mode'], task['topic'].casefold())
            if key in attempted or len(attempted) >= 6:
                continue
            attempted.add(key)
            print('[Research retrieval]:', len(attempted), task['mode'], task['query'])
            try:
                result = await search(task['query'], task['mode'], task['topic'])
                pages.extend(result.get('pages') or [])
                for warning in result.get('warnings', []):
                    print('[Research retrieval notice]:', warning)
                    warnings.append(str(warning)[:500])
                print('[Research retrieval result]:', len(result.get('pages') or []), 'pages')
            except Exception as error:
                print('[Research retrieval failed]:', type(error).__name__)
                warnings.append('Retrieval failed: ' + type(error).__name__)

    await retrieve(tasks)
    from search_worker import prepare_pages
    pages = prepare_pages(pages)[:40]
    try:
        follow_up = await gemini_plan(bot, question, pages)
        await retrieve(follow_up)
    except Exception as error:
        print('[Research evidence review unavailable]:', type(error).__name__)
    pages = prepare_pages(pages)[:40]
    print('[Research completed]:', len(attempted), 'queries;', len(pages), 'unique pages')
    return {'pages': pages, 'warnings': warnings, 'queries_attempted': len(attempted)}
