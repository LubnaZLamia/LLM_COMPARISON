from django.urls import path

from . import views

app_name = "annotations"

urlpatterns = [
    path("", views.home, name="home"),
    path("next/", views.next_item, name="next"),
    path("back/", views.back, name="back"),
    path("item/<int:pk>/", views.annotate, name="item"),
    path("results/", views.results, name="results"),
    path("download/", views.download, name="download"),
    path("download/items/", views.download_items, name="download_items"),
    path("download/parts/", views.download_units, name="download_units"),
]
