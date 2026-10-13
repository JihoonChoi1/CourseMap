#!/usr/bin/env bash
# Build the browser demo into demo/dist: the Go engine as WebAssembly, Go's JS glue, the page, and the UBC data.
#
#   demo/build.sh                      # then serve it: python3 -m http.server -d demo/dist 8080
set -euo pipefail

cd "$(dirname "$0")/.."
OUT=demo/dist
rm -rf "$OUT"
mkdir -p "$OUT/data"

(cd goengine && GOOS=js GOARCH=wasm go build -trimpath -ldflags="-s -w" -o "../$OUT/coursemap.wasm" ./cmd/wasm)
cp "$(go env GOROOT)/lib/wasm/wasm_exec.js" "$OUT/"   # must come from the same Go version as the build
cp demo/index.html demo/worker.js "$OUT/"
cp data/ubc/catalog.json data/ubc/programs.json data/ubc/students.json "$OUT/data/"
touch "$OUT/.nojekyll"

echo "built $OUT ($(du -sh "$OUT" | cut -f1))"
