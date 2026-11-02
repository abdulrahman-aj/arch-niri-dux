# Installer integration

`install.py` is the shared wizard and unattended VM installation engine. The build
copies this directory to `/usr/local/lib/arch-niri-dux/installer` on the live ISO,
and exposes `arch-niri-install` as its entry point.

The live ISO must provide `fzf` for selection menus, plus:

- `/run/archiso/bootmnt/repo`: a local pacman repository with the complete target closure;
- `/usr/local/share/arch-niri-dux/packages.target`: one package name per line;
- `/usr/local/share/arch-niri-dux/desktop`: a target-root overlay containing upstream
  Niri/Dux defaults and greeter configuration. The required Bash script
  `/usr/local/lib/arch-niri-dux/configure-desktop USERNAME` runs inside the installed
  system after the overlay is copied, to create user configuration and enable
  the appropriate greeter. It must not download anything.

Partitioning uses a 4 GiB FAT32 ESP and a LUKS2 Btrfs root. Independent root,
home, logs, package cache, and snapshots subvolumes keep recovery away from home.
The kernel and initramfs are managed by upstream Limine tools. Snapshot boot
uses their systemd Btrfs overlay hook; restoration uses their `replace` method.

Swap choices are Auto (RAM rounded up to GiB plus 2 GiB), a custom size, or none.
An enabled swapfile lives in its own `@swap` subvolume inside the encrypted
filesystem. Btrfs computes its physical resume offset. Recovery entries use
`noresume` and block hibernation so an old snapshot cannot resume a different
system's memory image. Hibernate/resume passed on the Zenbook; see
[verification](../VERIFICATION.md) for the evidence and remaining hardware checks.

Boot uses Plymouth's stock `bgrt` theme with `quiet splash`: the disk
passphrase appears in a graphical box instead of inline boot logs, with an
automatic text fallback when KMS is unavailable. `sd-encrypt` retries a
mistyped passphrase. Press Esc during boot to show messages; boot failures
stay visible because only `quiet splash` quiets the console. To force the
plain console prompt, append `plymouth.enable=0` (and
`disablehooks=plymouth` for the initramfs hook) to the kernel cmdline.

Acceptance (parent runs the VM serially; this change adds no `make iso` run):

- `make test` — source and build tests, including the Plymouth hook order,
  `quiet splash`, signed closure addition and login-retry coverage.
- `make verify ARGS='--directory out/verify-plymouth'` — full VM run:
  wrong-passphrase retry with `02-boot-plymouth-prompt/retry` screenshots,
  `plymouth-config`/`plymouth-status` probes, snapshot recovery, and a
  `plymouth.enable=0` text-fallback roundtrip that restores the splash.
- `make verify-hibernate ARGS='--directory out/verify-plymouth'` — encrypted
  swap resume still works through the splash.

Limitations: VM-only qualification here; laptop backlight/BGRT logo behavior
is hardware-specific and still needs a physical boot check. QEMU screenshots
are best-effort (VirGL can report `no surface`); the serial passphrase prompt
remains the authoritative automation path. No Secure Boot or key changes.

Automation uses a private mode-0600 JSON file containing Settings fields and
`--confirm-disk /dev/vda`. It exercises the same validation and installation
engine as the wizard. Never include real personal passwords in fixtures or logs.
The optional `swap_gib` field accepts `null` for Auto, `0` for disabled, or a
positive integer. Both modes check capacity before erasing; the interactive
wizard shows the resolved size and defaults its final erase confirmation to
Cancel. Completion time starts at that final confirmation.
Auto swap needs roughly RAM plus 31 GiB of disk space; smaller disks can use a
custom size or disable swap, while the base installation requires 32 GiB.
