"""Shared continuous corners for first-party raster graphics."""
import math

CORNER_RADII = (8, 18, 32, 44)
CURVE_PARAMETER = 1.5
CORNER_POWER = 2 ** (1 - CURVE_PARAMETER)


def corner_points(box, radius):
    """Trace an continuous superellipse at each corner, joined by straight edges."""
    x0, y0, x1, y1 = box
    radius = max(0, min(radius, (x1 - x0) / 2, (y1 - y0) / 2))
    if not radius:
        return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    points = []
    corners = ((x1 - radius, y0 + radius), (x1 - radius, y1 - radius),
               (x0 + radius, y1 - radius), (x0 + radius, y0 + radius))
    for index, (cx, cy) in enumerate(corners):
        for step in range(17):
            angle = (index - 1 + step / 16) * math.pi / 2
            c, s = math.cos(angle), math.sin(angle)
            dx = math.copysign(abs(c) ** CORNER_POWER, c) if abs(c) > 1e-12 else 0
            dy = math.copysign(abs(s) ** CORNER_POWER, s) if abs(s) > 1e-12 else 0
            points.append((cx + radius * dx, cy + radius * dy))
    return points


def continuous_rectangle(draw, box, radius, fill=None, outline=None, width=1):
    points = corner_points(box, radius)
    draw.polygon(points, fill=fill, outline=outline, width=width)
