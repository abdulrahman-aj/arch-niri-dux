# Installer integration

`install.py` is the shared wizard and unattended VM installation engine. The build
copies this directory to `/usr/local/lib/arch-niri-dux/installer` on the live ISO,
and exposes `arch-niri-install` as its entry point.

The live ISO must provide:

- `/run/archiso/bootmnt/repo`: a local pacman repository with the complete target closure;
- `/usr/local/share/arch-niri-dux/packages.target`: one package name per line;
- `/usr/local/share/arch-niri-dux/desktop`: a target-root overlay containing upstream
  Niri/Dux defaults and greeter configuration. An optional executable
  `/usr/local/lib/arch-niri-dux/configure-desktop USERNAME` runs inside the installed
  system after the overlay is copied, to create user configuration and enable
  the appropriate greeter. It must not download anything.

Partitioning uses a 4 GiB FAT32 ESP and a LUKS2 Btrfs root. Independent root,
home, logs, package cache, and snapshots subvolumes keep recovery away from home.
The kernel and initramfs are managed by upstream Limine tools. Snapshot boot
uses their systemd Btrfs overlay hook; restoration uses their `replace` method.

Automation uses a private mode-0600 JSON file containing Settings fields and
`--confirm-disk /dev/vda`. It exercises the same validation and installation
engine as the wizard. Never include real personal passwords in fixtures or logs.
