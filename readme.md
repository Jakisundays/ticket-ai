# 📄 Ticket AI: Extractor de información de facturas

## ¿Qué es esto? 🤔

Ticket AI es una app **brutal** que usa IA para extraer toda la info importante de tus facturas. Ya no más copiar datos manualmente ni perder tiempo con OCR de baja calidad. Esta herramienta lo hace todo por ti en segundos.

## Características principales ✨

- **Procesa múltiples formatos**: Soporta PDF, PNG, JPEG, WEBP y GIF (no animados).
- **Extracción inteligente**: Obtiene automáticamente datos del emisor, receptor, ítems facturados e impuestos.
- **Interfaz clean**: Diseñada con Streamlit para una experiencia de usuario súper fluida.
- **Visualización pro**: Los datos extraídos se muestran en tablas y cards organizadas.
- **API de Claude**: Usa el modelo Claude 3.7 Sonnet de Anthropic para una extracción de datos on point.

## Cómo funciona la magia 🪄

La app utiliza un pipeline completo:

1. **Carga de archivos**: Sube tu factura desde la interfaz.
2. **Procesamiento**: Se extrae el contenido usando la API de Claude Vision.
3. **Estructuración**: La info se organiza en forma JSON estructurado.
4. **Visualización**: Se presentan los datos en una interfaz user-friendly.

## Datos que extrae 📋

- **Datos del comprobante**: Tipo, número, fecha de emisión, moneda.
- **Datos del emisor**: Nombre, ID fiscal, dirección, condición de IVA.
- **Datos del receptor**: Nombre, ID fiscal, dirección, condición de IVA.
- **Ítems facturados**: Descripción, cantidad, precio unitario, precio total.
- **Impuestos y retenciones**: Tipo, base imponible, alícuota, importe.

## Tecnologías que usa 💻

- **Backend**: Python con procesamiento asíncrono (asyncio) para mayor velocidad.
- **Frontend**: Streamlit para una interfaz responsive y aesthetic.
- **IA**: Claude 3.7 Sonnet para la extracción de datos mediante visión artificial.
- **Procesamiento de imágenes**: PIL y pdf2image para convertir y procesar documentos.
- **Manipulación de datos**: Pandas para formatear y mostrar tablas de datos.

## Estructura del código 🏗️

El código se divide en varias funciones principales:

- `mostrar_datos()`: Visualiza los datos extraídos en la interfaz.
- `mostrar_datos_comprobante()`: Muestra la información general de la factura.
- `mostrar_items_facturados()`: Visualiza los ítems y totales.
- `mostrar_impuestos()`: Presenta los impuestos y retenciones.
- `call_claude_vision()` y `call_claude_pdf()`: Comunicación con la API de Claude.
- `main()`: Función principal que orquesta toda la aplicación.

## Beneficios reales 🚀

- **Ahorra MUCHÍSIMO tiempo**: Lo que antes te tomaba minutos, ahora son segundos.
- **Reduce errores**: La extracción automatizada minimiza errores de transcripción.
- **Organiza tu info**: Convierte documentos en datos estructurados fácilmente exportables.
- **Análisis contable más rápido**: Facilita el procesamiento de múltiples facturas.

## Requisitos técnicos ⚙️

- Python 3.7+
- Una API key de Anthropic (se configura como variable de entorno)
- Las dependencias indicadas en el código (Streamlit, PIL, pdf2image, pandas, etc.)

## Cómo empezar 🚀

1. Clona el repositorio
2. Instala las dependencias con `pip install -r requirements.txt`
3. Configura tu API key: `export ANTHROPIC_API_KEY=tu_api_key`
4. Ejecuta la app: `streamlit run app_v5.py`
5. Sube una factura y ¡voilà! 

---

No más copy-paste, no más errores de transcripción, no más sufrir con facturas. Ticket AI hace el trabajo pesado por ti. Tu yo del futuro te lo agradecerá.

¿Preguntas? ¿Sugerencias? ¡Abre un issue en el repo! 👾