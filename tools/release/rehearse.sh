#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
iso=${1:?Usage: make rehearse-release ARGS='ISO TAG'}
tag=${2:?Usage: make rehearse-release ARGS='ISO TAG'}
mkdir -p out/rehearsal-repo
bsdtar -xf "$iso" -C out/rehearsal-repo repo
chmod u+w out/rehearsal-repo/repo
cmp out/integration/arch-niri-dux.db out/rehearsal-repo/repo/offline.db
cp out/integration/arch-niri-dux.db* out/rehearsal-repo/repo/
rm -f out/rehearsal-port
python3 -m tools.build.serve_state --directory out/rehearsal-repo/repo --port-file out/rehearsal-port >out/rehearsal-http.log 2>&1 &
server=$!
trap 'kill "$server"' EXIT
for _ in $(seq 1 100); do
    test -s out/rehearsal-port && break
    kill -0 "$server"
    sleep 0.05
done
grep -Eq '^[0-9]+$' out/rehearsal-port
port=$(cat out/rehearsal-port)
stamp=$(date +%s)
make verify-release ARGS="--tag $tag --iso $iso --repository-url http://10.0.2.2:${port} --directory out/rehearsal-${stamp}"
