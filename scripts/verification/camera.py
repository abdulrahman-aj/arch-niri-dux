"""Camera acceptance behavior."""

import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
from .guest import wait_until, user_command, windows, screenshot, png_dimensions
from .vm import click_screen_text


def pipewire_video_sources():
    try:
        output = user_command('pw-dump', capture=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return []
    try:
        objects = json.loads(output)
    except ValueError:
        return []
    sources = []
    for obj in objects:
        if not isinstance(obj, dict) or obj.get('type') != 'PipeWire:Interface:Node':
            continue
        info = obj.get('info', {})
        props = obj.get('props', {}) or (info.get('props', {}) if isinstance(info, dict) else {})
        if props.get('media.class') == 'Video/Source':
            sources.append(props.get('node.name') or props.get('node.description') or 'video-source')
    return sources


def focus_camera():
    window = next(window for window in windows()
                  if 'snapshot' in (window.get('app_id') or '').lower())
    user_command('niri', 'msg', 'action', 'focus-window', '--id', str(window['id']))


def camera_cleanup():
    pid_file = Path('/tmp/dux-synthetic-camera.pid')
    if pid_file.is_file():
        pid = int(pid_file.read_text())
        pid_file.unlink()
        user_command('sh', '-c', f'kill {pid} 2>/dev/null || true')
    user_command('sh', '-c', 'pkill -x snapshot || true')


def camera():
    assert Path('/usr/share/applications/org.gnome.Snapshot.desktop').is_file()
    # Supply only a generated video stream, never a physical camera/microphone.
    user_command('gsettings', 'set', 'org.gnome.Snapshot', 'enable-audio-recording', 'false')
    user_command('gsettings', 'set', 'org.gnome.Snapshot', 'countdown', '0')
    user_command('gsettings', 'set', 'org.gnome.Snapshot', 'capture-mode', "'picture'")
    Path('/tmp/dux-synthetic-camera.pid').unlink(missing_ok=True)
    user_command('sh', '-c',
                 'gst-launch-1.0 videotestsrc pattern=ball is-live=true ! '
                 'video/x-raw,width=640,height=480,framerate=30/1 ! pipewiresink mode=provide '
                 'stream-properties="properties,media.class=Video/Source,media.role=Camera,node.name=DuxTestCamera" '
                 'client-name="DUX verification camera" '
                 '>/tmp/dux-synthetic-camera.log 2>&1 & echo $! > /tmp/dux-synthetic-camera.pid')
    try:
        wait_until('Synthetic PipeWire camera', lambda:
                   'DuxTestCamera' in pipewire_video_sources(), timeout=60)
        before = [str(path) for folder in ('Pictures', 'Videos')
                  for path in Path('/home/tester', folder, 'Camera').glob('*')]
        user_command('sh', '-c', 'snapshot --debug >/tmp/snapshot.log 2>&1 &')
        wait_until('Snapshot window', lambda: any(
            'snapshot' in (window.get('app_id') or '').lower() for window in windows()), timeout=60)
        focus_camera()
        screenshot('02-snapshot.png')
        width, height = png_dimensions(Path('/run/verify/02-snapshot.png'))
        Path('/run/verify/camera.json').write_text(json.dumps({
            'synthetic': True, 'source': 'DuxTestCamera', 'before': before,
            'width': width, 'height': height,
        }, indent=2) + '\n')
    except Exception:
        camera_cleanup()
        raise


def camera_media(video=False):
    evidence = Path('/run/verify/camera.json')
    state = json.loads(evidence.read_text())
    folder = Path('/home/tester', 'Videos' if video else 'Pictures', 'Camera')
    def completed_capture():
        for path in folder.glob('*'):
            if str(path) in state['before'] or not path.is_file():
                continue
            data = path.read_bytes()
            if video:
                valid = len(data) > 512 and (data.startswith(b'\x1aE\xdf\xa3') or data[4:8] == b'ftyp')
            else:
                valid = data.startswith(b'\xff\xd8') and data.endswith(b'\xff\xd9')
            if valid:
                return path
        return None
    captured = wait_until('Snapshot video recording' if video else 'Snapshot photograph',
                          completed_capture, timeout=60)
    target = Path('/run/verify') / ('camera-video' if video else 'camera-photo')
    target = target.with_suffix(captured.suffix)
    shutil.copyfile(captured, target)
    state['video' if video else 'photo'] = {'path': str(captured), 'bytes': captured.stat().st_size}
    evidence.write_text(json.dumps(state, indent=2) + '\n')
    if video:
        screenshot('02-snapshot-captured.png')
    else:
        user_command('gsettings', 'set', 'org.gnome.Snapshot', 'capture-mode', "'video'")
        focus_camera()


def verify(vm, passed):
    vm.probe('camera', 'camera', timeout=300)
    try:
        permission_deadline = time.monotonic() + 60
        while time.monotonic() < permission_deadline:
            vm.probe('camera', 'camera-screen')
            if (click_screen_text(vm, vm.directory / '02-snapshot.png', 'Grant Access')
                    or click_screen_text(vm, vm.directory / '02-snapshot.png', 'GrantAccess')):
                break
            time.sleep(1)
        else:
            raise TimeoutError('Snapshot did not request camera permission on the fresh installation.')
        time.sleep(3)
        vm.probe('camera', 'camera-screen')
        # Use Snapshot 51's upstream shutter shortcut to exercise its own
        # capture path in both modes, rather than generating files separately.
        # https://github.com/GNOME/snapshot/blob/51.0/src/application.rs
        vm.key('t')
        vm.probe('camera', 'camera-photo', timeout=90)
        vm.key('t')
        time.sleep(3)
        vm.key('t')
        vm.probe('camera', 'camera-video', timeout=90)
    finally:
        vm.probe('camera', 'camera-cleanup')
    passed('Snapshot preview, shutter photo and video recording work with a synthetic camera')


if __name__ == '__main__':
    action = sys.argv[1]
    if action == 'camera':
        camera()
    elif action == 'camera-photo':
        camera_media()
    elif action == 'camera-video':
        camera_media(video=True)
    elif action == 'camera-screen':
        screenshot('02-snapshot.png')
    elif action == 'camera-cleanup':
        camera_cleanup()
    else:
        raise SystemExit('Unknown camera probe')
