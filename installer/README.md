# Installer integration

`install.py` is the shared wizard and unattended VM installation engine. The build
copies this directory to `/usr/local/lib/arch-niri-dux/installer` on the live ISO,
and exposes `arch-niri-install` as its entry point.

The live ISO must provide `fzf` for selection menus, plus:

- `/run/archiso/bootmnt/repo`: a local pacman repository with the complete target closure;
- `/usr/local/share/arch-niri-dux/packages.target`: one package name per line;
- `arch-niri-dux` and `arch-niri-dux-baseline` in the offline repository. Pacman
  owns the shared desktop integration. The baseline tracks the selected package
  list. It installs after initial boot setup so `snap-pac` starts with a bootable
  system. `/usr/lib/arch-niri-dux/initialize-user` runs as the new desktop user
  to seed absent settings. Machine initialization and service enablement remain
  in `configure-target.sh`. None of these steps downloads anything.

Partitioning uses a 4 GiB FAT32 ESP and a LUKS2 Btrfs root. Independent root,
home, logs, package cache, and snapshots subvolumes keep recovery away from home.
Upstream Limine tools manage the kernel and initramfs. Snapshot boot
uses their systemd Btrfs overlay hook, and restoration uses their `replace` method.

Swap choices are Auto, a custom size, or none. Auto uses RAM rounded up to GiB
plus 2 GiB.
An enabled swapfile lives in its own `@swap` subvolume inside the encrypted
filesystem. Btrfs computes its physical resume offset. Recovery entries use
`noresume` and block hibernation so an old snapshot cannot resume a different
system's memory image. Hibernate/resume passed on the Zenbook. See
[verification](../VERIFICATION.md) for the evidence and remaining hardware checks.

Boot uses Plymouth's stock `bgrt` theme with `quiet splash`. The disk
passphrase appears in a graphical box instead of inline boot logs, with an
automatic text fallback when KMS is unavailable. `sd-encrypt` retries a
mistyped passphrase. Press Esc during boot to show messages. Boot failures
stay visible because only `quiet splash` quiets the console. To force the
plain console prompt, append `plymouth.enable=0` to the kernel cmdline.
`disablehooks=plymouth` optionally bypasses the initramfs hook as well.

Acceptance after rebuilding with `make iso`:

- `make test` runs source and build tests, including fallback-setting preservation.
- `make verify ARGS='--directory out/verify-plymouth'` runs full VM acceptance:
  keyboard unlock and wrong-passphrase retry with `02-graphical-unlock-*` screenshots,
  `plymouth-config`/`plymouth-status` probes, snapshot recovery, and a
  serial-free, screenshot-qualified `plymouth.enable=0` text-fallback roundtrip
  that restores the graphical password screen afterward.
- `make verify-hibernate ARGS='--directory out/verify-plymouth'` checks that encrypted
  swap resume still works through the splash.

Laptop backlight/BGRT logo behavior is hardware-specific. The graphical unlock
check requires screenshots and keyboard input. Serial unlock alone does not
qualify its appearance. Secure Boot remains deferred.

Automation uses a private mode-0600 JSON file containing Settings fields and
`--confirm-disk /dev/vda`. It exercises the same validation and installation
engine as the wizard. Never include real personal passwords in fixtures or logs.
The optional `swap_gib` field accepts `null` for Auto, `0` for disabled, or a
positive integer. Both modes check capacity before erasing. The interactive
wizard shows the resolved size and defaults its final erase confirmation to
Cancel. Installation timing starts when `install()` begins.
Auto swap needs roughly RAM plus 31 GiB of disk space. Smaller disks can use a
custom size or disable swap, while the base installation requires 32 GiB.
