from django.contrib.admin import AdminSite, ModelAdmin

from .models import (
    AuditLog,
    BOM,
    BOMLine,
    BOMTemplate,
    BOMTemplateLine,
    CompanySettings,
    Customer,
    CustomerPrice,
    Dispatch,
    DispatchLine,
    Invoice,
    InvoiceLine,
    Item,
    Order,
    OrderLine,
    Payment,
    PriceList,
    PriceListItem,
    Product,
    ProductPackSize,
    ProductionConsumption,
    ProductionRun,
    Purchase,
    PurchaseLine,
    StockBatch,
    StockInward,
    StockMovement,
    Supplier,
    UnitConversion,
)


class RestrictedAdminSite(AdminSite):
    site_header = "NITT ERP administration"
    site_title = "NITT ERP"
    index_title = "Business administration"

    def has_permission(self, request):
        user = request.user
        return (
            user.is_active
            and user.is_staff
            and (
                user.is_superuser
                or user.groups.filter(name__in=("Admin", "Owner")).exists()
            )
        )


admin_site = RestrictedAdminSite(name="admin")


class BusinessAdmin(ModelAdmin):
    def has_module_permission(self, request):
        return admin_site.has_permission(request)

    def has_view_permission(self, request, obj=None):
        return admin_site.has_permission(request)

    def has_add_permission(self, request):
        return admin_site.has_permission(request)

    def has_change_permission(self, request, obj=None):
        return admin_site.has_permission(request)

    def has_delete_permission(self, request, obj=None):
        return admin_site.has_permission(request)


class ReadOnlyBusinessAdmin(BusinessAdmin):
    def get_readonly_fields(self, request, obj=None):
        return tuple(field.name for field in self.model._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return admin_site.has_permission(request) and obj is None

    def has_delete_permission(self, request, obj=None):
        return False


class StockMovementAdmin(ReadOnlyBusinessAdmin):
    list_display = (
        "timestamp",
        "item",
        "movement_type",
        "qty_in",
        "qty_out",
        "source_doc_type",
        "source_doc_id",
        "user",
    )
    list_filter = ("movement_type", "timestamp")
    search_fields = ("item__name", "item__sku", "source_doc_type")
    date_hierarchy = "timestamp"


class AuditLogAdmin(ReadOnlyBusinessAdmin):
    list_display = (
        "timestamp",
        "user",
        "action",
        "entity_type",
        "entity_id",
        "source_doc_type",
        "source_doc_id",
    )
    list_filter = ("action", "entity_type", "timestamp")
    search_fields = ("entity_type", "source_doc_type", "old_value", "new_value")
    date_hierarchy = "timestamp"


class CompanySettingsAdmin(BusinessAdmin):
    list_display = ("name", "gstin", "state", "fy_start_month")

    def has_add_permission(self, request):
        return (
            super().has_add_permission(request)
            and not CompanySettings.objects.exists()
        )

    def has_delete_permission(self, request, obj=None):
        return False


for model in (
    Item,
    UnitConversion,
    Supplier,
    Customer,
    Product,
    ProductPackSize,
    BOM,
    BOMLine,
    BOMTemplate,
    BOMTemplateLine,
    PriceList,
    PriceListItem,
    CustomerPrice,
):
    admin_site.register(model, BusinessAdmin)

admin_site.register(CompanySettings, CompanySettingsAdmin)

for model in (
    StockBatch,
    StockInward,
    Order,
    OrderLine,
    Invoice,
    InvoiceLine,
    Dispatch,
    DispatchLine,
    Payment,
    ProductionRun,
    ProductionConsumption,
    Purchase,
    PurchaseLine,
):
    admin_site.register(model, ReadOnlyBusinessAdmin)

admin_site.register(StockMovement, StockMovementAdmin)
admin_site.register(AuditLog, AuditLogAdmin)
