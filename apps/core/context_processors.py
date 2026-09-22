from django.conf import settings


def app_status(request):
    """Expose API configuration state to every template (for the header banner)."""
    return {
        "api_key_configured": bool(settings.OPENROUTER_API_KEY),
        "mock_mode": settings.LLM_MOCK_MODE,
    }
