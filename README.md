# Arch + Niri + Dux

An offline Arch installer for x86_64 UEFI machines. Includes Niri, the Dux 2.1
desktop and apps, encrypted Btrfs and snapshot recovery. Common files open in
Papers, Loupe, GNOME Text Editor, Celluloid and Decibels. Includes Chromium,
Alacritty, Neovim, Snapshot, Calculator, standard CLI tools, Btop and Fastfetch,
plus driverless printing and phone/camera file-access backends. Fastfetch is
also available on the live ISO.

## Build and test

Building requires Linux, Nix, Make and KVM access. Allow 24 GiB of build space, 8 GiB of
RAM and about 2.5 GiB for the ISO. Build cache goes in
`~/.cache/arch-niri-dux-build`. Use `BUILD_STATE=/path/with/space make iso` to change it.

```sh
make iso
make verify
```

`make lint` runs Ruff, ShellCheck and actionlint. It discovers Python sources,
shell files by extension or shebang, and our `PKGBUILD` recipes, including
untracked scripts. Generated output and vendored AUR recipes are excluded.
`make test` runs lint, source tests and build tests in CI.

Acceptance scenarios live in `scripts/verification/`. Each scenario keeps its
host interaction, guest probes and cleanup together. `scripts/verify.py` runs
them in order. Alongside installation and hibernation reuse the same VM control
and probe staging. The upgrade test records the state that snapshot restoration
must recover.

Accepted machine-readable evidence lives in `verification/`. Logs, screenshots
and VM disks stay in disposable workspaces. The default workspace is under
`/tmp`. Use `--directory` to select another location, such as an `out/` subdirectory.

`make iso` uses the recipe version's signed release packages. For unreleased
integration changes, use `make iso-development` instead. See [release maintenance](packages/RELEASING.md).

The build writes the ISO and checksums to `out/`. VM tests need a GPU render node
and virtio 3D acceleration. `QEMU_RENDER_NODE` defaults to `/dev/dri/renderD128`.

Signed binary packages and reviewed Dux AUR recipes use separate pins. See
[package inputs](packages/README.md). Desktop configuration comes from upstream
Dux. Rebuilds use the same inputs, but are not byte-identical.

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

Personal Niri overrides can live in `~/.config/niri/overrides.kdl`, including a
Stow-managed symlink. See [personal configuration](desktop/README.md#personal-niri-configuration)
for examples and upgrade behavior on existing installations.

Use Dux's system updater or `yay` for repository and AUR updates.
The installer offers automatic/custom encrypted swap or no hibernation.
With swap enabled, use `systemctl hibernate`. Closing the lid suspends when
undocked and allows continued use on an external display when docked.

## Recover

Boot a known-good snapshot from Limine, log in, and run:

```sh
sudo limine-snapper-restore
```

Restore and reboot into the normal entry. `/home`, including personal config,
stays untouched.

`snap-pac` creates snapshots around package transactions. Recovery entries use
`noresume` to prevent resuming a normal-root hibernation image over an older
snapshot. Timeline snapshots are disabled. Number cleanup uses limits of 5
regular and 5 important snapshots, with `snapper-cleanup.timer` enabled.

## Status

[VM verification](VERIFICATION.md) covers installation, desktop, updates, recovery
and hibernation. Laptop hibernate/resume also passed. The intermittent lid symptom
and docked display behavior still need checks on physical hardware.
Secure Boot is deferred. Track remaining work in
[GitHub issues](https://github.com/abdulrahman-aj/arch-niri-dux/issues).
