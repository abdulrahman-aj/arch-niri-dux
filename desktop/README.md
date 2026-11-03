# Target desktop overlay

`usr/share/arch-niri-dux/niri` contains the upstream Dux v2.0.0 Niri integration,
vendored verbatim. `packages/desktop-sources.json` records source URLs and hashes;
the upstream license is included. No personal dotfiles are used.

`configure-desktop USERNAME` installs those defaults with Alacritty and enables
the packaged Dux user services. Dux Greeter starts the packaged Niri session.
Dux creates its own shell settings on first launch.

Niri's stock configuration and terminal-resolved bindings are exposed as
`/usr/share/arch-niri-dux/niri/defaults.kdl`, generated during the ISO build.
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

Papers (`org.gnome.Papers.desktop`) is the default PDF viewer in both
`/etc/xdg/mimeapps.list` and per-user `~/.config/mimeapps.list`. Snapshot
(`org.gnome.Snapshot.desktop`) is the installed camera app from Arch's official
`extra` repository. Both come from signed official packages, not the live
desktop; the live ISO remains terminal-only.

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
(`~/.config/environment.d/90-dux.conf`) and Niri spawns.
Greetd's default profile sourcing passes the login PATH to the user session.
The greeter still uses its supported `--command niri` compositor selector.
Each context preserves system and Nix PATH entries; no mise shims are added.
The `session-path` stage of `make verify` checks the user-service environment,
login shell and a real Niri-spawned wrapper without personal dotfiles.
