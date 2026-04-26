import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import dashboard
from dashboard import _equity_series, _variant_pid_running, build_dashboard_data
from fastapi.testclient import TestClient

client = TestClient(dashboard.app)

_MOCK_BOT_STATUS = {
    "running": False, "pid": None,
    "cpu_percent": 0.0, "memory_mb": 0.0, "uptime_seconds": 0,
}


class TestEquitySeries(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_closed(self, name, closed_at, pnl):
        m = {"position": {"status": "closed", "closed_at": closed_at, "pnl": pnl}}
        (self.tmp / name).write_text(json.dumps(m), encoding="utf-8")

    def test_nonexistent_dir_returns_empty(self):
        self.assertEqual(_equity_series(self.tmp / "missing"), [])

    def test_no_closed_positions_returns_empty(self):
        m = {"position": {"status": "open"}}
        (self.tmp / "nyc.json").write_text(json.dumps(m), encoding="utf-8")
        self.assertEqual(_equity_series(self.tmp), [])

    def test_single_closed_position_returns_empty(self):
        # fewer than 2 events → returns []
        self._write_closed("nyc.json", "2026-04-01T10:00:00", 5.0)
        self.assertEqual(_equity_series(self.tmp), [])

    def test_equity_replay_correct(self):
        self._write_closed("a.json", "2026-04-01T10:00:00", 10.0)
        self._write_closed("b.json", "2026-04-02T10:00:00", -5.0)
        self._write_closed("c.json", "2026-04-03T10:00:00", 20.0)
        result = _equity_series(self.tmp)
        self.assertEqual(result, [1000.0, 1010.0, 1005.0, 1025.0])

    def test_capped_at_50_points(self):
        for i in range(60):
            month = (i // 28) + 1
            day = (i % 28) + 1
            ts = f"2026-{month:02d}-{day:02d}T10:00:00"
            self._write_closed(f"t{i:03d}.json", ts, 1.0)
        result = _equity_series(self.tmp)
        self.assertLessEqual(len(result), 50)

    def test_corrupt_json_file_skipped(self):
        self._write_closed("good1.json", "2026-04-01T10:00:00", 5.0)
        (self.tmp / "bad.json").write_text("not valid json", encoding="utf-8")
        self._write_closed("good2.json", "2026-04-02T10:00:00", 3.0)
        result = _equity_series(self.tmp)
        self.assertEqual(result, [1000.0, 1005.0, 1008.0])


class TestVariantPidRunning(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_no_pid_file_returns_false(self):
        with patch.object(dashboard, "RUNS_DIR", self.tmp):
            self.assertFalse(_variant_pid_running("baseline"))

    def test_non_integer_pid_file_returns_false(self):
        d = self.tmp / "baseline"
        d.mkdir(parents=True)
        (d / "weatherbot.pid").write_text("not-a-pid", encoding="utf-8")
        with patch.object(dashboard, "RUNS_DIR", self.tmp):
            self.assertFalse(_variant_pid_running("baseline"))

    def test_dead_pid_returns_false(self):
        d = self.tmp / "baseline"
        d.mkdir(parents=True)
        (d / "weatherbot.pid").write_text("99999999", encoding="utf-8")
        with patch.object(dashboard, "RUNS_DIR", self.tmp):
            self.assertFalse(_variant_pid_running("baseline"))

    def test_own_pid_returns_true(self):
        d = self.tmp / "baseline"
        d.mkdir(parents=True)
        (d / "weatherbot.pid").write_text(str(os.getpid()), encoding="utf-8")
        with patch.object(dashboard, "RUNS_DIR", self.tmp):
            self.assertTrue(_variant_pid_running("baseline"))


class TestApiVariants(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_no_config_files_returns_empty_variants_list(self):
        with (
            patch.object(dashboard, "RUNS_DIR", self.tmp),
            patch.object(dashboard, "STATE_FILE", self.tmp / "state.json"),
        ):
            resp = client.get("/api/variants")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["variants"], [])
        self.assertFalse(data["main_running"])

    def test_variant_listed_when_config_json_exists(self):
        d = self.tmp / "baseline"
        d.mkdir(parents=True)
        (d / "config.json").write_text(json.dumps({}), encoding="utf-8")
        with (
            patch.object(dashboard, "RUNS_DIR", self.tmp),
            patch.object(dashboard, "STATE_FILE", self.tmp / "state.json"),
        ):
            resp = client.get("/api/variants")
        data = resp.json()
        self.assertEqual(len(data["variants"]), 1)
        self.assertEqual(data["variants"][0]["name"], "baseline")

    def test_main_running_true_when_state_file_exists(self):
        state_file = self.tmp / "state.json"
        state_file.write_text(json.dumps({"balance": 1000.0}), encoding="utf-8")
        with (
            patch.object(dashboard, "RUNS_DIR", self.tmp),
            patch.object(dashboard, "STATE_FILE", state_file),
        ):
            resp = client.get("/api/variants")
        self.assertTrue(resp.json()["main_running"])


class TestApiSourceDashboard(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_unknown_variant_returns_404(self):
        resp = client.get("/api/source/notavariant/dashboard")
        self.assertEqual(resp.status_code, 404)

    def test_known_variant_returns_200_with_kpi(self):
        with patch.object(dashboard, "RUNS_DIR", self.tmp):
            resp = client.get("/api/source/baseline/dashboard")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("kpi", data)
        self.assertIn("open_positions", data)
        self.assertIn("closed_positions", data)

    def test_variant_response_has_empty_balance_history(self):
        with patch.object(dashboard, "RUNS_DIR", self.tmp):
            resp = client.get("/api/source/baseline/dashboard")
        self.assertEqual(resp.json()["balance_history"], [])

    def test_variant_response_has_empty_activity(self):
        with patch.object(dashboard, "RUNS_DIR", self.tmp):
            resp = client.get("/api/source/baseline/dashboard")
        self.assertEqual(resp.json()["activity"], [])

    def test_variant_bot_status_running_reflects_pid_check(self):
        with (
            patch.object(dashboard, "RUNS_DIR", self.tmp),
            patch.object(dashboard, "_variant_pid_running", return_value=True),
        ):
            resp = client.get("/api/source/baseline/dashboard")
        self.assertTrue(resp.json()["bot_status"]["running"])

    def test_variant_bot_status_stopped_when_no_pid(self):
        with (
            patch.object(dashboard, "RUNS_DIR", self.tmp),
            patch.object(dashboard, "_variant_pid_running", return_value=False),
        ):
            resp = client.get("/api/source/baseline/dashboard")
        self.assertFalse(resp.json()["bot_status"]["running"])


class TestApiComparison(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_no_data_returns_empty_sources(self):
        with (
            patch.object(dashboard, "STATE_FILE", self.tmp / "state.json"),
            patch.object(dashboard, "RUNS_DIR", self.tmp),
        ):
            resp = client.get("/api/comparison")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["sources"], [])
        self.assertIn("generated_at", data)

    def test_variant_appears_when_config_exists(self):
        d = self.tmp / "prob_model"
        d.mkdir(parents=True)
        (d / "config.json").write_text(json.dumps({}), encoding="utf-8")
        with (
            patch.object(dashboard, "STATE_FILE", self.tmp / "state.json"),
            patch.object(dashboard, "RUNS_DIR", self.tmp),
        ):
            resp = client.get("/api/comparison")
        names = [s["name"] for s in resp.json()["sources"]]
        self.assertIn("prob_model", names)

    def test_true_strategy_flags_appear_in_flags_list(self):
        d = self.tmp / "prob_model"
        d.mkdir(parents=True)
        cfg = {"strategy": {"prob_model_normal_cdf": True, "time_decay": False}}
        (d / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
        with (
            patch.object(dashboard, "STATE_FILE", self.tmp / "state.json"),
            patch.object(dashboard, "RUNS_DIR", self.tmp),
        ):
            resp = client.get("/api/comparison")
        variant = next(s for s in resp.json()["sources"] if s["name"] == "prob_model")
        self.assertIn("prob_model_normal_cdf", variant["flags"])
        self.assertNotIn("time_decay", variant["flags"])

    def test_variant_pnl_summed_from_closed_positions(self):
        d = self.tmp / "baseline"
        d.mkdir(parents=True)
        (d / "config.json").write_text(json.dumps({}), encoding="utf-8")
        markets = d / "data" / "markets"
        markets.mkdir(parents=True)
        (markets / "trade1.json").write_text(
            json.dumps({"position": {"status": "closed", "pnl": 25.0}}), encoding="utf-8"
        )
        (markets / "trade2.json").write_text(
            json.dumps({"position": {"status": "closed", "pnl": -5.0}}), encoding="utf-8"
        )
        with (
            patch.object(dashboard, "STATE_FILE", self.tmp / "state.json"),
            patch.object(dashboard, "RUNS_DIR", self.tmp),
        ):
            resp = client.get("/api/comparison")
        variant = next(s for s in resp.json()["sources"] if s["name"] == "baseline")
        self.assertAlmostEqual(variant["pnl"], 20.0, places=2)
        self.assertEqual(variant["trades"], 2)


class TestBuildDashboardDataVariant(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._history_snapshot = len(dashboard.balance_history)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        del dashboard.balance_history[self._history_snapshot:]

    def test_is_variant_does_not_mutate_balance_history(self):
        before = len(dashboard.balance_history)
        build_dashboard_data(data_dir=self.tmp, is_variant=True)
        self.assertEqual(len(dashboard.balance_history), before)

    def test_is_variant_false_appends_to_balance_history(self):
        # Write a state.json with a balance that won't match any existing tail
        state_file = self.tmp / "state.json"
        state_file.write_text(
            json.dumps({"balance": 88888.88, "starting_balance": 1000.0}), encoding="utf-8"
        )
        before = len(dashboard.balance_history)
        with patch.object(dashboard, "check_bot_status", return_value=_MOCK_BOT_STATUS):
            build_dashboard_data(data_dir=self.tmp, is_variant=False)
        self.assertGreater(len(dashboard.balance_history), before)


if __name__ == "__main__":
    unittest.main()
