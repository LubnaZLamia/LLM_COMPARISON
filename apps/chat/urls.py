from django.urls import path

from . import views

app_name = "chat"

urlpatterns = [
    path("", views.chat, name="new"),
    path("send/", views.send, name="send"),
    path("<int:pk>/", views.chat, name="conversation"),
    path("<int:pk>/regenerate/", views.regenerate, name="regenerate"),
    path("<int:pk>/delete/", views.delete, name="delete"),
    path("<int:pk>/export/", views.export, name="export"),
]
