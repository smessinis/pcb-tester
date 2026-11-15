#!/usr/bin/env bash
# Run the full check suite on a public STM32H743 flight-controller project.
# The board is not vendored here. This clones it, runs PCB Tester, and
# writes HTML, PDF and JSON reports under ./demo-out.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEMO="${DEMO_DIR:-$ROOT/.demo}"
OUT="${1:-$ROOT/demo-out}"
REPO="https://github.com/SREESAIARJUN/nano-h743-aio-flight-controller.git"
mkdir -p "$DEMO" "$OUT"
if [[ ! -f "$DEMO/nano/kicad/nano-h743-aio.kicad_pro" ]]; then
  rm -rf "$DEMO/nano"
  git clone --depth 1 "$REPO" "$DEMO/nano"
fi
cd "$ROOT"
python3 -m pcbtester run "$DEMO/nano/kicad" \
  --checks erc,drc,connectivity,mcu_pins,spice \
  --work "$DEMO/work" \
  --html "$OUT/nano-h743-report.html" \
  --pdf "$OUT/nano-h743-report.pdf" \
  --json "$OUT/nano-h743-report.json"
echo "wrote $OUT"
