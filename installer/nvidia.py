"""Which NVIDIA driver list, if any, the installation adds.

Detection reads the cached sysfs IDs rather than lspci, which reads PCI config
space and wakes a runtime-suspended GPU. The thresholds are Omarchy's
(omarchy-hw-nvidia-gsp, omarchy-hw-nvidia-without-gsp).
"""

from pathlib import Path

DEVICES = Path('/sys/bus/pci/devices')
# Turing, the first generation with GSP firmware, is also the first with device
# IDs at 0x1e00 and above; nvidia-open needs GSP.
OPEN_FIRST_DEVICE = 0x1E00
# Maxwell starts one ID after the last Kepler part. Kepler and older need a
# legacy driver nobody packages for current kernels.
MAXWELL_FIRST_DEVICE = 0x1340
CHOICES = ('auto', 'open', '580xx', 'none')
LISTS = {'open': 'packages.nvidia', '580xx': 'packages.nvidia-580xx'}
WIKI = 'https://wiki.archlinux.org/title/NVIDIA'


def detect(devices=DEVICES):
    """Return (band, 'vendor:device') for the newest NVIDIA display controller, or None.

    band is 'open', '580xx' or 'unsupported'. NVIDIA's audio and USB functions
    share the vendor ID but are not display controllers, so they are skipped.
    """
    found = []
    for device in sorted(devices.glob('*')):
        try:
            vendor = (device / 'vendor').read_text().strip()
            device_class = (device / 'class').read_text().strip()
            identifier = int((device / 'device').read_text().strip(), 16)
        except (OSError, ValueError):
            continue
        if vendor != '0x10de' or not device_class.startswith('0x03'):
            continue
        if identifier >= OPEN_FIRST_DEVICE:
            band = 'open'
        elif identifier >= MAXWELL_FIRST_DEVICE:
            band = '580xx'
        else:
            band = 'unsupported'
        found.append((('unsupported', '580xx', 'open').index(band), band, f'10de:{identifier:04x}'))
    if not found:
        return None
    _, band, name = max(found)
    return band, name


def band(choice, devices=DEVICES):
    """Resolve a Settings.nvidia choice to the driver list to install: 'open', '580xx' or None.

    A forced band installs without a matching card; VM verification relies on that.
    """
    if choice in LISTS:
        return choice
    if choice == 'auto':
        detected = detect(devices)
        if detected and detected[0] in LISTS:
            return detected[0]
    return None
