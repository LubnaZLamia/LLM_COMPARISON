from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("dataset/", include("apps.dataset.urls")),
    path("rate/", include("apps.ratings.urls")),
    path("help/", include("apps.core.urls")),
    path("", include("apps.comparisons.urls")),
]
