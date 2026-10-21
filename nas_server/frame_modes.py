"""Mode metadata observation, isolated from stack eligibility decisions."""
import time


def mode_from_header(header):
    """Preserve existing 0/1 inference; unreadable files are handled separately."""
    if header.get('EQMODE') == 1:
        return 1
    if header.get('EQMODE') == 0:
        return 0
    try:
        exposure = float(header.get('EXPTIME') or 0)
    except Exception:
        exposure = 0.0
    return int(exposure >= 29.5)


def persist_frame_modes(all_paths, qualifying_paths, selected_modes, *,
                        read_header, get_conn, logger):
    """Observe rejected inventory once; caller isolates all metadata failures.

    Existing selected observations are reused, including preserving unreadable
    selected headers as unknown. Never mutate the selection's sets or counters.
    This function is a per-stack pass, not a library backfill.
    """
    started = time.monotonic()
    extra_reads = unreadable = 0
    modes = dict(selected_modes)
    try:
        extra_paths = sorted({str(p) for p in all_paths} - {str(p) for p in qualifying_paths})
        for path in extra_paths:
            extra_reads += 1
            try:
                modes[path] = mode_from_header(read_header(path, ext=0))
            except Exception:
                unreadable += 1
        with get_conn() as conn:
            conn.executemany('UPDATE light_files SET eq_mode=? WHERE file_path=?',
                             [(mode, path) for path, mode in modes.items()])
    finally:
        logger.info('[stack] eq_mode metadata pass: %.3fs, %d extra header reads, '
                    '%d unreadable, %d resolved (selection unchanged)',
                    time.monotonic() - started, extra_reads, unreadable, len(modes))
