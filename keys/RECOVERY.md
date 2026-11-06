# Release keys and recovery

## What each key does

| Item | Purpose | Location |
| --- | --- | --- |
| Master private key | Certifies signing subkeys and revokes them | Encrypted recovery backup only |
| Signing private subkey | Signs our packages, repository databases and release manifests | GitHub environment `integration-release`, secret `PACKAGE_SIGNING_KEY`; also in the recovery backup |
| Public certificate | Contains the master public key, signing public subkeys and their binding signatures | `keys/integration-signing.gpg`; installed into pacman's keyring by the ISO |
| Encrypted recovery backup | Contains both the master private key and signing private subkey | Initially `out/integration-recovery.gpg`; keep an off-machine copy |

The master's binding signature authorizes a signing subkey under the same project
identity. GnuPG creates and verifies that signature. A hash alone would not prove
authorization.

The recovery password decrypts the backup file. It is not needed for ordinary CI
releases or laptop updates. Inside the decrypted bundle, the private keys have no
additional passphrase. Protect any imported keyring or private-key export.

The current master fingerprint is:

```text
BB9E749687704AB10C3556FDC61E39EC09709E11
```

The current signing-subkey fingerprint is:

```text
B2B09CA6137008FD53A8CDD590B58347E44B9F88
```

The master has no expiry. The current signing subkey expires on **3 November
2028 at 13:02:22 UTC**. These values describe the initial identity; inspect the
certificate and update this guide when changing keys.

## Before erasing a development machine

Copy the encrypted backup off the machine. A descriptive filename is:

```text
Keys/arch-niri-dux/master-and-signing-private-keys-2026-11-04.gpg
```

Renaming the file does not change its contents. The date is when this backup was
created; use the new backup date after a key change. The whole `out/` directory is
Git-ignored, so pushing the repository does not back it up.

Keep the file in a private cloud folder or on a spare USB, and keep its password
separately in a password manager. Before erasing the machine, download a backup
copy and complete the inspection below with the recovery password. A matching
file hash proves the copy is intact; successful decryption and the expected
fingerprints prove it is the recovery bundle you need.

Keep only public keys in Git. Do not upload a decrypted private-key export.

## Inspect or access the keys

Use a trusted machine. From the repository root, open a Bash terminal and set the
downloaded backup's path:

```bash
backup='/absolute/path/to/master-and-signing-private-keys-2026-11-04.gpg'
set -o pipefail
recovery_home=$(mktemp -d)
chmod 700 "$recovery_home"
```

Inspect the bundle without importing it or writing a decrypted export:

```bash
gpg --homedir "$recovery_home" --decrypt "$backup" \
    | gpg --homedir "$recovery_home" --show-keys --with-subkey-fingerprint
```

Enter the recovery password when prompted. The primary key with `[C]` is the
master certification key. The subkey with `[S]` is the signing key. Compare both
fingerprints with the values above. This command displays metadata, not private
key bytes. `pipefail` makes a decryption failure fail the pipeline too.

To work with either private key, import the bundle into the same separate
keyring:

```bash
gpg --homedir "$recovery_home" --decrypt "$backup" \
    | gpg --homedir "$recovery_home" --import
gpg --homedir "$recovery_home" --list-secret-keys --with-subkey-fingerprint
```

Both private keys are now available in that keyring. Master-key management uses
the primary fingerprint; package signing uses the signing subkey. Pass
`--homedir "$recovery_home"` to GnuPG commands so they use this keyring rather
than your normal one. Importing alone does not change CI or installed devices.

For the current single-signing-subkey layout, export the CI key without the
master private material:

```bash
primary='BB9E749687704AB10C3556FDC61E39EC09709E11'
gpg --homedir "$recovery_home" --armor \
    --output "$recovery_home/signing-subkey.asc" \
    --export-secret-subkeys "$primary"
```

This export includes the public certificate, a placeholder for the master
private key, and the signing private subkey. It is not encrypted. It is suitable
for CI secret import, not for replacing the full recovery backup. Do not print
it in logs or commit it. With multiple signing subkeys, select and verify the
intended CI export before uploading it; the current helper assumes one subkey.

After inspection or key work, remove the temporary keyring and any export in it:

```bash
gpgconf --homedir "$recovery_home" --kill all
rm -r -- "$recovery_home"
unset recovery_home
```

This removes these working copies, not the encrypted backup. File removal is not
a guarantee of secure erasure on SSDs or snapshotting filesystems; use encrypted
storage on the trusted machine.

## Restore GitHub signing after losing the CI secret

GitHub can use its stored secret in an authorized workflow, but its Settings UI
and secrets API do not return the stored value. Erasing this PC does not erase
that secret. GitHub has no master private key, so it cannot replace the full
recovery backup.

The existing helper restores the **same identity and subkey** from the backup.
It does not rotate keys or renew expiry. From a trusted checkout of the project,
authenticate `gh` with access to the repository's `integration-release`
environment. Create a temporary password file without putting the password in
shell history:

```bash
umask 077
password_file=$(mktemp)
read -r -s -p 'Recovery password: ' recovery_password
printf '\n'
printf '%s\n' "$recovery_password" > "$password_file"
unset recovery_password
./scripts/build-in-dev python3 scripts/setup-release-key.py \
    --passphrase-file "$password_file" --restore "$backup"
rm -- "$password_file"
unset password_file
```

**This replaces the live `PACKAGE_SIGNING_KEY` GitHub secret. Do not run it just
to inspect a backup.** The helper verifies the primary fingerprint against
`keys/integration-signing.fingerprint`, refreshes the checked-in public
certificate, and uploads only the signing private subkey. Remove the password
file even if restoration fails. No new backup is made during restoration.

## Rotation and compromise recovery

Normal publication uses the CI signing subkey. Only initial provisioning and
key maintenance need the master. Keep the master out of CI.

For routine rotation while the old key is safe and valid, the master certifies a
new signing subkey. A release signed by the old subkey distributes the updated
public certificate. Clients that install it can verify future releases signed
by the new subkey. Then replace the CI secret and verify the next release uses
the intended signer. Keep historical public subkeys so old releases remain
verifiable, and create a fresh encrypted full backup after changing keys.

Publishing that transition release does not ensure every client installs it.
A client that skips it can reject the next newly signed repository database
before it can download the keyring update. Retaining old package files does not
solve this. The current keyring hook runs after an integration-package upgrade;
it does not refresh trust before repository verification.

For compromise recovery, use the safe master key to revoke the stolen signing
subkey and certify a replacement. Deleting the GitHub secret does not invalidate
a stolen copy. Clients must receive the revocation and replacement through a
trusted recovery path; revocation is not an instant remote kill switch. If a
malicious package was installed, replacing keys does not repair that device.

**Neither transition is currently qualified end to end.** The helper rejects
multiple signing subkeys, and the publisher rejects historical packages signed
by a now-revoked key. Emergency publication and clients that miss a transition
need a tested procedure. Track both paths in
[issue #52](https://github.com/abdulrahman-aj/arch-niri-dux/issues/52).
Do not treat this overview as an executable rotation or incident-response
runbook. Do not disable signature checks to get past a key error.

The encrypted backup and its password are both needed to recover the master.
If those are lost and only the CI subkey survives, creating a new project
identity requires manual trust recovery on installed devices.
