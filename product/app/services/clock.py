"""Метки времени строкой ISO-8601 в UTC.

ТЗ §2 «смена грейда ограничена по времени»: интервалы считаются от этих меток.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def utc_after(minutes: int) -> str:
    moment = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    return moment.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_utc(stamp: str) -> datetime:
    moment = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def whole_days_since(stamp: str) -> int:
    """Целые сутки вниз от метки до сейчас. Сегодня — 0."""
    seconds = (datetime.now(timezone.utc) - parse_utc(stamp)).total_seconds()
    if seconds <= 0:
        return 0
    return int(seconds // 86400)


def days_elapsed(stamp: str, days: int) -> bool:
    return datetime.now(timezone.utc) >= parse_utc(stamp) + timedelta(days=days)


def days_remaining(stamp: str, days: int) -> int:
    """Полных суток до stamp+days, округление вверх. После дедлайна — 0."""
    leftover = parse_utc(stamp) + timedelta(days=days) - datetime.now(timezone.utc)
    seconds = leftover.total_seconds()
    if seconds <= 0:
        return 0
    return int((seconds + 86399) // 86400)


def is_past(stamp: str | None) -> bool:
    if not stamp:
        return True
    try:
        moment = parse_utc(stamp)
    except ValueError:
        return True
    return moment <= datetime.now(timezone.utc)
