# arch-niri-dux: session-wide ~/.local/bin for POSIX login shells.
# Appends, so system binaries keep precedence. Preserves any existing PATH,
# including Nix profiles, and never uses a static list. systemd user services
# use ~/.config/environment.d/90-dux.conf; the graphical Niri session inherits
# from greetd's login profile.
case ":${PATH-}:" in
    *":${HOME}/.local/bin:"*) ;;
    *)
        if [ -n "${HOME-}" ]; then
            PATH="${PATH:+$PATH:}${HOME}/.local/bin"
            export PATH
        fi
        ;;
esac
