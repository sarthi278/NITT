import csv
from io import BytesIO
import uuid
from decimal import Decimal

from django.http import HttpResponse, JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import IntegrityError
from django.db.models import Q
from django.core.paginator import Paginator
from django.db import transaction
from django.utils import timezone
from django.db import connection
from django.views.decorators.http import require_POST
from django.views.decorators.http import require_GET
from django.conf import settings
from django.http import FileResponse
from core.audit import record_business_event

from core.forms import (
    BOMForm,
    BOMLineFormSetFactory,
    CustomerForm,
    CustomerPriceFormSetFactory,
    ItemConversionFormSetFactory,
    ItemForm,
    OrderLineFormSetFactory,
    PaymentForm,
    PriceListForm,
    PriceListItemFormSetFactory,
    ProductForm,
    ProductPackSizeFormSetFactory,
    ReportFilterForm,
    StockInwardForm,
    SupplierForm,
)
from core.models import (
    BOM,
    BOMLine,
    Customer,
    Dispatch,
    Invoice,
    Item,
    Order,
    PriceList,
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
from core.services.orders import approve_order, create_order, reject_order
from core.services.invoicing import create_invoice
from core.services.dispatch import dispatch_invoice, remaining_dispatch_quantities
from core.services.dispatch import available_for_dispatch
from core.services.payments import invoice_outstanding, record_payment
from core.services.credit_notes import issue_credit_note
from core.services.reports import build_report
from core.services.dashboard import (
    admin_dashboard_data,
    owner_dashboard_data,
    salesman_dashboard_data,
)
from core.services.pdf import build_invoice_pdf, build_report_pdf
from core.services.units import item_unit_multiplier


def health(request):
    return JsonResponse({"status": "ok", "database": connection.vendor})


def _spreadsheet_safe(value):
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
        return f"'{value}"
    return value


@require_GET
def service_worker(request):
    worker = settings.BASE_DIR / "static" / "service-worker.js"
    response = FileResponse(
        open(worker, "rb"),
        content_type="application/javascript",
    )
    response["Service-Worker-Allowed"] = "/"
    return response


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
    roles = set(request.user.groups.values_list("name", flat=True))
    if "Owner" in roles or request.user.is_superuser:
        return render(
            request,
            "owner_dashboard.html",
            {"role": "Owner", **owner_dashboard_data()},
        )
    if "Admin" in roles:
        return render(
            request,
            "dashboard.html",
            {"role": "Admin", **admin_dashboard_data()},
        )
    if "Salesman" in roles:
        return render(
            request,
            "dashboard.html",
            {"role": "Salesman", **salesman_dashboard_data(request.user)},
        )
    if "Accountant" in roles:
        return render(request, "dashboard.html", {"role": "Accountant"})
    if "Production" in roles:
        return redirect("production")
    return JsonResponse({"detail": "Forbidden"}, status=403)


@role_allowed("Production", "Admin", "Owner")
def purchase_view(request):
    if request.method == "POST":
        try:
            supplier = Supplier.objects.get(pk=request.POST["supplier"])
            if not supplier.is_active:
                raise ValueError("This supplier is inactive.")
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
                "suppliers": Supplier.objects.filter(is_active=True),
                "items": Item.objects.filter(is_active=True).exclude(item_type="FINISHED"),
                "next_number": f"PUR-{Purchase.objects.count() + 1:04d}",
                "error": str(error),
            })
    return render(request, "purchase.html", {
        "suppliers": Supplier.objects.filter(is_active=True),
        "items": Item.objects.filter(is_active=True).exclude(item_type="FINISHED"),
        "next_number": f"PUR-{Purchase.objects.count() + 1:04d}",
    })


@role_allowed("Production", "Admin", "Owner")
def production_view(request):
    product_id = request.GET.get("product") or request.POST.get("product")
    if request.method == "POST":
        try:
            product = get_object_or_404(Product, pk=request.POST.get("product"))
            try:
                bom_lines = product.bom.lines.select_related("item").order_by("pk")
            except BOM.DoesNotExist:
                bom_lines = []
            run, consumptions, yield_variance, yield_flagged = post_production_run(
                product=product,
                batch_count=request.POST.get("batch_count", "1"),
                actual_good_output=request.POST.get("actual_good_output"),
                labour_cost=request.POST.get("labour_cost", "0"),
                power_gas_cost=request.POST.get("power_gas_cost", "0"),
                actual_quantities={
                    line.pk: request.POST.get(f"actual_{line.pk}")
                    for line in bom_lines
                },
                wastage_quantities={
                    line.pk: request.POST.get(f"wastage_{line.pk}", "0")
                    for line in bom_lines
                },
                run_date=timezone.localdate(),
                number=request.POST.get("number", "").strip(),
                user=request.user,
            )
            context = production_context(product.pk)
            context.update({
                "message": f"Posted {run.number}: yield variance {yield_variance}%"
                          + (" - flagged" if yield_flagged else ""),
                "consumptions": consumptions,
            })
            return render(request, "production.html", context)
        except IntegrityError:
            error = "That production run number is already in use."
        except (KeyError, ValueError) as error:
            error = str(error)
        context = production_context(product_id)
        context["error"] = error
        return render(request, "production.html", context)
    return render(request, "production.html", production_context(product_id))


def production_context(product_id=None):
    products = Product.objects.filter(
        is_active=True,
        finished_item__is_active=True,
    ).select_related("finished_item").order_by("name")
    try:
        selected_product = products.filter(pk=product_id).first() if product_id else None
    except (TypeError, ValueError):
        selected_product = None
    if selected_product is None:
        selected_product = products.first()

    bom_lines = []
    if selected_product is not None:
        try:
            bom_lines = list(
                selected_product.bom.lines.select_related("item").order_by("pk")
            )
        except BOM.DoesNotExist:
            pass

    has_bom = selected_product is not None and bool(bom_lines)
    return {
        "products": products,
        "selected_product": selected_product,
        "bom_lines": bom_lines,
        "has_bom": has_bom,
        "runs": ProductionRun.objects.select_related("product").order_by("-id"),
        "next_number": f"RUN-{ProductionRun.objects.count() + 1:04d}",
    }


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


def _management_page(queryset, request):
    paginator = Paginator(queryset, 20)
    return paginator.get_page(request.GET.get("page"))


@role_allowed("Admin", "Owner")
def products_view(request):
    search = request.GET.get("q", "").strip()[:100]
    status = request.GET.get("status", "")
    products = Product.objects.select_related("finished_item").prefetch_related(
        "pack_sizes", "bom__lines__item",
    )
    if search:
        products = products.filter(Q(name__icontains=search) | Q(sku__icontains=search))
    if status in ("active", "inactive"):
        products = products.filter(is_active=(status == "active"))
    return render(request, "products.html", {
        "products": _management_page(products.order_by("name"), request),
        "search": search,
        "status": status,
    })


@role_allowed("Admin", "Owner")
def product_create_view(request):
    return _product_form_response(request)


@role_allowed("Admin", "Owner")
def product_edit_view(request, product_id):
    product = get_object_or_404(Product, pk=product_id)
    return _product_form_response(request, product=product)


@role_allowed("Admin", "Owner")
@require_POST
def product_toggle_active_view(request, product_id):
    product = get_object_or_404(Product, pk=product_id)
    product.is_active = not product.is_active
    product.save(update_fields=["is_active"])
    messages.success(
        request,
        f"Product {product.name} was {'activated' if product.is_active else 'deactivated'}.",
    )
    return redirect("products")


def _product_form_response(request, *, product=None):
    existing_bom = None
    if product:
        try:
            existing_bom = product.bom
        except BOM.DoesNotExist:
            pass
    bom = existing_bom or BOM()

    if request.method == "POST":
        template_lines = []
        product_form = ProductForm(request.POST, instance=product)
        bom_form = BOMForm(request.POST, instance=bom)
        line_formset = BOMLineFormSetFactory(
            request.POST, instance=bom, prefix="bom_lines",
        )
        pack_formset = ProductPackSizeFormSetFactory(
            request.POST,
            instance=product,
            prefix="pack_sizes",
        )
        product_valid = product_form.is_valid()
        bom_valid = bom_form.is_valid()
        lines_valid = line_formset.is_valid()
        packs_valid = pack_formset.is_valid()
        template = (
            product_form.cleaned_data.get("bom_template")
            if product_valid
            else None
        )
        if template:
            template_lines = list(
                template.components.select_related("item").order_by("pk")
            )
            for template_line in template_lines:
                try:
                    if template_line.qty_per_batch <= 0:
                        raise ValueError("Component quantities must be positive.")
                    item_unit_multiplier(template_line.item, template_line.unit)
                except ValueError as error:
                    line_formset.add_formset_error(str(error))
                    lines_valid = False
                    break
        has_manual_lines = lines_valid and any(
            form.cleaned_data.get("item")
            and not form.cleaned_data.get("DELETE")
            for form in line_formset.forms
        )
        has_template_lines = bool(template_lines)
        has_bom_source = has_template_lines if template else has_manual_lines
        if lines_valid and not has_bom_source:
            line_formset.add_formset_error(
                "Add a BOM line or choose a template that has BOM lines.",
            )
            lines_valid = False
        has_pack_sizes = packs_valid and any(
            form.cleaned_data.get("name")
            and not form.cleaned_data.get("DELETE")
            for form in pack_formset.forms
        )
        if packs_valid and not has_pack_sizes:
            pack_formset.add_error(None, "Add at least one pack-size variant.")
            packs_valid = False

        if product_valid and bom_valid and lines_valid and packs_valid:
            with transaction.atomic():
                saved_product = product_form.save()
                pack_formset.instance = saved_product
                pack_formset.save()

                saved_bom = bom_form.save(commit=False)
                saved_bom.product = saved_product
                saved_bom.save()
                if template:
                    saved_bom.lines.all().delete()
                    BOMLine.objects.bulk_create([
                        BOMLine(
                            bom=saved_bom,
                            item=line.item,
                            qty_per_batch=line.qty_per_batch,
                            unit=line.unit,
                        )
                        for line in template_lines
                    ])
                    record_business_event(
                        action="TEMPLATE_APPLIED",
                        entity_type="BOM",
                        entity_id=saved_bom.pk,
                        user=request.user,
                        source_doc_type="PRODUCT",
                        source_doc_id=saved_product.pk,
                        details={"template": template.name},
                    )
                else:
                    line_formset.instance = saved_bom
                    line_formset.save()
            messages.success(request, f"Product {saved_product.name} was saved.")
            return redirect("products")
    else:
        product_form = ProductForm(instance=product)
        bom_form = BOMForm(instance=bom)
        line_formset = BOMLineFormSetFactory(instance=bom, prefix="bom_lines")
        pack_formset = ProductPackSizeFormSetFactory(
            instance=product,
            prefix="pack_sizes",
        )

    return render(request, "product_form.html", {
        "product": product,
        "product_form": product_form,
        "bom_form": bom_form,
        "line_formset": line_formset,
        "pack_formset": pack_formset,
    })


@role_allowed("Admin", "Owner")
def customers_view(request):
    search = request.GET.get("q", "").strip()[:100]
    state = request.GET.get("state", "").strip()[:80]
    customer_type = request.GET.get("customer_type", "")
    customers = Customer.objects.select_related(
        "assigned_salesman", "price_list",
    )
    if search:
        customers = customers.filter(
            Q(name__icontains=search)
            | Q(gstin__icontains=search)
            | Q(address__icontains=search)
        )
    if state:
        customers = customers.filter(state__icontains=state)
    if customer_type in Customer.CustomerType.values:
        customers = customers.filter(customer_type=customer_type)
    return render(request, "customers.html", {
        "customers": _management_page(customers.order_by("name"), request),
        "search": search,
        "state": state,
        "customer_type": customer_type,
        "customer_types": Customer.CustomerType.choices,
    })


@role_allowed("Admin", "Owner")
def customer_create_view(request):
    return _customer_form_response(request)


@role_allowed("Admin", "Owner")
def customer_edit_view(request, customer_id):
    customer = get_object_or_404(Customer, pk=customer_id)
    return _customer_form_response(request, customer=customer)


def _customer_form_response(request, *, customer=None):
    if request.method == "POST":
        form = CustomerForm(request.POST, instance=customer)
        price_formset = CustomerPriceFormSetFactory(
            request.POST, instance=customer, prefix="prices",
        )
        if form.is_valid() and price_formset.is_valid():
            with transaction.atomic():
                saved_customer = form.save()
                price_formset.instance = saved_customer
                price_formset.save()
            messages.success(request, f"Customer {saved_customer.name} was saved.")
            return redirect("customers")
    else:
        form = CustomerForm(instance=customer)
        price_formset = CustomerPriceFormSetFactory(
            instance=customer, prefix="prices",
        )
    return render(request, "customer_form.html", {
        "customer": customer,
        "form": form,
        "price_formset": price_formset,
    })


@role_allowed("Admin", "Owner")
def suppliers_view(request):
    search = request.GET.get("q", "").strip()[:100]
    state = request.GET.get("state", "").strip()[:80]
    suppliers = Supplier.objects.all()
    if search:
        suppliers = suppliers.filter(
            Q(name__icontains=search)
            | Q(gstin__icontains=search)
            | Q(address__icontains=search)
        )
    if state:
        suppliers = suppliers.filter(state__icontains=state)
    return render(request, "suppliers.html", {
        "suppliers": _management_page(suppliers.order_by("name"), request),
        "search": search,
        "state": state,
    })


@role_allowed("Admin", "Owner")
def supplier_create_view(request):
    return _supplier_form_response(request)


@role_allowed("Admin", "Owner")
def supplier_edit_view(request, supplier_id):
    supplier = get_object_or_404(Supplier, pk=supplier_id)
    return _supplier_form_response(request, supplier=supplier)


def _supplier_form_response(request, *, supplier=None):
    form = SupplierForm(request.POST or None, instance=supplier)
    if request.method == "POST" and form.is_valid():
        saved_supplier = form.save()
        messages.success(request, f"Supplier {saved_supplier.name} was saved.")
        return redirect("suppliers")
    return render(request, "supplier_form.html", {
        "supplier": supplier,
        "form": form,
    })


@role_allowed("Admin", "Owner")
def price_lists_view(request):
    return render(request, "price_lists.html", {
        "price_lists": _management_page(
            PriceList.objects.prefetch_related("items__product").order_by("name"),
            request,
        ),
    })


@role_allowed("Admin", "Owner")
def price_list_create_view(request):
    return _price_list_form_response(request)


@role_allowed("Admin", "Owner")
def price_list_edit_view(request, price_list_id):
    price_list = get_object_or_404(PriceList, pk=price_list_id)
    return _price_list_form_response(request, price_list=price_list)


def _price_list_form_response(request, *, price_list=None):
    if request.method == "POST":
        form = PriceListForm(request.POST, instance=price_list)
        item_formset = PriceListItemFormSetFactory(
            request.POST, instance=price_list, prefix="items",
        )
        if form.is_valid() and item_formset.is_valid():
            with transaction.atomic():
                saved_price_list = form.save()
                item_formset.instance = saved_price_list
                item_formset.save()
            messages.success(request, f"Price list {saved_price_list.name} was saved.")
            return redirect("price_lists")
    else:
        form = PriceListForm(instance=price_list)
        item_formset = PriceListItemFormSetFactory(
            instance=price_list, prefix="items",
        )
    return render(request, "price_list_form.html", {
        "price_list": price_list,
        "form": form,
        "item_formset": item_formset,
    })


@role_allowed("Production", "Admin", "Accountant", "Owner")
def ledger_view(request):
    movements = StockMovement.objects.select_related("item", "user").order_by("-timestamp", "-id")
    return render(request, "ledger.html", {"movements": movements})


@role_allowed("Salesman")
def order_create_view(request):
    customers = Customer.objects.filter(
        assigned_salesman=request.user,
        is_active=True,
    ).order_by("name")
    if request.user.is_superuser:
        customers = Customer.objects.filter(is_active=True).order_by("name")

    if request.method == "POST":
        formset = OrderLineFormSetFactory(request.POST, prefix="lines")
        try:
            customer = customers.filter(pk=request.POST.get("customer")).first()
        except (TypeError, ValueError):
            customer = None
        if formset.is_valid() and customer:
            submitted_lines = [
                form.cleaned_data
                for form in formset.forms
                if form.cleaned_data.get("product")
            ]
            try:
                order, created = create_order(
                    customer=customer,
                    created_by=request.user,
                    lines=submitted_lines,
                    client_uuid=request.POST.get("client_uuid"),
                    notes=request.POST.get("notes", ""),
                )
                if created:
                    messages.success(request, f"Order #{order.pk} was submitted.")
                else:
                    messages.info(request, "This offline order was already synchronized.")
                return redirect("sales_orders")
            except ValueError as error:
                formset.add_error(None, str(error))
        elif not customer:
            messages.error(request, "Choose one of your assigned customers.")
    else:
        formset = OrderLineFormSetFactory(prefix="lines")

    return render(request, "order_form.html", {
        "customers": customers,
        "formset": formset,
        "client_uuid": request.POST.get("client_uuid") or uuid.uuid4(),
    })


@login_required
def sales_orders_view(request):
    groups = set(request.user.groups.values_list("name", flat=True))
    can_view_all = request.user.is_superuser or bool(
        groups.intersection({"Admin", "Owner", "Accountant"})
    )
    if not can_view_all and "Salesman" not in groups:
        return JsonResponse({"detail": "Forbidden"}, status=403)

    orders = Order.objects.select_related(
        "customer",
        "created_by",
    ).prefetch_related("lines__product").order_by("-submitted_at")
    if not can_view_all:
        orders = orders.filter(created_by=request.user)
    return render(request, "orders.html", {
        "orders": orders,
        "can_review_orders": request.user.is_superuser
        or bool(groups.intersection({"Admin", "Owner"})),
        "can_create_orders": "Salesman" in groups or request.user.is_superuser,
    })


@role_allowed("Admin", "Owner")
def order_review_view(request, order_id):
    order = get_object_or_404(
        Order.objects.select_related("customer", "created_by").prefetch_related(
            "lines__product__finished_item"
        ),
        pk=order_id,
    )
    error = None
    if request.method == "POST":
        try:
            if request.POST.get("action") == "reject":
                reject_order(order=order, rejected_by=request.user)
                messages.success(request, f"Order #{order.pk} was rejected.")
                return redirect("sales_orders")
            if request.POST.get("action") != "approve":
                raise ValueError("Choose approve or reject.")
            quantities = {
                line.pk: request.POST.get(f"approved_{line.pk}", "0")
                for line in order.lines.all()
            }
            approved_order = approve_order(
                order=order,
                approved_quantities=quantities,
                approved_by=request.user,
            )
            if approved_order.status == Order.Status.SUBMITTED:
                messages.info(request, f"Order #{order.pk} was held with no approved quantity.")
            else:
                messages.success(request, f"Order #{order.pk} was approved.")
            return redirect("sales_orders")
        except ValueError as exception:
            error = str(exception)

    lines = []
    for line in order.lines.select_related("product__finished_item"):
        available = (
            available_for_dispatch(
                item=line.product.finished_item,
                dispatch_date=timezone.localdate(),
            )
            if line.product.finished_item_id
            else Decimal("0")
        )
        lines.append({
            "line": line,
            "available": available,
            "shortage": max(line.quantity - available, Decimal("0")),
        })
    return render(request, "order_review.html", {
        "order": order,
        "lines": lines,
        "error": error,
    })


@role_allowed("Admin", "Owner")
@require_POST
def order_invoice_view(request, order_id):
    order = get_object_or_404(Order, pk=order_id)
    try:
        invoice = create_invoice(
            order=order,
            posted_by=request.user,
            invoice_date=timezone.localdate(),
        )
    except ValueError as error:
        messages.error(request, str(error))
        return redirect("sales_orders")
    messages.success(request, f"Invoice {invoice.number} was posted.")
    return redirect("invoice_detail", invoice_id=invoice.pk)


@login_required
def invoice_detail_view(request, invoice_id):
    invoice = get_object_or_404(
        Invoice.objects.select_related(
            "customer",
            "order__created_by",
            "posted_by",
        ).prefetch_related("lines__product", "payments"),
        pk=invoice_id,
    )
    groups = set(request.user.groups.values_list("name", flat=True))
    can_view_all = request.user.is_superuser or bool(
        groups.intersection({"Admin", "Owner", "Accountant"})
    )
    if "Salesman" not in groups and not can_view_all:
        return JsonResponse({"detail": "Forbidden"}, status=403)
    if not can_view_all and invoice.order.created_by_id != request.user.pk:
        return JsonResponse({"detail": "Forbidden"}, status=403)
    remaining = remaining_dispatch_quantities(invoice)
    dispatch_lines = [
        {"line": line, "remaining": remaining[line.pk]}
        for line in invoice.lines.all()
        if remaining[line.pk] > Decimal("0")
    ]
    dispatches = Dispatch.objects.filter(invoice=invoice).prefetch_related(
        "lines__invoice_line",
    ).order_by("dispatch_date", "pk")
    has_dispatch = dispatches.exists()
    return render(request, "invoice_detail.html", {
        "invoice": invoice,
        "can_dispatch": request.user.is_superuser
        or bool(groups.intersection({"Admin", "Owner"})),
        "can_credit": request.user.is_superuser
        or bool(groups.intersection({"Admin", "Owner"})),
        "has_dispatch": has_dispatch,
        "dispatches": dispatches,
        "dispatch_lines": dispatch_lines,
        "dispatch_submission_key": uuid.uuid4(),
        "credit_note": getattr(invoice, "credit_note", None),
        "outstanding": invoice_outstanding(invoice),
    })


@role_allowed("Admin", "Owner")
@require_POST
def invoice_dispatch_view(request, invoice_id):
    invoice = get_object_or_404(Invoice.objects.prefetch_related("lines"), pk=invoice_id)
    try:
        dispatch, created = dispatch_invoice(
            invoice=invoice,
            dispatched_by=request.user,
            dispatch_date=timezone.localdate(),
            dispatch_quantities={
                str(line.pk): request.POST.get(f"quantity_{line.pk}", "0")
                for line in invoice.lines.all()
            },
            submission_key=request.POST.get("submission_key"),
        )
    except ValueError as error:
        messages.error(request, str(error))
    else:
        if created:
            messages.success(request, f"Dispatch #{dispatch.pk} was posted.")
        else:
            messages.info(request, "This invoice was already dispatched.")
    return redirect("invoice_detail", invoice_id=invoice.pk)


@login_required
def payments_view(request):
    groups = set(request.user.groups.values_list("name", flat=True))
    can_record_payment = request.user.is_superuser or bool(
        groups.intersection({"Admin", "Owner"})
    )
    can_view_payments = can_record_payment or "Accountant" in groups
    if not can_view_payments:
        return JsonResponse({"detail": "Forbidden"}, status=403)
    if request.method == "POST" and not can_record_payment:
        return JsonResponse({"detail": "Forbidden"}, status=403)

    invoices = Invoice.objects.filter(
        status=Invoice.Status.POSTED,
    ).select_related(
        "customer",
    ).prefetch_related("payments").order_by("invoice_date", "number")
    invoice_rows = []
    eligible_invoice_ids = []
    for invoice in invoices:
        outstanding = invoice_outstanding(invoice)
        if outstanding > Decimal("0"):
            eligible_invoice_ids.append(invoice.pk)
            invoice_rows.append({"invoice": invoice, "outstanding": outstanding})

    if request.method == "POST":
        form = PaymentForm(request.POST)
        form.fields["invoice"].queryset = Invoice.objects.filter(
            pk__in=eligible_invoice_ids,
        ).select_related("customer")
        if form.is_valid():
            data = form.cleaned_data
            try:
                payment = record_payment(
                    invoice=data["invoice"],
                    amount=data["amount"],
                    payment_date=data["payment_date"],
                    method=data["method"],
                    reference=data["reference"],
                    received_by=request.user,
                )
                messages.success(request, f"Payment of ₹{payment.amount} was recorded.")
                return redirect("payments")
            except ValueError as error:
                form.add_error(None, str(error))
    else:
        form = PaymentForm()
        form.fields["invoice"].queryset = Invoice.objects.filter(
            pk__in=eligible_invoice_ids,
        ).select_related("customer")
    return render(request, "payments.html", {
        "form": form,
        "invoice_rows": invoice_rows,
        "can_record_payment": can_record_payment,
    })


@role_allowed("Admin", "Owner")
@require_POST
def invoice_credit_note_view(request, invoice_id):
    invoice = get_object_or_404(Invoice, pk=invoice_id)
    try:
        credit_note = issue_credit_note(
            invoice=invoice,
            issue_date=timezone.localdate(),
            reason=request.POST.get("reason", ""),
            created_by=request.user,
        )
    except ValueError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, f"Credit note {credit_note.number} was issued.")
    return redirect("invoice_detail", invoice_id=invoice.pk)


@login_required
def reports_view(request):
    groups = set(request.user.groups.values_list("name", flat=True))
    if not request.user.is_superuser and not groups.intersection(
        {"Admin", "Owner", "Accountant"}
    ):
        return JsonResponse({"detail": "Forbidden"}, status=403)

    form = ReportFilterForm(request.GET or None)
    if not request.GET or not form.is_valid():
        return render(request, "reports.html", {"form": form})

    data = form.cleaned_data
    title, columns, rows = build_report(
        data["report"],
        data["start_date"],
        data["end_date"],
    )
    filename = data["report"]
    if data["export"] == "csv":
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="{filename}.csv"'
        writer = csv.writer(response)
        writer.writerow([_spreadsheet_safe(value) for value in columns])
        writer.writerows([
            [_spreadsheet_safe(value) for value in row]
            for row in rows
        ])
        return response
    if data["export"] == "xlsx":
        from openpyxl import Workbook

        workbook = Workbook()
        worksheet = workbook.active
        worksheet.title = title[:31]
        worksheet.append([_spreadsheet_safe(value) for value in columns])
        for row in rows:
            worksheet.append([_spreadsheet_safe(value) for value in row])
        content = BytesIO()
        workbook.save(content)
        response = HttpResponse(
            content.getvalue(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = f'attachment; filename="{filename}.xlsx"'
        return response
    if data["export"] == "pdf":
        pdf = build_report_pdf(
            title=title,
            columns=columns,
            rows=rows,
            start_date=data["start_date"],
            end_date=data["end_date"],
        )
        response = HttpResponse(pdf, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{filename}.pdf"'
        return response
    return render(request, "reports.html", {
        "form": form,
        "title": title,
        "columns": columns,
        "rows": rows,
    })


@login_required
def invoice_pdf_view(request, invoice_id):
    invoice = get_object_or_404(
        Invoice.objects.select_related("customer", "order__created_by").prefetch_related(
            "lines",
        ),
        pk=invoice_id,
    )
    groups = set(request.user.groups.values_list("name", flat=True))
    can_view_all = request.user.is_superuser or bool(
        groups.intersection({"Admin", "Owner", "Accountant"})
    )
    if "Salesman" not in groups and not can_view_all:
        return JsonResponse({"detail": "Forbidden"}, status=403)
    if not can_view_all and invoice.order.created_by_id != request.user.pk:
        return JsonResponse({"detail": "Forbidden"}, status=403)

    pdf = build_invoice_pdf(invoice)
    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = (
        f'inline; filename="{invoice.number.replace("/", "-")}.pdf"'
    )
    return response
