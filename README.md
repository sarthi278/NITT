# Order-to-Audit Prototype

This repository is a working Django prototype for an order-to-audit workflow. It includes inventory and production, sales and invoicing, payments, reports, a role-aware dashboard, and a mobile order form with an offline outbox.

## Run with Docker Compose

```powershell
Copy-Item .env.example .env
docker compose up --build
```

Open `http://localhost:8000/`. Health endpoint: `http://localhost:8000/health/`.

## Start from VS Code

Press **Ctrl+Shift+B** to run the default build task. It starts PostgreSQL in a Docker Compose container and Django in a separate integrated terminal; Django serves both the HTML frontend and backend. No Node/frontend dependency install is needed. The task creates `.venv` and installs `requirements.txt` only when the environment is missing or the requirements have changed, then waits for PostgreSQL, runs migrations, and starts the server.

Start Docker Desktop first. `.env` is read if present and is never created, changed, or printed by the startup scripts. Set `USE_SQLITE=1` in the environment to skip Docker and run the existing SQLite database. The default application and PostgreSQL host ports are 8000 and 5432; override them with `APP_PORT` and `POSTGRES_HOST_PORT`.

If an older server already owns the application port but uses a different database backend, the task reports the mismatch rather than starting a duplicate. Stop that server and press Ctrl+Shift+B again.

## Run locally

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

Without `DATABASE_HOST`, Django uses SQLite.

### Configure hosted PostgreSQL

Set these variables in the deployment platform's secret/environment-variable settings (do not commit `.env`):

- `DATABASE_HOST`: hostname only, without `postgres://` or `https://`
- `DATABASE_PORT`: provider's PostgreSQL port, usually `5432`
- `DATABASE_NAME`, `DATABASE_USER`, `DATABASE_PASSWORD`
- `DATABASE_SSLMODE=require` if the provider requires TLS
- `DJANGO_SECRET_KEY`: a private random value
- `DJANGO_DEBUG=0`
- `DJANGO_ALLOWED_HOSTS`: comma-separated deployed hostnames

The app selects PostgreSQL when `DATABASE_HOST` is set. Run `python manage.py migrate` against that environment to create its schema. `python manage.py seed_benchmark` is optional and should only be used for a fresh demo database. This does not copy data from the local SQLite database.

Verify a connection from the configured app environment with:

```powershell
python manage.py shell -c "from django.db import connection; connection.ensure_connection(); print(connection.vendor, connection.cursor().execute('SELECT 1').fetchone())"
```

## Stack justification

Django provides authentication, groups, migrations and admin in one framework, reducing custom infrastructure. PostgreSQL provides a robust transactional ledger when run with Docker, while SQLite keeps local setup simple. Server-rendered Bootstrap 5 pages keep the mobile salesman and desktop workflows readable with little JavaScript.

## Engineering decisions

- Money and quantities use Decimal fields: 2 decimal places for money and 3 decimal places for quantities.
- `StockMovement` is append-only in business services; ledger-derived balances are never stored in an editable stock table.
- `transaction.atomic()` and `select_for_update()` protect stock-affecting services.
- All business rules are placed in `services/` modules.
- Django auth groups provide Salesman, Admin, Production, Accountant, and Owner roles; views and querysets enforce access.

## Phase 0 status

The Django skeleton, Docker environment, and Mermaid data-model diagram are ready.

## Phase 1 status

Run the benchmark seed:

```powershell
python manage.py migrate
python manage.py seed_benchmark
pytest -q
```

Test logins use password `demo123`: `sales-a`, `sales-b`, `admin`, `production`, `accountant`, `owner`.

The seed creates Retailer A, Distributor B, Murukku 200 g, its BOM and benchmark opening balances. Inventory balances are not stored on Item; they are computed from append-only `StockMovement.qty_in - qty_out`.

## Phase 2 status

The production role can post purchases and BOM-driven production runs from responsive Bootstrap screens. The run form renders the selected product's BOM lines instead of assuming fixed material SKUs. Purchases and production post ledger movements inside database transactions, and production checks all required stock before writing any run data. The stock summary and ledger screens are available to production, admin, owner and accountant roles.

### Production valuation and rounding

Inventory valuation uses moving weighted average cost per base unit. Every new inward movement records its normalized `unit_cost_base`; each production consumption line is valued at the item's current weighted average, rounded to cents, and run overheads are added directly to that run. Cost per packet is total run cost divided by good output, rounded half-up to cents; margin uses that displayed cost per packet. For the benchmark run, this gives material cost ₹4,055, total cost ₹5,855, cost per packet ₹31.14, and margin ₹18.86 per packet.

Quantities are converted from the BOM or purchase unit to the item's base unit through its `UnitConversion` records. A production run records normal issue and wastage as distinct movements, while its finished-goods movement includes only good output. A migration backfills known opening-stock and stock-inward rates; historical purchase rows do not retain their original display unit, so their pre-migration unit rates cannot be reliably normalized.

## Orders, invoices, and collections

Salesmen can submit orders only for customers assigned to them. A client UUID makes repeated online/offline submissions idempotent. Admin and Owner users review availability, reduce quantities for partial approval, hold lines at zero, or reject an order. Dispatch checks and locks finished stock again before posting outbound movements.

Invoices use financial-year numbers such as `2026-27/0001`. Taxable values are rounded per line to paise; GST is then rounded on the invoice taxable total per GST rate, split between CGST/SGST for Tamil Nadu or recorded as IGST for other states. Where multiple lines share a rate, rounded tax cents are allocated to lines by largest remainder so line totals reconcile to the invoice total. Customer-specific prices override price-list prices, which override the product selling price.

Payments are append-only and cannot exceed an invoice's outstanding balance. A full credit note is allowed only for an invoice with no recorded payments; a dispatched invoice's credit note posts reversal stock movements. Dispatch allocates dated batches by FEFO, skips expired lots, and consumes unbatched stock only after eligible lots. Admin and Owner can collect payments and approve orders; Accountant has read-only access to finance reports and collections.

## Reports and dashboard

Admin, Owner, and Accountant users can filter sales, customer/product sales, GST/B2B-B2C, HSN, purchases, stock, ledger, production variance, collections, and outstanding ageing reports by date. Reports export to CSV, Excel, and PDF; invoice PDFs are also available. The Owner dashboard shows today's orders, low-stock items, today's variance alerts, outstanding dues, recent run costs, and current stock.

Audit logging records model changes with the acting user, field-level old/new values, and timestamp. Posted invoices are not deleted; full credit notes reverse invoice balances and, when already dispatched, restore stock through new ledger movements.

## Known gaps

The salesman order form queues submissions in IndexedDB and retries them through the service worker or the browser's online event; offline entry requires the form to have been loaded while online. Automatic refund tracking and push/email low-stock notifications are not implemented. Legacy purchase movements without their original display unit cannot be reconstructed into a reliable base-unit cost by the migration.

## Stock inward

Production, Admin, and Owner users can add stock from the Stock page. Each entry creates a persisted inward document and an `ADJUSTMENT` `StockMovement` in one transaction. The movement quantity is converted to the item's base unit using that item's `UnitConversion` records (the base unit is always available as a 1:1 option); balances continue to come only from the ledger. Repeated submissions carry a unique form token, so retrying the same submission cannot add stock twice.

Assumptions for stock inward:

- The unit cost is entered and recorded per selected display unit; the inward document retains that unit while the movement quantity is in base units.
- Manufacturing or expiry dates require a batch number. Manufacturing cannot be after the entry date, expiry must be after manufacturing and on or after entry, and expired stock is rejected.
- A batch number is unique per item. Reusing an existing batch is allowed only when any supplied dates match its recorded dates.

Apply the new schema with `python manage.py migrate` before running the application or deploying the feature.

## Item management

Admin, Owner, and Production users can create and edit items from the Stock page. Item creation saves the item and its unit conversions atomically and does not create stock movements. Search, item-type filtering, reorder levels, and low-stock status are shown in the stock list. Items are deactivated/reactivated rather than hard-deleted so movements and business history remain intact. An item's base unit cannot be changed after it has related stock or business records.

The Item model has no description field, so the form does not add one. GST rate is treated as a percentage from 0 to 100; reorder level is expressed in the base unit.

## Demo

Follow [DEMO_SCRIPT.md](./DEMO_SCRIPT.md) for a timed walkthrough of the benchmark workflow and the expected values to show.
