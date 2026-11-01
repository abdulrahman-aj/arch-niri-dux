# Verification — 2026-11-01

The current local ISO passed the complete automated offline acceptance run:

```
arch-niri-dux-2026.11.01-x86_64.iso
SHA-256 69516c924635696c71793fbc0bff61c5e1d60dc936719e709c4f5c4ba4591a2e
```

The image contains 464 pinned, signature-checked packages: Dux 2.0.0-1,
Niri 26.04-1, Linux 7.2.11.arch1-1, SOF firmware 2026.09.2-1 and
GitHub CLI 2.102.1-1. Adding GitHub CLI and mailcap left all previous pins unchanged.
The stable Nix shell supplies QEMU 10.2.4 and Mesa 26.1.8. An upstream
[QEMU EGL cleanup regression](https://github.com/qemu/qemu/commit/baca25172d8c195b9abbe6f8a3ab5915dba09a91)
in the newer toolchain was reproduced and avoided by this pin; the minimal
EGL startup/quit test and the full VM run exit successfully.

`make test` passed 39 source/build tests. The actual ISO was tested with:

```sh
make verify ARGS='--directory out/verify-usb --disk /dev/shm/dux-usb.qcow2'
```

The VM uses UEFI, KVM, 8 GiB RAM, USB mass-storage boot, virtio 3D graphics and
no network device. Unlike optical boot, USB boot can trigger Archiso's automatic
RAM copy, which unmounts the offline repository. The ISO now sets `copytoram=n`;
verification requires the USB mount and repository to remain available.
The runner
verified these behaviors through the shared installer and real applications:

- Offline installation with an encrypted Btrfs root and separate home.
- Disk-passphrase unlock and boot without the installation ISO.
- Greeter login, an active Niri output, Dux IPC and a rendered desktop PNG.
- Five Arabic/Urdu/Latin font-selection checks, including untagged Arabic glyphs.
- Alacritty command execution, headless Neovim editing, graphical Chromium
  loading a local page, PipeWire controls, Git, GitHub CLI and Yay execution.
- Screen lock and password unlock using virtual keyboard input.
- Automatic pre/post snapshots during a real local package transaction.
- Booting the selected known-good snapshot, restoring it with the upstream
  interactive tool, then rebooting into the normal root.
- A home file changed after the snapshot retained its new contents throughout
  restoration, while the root marker reverted and the greeter recovered.

Local evidence is under `out/verify-usb/`: `ACCEPTANCE-PASSED`,
`milestones.json`, `inputs.json`, `serial.log`, QEMU command lines and
`02-desktop-apps.png`. These outputs are not committed or uploaded. QMP cannot
capture this GL backend's GPU texture; unavailable QMP screenshots are recorded
explicitly, and the desktop PNG is captured by Niri and required for acceptance.

The separate online personal bootstrap also passed, using dotfiles commit
`5de57c3` from its PR and an archive of the local agent-config Git HEAD:

```sh
make verify-personal ARGS='--disk /dev/shm/dux-gh.qcow2 --dotfiles out/dotfiles-pr --agent-config /home/abdulrahman/agent-config --directory out/personal-nix'
```

This rewound the stopped test disk to its pristine installation, added a network
device, and ran the real dotfiles `make` as the test user. Official/AUR package
downloads, Mise tools, portable stow deployment, agent-config deployment and
Fisher completed. Assertions verified Fish as the login shell, Zen MIME defaults,
required commands and tool versions, unchanged Niri configuration, and excluded
Omarchy/Hyprland/Alacritty/OpenCode dotfiles links. Arch's native `zeditor` and
the Fish `zw` function are checked. Nix 2.35.4 was installed, its daemon socket
was active, and an unprivileged Nix store write/read succeeded. User configuration
enables nix-command and flakes. Nix is part of personal setup, not the ISO.

Deployment tests cover real Stow roundtrips for full/portable profiles, conflict
backups, preserved excluded files and the alternate-home guard. The mocked
command-argument tests were removed. Omarchy's package and Chrome/Zen installer
invocations were checked against the installed CLI help; its full bootstrap was
not executed on the host.

Evidence is under `out/personal-nix/`: `PERSONAL-PASSED`, `sources.json`,
`bootstrap.log` and `serial.log`. This online step does not alter the offline
ISO. Personal application GUI workflows were not exhaustively tested.

Physical Zenbook audio, Wi-Fi, display behavior and lid suspend remain hardware
checks; the VM does not qualify those devices. Secure Boot is outside v1.
Planned features are tracked in TODO.md.
