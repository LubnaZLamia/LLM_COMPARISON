import re

from django import template

register = template.Library()

_ITEM = re.compile(r"^\s*(?:\d+[.)]|[-*•])\s+(.*)$")


@register.filter
def as_items(text):
    """Return list items if `text` is a numbered/bulleted list, else an empty list."""
    items = []
    for line in (text or "").splitlines():
        m = _ITEM.match(line)
        if m:
            items.append(m.group(1).replace("**", "").strip())
        elif line.strip() and items:
            items[-1] += " " + line.strip()
    return items if len(items) >= 2 else []
