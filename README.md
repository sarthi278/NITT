# NITT ERP — order-to-audit workflow

A server-rendered Django ERP demo for inventory, production, sales, GST invoicing, collections, and reports. The stock ledger is the source of truth; operational changes go through transactional services rather than editable balance fields.

## Run the application

### Docker Compose (recommended demo)

Start Docker Desktop, then in PowerShell:

```powershell
Copy-Item .env.example .env
docker compose up --build
```

In another terminal, seed the benchmark:

```powershell
docker compose exec web python manage.py seed_benchmark
```

Open [http://localhost:8000/](http://localhost:8000/); `/health/` reports the active database backend. Stop with `Ctrl+C` and `docker compose down` (database data remains in the named volume). Compose is the development/demo configuration and runs Django's development server.

### Local SQLite

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:DJANGO_DEBUG = "1"
python manage.py migrate
python manage.py seed_benchmark
python manage.py runserver
```

`DJANGO_DEBUG=1` is required for local development; production mode deliberately rejects the development secret key. Without `DATABASE_HOST`, the default database is the repository's `db.sqlite3`.

The seeded demo passwords are `demo123` for `sales-a`, `sales-b`, `admin`, `production`, `accountant`, and `owner`. Change/remove these accounts before exposing a deployment.

## Benchmark workflow

`seed_benchmark` is additive/idempotent: it refreshes benchmark master data and creates opening balances only when absent. It does not erase orders, purchases, invoices, or payments. Use it repeatedly without duplicating opening stock.

For a reproducible clean demo, use a dedicated local/demo database. Set these values in its untracked `.env`:

```dotenv
DJANGO_DEBUG=1
DJANGO_ENV=development
BENCHMARK_DEMO_DATABASE=order_audit
BENCHMARK_RESET_ENABLED=1
```

The configured benchmark name must exactly match `DATABASE_NAME`; PostgreSQL reset is limited to local Compose hosts (`db`, `localhost`, or `127.0.0.1`). The command also requires `--confirm` and refuses production mode. It clears demo transactions and their audit history, retains auth and master tables, then calls `seed_benchmark` to restore benchmark masters and opening stock:

```powershell
docker compose up -d --force-recreate web
docker compose exec web python manage.py reset_benchmark --confirm
```

Never enable reset for a database containing non-demo business data.

### Benchmark values

- Opening stock: flour 100 kg, oil 30 L, spice 5 kg, 200 g pouches 500, cartons 30, and finished 200 g packets 50.
- The 60 kg flour purchase raises flour to 160 kg.
- Standard 200 g Murukku batch: 200 packets; 500 g variant: 80 packets. Their BOMs use distinct pouch quantities and carton yields.
- Benchmark 200 g production: flour 120 kg, oil 19.5 L, spice 3.8 kg, 200 g pouches 306, cartons 21, finished goods 238; production cost ₹5,855, ₹31.14 per good packet, and ₹18.86 margin.
- Retailer A's 150 packets invoice at ₹7,500 taxable + ₹187.50 CGST + ₹187.50 SGST = ₹7,875. Dispatch leaves 88 packets.
- Distributor B requests 120 packets and is short by 32. Approval for 88 packets yields ₹4,400 taxable + ₹220 IGST.
- A ₹4,000 Retailer A payment leaves ₹3,875 outstanding.

Run the end-to-end test that asserts this workflow with:

```powershell
$env:DJANGO_DEBUG = "1"
python -m pytest core\tests\test_benchmark_e2e.py -q
```

## Roles and controls

- Django Admin is limited to active staff who are in the `Admin` or `Owner` group (or superusers). Seeded `Production` and `Accountant` staff cannot enter it.
- Admin/Owner can manage products, BOMs, customers, suppliers, price lists, and company settings. Production/Admin/Owner retain their existing operational stock and production permissions.
- Order submission is scoped to an active assigned customer. Customer-specific prices override price-list prices, which override the product price.
- Invoice, dispatch, payment, approval, rejection, credit-note, stock, and reporting actions are role-checked. Sensitive mutations use POST forms.
- Dispatches can be split across multiple shipments; approved but not-yet-dispatched quantities reserve stock during subsequent order approvals.
- StockMovement and AuditLog are append-only through the model APIs and read-only in Django Admin. Corrections use adjustment/reversal records.
- The Owner dashboard shows database-derived sales and production KPIs with six-month trends; Admin and Salesman dashboards show role-specific activity.
- GSTIN format validation, intra-state CGST/SGST, interstate IGST, invoice PDF company details and Indian-numbered amount-in-words, and export-safe CSV/XLSX values are supported.

## Configuration and production

Do not commit `.env` or use the Compose development credentials in production. Configure secret environment variables:

- `DJANGO_SECRET_KEY`: long, private random value
- `DJANGO_DEBUG=0`, `DJANGO_ENV=production`
- `DJANGO_ALLOWED_HOSTS`: comma-separated deployed hostnames
- `DJANGO_CSRF_TRUSTED_ORIGINS`: full `https://` origins
- `DATABASE_HOST`, `DATABASE_PORT`, `DATABASE_NAME`, `DATABASE_USER`, `DATABASE_PASSWORD`
- `DATABASE_SSLMODE=require` when required by the database provider

The production Dockerfile uses Gunicorn and WhiteNoise. Apply migrations with `python manage.py migrate`; do not run the benchmark reset or seed commands in production. Configure company name/address/GSTIN/contact and FY start month in Django Admin before issuing invoices. Run `python manage.py check --deploy` against deployment settings before release.

Non-debug startup requires a non-demo `DJANGO_SECRET_KEY`, PostgreSQL `DATABASE_HOST` and non-demo `DATABASE_PASSWORD`, plus explicit `DJANGO_ALLOWED_HOSTS` and HTTPS `DJANGO_CSRF_TRUSTED_ORIGINS`. The committed Compose defaults are for local development and are intentionally rejected in production mode.

## Verification

```powershell
$env:DJANGO_DEBUG = "1"
python manage.py check
python manage.py makemigrations --check --dry-run
python -m pytest -q
docker compose config --quiet
```

## Design and demo

See [DESIGN_NOTE.md](./DESIGN_NOTE.md) for the architecture, data integrity, and limitations; use [DEMO_SCRIPT.md](./DEMO_SCRIPT.md) for the walkthrough.

## Known limitations

- Offline order entry requires the form to have loaded while online; the outbox retries with a client UUID.
- Automated refunds, push/email notifications, and a production-grade payment gateway are not included.
- This is an internship/demo ERP, not a financial or tax compliance certification. Validate tax and accounting outputs with an authorized professional before production use.
