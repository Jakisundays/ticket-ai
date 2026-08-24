"""
Test offline: compatibilidad de ruteo para los 8 endpoints del dashboard
que reciben `process_id` como path param en
routes/process_invoice_google_2.py, tras el cambio de separador de
sub-documentos generados por ZIP: el nuevo separador es "--"
(ej. "abc123--email-000-factura_pdf"), reemplazando al separador viejo "/"
(ej. "abc123/email-000-factura_pdf").

Por qué importa cada escenario:

- F1 (separador nuevo "--"): confirma que un process_id de un archivo
  dentro de un ZIP (generado por batch_import.py con "--") llega INTACTO
  como un solo string a los 8 endpoints reales que el dashboard usa para
  reintentar extracción, ver el archivo, crear orden de pago, registrar
  comprobante, re-chequear proveedor, o borrar la factura/orden de pago.
  Si algún endpoint recortara o rechazara ese process_id, el dashboard
  quedaría roto para TODO documento que vino de un ZIP.

- F2 (separador viejo "/", literal y percent-encoded "%2F"): reproduce
  EMPÍRICAMENTE el bug original que motivó el cambio de separador --
  Starlette parte un {process_id} de un solo segmento en la barra "/", así
  que un process_id viejo con "/" nunca llegaba completo al handler (o
  directamente 404). Este test confirma que ese bug sigue siendo
  reproducible con el separador viejo (para saber que el fix real fue
  cambiar el separador, no una casualidad de ruteo) y que el separador
  nuevo "--" no sufre el mismo problema porque "-" nunca es tratado como
  separador de segmento por Starlette.

Metodología: en vez de reimplementar a mano cómo Starlette hace matching
de path converters (fácil de hacer mal y de "confirmar" un bug que no
existe en el código real), se arma un APIRouter() de juguete registrando
los 8 paths EXACTOS tal como están hoy en routes/process_invoice_google_2.py,
con un handler dummy idéntico para los 8 (lo único que importa acá es el
path, no la lógica de negocio -- esa lógica se prueba en otros scripts de
este repo). Se monta ese router en una FastAPI() real con el mismo
prefix="/gemini2" y se ejercita el matching real vía
fastapi.testclient.TestClient -- así el resultado depende del motor de
ruteo real de Starlette/FastAPI, no de una reimplementación paralela.
Como salvaguarda extra contra que estos 8 paths se desincronicen del
router real, el test primero verifica que cada path literal sigue
apareciendo tal cual en el código fuente de
routes/process_invoice_google_2.py, antes de ejercitar nada.

Uso: python3 scripts/test_offline_zip_endpoints_routing_compat.py
Sale con código 0 si todo pasa, 1 si algo falla.
"""

import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI, APIRouter  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
TARGET_FILE = REPO_ROOT / "routes" / "process_invoice_google_2.py"

FALLOS = []


def check(descripcion, condicion):
    estado = "OK" if condicion else "FALLA"
    print(f"  [{estado}] {descripcion}")
    if not condicion:
        FALLOS.append(descripcion)


# ============================================================================
# Los 8 endpoints reales del dashboard que reciben {process_id}, tal como
# están montados hoy en routes/process_invoice_google_2.py (ese router se
# monta en la app con prefix="/gemini2").
# ============================================================================
ENDPOINTS = [
    ("POST", "/retry-op/{process_id}"),
    ("GET", "/invoices/{process_id}/file"),
    ("POST", "/invoices/{process_id}/retry-extraction"),
    ("POST", "/payment-orders/{process_id}/create"),
    ("POST", "/invoices/{process_id}/register-comprobante"),
    ("POST", "/invoices/{process_id}/recheck-provider"),
    ("DELETE", "/invoices/{process_id}"),
    ("DELETE", "/payment-orders/{process_id}"),
]

# ============================================================================
# Salvaguarda: confirmar que estos 8 paths siguen apareciendo tal cual en
# el código fuente real, para que este test no quede "probando" rutas que
# ya no existen (o que nunca existieron) en process_invoice_google_2.py.
# No se importa el módulo (evita efectos secundarios de nivel de módulo) --
# solo se lee el texto fuente.
# ============================================================================
print("=" * 78)
print("Salvaguarda -- los 8 paths siguen presentes en el código fuente real")
print("=" * 78)

if TARGET_FILE.exists():
    codigo_fuente = TARGET_FILE.read_text(encoding="utf-8")
    for metodo, path in ENDPOINTS:
        check(
            f"'{path}' ({metodo}) sigue presente en "
            f"{TARGET_FILE.relative_to(REPO_ROOT)}",
            path in codigo_fuente,
        )
else:
    print(
        f"  [AVISO] No se encontró {TARGET_FILE} en este entorno -- se omite la "
        f"verificación de presencia en el código fuente y se prueba solo el "
        f"comportamiento de ruteo real de Starlette/FastAPI con estos 8 paths "
        f"(tal como están documentados en el router real)."
    )

if FALLOS:
    print(
        "\nLos 8 paths de este test se desincronizaron del router real -- "
        "abortando."
    )
    sys.exit(1)


# ============================================================================
# Router de juguete: mismos 8 paths EXACTOS, mismo prefix "/gemini2", un
# handler dummy que solo devuelve el process_id recibido -- lo que se
# ejercita es el matching real de FastAPI/Starlette, no una
# reimplementación a mano de path converters.
# ============================================================================
router = APIRouter(prefix="/gemini2")


def _make_handler():
    def _handler(process_id: str):
        return {"process_id": process_id}

    return _handler


for _metodo, _path in ENDPOINTS:
    router.add_api_route(_path, _make_handler(), methods=[_metodo])

app = FastAPI()
app.include_router(router)
client = TestClient(app, raise_server_exceptions=False)

PREFIX = "/gemini2"  # el prefix real con el que se monta este router en la app


def _pedir(metodo, path_template, process_id):
    """Arma la URL completa (con el prefix real) y hace el request real."""
    path_completo = PREFIX + path_template.format(process_id=process_id)
    return client.request(metodo, path_completo)


def _extraer_process_id(resp):
    if resp.status_code == 404:
        return None
    try:
        return resp.json().get("process_id")
    except Exception:
        return None


PROCESS_ID_NUEVO = "abc123--email-000-factura_pdf"
PROCESS_ID_VIEJO = "abc123/email-000-factura_pdf"
PROCESS_ID_VIEJO_ENCODED = urllib.parse.quote(PROCESS_ID_VIEJO, safe="")  # -> %2F

check(
    "El fixture del separador viejo percent-encoded contiene '%2F' "
    f"(quote()='{PROCESS_ID_VIEJO_ENCODED}')",
    "%2F" in PROCESS_ID_VIEJO_ENCODED,
)

# ============================================================================
# Un solo loop sobre los 8 endpoints: para cada uno se prueba F1 (separador
# nuevo, debe matchear completo) y F2 (separador viejo, literal y
# percent-encoded, NO debe matchear completo).
# ============================================================================
print()
print("=" * 78)
print(
    "F1/F2 -- matching real de FastAPI/Starlette para los 8 endpoints, con "
    "process_id en formato nuevo ('--') y viejo ('/', literal y %2F)"
)
print("=" * 78)

resultados_f1 = []
resultados_f2_literal = []
resultados_f2_encoded = []

for metodo, path_template in ENDPOINTS:
    print(f"-- {metodo} {path_template}")

    # F1: separador nuevo "--" -> debe matchear completo, sin recortar nada.
    resp_f1 = _pedir(metodo, path_template, PROCESS_ID_NUEVO)
    process_id_f1 = _extraer_process_id(resp_f1)
    ok_f1 = resp_f1.status_code != 404 and process_id_f1 == PROCESS_ID_NUEVO
    check(
        f"F1 {metodo} {path_template} -- process_id nuevo llega COMPLETO "
        f"(status={resp_f1.status_code}, recibido={process_id_f1!r})",
        ok_f1,
    )
    resultados_f1.append(ok_f1)

    # F2a: separador viejo "/" literal -> NO debe matchear completo
    # (Starlette lo trata como separador de segmento: 404, o matchea otra
    # cosa con el process_id truncado).
    resp_f2a = _pedir(metodo, path_template, PROCESS_ID_VIEJO)
    process_id_f2a = _extraer_process_id(resp_f2a)
    bug_f2a = resp_f2a.status_code == 404 or process_id_f2a != PROCESS_ID_VIEJO
    check(
        f"F2 {metodo} {path_template} -- barra LITERAL en process_id NO "
        f"matchea completo (status={resp_f2a.status_code}, "
        f"recibido={process_id_f2a!r})",
        bug_f2a,
    )
    resultados_f2_literal.append(bug_f2a)

    # F2b: separador viejo "/" percent-encoded (%2F) -> tampoco debe
    # matchear completo (el ASGI server/Starlette lo decodifica a "/"
    # antes del matching de rutas).
    resp_f2b = _pedir(metodo, path_template, PROCESS_ID_VIEJO_ENCODED)
    process_id_f2b = _extraer_process_id(resp_f2b)
    bug_f2b = resp_f2b.status_code == 404 or process_id_f2b != PROCESS_ID_VIEJO
    check(
        f"F2 {metodo} {path_template} -- barra PERCENT-ENCODED (%2F) en "
        f"process_id NO matchea completo (status={resp_f2b.status_code}, "
        f"recibido={process_id_f2b!r})",
        bug_f2b,
    )
    resultados_f2_encoded.append(bug_f2b)

# ============================================================================
# Resumen: cuántos de los 8 endpoints pasaron cada escenario.
# ============================================================================
n_f1_ok = sum(resultados_f1)
n_f2_literal_ok = sum(resultados_f2_literal)
n_f2_encoded_ok = sum(resultados_f2_encoded)
n_total_ok = sum(
    1
    for ok1, ok2, ok3 in zip(resultados_f1, resultados_f2_literal, resultados_f2_encoded)
    if ok1 and ok2 and ok3
)

print()
print("=" * 78)
print("Resumen")
print("=" * 78)
check(
    f"F1 -- {n_f1_ok}/{len(ENDPOINTS)} endpoints matchean correctamente con "
    f"el separador nuevo '--'",
    n_f1_ok == len(ENDPOINTS),
)
check(
    f"F2 (barra literal) -- {n_f2_literal_ok}/{len(ENDPOINTS)} endpoints "
    f"reproducen el bug del separador viejo '/' (no matchean completo)",
    n_f2_literal_ok == len(ENDPOINTS),
)
check(
    f"F2 (percent-encoded %2F) -- {n_f2_encoded_ok}/{len(ENDPOINTS)} "
    f"endpoints reproducen el bug del separador viejo percent-encoded (no "
    f"matchean completo)",
    n_f2_encoded_ok == len(ENDPOINTS),
)
check(
    f"GLOBAL -- {n_total_ok}/{len(ENDPOINTS)} endpoints pasan F1 y F2 en las "
    f"tres variantes probadas (nuevo matchea completo, viejo no matchea, "
    f"tanto literal como percent-encoded)",
    n_total_ok == len(ENDPOINTS),
)

print()
print("=" * 78)
if FALLOS:
    print(f"RESULTADO: {len(FALLOS)} chequeo(s) fallaron:")
    for f in FALLOS:
        print(f"  - {f}")
    sys.exit(1)
else:
    print("RESULTADO: todos los chequeos pasaron.")
    sys.exit(0)
