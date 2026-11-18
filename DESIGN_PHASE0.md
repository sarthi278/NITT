# Phase 0 Design Note

## Assumption

Ledger quantities use base units: grams for kg, millilitres for litres, and pieces for count items. UI quantities are converted at the edge.

## ER diagram

```mermaid
erDiagram
    USER ||--o{ ORDER : creates
    CUSTOMER ||--o{ ORDER : places
    ORDER ||--|{ ORDER_LINE : contains
    ORDER ||--o| INVOICE : becomes
    INVOICE ||--|{ INVOICE_LINE : contains
    INVOICE ||--o{ PAYMENT : receives
    INVOICE ||--o{ CREDIT_NOTE : may_cancel

    PRODUCT ||--|{ PRODUCT_PACK_SIZE : has
    PRODUCT ||--|{ BOM : has
    BOM ||--|{ BOM_LINE : contains
    BOM_LINE }o--|| ITEM : consumes

    SUPPLIER ||--o{ PURCHASE : supplies
    PURCHASE ||--|{ PURCHASE_LINE : contains
    PURCHASE ||--o{ STOCK_MOVEMENT : generates

    PRODUCTION_RUN ||--|{ PRODUCTION_CONSUMPTION : records
    PRODUCTION_RUN ||--o{ STOCK_MOVEMENT : generates

    ITEM ||--o{ UNIT_CONVERSION : converts
    ITEM ||--o{ STOCK_MOVEMENT : moves
    ITEM ||--o{ STOCK_BATCH : may_track
    STOCK_MOVEMENT }o--o| STOCK_BATCH : optional_batch

    USER ||--o{ AUDIT_LOG : performs
```

## Model decisions

- `StockMovement` is the source of truth. Each row has positive `qty_in` and `qty_out` Decimal values in the item's base unit.
- Movements are append-only. Corrections insert adjustment or reversal movements.
- Source document fields trace movements to Purchase, ProductionRun, Invoice/Dispatch, Adjustment or CreditNote.
- Business logic is isolated in `services/`.
- Posted invoices cannot be deleted. Cancellation uses a CreditNote and reversing movements.

## Phase 1 implementation

The `Item.base_unit` is the ledger storage unit: grams for flour and spice, millilitres for oil, and pieces for pouches/cartons/packets. `UnitConversion` stores the display-to-base multiplier.

`StockMovement` stores positive `qty_in` and `qty_out` values, a positive/zero unit cost, movement type, source document type/id and user. Quantity and money fields use three decimal places and two decimal places respectively. Balances are calculated with `SUM(qty_in) - SUM(qty_out)` rather than overwritten.
