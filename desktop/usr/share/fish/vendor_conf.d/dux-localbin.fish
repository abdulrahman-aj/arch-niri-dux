# arch-niri-dux: session-wide ~/.local/bin for fish shells.
# Appends, so system binaries keep precedence. Preserves any existing PATH,
# including Nix profiles. (POSIX login shells use /etc/profile.d/dux-localbin.sh;
# systemd user services use ~/.config/environment.d/90-dux.conf; the graphical
# Niri session inherits from dux-niri-session.)
if test -n "$HOME"; and not contains -- "$HOME/.local/bin" $PATH
    set -gx PATH $PATH "$HOME/.local/bin"
end
