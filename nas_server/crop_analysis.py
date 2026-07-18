"""
Named crop region editor and analysis page.

Served at GET /crops/{target}/{filename:path}

Lets the user define named regions (e.g. "star_field", "core", "background")
on any JPEG image in _processed/, save them, and send them to Claude for
targeted analysis. Crop JPEGs are stored in ~/seestar_database/crop_previews/.
"""
import os
from pathlib import Path

from nas_server.config import settings

_CROP_DIR = Path(settings["db_path"]).parent / "crop_previews"


def crop_preview_path(crop_id: int) -> Path:
    return _CROP_DIR / f"{crop_id}.jpg"


def generate_crop_jpeg(source_jpeg: Path, crop_id: int,
                       x: float, y: float, w: float, h: float,
                       natural_w: int, natural_h: int,
                       display_w: int, display_h: int) -> Path:
    """
    Crop source_jpeg using the Cropper.js-reported coordinates and save to
    the crop_previews directory. Returns the path to the saved crop JPEG.

    Cropper.js getData() returns coordinates in the *original image* pixel
    space (after accounting for any zoom/pan but before display scaling), so
    we can crop the original file directly.
    """
    from PIL import Image

    _CROP_DIR.mkdir(parents=True, exist_ok=True)
    out_path = crop_preview_path(crop_id)

    img = Image.open(source_jpeg)
    iw, ih = img.size

    # Clamp to image bounds
    x0 = max(0, int(round(x)))
    y0 = max(0, int(round(y)))
    x1 = min(iw, int(round(x + w)))
    y1 = min(ih, int(round(y + h)))

    if x1 <= x0 or y1 <= y0:
        # Degenerate crop — save full image
        cropped = img
    else:
        cropped = img.crop((x0, y0, x1, y1))

    cropped.save(str(out_path), "JPEG", quality=88)
    return out_path


def measure_crop_physics(jpeg_path: Path) -> dict:
    """
    Compute objective pixel-level metrics from a crop JPEG.

    Returns a dict with keys: bg_median, bg_rms, snr_est, clipping_pct,
    gradient_rms, star_count, fwhm_median, ecc_median.
    """
    import numpy as np
    try:
        from PIL import Image
        img = Image.open(jpeg_path).convert("L")  # grayscale
        arr = np.asarray(img, dtype=np.float32)
    except Exception as e:
        return {"error": str(e)}

    # Sigma-clipped background estimate
    flat = arr.flatten()
    for _ in range(3):
        med = float(np.median(flat))
        std = float(np.std(flat))
        flat = flat[np.abs(flat - med) < 3 * std]
    bg_median = float(np.median(flat))
    bg_rms = float(np.std(flat))

    # Peak SNR estimate: (p95 signal - background) / background noise
    p95 = float(np.percentile(arr, 95))
    snr_est = round((p95 - bg_median) / max(bg_rms, 1e-6), 1)

    # Clipping fraction (pixels within top 1% of dynamic range)
    arr_max = float(arr.max())
    clip_thresh = arr_max * 0.99
    clipping_pct = round(float(np.mean(arr >= clip_thresh)) * 100, 2)

    # Gradient: divide image into 4×4 blocks, measure RMS of block medians
    h, w = arr.shape
    bh, bw = max(1, h // 4), max(1, w // 4)
    block_meds = []
    for r in range(4):
        for c in range(4):
            block = arr[r*bh:(r+1)*bh, c*bw:(c+1)*bw]
            if block.size:
                block_meds.append(float(np.median(block)))
    gradient_rms = round(float(np.std(block_meds)), 2) if block_meds else 0.0

    result = {
        "bg_median": round(bg_median, 1),
        "bg_rms": round(bg_rms, 2),
        "snr_est": snr_est,
        "clipping_pct": clipping_pct,
        "gradient_rms": gradient_rms,
    }

    # Star detection via SEP (SExtractor Python)
    try:
        import sep
        data = arr.copy()
        bkg = sep.Background(data)
        data_sub = data - bkg.back()
        thresh = 3.0 * bkg.globalrms
        objs = sep.extract(data_sub, thresh, minarea=5)
        if len(objs) > 0:
            a = objs["a"]  # semi-major axis
            b = objs["b"]  # semi-minor axis
            fwhm = 2.355 * np.sqrt((a**2 + b**2) / 2)
            ecc = np.sqrt(1 - (b / np.maximum(a, 1e-6))**2)
            # Filter out very large detections (cosmic rays, galaxies)
            mask = fwhm < 20
            result["star_count"] = int(np.sum(mask))
            if np.any(mask):
                result["fwhm_median"] = round(float(np.median(fwhm[mask])), 2)
                result["ecc_median"] = round(float(np.median(ecc[mask])), 3)
    except Exception:
        pass  # SEP not available or no stars detected

    return result


def format_physics(metrics: dict) -> str:
    """Format physics metrics as a concise string for inclusion in Claude prompts."""
    if not metrics or "error" in metrics:
        return ""
    parts = []
    if "bg_rms" in metrics:
        parts.append(f"bg_noise={metrics['bg_rms']:.1f}")
    if "snr_est" in metrics:
        parts.append(f"SNR≈{metrics['snr_est']}")
    if "gradient_rms" in metrics:
        parts.append(f"gradient_rms={metrics['gradient_rms']:.1f}")
    if "clipping_pct" in metrics:
        parts.append(f"clipping={metrics['clipping_pct']}%")
    if "star_count" in metrics:
        parts.append(f"stars={metrics['star_count']}")
    if "fwhm_median" in metrics:
        parts.append(f"FWHM={metrics['fwhm_median']}px")
    if "ecc_median" in metrics:
        parts.append(f"ecc={metrics['ecc_median']}")
    return " | ".join(parts)


_DEFAULT_QUESTIONS = {
    "star_field": "Evaluate star roundness, sharpness, and whether there are halo rings or diffraction spikes. Is the PSF consistent across the crop?",
    "core":       "Evaluate fine detail, core sharpness, and whether the core is clipped or blown out. Does the processing look natural?",
    "background": "Evaluate background noise, smoothness, and whether there are gradients, vignetting, or processing artifacts visible in the background.",
    "nebula":     "Evaluate nebula structure detail, noise level, and how well the processing has preserved fine emission features.",
    "galaxy":     "Evaluate galaxy detail, dust lane visibility, outer halo gradient, and whether there's any overprocessing or ringing.",
}


def default_question(name: str) -> str:
    """Return a sensible default analysis question for a known region name."""
    name_lower = name.lower().replace(" ", "_")
    for key, q in _DEFAULT_QUESTIONS.items():
        if key in name_lower:
            return q
    return (
        "Evaluate this region for image quality: noise level, sharpness, "
        "artifacts, and overall appearance. Be specific about what you observe."
    )


def _render_score_bars_html(scores: dict, aggregate: float, summary: str, concerns: list) -> str:
    """Render structured AI scores as HTML score bars."""
    dim_labels = {
        "sharpness": "Sharpness", "noise": "Noise", "naturalness": "Naturalness",
        "artifact_level": "Artifacts", "background_quality": "Background", "star_quality": "Stars",
    }
    bars = ""
    for dim, label in dim_labels.items():
        v = scores.get(dim, 0)
        pct = v / 10 * 100
        color = "#4ade80" if v >= 7 else "#fbbf24" if v >= 4 else "#f87171"
        bars += (
            f'<div class="sc-row">'
            f'<span class="sc-label">{label}</span>'
            f'<div class="sc-bar"><div class="sc-fill" style="width:{pct:.0f}%;background:{color}"></div></div>'
            f'<span class="sc-val">{v:.1f}</span>'
            f'</div>'
        )
    concern_html = ""
    if concerns:
        items = "".join(f"<li>{c}</li>" for c in concerns)
        concern_html = f'<ul class="sc-concerns">{items}</ul>'
    return (
        f'<div class="score-result">'
        f'<div class="sc-aggregate">Overall <b>{aggregate:.1f}</b>/10</div>'
        f'<div class="sc-bars">{bars}</div>'
        f'<div class="sc-summary">{summary}</div>'
        f'{concern_html}'
        f'</div>'
    )


def crop_analysis_page(target: str, filename: str, crops: list[dict]) -> str:
    from nas_server.story import _page_shell
    from nas_server.database import get_crop_analyses

    img_url = f"/image/{target}/{filename}"
    back_url = f"/target/{target}"

    # Infer target_type from target name for default weights
    tl = target.lower()
    if any(k in tl for k in ["m51", "m31", "m81", "m101", "ngc", "galaxy"]):
        target_type = "galaxy"
    elif any(k in tl for k in ["m42", "m8", "m20", "ic", "sh2", "nebula"]):
        target_type = "nebula"
    else:
        target_type = "default"

    regions_html = ""
    for c in crops:
        cid = c["id"]
        name = c["name"]
        preview_url = f"/crops/preview/{cid}"

        cp = crop_preview_path(cid)
        physics: dict = {}
        physics_str = ""
        physics_html = ""
        if cp.exists():
            physics = measure_crop_physics(cp)
            physics_str = format_physics(physics)
            if physics_str:
                chip_parts = []
                if "bg_rms" in physics:
                    chip_parts.append(f'<span class="pc">bg_noise <b>{physics["bg_rms"]:.1f}</b></span>')
                if "snr_est" in physics:
                    chip_parts.append(f'<span class="pc">SNR <b>{physics["snr_est"]}</b></span>')
                if "gradient_rms" in physics:
                    chip_parts.append(f'<span class="pc">gradient <b>{physics["gradient_rms"]:.1f}</b></span>')
                if "clipping_pct" in physics:
                    chip_parts.append(f'<span class="pc">clip <b>{physics["clipping_pct"]}%</b></span>')
                if "star_count" in physics:
                    chip_parts.append(f'<span class="pc">stars <b>{physics["star_count"]}</b></span>')
                if "fwhm_median" in physics:
                    chip_parts.append(f'<span class="pc">FWHM <b>{physics["fwhm_median"]}px</b></span>')
                if "ecc_median" in physics:
                    chip_parts.append(f'<span class="pc">ecc <b>{physics["ecc_median"]}</b></span>')
                physics_html = f'<div class="physics-chips">{"".join(chip_parts)}</div>'

        # Load most recent stored analysis if any
        stored = get_crop_analyses(cid)
        stored_html = ""
        if stored:
            latest = stored[0]
            stored_html = _render_score_bars_html(
                latest["scores"], latest["aggregate_score"] or 0,
                latest["summary"] or "", latest["concerns"],
            )

        regions_html += f"""
<div class="region-card" id="rc-{cid}">
  <img class="region-thumb" src="{preview_url}" alt="{name}"
       onerror="this.style.display='none'">
  <div class="region-info">
    <div class="region-name">{name}</div>
    <div class="region-meta">{c['created_at'][:16]}
      &nbsp;·&nbsp; {int(c['w'])}×{int(c['h'])}px
    </div>
    {physics_html}
    <div class="region-analysis" id="ra-{cid}">{stored_html}</div>
  </div>
  <div class="region-btns">
    <button class="btn-analyze" data-id="{cid}"
            data-name="{name}"
            data-target="{target}"
            data-target-type="{target_type}">Analyze</button>
    <button class="btn-delete" data-id="{cid}">✕</button>
  </div>
</div>"""

    if not regions_html:
        regions_html = '<p class="no-regions">No saved regions yet — draw a box and save one above.</p>'

    body = f"""
<div class="ca-wrap">
  <div class="ca-header">
    <div>
      <a href="{back_url}" class="ca-back">← {target}</a>
      <h2 style="margin:.25rem 0 0">{filename}</h2>
    </div>
    <span style="color:var(--text2);font-size:.82rem">
      Draw a box, name it, and save to analyze specific image regions with Claude.
    </span>
  </div>

  <!-- Cropper -->
  <div class="ca-editor">
    <div class="ca-img-wrap">
      <img id="ca-img" src="{img_url}" alt="{filename}" crossorigin="anonymous">
    </div>

    <div class="ca-controls">
      <div class="rot-row">
        <span style="font-size:.82rem;color:var(--text2)">Rotation</span>
        <input type="range" id="rot-slider" min="-180" max="180" step="0.5" value="0"
               style="flex:1;accent-color:var(--accent)">
        <span id="rot-label" style="min-width:44px;text-align:right;font-size:.82rem;color:var(--accent)">0.0°</span>
        <button class="btn-sm" id="rot-reset">Reset</button>
      </div>
      <div style="display:flex;gap:.5rem;flex-wrap:wrap">
        <button class="btn-sm" id="flip-h">Flip H</button>
        <button class="btn-sm" id="flip-v">Flip V</button>
        <button class="btn-sm" id="reset-crop">Reset</button>
      </div>
      <div class="save-row">
        <input type="text" id="region-name" placeholder="Region name (e.g. star_field, core, background)"
               list="region-suggestions">
        <datalist id="region-suggestions">
          <option value="star_field">
          <option value="core">
          <option value="background">
          <option value="nebula">
          <option value="galaxy">
        </datalist>
        <button class="btn-primary" id="save-btn">Save region</button>
        <span id="save-status" style="font-size:.82rem;color:#fbbf24;display:none">Saving…</span>
      </div>
    </div>
  </div>

  <!-- Saved regions -->
  <div class="ca-regions">
    <h3 style="font-size:.9rem;color:var(--text2);text-transform:uppercase;letter-spacing:.07em;margin-bottom:.75rem">
      Saved regions
    </h3>
    <div id="regions-list">{regions_html}</div>
  </div>
</div>

<!-- Cropper.js -->
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/cropperjs@1.6.2/dist/cropper.min.css">
<script src="https://cdn.jsdelivr.net/npm/cropperjs@1.6.2/dist/cropper.min.js"></script>

<script>
(function() {{
  var img = document.getElementById('ca-img');
  var slider = document.getElementById('rot-slider');
  var rotLabel = document.getElementById('rot-label');
  var saveBtn = document.getElementById('save-btn');
  var saveStatus = document.getElementById('save-status');
  var nameInput = document.getElementById('region-name');
  var scaleX = 1, scaleY = 1;
  var cropper;

  function initCropper() {{
    cropper = new Cropper(img, {{
      viewMode: 1, dragMode: 'move', autoCropArea: 0.4,
      rotatable: true, scalable: true, zoomable: true,
      guides: true, center: true, background: true,
      responsive: true, checkOrientation: false,
    }});
  }}
  if (img.complete && img.naturalWidth > 0) initCropper();
  else img.addEventListener('load', initCropper);

  slider.addEventListener('input', function() {{
    var a = parseFloat(this.value);
    rotLabel.textContent = a.toFixed(1) + '°';
    if (cropper) cropper.rotateTo(a);
  }});
  document.getElementById('rot-reset').addEventListener('click', function() {{
    slider.value = 0; rotLabel.textContent = '0.0°';
    if (cropper) cropper.rotateTo(0);
  }});
  document.getElementById('flip-h').addEventListener('click', function() {{ scaleX=-scaleX; if(cropper) cropper.scaleX(scaleX); }});
  document.getElementById('flip-v').addEventListener('click', function() {{ scaleY=-scaleY; if(cropper) cropper.scaleY(scaleY); }});
  document.getElementById('reset-crop').addEventListener('click', function() {{
    if(cropper) cropper.reset();
    slider.value=0; rotLabel.textContent='0.0°'; scaleX=1; scaleY=1;
  }});

  saveBtn.addEventListener('click', function() {{
    if (!cropper) return;
    var name = nameInput.value.trim();
    if (!name) {{ nameInput.focus(); return; }}
    var d = cropper.getData(false);
    saveBtn.disabled = true;
    saveStatus.style.display = 'inline';
    fetch(window.location.pathname + '/save', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{
        name: name,
        x: d.x, y: d.y, w: d.width, h: d.height,
        natural_w: img.naturalWidth, natural_h: img.naturalHeight,
      }})
    }})
    .then(function(r) {{ return r.json(); }})
    .then(function(resp) {{
      if (resp.ok) location.reload();
      else {{ saveStatus.textContent = 'Error: ' + (resp.error || 'unknown'); }}
    }})
    .catch(function(e) {{ saveStatus.textContent = 'Error: ' + e; }})
    .finally(function() {{ saveBtn.disabled = false; }});
  }});

  // Analyze buttons
  function renderScores(d) {{
    if (d.error) return '<span style="color:#f87171">Error: ' + d.error + '</span>';
    var dims = [['sharpness','Sharpness'],['noise','Noise'],['naturalness','Naturalness'],
                ['artifact_level','Artifacts'],['background_quality','Background'],['star_quality','Stars']];
    var bars = dims.map(function(dm) {{
      var v = (d.scores || {{}})[dm[0]] || 0;
      var pct = (v/10*100).toFixed(0);
      var col = v>=7?'#4ade80':v>=4?'#fbbf24':'#f87171';
      return '<div class="sc-row"><span class="sc-label">'+dm[1]+'</span>'+
             '<div class="sc-bar"><div class="sc-fill" style="width:'+pct+'%;background:'+col+'"></div></div>'+
             '<span class="sc-val">'+v.toFixed(1)+'</span></div>';
    }}).join('');
    var concerns = (d.concerns||[]).map(function(c){{return '<li>'+c+'</li>';}}).join('');
    return '<div class="score-result">'+
      '<div class="sc-aggregate">Overall <b>'+(d.aggregate||0).toFixed(1)+'</b>/10</div>'+
      '<div class="sc-bars">'+bars+'</div>'+
      '<div class="sc-summary">'+(d.summary||'')+'</div>'+
      (concerns?'<ul class="sc-concerns">'+concerns+'</ul>':'')+
      '</div>';
  }}

  document.querySelectorAll('.btn-analyze').forEach(function(btn) {{
    btn.addEventListener('click', function() {{
      var id = this.dataset.id;
      var name = this.dataset.name;
      var tgt = this.dataset.target || '';
      var tgtType = this.dataset.targetType || 'default';
      var resultEl = document.getElementById('ra-' + id);
      btn.disabled = true;
      btn.textContent = 'Analyzing…';
      resultEl.innerHTML = '<span style="color:var(--text2)">⏳ Sending to Claude…</span>';
      fetch('/analyze-crop', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{crop_id: parseInt(id), crop_name: name,
                               target: tgt, target_type: tgtType}})
      }})
      .then(function(r) {{ return r.json(); }})
      .then(function(d) {{
        resultEl.innerHTML = renderScores(d);
        btn.textContent = 'Re-analyze';
        btn.disabled = false;
      }})
      .catch(function(e) {{
        resultEl.innerHTML = '<span style="color:#f87171">Error: ' + e + '</span>';
        btn.textContent = 'Analyze';
        btn.disabled = false;
      }});
    }});
  }});

  // Delete buttons
  document.querySelectorAll('.btn-delete').forEach(function(btn) {{
    btn.addEventListener('click', function() {{
      var id = this.dataset.id;
      if (!confirm('Delete this region?')) return;
      fetch('/crops/' + id, {{method: 'DELETE'}})
        .then(function() {{ document.getElementById('rc-' + id).remove(); }});
    }});
  }});
}})();
</script>"""

    css = """
  .ca-wrap { max-width: 1100px; margin: 0 auto; padding: 1.5rem 1rem 3rem; display: flex; flex-direction: column; gap: 1.5rem; }
  .ca-header { display: flex; justify-content: space-between; align-items: flex-start; flex-wrap: wrap; gap: .5rem; }
  .ca-back { font-size: .82rem; color: var(--text2); }
  .ca-editor { display: flex; gap: 1rem; align-items: flex-start; flex-wrap: wrap; }
  .ca-img-wrap { flex: 1; min-width: 300px; max-height: 60vh; overflow: hidden; background: #000; border-radius: 8px; border: 1px solid var(--border); }
  .ca-img-wrap img { display: block; max-width: 100%; }
  .ca-controls { width: 280px; flex-shrink: 0; display: flex; flex-direction: column; gap: .75rem; background: var(--bg2); border: 1px solid var(--border); border-radius: 8px; padding: 1rem; }
  .rot-row { display: flex; align-items: center; gap: .5rem; flex-wrap: wrap; }
  .save-row { display: flex; flex-direction: column; gap: .5rem; }
  .save-row input { background: var(--bg3); border: 1px solid var(--border); border-radius: 6px; color: var(--text); padding: .45rem .65rem; font-size: .85rem; width: 100%; }
  .save-row input:focus { outline: none; border-color: var(--accent); }
  .btn-primary { background: var(--accent); color: #000; border: none; border-radius: 6px; padding: .5rem 1.2rem; font-weight: 600; font-size: .9rem; cursor: pointer; }
  .btn-primary:disabled { opacity: .5; cursor: not-allowed; }
  .btn-sm { background: var(--bg3); color: var(--text); border: 1px solid var(--border); border-radius: 5px; padding: .35rem .7rem; font-size: .82rem; cursor: pointer; white-space: nowrap; }
  .ca-regions h3 { margin-top: 0; }
  .region-card { display: flex; align-items: flex-start; gap: .75rem; background: var(--bg2); border: 1px solid var(--border); border-radius: 8px; padding: .75rem; margin-bottom: .6rem; }
  .region-thumb { width: 80px; height: 60px; object-fit: cover; border-radius: 4px; border: 1px solid var(--border); flex-shrink: 0; }
  .region-info { flex: 1; min-width: 0; }
  .region-name { font-weight: 600; font-size: .9rem; margin-bottom: .15rem; }
  .region-meta { font-size: .75rem; color: var(--text2); margin-bottom: .35rem; }
  .region-analysis { font-size: .83rem; color: var(--text); margin-top: .35rem; }
  .score-result { background: var(--bg3); border-radius: 6px; padding: .6rem .75rem; border-left: 2px solid var(--accent); }
  .sc-aggregate { font-size: .82rem; color: var(--text2); margin-bottom: .4rem; }
  .sc-aggregate b { color: var(--accent); font-size: 1rem; }
  .sc-bars { display: flex; flex-direction: column; gap: .25rem; margin-bottom: .4rem; }
  .sc-row { display: flex; align-items: center; gap: .4rem; }
  .sc-label { font-size: .72rem; color: var(--text2); width: 76px; flex-shrink: 0; }
  .sc-bar { flex: 1; height: 6px; background: var(--bg2); border-radius: 3px; overflow: hidden; }
  .sc-fill { height: 100%; border-radius: 3px; transition: width .3s; }
  .sc-val { font-size: .72rem; width: 28px; text-align: right; color: var(--text); }
  .sc-summary { font-size: .78rem; color: var(--text2); line-height: 1.4; margin-top: .25rem; }
  .sc-concerns { margin: .3rem 0 0 1rem; padding: 0; font-size: .75rem; color: #f87171; }
  .sc-concerns li { margin-bottom: .1rem; }
  .region-btns { display: flex; flex-direction: column; gap: .4rem; flex-shrink: 0; }
  .btn-analyze { background: #1f6feb33; border: 1px solid #1f6feb; color: var(--accent); border-radius: 5px; padding: .3rem .7rem; font-size: .78rem; cursor: pointer; white-space: nowrap; }
  .btn-analyze:hover { background: #1f6feb55; }
  .btn-analyze:disabled { opacity: .5; cursor: not-allowed; }
  .btn-delete { background: none; border: 1px solid var(--border); color: var(--text2); border-radius: 5px; padding: .3rem .55rem; font-size: .82rem; cursor: pointer; }
  .btn-delete:hover { border-color: #f87171; color: #f87171; }
  .no-regions { color: var(--text2); font-size: .88rem; font-style: italic; }
  .physics-chips { display: flex; flex-wrap: wrap; gap: .25rem; margin-bottom: .3rem; }
  .pc { font-size: .72rem; background: var(--bg3); border: 1px solid var(--border); border-radius: 4px; padding: 1px 6px; color: var(--text2); }
  .pc b { color: var(--text); font-weight: 600; }
  @media (max-width: 700px) { .ca-editor { flex-direction: column; } .ca-controls { width: 100%; } }
"""
    return _page_shell(f"Crop Analysis — {target}", body, css)
