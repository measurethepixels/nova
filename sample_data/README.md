# NOVA public verification sample

This directory contains twelve real 10-second SeeStar S50 light frames of M42.
They are deliberately a small **functional verification set**, not enough
integration time for a showcase result.

The files were renamed and their location-bearing FITS header cards were
removed through a science-metadata allowlist. `manifest.json` records a checksum
for every sanitized file and a hash
of each pixel array. The preparation tool verifies that the pixel arrays are
unchanged by sanitization and never records the private source paths or removed
values.

## Release status

- Data licence: **CC BY 4.0**, approved by capture owner Jeff Henry. See
  `LICENSE.md` for the attribution text.
- Golden metrics: one-machine baseline still needs to be generated.
- Reference profile: the `public_free_core` workflow with no aesthetic API, so
  the final score uses NOVA's deterministic physics fallback. This workflow
  explicitly pins free/headless variants rather than attempting unavailable
  paid tools and relying on fallbacks.
- Tolerances: cannot be called machine-variance tolerances until a second,
  genuinely different clean machine has run the protocol.

Do not tag this package as a verified release while the golden evidence remains
pending. The files are staged here so the mechanics and first baseline can be
tested without turning an unfinished evidence package into a reproducibility
claim.

`validation/owner_vm_stack.json` records the initial viability check: Siril
plate-solved, registered, and stacked all 12 frames. That evidence qualifies the
capture selection; it does not qualify the end-to-end release.
