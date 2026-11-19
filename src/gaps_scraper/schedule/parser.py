"""Normalise iCalendar et développe les récurrences finies de GAPS."""

from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from zoneinfo import ZoneInfo

from dateutil.rrule import rrulestr
from icalendar import Calendar

from gaps_scraper.common.parsing import ParseError
from gaps_scraper.common.storage import canonical

LOCAL = ZoneInfo("Europe/Zurich")
MAX_OCCURRENCES = 5000


def moment(value):
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ParseError("Heure d'horaire sans fuseau : interprétation refusée.")
        return value.astimezone(LOCAL)
    if isinstance(value, date):
        return value
    raise ParseError("Date d'horaire inconnue.")


def key(value):
    return value.astimezone(timezone.utc).isoformat() if isinstance(value, datetime) else value.isoformat()


def identifier(*parts):
    return sha256(canonical(parts).encode()).hexdigest()


def parse_schedule(source, selection):
    try:
        return _parse_schedule(source, selection)
    except ParseError:
        raise
    except (ValueError, TypeError, KeyError, OverflowError, AttributeError):
        raise ParseError("Calendrier invalide ou construction iCalendar non reconnue.") from None


def _parse_schedule(source, selection):
    if not source.strip().startswith("BEGIN:VCALENDAR") or not source.strip().endswith("END:VCALENDAR"):
        raise ParseError("Calendrier incomplet ou réponse HTML.")
    calendar = Calendar.from_ical(source)
    if str(calendar.get("VERSION")) != "2.0" or any(c.errors for c in calendar.walk()):
        raise ParseError("Version ou propriété de calendrier invalide.")
    if any(c.name not in ("VTIMEZONE", "VEVENT") for c in calendar.subcomponents):
        raise ParseError("Composant de calendrier non pris en charge.")
    series, occurrences, seen = [], [], set()
    for event in calendar.walk("VEVENT"):
        uid = str(event.get("UID", ""))
        if not uid or uid in seen:
            raise ParseError("Identifiant de série absent ou dupliqué.")
        seen.add(uid)
        if any(prop in event for prop in ("RECURRENCE-ID", "RDATE", "EXRULE")):
            raise ParseError("Exception ou règle d'horaire non encore prise en charge.")
        if event.subcomponents:
            raise ParseError("Sous-composant d'événement non pris en charge.")
        start, end = moment(event.decoded("DTSTART")), moment(event.decoded("DTEND"))
        if isinstance(start, datetime) != isinstance(end, datetime) or end <= start:
            raise ParseError("Intervalle d'horaire incohérent.")
        duration = end-start
        if duration > timedelta(days=366):
            raise ParseError("Durée d'événement excessive.")
        status = str(event.get("STATUS", "CONFIRMED"))
        summary = str(event.get("SUMMARY", ""))
        if status not in ("CONFIRMED", "TENTATIVE", "CANCELLED") or not summary:
            raise ParseError("Statut ou intitulé d'horaire inconnu.")
        excluded = set()
        values = event.get("EXDATE", [])
        for item in values if isinstance(values, list) else [values]:
            for exclusion in item.dts:
                ex = moment(exclusion.dt)
                if isinstance(ex, datetime) != isinstance(start, datetime):
                    raise ParseError("Type de date exclue incohérent.")
                excluded.add(key(ex))
        rule = event.get("RRULE")
        rule_text = None
        if rule is not None:
            if not isinstance(start, datetime):
                raise ParseError("Récurrence de journées entières non encore prise en charge.")
            if rule.get("FREQ") not in (["WEEKLY"], ["DAILY"]):
                raise ParseError("Fréquence de récurrence non prise en charge.")
            if set(rule) - {"FREQ", "UNTIL", "COUNT", "INTERVAL", "BYDAY", "WKST"}:
                raise ParseError("Paramètre de récurrence non pris en charge.")
            if "COUNT" not in rule and "UNTIL" not in rule:
                raise ParseError("Récurrence sans fin explicite.")
            if "COUNT" in rule and "UNTIL" in rule:
                raise ParseError("Récurrence avec deux limites incompatibles.")
            if not 1 <= int(rule.get("INTERVAL", [1])[0]) <= 52:
                raise ParseError("Intervalle de récurrence invalide.")
            if "COUNT" in rule and (int(rule["COUNT"][0]) <= 0 or int(rule["COUNT"][0]) > 400):
                raise ParseError("Nombre de récurrences excessif.")
            if "UNTIL" in rule:
                until = moment(rule["UNTIL"][0])
                if not isinstance(until, datetime) or not start <= until <= start+timedelta(days=400):
                    raise ParseError("Fin de récurrence incohérente ou excessive.")
            rule_text = rule.to_ical().decode().rstrip(";")
            dates = rrulestr(rule_text, dtstart=start)
        else:
            dates = [start]
        series_id = identifier(selection["account_id"], selection["academic_year"]["value"],
                               selection["source_period"], uid)
        info = {"id": series_id, "source_uid": uid, "summary": summary,
                "description": str(event.get("DESCRIPTION", "")),
                "location": str(event.get("LOCATION", "")), "status": status,
                "all_day": not isinstance(start, datetime), "start": start.isoformat(), "end": end.isoformat(),
                "rrule": rule_text, "excluded_dates": sorted(excluded)}
        series.append(info)
        for index, occurrence_start in enumerate(dates):
            if index >= 400 or len(occurrences) >= MAX_OCCURRENCES:
                raise ParseError("Nombre d'occurrences excessif.")
            if isinstance(occurrence_start, datetime):
                occurrence_start = occurrence_start.astimezone(LOCAL)
                if occurrence_start > start+timedelta(days=400):
                    raise ParseError("Récurrence trop longue.")
            if key(occurrence_start) in excluded or status == "CANCELLED":
                continue
            occurrences.append({"id": identifier(series_id, key(occurrence_start)), "series_id": series_id,
                                "summary": summary, "description": info["description"],
                                "location": info["location"], "status": status,
                                "all_day": info["all_day"], "start": occurrence_start.isoformat(),
                                "end": (occurrence_start+duration).isoformat()})
    return {"schema_version": 1, "kind": "individual_schedule", **selection,
            "timezone": "Europe/Zurich", "series": sorted(series, key=lambda e: e["id"]),
            "occurrences": sorted(occurrences, key=lambda e: (e["start"], e["id"]))}
