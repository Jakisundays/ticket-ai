#!/usr/bin/env python3
"""
Aplicación Streamlit para detección de facturas usando OCR

Requisitos de instalación de Tesseract:
- macOS: brew install tesseract tesseract-lang
- Ubuntu/Debian: sudo apt-get install tesseract-ocr tesseract-ocr-spa tesseract-ocr-eng
- Windows: Descargar desde https://github.com/UB-Mannheim/tesseract/wiki

Asegúrate de que Tesseract esté en tu PATH del sistema.
"""

import streamlit as st
import pytesseract
from PIL import Image
import re

# Configuración de la página
st.set_page_config(
    page_title="Detector de Facturas OCR",
    page_icon="📄",
    layout="wide"
)

# Título principal
st.title("📄 Detector de Facturas con OCR")
st.markdown("Sube una imagen para detectar si es una factura usando reconocimiento óptico de caracteres.")

# Palabras clave para detectar facturas
PALABRAS_CLAVE_FACTURAS = [
    # Español
    "factura", "ruc", "subtotal", "total", "iva", "impuesto", "nit", "cuit",
    "fecha", "vencimiento", "cliente", "proveedor", "cantidad", "precio",
    # Inglés
    "invoice", "tax id", "amount", "subtotal", "total", "tax", "vat",
    "date", "due date", "customer", "vendor", "quantity", "price",
    "bill", "receipt"
]

def extraer_texto_imagen(imagen):
    """
    Extrae texto de una imagen usando pytesseract con soporte para español e inglés.
    
    Args:
        imagen: Objeto PIL Image
    
    Returns:
        str: Texto extraído de la imagen
    """
    try:
        # Configurar pytesseract para español e inglés
        texto = pytesseract.image_to_string(imagen, lang='spa+eng')
        return texto
    except Exception as e:
        st.error(f"Error al extraer texto: {str(e)}")
        st.error("Asegúrate de que Tesseract esté instalado correctamente.")
        return ""

def detectar_factura(texto):
    """
    Detecta si el texto contiene palabras clave de facturas.
    
    Args:
        texto (str): Texto extraído de la imagen
    
    Returns:
        tuple: (es_factura: bool, coincidencias: list, total_coincidencias: int)
    """
    # Convertir texto a minúsculas para búsqueda insensible a mayúsculas
    texto_lower = texto.lower()
    
    # Buscar coincidencias
    coincidencias = []
    for palabra in PALABRAS_CLAVE_FACTURAS:
        if palabra.lower() in texto_lower:
            coincidencias.append(palabra)
    
    # Eliminar duplicados manteniendo el orden
    coincidencias = list(dict.fromkeys(coincidencias))
    
    # Determinar si es factura (al menos 2 coincidencias)
    es_factura = len(coincidencias) >= 2
    
    return es_factura, coincidencias, len(coincidencias)

# Interfaz principal
st.markdown("### 📤 Subir Imagen")

# Widget para subir archivo
archivo_subido = st.file_uploader(
    "Selecciona una imagen",
    type=["jpg", "jpeg", "png", "bmp", "tiff"],
    help="Formatos soportados: JPG, JPEG, PNG, BMP, TIFF"
)

if archivo_subido is not None:
    try:
        # Cargar y mostrar la imagen
        imagen = Image.open(archivo_subido)
        
        # Crear dos columnas
        col1, col2 = st.columns([1, 1])
        
        with col1:
            st.markdown("### 🖼️ Imagen Subida")
            st.image(imagen, caption=f"Archivo: {archivo_subido.name}", use_column_width=True)
            
            # Información de la imagen
            st.markdown("**Información de la imagen:**")
            st.write(f"- **Formato:** {imagen.format}")
            st.write(f"- **Tamaño:** {imagen.size[0]} x {imagen.size[1]} píxeles")
            st.write(f"- **Modo:** {imagen.mode}")
        
        with col2:
            st.markdown("### 🔍 Análisis OCR")
            
            # Botón para procesar
            if st.button("🚀 Procesar Imagen", type="primary"):
                with st.spinner("Extrayendo texto de la imagen..."):
                    # Extraer texto
                    texto_extraido = extraer_texto_imagen(imagen)
                    
                    if texto_extraido.strip():
                        # Detectar si es factura
                        es_factura, coincidencias, total_coincidencias = detectar_factura(texto_extraido)
                        
                        # Mostrar resultado
                        if es_factura:
                            st.success("✅ Es una factura")
                        else:
                            st.error("❌ No es una factura")
                        
                        # Mostrar estadísticas
                        st.markdown("**Estadísticas de detección:**")
                        st.write(f"- **Palabras clave encontradas:** {total_coincidencias}")
                        st.write(f"- **Umbral mínimo:** 2 palabras clave")
                        
                        if coincidencias:
                            st.markdown("**Palabras clave detectadas:**")
                            for palabra in coincidencias:
                                st.write(f"• {palabra.title()}")
                        
                        # Mostrar texto extraído en un expander
                        with st.expander("📝 Ver texto extraído completo"):
                            st.text_area(
                                "Texto extraído:",
                                texto_extraido,
                                height=200,
                                disabled=True
                            )
                    else:
                        st.warning("⚠️ No se pudo extraer texto de la imagen.")
                        st.info("Verifica que la imagen contenga texto legible y que Tesseract esté configurado correctamente.")
    
    except Exception as e:
        st.error(f"Error al procesar la imagen: {str(e)}")

else:
    # Mostrar información cuando no hay archivo
    st.info("👆 Sube una imagen para comenzar el análisis.")
    
    # Información adicional
    st.markdown("### ℹ️ Información")
    
    col1, col2 = st.columns([1, 1])
    
    with col1:
        st.markdown("**¿Cómo funciona?**")
        st.write("1. Sube una imagen de una posible factura")
        st.write("2. La aplicación extrae el texto usando OCR")
        st.write("3. Busca palabras clave específicas de facturas")
        st.write("4. Determina si es una factura basándose en las coincidencias")
    
    with col2:
        st.markdown("**Palabras clave buscadas:**")
        st.write("**Español:** Factura, RUC, Subtotal, Total, IVA, NIT, CUIT")
        st.write("**Inglés:** Invoice, Tax ID, Amount, VAT, Bill, Receipt")
        st.write("**Criterio:** Mínimo 2 palabras clave para confirmar")

# Footer
st.markdown("---")
st.markdown(
    "<div style='text-align: center; color: gray;'>""Detector de Facturas OCR - Powered by Tesseract & Streamlit""</div>",
    unsafe_allow_html=True
)