from django import template

register = template.Library()


@register.filter
def pct(value):
    """0.623 -> '62%'. Missing values show as a dash."""
    return "–" if value is None else f"{value:.0%}"


@register.filter
def num(value, places=2):
    """A statistic to a fixed number of places, or a dash when it can't be computed."""
    return "–" if value is None else f"{value:.{int(places)}f}"


@register.filter
def signed(value):
    """A gap in percentage points, with its sign: 0.12 -> '+12 pts'."""
    return "–" if value is None else f"{value * 100:+.0f} pts"
