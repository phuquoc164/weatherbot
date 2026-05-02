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
            "status": "resolved" if resolved else "open",
            "actual_temp": actual_temp,
            "forecast_snapshots": [
                {"ecmwf": forecast_temp if source == "ecmwf" else None,
                 "hrrr":  forecast_temp if source == "hrrr"  else None,
                 "metar": forecast_temp if source == "metar" else None,
                 "ts": "2026-04-25T00:00:00"}
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

    def test_early_exit_market_with_actual_temp_included(self):
        # Early-exit markets (status=open) with actual_temp should still calibrate
        markets = [
            self._market("london", actual_temp=20.0, forecast_temp=21.0, resolved=False),
            self._market("london", actual_temp=20.0, forecast_temp=21.0, resolved=False),
        ]
        with (
            patch.object(weatherbot, "CALIBRATION_FILE", self.cal_path),
            patch.object(weatherbot, "CALIBRATION_MIN", 2),
        ):
            result = weatherbot.run_calibration(markets)

        self.assertIn("london_ecmwf", result)

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


class TestBucketProbWithProbModel(unittest.TestCase):
    """bucket_prob() interior-bucket behavior with STRAT_PROB_MODEL toggled."""

    def test_interior_bucket_returns_continuous_probability(self):
        with patch.object(weatherbot, "STRAT_PROB_MODEL", True):
            prob = weatherbot.bucket_prob(46.5, 46.0, 47.0, sigma=2.0)
        self.assertGreater(prob, 0.0)
        self.assertLess(prob, 1.0)

    def test_interior_bucket_probability_matches_normal_cdf_formula(self):
        # Forecast 46.5, bucket [46, 47], sigma=2: CDF((47-46.5)/2) - CDF((46-46.5)/2)
        with patch.object(weatherbot, "STRAT_PROB_MODEL", True):
            prob = weatherbot.bucket_prob(46.5, 46.0, 47.0, sigma=2.0)
        expected = weatherbot.norm_cdf(0.25) - weatherbot.norm_cdf(-0.25)
        self.assertAlmostEqual(prob, expected, places=6)

    def test_interior_bucket_forecast_far_outside_is_near_zero(self):
        with patch.object(weatherbot, "STRAT_PROB_MODEL", True):
            prob = weatherbot.bucket_prob(100.0, 46.0, 47.0, sigma=2.0)
        self.assertLess(prob, 0.001)

    def test_interior_bucket_flag_off_remains_binary(self):
        with patch.object(weatherbot, "STRAT_PROB_MODEL", False):
            self.assertEqual(weatherbot.bucket_prob(46.5, 46.0, 47.0), 1.0)
            self.assertEqual(weatherbot.bucket_prob(50.0, 46.0, 47.0), 0.0)

    def test_edge_buckets_use_cdf_regardless_of_flag(self):
        # Lower-edge bucket (t_low=-999) always uses CDF — flag has no effect
        with patch.object(weatherbot, "STRAT_PROB_MODEL", False):
            p_off = weatherbot.bucket_prob(27.0, -999, 32.0, sigma=2.0)
        with patch.object(weatherbot, "STRAT_PROB_MODEL", True):
            p_on = weatherbot.bucket_prob(27.0, -999, 32.0, sigma=2.0)
        self.assertAlmostEqual(p_off, p_on, places=6)


class TestBetSizeWithTimeDecay(unittest.TestCase):
    """bet_size() horizon multiplier behaviour with STRAT_TIME_DECAY toggled."""

    def test_flag_off_ignores_horizon_days(self):
        with (
            patch.object(weatherbot, "STRAT_TIME_DECAY", False),
            patch.object(weatherbot, "MAX_BET", 500.0),
        ):
            no_horizon = weatherbot.bet_size(0.1, 1000, horizon_days=None)
            with_horizon = weatherbot.bet_size(0.1, 1000, horizon_days=3)
        self.assertEqual(no_horizon, with_horizon)

    def test_horizon_0_no_decay(self):
        with (
            patch.object(weatherbot, "STRAT_TIME_DECAY", True),
            patch.object(weatherbot, "MAX_BET", 500.0),
        ):
            result = weatherbot.bet_size(0.1, 1000, horizon_days=0)
        self.assertAlmostEqual(result, 100.0, places=2)  # 0.1 * 1000 * 1.0

    def test_horizon_1_80_percent(self):
        with (
            patch.object(weatherbot, "STRAT_TIME_DECAY", True),
            patch.object(weatherbot, "MAX_BET", 500.0),
        ):
            result = weatherbot.bet_size(0.1, 1000, horizon_days=1)
        self.assertAlmostEqual(result, 80.0, places=2)  # 0.1 * 1000 * 0.8

    def test_horizon_2_60_percent(self):
        with (
            patch.object(weatherbot, "STRAT_TIME_DECAY", True),
            patch.object(weatherbot, "MAX_BET", 500.0),
        ):
            result = weatherbot.bet_size(0.1, 1000, horizon_days=2)
        self.assertAlmostEqual(result, 60.0, places=2)  # 0.1 * 1000 * 0.6

    def test_horizon_3_40_percent(self):
        with (
            patch.object(weatherbot, "STRAT_TIME_DECAY", True),
            patch.object(weatherbot, "MAX_BET", 500.0),
        ):
            result = weatherbot.bet_size(0.1, 1000, horizon_days=3)
        self.assertAlmostEqual(result, 40.0, places=2)  # 0.1 * 1000 * 0.4

    def test_unknown_horizon_defaults_to_full_size(self):
        # horizon_days=5 not in _HORIZON_MULT → dict.get default is 1.0
        with (
            patch.object(weatherbot, "STRAT_TIME_DECAY", True),
            patch.object(weatherbot, "MAX_BET", 500.0),
        ):
            result = weatherbot.bet_size(0.1, 1000, horizon_days=5)
        self.assertAlmostEqual(result, 100.0, places=2)

    def test_none_horizon_no_decay_even_with_flag_on(self):
        # Guard condition: `horizon_days is not None` prevents decay when None
        with (
            patch.object(weatherbot, "STRAT_TIME_DECAY", True),
            patch.object(weatherbot, "MAX_BET", 500.0),
        ):
            result = weatherbot.bet_size(0.1, 1000, horizon_days=None)
        self.assertAlmostEqual(result, 100.0, places=2)


class TestDynamicEvScaling(unittest.TestCase):
    """Verify the STRAT_DYNAMIC_EV formula: MIN_EV * max(1.0, sigma / SIGMA_REF)."""

    def _effective_min_ev(self, sigma):
        """Replicates the inline formula from scan_and_update."""
        if weatherbot.STRAT_DYNAMIC_EV:
            return weatherbot.MIN_EV * max(1.0, sigma / weatherbot.SIGMA_REF)
        return weatherbot.MIN_EV

    def test_flag_off_always_returns_min_ev(self):
        with patch.object(weatherbot, "STRAT_DYNAMIC_EV", False):
            for sigma in [0.5, 2.0, 5.0]:
                self.assertEqual(self._effective_min_ev(sigma), weatherbot.MIN_EV)

    def test_sigma_at_ref_no_scaling(self):
        with (
            patch.object(weatherbot, "STRAT_DYNAMIC_EV", True),
            patch.object(weatherbot, "SIGMA_REF", 2.0),
        ):
            result = self._effective_min_ev(2.0)
        self.assertAlmostEqual(result, weatherbot.MIN_EV, places=6)

    def test_sigma_below_ref_no_scaling(self):
        # max(1.0, 1.0/2.0) = 1.0 → threshold unchanged
        with (
            patch.object(weatherbot, "STRAT_DYNAMIC_EV", True),
            patch.object(weatherbot, "SIGMA_REF", 2.0),
        ):
            result = self._effective_min_ev(1.0)
        self.assertAlmostEqual(result, weatherbot.MIN_EV, places=6)

    def test_sigma_double_ref_doubles_threshold(self):
        with (
            patch.object(weatherbot, "STRAT_DYNAMIC_EV", True),
            patch.object(weatherbot, "MIN_EV", 0.10),
            patch.object(weatherbot, "SIGMA_REF", 2.0),
        ):
            result = self._effective_min_ev(4.0)  # 4.0/2.0 = 2.0
        self.assertAlmostEqual(result, 0.20, places=6)

    def test_sigma_one_and_half_ref_scales_proportionally(self):
        with (
            patch.object(weatherbot, "STRAT_DYNAMIC_EV", True),
            patch.object(weatherbot, "MIN_EV", 0.10),
            patch.object(weatherbot, "SIGMA_REF", 2.0),
        ):
            result = self._effective_min_ev(3.0)  # 3.0/2.0 = 1.5
        self.assertAlmostEqual(result, 0.15, places=6)


class TestCheckStopsAndExits(unittest.TestCase):
    """Tests for _check_stops_and_exits — trailing stop tiers and forecast-shift guards."""

    _SNAP = {"ts": "2026-01-01T00:00:00Z"}
    _LOC  = {"unit": "F", "name": "TestCity"}

    def _mkt(self, entry=0.40, stop_price=None, trailing_activated=False,
             status="open", bucket_low=32.0, bucket_high=40.0):
        pos = {
            "market_id": "m1",
            "entry_price": entry,
            "shares": round(2.0 / entry, 4),
            "cost": 2.0,
            "stop_price": stop_price if stop_price is not None else round(entry * 0.80, 4),
            "trailing_activated": trailing_activated,
            "status": status,
            "bucket_low": bucket_low,
            "bucket_high": bucket_high,
        }
        return {"position": pos, "date": "2026-01-01"}

    def _outcomes(self, bid, market_id="m1"):
        return [{"market_id": market_id, "bid": bid, "price": bid}]

    # ------------------------------------------------------------------
    # Two-tier trailing stop
    # ------------------------------------------------------------------

    def test_trailing_above_092_uses_90_pct(self):
        """current_price >= 0.92 → new stop = price * 0.90."""
        mkt = self._mkt(entry=0.50, stop_price=0.80, trailing_activated=True)
        weatherbot._check_stops_and_exits(
            mkt, self._outcomes(bid=0.95), self._SNAP, self._LOC,
            forecast_temp=None, balance=1000.0,
        )
        self.assertAlmostEqual(mkt["position"]["stop_price"], round(0.95 * 0.90, 4))

    def test_trailing_below_092_uses_75_pct(self):
        """current_price in [entry*1.20, 0.92) → new stop = price * 0.75."""
        mkt = self._mkt(entry=0.50, stop_price=0.60, trailing_activated=True)
        weatherbot._check_stops_and_exits(
            mkt, self._outcomes(bid=0.85), self._SNAP, self._LOC,
            forecast_temp=None, balance=1000.0,
        )
        self.assertAlmostEqual(mkt["position"]["stop_price"], round(0.85 * 0.75, 4))

    def test_trailing_first_activation_sets_breakeven(self):
        """First time current_price >= entry*1.20: stop rises to entry (breakeven)."""
        mkt = self._mkt(entry=0.50, stop_price=0.40, trailing_activated=False)
        weatherbot._check_stops_and_exits(
            mkt, self._outcomes(bid=0.62), self._SNAP, self._LOC,
            forecast_temp=None, balance=1000.0,
        )
        self.assertEqual(mkt["position"]["stop_price"], 0.50)
        self.assertTrue(mkt["position"]["trailing_activated"])

    def test_trailing_not_activated_below_threshold(self):
        """current_price < entry*1.20: trailing stop never activates, stop unchanged."""
        mkt = self._mkt(entry=0.50, stop_price=0.40, trailing_activated=False)
        weatherbot._check_stops_and_exits(
            mkt, self._outcomes(bid=0.55), self._SNAP, self._LOC,
            forecast_temp=None, balance=1000.0,
        )
        self.assertEqual(mkt["position"]["stop_price"], 0.40)
        self.assertFalse(mkt["position"]["trailing_activated"])

    # ------------------------------------------------------------------
    # forecast_changed must not overwrite stop_loss
    # ------------------------------------------------------------------

    def test_stop_loss_close_reason_not_overwritten_by_forecast_changed(self):
        """Stop fires first → close_reason stays stop_loss even when forecast also diverges."""
        mkt = self._mkt(entry=0.40, stop_price=0.30)
        # bid=0.25 triggers stop; forecast_temp=50 is outside bucket [32,40]
        weatherbot._check_stops_and_exits(
            mkt, self._outcomes(bid=0.25), self._SNAP, self._LOC,
            forecast_temp=50.0, balance=1000.0,
        )
        self.assertEqual(mkt["position"]["close_reason"], "stop_loss")
        self.assertEqual(mkt["position"]["status"], "closed")

    def test_stop_loss_balance_not_double_counted(self):
        """Balance is credited exactly once, not once for stop and again for forecast_changed."""
        entry = 0.40
        shares = round(2.0 / entry, 4)
        initial = 1000.0
        mkt = self._mkt(entry=entry, stop_price=0.30)
        balance, closed = weatherbot._check_stops_and_exits(
            mkt, self._outcomes(bid=0.25), self._SNAP, self._LOC,
            forecast_temp=50.0, balance=initial,
        )
        expected_pnl = round((0.25 - entry) * shares, 2)
        self.assertAlmostEqual(balance, initial + 2.0 + expected_pnl, places=5)
        self.assertEqual(closed, 1)

    # ------------------------------------------------------------------
    # Suppress forecast_changed when market price >= 0.80
    # ------------------------------------------------------------------

    def test_forecast_changed_suppressed_at_or_above_080(self):
        """current_price >= 0.80: position stays open even when forecast leaves the bucket."""
        mkt = self._mkt(entry=0.40)
        balance, closed = weatherbot._check_stops_and_exits(
            mkt, self._outcomes(bid=0.85), self._SNAP, self._LOC,
            forecast_temp=50.0, balance=1000.0,
        )
        self.assertEqual(mkt["position"]["status"], "open")
        self.assertEqual(closed, 0)

    def test_forecast_changed_still_fires_below_080(self):
        """current_price < 0.80: forecast_changed exit fires normally."""
        mkt = self._mkt(entry=0.40)
        _, closed = weatherbot._check_stops_and_exits(
            mkt, self._outcomes(bid=0.50), self._SNAP, self._LOC,
            forecast_temp=50.0, balance=1000.0,
        )
        self.assertEqual(mkt["position"]["close_reason"], "forecast_changed")
        self.assertEqual(closed, 1)


if __name__ == "__main__":
    unittest.main()
