"""Changed Dux app recipes must bump pkgver-pkgrel before release."""

import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import dux_apps as apps

RELEASE_TAG = re.compile(r'v[0-9]+(?:\.[0-9]+)*-[0-9]+(?:\.[0-9]+)*$')


def latest_release_tag(root):
    """Newest v* tag that is an ancestor of HEAD, ordered with vercmp like the publisher."""
    tags = subprocess.check_output(['git', '-C', str(root), 'tag', '--list', 'v*'], text=True).split()
    candidates = [tag for tag in tags if RELEASE_TAG.fullmatch(tag)]
    merged = [
        tag
        for tag in candidates
        if subprocess.run(['git', '-C', str(root), 'merge-base', '--is-ancestor', tag, 'HEAD'], check=False).returncode
        == 0
    ]
    if not merged:
        raise ValueError('No published release tag in history.')
    best = merged[0]
    for tag in merged[1:]:
        if int(subprocess.check_output(['vercmp', tag[1:], best[1:]], text=True).strip()) > 0:
            best = tag
    return best


def show_tag(root, tag, *paths):
    for path in paths:
        try:
            return subprocess.check_output(
                ['git', '-C', str(root), 'show', f'{tag}:{path}'], text=True, stderr=subprocess.DEVNULL
            )
        except subprocess.CalledProcessError:
            continue
    raise ValueError(f'None of {paths} exists in {tag}.')


def base_recipes(root, tag):
    text = show_tag(root, tag, 'packages/dux-apps-recipes.json', 'packages/aur-recipes.json')
    return json.loads(text)


def base_versions(root, tag, recipes):
    versions = {}
    for recipe in recipes:
        name = recipe['name']
        text = show_tag(root, tag, f'packages/dux-apps/{name}/.SRCINFO', f'packages/aur/{name}/.SRCINFO')
        versions[name] = apps.version_from_text(text, f'{tag}:{name}/.SRCINFO')
    return versions


def find_violations(base, base_version, current, current_version):
    """Recipe file changes without a version change, the release-time pkgrel rule."""
    old_files = {recipe['name']: recipe['files'] for recipe in base}
    new_files = {recipe['name']: recipe['files'] for recipe in current}
    problems = []
    for name, files in new_files.items():
        if name not in old_files:
            continue
        if files != old_files[name] and current_version.get(name) == base_version.get(name):
            problems.append(
                f'{name} changed since the release without a new pkgrel '
                f'(still {current_version.get(name)}); bump pkgrel and refresh .SRCINFO.'
            )
    return problems


def live_violations(root):
    tag = latest_release_tag(root)
    old = base_recipes(root, tag)
    old_versions = base_versions(root, tag, old)
    declared = json.loads((root / 'packages/dux-apps-recipes.json').read_text())
    new_versions = {}
    for recipe, source, _ in apps.recipes(root):
        new_versions[recipe['name']] = apps.version(source)
    return tag, find_violations(old, old_versions, declared, new_versions)


class RecipeBumpTests(unittest.TestCase):
    def test_changed_recipe_without_a_bump_fails(self):
        base = [dict(name='dux-example-bin', files={'.SRCINFO': 'a', 'PKGBUILD': 'b'})]
        current = [dict(name='dux-example-bin', files={'.SRCINFO': 'a', 'PKGBUILD': 'CHANGED'})]
        problems = find_violations(base, {'dux-example-bin': '2.1.2-1'}, current, {'dux-example-bin': '2.1.2-1'})
        self.assertEqual(len(problems), 1)
        self.assertIn('dux-example-bin', problems[0])

    def test_changed_recipe_with_a_bump_passes(self):
        base = [dict(name='dux-example-bin', files={'.SRCINFO': 'a', 'PKGBUILD': 'b'})]
        current = [dict(name='dux-example-bin', files={'.SRCINFO': 'CHANGED', 'PKGBUILD': 'CHANGED'})]
        self.assertEqual(
            find_violations(base, {'dux-example-bin': '2.1.2-1'}, current, {'dux-example-bin': '2.1.2-2'}), []
        )

    def test_unchanged_recipe_passes(self):
        recipe = [dict(name='dux-example-bin', files={'.SRCINFO': 'a', 'PKGBUILD': 'b'})]
        self.assertEqual(
            find_violations(recipe, {'dux-example-bin': '2.1.2-1'}, recipe, {'dux-example-bin': '2.1.2-1'}), []
        )

    def test_published_recipes_require_a_bump(self):
        tag, problems = live_violations(ROOT)
        self.assertEqual(problems, [], f'Base release {tag} still publishes these versions: {problems}')


if __name__ == '__main__':
    unittest.main()
