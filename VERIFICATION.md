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
requires manual repair, and this target performs no migration. It checks Niri
validation, the Dux shortcut editor/cheatsheet, enabled units and presets, greetd
login, unlocked GNOME Keyring, and loaded vendor systemd drop-ins. It does not
replace fresh installation or snapshot/alongside qualification.

The latest completed baseline qualification is
[desktop recommendations](verification/desktop-recommendations-2026-11-05.json):
0.2.0-4, 27 fresh offline acceptance stages, 123 source and seven build tests.
The latest [alongside qualification](verification/alongside-2026-11-02.json)
used real Debian 13 and Windows 11 fixtures. Fixture images and raw logs are
local artifacts, not committed; use `make provision-alongside` and
`make verify-alongside` to repeat them.

## Alongside acceptance

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
