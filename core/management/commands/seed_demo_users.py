from django.contrib.auth.models import Group, User
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Create demo login users without resetting business data"

    def handle(self, *args, **options):
        users = [
            ("sales-a", "Salesman"),
            ("sales-b", "Salesman"),
            ("admin", "Admin"),
            ("production", "Production"),
            ("accountant", "Accountant"),
            ("owner", "Owner"),
        ]

        for username, role in users:
            group, _ = Group.objects.get_or_create(name=role)
            user, _ = User.objects.get_or_create(username=username)
            user.set_password("demo123")
            user.is_staff = role in ("Admin", "Production", "Accountant", "Owner")
            user.save()
            user.groups.add(group)

        self.stdout.write(self.style.SUCCESS("Demo users created successfully"))
