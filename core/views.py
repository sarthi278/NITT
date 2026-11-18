from django.http import JsonResponse
from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib import messages
from django.db import IntegrityError
from django.db.models import Q
from django.utils import timezone
from django.db import connection
from decimal import Decimal
import uuid
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_POST

from core.forms import ItemConversionFormSetFactory, ItemForm, StockInwardForm
from core.models import (
    Customer,
    Item,
    Product,
    ProductionRun,
    Purchase,
    Supplier,
    StockInward,
    StockMovement,
)
from core.services.stock import balance
from core.services.items import save_item_with_conversions
from core.services.stock_inward import post_stock_inward
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
            item = get_object_or_404(
                Item.objects.filter(is_active=True),
                pk=request.POST["item"],
            )
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
                "items": Item.objects.filter(is_active=True).exclude(item_type="FINISHED"),
                "next_number": f"PUR-{Purchase.objects.count() + 1:04d}",
                "error": str(error),
            })
    return render(request, "purchase.html", {
        "suppliers": Supplier.objects.all(),
        "items": Item.objects.filter(is_active=True).exclude(item_type="FINISHED"),
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
    search = request.GET.get("q", "").strip()[:100]
    item_type = request.GET.get("item_type", "")
    item_queryset = Item.objects.all()
    if search:
        item_queryset = item_queryset.filter(
            Q(name__icontains=search) | Q(sku__icontains=search)
        )
    if item_type in Item.ItemType.values:
        item_queryset = item_queryset.filter(item_type=item_type)

    items = []
    for item in item_queryset.order_by("item_type", "name"):
        current_stock = balance(item)
        items.append({
            "item": item,
            "balance": current_stock,
            "is_low_stock": current_stock < item.reorder_level,
        })
    can_add_stock = request.user.is_superuser or request.user.groups.filter(
        name__in=["Production", "Admin", "Owner"]
    ).exists()
    return render(
        request,
        "stock.html",
        {
            "items": items,
            "can_add_stock": can_add_stock,
            "can_manage_items": can_add_stock,
            "has_any_items": Item.objects.exists(),
            "search": search,
            "item_type": item_type,
            "item_types": Item.ItemType.choices,
        },
    )


@role_allowed("Production", "Admin", "Owner")
def stock_inward_view(request):
    items = list(
        Item.objects.filter(is_active=True)
        .prefetch_related("conversions")
        .order_by("name")
    )
    item_metadata = {}
    for item in items:
        units = [conversion.unit for conversion in item.conversions.all()]
        if item.base_unit not in units:
            units.append(item.base_unit)
        item_metadata[item.pk] = {
            "sku": item.sku,
            "item_type": item.get_item_type_display(),
            "units": units,
        }

    if request.method == "POST":
        form = StockInwardForm(request.POST)
        if form.is_valid():
            data = form.cleaned_data
            try:
                inward, created = post_stock_inward(
                    submission_key=data["submission_key"],
                    item=data["item"],
                    display_quantity=data["quantity"],
                    unit=data["unit"],
                    unit_cost=data["unit_cost"],
                    entry_date=data["entry_date"],
                    user=request.user,
                    batch_no=data["batch_no"],
                    manufacturing_date=data["manufacturing_date"],
                    expiry_date=data["expiry_date"],
                    supplier=data["supplier"],
                    remarks=data["remarks"],
                )
            except IntegrityError:
                inward = StockInward.objects.filter(
                    submission_key=data["submission_key"]
                ).first()
                if inward is None:
                    raise
                created = False

            if created:
                messages.success(request, f"Stock entry #{inward.pk} was posted.")
            else:
                messages.info(request, "This stock submission was already processed.")
            return redirect("stock")
    else:
        form = StockInwardForm(initial={"submission_key": uuid.uuid4()})

    return render(
        request,
        "stock_inward.html",
        {"form": form, "items": items, "item_metadata": item_metadata},
    )


def _item_form_response(request, *, item=None):
    if request.method == "POST":
        item_form = ItemForm(request.POST, instance=item)
        item_is_valid = item_form.is_valid()
        item_instance = item_form.save(commit=False) if item_is_valid else item_form.instance
        conversion_formset = ItemConversionFormSetFactory(
            request.POST,
            instance=item_instance,
            prefix="conversions",
        )
        conversions_are_valid = conversion_formset.is_valid()

        if item_is_valid and conversions_are_valid:
            conversions = conversion_formset.save(commit=False)
            deleted_conversions = conversion_formset.deleted_objects
            saved_item = save_item_with_conversions(
                item_instance,
                conversions,
                deleted_conversions,
            )
            action = "updated" if item else "created"
            messages.success(request, f"Item {saved_item.name} was {action}.")
            return redirect("stock")
    else:
        item_form = ItemForm(instance=item)
        conversion_formset = ItemConversionFormSetFactory(
            instance=item_form.instance,
            prefix="conversions",
        )

    return render(
        request,
        "item_form.html",
        {
            "item_form": item_form,
            "conversion_formset": conversion_formset,
            "item": item,
        },
    )


@role_allowed("Production", "Admin", "Owner")
def item_create_view(request):
    return _item_form_response(request)


@role_allowed("Production", "Admin", "Owner")
def item_edit_view(request, item_id):
    item = get_object_or_404(Item, pk=item_id)
    return _item_form_response(request, item=item)


@role_allowed("Production", "Admin", "Owner")
@require_POST
def item_toggle_active_view(request, item_id):
    item = get_object_or_404(Item, pk=item_id)
    item.is_active = not item.is_active
    item.save(update_fields=["is_active"])
    status = "activated" if item.is_active else "deactivated"
    messages.success(request, f"Item {item.name} was {status}.")
    return redirect("stock")


@role_allowed("Production", "Admin", "Accountant", "Owner")
def ledger_view(request):
    movements = StockMovement.objects.select_related("item", "user").order_by("-timestamp", "-id")
    return render(request, "ledger.html", {"movements": movements})
