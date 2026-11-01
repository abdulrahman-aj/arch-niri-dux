#!/usr/bin/env python3
"""Verify the real personal bootstrap on a restored disposable VM disk."""

import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile

from verify import VM, PASSWORD, installed_login


GUEST_SETUP = r'''#!/bin/bash
set -euo pipefail
nm-online -s -q --timeout=180
mkdir -p /run/verify /home/tester/dotfiles /home/tester/agent-config
tar -xf /run/verify/dotfiles.tar -C /home/tester/dotfiles
tar -xf /run/verify/agent-config.tar -C /home/tester/agent-config
chown -R tester:tester /home/tester/dotfiles /home/tester/agent-config
install -d -m 755 /usr/local/lib/verify-personal
cat > /usr/local/lib/verify-personal/yay <<'EOF'
#!/bin/sh
exec /usr/bin/yay --noconfirm --answerdiff None --answerclean None "$@"
EOF
cat > /usr/local/lib/verify-personal/chsh <<'EOF'
#!/bin/sh
exec sudo /usr/bin/chsh "$@"
EOF
chmod 755 /usr/local/lib/verify-personal/{yay,chsh}
# Sudo's last matching rule wins; this must sort after the production wheel rule.
printf 'tester ALL=(ALL:ALL) NOPASSWD: ALL\n' > /etc/sudoers.d/zz-verify-personal
chmod 440 /etc/sudoers.d/zz-verify-personal
visudo -cf /etc/sudoers.d/zz-verify-personal
runuser -u tester -- sudo -n true
runuser -u tester -- env HOME=/home/tester USER=tester LOGNAME=tester \
    PATH=/usr/local/lib/verify-personal:/usr/local/bin:/usr/bin:/bin \
    bash /run/verify/personal-user.sh > /run/verify/bootstrap.log 2>&1
'''

CHECK_PORTABLE = r'''
for dir in hypr omarchy alacritty opencode; do
    while IFS= read -r -d '' link; do
        case "$(readlink -f "$link")" in
            "$HOME/dotfiles/"*)
                echo "Unexpected dotfiles link: $link" >&2
                exit 1 ;;
        esac
    done < <(find "$HOME/.config/$dir" -type l -print0 2>/dev/null)
done
'''

GUEST_USER = r'''#!/bin/bash
set -euo pipefail
cd "$HOME/dotfiles"
niri_before=$(sha256sum "$HOME/.config/niri/config.kdl")
# Some tools expect a repository, but archives intentionally omit git internals.
git init -q
git -C "$HOME/agent-config" init -q
make
test "$(sha256sum "$HOME/.config/niri/config.kdl")" = "$niri_before"

test "$(getent passwd tester | cut -d: -f7)" = "$(command -v fish)"
test "$(readlink -f "$HOME/.config/fish/config.fish")" = "$HOME/dotfiles/home/.config/fish/config.fish"
test "$(readlink -f "$HOME/.config/herdr/config.toml")" = "$HOME/dotfiles/home/.config/herdr/config.toml"
test "$(readlink -f "$HOME/.codex/AGENTS.md")" = "$HOME/agent-config/global/AGENTS.md"
''' + CHECK_PORTABLE + r'''
for mime in text/html x-scheme-handler/http x-scheme-handler/https; do
    test "$(xdg-mime query default "$mime")" = zen.desktop
done
fish -c 'type -q fisher'
for app in fish stow gh delta starship zoxide eza fzf fd bat lazygit jq zeditor mise node npm yay wt google-chrome-stable zen-browser t3code; do
    command -v "$app" >/dev/null || { echo "Missing command: $app" >&2; exit 1; }
done
fish -c 'functions -q zw'
mise exec herdr -- herdr --version
mise exec codex -- codex --version
mise exec npm:hunkdiff -- hunk --version
mise exec npm:@opencode/cli -- opencode --version
'''


def archive(repo, destination):
    sha = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', '--verify', 'HEAD^{commit}'], text=True).strip()
    with destination.open('wb') as output:
        subprocess.run(['git', '-C', str(repo), 'archive', '--format=tar', sha], stdout=output, check=True)
    return sha


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--disk', type=Path, required=True, help='Disposable qcow2 with pristine snapshot')
    parser.add_argument('--dotfiles', type=Path, required=True, help='Dotfiles Git repository')
    parser.add_argument('--agent-config', type=Path, required=True, help='Agent config Git repository')
    parser.add_argument('--directory', type=Path, help='Fresh evidence directory; default under /tmp')
    args = parser.parse_args()
    disk = args.disk.resolve(strict=True)
    if not disk.is_file():
        parser.error('--disk must be an existing qcow2 file')
    if args.directory:
        directory = args.directory
        directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    else:
        directory = Path(tempfile.mkdtemp(prefix='dux-personal-', dir='/tmp'))
    # qemu-img acquires the image lock; never bypass a running QA VM's lock.
    subprocess.run(['qemu-img', 'snapshot', '-a', 'pristine', str(disk)], check=True)
    print(f'VM evidence: {directory}', flush=True)
    vm = None
    try:
        shas = {'dotfiles': archive(args.dotfiles, directory / 'dotfiles.tar'),
                'agent-config': archive(args.agent_config, directory / 'agent-config.tar')}
        (directory / 'sources.json').write_text(json.dumps(shas, indent=2) + '\n')
        (directory / 'personal-setup.sh').write_text(GUEST_SETUP)
        (directory / 'personal-user.sh').write_text(GUEST_USER)
        vm = VM(directory, None, disk)
        vm.start(cdrom=False, network=True)
        installed_login(vm)
        vm.command('mkdir -p /run/verify; mount -t 9p -o trans=virtio,version=9p2000.L verify /run/verify')
        vm.command('bash /run/verify/personal-setup.sh', timeout=7200)
        vm.command('sync')
        vm.stop()
        (directory / 'PERSONAL-PASSED').write_text('Personal bootstrap and assertions passed.\n')
        print(f'Personal bootstrap passed. Evidence: {directory}', flush=True)
        return 0
    except Exception as error:
        (directory / 'FAILURE.txt').write_text(str(error) + '\n')
        print(f'Personal bootstrap failed: {error}\nEvidence: {directory}', file=sys.stderr)
        return 1
    finally:
        if vm is not None:
            vm.stop()


if __name__ == '__main__':
    sys.exit(main())
