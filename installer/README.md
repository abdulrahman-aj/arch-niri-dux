# Installer integration

`install.py` is the shared wizard and unattended VM installation engine. The build
copies this directory to `/usr/local/lib/arch-niri-dux/installer` on the live ISO,
and exposes `arch-niri-install` as its entry point.

The live ISO must provide `fzf` for selection menus, plus:

- `/run/archiso/bootmnt/repo`: a local pacman repository with the complete target closure;
- `/usr/local/share/arch-niri-dux/packages.target`: one package name per line;
- `arch-niri-dux` and `arch-niri-dux-baseline` in the offline repository.
  The baseline installs after initial boot setup so `snap-pac` starts with a
  bootable system. See [desktop initialization](../desktop/README.md) for package
  ownership and missing-setting seeds. `configure-target.sh` initializes the
  machine and applies package-owned service presets. Installation downloads
  nothing.

Partitioning uses a 4 GiB FAT32 ESP and a LUKS2 Btrfs root. LUKS2 uses
cryptsetup's default argon2id parameters (benchmarked memory, about 1 GiB on
the install machine) with no pinned memory limit. Independent root,
home, logs, package cache, and snapshots subvolumes keep recovery away from home.
Upstream Limine tools manage the kernel and initramfs. Snapshot boot
uses their systemd Btrfs overlay hook, and restoration uses their `replace` method.

Swap choices are Auto, a custom size, or none. Auto uses RAM rounded up to GiB
plus 2 GiB. An enabled swapfile lives in its own `@swap` subvolume inside the
encrypted filesystem. Btrfs computes its physical resume offset. See [system
policy](../desktop/README.md#system-policy) for snapshot recovery safeguards.

## Disk unlock

Boot uses Plymouth's stock `bgrt` theme with `quiet splash`. The disk
passphrase appears in a graphical box instead of inline boot logs, with an
automatic text fallback when KMS is unavailable. `sd-encrypt` retries a
mistyped passphrase. Press Esc during boot to show messages. Boot failures
stay visible because only `quiet splash` quiets the console. To force the
plain console prompt, append `plymouth.enable=0` to the kernel cmdline.
`disablehooks=plymouth` optionally bypasses the initramfs hook as well.

Graphical unlock acceptance requires screenshots and keyboard input. Serial
unlock alone does not qualify its appearance. See
[verification](../VERIFICATION.md) for the acceptance targets and hardware gaps.

## Unattended installation

Automation uses a private mode-0600 JSON file containing Settings fields and
`--confirm-disk /dev/vda`. It exercises the same validation and installation
engine as the wizard. Never include real personal passwords in fixtures or logs.
The optional `swap_gib` field accepts `null` for Auto, `0` for disabled, or a
positive integer. Both modes check capacity before erasing. The interactive
wizard shows the resolved size and defaults its final erase confirmation to
Cancel.  Auto swap needs roughly RAM plus 31 GiB of disk space. Smaller disks
can use a custom size or disable swap, while the base installation requires 32
GiB.

## Machine configuration

[The integration package](../desktop/README.md#system-policy) owns shared system
policy. `install.py` writes UUIDs, kernel arguments, the resume offset, EFI
deferral and the no-swap sleep policy before the first pacstrap.
`configure-target.sh` receives only the username and timezone for chroot setup. `SNAPSHOT_KERNEL_PARAMETERS+=noresume` stays in `/etc/default/limine`
because snapper sync does not read entry-tool drop-ins. The installer also sets
`SNAPPER_CONFIGS` in `/etc/conf.d/snapper` and adjusts NSS for mDNS; neither has
a suitable independent drop-in.

The target's minimal pacman options include the [package-owned repository
fragment](../packages/README.md#repositories). Installation uses its separate
offline `file://` repository throughout.

Fresh installation explicitly runs `limine-mkinitcpio` after writing machine
settings. In alongside mode, `install.py` mounts the new ESP and writes
the complete machine settings, including `ESP_PATH=/boot` and `SKIP_UEFI=yes`,
before the first pacstrap to defer upstream EFI/kernel hooks. Deferral covers
both package transactions and the explicit initramfs rebuild. Only successful
firmware verification and final registration allow `install.py` to regenerate
settings without deferral. See [alongside installation](../ALONGSIDE.md) for preservation checks
and [the upgrade hook](../desktop/README.md#boot-policy-upgrades) for later
updates.
