# Packaged desktop integration

`usr/share/arch-niri-dux/niri` contains the upstream Dux v2.0.0 Niri integration,
vendored verbatim. `packages/desktop-sources.json` records source URLs and hashes;
the upstream license is included. No personal dotfiles are used.

The `arch-niri-dux` package owns these system files. The installer enables the
Dux services and runs `/usr/lib/arch-niri-dux/initialize-user` as the desktop
user to seed only missing settings. Package upgrades never replay account or
user initialization. Dux Greeter starts Niri; Dux creates its own shell settings.
The Greetd service selects our `/etc/greetd/dux.toml`; Arch retains ownership of
its separate stock `config.toml`. Pacman preserves local edits to `dux.toml`.

Niri's stock configuration and terminal-resolved bindings are exposed as
`/usr/share/arch-niri-dux/niri/defaults.kdl`, generated during package construction.
The user entrypoint includes these
defaults before local `dms/*.kdl` files; `dms/binds.kdl` starts empty and holds
only overrides. Later bindings replace earlier ones, so a disabled shortcut
can override its stock action with `spawn "true"` (this still consumes the key).
An optional `recent-windows.kdl` loads before `dms/binds.kdl` for hand-managed
MRU preferences. This avoids [Dux 2.0's shortcut-save bugs](https://github.com/abdulrahman-aj/arch-niri-dux/issues/33)
until a fixed package is available; generated `dms/alttab.kdl` controls appearance.
Existing user Niri files are preserved when configuring another time. Generated
layout/input files remain local, so Dux settings continue to control them.
Existing installations keep their full-copy configuration until explicitly
switched to the new entrypoint; no automatic migration changes user files.

New users start on Dux's upstream "bloom" material preset. The seed lives in
`~/.local/state/dms/session.json` and is only created when absent; existing
`session.json` files are never overwritten, so chosen wallpapers are preserved.
The preset assets remain owned by upstream Dux; no vendored Niri files are
edited for this default.

Papers (`org.gnome.Papers.desktop`) is the default PDF viewer from the factory
`/etc/xdg/mimeapps.list`. Fresh users also inherit Loupe for common images,
GNOME Text Editor for plain text and Markdown, Celluloid for common video,
Decibels for common audio, Chromium for the browser and Dux Files for folders;
User initialization preserves `~/.config/mimeapps.list`, so user choices
keep winning across reruns. GNOME Calculator is installed standalone;
`EDITOR`/`VISUAL` remain `nvim`. Snapshot (`org.gnome.Snapshot.desktop`) is the
camera app. These apps come from signed Arch packages; the live ISO remains
terminal-only.

CUPS and Avahi start for Dux's native driverless printer settings; CUPS keeps
its stock localhost-only listeners. GVFS supplies Android, Apple-device
and camera file access. SMB shares remain an optional install. Qt Multimedia
and cava support Dux's own effects without changing its settings.

Matugen supplies Dux's colors and system light/dark preference. A first-login
service applies upstream's GTK integration; Qt uses upstream's GTK passthrough.
Terminal shortcuts, user services and recovery tools use `xdg-terminal-exec`.
Alacritty is the factory preference in `/etc/xdg/xdg-terminals.list`; users can
select another installed terminal through Dux or `~/.config/xdg-terminals.list`.
Dux Files handles folders and portal file dialogs, with GTK as fallback. The
GNOME portal handles screen sharing; Nautilus remains its Arch dependency.

Logind suspends on lid-close when undocked and permits docked use. Niri already
controls the built-in panel according to the lid switch. Intermittent wakeups
still need physical laptop evidence: `journalctl -b -u systemd-logind -u
systemd-suspend` and `systemd-inhibit --list` show sleep events and inhibitors.

Session PATH: `~/.local/bin` resolves in POSIX login shells
(`/etc/profile.d/dux-localbin.sh`), systemd user services
(`/usr/lib/environment.d/90-dux.conf`, with per-user overrides) and Niri spawns.
Greetd's default profile sourcing passes the login PATH to the user session.
The greeter still uses its supported `--command niri` compositor selector.
Each context preserves system and Nix PATH entries; no mise shims are added.
The `session-path` stage of `make verify` checks the user-service environment,
login shell and a real Niri-spawned wrapper without personal dotfiles.
