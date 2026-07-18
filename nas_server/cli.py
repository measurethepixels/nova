#!/usr/bin/env python3
"""
SeeStar CLI — control and query the NAS server from the command line.

Usage:
    seestar status
    seestar mounts
    seestar pipeline [target]
    seestar stage <target> <stage>
    seestar organize <target>
    seestar scan
    seestar check
    seestar logs [--n N]
    seestar processed <target> [--notes]
    seestar assess <target>
    seestar cc <target> [--mode MODE]
    seestar stack <target> [--engine siril|imagemm]
    seestar stack-status
    seestar stack-kill <target>

Stages: captured | stacked | processing | processed | exported
"""

import argparse
import json
import os
import sys

try:
    import requests
except ImportError:
    print("requests not installed. Run: pip install requests")
    sys.exit(1)

SETTINGS_FILE = os.path.expanduser("~/seestar_database/settings.json")
DEFAULT_URL = "http://localhost:8000"

STAGE_SYMBOLS = {
    "captured":   "○",
    "stacked":    "◑",
    "processing": "◕",
    "processed":  "●",
    "exported":   "✓",
}


def _load_url() -> str:
    try:
        with open(SETTINGS_FILE) as f:
            data = json.load(f)
        return data.get("nas_api_url", DEFAULT_URL)
    except Exception:
        return DEFAULT_URL


def _get(url, path, **params):
    try:
        r = requests.get(f"{url}{path}", params=params, timeout=8)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.ConnectionError:
        print(f"Cannot connect to {url} — is the server running?")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


def _post(url, path, data=None, params=None):
    try:
        r = requests.post(f"{url}{path}", json=data, params=params, timeout=8)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.ConnectionError:
        print(f"Cannot connect to {url} — is the server running?")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


def _delete(url, path):
    try:
        r = requests.delete(f"{url}{path}", timeout=8)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.ConnectionError:
        print(f"Cannot connect to {url} — is the server running?")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


def cmd_status(url, _args):
    data = _get(url, "/status")
    print(f"  Status:   {data['status']}")
    print(f"  Incoming: {data['incoming']}")
    print(f"  Library:  {data['library']}")
    print(f"  DB:       {data['db']}")


def cmd_mounts(url, _args):
    data = _get(url, "/mounts")
    for name, info in data.items():
        ok = "✓" if info["accessible"] else "✗"
        items = f"  ({info['items']} items)" if info.get("items") is not None else ""
        err = f"  ERROR: {info['error']}" if "error" in info else ""
        print(f"  {ok} {name}: {info['path']}{items}{err}")


def cmd_pipeline(url, args):
    if args.target:
        data = _get(url, f"/pipeline/{requests.utils.quote(args.target, safe='')}")
        rows = [data]
    else:
        rows = _get(url, "/pipeline")

    if not rows:
        print("  No targets in pipeline.")
        return

    col_w = max(len(r["target"]) for r in rows)
    print(f"  {'TARGET':<{col_w}}  {'STAGE':<12}  {'STACKED':>7}  {'LIGHTS':>6}  {'EXP(h)':>6}  UPDATED")
    print(f"  {'-'*col_w}  {'-'*12}  {'-'*7}  {'-'*6}  {'-'*6}  {'-'*16}")
    for r in rows:
        sym = STAGE_SYMBOLS.get(r["stage"], "?")
        exp_h = round((r.get("total_exposure") or 0) / 3600, 1)
        updated = (r.get("updated_at") or "")[:16]
        print(f"  {r['target']:<{col_w}}  {sym} {r['stage']:<10}  {r.get('stacked_count',0):>7}  "
              f"{r.get('light_count',0):>6}  {exp_h:>6.1f}  {updated}")


def cmd_stage(url, args):
    payload = {"stage": args.stage}
    if args.notes:
        payload["notes"] = args.notes
    data = _post(url, f"/pipeline/{requests.utils.quote(args.target, safe='')}", payload)
    print(f"  '{data['target']}' → {data['stage']}")


def cmd_organize(url, args):
    data = _post(url, "/organize", {"target": args.target})
    print(f"  {data['message']}")


def cmd_scan(url, _args):
    data = _post(url, "/sync")
    print(f"  {data['message']}")


def cmd_check(url, _args):
    data = _post(url, "/check")
    print(f"  {data['message']} — {data['pending']} pending")


def cmd_logs(url, args):
    n = args.n if args.n else 50
    entries = _get(url, "/logs", n=n)
    for e in entries:
        print(f"  {e['time']}  {e['level']:<8}  {e['message']}")


def cmd_processed(url, args):
    data = _get(url, f"/processed/{requests.utils.quote(args.target, safe='')}")
    files = data.get("files", [])
    if not files:
        print(f"  No processed files for '{args.target}'.")
        return
    print(f"  {'ID':>4}  {'FILENAME':<50}  {'TOOL':<10}  {'STEP':<12}  {'INTEG(h)':>8}  {'FLAGS'}")
    print(f"  {'-'*4}  {'-'*50}  {'-'*10}  {'-'*12}  {'-'*8}  {'-'*20}")
    for f in files:
        integ_h = round((f.get("total_integration") or 0) / 3600, 1)
        flags = f.get("flags") or "{}"
        try:
            import json
            flag_keys = ", ".join(k for k, v in json.loads(flags).items() if v)
        except Exception:
            flag_keys = flags
        auto = " [auto]" if f.get("is_auto") else ""
        print(f"  {f['id']:>4}  {f['filename']:<50}  {(f.get('tool') or ''):.<10}  "
              f"{(f.get('step') or ''):.<12}  {integ_h:>8.1f}  {flag_keys}{auto}")
    if args.notes and len(files) == 1:
        print(f"\n  Notes: {files[0].get('notes') or '(none)'}")


def cmd_assess(url, args):
    data = _post(url, f"/assess/{args.target}", {})
    if "error" in data:
        print(f"  Error: {data['error']}")
    elif "message" in data:
        print(f"  {data['message']}")
    else:
        scores = data.get("scores", {})
        overall = scores.get("overall", "?")
        print(f"  {args.target}: Claude score {overall}/10")
        if scores.get("issues"):
            print(f"  Issues: {', '.join(scores['issues'])}")
        if scores.get("suggestions"):
            print(f"  Suggestions: {', '.join(scores['suggestions'])}")


def cmd_cc(url, args):
    mode = args.mode
    print(f"  Running CC {mode} on {args.target} (may take several minutes)...")
    try:
        r = requests.post(f"{url}/cc/{args.target}", params={"mode": mode}, timeout=1800)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"  Error: {e}")
        return
    if data.get("ok"):
        print(f"  {args.target}: CC {mode} complete in {data.get('elapsed_s')}s")
        if data.get("output_path"):
            print(f"  Output: {data['output_path']}")
    else:
        print(f"  Error: {data.get('error', data)}")


def cmd_stretch(url, args):
    params = {"mode": args.mode, "target_median": args.median}
    if args.mode == "ghs":
        params["alpha"] = args.alpha
        params["gamma"] = args.gamma
    try:
        r = requests.post(f"{url}/stretch/{args.target}", params=params, timeout=300)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"  Error: {e}")
        return
    if data.get("ok"):
        print(f"  {args.target}: {args.mode} stretch done in {data.get('elapsed_s')}s")
        print(f"  Output: {data.get('output_path')}")
    else:
        print(f"  Error: {data.get('error', data)}")


def cmd_bgextract(url, args):
    print(f"  Running GraXpert background extraction on {args.target}...")
    try:
        r = requests.post(f"{url}/bgextract/{args.target}",
                          params={"correction": args.correction, "smoothing": args.smoothing},
                          timeout=1800)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"  Error: {e}")
        return
    if data.get("ok"):
        print(f"  Done in {data.get('elapsed_s')}s → {data.get('output_path')}")
    else:
        print(f"  Error: {data.get('error', data)}")


def cmd_score(url, args):
    data = _get(url, f"/score/{args.target}", bottom_pct=args.bottom_pct)
    if not data.get("ok"):
        print(f"  Error: {data.get('error', data)}")
        return
    frames = data.get("frames", [])
    flagged = set(data.get("flagged", []))
    pre_excl = data.get("pre_excluded", [])
    print(f"  {args.target}: {data['total']} frames scored in {data['elapsed_s']}s")
    if pre_excl:
        print(f"  Already in _exclude: {len(pre_excl)} frames")
    print(f"  Bottom {args.bottom_pct*100:.0f}% flagged: {len(flagged)} frames")
    if args.all:
        print(f"\n  {'File':<40} {'FWHM':>6} {'Ecc':>6} {'Stars':>6} {'Score':>8}")
        print("  " + "-" * 70)
        for f in frames:
            flag = " ← flag" if f["file"] in flagged else ""
            print(f"  {f['file']:<40} {str(f['fwhm'] or '-'):>6} {str(f['eccentricity'] or '-'):>6} "
                  f"{f['star_count']:>6} {f['score']:>8.1f}{flag}")
    else:
        print(f"\n  Flagged for exclusion:")
        for name in flagged:
            print(f"    ✗ {name}")


def _link_url(url):
    """Rewrite localhost URLs to a shareable host for printed links.

    Host comes from $SEESTAR_NAS_HOST or settings.json web_link_host; if neither
    is set the URL is printed as-is.
    """
    import json as _json
    host = os.environ.get("SEESTAR_NAS_HOST", "")
    if not host:
        try:
            _sp = os.path.expanduser("~/seestar_database/settings.json")
            host = _json.load(open(_sp)).get("web_link_host", "")
        except Exception:
            host = ""
    if not host:
        return url
    return url.replace("localhost", host).replace("127.0.0.1", host)


def cmd_report(url, args):
    nas_url = _link_url(url)
    target = getattr(args, "target", None)
    run_id = getattr(args, "run_id", None)
    if target and run_id:
        link = f"{nas_url}/report/{target.replace(' ', '%20')}/{run_id}"
        print(f"  Run report: {link}")
    elif target:
        link = f"{nas_url}/report/{target.replace(' ', '%20')}"
        print(f"  All runs for {target}: {link}")
        try:
            r = requests.get(f"{url}/report/{target}", timeout=10)
            r.raise_for_status()
        except Exception as e:
            print(f"  {e}")
    else:
        print(f"  Reports index: {nas_url}/report/")
        print(f"  Usage: seestar report <target> [--run-id N]")


def cmd_devlog(url, args):
    nas_url = _link_url(url)
    if getattr(args, "add", False):
        title = args.title or input("  Title: ").strip()
        body = args.body or input("  Body: ").strip()
        category = args.category or "decision"
        files = args.files or ""
        if not title or not body:
            print("  Title and body are required.")
            return
        try:
            r = requests.post(f"{url}/devlog",
                              params={"title": title, "body": body,
                                      "category": category, "files": files},
                              timeout=15)
            r.raise_for_status()
            d = r.json()
            print(f"  Added entry: {d['id']} — {title}")
        except Exception as e:
            print(f"  Error: {e}")
    else:
        print(f"  Dev journal: {nas_url}/devlog")
        if not getattr(args, "open", False):
            print(f"  Add entry:   seestar devlog --add --title '...' --body '...'")


def cmd_story(url, args):
    if getattr(args, "previews", False):
        target = getattr(args, "target", None)
        params = {}
        if target:
            params["target"] = target
        print("  Generating preview JPEGs for targets missing thumbnails...")
        try:
            r = requests.post(f"{url}/story/previews", params=params, timeout=300)
            r.raise_for_status()
            d = r.json()
            print(f"  Generated: {d['generated']}  |  Already had JPG: {d['skipped_already_have_jpg']}  |  Errors: {d['errors']}")
            for item in d.get("details", []):
                print(f"    ✓ {item['target']:30s}  {item['fits']} → {item['preview']}")
            for item in d.get("error_details", []):
                print(f"    ✗ {item['target']:30s}  {item['error']}")
        except Exception as e:
            print(f"  Error: {e}")
    elif args.export:
        out = getattr(args, "out", None) or "astro_story.html"
        embed = getattr(args, "embed_images", False)
        print(f"  Downloading story to {out}...")
        try:
            r = requests.get(f"{url}/story/export",
                             params={"embed_images": str(embed).lower()}, timeout=60)
            r.raise_for_status()
            with open(out, "wb") as f:
                f.write(r.content)
            print(f"  Saved: {out}  ({len(r.content):,} bytes)")
        except Exception as e:
            print(f"  Error: {e}")
    elif args.regenerate:
        target = getattr(args, "target", None)
        params = {}
        if target:
            params["target"] = target
        print("  Regenerating Claude narratives (requires API key)...")
        try:
            r = requests.post(f"{url}/story/regenerate", params=params, timeout=300)
            r.raise_for_status()
            d = r.json()
            print(f"  Started — {d.get('total', 0)} targets to process")
            print(f"  Status: GET {url}/story/regenerate")
        except Exception as e:
            print(f"  Error: {e}")
    else:
        nas_url = _link_url(url)
        print(f"  Story page:  {nas_url}/story")
        print(f"  Single target: {nas_url}/story?target=C+77")
        print(f"")
        print(f"  Commands:")
        print(f"    seestar story --previews           generate missing thumbnails")
        print(f"    seestar story --regenerate         refresh Claude narratives")
        print(f"    seestar story --export             download as HTML file")
        print(f"    seestar story --export --embed-images  self-contained (no server needed)")


def cmd_autoprocess(url, args):
    dry = getattr(args, "dry_run", False)
    workflow = getattr(args, "workflow", "seestar_broadband")
    experiment = getattr(args, "experiment", False)
    label = f"workflow={workflow}" + (" +experiment" if experiment else "") + (" DRY RUN" if dry else "")
    print(f"  Starting autoprocess for {args.target} ({label})...")
    try:
        r = requests.post(
            f"{url}/autoprocess/{args.target}",
            params={"workflow": workflow, "dry_run": str(dry).lower(),
                    "experiment_mode": str(experiment).lower()},
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"  Error: {e}")
        return
    print(f"  {data.get('message', data)}")
    print(f"  Poll status: seestar autoprocess-status {args.target}")


def cmd_postprocess(url, args):
    target = args.target
    params = {"engine": "pixinsight"}
    # Background
    if getattr(args, "dbe", False):
        params["dbe"] = "true"
    if getattr(args, "no_gc", False):
        params["gradient_correction"] = "false"
    if getattr(args, "adbe", False):
        params["adbe"] = "true"
        params["gradient_correction"] = "false"
        params["adbe_degree"] = getattr(args, "adbe_degree", 2)
        params["adbe_rbf_smooth"] = getattr(args, "adbe_smooth", 0.1)
    # Color
    if getattr(args, "no_cc", False):
        params["color_calibration"] = "false"
    if getattr(args, "bgn", False):
        params["bgn"] = "true"
    if getattr(args, "spcc", False):
        params["spcc"] = "true"
    # Linear
    if getattr(args, "mlt", False):
        params["mlt"] = "true"
        params["mlt_sharpen"] = getattr(args, "mlt_sharpen", 0.20)
        params["mlt_denoise"] = getattr(args, "mlt_denoise", 0.50)
    if getattr(args, "tgv", False):
        params["tgv"] = "true"
        params["tgv_strength"] = getattr(args, "tgv_strength", 1.0)
    # AI
    if getattr(args, "no_bxt", False):
        params["bxt"] = "false"
    if getattr(args, "bxt_auto_psf", False):
        params["bxt_auto_psf"] = "true"
    if getattr(args, "bxt_psf", None) is not None:
        params["bxt_psf"] = args.bxt_psf
    if getattr(args, "no_nxt", False):
        params["nxt"] = "false"
    if getattr(args, "nxt_denoise", None) is not None:
        params["nxt_denoise"] = args.nxt_denoise
    if getattr(args, "starxt", False):
        params["starxt"] = "true"
    # Stretch
    if getattr(args, "ht", False):
        params["ht"] = "true"
        params["ht_target_bg"] = getattr(args, "ht_target_bg", 0.12)
    # Non-linear
    if getattr(args, "scnr", False):
        params["scnr"] = "true"
        params["scnr_amount"] = getattr(args, "scnr_amount", 0.9)
    if getattr(args, "hdrmt", False):
        params["hdrmt"] = "true"
    if getattr(args, "lhe", False):
        params["lhe"] = "true"
        params["lhe_amount"] = getattr(args, "lhe_amount", 0.5)
    if getattr(args, "color_sat", False):
        params["color_sat"] = "true"
        params["color_sat_boost"] = getattr(args, "color_sat_boost", 0.3)
    if getattr(args, "curves", False):
        params["curves"] = "true"
        params["curves_shape"] = getattr(args, "curves_shape", "s_med")

    print(f"  Starting PixInsight post-processing for '{target}'...")
    enabled = [k for k, v in params.items()
               if k not in ("engine",) and str(v) not in ("false", "0")]
    print(f"  Enabled: {', '.join(enabled)}")
    data = _post(url, f"/postprocess/{target}", params=params)
    if data.get("error"):
        print(f"  Error: {data['error']}")
    else:
        print(f"  Status: {data.get('status')} | source: {data.get('source')}")
        print(f"  Check progress: seestar postprocess-status {target}")


def cmd_postprocess_status(url, args):
    data = _get(url, f"/postprocess/{args.target}")
    phase = data.get("phase", "idle")
    print(f"  {args.target}: phase={phase}")
    if data.get("steps"):
        print(f"  Steps: {', '.join(data['steps'])}")
    if data.get("elapsed"):
        print(f"  Elapsed: {data['elapsed']:.0f}s")
    if data.get("output_path"):
        print(f"  Output: {data['output_path']}")
    if data.get("log"):
        print(f"  Last log lines:\n    " + "\n    ".join(data["log"].splitlines()[-5:]))


def cmd_autoprocess_status(url, args):
    data = _get(url, f"/autoprocess/{args.target}")
    if not data.get("active"):
        print(f"  No active autoprocess for '{args.target}'")
        return
    print(f"  {args.target}: phase={data.get('phase')} workflow={data.get('workflow')}")
    if data.get("steps_applied"):
        print(f"  Steps applied: {', '.join(data['steps_applied'])}")
    if data.get("final_scores"):
        for k, v in data["final_scores"].items():
            if isinstance(v, (int, float)) and k not in ("input_tokens", "output_tokens"):
                print(f"    {k}: {v}/10")
    if data.get("elapsed"):
        print(f"  Elapsed: {data['elapsed']:.0f}s")


def cmd_queue(url, args):
    sub = getattr(args, "queue_cmd", None)
    if sub == "add":
        workflow = getattr(args, "workflow", "seestar_broadband")
        experiment = getattr(args, "experiment", False)
        dry = getattr(args, "dry_run", False)
        source_file = getattr(args, "source_file", None)
        params = {
            "target": args.target, "workflow": workflow,
            "experiment_mode": str(experiment).lower(), "dry_run": str(dry).lower(),
        }
        if source_file:
            params["source_file"] = source_file
        data = _post(url, "/queue", params=params)
        if data:
            label = f"workflow={workflow}" + (" +experiment" if experiment else "")
            sf_note = f"  source={source_file}" if source_file else ""
            print(f"  Queued '{args.target}' ({label}) at position {data.get('position')}{sf_note}")
    elif sub == "list":
        data = _get(url, "/queue")
        if not data:
            return
        count = data.get("count", 0)
        if count == 0:
            print("  Queue is empty")
            return
        print(f"  {count} job(s) pending:")
        for item in data.get("queue", []):
            exp = " +experiment" if item.get("experiment_mode") else ""
            print(f"    [{item['position']}] {item['target']}  workflow={item['workflow']}{exp}")
    elif sub == "clear":
        data = _delete(url, "/queue")
        if data:
            print(f"  {data.get('message', 'Queue cleared')}")
    elif sub == "remove":
        data = _delete(url, f"/queue/{args.position}")
        if data:
            print(f"  {data.get('message', 'Removed')}")
    else:
        print("  Usage: seestar queue <add|list|clear|remove>")


def cmd_stack(url, args):
    engine = getattr(args, "engine", "siril")
    print(f"  Starting {engine} stack for {args.target}...")
    data = _post(url, f"/stack/{args.target}", params={"engine": engine})
    print(f"  {data.get('message', data)}")


def cmd_stack_status(url, _args):
    data = _get(url, "/stack")
    running = data.get("running", [])
    if not running:
        print("  No stacks currently running")
    else:
        for s in running:
            print(f"  {s['target']}: {s['frames']} frames, running {s['elapsed_human']}, pid {s['pid']}")


def cmd_stack_kill(url, args):
    data = _delete(url, f"/stack/{args.target}")
    print(f"  {data.get('message', data)}")


def _delete(url, path):
    try:
        resp = requests.delete(f"{url}{path}", timeout=30)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.ConnectionError:
        print(f"  Cannot connect to {url}")
        sys.exit(1)
    except Exception as e:
        print(f"  Request failed: {e}")
        sys.exit(1)


def cmd_sort(url, args):
    if args.run:
        target = args.target if args.target else None
        data = _post(url, "/sort", {"target": target} if target else {})
        print(f"  {data['message']}")
        if data.get("targets"):
            for t in data["targets"]:
                print(f"    → {t}")
    else:
        data = _get(url, "/sort")
        if not data["sessions"]:
            print(f"  Nothing in Downloaded to sort  ({data['path']})")
        else:
            print(f"  {data['count']} session(s) in Downloaded to sort:")
            for s in data["sessions"]:
                print(f"    • {s}")


def cmd_experiment(url, args):
    target = args.target
    step = getattr(args, "step", "background_extraction") or "background_extraction"
    dry = getattr(args, "dry_run", False)
    print(f"  Starting experiment for '{target}' / step '{step}' (dry_run={dry})...")
    data = _post(url, f"/experiment/{target}/{step}", params={"dry_run": str(dry).lower()})
    if data.get("error"):
        print(f"  ERROR: {data['error']}")
        return
    print(f"  {data.get('status', 'submitted')}")
    print(f"  Results will be sent via Telegram when done.")
    print(f"  Check status: seestar experiment {target}")


def cmd_learning(url, args):
    step = getattr(args, "step", None)
    obj_type = getattr(args, "object_type", None)
    if step:
        data = _get(url, f"/learning/{step}", **({"object_type": obj_type} if obj_type else {}))
        learned = data.get("learned_defaults", {})
        priors = data.get("priors", {})
        n = priors.get("sample_count", 0)
        if n == 0:
            print(f"  No experiment data yet for step '{step}'")
            return
        print(f"  Learning priors for '{step}' ({n} experiments):")
        top = learned.get("variant")
        conf = int(learned.get("confidence", 0) * 100)
        if top:
            print(f"    Best variant: {top} ({conf}% win rate)")
        wr = priors.get("variant_win_rate", {})
        for variant, rate in sorted(wr.items(), key=lambda x: -x[1]):
            bar = "█" * int(rate * 20)
            print(f"    {variant:<30} {bar} {int(rate*100)}%")
        avg_params = learned.get("params", {})
        if avg_params:
            print(f"    Avg winning params: {json.dumps(avg_params, indent=6)}")
    else:
        data = _get(url, "/learning")
        steps = data.get("steps_with_data", [])
        if not steps:
            print("  No experiment data yet. Run: seestar experiment <target> <step>")
            return
        print(f"  Steps with accumulated learning data:")
        for s in data.get("summary", []):
            ld = s.get("learned", {})
            n = ld.get("sample_count", 0)
            top = ld.get("variant", "?")
            conf = int(ld.get("confidence", 0) * 100)
            print(f"    {s['step']:<30} n={n:>4}  best={top} ({conf}%)")


def main():
    parser = argparse.ArgumentParser(prog="seestar", description="SeeStar NAS CLI")
    parser.add_argument("--url", default=None, help=f"API URL (default: {DEFAULT_URL})")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="Server status and paths")
    sub.add_parser("mounts", help="Check SMB mount health")

    p_pipeline = sub.add_parser("pipeline", help="Show pipeline status")
    p_pipeline.add_argument("target", nargs="?", help="Show a single target")

    p_stage = sub.add_parser("stage", help="Update a target's pipeline stage")
    p_stage.add_argument("target")
    p_stage.add_argument("stage", choices=["captured", "stacked", "processing", "processed", "exported"])
    p_stage.add_argument("--notes", default=None)

    p_org = sub.add_parser("organize", help="Organize a session from incoming to library")
    p_org.add_argument("target")

    sub.add_parser("scan", help="Trigger full library re-scan")
    sub.add_parser("check", help="Force watcher to check incoming now")

    p_logs = sub.add_parser("logs", help="Show recent log entries")
    p_logs.add_argument("--n", type=int, default=50, help="Number of entries (default 50)")

    p_sort = sub.add_parser("sort", help="List or process sessions in Downloaded to sort")
    p_sort.add_argument("target", nargs="?", help="Specific target to organize (default: all)")
    p_sort.add_argument("--run", action="store_true", help="Actually run the organizer (default: just list)")

    p_proc = sub.add_parser("processed", help="List processed files for a target")
    p_proc.add_argument("target", help="Target name (e.g. 'C 77')")
    p_proc.add_argument("--notes", action="store_true", help="Show notes (only with single result)")

    p_assess = sub.add_parser("assess", help="Run Claude quality assessment on latest stack")
    p_assess.add_argument("target", help="Target name (e.g. 'C 77')")

    p_stretch = sub.add_parser("stretch", help="Stretch latest stacked FITS (stat or GHS)")
    p_stretch.add_argument("target")
    p_stretch.add_argument("--mode", default="stat", choices=["stat", "ghs", "veralux"],
                           help="Stretch algorithm (default: stat)")
    p_stretch.add_argument("--median", type=float, default=0.25,
                           help="Target median / GHS pivot (default: 0.25)")
    p_stretch.add_argument("--alpha", type=float, default=5.0,
                           help="GHS alpha (strength, default: 5.0)")
    p_stretch.add_argument("--gamma", type=float, default=3.0,
                           help="GHS gamma (shape, default: 3.0)")

    p_score = sub.add_parser("score", help="Score frame quality (FWHM, eccentricity, stars)")
    p_score.add_argument("target")
    p_score.add_argument("--bottom-pct", type=float, default=0.10,
                         help="Flag bottom N%% as poor quality (default: 10%%)")
    p_score.add_argument("--all", action="store_true", help="Show all frames, not just flagged")

    p_bg = sub.add_parser("bgextract", help="AI background extraction via GraXpert")
    p_bg.add_argument("target")
    p_bg.add_argument("--correction", default="Subtraction",
                      choices=["Subtraction", "Division"],
                      help="Correction type (default: Subtraction)")
    p_bg.add_argument("--smoothing", type=float, default=0.5,
                      help="Smoothing 0.0–1.0 (default: 0.5)")

    p_cc = sub.add_parser("cc", help="Run Cosmic Clarity AI processing on latest stack")
    p_cc.add_argument("target", help="Target name")
    p_cc.add_argument("--mode", default="denoise",
                      choices=["denoise", "sharpen", "both", "satellite", "darkstar"],
                      help="Processing mode (default: denoise)")

    p_stack = sub.add_parser("stack", help="Stack frames for a target")
    p_stack.add_argument("target", help="Target name")
    p_stack.add_argument("--engine", default="siril", choices=["siril", "imagemm", "both"],
                         help="Stacking engine: siril (default) | imagemm | both")

    sub.add_parser("stack-status", help="Show all currently running stacks")

    p_kill = sub.add_parser("stack-kill", help="Kill a running stack process")
    p_kill.add_argument("target", help="Target name to kill")

    p_pp = sub.add_parser("postprocess", help="Run PixInsight post-processing (full tool suite)")
    p_pp.add_argument("target", help="Target name (e.g. 'M 51')")
    # Background
    p_pp.add_argument("--dbe", action="store_true",
                      help="Use DynamicBackgroundExtraction instead of GradientCorrection")
    p_pp.add_argument("--no-gc", action="store_true", help="Disable GradientCorrection")
    p_pp.add_argument("--adbe", action="store_true",
                      help="Run SASpro ADBE before PI (replaces GC/DBE)")
    p_pp.add_argument("--adbe-degree", type=int, default=2, help="ADBE poly degree 1-6 (default: 2)")
    p_pp.add_argument("--adbe-smooth", type=float, default=0.1, help="ADBE RBF smooth (default: 0.1)")
    # Color
    p_pp.add_argument("--no-cc", action="store_true", help="Disable ColorCalibration")
    p_pp.add_argument("--bgn", action="store_true", help="Enable BackgroundNeutralization")
    p_pp.add_argument("--spcc", action="store_true", help="Enable SPCC (requires plate-solved WCS)")
    # Linear sharp/denoise
    p_pp.add_argument("--mlt", action="store_true", help="Enable MultiscaleLinearTransform sharpening")
    p_pp.add_argument("--mlt-sharpen", type=float, default=0.20, help="MLT sharpen coeff (default: 0.20)")
    p_pp.add_argument("--mlt-denoise", type=float, default=0.50, help="MLT denoise coeff (default: 0.50)")
    p_pp.add_argument("--tgv", action="store_true", help="Enable TGVDenoise (CPU, no GPU needed)")
    p_pp.add_argument("--tgv-strength", type=float, default=1.0, help="TGV strength (default: 1.0)")
    # AI plugins
    p_pp.add_argument("--no-bxt", action="store_true", help="Disable BlurXTerminator")
    p_pp.add_argument("--bxt-auto-psf", action="store_true", help="BXT: auto-detect PSF")
    p_pp.add_argument("--bxt-psf", type=float, default=4.0, help="BXT PSF FWHM in pixels (default: 4.0)")
    p_pp.add_argument("--no-nxt", action="store_true", help="Disable NoiseXTerminator")
    p_pp.add_argument("--nxt-denoise", type=float, default=0.70, help="NXT denoise 0–1 (default: 0.70)")
    p_pp.add_argument("--starxt", action="store_true", help="Enable StarXTerminator (star removal)")
    # Stretch
    p_pp.add_argument("--ht", action="store_true", help="Apply HistogramTransformation auto-stretch")
    p_pp.add_argument("--ht-target-bg", type=float, default=0.12, help="HT target background (default: 0.12)")
    # Non-linear
    p_pp.add_argument("--scnr", action="store_true", help="Enable SCNR green removal")
    p_pp.add_argument("--scnr-amount", type=float, default=0.9, help="SCNR amount (default: 0.9)")
    p_pp.add_argument("--hdrmt", action="store_true", help="Enable HDRMultiscaleTransform (nebulae)")
    p_pp.add_argument("--lhe", action="store_true", help="Enable LocalHistogramEqualization")
    p_pp.add_argument("--color-sat", action="store_true", help="Enable ColorSaturation boost")
    p_pp.add_argument("--color-sat-boost", type=float, default=0.3, help="Saturation boost (default: 0.3)")
    p_pp.add_argument("--curves", action="store_true", help="Enable CurvesTransformation")
    p_pp.add_argument("--curves-shape", default="s_med",
                      choices=["s_mild", "s_med", "s_strong", "rolloff_highlights", "lift_shadows"],
                      help="Curves shape (default: s_med)")

    sub.add_parser("postprocess-status", help="Check PI postprocess status").add_argument("target")

    p_ap = sub.add_parser("autoprocess", help="Run automated Claude-driven processing pipeline")
    p_ap.add_argument("target", help="Target name (e.g. 'C 77')")
    p_ap.add_argument("--workflow", default="seestar_broadband",
                      choices=["seestar_broadband", "seestar_fast", "seestar_galaxy",
                               "seestar_nebula", "linear_only", "experiment_full",
                               "seestar_starless_stretch", "spcc_only", "quick_default"],
                      help="Processing workflow (default: seestar_broadband)")
    p_ap.add_argument("--dry-run", action="store_true",
                      help="Plan steps without writing files")
    p_ap.add_argument("--experiment", action="store_true",
                      help="Experiment mode: try all variants per step, Claude picks best")

    p_aps = sub.add_parser("autoprocess-status", help="Check autoprocess status for a target")
    p_aps.add_argument("target", help="Target name")

    p_q = sub.add_parser("queue", help="Manage the processing queue")
    q_sub = p_q.add_subparsers(dest="queue_cmd")
    p_qa = q_sub.add_parser("add", help="Add a target to the queue")
    p_qa.add_argument("target", help="Target name (e.g. 'M 51')")
    p_qa.add_argument("--workflow", default="seestar_broadband",
                      choices=["seestar_broadband", "seestar_fast", "seestar_galaxy",
                               "seestar_nebula", "linear_only", "experiment_full",
                               "seestar_starless_stretch", "spcc_only", "quick_default"],
                      help="Processing workflow (default: seestar_broadband)")
    p_qa.add_argument("--experiment", action="store_true",
                      help="Run all variants per step, Claude picks best")
    p_qa.add_argument("--dry-run", action="store_true", help="Plan only, no writes")
    p_qa.add_argument("--source-file", default=None,
                      help="Exact filename to process (overrides DB created_at ordering)")
    q_sub.add_parser("list", help="Show pending queue items")
    q_sub.add_parser("clear", help="Remove all pending queue items")
    p_qr = q_sub.add_parser("remove", help="Remove a specific queue item by position")
    p_qr.add_argument("position", type=int, help="1-based position from queue list")

    p_exp = sub.add_parser("experiment", help="Run experiment mode: compare all variants for a step")
    p_exp.add_argument("target", help="Target name (e.g. 'C 77')")
    p_exp.add_argument("step", nargs="?", default="background_extraction",
                       help="Step to experiment: background_extraction | sharpen_linear | denoise_linear")
    p_exp.add_argument("--dry-run", action="store_true",
                       help="Show what would run without executing")

    p_learn = sub.add_parser("learning", help="Show accumulated learning priors for a step")
    p_learn.add_argument("step", nargs="?", help="Step name (omit for all)")
    p_learn.add_argument("--object-type", default=None,
                         help="Filter by object type (galaxy, emission_nebula, ...)")

    p_report = sub.add_parser("report", help="Open processing run reports for a target")
    p_report.add_argument("target", nargs="?", help="Target name (omit to see all recent runs)")
    p_report.add_argument("--run-id", type=int, default=None, help="Specific run ID")
    p_report.add_argument("--open", action="store_true", help="Open in browser")

    p_devlog = sub.add_parser("devlog", help="View or add pipeline development journal entries")
    p_devlog.add_argument("--add", action="store_true", help="Add a new entry interactively")
    p_devlog.add_argument("--title", default="", help="Entry title (for --add)")
    p_devlog.add_argument("--category", default="decision",
                          choices=["feature", "bug_fix", "decision", "experiment", "data"],
                          help="Entry category (for --add)")
    p_devlog.add_argument("--body", default="", help="Entry body text (for --add)")
    p_devlog.add_argument("--files", default="", help="Comma-separated file list (for --add)")
    p_devlog.add_argument("--open", action="store_true", help="Open devlog in browser")

    p_story = sub.add_parser("story", help="Open or export the astrophotography story page")
    p_story.add_argument("target", nargs="?", help="Single target (default: all)")
    p_story.add_argument("--export", action="store_true", help="Download as HTML file")
    p_story.add_argument("--out", default="astro_story.html", help="Output path for --export")
    p_story.add_argument("--embed-images", action="store_true",
                         help="Embed images as base64 in exported HTML (larger file)")
    p_story.add_argument("--regenerate", action="store_true",
                         help="Regenerate Claude narrative paragraphs (requires API key)")
    p_story.add_argument("--previews", action="store_true",
                         help="Generate missing JPEG thumbnails for targets with stacked FITS")

    args = parser.parse_args()
    url = args.url or _load_url()

    commands = {
        "status": cmd_status,
        "mounts": cmd_mounts,
        "pipeline": cmd_pipeline,
        "stage": cmd_stage,
        "organize": cmd_organize,
        "scan": cmd_scan,
        "check": cmd_check,
        "logs": cmd_logs,
        "sort": cmd_sort,
        "processed": cmd_processed,
        "assess": cmd_assess,
        "score": cmd_score,
        "stretch": cmd_stretch,
        "bgextract": cmd_bgextract,
        "cc": cmd_cc,
        "stack": cmd_stack,
        "stack-status": cmd_stack_status,
        "stack-kill": cmd_stack_kill,
        "postprocess": cmd_postprocess,
        "postprocess-status": cmd_postprocess_status,
        "autoprocess": cmd_autoprocess,
        "autoprocess-status": cmd_autoprocess_status,
        "queue": cmd_queue,
        "experiment": cmd_experiment,
        "learning": cmd_learning,
        "story": cmd_story,
        "report": cmd_report,
        "devlog": cmd_devlog,
    }

    print()
    commands[args.command](url, args)
    print()


if __name__ == "__main__":
    main()
