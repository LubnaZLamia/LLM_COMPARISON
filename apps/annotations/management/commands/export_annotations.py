"""Write peer annotations to exports/annotations/, one CSV per annotator, to share through git.

    python manage.py export_annotations                     # everyone in this database
    python manage.py export_annotations --annotator "Sam"   # just one person
    python manage.py export_annotations --dir other/        # or another folder

Commit the files and push. Everyone else gets them on their next start, because
run.bat and run.sh run import_exports. Nothing here calls a model.
"""
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.annotations.services import sharing


class Command(BaseCommand):
    help = "Write peer annotations to exports/annotations/, one CSV per annotator."

    def add_arguments(self, parser):
        parser.add_argument("--dir", default=str(settings.BASE_DIR / "exports"), help="The exports folder.")
        parser.add_argument("--annotator", default="", help="Only this annotator.")

    def handle(self, *args, **opts):
        written = sharing.write(Path(opts["dir"]) / "annotations", opts["annotator"].strip())
        for path, rows in sorted(written.items()):
            self.stdout.write(f"{path.name}: {rows} annotation(s)")
        if written:
            self.stdout.write(self.style.SUCCESS(f"Wrote {sum(written.values())} annotation(s) to {len(written)} file(s)."))
        else:
            self.stdout.write(self.style.WARNING("No annotations to export."))
