"""Source-backed celestial environment cards; no invented live forecasts."""
import re
from io import BytesIO

from PIL import Image, ImageDraw
from corner_geometry import continuous_rectangle, WEATHER_PANEL_RADIUS


CATALOG = {
    'betelgeuse': {
        'name': 'Betelgeuse', 'kind': 'RED SUPERGIANT', 'accent': '#ffb07c',
        'headline': 'About 3,300+ °C', 'metric': 'Approximate visible surface temperature',
        'facts': [('Observed environment', 'A red supergiant with a dynamic atmosphere.'),
                  ('Weather outlook', 'No operational hourly or daily forecast is available.'),
                  ('Unknown conditions', 'Local wind, rainfall and human apparent temperature are unavailable.')],
        'brief': 'A stellar surface is no picnic destination, meow.',
        'basis': 'NASA educational summary; approximate characteristic temperature, not a current reading.',
        'source': 'https://science.nasa.gov/universe/what-is-betelgeuse-inside-the-strange-volatile-star/'},
    'mimas': {
        'name': 'Mimas', 'kind': 'SATURNIAN MOON', 'accent': '#93dcf1',
        'headline': '77 K / 92 K', 'metric': 'Typical cold / warm regions in a historical daytime map',
        'facts': [('Observed environment', 'Cassini measured strongly contrasting surface temperatures.'),
                  ('Temperature context', 'Approximately -196 °C / -181 °C; not global daily minimum / maximum.'),
                  ('Weather outlook', 'No live local wind, precipitation or hourly forecast is available.')],
        'brief': 'This is icy-world history, not a forecast for your next walk, meow.',
        'basis': 'Cassini CIRS observation: 2010-02-13. NASA publication: 2010-03-29.',
        'source': 'https://science.nasa.gov/resource/bizarre-temperatures-on-mimas-not-annotated/'},
    'kepler22b': {
        'name': 'Kepler-22 b', 'kind': 'EXOPLANET', 'accent': '#b5b0ff',
        'headline': 'Weather unknown', 'metric': 'Actual surface temperature is not established',
        'facts': [('Established context', 'A confirmed exoplanet in the habitable zone of a Sun-like star.'),
                  ('Important distinction', 'Habitable zone does not establish habitability or liquid surface water.'),
                  ('Unknown conditions', 'Atmosphere, surface conditions, local wind and rainfall remain unconfirmed.')],
        'brief': 'An intriguing destination, but the umbrella forecast is still unknown, meow.',
        'basis': 'NASA mission background. No live weather measurements or operational forecast.',
        'source': 'https://science.nasa.gov/resource/searching-for-habitable-worlds/'}
}
ALIASES = {'betelgeuse': 'betelgeuse', 'alpha orionis': 'betelgeuse',
           'mimas': 'mimas', 'saturn i': 'mimas', 'kepler22b': 'kepler22b'}
SOLAR_NAMES = ('sun mercury venus earth mars jupiter saturn uranus neptune moon phobos deimos io europa '
               'ganymede callisto enceladus tethys dione rhea titan hyperion iapetus miranda ariel '
               'umbriel titania oberon triton nereid charon pluto ceres eris haumea makemake').split()
ALIASES.update({name: name for name in SOLAR_NAMES})
ALIASES.update({'luna': 'moon', 'sol': 'sun'})
LABELS = {'title': 'CELESTIAL WEATHER', 'status': 'ENVIRONMENT PROFILE · NOT A LIVE FORECAST',
          'basis': 'Evidence & observation epoch', 'source': 'Source: NASA Science',
          'checked': 'Source review: 2026-11-19 · card generation does not refresh observations',
          'unsupported': 'No verified environment profile for this object yet. Try Betelgeuse, Mimas or Kepler-22 b.'}


def normalize(value):
    return re.sub(r'[\s_\-]+', '', value.strip().casefold())


def resolve_body(value, aliases=None):
    table = {normalize(k): v for k, v in {**ALIASES, **(aliases or {})}.items()}
    key = normalize(value)
    if key in table:
        return table[key]
    # Catalog-like unknown objects must never be sent to terrestrial geocoding.
    if re.fullmatch(r'(?:(?:kepler|toi|k2|hd|hip|gliese|gj|wasp|trappist)\d+(?:\.\d+)?[a-z]?|(?:jupiter|saturn|uranus|neptune)[ivxlcdm]+)', key):
        return value.strip()
    return None


def celestial_request(text):
    match = re.fullmatch(r'/?(?:celestial|space) weather(?:\s*:\s*|\s+)(.{1,120})', text.strip(), re.I)
    return match[1].strip() if match else None


class CelestialReport(str):
    def __new__(cls, body, labels):
        instance = super().__new__(cls, body['name'] + ': ' + body['headline'])
        instance.celestial_data, instance.celestial_labels = body, labels
        return instance


def make_report(key, language='en'):
    body, labels = CATALOG[key], LABELS
    if language == 'zh':
        try:
            from localized_celestial import localize
            body, labels = localize(key, body, labels)
        except ImportError:
            pass
    return CelestialReport(body, labels)


def render_celestial(report, font):
    """Measure the full layout first so dynamic paragraphs and citations never clip."""
    body, labels = report.celestial_data, report.celestial_labels
    scale, width, y = 2, 1800, 64
    probe = ImageDraw.Draw(Image.new('RGB', (1, 1)))
    operations = []

    def text(value, size=34, color='#d4dff0', bold=False, left=100, limit=1590):
        nonlocal y
        face = font(size * scale, bold)
        for paragraph in str(value).splitlines() or ['']:
            line = ''
            for char in paragraph:
                if line and probe.textlength(line + char, font=face) > limit * scale:
                    operations.append((left * scale, y * scale, line, face, color))
                    y += size * 1.55
                    line = ''
                line += char
            operations.append((left * scale, y * scale, line, face, color))
            y += size * 1.55

    text(labels['title'], 30, body['accent'], True)
    text(body['name'], 82, '#ffffff', True)
    text(body['kind'], 28, '#9caec9')
    text(labels['status'], 28, '#9caec9')
    y += 26
    top = y
    y += 35
    text(body['headline'], 68, body['accent'], True)
    text(body['metric'], 32)
    bottom = y + 35
    y = bottom + 35
    for title, content in body['facts']:
        text(title, 37, '#ffffff', True)
        text(content, 34)
        y += 20
    text(body['brief'], 35, body['accent'], True)
    y += 26
    text(labels['basis'], 28, '#ffffff', True)
    text(body['basis'], 27, '#9caec9')
    text(labels['checked'], 24, '#9caec9')
    y += 16
    text(labels['source'], 26, '#9caec9', True)
    text(body['source'], 22, '#9caec9')
    height = int(y + 60)
    if height > 10000:
        raise ValueError('Celestial card exceeds safe canvas height')
    image = Image.new('RGB', (width * scale, height * scale), '#101827')
    draw = ImageDraw.Draw(image)
    continuous_rectangle(draw, (64 * scale, top * scale, 1736 * scale, bottom * scale),
                         WEATHER_PANEL_RADIUS * scale, fill='#213049')
    for x, y, line, face, color in operations:
        draw.text((x, y), line, font=face, fill=color)
    image = image.resize((width, height), Image.Resampling.LANCZOS)
    out = BytesIO()
    image.save(out, 'PNG')
    return out.getvalue()
