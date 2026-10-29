"""A two-level visual guide with copyable plain-text compatibility."""
import asyncio
import base64
import json
import math
import re
from io import BytesIO

from PIL import Image, ImageDraw
from weather_delivery import report_font

MENU = {
    'weather': ['weather', 'local-weather', 'weather-maps'],
    'maps': ['maps', 'custom maps', 'advanced maps'],
    'search': ['search', 'video'],
    'interaction': ['interaction'],
    'community': ['community', 'earthquakes'],
    'website': ['website', 'food map'],
    'identity': ['identity'],
    'admin': ['admin'],
}


class HelpReply(str):
    def __new__(cls, text, cards, title, caption):
        instance = super().__new__(cls, text)
        instance.cards, instance.title, instance.caption = cards, title, caption
        return instance


def make_reply(topics, topic=None, *, menu=None, root='general', title='Bot · function guide',
               caption='Two levels only. Send help CATEGORY or help text CATEGORY for copyable commands.', text_only=False):
    menu = menu or MENU
    topic = topic or root
    if text_only:
        return topics.get(topic, topics[root])
    category = next((name for name, keys in menu.items() if topic == name or topic in keys), None)
    if category:
        cards = [(name, topics[name].splitlines()) for name in menu[category] if name in topics]
        if not cards:
            cards = [(category, [topics.get(topic, topics[root])])]
    else:
        cards = [(name, ['help '+name]+[topics[k].splitlines()[0] for k in keys if k in topics][:3])
                 for name, keys in menu.items()]
    return HelpReply(topics.get(topic, topics[root]), cards, title, caption)


def render_help(reply):
    width, gap, margin = 1600, 30, 55
    column = (width-margin*2-gap)//2
    font, heading = report_font(26), report_font(31)
    probe = ImageDraw.Draw(Image.new('RGB', (1,1)))
    cards = []
    for title, paragraphs in reply.cards:
        lines = []
        for paragraph in paragraphs:
            paragraph = re.sub(r'[\U0001f000-\U0001ffff\u2600-\u27ff\ufe0f]', '', paragraph).strip()
            current = ''
            for token in re.findall(r'[A-Za-z0-9_./:+°%≤≥-]+\s*|.', paragraph):
                for char in token:
                    if current and probe.textlength(current+char, font=font) > column-64:
                        lines.append(current); current = ''
                    current += char
            lines.append(current)
        cards.append((title,lines,110+len(lines)*39))
    row_heights = [max(c[2] for c in cards[i:i+2]) for i in range(0,len(cards),2)]
    height = 210+sum(row_heights)+gap*len(row_heights)+65
    image = Image.new('RGB',(width,height),'#edf4f7'); draw = ImageDraw.Draw(image)
    draw.text((margin,40),reply.title,font=report_font(43),fill='#153d51')
    # Long instructions remain in the message caption rather than overlapping cards.
    draw.text((margin,105),getattr(reply,'subtitle',str(len(cards))+' sections · two-level navigation'),font=report_font(24),fill='#597486')
    y = 170
    for row, row_height in enumerate(row_heights):
        for col in range(2):
            index = row*2+col
            if index >= len(cards):
                break
            title,lines,_ = cards[index]; x = margin+col*(column+gap)
            draw.rounded_rectangle((x,y,x+column,y+row_height),25,fill='white')
            draw.rounded_rectangle((x+24,y+25,x+30,y+66),3,fill='#1a8b9a')
            draw.text((x+46,y+24),title,font=heading,fill='#153d51')
            for j,line in enumerate(lines):
                draw.text((x+30,y+88+j*39),line,font=font,fill='#365669')
        y += row_height+gap
    stream=BytesIO(); image.save(stream,'PNG')
    return stream.getvalue()


async def deliver_help(ws, kind, group, user, reply):
    try:
        image = await asyncio.to_thread(render_help, reply)
        message = [{'type':'image','data':{'file':'base64://'+base64.b64encode(image).decode('ascii')}},
                   {'type':'text','data':{'text':reply.caption}}]
        print('[Help guide]: Visual two-level guide rendered; not added to stickers')
    except Exception as error:
        print('[Help guide]: Render failed; using copyable text', type(error).__name__)
        message = [{'type':'text','data':{'text':str(reply)}}]
    await ws.send(json.dumps({'action':'send_group_msg' if kind=='group' else 'send_private_msg',
        'params':{'group_id' if kind=='group' else 'user_id':group if kind=='group' else user,'message':message}}))
