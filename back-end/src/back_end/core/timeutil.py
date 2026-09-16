from datetime import UTC, date, datetime, timedelta


def utcnow() -> datetime:
    return datetime.now(UTC)


def as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def days_since(value: datetime, at: datetime | None = None) -> int:
    """Calendar days between two instants (local midnight to midnight, like the UI shows)."""
    now = as_utc(at or utcnow()).astimezone()
    then = as_utc(value).astimezone()
    return (now.date() - then.date()).days


def iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return as_utc(value).isoformat().replace("+00:00", "Z")


def resolve_relative(token: str, anchor: datetime | None = None) -> datetime | date | str:
    """Seed files store times as '@ago:<hours>' or '@agod:<days>' so data stays fresh whenever it is loaded."""
    base = anchor or utcnow()
    if token.startswith("@ago:"):
        return base - timedelta(hours=float(token[5:]))
    if token.startswith("@agod:"):
        return (base - timedelta(days=int(token[6:]))).date()
    return token
