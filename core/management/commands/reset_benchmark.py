import os

from django.conf import settings
from django.core.management import BaseCommand, CommandError, call_command
from django.db import connection, transaction

from core.models import (
    AuditLog,
    CompanySettings,
    CreditNote,
    Dispatch,
    Invoice,
    InvoiceLine,
    InvoiceSequence,
    Order,
    OrderLine,
    Payment,
    ProductionConsumption,
    ProductionRun,
    Purchase,
    PurchaseLine,
    StockBatch,
    StockInward,
    StockMovement,
)


class Command(BaseCommand):
    help = "Reset benchmark transactions in a separately configured demo database."

    def add_arguments(self, parser):
        parser.add_argument(
            "--confirm",
            action="store_true",
            help="Confirm deletion of demo transactions and audit history.",
        )

    def _validate_target(self, confirmed):
        if not confirmed:
            raise CommandError("Pass --confirm to reset benchmark data.")
        if (
            not settings.DEBUG
            or not settings.BENCHMARK_RESET_ENABLED
            or not settings.BENCHMARK_DEMO_DATABASE
            or os.getenv("DJANGO_ENV", "").casefold() == "production"
        ):
            raise CommandError(
                "Reset is disabled. Enable it only for a dedicated development demo database."
            )

        configured_name = str(settings.BENCHMARK_DEMO_DATABASE)
        active_name = str(connection.settings_dict["NAME"])
        if connection.vendor == "sqlite":
            configured_name = os.path.normcase(os.path.abspath(configured_name))
            active_name = os.path.normcase(os.path.abspath(active_name))
        if configured_name != active_name:
            raise CommandError(
                "The configured benchmark database does not match the active database."
            )
        if connection.vendor == "postgresql" and connection.settings_dict.get(
            "HOST"
        ) not in {"localhost", "127.0.0.1", "db"}:
            raise CommandError("Benchmark reset refuses a non-local PostgreSQL host.")

    @transaction.atomic
    def handle(self, *args, **options):
        self._validate_target(options["confirm"])
        # This command is the only intentional bypass of append-only model APIs.
        # It is restricted to the explicitly configured disposable demo database.
        reset_models = (
            InvoiceLine,
            CreditNote,
            Payment,
            Dispatch,
            Invoice,
            InvoiceSequence,
            OrderLine,
            Order,
            ProductionConsumption,
            ProductionRun,
            PurchaseLine,
            Purchase,
            StockInward,
            StockMovement,
            StockBatch,
            AuditLog,
        )
        with connection.cursor() as cursor:
            for model in reset_models:
                cursor.execute(
                    f"DELETE FROM {connection.ops.quote_name(model._meta.db_table)}"
                )
        CompanySettings.objects.update_or_create(
            pk=1,
            defaults={
                "name": "Demo Snacks",
                "address": "",
                "gstin": "",
                "state": "Tamil Nadu",
                "phone": "",
                "email": "",
                "fy_start_month": 4,
            },
        )
        call_command("seed_benchmark", verbosity=options["verbosity"])
        self.stdout.write(self.style.SUCCESS(
            "Benchmark transactions and audit history were reset; benchmark masters and opening stock were restored."
        ))
