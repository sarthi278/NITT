from datetime import date
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from core.models import (
    Customer,
    Invoice,
    Item,
    Order,
    ProductionConsumption,
    ProductionRun,
)
from core.services.stock import balance

ZERO = Decimal("0")


def _month_start(current_date, offset=0):
    month_index = current_date.year * 12 + current_date.month - 1 + offset
    return date(month_index // 12, month_index % 12 + 1, 1)


def _monthly_series(today):
    sales = []
    production = []
    for offset in range(-5, 1):
        start = _month_start(today, offset)
        end = _month_start(today, offset + 1)
        taxable_sales = Invoice.objects.filter(
            status=Invoice.Status.POSTED,
            invoice_date__gte=start,
            invoice_date__lt=end,
        ).aggregate(total=Sum("taxable_total"))["total"] or ZERO
        good_output = ProductionRun.objects.filter(
            run_date__gte=start,
            run_date__lt=end,
        ).aggregate(total=Sum("actual_good_output"))["total"] or 0
        label = start.strftime("%b %Y")
        sales.append({"label": label, "amount": taxable_sales})
        production.append({"label": label, "amount": good_output})

    max_sales = max((row["amount"] for row in sales), default=ZERO)
    max_production = max((row["amount"] for row in production), default=0)
    for row in sales:
        row["bar_pct"] = (
            (row["amount"] / max_sales * Decimal("100")).quantize(Decimal("0.1"))
            if max_sales
            else ZERO
        )
    for row in production:
        row["bar_pct"] = (
            (Decimal(row["amount"]) / Decimal(max_production) * Decimal("100"))
            .quantize(Decimal("0.1"))
            if max_production
            else ZERO
        )
    return sales, production


def owner_dashboard_data():
    today = timezone.localdate()
    stock_rows = []
    for item in Item.objects.filter(is_active=True).order_by("item_type", "name"):
        current_balance = balance(item)
        stock_rows.append({
            "item": item,
            "balance": current_balance,
            "is_low": current_balance < item.reorder_level,
        })

    monthly_sales, monthly_production = _monthly_series(today)
    today_sales = Invoice.objects.filter(
        status=Invoice.Status.POSTED,
        invoice_date=today,
    ).aggregate(total=Sum("taxable_total"))["total"] or ZERO
    outstanding_total = ZERO
    for invoice in Invoice.objects.filter(
        status=Invoice.Status.POSTED,
    ).prefetch_related("payments"):
        paid = sum((payment.amount for payment in invoice.payments.all()), ZERO)
        outstanding_total += max(invoice.total - paid, ZERO)
    latest_runs = list(
        ProductionRun.objects.select_related("product").order_by("-run_date", "-pk")[:5]
    )
    return {
        "today_orders": Order.objects.filter(submitted_at__date=today).count(),
        "pending_orders": Order.objects.filter(status=Order.Status.SUBMITTED).count(),
        "today_sales": today_sales,
        "monthly_revenue": monthly_sales[-1]["amount"],
        "today_production": ProductionRun.objects.filter(
            run_date=today,
        ).aggregate(total=Sum("actual_good_output"))["total"] or 0,
        "monthly_sales": monthly_sales,
        "monthly_production": monthly_production,
        "stock_rows": stock_rows,
        "low_stock_count": sum(1 for row in stock_rows if row["is_low"]),
        "variance_alerts": ProductionConsumption.objects.filter(
            flagged=True,
            run__run_date=today,
        ).count(),
        "latest_runs": latest_runs,
        "outstanding_total": outstanding_total,
    }


def admin_dashboard_data():
    today = timezone.localdate()
    return {
        "today_orders": Order.objects.filter(submitted_at__date=today).count(),
        "pending_orders": Order.objects.filter(status=Order.Status.SUBMITTED).count(),
        "today_invoices": Invoice.objects.filter(
            status=Invoice.Status.POSTED,
            invoice_date=today,
        ).count(),
    }


def salesman_dashboard_data(user):
    today = timezone.localdate()
    month_start = _month_start(today)
    orders = Order.objects.filter(created_by=user)
    monthly_sales = Invoice.objects.filter(
        order__created_by=user,
        status=Invoice.Status.POSTED,
        invoice_date__gte=month_start,
        invoice_date__lte=today,
    ).aggregate(total=Sum("taxable_total"))["total"] or ZERO
    return {
        "assigned_customer_count": Customer.objects.filter(
            assigned_salesman=user,
            is_active=True,
        ).count(),
        "today_orders": orders.filter(submitted_at__date=today).count(),
        "pending_orders": orders.filter(status=Order.Status.SUBMITTED).count(),
        "monthly_sales": monthly_sales,
        "recent_orders": orders.select_related("customer").order_by(
            "-submitted_at", "-pk",
        )[:5],
    }
