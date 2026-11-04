import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.main import app

EXAMPLES = Path(__file__).resolve().parents[1] / "frontend" / "examples"


class AssayApiTests(unittest.TestCase):
    def setUp(self):
        folder = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.dict(os.environ, {"ASSAY_DB": str(Path(folder) / "test.db")}))
        self.client = self.enterContext(TestClient(app))
        self.plate = (EXAMPLES / "demo-pass.csv").read_bytes()

    def upload(self, body, name="Test plate"):
        return self.client.post("/api/runs", params={"name": name}, content=body,
                                headers={"Content-Type": "text/csv"})

    def test_round_trip_and_exact_byte_retry(self):
        response = self.upload(self.plate)
        self.assertEqual(response.status_code, 201)
        run = response.json()
        self.assertTrue(run["passed"])
        self.assertEqual(run["well_count"], 24)
        retry = self.upload(self.plate, "Changed name")
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(retry.json(), run)
        self.assertEqual(len(self.client.get("/api/runs").json()), 1)
        with TestClient(app) as reopened:
            self.assertEqual(reopened.get(f"/api/runs/{run['id']}").json(), run)
            exported = reopened.get(f"/api/runs/{run['id']}/export")
        self.assertIn("well,role,sample,signal,inhibition", exported.text)
        self.assertIn("attachment", exported.headers["content-disposition"])

    def test_noisy_controls_are_persisted_for_review(self):
        run = self.upload((EXAMPLES / "demo-review.csv").read_bytes())
        self.assertEqual(run.status_code, 201)
        self.assertFalse(run.json()["passed"])

    def test_invalid_inputs_do_not_create_runs(self):
        cases = [
            (b"bad,header\n", 422),
            (self.plate + b"A01,sample,Repeated,90\n", 422),
            (self.plate.replace(b",880", b",NaN"), 422),
            (b"x" * (256 * 1024 + 1), 413),
            (b"\xff", 422),
        ]
        for raw, code in cases:
            with self.subTest(code=code, size=len(raw)):
                self.assertEqual(self.upload(raw).status_code, code)
        self.assertEqual(self.client.get("/api/runs").json(), [])
        self.assertEqual(self.client.get("/api/runs/missing").status_code, 404)
        self.assertEqual(self.client.post("/api/runs", content=self.plate).status_code, 415)

    def test_export_escapes_spreadsheet_formulas(self):
        run = self.upload(self.plate.replace(b"Compound-B-4", b"=1+1")).json()
        exported = self.client.get(f"/api/runs/{run['id']}/export")
        self.assertIn("'=1+1", exported.text)


if __name__ == "__main__":
    unittest.main()
