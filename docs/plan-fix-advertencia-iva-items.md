# Plan: arreglar el falso positivo de "los ítems no suman" (IVA)

**Fecha:** 2026-07-20. **Alcance:** solo el frontend (`InvoiceReviewForm.tsx`) — no toca
el payload que Invoicy arma para BAS ni el prompt de extracción de Gemini (eso queda
fuera, ver "Explícitamente fuera de este plan").

## Diagnóstico (recap)

- Gemini extrae `precio_unitario`/`precio_total` de cada ítem **netos** (sin IVA) a
  propósito (`Invoicy/tools.py`), y el `total` de la factura **con** IVA
  (`subtotal + impuestos - retenciones`).
- `InvoiceReviewForm.tsx:135-137` compara la suma de ítems (neta) contra
  `invoiceDraft.total` (bruto) sin saber que están en bases distintas — dispara la
  advertencia en casi cualquier factura con IVA, no solo en casos anómalos.
- El dato correcto para comparar ya existe y ya se extrae: `invoiceDraft.subtotal`
  (`InvoiceDraft` en el mismo archivo ya lo incluye, y Invoicy ya lo persiste:
  `_items_info.get("subtotal")` en `process_invoice_google_2.py`). El bug es puramente
  que el frontend compara contra el campo equivocado — no hace falta tocar extracción
  ni backend para arreglar esto.

## El cambio puntual

En `app/(dashboard)/invoices/[id]/InvoiceReviewForm.tsx`, líneas 133-137:

```tsx
const itemsTotal = items.reduce((sum, item) => sum + (itemDrafts[item.id]?.precio_total ?? 0), 0);
const itemsTotalMismatch =
  items.length > 0 && Math.abs(itemsTotal - invoiceDraft.total) > 0.01;
```

pasa a comparar contra `invoiceDraft.subtotal` en vez de `invoiceDraft.total`, y el texto
de la advertencia (líneas ~339-343) deja de decir "la factura dice" (ambiguo sobre a qué
número se refiere) para decir explícitamente "el subtotal (sin IVA) dice".

## Casos límite a resolver en la implementación

1. **`subtotal` en 0 o ausente** (facturas viejas de antes de que este campo existiera,
   o una extracción donde Gemini no lo pudo calcular): comparar contra `0` daría un
   falso positivo peor que el actual. Regla: si `invoiceDraft.subtotal` es `0` o
   `falsy`, **no mostrar la advertencia** en vez de caer a comparar contra `total` (esa
   caída sería reintroducir el bug original a medias). Es preferible "no advertir" a
   "advertir mal".
2. **Tolerancia de redondeo:** cada `precio_total` de ítem ya viene redondeado a
   centavos; sumar varios puede acumular unos centavos de diferencia contra el
   `subtotal` general. Mantener una tolerancia absoluta chica (ej. `0.05` en vez de
   `0.01`) es razonable, o usar tolerancia relativa (`0.5%` del subtotal) si en la
   práctica siguen apareciendo falsos positivos por redondeo con facturas de montos
   grandes — decidir con datos reales, no de antemano.
3. **Ítems editados a mano por el usuario:** el mismo `precio_total` editable ya
   existe hoy (auto-recalculado al tocar cantidad/precio unitario, línea 127). Nada
   cambia acá — el usuario sigue pudiendo ajustar cualquier ítem y la comparación se
   recalcula igual, solo que ahora contra la base correcta.
4. **Texto de la advertencia:** cambiar "la factura dice {total}" por algo como
   "el subtotal (sin IVA) dice {subtotal}" — evita que alguien lea la advertencia y
   piense que el total con IVA está mal, cuando el total nunca estuvo en discusión.

## Explícitamente fuera de este plan (decidido con el usuario, no se toca ahora)

- **Payload a BAS** (`Invoicy/routes/process_invoice_google_2.py`): sigue con
  `Total`/`TotalGravado` usando el mismo valor bruto, `ImporteTotal` por ítem en neto,
  y `TasaIva: 21` hardcodeado. Esto es un problema de datos contables reales (no solo
  de UI) que toca un flujo financiero en producción — el usuario decidió NO incluirlo en
  este plan, queda pendiente para una decisión de producto separada, con más cuidado
  dado que ya toca `Total`/`OrdenesPago` (mismo flujo con un bloqueo sin resolver
  documentado en `flujo-crear-orden-pago-documentacion-completa.md`).
- **Extracción de Gemini** (`Invoicy/tools.py`): no se le pide que exponga IVA por
  ítem individual — sigue extrayendo solo a nivel de cabecera (`subtotal`, impuestos
  generales). Suficiente para este fix puntual del frontend, que solo necesita el
  subtotal ya existente.

## Implementación (un solo archivo, bajo riesgo)

1. Cambiar la comparación de `invoiceDraft.total` a `invoiceDraft.subtotal` en el
   cálculo de `itemsTotalMismatch`.
2. Agregar el guard de "subtotal en 0 → no mostrar advertencia".
3. Ajustar el texto de la advertencia para referirse al subtotal, no al total.
4. (Opcional, evaluar con casos reales) ajustar la tolerancia de `0.01` si aparecen
   falsos positivos por acumulación de redondeo.

No requiere cambios de schema, ni backend, ni migración — es un fix de una función pura
en un componente cliente ya existente.
