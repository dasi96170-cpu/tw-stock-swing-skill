from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sysconfig
from typing import Any
import tomllib


def _contract_path(name: str) -> Path:
    candidates = (
        Path(__file__).resolve().parents[2] / name,
        Path(__file__).resolve().parent.parent / "share" / "tw-stock-swing" / name,
        Path(sysconfig.get_path("data")) / "share" / "tw-stock-swing" / name,
    )
    return next((path for path in candidates if path.exists()), candidates[-1])


CATALOG_PATH = _contract_path("DATA_CATALOG.toml")
SOURCE_CATALOG_PATH = _contract_path("SOURCE_CATALOG.toml")
TRACEABILITY_PATH = _contract_path("TRACEABILITY.toml")

VALID_PRIORITIES = frozenset({"P0", "P1", "P2"})
VALID_IMPLEMENTATIONS = frozenset({"implemented", "partial", "planned"})


class CatalogError(ValueError):
    """Raised when a machine-readable contract is incomplete or inconsistent."""


@dataclass(frozen=True, slots=True)
class FieldDefinition:
    id: str
    name: str
    category: str
    priority: str
    kind: str
    unit: str
    freshness: str
    implementation: str
    sources: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ResearchCatalog:
    version: str
    timezone: str
    allowed_statuses: frozenset[str]
    fields: tuple[FieldDefinition, ...]

    @property
    def by_id(self) -> dict[str, FieldDefinition]:
        return {field.id: field for field in self.fields}


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise CatalogError(f"無法讀取目錄 {path}: {exc}") from exc


def load_catalog(path: str | Path = CATALOG_PATH) -> ResearchCatalog:
    data = _read_toml(Path(path))
    metadata = data.get("catalog")
    raw_fields = data.get("fields")
    if not isinstance(metadata, dict) or not isinstance(raw_fields, list):
        raise CatalogError("資料目錄必須包含 [catalog] 與 [[fields]]")

    statuses = frozenset(str(value) for value in metadata.get("status_values", []))
    if not statuses:
        raise CatalogError("資料目錄未定義 status_values")

    required = {"id", "name", "category", "priority", "kind", "unit", "freshness", "implementation", "sources"}
    fields: list[FieldDefinition] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_fields):
        if not isinstance(raw, dict):
            raise CatalogError(f"fields[{index}] 不是物件")
        missing = sorted(required - raw.keys())
        if missing:
            raise CatalogError(f"fields[{index}] 缺少欄位: {', '.join(missing)}")
        field_id = str(raw["id"])
        if field_id in seen:
            raise CatalogError(f"重複的資料項目 ID: {field_id}")
        seen.add(field_id)
        priority = str(raw["priority"])
        implementation = str(raw["implementation"])
        sources = raw["sources"]
        if priority not in VALID_PRIORITIES:
            raise CatalogError(f"{field_id} 使用未知優先級 {priority}")
        if implementation not in VALID_IMPLEMENTATIONS:
            raise CatalogError(f"{field_id} 使用未知實作狀態 {implementation}")
        if not isinstance(sources, list) or not sources:
            raise CatalogError(f"{field_id} 必須至少指定一個來源")
        fields.append(
            FieldDefinition(
                id=field_id,
                name=str(raw["name"]),
                category=str(raw["category"]),
                priority=priority,
                kind=str(raw["kind"]),
                unit=str(raw["unit"]),
                freshness=str(raw["freshness"]),
                implementation=implementation,
                sources=tuple(str(source) for source in sources),
            )
        )
    return ResearchCatalog(
        version=str(metadata.get("version", "")),
        timezone=str(metadata.get("timezone", "Asia/Taipei")),
        allowed_statuses=statuses,
        fields=tuple(fields),
    )


def load_source_catalog(path: str | Path = SOURCE_CATALOG_PATH) -> dict[str, Any]:
    return _read_toml(Path(path))


def load_traceability(path: str | Path = TRACEABILITY_PATH) -> dict[str, Any]:
    return _read_toml(Path(path))
