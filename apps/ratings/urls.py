from django.urls import path

from . import views

app_name = "ratings"

urlpatterns = [
    path("", views.run_list, name="list"),
    path("<int:pk>/", views.rate_run, name="rate"),
    path("<int:pk>/done/", views.done, name="done"),
    path("download/", views.download_ratings, name="download"),
]
