# NOVA — Autonomous Processing Engine

**Measure the Pixels** · *Building an AI Astrophotographer* — the real, production
code behind the channel: a system that stacks,
processes, grades, and plans astrophotography captures on its own — every step
measured from pixel statistics, guarded against artifacts, and versioned like the
software it is.

This preliminary source snapshot was last refreshed on **2026-10-21**. Development
continues separately; newer features shown in project episodes may not be included
here. This documentation update does not refresh the code snapshot.

> [!IMPORTANT]
> **Preliminary public preview.** NOVA is under active development, and the public
> replication package is still being formalized. The production project is real,
> but the clean-install documentation and configuration contract are not yet
> complete enough for a supported installation. The sample frames are included
> under the approved CC BY 4.0 licence recorded in `sample_data/manifest.json`;
> golden metrics and clean-machine verification remain pending.
> You are welcome to explore the code and follow its progress. A verified tagged
> release and filmed AI-assisted installation walkthrough are planned.

**There is no installer — on purpose.** Every deployment is a port to your OS,
storage, and toolset. Open this repo with an AI coding agent (Claude Code, Codex,
Gemini CLI, …) and say: *"Read REPLICATE.md and set this up for my system."* The
contract in that file tells the agent how to discover your environment, what it may
adapt, and which checks are currently possible.

This export contains a small real-capture sample and its verification tooling,
but it cannot prove an end-to-end result until the pending fields in
`sample_data/README.md` are resolved and a clean-machine pass is recorded. Until
then, treat this as source-preview documentation rather than a validated quickstart.

Free-tool core: Siril + ASTAP + GraXpert + Seti Astro Suite Pro (pip). Optional
paid tier: PixInsight + RC-Astro plugins. See the capability matrix in REPLICATE.md.
Every one of those tools is a separate, independently licensed project doing
the actual pixel-level work — see [ACKNOWLEDGMENTS.md](ACKNOWLEDGMENTS.md) for
real credit and license details.

### Development news — not included in this snapshot

Current development can stop with an explicit scientific refusal and retain
evidence for a Studio manual-review handoff; completion, grading and acceptance
are separate states. In the First Light development story, Jeff sets up the S50
while NOVA tests attended control and measured autofocus. Acquisition remains
experimental. Recent work also strengthens background-model evidence retention
and bounded handling of transient network-storage reads. These updates describe
newer development, not capabilities bundled in this source snapshot, and do not
establish universal unattended operation or a newly validated public release.

Support boundary: shared as-is alongside the channel; issues/PRs welcome, responses
not guaranteed — hobby project, fixed time budget.

**License:** NOVA source code is licensed under the [MIT License](LICENSE). Website
content, videos, graphics, astrophotography, and other original materials are
© 2026 Jeff unless otherwise noted. Measure the Pixels™ is a brand used by
Jeff.

*Measure the pixels. Make the call.*
