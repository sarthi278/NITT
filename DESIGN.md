# Data Model and Design Note

## Core entities

- **Users** have a role: salesman, admin, production, auditor or owner.
- **Items** separate raw materials, packaging and finished goods; balances are derived from `stock_ledger`.
- **Products** represent saleable finished products.
- **BOM** stores component quantities per finished product.
- **Sales orders / order lines** precede invoices.
- **Invoices / invoice lines** hold immutable posted tax amounts.
- **Payments** reduce customer receivables.
- **Purchases / purchase lines** receive raw or packaging stock.
- **Production runs / run consumptions** post planned-versus-actual consumption and finish output.
- **Audit log** records identity, time and entity changes.

## Stock and traceability

Every stock movement is an append-only ledger row with item, signed quantity, movement type, source document type/id, note and user. Balances are `SUM(quantity_milli)` by item. Opening stock, purchase receipt, production issue, production output, dispatch and adjustment are all ledger movements. No balance column is overwritten.

## BOM and production

One BOM batch contains 40 kg flour, 10 L oil, 1.2 kg spice mix, 200 pouches and 10 cartons and yields 200 packets. On run post, actual components are deducted and good output is added. Yield variance is `(actual-output - planned)/planned`; oil variance is `(actual-consumed - planned)/planned`. A magnitude above 3% is flagged. Quantities are stored in thousandths of base units: kg, litre or piece; finished packets use packet milliunits for consistent integer arithmetic.

## Money and costing

Amounts use integer paise. Retailer A tax is 5%: taxable ₹7,500, CGST ₹187.50 and SGST ₹187.50. Karnataka uses IGST. Flour uses weighted average, and a run adds direct component cost plus labour/gas/power divided by actual good output.
