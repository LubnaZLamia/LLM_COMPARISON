from django.urls import path

from .views import help_page

app_name = "core"

urlpatterns = [
    path("", help_page, name="help"),
]
