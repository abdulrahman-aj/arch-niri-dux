"""Guest-only package upgrade, baseline dependency and config preservation check."""

from tools.verification.login import installed_login
from tools.verification.payload import probe, mount_probes


def verify_published(vm, passed):
    vm.start(cdrom=False, network=True)
    installed_login(vm)
    mount_probes(vm)
    probe(vm, 'upgrade', 'published-repository', timeout=300)
    passed('Published repository and packages verify against offline trust and exact release hashes')
    vm.poweroff()
    vm.stop()
