# Arch + Niri + Dux

An offline Arch installer for x86_64 UEFI machines, with Niri, Dux 2.1,
encrypted Btrfs and snapshot recovery. The installed system includes
[desktop apps and device backends](desktop/README.md#applications-and-session-defaults).
The live ISO is terminal-only and includes Fastfetch.

## Build and test

Building requires Linux, Nix and Make. Allow at least 30 GiB of free build space and about 2.5
GiB for the ISO. VM verification also requires KVM access. Build state goes in
`~/.cache/arch-niri-dux-build-<checkout-path-hash>`. Use `BUILD_STATE=/path/with/space make iso` to
change it. An explicit `BUILD_STATE` wins and is yours to manage; GC leaves
it alone. Every build state shares the verified bootstrap ISO and packages in
`~/.cache/arch-niri-dux-downloads`. Builds recheck each file's hash or
signature before use. The cache never prunes old versions; deleting it is safe.

```sh
make iso
make verify
```

See [verification](VERIFICATION.md) for test targets, VM requirements and current limits.

`make iso` uses the recipe version's signed release packages. For unreleased
integration changes, use `make iso-development` instead. See [release maintenance](packages/RELEASING.md).

The build writes the ISO and checksums to `out/`.

Signed binary packages and reviewed Dux app recipes use separate pins. See
[package inputs](packages/README.md) and [desktop
configuration](desktop/README.md#niri-configuration) for details. Pinned inputs
do not produce byte-identical rebuilds.

## Install

1. Write the ISO to a USB drive and boot it in UEFI mode with Secure Boot off.
2. Follow the terminal wizard. **Erase mode erases the selected disk.** Alongside
   mode asks you to select an unallocated GPT region. See
   [supported layouts and verification](ALONGSIDE.md).
3. Remove the USB and reboot. Unlock the disk in the graphical prompt
   and log in. Press Esc to show boot messages. A text prompt appears if graphics
   are unavailable.

Super+Return opens a terminal, Super+Space opens the launcher, and Super+Alt+L
locks the screen.

Use Dux's system updater or `sudo pacman -Syu` for updates.
The installer offers automatic/custom encrypted swap or no hibernation.
With swap enabled, use `systemctl hibernate`. Closing the lid suspends when
undocked and allows continued use on an external display when docked.

## Recover

Boot a known-good snapshot from Limine, log in, and run:

```sh
sudo limine-snapper-restore
```

Restore and reboot into the normal entry. `/home`, including personal config,
stays untouched. After restoring an older snapshot, run a full system update
before installing anything else. The restored system may trust signing keys
that were revoked after the snapshot was taken.

See [snapshot policy](desktop/README.md#system-policy) for transaction snapshots
and recovery safeguards.

## Status

[Verification](VERIFICATION.md) records accepted software paths and hardware
gaps. Track remaining work in [GitHub
issues](https://github.com/abdulrahman-aj/arch-niri-dux/issues).
