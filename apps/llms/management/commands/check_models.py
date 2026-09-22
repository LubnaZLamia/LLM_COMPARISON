from django.core.management.base import BaseCommand

from apps.llms.models import LLMModel
from apps.llms.services.openrouter import list_model_ids


class Command(BaseCommand):
    help = "Check that each configured model id still exists on OpenRouter, and suggest alternatives."

    def handle(self, *args, **opts):
        available = list_model_ids()
        self.stdout.write(f"OpenRouter currently lists {len(available)} models.\n")
        for m in LLMModel.objects.all():
            if m.openrouter_id in available:
                self.stdout.write(self.style.SUCCESS(f"  OK       {m.openrouter_id}"))
                continue
            vendor = m.openrouter_id.split("/")[0]
            suggestions = sorted(i for i in available if i.startswith(vendor + "/"))[:8]
            self.stdout.write(self.style.ERROR(f"  MISSING  {m.openrouter_id}"))
            if suggestions:
                self.stdout.write("           try: " + ", ".join(suggestions))
        self.stdout.write("\nEdit model ids at http://127.0.0.1:8000/admin/llms/llmmodel/")
