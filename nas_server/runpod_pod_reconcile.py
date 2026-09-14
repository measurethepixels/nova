"""Periodic reconciliation of NOVA-owned RunPod Pods and local lifecycle rows.

The inventory boundary is fail-closed: if the complete RunPod listing cannot
be obtained and validated, this module performs zero lifecycle mutations and
zero termination calls.  It never touches a pod without the exact Candidate 6
ownership marker.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from nas_server import runpod_pod_client as pod_client
from nas_server import runpod_pod_lifecycle as lifecycle
from nas_server.runpod_dispatch import MANAGED_BY


@dataclass
class ReconcileResult:
    healthy: bool
    checked_at: str
    managed_remote: int = 0
    active_local: int = 0
    terminated_remote: int = 0
    failed_local: int = 0
    matched: int = 0
    error: str | None = None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _write_state(path: Path, result: ReconcileResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(asdict(result), handle, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _notify(message: str) -> None:
    from nas_server import telegram

    telegram.send(message)


def reconcile(
    *,
    state_path: Path,
    now: Callable[[], datetime] = _utcnow,
    notify: Callable[[str], None] = _notify,
) -> ReconcileResult:
    checked = now()
    result = ReconcileResult(healthy=False, checked_at=checked.isoformat())
    try:
        remote_all = pod_client.list_pods()
    except Exception as exc:
        result.error = f"inventory_failed: {exc}"
        _write_state(state_path, result)
        return result

    # Only exact positive ownership is in scope.  Names are never sufficient.
    remote = {
        pod["pod_id"]: pod for pod in remote_all
        if pod.get("env", {}).get("NOVA_MANAGED_BY") == MANAGED_BY
    }
    all_remote_ids = {pod["pod_id"] for pod in remote_all}
    from nas_server.database import get_all_active_pod_lifecycle_rows

    local_rows = get_all_active_pod_lifecycle_rows(
        terminal_states=lifecycle.CONFIRMED_GONE_STATES
    )
    local_by_id = {str(row["id"]): row for row in local_rows}
    local_by_pod = {row["pod_id"]: row for row in local_rows if row.get("pod_id")}
    result.managed_remote = len(remote)
    result.active_local = len(local_rows)
    errors = []
    matched_remote_ids = set()
    resolved_local_ids = set()

    for pod_id, pod in remote.items():
        marker_id = pod.get("env", {}).get("NOVA_LIFECYCLE_ID")
        row = local_by_id.get(str(marker_id)) if marker_id is not None else None
        matched = (
            row is not None
            and row.get("pod_id") == pod_id
            and row.get("state") not in (lifecycle.FAILED, lifecycle.TERMINATING)
        )
        if matched:
            result.matched += 1
            matched_remote_ids.add(pod_id)
            continue

        # The exact pod_id is authoritative for deciding which local row is
        # moved to TERMINATED.  A lifecycle marker can recover an ambiguous
        # create (marker row has no pod_id), but a contradictory marker must
        # never terminate some other row that references a different pod.
        termination_row = local_by_pod.get(pod_id)
        if termination_row is None and row is not None and not row.get("pod_id"):
            termination_row = row

        reason = "unowned managed pod"
        if row is not None and not row.get("pod_id"):
            reason = "ambiguous-create pod recovered by ownership marker"
        elif row is not None:
            reason = "ownership marker contradicts local pod ID"
        if termination_row is not None:
            # This local row is handled by the termination path below.  Even
            # if absence confirmation fails, do not overwrite TERMINATING
            # with a generic inventory-mismatch failure in the second pass.
            resolved_local_ids.add(int(termination_row["id"]))
        try:
            if termination_row is not None:
                lifecycle.mark_terminating(int(termination_row["id"]), reason)
            if not pod_client.terminate_pod(pod_id):
                raise RuntimeError("DELETE did not return documented HTTP 204")
            if not pod_client.confirm_pod_absent(pod_id):
                raise RuntimeError("DELETE accepted but follow-up GET did not confirm absence")
            if termination_row is not None:
                lifecycle.mark_terminated(int(termination_row["id"]), now=checked)
            result.terminated_remote += 1
            notify(
                f"⚠️ <b>RunPod reconciliation</b>: terminated NOVA pod "
                f"<code>{pod_id}</code> ({reason})."
            )
        except Exception as exc:
            errors.append(f"terminate {pod_id}: {exc}")

    for pod_id, row in local_by_pod.items():
        if pod_id in matched_remote_ids or int(row["id"]) in resolved_local_ids:
            continue
        try:
            mismatch = (
                "active local lifecycle pod exists remotely without NOVA ownership marker"
                if pod_id in all_remote_ids and pod_id not in remote
                else "active local lifecycle pod absent from complete RunPod inventory"
            )
            failure_reason = f"reconciliation mismatch: {mismatch}"
            newly_detected = not (
                row.get("state") == lifecycle.FAILED
                and row.get("failure_reason") == failure_reason
            )
            if newly_detected:
                lifecycle.mark_failed(int(row["id"]), failure_reason)
            result.failed_local += 1
            if newly_detected:
                notify(
                    f"⚠️ <b>RunPod reconciliation</b>: local lifecycle "
                    f"<code>{row['id']}</code> references absent pod <code>{pod_id}</code>."
                )
            if pod_id in all_remote_ids and pod_id not in remote:
                errors.append(f"ownership marker missing for local pod {pod_id}")
        except Exception as exc:
            errors.append(f"mark local {row['id']} failed: {exc}")

    result.healthy = not errors
    result.error = "; ".join(errors) if errors else None
    _write_state(state_path, result)
    return result


def main() -> int:
    from nas_server.config import settings

    state = str(settings.get("runpod_reconciliation_state_path", "")).strip()
    if not state:
        print("runpod_reconciliation_state_path is not configured")
        return 2
    result = reconcile(state_path=Path(state))
    print(json.dumps(asdict(result), sort_keys=True))
    return 0 if result.healthy else 1


if __name__ == "__main__":
    raise SystemExit(main())
