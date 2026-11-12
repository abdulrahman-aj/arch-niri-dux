# Packaged desktop integration

`usr/share/arch-niri-dux/niri` contains the upstream Dux Niri integration,
vendored verbatim from the dux-shell release matching the pinned dux-shell.
`make lock` refreshes these files with the archive date. The upstream license
is included. No personal dotfiles are used.

The `arch-niri-dux` package owns shared system configuration. The installer
applies vendor presets; new accounts seed from `/etc/skel` via `useradd -m`.
Package upgrades never touch home directories. Changing a service preset
requires an explicit migration for existing systems
([#54](https://github.com/abdulrahman-aj/arch-niri-dux/issues/54)).

## Migrations

User migrations run once per account at graphical login, in file order.
Markers in `~/.local/state/arch-niri-dux/migrations/` stop the queue on
failure; `/etc/skel` carries the same markers so fresh accounts never execute.
No production scripts ship today. See [the migrations
skill](../.agents/skills/migrations/SKILL.md).

## Niri configuration

Greetd uses our `/etc/greetd/dux.toml`; Arch keeps its stock `config.toml`.
The home entrypoint (`~/.config/niri/config.kdl`, from `/etc/skel`) loads
package defaults, optional relative `dms/*.kdl` includes, package-owned
`tail.kdl`, then `overrides.kdl` last. Put personal settings in
`~/.config/niri/overrides.kdl`; skel seeds it with comments only.
`dms/binds.kdl` starts empty; later bindings replace earlier ones, so disable
a shortcut with `spawn "true"`. Seeded layout and input files stay local, so
Dux Settings keeps controlling them.

## Applications and session defaults

Fresh users get Papers (PDF), Loupe (images), Text Editor, Celluloid (video),
Decibels (audio), Chromium, Dux Files (folders, portal dialogs), Snapshot
(camera) and Calculator. Defaults live in `/etc/xdg/mimeapps.list`;
`~/.config/mimeapps.list` is left alone. `EDITOR`/`VISUAL` stay `nvim`.
Pick the terminal in Dux Settings or `~/.config/xdg-terminals.list`
(Alacritty is factory default). `~/.local/bin` is on PATH for login shells,
user services and Niri spawns. New users start on Dux's upstream bloom preset;
existing `session.json` files are never overwritten.

## Dux defaults and updates

`/etc/skel` seeds each feature into `~/.config/dms/config.d/00-arch-niri-dux-*.json`.
Change them in Dux Settings, in that file, or in a later-sorting `config.d`
file; `settings.json` loads first and loses. An emptied seed stays so restarts
do not undo an opt-out. Seeds never change an existing value; for existing
accounts add a user migration and update the skel seed.

The update seed puts our release button on the main bar, right of the clock,
visible only when Dux's updater reports a newer arch-niri-dux release.
Clicking opens Dux's updater; Update All runs `pacman -Syu`. Move or remove it
in Dux Settings, Bar. The seeded bar copies Dux 2.1 defaults; re-sync it when
a Dux update changes its default bar.

## Firewall and mDNS

`arch-niri-dux-firewall.service` loads `/usr/share/arch-niri-dux/firewall.nft`
into its own table and drops unsolicited inbound traffic. Avahi runs
resolve-only: printer discovery and `.local` names work, nothing is published.
To open a port (e.g. `sshd`), write to `/etc/arch-niri-dux/firewall.d/sshd.nft`
and run `systemctl reload arch-niri-dux-firewall`. A broken include fails the
reload and keeps the running rules; at boot it fails the unit and leaves only
loopback. Stopping the unit deletes its table and leaves inbound open. Guests
(Docker/libvirt) need an `iifname` rule for their bridge. To use another
firewall, disable the unit; with stock `nftables.service` a packet must pass
both tables.
To publish again, add a later-sorting
`/etc/systemd/system/avahi-daemon.service.d/` drop-in resetting `ExecStart=`
to `/usr/bin/avahi-daemon -s`, then allow inbound UDP 5353.

## System policy

System policy belongs to the integration package: mkinitcpio MODULES and HOOKS,
Limine defaults, the root Snapper config, service presets and recovery
drop-ins. To override mkinitcpio, use a later-sorting
`/etc/mkinitcpio.conf.d/*.conf` drop-in and rebuild the initramfs. Systemd
overrides belong under `/etc/systemd/`. See [package
repositories](../packages/README.md#repositories) for update sources.
The `[omarchy]` section sets `Usage = Install Sync`: `pacman -Syu` upgrades
the pinned boot packages from our repository only, and our first-listed
repository wins those names regardless of version, so explicit installs take
the pinned copies too. Omarchy's repo stays available for explicit installs
of packages we don't carry.
`snap-pac` snapshots around transactions; recovery entries use `noresume`.
Timeline snapshots are disabled; number cleanup keeps 10 regular and
5 important snapshots.

## Boot-policy upgrades

Every `arch-niri-dux` upgrade triggers `95-arch-niri-dux-boot-policy.hook`,
which rebuilds initramfs images and boot entries. The hook excludes first
installation; the installer rebuilds explicitly after machine settings and EFI
deferral.

## Locking

The [Dux defaults](#dux-defaults-and-updates) seeder writes lock choices into
`~/.config/dms/config.d/00-arch-niri-dux-lock.json`: lock before suspend and
on idle after 600 seconds on AC or battery. Existing sessions pick them up on
the next DMS start.
