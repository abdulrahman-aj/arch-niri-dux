# Target desktop overlay

`usr/share/arch-niri-dux/niri` contains the upstream Dux v2.0.0 Niri integration,
vendored verbatim. `packages/desktop-sources.json` records source URLs and hashes;
the upstream license is included. No personal dotfiles are used.

`configure-desktop USERNAME` installs those defaults with Alacritty and enables
the packaged Dux user services. Dux Greeter starts the packaged Niri session.
Dux creates its own shell settings on first launch.

Matugen supplies Dux's colors and system light/dark preference. A first-login
service applies upstream's GTK integration; Qt uses upstream's GTK passthrough.
Dux Files handles folders and portal file dialogs, with GTK as fallback. The
GNOME portal handles screen sharing; Nautilus remains its Arch dependency.

Logind suspends on lid-close when undocked and permits docked use. Niri already
controls the built-in panel according to the lid switch. Intermittent wakeups
still need physical laptop evidence: `journalctl -b -u systemd-logind -u
systemd-suspend` and `systemd-inhibit --list` show sleep events and inhibitors.
