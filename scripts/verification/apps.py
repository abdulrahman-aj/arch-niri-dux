#!/usr/bin/env python3
"""Fresh-desktop acceptance; run only inside the disposable verification guest."""

import json
import math
from pathlib import Path
import pwd
import shlex
import shutil
import socket
import struct
import subprocess
import sys
import time
import wave
import zlib

from . import guest
from .pdf import sample_pdf_bytes

FILES = Path('/tmp/dux-baseline-files')
EVIDENCE = Path('/run/verify')


def record(name, value):
    (EVIDENCE / (name + '.json')).write_text(json.dumps(value, indent=2) + '\n')


def daemon_request(method, params=None):
    session = Path('/run/user') / str(pwd.getpwnam('tester').pw_uid)
    api = next(session.glob('dms*.sock'))
    with socket.socket(socket.AF_UNIX) as connection:
        connection.settimeout(15)
        connection.connect(str(api))
        stream = connection.makefile('rwb')
        json.loads(stream.readline())  # Initial capabilities greeting.
        request = {'id': 1, 'method': method} | ({'params': params} if params else {})
        stream.write((json.dumps(request) + '\n').encode())
        stream.flush()
        response = json.loads(stream.readline())
    assert not response.get('error'), response
    return response['result']


def app_window(name):
    return next((window for window in guest.windows() if name.lower() in (window.get('app_id') or '').lower()), None)


def focus_app(name):
    try:
        window = guest.wait_until(name + ' window', lambda: app_window(name))
    except TimeoutError:
        record('baseline-missing-' + name.lower(), {'windows': guest.windows()})
        guest.screenshot('baseline-missing-' + name.lower() + '.png')
        raise
    guest.focus_window(window['id'])
    guest.wait_until(name + ' focused', lambda: (app_window(name) or {}).get('is_focused'))
    return window


def close_app(name):
    if app_window(name):
        focus_app(name)
        guest.user_command('niri', 'msg', 'action', 'close-window')
        guest.wait_until(name + ' closed', lambda: app_window(name) is None)


def cli():
    result = guest.user_command(
        'bash',
        '-eu',
        '-c',
        r"""
for command in man less zip unzip rsync lsusb lsof strace dig nslookup jq rg fd wget fzf btop; do
    command -v "$command"
done
env LC_ALL=C MANWIDTH=80 man -P cat grep | grep -q SYNOPSIS
env LC_ALL=C MANWIDTH=80 man -P cat 2 open | grep -q O_CREAT
less --version
dig -v
btop --version
work=$(mktemp -d)
trap 'rm -rf -- "$work"' EXIT
cd "$work"
printf 'desktop-baseline\n' > input.txt
zip -q sample.zip input.txt
unzip -p sample.zip input.txt > unpacked.txt
cmp input.txt unpacked.txt
rsync -a input.txt copied.txt
cmp input.txt copied.txt
printf '{"baseline":true}\n' | jq -e '.baseline == true'
rg -q desktop-baseline copied.txt
test -n "$(fd -t f -g '*.txt')"
strace -o trace.log /usr/bin/true
grep -q execve trace.log
lsof -p $$ > open-files.txt
test -s open-files.txt
""",
        capture=True,
        timeout=90,
    )
    record('baseline-cli', {'passed': True, 'output': result.stdout})


def png_sample():
    width, height = 480, 320
    pixels = b''.join(
        b'\0' + b''.join(bytes((220, 40, 70) if (x // 60 + y // 40) % 2 else (20, 170, 220)) for x in range(width))
        for y in range(height)
    )

    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))

    return (
        b'\x89PNG\r\n\x1a\n'
        + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
        + chunk(b'IDAT', zlib.compress(pixels))
        + chunk(b'IEND', b'')
    )


def prepare():
    FILES.mkdir(exist_ok=True)
    shutil.chown(FILES, user='tester')
    (FILES / 'baseline-image.png').write_bytes(png_sample())
    (FILES / 'baseline-notes.txt').write_text('Dux baseline text editing verification.\n')
    for path in FILES.iterdir():
        shutil.chown(path, user='tester')
    guest.user_command(
        'ffmpeg',
        '-nostdin',
        '-loglevel',
        'error',
        '-y',
        '-f',
        'lavfi',
        '-i',
        'testsrc2=size=480x320:rate=12',
        '-f',
        'lavfi',
        '-i',
        'sine=frequency=660:sample_rate=48000',
        '-t',
        '30',
        '-c:v',
        'libx264',
        '-preset',
        'ultrafast',
        '-pix_fmt',
        'yuv420p',
        '-c:a',
        'aac',
        str(FILES / 'baseline-video.mp4'),
        timeout=90,
    )
    for extension, codec in [('flac', 'flac'), ('mp3', 'libmp3lame')]:
        guest.user_command(
            'ffmpeg',
            '-nostdin',
            '-loglevel',
            'error',
            '-y',
            '-f',
            'lavfi',
            '-i',
            'sine=frequency=440:sample_rate=48000',
            '-t',
            '30',
            '-c:a',
            codec,
            str(FILES / ('baseline-audio.' + extension)),
            timeout=60,
        )
    guest.user_command(
        'ffmpeg',
        '-nostdin',
        '-loglevel',
        'error',
        '-y',
        '-i',
        str(FILES / 'baseline-video.mp4'),
        '-t',
        '3',
        '-c',
        'copy',
        str(FILES / 'baseline-video.mkv'),
        timeout=30,
    )
    guest.user_command(
        'ffmpeg',
        '-nostdin',
        '-loglevel',
        'error',
        '-y',
        '-i',
        str(FILES / 'baseline-video.mp4'),
        '-t',
        '3',
        '-c:v',
        'libvpx',
        '-deadline',
        'realtime',
        '-cpu-used',
        '8',
        '-c:a',
        'libvorbis',
        str(FILES / 'baseline-video.webm'),
        timeout=60,
    )
    for filename in (
        'baseline-video.mp4',
        'baseline-video.mkv',
        'baseline-video.webm',
        'baseline-audio.flac',
        'baseline-audio.mp3',
    ):
        guest.user_command(
            'gst-launch-1.0',
            '-q',
            'playbin',
            'uri=' + (FILES / filename).as_uri(),
            'audio-sink=fakesink',
            'video-sink=fakesink',
            timeout=90,
        )
        guest.user_command(
            'mpv',
            '--no-config',
            '--no-terminal',
            '--vo=null',
            '--ao=null',
            '--length=1',
            str(FILES / filename),
            timeout=30,
        )
    guest.user_command(
        'ffmpegthumbnailer',
        '-i',
        str(FILES / 'baseline-video.mp4'),
        '-o',
        str(FILES / 'thumbnail.png'),
        '-s',
        '160',
        timeout=30,
    )
    guest.png_dimensions(FILES / 'thumbnail.png')
    guest.user_command('pactl', 'load-module', 'module-null-sink', 'sink_name=dux_verify_audio', capture=True)
    guest.user_command('pactl', 'set-default-sink', 'dux_verify_audio')
    guest.user_command('pactl', 'set-sink-volume', 'dux_verify_audio', '100%')
    record(
        'baseline-codecs',
        {
            'decoded': ['MP4/H.264/AAC', 'Matroska/H.264/AAC', 'WebM/VP8/Vorbis', 'FLAC', 'MP3'],
            'gstreamer_and_mpv': True,
            'video_thumbnail': True,
        },
    )


def open_file(filename, name):
    path = FILES / filename
    guest.user_command(
        'sh',
        '-c',
        'xdg-open ' + shlex.quote(str(path)) + ' >' + str(EVIDENCE / ('baseline-' + name.lower() + '.log')) + ' 2>&1 &',
    )
    focus_app(name)
    guest.screenshot('baseline-' + name.lower() + '.png')


def image():
    expected = 'org.gnome.Loupe.desktop'
    actual = guest.user_command('xdg-mime', 'query', 'default', 'image/png', capture=True).stdout.strip()
    assert actual == expected, actual
    open_file('baseline-image.png', 'Loupe')
    record('baseline-image', {'default': actual, 'opened': True})
    close_app('Loupe')


def text():
    for mime in ('text/plain', 'text/markdown'):
        actual = guest.user_command('xdg-mime', 'query', 'default', mime, capture=True).stdout.strip()
        assert actual == 'org.gnome.TextEditor.desktop', (mime, actual)
    open_file('baseline-notes.txt', 'TextEditor')
    # Earlier probes leave Chromium open beside the editor. Use a known input
    # surface rather than assuming the editor occupies the left half.
    guest.user_command('niri', 'msg', 'action', 'fullscreen-window')
    outputs = json.loads(guest.user_command('niri', 'msg', '--json', 'outputs', capture=True).stdout)
    logical = next(output['logical'] for output in outputs.values() if output.get('logical'))
    guest.wait_until(
        'Full-screen editor', lambda: app_window('TextEditor')['layout']['window_size'][0] >= logical['width'] - 4
    )
    # Client size changes before Niri's full-screen animation finishes moving it.
    time.sleep(1.5)


def text_focused():
    assert (app_window('TextEditor') or {}).get('is_focused'), guest.windows()


def text_saved():
    guest.wait_until(
        'Graphical editor saved the edited document',
        lambda: (FILES / 'baseline-notes.txt').read_text().strip() == 'editedbyduxverification',
        timeout=10,
    )
    guest.screenshot('baseline-text-saved.png')
    record('baseline-text', {'opened_with': 'org.gnome.TextEditor.desktop', 'edited_and_saved': True})
    close_app('TextEditor')


def calculator():
    guest.user_command('sh', '-c', 'gtk-launch org.gnome.Calculator >/run/verify/baseline-calculator.log 2>&1 &')
    focus_app('Calculator')
    guest.screenshot('baseline-calculator.png')
    record('baseline-calculator', {'launched': True})
    close_app('Calculator')


def media(kind):
    filename, application, mime, expected = {
        'audio': ('baseline-audio.flac', 'Decibels', 'audio/flac', 'org.gnome.Decibels.desktop'),
        'video': ('baseline-video.mp4', 'Celluloid', 'video/mp4', 'io.github.celluloid_player.Celluloid.desktop'),
    }[kind]
    actual = guest.user_command('xdg-mime', 'query', 'default', mime, capture=True).stdout.strip()
    assert actual == expected, (mime, actual)
    open_file(filename, application)


def tone_amplitude(path, frequency=660):
    with wave.open(str(path)) as source:
        assert source.getsampwidth() == 2 and source.getnchannels() == 1
        rate = source.getframerate()
        data = source.readframes(source.getnframes())
    samples = struct.unpack('<' + 'h' * (len(data) // 2), data)
    # A matching frequency distinguishes our fixture from notification sounds.
    count = len(samples)
    if not count:
        return 0
    real = sum(value * math.cos(2 * math.pi * frequency * i / rate) for i, value in enumerate(samples))
    imaginary = sum(value * math.sin(2 * math.pi * frequency * i / rate) for i, value in enumerate(samples))
    return 2 * math.hypot(real, imaginary) / count


def capture_audio(kind):
    path = FILES / ('captured-' + kind + '.wav')
    guest.user_command(
        'ffmpeg',
        '-nostdin',
        '-loglevel',
        'error',
        '-y',
        '-f',
        'pulse',
        '-i',
        'dux_verify_audio.monitor',
        '-t',
        '3',
        '-ac',
        '1',
        '-ar',
        '48000',
        '-c:a',
        'pcm_s16le',
        str(path),
        timeout=15,
    )
    frequency = 440 if kind == 'audio' else 660
    amplitude = tone_amplitude(path, frequency)
    record(
        'baseline-' + kind,
        {
            'frequency_hz': frequency,
            'tone_amplitude': amplitude,
            'tone_detected': amplitude > 200,
            'method': 'PipeWire null-sink monitor; no physical audio device',
        },
    )


def media_played(kind):
    data = json.loads((EVIDENCE / ('baseline-' + kind + '.json')).read_text())
    assert data['tone_detected'], data
    application = 'Decibels' if kind == 'audio' else 'Celluloid'
    guest.screenshot('baseline-' + application.lower() + '-playing.png')
    close_app(application)


def shared_connection():
    # Veth peers exercise NetworkManager's hotspot DHCP helper without a Wi-Fi radio.
    guest.run('ip', 'link', 'add', 'dv-share', 'type', 'veth', 'peer', 'name', 'dv-client')
    try:
        for interface, method in [('dv-share', 'shared'), ('dv-client', 'auto')]:
            guest.run('nmcli', 'device', 'set', interface, 'managed', 'yes')
            guest.run(
                'nmcli',
                'connection',
                'add',
                'type',
                'ethernet',
                'ifname',
                interface,
                'con-name',
                interface,
                'connection.autoconnect',
                'no',
                'ipv4.method',
                method,
                'ipv4.never-default',
                'yes',
                'ipv6.method',
                'disabled',
            )
        for interface in ('dv-share', 'dv-client'):
            guest.run('nmcli', '--wait', '20', 'connection', 'up', interface)
        address = guest.run(
            'nmcli', '--get-values', 'IP4.ADDRESS', 'device', 'show', 'dv-client', stdout=subprocess.PIPE
        ).stdout.strip()
        assert address.startswith('10.42.'), address
        return address
    finally:
        for interface in ('dv-client', 'dv-share'):
            subprocess.run(
                ['nmcli', 'connection', 'delete', interface],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        subprocess.run(['ip', 'link', 'delete', 'dv-share'], check=False)


def backends():
    for package, executable in [
        ('gvfs-mtp', 'gvfsd-mtp'),
        ('gvfs-afc', 'gvfsd-afc'),
        ('gvfs-gphoto2', 'gvfsd-gphoto2'),
    ]:
        files = subprocess.check_output(['pacman', '-Qlq', package], text=True).splitlines()
        binary = next(Path(path) for path in files if Path(path).name == executable)
        result = guest.run('ldd', str(binary), stdout=subprocess.PIPE)
        assert 'not found' not in result.stdout, result.stdout
    qml = FILES / 'multimedia-check.qml'
    qml.write_text(
        'import QtQuick\nimport QtMultimedia\nimport Quickshell\n'
        'Scope { MediaPlayer { id: player }\n'
        'Timer { interval: 100; running: true; onTriggered: {\n'
        'console.log("DUX_MULTIMEDIA_OK"); Qt.quit(); } } }\n'
    )
    shutil.chown(qml, user='tester')
    result = guest.user_command('bash', '-c', f'quickshell -p {qml} 2>&1', capture=True, timeout=30)
    assert 'DUX_MULTIMEDIA_OK' in result.stdout, result.stdout
    guest.user_command('cava', '-v', capture=True, timeout=10)
    # Exercise Qt's decoder, not Loupe's separate image stack or plugin filenames.
    for extension, codec_options in [
        ('avif', ['-c:v', 'libaom-av1', '-still-picture', '1']),
        ('webp', ['-c:v', 'libwebp']),
    ]:
        image = FILES / ('baseline-image.' + extension)
        guest.user_command(
            'ffmpeg',
            '-nostdin',
            '-loglevel',
            'error',
            '-y',
            '-i',
            str(FILES / 'baseline-image.png'),
            '-frames:v',
            '1',
            *codec_options,
            str(image),
            timeout=60,
        )
        # A synchronous decode can finish before Quickshell connects its quit handler.
        qml.write_text(
            'import QtQuick\nimport Quickshell\nScope {\n'
            'Image { id: image; source: "' + image.as_uri() + '" }\n'
            'Timer { interval: 100; running: true; repeat: true; onTriggered: {\n'
            'if (image.status === Image.Ready) { console.log("DUX_IMAGE_OK"); Qt.quit(); }\n'
            'if (image.status === Image.Error) Qt.exit(1); } } }\n'
        )
        result = guest.user_command('bash', '-c', f'quickshell -p {qml} 2>&1', capture=True, timeout=30)
        assert 'DUX_IMAGE_OK' in result.stdout, (extension, result.stdout)
    fingerprint = json.loads(
        guest.run(
            'busctl',
            '--system',
            '--json=short',
            'call',
            'net.reactivated.Fprint',
            '/net/reactivated/Fprint/Manager',
            'net.reactivated.Fprint.Manager',
            'GetDevices',
            stdout=subprocess.PIPE,
        ).stdout
    )
    assert fingerprint['type'] == 'ao', fingerprint
    smart = json.loads(guest.run('smartctl', '--json', '--scan', stdout=subprocess.PIPE).stdout)
    assert smart['smartctl']['exit_status'] == 0, smart
    drives = daemon_request('drives.list')
    assert Path(drives['smartctl']).name == 'smartctl', drives
    guest.run('dnsmasq', '--test')
    shared_address = shared_connection()
    guest.run('udevadm', 'verify', '/usr/lib/udev/rules.d/60-arch-niri-dux-ddc.rules')
    assert Path('/sys/module/i2c_dev').is_dir(), 'i2c-tools did not load i2c-dev at boot'
    record(
        'baseline-backends',
        {
            'phone_camera_binaries_load': True,
            'qt_multimedia_import': True,
            'cava_available': True,
            'qt_avif_decoded': True,
            'qt_webp_decoded': True,
            'fingerprint_backend_responded': True,
            'smartctl_discovered_by_dux': drives['smartctl'],
            'shared_connection_client_address': shared_address,
            'i2c_module_loaded': True,
            'note': 'Physical phone/camera access, fingerprint authentication and drive S.M.A.R.T. health data, external-monitor DDC and Wi-Fi hotspot operation are not qualified by this VM check.',
        },
    )


def printing():
    for service in ('cups.service', 'avahi-daemon.service'):
        guest.run('systemctl', 'is-enabled', service)
        guest.run('systemctl', 'is-active', service)
    guest.run('lpstat', '-r')
    guest.run('getent', 'hosts', 'localhost')
    hosts = next(
        line.split() for line in Path('/etc/nsswitch.conf').read_text().splitlines() if line.startswith('hosts:')
    )
    assert hosts.count('mdns_minimal') == 1 and hosts[hosts.index('mdns_minimal') + 1] == '[NOTFOUND=return]', hosts
    assert all(hosts.index('mdns_minimal') < hosts.index(source) for source in ('resolve', 'dns') if source in hosts), (
        hosts
    )
    assert Path('/usr/share/dbus-1/system-services/org.opensuse.CupsPkHelper.Mechanism.service').is_file()
    spool = FILES / 'print-spool'
    spool.mkdir(exist_ok=True)
    with (EVIDENCE / 'baseline-printer.log').open('w') as log:
        printer = subprocess.Popen(
            [
                'ippeveprinter',
                '-n',
                'localhost',
                '-p',
                '8631',
                '-r',
                'off',
                '-d',
                str(spool),
                '-k',
                '-f',
                'application/pdf',
                'DuxVirtual',
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            uri = 'ipp://localhost:8631/ipp/print'

            def ready():
                assert printer.poll() is None, 'Virtual printer exited; inspect baseline-printer.log'
                return (
                    subprocess.run(
                        ['ipptool', uri, '/usr/share/cups/ipptool/get-printer-attributes.test'],
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        check=False,
                    ).returncode
                    == 0
                )

            guest.wait_until('Virtual IPP printer', ready, timeout=30)
            guest.run('lpadmin', '-p', 'DuxProbe', '-E', '-v', uri, '-m', 'everywhere')
            assert Path('/etc/cups/ppd/DuxProbe.ppd').is_file()
            printers = daemon_request('cups.getPrinters')
            assert any(printer['name'] == 'DuxProbe' for printer in printers), printers
            document = FILES / 'print-test.pdf'
            document.write_bytes(sample_pdf_bytes())
            document.chmod(0o644)
            result = guest.run('lp', '-d', 'DuxProbe', str(document), stdout=subprocess.PIPE)

            def completed():
                files = [path for path in spool.iterdir() if path.is_file() and path.read_bytes().startswith(b'%PDF-')]
                pending = subprocess.check_output(['lpstat', '-o', 'DuxProbe'], text=True)
                return files if files and not pending.strip() else None

            documents = guest.wait_until('PDF delivered through driverless CUPS queue', completed, timeout=60)
            record(
                'baseline-printing',
                {
                    'driverless_queue': True,
                    'dux_printers': printers,
                    'submitted': result.stdout.strip(),
                    'received_pdf_bytes': [path.stat().st_size for path in documents],
                    'note': 'Loopback IPP emulator; physical printers and LAN discovery untested.',
                },
            )
        finally:
            subprocess.run(['lpadmin', '-x', 'DuxProbe'], check=False)
            printer.terminate()
            printer.wait(timeout=10)


def verify(vm, passed):
    vm.probe('apps', 'cli', timeout=120)
    passed('Manuals, archive round-trip, rsync, JSON/search and diagnostic CLI tools work')
    vm.probe('apps', 'prepare', timeout=300)
    vm.probe('apps', 'image', timeout=120)
    vm.probe('apps', 'text', timeout=120)
    vm.click(0.2, 0.2)
    time.sleep(0.2)
    vm.probe('apps', 'text-focused')
    vm.chord('ctrl', 'a')
    time.sleep(0.1)
    vm.type_graphical('editedbyduxverification')
    vm.chord('ctrl', 's')
    vm.probe('apps', 'text-saved', timeout=60)
    vm.probe('apps', 'calculator', timeout=120)
    passed('Images and text open in the selected apps; text edits save; Calculator launches')
    # Reopen video four times: first-launch success missed the original hang.
    for kind in ('audio', 'video', 'video', 'video', 'video'):
        vm.probe('apps', 'open-' + kind, timeout=120)
        vm.probe('apps', 'capture-' + kind, timeout=30)
        capture_file = vm.directory / ('baseline-' + kind + '.json')
        if not json.loads(capture_file.read_text())['tone_detected']:
            # Some players open paused; exercise their normal play shortcut.
            vm.key('spc')
            vm.probe('apps', 'capture-' + kind, timeout=30)
        vm.probe('apps', 'played-' + kind, timeout=60)
    passed('Decibels audio and four Celluloid open/play/close cycles output the expected fixture tones')
    vm.probe('apps', 'backends', timeout=90)
    passed('Dux discovers drive tools; Qt images, shared networking and desktop backends work')
    vm.probe('apps', 'printing', timeout=150)
    passed('Driverless CUPS queue delivered a PDF to a loopback IPP printer')


if __name__ == '__main__':
    if (
        not (EVIDENCE / 'install.json').is_file()
        or subprocess.run(['systemd-detect-virt', '--vm', '--quiet'], check=False).returncode != 0
    ):
        raise SystemExit('Run this probe only inside the disposable verification VM.')
    action = sys.argv[1]
    if action in (
        'cli',
        'prepare',
        'image',
        'text',
        'text-focused',
        'text-saved',
        'calculator',
        'backends',
        'printing',
    ):
        {
            'cli': cli,
            'prepare': prepare,
            'image': image,
            'text': text,
            'text-focused': text_focused,
            'text-saved': text_saved,
            'calculator': calculator,
            'backends': backends,
            'printing': printing,
        }[action]()
    elif action.startswith('open-'):
        media(action.removeprefix('open-'))
    elif action.startswith('capture-'):
        capture_audio(action.removeprefix('capture-'))
    elif action.startswith('played-'):
        media_played(action.removeprefix('played-'))
    else:
        raise SystemExit('Unknown baseline probe')
