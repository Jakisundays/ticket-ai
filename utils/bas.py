"""
Cliente para la BAS CS WebAPI (ERP) — creación de órdenes de pago a partir de facturas.

Diseño y razonamiento documentados en: docs/bas-orden-de-pago-research.md

Flujo (robusto a ambos casos):
    1. Autenticación      -> POST /auth/token              (token cacheado + refresh)
    2. Validar factura    -> GET  /api/ConsultaComprobantesExternos
       - si no existe (204) -> POST /api/ComprobantesCompra  (alta previa)
    3. Crear orden pago   -> POST /api/OrdenesPago          (factura en ComprobantesAplicados)
    4. Confirmar          -> respuesta 201 RespuestaComprobantes (IdTransaccion + OP Prefijo/Numero)

Seguridad: los métodos de escritura (crear_comprobante_compra / crear_orden_de_pago)
aceptan `dry_run`. El flujo de alto nivel usa `dry_run=True` por defecto: arma y
devuelve el payload SIN hacer el POST, para poder revisarlo antes de impactar el ERP.
"""

import os
import time
import logging
import datetime
import unicodedata
from typing import Any, Optional

import requests

app_logger = logging.getLogger("app_logger")

# --- Códigos de tipo de comprobante en BAS (de GET /api/TiposComprobantes) ---
COMPROBANTES_FACTURA_COMPRA = ("MA", "MB", "MC", "MI", "MM")  # Factura de Compra A/B/C/I/M
COMPROBANTE_ORDEN_PAGO = "OP"                                  # Orden de pago
COMPROBANTE_APLICACION_PROV = "AV"                             # Aplicac. Ctas. Ctes. Proveedores

# --- Arrays de medios de pago válidos dentro de OrdenDePago (agnóstico al medio) ---
MEDIOS_PAGO_VALIDOS = frozenset(
    {
        "Efectivos",
        "PagosPorBanco",
        "CobrosPorBanco",
        "Cheques",
        "ChequesPropios",
        "Pagares",
        "PagaresPropios",
        "Tarjetas",
    }
)

# Prefijo de cuenta corriente — schema pattern [CPA]: C=Clientes, P=Proveedores, A=Agentes.
PREFIJOS_CTACTE_VALIDOS = frozenset({"C", "P", "A"})
PREFIJO_CTACTE_PROVEEDORES = "P"  # una OP paga a un proveedor

# Indicador Ingreso/Egreso de cada medio de pago — schema pattern [IE]. Un pago = Egreso.
INGRESO = "I"
EGRESO = "E"

DEFAULT_BASE_URL = "http://190.210.77.103:32501"


def medio_pago_egreso(medio_pago: str, importe: float, **extra) -> dict:
    """Arma un ítem de medio de pago de egreso (IngresooEgreso='E'), requerido por BAS."""
    item = {"MedioPago": medio_pago, "Importe": importe, "IngresooEgreso": EGRESO}
    item.update(extra)
    return item


class BasApiError(Exception):
    """Error de la API de BAS. Conserva el código HTTP y el detalle del backend."""

    def __init__(self, status_code: int, detail: Any, path: str = ""):
        self.status_code = status_code
        self.detail = detail
        self.path = path
        super().__init__(f"BAS API {status_code} en {path}: {detail}")


class BasClient:
    """
    Cliente sincrónico de la BAS CS WebAPI.

    Configuración por variables de entorno:
        BAS_BASE_URL      (default: DEFAULT_BASE_URL)
        BAS_GRAND_TYPE    (default: "password")   -- nombre tal cual está en el .env
        BAS_USER, BAS_PASSWORD
        BAS_CLIENT_ID     (default: "api")
        BAS_CLIENT_SECRET (default: "secret")
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        user: Optional[str] = None,
        password: Optional[str] = None,
        grant_type: Optional[str] = None,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        timeout: int = 30,
        token_margin: int = 60,
    ):
        self.base_url = (base_url or os.getenv("BAS_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.user = user or os.getenv("BAS_USER")
        self.password = password or os.getenv("BAS_PASSWORD")
        self.grant_type = grant_type or os.getenv("BAS_GRAND_TYPE") or "password"
        self.client_id = client_id or os.getenv("BAS_CLIENT_ID") or "api"
        self.client_secret = client_secret or os.getenv("BAS_CLIENT_SECRET") or "secret"
        self.timeout = timeout
        self.token_margin = token_margin

        self._access_token: Optional[str] = None
        self._refresh_token: Optional[str] = None
        self._token_expira_en: float = 0.0  # epoch seconds

    # ------------------------------------------------------------------ #
    # Autenticación / token
    # ------------------------------------------------------------------ #
    def _solicitar_token(self, refresh_token: Optional[str] = None) -> dict:
        """POST /auth/token (multipart/form-data). Usa refresh si se provee."""
        if refresh_token:
            campos = {
                "grant_type": "refresh_token",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": refresh_token,
            }
        else:
            if not self.user or not self.password:
                raise BasApiError(0, "Faltan BAS_USER / BAS_PASSWORD", "/auth/token")
            campos = {
                "grant_type": self.grant_type,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "username": self.user,
                "password": self.password,
            }
        # `files=` fuerza multipart/form-data; cada campo va como parte de texto.
        files = {k: (None, str(v)) for k, v in campos.items()}
        resp = requests.post(f"{self.base_url}/auth/token", files=files, timeout=self.timeout)
        if resp.status_code != 200:
            raise BasApiError(resp.status_code, _detalle(resp), "/auth/token")
        return resp.json()

    def _guardar_token(self, data: dict) -> None:
        self._access_token = data.get("access_token")
        self._refresh_token = data.get("refresh_token") or self._refresh_token
        expires_in = int(data.get("expires_in") or 0)
        self._token_expira_en = time.time() + max(0, expires_in - self.token_margin)

    def _token_valido(self) -> bool:
        return bool(self._access_token) and time.time() < self._token_expira_en

    def get_token(self) -> str:
        """Devuelve un access_token válido (cacheado, con refresh y fallback a re-login)."""
        if self._token_valido():
            return self._access_token
        # Intentar refresh primero
        if self._refresh_token:
            try:
                self._guardar_token(self._solicitar_token(refresh_token=self._refresh_token))
                app_logger.info("BAS: token renovado vía refresh_token")
                return self._access_token
            except BasApiError as e:
                app_logger.warning(f"BAS: refresh falló ({e.status_code}); re-autenticando")
        self._guardar_token(self._solicitar_token())
        app_logger.info("BAS: autenticación exitosa")
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
        url = f"{self.base_url}{path}"
        headers = {"Authorization": f"Bearer {self.get_token()}"}
        resp = requests.request(
            method, url, params=params, json=json_body, headers=headers, timeout=self.timeout
        )
        # Token rechazado: invalidar y reintentar una sola vez.
        if resp.status_code == 401 and _reintento_auth:
            app_logger.warning("BAS: 401, invalidando token y reintentando")
            self._invalidar_token()
            return self._request(
                method, path, params=params, json_body=json_body, _reintento_auth=False
            )
        return resp

    # ------------------------------------------------------------------ #
    # 1/2. Consultas (lectura, idempotentes)
    # ------------------------------------------------------------------ #
    def consultar_comprobante(
        self,
        empresa: int,
        sucursal: int,
        comprobante: str,
        prefijo: str,
        numero: int,
        fecha: Optional[str] = None,
    ) -> Optional[dict]:
        """GET /api/ConsultaComprobantes — por numeración interna de BAS. None si 204."""
        params = {
            "Empresa": empresa,
            "Sucursal": sucursal,
            "Comprobante": comprobante,
            "Prefijo": prefijo,
            "Numero": numero,
        }
        if fecha:
            params["Fecha"] = fecha
        resp = self._request("GET", "/api/ConsultaComprobantes", params=params)
        return _normalizar_comprobante(_json_o_none(resp, "/api/ConsultaComprobantes"))

    def consultar_comprobante_externo(
        self,
        empresa: int,
        sucursal: int,
        comprobante: str,
        *,
        prefijo_externo: Optional[str] = None,
        numero_externo: Optional[int] = None,
        fecha_externo: Optional[str] = None,
        prefijo: Optional[str] = None,
        numero: Optional[int] = None,
    ) -> Optional[dict]:
        """
        GET /api/ConsultaComprobantesExternos — por el número del proveedor (lo que
        extrae Invoicy). Devuelve el comprobante (con Prefijo/Numero internos) o None (204).

        El backend exige una de dos combinaciones (validado contra el servidor):
          - `prefijo` + `numero` (numeración interna de BAS), o
          - `fecha_externo` + `prefijo_externo` + `numero_externo`.
        """
        tiene_interno = prefijo is not None and numero is not None
        tiene_externo = (
            fecha_externo is not None
            and prefijo_externo is not None
            and numero_externo is not None
        )
        if not (tiene_interno or tiene_externo):
            raise ValueError(
                "ConsultaComprobantesExternos requiere (prefijo + numero) o "
                "(fecha_externo + prefijo_externo + numero_externo)."
            )
        params: dict = {"Empresa": empresa, "Sucursal": sucursal, "Comprobante": comprobante}
        if prefijo_externo is not None:
            params["PrefijoComprobanteExterno"] = prefijo_externo
        if numero_externo is not None:
            params["NumeroComprobanteExterno"] = numero_externo
        if fecha_externo is not None:
            params["FechaComprobanteExterno"] = fecha_externo
        if prefijo is not None:
            params["Prefijo"] = prefijo
        if numero is not None:
            params["Numero"] = numero
        resp = self._request("GET", "/api/ConsultaComprobantesExternos", params=params)
        return _normalizar_comprobante(_json_o_none(resp, "/api/ConsultaComprobantesExternos"))

    def consultar_talonarios(self, empresa: int) -> list:
        """
        GET /api/Talonarios/{empresa} — talonarios (numeradores) de la empresa.
        Nota: el {id} de la ruta es el CÓDIGO DE EMPRESA, no el del talonario.
        """
        resp = self._request("GET", f"/api/Talonarios/{empresa}")
        data = _json_o_none(resp, f"/api/Talonarios/{empresa}")
        if data is None:
            return []
        return data if isinstance(data, list) else [data]

    def buscar_prefijo_talonario(
        self, empresa: int, comprobante: str, excluir_saldo_inicial: bool = True
    ) -> Optional[str]:
        """
        Devuelve el `Prefijo` del talonario que emite `comprobante` (ej. 'MA', 'OP').
        Evita por defecto los talonarios de 'SALDO INICIAL'. None si no hay ninguno.
        Así el Prefijo se resuelve dinámicamente en vez de hardcodearlo.
        """
        for t in self.consultar_talonarios(empresa):
            codigos = [c.get("Codigo") for c in t.get("Comprobantes", [])]
            if comprobante in codigos:
                desc = (t.get("Descripcion") or "").upper()
                if excluir_saldo_inicial and "SALDO INICIAL" in desc:
                    continue
                return t.get("Prefijo")
        return None

    def obtener_proveedor(self, codigo: str) -> Optional[dict]:
        """GET /api/Proveedores/{id} — proveedor por su Código (Texto ≤8). None si 204."""
        resp = self._request("GET", f"/api/Proveedores/{codigo}")
        return _json_o_none(resp, f"/api/Proveedores/{codigo}")

    def buscar_proveedor_por_razon_social(self, razon_social: str) -> Optional[dict]:
        """GET /api/Proveedores/razonsocial={x} — coincidencia exacta de Razón Social."""
        resp = self._request("GET", f"/api/Proveedores/razonsocial={razon_social}")
        data = _json_o_none(resp, "/api/Proveedores/razonsocial=")
        return _normalizar_comprobante(data)

    def listar_proveedores(self, page_size: int = 500, max_paginas: int = 20) -> list:
        """
        Trae TODOS los proveedores paginando (no hay filtro por CUIT en la API).
        `max_paginas` es un techo de seguridad (500*20 = 10000 proveedores).
        """
        proveedores = []
        for pagina in range(1, max_paginas + 1):
            resp = self._request(
                "GET", "/api/Proveedores", params={"pageSize": page_size, "pageNumber": pagina}
            )
            data = _json_o_none(resp, "/api/Proveedores")
            items = data if isinstance(data, list) else ([data] if data else [])
            if not items:
                break
            proveedores.extend(items)
            if len(items) < page_size:
                break
        return proveedores

    def buscar_proveedor_por_cuit(
        self, cuit: str, page_size: int = 500, max_paginas: int = 20
    ) -> Optional[dict]:
        """
        Busca un proveedor por CUIT (campo `NumeroImpositivo1`), comparando solo dígitos
        (ignora guiones/espacios). No hay filtro por CUIT en la API: pagina el maestro y
        corta apenas encuentra la coincidencia (no espera a traer todo el listado).
        Con >1000 proveedores esto puede tardar varios segundos si el CUIT no existe
        (recorre todo el maestro) — conviene cachear el resultado del lado del caller.
        """
        objetivo = "".join(c for c in cuit if c.isdigit())
        if not objetivo:
            return None
        for pagina in range(1, max_paginas + 1):
            resp = self._request(
                "GET", "/api/Proveedores", params={"pageSize": page_size, "pageNumber": pagina}
            )
            data = _json_o_none(resp, "/api/Proveedores")
            items = data if isinstance(data, list) else ([data] if data else [])
            if not items:
                break
            for p in items:
                numero = p.get("NumeroImpositivo1") or ""
                if "".join(c for c in numero if c.isdigit()) == objetivo:
                    return p
            if len(items) < page_size:
                break
        return None

    def codigo_cuenta_corriente_proveedor(self, proveedor: dict) -> Optional[str]:
        """
        El `CodigoCuentaCorriente` que exige OrdenDePago/ComprobanteCompra ES el propio
        `Codigo` del proveedor (la cuenta corriente de un proveedor se identifica por su
        código de maestro; por eso PrefijoCuentaCorriente='P' + este código).
        NO confundir con `Proveedor.CuentasCorrientes[].ImputacionContable`, que es la
        cuenta CONTABLE de imputación (plan de cuentas), no el código de cta cte de la OP.
        """
        return proveedor.get("Codigo") if proveedor else None

    # ------------------------------------------------------------------ #
    # Builders de payload (sin efectos)
    # ------------------------------------------------------------------ #
    def construir_payload_orden_pago(
        self,
        *,
        empresa: int,
        sucursal: int,
        fecha: str,
        total: float,
        comprobantes_aplicados: list,
        pagos: Optional[dict] = None,
        prefijo_ctacte: Optional[str] = None,
        codigo_ctacte: Optional[str] = None,
        moneda_ctacte: Optional[str] = None,
        cotizacion: float = 1.0,
        retenciones: Optional[list] = None,
        observacion: Optional[str] = None,
        prefijo: Optional[str] = None,
        numero: Optional[int] = None,
        usuario: Optional[str] = None,
        caja: Optional[str] = None,
    ) -> dict:
        """
        Arma el body OrdenDePago. `pagos` es agnóstico al medio: un dict
        {nombreArray: [items]} (ej. {"PagosPorBanco": [{"MedioPago": "...", "Importe": 100}]}).

        `caja`: código de caja (Texto 4). BAS lo exige en runtime para poder
        registrar el medio de pago, aunque el schema no lo marque required.
        """
        payload: dict = {
            "Fecha": fecha,
            "Total": total,
            "Empresa": empresa,
            "Sucursal": sucursal,
            "CotizacionMonedaComprobante": cotizacion,
            "ComprobantesAplicados": comprobantes_aplicados,
        }
        if prefijo_ctacte is not None:
            if prefijo_ctacte not in PREFIJOS_CTACTE_VALIDOS:
                raise ValueError(
                    f"PrefijoCuentaCorriente debe ser uno de {sorted(PREFIJOS_CTACTE_VALIDOS)} "
                    "(C=Clientes, P=Proveedores, A=Agentes)."
                )
            payload["PrefijoCuentaCorriente"] = prefijo_ctacte
        if codigo_ctacte is not None:
            payload["CodigoCuentaCorriente"] = codigo_ctacte
        if moneda_ctacte is not None:
            payload["MonedaCtaCte"] = moneda_ctacte
        if observacion is not None:
            payload["ObservacionComprobante"] = observacion
        if prefijo is not None:
            payload["Prefijo"] = prefijo
        if numero is not None:
            payload["Numero"] = numero
        if usuario is not None:
            payload["Usuario"] = usuario
        if caja is not None:
            payload["Caja"] = caja
        if retenciones:
            payload["Retenciones"] = retenciones
        for nombre, items in (pagos or {}).items():
            if nombre not in MEDIOS_PAGO_VALIDOS:
                raise ValueError(
                    f"Medio de pago '{nombre}' inválido. Válidos: {sorted(MEDIOS_PAGO_VALIDOS)}"
                )
            payload[nombre] = items
        return payload

    def construir_payload_proveedor(
        self,
        *,
        codigo: str,
        razon_social: str,
        empresa_alta: int,
        trat_impositivo: str,
        trat_impositivo_prov: str,
        numero_impositivo_tipo: Optional[str] = None,
        numero_impositivo1: Optional[str] = None,
    ) -> dict:
        """
        Arma el body Proveedor. Requeridos por schema: Codigo, RazonSocial,
        TratImpositivo, TratImpositivoProv, EmpresaAlta. Los códigos de
        TratImpositivo/TratImpositivoProv deben salir de
        GET /api/TratamientosImpositivos(Provinciales)/{empresa} — no inventarlos.
        """
        payload: dict = {
            "Codigo": codigo,
            "RazonSocial": razon_social,
            "EmpresaAlta": empresa_alta,
            "TratImpositivo": trat_impositivo,
            "TratImpositivoProv": trat_impositivo_prov,
        }
        if numero_impositivo_tipo is not None:
            payload["NumeroImpositivoTipo"] = numero_impositivo_tipo
        if numero_impositivo1 is not None:
            payload["NumeroImpositivo1"] = numero_impositivo1
        return payload

    # ------------------------------------------------------------------ #
    # 1B/3. Escrituras (con dry_run de seguridad)
    # ------------------------------------------------------------------ #
    def crear_proveedor(self, payload: dict, *, dry_run: bool = False) -> dict:
        """POST /api/Proveedores. Da de alta un proveedor nuevo en el maestro."""
        if dry_run:
            app_logger.info("BAS [dry_run]: NO se crea Proveedor; se devuelve el payload")
            return {"dry_run": True, "endpoint": "/api/Proveedores", "payload": payload}
        resp = self._request("POST", "/api/Proveedores", json_body=payload)
        return _json_o_error(resp, "/api/Proveedores", ok=(200, 201))

    def crear_comprobante_compra(
        self, payload: dict, *, ignora_advertencias: bool = False, dry_run: bool = False
    ) -> dict:
        """POST /api/ComprobantesCompra. Registra la factura de compra en BAS."""
        if dry_run:
            app_logger.info("BAS [dry_run]: NO se crea ComprobanteCompra; se devuelve el payload")
            return {"dry_run": True, "endpoint": "/api/ComprobantesCompra", "payload": payload}
        resp = self._request(
            "POST",
            "/api/ComprobantesCompra",
            params={"IgnoraAdvertencias": str(bool(ignora_advertencias)).lower()},
            json_body=payload,
        )
        return _json_o_error(resp, "/api/ComprobantesCompra", ok=(200, 201))

    def crear_orden_de_pago(self, payload: dict, *, dry_run: bool = False) -> dict:
        """POST /api/OrdenesPago. Devuelve RespuestaComprobantes (201) con IdTransaccion + OP."""
        if dry_run:
            app_logger.info("BAS [dry_run]: NO se crea OrdenDePago; se devuelve el payload")
            return {"dry_run": True, "endpoint": "/api/OrdenesPago", "payload": payload}
        resp = self._request("POST", "/api/OrdenesPago", json_body=payload)
        return _json_o_error(resp, "/api/OrdenesPago", ok=(200, 201))

    # ------------------------------------------------------------------ #
    # 4. Orquestación de alto nivel (flujo robusto)
    # ------------------------------------------------------------------ #
    def verificar_o_dar_de_alta_proveedor(
        self,
        *,
        cuit: str,
        razon_social: str,
        empresa_alta: int,
        trat_impositivo: str,
        trat_impositivo_prov: str,
        codigo_sugerido: Optional[str] = None,
        dry_run: bool = False,
    ) -> dict:
        """
        Busca un proveedor por CUIT; si no existe, lo da de alta automáticamente.

        Orquesta piezas ya existentes (buscar_proveedor_por_cuit,
        construir_payload_proveedor, crear_proveedor) que hasta ahora se usaban
        sueltas. Devuelve el proveedor (existente o recién creado) con una
        clave extra `_nuevo: bool` para que el caller sepa si hubo alta.
        """
        encontrado = self.buscar_proveedor_por_cuit(cuit)
        if encontrado is not None:
            app_logger.info(
                f"BAS: proveedor con CUIT {cuit} ya existe ({encontrado.get('Codigo')})"
            )
            return {**encontrado, "_nuevo": False}

        codigo = codigo_sugerido or _derivar_codigo_proveedor(razon_social)
        payload = self.construir_payload_proveedor(
            codigo=codigo,
            razon_social=razon_social,
            empresa_alta=empresa_alta,
            trat_impositivo=trat_impositivo,
            trat_impositivo_prov=trat_impositivo_prov,
            numero_impositivo_tipo="80",  # CUIT
            numero_impositivo1=cuit,
        )
        app_logger.info(
            f"BAS: proveedor con CUIT {cuit} no existe; dando de alta como '{codigo}'"
        )
        creado = self.crear_proveedor(payload, dry_run=dry_run)
        if dry_run:
            return {**creado, "_nuevo": True}
        # crear_proveedor puede devolver un ack parcial; re-consultar para
        # devolver siempre la misma forma que el caso "ya existe".
        proveedor = self.obtener_proveedor(codigo) or creado
        return {**proveedor, "_nuevo": True}

    def crear_orden_de_pago_desde_factura(
        self,
        *,
        empresa: int,
        sucursal: int,
        comprobante_factura: str,
        prefijo_externo: str,
        numero_externo: int,
        importe: float,
        fecha: Optional[str] = None,
        fecha_externo: Optional[str] = None,
        prefijo_op: Optional[str] = None,
        caja_op: Optional[str] = None,
        prefijo_ctacte: Optional[str] = None,
        codigo_ctacte: Optional[str] = None,
        pagos: Optional[dict] = None,
        retenciones: Optional[list] = None,
        observacion: Optional[str] = None,
        comprobante_compra_payload: Optional[dict] = None,
        registrar_si_no_existe: bool = True,
        dry_run: bool = True,
    ) -> dict:
        """
        Flujo completo y robusto:
          1) Valida la factura por número externo del proveedor.
          2) Si no existe y `registrar_si_no_existe`, la registra (requiere
             `comprobante_compra_payload`).
          3) Arma e (opcionalmente, según `dry_run`) crea la orden de pago.

        `dry_run=True` (default) NO impacta el ERP: arma todo y devuelve el payload
        de la OP para revisión. Poné `dry_run=False` para ejecutar de verdad.

        `prefijo_op`: Prefijo del talonario de la propia Orden de Pago (BAS lo exige
        en runtime aunque el schema no lo marque required). Resolverlo con
        `buscar_prefijo_talonario(empresa, "OP")` o pasarlo fijo si ya se conoce.

        `caja_op`: código de caja de la OP (BAS también lo exige en runtime para
        poder registrar el medio de pago).

        Devuelve un dict con las claves: `factura` (consulta o alta), `orden_pago`
        (respuesta o payload en dry_run).
        """
        fecha = fecha or datetime.date.today().isoformat()

        # 1) Validar factura por número externo.
        encontrada = self.consultar_comprobante_externo(
            empresa,
            sucursal,
            comprobante_factura,
            prefijo_externo=prefijo_externo,
            numero_externo=numero_externo,
            fecha_externo=fecha_externo,
        )

        if encontrada is not None:
            app_logger.info("BAS: factura encontrada en cuenta corriente")
            factura_resultado = encontrada
            prefijo_int = encontrada.get("Prefijo")
            numero_int = encontrada.get("Numero")
        else:
            # 2) No existe: registrarla si corresponde.
            if not registrar_si_no_existe:
                raise BasApiError(
                    404,
                    "La factura no existe en BAS y registrar_si_no_existe=False",
                    "/api/ConsultaComprobantesExternos",
                )
            if not comprobante_compra_payload:
                raise ValueError(
                    "La factura no existe en BAS; se requiere `comprobante_compra_payload` "
                    "para registrarla antes de crear la orden de pago."
                )
            app_logger.info("BAS: factura no encontrada; registrando ComprobanteCompra")
            factura_resultado = self.crear_comprobante_compra(
                comprobante_compra_payload, dry_run=dry_run
            )
            cmp = _primer_comprobante(factura_resultado)
            prefijo_int = cmp.get("Prefijo") if cmp else None
            numero_int = cmp.get("Numero") if cmp else None

        # 3) Construir y crear la orden de pago.
        comprobantes_aplicados = [
            {
                "Comprobante": comprobante_factura,
                "Prefijo": prefijo_int,
                "Numero": int(numero_int) if numero_int not in (None, "") else None,
                "Importe": importe,
            }
        ]
        payload_op = self.construir_payload_orden_pago(
            empresa=empresa,
            sucursal=sucursal,
            fecha=fecha,
            total=importe,
            comprobantes_aplicados=comprobantes_aplicados,
            pagos=pagos or {},
            prefijo=prefijo_op,
            caja=caja_op,
            prefijo_ctacte=prefijo_ctacte,
            codigo_ctacte=codigo_ctacte,
            retenciones=retenciones,
            observacion=observacion,
        )
        try:
            orden_pago = self.crear_orden_de_pago(payload_op, dry_run=dry_run)
        except BasApiError as e:
            # La factura YA quedó registrada (factura_resultado); no perder esa
            # información aunque la OP falle. El caller distingue éxito/error
            # revisando si orden_pago trae la clave "_error".
            app_logger.warning(
                f"BAS: factura registrada OK, pero la orden de pago falló "
                f"({e.status_code}): {e.detail}"
            )
            orden_pago = {"_error": True, "status_code": e.status_code, "detail": e.detail}
        return {"factura": factura_resultado, "orden_pago": orden_pago}


# ---------------------------------------------------------------------- #
# Helpers de alta de proveedor
# ---------------------------------------------------------------------- #
def _derivar_codigo_proveedor(razon_social: str) -> str:
    """
    Código de proveedor (Texto <=8) derivado de la razón social: mayúsculas,
    sin acentos ni espacios, truncado a 8 -- mismo criterio informal ya usado
    para dar de alta a SUPERCOOP ("SUPERCOO"). No garantiza unicidad global;
    si colisiona con otro proveedor, el caller debe pasar `codigo_sugerido`.
    """
    nfkd = unicodedata.normalize("NFKD", razon_social or "")
    limpio = "".join(c for c in nfkd if not unicodedata.combining(c) and c.isalnum())
    return limpio.upper()[:8] or "PROVNN"


# ---------------------------------------------------------------------- #
# Helpers de respuesta
# ---------------------------------------------------------------------- #
def _detalle(resp: requests.Response) -> Any:
    try:
        return resp.json()
    except Exception:
        return (resp.text or "")[:500]


def _json_o_none(resp: requests.Response, path: str):
    """200 -> json (dict o list); 204 -> None; otro -> BasApiError."""
    if resp.status_code == 200:
        return resp.json()
    if resp.status_code == 204:
        return None
    raise BasApiError(resp.status_code, _detalle(resp), path)


def _normalizar_comprobante(data: Any) -> Optional[dict]:
    """
    Normaliza la respuesta de una consulta a 'un comprobante o None'.
    El backend puede devolver 204, un objeto, o un array (vacío = sin coincidencia).
    Si hay varias coincidencias se devuelve la primera (la búsqueda es por número exacto).
    """
    if not data:  # None, [], {}, ""
        return None
    if isinstance(data, list):
        return data[0] if data else None
    if isinstance(data, dict):
        return data
    return None


def _json_o_error(resp: requests.Response, path: str, ok=(200, 201)) -> dict:
    if resp.status_code in ok:
        try:
            return resp.json()
        except Exception:
            return {"status_code": resp.status_code, "raw": (resp.text or "")[:500]}
    raise BasApiError(resp.status_code, _detalle(resp), path)


def _primer_comprobante(respuesta: Any) -> Optional[dict]:
    """Extrae el primer Cmp de una RespuestaComprobantes (o del payload en dry_run)."""
    if not isinstance(respuesta, dict):
        return None
    comps = respuesta.get("Comprobantes")
    if isinstance(comps, list) and comps:
        return comps[0]
    return None


if __name__ == "__main__":
    # Smoke test SEGURO (no escribe nada en el ERP):
    #   1) auth real, 2) consulta de lectura válida, 3) armado de payload de OP.
    import json as _json
    from dotenv import load_dotenv

    load_dotenv()
    logging.basicConfig(level=logging.INFO)
    cli = BasClient()
    print("base_url:", cli.base_url)
    print("1) auth ->", "OK, token len", len(cli.get_token()))

    # 2) Consulta de lectura (combinación válida: fecha + prefijo + número externo).
    try:
        res = cli.consultar_comprobante_externo(
            empresa=1,
            sucursal=1,
            comprobante="MA",
            fecha_externo="2025-01-01",
            prefijo_externo="0001",
            numero_externo=12345678,
        )
        print("2) consulta externa ->", "sin coincidencia (204)" if res is None else res)
    except BasApiError as e:
        print("2) consulta externa -> BasApiError", e.status_code, e.detail)

    # 3) Armado del payload de la orden de pago (puro, sin red).
    payload = cli.construir_payload_orden_pago(
        empresa=1,
        sucursal=1,
        fecha=datetime.date.today().isoformat(),
        total=104694.0,
        comprobantes_aplicados=[
            {"Comprobante": "MA", "Prefijo": "0001", "Numero": 99, "Importe": 104694.0}
        ],
        pagos={"Efectivos": [{"MedioPago": "EF", "Importe": 104694.0}]},
    )
    print("3) payload OrdenDePago (dry):")
    print(_json.dumps(payload, indent=2, ensure_ascii=False))
