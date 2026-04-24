import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))
import weatherbot


class TestParseResolutionStation(unittest.TestCase):

    def test_valid_wunderground_url(self):
        url = "https://www.wunderground.com/history/daily/fr/bonneuil-en-france/LFPB"
        self.assertEqual(weatherbot.parse_resolution_station(url), "LFPB")

    def test_trailing_slash(self):
        url = "https://www.wunderground.com/history/daily/fr/bonneuil-en-france/LFPB/"
        self.assertEqual(weatherbot.parse_resolution_station(url), "LFPB")

    def test_us_station(self):
        url = "https://www.wunderground.com/history/daily/us/new-york/KLGA"
        self.assertEqual(weatherbot.parse_resolution_station(url), "KLGA")

    def test_lowercase_station_uppercased(self):
        url = "https://www.wunderground.com/history/daily/fr/bonneuil-en-france/lfpb"
        self.assertEqual(weatherbot.parse_resolution_station(url), "LFPB")

    def test_returns_none_for_empty_string(self):
        self.assertIsNone(weatherbot.parse_resolution_station(""))

    def test_returns_none_for_none(self):
        self.assertIsNone(weatherbot.parse_resolution_station(None))

    def test_returns_none_for_segment_too_long(self):
        url = "https://www.wunderground.com/history/daily/us/new-york/TOOLONG"
        self.assertIsNone(weatherbot.parse_resolution_station(url))

    def test_returns_none_for_segment_too_short(self):
        url = "https://www.wunderground.com/history/daily/us/new-york/LGA"
        self.assertIsNone(weatherbot.parse_resolution_station(url))

    def test_returns_none_for_segment_with_digits(self):
        url = "https://www.wunderground.com/history/daily/us/san-francisco/KCA1"
        self.assertIsNone(weatherbot.parse_resolution_station(url))

    def test_returns_none_for_bare_domain(self):
        self.assertIsNone(weatherbot.parse_resolution_station("https://www.wunderground.com"))


class TestParseTempRange(unittest.TestCase):

    def test_fahrenheit_range(self):
        self.assertEqual(weatherbot.parse_temp_range("between 46-47°F"), (46.0, 47.0))

    def test_celsius_range(self):
        self.assertEqual(weatherbot.parse_temp_range("between 20-21°C"), (20.0, 21.0))

    def test_negative_range(self):
        self.assertEqual(weatherbot.parse_temp_range("between -5--3°C"), (-5.0, -3.0))

    def test_exact_value(self):
        self.assertEqual(weatherbot.parse_temp_range("be 72°F on March 7"), (72.0, 72.0))

    def test_or_below(self):
        self.assertEqual(weatherbot.parse_temp_range("32°F or below"), (-999.0, 32.0))

    def test_or_higher(self):
        self.assertEqual(weatherbot.parse_temp_range("95°F or higher"), (95.0, 999.0))

    def test_returns_none_for_none(self):
        self.assertIsNone(weatherbot.parse_temp_range(None))

    def test_returns_none_for_empty_string(self):
        self.assertIsNone(weatherbot.parse_temp_range(""))

    def test_returns_none_for_unrecognized_format(self):
        self.assertIsNone(weatherbot.parse_temp_range("Will it be warm tomorrow?"))


class TestInBucket(unittest.TestCase):

    def test_value_inside_range(self):
        self.assertTrue(weatherbot.in_bucket(46.5, 46.0, 47.0))

    def test_value_at_lower_bound(self):
        self.assertTrue(weatherbot.in_bucket(46.0, 46.0, 47.0))

    def test_value_at_upper_bound(self):
        self.assertTrue(weatherbot.in_bucket(47.0, 46.0, 47.0))

    def test_value_below_range(self):
        self.assertFalse(weatherbot.in_bucket(45.9, 46.0, 47.0))

    def test_value_above_range(self):
        self.assertFalse(weatherbot.in_bucket(47.1, 46.0, 47.0))

    def test_exact_bucket_rounds_correctly(self):
        # t_low == t_high: uses round(), so 71.6 rounds to 72
        self.assertTrue(weatherbot.in_bucket(71.6, 72.0, 72.0))

    def test_exact_bucket_outside_rounding(self):
        self.assertFalse(weatherbot.in_bucket(71.4, 72.0, 72.0))


class TestBucketProb(unittest.TestCase):

    def test_forecast_in_regular_bucket_returns_one(self):
        self.assertEqual(weatherbot.bucket_prob(46.5, 46.0, 47.0), 1.0)

    def test_forecast_outside_regular_bucket_returns_zero(self):
        self.assertEqual(weatherbot.bucket_prob(50.0, 46.0, 47.0), 0.0)

    def test_lower_edge_bucket_high_prob_when_forecast_well_below(self):
        # "32°F or below" — forecast of 27 should have high probability
        prob = weatherbot.bucket_prob(27.0, -999, 32.0, sigma=2.0)
        self.assertGreater(prob, 0.99)

    def test_lower_edge_bucket_low_prob_when_forecast_well_above(self):
        prob = weatherbot.bucket_prob(37.0, -999, 32.0, sigma=2.0)
        self.assertLess(prob, 0.01)

    def test_upper_edge_bucket_high_prob_when_forecast_well_above(self):
        # "95°F or higher" — forecast of 100 should have high probability
        prob = weatherbot.bucket_prob(100.0, 95.0, 999, sigma=2.0)
        self.assertGreater(prob, 0.99)

    def test_upper_edge_bucket_low_prob_when_forecast_well_below(self):
        prob = weatherbot.bucket_prob(90.0, 95.0, 999, sigma=2.0)
        self.assertLess(prob, 0.01)


class TestCalcEv(unittest.TestCase):

    def test_positive_ev_when_prob_exceeds_price(self):
        # p=0.8, price=0.5 → 0.8*(1/0.5 - 1) - 0.2 = 0.6
        self.assertAlmostEqual(weatherbot.calc_ev(0.8, 0.5), 0.6, places=4)

    def test_zero_ev_at_fair_price(self):
        self.assertAlmostEqual(weatherbot.calc_ev(0.5, 0.5), 0.0, places=4)

    def test_negative_ev_when_prob_below_price(self):
        # p=0.2, price=0.5 → 0.2*1 - 0.8 = -0.6
        self.assertAlmostEqual(weatherbot.calc_ev(0.2, 0.5), -0.6, places=4)

    def test_zero_ev_at_price_zero(self):
        self.assertEqual(weatherbot.calc_ev(0.8, 0.0), 0.0)

    def test_zero_ev_at_price_one(self):
        self.assertEqual(weatherbot.calc_ev(0.8, 1.0), 0.0)


class TestCalcKelly(unittest.TestCase):

    def test_positive_kelly_for_favorable_bet(self):
        # p=0.8, price=0.5 → b=1.0, f=0.6, kelly=0.6*0.25=0.15
        with patch.object(weatherbot, "KELLY_FRACTION", 0.25):
            result = weatherbot.calc_kelly(0.8, 0.5)
        self.assertAlmostEqual(result, 0.15, places=4)

    def test_zero_kelly_for_unfavorable_bet(self):
        # p=0.3 at price=0.5: negative raw kelly → clamped to 0
        with patch.object(weatherbot, "KELLY_FRACTION", 0.25):
            result = weatherbot.calc_kelly(0.3, 0.5)
        self.assertEqual(result, 0.0)

    def test_zero_kelly_at_price_zero(self):
        self.assertEqual(weatherbot.calc_kelly(0.8, 0.0), 0.0)

    def test_zero_kelly_at_price_one(self):
        self.assertEqual(weatherbot.calc_kelly(0.8, 1.0), 0.0)


class TestBetSize(unittest.TestCase):

    def test_capped_at_max_bet(self):
        with patch.object(weatherbot, "MAX_BET", 20.0):
            result = weatherbot.bet_size(1.0, 10000)
        self.assertEqual(result, 20.0)

    def test_scales_with_balance(self):
        with patch.object(weatherbot, "MAX_BET", 500.0):
            result = weatherbot.bet_size(0.1, 100)
        self.assertAlmostEqual(result, 10.0, places=2)

    def test_zero_kelly_returns_zero(self):
        with patch.object(weatherbot, "MAX_BET", 20.0):
            result = weatherbot.bet_size(0.0, 1000)
        self.assertEqual(result, 0.0)


class TestRunCalibration(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        json.dump({}, tmp)
        tmp.close()
        self.cal_path = Path(tmp.name)

    def tearDown(self):
        self.cal_path.unlink(missing_ok=True)

    def _market(self, city, actual_temp, forecast_temp, source="ecmwf", resolved=True):
        return {
            "city": city,
            "resolved": resolved,
            "actual_temp": actual_temp,
            "forecast_snapshots": [
                {"source": source, "temp": forecast_temp, "ts": "2026-04-25T00:00:00"}
            ],
        }

    def test_sigma_equals_mae(self):
        # errors: 1, 2, 3 → MAE = 2.0
        markets = [
            self._market("london", actual_temp=20.0, forecast_temp=21.0),
            self._market("london", actual_temp=20.0, forecast_temp=22.0),
            self._market("london", actual_temp=20.0, forecast_temp=23.0),
        ]
        with (
            patch.object(weatherbot, "CALIBRATION_FILE", self.cal_path),
            patch.object(weatherbot, "CALIBRATION_MIN", 2),
        ):
            result = weatherbot.run_calibration(markets)

        self.assertIn("london_ecmwf", result)
        self.assertAlmostEqual(result["london_ecmwf"]["sigma"], 2.0)
        self.assertEqual(result["london_ecmwf"]["n"], 3)

    def test_skipped_when_below_calibration_min(self):
        markets = [self._market("london", actual_temp=20.0, forecast_temp=21.0)]
        with (
            patch.object(weatherbot, "CALIBRATION_FILE", self.cal_path),
            patch.object(weatherbot, "CALIBRATION_MIN", 30),
        ):
            result = weatherbot.run_calibration(markets)

        self.assertNotIn("london_ecmwf", result)

    def test_markets_without_actual_temp_excluded(self):
        markets = [self._market("london", actual_temp=None, forecast_temp=20.0)]
        with (
            patch.object(weatherbot, "CALIBRATION_FILE", self.cal_path),
            patch.object(weatherbot, "CALIBRATION_MIN", 1),
        ):
            result = weatherbot.run_calibration(markets)

        self.assertNotIn("london_ecmwf", result)

    def test_unresolved_markets_excluded(self):
        markets = [self._market("london", actual_temp=20.0, forecast_temp=21.0, resolved=False)]
        with (
            patch.object(weatherbot, "CALIBRATION_FILE", self.cal_path),
            patch.object(weatherbot, "CALIBRATION_MIN", 1),
        ):
            result = weatherbot.run_calibration(markets)

        self.assertNotIn("london_ecmwf", result)

    def test_calibration_written_to_file(self):
        markets = [
            self._market("paris", actual_temp=18.0, forecast_temp=19.0),
            self._market("paris", actual_temp=18.0, forecast_temp=19.0),
        ]
        with (
            patch.object(weatherbot, "CALIBRATION_FILE", self.cal_path),
            patch.object(weatherbot, "CALIBRATION_MIN", 2),
        ):
            weatherbot.run_calibration(markets)

        saved = json.loads(self.cal_path.read_text())
        self.assertIn("paris_ecmwf", saved)

    def test_multiple_cities_calibrated_independently(self):
        markets = [
            self._market("london", actual_temp=20.0, forecast_temp=21.0),
            self._market("london", actual_temp=20.0, forecast_temp=21.0),
            self._market("paris", actual_temp=15.0, forecast_temp=17.0),
            self._market("paris", actual_temp=15.0, forecast_temp=17.0),
        ]
        with (
            patch.object(weatherbot, "CALIBRATION_FILE", self.cal_path),
            patch.object(weatherbot, "CALIBRATION_MIN", 2),
        ):
            result = weatherbot.run_calibration(markets)

        self.assertAlmostEqual(result["london_ecmwf"]["sigma"], 1.0)
        self.assertAlmostEqual(result["paris_ecmwf"]["sigma"], 2.0)


if __name__ == "__main__":
    unittest.main()
