import json
import sys
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from strategies.runner import _discover_variants, STRATS_DIR


def _write(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


_VALID_CFG = {
    "description": "Test variant",
    "vc_key": "TESTKEY",
    "strategy": {"prob_model_normal_cdf": False},
}


class TestDiscoverVariants(unittest.TestCase):

    def _patch_dir(self, tmp: Path):
        return patch("strategies.runner.STRATS_DIR", tmp)

    # ------------------------------------------------------------------
    # Empty / missing directory
    # ------------------------------------------------------------------

    def test_empty_directory_returns_empty_list(self):
        tmp = Path(tempfile.mkdtemp())
        with self._patch_dir(tmp):
            result = _discover_variants()
        self.assertEqual(result, [])

    def test_empty_directory_warns_to_stderr(self):
        tmp = Path(tempfile.mkdtemp())
        with self._patch_dir(tmp):
            with patch("sys.stderr", new_callable=StringIO) as mock_err:
                _discover_variants()
                self.assertIn("empty", mock_err.getvalue())

    def test_only_example_config_treated_as_empty(self):
        tmp = Path(tempfile.mkdtemp())
        _write(tmp / "example.json", _VALID_CFG)
        with self._patch_dir(tmp):
            with patch("sys.stderr", new_callable=StringIO) as mock_err:
                result = _discover_variants()
        self.assertEqual(result, [])
        self.assertIn("empty", mock_err.getvalue())

    # ------------------------------------------------------------------
    # Valid configs
    # ------------------------------------------------------------------

    def test_valid_config_is_returned(self):
        tmp = Path(tempfile.mkdtemp())
        _write(tmp / "alpha.json", _VALID_CFG)
        with self._patch_dir(tmp):
            result = _discover_variants()
        self.assertEqual(result, ["alpha"])

    def test_multiple_valid_configs_returned_sorted(self):
        tmp = Path(tempfile.mkdtemp())
        _write(tmp / "zeta.json", _VALID_CFG)
        _write(tmp / "alpha.json", _VALID_CFG)
        _write(tmp / "beta.json", _VALID_CFG)
        with self._patch_dir(tmp):
            result = _discover_variants()
        self.assertEqual(result, ["alpha", "beta", "zeta"])

    def test_example_excluded_from_valid_results(self):
        tmp = Path(tempfile.mkdtemp())
        _write(tmp / "example.json", _VALID_CFG)
        _write(tmp / "real.json", _VALID_CFG)
        with self._patch_dir(tmp):
            result = _discover_variants()
        self.assertEqual(result, ["real"])

    # ------------------------------------------------------------------
    # Invalid JSON
    # ------------------------------------------------------------------

    def test_invalid_json_is_skipped(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "broken.json").write_text("not valid json", encoding="utf-8")
        with self._patch_dir(tmp):
            result = _discover_variants()
        self.assertNotIn("broken", result)

    def test_invalid_json_warns_to_stderr(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "broken.json").write_text("{bad", encoding="utf-8")
        with self._patch_dir(tmp):
            with patch("sys.stderr", new_callable=StringIO) as mock_err:
                _discover_variants()
                self.assertIn("broken.json", mock_err.getvalue())

    def test_valid_config_still_returned_alongside_invalid_json(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "broken.json").write_text("{bad", encoding="utf-8")
        _write(tmp / "good.json", _VALID_CFG)
        with self._patch_dir(tmp):
            result = _discover_variants()
        self.assertEqual(result, ["good"])

    # ------------------------------------------------------------------
    # Missing required fields
    # ------------------------------------------------------------------

    def test_missing_description_is_skipped(self):
        tmp = Path(tempfile.mkdtemp())
        cfg = {k: v for k, v in _VALID_CFG.items() if k != "description"}
        _write(tmp / "nodesc.json", cfg)
        with self._patch_dir(tmp):
            result = _discover_variants()
        self.assertNotIn("nodesc", result)

    def test_missing_strategy_is_skipped(self):
        tmp = Path(tempfile.mkdtemp())
        cfg = {k: v for k, v in _VALID_CFG.items() if k != "strategy"}
        _write(tmp / "nostrat.json", cfg)
        with self._patch_dir(tmp):
            result = _discover_variants()
        self.assertNotIn("nostrat", result)

    def test_missing_vc_key_is_skipped(self):
        tmp = Path(tempfile.mkdtemp())
        cfg = {k: v for k, v in _VALID_CFG.items() if k != "vc_key"}
        _write(tmp / "nokey.json", cfg)
        with self._patch_dir(tmp):
            result = _discover_variants()
        self.assertNotIn("nokey", result)

    def test_missing_field_warns_with_field_name(self):
        tmp = Path(tempfile.mkdtemp())
        cfg = {k: v for k, v in _VALID_CFG.items() if k != "vc_key"}
        _write(tmp / "nokey.json", cfg)
        with self._patch_dir(tmp):
            with patch("sys.stderr", new_callable=StringIO) as mock_err:
                _discover_variants()
                self.assertIn("vc_key", mock_err.getvalue())

    def test_multiple_missing_fields_all_named_in_warning(self):
        tmp = Path(tempfile.mkdtemp())
        _write(tmp / "bare.json", {"balance": 1000})
        with self._patch_dir(tmp):
            with patch("sys.stderr", new_callable=StringIO) as mock_err:
                _discover_variants()
                stderr = mock_err.getvalue()
                self.assertIn("description", stderr)
                self.assertIn("strategy", stderr)
                self.assertIn("vc_key", stderr)

    def test_incomplete_config_skipped_while_valid_config_returned(self):
        tmp = Path(tempfile.mkdtemp())
        cfg = {k: v for k, v in _VALID_CFG.items() if k != "vc_key"}
        _write(tmp / "incomplete.json", cfg)
        _write(tmp / "complete.json", _VALID_CFG)
        with self._patch_dir(tmp):
            result = _discover_variants()
        self.assertEqual(result, ["complete"])


if __name__ == "__main__":
    unittest.main()
