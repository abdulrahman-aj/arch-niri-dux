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

## Fresh-install baseline (2026-11-04, blocked on video)

The candidate ISO is 2,640,097,280 bytes (2.46 GiB). All 627 existing binary
pins are preserved; the 58 added payloads passed hash and signature checks.
The source/build suites passed 76 and 10 tests respectively. Independent Muse
review completed; its MIME-alias and NSS-verification findings were addressed.

Fresh offline installation and resumed acceptance completed 25 non-video stages,
including real image opening, text editing/saving, audio output, CLI operations,
virtual driverless printing, synthetic camera capture, portals, snapshot recovery
and graphical/text disk unlock. The verifier's window-input fix was reproduced
with Chromium open. This was a resumed run, not a successful uninterrupted
`make verify`; no `ACCEPTANCE-PASSED` marker was created.

Showtime intermittently hangs before mapping a window. Its captured main thread
blocks in `gst_play_get_volume`, matching the signature in
[Fedora bug 2509505](https://bugzilla.redhat.com/show_bug.cgi?id=2509505).
No packaged fix was found; selecting a different video app requires the owner's
decision. PR #35 stays draft. Physical device access, LAN discovery and paper
output remain hardware checks; the laptop was untouched.

Results, source/artifact hashes and limits are recorded in
`verification/fresh-install-baseline-2026-11-04.json`; the relevant thread stacks
are in `verification/showtime-deadlock-2026-11-04.txt`.
