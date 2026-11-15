"""Boot acceptance behavior."""

from tools.verification.login import installed_login, graphical_login
from tools.verification.payload import probe, mount_probes


def verify_boot(vm, passed, swap_gib):
    # A CPU-readable framebuffer qualifies the real pre-login UI. A serial
    # kernel console would force Plymouth into its details/text mode.
    vm.start(cdrom=False, software_display=True)
    installed_login(vm, keyboard_unlock=True, screenshot_tag='02-graphical-unlock')
    mount_probes(vm)
    probe(vm, 'boot', 'plymouth-status')
    passed('Graphical disk unlock accepts keyboard input and retries an incorrect password')
    probe(vm, 'boot', 'serial-console-enable', timeout=300)
    vm.poweroff()
    vm.stop()
    vm.start(cdrom=False)
    installed_login(vm)
    passed('UEFI boot from encrypted disk without installation ISO')
    vm.command(
        'test -e /dev/mapper/dux-root; findmnt -n -o FSTYPE / | grep -x btrfs; test "$(ls /sys/class/net | tr "\\n" " ")" = "lo "'
    )
    vm.command(
        'cat /proc/cmdline; ! grep -q /@snapshots/ /proc/cmdline; systemctl is-enabled greetd; systemctl is-active greetd; test -f /home/tester/.config/niri/config.kdl'
    )
    vm.command(
        'systemctl is-active NetworkManager; systemctl is-enabled bluetooth; command -v nvim chromium alacritty xdg-terminal-exec niri dms papers snapshot; ! command -v yay'
    )
    vm.command(
        'mkdir -p /run/verify; mount -t 9p -o trans=virtio,version=9p2000.L verify /run/verify || mountpoint -q /run/verify'
    )
    probe(vm, 'boot', 'plymouth-config')
    probe(vm, 'boot', 'plymouth-status')
    passed('Plymouth configuration and boot services are active')
    probe(vm, 'boot', 'keyboard')
    passed('Console keymap carries over to the X11/login layout')
    graphical_login(vm)
    probe(vm, 'boot', 'luks-pbkdf')
    passed('LUKS2 argon2id uses cryptsetup defaults, not the old 256 MiB pin')
    probe(vm, 'boot', 'storage')
    passed(
        'No-swap installation disables hibernation'
        if swap_gib == 0
        else 'Encrypted swap outside root snapshots; physical resume offset matches the kernel'
    )


def verify_fallback(vm, passed):
    # Real text-fallback path: same serial automation unlocks the disk with
    # the splash disabled, then the graphical prompt is restored.
    probe(vm, 'boot', 'serial-console-disable', timeout=300)
    probe(vm, 'boot', 'splash-fallback-disable')
    vm.command('sync')
    vm.poweroff()
    vm.stop()
    vm.start(cdrom=False, software_display=True)
    installed_login(vm, keyboard_unlock=True, screenshot_tag='06-text-fallback')
    mount_probes(vm)
    vm.command(
        'grep -qw plymouth.enable=0 /proc/cmdline; cd /run/verify && python -m tools.verification.probes.boot storage'
    )
    passed('Text fallback unlocks the disk with the splash disabled')
    probe(vm, 'boot', 'splash-fallback-enable')
    vm.command('sync')
    vm.poweroff()
    vm.stop()
    vm.start(cdrom=False, software_display=True)
    installed_login(vm, keyboard_unlock=True, screenshot_tag='07-restored-splash')
    mount_probes(vm)
    vm.command(
        '! grep -qw plymouth.enable=0 /proc/cmdline; cd /run/verify && python -m tools.verification.probes.boot plymouth-status'
    )
    probe(vm, 'recovery', 'restored')
    passed('Graphical password screen works again after text fallback')
    probe(vm, 'boot', 'serial-console-enable', timeout=300)
    vm.poweroff()
    vm.stop()
