#!/usr/bin/env bash
set -euo pipefail

dux_user=$1
dux_timezone=$2
dux_luks_uuid=$3
dux_btrfs_uuid=$4

ln -sf "/usr/share/zoneinfo/$dux_timezone" /etc/localtime
locale-gen
systemd-machine-id-setup
useradd -m -G wheel -s /bin/bash "$dux_user"
passwd --lock root
install -d -m 0750 /etc/sudoers.d
printf '%%wheel ALL=(ALL:ALL) ALL\n' >/etc/sudoers.d/wheel
chmod 0440 /etc/sudoers.d/wheel
visudo -cf /etc/sudoers.d/wheel

cat >/etc/mkinitcpio.conf <<'EOF'
MODULES=(btrfs)
BINARIES=()
FILES=()
HOOKS=(base systemd autodetect microcode modconf kms keyboard sd-vconsole block sd-encrypt filesystems sd-btrfs-overlayfs fsck)
COMPRESSION="zstd"
EOF

install -d /etc/kernel /etc/default /etc/conf.d /etc/snapper/configs
printf 'rd.luks.name=%s=dux-root root=/dev/mapper/dux-root rootflags=subvol=@ rw\n' "$dux_luks_uuid" >/etc/kernel/cmdline
cat >/etc/default/limine <<EOF
ESP_PATH="/boot"
TARGET_OS_NAME="Arch Niri Dux"
ENABLE_UKI=no
ENABLE_LIMINE_FALLBACK=yes
KERNEL_CMDLINE[default]="rd.luks.name=$dux_luks_uuid=dux-root root=/dev/mapper/dux-root rootflags=subvol=@ rw"
MAX_SNAPSHOT_ENTRIES=12
SNAPPER_CONFIG_NAME="root"
ROOT_SUBVOLUME_PATH="/@"
ROOT_SNAPSHOTS_PATH="/@snapshots"
FS_UUID="$dux_btrfs_uuid"
RESTORE_METHOD=replace
TERMINAL=alacritty
TERMINAL_ARG="-e"
EOF

cat >/etc/snapper/configs/root <<'EOF'
SUBVOLUME="/"
FSTYPE="btrfs"
NUMBER_CLEANUP="yes"
NUMBER_MIN_AGE="0"
NUMBER_LIMIT="5"
NUMBER_LIMIT_IMPORTANT="5"
TIMELINE_CREATE="no"
EOF
printf 'SNAPPER_CONFIGS="root"\n' >/etc/conf.d/snapper

# Bootstrap a bootable baseline before package hooks create recovery snapshots.
limine-install --fallback
limine-mkinitcpio

systemctl enable NetworkManager bluetooth snapper-cleanup.timer limine-snapper-sync.service
systemctl enable systemd-timesyncd.service
systemctl disable snapper-timeline.timer 2>/dev/null || true
systemctl enable power-profiles-daemon.service
systemctl enable getty@tty2.service

# The overlay is ISO data; setup must not depend on preserved executable bits.
bash /usr/local/lib/arch-niri-dux/configure-desktop "$dux_user"

# Restore online Arch repositories only on the installed system.
cat >/etc/pacman.conf <<'EOF'
[options]
HoldPkg = pacman glibc
Architecture = auto
CheckSpace
SigLevel = Required DatabaseOptional
LocalFileSigLevel = Optional
ParallelDownloads = 5
Color

[core]
Include = /etc/pacman.d/mirrorlist

[extra]
Include = /etc/pacman.d/mirrorlist
EOF
printf 'Server = https://geo.mirror.pkgbuild.com/$repo/os/$arch\n' >/etc/pacman.d/mirrorlist
