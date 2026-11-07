# Verification

## Targets

`make lint` runs Ruff, ShellCheck and actionlint in the Nix dev shell. It
discovers Python sources, shell files by extension or shebang, and our
PKGBUILDs, including untracked scripts. Generated output and vendored Dux app
recipes are excluded. `make test` adds source and build tests. CI runs these
checks without graphical VM acceptance.

`make iso-development` builds candidate integration packages and the ISO. Run
VMs on `msi-dux`. The runner waits for 10 GiB available host RAM before
starting an 8 GiB guest. VM tests require KVM and a GPU render node with
virtio 3D acceleration; `QEMU_RENDER_NODE` defaults to `/dev/dri/renderD128`.

## `make verify`

Full acceptance qualifies a fresh offline encrypted UEFI install on a
disposable 48 GiB VM disk. The workspace (default under `/tmp`, overridable
with `--directory`) records `install.json`, `inputs.json` with the ISO,
package-manifest and verification-script hashes, `OVMF_VARS.fd`, serial and
QEMU logs, screenshots and a milestone list. Only a full run writes
`ACCEPTANCE-PASSED`.

It boots the ISO with loopback as the only network interface, confirms the
package manifest and installer payload hashes, then installs with
`arch-niri-install` and qualifies graphical disk unlock with a
wrong-passphrase retry, UEFI boot without the ISO, package presets with
systemd policy and the PAM-unlocked keyring, the inbound firewall and
resolve-only mDNS seen from a veth neighbour namespace, Niri/Dux/terminal/editor/browser
smoke checks, desktop apps (manuals, archives, media playback, driverless
printing to a loopback printer), PDF rendering, the synthetic camera, screen
sharing, screen lock, migration ordering with retries and session triggers,
an update started through Dux's updater (its pacman backend, `pkexec` and
authentication dialog) running system and user migrations,
an integration upgrade with Limine snapshot boot and restore preserving home,
and the `plymouth.enable=0` text fallback with graphical unlock restored
afterward. `make verify-release` additionally checks published HTTPS package
delivery against offline trust and exact release hashes. `--install-only` is
a debug run and does not produce acceptance success. Full acceptance is
required for installer changes and releases.

## `make verify-fast`

`make verify-fast ARGS="--fixture out/accepted/disk.qcow2 --directory out/fast"`
builds current integration packages inside a disposable overlay of a completed
`make verify` image. Use an accepted 0.3+ fixture. Keep `ACCEPTANCE-PASSED`,
`install.json`, `inputs.json` and `OVMF_VARS.fd` next to it. It checks ALPM
regeneration of the active Limine initramfs and boot menu without a manual
rebuild, Niri validation, Dux shortcut editing/cheatsheet, enabled units and
presets, greetd login, unlocked GNOME Keyring, loaded vendor systemd drop-ins
and migration behavior. It does not replace fresh installation or snapshot
and alongside qualification. Pass `--kernel-package PATH` to reinstall a
local kernel archive in the same transaction and exercise both boot rebuild
hooks.

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
- Plymouth firmware-logo/backlight behavior and [intermittent lid-close wakeups](https://github.com/abdulrahman-aj/arch-niri-dux/issues/48)
  need physical laptop evidence. VM hibernation does not qualify every device.
- Synthetic audio, camera, network and printer checks qualify software paths.
  Package and ISO verification do not change physical machines.
