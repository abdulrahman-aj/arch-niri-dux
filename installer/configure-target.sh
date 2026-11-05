#!/usr/bin/env bash
set -euo pipefail

dux_user=$1
dux_timezone=$2
dux_luks_uuid=$3
dux_btrfs_uuid=$4
dux_resume_offset=${5:-}
dux_defer_efi=${6:-}

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
HOOKS=(base systemd autodetect microcode modconf kms keyboard sd-vconsole block plymouth sd-encrypt filesystems sd-btrfs-overlayfs fsck)
COMPRESSION="zstd"
EOF

install -d /etc/kernel /etc/default /etc/conf.d /etc/snapper/configs
dux_cmdline="rd.luks.name=$dux_luks_uuid=dux-root root=/dev/mapper/dux-root rootflags=subvol=@ rw quiet splash"
if [[ -n "$dux_resume_offset" ]]; then
    [[ "$dux_resume_offset" =~ ^[0-9]+$ ]]
    dux_cmdline+=" resume=/dev/mapper/dux-root resume_offset=$dux_resume_offset"
else
    install -d /etc/systemd/sleep.conf.d
    printf '[Sleep]\nAllowHibernation=no\n' >/etc/systemd/sleep.conf.d/60-hibernation.conf
fi
printf '%s\n' "$dux_cmdline" >/etc/kernel/cmdline
cat >/etc/default/limine <<EOF
ESP_PATH="/boot"
TARGET_OS_NAME="Arch Niri Dux"
ENABLE_UKI=no
ENABLE_LIMINE_FALLBACK=yes
KERNEL_CMDLINE[default]="$dux_cmdline"
MAX_SNAPSHOT_ENTRIES=12
SNAPPER_CONFIG_NAME="root"
ROOT_SUBVOLUME_PATH="/@"
ROOT_SNAPSHOTS_PATH="/@snapshots"
FS_UUID="$dux_btrfs_uuid"
RESTORE_METHOD=replace
TERMINAL=xdg-terminal-exec
TERMINAL_ARG="-e"
EOF
# Keep package/kernel hooks from registering boot entries before the installer
# has verified that the original firmware entries survived configuration.
if [[ "$dux_defer_efi" == defer-efi ]]; then
    printf '\nSKIP_UEFI=yes\n' >>/etc/default/limine
fi

# Recovery must never resume a normal-root memory image over an older snapshot.
printf '\nSNAPSHOT_KERNEL_PARAMETERS+=noresume\n' >>/etc/limine-snapper-sync.conf
for dux_sleep_unit in systemd-hibernate systemd-hybrid-sleep systemd-suspend-then-hibernate; do
    install -d "/etc/systemd/system/$dux_sleep_unit.service.d"
    printf '[Unit]\nConditionKernelCommandLine=!noresume\n' \
        >"/etc/systemd/system/$dux_sleep_unit.service.d/60-recovery.conf"
done

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

# Graphical LUKS prompt with a working text fallback. Arch's stock bgrt theme
# keeps any firmware logo and falls back to spinner, then to text/details when
# KMS is unavailable. Esc shows boot messages; boot failures stay visible
# because only quiet+splash are set. plymouth.enable=0 with
# disablehooks=plymouth restores the plain console prompt.
plymouth-set-default-theme bgrt
# Let the splash quit before the greeter so the login screen is never hidden.
install -d /etc/systemd/system/greetd.service.d
printf '[Unit]\nAfter=plymouth-quit.service plymouth-quit-wait.service\n' \
    >/etc/systemd/system/greetd.service.d/10-plymouth.conf

# Bootstrap a bootable baseline before package hooks create recovery snapshots.
limine-install --fallback
limine-mkinitcpio

systemctl enable NetworkManager bluetooth snapper-cleanup.timer limine-snapper-sync.service
systemctl enable systemd-timesyncd.service
systemctl disable snapper-timeline.timer 2>/dev/null || true
systemctl enable power-profiles-daemon.service
systemctl enable getty@tty2.service

# Dux uses localhost:631, so start CUPS itself as well as its Unix socket.
# Avahi supplies printer discovery; keep stock local-only CUPS configuration.
systemctl enable cups.service avahi-daemon.service

# Resolve printer .local names before unicast DNS, preserving other NSS sources.
if ! grep -Eq '^[[:space:]]*hosts:.*mdns' /etc/nsswitch.conf 2>/dev/null; then
    if grep -Eq '^[[:space:]]*hosts:.*(^|[[:space:]])resolve([[:space:]]|$)' /etc/nsswitch.conf; then
        sed -i -E '/^[[:space:]]*hosts:/ s/(^|[[:space:]])resolve([[:space:]]|$)/\1mdns_minimal [NOTFOUND=return] resolve\2/' /etc/nsswitch.conf
    elif grep -Eq '^[[:space:]]*hosts:.*(^|[[:space:]])dns([[:space:]]|$)' /etc/nsswitch.conf; then
        sed -i -E '/^[[:space:]]*hosts:/ s/(^|[[:space:]])dns([[:space:]]|$)/\1mdns_minimal [NOTFOUND=return] dns\2/' /etc/nsswitch.conf
    fi
fi

# Account initialization is separate from package updates and system policy.
runuser -u "$dux_user" -- env -u XDG_CONFIG_HOME -u XDG_STATE_HOME -u XDG_DATA_HOME \
    bash /usr/lib/arch-niri-dux/initialize-user
systemctl --global enable dms.service dsearch.service dcal.service dfiles.service dux-application-themes.service
systemd-tmpfiles --create /usr/lib/tmpfiles.d/dms-greeter.conf
# Calendar credentials use the Secret portal; unlock its keyring at login.
for dux_pam_line in 'auth optional pam_gnome_keyring.so' 'session optional pam_gnome_keyring.so auto_start'; do
    if ! grep -Fxq "$dux_pam_line" /etc/pam.d/greetd; then
        printf '\n%s\n' "$dux_pam_line" >>/etc/pam.d/greetd
    fi
done
systemctl enable greetd

# The installer stays offline; enable online repositories only on the target.
pacman-key --populate omarchy
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

[omarchy]
Server = https://pkgs.omarchy.org/stable/$arch
EOF
# Pacman expands these placeholders on the installed system.
# shellcheck disable=SC2016
printf 'Server = https://geo.mirror.pkgbuild.com/$repo/os/$arch\n' >/etc/pacman.d/mirrorlist
[[ -s /usr/share/pacman/keyrings/arch-niri-dux.gpg ]] || {
    echo 'Integration release keyring is missing; refusing a system without its update source.' >&2
    exit 1
}
pacman-key --populate arch-niri-dux
printf '\nInclude = /etc/pacman.d/arch-niri-dux.conf\n' >>/etc/pacman.conf
