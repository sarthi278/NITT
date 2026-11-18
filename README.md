# Order-to-Audit Prototype

This repository follows the internship build in phases. Phases 0-2 establish the Django foundation, inventory ledger and production workflows.

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
