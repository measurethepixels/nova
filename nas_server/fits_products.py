"""Read-only discovery and classification of user-facing FITS products.

Astronomical identity and storage identity are deliberately separate here.  A
canonical target such as ``M 31`` may have native files stored below
``M 31_mosaic``; callers keep that storage target in every URL while presenting
one canonical target.  This module never moves files or broadens processing
eligibility.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Callable, Iterable
from urllib.parse import quote


PRODUCT_PIPELINE_STACK = "pipeline_stack"
PRODUCT_PROCESSED_FINAL = "processed_final"
PRODUCT_SEESTAR_STACK = "seestar_stack"

_MEDIA_EXTENSIONS = {".fit", ".fits", ".xisf", ".tif", ".tiff"}
_NATIVE_RE = re.compile(
    r"^Stacked_(?P<count>\d+).*?_(?P<seconds>\d+(?:\.\d+)?)s_.*?_(?P<date>\d{8})",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class FitsProduct:
    canonical_target: str
    storage_target: str
    relative_path: str
    provenance: str
    mosaic: bool
    raw_input_eligible: bool
    frame_count: int | None = None
    total_integration: float | None = None
    obs_date: str | None = None
    tool: str | None = None
    step: str | None = None

    @property
    def filename(self) -> str:
        return Path(self.relative_path).name

    @property
    def identity(self) -> tuple[str, str]:
        return self.storage_target, self.relative_path

    @property
    def label(self) -> str:
        return {
            PRODUCT_PIPELINE_STACK: "Pipeline Stack",
            PRODUCT_PROCESSED_FINAL: "Processed Final",
            PRODUCT_SEESTAR_STACK: "SeeStar Stack",
        }[self.provenance]

    @property
    def viewer_url(self) -> str:
        return (
            f"/fits/{quote(self.storage_target, safe='')}/"
            f"{quote(self.relative_path, safe='/')}"
        )


def _target_is_mosaic(target: str) -> bool:
    from nas_server.database import get_conn

    with get_conn() as conn:
        row = conn.execute(
            "SELECT mosaic FROM targets WHERE target=?", (target,)
        ).fetchone()
    return bool(row and row[0])


def canonical_target_for_storage(
    storage_target: str, *, mosaic_enabled: bool | None = None
) -> str:
    """Resolve a proven ``<canonical>_mosaic`` storage identity.

    A suffix alone is never enough: the canonical DB target must explicitly be
    marked mosaic.  False negatives are preferable to aliasing unrelated data.
    """
    if not storage_target.endswith("_mosaic"):
        return storage_target
    candidate = storage_target[: -len("_mosaic")]
    enabled = _target_is_mosaic(candidate) if mosaic_enabled is None else mosaic_enabled
    return candidate if enabled else storage_target


def _native_metadata(filename: str) -> tuple[int | None, float | None, str | None]:
    match = _NATIVE_RE.match(filename)
    if not match:
        return None, None, None
    count = int(match.group("count"))
    seconds = float(match.group("seconds"))
    raw_date = match.group("date")
    return count, count * seconds, f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}"


def _storage_for_path(path: Path, library: Path, storage_targets: Iterable[str]) -> str | None:
    absolute = path if path.is_absolute() else library / path
    for storage_target in storage_targets:
        try:
            absolute.relative_to(library / storage_target)
            return storage_target
        except ValueError:
            continue
    return None


def list_fits_products(
    canonical_target: str,
    *,
    library: Path | None = None,
    processed_rows: list[dict] | None = None,
    mosaic_enabled: bool | None = None,
    raw_stack_classifier: Callable[[str, str | None], bool] | None = None,
) -> list[FitsProduct]:
    """Return deterministic, de-duplicated products for one canonical target."""
    if processed_rows is None or raw_stack_classifier is None:
        from nas_server.database import get_processed_files, is_raw_stack
        if raw_stack_classifier is None:
            raw_stack_classifier = is_raw_stack

    if library is None:
        from nas_server.config import settings

        library = Path(settings["seestar_library_path"])
    else:
        library = Path(library)

    enabled = _target_is_mosaic(canonical_target) if mosaic_enabled is None else mosaic_enabled
    storage_targets = [canonical_target]
    mosaic_storage = f"{canonical_target}_mosaic"
    if enabled and (library / mosaic_storage).is_dir():
        storage_targets.append(mosaic_storage)

    rows = get_processed_files(canonical_target) if processed_rows is None else processed_rows
    products: dict[tuple[str, str], FitsProduct] = {}

    for row in rows:
        raw_path = Path(str(row.get("file_path") or ""))
        storage_target = _storage_for_path(raw_path, library, storage_targets)
        if storage_target is None:
            storage_target = canonical_target
            raw_path = library / storage_target / "_processed" / str(row.get("filename") or "")
        try:
            relative = raw_path.relative_to(library / storage_target).as_posix()
        except ValueError:
            relative = f"_processed/{Path(str(row.get('filename') or '')).name}"
        filename = Path(relative).name
        raw = raw_stack_classifier(filename, row.get("step"))
        product = FitsProduct(
            canonical_target=canonical_target,
            storage_target=storage_target,
            relative_path=relative,
            provenance=PRODUCT_PIPELINE_STACK if raw else PRODUCT_PROCESSED_FINAL,
            mosaic=storage_target != canonical_target,
            raw_input_eligible=raw and storage_target == canonical_target,
            frame_count=row.get("frame_count"),
            total_integration=row.get("total_integration"),
            obs_date=row.get("obs_date"),
            tool=row.get("tool"),
            step=row.get("step"),
        )
        products[product.identity] = product

    for storage_target in storage_targets:
        target_dir = library / storage_target
        if not target_dir.is_dir():
            continue
        candidates = list(target_dir.iterdir())
        processed_dir = target_dir / "_processed"
        if processed_dir.is_dir():
            candidates.extend(processed_dir.iterdir())
        for path in candidates:
            if not path.is_file() or path.suffix.lower() not in _MEDIA_EXTENSIONS:
                continue
            relative = path.relative_to(target_dir).as_posix()
            identity = (storage_target, relative)
            if identity in products:
                continue
            if path.parent == target_dir:
                count, integration, obs_date = _native_metadata(path.name)
                provenance = PRODUCT_SEESTAR_STACK
                raw = False
            else:
                count = integration = obs_date = None
                raw = raw_stack_classifier(path.name, None)
                provenance = PRODUCT_PIPELINE_STACK if raw else PRODUCT_PROCESSED_FINAL
            products[identity] = FitsProduct(
                canonical_target=canonical_target,
                storage_target=storage_target,
                relative_path=relative,
                provenance=provenance,
                mosaic=storage_target != canonical_target,
                raw_input_eligible=raw and storage_target == canonical_target,
                frame_count=count,
                total_integration=integration,
                obs_date=obs_date,
            )

    order = {
        PRODUCT_PIPELINE_STACK: 0,
        PRODUCT_PROCESSED_FINAL: 1,
        PRODUCT_SEESTAR_STACK: 2,
    }
    return sorted(
        products.values(),
        key=lambda product: (
            order[product.provenance],
            -(product.frame_count or 0),
            product.storage_target.casefold(),
            product.relative_path.casefold(),
        ),
    )


def find_fits_product(
    storage_target: str, relative_path: str, *, library: Path | None = None
) -> tuple[str, FitsProduct | None, list[FitsProduct]]:
    """Resolve the canonical target and selected product for an existing URL."""
    canonical = canonical_target_for_storage(storage_target)
    products = list_fits_products(canonical, library=library)
    normalized = Path(relative_path).as_posix()
    selected = next(
        (
            product
            for product in products
            if product.storage_target == storage_target
            and product.relative_path == normalized
        ),
        None,
    )
    return canonical, selected, products
