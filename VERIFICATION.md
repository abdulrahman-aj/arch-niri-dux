# Verification

## Targets

`make lint` runs Ruff, ShellCheck and actionlint in the Nix dev shell. It
discovers Python sources, shell files by extension or shebang, and our
PKGBUILDs, including untracked scripts. Generated output and vendored Dux app
recipes are excluded. `make test` adds source and build tests. CI runs these
checks without graphical VM acceptance.

`make iso-development` builds candidate integration packages and the ISO. `make
verify` qualifies a fresh offline encrypted UEFI install, graphical unlock and
wrong-passphrase retry, desktop apps, Dux bindings, package upgrades,
migrations, snapshot boot/restore and the `plymouth.enable=0` text fallback.
`make verify-hibernate` checks encrypted swap hibernate/resume separately. Full
acceptance is required for installer changes and releases. A development image
does not qualify signed release bytes or production HTTPS delivery. Follow
[release maintenance](packages/RELEASING.md) for `make iso` and `make
verify-release`.

`make verify-fast ARGS="--fixture out/accepted/disk.qcow2 --directory out/fast"`
builds current integration packages inside a disposable overlay of a completed
`make verify` image. Use an accepted 0.3+ fixture. Keep `ACCEPTANCE-PASSED`,
`install.json`, `inputs.json` and `OVMF_VARS.fd` next to it. It checks ALPM
regeneration of the active Limine initramfs and boot menu without a manual
rebuild, Niri validation, Dux shortcut editing/cheatsheet, enabled units and
presets, greetd login, unlocked GNOME Keyring, loaded vendor systemd drop-ins
and migration behavior. It does not replace fresh installation or snapshot and
alongside qualification. Pass `--kernel-package PATH` to reinstall a local
kernel archive in the same transaction and exercise both boot rebuild hooks.

Acceptance scenarios live in `scripts/verification/`. `scripts/verify.py` runs
them in order. Alongside and hibernation tests reuse the VM control and probes.
Accepted machine-readable evidence lives in `verification/`; logs, screenshots
and VM disks stay in disposable workspaces. The default full-acceptance
workspace is under `/tmp`. Use `--directory` to select a fresh location. Run VMs
on `msi-dux`. The runner waits for 10 GiB available host RAM before starting an
8 GiB guest. VM tests require KVM and a GPU render node with virtio 3D
acceleration; `QEMU_RENDER_NODE` defaults to `/dev/dri/renderD128`.

## Accepted software paths

- [Package-owned policy](verification/package-owned-policy-final-2026-11-07.json)
  passed the fast tier for a policy-only upgrade and a combined kernel transaction.
  ALPM regenerated the active initramfs and Limine menu; reboot, greetd/keyring,
  Niri/Dux and loaded-policy checks passed. Repository-resolution probes confirmed
  the [configured sources](packages/README.md#repositories).
- [Signed Dux apps](verification/dux-apps-signed-repo-2026-11-07.json) passed all
  28 fresh offline acceptance stages and the fast tier on a 0.3.0-2 development
  ISO. All four apps came from our signed repository. Guest probes confirmed yay
  was absent and the pinned release key enforced `Required TrustedOnly`.
- [Migrations](verification/migration-core-2026-11-07.json) passed full acceptance
  on the package-policy base and fast qualification after the signed-app rebase,
  plus 134 source tests and seven build tests. Fixtures covered skipped-version
  upgrades, ordered retry, failure notification/recovery, active-session and
  next-session activation, fresh-account baselines, custom files/symlinks and
  system-record rollback with surviving user records. The 120-second stale
  pacman-lock timeout and recovery passed. No production migrations ship.
  Full ISO acceptance was not repeated after that rebase.

These records qualify the commits and artifacts they name. They do not qualify
later signed release bytes or hardware behavior.

## Alongside acceptance

The [alongside qualification](verification/alongside-2026-11-06.json) passed all
six stages on the 0.3.0-1 development ISO for Debian 13 and Windows 11
Enterprise LTSC Evaluation, build 26100. Every original partition byte survived
installation. The new Arch system booted by default, kernel/snapshot hooks
retained the old-OS entry, and both original systems booted through Limine with
their sentinels.

[ALONGSIDE.md](ALONGSIDE.md#automated-provisioning-and-acceptance) owns the
commands, fixture contracts and disk-space safeguards. Run Debian first, then
Windows, sequentially.

## Known limits and hardware gaps

- Secure Boot remains [deferred](https://github.com/abdulrahman-aj/arch-niri-dux/issues/49).
  Alongside fixtures used 512-byte sectors; 4K-sector layouts are unqualified.
- Physical fingerprint enrollment, monitor DDC, drive-health readings, Wi-Fi
  hotspots, USB device access, LAN printer discovery and paper output need
  hardware checks.
- Plymouth firmware-logo/backlight behavior and [intermittent lid-close wakeups](https://github.com/abdulrahman-aj/arch-niri-dux/issues/48)
  need physical laptop evidence. VM hibernation does not qualify every device.
- Synthetic audio, camera, network and printer checks qualify software paths.
  Package and ISO verification do not change physical machines.
