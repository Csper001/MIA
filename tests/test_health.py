"""Regression tests for the existing non-sensitive health endpoint.

Run with: python -m unittest discover -s tests
"""
import unittest

from fastapi.testclient import TestClient
from app import app


class HealthEndpointTests(unittest.TestCase):
    def test_health_returns_success_without_secrets(self):
        with TestClient(app) as client:
            response = client.get("/health")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIs(data.get("ok"), True)
        self.assertIsInstance(data.get("api_configured"), bool)
        self.assertNotIn("api_key", data)
        self.assertNotIn("secret", data)
        self.assertNotIn("token", data)

    def test_health_is_unauthenticated(self):
        with TestClient(app) as client:
            response = client.get("/health")
        self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
