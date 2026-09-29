"""`{% model_mark obj %}` — the little shape badge that identifies a model.

`obj` is anything with .mark / .tint / .ink (an LLMModel or a ModelOutput), so the
same tag works on the Compare page, the results cards and the ratings tables.
"""
from django import template

register = template.Library()


@register.inclusion_tag("partials/model_mark.html")
def model_mark(obj, size="", label=""):
    return {
        "mark": getattr(obj, "mark", "spark"),
        "tint": getattr(obj, "tint", "rgba(100,116,139,.12)"),
        "line": getattr(obj, "line", "rgba(100,116,139,.3)"),
        "ink": getattr(obj, "ink", "#64748b"),
        "size": size,  # "" (24px), "lg" (28px) or "sm" (18px, plain grey)
        "label": label or getattr(obj, "provider", ""),
    }
