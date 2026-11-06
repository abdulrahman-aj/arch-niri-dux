# Verification

`make test` runs Ruff, ShellCheck, actionlint and source/build tests in the Nix
shell. `make iso-development` builds candidate integration packages and the ISO.
`make verify` qualifies a fresh offline encrypted UEFI install, desktop apps,
Dux bindings, package upgrades, snapshot boot/restore and disk unlock. Full
acceptance remains required for installer changes and releases. A development
image does not qualify later signed release bytes or production HTTPS delivery.

`make verify-fast ARGS="--fixture out/accepted/disk.qcow2 --directory out/fast"`
builds current integration packages inside a disposable overlay of a completed
`make verify` image. Keep `ACCEPTANCE-PASSED`, `install.json` and `OVMF_VARS.fd`
next to the fixture. Use an accepted 0.3+ fixture: older installer-owned policy
requires manual repair, and this target performs no migration. It verifies ALPM regenerated the active Limine initramfs and boot menu,
without manually invoking the rebuild tool, then checks Niri
validation, the Dux shortcut editor/cheatsheet, enabled units and presets, greetd
login, unlocked GNOME Keyring, and loaded vendor systemd drop-ins. It does not
replace fresh installation or snapshot/alongside qualification.
Pass `--kernel-package PATH` to include a local kernel archive in the same
transaction and exercise both boot rebuild hooks.

The current [0.3.0-1 qualification](verification/package-owned-policy-2026-11-06.json)
passed all 28 fresh offline acceptance stages and the fast package tier, with
123 source tests and seven build tests. The one-line shim failed Dux 2.1's real
manual-MRU cheatsheet check, so the qualified configuration uses relative home
includes, package-owned defaults/session tail and `overrides.kdl` last. Policy
ownership, both user preset cases, greetd/keyring, snapshot restoration and
recovery `noresume` passed. Raw evidence is under
`out/verify-package-owned-hybrid/` and `out/verify-package-owned-fast-2/`.

The [0.3.0-2 boot-policy upgrade qualification](verification/boot-policy-upgrade-2026-11-06.json)
passed the fast tier twice: a policy-only package upgrade, and the same upgrade
with a pinned-kernel reinstall exercising ALPM's Upgrade trigger. The newly
shipped hook ran in its first installation transaction. Both active initramfs
and `limine.conf` mtimes/hashes changed without a manual rebuild. In the combined
transaction the native and policy hooks rebuilt sequentially; reboot, greetd,
keyring, Niri/Dux and loaded-policy checks passed afterward. Full fresh
0.3.0-2 ISO/install qualification remains required before release; the fresh
and alongside results below/above qualify the 0.3.0-1 fixture.

The [dux-apps signed-repo qualification](verification/dux-apps-signed-repo-2026-11-07.json)
passed all 28 fresh offline acceptance stages plus the fast package tier on a
development ISO built from the rebased branch (PR #59 onto main, AUR origin
fields removed from the recipes). It delivers the four Dux apps (2.1.0-1)
through the signed integration repository and drops yay from the baseline.
Manual overlay probes show `pacman -Q yay` failing with the package not found,
the apps listed from the extracted offline repo database, the pinned release
key in the guest keyring with `Required TrustedOnly` enforcement, and
`pacman-conf --repo-list` showing core, extra, omarchy, arch-niri-dux. It
completes the fresh 0.3.0-2 qualification for this branch. Raw evidence is under
`out/verify-pr59-rebased/`, `out/verify-pr59-rebased-fast/`,
`out/verify-pr59-rebased-manual/` and `out/verify-pr59-rebased-probes/`.

## Alongside acceptance

The [current alongside qualification](verification/alongside-2026-11-06.json)
passed all six stages for both Debian 13 and Windows 11 Enterprise LTSC Evaluation
(build 26100). Every original partition byte survived installation. The new
Arch system booted by default, kernel/snapshot hooks retained the old-OS entry,
and both original systems booted through Limine afterward with their sentinels.
Fresh official fixtures were provisioned for this run. Raw evidence is under
`out/alongside/linux-policy/` and `out/alongside/windows-policy/`.

Fetch and provision official fixtures with `make fetch-alongside` and
`make provision-alongside`, then run `make verify-alongside` with the candidate
ISO, fixture disk and its firmware. Run Debian first, then Windows, sequentially.
[ALONGSIDE.md](ALONGSIDE.md#automated-provisioning-and-acceptance) contains the
commands and fixture contracts. These tools reserve at least 8 GiB of host disk
space; they use the normal 8 GiB guest RAM allocation.

## Known limits and hardware gaps

- Secure Boot remains deferred. Alongside fixtures used 512-byte sectors;
  4K-sector layouts are unqualified.
- Physical fingerprint enrollment, monitor DDC, drive-health readings, Wi-Fi
  hotspots, USB device access, LAN printer discovery and paper output need
  hardware checks.
- Plymouth firmware-logo/backlight behavior and intermittent lid-close wakeups
  need physical laptop evidence. VM hibernation does not qualify every device.
- Synthetic audio, camera, network and printer checks qualify software paths.
  No physical machine changes are part of package or ISO verification.
