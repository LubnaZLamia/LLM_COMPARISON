"""Intentionally empty.

An earlier version of this migration created a cache table for API embeddings.
That approach was dropped before it shipped, so the migration is kept as a no-op:
it applies cleanly whether or not the earlier version already ran, and nothing
else in the project refers to that table.
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [("llms", "0002_llmmodel_is_judge")]

    operations = []
