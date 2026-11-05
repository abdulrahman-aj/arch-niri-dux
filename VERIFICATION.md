# Verification

## Session PATH regression (2026-11-02)

Issue #23 was reproduced on the accepted desktop image. After applying the
login-profile and user-service PATH defaults to a disposable VM overlay and
rebooting, Bash and an actual Niri spawn resolved the local test executable.
Evidence is in the issue worktree's `out/verify-session-path/`: `before-fix.txt`,
`session-path.json`, `SESSION-PATH-PASSED` and `serial.log`. This tested the overlay
on an existing installation; no physical laptop configuration was changed.

The follow-up removes optional fish configuration and keeps two focused PATH
preservation tests plus the normal acceptance suite's Niri launch check.
`make test` passed 43 source tests and nine build tests.

## Bloom, PDF and camera defaults (2026-11-02)

Image: `arch-niri-dux-2026.11.02-x86_64.iso`

```
SHA-256 72c08107c4e45fc02238487c5053b24c3b0e6714aa9686f8ba8cfe8456b1eb57
```

The image contains 626 signature-checked distribution packages and the same
four reviewed Dux builds. Papers and Snapshot add 21 packages to the verified
Plymouth image without changing its pins or signing keys.

`make test` passed 41 source tests and nine build tests. `make iso` and
`make verify ARGS='--directory out/verify-desktop-qualified'` passed all 20
acceptance stages on a fresh offline installation. Independent Muse review
approved the code with real VM qualification required before merging.

The real first-session state selects bloom; screenshot review confirms its
render. Running the real desktop configuration for a disposable user with an
existing orbit choice preserved the state byte for byte. `xdg-open` routed the
sample PDF to Papers, whose screenshot shows the expected document text.

Snapshot received a synthetic PipeWire video source, requested camera permission,
and captured a photo and a three-second video through its own shutter shortcut.
The screenshot shows the live moving-ball preview; the JPEG contains the ball.
The WebM decoded successfully with FFmpeg as VP8, 640x480, three seconds. No
physical camera or microphone was accessed. Physical camera compatibility remains
unqualified. The full run also passed Dux integration, themes, file dialogs,
screen sharing, lock, package snapshots, recovery, normal reboot, text unlock
fallback and restored graphical unlock.

Local evidence: `out/verify-desktop-qualified/ACCEPTANCE-PASSED`, `inputs.json`,
`milestones.json`, `wallpaper.json`, `wallpaper-preserved.json`, `pdf.json`,
`camera.json`, `camera-photo.jpeg`, `camera-video.webm`, `02-wallpaper-bloom.png`,
`02-papers.png`, `02-snapshot-captured.png` and serial logs. The earlier failed
camera run exposed a missing permission-dialog interaction; the fresh run
includes the visible Grant Access step and all capture checks.

## Graphical disk unlock (2026-11-02)

Image: `arch-niri-dux-2026.11.02-x86_64.iso`

```
SHA-256 0f4ff7110ed3473be26239aaaab50375eac119e73e0f2034989c9955d4c8a83c
```

The rebuilt image adds signed Plymouth 26.134.222-3 to the previous 604-package
closure, retaining all existing pins and the four reviewed Dux builds. It uses
the stock bgrt password box, with text fallback and the existing recovery path.

`make test` passed 40 source tests and nine build tests. `make iso` and
`make verify ARGS='--directory out/verify-plymouth-qualified'` passed all 16
acceptance stages. The GUI check boots without a serial kernel console, uses a
CPU-readable framebuffer, waits for the visible password prompt and types an
incorrect password followed by the correct one. Screenshots confirm the box,
the forced `plymouth.enable=0` console prompt, and the graphical prompt after
restoring the setting. Desktop, screen capture, lock, update snapshots,
snapshot restoration and normal reboot also passed. Independent Muse review
covered the boot changes; its acceptance findings were addressed before this run.

`make verify-hibernate ARGS='--directory out/verify-plymouth-qualified'` passed:
the kernel boot ID and RAM-backed nonce survived encrypted hibernation/resume.
Local evidence includes `ACCEPTANCE-PASSED`, `milestones.json`, the
`02-graphical-unlock-*`, `06-text-fallback-*` and `07-restored-splash-*` PNGs,
`HIBERNATE-RESUME-PASSED` and `hibernate-resume.json` in that directory.
Laptop-specific BGRT/backlight behavior has not been tested physically.

## Fastfetch rebuild (2026-11-02)

Image: `arch-niri-dux-2026.11.02-x86_64.iso`

```
SHA-256 ccaa911077c6d5214a2743b3dfdd7d175b6e3ecf99ced2f8f8451e2043a9aa32
```

This rebuild adds Fastfetch 2.70.0-1 to the live ISO and installed system, plus
its required yyjson 0.13.0-1 library. All previous package pins are unchanged.
The manifest contains 604 signature-checked distribution packages and the same
four reviewed, locally built Dux packages; both additions verify with the
existing trusted keyring.

`make iso` and `make verify ARGS='--directory out/verify-fastfetch'` passed.
The VM executed `fastfetch --logo none --structure OS:Kernel` successfully on
the live USB image and as the installed desktop user, without a network device.
All 12 acceptance stages passed, including snapshot restoration and the normal
reboot afterward. `make test` passed 42 source tests and nine build tests;
independent Muse review approved the package and probe changes.

Local evidence: `out/verify-fastfetch/ACCEPTANCE-PASSED`, `milestones.json`,
`inputs.json`, `serial.log`, `out/packages.manifest.json` and `out/SHA256SUMS`.
The earlier hibernation run below was not repeated for this package-only change.

## Original release image (before Fastfetch)

Release: `arch-niri-dux-2026.11.02-x86_64.iso`

```
SHA-256 6db9b19744a947b3309cb0be58decec5d88b4868f34d570ef99171d23e91247c
```

The image contains 602 signature-checked distribution packages and four locally
built Dux packages from reviewed, pinned AUR recipes. The output manifest records
both. `make test` passed 42 source tests and nine build tests; independent Muse
review covered installer safety, package preparation and acceptance probes.

## Offline VM

`make iso` and `make verify ARGS='--directory out/verify-release'` passed using
the default persistent build cache. The VM uses UEFI, USB boot, KVM, virtio 3D
and no network device. Installation took 66 seconds with another VM running.

All 12 acceptance stages passed: encrypted installation and boot; graphical
login; active encrypted swap and its physical resume offset; Dux GOP, Search,
Calendar and Files; live light/dark propagation to Chromium, GTK, portals and
Dux apps; Dux file dialogs and cancellation; nonblank browser screen capture;
lock/unlock; automatic package snapshots; snapshot boot/restore and normal reboot
with the newer home-directory data preserved. Recovery entries use `noresume`;
restored normal boots retain the correct swap offset without `noresume`.

`make verify-hibernate ARGS='--directory out/verify-release'` also passed. The
kernel boot ID and a RAM-backed nonce survived actual hibernation and resume
through the encrypted swapfile. The temporary test-only 9p share is unmounted
before hibernation.

Evidence: `out/verify-release/ACCEPTANCE-PASSED`, `milestones.json`, `inputs.json`,
`screen-sharing.json`, `HIBERNATE-RESUME-PASSED`, `hibernate-resume.json` and PNGs.
These local outputs are not committed or uploaded.

## Disabled swap

The disabled-swap branch also passed an actual offline installation with
`--swap-gib 0 --install-only`, verifying no swapfile/resume arguments and disabled
hibernation. Install-only runs do not receive a full acceptance marker.

## Laptop checks

Collected Zenbook logs show successful lid-close suspend cycles, with wakeups
coinciding with lid opening. The intermittent reported symptom has not yet been
captured. The VM does not qualify the laptop's wake sources or display behavior;
those checks remain open. Secure Boot remains outside scope.

On 2026-11-02, an SSH audit of the ASUS Zenbook 14 UX3405MA running
`7.2.14-arch1-1` found an existing hardware hibernate/resume cycle in the current
boot's journal: systemd started hibernation at 10:52:44 and returned at 10:53:53
(Asia/Amman). The kernel recorded S4 entry, waking from S4 and hibernation exit.
The active 17 GiB swapfile resides in the encrypted Btrfs `@swap` subvolume;
the running kernel's resume device and offset match its boot arguments.
However, the kernel also reported a `cs35l41` speaker-amplifier resume timeout
(`-110`), followed by firmware-initialization errors. The user confirmed that
sound works through the built-in speakers after resume. This closes the laptop
hibernate/resume TODO; the driver warning remains recorded here in case audio
problems recur. This does not establish that every device resumed correctly.

The live audit also confirmed active greetd, Dux Shell/Search/Files and GNOME/GTK
portals; installed Dux GOP, Greeter and Calendar; Dux Files as the folder default;
`dfiles;gtk` file-picker preference; and repository order `core`, `extra`,
`omarchy`. Only the internal display was connected. No sleep or hibernate action
was triggered by this audit.

## TODO audit (2026-11-02)

Removed eight completed TODOs based on the implementation merged in PR #12,
its automated source tests and the verification recorded above: GNOME screen
sharing; guided installer polish;
Cancel-first erase confirmation; elapsed installation time; graphical Dux
Greeter; system light/dark propagation; the signed Omarchy repository; and Dux
GOP, Search, Calendar and Files integration.

The repository reminder's separate dotfiles change is also merged: commit
`4c56ac8` (dotfiles PR #3) installs Chrome, Zen and T3 Code from configured binary
repositories on plain Arch, without the automatic AUR installation path.

This audit reran `make test` in the Nix dev shell: 42 source tests and nine build
tests passed. The earlier VM evidence directories are absent from this checkout;
the VM results above are the committed release record, not new runs from this
audit. The subsequent SSH hardware evidence is recorded under Laptop checks.
The hibernation TODO was subsequently removed after the hardware journal checks
and the user's confirmation of working speaker audio. Nine completed items have
now been removed in total; lid/display behavior remains open separately.
The VM installer uses unattended configuration; interactive wizard behavior and
custom swap sizing are covered by source tests rather than an interactive VM run.

## Verified alongside installation (2026-11-02)

The implementation at `73139365d6c8ce44dd0dcfd27f77b624447b6935` includes main `0320799` (Plymouth, app/defaults,
session PATH and fish-scope cleanup). Both fresh scenarios used
`out/arch-niri-dux-2026.11.02-x86_64.iso`, SHA256:

```
591fc771dadeaaf5306cf1db00127dc422ff033b325873667eafdf6f2fb27bd9
```

`make test` passed **70 source tests and 10 build tests**. The final ISO was built
with the normal shared cache and flock. Both real-OS scenarios passed all six
acceptance milestones:

- Original OS boots and its sentinel is intact.
- Every original partition's whole-device SHA256 is unchanged by installation.
- Firmware defaults to Limine and boots the new encrypted Arch installation.
- Reinstalling the pinned kernel exercises package/snapshot hooks and retains
  the original OS menu entry.
- Updated encrypted Arch boots again.
- Limine chainloads the original OS after the update and its sentinel remains.

The original installations were official Debian 13 amd64 (124 MiB ESP) and
Windows 11 Enterprise LTSC Evaluation 24H2, build 26100 (100 MiB ESP). Windows
was deployed using official WinPE/DISM and BCDBoot and reported its real running
OS through CIM before and after installation; no EFI stub qualifies the run.
Windows' disk-ID bytes 440–443 remain `67b98660` (little-endian `0x6086b967`).
An additional post-boot audit found the original 446 MBR-prefix bytes and every
occupied original GPT entry unchanged in both scenarios, including attributes.
The original firmware entries were retained and the new entry was active.

Machine-readable results, exact media/fixture/OVMF/ISO hashes, implementation
file hashes and original-partition hashes are committed in
[verification/alongside-2026-11-02.json](verification/alongside-2026-11-02.json).
The runner checked the in-ISO `install.py` and `configure-target.sh` hashes
against this implementation before installation. The final documentation commit
does not change those tested implementation bytes.

Full local evidence remains in:

- `out/alongside/windows-final2/`: SUCCESS, six milestones, before/after Windows
  CIM reports, screenshots, original-partition hashes, firmware listings,
  menu after the kernel update, raw GPT prefixes and serial/QEMU logs.
- `out/alongside/debian-final2/`: SUCCESS and the equivalent Linux evidence.
- `out/alongside/final-unit-tests.log`: final source/build results.
- `out/alongside/writer-check/writer-results.json`: real SATA-block comparison
  showing sgdisk clearing the Windows signature and sfdisk retaining it.

Only obsolete overlays explicitly approved for reclamation were removed; their
logs, reports, hashes and SUCCESS markers remain. The VMs used a 9 GiB stop
threshold to protect the required 8 GiB host reserve. No physical laptop writes,
reboots, key changes or Secure Boot changes were performed. The VM layouts used
512-byte logical sectors; no additional 4K-sector VM claim is made.

### Review findings and disposition

1. Real-OS evidence gap: closed by the two fresh six-stage runs above.
2. Initial hook registration: all initial package/kernel transactions use
   `SKIP_UEFI=yes`; it is removed only after successful final registration.
   A failed/unexpected-firmware path retains deferral.
3. EFI registration safety: require the new binary before registration; verify
   the new BootXXXX active flag, partition number, ESP GUID and loader path
   before setting BootOrder and verify again afterward. Missing/inactive/wrong
   identity cases have regression tests.
4. GPT/MBR preservation: append with util-linux sfdisk and both wiping modes
   disabled; verify with sgdisk, both raw GPT CRCs, occupied original entries,
   original MBR prefix and kernel geometry before formatting. Tests include
   nonzero Windows bytes 440–443, GPT attributes, arbitrary partition data,
   changed extents/tables, raw-entry padding and corruption.
5. Firmware precondition: check original entries/order before appending the
   chainload configuration. An unexpected change aborts without enabling
   registration or making an inactive new entry default.
6. EBUSY disposition: remove the redundant full kernel reread; wait for udev
   and require exact kernel partition starts/sizes. No blind recovery or table
   restoration is attempted. Both final runs exercise this path successfully.

The builder also waits for Archiso's keyring initializer before its own key
operations, with a regression for the initialization barrier and failure path.
Independent Muse review of `8fb640d` found no code blockers and all six earlier
findings resolved. Main's subsequent fish cleanup was merged normally. Root
ran the combined standalone acceptance on the same ISO: all 21 stages passed,
including the shared PATH fix, apps, graphical/text unlock, screen sharing and
snapshot restoration. Evidence is in `out/verify-combined-final/` and the committed
JSON record. The completed alongside TODO is removed.

To repeat only these alongside runs using the retained fixtures, run from this
worktree with fresh output directories:

```sh
make test
make verify-alongside ARGS="--os windows --iso out/arch-niri-dux-2026.11.02-x86_64.iso --fixture out/alongside/windows4/disk.qcow2 --firmware out/alongside/windows4/OVMF_VARS.fd --windows-token out/alongside/windows4/report-token --directory out/alongside/windows-repeat"
make verify-alongside ARGS="--os linux --iso out/arch-niri-dux-2026.11.02-x86_64.iso --fixture out/alongside/linux-fixture/disk.qcow2 --firmware out/alongside/linux-fixture/OVMF_VARS.fd --directory out/alongside/debian-repeat"
```

The combined acceptance passed from this worktree, with matching installer
source hashes; repeat it using a fresh evidence directory:

```sh
make verify ARGS="--iso out/arch-niri-dux-2026.11.02-x86_64.iso --directory out/verify-combined-final"
```

## Default terminal resolution (2026-11-03)

`make test` passed 72 source and 10 build tests. The two resolver regressions
also passed against the exact signed Arch `xdg-terminal-exec 0.14.3-2` package
in the disposable VM. All 626 existing package pins remain unchanged.

Targeted verification applied the production desktop overlay to an existing
accepted installation: fresh terminal preference seeding, actual Niri-launched
Alacritty and command payload, systemd user environment, recovery `-e` invocation,
existing-choice preservation, desktop smoke and native Niri validation passed.
This qualifies the package/overlay delta; no ISO was rebuilt or full install,
hibernation or alongside acceptance repeated. Independent Muse review found no
blocking defects. Source hashes and check scope are recorded in
`verification/default-terminal-2026-11-03.json`.

## Fresh-install baseline (2026-11-04)

The final ISO is 2,643,089,408 bytes (2.46 GiB). All 627 existing binary pins
are preserved; the 62 added payloads passed hash and signature checks. The
source/build suites passed 76 and 10 tests respectively. Independent Muse
reviews of the baseline and Celluloid replacement found no outstanding blockers.

A complete uninterrupted `make verify` run on a fresh offline UEFI VM passed
all 26 stages and wrote `ACCEPTANCE-PASSED`. This includes real image opening,
text editing/saving, audio output, four Celluloid open/play/close cycles, CLI
operations, virtual driverless printing through Dux, synthetic camera capture,
portals, snapshot recovery, and graphical/text disk unlock.

Celluloid is the owner-approved video default; Decibels remains the audio app.
The original Showtime candidate intermittently blocked in `gst_play_get_volume`,
matching the signature in [Fedora bug 2509505](https://bugzilla.redhat.com/show_bug.cgi?id=2509505).
The relevant historical thread stacks remain in
`verification/showtime-deadlock-2026-11-04.txt`; no workaround for that app ships.

Physical device access, LAN discovery and paper output remain hardware checks;
the laptop was untouched. Results, source/artifact hashes and limits are recorded
in `verification/fresh-install-baseline-2026-11-04.json`. Local run evidence is in
`out/verify-celluloid-final/`.

## Packaged integration foundation — 2026-11-04

`make test` passed 79 source and 10 build tests. A fresh ISO built the owning
`arch-niri-dux` package and separate full-baseline metapackage with makepkg;
the offline VM passed all 26 acceptance milestones. Pacman owns shared defaults,
helpers and vendor units; user initialization runs unprivileged and preserves
existing settings. Greetd retains its upstream config while a vendor service
drop-in selects our separately owned, backup-designated `dux.toml`.

An offline test release upgraded both packages from `0.1.0-1` to `0.1.0-2`,
added a baseline dependency, added/removed owned files and preserved personal
configuration. Removing the baseline left integration installed. Snapshot
restoration recovered the earlier owned files and package database, removed the
new fixture package and preserved home. The normal boot and both unlock paths
passed again afterward.

Sol implemented the refactor; Muse and GLM reviewed YAGNI/correctness. Production
signing/repository delivery remains a separate implementation gate; this local
upgrade fixture is deliberately unsigned. No prototype-ISO migration or laptop
changes were performed. Hashes and results are in
`verification/packaged-integration-2026-11-04.json`; local evidence is in
`out/verify-packaged-integration-qualified/`.

## Signed integration updates — 2026-11-04

All 85 source and 10 build tests passed, including real pacman install/upgrade
against a signed temporary repository, source/artifact tampering, historical
subkey expiry/replacement, encrypted recovery and signing with the CI-only
subkey. Workflow syntax is checked with actionlint.

A fresh offline ISO using a disposable release identity passed all 26 VM
milestones. The installer populated trusted release keys and enabled the signed
repository; the upgrade fixture rejected a tampered database before changing
packages, then upgraded both packages and a new baseline dependency with required
package/database signatures. The installed keyring-refresh hook ran. Personal
configuration, baseline opt-out and snapshot recovery passed again.

This fixture does not claim production GitHub/Pages delivery. Its exact inputs,
identity and results are in `verification/signed-updates-2026-11-04.json`;
local evidence is in `out/verify-signed-fixture/`.

### Published release qualification

CI signed and published `integration-v0.1.0-2` to GitHub Releases and Pages.
`make iso` fetched those exact authenticated artifacts. The release ISO is
2,643,109,888 bytes (2.46 GiB), SHA256
`54f0fb68484cb675087ecdfe784a4dbf14d19437fc63b2d49f88e9b32d3afc9b`.

All 27 stages of `make verify-release` were covered. The initial runner stopped
after 25 stages without writing a failure marker; the final graphical and HTTPS
checks resumed on the same disk with identical verification scripts. The new
online check downloaded the real signed database and both packages using only
the ISO-provisioned trust. Package hashes matched the signed release input;
personal config stayed unchanged and the package database remained consistent.

Actionlint, 86 source tests and 10 build tests passed. Independent GLM review
approved the release-delivery probe. Source/artifact hashes, CI/release URLs,
production identity, all milestones and the continuation are recorded in
`verification/published-integration-2026-11-04.json`. Local evidence is in
`out/verify-published-release/`. No laptop changes were made.

## ISO payload ownership — 2026-11-04

The payload finalizer now owns inventory validation, the final offline database,
combined manifest and checksums. Actionlint, 100 source tests and 10 build tests
passed. Fourteen finalizer tests exercise real package archives, `repo-add` and
the installer's checksum consumer, including zero-AUR assembly and rejection of
missing, unexpected, duplicate, corrupted and inconsistent package inputs.
Independent Muse review found no blockers.

`make iso` succeeded with the same 695 package files and a byte-identical
combined manifest. The image is 2,641,993,728 bytes (2.46 GiB), SHA256
`1642f02d59c83e73f26de313aa43caf389ed6d5bca6e3ac76dfa7a4c176372ea`.
The fresh offline VM passed manifest equality, payload checksums, dependency
preflight, encrypted installation and guest preparation. This was an
`--install-only` run; the desktop/recovery suite was not repeated. Package
contents and the signed integration release are unchanged. Exact inputs and
limits are in `verification/iso-payload-2026-11-04.json`; local diagnostics are
in `out/verify-iso-payload/`. No laptop changes were made.

## Dux 2.1 clean baseline — 2026-11-05

Shell, GOP, Greeter, Search, Calendar and Files are all 2.1.0. The lock retains
687 existing signed pins unchanged, updates Shell/GOP and adds four PDF-preview
packages. All six new/replaced signed payloads verified with existing keys;
the four unmodified AUR recipes match their upstream release asset digests.
Actionlint, 105 source tests and 10 build tests passed. Independent Muse review
found no outstanding source blockers after the GTK startup race was fixed and
regression-tested.

The development ISO is 2,742,853,632 bytes (2.55 GiB), SHA256
`19b23401dc7305ab6857460ca7e50349145daf3a6ebda9c6f3fc92a48c879c3d`.
A fresh offline VM passed all 27 acceptance milestones without continuation.
This includes the native generated Niri bindings and inherited Alt+F4, live
GTK/Chromium appearance changes, fresh-account initialization and preservation,
real Dux Files PDF rendering, Papers remaining the PDF default, media, synthetic
camera capture, file dialogs, screen sharing, locking, signed integration
upgrade/baseline opt-out, snapshot restoration and both disk-unlock paths.

A disposable overlay loaded the seven actual dotfiles from commit
`193cd7f7e3c7cefb476f86221cb093ea0ac76f6a` as file symlinks. Native settings and
shortcut writes changed the owning Git files without replacing their links;
changes survived a Dux restart. Real keypresses confirmed inherited Alt+F4 and
personal Super+W close windows, while Super+Q remains a no-op. Island styling,
keyboard/idle settings and lock privacy/power settings loaded without a base
`settings.json`. This did not run the full personal bootstrap or edit dotfiles.

This is an unpublished `0.2.0-1` development build. Qualification of exact
CI-signed 0.2.0 artifacts and production HTTPS delivery requires a later approved
merge/release. No automatic 2.0 home-config migration is supported. The laptop
was untouched; physical device, printer and LAN tests are not claimed. Exact
inputs, results and limits are in `verification/dux-2.1-2026-11-05.json`; local
evidence is in `out/verify-dux21-accepted/`.

## Dead-code cleanup and lint gate — 2026-11-05

`make test` passed Ruff, ShellCheck, actionlint, 106 source tests and 10 build
tests. A package-output regression test executes the real packaging functions:
generated defaults and user seeds remain, build-only originals are not installed,
and the standard license copy is preserved. Both real makepkg outputs built as
`0.2.0-2`; their file inventories lose exactly the three redundant paths.

An offline disposable overlay of the accepted 2.1 disk installed both candidate
packages and validated Niri with the trimmed payload. The signed upgrade fixture
then exercised owned-file deletion, baseline additions/opt-out and personal-file
preservation. Snapshot boot, restoration, the normal reboot and restored Niri
validation passed. The fixture now uses a private package cache because old
disposable signatures survive root restoration in `@cache`.

Independent Muse review found no blockers. No ISO rebuild, full fresh-install
rerun, production publication or laptop changes were performed. Exact package
hashes, probe hashes and limits are in
`verification/dead-code-cleanup-2026-11-05.json`; local evidence is in
`out/verify-dead-code-cleanup-qualified/`.

Lint discovery follow-up: Python is checked repository-wide; shell sources and
first-party recipes are discovered from tracked and untracked files at the Git
root. Five real-tool tests cover new paths, exclusions, recipe context and
subdirectory invocation. Ruff, ShellCheck, actionlint, 111 source tests and
10 build tests passed. Muse and GLM Flash independently reviewed the change;
their root/exclusion edge cases were fixed with regression coverage. Real package
builds still emit the same inventory and all 28 installed file contents as the
VM-qualified package. The evidence JSON records the new recipe/artifact hashes;
no further VM or ISO run was needed for this lint-only follow-up.
