"""Experimental Image Atlas identity sidecar (Milestone 1).

This module deliberately stores metadata only.  It is not imported by the
production pipeline and must not influence pixels or processing decisions.

Minimal demonstration::

    header = fits.getheader(frozen_checkpoint, memmap=True)  # header only
    snapshot = new_snapshot(checkpoint_id="M 31", checkpoint_hash=expected)
    write_snapshot_atomic("atlas.json", snapshot)
    assert load_snapshot_strict("atlas.json", expected_checkpoint_hash=expected) == snapshot
    load_snapshot_strict("atlas.json", expected_checkpoint_hash="0" * 64)  # raises

The writer and file hasher are reused from :mod:`nas_server.pipeline_manifest`:
the already-tested sibling-temp-file + fsync + replace behavior applies here too.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Sequence

from nas_server.pipeline_manifest import sha256_file, write_manifest_atomic

SCHEMA_VERSION = 1


class ValidityState(str, Enum):
    SUPPORTED = "supported"
    UNCERTAIN = "uncertain"
    CONFLICT = "conflict"
    UNKNOWN = "unknown"
    INVALID = "invalid"
    STALE = "stale"
    PARTIAL = "partial"


class AtlasManifestError(ValueError):
    """The Atlas sidecar is corrupt or violates the identity contract."""


class WrongCheckpointError(AtlasManifestError):
    """The sidecar belongs to a different checkpoint."""


class PartialSnapshotError(AtlasManifestError):
    """A partial layer cannot be accepted as completed evidence."""


def _canonical_json(value: Any) -> bytes:
    try:
        text = json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise AtlasManifestError(f"value is not canonically JSON serializable: {exc}") from exc
    return text.encode("utf-8")


def _stable_id(prefix: str, value: Any) -> str:
    return f"{prefix}_{hashlib.sha256(_canonical_json(value)).hexdigest()}"


def derivation_fingerprint(*, checkpoint_hash: str, method: str,
                           method_version: str, params: Mapping[str, Any]) -> str:
    """Stable identity for a method bundle applied to one exact checkpoint."""
    return hashlib.sha256(_canonical_json({
        "checkpoint_hash": checkpoint_hash,
        "method": method,
        "method_version": method_version,
        "params": params,
    })).hexdigest()


@dataclass(frozen=True)
class AtlasArrayRef:
    """Canonical coordinates and storage identity for an array-backed layer.

    ``origin`` is the crop's zero-based (x, y, ...) offset in the canonical
    checkpoint frame.  Milestone 1 has no array URI or pixel payload.
    """

    coordinate_frame_id: str
    shape: tuple[int, ...]
    axes: tuple[str, ...]
    value_domain: str
    origin: tuple[int, ...] = ()
    storage_key: str | None = None
    data_sha256: str | None = None

    def __post_init__(self) -> None:
        if not self.shape or len(self.shape) != len(self.axes):
            raise AtlasManifestError("shape and axes must be non-empty and have equal length")
        if any(size <= 0 for size in self.shape):
            raise AtlasManifestError("shape dimensions must be positive")
        if self.origin and len(self.origin) != len(self.shape):
            raise AtlasManifestError("origin must be empty or match shape dimensionality")

    def checkpoint_to_local(self, coordinates: Sequence[float]) -> tuple[float, ...]:
        origin = self.origin or (0,) * len(self.shape)
        if len(coordinates) != len(origin):
            raise AtlasManifestError("coordinate dimensionality does not match array reference")
        return tuple(float(value) - offset for value, offset in zip(coordinates, origin))

    def local_to_checkpoint(self, coordinates: Sequence[float]) -> tuple[float, ...]:
        origin = self.origin or (0,) * len(self.shape)
        if len(coordinates) != len(origin):
            raise AtlasManifestError("coordinate dimensionality does not match array reference")
        return tuple(float(value) + offset for value, offset in zip(coordinates, origin))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AtlasArrayRef":
        return cls(
            coordinate_frame_id=str(data["coordinate_frame_id"]),
            shape=tuple(data["shape"]), axes=tuple(data["axes"]),
            value_domain=str(data["value_domain"]),
            origin=tuple(data.get("origin", ())),
            storage_key=data.get("storage_key"),
            data_sha256=data.get("data_sha256"),
        )


@dataclass(frozen=True)
class AtlasLayer:
    layer_id: str
    name: str
    array_ref: AtlasArrayRef
    source_layer_ids: tuple[str, ...]
    method: str
    method_version: str
    params_fingerprint: str
    validity_state: ValidityState
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "layer_id": self.layer_id, "name": self.name,
            "array_ref": self.array_ref.to_dict(),
            "source_layer_ids": list(self.source_layer_ids),
            "method": self.method, "method_version": self.method_version,
            "params_fingerprint": self.params_fingerprint,
            "validity_state": self.validity_state.value,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AtlasLayer":
        try:
            state = ValidityState(data["validity_state"])
        except (KeyError, ValueError) as exc:
            raise AtlasManifestError("layer has an unknown validity_state") from exc
        return cls(
            layer_id=str(data["layer_id"]), name=str(data["name"]),
            array_ref=AtlasArrayRef.from_dict(data["array_ref"]),
            source_layer_ids=tuple(data.get("source_layer_ids", ())),
            method=str(data["method"]), method_version=str(data["method_version"]),
            params_fingerprint=str(data["params_fingerprint"]), validity_state=state,
            metadata=dict(data.get("metadata", {})),
        )


@dataclass(frozen=True)
class AtlasSnapshot:
    atlas_id: str
    checkpoint_id: str
    checkpoint_hash: str
    parent_checkpoint_ids: tuple[str, ...] = ()
    layers: tuple[AtlasLayer, ...] = ()
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version, "atlas_id": self.atlas_id,
            "checkpoint_id": self.checkpoint_id, "checkpoint_hash": self.checkpoint_hash,
            "parent_checkpoint_ids": list(self.parent_checkpoint_ids),
            "layers": [layer.to_dict() for layer in self.layers],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AtlasSnapshot":
        required = {"atlas_id", "checkpoint_id", "checkpoint_hash", "layers"}
        if not required.issubset(data):
            raise AtlasManifestError(f"missing required fields: {sorted(required - data.keys())}")
        if data.get("schema_version") != SCHEMA_VERSION:
            raise AtlasManifestError("unsupported Atlas schema version")
        return cls(
            atlas_id=str(data["atlas_id"]), checkpoint_id=str(data["checkpoint_id"]),
            checkpoint_hash=str(data["checkpoint_hash"]),
            parent_checkpoint_ids=tuple(data.get("parent_checkpoint_ids", ())),
            layers=tuple(AtlasLayer.from_dict(item) for item in data["layers"]),
        )


@dataclass(frozen=True)
class AtlasInspection:
    """Non-authoritative inspection result; callers must check both flags."""

    snapshot: AtlasSnapshot
    checkpoint_matches: bool
    complete: bool


def new_snapshot(*, checkpoint_id: str, checkpoint_hash: str,
                 parent_checkpoint_ids: Sequence[str] = ()) -> AtlasSnapshot:
    parents = tuple(parent_checkpoint_ids)
    atlas_id = _stable_id("atlas", {"checkpoint_id": checkpoint_id,
                                    "checkpoint_hash": checkpoint_hash,
                                    "parent_checkpoint_ids": parents})
    return AtlasSnapshot(atlas_id=atlas_id, checkpoint_id=checkpoint_id,
                         checkpoint_hash=checkpoint_hash, parent_checkpoint_ids=parents)


def new_layer(*, name: str, array_ref: AtlasArrayRef, checkpoint_hash: str,
              method: str, method_version: str, params: Mapping[str, Any],
              source_layer_ids: Sequence[str] = (),
              validity_state: ValidityState = ValidityState.SUPPORTED,
              metadata: Mapping[str, Any] | None = None) -> AtlasLayer:
    fingerprint = derivation_fingerprint(checkpoint_hash=checkpoint_hash, method=method,
                                         method_version=method_version, params=params)
    sources = tuple(source_layer_ids)
    layer_id = _stable_id("layer", {"name": name, "fingerprint": fingerprint,
                                    "source_layer_ids": sources,
                                    "array_ref": array_ref.to_dict(),
                                    "metadata": metadata or {}})
    return AtlasLayer(layer_id=layer_id, name=name, array_ref=array_ref,
                      source_layer_ids=sources, method=method,
                      method_version=method_version, params_fingerprint=fingerprint,
                      validity_state=validity_state, metadata=dict(metadata or {}))


def needs_regeneration(layer: AtlasLayer, *, checkpoint_hash: str, method: str,
                       method_version: str, params: Mapping[str, Any]) -> bool:
    expected = derivation_fingerprint(checkpoint_hash=checkpoint_hash, method=method,
                                      method_version=method_version, params=params)
    return layer.params_fingerprint != expected or layer.validity_state in {
        ValidityState.INVALID, ValidityState.STALE, ValidityState.PARTIAL,
    }


def write_snapshot_atomic(path: str | Path, snapshot: AtlasSnapshot) -> None:
    """Atomically persist metadata using the proven pipeline-manifest writer."""
    write_manifest_atomic(path, snapshot)  # both contracts expose deterministic to_dict()


def load_snapshot_strict(path: str | Path, *, expected_checkpoint_hash: str,
                         require_complete: bool = True) -> AtlasSnapshot:
    p = Path(path)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise
    except (json.JSONDecodeError, OSError) as exc:
        raise AtlasManifestError(f"{p}: unreadable Atlas manifest: {exc}") from exc
    if not isinstance(data, dict):
        raise AtlasManifestError(f"{p}: Atlas manifest must be an object")
    snapshot = AtlasSnapshot.from_dict(data)
    if snapshot.checkpoint_hash != expected_checkpoint_hash:
        raise WrongCheckpointError(
            f"Atlas checkpoint {snapshot.checkpoint_hash} does not match {expected_checkpoint_hash}")
    if require_complete and any(layer.validity_state is ValidityState.PARTIAL
                                for layer in snapshot.layers):
        raise PartialSnapshotError("Atlas contains a partial layer")
    return snapshot


def inspect_snapshot(path: str | Path, *, expected_checkpoint_hash: str) -> AtlasInspection:
    """Inspect a sidecar without accidentally treating existence as validity."""
    snapshot = load_snapshot_strict(path, expected_checkpoint_hash=snapshot_hash(path),
                                    require_complete=False)
    return AtlasInspection(
        snapshot=snapshot,
        checkpoint_matches=snapshot.checkpoint_hash == expected_checkpoint_hash,
        complete=all(layer.validity_state is not ValidityState.PARTIAL
                     for layer in snapshot.layers),
    )


def snapshot_hash(path: str | Path) -> str:
    """Read only the recorded checkpoint hash, validating the JSON object shape."""
    p = Path(path)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        value = data["checkpoint_hash"]
    except (FileNotFoundError, json.JSONDecodeError, OSError, KeyError, TypeError) as exc:
        raise AtlasManifestError(f"{p}: cannot inspect checkpoint identity") from exc
    if not isinstance(value, str):
        raise AtlasManifestError(f"{p}: checkpoint_hash must be a string")
    return value


def verify_checkpoint(path: str | Path, expected_hash: str) -> bool:
    """Physical existence is not validity: verify the full checkpoint hash."""
    p = Path(path)
    return p.is_file() and sha256_file(p) == expected_hash
