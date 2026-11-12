# Arch + Niri + Dux

An offline installer that sets up Arch Linux with the Niri compositor and the Dux desktop shell on an
encrypted disk with Btrfs snapshots. Signed updates and migrations keep installed machines current after setup.

The desktop software comes from upstream: Arch, Niri, Dux, Limine and snapper. This project chooses the
defaults, wires the pieces together, tests the result in a VM before every release and ships fixes to
machines that are already installed. The live ISO itself is terminal-only.

## What you get

- The live ISO installs the full desktop with no network and checks its signed on-medium repository before it partitions.
- The wizard lists only disks of at least 32 GiB, excludes the live USB, defaults the final prompt to Cancel and rechecks
  disk identity before writing.
- Erase mode creates a 4 GiB ESP and one LUKS2 Btrfs volume with separate subvolumes for home, logs, the package cache and
  snapshots, so a rollback keeps your files.
- Alongside mode uses only existing unallocated space, keeps the old system bootable from Limine, and refuses BitLocker,
  dynamic disks, BIOS boot and damaged GPT before changing anything.
- You unlock the disk at a graphical prompt that lets you retry a mistyped passphrase and falls back to text. Encrypted
  swap, chosen with one installer question, powers hibernate and resume.
- You log in from the Dux greeter straight into Niri, with the GNOME keyring already unlocked, so browsers ask for no
  second password.
- PDFs open in Papers, images in Loupe, video in Celluloid, audio in Decibels, links in Chromium and folders in Dux Files.
- Picking light or dark in Dux switches GTK, Qt and Chromium together, and file pickers open the floating Dux Files dialog.
- Closing the lid suspends the laptop when undocked and keeps it running on an external display when docked. The screen
  locks before suspend and after 10 idle minutes.
- The firewall drops unsolicited inbound traffic and still lets printer discovery, `.local` names, hotspots and Tailscale
  work.
- Every package change takes a snapshot that you can boot from Limine and restore without touching your home directory.
- Updates come from signed repositories, ours first. Dux shows an update button only when updates are waiting.
- Your settings in `~/.config/niri/overrides.kdl` and Dux Settings survive updates, which only change the defaults.

Every release installs in a VM and passes the [acceptance checks](VERIFICATION.md) first, from disk unlock
through suspend, updates and snapshot restore.

## What it isn't

- It isn't a general Arch installer. It installs this one desktop with these choices.
- It is young, and only x86_64 UEFI machines are supported.
- It isn't frozen. It follows the live Arch repositories and takes a few packages from the Omarchy repository.

## Install

1. Get the ISO. Build it with `make iso` (see [BUILDING.md](BUILDING.md)), or [verify a prebuilt
   ISO](#verify-a-prebuilt-iso) that someone gave you.
2. Write it to a USB drive and boot it in UEFI mode with Secure Boot off.
3. Follow the terminal wizard. **Erase mode erases the selected disk.** Alongside mode asks you to pick an
   unallocated GPT region. See [supported layouts](ALONGSIDE.md).
4. Remove the USB drive and reboot. Unlock the disk in the graphical prompt and log in. Press Esc to show
   boot messages. A text prompt appears if graphics are unavailable.

Super+Return opens a terminal, Super+Space opens the launcher, and Super+Alt+L locks the screen.

The installer offers automatic or custom encrypted swap, or no hibernation. With swap, use
`systemctl hibernate`. Closing the lid suspends the laptop when it is undocked. Docked, it keeps running on
the external display.

### Verify a prebuilt ISO

If someone gave you `arch-niri-dux-0.3.2-x86_64.iso`, verify it before flashing. Get `SHA256SUMS` and
`SHA256SUMS.sig` from the same person, all three files together. Download the public key separately from
this repository,
[`keys/integration-signing.gpg`](https://raw.githubusercontent.com/abdulrahman-aj/arch-niri-dux/v0.3.2-1/keys/integration-signing.gpg),
and save it as `integration-signing.gpg` next to those files. Take these instructions from this repository,
not from whoever sent the ISO. A public key that arrives with the ISO proves nothing on its own.

On Linux with GnuPG and GNU coreutils, run this in that directory:

```sh
echo 'd296f2efe406b0172b0006fb98e9e515ef1e9cf69dc63e6d6694cf572886fe0f  integration-signing.gpg' |
  sha256sum --check --strict &&
gpgv --keyring ./integration-signing.gpg SHA256SUMS.sig SHA256SUMS &&
sha256sum --check --strict SHA256SUMS
```

Flash the ISO only if every command succeeds, including a good signature and
`arch-niri-dux-0.3.2-x86_64.iso: OK`. The first command checks the whole public key against these
instructions. The signature shows the project released these exact ISO bytes. It does not show that CI
built or reproduced them.

Make sure it is the release you meant to install, because signatures don't stop someone from handing you an
older one. `gpgv` doesn't check key expiry or revocation, so follow any [key recovery
advisory](keys/RECOVERY.md#iso-verification-and-recovery) before installing.

## Update

Click the update button next to the clock when it appears, or run `sudo pacman -Syu`. Migrations that an update
brings run right after it finishes, or at the next boot.

If the first update on an older install fails on a new packager key, refresh the keyring first with
`sudo pacman -Sy archlinux-keyring && sudo pacman -Su`.

## Recover

Boot a working snapshot from the Limine menu, log in, and run:

```sh
sudo limine-snapper-restore
```

Restore it and reboot into the normal entry. A restore rolls back everything outside the separate
subvolumes: the root system plus `/var/lib` (databases, Docker/Podman, libvirt images, system Flatpaks),
`/root`, `/srv`, `/opt` and `/usr/local`. It keeps `/home`, `/var/log` and the package cache
(`/var/cache/pacman/pkg`), which live on their own subvolumes.

Upstream restore keeps the replaced root as a "backup" boot entry (see
`/usr/share/doc/limine-snapper-sync/README.md`), so you can boot back if the restore was wrong.
The backup is a snapper snapshot with no cleanup algorithm, so snapper's number cleanup leaves it
alone; delete it manually with `sudo snapper -c root delete <id>` once you no longer need it.

After restoring an older snapshot, run a full system update before installing anything else,
because the restored system may trust signing keys that were revoked after the snapshot. The [snapshot
policy](desktop/README.md#system-policy) covers transaction snapshots and other safeguards.

### If the boot menu doesn't appear

Boot the ISO, unlock the disk, mount the system and reinstall the bootloader from the ISO terminal.
Replace `/dev/<encrypted-part>` with the LUKS partition and `/dev/<esp-part>` with the EFI partition
(use `lsblk` to find them):

```sh
cryptsetup open /dev/<encrypted-part> dux-root
mount -o subvol=@,compress=zstd:1,noatime /dev/mapper/dux-root /mnt
mount /dev/<esp-part> /mnt/boot
arch-chroot /mnt
pacman -S limine limine-mkinitcpio-hook limine-snapper-sync linux
limine-install
limine-mkinitcpio
limine-snapper-sync
exit
reboot
```

`pacman -S` needs network; the reinstall triggers the initramfs and boot-menu rebuild.

## Build and contribute

[BUILDING.md](BUILDING.md) covers building the ISO and running the VM tests. [Verification](VERIFICATION.md)
records what the tests cover and which hardware checks remain. Open work lives in [GitHub
issues](https://github.com/abdulrahman-aj/arch-niri-dux/issues).
