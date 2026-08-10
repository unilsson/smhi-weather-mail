import importlib.util
import os
import unittest
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

MODULE_PATH = Path(__file__).resolve().parents[1] / "src" / "smhi_weather_mail.py"
SPEC = importlib.util.spec_from_file_location("smhi_weather_mail", MODULE_PATH)
weather = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
import sys
sys.modules[SPEC.name] = weather
SPEC.loader.exec_module(weather)


def entry(valid, interval_start, temp, symbol=1, precip=0.0, probability=0):
    return {
        "time": valid,
        "intervalParametersStartTime": interval_start,
        "data": {
            "air_temperature": temp,
            "wind_from_direction": 225,
            "wind_speed": 3.5,
            "wind_speed_of_gust": 7.2,
            "relative_humidity": 65,
            "precipitation_amount_mean": precip,
            "probability_of_precipitation": probability,
            "symbol_code": symbol,
        },
    }


class WeatherMailTests(unittest.TestCase):
    def setUp(self):
        self.config = weather.Config(
            latitude=59.2,
            longitude=18.1,
            location_name="Testplats",
            timezone_name="Europe/Stockholm",
            mail_from="",
            mail_to=(),
            smtp_host="",
            smtp_port=587,
            smtp_user="",
            smtp_password="",
            smtp_security="starttls",
            smtp_timeout=20,
        )

    def test_symbol_mapping(self):
        self.assertEqual(weather.symbol_info(1), ("☀️", "Klart"))
        self.assertEqual(weather.symbol_info(27), ("❄️", "Kraftigt snöfall"))

    def test_compass_direction(self):
        self.assertEqual(weather.compass_direction(0), "N")
        self.assertEqual(weather.compass_direction(90), "O")
        self.assertEqual(weather.compass_direction(225), "SV")

    def test_local_day_filter_handles_utc(self):
        forecast = {
            "timeSeries": [
                entry(
                    "2026-08-09T23:00:00Z",
                    "2026-08-09T22:00:00Z",
                    12,
                ),
                entry(
                    "2026-08-10T06:00:00Z",
                    "2026-08-10T05:00:00Z",
                    17,
                ),
            ]
        }
        tz = ZoneInfo("Europe/Stockholm")
        result = weather.entries_for_local_day(
            forecast, datetime(2026, 8, 10).date(), tz
        )
        # 23:00 UTC on Aug 9 is 01:00 local on Aug 10 in CEST.
        self.assertEqual(len(result), 2)

    def test_precipitation_integrates_interval_rate(self):
        forecast = {
            "timeSeries": [
                entry(
                    "2026-08-10T10:00:00Z",
                    "2026-08-10T09:00:00Z",
                    18,
                    precip=0.5,
                ),
                entry(
                    "2026-08-10T11:00:00Z",
                    "2026-08-10T10:00:00Z",
                    19,
                    precip=1.0,
                ),
            ]
        }
        tz = ZoneInfo("Europe/Stockholm")
        entries = weather.entries_for_local_day(
            forecast, datetime(2026, 8, 10).date(), tz
        )
        self.assertAlmostEqual(
            weather.accumulated_precipitation(
                entries, datetime(2026, 8, 10).date(), tz
            ),
            1.5,
        )


    def test_precipitation_includes_interval_ending_at_next_midnight(self):
        tz = ZoneInfo("Europe/Stockholm")
        day = datetime(2026, 8, 10).date()
        entries = [
            entry(
                "2026-08-10T22:00:00Z",  # 00:00 Aug 11 CEST
                "2026-08-10T21:00:00Z",  # 23:00 Aug 10 CEST
                15,
                precip=0.7,
            )
        ]
        self.assertAlmostEqual(
            weather.accumulated_precipitation(entries, day, tz),
            0.7,
        )

    def test_summary(self):
        forecast = {
            "referenceTime": "2026-08-10T04:30:00Z",
            "timeSeries": [
                entry(
                    "2026-08-10T06:00:00Z",
                    "2026-08-10T05:00:00Z",
                    15,
                    symbol=2,
                ),
                entry(
                    "2026-08-10T10:00:00Z",
                    "2026-08-10T09:00:00Z",
                    21,
                    symbol=1,
                ),
                entry(
                    "2026-08-10T14:00:00Z",
                    "2026-08-10T13:00:00Z",
                    23,
                    symbol=3,
                    precip=0.2,
                    probability=40,
                ),
                entry(
                    "2026-08-10T18:00:00Z",
                    "2026-08-10T17:00:00Z",
                    18,
                    symbol=5,
                ),
            ],
        }
        summary = weather.summarize(
            forecast,
            self.config,
            now=datetime(2026, 8, 10, 4, 30, tzinfo=timezone.utc),
        )
        self.assertEqual(summary["date_text"], "måndag 10 augusti 2026")
        self.assertEqual(summary["temp_min"], 15)
        self.assertEqual(summary["temp_max"], 23)
        self.assertEqual(summary["precip_probability_max"], 40)
        self.assertEqual(len(summary["points"]), 4)


if __name__ == "__main__":
    unittest.main()
