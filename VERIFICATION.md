# Verification

## Targets

`make lint` runs Ruff, ShellCheck and actionlint in the Nix dev shell. It
discovers Python sources, shell files by extension or shebang, and our
PKGBUILDs, including untracked scripts. Generated output and vendored Dux app
recipes are excluded. `make test` adds source and build tests. CI runs these
checks without graphical VM acceptance.

`make iso-development` builds candidate integration packages and the ISO. Run
VMs on your build machine. Builds and VM disk creation require 30 GiB
free. The runner waits for 10 GiB available host RAM before
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
sharing, and screen lock.
An integration upgrade with Limine snapshot boot and restore preserving home,
and the `plymouth.enable=0` text fallback with graphical unlock restored
follow afterward. `make verify-release` is a separate release path (see
[release acceptance](packages/RELEASING.md#publish)): it installs the release
ISO, boots once, and runs only the online delivery check. `--install-only` is
a debug run and does not produce acceptance success. Full acceptance is
required for installer changes and releases.

The runner continues past stage failures while the guest still answers:
apps, PDF, camera and browser depend only on the desktop session, not on each
other, so one of them failing still runs the rest. If boot or desktop fails,
the run stops there, since later stages cannot mean anything without them. Any
other failure still runs the rest, and the run exits non-zero listing every
failure and skip in `FAILURE.txt`. Only a passing full run writes
`ACCEPTANCE-PASSED` and can become a fixture, so a partial disk never feeds
fixture auto-discovery.

The Dux updater stage is slow: its two scheduled-check waits take
about eight minutes, so ordinary runs skip it and finish in about eight and a
half minutes instead of sixteen and a half. Request it explicitly with
`make verify ARGS='--slow'` when qualifying changes touching the update
button, its seeds, or Dux itself. Full runs with `--online-repository` also
include it; releases qualify delivery separately through `make verify-release`,
which runs only the online check.

`make verify ARGS='--keep'`: on failure the guest stays running with its
serial/QMP sockets under `<evidence>/.sockets` and prints attach commands;
the failed disk is still unlinked, so it can never become a fixture (QEMU
holds the open file; the space frees at poweroff). Attach with
`scripts/vm_console.py --directory <evidence> sh '<command>'`, rerun one
reboot-free stage (`... stage <apps|browser|camera|desktop|dux_update|pdf>`),
then `... poweroff`. Suspend and recovery reboot the guest, so they are
rejected. On success `--keep` changes nothing.

Suspend qualification requires logind `LockedHint=yes`, Niri's lock event, an unchanged boot ID and kernel S3 entry/exit. Post-resume QEMU GL/input and shutdown can stall, so screenshots stay best-effort and this stage does not qualify password unlock. The probe syncs the guest before the host restarts it.

## `make verify-fast`

`make verify-fast` is a thin alias for fixture mode in `verify.py`
(`--fixture`), not a second script. It builds current
integration packages inside a disposable overlay of the newest accepted
`make verify` image. `--fixture` defaults to
the newest registered 0.3+ workspace holding `ACCEPTANCE-PASSED` and `disk.qcow2`,
and `--directory` defaults to a fresh `out/fast-<timestamp>`; pass explicit
paths to override either default. Keep `ACCEPTANCE-PASSED`,
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

`make verify-fast ARGS="--directory out/debug --keep"` (the fixture is
auto-found; pass `--fixture` to override) writes `FAST-PASSED` as usual, then keeps the VM running with its serial/QMP
sockets under `out/debug/.sockets` and prints paste-ready attach commands
(the same block prints if the run fails, when the live guest is most useful).
Without `--keep`, behavior is unchanged.

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
For multi-line guest work, pipe a script on stdin instead of nested quoting:
`scripts/vm_console.py --directory out/debug sh < fix.sh`. Commands needing
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
