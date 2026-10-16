from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Mapping

from .catalog import FieldDefinition, ResearchCatalog


@dataclass(frozen=True, slots=True)
class CoverageEntry:
    field_id: str
    status: str
    reason: str
    source_id: str | None = None
    observed_at: datetime | None = None


class CoverageError(ValueError):
    """Raised when coverage is incomplete or contains an illegal state."""


def complete_coverage(
    catalog: ResearchCatalog,
    observed: Mapping[str, CoverageEntry] | Iterable[CoverageEntry],
) -> tuple[CoverageEntry, ...]:
    """Return exactly one explicit status for every catalog item.

    Unknown observations and duplicate IDs are errors. Planned fields become
    ``missing`` with a visible ``not_implemented`` reason; they never disappear.
    """

    entries = list(observed.values()) if isinstance(observed, Mapping) else list(observed)
    supplied: dict[str, CoverageEntry] = {}
    catalog_ids = catalog.by_id
    for entry in entries:
        if entry.field_id not in catalog_ids:
            raise CoverageError(f"coverage 含未知資料項目: {entry.field_id}")
        if entry.field_id in supplied:
            raise CoverageError(f"coverage 重複資料項目: {entry.field_id}")
        if entry.status not in catalog.allowed_statuses:
            raise CoverageError(f"{entry.field_id} 使用未知狀態: {entry.status}")
        supplied[entry.field_id] = entry

    result: list[CoverageEntry] = []
    for field in catalog.fields:
        entry = supplied.get(field.id)
        if entry is None:
            reason = "not_implemented" if field.implementation == "planned" else "not_returned_by_source"
            entry = CoverageEntry(field.id, "missing", reason)
        result.append(entry)
    return tuple(result)


def assert_strict_p0_coverage(catalog: ResearchCatalog, entries: Iterable[CoverageEntry]) -> None:
    by_id = {entry.field_id: entry for entry in entries}
    blocking = []
    for field in catalog.fields:
        if field.priority != "P0" or field.implementation != "implemented":
            continue
        entry = by_id.get(field.id)
        if entry is None or entry.status in {"missing", "stale", "failed", "conflicting", "unverified"}:
            blocking.append(field.id)
    if blocking:
        raise CoverageError("P0 資料不完整: " + ", ".join(blocking))


def summarize_by_category(
    catalog: ResearchCatalog, entries: Iterable[CoverageEntry]
) -> dict[str, dict[str, int]]:
    fields: dict[str, FieldDefinition] = catalog.by_id
    result: dict[str, dict[str, int]] = {}
    for entry in entries:
        category = fields[entry.field_id].category
        summary = result.setdefault(category, {})
        summary[entry.status] = summary.get(entry.status, 0) + 1
    return result
