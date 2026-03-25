import unittest

from fastapi.testclient import TestClient

from app.main import app


class V100SmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(app)

    def test_health(self) -> None:
        resp = self.client.get("/api/health")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json().get("status"), "ok")

    def test_sync_job_and_runs(self) -> None:
        job_resp = self.client.get("/api/sync/job")
        self.assertEqual(job_resp.status_code, 200)
        job = job_resp.json()
        self.assertIn("cron_expr", job)
        self.assertIn("enabled", job)

        runs_resp = self.client.get("/api/sync/runs", params={"limit": 5})
        self.assertEqual(runs_resp.status_code, 200)
        self.assertIsInstance(runs_resp.json(), list)


if __name__ == "__main__":
    unittest.main()
