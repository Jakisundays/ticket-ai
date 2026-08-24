"""
Cliente para PocketBase — persistencia de facturas, ítems, jobs y estado de
integración BAS. Mismo estilo que utils/bas.py (BasClient): auth cacheada +
refresh, método _request() genérico sobre requests, métodos tipados.

Autenticación: colección de auth "service_accounts" (NO "users", esa es para
el dashboard humano) vía POST /api/collections/service_accounts/auth-with-password.

Diseño defensivo a propósito: TODOS los métodos públicos (los "tipados")
atrapan cualquier error de red/HTTP/config faltante y devuelven None/False en
vez de propagar excepciones -- este cliente es un mecanismo de persistencia
best-effort para InvoiceOrchestrator (routes/process_invoice_google_2.py), y
un fallo acá NUNCA debe romper Sheets/Drive/BAS/email. Mismo criterio que ya
usa guardar_items_en_sheets() en ese archivo.

Configuración por variables de entorno (via os.getenv; se asume que el módulo
que importa este cliente ya corrió load_dotenv()):
    POCKETBASE_URL
    POCKETBASE_SERVICE_EMAIL
    POCKETBASE_SERVICE_PASSWORD

Nota sobre el header de auth: a diferencia de BasClient (que usa
"Authorization: Bearer <token>"), la API de PocketBase espera el token JWT
crudo en el header Authorization, SIN el prefijo "Bearer " (así lo hace el
SDK oficial pocketbase-js). No "corregir" esto sin confirmar contra la
instancia real.

Colecciones esperadas en PocketBase (crearlas antes de habilitar esta
integración; este módulo no las crea, solo las consume):
    service_accounts        (auth collection) — email/password del service account.
    invoices                 — process_id (text, único), status (select, REQUERIDO:
                                pending/processing/completed/error) + resto de campos del
                                contrato de schema (ver plan de arquitectura).
    invoice_items             — invoice (relation a invoices, REQUERIDA -- el id del record,
                                no el process_id) + campos por ítem (descripcion, cantidad,
                                precio_unitario, precio_total, categoria, bas_codigo_item, linea).
    processing_jobs           — process_id (text, único), status (select REQUERIDO:
                                queued/processing/done/error), error_message, ...
    bas_processing_status     — invoice (relation a invoices, REQUERIDA en creación), process_id,
                                proveedor_resuelto, proveedor_codigo, comprobante_prefijo,
                                comprobante_numero, comprobante_registrado, orden_pago_status
                                (select REQUERIDO: pending/success/failed -- OJO, no "error" ni
                                "no_intentado"), orden_pago_error, retry_count, last_attempt_at.
    bas_providers              — cuit (text, único), bas_codigo (text), razon_social (text),
                                nuevo (bool), last_verified_at (date). Campos FLAT (no JSON
                                embebido) -- ver get_provider_cache/set_provider_cache para el
                                mapeo hacia/desde la forma de un proveedor BAS real.

CORRECCIÓN POST-REVISIÓN (los nombres de colección/campo de abajo fueron
verificados contra las migraciones reales en ticket-ai-infra/pocketbase/pb_migrations/
-- la primera versión de este cliente asumía nombres distintos sin haber visto
el schema real; ver historial si hace falta el detalle).
"""

import base64
import datetime
import json
import logging
import time
from typing import Any, Optional

import requests

app_logger = logging.getLogger("app_logger")

# --- Nombres de colecciones (constantes, mismo criterio que bas_config.py) ---
AUTH_COLLECTION_DEFAULT = "service_accounts"
INVOICES_COLLECTION = "invoices"
INVOICE_ITEMS_COLLECTION = "invoice_items"
PROCESSING_JOBS_COLLECTION = "processing_jobs"
BAS_PROCESSING_STATUS_COLLECTION = "bas_processing_status"
BAS_PROVIDERS_COLLECTION = "bas_providers"
BAS_PAYMENT_METHODS_COLLECTION = "bas_payment_methods"
PAYMENT_ORDERS_COLLECTION = "payment_orders"
BAS_CATEGORY_MAP_COLLECTION = "bas_category_map"
BAS_ITEMS_COLLECTION = "bas_items"
IMPORT_BATCHES_COLLECTION = "import_batches"
IMPORT_BATCH_ITEMS_COLLECTION = "import_batch_items"


class PocketBaseApiError(Exception):
    """Error de la API de PocketBase. Conserva el código HTTP y el detalle del backend."""

    def __init__(self, status_code: int, detail: Any, path: str = ""):
        self.status_code = status_code
        self.detail = detail
        self.path = path
        super().__init__(f"PocketBase API {status_code} en {path}: {detail}")


class PocketBaseClient:
    """
    Cliente sincrónico de la API REST de PocketBase.

    Configuración por variables de entorno:
        POCKETBASE_URL
        POCKETBASE_SERVICE_EMAIL
        POCKETBASE_SERVICE_PASSWORD

    Si falta configuración (típicamente porque PocketBase todavía no está
    desplegado/configurado), los métodos públicos devuelven None/False en vez
    de romper -- ver docstring del módulo.
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        service_email: Optional[str] = None,
        service_password: Optional[str] = None,
        auth_collection: Optional[str] = None,
        timeout: int = 30,
        token_margin: int = 60,
        fallback_ttl: int = 600,
    ):
        import os

        self.base_url = (base_url or os.getenv("POCKETBASE_URL") or "").rstrip("/")
        self.service_email = service_email or os.getenv("POCKETBASE_SERVICE_EMAIL")
        self.service_password = service_password or os.getenv("POCKETBASE_SERVICE_PASSWORD")
        self.auth_collection = auth_collection or AUTH_COLLECTION_DEFAULT
        self.timeout = timeout
        self.token_margin = token_margin
        # TTL de respaldo si no se puede decodificar el `exp` del JWT (no debería
        # pasar en un uso normal, pero evita cachear un token para siempre por error).
        self.fallback_ttl = fallback_ttl

        self._access_token: Optional[str] = None
        self._token_expira_en: float = 0.0  # epoch seconds

    # ------------------------------------------------------------------ #
    # Autenticación / token
    # ------------------------------------------------------------------ #
    def _solicitar_token_password(self) -> dict:
        """POST /api/collections/{auth_collection}/auth-with-password."""
        if not self.base_url:
            raise PocketBaseApiError(0, "Falta POCKETBASE_URL", "/auth-with-password")
        if not self.service_email or not self.service_password:
            raise PocketBaseApiError(
                0, "Faltan POCKETBASE_SERVICE_EMAIL / POCKETBASE_SERVICE_PASSWORD", "/auth-with-password"
            )
        path = f"/api/collections/{self.auth_collection}/auth-with-password"
        try:
            resp = requests.post(
                f"{self.base_url}{path}",
                json={"identity": self.service_email, "password": self.service_password},
                timeout=self.timeout,
            )
        except requests.exceptions.RequestException as e:
            raise PocketBaseApiError(0, str(e), path)
        if resp.status_code != 200:
            raise PocketBaseApiError(resp.status_code, _detalle(resp), path)
        return resp.json()

    def _solicitar_token_refresh(self) -> dict:
        """POST /api/collections/{auth_collection}/auth-refresh, usando el token actual."""
        if not self._access_token:
            raise PocketBaseApiError(0, "No hay token para refrescar", "/auth-refresh")
        path = f"/api/collections/{self.auth_collection}/auth-refresh"
        try:
            resp = requests.post(
                f"{self.base_url}{path}",
                headers={"Authorization": self._access_token},
                timeout=self.timeout,
            )
        except requests.exceptions.RequestException as e:
            raise PocketBaseApiError(0, str(e), path)
        if resp.status_code != 200:
            raise PocketBaseApiError(resp.status_code, _detalle(resp), path)
        return resp.json()

    def _guardar_token(self, data: dict) -> None:
        self._access_token = data.get("token")
        exp = _jwt_exp_epoch(self._access_token) if self._access_token else None
        if exp:
            self._token_expira_en = exp - self.token_margin
        else:
            # No se pudo decodificar el `exp` del JWT: cachear con un margen
            # conservador en vez de asumir que el token nunca expira.
            self._token_expira_en = time.time() + max(0, self.fallback_ttl - self.token_margin)

    def _token_valido(self) -> bool:
        return bool(self._access_token) and time.time() < self._token_expira_en

    def get_token(self) -> str:
        """Devuelve un access_token válido (cacheado, con refresh y fallback a re-login)."""
        if self._token_valido():
            return self._access_token
        if self._access_token:
            try:
                self._guardar_token(self._solicitar_token_refresh())
                app_logger.info("PocketBase: token renovado vía auth-refresh")
                return self._access_token
            except PocketBaseApiError as e:
                app_logger.warning(f"PocketBase: refresh falló ({e.status_code}); re-autenticando")
        self._guardar_token(self._solicitar_token_password())
        app_logger.info("PocketBase: autenticación exitosa")
        return self._access_token

    def _invalidar_token(self) -> None:
        self._access_token = None
        self._token_expira_en = 0.0

    # ------------------------------------------------------------------ #
    # Transporte
    # ------------------------------------------------------------------ #
    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict] = None,
        json_body: Optional[Any] = None,
        _reintento_auth: bool = True,
    ) -> requests.Response:
        if not self.base_url:
            raise PocketBaseApiError(0, "Falta POCKETBASE_URL", path)
        url = f"{self.base_url}{path}"
        # Sin prefijo "Bearer " -- ver nota en el docstring del módulo.
        headers = {"Authorization": self.get_token()}
        try:
            resp = requests.request(
                method, url, params=params, json=json_body, headers=headers, timeout=self.timeout
            )
        except requests.exceptions.RequestException as e:
            raise PocketBaseApiError(0, str(e), path)
        if resp.status_code == 401 and _reintento_auth:
            app_logger.warning("PocketBase: 401, invalidando token y reintentando")
            self._invalidar_token()
            return self._request(
                method, path, params=params, json_body=json_body, _reintento_auth=False
            )
        return resp

    def _request_multipart(
        self,
        method: str,
        path: str,
        *,
        files: dict,
        _reintento_auth: bool = True,
    ) -> requests.Response:
        """Variante de _request() para escrituras multipart/form-data -- lo
        que exige la API de PocketBase para un campo tipo "file" (un PATCH
        con json= NO sirve para subir archivos, a diferencia del resto de
        los campos que sí usan _request/_update normal)."""
        if not self.base_url:
            raise PocketBaseApiError(0, "Falta POCKETBASE_URL", path)
        url = f"{self.base_url}{path}"
        headers = {"Authorization": self.get_token()}
        try:
            resp = requests.request(
                method, url, files=files, headers=headers, timeout=self.timeout
            )
        except requests.exceptions.RequestException as e:
            raise PocketBaseApiError(0, str(e), path)
        if resp.status_code == 401 and _reintento_auth:
            app_logger.warning("PocketBase: 401, invalidando token y reintentando (multipart)")
            self._invalidar_token()
            return self._request_multipart(method, path, files=files, _reintento_auth=False)
        return resp

    # ------------------------------------------------------------------ #
    # Helpers genéricos de colección (privados, pueden lanzar PocketBaseApiError)
    # ------------------------------------------------------------------ #
    def _find_one(self, collection: str, filter_str: str) -> Optional[dict]:
        resp = self._request(
            "GET",
            f"/api/collections/{collection}/records",
            params={"filter": filter_str, "perPage": 1, "page": 1},
        )
        data = _json_o_error(resp, f"/api/collections/{collection}/records", ok=(200,))
        items = (data or {}).get("items") or []
        return items[0] if items else None

    def _list_all(self, collection: str, filter_str: str = "", page_size: int = 200) -> list:
        """Trae TODOS los records de una colección chica (sin paginar de verdad
        -- page_size generoso alcanza para colecciones de config como
        bas_category_map, que nunca van a tener cientos de filas)."""
        params = {"perPage": page_size, "page": 1}
        if filter_str:
            params["filter"] = filter_str
        resp = self._request("GET", f"/api/collections/{collection}/records", params=params)
        data = _json_o_error(resp, f"/api/collections/{collection}/records", ok=(200,))
        return (data or {}).get("items") or []

    def _create(self, collection: str, data: dict) -> dict:
        resp = self._request("POST", f"/api/collections/{collection}/records", json_body=data)
        return _json_o_error(resp, f"/api/collections/{collection}/records", ok=(200, 201))

    def _update(self, collection: str, record_id: str, data: dict) -> dict:
        resp = self._request(
            "PATCH", f"/api/collections/{collection}/records/{record_id}", json_body=data
        )
        return _json_o_error(
            resp, f"/api/collections/{collection}/records/{record_id}", ok=(200, 201)
        )

    def _upsert(self, collection: str, key_field: str, key_value: str, data: dict) -> dict:
        existente = self._find_one(collection, _pb_filter_eq(key_field, key_value))
        payload = dict(data)
        payload[key_field] = key_value
        if existente:
            return self._update(collection, existente["id"], payload)
        return self._create(collection, payload)

    def _soft_delete(
        self, collection: str, record_id: str, deleted_by: Optional[str], reason: Optional[str]
    ) -> dict:
        """
        Nunca un borrado físico -- ver migración
        1783483896_add_soft_delete_fields.js (BAS nunca se entera de un
        borrado en PocketBase; perder el registro entero perdería toda la
        trazabilidad sin revertir nada del lado de BAS). Solo marca
        deleted_at/deleted_by/delete_reason; el caller es responsable de
        filtrar deleted_at="" en cualquier listado.
        """
        payload = {
            "deleted_at": datetime.datetime.utcnow().isoformat() + "Z",
            "deleted_by": deleted_by,
            "delete_reason": reason or "",
        }
        return self._update(collection, record_id, payload)

    # ------------------------------------------------------------------ #
    # Métodos tipados (públicos) -- todos defensivos: devuelven None/False
    # en vez de propagar excepciones.
    # ------------------------------------------------------------------ #
    def upsert_invoice(self, data: dict) -> Optional[dict]:
        """
        Upsert de una factura en INVOICES_COLLECTION, key = process_id.
        `data` debe incluir la clave "process_id". Devuelve el record de
        PocketBase o None si falló (o si falta process_id).
        """
        try:
            process_id = data.get("process_id") if data else None
            if not process_id:
                app_logger.warning("PocketBase: upsert_invoice sin process_id, se omite")
                return None
            payload = {k: v for k, v in data.items() if k != "process_id"}
            return self._upsert(INVOICES_COLLECTION, "process_id", process_id, payload)
        except Exception as e:
            app_logger.warning(f"PocketBase: error en upsert_invoice: {e}")
            return None

    def adjuntar_archivo_original(
        self, record_id: str, file_path: str, filename: str, mime_type: str
    ) -> bool:
        """
        Adjunta el archivo original (imagen/PDF) al campo "documento_original"
        de una factura ya persistida. `record_id` es el id de PocketBase
        (record["id"]), NO el process_id -- a diferencia de upsert_invoice,
        que sí resuelve por process_id, PATCH /records/{id} necesita el id
        real. Llamar ANTES de borrar el archivo local (ver
        _procesar_imagen_o_pdf). Best-effort: un fallo acá nunca debe frenar
        el resto del procesamiento (Sheets/BAS ya se hicieron para cuando se
        llega a este punto). Devuelve True si quedó adjuntado.
        """
        try:
            with open(file_path, "rb") as f:
                resp = self._request_multipart(
                    "PATCH",
                    f"/api/collections/{INVOICES_COLLECTION}/records/{record_id}",
                    files={"documento_original": (filename, f, mime_type)},
                )
            if resp.status_code not in (200, 201):
                app_logger.warning(
                    f"PocketBase: adjuntar_archivo_original {resp.status_code}: {_detalle(resp)}"
                )
                return False
            return True
        except Exception as e:
            app_logger.warning(f"PocketBase: error en adjuntar_archivo_original: {e}")
            return False

    def obtener_categoria_map(self) -> dict:
        """
        {categoria: {alicuota: codigo_item}} desde "bas_category_map", solo
        filas con confirmado=true (las sin confirmar son borradores del
        dashboard, todavía no verificadas contra el catálogo real de BAS --
        no deben llegar al LLM ni usarse para armar un ComprobanteCompra
        real). Una categoria puede tener varias filas, una por cada alicuota
        de IVA con CodigoItem real en el catálogo de BAS (ver migración
        1783483945_add_alicuota_to_bas_category_map.js) -- antes había una
        sola fila por categoria, siempre a 21%, lo cual rompía cualquier
        factura con otra alícuota real (ver bas_config.py:resolver_item_bas).
        Devuelve {} (no None) en caso de error -- el caller (bas_config.py)
        decide el fallback.
        """
        try:
            records = self._list_all(BAS_CATEGORY_MAP_COLLECTION, filter_str="confirmado = true")
        except Exception as e:
            app_logger.warning(f"PocketBase: error en obtener_categoria_map: {e}")
            return {}
        mapa: dict = {}
        for r in records:
            categoria = r.get("categoria")
            codigo_item = r.get("codigo_item")
            alicuota = r.get("alicuota")
            if not categoria or not codigo_item or alicuota is None:
                continue
            # Conversión por FILA, no en el try de arriba (que solo cubre la
            # llamada de red): una sola fila con un valor de alicuota no
            # numérico (dato corrupto de una edición manual) no debe tirar
            # TODA la colección al fallback hardcodeado -- eso rompería
            # silenciosamente la resolución de CodigoItem para TODAS las
            # categorías, no solo la fila con el dato malo.
            try:
                alicuota = round(float(alicuota), 2)
            except (TypeError, ValueError):
                app_logger.warning(
                    f"PocketBase: fila de bas_category_map con alicuota no numérica "
                    f"({categoria!r}: {alicuota!r}), se ignora esa fila."
                )
                continue
            mapa.setdefault(categoria, {})[alicuota] = codigo_item
        return mapa

    # ------------------------------------------------------------------ #
    # bas_items -- catálogo real de Servicios/Bienes de BAS (nuevo alcance
    # 2026-08-10). Solo lo escribe el sync (utils/bas_items_sync.py, via
    # service_accounts) -- nunca un humano desde el dashboard.
    # ------------------------------------------------------------------ #
    def upsert_bas_item(self, codigo: str, **campos) -> Optional[dict]:
        """Upsert de un ítem del catálogo BAS en BAS_ITEMS_COLLECTION,
        key = codigo. `campos` puede incluir cualquiera de: descripcion,
        descripcion_larga, tipo, codigo_impuesto, tasa_iva_compras,
        codigo_posicion, elegible_compras, activo, confirmado,
        sincronizado_en."""
        try:
            if not codigo:
                app_logger.warning("PocketBase: upsert_bas_item sin codigo, se omite")
                return None
            return self._upsert(BAS_ITEMS_COLLECTION, "codigo", codigo, campos)
        except Exception as e:
            app_logger.warning(f"PocketBase: error en upsert_bas_item({codigo}): {e}")
            return None

    def list_bas_items(self, solo_elegibles: bool = True) -> list:
        """Catálogo completo cacheado en PocketBase. `solo_elegibles=True`
        (default) filtra a activo=true AND elegible_compras=true -- el
        subset que realmente se puede usar en una línea de ComprobanteCompra
        (confirmado con SQL real: 193 de 256 ítems del maestro cumplen esta
        condición vía CONCEPTOSPOSCNT.CODCPT='COM'). `False` trae todo
        (incluidos inactivos/no elegibles) para pantallas de diagnóstico.
        Devuelve [] en caso de error -- mismo criterio que obtener_categoria_map."""
        try:
            filtro = 'activo = true && elegible_compras = true' if solo_elegibles else ""
            return self._list_all(BAS_ITEMS_COLLECTION, filter_str=filtro, page_size=500)
        except Exception as e:
            app_logger.warning(f"PocketBase: error en list_bas_items: {e}")
            return []

    def get_bas_item(self, codigo: str) -> Optional[dict]:
        """Un ítem puntual del catálogo cacheado, por código. None si no
        existe o si falló la consulta."""
        try:
            if not codigo:
                return None
            return self._find_one(BAS_ITEMS_COLLECTION, _pb_filter_eq("codigo", codigo))
        except Exception as e:
            app_logger.warning(f"PocketBase: error en get_bas_item({codigo}): {e}")
            return None

    def obtener_file_token(self) -> Optional[str]:
        """
        POST /api/files/token -- token de corta duración (credenciales del
        service account) requerido para leer un campo tipo "file" marcado
        protected=true, como "documento_original". Lo usa el proxy
        GET /gemini2/invoices/{process_id}/file para servir el archivo.
        """
        try:
            resp = self._request("POST", "/api/files/token")
            data = _json_o_error(resp, "/api/files/token", ok=(200,))
            return (data or {}).get("token")
        except Exception as e:
            app_logger.warning(f"PocketBase: error en obtener_file_token: {e}")
            return None

    def get_provider_cache(self, cuit: str) -> Optional[dict]:
        """
        Cache persistente (2do nivel) de proveedores BAS ya resueltos, key = CUIT
        normalizado (solo dígitos). La colección "bas_providers" guarda campos
        FLAT (bas_codigo/razon_social/nuevo), no un blob JSON -- acá se
        reconstruye un dict con la forma mínima que espera el caller
        (procesar_factura_en_bas hace proveedor.get("Codigo")), tomada de los
        campos reales que sí persiste `set_provider_cache`. Devuelve None si no
        hay hit o si PocketBase no responde.
        """
        try:
            cuit_normalizado = "".join(c for c in (cuit or "") if c.isdigit())
            if not cuit_normalizado:
                return None
            record = self._find_one(
                BAS_PROVIDERS_COLLECTION, _pb_filter_eq("cuit", cuit_normalizado)
            )
            if not record:
                return None
            return {
                "Codigo": record.get("bas_codigo"),
                "RazonSocial": record.get("razon_social"),
                "_nuevo": record.get("nuevo"),
                # id del record de PocketBase (no de BAS) -- lo necesita el
                # caller para setear relations que apunten a este proveedor
                # cacheado (ej. invoices.bas_provider, nuevo alcance
                # 2026-08-10). Agregado sin quitar nada -- cualquier código
                # viejo que solo lea Codigo/RazonSocial/_nuevo sigue andando
                # igual.
                "_pb_id": record.get("id"),
            }
        except Exception as e:
            app_logger.warning(f"PocketBase: error en get_provider_cache({cuit}): {e}")
            return None

    def set_provider_cache(self, cuit: str, proveedor: dict) -> Optional[dict]:
        """
        Guarda/actualiza el proveedor resuelto para un CUIT en la colección
        "bas_providers" (campos flat: bas_codigo, razon_social, nuevo,
        last_verified_at). `proveedor` es el dict que devuelve BasClient (con
        claves BAS reales: Codigo, RazonSocial, _nuevo).

        Devuelve el record de PocketBase ya escrito (con su "id") o None si
        falló -- ANTES devolvía bool; se cambió el contrato (2026-08-10,
        nuevo alcance) porque el único caller (InvoiceOrchestrator.
        _buscar_proveedor_bas) necesita el id para setear invoices.
        bas_provider (relation). Sigue siendo "truthy" en éxito y falsy en
        fallo, así que un `if resultado:` viejo seguiría funcionando igual.
        """
        try:
            cuit_normalizado = "".join(c for c in (cuit or "") if c.isdigit())
            if not cuit_normalizado or not proveedor:
                return None
            return self._upsert(
                BAS_PROVIDERS_COLLECTION,
                "cuit",
                cuit_normalizado,
                {
                    "bas_codigo": proveedor.get("Codigo"),
                    "razon_social": proveedor.get("RazonSocial"),
                    "nuevo": bool(proveedor.get("_nuevo")),
                    "last_verified_at": datetime.datetime.utcnow().isoformat() + "Z",
                },
            )
        except Exception as e:
            app_logger.warning(f"PocketBase: error en set_provider_cache({cuit}): {e}")
            return None

    def upsert_bas_processing_status(
        self, process_id: str, *, invoice: Optional[str] = None, **campos
    ) -> Optional[dict]:
        """
        Upsert del resultado de la integración BAS para un process_id, en
        BAS_PROCESSING_STATUS_COLLECTION. `campos` son los pares clave/valor a
        guardar (proveedor_resuelto, proveedor_codigo, comprobante_prefijo,
        comprobante_numero, comprobante_registrado, orden_pago_status,
        orden_pago_error, retry_count, last_attempt_at, ...).

        `invoice`: id del record de INVOICES_COLLECTION (relation, REQUERIDO
        por el schema). Si el record de bas_processing_status ya existe (se
        está actualizando, ej. desde el endpoint de retry), `invoice` puede
        omitirse -- PocketBase no exige reenviar un campo requerido que ya
        tiene un valor válido en un PATCH. Si el record NO existe todavía
        (primera vez) y no se pasa `invoice`, la creación va a fallar la
        validación de PocketBase (campo requerido faltante) y este método
        devuelve None sin persistir nada -- lo logueamos explícito para que no
        pase desapercibido.
        """
        try:
            if not process_id:
                return None
            existente = self._find_one(
                BAS_PROCESSING_STATUS_COLLECTION, _pb_filter_eq("process_id", process_id)
            )
            if existente:
                return self._update(BAS_PROCESSING_STATUS_COLLECTION, existente["id"], campos)
            if not invoice:
                app_logger.warning(
                    f"PocketBase: upsert_bas_processing_status({process_id}) es una creación "
                    "nueva pero falta `invoice` (campo requerido) -- se omite para no mandar "
                    "un create que la API va a rechazar igual."
                )
                return None
            payload = dict(campos)
            payload["process_id"] = process_id
            payload["invoice"] = invoice
            return self._create(BAS_PROCESSING_STATUS_COLLECTION, payload)
        except Exception as e:
            app_logger.warning(f"PocketBase: error en upsert_bas_processing_status({process_id}): {e}")
            return None

    def bulk_create_invoice_items(self, invoice: str, items: list) -> bool:
        """
        Crea un record en INVOICE_ITEMS_COLLECTION por cada ítem de `items`
        (lista de dicts, pass-through + invoice). `invoice` DEBE ser el id del
        record de INVOICES_COLLECTION (relation requerida por el schema, no el
        process_id -- usar el "id" que devuelve upsert_invoice()). Aislado por
        ítem: si uno falla, se loguea y se sigue con el resto. Devuelve True si
        TODOS los ítems se crearon, False si hubo al menos un fallo (incluye
        el caso de `invoice` vacío o PocketBase no configurado/caído).
        """
        try:
            if not items:
                return True
            if not invoice:
                app_logger.warning(
                    "PocketBase: bulk_create_invoice_items sin `invoice` (id de la factura "
                    "en PocketBase) -- se omiten todos los ítems, el campo es requerido."
                )
                return False
            todo_ok = True
            for item in items:
                payload = dict(item)
                payload["invoice"] = invoice
                try:
                    self._create(INVOICE_ITEMS_COLLECTION, payload)
                except Exception as e:
                    todo_ok = False
                    app_logger.warning(
                        f"PocketBase: error creando ítem de factura ({invoice}): {e}"
                    )
            return todo_ok
        except Exception as e:
            app_logger.warning(f"PocketBase: error en bulk_create_invoice_items({invoice}): {e}")
            return False

    def create_processing_job(self, process_id: str, **campos) -> Optional[dict]:
        """Crea un record en PROCESSING_JOBS_COLLECTION. Devuelve el record o None."""
        try:
            if not process_id:
                return None
            payload = dict(campos)
            payload["process_id"] = process_id
            return self._create(PROCESSING_JOBS_COLLECTION, payload)
        except Exception as e:
            app_logger.warning(f"PocketBase: error en create_processing_job({process_id}): {e}")
            return None

    def update_processing_job(self, process_id: str, **campos) -> Optional[dict]:
        """
        Actualiza el record de PROCESSING_JOBS_COLLECTION para `process_id`.
        Si no existe (p.ej. create_processing_job falló antes por PocketBase
        caído), lo crea ahora para no perder el estado final del job.
        """
        try:
            if not process_id:
                return None
            existente = self._find_one(
                PROCESSING_JOBS_COLLECTION, _pb_filter_eq("process_id", process_id)
            )
            if not existente:
                return self.create_processing_job(process_id, **campos)
            return self._update(PROCESSING_JOBS_COLLECTION, existente["id"], campos)
        except Exception as e:
            app_logger.warning(f"PocketBase: error en update_processing_job({process_id}): {e}")
            return None

    def get_processing_job(self, process_id: str) -> Optional[dict]:
        try:
            if not process_id:
                return None
            return self._find_one(
                PROCESSING_JOBS_COLLECTION, _pb_filter_eq("process_id", process_id)
            )
        except Exception as e:
            app_logger.warning(f"PocketBase: error en get_processing_job({process_id}): {e}")
            return None

    def get_invoice_by_process_id(self, process_id: str) -> Optional[dict]:
        try:
            if not process_id:
                return None
            return self._find_one(INVOICES_COLLECTION, _pb_filter_eq("process_id", process_id))
        except Exception as e:
            app_logger.warning(f"PocketBase: error en get_invoice_by_process_id({process_id}): {e}")
            return None

    def get_bas_processing_status(self, process_id: str) -> Optional[dict]:
        try:
            if not process_id:
                return None
            return self._find_one(
                BAS_PROCESSING_STATUS_COLLECTION, _pb_filter_eq("process_id", process_id)
            )
        except Exception as e:
            app_logger.warning(f"PocketBase: error en get_bas_processing_status({process_id}): {e}")
            return None

    def get_invoice_items(self, invoice_id: str) -> list:
        """
        Lista todos los invoice_items de una factura (por el id de PocketBase,
        no process_id -- mismo criterio que bulk_create_invoice_items). []
        si no hay items o si PocketBase no responde. Usado por
        /payment-orders/{process_id}/create para reconstruir el payload de
        ComprobantesCompra con los valores ACTUALES (potencialmente editados
        durante la revisión humana), no con los de la extracción original.
        """
        try:
            if not invoice_id:
                return []
            resp = self._request(
                "GET",
                f"/api/collections/{INVOICE_ITEMS_COLLECTION}/records",
                params={"filter": _pb_filter_eq("invoice", invoice_id), "perPage": 200, "sort": "linea"},
            )
            data = _json_o_error(resp, f"/api/collections/{INVOICE_ITEMS_COLLECTION}/records", ok=(200,))
            return (data or {}).get("items") or []
        except Exception as e:
            app_logger.warning(f"PocketBase: error en get_invoice_items({invoice_id}): {e}")
            return []

    def get_payment_method(self, metodo_pago: str) -> Optional[dict]:
        """Lee el código BAS configurado para un método de pago (efectivo/
        cheque/transferencia) desde bas_payment_methods. None si no hay
        mapeo o si PocketBase no responde -- el caller debe tratar eso como
        "no se puede crear la orden con este método todavía", no adivinar."""
        try:
            if not metodo_pago:
                return None
            return self._find_one(
                BAS_PAYMENT_METHODS_COLLECTION, _pb_filter_eq("metodo_pago", metodo_pago)
            )
        except Exception as e:
            app_logger.warning(f"PocketBase: error en get_payment_method({metodo_pago}): {e}")
            return None

    def get_payment_order(self, process_id: str) -> Optional[dict]:
        try:
            if not process_id:
                return None
            return self._find_one(PAYMENT_ORDERS_COLLECTION, _pb_filter_eq("process_id", process_id))
        except Exception as e:
            app_logger.warning(f"PocketBase: error en get_payment_order({process_id}): {e}")
            return None

    def upsert_payment_order(
        self, process_id: str, *, invoice: Optional[str] = None, **campos
    ) -> Optional[dict]:
        """
        Upsert del intento de Orden de Pago para un process_id, en
        PAYMENT_ORDERS_COLLECTION. Mismo patrón exacto que
        upsert_bas_processing_status (ver ese docstring para el detalle de
        por qué `invoice` es requerido solo en la creación, no en updates
        subsiguientes) -- a propósito, para no duplicar el razonamiento.
        """
        try:
            if not process_id:
                return None
            existente = self._find_one(
                PAYMENT_ORDERS_COLLECTION, _pb_filter_eq("process_id", process_id)
            )
            if existente:
                # Si el registro existente estaba soft-eliminado y esta
                # llamada no es en sí un soft-delete (no trae `deleted_at`
                # en `campos`), es un nuevo intento real reutilizando la
                # misma fila -- limpiar los campos de borrado explícitamente.
                # Si no se limpian, una Orden de Pago real y vigente puede
                # quedar marcada `deleted_at` heredado de un borrado previo,
                # y el guardrail de eliminar_invoice (que solo bloquea el
                # borrado de la factura si encuentra una payment_order
                # activa, es decir sin deleted_at) deja de detectarla.
                if existente.get("deleted_at") and "deleted_at" not in campos:
                    campos = {
                        **campos,
                        "deleted_at": None,
                        "deleted_by": None,
                        "delete_reason": None,
                    }
                return self._update(PAYMENT_ORDERS_COLLECTION, existente["id"], campos)
            if not invoice:
                app_logger.warning(
                    f"PocketBase: upsert_payment_order({process_id}) es una creación nueva pero "
                    "falta `invoice` (campo requerido) -- se omite."
                )
                return None
            payload = dict(campos)
            payload["process_id"] = process_id
            payload["invoice"] = invoice
            return self._create(PAYMENT_ORDERS_COLLECTION, payload)
        except Exception as e:
            app_logger.warning(f"PocketBase: error en upsert_payment_order({process_id}): {e}")
            return None

    def soft_delete_invoice(
        self, process_id: str, *, deleted_by: Optional[str], reason: Optional[str] = None
    ) -> Optional[dict]:
        """
        Soft-delete de una invoice (usado tanto para "Cola de revisión" como
        "Facturas" -- ambas secciones del dashboard leen la misma colección,
        solo con filtros de status/review_status distintos). Idempotente: si
        el record ya tenía deleted_at seteado, lo devuelve tal cual sin
        volver a escribir (evita pisar deleted_by/delete_reason originales
        en una carrera de doble click). Las decisiones de negocio (bloquear
        si hay una payment_order exitosa activa, etc.) viven en el endpoint
        que llama a este método, no acá -- este método solo persiste.
        `deleted_by` debe ser el id de PocketBase del user autenticado,
        resuelto server-side desde la sesión, nunca del body del caller.
        """
        try:
            if not process_id:
                return None
            record = self.get_invoice_by_process_id(process_id)
            if record is None:
                return None
            if record.get("deleted_at"):
                return record
            return self._soft_delete(INVOICES_COLLECTION, record["id"], deleted_by, reason)
        except Exception as e:
            app_logger.warning(f"PocketBase: error en soft_delete_invoice({process_id}): {e}")
            return None

    def soft_delete_payment_order(
        self, process_id: str, *, deleted_by: Optional[str], reason: Optional[str] = None
    ) -> Optional[dict]:
        """Igual que soft_delete_invoice pero sobre PAYMENT_ORDERS_COLLECTION
        -- ver ese docstring para el criterio de idempotencia y de dónde
        vive `deleted_by`."""
        try:
            if not process_id:
                return None
            record = self.get_payment_order(process_id)
            if record is None:
                return None
            if record.get("deleted_at"):
                return record
            return self._soft_delete(PAYMENT_ORDERS_COLLECTION, record["id"], deleted_by, reason)
        except Exception as e:
            app_logger.warning(f"PocketBase: error en soft_delete_payment_order({process_id}): {e}")
            return None

    # ------------------------------------------------------------------ #
    # Importación masiva de facturas (ver
    # docs/plan-importacion-masiva-facturas.md)
    # ------------------------------------------------------------------ #
    def create_import_batch(
        self,
        *,
        label: Optional[str],
        total_files: int,
        started_at: str,
        created_by: Optional[str] = None,
        monto_override: Optional[float] = None,
    ) -> Optional[dict]:
        try:
            payload = {
                "label": label or "",
                "status": "running",
                "total_files": total_files,
                "started_at": started_at,
            }
            if created_by:
                payload["created_by"] = created_by
            if monto_override is not None:
                payload["monto_override"] = monto_override
            return self._create(IMPORT_BATCHES_COLLECTION, payload)
        except Exception as e:
            app_logger.warning(f"PocketBase: error en create_import_batch: {e}")
            return None

    def get_import_batch(self, batch_id: str) -> Optional[dict]:
        try:
            if not batch_id:
                return None
            resp = self._request(
                "GET", f"/api/collections/{IMPORT_BATCHES_COLLECTION}/records/{batch_id}"
            )
            if resp.status_code == 404:
                return None
            return _json_o_error(
                resp, f"/api/collections/{IMPORT_BATCHES_COLLECTION}/records/{batch_id}", ok=(200,)
            )
        except Exception as e:
            app_logger.warning(f"PocketBase: error en get_import_batch({batch_id}): {e}")
            return None

    def update_import_batch(self, batch_id: str, **campos) -> Optional[dict]:
        try:
            if not batch_id:
                return None
            return self._update(IMPORT_BATCHES_COLLECTION, batch_id, campos)
        except Exception as e:
            app_logger.warning(f"PocketBase: error en update_import_batch({batch_id}): {e}")
            return None

    def create_batch_item(
        self,
        *,
        batch: str,
        original_path: str,
        file_name: str,
        content_hash: str,
        status: str = "pending",
        **campos,
    ) -> Optional[dict]:
        try:
            payload = {
                "batch": batch,
                "original_path": original_path,
                "file_name": file_name,
                "content_hash": content_hash,
                "status": status,
                **campos,
            }
            return self._create(IMPORT_BATCH_ITEMS_COLLECTION, payload)
        except Exception as e:
            app_logger.warning(f"PocketBase: error en create_batch_item({original_path}): {e}")
            return None

    def get_batch_item(self, item_id: str) -> Optional[dict]:
        try:
            if not item_id:
                return None
            resp = self._request(
                "GET", f"/api/collections/{IMPORT_BATCH_ITEMS_COLLECTION}/records/{item_id}"
            )
            if resp.status_code == 404:
                return None
            return _json_o_error(
                resp,
                f"/api/collections/{IMPORT_BATCH_ITEMS_COLLECTION}/records/{item_id}",
                ok=(200,),
            )
        except Exception as e:
            app_logger.warning(f"PocketBase: error en get_batch_item({item_id}): {e}")
            return None

    def update_batch_item(self, item_id: str, **campos) -> Optional[dict]:
        try:
            if not item_id:
                return None
            return self._update(IMPORT_BATCH_ITEMS_COLLECTION, item_id, campos)
        except Exception as e:
            app_logger.warning(f"PocketBase: error en update_batch_item({item_id}): {e}")
            return None

    def list_batch_items(self, batch_id: str) -> list:
        """Todos los items de un batch, sin paginar de verdad -- a la escala
        real (decenas de archivos por corrida, mismo orden de magnitud que el
        MAX_ARCHIVOS_ZIP=20 ya existente), page_size generoso alcanza."""
        try:
            if not batch_id:
                return []
            return self._list_all(
                IMPORT_BATCH_ITEMS_COLLECTION,
                _pb_filter_eq("batch", batch_id),
                page_size=500,
            )
        except Exception as e:
            app_logger.warning(f"PocketBase: error en list_batch_items({batch_id}): {e}")
            return []

    def find_invoice_by_content_hash(self, content_hash: str) -> Optional[dict]:
        """Dedup histórico/cross-batch: busca CUALQUIER invoice (de cualquier
        fuente -- subida suelta o batch anterior) con este hash de contenido,
        no soft-deleteada. Si existe, no hay que volver a registrar nada en
        BAS -- ver docs/plan-importacion-masiva-facturas.md, sección 3."""
        try:
            if not content_hash:
                return None
            filtro = f'{_pb_filter_eq("content_hash", content_hash)} && deleted_at = ""'
            return self._find_one(INVOICES_COLLECTION, filtro)
        except Exception as e:
            app_logger.warning(
                f"PocketBase: error en find_invoice_by_content_hash({content_hash}): {e}"
            )
            return None

    def find_stale_batch_items(self, batch_id: str, *, older_than_minutes: int = 15) -> list:
        """Items de un batch que quedaron colgados en 'uploading'/'processing'
        -- típicamente porque el backend se reinició a mitad de camino (ver
        el gap de robustez documentado en el plan, sección 6: el cierre
        "self-closing" del batch nunca dispara si el proceso muere antes de
        terminar). Filtra por tiempo en Python, no en el filtro de PocketBase
        -- a esta escala (decenas de items) es más simple y no depende de
        adivinar el formato exacto de comparación de fechas que acepta la
        API, que no está verificado en ningún otro lugar de este código."""
        try:
            if not batch_id:
                return []
            candidatos = self._list_all(
                IMPORT_BATCH_ITEMS_COLLECTION,
                f'{_pb_filter_eq("batch", batch_id)} && (status = "uploading" || status = "processing")',
                page_size=500,
            )
            corte = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
                minutes=older_than_minutes
            )
            resultado = []
            for item in candidatos:
                actualizado = item.get("updated")
                if not actualizado:
                    continue
                try:
                    ts = datetime.datetime.fromisoformat(actualizado.replace("Z", "+00:00"))
                except ValueError:
                    continue
                if ts < corte:
                    resultado.append(item)
            return resultado
        except Exception as e:
            app_logger.warning(f"PocketBase: error en find_stale_batch_items({batch_id}): {e}")
            return []

    def find_stale_invoices(self, *, antes_de: "datetime.datetime") -> list:
        """Mismo problema que find_stale_batch_items, aplicado a invoices en
        general (no solo las de un ZIP): una factura que quedó en
        status="pending"/"processing" porque el backend se reinició a mitad
        de camino -- ver Fase A/B de _extraer_zip_y_despachar_individualmente
        en process_invoice_google_2.py y docs/plan-fase1-zip-REDISEÑO.md
        sección 3.

        `antes_de` DEBE ser el momento en que el proceso ACTUAL arrancó
        (InvoiceOrchestrator._iniciado_en), no un umbral relativo tipo
        "hace 30 minutos". Es la diferencia real que importa: cualquier
        invoice pending/processing con `updated` ANTERIOR al arranque de
        este proceso quedó huérfana con certeza (ningún código de ESTE
        proceso pudo haberla tocado antes de existir -- el loop de Fase B
        que la estaba procesando murió con el proceso viejo, sin importar
        cuántos minutos lleve, sean 10 segundos o hace 3 horas). Un umbral
        de antigüedad fijo (el diseño original, 30 minutos) es incorrecto
        en el caso más común real: con auto-restart (systemd/supervisor/
        docker restart=always), el proceso vuelve a arrancar en segundos, y
        el barrido corre a los 10s -- en ese momento las filas recién
        huérfanas tienen segundos de antigüedad, no 30 minutos, así que un
        umbral de edad las descarta TODAS y, como el barrido corre una sola
        vez, quedan "pending" invisibles para siempre. Comparar contra el
        arranque del proceso (no contra "ahora") es correcto sin importar
        cuánto haya tardado el restart.

        No hace falta distinguir "vino de un ZIP" de "factura suelta": el
        mismo botón "Reintentar" del dashboard resuelve ambos casos por
        igual. Mismo criterio de filtrar por tiempo en Python (no en el
        filtro de PocketBase) que find_stale_batch_items, por el mismo
        motivo."""
        try:
            candidatos = self._list_all(
                INVOICES_COLLECTION,
                '(status = "pending" || status = "processing") && deleted_at = ""',
                page_size=500,
            )
            resultado = []
            for inv in candidatos:
                actualizado = inv.get("updated")
                if not actualizado:
                    continue
                try:
                    ts = datetime.datetime.fromisoformat(actualizado.replace("Z", "+00:00"))
                except ValueError:
                    continue
                if ts < antes_de:
                    resultado.append(inv)
            return resultado
        except Exception as e:
            app_logger.warning(f"PocketBase: error en find_stale_invoices: {e}")
            return []


# ---------------------------------------------------------------------- #
# Helpers de respuesta / auth (privados al módulo)
# ---------------------------------------------------------------------- #
def _detalle(resp: requests.Response) -> Any:
    try:
        return resp.json()
    except Exception:
        return (resp.text or "")[:500]


def _json_o_error(resp: requests.Response, path: str, ok=(200,)) -> dict:
    if resp.status_code in ok:
        try:
            return resp.json()
        except Exception:
            return {"status_code": resp.status_code, "raw": (resp.text or "")[:500]}
    raise PocketBaseApiError(resp.status_code, _detalle(resp), path)


def _pb_filter_eq(field: str, value: str) -> str:
    """Arma un filtro PocketBase `field="value"` escapando comillas/backslashes."""
    escapado = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'{field}="{escapado}"'


def _jwt_exp_epoch(token: str) -> Optional[float]:
    """
    Decodifica el claim `exp` de un JWT sin depender de una librería de JWT
    (PocketBase no devuelve `expires_in` como sí hace BAS). Devuelve None si
    el token no es un JWT válido o no tiene `exp`.
    """
    try:
        payload_b64 = token.split(".")[1]
        padding = "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64 + padding))
        exp = payload.get("exp")
        return float(exp) if exp is not None else None
    except Exception:
        return None


if __name__ == "__main__":
    # Smoke test SEGURO (solo lectura): 1) auth real, 2) una consulta de lectura.
    import logging as _logging
    from dotenv import load_dotenv

    load_dotenv()
    _logging.basicConfig(level=_logging.INFO)
    cli = PocketBaseClient()
    print("base_url:", cli.base_url)
    print("1) auth ->", "OK, token len", len(cli.get_token()))

    try:
        job = cli.get_processing_job("__smoke_test_process_id__")
        print("2) get_processing_job ->", "sin coincidencia (None)" if job is None else job)
    except PocketBaseApiError as e:
        print("2) get_processing_job -> PocketBaseApiError", e.status_code, e.detail)
