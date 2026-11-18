from django.contrib import admin
from django.urls import path
from core.views import health, home

urlpatterns = [
    path("admin/", admin.site.urls),
    path("health/", health),
    path("", home),
]
