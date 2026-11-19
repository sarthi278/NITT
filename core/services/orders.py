from decimal import Decimal, InvalidOperation
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from core.audit import record_business_event
from core.models import (
    Customer,
    CustomerPrice,
    Item,
    Order,
    OrderLine,
    PriceListItem,
    Product,
)
from core.services.dispatch import available_for_dispatch

ZERO = Decimal("0")


def _quantity(value):
    try:
        quantity = Decimal(str(value))
    except (InvalidOperation, TypeError):
        raise ValueError("Enter a valid order quantity.")
    if not quantity.is_finite() or quantity <= ZERO:
        raise ValueError("Order quantities must be greater than zero.")
    normalized = quantity.quantize(Decimal("0.001"))
    if normalized <= ZERO or quantity != normalized:
        raise ValueError("Order quantities must use at most 3 decimal places.")
    return normalized


def _price_for(customer, product):
    customer_price = CustomerPrice.objects.filter(
        customer=customer,
        product=product,
    ).first()
    if customer_price:
        return customer_price.unit_price
    if customer.price_list_id and customer.price_list.is_active:
        list_price = PriceListItem.objects.filter(
            price_list=customer.price_list,
            product=product,
        ).first()
        if list_price:
            return list_price.unit_price
    return product.selling_price


@transaction.atomic
def create_order(*, customer, created_by, lines, client_uuid, notes=""):
    try:
        submission_id = UUID(str(client_uuid))
    except (ValueError, TypeError, AttributeError):
        raise ValueError("A valid order submission ID is required.")

    existing = Order.objects.filter(client_uuid=submission_id).first()
    if existing:
        if existing.created_by_id != created_by.pk:
            raise ValueError("This order submission ID belongs to another user.")
        return existing, False

    customer = Customer.objects.select_for_update().get(pk=customer.pk)
    if (
        created_by.groups.filter(name="Salesman").exists()
        and not created_by.is_superuser
        and customer.assigned_salesman_id != created_by.pk
    ):
        raise ValueError("You can only create orders for your assigned customers.")

    prepared_lines = []
    for submitted_line in lines:
        try:
            product = Product.objects.get(pk=submitted_line["product"].pk)
        except (KeyError, Product.DoesNotExist, AttributeError):
            raise ValueError("Select a valid product for every order line.")
        if (
            not product.is_active
            or not product.finished_item_id
            or not product.finished_item.is_active
        ):
            raise ValueError(f"{product.name} is not available for sale.")
        if not customer.is_active:
            raise ValueError("This customer is inactive and cannot place orders.")
        quantity = _quantity(submitted_line.get("quantity"))
        unit_price = _price_for(customer, product)
        if not unit_price.is_finite() or unit_price < ZERO:
            raise ValueError(f"The price for {product.name} is invalid.")
        prepared_lines.append({
            "product": product,
            "quantity": quantity,
            "unit_price": unit_price,
        })

    if not prepared_lines:
        raise ValueError("Add at least one product to the order.")

    order = Order.objects.create(
        customer=customer,
        created_by=created_by,
        client_uuid=submission_id,
        notes=notes.strip(),
    )
    OrderLine.objects.bulk_create([
        OrderLine(order=order, **line)
        for line in prepared_lines
    ])
    record_business_event(
        action="LINES_CREATED",
        entity_type="Order",
        entity_id=order.pk,
        user=created_by,
        source_doc_type="ORDER",
        source_doc_id=order.pk,
        details={"line_count": len(prepared_lines)},
    )
    return order, True


@transaction.atomic
def approve_order(*, order, approved_quantities, approved_by):
    order = Order.objects.select_for_update().get(pk=order.pk)
    if order.status != Order.Status.SUBMITTED:
        raise ValueError("Only submitted orders can be approved.")

    lines = list(
        order.lines.select_related("product__finished_item").order_by("pk")
    )
    item_ids = {line.product.finished_item_id for line in lines}
    if None in item_ids:
        raise ValueError("A finished-stock item is missing from this order.")
    item_ids = sorted(item_ids)

    locked_items = {
        item.pk: item
        for item in Item.objects.select_for_update().filter(pk__in=item_ids)
    }

    available_by_item = {
        item_id: available_for_dispatch(
            item=locked_items[item_id],
            dispatch_date=timezone.localdate(),
        )
        for item_id in item_ids
    }
    accepted = []
    for line in lines:
        value = approved_quantities.get(
            line.pk,
            approved_quantities.get(str(line.pk), line.quantity),
        )
        try:
            quantity = Decimal(str(value))
        except (InvalidOperation, TypeError):
            raise ValueError(f"Enter a valid approved quantity for {line.product.name}.")
        if not quantity.is_finite() or quantity < ZERO:
            raise ValueError("Approved quantities cannot be negative.")
        normalized = quantity.quantize(Decimal("0.001"))
        if quantity != normalized:
            raise ValueError("Approved quantities must use at most 3 decimal places.")
        if quantity > line.quantity:
            raise ValueError("Approved quantity cannot exceed the submitted quantity.")

        finished_item = locked_items[line.product.finished_item_id]
        available = available_by_item[finished_item.pk]
        if quantity > available:
            shortage = quantity - available
            raise ValueError(
                f"{line.product.name} is short by {shortage} packets. "
                "Reduce the approved quantity or hold the line."
            )
        available_by_item[finished_item.pk] -= quantity
        line.approved_quantity = normalized
        accepted.append(line)

    OrderLine.objects.bulk_update(accepted, ["approved_quantity"])
    record_business_event(
        action="LINES_APPROVED",
        entity_type="Order",
        entity_id=order.pk,
        user=approved_by,
        source_doc_type="ORDER",
        source_doc_id=order.pk,
        details={
            "approved_quantities": {
                str(line.pk): line.approved_quantity for line in accepted
            }
        },
    )
    if any(line.approved_quantity > ZERO for line in accepted):
        order.status = Order.Status.APPROVED
        order.approved_by = approved_by
        order.save(update_fields=["status", "approved_by", "updated_at"])
    return order


@transaction.atomic
def reject_order(*, order, rejected_by):
    order = Order.objects.select_for_update().get(pk=order.pk)
    if order.status != Order.Status.SUBMITTED:
        raise ValueError("Only submitted orders can be rejected.")
    order.status = Order.Status.REJECTED
    order.approved_by = rejected_by
    order.save(update_fields=["status", "approved_by", "updated_at"])
    record_business_event(
        action="REJECT",
        entity_type="Order",
        entity_id=order.pk,
        user=rejected_by,
        source_doc_type="ORDER",
        source_doc_id=order.pk,
    )
    return order
