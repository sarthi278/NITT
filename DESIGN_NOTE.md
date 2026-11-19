# NITT ERP engineering design note

## Scope

The application implements an order-to-audit demo for purchasing, inventory, BOM-based production, sales orders, GST invoices, dispatch, payments, credit notes, and reports. Django templates and Bootstrap provide the UI; SQLite is the local default, and Docker Compose supplies PostgreSQL for a local multi-user demo.

## Data integrity

- `StockMovement` is the inventory ledger. Balances are `SUM(qty_in) - SUM(qty_out)` in the item's base unit; there is no independently editable balance.
- Stock movement and audit-log model instances reject edits/deletes, their standard QuerySets reject update/delete, and their admin pages are read-only. Corrections use adjustment or reversal movements. Direct SQL/database-owner access is outside this application-layer protection.
- Purchase, production, dispatch, payment, invoice, credit-note, and order services use transactions; stock operations lock the relevant inventory rows and recheck availability before posting. Partial dispatches are recorded per invoice line, and approved undispatched quantities are reserved during later approvals.
- Invoice and GST line amounts are rounded in Decimal arithmetic. Same-state supplies split GST into CGST/SGST; other-state supplies use IGST. The invoice stores the company GSTIN and place of supply at posting time.
- Invoice PDFs render the total in Indian rupee words, including paise when present. Owner dashboard trends aggregate posted invoices and production runs from their actual dates.
- Signals audit normal model changes; explicit audit events cover bulk-created/updated order and invoice lines, approvals/rejections, production, and stock inward. Events carry actor, timestamp, entity/action, and source-document fields.

## Product and customer masters

- Product variants are separate Products attached to separate finished-goods Items. Each Product can have pack-size rows and one BOM with a packet yield and tolerance.
- Reusable BOM templates store component quantities/units and can be copied into a Product BOM. The seeded Murukku examples use 200 packets for 200 g and 80 packets for 500 g, with variant-specific pouches and carton quantities.
- Customer and supplier forms support GSTIN, state, address, contact details, and deactivation. Customer assignment scopes salesman orders. Customer prices override active price-list entries, which override the product selling price.
- Company address, name, GSTIN, state, phone, email, and financial-year start month are configurable from the restricted Django Admin; invoice PDFs print those company details.

## Authorization

- Business views use role checks at the request boundary. Customer/product/supplier/price-list maintenance and order approval/invoicing/dispatch/credit notes are Admin/Owner-only. Production and stock workflows are limited to Production/Admin/Owner as appropriate; Accountant access is read-only for inventory and reports/collections.
- The custom AdminSite requires an active staff account and Admin/Owner group membership, or superuser status. Transactional records are not editable through admin; financial and inventory master data changes remain role-gated.
- Production deployment must set a private `DJANGO_SECRET_KEY`, `DJANGO_DEBUG=0`, allowed hosts, and trusted HTTPS origins. Secure cookies, HTTPS redirect, HSTS, content sniffing, and frame restrictions are enabled outside debug mode.

## Benchmark reproducibility

`seed_benchmark` refreshes master data and creates opening movements only if the corresponding opening movement is absent. It is intentionally not a transaction reset.

`reset_benchmark --confirm` requires all of the following: debug mode, `BENCHMARK_RESET_ENABLED=1`, an explicitly configured database name matching the active database, a local SQLite or local Compose PostgreSQL connection, and a non-production environment. It clears core demo transaction tables and audit history, retains migrations/auth/master tables, and calls the existing seed command to restore benchmark masters and opening stock. Do not configure this against a database containing non-demo business data.

## Schema and verification

Migration `0007` adds optional contact/address fields and audit source references, GSTIN validators, product deactivation, and BOM-template tables. It does not rewrite stock or financial history. Apply it with `python manage.py migrate`.

The integration benchmark test runs the 60 kg flour purchase, production, Retailer A invoice/dispatch, Distributor B shortage/approval, payment, and reports. Other tests cover role restrictions, admin immutability, product/BOM management, GST, reports, and stock behavior.

## Limitations

- The application's model/QuerySet append-only protections are not database triggers and do not protect against privileged direct SQL or database administrators.
- The application has not been certified for statutory GST/accounting use. Independently validate tax treatment and report outputs before production use.
- Payment gateway/refund automation, push/email notifications, and fully offline first-load behavior are not implemented.
