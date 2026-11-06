# Packaged desktop integration

`usr/share/arch-niri-dux/niri` contains the upstream Dux v2.1.0 Niri integration,
vendored verbatim. `packages/desktop-sources.json` records source URLs and hashes.
The upstream license is included. No personal dotfiles are used.

The `arch-niri-dux` package owns these system files. The installer enables the
Dux services and runs `/usr/lib/arch-niri-dux/initialize-user` as the desktop
user to seed only missing settings. Package upgrades never replay account or
user initialization. Dux Greeter starts Niri. Dux creates its own shell settings.
The Greetd service selects our `/etc/greetd/dux.toml`. Arch retains ownership of
its separate stock `config.toml`. Pacman preserves local edits to `dux.toml`.

Package construction generates Niri's stock configuration and terminal-resolved
bindings in `/usr/share/arch-niri-dux/niri/defaults.kdl`. The user entrypoint includes these
defaults before local `dms/*.kdl` files. `dms/binds.kdl` starts empty and holds
only overrides. Dux generates `dms/binds-next.kdl` for the installed Niri. The
entrypoint includes it before personal bindings. Keep that generated file local.
Later bindings replace earlier ones, so a disabled shortcut
can override its stock action with `spawn "true"`. This still consumes the key.
An optional `recent-windows.kdl` loads before `dms/binds.kdl` for hand-managed
MRU preferences. This avoids [Dux's shortcut-save bugs, still present in 2.1](https://github.com/abdulrahman-aj/arch-niri-dux/issues/33)
until a fixed package is available. Generated `dms/alttab.kdl` controls appearance.
Rerunning user initialization preserves existing user Niri files. Generated
layout/input files remain local, so Dux settings continue to control them.

## Personal Niri configuration

Put personal overrides in `~/.config/niri/overrides.kdl`. The entrypoint includes it
last, after the packaged defaults and Dux-generated settings. Initialization seeds
a comment-only file explaining its purpose when it is absent. Existing contents
and symlinks are preserved. Dotfile tools such as Stow can manage this file
without taking ownership of `config.kdl` or Dux-generated files.

For example, to make tiled columns full width while leaving floating dialogs
at their application-requested sizes:

```kdl
layout {
    default-column-width { proportion 1.0; }
}
```

You can also use relative `include` directives inside `overrides.kdl` to split your
configuration into files. Paths resolve from `~/.config/niri/`, including when
`overrides.kdl` is a symlink. Settings in `overrides.kdl` take precedence over Dux's
settings, so only override the settings you intend to manage yourself.

On existing packaged installations, the next Niri login migrates an unchanged
stock `config.kdl` from releases `0.2.0-1` through `0.2.0-4`. The session runs
`/usr/lib/arch-niri-dux/migrate-niri-overrides` as the desktop user. It backs up
the original to `config.kdl.before-overrides`, seeds a missing `overrides.kdl`,
and atomically replaces the stock entrypoint. Existing overrides are preserved.

Customized entrypoints and symlinks are left alone. To enable the extension
point in one of those, add this line at the end of your entrypoint:

```kdl
include optional=true "overrides.kdl"
```

To apply the stock-entrypoint migration in a running session or retry a failure,
run `/usr/lib/arch-niri-dux/migrate-niri-overrides` as your desktop user. Login
attempts log through `systemd-cat`; inspect them with
`journalctl -t arch-niri-dux-niri`. The helper retries safely and never
overwrites an unrelated backup. A package update does not restart your session.

## Desktop defaults

New users start on Dux's upstream "bloom" material preset. The seed lives in
`~/.local/state/dms/session.json` and is only created when absent. Existing
`session.json` files are never overwritten, so chosen wallpapers are preserved.
Dux provides the preset assets. The installer does not edit the vendored Niri
files for this default.

Papers uses `org.gnome.Papers.desktop` and is the default PDF viewer in the factory
`/etc/xdg/mimeapps.list`. Fresh users also get Loupe for common images,
GNOME Text Editor for plain text and Markdown, Celluloid for common video,
Decibels for common audio, Chromium for the browser and Dux Files for folders.
User initialization preserves `~/.config/mimeapps.list`, so user choices
survive reruns. GNOME Calculator is installed standalone.
`EDITOR`/`VISUAL` remain `nvim`. Snapshot is the camera app and uses
`org.gnome.Snapshot.desktop`. Qt PDF support in `qt6-webengine` enables PDF preview inside Dux
Files, while Papers remains the default document app. These apps come from
signed Arch packages. `kimageformats` adds AVIF, HEIF and JPEG XL decoding for Qt
apps. `qt6-imageformats` supplies WebP, TIFF and other Qt decoders.
`smartmontools` supports System Monitor's drive-health view. `fprintd` supplies
the fingerprint backend; enrollment and authentication require supported hardware and user setup. The live ISO remains terminal-only.

CUPS and Avahi start for Dux's native driverless printer settings. CUPS keeps
its stock localhost-only listeners. GVFS supplies Android, Apple-device
and camera file access. SMB shares remain an optional install. Qt Multimedia
and cava support Dux's own effects without changing its settings.

Dux controls external-monitor brightness through I2C/DDC. `i2c-tools` loads
`i2c-dev` at boot. A udev rule grants the active local session access to I2C
buses under display controllers. Reboot after installing this update; monitor
and connection support still determine whether DDC works.
NetworkManager uses `dnsmasq` for hotspot sharing. The standalone dnsmasq service
is not enabled.

Matugen supplies Dux's colors and system light/dark preference. A first-login
service applies upstream's GTK integration and selects the matching GTK theme
and light/dark preference once. Dux keeps subsequent choices. The setup preserves
a pre-existing custom GTK theme. Qt uses upstream's GTK passthrough.
Terminal shortcuts, user services and recovery tools use `xdg-terminal-exec`.
Alacritty is the factory preference in `/etc/xdg/xdg-terminals.list`. Users can
select another installed terminal through Dux or `~/.config/xdg-terminals.list`.
Dux Files handles folders and portal file dialogs, with GTK as fallback. The
GNOME portal handles screen sharing. Nautilus remains its Arch dependency.

Logind suspends on lid-close when undocked and permits docked use. Niri already
controls the built-in panel according to the lid switch. Intermittent wakeups
still need [physical laptop evidence](https://github.com/abdulrahman-aj/arch-niri-dux/issues/48): `journalctl -b -u systemd-logind -u
systemd-suspend` and `systemd-inhibit --list` show sleep events and inhibitors.

The session adds `~/.local/bin` to PATH for POSIX login shells, systemd user
services and Niri spawns. Login shells read `/etc/profile.d/dux-localbin.sh`.
User services read `/usr/lib/environment.d/90-dux.conf` and per-user overrides.
Greetd's default profile sourcing passes the login PATH to the user session.
The greeter still uses its supported `--command niri` compositor selector.
Each context preserves system and Nix PATH entries. No mise shims are added.
The `session-path` stage of `make verify` checks the user-service environment,
login shell and a real Niri-spawned wrapper without personal dotfiles.
