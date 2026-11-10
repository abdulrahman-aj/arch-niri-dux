# Verification

## Targets

`make lint` runs Ruff, ShellCheck and actionlint in the Nix dev shell. It
discovers Python sources, shell files by extension or shebang, and our
PKGBUILDs, including untracked scripts. Generated output and vendored Dux app
recipes are excluded. `make test` adds source and build tests. CI runs these
checks without graphical VM acceptance.

`make iso-development` builds candidate integration packages and the ISO. Run
VMs on your build machine. Set `AGENT_HEAVY_JOBS=deny` on machines that
should only run light targets. Builds and VM disk creation require 30 GiB
free; `make gc ARGS=--dry-run` lists orphaned checkout storage. The runner waits for 10 GiB available host RAM before
starting an 8 GiB guest. VM tests require KVM and a GPU render node with
virtio 3D acceleration; `QEMU_RENDER_NODE` defaults to `/dev/dri/renderD128`.

## `make verify`

Full acceptance qualifies a fresh offline encrypted UEFI install on a
disposable 48 GiB VM disk. The workspace (default `out/verify/dux-verify-*`
on disk, overridable with `--directory`) records `install.json`, `inputs.json` with the ISO,
package-manifest and verification-script hashes, `OVMF_VARS.fd`, serial and
QEMU logs, screenshots and a milestone list. Only a full run writes
`ACCEPTANCE-PASSED`. A failed run deletes its disk and keeps the rest; a passed
run keeps its ~6 GB disk as a fixture, so delete workspaces you no longer need.

It boots the ISO with loopback as the only network interface. It confirms the
package manifest and installer payload hashes, then installs with
`arch-niri-install`. It qualifies graphical disk unlock with a
wrong-passphrase retry and UEFI boot without the ISO. It checks package presets with
systemd policy and the PAM-unlocked keyring, the inbound firewall and
resolve-only mDNS seen from a veth neighbour namespace, and Niri/Dux/terminal/editor/browser
smoke checks. It checks desktop apps (manuals, archives, media playback, driverless
printing to a loopback printer), PDF rendering, the synthetic camera, screen
sharing, and screen lock. It checks migration ordering with retries and session triggers,
an update started through Dux's updater (its pacman backend, `pkexec` and
authentication dialog) running system and user migrations,
an integration upgrade with Limine snapshot boot and restore preserving home,
and the `plymouth.enable=0` text fallback with graphical unlock restored
afterward. `make verify-release` additionally checks published HTTPS package
delivery against offline trust and exact release hashes. `--install-only` is
a debug run and does not produce acceptance success. Full acceptance is
required for installer changes and releases.

Suspend qualification requires logind `LockedHint=yes`, Niri's lock event, an unchanged boot ID and kernel S3 entry/exit. Post-resume QEMU GL/input and shutdown can stall, so screenshots stay best-effort and this stage does not qualify password unlock. The probe syncs the guest before the host restarts it.

## `make verify-fast`

`make verify-fast ARGS="--fixture out/accepted/disk.qcow2 --directory out/fast"`
builds current integration packages inside a disposable overlay of a completed
`make verify` image. Use an accepted 0.3+ fixture with a fresh `--directory`. Keep `ACCEPTANCE-PASSED`,
`install.json`, `inputs.json` and `OVMF_VARS.fd` next to the fixture disk. It checks ALPM
regeneration of the active Limine initramfs and boot menu without a manual
rebuild, Niri validation, Dux shortcut editing/cheatsheet, enabled units and
presets, greetd login, unlocked GNOME Keyring, loaded vendor systemd drop-ins
and migration behavior. It does not replace fresh installation or snapshot
and alongside qualification. Pass `--kernel-package PATH` to reinstall a
local kernel archive in the same transaction and exercise both boot rebuild
hooks.

## Debugging loop (kept verify-fast VM)

Rule: QML/probe-only changes never rerun full `make verify`; full verify is
final qualification. Iterate on a kept VM, then qualify once.

`make verify-fast ARGS="--fixture out/accepted/disk.qcow2 --directory out/debug --keep"`
writes `FAST-PASSED` as usual, then keeps the VM running with its serial/QMP
sockets under `out/debug/.sockets` and prints paste-ready attach commands
(the same block prints if the run fails, when the live guest is most useful).
Without `--keep`, behavior is unchanged. No new make target: `--keep` passes
through the existing `verify-fast` `ARGS=` convention.

Drive the kept VM with `scripts/vm_console.py` (one driver at a time):

```
./scripts/build-in-dev python3 scripts/vm_console.py --directory out/debug sh '<command>'
./scripts/build-in-dev python3 scripts/vm_console.py --directory out/debug shot <name.png>
./scripts/build-in-dev python3 scripts/vm_console.py --directory out/debug click <x_frac> <y_frac>
./scripts/build-in-dev python3 scripts/vm_console.py --directory out/debug key <qcode>
./scripts/build-in-dev python3 scripts/vm_console.py --directory out/debug probe <module> <function>
./scripts/build-in-dev python3 scripts/vm_console.py --directory out/debug poweroff
```

Per iteration there is no ISO rebuild, reinstall or scheduler wait. The
evidence directory is mounted in the guest at `/run/verify`, so copy a changed
file into `out/debug/` on the host, then e.g.
`sh 'cp /run/verify/ReleaseWidget.qml /etc/xdg/quickshell/dms-plugins/archNiriDuxRelease/ReleaseWidget.qml'`.
For multi-line guest work, write a small script into `out/debug/` on the host
and run `sh 'sh /run/verify/x.sh'` to avoid nested quoting. Commands needing
the tester user's environment are best run as probes (`probe <module>
<function>`), which get the full environment from `guest.user_command`.
`shot <name.png>` takes a real desktop screenshot via the guest.
The verbs assume a logged-in guest; on a failure before login, rerun rather
than attach.

A kept VM holds 8 GiB guest RAM; power it off before builds or other VMs and
remove the directory when finished.

## `make verify-hibernate`

`make verify-hibernate` checks encrypted swap hibernate and resume on a
completed `make verify` workspace; the ISO hash must be unchanged and the
qcow2 must carry its pristine snapshot. It records the kernel boot ID and a
RAM-backed token, hibernates through systemd, unlocks the resumed image and
requires the same boot ID and token afterward, proving genuine memory resume
rather than a fresh boot. It writes `hibernate-resume.json` and only a full
pass writes `HIBERNATE-RESUME-PASSED`.

## `make verify-alongside`

`make verify-alongside` qualifies installation into existing unallocated GPT
space against real Debian 13 and Windows 11 LTSC fixtures provisioned per
[ALONGSIDE.md](ALONGSIDE.md#automated-provisioning-and-acceptance). It boots
the existing OS first and hashes every byte of every original partition while
offline, installs into the selected region, then confirms the old hashes
still verify, the new encrypted Arch installation boots by default, a pinned
kernel reinstall exercises the actual package and snapshot hooks while
preserving the existing OS entry, the updated system reboots, and the original
OS chainloads through Limine by entry path with its sentinel intact. Only a
full run writes `SUCCESS`. Run Debian first, then Windows, sequentially.

## Accepted software paths

- [Package-owned policy](https://github.com/abdulrahman-aj/arch-niri-dux/pull/58)
  passed the fast tier for a policy-only upgrade and a combined kernel
  transaction. ALPM regenerated the active initramfs and Limine menu; reboot,
  greetd/keyring, Niri/Dux and loaded-policy checks passed.
- [Signed Dux apps](https://github.com/abdulrahman-aj/arch-niri-dux/pull/59)
  passed all 28 fresh offline acceptance stages and the fast tier. All four
  apps came from our signed repository; guest probes confirmed yay was absent
  and the pinned release key enforced `Required TrustedOnly`.
- [Migrations](https://github.com/abdulrahman-aj/arch-niri-dux/pull/61)
  passed full acceptance on the package-policy base and fast qualification
  after the signed-app rebase, plus source and build tests. Fixtures covered
  skipped-version upgrades, ordered retry, failure notification and recovery,
  active-session and next-session activation, fresh-account baselines, custom
  files and symlinks, and system-record rollback with surviving user records.
  No production migrations ship.
- [Alongside](https://github.com/abdulrahman-aj/arch-niri-dux/pull/19) passed
  all six stages for Debian 13 and Windows 11 Enterprise LTSC Evaluation,
  build 26100. Every original partition byte survived installation, the new
  Arch system booted by default, kernel/snapshot hooks retained the old-OS
  entry, and both original systems booted through Limine with their
  sentinels.

These records qualify the commits and artifacts they name. They do not
qualify later signed release bytes or hardware behavior.

## Known limits and hardware gaps

- Secure Boot remains [deferred](https://github.com/abdulrahman-aj/arch-niri-dux/issues/49).
  Alongside fixtures used 512-byte sectors; 4K-sector layouts are unqualified.
- Physical fingerprint enrollment, monitor DDC, drive-health readings, Wi-Fi
  hotspots, USB device access, LAN printer discovery and paper output need
  hardware checks.
- Check undocked lid closes within 30 seconds of boot and resume, both unlocked
  and already locked: the panel must go dark immediately, reopening must show
  the lock screen, and logind must still suspend after its holdoff. Repeat
  outside the holdoff and check docked use with the built-in panel off. These
  [lid checks](https://github.com/abdulrahman-aj/arch-niri-dux/issues/48) and Plymouth
  firmware-logo/backlight behavior need physical laptop evidence. VM hibernation
  does not qualify every device.
- Synthetic audio, camera, network and printer checks qualify software paths.
  Package and ISO verification do not change physical machines.
