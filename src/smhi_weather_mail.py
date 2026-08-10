#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import logging
import os
import smtplib
import ssl
import sys
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

SMHI_BASE_URL = (
    "https://opendata-download-metfcst.smhi.se/api/category/"
    "snow1g/version/1/geotype/point"
)

PARAMETERS = (
    "air_temperature",
    "wind_from_direction",
    "wind_speed",
    "wind_speed_of_gust",
    "relative_humidity",
    "precipitation_amount_mean",
    "probability_of_precipitation",
    "symbol_code",
)

SWEDISH_WEEKDAYS = (
    "måndag", "tisdag", "onsdag", "torsdag",
    "fredag", "lördag", "söndag",
)

SWEDISH_MONTHS = (
    "januari", "februari", "mars", "april", "maj", "juni",
    "juli", "augusti", "september", "oktober", "november", "december",
)

SYMBOLS: dict[int, tuple[str, str]] = {
    1: ("☀️", "Klart"),
    2: ("🌤️", "Mest klart"),
    3: ("⛅", "Växlande molnighet"),
    4: ("🌥️", "Halvklart"),
    5: ("☁️", "Molnigt"),
    6: ("☁️", "Mulet"),
    7: ("🌫️", "Dimma"),
    8: ("🌦️", "Lätta regnskurar"),
    9: ("🌦️", "Regnskurar"),
    10: ("🌧️", "Kraftiga regnskurar"),
    11: ("⛈️", "Åskskurar"),
    12: ("🌨️", "Lätta snöblandade skurar"),
    13: ("🌨️", "Snöblandade skurar"),
    14: ("🌨️", "Kraftiga snöblandade skurar"),
    15: ("🌨️", "Lätta snöbyar"),
    16: ("🌨️", "Snöbyar"),
    17: ("❄️", "Kraftiga snöbyar"),
    18: ("🌦️", "Lätt regn"),
    19: ("🌧️", "Regn"),
    20: ("🌧️", "Kraftigt regn"),
    21: ("⛈️", "Åska"),
    22: ("🌨️", "Lätt snöblandat regn"),
    23: ("🌨️", "Snöblandat regn"),
    24: ("🌨️", "Kraftigt snöblandat regn"),
    25: ("🌨️", "Lätt snöfall"),
    26: ("❄️", "Snöfall"),
    27: ("❄️", "Kraftigt snöfall"),
}


@dataclass(frozen=True)
class Config:
    latitude: float
    longitude: float
    location_name: str
    timezone_name: str
    mail_from: str
    mail_to: tuple[str, ...]
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str
    smtp_security: str
    smtp_timeout: int

    @classmethod
    def from_env(cls, require_mail: bool = True) -> "Config":
        def required(name: str) -> str:
            value = os.getenv(name, "").strip()
            if not value:
                raise ValueError(f"Miljövariabeln {name} saknas.")
            return value

        latitude = float(required("SMHI_LAT"))
        longitude = float(required("SMHI_LON"))
        if not (-90 <= latitude <= 90):
            raise ValueError("SMHI_LAT måste ligga mellan -90 och 90.")
        if not (-180 <= longitude <= 180):
            raise ValueError("SMHI_LON måste ligga mellan -180 och 180.")

        mail_to_raw = os.getenv("MAIL_TO", "")
        mail_to = tuple(
            item.strip() for item in mail_to_raw.split(",") if item.strip()
        )

        smtp_host = os.getenv("SMTP_HOST", "").strip()
        mail_from = os.getenv("MAIL_FROM", "").strip()

        if require_mail:
            if not smtp_host:
                raise ValueError("Miljövariabeln SMTP_HOST saknas.")
            if not mail_from:
                raise ValueError("Miljövariabeln MAIL_FROM saknas.")
            if not mail_to:
                raise ValueError("Miljövariabeln MAIL_TO saknas.")

        smtp_security = os.getenv("SMTP_SECURITY", "starttls").strip().lower()
        if smtp_security not in {"starttls", "ssl", "none"}:
            raise ValueError("SMTP_SECURITY måste vara starttls, ssl eller none.")

        default_port = 465 if smtp_security == "ssl" else 587

        return cls(
            latitude=latitude,
            longitude=longitude,
            location_name=os.getenv("LOCATION_NAME", "Hemma").strip() or "Hemma",
            timezone_name=os.getenv("TIMEZONE", "Europe/Stockholm").strip()
            or "Europe/Stockholm",
            mail_from=mail_from,
            mail_to=mail_to,
            smtp_host=smtp_host,
            smtp_port=int(os.getenv("SMTP_PORT", str(default_port))),
            smtp_user=os.getenv("SMTP_USER", "").strip(),
            smtp_password=os.getenv("SMTP_PASSWORD", ""),
            smtp_security=smtp_security,
            smtp_timeout=int(os.getenv("SMTP_TIMEOUT", "20")),
        )


def parse_iso_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def coordinate_for_url(value: float) -> str:
    """SMHI accepts at most six decimal places in point coordinates."""
    return f"{value:.6f}".rstrip("0").rstrip(".")


def fetch_forecast(config: Config, timeseries: int = 30) -> dict[str, Any]:
    # Fetch all parameters from SMHI.
    # This avoids interoperability problems with comma-separated parameter
    # filtering while keeping the response small by limiting timeSeries.
    query = urlencode({
        "timeseries": timeseries,
    })
    longitude = coordinate_for_url(config.longitude)
    latitude = coordinate_for_url(config.latitude)
    url = (
        f"{SMHI_BASE_URL}/lon/{longitude}/lat/{latitude}/data.json"
        f"?{query}"
    )
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "smhi-weather-mail/1.0",
        },
    )

    logging.info("Hämtar prognos från SMHI för %s.", config.location_name)
    try:
        with urlopen(request, timeout=20) as response:
            payload = response.read().decode("utf-8")
    except HTTPError as exc:
        if exc.code == 404:
            raise RuntimeError(
                "SMHI svarade med HTTP 404. "
                f"Anropade koordinater: lon={longitude}, lat={latitude}."
            ) from exc
        raise RuntimeError(f"SMHI svarade med HTTP {exc.code}.") from exc
    except URLError as exc:
        raise RuntimeError(f"Kunde inte ansluta till SMHI: {exc.reason}") from exc

    data = json.loads(payload)
    if not isinstance(data.get("timeSeries"), list):
        raise RuntimeError("SMHI-svaret saknar timeSeries.")
    return data


def local_day_bounds(day: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    start_local = datetime.combine(day, time.min, tzinfo=tz)
    end_local = start_local + timedelta(days=1)
    return start_local, end_local


def entries_for_local_day(
    forecast: dict[str, Any], day: date, tz: ZoneInfo
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for entry in forecast.get("timeSeries", []):
        valid_utc = parse_iso_utc(entry["time"])
        valid_local = valid_utc.astimezone(tz)
        if valid_local.date() == day:
            copied = dict(entry)
            copied["_valid_utc"] = valid_utc
            copied["_valid_local"] = valid_local
            result.append(copied)
    return result


def symbol_info(code: Any) -> tuple[str, str]:
    try:
        return SYMBOLS[int(code)]
    except (TypeError, ValueError, KeyError):
        return ("🌡️", "Väder")


def compass_direction(degrees: Any) -> str:
    try:
        value = float(degrees) % 360
    except (TypeError, ValueError):
        return "–"
    directions = ("N", "NO", "O", "SO", "S", "SV", "V", "NV")
    return directions[int((value + 22.5) // 45) % 8]


def fmt_number(value: Any, digits: int = 1) -> str:
    if value is None:
        return "–"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "–"
    text = f"{number:.{digits}f}"
    if digits > 0:
        text = text.rstrip("0").rstrip(".")
    return text.replace(".", ",")


def nearest_entry(
    entries: list[dict[str, Any]], hour: int
) -> dict[str, Any] | None:
    if not entries:
        return None
    tz = entries[0]["_valid_local"].tzinfo
    day = entries[0]["_valid_local"].date()
    target = datetime.combine(day, time(hour=hour), tzinfo=tz)
    return min(entries, key=lambda e: abs(e["_valid_local"] - target))


def accumulated_precipitation(
    entries: list[dict[str, Any]], day: date, tz: ZoneInfo
) -> float:
    """
    SNOW precipitation values are interval parameters. The API documents
    intervalParametersStartTime and states that the unit remains mm/h.
    Integrate the rate over the portion of each interval that falls within
    the requested local day.
    """
    day_start_local, day_end_local = local_day_bounds(day, tz)
    day_start_utc = day_start_local.astimezone(timezone.utc)
    day_end_utc = day_end_local.astimezone(timezone.utc)

    total = 0.0
    for entry in entries:
        data = entry.get("data", {})
        rate = data.get("precipitation_amount_mean")
        interval_start = entry.get("intervalParametersStartTime")
        if rate is None or interval_start is None:
            continue

        try:
            rate_value = float(rate)
            start_utc = parse_iso_utc(interval_start)
            end_utc = parse_iso_utc(entry["time"])
        except (TypeError, ValueError):
            continue

        overlap_start = max(start_utc, day_start_utc)
        overlap_end = min(end_utc, day_end_utc)
        if overlap_end <= overlap_start:
            continue

        hours = (overlap_end - overlap_start).total_seconds() / 3600
        total += max(0.0, rate_value) * hours

    return total


def swedish_date(day: date) -> str:
    return (
        f"{SWEDISH_WEEKDAYS[day.weekday()]} {day.day} "
        f"{SWEDISH_MONTHS[day.month - 1]} {day.year}"
    )


def summarize(
    forecast: dict[str, Any],
    config: Config,
    now: datetime | None = None,
) -> dict[str, Any]:
    tz = ZoneInfo(config.timezone_name)
    local_now = now.astimezone(tz) if now else datetime.now(tz)
    day = local_now.date()
    entries = entries_for_local_day(forecast, day, tz)

    # At a morning run this normally contains all remaining hours of the day.
    # If the service is run manually late at night, fail clearly rather than
    # sending an empty or misleading message.
    if not entries:
        raise RuntimeError("SMHI-svaret innehåller inga prognospunkter för idag.")

    temperatures = [
        float(e["data"]["air_temperature"])
        for e in entries
        if e.get("data", {}).get("air_temperature") is not None
    ]
    wind_speeds = [
        float(e["data"]["wind_speed"])
        for e in entries
        if e.get("data", {}).get("wind_speed") is not None
    ]
    gusts = [
        float(e["data"]["wind_speed_of_gust"])
        for e in entries
        if e.get("data", {}).get("wind_speed_of_gust") is not None
    ]
    precip_probs = [
        float(e["data"]["probability_of_precipitation"])
        for e in entries
        if e.get("data", {}).get("probability_of_precipitation") is not None
    ]

    if not temperatures:
        raise RuntimeError("SMHI-svaret saknar temperaturdata för idag.")

    representative = nearest_entry(entries, 12) or entries[0]
    rep_symbol = symbol_info(representative.get("data", {}).get("symbol_code"))

    target_hours = (8, 12, 16, 20)
    points = []
    used_times: set[str] = set()
    for target_hour in target_hours:
        entry = nearest_entry(entries, target_hour)
        if entry is None:
            continue
        local_time = entry["_valid_local"]
        key = local_time.isoformat()
        if key in used_times:
            continue
        used_times.add(key)
        data = entry.get("data", {})
        emoji, description = symbol_info(data.get("symbol_code"))
        points.append({
            "time": local_time.strftime("%H:%M"),
            "emoji": emoji,
            "description": description,
            "temperature": data.get("air_temperature"),
            "wind_speed": data.get("wind_speed"),
            "wind_direction": compass_direction(data.get("wind_from_direction")),
            "gust": data.get("wind_speed_of_gust"),
            "precip_rate": data.get("precipitation_amount_mean"),
            "precip_probability": data.get("probability_of_precipitation"),
            "humidity": data.get("relative_humidity"),
        })

    reference_time = forecast.get("referenceTime")
    reference_local = None
    if reference_time:
        reference_local = parse_iso_utc(reference_time).astimezone(tz)

    return {
        "date": day,
        "date_text": swedish_date(day),
        "location": config.location_name,
        "emoji": rep_symbol[0],
        "description": rep_symbol[1],
        "temp_min": min(temperatures),
        "temp_max": max(temperatures),
        "wind_max": max(wind_speeds) if wind_speeds else None,
        "gust_max": max(gusts) if gusts else None,
        "precip_total": accumulated_precipitation(forecast.get("timeSeries", []), day, tz),
        "precip_probability_max": max(precip_probs) if precip_probs else None,
        "points": points,
        "reference_local": reference_local,
    }


def build_plain_text(summary: dict[str, Any]) -> str:
    lines = [
        f"Dagens väder – {summary['location']}",
        summary["date_text"].capitalize(),
        "",
        f"{summary['emoji']} {summary['description']}",
        f"Temperatur: {fmt_number(summary['temp_min'])}–{fmt_number(summary['temp_max'])} °C",
        f"Vind: upp till {fmt_number(summary['wind_max'])} m/s",
    ]

    if summary["gust_max"] is not None:
        lines.append(f"Vindbyar: upp till {fmt_number(summary['gust_max'])} m/s")

    lines.extend([
        f"Nederbörd: cirka {fmt_number(summary['precip_total'])} mm",
        (
            "Högsta nederbördsrisk: "
            f"{fmt_number(summary['precip_probability_max'], 0)} %"
        ),
        "",
    ])

    for point in summary["points"]:
        lines.extend([
            f"{point['time']}  {point['emoji']} {point['description']}",
            (
                f"  {fmt_number(point['temperature'])} °C · "
                f"vind {fmt_number(point['wind_speed'])} m/s "
                f"{point['wind_direction']} · "
                f"nederbörd {fmt_number(point['precip_rate'])} mm/h "
                f"({fmt_number(point['precip_probability'], 0)} %)"
            ),
            "",
        ])

    if summary["reference_local"] is not None:
        lines.append(
            "SMHI-prognosens referenstid: "
            + summary["reference_local"].strftime("%Y-%m-%d %H:%M %Z")
        )
    lines.append("Källa: SMHI Open Data, SNOW1gv1")
    return "\n".join(lines)


def build_html(summary: dict[str, Any]) -> str:
    esc_location = html.escape(summary["location"])
    rows = []
    for point in summary["points"]:
        rows.append(f"""
        <tr>
          <td style="padding:12px 8px;border-bottom:1px solid #e5e7eb;font-weight:700;">
            {html.escape(point['time'])}
          </td>
          <td style="padding:12px 8px;border-bottom:1px solid #e5e7eb;">
            <span style="font-size:22px;">{html.escape(point['emoji'])}</span>
            {html.escape(point['description'])}
          </td>
          <td style="padding:12px 8px;border-bottom:1px solid #e5e7eb;white-space:nowrap;">
            {fmt_number(point['temperature'])} °C
          </td>
          <td style="padding:12px 8px;border-bottom:1px solid #e5e7eb;white-space:nowrap;">
            {fmt_number(point['wind_speed'])} m/s {html.escape(point['wind_direction'])}
          </td>
          <td style="padding:12px 8px;border-bottom:1px solid #e5e7eb;white-space:nowrap;">
            {fmt_number(point['precip_rate'])} mm/h ·
            {fmt_number(point['precip_probability'], 0)} %
          </td>
        </tr>
        """)

    reference = ""
    if summary["reference_local"] is not None:
        reference = (
            " · Prognosens referenstid "
            + summary["reference_local"].strftime("%Y-%m-%d %H:%M %Z")
        )

    return f"""<!doctype html>
<html lang="sv">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width">
  <title>Dagens väder – {esc_location}</title>
</head>
<body style="margin:0;padding:0;background:#f3f4f6;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;color:#111827;">
  <div style="max-width:680px;margin:0 auto;padding:24px 12px;">
    <div style="background:#ffffff;border-radius:16px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.08);">
      <div style="padding:26px 28px;background:#eef6ff;">
        <div style="font-size:14px;color:#4b5563;text-transform:uppercase;letter-spacing:.06em;">
          Dagens väder · {esc_location}
        </div>
        <div style="font-size:22px;font-weight:700;margin-top:6px;">
          {html.escape(summary['date_text'].capitalize())}
        </div>
        <div style="font-size:46px;margin-top:18px;">{html.escape(summary['emoji'])}</div>
        <div style="font-size:28px;font-weight:700;margin-top:4px;">
          {html.escape(summary['description'])}
        </div>
        <div style="font-size:20px;margin-top:8px;">
          {fmt_number(summary['temp_min'])}–{fmt_number(summary['temp_max'])} °C
        </div>
      </div>

      <div style="padding:22px 28px;">
        <table role="presentation" style="width:100%;border-collapse:collapse;margin-bottom:22px;">
          <tr>
            <td style="padding:8px 12px;background:#f9fafb;border-radius:10px;">
              <strong>💨 Vind</strong><br>
              upp till {fmt_number(summary['wind_max'])} m/s
            </td>
            <td style="width:12px;"></td>
            <td style="padding:8px 12px;background:#f9fafb;border-radius:10px;">
              <strong>🌧 Nederbörd</strong><br>
              cirka {fmt_number(summary['precip_total'])} mm
            </td>
          </tr>
        </table>

        <div style="font-size:16px;margin:0 0 18px;">
          Högsta nederbördsrisk:
          <strong>{fmt_number(summary['precip_probability_max'], 0)} %</strong>
          · Vindbyar upp till <strong>{fmt_number(summary['gust_max'])} m/s</strong>
        </div>

        <table role="presentation" style="width:100%;border-collapse:collapse;font-size:14px;">
          <thead>
            <tr style="text-align:left;color:#6b7280;">
              <th style="padding:8px;">Tid</th>
              <th style="padding:8px;">Väder</th>
              <th style="padding:8px;">Temp.</th>
              <th style="padding:8px;">Vind</th>
              <th style="padding:8px;">Nederbörd</th>
            </tr>
          </thead>
          <tbody>
            {''.join(rows)}
          </tbody>
        </table>
      </div>

      <div style="padding:16px 28px;background:#f9fafb;color:#6b7280;font-size:12px;">
        Källa: SMHI Open Data, SNOW1gv1{html.escape(reference)}
      </div>
    </div>
  </div>
</body>
</html>
"""


def make_message(config: Config, summary: dict[str, Any]) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = (
        f"Dagens väder – {summary['location']} – {summary['date_text']}"
    )
    message["From"] = config.mail_from
    message["To"] = ", ".join(config.mail_to)
    message.set_content(build_plain_text(summary))
    message.add_alternative(build_html(summary), subtype="html")
    return message


def send_message(config: Config, message: EmailMessage) -> None:
    context = ssl.create_default_context()

    if config.smtp_security == "ssl":
        smtp: smtplib.SMTP = smtplib.SMTP_SSL(
            config.smtp_host,
            config.smtp_port,
            timeout=config.smtp_timeout,
            context=context,
        )
    else:
        smtp = smtplib.SMTP(
            config.smtp_host,
            config.smtp_port,
            timeout=config.smtp_timeout,
        )

    with smtp:
        smtp.ehlo()
        if config.smtp_security == "starttls":
            smtp.starttls(context=context)
            smtp.ehlo()
        if config.smtp_user:
            smtp.login(config.smtp_user, config.smtp_password)
        smtp.send_message(message)

    logging.info("Vädermail skickat till %s.", ", ".join(config.mail_to))


def load_forecast_file(path: str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Skicka ett dagligt vädermail från SMHI SNOW1gv1."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Hämta prognosen och skriv mailet till stdout utan att skicka.",
    )
    parser.add_argument(
        "--forecast-file",
        help="Läs SMHI-JSON från fil i stället för att anropa API:t.",
    )
    parser.add_argument(
        "--html-output",
        help="Skriv renderad HTML till denna fil, användbart vid test.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(message)s",
    )

    try:
        config = Config.from_env(require_mail=not args.dry_run)
        forecast = (
            load_forecast_file(args.forecast_file)
            if args.forecast_file
            else fetch_forecast(config)
        )
        summary = summarize(forecast, config)
        plain = build_plain_text(summary)
        html_body = build_html(summary)

        if args.html_output:
            Path(args.html_output).write_text(html_body, encoding="utf-8")
            logging.info("HTML-preview skriven till %s.", args.html_output)

        if args.dry_run:
            print(plain)
            return 0

        message = make_message(config, summary)
        send_message(config, message)
        return 0

    except Exception as exc:
        logging.exception("Körningen misslyckades: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
