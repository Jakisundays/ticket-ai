# Plan post-deploy: limpieza, pruebas reales y hardening operativo

> Escrito el 2026-07-15 para continuar en una conversación nueva (esta se quedó
> sin contexto). Contiene todo lo que la próxima sesión necesita para ejecutar
> sin tener que re-descubrir el estado del Droplet.

---

## Contexto / acceso

- **Droplet**: `root@137.184.219.162` (DigitalOcean). SSH por clave ya configurado
  (`~/.ssh/id_ed25519`), sin contraseña — `ssh root@137.184.219.162` conecta directo.
- **Specs**: 960MB RAM + swap de 2GB en `/swapfile` (agregado durante el deploy,
  ya persistido en `/etc/fstab`). 1 vCPU. Recurso escaso — cualquier operación
  nueva debe considerar esto (ver notas de cada tarea).
- **Stack nuevo en producción** (`/root/v2/ticket-ai-infra/`, `docker compose`):
  `pocketbase`, `dashboard`, `invoice-api-wa`, `invoice-api-bas`, `invoice-api-core`,
  `wa-bot`, `gateway`. Todos con `restart: unless-stopped`.
  - Dashboard: `http://137.184.219.162:3000` (sin dominio propio todavía — el
    usuario iba a agregar DNS para `panel.ticketia.devstage.com.ar` y
    `pocketbase.ticketia.devstage.com.ar`, pero los registros no resolvían al
    momento de escribir esto; puede que ya estén, revisar con `dig` antes de
    asumir que siguen sin andar).
  - Backend real: `https://backend.ticketia.devstage.com.ar` (nginx + certbot,
    ya andando, apunta a `gateway` en el puerto 8000 del host).
  - PocketBase: `http://137.184.219.162:8090` (admin UI en `/_/`).
  - WhatsApp bot: vinculado y conectado (verificado con un mensaje real).
- **Credenciales relevantes** (generadas durante el deploy, viven en
  `/root/v2/ticket-ai-infra/.env` en el Droplet — no están en este repo):
  - PocketBase superuser: `admin@dinardi.internal` (password en
    `/root/v2/.superuser_pw_temp` en el Droplet).
  - Usuario dashboard de prueba: `equipo@dinardi.internal` (password entregada
    al usuario por chat en su momento — pedirle que la tenga guardada, o
    resetearla si no la encuentra).
  - Cuenta de servicio PocketBase (`invoice-api-bas@invoicy.internal`): password
    en `POCKETBASE_SERVICE_PASSWORD` dentro del `.env`.
- **Memoria relevante para más contexto histórico**: `project_ticket_ai_dashboard_redesign.md`
  y `project_invoicy_bas_orden_pago.md` en el sistema de memoria del usuario.

---

## Tarea A — Limpieza del stack viejo

Los contenedores viejos siguen **detenidos pero no borrados** desde el cutover,
como red de seguridad. Confirmar con el usuario que todo anda bien hace un
tiempo prudencial antes de borrar (no hay urgencia — no ocupan RAM detenidos,
solo disco).

```bash
ssh root@137.184.219.162 "docker ps -a --format 'table {{.Names}}\t{{.Status}}' | grep -E 'ticket-ai-api|ticket-wa|ticket-ai-wa-v2'"
```

Si el usuario confirma que quiere borrarlos:

```bash
ssh root@137.184.219.162 "
docker rm ticket-ai-api ticket-wa ticket-ai-wa-v2
docker image prune -a -f   # ojo: esto borra TODAS las imágenes sin contenedor asociado, no solo las viejas
"
```

También evaluar borrar (con confirmación explícita, son directorios con código +
posibles datos):
- `/root/ticket-ai/` (backend Python viejo, Sheets/Drive)
- `/root/ticket-ai-wa/`, `/root/ticket-ai-wa-v2/` (bots viejos)

**No borrar sin preguntar**: si alguno de estos directorios tiene un `.env` con
credenciales que no se copiaron a `/root/v2/ticket-ai-infra/.env`, perderlas
sería irreversible. Diff rápido antes de borrar:
```bash
ssh root@137.184.219.162 "diff <(grep -oE '^[A-Z_]+=' /root/ticket-ai/.env | sort) <(grep -oE '^[A-Z_]+=' /root/v2/ticket-ai-infra/.env | sort)"
```

---

## Tarea B — Pruebas reales de los flujos que mutan datos

**Nunca se probaron en producción** (a propósito, para no arriesgar datos reales
sin supervisión directa del usuario). Ahora que el stack lleva un tiempo estable,
tiene sentido probarlos — pero seguir la convención de seguridad ya establecida
en este proyecto: **toda prueba real contra BAS debe ser Total=1 peso** (ver
memoria `feedback_bas_pruebas_1_peso`).

### B.1 — Confirmar una factura real
1. Loguearse en el dashboard (`http://137.184.219.162:3000/login`).
2. Ir a Cola de revisión, abrir una factura real pendiente.
3. Confirmar que los datos extraídos son correctos (o corregirlos).
4. Click "Confirmar factura" (⌘+Enter también sirve).
5. Verificar: la factura pasa a `review_status=confirmed`, aparece en el listado
   de Facturas con el estado correcto, y dispara el intento automático de BAS
   (revisar `bas_processing_status` de esa factura vía la sección "Estado BAS").

### B.2 — Reabrir una factura confirmada
1. Desde una factura ya confirmada, click "Reabrir factura" en el `AlertDialog`.
2. Confirmar que vuelve a `needs_review` y aparece de nuevo en la Cola.
3. Ojo: si esa factura ya tenía una Orden de Pago exitosa en BAS, reabrirla NO
   la bloquea (decisión de producto documentada, no es un bug).

### B.3 — Crear una Orden de Pago real (el más sensible)
- **Antes de tocar nada**: confirmar con el usuario qué factura real usar, y que
  el monto/proveedor es apropiado para una prueba real contra BAS. Si hay dudas,
  preguntarle directamente — no asumir.
- Ir a una factura confirmada → panel "Orden de pago" → elegir método de pago →
  "Crear orden de pago".
- Esto pega contra el BAS ERP real (`invoice-api-bas` → `utils/bas.py`). Puede
  fallar con el bloqueador ya documentado en memoria ("no existe para
  aplicarlo") — si pasa, NO es una regresión, es el problema externo de BAS ya
  conocido. Documentar el resultado (éxito u error) en la memoria del proyecto
  para no volver a investigar lo mismo.

### B.4 — WhatsApp bot end-to-end
1. Mandar una foto/PDF de una factura real al número de WhatsApp vinculado.
2. Verificar en los logs (`docker logs ticket-ai-infra-wa-bot-1 -f` y
   `docker logs ticket-ai-infra-invoice-api-wa-1 -f`) que la recibe y la manda a
   procesar.
3. Confirmar que la factura aparece en la Cola de revisión del dashboard.
4. Si falla, revisar también `invoice-api-core` (el core del procesamiento con
   Gemini/Claude) y el archivo `webhooks.json`/logs de Google Sheets si aplica.

---

## Tarea C — Backups de PocketBase

Hoy el volumen `ticket-ai-infra_pocketbase-data` **no tiene ningún backup** y ya
es la fuente de verdad de facturas reales confirmadas. Antes de implementar,
**preguntarle al usuario dónde quiere guardar los backups** (S3, Cloudflare R2,
Google Drive, otro Droplet) — ya hay precedente en otro proyecto del mismo
usuario (`project_felicity_db_backups.md`: backups a R2 vía `rclone`, con el
gotcha de que `rclone` necesita `--s3-no-check-bucket` con un token R2 no-admin).
Probablemente el mismo patrón sirva acá.

Propuesta de implementación (ajustar destino según lo que responda el usuario):

1. Script `/root/v2/backup-pocketbase.sh` en el Droplet:
   ```bash
   #!/bin/bash
   set -euo pipefail
   TS=$(date -u +%Y%m%d-%H%M%S)
   DEST="/root/backups/pocketbase-$TS.tar.gz"
   mkdir -p /root/backups
   docker run --rm -v ticket-ai-infra_pocketbase-data:/data -v /root/backups:/backup \
     alpine tar czf "/backup/pocketbase-$TS.tar.gz" -C /data .
   # subir a destino remoto (ajustar):
   # rclone copy "$DEST" remote:bucket/pocketbase-backups/ --s3-no-check-bucket
   find /root/backups -name 'pocketbase-*.tar.gz' -mtime +14 -delete
   ```
2. Cron diario: `0 4 * * * /root/v2/backup-pocketbase.sh >> /var/log/pb-backup.log 2>&1`
3. **Importante**: probar una restauración real al menos una vez (no solo que el
   backup se genere) — parar `pocketbase`, restaurar el tar en el volumen, volver
   a levantar, confirmar que los datos están. Un backup nunca probado no cuenta
   como backup.
4. Espacio en disco: el Droplet tiene ~10GB libres (verificar de nuevo, puede
   haber cambiado) — con retención de 14 días y una BD chica esto no debería ser
   problema, pero vigilarlo.

---

## Tarea D — Monitoreo / alertas

Nada existe hoy. Dado que el Droplet es muy chico (960MB RAM), evitar stacks
pesados (Prometheus+Grafana no tiene sentido acá). Opciones livianas, de más a
menos simple:

1. **Uptime check externo (recomendado, gratis, cero carga en el Droplet)**:
   UptimeRobot o similar pegándole a `https://backend.ticketia.devstage.com.ar/`
   y (cuando tenga dominio) al dashboard, cada 5 min, con alerta a email/Slack/
   WhatsApp si cae. Esto requiere que el usuario cree la cuenta (no lo puedo
   hacer yo sin sus credenciales).
2. **Watchdog local simple** (cron cada 5 min en el Droplet):
   ```bash
   #!/bin/bash
   cd /root/v2/ticket-ai-infra
   DOWN=$(docker compose ps --status exited --format '{{.Service}}')
   if [ -n "$DOWN" ]; then
     echo "Servicios caídos: $DOWN" # reemplazar por un curl a un webhook (Slack/Discord/ntfy.sh)
     docker compose up -d
   fi
   ```
   `ntfy.sh` es la opción más simple para notificaciones push sin cuenta paga si
   el usuario quiere algo ya mismo sin configurar Slack/Discord.
3. **Alertas de recursos** (RAM/disco): dado lo ajustado que está el Droplet, un
   chequeo simple de `free`/`df` por cron que avise si RAM disponible <100MB o
   disco >90% sería barato de agregar y probablemente valioso, dado que ya se vio
   swap en uso real durante el deploy.
4. Antes de implementar: **preguntar al usuario qué canal de alertas prefiere**
   (email, Slack, WhatsApp al propio bot, ntfy.sh) — no asumir.

---

## Orden sugerido al retomar
1. Tarea B primero (probar que todo funciona de verdad en producción — es lo
   más urgente, valida que el deploy fue exitoso end-to-end).
2. Tarea C (backups) — antes de seguir usando el sistema en producción sin red
   de seguridad de datos.
3. Tarea D (monitoreo) — para enterarse solo si algo se cae.
4. Tarea A (limpieza) al final, una vez que B/C/D dieron confianza de que el
   stack nuevo es estable.
