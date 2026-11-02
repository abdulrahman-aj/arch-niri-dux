# Verification

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

## Online setup

The separate online run completed the real personal bootstrap, including binary
Chrome/Zen/T3 packages, Nix, agent configuration and Fish. Yay system updates and
an explicit greeter rebuild/reinstall completed. Live Zen/T3 appearance and the
post-setup reboot passed, including T3's rendered dark class, Fish login,
graphical greeter login and Dux service startup. The test completes Zen's normal
first-run wizard without changing its default Auto appearance choice.

Evidence is under `out/personal-binaries/`: `PERSONAL-PASSED`, `sources.json`,
`bootstrap.log`, `personal-appearance.json` and a screenshot. The run used
dotfiles commit `78d029a` and agent-config `d9dadbb`. Its base image has the same
package manifest as the release; the subsequent rebuild changed build storage
and timestamps. `--resume-desktop` resumed the completed bootstrap's GUI checks;
`BOOTSTRAP-RECOVERY.txt` records its original successful console completion.

The disabled-swap branch also passed an actual offline installation with
`--swap-gib 0 --install-only`, verifying no swapfile/resume arguments and disabled
hibernation. Install-only runs do not receive a full acceptance marker.

## Laptop checks

Collected Zenbook logs show successful lid-close suspend cycles, with wakeups
coinciding with lid opening. The intermittent reported symptom has not yet been
captured. The VM does not qualify the laptop's wake sources, display or hardware
hibernate/resume. Those checks remain open; Secure Boot remains outside scope.
