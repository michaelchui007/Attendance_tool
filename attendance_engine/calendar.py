from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Callable
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from chinese_calendar import get_holiday_detail, is_holiday, is_workday

from .models import DayType

HOLIDAY_URL_TEMPLATES = (
    "https://fastly.jsdelivr.net/gh/NateScarlet/holiday-cn@master/{year}.json",
    "https://cdn.jsdelivr.net/gh/NateScarlet/holiday-cn@master/{year}.json",
    "https://raw.githubusercontent.com/NateScarlet/holiday-cn/master/{year}.json",
)
CACHE_TTL = timedelta(hours=24)
DOWNLOAD_TIMEOUT_SECONDS = 3.0
MAX_RESPONSE_BYTES = 1024 * 1024
_CACHE_SCHEMA_VERSION = 1
_YEAR_LOCKS: dict[int, Lock] = {}
_YEAR_LOCKS_GUARD = Lock()


class HolidayDataUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class HolidayOverride:
    name: str
    is_off_day: bool


@dataclass(slots=True)
class HolidayCalendar:
    overrides: dict[date, HolidayOverride] = field(default_factory=dict)
    online_years: set[int] = field(default_factory=set)
    year_sources: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def classify_day(self, value: date) -> DayType:
        override = self.overrides.get(value)
        if override is not None:
            return DayType.STATUTORY_HOLIDAY if override.is_off_day else DayType.MAKEUP_WORKDAY
        if value.year in self.online_years:
            return DayType.ORDINARY_WEEKEND if value.weekday() >= 5 else DayType.NORMAL_WORKDAY
        return classify_day(value)

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": "online-cache-builtin",
            "years": self.year_sources,
            "warnings": self.warnings,
        }

    @classmethod
    def for_period(
        cls,
        start: date,
        end: date,
        *,
        cache_dir: str | Path | None = None,
        allow_online: bool = True,
        fetcher: Callable[[int], tuple[dict[str, Any], str]] | None = None,
        now: datetime | None = None,
    ) -> HolidayCalendar:
        calendar = cls()
        cache_root = _resolve_cache_dir(cache_dir)
        current_time = now or datetime.now(timezone.utc)
        target_years = list(range(start.year, end.year + 1))
        years_to_load = list(target_years)
        if end.month == 12:
            years_to_load.append(end.year + 1)

        loaded_payloads: dict[int, dict[str, Any]] = {}
        for year in years_to_load:
            required = year in target_years
            result = _load_year(
                year,
                cache_root,
                allow_online=allow_online,
                fetcher=fetcher,
                now=current_time,
            )
            if result is not None:
                payload, metadata = result
                loaded_payloads[year] = payload
                calendar.year_sources.append(metadata)
                if required:
                    calendar.online_years.add(year)
                continue
            if required and _builtin_supports_year(year):
                calendar.year_sources.append({"year": year, "source": "builtin"})
                calendar.warnings.append(f"{year} 年在线节假日数据不可用，已使用内置数据")
                continue
            if required:
                raise HolidayDataUnavailableError(
                    f"无法获取 {year} 年节假日安排，且内置节假日库不支持该年份。"
                    "请检查网络后重试。"
                )

        for payload in loaded_payloads.values():
            for item in payload["days"]:
                item_date = date.fromisoformat(item["date"])
                calendar.overrides[item_date] = HolidayOverride(
                    name=item["name"],
                    is_off_day=item["isOffDay"],
                )
        return calendar


def classify_day(value: date) -> DayType:
    if is_workday(value):
        return DayType.MAKEUP_WORKDAY if value.weekday() >= 5 else DayType.NORMAL_WORKDAY
    if value.weekday() >= 5:
        holiday, name = get_holiday_detail(value)
        if holiday and name:
            return DayType.STATUTORY_HOLIDAY
        return DayType.ORDINARY_WEEKEND
    if is_holiday(value):
        return DayType.STATUTORY_HOLIDAY
    return DayType.NORMAL_WORKDAY


def prefetch_holiday_data(years: list[int] | None = None) -> None:
    today = date.today()
    for year in years or [today.year, today.year + 1]:
        try:
            _load_year(
                year,
                _resolve_cache_dir(None),
                allow_online=True,
                fetcher=None,
                now=datetime.now(timezone.utc),
            )
        except Exception:
            continue


def _resolve_cache_dir(value: str | Path | None) -> Path:
    configured = value or os.environ.get("ATTENDANCE_HOLIDAY_CACHE_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    return Path.home() / ".attendance-tool" / "cache" / "holidays"


def _year_lock(year: int) -> Lock:
    with _YEAR_LOCKS_GUARD:
        return _YEAR_LOCKS.setdefault(year, Lock())


def _load_year(
    year: int,
    cache_dir: Path,
    *,
    allow_online: bool,
    fetcher: Callable[[int], tuple[dict[str, Any], str]] | None,
    now: datetime,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    with _year_lock(year):
        cached = _read_cache(cache_dir, year)
        if cached is not None:
            payload, fetched_at, source_url = cached
            if now - fetched_at <= CACHE_TTL:
                return payload, _source_metadata(year, "cache", fetched_at, source_url)

        if allow_online:
            try:
                payload, source_url = (fetcher or _fetch_year)(year)
                payload = _validate_payload(payload, year)
                fetched_at = now
                _write_cache(cache_dir, year, payload, fetched_at, source_url)
                return payload, _source_metadata(year, "online", fetched_at, source_url)
            except Exception:
                pass

        if cached is not None:
            payload, fetched_at, source_url = cached
            return payload, _source_metadata(year, "stale-cache", fetched_at, source_url)
        return None


def _source_metadata(year: int, source: str, fetched_at: datetime, source_url: str) -> dict[str, Any]:
    return {
        "year": year,
        "source": source,
        "fetchedAt": fetched_at.astimezone(timezone.utc).isoformat(),
        "sourceUrl": source_url,
    }


def _fetch_year(year: int) -> tuple[dict[str, Any], str]:
    executor = ThreadPoolExecutor(max_workers=len(HOLIDAY_URL_TEMPLATES))
    futures = {
        executor.submit(_download_json, template.format(year=year)): template.format(year=year)
        for template in HOLIDAY_URL_TEMPLATES
    }
    errors: list[str] = []
    try:
        for future in as_completed(futures, timeout=DOWNLOAD_TIMEOUT_SECONDS + 1):
            url = futures[future]
            try:
                return _validate_payload(future.result(), year), url
            except Exception as exc:
                errors.append(f"{url}: {exc}")
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    raise HolidayDataUnavailableError("；".join(errors) or f"{year} 年在线节假日数据请求超时")


def _download_json(url: str) -> dict[str, Any]:
    request = Request(url, headers={"User-Agent": "AttendanceTool/0.1 holiday-data"})
    with urlopen(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
        content = response.read(MAX_RESPONSE_BYTES + 1)
    if len(content) > MAX_RESPONSE_BYTES:
        raise ValueError("节假日数据超过大小限制")
    return json.loads(content.decode("utf-8"))


def _validate_payload(payload: Any, expected_year: int) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("year") != expected_year:
        raise ValueError("节假日数据年份不匹配")
    days = payload.get("days")
    papers = payload.get("papers")
    if not isinstance(days, list) or not days:
        raise ValueError("节假日数据缺少 days")
    if not isinstance(papers, list) or not papers or not all(_is_government_url(item) for item in papers):
        raise ValueError("节假日数据缺少公告来源")
    normalized_days = []
    for item in days:
        if not isinstance(item, dict):
            raise ValueError("节假日日期项格式错误")
        name = item.get("name")
        date_text = item.get("date")
        is_off_day = item.get("isOffDay")
        if not isinstance(name, str) or not isinstance(date_text, str) or not isinstance(is_off_day, bool):
            raise ValueError("节假日日期项字段错误")
        parsed_date = date.fromisoformat(date_text)
        if abs(parsed_date.year - expected_year) > 1:
            raise ValueError("节假日日期超出合理年份范围")
        normalized_days.append({"name": name, "date": parsed_date.isoformat(), "isOffDay": is_off_day})
    return {
        "year": expected_year,
        "papers": papers,
        "days": normalized_days,
    }


def _cache_path(cache_dir: Path, year: int) -> Path:
    return cache_dir / f"{year}.json"


def _read_cache(cache_dir: Path, year: int) -> tuple[dict[str, Any], datetime, str] | None:
    path = _cache_path(cache_dir, year)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        if document.get("schemaVersion") != _CACHE_SCHEMA_VERSION:
            return None
        payload = _validate_payload(document.get("payload"), year)
        fetched_at = datetime.fromisoformat(document["fetchedAt"]).astimezone(timezone.utc)
        source_url = str(document["sourceUrl"])
        return payload, fetched_at, source_url
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


def _write_cache(
    cache_dir: Path,
    year: int,
    payload: dict[str, Any],
    fetched_at: datetime,
    source_url: str,
) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = _cache_path(cache_dir, year)
    temporary = path.with_suffix(".tmp")
    document = {
        "schemaVersion": _CACHE_SCHEMA_VERSION,
        "fetchedAt": fetched_at.astimezone(timezone.utc).isoformat(),
        "sourceUrl": source_url,
        "payload": payload,
    }
    temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _builtin_supports_year(year: int) -> bool:
    try:
        is_workday(date(year, 1, 1))
        is_workday(date(year, 12, 31))
        return True
    except NotImplementedError:
        return False


def _is_government_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value)
    hostname = (parsed.hostname or "").lower()
    return parsed.scheme in {"http", "https"} and (hostname == "gov.cn" or hostname.endswith(".gov.cn"))
