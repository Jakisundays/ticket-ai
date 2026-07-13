# Cómo funciona (y dónde se traba) el flujo Invoicy → BAS

Este documento explica en texto, paso a paso, todo el camino que recorre una factura
desde que llega como foto hasta que debería convertirse en una orden de pago dentro de
BAS. No es solo una lista de pasos: en cada uno explico **por qué** existe ese paso,
qué problema resuelve, y si hoy funciona o no. La versión con diagramas está en
[bas-flujo-diagrama.md](bas-flujo-diagrama.md); el detalle técnico exhaustivo (payloads,
errores exactos) está en [bas-orden-de-pago-research.md](bas-orden-de-pago-research.md).

**Resumen de una línea, para no perderse:** logramos que BAS registre la factura de
compra de verdad (esto era el problema grande, y ya está resuelto). Lo único que falta
es un paso más chico y más raro: convertir esa factura registrada en una orden de pago.

---

## Paso 1 — Invoicy lee la factura

Todo arranca con una foto o un PDF de una factura de un proveedor, que llega por
WhatsApp o email. Invoicy usa un modelo de lenguaje para leer esa imagen y sacar los
datos estructurados: quién la emitió, su CUIT, el número de comprobante, la fecha, los
ítems comprados, los impuestos, y el CAE (el código de autorización fiscal que emite
AFIP). Esto ya funcionaba antes de que empezáramos a tocar BAS — es el punto de partida,
y es la razón de ser de todo lo demás: sin estos datos no hay nada que registrar.

**Por qué importa:** es el único paso que no depende de BAS. Si esto falla, no hay
factura que procesar. Todo lo que viene después consume los datos que salen de acá.

---

## Paso 2 — Autenticarse contra BAS

Antes de poder pedirle nada a BAS, hay que probar quiénes somos. Le mandamos usuario y
contraseña a `POST /auth/token`, y BAS nos devuelve un token (una especie de credencial
temporal) que dura alrededor de 100 minutos. A partir de ahí, cada pedido que le hagamos
a BAS tiene que llevar ese token pegado, como una identificación que mostramos en cada
puerta.

**Por qué importa:** es la puerta de entrada. Sin este token, cualquier otro pedido es
rechazado. Es el único paso que es puramente técnico y no tiene ninguna decisión de
negocio detrás — simplemente hay que hacerlo, y ya está resuelto y probado.

---

## Paso 3 — Juntar la información de contexto que BAS necesita

Antes de registrar nada, hay que saberle contestar a BAS varias preguntas que da por
sentado que uno ya sabe. Esto es porque BAS es un sistema que administra **varias
empresas a la vez** (en este caso, dos: PLATINUM HOMES y FIRST PARKING), y dentro de
cada empresa hay sucursales, talonarios, tipos de comprobante, proveedores, y catálogos
de productos. Si no le decimos exactamente en qué "cajón" de esa estructura va cada
cosa, no sabe dónde guardarla.

Los datos que hay que resolver acá son:

- **Qué empresa está pagando.** No es un dato del proveedor, es una decisión nuestra:
  ¿la compra es de PLATINUM HOMES o de FIRST PARKING? En nuestro caso, es PLATINUM
  HOMES, con el código `1`.
- **Qué sucursal.** Dentro de esa empresa, hay una sola sucursal relevante, la `1`.
- **Qué tipo de comprobante es.** Una factura de compra de tipo A (la más común entre
  empresas que están registradas para IVA) tiene el código interno `MA`. Esto no es un
  detalle menor: hay varios tipos (A, B, C, etc.) y cada uno tiene sus propias reglas.
- **Qué talonario usa ese tipo de comprobante.** Un talonario es, básicamente, el
  numerador — la secuencia de números que le va asignando BAS a cada factura que entra.
  Cada tipo de comprobante tiene su propio talonario, y cada talonario tiene su propio
  "prefijo" (un código corto que lo identifica). Para las facturas de compra tipo A, el
  prefijo es `00001`.
- **Que el proveedor exista en el sistema.** Acá tuvimos un problema serio al
  principio: intentábamos registrar una factura de SUPERCOOP, pero SUPERCOOP nunca
  había sido dado de alta como proveedor en BAS. Es como intentar pagarle a alguien que
  el banco no tiene en su lista de contactos. Solucionamos esto dándolo de alta
  nosotros mismos, con su nombre y CUIT reales.
- **Que el producto o servicio de la factura tenga su "cajón contable" asignado.**
  Este fue el obstáculo más grande, y vale la pena explicarlo bien: cada vez que BAS
  registra una compra, tiene que anotar automáticamente en qué cuenta contable cae ese
  gasto (para que la contabilidad de la empresa cuadre). Para eso, cada producto o
  servicio del catálogo necesita tener configurado a qué cuenta contable corresponde
  cuando se lo **compra** (a diferencia de cuando se lo vende, que es una cuenta
  distinta). Al principio elegimos un ítem del catálogo que resultó no tener esa
  configuración para compras — y ahí el sistema frenaba, con toda razón: no sabía dónde
  anotar el gasto. Después nos dimos cuenta de que la gran mayoría de los productos del
  catálogo (tres de cada cuatro) sí tienen esa configuración lista; simplemente
  elegimos mal el primer producto de prueba. Usando uno con la configuración correcta
  (un ítem genérico de "Gastos Generales"), el problema desapareció.

**Por qué importa:** ninguno de estos seis datos es opcional ni se puede "adivinar" —
si falta alguno, BAS rechaza el pedido con un error específico explicando exactamente
qué falta. La buena noticia es que los seis ya están resueltos y confirmados.

---

## Paso 4 — Preguntarle a BAS si esa factura ya existe

Antes de registrar una factura, tiene sentido preguntar primero: ¿ya está cargada?
Esto evita duplicados. Le preguntamos a BAS por el número de comprobante del proveedor
(el que aparece impreso en la factura real, no un número interno de BAS), y la
respuesta, en nuestro caso, **siempre** es "no, no existe" — porque las facturas que
maneja Invoicy vienen de fotos que le mandan por WhatsApp, y BAS nunca las vio antes.

**Por qué importa:** aunque la respuesta sea siempre la misma, el paso es necesario
para no duplicar información si en algún momento alguien de contabilidad ya cargó esa
factura a mano. Es una validación de seguridad, no un trámite inútil.

---

## Paso 5 — Registrar la factura de compra (el gran logro de esta etapa)

Este es el paso donde le decimos a BAS "esta factura existe, anotala". Y acá es donde
más tiempo se fue, porque BAS es estricto: por cada cosa que le faltaba, respondía con
un error puntual explicando qué necesitaba, y hubo que ir completando esa lista una por
una. En total, terminamos necesitando doce datos distintos en el pedido, entre ellos:

- El depósito donde "entra" la mercadería (aunque en muchos casos sea un servicio y no
  haya mercadería física, BAS igual pide indicar uno).
- La caja contable asociada.
- Un "centro de apropiación", que es una forma de decir a qué área interna de la
  empresa se le imputa el gasto (usamos la opción genérica "sin definir", que existe
  justamente para estos casos).
- El número de CAE y su fecha de vencimiento — esto es un dato fiscal real que ya
  veníamos extrayendo de la factura original.
- Indicarle a BAS que la factura se paga "a cuenta corriente" y no "al contado". Esto
  es clave: si no se lo decimos, BAS asume por defecto que se está pagando al contado
  ahí mismo, y en ese caso exige que le mandemos también los datos completos del pago
  — cosa que todavía no tenemos resuelta (ver más abajo). Diciéndole que es "a cuenta
  corriente", separamos limpiamente el registro de la factura del momento de pagarla,
  que es exactamente el flujo que necesitamos: primero registrar, después pagar.
- El total del comprobante que está gravado por impuestos, a nivel general (no solo
  dentro de cada línea del detalle).

Cuando por fin armamos el pedido con estos doce datos, BAS respondió que sí, que la
factura quedó registrada, y nos devolvió un número de comprobante interno real. Después
lo volvimos a consultar por separado, y confirmamos que efectivamente había quedado
guardada y activa.

**Por qué importa:** este era el paso que, hasta hace pocos días, pensábamos que
estaba bloqueado por un problema de configuración que solo BAS podía resolver. Resultó
que el problema era más chico de lo que creíamos (una confusión nuestra sobre qué
producto usar de prueba), y una vez identificado, el registro funciona de punta a
punta. Es la pieza más importante de todo el proceso, y ya está funcionando.

---

## Paso 6 — Crear la orden de pago (acá está el bloqueo actual)

Una vez que la factura está registrada, el paso lógico siguiente es decirle a BAS "che,
pagá esta factura" — eso es exactamente lo que hace una orden de pago. Uno le indica
qué factura se está cancelando, a qué proveedor, y con qué medio de pago (efectivo,
transferencia, etc.).

Acá es donde estamos trabados hoy. Le mandamos a BAS la orden de pago, apuntando
exactamente a la factura que acabamos de registrar con éxito, y BAS nos responde que
**esa factura no existe para poder aplicarle un pago** — a pesar de que, si le
preguntamos directamente "¿existe esta factura?", nos dice que sí, que está activa.

Es un mensaje contradictorio, y probamos muchísimas explicaciones posibles: si el
número de factura estaba mal formateado, si faltaba indicar la moneda, si el código
del medio de pago era el correcto, si había que esperar unos segundos después de crear
la factura por si el sistema tardaba en "verla", si el importe necesitaba un signo
distinto, entre otras. Ninguna de esas variantes resolvió el problema — cada una nos
daba, o el mismo error, o un error distinto que también descartamos por separado.

**Por qué creemos que esto ya no depende de nosotros:** a diferencia del problema del
paso anterior (que era, en el fondo, un dato de configuración que faltaba y que
nosotros mismos pudimos resolver), acá no parece faltar ningún dato — el comprobante
existe, lo confirma el propio sistema, y aun así el proceso interno que "aplica" el
pago no lo encuentra. Esto tiene más pinta de ser un comportamiento inconsistente del
sistema en este ambiente específico (que además confirmamos que es un ambiente de
pruebas, no el sistema real de producción) que de un dato que nos esté faltando
mandar. Por eso el siguiente paso razonable es que alguien con acceso directo al
servidor de BAS revise qué está pasando puntualmente con ese proceso de aplicación de
pagos.

---

## En resumen: qué significa cada semáforo

- **Verde, resuelto de punta a punta:** todo lo que pasa desde que llega la foto hasta
  que la factura queda registrada en BAS. Esto ya es real, se probó, y funciona.
- **Rojo, el único punto pendiente:** el paso final de crear la orden de pago que
  cancela esa factura. No es que falte un dato — es que el sistema, en este caso
  puntual, no se comporta como debería, y hace falta que alguien revise el problema
  desde adentro del servidor.

Una vez que se resuelva ese último punto, el proceso completo — desde que llega la
foto hasta que queda pagada — puede correr solo, sin que nadie tenga que cargar nada
a mano.
