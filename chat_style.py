"""Keep passive chat brief without silently chopping off ordinary sentences."""
import re


def prepare_passive_reply(text, limit=120):
    text=re.sub(r'<(?:think|analysis)>.*?</(?:think|analysis)>','',str(text),flags=re.I|re.S)
    text=re.sub(r'<(?:think|analysis)>.*$','',text,flags=re.I|re.S)
    text=re.sub(r'\[[A-Z_]+:[^\]]*\]','',text)
    text='[SPLIT]'.join(part.strip() for part in text.split('[SPLIT]') if part.strip())
    if len(text)<=limit:
        return text
    ends=[match.end() for match in re.finditer(r'[。！？!?;；.](?:[”’"）)]*)',text)]
    before=[end for end in ends if end<=limit]
    if before:
        return text[:before[-1]]
    after=[end for end in ends if end<=limit*2]
    if after:
        return text[:after[0]]
    if len(text)<=limit*2:
        return text
    return text[:limit].rstrip()+'…'


def generation_hit_limit(response):
    candidates=getattr(response,'candidates',None) or []
    reason=getattr(candidates[0],'finish_reason',None) if candidates else None
    return str(getattr(reason,'value',reason) or '').upper().split('.')[-1]=='MAX_TOKENS'
