# Acknowledgments

NOVA doesn't do its own stacking, plate-solving, gradient removal, or most of
its pixel-level processing. It orchestrates a small set of excellent
astrophotography tools built by other people, and measures the results. None
of the following projects are part of NOVA, and each is distributed and
licensed entirely on its own terms — you install and accept the license for
each one yourself. This file exists because they deserve real credit, not
just a line in a requirements table.

With one noted exception (a redistributed PixInsight script, see the
PixInsight section below), none of these projects' source is included in
this repository.

## Siril

The stacking and calibration engine NOVA drives headlessly via `siril-cli`.

- **License:** GNU General Public License v3.0
- **Source:** [gitlab.com/free-astro/siril](https://gitlab.com/free-astro/siril)
- **How NOVA uses it:** subprocess calls to the `siril-cli` binary with generated
  `.ssf` scripts — the same command-line interface Siril documents for any
  external caller.

## ASTAP

Plate-solving, by Han Kleijn.

- **License:** Mozilla Public License 2.0 (source at
  [github.com/han-k59/astap](https://github.com/han-k59/astap)); the bundled
  `AstroSimple` component is CC BY 4.0
- **Distribution:** [www.hnsky.org](https://www.hnsky.org)
- **How NOVA uses it:** subprocess calls to `astap_cli`.

## GraXpert

AI-based background gradient extraction.

- **License:** GNU General Public License v3.0
- **Source:** [github.com/Steffenhir/GraXpert](https://github.com/Steffenhir/GraXpert)
- **How NOVA uses it:** subprocess calls to the standalone `GraXpert` binary.

## PixInsight

Optional — used for the PJSR-scripted processing path (`seti_astro.py`'s
PJSR generation), plate-solving fallback, and calibration. Requires a
separately purchased license from Pleiades Astrophoto; not required for
NOVA's free core.

- **License:** proprietary, commercial EULA — see
  [pixinsight.com/license/pixinsight/EULA_en_US.html](https://pixinsight.com/license/pixinsight/EULA_en_US.html)
- **How NOVA uses it:** most PJSR (JavaScript) scripts NOVA runs through
  PixInsight's own scripting engine are NOVA's own — the documented,
  sanctioned mechanism PixInsight provides for building external tools. NOVA
  does not modify, reverse-engineer, or redistribute the PixInsight
  application itself.
- **Redistributed PixInsight script (exception):** `nas_server/imageSolver_headless.js`
  is a modified copy of PixInsight's own standard `ImageSolver.js` script
  (with its `ImageSolverDialog` UI class stripped so it runs in headless
  automation mode); a companion catalogs file,
  `nas_server/astro_catalogs_headless.jsh` (a similarly stripped copy of
  `AstronomicalCatalogs.jsh`), is tracked in this repository but not part of
  the public export. Both originals are Copyright (C) 2012-2024 Andrés del
  Pozo and Copyright (C) 2019-2024 Juan Conejero (PixInsight PTeam),
  distributed by Pleiades Astrophoto under a permissive BSD-2-Clause-style
  redistribution license embedded in each file's header (redistribution in
  source or binary form is permitted provided the copyright notice and
  disclaimer are retained; the license text is reproduced verbatim at the
  top of both files). NOVA's copies retain that notice unmodified.

## Seti Astro Suite Pro (SASpro)

Optional, recommended free core — provides the ImageMM stacking engine,
statistical/GHS stretches, narrowband tools (SSSC, NBExtract), star removal,
denoise, and several other operations NOVA's `seti_astro.py` calls directly.

- **License:** GNU General Public License v3.0
- **Source:** [github.com/setiastro/setiastrosuitepro](https://github.com/setiastro/setiastrosuitepro)
- **Distribution model:** donationware — free to use, with an optional
  suggested donation to the author
- **How NOVA uses it:** `pip install setiastrosuitepro`, then direct Python
  calls into its published modules (e.g. `setiastro.saspro.imageops`,
  `setiastro.saspro.abe`). This is a closer integration than the
  subprocess-based tools above — SASpro is published on PyPI specifically to
  be installed and imported as a Python package. NOVA never bundles,
  vendors, or redistributes any SASpro source; every environment that uses
  it installs the package independently and directly from its author.

## A note on integration style

Three of these tools (Siril, ASTAP, GraXpert) are driven as external
processes over their own CLI/script interfaces. Most of NOVA's PixInsight
integration is driven through its own documented scripting engine using
NOVA-authored scripts, with one exception noted above (a redistributed,
headless-adapted copy of PixInsight's own `ImageSolver.js`, under its own
permissive terms). SASpro is used as an installed Python library, imported
directly. Each tool is a separate, independently obtained, independently
licensed piece of software; aside from that one noted PixInsight script,
nothing here vendors or redistributes another project's source code.

If you maintain one of these projects and think anything above should be
described differently, please open an issue.

---

Real credit, in plain terms: NOVA's actual pixel-level work — the stacking
math, the plate solving, the gradient models, the stretch algorithms — is
these tools' work. NOVA's job is choosing when to use them, measuring what
they did, and being honest about the result. That split only works because
these projects exist and are this good. Thank you.
