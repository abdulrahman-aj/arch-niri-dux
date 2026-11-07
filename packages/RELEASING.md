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
3. Merge, then tag that main commit as `integration-v<version>-<pkgrel>` and push
   the tag. This step publishes. Main merges alone do not
   deliver updates to devices.
4. The tag workflow builds as an unprivileged user, signs in the protected
   `integration-release` environment, publishes GitHub Release assets, and deploys
   the signed repository to GitHub Pages. The build job has no signing secret.
5. Run `make iso` to build with those exact authenticated packages, rather than
   independently rebuilding packages under the same version.
   Then `make verify-release` runs offline acceptance followed by a networked VM
   boot. Real HTTPS repository/package verification uses the installed offline
   trust, checks exact release hashes, and preserves personal config.
   Select the artifact when retaining several ISOs:
   `make verify-release ARGS='--iso out/NAME.iso --directory out/verify-release-VERSION'`.

The protected release and Pages environments allow only `integration-v*` tags.
The publisher also requires the tagged commit to belong to main and refuses a
version older than an already published release. Each release contains every
Dux app. An app version already published keeps its signed bytes. The publisher
refuses an app downgrade, or changed package contents without a new `pkgrel`. A failed Pages deployment can
be retried. The publisher reuses the published release's package bytes. Published
integration packages remain available for clients with older cached databases.
Pages serves only the current Dux app versions, keeping it under its 1 GB limit;
GitHub Releases keep every version.
Do not replace release assets or force-move published tags. Make a new version.

Build ISOs from a trusted checkout. Its public key provides the initial release
verification. Installed systems trust the keyring provisioned offline
by their ISO. Retrying publication preserves package bytes. Signatures and
repository metadata are regenerated. Signatures do not prevent a server replaying an
older signed repository. Release CI runs unit tests and makepkg, not graphical
VM acceptance. Keep the candidate's local VM evidence with the PR.

## Signing and recovery

The [release-key guide](../keys/RECOVERY.md) explains the master and signing keys,
the encrypted backup, inspection, access to each key, and restoring the GitHub
signing secret. **Keep the full encrypted backup off-machine and its password
separately.** GitHub stores only the working signing subkey.

Initial provisioning uses `scripts/release_key.py --passphrase-file PATH`.
Ordinary releases use the existing CI secret. The helper's `--restore` operation
replaces that live secret; it does not rotate or renew keys.

See [rotation and compromise
recovery](../keys/RECOVERY.md#rotation-and-compromise-recovery) for
trust-transition limits and required qualification.
