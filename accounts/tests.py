from django.test import TestCase
from django.urls import reverse

from axes.utils import reset

from accounts.models import User


class BruteForceLoginTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            "locktest", password="rightpass", role=User.Role.SALES
        )
        # Clear any lockout state so each test starts from a clean slate
        reset()
        self.addCleanup(reset)

    def _login(self, password):
        return self.client.post(
            reverse("login"), {"username": "locktest", "password": password}
        )

    def test_valid_login_succeeds_with_axes_enabled(self):
        self._login("rightpass")
        self.assertIn("_auth_user_id", self.client.session)

    def test_locks_out_after_five_failed_attempts(self):
        for _ in range(5):
            self._login("wrong")
        # Locked now: even the correct password no longer logs the user in
        response = self._login("rightpass")
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(response.status_code, 429)
        self.assertContains(response, "vaqtincha", status_code=429)
