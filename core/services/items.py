from django.db import transaction


@transaction.atomic
def save_item_with_conversions(item, conversions, deleted_conversions):
    item.save()

    for conversion in deleted_conversions:
        if conversion.item_id != item.pk:
            raise ValueError("A conversion does not belong to this item.")
        conversion.delete()

    for conversion in conversions:
        if conversion.item_id not in (None, item.pk):
            raise ValueError("A conversion does not belong to this item.")
        conversion.item = item
        conversion.save()

    return item
