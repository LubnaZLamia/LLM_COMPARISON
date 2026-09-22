"""Create the models shown on the Compare page.

OpenRouter adds and retires models all the time, so instead of hard-coding ids
this command looks at OpenRouter's public model list and picks the newest
matching model for each provider slot below. If OpenRouter can't be reached,
it falls back to the static ids in FALLBACK.

    python manage.py seed_models            # only if no models exist yet
    python manage.py seed_models --repair   # swap out ids OpenRouter no longer offers (run.bat does this)
    python manage.py seed_models --refresh  # replace all models with the newest picks
"""
import re

from django.core.management.base import BaseCommand

from apps.llms.models import LLMModel
from apps.llms.services.openrouter import list_models

# Variants we never want as a default pick.
GLOBAL_EXCLUDE = re.compile(
    r"[:~]|(^|[-/._])(batch|free|preview|exp|beta|mini|nano|lite|tiny|small|audio|image|vision|tts|realtime|"
    r"search|embed|embedding|guard|codex|oss|deep|research|thinking|contributor|online)([-/._]|$)",
    re.I,
)

SLOTS = [
    # provider, colour, badge, selected by default, [(include regex, extra exclude regex)] tried in order
    dict(provider="OpenAI", color="#10a37f", badge="O", selected=True, judge=True,
         rules=[(r"^openai/gpt-", r"pro|turbo|chat|instruct")]),
    dict(provider="Anthropic", color="#d97706", badge="A", selected=True,
         rules=[(r"^anthropic/claude-", r"haiku|opus"), (r"^anthropic/claude-", r"")]),
    dict(provider="Google", color="#2563eb", badge="G", selected=True,
         rules=[(r"^google/gemini-", r"")]),
    dict(provider="Meta", color="#7c3aed", badge="M", selected=False,
         rules=[(r"^meta-llama/llama-.*(70b|405b|maverick|scout)", r""), (r"^meta(-llama)?/", r"")]),
    dict(provider="Mistral", color="#ea580c", badge="M", selected=False,
         rules=[(r"^mistralai/mistral-(large|medium)", r""), (r"^mistralai/", r"")]),
]

FALLBACK = {
    "OpenAI": ("openai/gpt-4o", "GPT-4o"),
    "Anthropic": ("anthropic/claude-sonnet-4", "Claude Sonnet 4"),
    "Google": ("google/gemini-2.5-flash", "Gemini 2.5 Flash"),
    "Meta": ("meta-llama/llama-3.3-70b-instruct", "Llama 3.3 70B"),
    "Mistral": ("mistralai/mistral-large", "Mistral Large"),
}


def _pretty_name(model: dict) -> str:
    name = model.get("name") or model["id"].split("/", 1)[1]
    return name.split(": ", 1)[-1].strip()


def pick_models(catalogue: list[dict]) -> dict[str, tuple[str, str]]:
    """Return {provider: (openrouter_id, display_name)} using the newest match per slot."""
    picks = {}
    for slot in SLOTS:
        for include, exclude in slot["rules"]:
            matches = [
                m for m in catalogue
                if re.search(include, m["id"], re.I)
                and not GLOBAL_EXCLUDE.search(m["id"])
                and not (exclude and re.search(exclude, m["id"], re.I))
            ]
            if matches:
                best = max(matches, key=lambda m: m.get("created") or 0)
                picks[slot["provider"]] = (best["id"], _pretty_name(best))
                break
    return picks


def replacement_for(model, catalogue: list[dict], picks: dict) -> tuple[str, str] | None:
    """Find a current model to replace a retired one, first by provider slot, then by vendor prefix."""
    if model.provider in picks:
        return picks[model.provider]
    vendor = model.openrouter_id.split("/")[0]
    matches = [m for m in catalogue if m["id"].startswith(vendor + "/") and not GLOBAL_EXCLUDE.search(m["id"])]
    if not matches:
        return None
    best = max(matches, key=lambda m: m.get("created") or 0)
    return best["id"], _pretty_name(best)


class Command(BaseCommand):
    help = "Create default LLMs, picking each provider's newest model from OpenRouter."

    def add_arguments(self, parser):
        parser.add_argument("--refresh", action="store_true", help="Delete existing models and pick again.")
        parser.add_argument("--repair", action="store_true",
                            help="Keep existing models, but replace any id that no longer exists on OpenRouter.")

    def repair(self, catalogue: list[dict], picks: dict) -> None:
        available = {m["id"] for m in catalogue}
        stale = [m for m in LLMModel.objects.all() if m.openrouter_id not in available]
        if not stale:
            self.stdout.write("All configured models are still available on OpenRouter.")
            return
        for model in stale:
            new = replacement_for(model, catalogue, picks)
            if not new or LLMModel.objects.filter(openrouter_id=new[0]).exclude(pk=model.pk).exists():
                model.is_active = False
                model.save(update_fields=["is_active"])
                self.stdout.write(self.style.WARNING(
                    f"  {model.openrouter_id} is retired and has no replacement; deactivated it."))
                continue
            old_id = model.openrouter_id
            model.openrouter_id, model.display_name = new
            model.save(update_fields=["openrouter_id", "display_name"])
            self.stdout.write(self.style.SUCCESS(f"  {old_id} is retired -> now using {new[0]}"))

    def handle(self, *args, **opts):
        if LLMModel.objects.exists() and not (opts["refresh"] or opts["repair"]):
            self.stdout.write("Models already configured (use --refresh to re-pick). Skipping.")
            return

        try:
            catalogue = list_models()
            picks = pick_models(catalogue)
        except Exception as exc:  # offline, proxy, etc.
            catalogue, picks = [], {}
            self.stdout.write(self.style.WARNING(f"Couldn't reach OpenRouter ({exc}); using the ids already configured."))

        # Existing setup: only swap out ids that OpenRouter no longer offers.
        if LLMModel.objects.exists() and opts["repair"] and not opts["refresh"]:
            if catalogue:
                self.repair(catalogue, picks)
            return

        if opts["refresh"]:
            LLMModel.objects.all().delete()
        if picks:
            self.stdout.write(f"Picked newest models from OpenRouter for {len(picks)} provider(s).")

        for order, slot in enumerate(SLOTS, start=1):
            model_id, name = picks.get(slot["provider"]) or FALLBACK[slot["provider"]]
            LLMModel.objects.update_or_create(
                openrouter_id=model_id,
                defaults=dict(
                    display_name=name, provider=slot["provider"], badge=slot["badge"], color=slot["color"],
                    selected_by_default=slot["selected"], is_judge=slot.get("judge", False), order=order,
                ),
            )
            self.stdout.write(f"  {slot['provider']:<10} {model_id}")
        self.stdout.write(self.style.SUCCESS("Done. Edit models any time at /admin/llms/llmmodel/"))
