"""
Sincroniza el catálogo REAL de Servicios/Bienes de BAS a PocketBase
(colección "bas_items"). Nuevo alcance confirmado 2026-08-10: el
CodigoItem que se manda al registrar un ComprobanteCompra tiene que salir
de este catálogo, nunca de una lista hardcodeada ni de una categoría
inventada por la IA.

Uso: sincronizar_bas_items(BasClient(), PocketBaseClient()). También
invocable como script standalone -- ver scripts/sync_bas_items.py.

Todas las funciones toman `bas_client`/`pb_client` por parámetro (no
instancian nada por su cuenta) para poder testear offline con dobles de
prueba -- ver scripts/test_offline_bas_items_sync.py.
"""

import datetime
import logging
from typing import Optional

from utils.bas_config import BAS_EMPRESA

app_logger = logging.getLogger("app_logger")


def _tasa_iva_compras(bas_client, codigo_impuesto: Optional[str], empresa: int, cache: dict) -> Optional[float]:
    """Resuelve TasaIvaCompras para un código de impuesto, cacheado dentro
    de esta corrida (muchos ítems comparten el mismo tratamiento
    impositivo -- evita pegarle a /api/Impuestos una vez por ítem)."""
    if not codigo_impuesto:
        return None
    if codigo_impuesto in cache:
        return cache[codigo_impuesto]
    try:
        impuesto = bas_client.obtener_impuesto(empresa, codigo_impuesto)
    except Exception as e:
        app_logger.warning(f"bas_items_sync: error consultando impuesto {codigo_impuesto!r}: {e}")
        impuesto = None
    tasa = None
    if impuesto:
        crudo = impuesto.get("TasaIvaCompras")
        try:
            tasa = float(crudo) if crudo is not None else None
        except (TypeError, ValueError):
            app_logger.warning(
                f"bas_items_sync: TasaIvaCompras no numérica en impuesto {codigo_impuesto!r}: {crudo!r}"
            )
            tasa = None
    cache[codigo_impuesto] = tasa
    return tasa


def _tiene_concepto_compras(posicion: dict) -> bool:
    """
    Determina si una PosicionContable tiene el concepto 'COM' (Compras)
    configurado -- la condición real que hace que un ítem sea usable en un
    ComprobanteCompra. Confirmado con SQL real contra la base restaurada
    (PLATINUM_TEST): el join correcto es CONCEPTOSPOSCNT.CODCPT='COM' para
    el CODTABPOS del ítem, NO la columna ITEMS.CODCUECOM (que existe pero
    no refleja lo mismo) -- 193 de 256 ítems del maestro cumplen esta
    condición (verificado 2026-08-11).

    El shape EXACTO de la respuesta REST de /api/PosicionesContables/{id}
    no se pudo confirmar sin llamar a BAS en vivo (regla del proyecto:
    nada de HTTP real durante esta investigación). Se soportan
    defensivamente 3 shapes plausibles:
      (a) posicion["Conceptos"] = [{"Codigo": "COM", ...}, ...]
      (b) posicion["Conceptos"] = ["COM", "VEN", ...]
      (c) posicion["Concepto"] / posicion["CodigoConcepto"] = "COM" (singular)
    Si ninguno matchea, se loguea un warning EXPLÍCITO (visible en la
    primera corrida real contra BAS) y se devuelve False -- fail-safe:
    mejor excluir de más un ítem elegible que dejar pasar uno que después
    BAS rechaza con 409 "posición contable no definida para el concepto
    COM". Ajustar esta función en cuanto la primera corrida real confirme
    el shape verdadero.
    """
    if not isinstance(posicion, dict):
        return False
    conceptos = posicion.get("Conceptos")
    if isinstance(conceptos, list):
        for c in conceptos:
            if isinstance(c, dict) and str(c.get("Codigo", "")).upper() == "COM":
                return True
            if isinstance(c, str) and c.upper() == "COM":
                return True
        return False  # tiene Conceptos, ninguno es COM
    singular = posicion.get("Concepto") or posicion.get("CodigoConcepto")
    if singular is not None:
        return str(singular).upper() == "COM"
    app_logger.warning(
        "bas_items_sync: no se pudo determinar el/los concepto(s) de la posición "
        f"contable {posicion.get('Codigo', posicion.get('Id', '?'))!r} -- shape de "
        "respuesta inesperado, se trata como NO elegible para compras."
    )
    return False


def _elegible_compras(bas_client, codigo_posicion: Optional[str], cache: dict) -> bool:
    if not codigo_posicion:
        return False
    if codigo_posicion in cache:
        return cache[codigo_posicion]
    try:
        posicion = bas_client.obtener_posicion_contable(codigo_posicion)
    except Exception as e:
        app_logger.warning(f"bas_items_sync: error consultando posición contable {codigo_posicion!r}: {e}")
        posicion = None
    elegible = _tiene_concepto_compras(posicion) if posicion else False
    cache[codigo_posicion] = elegible
    return elegible


def sincronizar_bas_items(bas_client, pb_client, empresa: int = BAS_EMPRESA) -> dict:
    """
    Trae TODOS los Servicios + Bienes reales de BAS, resuelve
    tasa_iva_compras y elegible_compras por cada uno, y hace upsert en
    PocketBase (bas_items). Los códigos que ya estaban en PocketBase pero
    no vinieron en esta corrida se marcan activo=false (soft-delete --
    nunca se borran, facturas históricas pueden seguir referenciándolos).

    Devuelve un resumen: {"total_bas", "nuevos", "actualizados",
    "inactivos", "elegibles"}. Nunca lanza -- un error puntual (un ítem sin
    Codigo, un GET que falla) no debe frenar la sincronización del resto,
    mismo criterio defensivo que el resto de PocketBaseClient.
    """
    tasa_cache: dict = {}
    posicion_cache: dict = {}
    ahora = datetime.datetime.utcnow().isoformat() + "Z"

    catalogo = []
    try:
        for s in bas_client.listar_servicios():
            s["_tipo"] = "servicio"
            catalogo.append(s)
    except Exception as e:
        app_logger.warning(f"bas_items_sync: error listando servicios: {e}")
    try:
        for b in bas_client.listar_bienes():
            b["_tipo"] = "bien"
            catalogo.append(b)
    except Exception as e:
        app_logger.warning(f"bas_items_sync: error listando bienes: {e}")

    vistos_ahora = set()
    nuevos = actualizados = elegibles = 0

    for item in catalogo:
        codigo = item.get("Codigo")
        if not codigo:
            app_logger.warning(f"bas_items_sync: ítem sin 'Codigo', se omite: {item!r}")
            continue
        vistos_ahora.add(codigo)

        codigo_impuesto = item.get("Impuesto")
        codigo_posicion = item.get("PosicionContable")
        tasa = _tasa_iva_compras(bas_client, codigo_impuesto, empresa, tasa_cache)
        elegible = _elegible_compras(bas_client, codigo_posicion, posicion_cache)
        if elegible:
            elegibles += 1

        existente = pb_client.get_bas_item(codigo)
        pb_client.upsert_bas_item(
            codigo,
            descripcion=item.get("Descripcion") or "",
            descripcion_larga=item.get("DescripcionLarga") or "",
            tipo=item["_tipo"],
            codigo_impuesto=codigo_impuesto or "",
            tasa_iva_compras=tasa,
            codigo_posicion=codigo_posicion or "",
            elegible_compras=elegible,
            activo=True,
            sincronizado_en=ahora,
        )
        if existente:
            actualizados += 1
        else:
            nuevos += 1

    # Soft-delete: filas que en PocketBase seguían activo=true y no vinieron
    # en esta corrida -- se dieron de baja del lado del ERP.
    inactivos = 0
    for fila in pb_client.list_bas_items(solo_elegibles=False):
        if fila.get("activo") and fila.get("codigo") not in vistos_ahora:
            pb_client.upsert_bas_item(fila["codigo"], activo=False, sincronizado_en=ahora)
            inactivos += 1

    resumen = {
        "total_bas": len(catalogo),
        "nuevos": nuevos,
        "actualizados": actualizados,
        "inactivos": inactivos,
        "elegibles": elegibles,
    }
    app_logger.info(f"bas_items_sync: {resumen}")
    return resumen
