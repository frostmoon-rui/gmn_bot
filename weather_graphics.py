"""Local weather pictograms and bounded raster antialiasing; no remote assets."""
import math
import re

from PIL import ImageDraw


def raster_scale(width, height):
    """Bound supersampled work to 48 million pixels; very long cards stay native."""
    pixels = width * height
    return 2 if pixels <= 12_000_000 else 1.5 if pixels <= 21_000_000 else 1


class SmoothDraw:
    """Draw in logical pixels, then downsample once; measurements stay unscaled."""
    def __init__(self, image, scale):
        self.draw = ImageDraw.Draw(image)
        self.scale = scale
        self.faces = {}

    def coords(self, value):
        return [self.coords(item) if isinstance(item, (tuple, list)) else item * self.scale for item in value]

    def options(self, values):
        result = dict(values)
        for name in ('width', 'radius'):
            if name in result:
                result[name] = max(1, round(result[name] * self.scale))
        return result

    def rounded_rectangle(self, xy, **kwargs):
        return self.draw.rounded_rectangle(self.coords(xy), **self.options(kwargs))

    def ellipse(self, xy, **kwargs):
        return self.draw.ellipse(self.coords(xy), **self.options(kwargs))

    def line(self, xy, **kwargs):
        return self.draw.line(self.coords(xy), **self.options(kwargs))

    def polygon(self, xy, **kwargs):
        return self.draw.polygon(self.coords(xy), **self.options(kwargs))

    def arc(self, xy, start, end, **kwargs):
        return self.draw.arc(self.coords(xy), start, end, **self.options(kwargs))

    def face(self, font):
        if self.scale == 1:
            return font
        token = id(font)
        if token not in self.faces:
            self.faces[token] = (font, font.font_variant(size=round(font.size * self.scale)))
        return self.faces[token][1]

    def text(self, xy, text, font, **kwargs):
        return self.draw.text(self.coords(xy), text, font=self.face(font), **kwargs)

    def textlength(self, text, font):
        return self.draw.textlength(text, font=self.face(font)) / self.scale


def condition_metadata(row):
    """Retain only explicit provider condition/daylight scalars, never icon URLs."""
    output = {}
    condition = row.get('weatherCondition')
    name = condition.get('type') if isinstance(condition, dict) else row.get('weather_type')
    if isinstance(name, str) and re.fullmatch('[A-Z_]{2,64}', name):
        output['weather_type'] = name
    code = row.get('weather_code')
    if type(code) in (int, float) and math.isfinite(code) and code == int(code) and 0 <= code <= 99:
        output['weather_code'] = int(code)
    flag = row.get('isDaytime', row.get('is_day'))
    if type(flag) in (bool, int) and flag in (0, 1):
        output['is_day'] = int(flag)
    return output


def condition_kind(row, daily=False):
    """Classify actual condition codes; missing measurements never imply sunshine."""
    period = row.get('daytimeForecast') if daily else None
    source = period if isinstance(period, dict) and period else row
    metadata = condition_metadata(source)
    code = metadata.get('weather_code')
    if code is not None:
        if code == 0:
            return 'clear'
        if code in (1, 2):
            return 'partly'
        if code == 3:
            return 'cloud'
        if code in (45, 48):
            return 'fog'
        if code in (51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82):
            return 'rain'
        if code in (71, 73, 75, 77, 85, 86):
            return 'snow'
        if code in (95, 96, 99):
            return 'storm'
    condition = source.get('weatherCondition', {})
    description = condition.get('description', {}) if isinstance(condition, dict) else {}
    description = description.get('text', '') if isinstance(description, dict) else ''
    text = str(metadata.get('weather_type') or source.get('desc') or source.get('condition')
               or description or (row.get('day_weather') if daily else '') or '').lower().replace('_', ' ')
    words = set(re.findall('[a-z]+', text))
    words |= {word[:-1] for word in words if word.endswith('s')}
    # Legacy provider condition names are matching-only protocol inputs, not UI text.
    legacy = {'storm': ('雷',), 'snow': ('雪', '冰雹'), 'rain': ('雨',), 'fog': ('雾', '霾'),
              'partly': ('多云', '晴间'), 'cloud': ('阴',), 'clear': ('晴',)}
    terms = {'storm': {'thunderstorm', 'thunder', 'storm'}, 'snow': {'snow', 'snowy', 'sleet', 'hail'},
             'rain': {'rain', 'rainy', 'showers', 'shower', 'drizzle'}, 'fog': {'fog', 'foggy', 'mist', 'haze'},
             'partly': {'partly', 'mostly', 'scattered'}, 'cloud': {'cloudy', 'clouds', 'overcast'},
             'clear': {'clear', 'sunny'}}
    for kind in terms:
        if words & terms[kind] or any(value in text for value in legacy[kind]):
            return kind
    return 'unknown'


def weather_icon(draw, x, y, size, row, daily=False, background='white'):
    """Vector symbols remain readable without emoji fonts or external requests."""
    kind = condition_kind(row, daily)
    flag = condition_metadata(row).get('is_day')
    night = flag == 0 and not daily
    def box(a, b, c, d):
        return (x+a*size, y+b*size, x+c*size, y+d*size)
    def line(points, color, width=2):
        draw.line([(x+a*size,y+b*size) for a,b in points], fill=color, width=max(1,width*size/48))
    def sun():
        if night:
            draw.ellipse(box(.17,.08,.79,.70), fill='#e9b94d')
            draw.ellipse(box(.39,.01,.91,.53), fill=background)
        else:
            draw.ellipse(box(.31,.23,.69,.61), fill='#efbb43')
            for angle in range(0,360,45):
                a=math.radians(angle)
                line([(.5+.27*math.cos(a),.42+.27*math.sin(a)),
                      (.5+.37*math.cos(a),.42+.37*math.sin(a))], '#efbb43', 2.6)
    def cloud():
        draw.ellipse(box(.12,.43,.54,.83), fill='#a6bacb')
        draw.ellipse(box(.33,.24,.79,.78), fill='#bccbd6')
        draw.ellipse(box(.60,.44,.94,.83), fill='#a6bacb')
        draw.rounded_rectangle(box(.24,.57,.82,.83),radius=size*.1,fill='#bccbd6')
    if kind == 'unknown':
        draw.ellipse(box(.30,.25,.70,.65),outline='#c5d0da',width=max(1,size*.04))
        return
    if kind == 'clear':
        sun()
        return
    if kind == 'partly':
        sun()
    cloud()
    if kind == 'rain':
        for offset in (.3,.53,.76):
            line([(offset,.88),(offset-.06,1)],'#378fae',2.6)
    elif kind == 'snow':
        for offset in (.32,.70):
            for angle in (0,60,120):
                a=math.radians(angle)
                line([(offset-.065*math.cos(a),.94-.065*math.sin(a)),
                      (offset+.065*math.cos(a),.94+.065*math.sin(a))],'#619cba',1.6)
    elif kind == 'storm':
        draw.polygon([(x+a*size,y+b*size) for a,b in ((.51,.64),(.38,.87),(.52,.87),(.46,1.03),(.70,.76),(.55,.76))],fill='#dfac36')
    elif kind == 'fog':
        for offset in (.89,1):
            line([(.19,offset),(.82,offset)],'#8da4b5',2.4)
