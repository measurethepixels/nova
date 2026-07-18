# REPLICATE.md — set this pipeline up on YOUR system (a contract for AI agents)

You are (probably) an AI coding agent — Claude Code, Codex, Gemini CLI, or similar —
and a human has asked you to make this astrophotography pipeline run on *their*
machine. This document is your contract. A human can follow it too; it will just
take longer.

This is not an installer. Every deployment of this system is a **port**: different
OS, storage, telescope workflow, and a different set of (partly commercial) tools.
Your job is to discover what exists on this machine, configure the pipeline around
it, and **prove the port works** with the verification protocol at the end. Success
is measured, not assumed — the same *measure → try → measure → adjust* loop the
pipeline itself runs on every image.

## 1. What this system is

A FastAPI service (`nas_server/`) that watches for new telescope captures, stacks
them (Siril / PixInsight / ImageMM), then runs a fully autonomous processing
pipeline (`auto_process.py`) in which every step is measured from pixel statistics,
gated by physics, guarded against artifacts, and graded at the end. Around that
core: a SQLite library (`database.py`), a web UI (`web.py`), a job queue
(`queue_manager.py`), an AI target planner, Telegram notifications, and optional
NINA / remote-worker integrations.

Module map worth loading before you edit anything: `config.py` (settings loader —
ALL personal/machine config lives in `settings.json`), `stacker.py`,
`auto_process.py`, `seti_astro.py` (image ops), `image_analyzer.py` (the ruler),
`main.py` (API), `workflow_version.py` (+ `critiques/WORKFLOW_CHANGELOG.md` — the
pipeline's versioned history; read it to understand why thresholds are what they
are).

## 2. Environment discovery (run these probes first)

1. OS + Python ≥3.12 + venv; GPU present? (`nvidia-smi`) — GPU accelerates
   GraXpert and the RC-Astro tools but nothing *requires* it.
2. Which engines exist? Probe `PATH` and common install dirs for: `siril-cli`,
   `astap_cli` (+ its star database), `GraXpert`, PixInsight (`PixInsight` binary),
   RC-Astro plugins inside PixInsight (BlurX/NoiseX/StarXTerminator), Seti Astro
   Suite Pro. Record versions.
3. Storage: where do captures land, where should the library live, how much space
   (stacking needs ~3× the frame set in scratch space)?
4. How do captures arrive? SeeStar SMB share, NINA output folder, or manual drops —
   this decides the watcher configuration.
5. Optional services: Telegram bot (notifications), Anthropic API key (final
   aesthetic eval + planner prose), Ollama (local fallback).

## 3. Capability matrix

| Tool | Status | Enables | Without it |
|---|---|---|---|
| Siril (`siril-cli`) | **required (free)** | registration + stacking | no pipeline |
| ASTAP + star DB | **required (free)** | plate solving, WCS | no solving/framing |
| GraXpert | **strongly recommended (free)** | background extraction | gradient removal degraded |
| Python stack (`requirements.txt`) | **required** | everything | — |
| Seti Astro Suite Pro (`pip install setiastrosuitepro`) | **recommended (free)** | ImageMM stacking engine, GHS/statistical stretches, star ops, narrowband tools | Siril-only stacking; reduced stretch/NB toolset |
| PixInsight (paid) | optional | PI stacking engine, SPCC, drizzle, star tools | Siril/ImageMM paths used instead |
| RC-Astro BXT/NXT/SXT (paid, in PI) | optional | deconvolution, best denoise, star removal | those steps skip; star split unavailable |
| Telegram bot | optional | push notifications | log-only |
| Anthropic API key | optional | final aesthetic eval, planner prose | physics-only grading (`physics_only`) |

**Free core** = Siril + ASTAP + GraXpert + Seti Astro Suite Pro (pip) + Python: a
complete, honest stack→process→grade path — including the pipeline's stretch
arsenal and a second stacking engine, all free. Configure what exists; make missing-tool steps *skip
cleanly* rather than crash. If you find a step that crashes on a missing optional
binary, that is a bug worth fixing — but fix it as a capability gate, not by
deleting the step.

## 4. The config contract

Everything machine- or person-specific lives in **`settings.json`** (path given to
the service; copy `settings.example.json`). You edit **config, not pipeline
logic**. Key groups:

- **Paths**: `seestar_incoming_path`, `seestar_library_path`, `db_path`,
  `nas_work_path`, `calibration_library_path`, `pixinsight_cache_dir`
- **Binaries**: `graxpert_bin`, `cosmicclarity_bin` (bare names = found on PATH)
- **Site**: `observer_lat/lon/elevation_m`, `observer_horizon` (for the planner —
  the user's coordinates, ask them)
- **Services**: `telegram_token/chat_id`, `anthropic_api_key`, `ollama_model`,
  `api_host/api_port`, `web_link_host`
- **Integrations** (leave unset if unused): `nina_*`, `remote_workers`, `vm_url`
- **Tuning**: `pi_*` memory budgets — set from this machine's RAM

## 5. Allowed vs forbidden adaptations

**Allowed (expected):** paths and mounts; service manager (a systemd unit ships in
`nas_server/deploy/` — translate to launchd/Task Scheduler/WSL as needed); engine
substitutions via capability flags; single-machine layout (API + worker on one
box); skipping optional integrations.

**Placeholders you MUST substitute:** exported files use `__REPO_ROOT__` (where you
cloned this repo), `__VENV__` (the Python venv you create), `__DATA_DIR__` (where
settings.json + the SQLite DB live), and `__USER__` (the service user). They appear
in the systemd unit and in the PixInsight JS headless-include paths
(`nas_server/pi_postprocess.js`, `pi_solve.js`) — PI's `#include` needs absolute
paths, so rewrite those two after cloning if PixInsight is in play.

**Forbidden without the human's explicit ask:** scoring/gating thresholds, guard
logic, stretch-picker behavior, workflow step order — these encode months of
measured iteration (see `critiques/WORKFLOW_CHANGELOG.md`; the version number is
stamped into every run). If a threshold seems wrong for the user's data, surface
it, don't silently change it.

## 6. What to ask the human to do (you can't)

- Buy/install licensed tools they want (PixInsight, RC-Astro, SASpro) and run each
  GUI once to accept licenses.
- Create a Telegram bot (@BotFather) if they want notifications; get an Anthropic
  API key if they want the aesthetic eval.
- Give you their site coordinates and where captures will arrive.
- Download the big star databases (ASTAP D20/D50; optionally Gaia DR3 for PI SPCC —
  tens of GB).

## 7. Verification protocol (the port isn't done until this passes)

Sample data: `sample_data/` (or the release-page link) — a small real SeeStar
capture set with golden outputs.

1. `python -m venv` + `pip install -r requirements.txt`; service starts; web UI
   loads (`/`, `/queue`, `/learning` return 200).
2. Stack the sample set through the queue → a `*_siril_stack.fit` lands in the
   library with a valid WCS (ASTAP solve log line present).
3. Run auto-process on the stack → run dir contains `run.log` with
   `workflow_version`, per-step `step_records`, a `stretch_pick` record, and a
   final score.
4. Compare measured metrics to `sample_data/golden.json` (SNR/FWHM/background
   within the stated tolerances — they encode machine variance).
5. Report to the human: capability matrix found, steps enabled/skipped, metric
   comparison table, and anything you adapted beyond config.

## 8. Support boundary

This project is shared as-is alongside the YouTube channel. Issues and PRs are
welcome, but responses aren't guaranteed — it's a hobby project on a fixed time
budget. Your best help: the changelog, the workflow docs page (`/workflows-doc`
when the service runs), and this file.
