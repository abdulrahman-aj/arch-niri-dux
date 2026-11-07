# Integration releases

[Package inputs](README.md) describes what our signed repository supplies and
how the baseline metapackage controls app additions. User initialization creates
missing personal defaults; package upgrades do not replay it. Pacman preserves
edits to files listed in the integration recipe's `backup` array through its
`.pacnew` behavior.

## Publish

1. Change `pkgver`/`pkgrel` in `integration/PKGBUILD` and submit a reviewed PR.
   Dux app updates arrive as PRs from the `dux-apps.yml` workflow. GitHub does
   not run CI on PRs created by Actions; close and reopen one to run it. That
   workflow needs "Allow GitHub Actions to create and approve pull requests".
   A merged app update ships with the next integration version.
2. Run `make test`, `make iso-development`, and `make verify` on the candidate.
   Update recorded evidence for changes affecting installation or the desktop.
3. Merge, then tag that main commit as `v<version>-<pkgrel>` and push
   the tag. This step publishes. Main merges alone do not
   deliver updates to devices.
4. The tag workflow builds as an unprivileged user, signs in the protected
   `integration-release` environment, publishes GitHub Release assets, and deploys
   the signed repository to GitHub Pages. The build job has no signing secret. The separate signing job uses the pinned
   Nix toolchain and performs no live `pacman -Syu`. It checks manifest hashes
   and source inputs before signing with the pinned subkey.
5. Run `make iso` to build with those exact authenticated packages, rather than
   independently rebuilding packages under the same version.
   Then `make verify-release` runs offline acceptance followed by a networked VM
   boot. Real HTTPS repository/package verification uses the installed offline
   trust, checks exact release hashes, and preserves personal config.
   Select the artifact when retaining several ISOs:
   `make verify-release ARGS='--iso out/NAME.iso --directory out/verify-release-VERSION'`.

The protected release and Pages environments allow only `v*` tags.
The publisher also requires the tagged commit to belong to main and refuses a
version older than an already published release. Each release contains every
Dux app. An app version already published keeps its signed bytes. The publisher
refuses an app downgrade, or changed package contents without a new `pkgrel`. A failed Pages deployment can
be retried. The publisher reuses the published release's package bytes. Published
integration packages within the checked-in history boundary remain available for clients with older cached databases.
Pages serves only the current Dux app versions, keeping it under its 1 GB limit;
GitHub Releases keep every version.
Do not replace release assets or force-move published tags. Make a new version.

Build ISOs from a trusted checkout. Its public key provides the initial release
verification. Installed systems trust the keyring provisioned offline
by their ISO. Retrying publication preserves package bytes. Signatures and
repository metadata are regenerated. Signatures do not prevent a server replaying an
older signed repository. Release CI runs unit tests and makepkg, not graphical
VM acceptance. Keep the candidate's local VM evidence with the PR.

## Authorize and distribute an ISO

Before each release, update the README verification section to name its intended
ISO version and check its public certificate digest. Merge these instructions and
`.github/workflows/iso-checksums.yml` before creating the release tag.
The workflow must exist on main for dispatch and in the selected package tag.
Use the existing package release tag, for example `v0.3.0-1`; do not create a
second ISO tag or move an existing tag. The protected `integration-release`
environment permits `v*` tags and requires maintainer approval.

Build from a clean trusted checkout of that tag and retain the local acceptance
evidence from the publish steps above. Choose exactly one release ISO. Copy it
to the canonical filename derived from the tag's version, then prepare its checksum:

```sh
cp out/CHOSEN.iso out/arch-niri-dux-0.3.0-x86_64.iso
make iso-checksums ARGS='--tag v0.3.0-1 --iso out/arch-niri-dux-0.3.0-x86_64.iso'
```

This light target hashes only the explicit file and writes
`out/iso-checksums/SHA256SUMS`. It refuses a noncanonical filename or an existing
checksum file. Retain or explicitly remove an earlier candidate's checksum file
before preparing another. Do not sign the build's `out/SHA256SUMS`, which may
include other ISOs retained in `out/`.

Dispatch **ISO checksum authorization** at the package tag, supplying the printed
64-character digest. For example, replace `DIGEST` below with that value:

```sh
gh workflow run iso-checksums.yml --ref v0.3.0-1 -f sha256=DIGEST
```

The secret-free validation job checks the tag's commit belongs to main and shows
the canonical filename, digest and source commit in its summary. Compare all
three against your trusted checkout and qualified artifact **before approving**
the `integration-release` job. Approval means "I authorize these exact ISO bytes."
ISOs are not byte-reproducible. CI does not receive, build, inspect or reproduce
the ISO. A compromised local builder or mistaken approval can therefore authorize
a malicious ISO. The shared
signing subkey also shares the package signer's compromise consequences.

After approval, the separate signing job repeats validation using the pinned Nix
tools. It imports only the existing CI secret into a disposable GnuPG home, signs
canonical `SHA256SUMS` with the exact pinned subkey, verifies the result, and removes
the private material and agent before upload. It appends `SHA256SUMS`,
`SHA256SUMS.sig` and `integration-signing.gpg` to the existing published package
release. It never creates a release, replaces assets, or publishes the ISO.

Identical reruns verify and preserve existing assets, including the original
signature bytes. Interrupted uploads can resume by adding only missing assets.
Any conflicting checksum, certificate or invalid signature stops publication.
Fix the cause; do not delete assets to replace a published ISO. Use a new package
release tag for changed ISO bytes. Canonical ISO filenames omit `pkgrel`, so keep
each tag's metadata separate and tell friends which release to use.

Share the canonical ISO through Google Drive, USB or another chosen host. Give
friends the release metadata and the trusted README verification instructions.
Check that the README certificate digest still matches the published certificate
whenever its bindings or revocations change. Neither a checksum nor a public key
next to an unauthenticated download establishes trust by itself.

## Signing and recovery

The [release-key guide](../keys/RECOVERY.md) explains the master and signing keys,
the encrypted backup, inspection, access to each key, and restoring the GitHub
signing secret. **Keep the full encrypted backup off-machine and its password
separately.** GitHub stores only the working signing subkey.

Initial provisioning uses `scripts/release_key.py --passphrase-file PATH`.
Ordinary releases use the existing CI secret. Preparation and `--restore` are offline by default. Only explicit `--upload`
replaces the live secret; restoration does not rotate or renew keys.

See [rotation and compromise
recovery](../keys/RECOVERY.md#rotation-and-compromise-recovery) for
trust-transition limits and required qualification.
