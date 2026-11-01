# Verification — 2026-11-01

The local ISO passed the complete automated offline acceptance run.

```
arch-niri-dux-2026.11.01-x86_64.iso
SHA-256 7ae8c83daae289013609363daf31d7186c0fc1f6cf4d022e860b77407f0dbc95
```

The image contains 462 pinned, signature-checked packages: Dux 2.0.0-1,
Niri 26.04-1, Linux 7.2.11.arch1-1 and SOF firmware 2026.09.2-1.
The stable Nix shell supplies QEMU 10.2.4 and Mesa 26.1.8. An upstream
[QEMU EGL cleanup regression](https://github.com/qemu/qemu/commit/baca25172d8c195b9abbe6f8a3ab5915dba09a91)
in the newer toolchain was reproduced and avoided by this pin; the minimal
EGL startup/quit test and the full VM run exit successfully.

`make test` passed 38 source tests. The actual ISO was tested with:

```sh
make verify ARGS='--directory out/verify-final --disk /dev/shm/dux-acceptance-final.qcow2'
```

The VM uses UEFI, KVM, virtio 3D graphics and no network device. The runner
verified these behaviors through the shared installer and real applications:

- Offline installation with an encrypted Btrfs root and separate home.
- Disk-passphrase unlock and boot without the installation ISO.
- Greeter login, an active Niri output, Dux IPC and a rendered desktop PNG.
- Alacritty command execution, headless Neovim editing, graphical Chromium
  loading a local page, PipeWire controls and Yay execution.
- Screen lock and password unlock using virtual keyboard input.
- Automatic pre/post snapshots during a real local package transaction.
- Booting the selected known-good snapshot, restoring it with the upstream
  interactive tool, then rebooting into the normal root.
- A home file changed after the snapshot retained its new contents throughout
  restoration, while the root marker reverted and the greeter recovered.

Local evidence is under `out/verify-final/`: `ACCEPTANCE-PASSED`,
`milestones.json`, `inputs.json`, `serial.log`, QEMU command lines and
`02-desktop-apps.png`. These outputs are not committed or uploaded. QMP cannot
capture this GL backend's GPU texture; unavailable QMP screenshots are recorded
explicitly, and the desktop PNG is captured by Niri and required for acceptance.

The separate online personal bootstrap also passed, using dotfiles commit
`7c30b94` from its PR and an archive of the local agent-config Git HEAD:

```sh
make verify ARGS='--install-only --directory out/unslop-base --disk /dev/shm/dux-unslop.qcow2'
make verify-personal ARGS='--disk /dev/shm/dux-unslop.qcow2 --dotfiles out/dotfiles-pr --agent-config /home/abdulrahman/agent-config --directory out/personal-unslop'
```

This rewound the stopped test disk to its pristine installation, added a network
device, and ran the real dotfiles `make` as the test user. Official/AUR package
downloads, Mise tools, portable stow deployment, agent-config deployment and
Fisher completed. Assertions verified Fish as the login shell, Zen MIME defaults,
required commands and tool versions, unchanged Niri configuration, and excluded
Omarchy/Hyprland/Alacritty/OpenCode dotfiles links. Arch's native `zeditor` and
the Fish `zw` function are checked. Nix-shell tests also cover both setup paths,
Omarchy integration, config filtering, parallel Make ordering and editor wiring.

Evidence is under `out/personal-unslop/`: `PERSONAL-PASSED`, `sources.json`,
`bootstrap.log` and `serial.log`. This online step does not alter the offline
ISO. Personal application GUI workflows were not exhaustively tested.

Physical Zenbook audio, Wi-Fi, display behavior and lid suspend remain hardware
checks; the VM does not qualify those devices. Secure Boot and hibernation are
outside v1. Screen-sharing integration is tracked in TODO.md.
