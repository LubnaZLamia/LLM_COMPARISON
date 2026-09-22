from django.urls import path

from . import views

app_name = "dataset"

urlpatterns = [
    path("", views.MessageListView.as_view(), name="list"),
    path("new/", views.MessageCreateView.as_view(), name="create"),
    path("<int:pk>/edit/", views.MessageUpdateView.as_view(), name="edit"),
    path("<int:pk>/delete/", views.MessageDeleteView.as_view(), name="delete"),
    path("import/", views.import_csv, name="import"),
    path("export/", views.export_csv, name="export"),
]
