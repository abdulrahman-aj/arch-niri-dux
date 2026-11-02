# Verification

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
