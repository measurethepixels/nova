# WSL2 reference-port guide

The pipeline already runs in production under WSL2 — this is the **supported
Windows path**. Native Windows is not supported (systemd, headless PixInsight
automation, and `siril-cli` all assume a POSIX host); run Ubuntu inside WSL2
instead and everything in [REPLICATE.md](REPLICATE.md) applies unchanged from
there. This guide covers only what's different about that host: prerequisites,
install steps, where WSL2 behaves unlike native Linux, and the failures that
are specific to it.

## Prerequisites

- Windows 10 (build 19041+) or Windows 11, with virtualization enabled in
  firmware (required for WSL2's lightweight VM).
- Admin access to run `wsl --install` and edit Windows Firewall rules if the
  service needs to be reachable from other machines on the LAN.
- An NVIDIA GPU if you want GPU acceleration for GraXpert or the RC-Astro
  tools — WSL2 CUDA passthrough works for both; nothing in the pipeline
  *requires* a GPU.

## Install steps

### 1. Install Ubuntu 22.04 under WSL2

In an elevated PowerShell:

```powershell
wsl --install Ubuntu-22.04
# restart if prompted, then set a Linux username/password at first launch
```

### 2. Configure WSL2 resources

Create `C:\Users\<you>\.wslconfig`:

```ini
[wsl2]
memory=12GB
processors=8
swap=8GB
networkingMode=mirrored
```

Size `memory`/`processors` to what you can spare from the Windows host —
stacking and PixInsight are the memory-heavy steps. `networkingMode=mirrored`
lets the pipeline's web UI and any remote-worker ports be reached at the
Windows host's own LAN IP instead of a NAT-translated WSL2-internal address;
skip it if you only need loopback access. **`networkingMode` is a `[wsl2]`
setting, not `[experimental]`, and mirrored networking requires Windows 11
22H2 or later** — on Windows 10, omit this line (WSL2 falls back to its
default NAT networking) and use Windows port-forwarding
(`netsh interface portproxy`) if LAN reachability is needed instead. Apply
with:

```powershell
wsl --shutdown
wsl -d Ubuntu-22.04
```

### 3. Enable systemd inside the WSL2 distro

`nas_server/deploy/seestar.service` is a systemd unit. WSL2 needs systemd
turned on explicitly — it's off by default. In `/etc/wsl.conf` inside Ubuntu:

```ini
[boot]
systemd=true
```

Then `wsl --shutdown` from PowerShell and relaunch the distro. Confirm with
`systemctl status` — it should show a running instance, not "System has not
been booted with systemd."

### 4. Install packages and set up storage

Ubuntu 22.04's default apt repos ship **Python 3.10**, not 3.12 — `apt install
python3.12` fails with `Unable to locate package` until you add a repo that
carries it. The [deadsnakes PPA](https://launchpad.net/~deadsnakes/+archive/ubuntu/ppa)
is the standard supported source for newer CPython builds on Ubuntu LTS
releases:

```bash
sudo apt update && sudo apt install -y software-properties-common
sudo add-apt-repository -y ppa:deadsnakes/ppa
sudo apt update && sudo apt install -y \
  cifs-utils python3.12 python3.12-venv python3-pip git
```

`cifs-utils` is only needed if your capture storage arrives over SMB, as in
the reference deployment; adjust to whatever mount mechanism your storage
actually uses.

**Do not `apt install siril`** — Ubuntu 22.04's distro package is well below
the version this pipeline requires, the same problem REPLICATE.md documents
for Ubuntu 24.04 (whose newer package installed 1.2.1, itself too old).
Install a current official Siril **1.4.3 or newer** build instead and put
its `siril-cli` first on `PATH`; confirm with `siril-cli --version` before
stacking, exactly as REPLICATE.md's "Capability matrix" section requires —
do not treat a successful old-package install as a supported capability.

Follow REPLICATE.md's environment-discovery checklist for everything else
(ASTAP, GraXpert, PixInsight, Seti Astro Suite Pro) — none of that differs
from native Linux.

**Filesystem placement matters for performance:** clone the repo and do all
stacking/scratch work inside the Linux filesystem (`~/...`, i.e. the WSL2 ext4
volume), never under `/mnt/c/...`. Reads/writes crossing into the Windows
filesystem through WSL2's 9P interop layer are dramatically slower and will
bottleneck registration/integration.

### 5. Adapt and install the `seestar.service` unit

Create the venv and install dependencies per REPLICATE.md §7 (`python3.12 -m
venv`, `pip install -r requirements.txt`) — nothing WSL2-specific there. Then,
**the shipped unit is not installable as-is**: `nas_server/deploy/seestar.service`
hard-codes the reference deployment's own `User=`, `WorkingDirectory=`, and
`ExecStart=` venv path — all absolute paths under one specific account's home
directory. Before `systemctl enable`-ing it, open the file and replace those
three values with your own WSL2 username and the paths where you actually
cloned the repo and created the venv — this is the same `__REPO_ROOT__`/
`__VENV__`/`__DATA_DIR__`/`__USER__` substitution REPLICATE.md §5 requires of
any port; the exported public copy of the unit carries those placeholders
literally, this repo's private copy carries the reference deployment's real
values directly, so here you're editing literal strings rather than filling
in placeholders. Then:

```bash
sudo cp nas_server/deploy/seestar.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now seestar
systemctl status seestar   # expect "active (running)"
```

### 6. PixInsight in WSL2 (only if you're using the paid tier)

PixInsight is optional and commercial. This guide makes no claim about
activation counts or cross-platform licensing terms — install and activate
it in WSL2 according to your own PixInsight license, the same way you would
on any other machine; check with Pleiades Astrophoto directly if you're
unsure what your license permits. Headless PI needs several X11 libraries
even though nothing is displayed:

```bash
sudo apt install -y libxcb1 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 \
  libxcb-render-util0 libxkbcommon-x11-0
```

Activate PI by running it once interactively with a display (e.g. via
`wsl.exe` X11 passthrough or a VNC session) before relying on headless
invocations. Verify with:

```bash
PixInsight --no-gui --force-exit -r=/dev/null
echo $?   # expect 0
```

## Known differences from native Linux

- **Networking** defaults to NAT — a service bound in WSL2 is not reachable
  from other LAN machines until you either forward the port or set
  `networkingMode=mirrored` (see step 2). Loopback access from Windows itself
  works either way.
- **systemd is opt-in** (step 3) — without it, `nas_server/deploy/seestar.service`
  has nothing to run under; use a manual `uvicorn` invocation or a Windows
  Task Scheduler entry that launches `wsl.exe -- bash -c "..."` as a
  workaround if you'd rather not enable systemd.
- **Python 3.12 isn't in Ubuntu 22.04's default apt repos** (step 4) — add
  the deadsnakes PPA first, same as on bare-metal Jammy.
- **The shipped `seestar.service` unit hard-codes the reference deployment's
  user/paths** (step 5) — edit it for your own username and clone location
  before enabling; this is unrelated to WSL2 specifically, but it blocks
  every fresh port, WSL2 included, so it's called out here rather than left
  implicit in REPLICATE.md §5.
- **Filesystem crossing is slow** — see step 4. This is the single biggest
  performance difference from bare-metal Linux; get scratch/library paths
  onto the Linux side.
- **GPU passthrough** works for CUDA (GraXpert, RC-Astro) but only needs the
  compatible NVIDIA driver installed on the **Windows** host — do not install
  a Linux NVIDIA display driver inside the WSL2 distro itself; the CUDA
  toolkit/libraries inside the distro use the Windows-side driver through
  passthrough.
- **WSL2 restarts drop mounts** — any SMB/network mount needs an `/etc/fstab`
  entry with `_netdev,nofail` so it survives `wsl --shutdown` / relaunch
  cycles, since WSL2 doesn't persist runtime mount state across a full
  distro restart.

## Troubleshooting

**Worker/service not reachable from other machines on the LAN**
- Confirm `networkingMode=mirrored` is set and WSL2 was restarted after
  editing `.wslconfig`.
- Check Windows Firewall allows the relevant inbound port.
- Confirm the IP you're using is the Windows host's LAN IP (mirrored mode
  shares it) — `ip addr` inside WSL2 or `ipconfig` in Windows.

**Network mount not present after a restart**
- Run `sudo mount -a` inside WSL2 to reapply `/etc/fstab`.
- Confirm the fstab entry has `_netdev,nofail` so a boot before the network
  is up doesn't hang or drop the entry.

**PixInsight headless exits nonzero or crashes**
- Run `PixInsight --no-gui --force-exit -r=/dev/null` and check the exit
  code directly — a nonzero code with no other output is almost always a
  missing X11 library (step 6).
- Confirm PI has been activated by running it once interactively before the
  first headless invocation.

**Process killed unexpectedly (OOM)**
- Increase `memory=` in `.wslconfig` and restart WSL2 (`wsl --shutdown`).
- Check current usage from inside the distro with `free -h` while a stack or
  process job is running.

## Verification

Once installed, run REPLICATE.md's verification protocol (Section 7) from
inside the WSL2 distro exactly as you would on native Linux — nothing about
the pass/fail criteria changes for this host; only the steps above (systemd,
filesystem placement, X11 libs, networking mode) are WSL2-specific setup.
