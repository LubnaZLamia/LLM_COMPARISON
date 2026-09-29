from django.urls import path

from . import views

app_name = "comparisons"

urlpatterns = [
    path("", views.compare, name="compare"),
    path("runs/<int:pk>/", views.compare, name="run"),
    path("runs/<int:pk>/retry/", views.retry_run, name="retry"),
    path("runs/<int:pk>/download/", views.download_run, name="download"),
]
