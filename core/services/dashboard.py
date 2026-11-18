from decimal import Decimal

from django.utils import timezone

from core.models import (
    Invoice,
    Item,
    Order,
    ProductionConsumption,
    ProductionRun,
)
from core.services.stock import balance

ZERO = Decimal("0")


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
        "stock_rows": stock_rows,
        "low_stock_count": sum(1 for row in stock_rows if row["is_low"]),
        "variance_alerts": ProductionConsumption.objects.filter(
            flagged=True,
            run__run_date=today,
        ).count(),
        "latest_runs": latest_runs,
        "outstanding_total": outstanding_total,
    }
