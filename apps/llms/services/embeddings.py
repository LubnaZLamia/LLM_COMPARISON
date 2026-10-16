"""Removed.

An earlier draft scored the clinician comparison with embeddings from an API
endpoint that had not been verified end to end. It was replaced by
`matcher.py`, which does the same alignment through the chat client the app
already uses for every generation.

This placeholder exists only so an old copy of the file cannot be imported by
mistake. It is safe to delete.
"""
raise ImportError("apps.llms.services.embeddings was removed; clinician matching lives in apps.comparisons.services.matcher")
