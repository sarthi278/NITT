from django.contrib import admin
from .models import (
    AuditLog, BOM, BOMLine, CompanySettings, Customer, Item, Product,
    ProductPackSize, StockBatch, StockInward, StockMovement, Supplier, UnitConversion,
)

admin.site.register([Item, UnitConversion, Supplier, Customer, Product,
                     ProductPackSize, BOM, BOMLine, StockBatch, StockInward, StockMovement,
                     CompanySettings, AuditLog])
