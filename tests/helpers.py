"""Shared fixtures for source tests: repo root and hyphen-script loading."""

from pathlib import Path

from scripts.verification._common import load_module as load_script

__all__ = ['ROOT', 'load_script']

ROOT = Path(__file__).resolve().parents[1]
