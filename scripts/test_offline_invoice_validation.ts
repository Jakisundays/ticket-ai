/**
 * Test OFFLINE (sin red, sin PocketBase, sin BAS) para
 * lib/invoice-validation.ts:validarFacturaParaConfirmar -- específicamente
 * la remoción (2026-08-19) de las validaciones viejas de ítems que seguían
 * bloqueando facturas reales con descuentos/bonificaciones negativas pese a
 * que invoice.total ya no depende de los ítems desde el cambio de
 * arquitectura del mismo día (ver utils/bas_payload.py en Invoicy).
 *
 * Mismo criterio que los tests offline de Invoicy (scripts/test_offline_*.py):
 * importa la función REAL (no la reimplementa), sin mocks -- acá no hace
 * falta ningún doble porque validarFacturaParaConfirmar es una función pura,
 * sin red ni estado.
 *
 * Uso: npx tsc scripts/test_offline_invoice_validation.ts lib/invoice-validation.ts \
 *        --outDir /tmp/invoice-validation-test --module commonjs --target es2020 \
 *        --moduleResolution node --esModuleInterop \
 *      && node /tmp/invoice-validation-test/scripts/test_offline_invoice_validation.js
 * (o correr scripts/run_test_offline_invoice_validation.sh, que hace exactamente esto)
 * Sale con código 0 si todo pasa, 1 si algo falla.
 */

import { validarFacturaParaConfirmar, InvoiceDraftForValidation, ItemDraftForValidation } from "../lib/invoice-validation";

const FALLOS: string[] = [];

function check(descripcion: string, condicion: boolean) {
  const estado = condicion ? "OK" : "FALLA";
  console.log(`  [${estado}] ${descripcion}`);
  if (!condicion) FALLOS.push(descripcion);
}

const DRAFT_BASE: InvoiceDraftForValidation = {
  emisor_cuit: "20111111111",
  moneda: "ARS",
  fecha_emision: "2026-08-01",
  numero_comprobante: "00010-00001854",
  cae: "12345678901234",
  cae_vencimiento: "2026-09-01",
  total: 4850.0,
};

function item(precio_total: number, id = "item-1"): ItemDraftForValidation {
  return { id, precio_total };
}

console.log("=".repeat(78));
console.log("A -- ítem con descuento/precio_total negativo -> NO bloquea");
console.log("=".repeat(78));
{
  const r = validarFacturaParaConfirmar(DRAFT_BASE, [item(-7356.41)]);
  check("isValid == true (el ítem negativo no bloquea)", r.isValid === true);
  check("itemErrors vacío (sin marca roja en la fila)", Object.keys(r.itemErrors).length === 0);
  check("messages no contiene 'Hay ítems con datos inconsistentes'", !r.messages.includes("Hay ítems con datos inconsistentes"));
}

console.log();
console.log("=".repeat(78));
console.log("B -- bonificación negativa junto a un ítem positivo -> NO bloquea (caso real Telefónica)");
console.log("=".repeat(78));
{
  const items: ItemDraftForValidation[] = [
    { id: "movistar", precio_total: 17431.8 },
    { id: "bonificacion", precio_total: -12598.74 },
  ];
  const r = validarFacturaParaConfirmar(DRAFT_BASE, items);
  check("isValid == true", r.isValid === true);
  check("itemErrors vacío para ambos ítems", Object.keys(r.itemErrors).length === 0);
}

console.log();
console.log("=".repeat(78));
console.log("C -- suma de ítems distinta de invoice.total -> NO bloquea");
console.log("=".repeat(78));
{
  // invoice.total=4850.00 (DRAFT_BASE), suma de ítems = 100 -- deliberadamente
  // sin ninguna relación aritmética entre sí.
  const r = validarFacturaParaConfirmar(DRAFT_BASE, [item(100, "a"), item(0.01, "b")]);
  check("isValid == true pese al descalce ítems vs. total", r.isValid === true);
  check("sin itemsSummaryError", r.itemsSummaryError === null);
  check("sin mensaje de descalce en 'messages'", !r.messages.some((m) => m.toLowerCase().includes("suma")));
}

console.log();
console.log("=".repeat(78));
console.log("D -- factura sin ítems -> NO bloquea del lado del dashboard");
console.log("=".repeat(78));
{
  const r = validarFacturaParaConfirmar(DRAFT_BASE, []);
  check("isValid == true con lista de ítems vacía", r.isValid === true);
  check("itemsSummaryError == null (ya no exige 'La factura no tiene ítems')", r.itemsSummaryError === null);
  check("'Sin ítems' ya no aparece en messages", !r.messages.includes("Sin ítems"));
}

console.log();
console.log("=".repeat(78));
console.log("E -- invoice.total <= 0 SIGUE bloqueando (no se tocó esta regla)");
console.log("=".repeat(78));
for (const totalInvalido of [0, -1, -4850]) {
  const draft = { ...DRAFT_BASE, total: totalInvalido };
  const r = validarFacturaParaConfirmar(draft, [item(1000)]);
  check(`total=${totalInvalido} -> isValid == false`, r.isValid === false);
  check(`total=${totalInvalido} -> fieldErrors.total poblado`, !!r.fieldErrors.total);
}
{
  const r = validarFacturaParaConfirmar(DRAFT_BASE, [item(1000)]);
  check("total=4850.00 (válido) -> fieldErrors.total ausente", !r.fieldErrors.total);
}

console.log();
console.log("=".repeat(78));
console.log("F -- resto de las validaciones de cabecera intactas (regresión rápida)");
console.log("=".repeat(78));
{
  const draftMalo = { ...DRAFT_BASE, emisor_cuit: "123", cae: "" };
  const r = validarFacturaParaConfirmar(draftMalo, [item(-500)]);
  check("CUIT inválido sigue bloqueando", !!r.fieldErrors.emisor_cuit);
  check("CAE faltante sigue bloqueando", !!r.fieldErrors.cae);
  check("isValid == false (por CUIT/CAE, no por el ítem negativo)", r.isValid === false);
}

console.log();
console.log("=".repeat(78));
if (FALLOS.length > 0) {
  console.log(`RESULTADO: ${FALLOS.length} chequeo(s) fallaron:`);
  for (const f of FALLOS) console.log(`  - ${f}`);
  process.exit(1);
} else {
  console.log("RESULTADO: todos los chequeos pasaron.");
  process.exit(0);
}
