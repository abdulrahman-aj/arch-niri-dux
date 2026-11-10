#!/bin/sh
# Rewrite our untouched updates seed for the release button. The Python helper
# owns the JSON work; this wrapper keeps the migration runner's Bash contract.
set -eu
exec /usr/bin/python3 /usr/lib/arch-niri-dux/migrate-dux-release-button.py
