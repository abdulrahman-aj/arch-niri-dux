import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('bump', ROOT / 'scripts/bump-dux-apps.py')
bump = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bump)

REPOSITORY = 'https://github.com/duxlabs-dev/dux-example'
RECIPE = """pkgname=dux-example-bin
pkgver=2.1.0
pkgrel=3
source=("LICENSE-${pkgver}::https://raw.githubusercontent.com/duxlabs-dev/dux-example/v${pkgver}/LICENSE")
source_x86_64=("dex-${pkgver}.gz::https://github.com/duxlabs-dev/dux-example/releases/download/v${pkgver}/dex-linux-amd64.gz")
sha256sums=('aaaa'
            'bbbb')
sha256sums_x86_64=('cccc')
sha256sums_aarch64=('dddd')

package() {
  install -Dm755 dex "$pkgdir/usr/bin/dex"
}
"""


def evaluate(pkgbuild, expression):
    with tempfile.NamedTemporaryFile('w', suffix='PKGBUILD') as file:
        file.write(pkgbuild)
        file.flush()
        return subprocess.check_output(['bash', '-c', f'source "$1"; {expression}', 'bash', file.name], text=True)


class RecipeUpdateTests(unittest.TestCase):
    def test_new_version_resets_pkgrel_and_replaces_every_checksum_array(self):
        updated = bump.set_version(RECIPE, '2.2.0')
        updated = bump.set_checksums(updated, "sha256sums=('1111')\nsha256sums_x86_64=('2222')\n"
                                              "sha256sums_aarch64=('3333')\n")
        values = evaluate(updated, 'echo "$pkgver-$pkgrel ${sha256sums[*]} ${sha256sums_x86_64[*]} '
                                   '${sha256sums_aarch64[*]} ${source_x86_64[*]}"; declare -F package')
        self.assertEqual(values.split(), [
            '2.2.0-1', '1111', '2222', '3333',
            'dex-2.2.0.gz::https://github.com/duxlabs-dev/dux-example/releases/download/v2.2.0/dex-linux-amd64.gz',
            'package'])

    def test_recipe_without_a_literal_version_is_refused(self):
        with self.assertRaisesRegex(ValueError, 'pkgver'):
            bump.set_version(RECIPE.replace('pkgver=2.1.0\n', 'pkgver=$(date)\n'), '2.2.0')


class ReleaseDigestTests(unittest.TestCase):
    def setUp(self):
        self.release = dict(tag_name='v2.2.0', assets=[dict(name='dex-linux-amd64.gz', digest='sha256:' + 'c' * 64)])

    def fields(self, asset_sum='c' * 64, asset_url=f'{REPOSITORY}/releases/download/v2.2.0/dex-linux-amd64.gz'):
        return {'source': ['LICENSE::https://raw.githubusercontent.com/duxlabs-dev/dux-example/v2.2.0/LICENSE',
                           'dsearch.service'],
                'sha256sums': ['a' * 64, 'b' * 64],
                'source_x86_64': [f'dex.gz::{asset_url}'], 'sha256sums_x86_64': [asset_sum]}

    def test_sources_matching_the_release_are_accepted(self):
        bump.check_sources(self.fields(), REPOSITORY, self.release)

    def test_asset_checksum_must_equal_the_published_digest(self):
        with self.assertRaisesRegex(ValueError, 'digest'):
            bump.check_sources(self.fields(asset_sum='e' * 64), REPOSITORY, self.release)

    def test_asset_without_a_published_digest_is_refused(self):
        self.release['assets'][0]['digest'] = None
        with self.assertRaisesRegex(ValueError, 'digest'):
            bump.check_sources(self.fields(), REPOSITORY, self.release)

    def test_source_from_another_release_or_host_is_refused(self):
        for url in (f'{REPOSITORY}/releases/download/v2.1.0/dex-linux-amd64.gz',
                    'https://example.invalid/dex-linux-amd64.gz'):
            with self.subTest(url=url), self.assertRaisesRegex(ValueError, 'not from the upstream release'):
                bump.check_sources(self.fields(asset_url=url), REPOSITORY, self.release)


if __name__ == '__main__':
    unittest.main()
