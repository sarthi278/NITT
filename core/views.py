from django.http import JsonResponse
from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.utils import timezone
from django.db import connection
from decimal import Decimal

from core.models import Customer, Item, Product, ProductionRun, Purchase, Supplier, StockMovement
from core.services.stock import balance
from core.services.purchasing import post_purchase
from core.services.production import post_production_run


def health(request):
    return JsonResponse({"status": "ok", "database": connection.vendor})


def home(request):
    if not request.user.is_authenticated:
        return redirect("login")
    return redirect("dashboard")


def role_allowed(*roles):
    def decorator(view):
        @login_required
        def wrapped(request, *args, **kwargs):
            if request.user.groups.filter(name__in=roles).exists() or request.user.is_superuser:
                return view(request, *args, **kwargs)
            return JsonResponse({"detail": "Forbidden"}, status=403)
        return wrapped
    return decorator


def login_view(request):
    if request.method == "POST":
        user = authenticate(
            request,
            username=request.POST.get("username", "").strip(),
            password=request.POST.get("password", ""),
        )
        if user:
            login(request, user)
            return redirect("dashboard")
        return render(request, "login.html", {"error": "Invalid username or password"})
    return render(request, "login.html")


def logout_view(request):
    logout(request)
    return redirect("login")


@login_required
def dashboard(request):
    role = next((g.name for g in request.user.groups.all()), "")
    if role == "Production" or request.user.is_superuser:
        return redirect("production")
    return render(request, "dashboard.html", {"role": role})


@role_allowed("Production", "Admin", "Owner")
def purchase_view(request):
    if request.method == "POST":
        try:
            supplier = Supplier.objects.get(pk=request.POST["supplier"])
            item = Item.objects.get(pk=request.POST["item"])
            post_purchase(
                supplier=supplier,
                item=item,
                display_quantity=Decimal(request.POST["quantity"]),
                unit=request.POST["unit"],
                unit_cost=Decimal(request.POST["unit_cost"]),
                purchase_date=timezone.localdate(),
                number=request.POST["number"].strip(),
                user=request.user,
            )
            return redirect("purchase")
        except (KeyError, ValueError) as error:
            return render(request, "purchase.html", {
                "suppliers": Supplier.objects.all(),
                "items": Item.objects.exclude(item_type="FINISHED"),
                "next_number": f"PUR-{Purchase.objects.count() + 1:04d}",
                "error": str(error),
            })
    return render(request, "purchase.html", {
        "suppliers": Supplier.objects.all(),
        "items": Item.objects.exclude(item_type="FINISHED"),
        "next_number": f"PUR-{Purchase.objects.count() + 1:04d}",
    })


@role_allowed("Production", "Admin", "Owner")
def production_view(request):
    if request.method == "POST":
        try:
            run, consumptions, yield_variance, yield_flagged = post_production_run(
                product=Product.objects.get(pk=request.POST["product"]),
                batch_count=Decimal(request.POST.get("batch_count", "1")),
                actual_good_output=int(request.POST["actual_good_output"]),
                damaged_pouches=int(request.POST.get("damaged_pouches", "0")),
                labour_cost=Decimal(request.POST.get("labour_cost", "0")),
                power_gas_cost=Decimal(request.POST.get("power_gas_cost", "0")),
                actual_quantities={
                    "RM-FLOUR": Decimal(request.POST["flour"]),
                    "RM-OIL": Decimal(request.POST["oil"]),
                    "RM-SPICE": Decimal(request.POST["spice"]),
                    "PK-POUCH": Decimal(request.POST["pouches"]),
                    "PK-CARTON": Decimal(request.POST["cartons"]),
                },
                run_date=timezone.localdate(),
                number=request.POST["number"].strip(),
                user=request.user,
            )
            return render(request, "production.html", {
                "products": Product.objects.all(),
                "runs": ProductionRun.objects.select_related("product").order_by("-id"),
                "next_number": f"RUN-{ProductionRun.objects.count() + 1:04d}",
                "message": f"Posted {run.number}: yield variance {yield_variance}%"
                          + (" - flagged" if yield_flagged else ""),
                "consumptions": consumptions,
            })
        except (KeyError, ValueError) as error:
            return render(request, "production.html", {
                "products": Product.objects.all(),
                "runs": ProductionRun.objects.select_related("product").order_by("-id"),
                "next_number": f"RUN-{ProductionRun.objects.count() + 1:04d}",
                "error": str(error),
            })
    return render(request, "production.html", {
        "products": Product.objects.all(),
        "runs": ProductionRun.objects.select_related("product").order_by("-id"),
        "next_number": f"RUN-{ProductionRun.objects.count() + 1:04d}",
    })


@role_allowed("Production", "Admin", "Accountant", "Owner")
def stock_view(request):
    items = []
    for item in Item.objects.all().order_by("item_type", "name"):
        items.append({"item": item, "balance": balance(item)})
    return render(request, "stock.html", {"items": items})


@role_allowed("Production", "Admin", "Accountant", "Owner")
def ledger_view(request):
    movements = StockMovement.objects.select_related("item", "user").order_by("-timestamp", "-id")
    return render(request, "ledger.html", {"movements": movements})
