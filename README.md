# Arch + Niri + Dux

An offline Arch installer for x86_64 UEFI machines. Includes Niri, the Dux 2.0
desktop and apps, Alacritty, Neovim, Chromium, Git and GitHub CLI, with encrypted
Btrfs and snapshot recovery.

## Build and test

Requires Linux, Nix, Make and KVM access. Allow 16 GiB of build space, 8 GiB of
RAM and 2.2 GiB for the ISO.

```sh
make iso
make verify
```

The ISO and checksums land in `out/`. VM tests need a GPU render node and virtio
3D acceleration; `QEMU_RENDER_NODE` defaults to `/dev/dri/renderD128`.

Signed binary packages and reviewed Dux AUR recipes are pinned separately; see
[package inputs](packages/README.md). Desktop configuration comes from upstream
Dux. Rebuilds use the same inputs, but are not byte-identical.

## Install

1. Write the ISO to a USB drive and boot it in UEFI mode with Secure Boot off.
2. Follow the terminal wizard. **It erases the selected disk.**
3. Remove the USB and reboot. Unlock the disk, then log in.

Super+Return opens a terminal, Super+Space opens the launcher, and Super+Alt+L
locks the screen. Personal apps and [dotfiles](https://github.com/abdulrahman-aj/dotfiles/pull/1)
are a separate online step.

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

[VM verification](VERIFICATION.md) covers installation, desktop, locking and
recovery. Laptop Wi-Fi, audio and suspend still need physical testing.
Secure Boot is outside v1; planned features are in [TODO.md](TODO.md).
