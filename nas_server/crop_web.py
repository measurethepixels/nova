"""
Manual crop editor page for the review system.

Served at GET /review/{id}/crop — uses Cropper.js for interactive crop+rotation.
Result posted to POST /review/{id}/apply-crop, which writes winner.fit and decides the review.
"""


def crop_editor_page(review_id: int, r: dict) -> str:
    from nas_server.story import _page_shell

    target = r.get("target", "")
    step   = r.get("step", "")

    css = """
.crop-wrap {
  max-width: 1100px; margin: 2rem auto; padding: 0 1rem;
  display: flex; flex-direction: column; gap: 1.25rem;
}
.crop-header { display: flex; flex-wrap: wrap; gap: .5rem; align-items: baseline; }
.crop-container {
  max-height: 65vh; overflow: hidden; background: #000;
  border-radius: 8px; border: 1px solid var(--border);
}
.crop-container img { display: block; max-width: 100%; }
.crop-controls {
  background: var(--bg2); border: 1px solid var(--border);
  border-radius: 8px; padding: 1rem; display: flex;
  flex-direction: column; gap: .75rem;
}
.rot-row { display: flex; align-items: center; gap: .75rem; }
.rot-row input[type=range] { flex: 1; accent-color: var(--accent); }
.rot-badge {
  min-width: 52px; text-align: right; font-size: .88rem;
  color: var(--accent); font-variant-numeric: tabular-nums;
}
.crop-actions { display: flex; flex-wrap: wrap; gap: .75rem; align-items: center; }
.btn-primary {
  background: var(--accent); color: #000; border: none;
  border-radius: 6px; padding: .5rem 1.4rem; font-weight: 600;
  font-size: .95rem; cursor: pointer;
}
.btn-primary:disabled { opacity: .5; cursor: not-allowed; }
.btn-secondary {
  background: var(--bg3); color: var(--text); border: 1px solid var(--border);
  border-radius: 6px; padding: .45rem 1rem; font-size: .88rem; cursor: pointer;
}
.crop-note { font-size: .8rem; color: var(--text2); }
#crop-status { font-size: .88rem; color: #fbbf24; display: none; }
"""

    body = f"""
<div class="crop-wrap">
  <div class="crop-header">
    <h2 style="margin:0">{target} / {step} — manual crop</h2>
    <a href="/review/{review_id}"
       style="font-size:.85rem;color:var(--text2);text-decoration:none;margin-left:auto">
      ← back to review
    </a>
  </div>

  <div class="crop-container">
    <img id="source-img" src="/review/{review_id}/source-image"
         alt="Source image" crossorigin="anonymous">
  </div>

  <div class="crop-controls">
    <div style="font-size:.85rem;color:var(--text2)">
      Drag to move crop box &nbsp;·&nbsp; Drag corner to resize &nbsp;·&nbsp;
      Scroll to zoom &nbsp;·&nbsp; Use slider to rotate
    </div>
    <div class="rot-row">
      <span style="font-size:.85rem;color:var(--text2);white-space:nowrap">Rotation</span>
      <input type="range" id="rot-slider" min="-180" max="180" step="0.5" value="0">
      <span class="rot-badge" id="rot-label">0.0°</span>
      <button class="btn-secondary" id="rot-reset" style="white-space:nowrap">Reset</button>
    </div>
    <div class="rot-row" style="gap:.5rem">
      <button class="btn-secondary" id="flip-h">Flip H</button>
      <button class="btn-secondary" id="flip-v">Flip V</button>
      <button class="btn-secondary" id="reset-crop">Reset crop</button>
    </div>
  </div>

  <div class="crop-actions">
    <button class="btn-primary" id="apply-btn">Apply crop</button>
    <span id="crop-status">Processing… this may take 10–30s for large images</span>
    <span class="crop-note">
      Applying writes the cropped FITS as the winner and resolves the review.
    </span>
  </div>

  <form id="crop-form" method="post" action="/review/{review_id}/apply-crop"
        style="display:none">
    <input type="hidden" name="x"             id="inp-x">
    <input type="hidden" name="y"             id="inp-y">
    <input type="hidden" name="width"         id="inp-w">
    <input type="hidden" name="height"        id="inp-h">
    <input type="hidden" name="rotate"        id="inp-r">
    <input type="hidden" name="natural_width" id="inp-nw">
    <input type="hidden" name="natural_height" id="inp-nh">
  </form>
</div>

<!-- Cropper.js -->
<link rel="stylesheet"
      href="https://cdn.jsdelivr.net/npm/cropperjs@1.6.2/dist/cropper.min.css">
<script src="https://cdn.jsdelivr.net/npm/cropperjs@1.6.2/dist/cropper.min.js"></script>

<script>
(function() {{
  var img    = document.getElementById('source-img');
  var slider = document.getElementById('rot-slider');
  var label  = document.getElementById('rot-label');
  var cropper;
  var scaleX = 1, scaleY = 1;
  var currentAngle = 0;

  function initCropper() {{
    cropper = new Cropper(img, {{
      viewMode: 1,
      dragMode: 'move',
      autoCropArea: 0.85,
      rotatable: true,
      scalable: true,
      zoomable: true,
      guides: true,
      center: true,
      highlight: true,
      background: true,
      responsive: true,
      checkOrientation: false,
    }});
  }}

  if (img.complete && img.naturalWidth > 0) {{
    initCropper();
  }} else {{
    img.addEventListener('load', initCropper);
  }}

  // Rotation slider
  slider.addEventListener('input', function() {{
    currentAngle = parseFloat(this.value);
    label.textContent = currentAngle.toFixed(1) + '°';
    if (cropper) cropper.rotateTo(currentAngle);
  }});

  document.getElementById('rot-reset').addEventListener('click', function() {{
    slider.value = 0;
    currentAngle = 0;
    label.textContent = '0.0°';
    if (cropper) cropper.rotateTo(0);
  }});

  document.getElementById('flip-h').addEventListener('click', function() {{
    scaleX = -scaleX;
    if (cropper) cropper.scaleX(scaleX);
  }});

  document.getElementById('flip-v').addEventListener('click', function() {{
    scaleY = -scaleY;
    if (cropper) cropper.scaleY(scaleY);
  }});

  document.getElementById('reset-crop').addEventListener('click', function() {{
    if (cropper) cropper.reset();
    slider.value = 0;
    currentAngle = 0;
    label.textContent = '0.0°';
    scaleX = 1; scaleY = 1;
  }});

  document.getElementById('apply-btn').addEventListener('click', function() {{
    if (!cropper) return;
    var data = cropper.getData(false);
    document.getElementById('inp-x').value  = data.x;
    document.getElementById('inp-y').value  = data.y;
    document.getElementById('inp-w').value  = data.width;
    document.getElementById('inp-h').value  = data.height;
    document.getElementById('inp-r').value  = data.rotate;
    document.getElementById('inp-nw').value = img.naturalWidth;
    document.getElementById('inp-nh').value = img.naturalHeight;

    // Show spinner, disable button, submit
    this.disabled = true;
    document.getElementById('crop-status').style.display = 'inline';
    document.getElementById('crop-form').submit();
  }});
}})();
</script>"""

    return _page_shell(f"Manual crop — {target}/{step}", body, css)
