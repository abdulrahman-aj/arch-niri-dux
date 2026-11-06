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
KERNEL_CMDLINE[default]="$dux_cmdline"
FS_UUID="$dux_btrfs_uuid"
# Snapshot recovery must not resume a normal-root memory image.
SNAPSHOT_KERNEL_PARAMETERS+=noresume
EOF
# Keep package/kernel hooks from registering boot entries before the installer
# has verified that the original firmware entries survived configuration.
if [[ "$dux_defer_efi" == defer-efi ]]; then
    printf '\nSKIP_UEFI=yes\n' >>/etc/default/limine
fi

printf 'SNAPPER_CONFIGS="root"\n' >/etc/conf.d/snapper

# Graphical LUKS prompt with a working text fallback. Arch's stock bgrt theme
# keeps any firmware logo and falls back to spinner, then to text/details when
# KMS is unavailable. Esc shows boot messages; boot failures stay visible
# because only quiet+splash are set. plymouth.enable=0 with
# disablehooks=plymouth restores the plain console prompt.
plymouth-set-default-theme bgrt
# Fresh installs use this explicit rebuild after machine settings and EFI
# deferral are ready, before snap-pac creates recovery snapshots. The integration
# ALPM hook handles upgrades only.
limine-install --fallback
limine-mkinitcpio

systemctl preset NetworkManager.service bluetooth.service snapper-cleanup.timer \
    limine-snapper-sync.service systemd-timesyncd.service power-profiles-daemon.service \
    getty@tty2.service cups.service avahi-daemon.service snapper-timeline.timer greetd.service

# Resolve printer .local names before unicast DNS, preserving other NSS sources.
if ! grep -Eq '^[[:space:]]*hosts:.*mdns' /etc/nsswitch.conf 2>/dev/null; then
    if grep -Eq '^[[:space:]]*hosts:.*(^|[[:space:]])resolve([[:space:]]|$)' /etc/nsswitch.conf; then
        sed -i -E '/^[[:space:]]*hosts:/ s/(^|[[:space:]])resolve([[:space:]]|$)/\1mdns_minimal [NOTFOUND=return] resolve\2/' /etc/nsswitch.conf
    elif grep -Eq '^[[:space:]]*hosts:.*(^|[[:space:]])dns([[:space:]]|$)' /etc/nsswitch.conf; then
        sed -i -E '/^[[:space:]]*hosts:/ s/(^|[[:space:]])dns([[:space:]]|$)/\1mdns_minimal [NOTFOUND=return] dns\2/' /etc/nsswitch.conf
    fi
fi

# Account initialization is separate from package updates and system policy.
arch-niri-dux-migrate baseline
runuser -u "$dux_user" -- env -u XDG_CONFIG_HOME -u XDG_STATE_HOME -u XDG_DATA_HOME \
    bash /usr/lib/arch-niri-dux/initialize-user
systemctl --global preset dms.service dsearch.service dcal.service dfiles.service dux-application-themes.service
systemd-tmpfiles --create /usr/lib/tmpfiles.d/dms-greeter.conf
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

Include = /etc/pacman.d/arch-niri-dux.conf
EOF
[[ -s /usr/share/pacman/keyrings/arch-niri-dux.gpg ]] || {
    echo 'Integration release keyring is missing; refusing a system without its update source.' >&2
    exit 1
}
pacman-key --populate arch-niri-dux
