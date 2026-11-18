from django.contrib.auth.models import Group, User
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

    def test_production_screen_has_mobile_layout_and_labels(self):
        response = self.client.get("/production/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="viewport"')
        self.assertContains(response, "Toggle navigation")
        self.assertContains(response, "Good packets")
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
        self.assertContains(response, "Sales order entry is coming next")
        self.assertNotContains(response, "Receive stock")
