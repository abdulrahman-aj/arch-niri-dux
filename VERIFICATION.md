# Verification

## Targets

`make lint` runs Ruff, ShellCheck and actionlint in the Nix dev shell. It
discovers Python sources, shell files by extension or shebang, and our
PKGBUILDs, including untracked scripts. Generated output and vendored Dux app
recipes are excluded. `make test` adds source and build tests. CI runs these
checks without graphical VM acceptance. `make qualify` runs the tier matching
the branch's changed files and writes `out/qualify/<head>/EVIDENCE.md`.

Every scenario records a `checkout` object in `inputs.json` with the commit
and dirty state. Dirty runs also record `diff_sha256`, the SHA-256 of
`git diff HEAD --binary`, matching the ISO's `.iso.source` identity. Dirty
state includes untracked files, but the diff hash covers tracked changes only.
Ignored build output does not mark the checkout dirty. Existing script hashes
are unchanged.

Guest probes live under `tools/verification/probes/` and execute from the staged
`/run/verify` tree. `tools/verification/payload.py` stages the complete probe
package, shared constants/image helpers, and the package builder plus its inputs
reader. The isolated staging test imports every guest module with the checkout
removed from Python's path. Host drivers, login and VM transport stay on the host;
import lint checks the boundary in both directions.

`make iso-development` builds candidate integration packages and the ISO. Run
VMs on your build machine. Builds and VM disk creation need the free space
noted in [BUILDING.md](BUILDING.md). The runner waits for 10 GiB available host RAM before
starting an 8 GiB guest. VM tests require KVM and a GPU render node with
virtio 3D acceleration; `QEMU_RENDER_NODE` defaults to `/dev/dri/renderD128`.

## Journey map

Negative controls deliberately supply bad input or state and require rejection;
partial controls below prove only the named part of a journey.

| Journey | Scenario, stage or probe | Target or qualification tier | Negative control |
| --- | --- | --- | --- |
| Fresh install, erase | [install scenario](tools/verification/scenarios/install.py) `run_install`, [full scenario](tools/verification/scenarios/full.py) | `make verify`; installer tier | No deliberate failed-install control |
| Install alongside Linux | [alongside scenario](tools/verification/scenarios/alongside.py), [install scenario](tools/verification/scenarios/install.py) `run_alongside_install` | `make verify-alongside ARGS='--os linux ...'`; alongside changes add this to installer tier | No control. A cheap control would only test `sha256sum`; the real check is `sha256sum -c` over the old partitions in `run_alongside_install`, which fails the run through `vm.command` on any mismatch |
| Install alongside Windows | [alongside scenario](tools/verification/scenarios/alongside.py), [install scenario](tools/verification/scenarios/install.py) `run_alongside_install` | `make verify-alongside ARGS='--os windows ...'`; alongside changes add this after Linux | No control, same check as Linux |
| First boot to desktop | [boot stage](tools/verification/boot.py) `verify_boot`, [desktop stage](tools/verification/desktop.py), [login](tools/verification/login.py) | `make verify`; probe/installer tiers | Partial, graphical unlock retries a wrong passphrase |
| Apps open | [desktop stage](tools/verification/desktop.py), [apps stage](tools/verification/apps.py), [apps probe](tools/verification/probes/apps.py), [browser stage](tools/verification/browser.py), [PDF stage](tools/verification/pdf.py), [camera stage](tools/verification/camera.py) | `make verify`; probe/installer tiers | Partial, [apps probe tests](tests/test_apps_probes.py) reject silent/wrong-tone audio and invalid thumbnails; `make test` |
| Upgrade keeps personal config | [recovery probe](tools/verification/probes/recovery.py) `snapshots` calls [upgrade probe](tools/verification/probes/upgrade.py) `verify_upgrade`; checks three personal config files | `make verify`, recovery stage; probe/installer tiers | Yes, [preservation tests](tests/test_preservation.py) overwrite each checked config; `make test` |
| Dux updater | [updater stage](tools/verification/dux_update.py), [updater probe](tools/verification/probes/dux_update.py) | `make verify ARGS=--slow`; ordinary qualification skips it | No deliberate broken-updater control |
| Suspend/hibernate resume | [suspend stage](tools/verification/suspend.py), [suspend probe](tools/verification/probes/suspend.py); [hibernate scenario](tools/verification/scenarios/hibernate.py) | Suspend: `make verify`; hibernate: separate `make verify-hibernate` on accepted workspace | No deliberate failed-resume control; password unlock after S3 and device-specific resume remain gaps |
| Recovery/fallback boot | [recovery stage](tools/verification/recovery.py), [recovery probe](tools/verification/probes/recovery.py); [boot stage](tools/verification/boot.py) `verify_fallback` | `make verify`; probe/installer tiers | Yes, recovery masks normal-root greetd before snapshot boot; fallback disables Plymouth |
| Release delivery | [release scenario](tools/verification/scenarios/release.py), [upgrade probe](tools/verification/probes/upgrade.py) `published_repository` | Separate `make verify-release` | Partial, `verify_upgrade` rejects a tampered signed database in `make verify`; release delivery itself has no tamper control |

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
follow afterward. `make verify-release` is the release mode (see
[release acceptance](packages/RELEASING.md#publish)): it installs the release
ISO, boots once, and runs only the online delivery check. `--install-only` is
a debug run and does not produce acceptance success. Full acceptance is
required for installer changes and releases.

The runner continues past stage failures while the guest still answers:
apps, PDF, camera and browser depend only on the desktop session, not on each
other, so one of them failing still runs the rest. If boot, desktop or suspend fails,
the run stops there. Later stages require the desktop and host evidence share;
suspend always attempts to reboot, remount the share and copy guest-local evidence
before returning its failure. Any other failure still runs the rest, and the run exits non-zero listing every
failure and skip in `FAILURE.txt`. Only a passing full run writes
`ACCEPTANCE-PASSED` and can become a fixture, so a partial disk never feeds
fixture auto-discovery.

The Dux updater stage is slow: its two scheduled-check waits take
about eight minutes, so ordinary runs skip it and finish in about eight and a
half minutes instead of sixteen and a half. Request it explicitly with
`make verify ARGS='--slow'` when qualifying changes touching the update
button, its seeds, or Dux itself. Releases qualify delivery separately through
`make verify-release`, which runs only the online check.

`make verify ARGS='--keep'`: on failure the guest stays running with its
serial/QMP sockets under `<evidence>/.sockets` and prints attach commands;
the failed disk is still unlinked, so it can never become a fixture (QEMU
holds the open file; the space frees at poweroff). Attach with
`python3 -m tools.vm.console --directory <evidence> sh '<command>'`, rerun one
reboot-free stage (`... stage <apps|browser|camera|desktop|dux_update|pdf>`),
then `... poweroff`. Suspend and recovery reboot the guest, so they are
rejected. On success `--keep` changes nothing.

Suspend qualification requires logind `LockedHint=yes`, Niri's lock event, an unchanged boot ID and kernel S3 entry/exit. Post-resume QEMU GL/input and shutdown can stall, so screenshots stay best-effort and this stage does not qualify password unlock. The probe syncs the guest before the host restarts it.

A timed-out post-resume monitor action writes `03-suspend-monitor-timeout.txt`
with live runuser/Niri process and thread states, kernel stacks, user-slice freezer
state, an independent Niri Outputs query, and kernel/DRM and Niri journal tails.
The action retains its 10-second bound and fails after collection; it is not retried.

## `make verify-fast`

`make verify-fast` is a thin alias for fixture mode in `verify.py`
(`--fixture`), not a second script. It builds current
integration packages inside a disposable overlay of an accepted
`make verify` image. `--fixture` defaults to the newest registered 0.3+
workspace holding `ACCEPTANCE-PASSED` and `disk.qcow2` whose recorded
`checkout.commit` is an ancestor of `origin/main` and whose `checkout.dirty`
is false. Discovery uses the local `origin/main` ref without fetching, reports
skipped fixtures with a reason, and prints the chosen fixture and commit.
Without a qualifying fixture it stops; run `make verify` on a clean main checkout
or pass `--fixture PATH` to override discovery. There is no fallback.
`--directory` defaults to a fresh `out/fast-<timestamp>`; pass explicit
paths to override either default. Keep `ACCEPTANCE-PASSED`,
`install.json`, `inputs.json` and `OVMF_VARS.fd` next to the fixture disk. It checks ALPM
regeneration of the active Limine initramfs and boot menu without a manual
rebuild, Niri validation, Dux shortcut editing/cheatsheet, enabled units and
presets, greetd login, unlocked GNOME Keyring, loaded vendor systemd drop-ins
and migration behavior. It does not replace fresh installation or snapshot
and alongside qualification. Pass `--kernel-package PATH` to reinstall a
local kernel archive in the same transaction and exercise both boot rebuild
hooks.

## Error gate

`make verify` and `make verify-fast` end with an error gate: a final clean
boot collects `journalctl -b -p warning` and `systemctl --failed` (system and
the tester's user manager) into the evidence dir, and any line without a
reviewed entry in `tools/verification/gate-allowlist.txt` fails the run
before `ACCEPTANCE-PASSED` / `FAST-PASSED` is written. Each allowlist line is
one regex on the normalized text with a `# <reason>` naming its judgement
rows. An optional trailing `[until PACKAGE VERSION]` in the reason permits an
entry only for that exact installed version. The gate saves guest `pacman -Q`
output as `guest-packages.txt` and fails on any version mismatch or missing
package, even when the warning is absent. Remove the expired entry and verify
that the upstream fix has shipped. Deliberate-failure probes (firewall containment, signature rejection)
run in earlier boots only, so the final-boot scan never sees them.

## Debugging loop (kept verify-fast VM)

Rule: QML/probe-only changes never rerun full `make verify`; full verify is
final qualification. Iterate on a kept VM, then qualify once.

`make verify-fast ARGS="--directory out/debug --keep"` (the fixture is
auto-found; pass `--fixture` to override) writes `FAST-PASSED` as usual, then keeps the VM running with its serial/QMP
sockets under `out/debug/.sockets` and prints paste-ready attach commands
(the same block prints if the run fails, when the live guest is most useful).
Without `--keep`, behavior is unchanged.

Drive the kept VM with `python3 -m tools.vm.console` (one driver at a time):

```
./scripts/in-dev-shell python3 -m tools.vm.console --directory out/debug sh '<command>'
./scripts/in-dev-shell python3 -m tools.vm.console --directory out/debug shot <name.png>
./scripts/in-dev-shell python3 -m tools.vm.console --directory out/debug click <x_frac> <y_frac>
./scripts/in-dev-shell python3 -m tools.vm.console --directory out/debug key <qcode>
./scripts/in-dev-shell python3 -m tools.vm.console --directory out/debug probe <module> <function>
./scripts/in-dev-shell python3 -m tools.vm.console --directory out/debug poweroff
```

Per iteration there is no ISO rebuild, reinstall or scheduler wait. The
evidence directory is mounted in the guest at `/run/verify`, so copy a changed
file into `out/debug/` on the host, then e.g.
`sh 'cp /run/verify/ReleaseWidget.qml /etc/xdg/quickshell/dms-plugins/archNiriDuxRelease/ReleaseWidget.qml'`.
For multi-line guest work, pipe a script on stdin instead of nested quoting:
`python3 -m tools.vm.console --directory out/debug sh < fix.sh`. Commands needing
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
[ALONGSIDE.md](ALONGSIDE.md#automated-provisioning-and-acceptance). Fixtures
live in the shared home `~/.local/share/arch-niri-dux/alongside/` (outside every
checkout, so `make gc` cannot remove them with a worktree); per-run evidence
stays under `out/`. It boots
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

## Installer UI preview

`make installer-preview ARGS='--disk-gib 32 --ram-gib 4'` boots the newest
checkout development ISO in `out/` with a disposable disk, ISO-first boot
order and a root serial console. Build it once with `make iso-development`.
Use `--iso PATH` to select a development ISO from another checkout and
`--directory out/preview-2` for a fresh preview workspace. It stages this
checkout's `installer/` over the evidence share, copies it into the live
system and restarts the tty1 installer. No network is enabled.

After editing installer files, drive the running preview with:

```
./scripts/in-dev-shell python3 -m tools.vm.console --directory out/preview preview-sync
./scripts/in-dev-shell python3 -m tools.vm.console --directory out/preview shot installer.png
./scripts/in-dev-shell python3 -m tools.vm.console --directory out/preview key ret
./scripts/in-dev-shell python3 -m tools.vm.console --directory out/preview poweroff
```

Screenshots use QMP's PNG screendump, including the live text console.
`sh '<command>'` provides the same serial shell used by kept desktop VMs.
Power off before removing the preview directory or starting another VM.
`make try` remains human-only; this disposable preview uses the regular
agent machine, memory and disk guards.

Previews never qualify a change. Final installer work still requires an
unmodified ISO built with `make iso-development` and full `make verify`.
Runtime copies bypass packaging and payload verification.

For desktop QML, use the [kept verify-fast loop](#debugging-loop-kept-verify-fast-vm).
For example, copy a widget into `out/debug/`, use `vm_console sh` to copy
`/run/verify/Widget.qml` to its installed path, then use `shot widget.png`.
Run package qualification after the final edit and stop with `poweroff`.
