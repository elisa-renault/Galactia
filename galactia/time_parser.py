from __future__ import annotations

import calendar
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from dateutil import parser as date_parser
from dateutil.relativedelta import relativedelta


@dataclass(frozen=True)
class TimeRangeResult:
    start: datetime
    end: datetime
    matched_rule: str
    confidence: str = "exact"
    is_explicit: bool = True
    notice: str | None = None


WEEKDAYS = {
    "lundi": 0,
    "mardi": 1,
    "mercredi": 2,
    "jeudi": 3,
    "vendredi": 4,
    "samedi": 5,
    "dimanche": 6,
}

MONTHS = {
    "janvier": 1,
    "fevrier": 2,
    "mars": 3,
    "avril": 4,
    "mai": 5,
    "juin": 6,
    "juillet": 7,
    "aout": 8,
    "septembre": 9,
    "octobre": 10,
    "novembre": 11,
    "decembre": 12,
}

ORDINALS = {
    "premier": 1,
    "1er": 1,
    "1": 1,
    "deuxieme": 2,
    "second": 2,
    "seconde": 2,
    "2e": 2,
    "2": 2,
    "troisieme": 3,
    "3e": 3,
    "3": 3,
    "quatrieme": 4,
    "4e": 4,
    "4": 4,
}

SEASONS = {
    "printemps": (3, 5),
    "ete": (6, 8),
    "automne": (9, 11),
    "hiver": (12, 2),
}

TIME_ABBREVIATION_ALIASES = (
    ("lun", "lundi"),
    ("mar", "mardi"),
    ("mer", "mercredi"),
    ("jeu", "jeudi"),
    ("ven", "vendredi"),
    ("sam", "samedi"),
    ("dim", "dimanche"),
    ("janv", "janvier"),
    ("fevr", "fevrier"),
    ("fev", "fevrier"),
    ("avr", "avril"),
    ("juil", "juillet"),
    ("sept", "septembre"),
    ("oct", "octobre"),
    ("nov", "novembre"),
    ("dec", "decembre"),
    ("aujd", "aujourd'hui"),
    ("auj", "aujourd'hui"),
    ("ajd", "aujourd'hui"),
    ("aprem", "apres-midi"),
    ("dern", "dernier"),
    ("der", "dernier"),
    ("dep", "depuis"),
    ("avt", "avant"),
    ("jusq", "jusqu'a"),
    ("trim", "trimestre"),
    ("tri", "trimestre"),
)


def normalize_time_text(text: str) -> str:
    normalized = (
        text.replace("’", "'")
        .replace("‘", "'")
        .replace("`", "'")
        .replace("´", "'")
        .replace("–", "-")
        .replace("—", "-")
    )
    normalized = unicodedata.normalize("NFKD", normalized)
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    normalized = normalized.lower()
    normalized = _expand_time_abbreviations(normalized)
    normalized = re.sub(r"[?!.,;:]+$", "", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()


def _expand_time_abbreviations(text: str) -> str:
    expanded = text
    for alias, replacement in TIME_ABBREVIATION_ALIASES:
        expanded = re.sub(
            rf"(?<!\w){re.escape(alias)}\.?(?!\w)",
            replacement,
            expanded,
        )
    expanded = re.sub(r"(?<!\w)(\d+)\s*mn\.?(?!\w)", r"\1 minutes", expanded)
    expanded = re.sub(r"(?<!\w)(\d+)\s*(?:jrs|jr|j)\.?(?!\w)", r"\1 jours", expanded)
    expanded = re.sub(r"(?<!\w)(\d+)\s*sem\.?(?!\w)", r"\1 semaines", expanded)
    return expanded


def parse_time_limit_deterministic(
    time_limit: str | None,
    *,
    now: datetime | None = None,
    timezone: str = "Europe/Paris",
    timezone_name: str | None = None,
) -> TimeRangeResult | None:
    if not time_limit:
        return None

    tz = ZoneInfo(timezone_name or timezone)
    current = now.astimezone(tz) if now else datetime.now(tz)
    text = normalize_time_text(time_limit)
    if not text:
        return None

    return (
        _parse_named_fixed_range(text, current, tz)
        or _parse_open_bounds(text, current, tz)
        or _parse_closed_range(text, current, tz)
        or _parse_relative_range(text, current, tz)
        or _parse_since_range(text, current, tz)
        or _parse_year_range(text, current, tz)
        or _parse_quarter_range(text, current, tz)
        or _parse_semester_range(text, current, tz)
        or _parse_season_range(text, current, tz)
        or _parse_month_range(text, current, tz)
        or _parse_weekday_range(text, current, tz)
        or _parse_explicit_date_range(text, current, tz)
    )


def _result(
    start: datetime,
    end: datetime,
    rule: str,
    *,
    confidence: str = "exact",
    notice: str | None = None,
) -> TimeRangeResult:
    return TimeRangeResult(
        start=start,
        end=end,
        matched_rule=rule,
        confidence=confidence,
        is_explicit=True,
        notice=notice,
    )


def _start_of_day(day: datetime, tz: ZoneInfo) -> datetime:
    return datetime.combine(day.date(), time.min, tzinfo=tz)


def _end_of_day(day: datetime, tz: ZoneInfo) -> datetime:
    return datetime.combine(day.date(), time(23, 59, 59), tzinfo=tz)


def _day_bounds(day: datetime, tz: ZoneInfo) -> tuple[datetime, datetime]:
    return _start_of_day(day, tz), _end_of_day(day, tz)


def _month_bounds(year: int, month: int, tz: ZoneInfo) -> tuple[datetime, datetime]:
    last_day = calendar.monthrange(year, month)[1]
    return (
        datetime(year, month, 1, 0, 0, 0, tzinfo=tz),
        datetime(year, month, last_day, 23, 59, 59, tzinfo=tz),
    )


def _year_bounds(year: int, tz: ZoneInfo) -> tuple[datetime, datetime]:
    return (
        datetime(year, 1, 1, 0, 0, 0, tzinfo=tz),
        datetime(year, 12, 31, 23, 59, 59, tzinfo=tz),
    )


def _part_bounds(day: datetime, part: str, tz: ZoneInfo) -> tuple[datetime, datetime]:
    base = day.date()
    if part == "matin":
        return (
            datetime.combine(base, time(0, 0, 0), tzinfo=tz),
            datetime.combine(base, time(11, 59, 59), tzinfo=tz),
        )
    if part in {"apres-midi", "apres midi"}:
        return (
            datetime.combine(base, time(12, 0, 0), tzinfo=tz),
            datetime.combine(base, time(17, 59, 59), tzinfo=tz),
        )
    if part == "soir":
        return (
            datetime.combine(base, time(18, 0, 0), tzinfo=tz),
            datetime.combine(base, time(23, 59, 59), tzinfo=tz),
        )
    return _day_bounds(day, tz)


def _parse_named_fixed_range(text: str, now: datetime, tz: ZoneInfo) -> TimeRangeResult | None:
    today_start, today_end = _day_bounds(now, tz)
    yesterday_start, yesterday_end = _day_bounds(now - timedelta(days=1), tz)

    if text in {
        "aujourd'hui",
        "aujourdhui",
        "d'aujourd'hui",
        "d'aujourdhui",
        "du jour",
        "ce jour",
        "jour",
        "la journee",
        "today",
    }:
        return _result(today_start, min(now, today_end), "today")
    if text == "hier":
        return _result(yesterday_start, yesterday_end, "yesterday")
    if text in {"ce matin", "matin"}:
        start, end = _part_bounds(now, "matin", tz)
        return _result(start, min(now, end), "this_morning", confidence="inferred")
    if text in {
        "apres-midi",
        "apres midi",
        "cet apres-midi",
        "cet apres midi",
        "cette apres-midi",
        "cette apres midi",
    }:
        start, end = _part_bounds(now, "apres-midi", tz)
        return _result(start, min(now, end), "this_afternoon", confidence="inferred")
    if text in {"ce soir", "soir"}:
        start, end = _part_bounds(now, "soir", tz)
        return _result(start, min(now, end) if now >= start else end, "this_evening", confidence="inferred")
    if text in {"cette semaine", "semaine courante"}:
        start = _start_of_day(now - timedelta(days=now.weekday()), tz)
        return _result(start, now, "current_week")
    if text in {"semaine derniere", "la semaine derniere"}:
        current_monday = _start_of_day(now - timedelta(days=now.weekday()), tz)
        start = current_monday - timedelta(days=7)
        end = current_monday - timedelta(seconds=1)
        return _result(start, end, "previous_week")
    if text in {"ce mois", "mois courant"}:
        start, _ = _month_bounds(now.year, now.month, tz)
        return _result(start, now, "current_month")
    return None


def _parse_relative_range(text: str, now: datetime, tz: ZoneInfo) -> TimeRangeResult | None:
    match = re.search(
        r"(?:les\s+)?(\d+)\s+derni(?:er|ere|ers|eres)\s+"
        r"(minutes?|min|heures?|h|jours?|jrs|jr|j|semaines?|sem|mois)",
        text,
    )
    if not match:
        return None

    amount = int(match.group(1))
    unit = match.group(2)
    start = _subtract_unit(now, amount, unit)
    return _result(start, now, "relative_last", confidence="inferred")


def _parse_since_range(text: str, now: datetime, tz: ZoneInfo) -> TimeRangeResult | None:
    clock_match = re.fullmatch(r"(?:depuis|a partir de)\s+(\d{1,2})h(\d{2})?", text)
    if clock_match:
        hour = int(clock_match.group(1))
        if hour >= 6:
            raw_clock = re.sub(r"^(?:depuis|a partir de)\s+", "", clock_match.group(0))
            start = _parse_endpoint(raw_clock, now, tz, prefer="start")
            if start:
                return _result(start, now, "since_clock", confidence="inferred")

    match = re.search(
        r"depuis\s+(\d+)\s*(minutes?|min|heures?|h|jours?|jrs|jr|j|semaines?|sem|mois)\b",
        text,
    )
    if match:
        amount = int(match.group(1))
        start = _subtract_unit(now, amount, match.group(2))
        return _result(start, now, "since_duration", confidence="inferred")

    match = re.search(r"(?:depuis|a partir de)\s+(.+?)(?:\s+jusqu(?:'a|a| a)?\s+(.+))?$", text)
    if not match:
        return None

    start = _parse_endpoint(match.group(1), now, tz, prefer="start")
    if not start:
        return None
    end = _parse_endpoint(match.group(2), now, tz, prefer="end") if match.group(2) else now
    if not end:
        return None
    return _result(start, end, "since_endpoint", confidence="inferred")


def _subtract_unit(now: datetime, amount: int, unit: str) -> datetime:
    if unit.startswith("min"):
        return now - timedelta(minutes=amount)
    if unit.startswith("h") or unit.startswith("heure"):
        return now - timedelta(hours=amount)
    if unit in {"j", "jr", "jrs"} or unit.startswith("jour"):
        return now - timedelta(days=amount)
    if unit == "sem" or unit.startswith("semaine"):
        return now - timedelta(weeks=amount)
    return now - relativedelta(months=amount)


def _parse_open_bounds(text: str, now: datetime, tz: ZoneInfo) -> TimeRangeResult | None:
    before = re.match(r"(?:avant|jusqu(?:'a|a| a)?)\s+(.+)$", text)
    if before:
        end = _parse_endpoint(before.group(1), now, tz, prefer="end")
        if not end:
            return None
        start = _start_of_day(end, tz)
        return _result(start, end, "open_before", confidence="inferred")

    after = re.match(r"(?:apres|après)\s+(.+)$", text)
    if after:
        start = _parse_endpoint(after.group(1), now, tz, prefer="start")
        if not start:
            return None
        end = now if start <= now <= _end_of_day(start, tz) else _end_of_day(start, tz)
        return _result(start, end, "open_after", confidence="inferred")

    return None


def _parse_closed_range(text: str, now: datetime, tz: ZoneInfo) -> TimeRangeResult | None:
    patterns = (
        r"entre\s+(.+?)\s+et\s+(.+)$",
        r"du\s+(.+?)\s+au\s+(.+)$",
        r"de\s+(.+?)\s+a\s+(.+)$",
    )
    for pattern in patterns:
        match = re.match(pattern, text)
        if not match:
            continue
        left = match.group(1).strip()
        right = match.group(2).strip()
        start = _parse_endpoint(left, now, tz, prefer="start")
        end = _parse_endpoint(right, now, tz, prefer="end", base=start)
        if start and end:
            return _result(start, end, "closed_range", confidence="inferred")
    return None


def _parse_year_range(text: str, now: datetime, tz: ZoneInfo) -> TimeRangeResult | None:
    match = re.fullmatch(r"(?:en\s+|annee\s+|toute\s+l'annee\s+)?(20\d{2}|19\d{2})", text)
    if not match:
        return None
    year = int(match.group(1))
    start, end = _year_bounds(year, tz)
    return _result(start, end, "year")


def _parse_quarter_range(text: str, now: datetime, tz: ZoneInfo) -> TimeRangeResult | None:
    match = re.fullmatch(r"(?:t|q)([1-4])(?:\s+(20\d{2}|19\d{2}))?", text)
    if match:
        quarter = int(match.group(1))
        year = int(match.group(2)) if match.group(2) else _infer_period_year(now, _quarter_bounds(now.year, quarter, tz)[1])
        start, end = _quarter_bounds(year, quarter, tz)
        return _result(start, end, "quarter", confidence="exact" if match.group(2) else "inferred")

    match = re.fullmatch(
        r"(?:(premier|1er|1|deuxieme|second|seconde|2e|2|troisieme|3e|3|quatrieme|4e|4)\s+)?"
        r"trimestre(?:\s+(dernier))?(?:\s+(20\d{2}|19\d{2}))?",
        text,
    )
    if not match:
        match = re.fullmatch(r"(?:dernier\s+trimestre|trimestre\s+dernier)", text)
        if match:
            current_quarter = ((now.month - 1) // 3) + 1
            quarter = current_quarter - 1
            year = now.year
            if quarter < 1:
                quarter = 4
                year -= 1
            start, end = _quarter_bounds(year, quarter, tz)
            return _result(start, end, "previous_quarter", confidence="inferred")
        return None

    ordinal, previous, year_text = match.groups()
    if previous:
        current_quarter = ((now.month - 1) // 3) + 1
        quarter = current_quarter - 1
        year = now.year
        if quarter < 1:
            quarter = 4
            year -= 1
    else:
        quarter = ORDINALS.get(ordinal or "", ((now.month - 1) // 3) + 1)
        year = int(year_text) if year_text else _infer_period_year(now, _quarter_bounds(now.year, quarter, tz)[1])
    start, end = _quarter_bounds(year, quarter, tz)
    return _result(start, end, "quarter", confidence="exact" if year_text else "inferred")


def _quarter_bounds(year: int, quarter: int, tz: ZoneInfo) -> tuple[datetime, datetime]:
    start_month = (quarter - 1) * 3 + 1
    end_month = start_month + 2
    start, _ = _month_bounds(year, start_month, tz)
    _, end = _month_bounds(year, end_month, tz)
    return start, end


def _parse_semester_range(text: str, now: datetime, tz: ZoneInfo) -> TimeRangeResult | None:
    match = re.fullmatch(r"s([12])(?:\s+(20\d{2}|19\d{2}))?", text)
    if not match:
        match = re.fullmatch(r"(?:(premier|deuxieme|second)\s+)?semestre(?:\s+(20\d{2}|19\d{2}))?", text)
    if not match:
        return None

    semester = int(match.group(1)) if match.group(1) and match.group(1).isdigit() else ORDINALS.get(match.group(1) or "", 1)
    year_text = match.group(2) if len(match.groups()) >= 2 else None
    year = int(year_text) if year_text else _infer_period_year(now, _semester_bounds(now.year, semester, tz)[1])
    start, end = _semester_bounds(year, semester, tz)
    return _result(start, end, "semester", confidence="exact" if year_text else "inferred")


def _semester_bounds(year: int, semester: int, tz: ZoneInfo) -> tuple[datetime, datetime]:
    if semester == 2:
        return datetime(year, 7, 1, 0, 0, 0, tzinfo=tz), datetime(year, 12, 31, 23, 59, 59, tzinfo=tz)
    return datetime(year, 1, 1, 0, 0, 0, tzinfo=tz), datetime(year, 6, 30, 23, 59, 59, tzinfo=tz)


def _parse_season_range(text: str, now: datetime, tz: ZoneInfo) -> TimeRangeResult | None:
    match = re.fullmatch(r"(printemps|ete|automne|hiver)(?:\s+(20\d{2}|19\d{2}))?", text)
    if not match:
        return None

    season, year_text = match.groups()
    if year_text:
        year = int(year_text)
    else:
        _, end = _season_bounds(season, now.year, tz)
        year = now.year if end <= now else now.year - 1
    start, end = _season_bounds(season, year, tz)
    return _result(start, end, "season", confidence="exact" if year_text else "inferred")


def _season_bounds(season: str, year: int, tz: ZoneInfo) -> tuple[datetime, datetime]:
    start_month, end_month = SEASONS[season]
    if season == "hiver":
        start = datetime(year, 12, 1, 0, 0, 0, tzinfo=tz)
        _, end = _month_bounds(year + 1, 2, tz)
        return start, end
    start, _ = _month_bounds(year, start_month, tz)
    _, end = _month_bounds(year, end_month, tz)
    return start, end


def _parse_month_range(text: str, now: datetime, tz: ZoneInfo) -> TimeRangeResult | None:
    month_names = "|".join(MONTHS)
    match = re.fullmatch(rf"(?:en\s+)?({month_names})(?:\s+(20\d{{2}}|19\d{{2}}))?", text)
    if match:
        month = MONTHS[match.group(1)]
        year_text = match.group(2)
        year = int(year_text) if year_text else _infer_month_year(month, now)
        start, end = _month_bounds(year, month, tz)
        return _result(start, end, "month", confidence="exact" if year_text else "inferred")

    match = re.fullmatch(rf"({month_names})\s+dernier", text)
    if match:
        month = MONTHS[match.group(1)]
        year = now.year if month < now.month else now.year - 1
        start, end = _month_bounds(year, month, tz)
        return _result(start, end, "month_previous", confidence="inferred")

    return None


def _parse_weekday_range(text: str, now: datetime, tz: ZoneInfo) -> TimeRangeResult | None:
    weekday_names = "|".join(WEEKDAYS)
    match = re.fullmatch(rf"({weekday_names})(?:\s+(dernier|matin|soir|apres-midi|apres midi))?", text)
    if not match:
        return None

    weekday = WEEKDAYS[match.group(1)]
    qualifier = match.group(2)
    day = _last_weekday_on_or_before(now, weekday)
    if qualifier == "dernier" and day.date() == now.date():
        day -= timedelta(days=7)
    if qualifier in {"matin", "soir", "apres-midi", "apres midi"}:
        start, end = _part_bounds(day, qualifier, tz)
        return _result(start, end, "weekday_part", confidence="inferred")
    start, end = _day_bounds(day, tz)
    return _result(start, end, "weekday", confidence="inferred")


def _parse_explicit_date_range(text: str, now: datetime, tz: ZoneInfo) -> TimeRangeResult | None:
    month_names = "|".join(MONTHS)
    match = re.fullmatch(rf"(?:le\s+)?(\d{{1,2}})\s+({month_names})(?:\s+(20\d{{2}}|19\d{{2}}))?", text)
    if match:
        day = int(match.group(1))
        month = MONTHS[match.group(2)]
        year = int(match.group(3)) if match.group(3) else _infer_date_year(month, day, now)
        start, end = _day_bounds(datetime(year, month, day, tzinfo=tz), tz)
        return _result(start, end, "date_named_month", confidence="exact" if match.group(3) else "inferred")

    match = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?", text)
    if match:
        day = int(match.group(1))
        month = int(match.group(2))
        year_text = match.group(3)
        year = _normalize_year(year_text) if year_text else _infer_date_year(month, day, now)
        start, end = _day_bounds(datetime(year, month, day, tzinfo=tz), tz)
        return _result(start, end, "date_numeric", confidence="exact" if year_text else "inferred")

    if not _looks_like_date(text):
        return None
    try:
        default = datetime(now.year, 1, 1, 0, 0, 0, tzinfo=tz)
        parsed = date_parser.parse(text, dayfirst=True, fuzzy=True, default=default)
    except (ValueError, OverflowError):
        return None
    parsed = parsed.replace(tzinfo=tz) if parsed.tzinfo is None else parsed.astimezone(tz)
    if not _has_year(text) and parsed > now:
        parsed -= relativedelta(years=1)
    start, end = _day_bounds(parsed, tz)
    return _result(start, end, "dateutil", confidence="inferred")


def _parse_endpoint(
    text: str | None,
    now: datetime,
    tz: ZoneInfo,
    *,
    prefer: str,
    base: datetime | None = None,
) -> datetime | None:
    if not text:
        return None
    value = normalize_time_text(text)
    if not value:
        return None

    clock = _parse_clock_time(value)
    if clock:
        day = base if base else now
        return datetime.combine(day.date(), clock, tzinfo=tz)

    weekday = _parse_weekday_endpoint(value, now, tz, prefer=prefer)
    if weekday:
        return weekday

    date_result = (
        _parse_named_fixed_range(value, now, tz)
        or _parse_month_range(value, now, tz)
        or _parse_explicit_date_range(value, now, tz)
    )
    if not date_result:
        return None
    return date_result.start if prefer == "start" else date_result.end


def _parse_clock_time(text: str) -> time | None:
    match = re.fullmatch(r"(\d{1,2})(?:h|:)(\d{2})?", text)
    if not match:
        match = re.fullmatch(r"(\d{1,2})h?", text)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or 0) if len(match.groups()) > 1 else 0
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        return None
    return time(hour, minute, 0)


def _parse_weekday_endpoint(text: str, now: datetime, tz: ZoneInfo, *, prefer: str) -> datetime | None:
    if text not in WEEKDAYS:
        return None
    day = _last_weekday_on_or_before(now, WEEKDAYS[text])
    return _start_of_day(day, tz) if prefer == "start" else _end_of_day(day, tz)


def _last_weekday_on_or_before(now: datetime, weekday: int) -> datetime:
    delta_days = (now.weekday() - weekday) % 7
    return now - timedelta(days=delta_days)


def _infer_month_year(month: int, now: datetime) -> int:
    return now.year if month <= now.month else now.year - 1


def _infer_date_year(month: int, day: int, now: datetime) -> int:
    year = now.year
    candidate = datetime(year, month, day, 23, 59, 59, tzinfo=now.tzinfo)
    if candidate > now:
        year -= 1
    return year


def _infer_period_year(now: datetime, period_end: datetime) -> int:
    return now.year if period_end <= now else now.year - 1


def _normalize_year(year_text: str | None) -> int:
    if not year_text:
        raise ValueError("year_text is required")
    year = int(year_text)
    return year + 2000 if year < 100 else year


def _has_year(text: str) -> bool:
    return bool(re.search(r"\b(?:19|20)\d{2}\b", text))


def _looks_like_date(text: str) -> bool:
    return bool(
        re.search(r"\d{1,2}[/-]\d{1,2}", text)
        or re.search(r"\b(?:19|20)\d{2}\b", text)
        or any(month in text for month in MONTHS)
    )
