"""Recovery acceptance behavior."""

from tools.verification.login import installed_login
from tools.verification.payload import probe, mount_probes


def verify(vm, passed):
    probe(vm, 'recovery', 'snapshots', timeout=600)
    passed('Package upgrade, baseline changes and personal preservation passed; recovery fixture prepared')
    vm.command('sync')
    vm.poweroff()
    vm.stop()
    vm.start(cdrom=False)
    installed_login(vm)
    mount_probes(vm)
    probe(vm, 'recovery', 'recovery', timeout=300)
    vm.screenshot('04-recovery')
    passed('Limine snapshot boot and restoration')
    vm.command('sync')
    vm.poweroff()
    vm.stop()
    vm.start(cdrom=False)
    installed_login(vm)
    mount_probes(vm)
    probe(vm, 'recovery', 'restored')
    vm.screenshot('05-restored')
    passed('Restored system rebooted normally and home was preserved')
