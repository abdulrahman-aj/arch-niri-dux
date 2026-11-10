# Packaged desktop integration

`usr/share/arch-niri-dux/niri` contains the upstream Dux v2.1.0 Niri integration,
vendored verbatim. `packages/desktop-sources.json` records source URLs and hashes.
The upstream license is included. No personal dotfiles are used.

The `arch-niri-dux` package owns shared system configuration. The installer
applies vendor presets for Dux services and runs
`/usr/lib/arch-niri-dux/initialize-user` as the desktop user to seed only
missing settings. Package upgrades never replay account or user initialization.
Changing a service preset requires an explicit migration for existing systems
([#54](https://github.com/abdulrahman-aj/arch-niri-dux/issues/54)); upgrades do
not reapply presets. Dux Greeter starts Niri. Dux creates its own shell
settings.

## Migrations

Package migrations run offline after pacman releases its database lock, and at
boot. System and user queues keep separate completion records. The desktop
continues starting if a migration fails; inspect `arch-niri-dux-migrate status`
and retry with `arch-niri-dux-migrate run`. Use sudo for the system queue.
Logs are in `journalctl -u arch-niri-dux-migrate.service` and
`journalctl --user -u arch-niri-dux-migrate.service`; user-session failures also
send a desktop notification. Completion records include intentional skip reasons.
System records roll back with root snapshots; user records stay with home.
Downgrades ignore unknown records and never reverse changes. Authoring and
release qualification are covered in [the migrations skill](../.agents/skills/migrations/SKILL.md).

## Niri configuration

The Greetd service selects our `/etc/greetd/dux.toml`. Arch retains ownership of
its separate stock `config.toml`. Pacman preserves local edits to `dux.toml`.

Package construction generates Niri's stock configuration in `/usr/share/arch-niri-dux/niri/defaults.kdl`
and terminal-resolved bindings in `/usr/share/arch-niri-dux/niri/binds.kdl`. The home entrypoint (`niri-user.kdl`
installed as `~/.config/niri/config.kdl`) loads package defaults, selected relative
optional `dms/*.kdl` includes, package-owned `tail.kdl`
(session environment and file-dialog rules), then optional `overrides.kdl` last.
Dux 2.1 ignores `~` includes. Relative home includes allow shortcut editing.
Put personal Niri settings in `~/.config/niri/overrides.kdl`; initialization
seeds comments only and preserves existing files and symlinks. Package updates
can change defaults and session policy without editing home. `dms/binds.kdl` starts empty and holds
only overrides. Dux generates `dms/binds-next.kdl` for the installed Niri. The
entrypoint includes it before personal bindings. Keep that generated file local.
Later bindings replace earlier ones, so a disabled shortcut
can override its stock action with `spawn "true"`. This still consumes the key.
Generated `dms/alttab.kdl` controls appearance.
Rerunning user initialization preserves existing user Niri files. Generated
layout/input files remain local, so Dux settings continue to control them.

## Dux shell source

The vendored files above track the `dux-shell` pin in
[`packages/closure.lock.json`](../packages/closure.lock.json). Fetch the
matching upstream source into `out/` with:

```sh
v=$(python3 -c "import json;print(next(p['version'] for p in json.load(open('packages/closure.lock.json'))['packages'] if p['name']=='dux-shell').split('-')[0])") && mkdir -p out && curl -L -o "out/dux-shell-$v.tar.gz" "https://github.com/duxlabs-dev/dux-shell/archive/refs/tags/v$v.tar.gz"
```

## Applications and session defaults

New users start on Dux's upstream "bloom" material preset. The seed lives in
`~/.local/state/dms/session.json` and is only created when absent. Existing
`session.json` files are never overwritten, so chosen wallpapers are preserved.
Dux provides the preset assets. The installer does not edit the vendored Niri
files for this default.

Papers uses `org.gnome.Papers.desktop` and is the default PDF viewer in the
factory `/etc/xdg/mimeapps.list`. Fresh users also get Loupe for common images,
GNOME Text Editor for plain text and Markdown, Celluloid for common video,
Decibels for common audio, Chromium for the browser and Dux Files for folders.
User initialization leaves `~/.config/mimeapps.list` untouched. GNOME Calculator
is installed standalone. `EDITOR`/`VISUAL` remain `nvim`. Snapshot is the camera
app and uses `org.gnome.Snapshot.desktop`. Qt PDF support in `qt6-webengine`
enables PDF preview inside Dux Files, while Papers remains the default document
app. The document and media viewers come from signed Arch packages.
`kimageformats` adds AVIF, HEIF and JPEG XL decoding for Qt apps.
`qt6-imageformats` supplies WebP, TIFF and other Qt decoders. `smartmontools`
supports System Monitor's drive-health view. `fprintd` supplies the fingerprint
backend; enrollment and authentication require supported hardware and user
setup. The live ISO remains terminal-only.

CUPS and Avahi start for Dux's native driverless printer settings. CUPS keeps
its stock localhost-only listeners. GVFS supplies Android, Apple-device
and camera file access. SMB shares remain an optional install. Qt Multimedia
and cava support Dux's own effects without changing its settings.

Dux controls external-monitor brightness through I2C/DDC. `i2c-tools` loads
`i2c-dev` at boot. A udev rule grants the active local session access to I2C
buses under display controllers. Monitor and connection support determine
whether DDC works. NetworkManager uses `dnsmasq` for hotspot sharing. The
standalone dnsmasq service is not enabled.

Matugen supplies Dux's colors and system light/dark preference. A first-login
service applies upstream's GTK integration and selects the matching GTK theme
and light/dark preference once. Dux keeps subsequent choices. The setup preserves
a pre-existing custom GTK theme. Qt uses upstream's GTK passthrough.
Terminal shortcuts, user services and recovery tools use `xdg-terminal-exec`.
Alacritty is the factory preference in `/etc/xdg/xdg-terminals.list`. Users can
select another installed terminal through Dux or `~/.config/xdg-terminals.list`.
Dux Files handles folders and portal file dialogs, with GTK as fallback. The
GNOME portal handles screen sharing. Nautilus remains its Arch dependency.

Logind suspends on lid-close when undocked and permits docked use. During its
30-second boot/resume holdoff, a Niri hook locks Dux and powers off the monitors,
even if Dux is already locked. Opening the lid powers the monitors back on at
the lock screen. The hook skips docked use and suspend already in progress;
logind still controls suspend timing.

The session adds `~/.local/bin` to PATH for POSIX login shells, systemd user
services and Niri spawns. Login shells read `/etc/profile.d/dux-localbin.sh`.
User services read `/usr/lib/environment.d/90-dux.conf` and per-user overrides.
Greetd's default profile sourcing passes the login PATH to the user session.
The greeter still uses its supported `--command niri` compositor selector.
Each context preserves system and Nix PATH entries. No mise shims are added.
The `session-path` stage of `make verify` checks the user-service environment,
login shell and a real Niri-spawned wrapper without personal dotfiles.

## Dux defaults and updates

Before DMS starts, its packaged service drop-in runs
`initialize-dux-settings.py` for new and existing accounts. Each feature seeds
the keys missing from `settings.json` and every `config.d/*.json` file into its
own `~/.config/dms/config.d/00-arch-niri-dux-*.json` file, once. Dux saves
later changes back to that file, so change these settings in Dux Settings, in
that file or in a later-sorting `config.d` file; `settings.json` loads first
and loses to them. An emptied seed file stays in place so restarts don't undo
an opt-out.

The update seed puts our release button on the main bar, right of the clock. It
appears only when Dux's own updater reports a newer arch-niri-dux release; any
other pending package alone keeps it hidden. The button is a system plugin that
holds Dux's updater alive, so its background checks keep running without the
built-in Software Updates widget, and `dms ipc call systemupdater updatestatus`
keeps reporting state. Dux's server checks every 6 hours, with no check at
login. It schedules from its last check, saved in
`~/.cache/dms/sysupdate.json`, so logins and reboots don't restart the
interval; a new account checks a few minutes after its first login. Failed
checks, such as while offline, stay silent and retry every 5 minutes. Clicking
the button opens Dux's updater; its Update All runs the same `pacman -Syu`
that installs our packages and triggers migrations. A bar layout saved before
the seed ran (`barConfigs` in a user file) is kept and gets no button. The
seeder also enables the plugin in `~/.config/dms/plugin_settings.json` unless
a choice is already recorded there. Move, remove or add the button in Dux
Settings, Bar; Software Updates shows the 6-hour interval as a custom interval.

Seeds never change a value that already exists. To change a shipped default for
existing accounts, add a user migration that rewrites the key only where it
still holds the old default, and update the seed for new accounts. The seeded
bar copies Dux 2.1's default widget lists; re-sync `MAIN_BAR` when a Dux update
changes its default bar.

## Firewall and mDNS

`arch-niri-dux-firewall.service` loads `/usr/share/arch-niri-dux/firewall.nft`
into its own nftables table, leaving Tailscale, Docker and libvirt tables
alone. It drops unsolicited inbound traffic. It accepts replies to outbound
connections, loopback, the ICMPv6 and DHCPv6 traffic IPv6 needs, IGMP
membership queries, mDNS responses, DHCP and DNS requests for NetworkManager hotspots, and everything
arriving on `tailscale0`. Avahi runs resolve-only from
`/usr/share/arch-niri-dux/avahi-daemon.conf`: printer discovery and `.local`
names work, but the host advertises nothing.

The ruleset includes `/etc/arch-niri-dux/firewall.d/*.nft`, which upgrades
leave alone. To open a port, for example after enabling `sshd`, write
`add rule inet arch_niri_dux input tcp dport 22 accept` to
`/etc/arch-niri-dux/firewall.d/sshd.nft` and run `systemctl reload
arch-niri-dux-firewall`. A broken include fails the reload and keeps the
running rules. At start or boot it fails the unit and leaves a table that
drops everything but loopback and replies, so new links get no DHCP until
the include is fixed. Docker or libvirt guests that must reach host
services need an `iifname` rule there for their bridge. Stopping the unit
deletes its table and leaves inbound traffic open. To use another firewall,
disable the unit; the stock `nftables.service` adds its own table, and a
packet must pass both. To publish again, add a later-sorting
`/etc/systemd/system/avahi-daemon.service.d/` drop-in that resets `ExecStart=`
to `/usr/bin/avahi-daemon -s`, then allow inbound UDP 5353.

## System policy

System policy belongs to the integration package: mkinitcpio's complete MODULES
and HOOKS arrays, Limine defaults, the root Snapper config, service presets,
Plymouth/recovery service drop-ins, and the greetd PAM service. To override
mkinitcpio policy, use a later-sorting `/etc/mkinitcpio.conf.d/*.conf` drop-in
and rebuild the initramfs. Pacman backs up the shipped mkinitcpio and Snapper
configs. Systemd overrides belong under `/etc/systemd/`, ahead of vendor files.

Greetd selects `/usr/lib/pam.d/arch-niri-dux` for authentication, including the
stock local-login stack and GNOME Keyring password/session integration. Its
greeter session uses the same stack. See [package
repositories](../packages/README.md#repositories) for online update sources.
The repository order is arch-niri-dux, core, extra, then omarchy. Keeping our
repository first prevents other repositories from shadowing our packages.

Do not run `dms-greeter sync` on arch-niri-dux. A full sync recursively grants
the greeter account read access to parts of the home directory, including files
unrelated to the login theme, and leaves the greeter cache group-writable. It
also targets upstream's `/etc/greetd/config.toml` rather than our
`/etc/greetd/dux.toml`, so greeter setup stays managed by this package and full
sync is unsupported.

`snap-pac` creates snapshots around package transactions. Recovery entries use
`noresume` and block hibernation to prevent resuming a normal-root memory image
over an older snapshot. Timeline snapshots are disabled. Number cleanup keeps 5
regular and 5 important snapshots, with `snapper-cleanup.timer` enabled. Pre-0.3
systems need manual repair.

## Boot-policy upgrades

Every `arch-niri-dux` upgrade triggers
`95-arch-niri-dux-boot-policy.hook`, which runs `limine-mkinitcpio` to rebuild
active initramfs images and boot entries. Pacman Path triggers match upgrades of
the package containing the target files, even when those files are unchanged.
A transaction that also changes the kernel may run both this hook and the upstream kernel hook; both use the same
serialized upstream rebuild implementation.

The hook excludes first installation. The
[installer](../installer/README.md#machine-configuration) rebuilds explicitly
after writing machine settings and EFI deferral.

## Locking and calendar transport

The [Dux defaults](#dux-defaults-and-updates) seeder writes the lock choices
into `~/.config/dms/config.d/00-arch-niri-dux-lock.json`. The defaults lock
before suspend, retain the upstream logind lock integration, and lock on idle
after 600 seconds on AC or battery. No administrator policy locks these
controls. Existing running sessions acquire the defaults on the next DMS start.

This protects ordinary logind suspend, including normal lid-close suspend.
Issue [#48](https://github.com/abdulrahman-aj/arch-niri-dux/issues/48) covers
logind ignoring a lid close during its 30-second post-resume holdoff. Its
separate Niri hook remains necessary for that window.

dcal's unauthenticated loopback HTTP API remains enabled. Dux's calendar
backend uses its Unix socket, but Dux Calendar 2.1.0 also uses the HTTP server
for Google/Microsoft OAuth redirects. Disabling it prevents online account
setup (`core/internal/ipc/accounts.go`, `redirectURLForHost`). S5-02 needs an
upstream change to separate OAuth callbacks from the data API or authenticate
the data API; a service environment flag cannot preserve full functionality.
