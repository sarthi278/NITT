from django.contrib import admin
from django.urls import path
from core.views import health, home, login_view, logout_view, dashboard
from core.views import purchase_view, production_view, stock_view, ledger_view

urlpatterns = [
    path("admin/", admin.site.urls),
    path("health/", health),
    path("login/", login_view, name="login"),
    path("logout/", logout_view, name="logout"),
    path("dashboard/", dashboard, name="dashboard"),
    path("purchase/", purchase_view, name="purchase"),
    path("production/", production_view, name="production"),
    path("stock/", stock_view, name="stock"),
    path("ledger/", ledger_view, name="ledger"),
    path("", home),
]
