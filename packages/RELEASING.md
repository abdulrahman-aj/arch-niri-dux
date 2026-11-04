# Integration releases

Installed systems receive `arch-niri-dux` and `arch-niri-dux-baseline` updates
through ordinary full pacman/Dux/Yay updates. Arch and Dux keep their existing
sources. Our small repository contains only our two packages, with required
package and database signatures. Personal files are absent-only seeds; editable
system files use pacman's `backup`/`.pacnew` behavior. Removing the baseline
metapackage opts out of its future app additions, without removing integration.

## Publish

1. Change `pkgver`/`pkgrel` in `integration/PKGBUILD` and submit a reviewed PR.
2. Run `make test`, `make iso-development`, and `make verify` on the candidate.
   Update recorded evidence for changes affecting installation or the desktop.
3. Merge, then tag that main commit as `integration-v<version>-<pkgrel>` and push
   the tag. This is the deliberate publication step; main merges alone do not
   deliver updates to devices.
4. The tag workflow builds as an unprivileged user, signs in the protected
   `integration-release` environment, publishes GitHub Release assets, and deploys
   the signed repository to GitHub Pages. The build job has no signing secret.
5. Run `make iso` to build with those exact authenticated packages, rather than
   independently rebuilding packages under the same version.
   Then `make verify-release` runs offline acceptance followed by a networked VM
   boot: real HTTPS repository/package verification uses the installed offline
   trust, checks exact release hashes, and preserves personal config.
   Select the artifact explicitly when retaining several ISOs:
   `make verify-release ARGS='--iso out/NAME.iso --directory out/verify-release-VERSION'`.

The protected release and Pages environments allow only `integration-v*` tags.
The publisher also requires the tagged commit to belong to main and refuses a
version older than an already published release. A failed Pages deployment can
be retried: the publisher reuses the published release's package bytes. Published
versioned packages remain available for clients with older cached databases.
Do not replace release assets or force-move published tags; make a new version.

ISO builders must start from a trusted checkout: its public identity bootstraps
release verification. Installed systems trust the keyring provisioned offline
by their ISO. Retrying publication preserves package bytes; signatures and
repository metadata are regenerated. Signatures do not prevent a server replaying an
older signed repository. Release CI runs unit tests and makepkg, not graphical
VM acceptance. Keep the candidate's local VM evidence with the PR.

## Signing and recovery

Initial provisioning uses `scripts/setup-release-key.py --passphrase-file PATH`.
It uploads only a signing subkey to GitHub's `integration-release` environment
as `PACKAGE_SIGNING_KEY`, writes the public identity to `keys/`, and saves the
full private certification/signing key as `out/integration-recovery.gpg`, encrypted
with the supplied recovery password. Private keys are never source inputs.

**Store that recovery file off this machine, and its password separately.**
GitHub's copy keeps existing CI releases working after a laptop reinstall. The
encrypted recovery copy also lets you restore the CI secret if it is lost:

```sh
python3 scripts/setup-release-key.py --passphrase-file PATH --restore RECOVERY.gpg
```

The checked-in fingerprint must match the recovery identity. The signing subkey
expires after two years. Before expiry, use the recovered certification key to
renew it or certify a replacement signing subkey under the same primary identity,
retain the historical public subkeys, ship the updated public keyring
in a release signed by the still-trusted subkey, then replace the CI secret.
Historical package verification accepts expired public subkeys retained under
that primary; fetched releases require an unexpired signing subkey. Devices left
offline past the trust overlap may need a manual keyring refresh.
Loss or compromise without a trusted overlap requires manual trust recovery;
ordinary signatures cannot bootstrap a wholly new identity automatically.

New ISOs install the public keyring offline and enable the repository. Updating
the keyring package refreshes that trust through a pacman hook. The 2.1 release is a clean configuration baseline: all 2.0 installations must
reinstall or explicitly adopt its Niri entrypoint. No automatic 2.0 home-config
migration is provided. Keep normal package-update qualification from 2.1 onward.
