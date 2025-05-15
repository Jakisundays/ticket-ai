def formatear_retenciones(retenciones):
    if not retenciones:
        return "No hay retenciones."
    resultado = []
    for i, r in enumerate(retenciones, start=1):
        texto = (
            f"Retención #{i}:\n"
            f"  - Tipo: {r['tipo']}\n"
            f"  - Descripción: {r.get('description', 'No especificada')}\n"
            f"  - Base Imponible: ${r['base_imponible']:.2f}\n"
        )
        resultado.append(texto)
    return "\n".join(resultado)


def formatear_impuestos(impuestos):
    if not impuestos:
        return "No hay impuestos."
    resultado = []
    for i, imp in enumerate(impuestos, start=1):
        # Build base text with required fields
        texto = (
            f"Impuesto #{i}:\n"
            f"  - Tipo: {imp['tipo']}\n"
            f"  - Base Imponible: ${imp['base_imponible']:.2f}\n"
            f"  - Importe: ${imp['importe']:.2f}\n"
        )

        # Add optional fields if present
        if "descripcion" in imp:
            texto = texto.replace(
                f"  - Tipo: {imp['tipo']}\n",
                f"  - Tipo: {imp['tipo']}\n" f"  - Descripción: {imp['descripcion']}\n",
            )

        if "alicuota" in imp and imp["alicuota"] is not None:
            texto = texto.replace(
                f"  - Base Imponible: ${imp['base_imponible']:.2f}\n",
                f"  - Base Imponible: ${imp['base_imponible']:.2f}\n"
                f"  - Alícuota: {imp['alicuota']:.2f}%\n",
            )

        resultado.append(texto)
    return "\n".join(resultado)
