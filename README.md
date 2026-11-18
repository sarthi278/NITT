# Order-to-Audit Prototype

This repository follows the internship build in phases. Phases 0-2 establish the Django foundation, inventory ledger and production workflows.

## Run with Docker Compose

```powershell
docker compose up --build
```

Open `http://localhost:8000/`. Health endpoint: `http://localhost:8000/health/`.

## Run locally

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

Without `DATABASE_HOST`, Django uses SQLite.

## Stack justification

Django provides authentication, groups, migrations and admin in one framework, reducing custom infrastructure. PostgreSQL provides a robust transactional ledger when run with Docker, while SQLite keeps local setup simple. Server-rendered Bootstrap 5 pages keep the mobile salesman and desktop workflows readable with little JavaScript.

## Engineering decisions

- Money and quantities use Decimal fields: paise or 2 decimal places for money and 3 decimal places for quantities.
- `StockMovement` will be append-only and ledger-derived balances will never be stored in an editable stock table.
- `transaction.atomic()` and `select_for_update()` will protect stock-affecting services.
- All business rules are placed in `services/` modules.
- Roles will use Django auth groups: Salesman, Admin, Production, Accountant, Owner.

## Phase 0 status

The Django skeleton and Docker environment are ready.

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

The production role can post flour purchases and benchmark runs from the responsive Bootstrap screens. Purchases and production post their stock movements inside database transactions. The stock summary and ledger screens are available to production, admin, owner and accountant roles. The mobile navigation collapses into a menu, tables scroll within their container on narrow screens, and dashboard actions are role-specific.
