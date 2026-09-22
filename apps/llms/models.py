from django.db import models


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
