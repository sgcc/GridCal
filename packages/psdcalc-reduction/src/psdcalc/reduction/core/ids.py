"""Stable source identities and authoritative external sidecar bindings."""

from dataclasses import dataclass
import json
from typing import Any, Iterable

from psdcalc.reduction.exceptions import IdentityError


@dataclass(frozen=True, order=True)
class StableId:
    """Backend-independent identity of one source component."""

    namespace: str
    component: str
    source_key: str

    def __post_init__(self) -> None:
        if not all((self.namespace, self.component, self.source_key)):
            raise IdentityError("StableId fields must be non-empty")

    def encode(self) -> str:
        """Encode without delimiter ambiguity or array positions."""
        return "sid:" + json.dumps(
            [self.namespace, self.component, self.source_key],
            ensure_ascii=False, separators=(",", ":"),
        )

    @classmethod
    def decode(cls, text: str) -> "StableId":
        """Decode a canonical stable ID or raise IdentityError."""
        if not text.startswith("sid:"):
            raise IdentityError("StableId must start with sid:")
        try:
            parts: Any = json.loads(text[4:])
        except json.JSONDecodeError as exc:
            raise IdentityError("Malformed StableId encoding") from exc
        if not isinstance(parts, list) or len(parts) != 3 or not all(
            isinstance(item, str) for item in parts
        ):
            raise IdentityError("StableId must contain three strings")
        return cls(*parts)


@dataclass(frozen=True)
class IdentityBinding:
    """Bind one backend component name to an authoritative StableId."""

    component: str
    backend_name: str
    source_id: StableId

    def __post_init__(self) -> None:
        if self.component != self.source_id.component or not self.backend_name:
            raise IdentityError("IdentityBinding component or backend name is invalid")


class IdentitySidecar:
    """Collision-checked identity table independent of PyPSA custom columns."""

    def __init__(self, bindings: Iterable[IdentityBinding]) -> None:
        rows = tuple(sorted(bindings, key=lambda row: (row.component, row.backend_name)))
        by_backend: dict[tuple[str, str], StableId] = {}
        by_source: dict[StableId, tuple[str, str]] = {}
        for row in rows:
            backend_key = (row.component, row.backend_name)
            if backend_key in by_backend or row.source_id in by_source:
                raise IdentityError("duplicate sidecar binding")
            by_backend[backend_key] = row.source_id
            by_source[row.source_id] = backend_key
        self._rows = rows
        self._by_backend = by_backend
        self._by_source = by_source

    def source_id(self, component: str, backend_name: str) -> StableId:
        """Return the source ID for a backend object or raise IdentityError."""
        try:
            return self._by_backend[(component, backend_name)]
        except KeyError as exc:
            raise IdentityError(f"Missing sidecar binding: {component}/{backend_name}") from exc

    def backend_name(self, source_id: StableId) -> str:
        """Return a backend name for a source ID or raise IdentityError."""
        try:
            return self._by_source[source_id][1]
        except KeyError as exc:
            raise IdentityError(f"Missing sidecar StableId: {source_id.encode()}") from exc

    def to_rows(self) -> list[dict[str, str]]:
        """Return deterministic, serializable sidecar rows."""
        return [
            {"component": row.component, "backend_name": row.backend_name,
             "source_id": row.source_id.encode()}
            for row in self._rows
        ]

    @classmethod
    def from_rows(cls, rows: Iterable[dict[str, str]]) -> "IdentitySidecar":
        """Restore sidecar bindings from serialized rows."""
        try:
            return cls(IdentityBinding(row["component"], row["backend_name"],
                                       StableId.decode(row["source_id"])) for row in rows)
        except KeyError as exc:
            raise IdentityError(f"Missing sidecar field: {exc}") from exc
