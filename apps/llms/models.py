from django.db import models

# Each provider gets one of these abstract shapes as its mark (see
# templates/partials/icons.html). They are plain geometry on purpose: they make a
# model recognisable at a glance without reproducing anyone's logo.
MARKS = ("hex", "trio", "rings", "diamond", "chevron", "spark")
PROVIDER_MARKS = {
    "openai": "rings",
    "anthropic": "chevron",
    "google": "diamond",
    "meta": "hex",
    "mistral": "trio",
    "deepseek": "spark",
    "x-ai": "spark",
    "xai": "spark",
    "qwen": "hex",
    "alibaba": "hex",
    "cohere": "rings",
    "microsoft": "diamond",
    "amazon": "trio",
    "nvidia": "chevron",
}
FALLBACK_COLOR = "#64748b"


def mark_for(provider: str, fallback: str = "") -> str:
    """Shape id for a provider. Known names are fixed; anything else is hashed
    into the same small set, so a new provider still gets a stable mark."""
    key = (provider or fallback or "").strip().lower()
    if key in PROVIDER_MARKS:
        return PROVIDER_MARKS[key]
    if not key:
        return "spark"
    return MARKS[sum(map(ord, key)) % len(MARKS)]


def _rgb(color: str) -> tuple[int, int, int]:
    raw = (color or "").lstrip("#")
    if len(raw) == 3:
        raw = "".join(c * 2 for c in raw)
    try:
        return int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16)
    except (ValueError, IndexError):
        return _rgb(FALLBACK_COLOR)


def tint_of(color: str, alpha: float = 0.12) -> str:
    """Very light wash of the model's colour, for the badge background."""
    r, g, b = _rgb(color)
    return f"rgba({r},{g},{b},{alpha})"


def line_of(color: str) -> str:
    """A hairline of the same colour for the badge's inner border."""
    return tint_of(color, 0.3)


def ink_of(color: str) -> str:
    """The same colour darkened until it reads on that pale wash."""
    r, g, b = _rgb(color)
    lum = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255
    if lum > 0.42:
        f = 0.42 / lum
        r, g, b = int(r * f), int(g * f), int(b * f)
    return f"#{r:02x}{g:02x}{b:02x}"


class LLMModel(models.Model):
    """A model that can be selected on the Compare page.

    `openrouter_id` is the exact slug from https://openrouter.ai/models
    (e.g. "openai/gpt-4o"). Edit these in the Django admin when models are
    added or retired.
    """

    display_name = models.CharField(max_length=80)
    provider = models.CharField(max_length=80, help_text="Shown in brackets, e.g. OpenAI")
    openrouter_id = models.CharField(max_length=160, unique=True)
    badge = models.CharField(max_length=3, blank=True, help_text="1-3 letters shown in the badge. Defaults to initials.")
    color = models.CharField(max_length=7, default="#2563eb", help_text="Hex colour for this model's card and bars.")
    is_active = models.BooleanField(default=True, help_text="Inactive models are hidden from the Compare page.")
    selected_by_default = models.BooleanField(default=False)
    is_judge = models.BooleanField(
        default=False,
        help_text="Use this model to score outputs when 'Score with judge model' is ticked "
                  "(ignored if JUDGE_MODEL is set in .env).",
    )
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "display_name"]
        verbose_name = "LLM model"

    def __str__(self):
        return f"{self.display_name} ({self.provider})"

    @property
    def badge_text(self) -> str:
        return self.badge or "".join(w[0] for w in self.display_name.split()[:2]).upper()

    # --- identity mark (shape + two shades of this model's colour) ---
    @property
    def mark(self) -> str:
        return mark_for(self.provider, self.display_name)

    @property
    def tint(self) -> str:
        return tint_of(self.color)

    @property
    def line(self) -> str:
        return line_of(self.color)

    @property
    def ink(self) -> str:
        return ink_of(self.color)
