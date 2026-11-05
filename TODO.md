- [ ] Replace the AUR packages for Dux Greeter, Search, Calendar and Files once
  suitable signed Arch/Omarchy packages are available, then remove Yay. Keep Yay
  and the existing update path working meanwhile. Do not maintain a package
  repository for these apps.
- [ ] Investigate the laptop's intermittently lit lock screen on opening the lid.
  Logs show successful suspend cycles waking when the lid opens. They have not
  captured the intermittent failure. Physical testing is deferred. When it
  resumes, verify undocked lid-close suspend and docked use with the built-in
  panel off.
- [ ] Hold a separate Secure Boot design session before implementation. Cover
  installer USB support and Windows/Linux switching with Secure Boot enabled,
  factory trust preservation, enrollment risks, automatic signing on updates,
  snapshot boot and recovery. Choose the boot-chain design in that session.
- [ ] Offer a one-button opt-in install of
  [dotfiles](https://github.com/abdulrahman-aj/dotfiles). Clone the repository and
  run `make` to set up Stow linking, agent-config, Fish shell and personal apps.
  Keep this option disabled by default and require explicit consent. The offline
  installer must not download anything. Fetch only when a network is available.
  Handle `gh` authentication failures, back up conflicting dotfiles, and verify
  the wizard or first-boot prompt in a VM.
