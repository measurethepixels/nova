"""
Video session manager for the pipeline documentary system.

Each VideoSession corresponds to one processing run of one target.
It collects annotated 1920×1080 JPEG frames and can compile them
into an MP4 via ffmpeg.

Frame storage layout:
  /mnt/nas_data/SeeStar/{target}/_video/{session_id}/
    frames.json          — ordered list of frames with metadata
    0001_stack_done.jpg
    0002_process_crop.jpg
    ...
  /mnt/nas_data/SeeStar/{target}/_video/{target}_{session_id}.mp4
"""
from __future__ import annotations

import json
import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

# Base directory for all SeeStar target data
_LIBRARY = Path("/mnt/nas_data/SeeStar")


class VideoSession:
    """Manages frame collection and video compilation for one processing run."""

    def __init__(self, target: str, session_id: str):
        self.target = target
        self.session_id = session_id
        slug = target.replace(" ", "_").replace("/", "_")
        self._dir = _LIBRARY / target / "_video" / session_id
        self._dir.mkdir(parents=True, exist_ok=True)
        self._meta_path = self._dir / "frames.json"
        self._frames: list[dict] = self._load_meta()
        self._counter = len(self._frames)
        # Step-panel state — updated by set_step_context() each step
        self._all_steps: list[str] = []
        self._completed_steps: list[str] = []
        self._current_step: str = ""
        self._last_image: Path | None = None   # persists image across text-card frames

    # ── Step context ─────────────────────────────────────────────────────────

    def set_step_context(
        self,
        all_steps: list[str],
        completed_steps: list[str],
        current_step: str,
    ) -> None:
        """
        Call at the start of each processing step in auto_process.py.
        All subsequent add_frame() calls will use this context for the steps panel.
        """
        self._all_steps = list(all_steps)
        self._completed_steps = list(completed_steps)
        self._current_step = current_step

    # ── Factory ───────────────────────────────────────────────────────────────

    @classmethod
    def for_run(cls, target: str, run_id: str | None = None) -> "VideoSession":
        """Get-or-create a session for target.  run_id defaults to today's date."""
        if run_id is None:
            run_id = datetime.now(timezone.utc).strftime("%Y%m%d")
        return cls(target, run_id)

    # ── Frame management ──────────────────────────────────────────────────────

    def add_frame(
        self,
        act: str,
        step_name: str = "",
        *,
        image_path: str | Path | None = None,
        stage: str = "process",
        step_label: str = "",
        caption: str = "",
        commentary: str = "",
        stats: dict | None = None,
        score: float | None = None,
        score_delta: float | None = None,
        bullet_lines: list[str] | None = None,
        duration_s: float = 3.0,
        data_viz: dict | None = None,
    ) -> Path | None:
        """
        Render and save one annotated frame.

        Step context (all_steps / completed_steps / current_step) is drawn
        automatically from the last set_step_context() call.

        For text-only planning cards (image_path=None, bullet_lines set),
        the last seen image is used in the left panel so the picture persists.

        Returns the output path, or None on error.
        """
        from nas_server.video_frame import save_frame, STEP_DISPLAY, STEP_CAPTIONS

        self._counter += 1
        safe_step = step_name.replace(" ", "_").replace("/", "_")[:30]
        filename = f"{self._counter:04d}_{act}_{safe_step}.jpg"
        out_path = self._dir / filename

        # Auto-fill display label and caption from step name if not provided
        if not step_label and step_name:
            step_label = STEP_DISPLAY.get(step_name, step_name.replace("_", " ").title())
        if not caption and step_name:
            caption = STEP_CAPTIONS.get(step_name, "")

        # Persist last known image — reuse for all frames without an explicit image
        # (step intro cards, text planning cards, etc.)
        if image_path is not None:
            self._last_image = Path(image_path)
        effective_image = image_path
        if effective_image is None and self._last_image and self._last_image.exists():
            effective_image = self._last_image

        try:
            save_frame(
                out_path,
                image_path=effective_image,
                stage=stage,
                step_label=step_label,
                caption=caption,
                commentary=commentary,
                stats=stats,
                score=score,
                score_delta=score_delta,
                bullet_lines=bullet_lines,
                target=self.target,
                duration_s=duration_s,
                all_steps=self._all_steps or None,
                completed_steps=self._completed_steps or None,
                current_step=self._current_step or None,
                data_viz=data_viz,
            )
        except Exception as e:
            log.warning(f"[video] {self.target}: frame render failed ({step_name}): {e}")
            self._counter -= 1
            return None

        # Record metadata
        self._frames.append({
            "n":          self._counter,
            "filename":   filename,
            "act":        act,
            "step_name":  step_name,
            "step_label": step_label,
            "caption":    caption,
            "score":      score,
            "duration_s": duration_s,
            "timestamp":  datetime.now(timezone.utc).isoformat(),
        })
        self._save_meta()
        log.info(f"[video] {self.target}: frame {self._counter:04d} saved — {act}/{step_name}")
        return out_path

    def frame_count(self) -> int:
        return len(self._frames)

    # ── Branch support: import a source run's early frames ────────────────────

    def import_frames_from(
        self,
        source_dir: str | Path,
        stop_before_step: str | None = None,
    ) -> int:
        """
        Copy frames from another session's dir into this one (renumbered to
        precede this session's frames), stopping before the first frame whose
        step_name == stop_before_step.

        Used by branch runs (e.g. the NBN branch) so the source run's early-step
        frames — the linear phase + stretch, which the branch fast-forwards past
        and never re-renders — appear at the front of the branch video, making it
        complete. Returns the number of frames imported.
        """
        import shutil

        source_dir = Path(source_dir)
        src_meta = source_dir / "frames.json"
        if not src_meta.exists():
            log.info(f"[video] {self.target}: no source frames at {source_dir}")
            return 0
        try:
            src_frames = json.loads(src_meta.read_text())
        except Exception as e:
            log.warning(f"[video] {self.target}: source frames unreadable: {e}")
            return 0

        imported = 0
        for fr in src_frames:
            if stop_before_step and fr.get("step_name") == stop_before_step:
                break
            src_file = source_dir / fr["filename"]
            if not src_file.exists():
                continue
            self._counter += 1
            # Keep the descriptive suffix, renumber the leading counter so the
            # imported frames sort ahead of the branch frames.
            suffix = fr["filename"].split("_", 1)[-1] if "_" in fr["filename"] else fr["filename"]
            new_name = f"{self._counter:04d}_{suffix}"
            try:
                shutil.copy2(src_file, self._dir / new_name)
            except Exception as e:
                log.warning(f"[video] {self.target}: frame copy failed ({fr['filename']}): {e}")
                self._counter -= 1
                continue
            nf = dict(fr)
            nf["n"] = self._counter
            nf["filename"] = new_name
            nf["imported_from"] = source_dir.name
            self._frames.append(nf)
            imported += 1

        self._save_meta()
        log.info(f"[video] {self.target}: imported {imported} source frames "
                 f"from {source_dir.name}")
        return imported

    # ── Video compilation ─────────────────────────────────────────────────────

    def compile_video(
        self,
        output_path: str | Path | None = None,
        fps: int = 24,
    ) -> Path | None:
        """
        Compile all collected frames into an MP4 using ffmpeg.

        Each frame is shown for its `duration_s` seconds.
        Returns the output path on success, None on failure.
        """
        if not self._frames:
            log.warning(f"[video] {self.target}: no frames to compile")
            return None

        slug = self.target.replace(" ", "_").replace("/", "_")
        if output_path is None:
            output_path = _LIBRARY / self.target / "_video" / f"{slug}_{self.session_id}.mp4"
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)

        # Build ffmpeg concat manifest
        manifest_path = self._dir / "concat.txt"
        lines = []
        for frame in self._frames:
            fpath = self._dir / frame["filename"]
            if not fpath.exists():
                log.warning(f"[video] missing frame file: {fpath}")
                continue
            lines.append(f"file '{fpath}'")
            lines.append(f"duration {frame['duration_s']}")
        # ffmpeg concat requires the last file entry to appear without duration
        # (or a repeat of the last entry) to avoid a 0-length last frame
        if self._frames:
            last = self._dir / self._frames[-1]["filename"]
            if last.exists():
                lines.append(f"file '{last}'")

        manifest_path.write_text("\n".join(lines))

        cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", str(manifest_path),
            "-vf", f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
                   f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color={_hex_to_ffmpeg('#0d1117')},"
                   f"fps={fps}",
            "-c:v", "libx264",
            "-preset", "slow",
            "-crf", "18",
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            str(out),
        ]

        log.info(f"[video] {self.target}: compiling {len(self._frames)} frames → {out.name}")
        try:
            result = subprocess.run(
                cmd,
                capture_output=True, text=True, timeout=300
            )
            if result.returncode != 0:
                log.error(f"[video] ffmpeg failed: {result.stderr[-600:]}")
                return None
            size_mb = out.stat().st_size / 1_048_576
            log.info(f"[video] {self.target}: compiled {out.name} ({size_mb:.1f} MB)")
            return out
        except Exception as e:
            log.error(f"[video] compile error: {e}")
            return None

    # ── Internal ──────────────────────────────────────────────────────────────

    def _load_meta(self) -> list[dict]:
        if self._meta_path.exists():
            try:
                return json.loads(self._meta_path.read_text())
            except Exception:
                pass
        return []

    def _save_meta(self) -> None:
        self._meta_path.write_text(json.dumps(self._frames, indent=2))


def find_run_video_session(run_dir: str | Path, target: str) -> str | None:
    """
    Resolve the video session_id for a given processing run directory.

    Prefers the run's ``video_session.txt`` marker (written when the session is
    created). Falls back — for runs that predate the marker — to the ``_video``
    session whose timestamp is the closest one at/after the run dir's stamp
    (the session is created a few seconds after the run dir within the same run),
    bounded to a 10-minute window. Returns the session_id, or None.
    """
    run_dir = Path(run_dir)
    marker = run_dir / "video_session.txt"
    if marker.exists():
        try:
            sid = marker.read_text().strip()
            if sid:
                return sid
        except Exception:
            pass

    vroot = _LIBRARY / target / "_video"
    if not vroot.is_dir():
        return None
    run_stamp = _parse_stamp(run_dir.name)
    if run_stamp is None:
        return None
    best_name = None
    best_delta = None
    for d in vroot.iterdir():
        if not d.is_dir():
            continue
        sess_stamp = _parse_stamp(d.name)
        if sess_stamp is None:
            continue
        delta = (sess_stamp - run_stamp).total_seconds()
        if delta < 0 or delta > 600:
            continue
        if best_delta is None or delta < best_delta:
            best_delta = delta
            best_name = d.name
    return best_name


def _parse_stamp(name: str):
    """Parse the leading 'YYYYMMDD_HHMMSS' of a run/session dir name → datetime."""
    parts = name.split("_")
    if len(parts) < 2:
        return None
    try:
        return datetime.strptime(f"{parts[0]}_{parts[1]}", "%Y%m%d_%H%M%S")
    except Exception:
        return None


# ── Canvas size constant (imported by video_frame.py) ────────────────────────
W, H = 1920, 1080


def _hex_to_ffmpeg(hex_color: str) -> str:
    """Convert '#0d1117' to ffmpeg color format '0x0d1117'."""
    return "0x" + hex_color.lstrip("#")
