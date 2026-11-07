"""Inbound firewall and resolve-only mDNS, observed from a LAN neighbour.

The neighbour is a network namespace joined to the guest by a veth pair, so the
check needs no QEMU networking and its traffic crosses the real input hook.
"""

import json
from pathlib import Path
import socket
import struct
import subprocess
import threading

from . import dispatch
from .guest import run, wait_until

NAMESPACE = 'verify-peer'
HOST_LINK = 'verify-host'
HOST_V4, PEER_V4 = '192.0.2.1', '192.0.2.2'
HOST_V6, PEER_V6 = 'fd00:d0c5::1', 'fd00:d0c5::2'
PORT = 8765
PEER_NAME = 'verify-peer.local'
LOCAL_RULE = Path('/etc/arch-niri-dux/firewall.d/90-verify.nft')
MDNS_GROUP = '224.0.0.251'


def peer(*args, **kwargs):
    return subprocess.Popen(['ip', 'netns', 'exec', NAMESPACE, *args], **kwargs)


def connect(address, namespace=False):
    """Return 'open', 'refused' or 'dropped' for a TCP connection attempt."""
    command = ['timeout', '3', 'bash', '-c', f'exec 3<>/dev/tcp/{address}/{PORT}']
    if namespace:
        command = ['ip', 'netns', 'exec', NAMESPACE, *command]
    code = subprocess.run(command, stderr=subprocess.DEVNULL, check=False).returncode
    # timeout(1) exits 124 when the SYN gets no answer, unlike a refusal.
    return {0: 'open', 124: 'dropped'}.get(code, 'refused')


def serve(server):
    while True:
        client, _ = server.accept()
        client.close()


def open_listener():
    return socket.create_server(('::', PORT), family=socket.AF_INET6, dualstack_ipv6=True)


def listener():
    """Accept TCP connections on PORT until killed."""
    serve(open_listener())


def create_neighbour():
    run('ip', 'netns', 'add', NAMESPACE)
    run('ip', 'link', 'add', HOST_LINK, 'type', 'veth', 'peer', 'name', 'eth0', 'netns', NAMESPACE)
    # Keep NetworkManager from running DHCP on the test link.
    wait_until(
        'NetworkManager releasing the test link',
        lambda: subprocess.run(['nmcli', 'device', 'set', HOST_LINK, 'managed', 'no'], check=False).returncode == 0,
    )
    for namespace, link, v4, v6 in ((None, HOST_LINK, HOST_V4, HOST_V6), (NAMESPACE, 'eth0', PEER_V4, PEER_V6)):
        prefix = ['ip', 'netns', 'exec', namespace] if namespace else []
        run(*prefix, 'ip', 'address', 'add', f'{v4}/24', 'dev', link)
        run(*prefix, 'ip', 'address', 'add', f'{v6}/64', 'dev', link, 'nodad')
        run(*prefix, 'ip', 'link', 'set', link, 'up')
    run('ip', 'netns', 'exec', NAMESPACE, 'ip', 'link', 'set', 'lo', 'up')


def reload_firewall():
    run('systemctl', 'reload', 'arch-niri-dux-firewall.service')


def own_name_resolves():
    hostname = socket.gethostname()
    result = subprocess.run(
        ['timeout', '10', 'avahi-resolve-host-name', '-4', f'{hostname}.local'],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0 and result.stdout.strip() != '', result


def check():
    run('systemctl', 'is-enabled', '--quiet', 'arch-niri-dux-firewall.service')
    run('systemctl', 'is-active', '--quiet', 'arch-niri-dux-firewall.service')
    server = open_listener()
    threading.Thread(target=serve, args=(server,), daemon=True).start()
    processes = []
    try:
        create_neighbour()
        assert connect('127.0.0.1') == 'open', 'Loopback must stay reachable.'
        inbound = {address: connect(address, namespace=True) for address in (HOST_V4, HOST_V6)}
        assert set(inbound.values()) == {'dropped'}, inbound

        processes.append(peer('python', '-m', 'verification.network', 'listener', cwd='/run/verify'))
        # IPv6 needs neighbour advertisements from the peer before any SYN leaves.
        for address in (PEER_V4, PEER_V6):
            wait_until(f'Outbound TCP to {address}', lambda address=address: connect(address) == 'open')

        LOCAL_RULE.write_text(f'add rule inet arch_niri_dux input tcp dport {PORT} accept\n')
        reload_firewall()
        opened = {address: connect(address, namespace=True) for address in (HOST_V4, HOST_V6)}
        assert set(opened.values()) == {'open'}, opened
        LOCAL_RULE.unlink()
        reload_firewall()
        assert connect(HOST_V4, namespace=True) == 'dropped', 'Removing the local rule must close the port.'

        processes.append(peer('python', '-m', 'verification.network', 'responder', cwd='/run/verify'))
        wait_until(
            f'mDNS resolution of {PEER_NAME}',
            lambda: (
                subprocess.run(
                    ['getent', 'ahostsv4', PEER_NAME], capture_output=True, text=True, check=False
                ).stdout.split()[:1]
                == [PEER_V4]
            ),
            timeout=60,
        )
        published, own = own_name_resolves()
        assert not published, f'Avahi still answers for this host: {own.stdout}'

        # Tailscale can't run offline; an interface with its name stands in.
        # The ruleset loaded before it existed, as at boot. Renaming needs the
        # link down, which drops its IPv6 addresses, so this checks IPv4.
        run('ip', 'link', 'set', HOST_LINK, 'down')
        run('ip', 'link', 'set', HOST_LINK, 'name', 'tailscale0')
        run('ip', 'link', 'set', 'tailscale0', 'up')
        wait_until('Inbound TCP on tailscale0', lambda: connect(HOST_V4, namespace=True) == 'open', timeout=20)
        Path('/run/verify/network-policy.json').write_text(
            json.dumps(
                {
                    'inbound_from_neighbour': inbound,
                    'outbound_to_neighbour': dict.fromkeys((PEER_V4, PEER_V6), 'open'),
                    'local_rule_opened': opened,
                    'neighbour_name_resolved_over_mdns': True,
                    'own_hostname_published': published,
                    'inbound_on_tailscale0': 'open',
                },
                indent=2,
            )
            + '\n'
        )
    finally:
        for process in processes:
            process.kill()
            process.wait()
        server.close()
        LOCAL_RULE.unlink(missing_ok=True)
        subprocess.run(['systemctl', 'reload', 'arch-niri-dux-firewall.service'], check=False)
        subprocess.run(['ip', 'netns', 'delete', NAMESPACE], check=False)


def name_labels(packet, offset):
    labels = []
    while packet[offset]:
        length = packet[offset]
        if length & 0xC0:
            # A compression pointer ends the name; later questions may use one.
            # Only the uncompressed PEER_NAME question matters here.
            return None, offset + 2
        labels.append(packet[offset + 1 : offset + 1 + length].decode(errors='replace'))
        offset += 1 + length
    return '.'.join(labels), offset + 1


def responder():
    """Answer A queries for PEER_NAME, as a printer would, until killed."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(('', 5353))
    interface = socket.inet_aton(PEER_V4)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, socket.inet_aton(MDNS_GROUP) + interface)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, interface)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
    encoded = b''.join(bytes([len(label)]) + label.encode() for label in PEER_NAME.split('.')) + b'\0'
    # Flags 0x8400: response, authoritative. Class 0x8001: IN with cache-flush.
    answer = (
        struct.pack('>6H', 0, 0x8400, 0, 1, 0, 0)
        + encoded
        + struct.pack('>HHIH', 1, 0x8001, 120, 4)
        + socket.inet_aton(PEER_V4)
    )
    while True:
        packet, _ = sock.recvfrom(9000)
        if len(packet) < 12 or packet[2] & 0x80:
            continue
        offset = 12
        for _ in range(struct.unpack('>H', packet[4:6])[0]):
            name, offset = name_labels(packet, offset)
            qtype = struct.unpack('>H', packet[offset : offset + 2])[0]
            offset += 4
            if name and name.lower() == PEER_NAME and qtype in (1, 255):
                sock.sendto(answer, (MDNS_GROUP, 5353))


if __name__ == '__main__':
    dispatch('network', {'check': check, 'listener': listener, 'responder': responder})
