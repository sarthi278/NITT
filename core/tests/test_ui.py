from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.test import TestCase


class ResponsivePagesTests(TestCase):
    def setUp(self):
        group = Group.objects.create(name="Production")
        user = User.objects.create_user(username="floor-user", password="demo123")
        user.groups.add(group)
        self.client.login(username="floor-user", password="demo123")

    def test_health_identifies_active_database_backend(self):
        response = self.client.get("/health/")
        self.assertEqual(response.json(), {"status": "ok", "database": "sqlite"})

    def test_service_worker_is_served_with_root_scope(self):
        response = self.client.get("/service-worker.js")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Service-Worker-Allowed"], "/")
        self.assertContains(response, "sync-sales-orders")

    def test_production_screen_has_mobile_layout_and_labels(self):
        call_command("seed_benchmark")
        response = self.client.get("/production/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="viewport"')
        self.assertContains(response, "Toggle navigation")
        self.assertContains(response, "Good packets")
        self.assertContains(response, "Actual used")
        self.assertContains(response, "Wastage")
        self.assertContains(response, "table-responsive")

    def test_stock_screen_uses_responsive_table(self):
        response = self.client.get("/stock/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Current stock")
        self.assertContains(response, "Add Item")
        self.assertContains(response, "table-responsive")

    def test_dashboard_only_shows_sales_tasks_for_salesman(self):
        response = self.client.get("/dashboard/")
        self.assertEqual(response.status_code, 302)

        group = Group.objects.get(name="Production")
        group.name = "Salesman"
        group.save()
        response = self.client.get("/dashboard/")
        self.assertContains(response, "Create an order")
        self.assertContains(response, "My orders")
        self.assertNotContains(response, "Receive stock")

    def test_admin_has_an_administration_dashboard(self):
        group = Group.objects.create(name="Admin")
        user = User.objects.create_user(username="admin-dash", password="demo123")
        user.groups.add(group)
        self.client.force_login(user)

        response = self.client.get("/dashboard/")

        self.assertContains(response, "Review orders")
        self.assertContains(response, "Pending review")
        self.assertNotContains(response, "Log a run")

    def test_authenticated_user_without_a_business_role_is_forbidden(self):
        user = User.objects.create_user(username="unassigned", password="demo123")
        self.client.force_login(user)

        response = self.client.get("/dashboard/")

        self.assertEqual(response.status_code, 403)
