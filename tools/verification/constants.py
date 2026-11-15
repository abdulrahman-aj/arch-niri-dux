"""Values shared by host controls and guest probes."""

from pathlib import Path


PASSWORD = 'vm-verification-only'
LOCAL_PROBES = Path('/var/tmp/arch-niri-dux-suspend')
PROMPT = 'dux-update-prompt.png'
NONE = 'dux-update-none.png'
AVAILABLE = 'dux-update-available.png'
PANEL = 'dux-update-panel.png'
BAR_MIDDLE = 22
BAR_ABOVE_PILLS = 6
