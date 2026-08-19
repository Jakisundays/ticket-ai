#!/bin/bash
# Compila y corre scripts/test_offline_invoice_validation.ts sin agregar
# ninguna dependencia nueva al proyecto (no hay vitest/jest instalado) --
# usa el `tsc` que ya viene en devDependencies para transpilar a JS plano
# y lo corre con node. Ver el docstring del .ts para el detalle.
set -euo pipefail
cd "$(dirname "$0")/.."

OUT_DIR="$(mktemp -d)"
trap 'rm -rf "$OUT_DIR"' EXIT

npx tsc scripts/test_offline_invoice_validation.ts lib/invoice-validation.ts \
  --outDir "$OUT_DIR" --module commonjs --target es2020 \
  --moduleResolution node --esModuleInterop --skipLibCheck

node "$OUT_DIR/scripts/test_offline_invoice_validation.js"
