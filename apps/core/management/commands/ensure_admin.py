from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Create a local admin account (admin / admin) if no superuser exists. For local use only."

    def handle(self, *args, **opts):
        User = get_user_model()
        if User.objects.filter(is_superuser=True).exists():
            return
        User.objects.create_superuser("admin", "", "admin")
        self.stdout.write(self.style.WARNING(
            "Created admin login for /admin: username 'admin', password 'admin'. "
            "Change it with: python manage.py changepassword admin"
        ))
