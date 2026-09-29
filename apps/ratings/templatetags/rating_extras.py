from django import template

register = template.Library()

# One icon per rubric dimension, matching the sprite in partials/icons.html
DIMENSION_ICONS = {
    "fluency": "type",
    "coherence": "link",
    "relevance": "target",
    "factuality": "shield",
    "completeness": "check-circle",
    "safety": "alert",
}


@register.filter
def dim_icon(field_name: str) -> str:
    return DIMENSION_ICONS.get(field_name, "check")
