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
            }
        except Exception as e:
            app_logger.warning(f"PocketBase: error en get_provider_cache({cuit}): {e}")
            return None

    def set_provider_cache(self, cuit: str, proveedor: dict) -> bool:
        """
        Guarda/actualiza el proveedor resuelto para un CUIT en la colección
        "bas_providers" (campos flat: bas_codigo, razon_social, nuevo,
        last_verified_at). `proveedor` es el dict que devuelve BasClient (con
        claves BAS reales: Codigo, RazonSocial, _nuevo). True si tuvo éxito.
        """
        try:
            cuit_normalizado = "".join(c for c in (cuit or "") if c.isdigit())
            if not cuit_normalizado or not proveedor:
                return False
            self._upsert(
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
            return True
        except Exception as e:
            app_logger.warning(f"PocketBase: error en set_provider_cache({cuit}): {e}")
            return False

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
