"""
Config de negocio para la integración BAS (instalación PLATINUM_TEST, Empresa 1).

Separado de utils/bas.py (el cliente HTTP, agnóstico a qué categorías o qué
empresa se usan) porque esto es dato de negocio de esta instalación puntual,
no lógica de transporte.

Mapeo categoría-de-ítem (la que elige el LLM al extraer la factura, ver
tools_standard.py) -> código de ítem real del catálogo de BAS. Las categorías
son un subconjunto curado de los ítems reales de BAS que SÍ tienen posición
contable con concepto COM (Compras) configurada -- ver
docs/bas-orden-de-pago-research.md (193 de 255 ítems tienen COM). No inventar
categorías nuevas: cada valor de este diccionario debe ser un CodigoItem
verificado contra el catálogo real de BAS.
"""

# CodigoItem real de BAS por categoría. Solo "Gastos Generales" está verificado
# con un 201 real (docs/bas-orden-de-pago-research.md); el resto son placeholders
# hasta confirmar sus códigos reales contra el catálogo (GET paginado, mismo
# patrón que BasClient.listar_proveedores()).
CATEGORIA_A_CODIGO_ITEM = {
    "Bebidas y Bar": "<codigo a confirmar>",
    "Insumos": "<codigo a confirmar>",
    "Limpieza": "<codigo a confirmar>",
    "Gastos Generales": "Gs Gs 21%",  # catch-all verificado (201 real, TasaIva 21)
}

CATEGORIA_CATCH_ALL = "Gastos Generales"

# Enum a exponerle al LLM en tools_standard.py -- deriva de las keys de arriba
# para que el schema y el mapeo real nunca se desincronicen.
CATEGORIAS_ITEM_BAS = list(CATEGORIA_A_CODIGO_ITEM.keys())


def codigo_item_de_categoria(categoria: str) -> str:
    """CodigoItem de BAS para una categoría. Cae al catch-all si no matchea."""
    return CATEGORIA_A_CODIGO_ITEM.get(categoria, CATEGORIA_A_CODIGO_ITEM[CATEGORIA_CATCH_ALL])


# --- Config de negocio fija de esta instalación (Empresa 1 = PLATINUM HOMES) ---
# Documentado con evidencia real en docs/bas-orden-de-pago-research.md (201 logrado).
BAS_EMPRESA = 1
BAS_SUCURSAL = 1
BAS_DEPOSITO = 1
BAS_CAJA = "1"
BAS_METODO_PAGO_CTA_CTE = "C"  # cuenta corriente (no contado)
BAS_TIPO_ENTREGA_SIN_STOCK = "E"  # entrega pendiente, no mueve mercadería
BAS_CENTRO_APROPIACION_SD = "SD"  # "sin definir", genérico
BAS_EMITIDO_POR_CAE = "2"  # factura con CAE (electrónica)
BAS_TRAT_IMPOSITIVO_RI = "2"  # Responsable Inscripto (catálogo real de BAS)
BAS_TRAT_IMPOSITIVO_PROV_RI = "1"  # NO RET NO PERCEP (catálogo real de BAS)
BAS_NUMERO_IMPOSITIVO_TIPO_CUIT = "80"
# TODO: resolver dinámico con buscar_prefijo_talonario(empresa, "MA"/"OP") en vez
# de hardcodear -- por ahora, valores reales confirmados con un 201 real.
BAS_PREFIJO_TALONARIO_MA = "00001"  # talonario de Factura de Compra A
BAS_PREFIJO_TALONARIO_OP = "00001"  # talonario de Orden de Pago (distinto talonario, mismo prefijo)
