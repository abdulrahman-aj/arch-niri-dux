"""Browser acceptance behavior."""

import json
import html
from pathlib import Path
import re
import sys
import time
import urllib.error
import urllib.request
from .guest import wait_until, focus_window, windows, screenshot
from .vm import click_screen_text


def browser_state():
    with urllib.request.urlopen('http://127.0.0.1:9222/json/list', timeout=2) as response:
        pages = json.load(response)
    for page in pages:
        if page.get('url') == 'file:///tmp/chromium-smoke.html' and page.get('title', '').startswith('DUX-VERIFY '):
            # Chromium's HTTP discovery endpoint HTML-escapes target titles.
            return json.loads(html.unescape(page['title'].removeprefix('DUX-VERIFY ')))
    return None


def focus_browser():
    browser = next(window for window in windows() if window.get('app_id', '').lower().startswith('chromium'))
    focus_window(browser['id'])
    assert browser_state(), 'The graphical verification page is unavailable.'


def file_chooser():
    wait_until('Chromium file-picker request', lambda:
               (state := browser_state()) is not None and state['file'] == 'pending')
    wait_until('Dux Files handling Chromium open-file dialog', lambda: any(
        window.get('app_id') == 'dev.duxlabs.files' and re.match(r'(?i)(open|select)', window.get('title', ''))
        for window in windows()))
    screenshot('02-file-chooser.png')


def captured_frame():
    state = browser_state()
    if state and state['capture'].startswith('error:'):
        raise RuntimeError(state['capture'])
    if state and state['capture'] == 'passed' and state['frames'] >= 3 and state['width'] > 0 and state['height'] > 0:
        pixels = state.get('pixels', {})
        if pixels.get('nonBlack', 0) >= 10 and pixels.get('maximum', 0) - pixels.get('minimum', 0) > 60 and pixels.get('colors', 0) >= 8:
            return state
    return False


def capture():
    state = wait_until('Chromium screen sharing producing nonblank video content', captured_frame)
    Path('/run/verify/screen-sharing.json').write_text(json.dumps(state, indent=2) + '\n')
    screenshot('02-screen-sharing.png')

BROWSER_PAGE = '''<!doctype html>
<meta charset="utf-8"><meta name="color-scheme" content="light dark">
<title>Dux verification loading</title>
<style>body { font: 24px sans-serif; color-scheme: light dark; padding: 40px; }
button { font: inherit; padding: 16px; } video, canvas { width: 500px; display: block; }
[hidden] { display: none !important; }</style>
<h1>Offline Dux verification</h1><p id="status"></p>
<button id="share" autofocus>Share a screen or window (C)</button>
<input id="picker" type="file" hidden><video muted autoplay></video><canvas hidden></canvas>
<script>
const media = matchMedia('(prefers-color-scheme: dark)');
const state = {scheme: '', changes: 0, capture: 'idle', frames: 0, width: 0, height: 0, file: 'idle'};
function publish() {
  state.scheme = media.matches ? 'dark' : 'light';
  document.title = 'DUX-VERIFY ' + JSON.stringify(state);
  document.getElementById('status').textContent = JSON.stringify(state);
}
media.addEventListener('change', () => { state.changes++; publish(); });
const picker = document.getElementById('picker');
picker.addEventListener('cancel', () => { state.file = 'cancelled'; publish(); });
picker.addEventListener('change', () => { state.file = 'selected:' + picker.files[0].name; publish(); });
document.addEventListener('keydown', event => {
  if (event.key === 'c') document.getElementById('share').click();
  if (event.key === 'o') { state.file = 'pending'; publish(); picker.click(); }
});
document.getElementById('share').onclick = async () => {
  state.capture = 'pending'; publish();
  try {
    const stream = await navigator.mediaDevices.getDisplayMedia({video: true, audio: false});
    const video = document.querySelector('video');
    video.srcObject = stream;
    const canvas = document.querySelector('canvas');
    canvas.width = 320; canvas.height = 200;
    const context = canvas.getContext('2d', {willReadFrequently: true});
    function sampleFrame() {
      try {
        state.frames++;
        state.width = video.videoWidth; state.height = video.videoHeight;
        context.drawImage(video, 0, 0, canvas.width, canvas.height);
        const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
        let nonBlack = 0, minimum = 765, maximum = 0;
        const colors = new Set();
        for (let y = 8; y < canvas.height; y += 16) {
          for (let x = 8; x < canvas.width; x += 16) {
            const i = (y * canvas.width + x) * 4;
            const sum = pixels[i] + pixels[i+1] + pixels[i+2];
            if (sum > 60) nonBlack++;
            minimum = Math.min(minimum, sum); maximum = Math.max(maximum, sum);
            colors.add([pixels[i] >> 3, pixels[i+1] >> 3, pixels[i+2] >> 3].join(','));
          }
        }
        state.pixels = {nonBlack, minimum, maximum, colors: colors.size};
        if (state.frames >= 3 && nonBlack >= 10 && maximum - minimum > 60 && colors.size >= 8) {
          state.capture = 'passed'; canvas.hidden = false; video.hidden = true;
          stream.getTracks().forEach(track => track.stop());
        } else {
          video.requestVideoFrameCallback(sampleFrame);
        }
        publish();
      } catch (error) {
        state.capture = 'error: ' + error.name + ': ' + error.message; publish();
        stream.getTracks().forEach(track => track.stop());
      }
    }
    video.requestVideoFrameCallback(sampleFrame);
    await video.play();
  } catch (error) { state.capture = 'error: ' + error.name + ': ' + error.message; publish(); }
};
publish();
</script>
'''


def verify(vm, passed):
    vm.probe('browser', 'focus-browser')
    vm.key('o')
    vm.probe('browser', 'file-chooser', timeout=90)
    vm.key('esc')
    vm.probe('browser', 'file-chooser-cancelled', timeout=90)
    passed('Chromium open-file request uses Dux Files and responds to cancellation')
    vm.probe('browser', 'focus-browser')
    vm.key('c')
    time.sleep(1)
    # Chromium first asks for the source type, then GNOME selects the
    # actual screen; Chromium finally confirms that selected source.
    source_deadline = time.monotonic() + 60
    while time.monotonic() < source_deadline:
        vm.probe('browser', 'capture-source')
        if click_screen_text(vm, vm.directory / '02-chromium-share-source.png', 'Entire screen'):
            break
        time.sleep(1)
    else:
        raise TimeoutError('Chromium did not display its Entire screen source choice.')
    vm.probe('browser', 'capture-picker', timeout=90)
    vm.click(0.725, 0.245)
    time.sleep(1)
    vm.click(0.935, 0.8)
    vm.probe('browser', 'capture', timeout=90)
    passed('Chromium screen sharing produced nonblank captured desktop content')


def file_chooser_cancelled():
    wait_until('Chromium file chooser cancellation', lambda:
               (state := browser_state()) is not None and state['file'] == 'cancelled')


if __name__ == '__main__':
    action = sys.argv[1]
    if action == 'focus-browser':
        focus_browser()
    elif action == 'file-chooser':
        file_chooser()
    elif action == 'file-chooser-cancelled':
        file_chooser_cancelled()
    elif action == 'capture':
        capture()
    elif action == 'capture-source':
        screenshot('02-chromium-share-source.png')
    elif action == 'capture-picker':
        wait_until('GNOME screen-sharing source chooser', lambda: any(
            'portal' in window.get('app_id', '').lower() and window.get('title') == 'Share Screen'
            for window in windows()))
        screenshot('02-screen-share-picker.png')
    else:
        raise SystemExit('Unknown browser probe')
