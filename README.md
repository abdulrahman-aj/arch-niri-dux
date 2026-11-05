# Arch + Niri + Dux

An offline Arch installer for x86_64 UEFI machines. Includes Niri, the Dux 2.1
desktop and apps, encrypted Btrfs and snapshot recovery. Common files open in
Papers, Loupe, GNOME Text Editor, Celluloid and Decibels. Includes Chromium,
Alacritty, Neovim, Snapshot, Calculator, standard CLI tools, Btop and Fastfetch,
plus driverless printing and phone/camera file-access backends. Fastfetch is
also available on the live ISO.

## Build and test

Requires Linux, Nix, Make and KVM access. Allow 24 GiB of build space, 8 GiB of
RAM and about 2.5 GiB for the ISO. Build cache goes in
`~/.cache/arch-niri-dux-build`; `BUILD_STATE=/path/with/space make iso` overrides it.

```sh
make iso
make verify
```

`make iso` uses the recipe version's signed release packages. For unreleased
integration changes, use `make iso-development` instead. See [release maintenance](packages/RELEASING.md).

The ISO and checksums land in `out/`. VM tests need a GPU render node and virtio
3D acceleration; `QEMU_RENDER_NODE` defaults to `/dev/dri/renderD128`.

Signed binary packages and reviewed Dux AUR recipes are pinned separately; see
[package inputs](packages/README.md). Desktop configuration comes from upstream
Dux. Rebuilds use the same inputs, but are not byte-identical.

## Install

1. Write the ISO to a USB drive and boot it in UEFI mode with Secure Boot off.
2. Follow the terminal wizard. **Erase mode erases the selected disk.** Alongside mode uses explicitly selected existing unallocated GPT space; see [supported layouts and verification](ALONGSIDE.md).
3. Remove the USB and reboot. Unlock the disk in the graphical prompt
   (Esc shows boot messages; a text prompt appears automatically without
   graphics), then log in.

Super+Return opens a terminal, Super+Space opens the launcher, and Super+Alt+L
locks the screen.

Update through Dux's system updater or run `yay`; both cover repository and AUR
packages. The installer offers automatic/custom encrypted swap or no hibernation.
With swap enabled, use `systemctl hibernate`; closing the lid suspends when
undocked and allows continued use on an external display when docked.

## Recover

Boot a known-good snapshot from Limine, log in, and run:

```sh
sudo limine-snapper-restore
```

Restore and reboot into the normal entry. `/home`, including personal config,
stays untouched.

## Status

[VM verification](VERIFICATION.md) covers installation, desktop, updates, recovery
and hibernation. Laptop hibernate/resume also passed; the intermittent lid symptom
and docked display behavior remain hardware checks.
Secure Boot is outside v1; planned features are in [TODO.md](TODO.md).

Alongside installation design, supported layouts and pending VM acceptance are documented in [ALONGSIDE.md](ALONGSIDE.md).
