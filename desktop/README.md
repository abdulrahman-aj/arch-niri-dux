# Target desktop overlay

`usr/share/arch-niri-dux/niri` contains the upstream Dux v2.0.0 Niri integration,
vendored verbatim. `packages/desktop-sources.json` records source URLs and hashes;
the upstream license is included. No personal dotfiles are used.

The offline `configure-desktop USERNAME` hook copies those defaults to the user,
substitutes Alacritty in upstream terminal placeholders, and adds `dms run` at
session startup. Dux creates its own shell settings on first launch. The hook
enables greetd/tuigreet with niri-session, NetworkManager, Bluetooth and power
profiles. It does not access the network.
