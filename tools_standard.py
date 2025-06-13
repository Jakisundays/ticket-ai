tools = [
    {
        "prompt": (
            "Extrae los detalles estructurados de la factura. "
            "Incluye el domicilio comercial del emisor y del receptor, "
            "priorizando siempre el Domicilio Comercial sobre el Legal o Fiscal. "
            "SE MUY CRÍTICO PARA NÚMERO DE FACTURA - Identifica el número único de esta factura/comprobante"
        ),
        "data": {
            "type": "function",
            "function": {
                "name": "datos_del_emisor_y_receptor",
                "description": "Extrae y gestiona los datos clave de un comprobante fiscal, incluyendo datos del emisor y del receptor con validación de enums.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "comprobante": {
                            "type": "object",
                            "description": "Datos generales del comprobante.",
                            "properties": {
                                "tipo": {
                                    "type": "string",
                                    "enum": [
                                        "Factura",
                                        "Nota de Crédito",
                                        "Nota de Débito",
                                    ],
                                    "description": "Tipo de comprobante fiscal.",
                                },
                                "subtipo": {
                                    "type": "string",
                                    "enum": [
                                        "Para operaciones entre responsables inscriptos",
                                        "Para consumidores finales y exentos",
                                        "Emitida por monotributistas",
                                    ],
                                    "description": """Extraé el valor de "subtipo" según el régimen fiscal del receptor indicado en el comprobante. Buscá palabras clave como: "Responsable Inscripto", "Consumidor Final", "Exento", o "Monotributista".

                                                Asigná uno de estos valores exactos:
                                                - "Para operaciones entre responsables inscriptos"
                                                - "Para consumidores finales y exentos"
                                                - "Emitida por monotributistas"

                                                Reglas:
                                                - Si menciona "Responsable Inscripto" → "Para operaciones entre responsables inscriptos"
                                                - Si menciona "Consumidor Final" o "Exento" → "Para consumidores finales y exentos"
                                                - Si menciona "Monotributista" → "Emitida por monotributistas"
                                                """,
                                },
                                "jurisdiccion_fiscal": {
                                    "type": "string",
                                    "description": "País o jurisdicción fiscal del comprobante, como 'Argentina'.",
                                },
                                "numero": {
                                    "type": "string",
                                    "description": "Número de la factura/comprobante fiscal. Este es el número único identificatorio del comprobante que aparece impreso en el documento. Busca líneas que contengan 'Nro:', 'Número:', 'N°:' seguido del número, o el número que aparece directamente después del tipo y código de comprobante. Formatos típicos: 'XX-XXXXX', 'XX-XXXXX', 'XXXXX-XXXXX'. EXTRAE EXACTAMENTE como está impreso: mantén todos los dígitos, guiones, ceros iniciales y formato original. NO modifiques, no reformatees, no agregues ni quites caracteres - copia literal el número de factura completo",
                                },
                                "fecha_emision": {
                                    "type": "string",
                                    "format": "date",
                                    "description": "Fecha de emisión del comprobante (YYYY-MM-DD).",
                                },
                                "moneda": {
                                    "type": "string",
                                    "description": "Moneda en la que se emite el comprobante, por ejemplo 'ARS', 'USD'.",
                                },
                            },
                            "required": [
                                "tipo",
                                "jurisdiccion_fiscal",
                                "numero",
                                "fecha_emision",
                                "moneda",
                            ],
                            "additionalProperties": False,
                        },
                        "emisor": {
                            "type": "object",
                            "description": "Datos del emisor del comprobante.",
                            "properties": {
                                "nombre": {
                                    "type": "string",
                                    "description": "Nombre o razón social del emisor.",
                                },
                                "id_fiscal": {
                                    "type": "string",
                                    "description": "CUIT o CUIL del emisor.",
                                },
                                "direccion": {
                                    "type": "string",
                                    "description": "Extrae el domicilio comercial del emisor. Prioriza siempre el Domicilio Comercial sobre el Legal o Fiscal.",
                                },
                                "condicion_iva": {
                                    "type": "string",
                                    "enum": [
                                        "Responsable Inscripto",
                                        "Monotributo",
                                        "Exento",
                                        "Desconocido",
                                    ],
                                    "description": "Condición frente al IVA del emisor.",
                                },
                            },
                            "required": ["nombre", "id_fiscal"],
                            "additionalProperties": False,
                        },
                        "receptor": {
                            "type": "object",
                            "description": "Datos del receptor del comprobante.",
                            "properties": {
                                "nombre": {
                                    "type": "string",
                                    "description": "Nombre o razón social del receptor.",
                                },
                                "id_fiscal": {
                                    "type": "string",
                                    "description": "CUIT o CUIL del receptor.",
                                },
                                "direccion": {
                                    "type": "string",
                                    "description": "Extrae el domicilio comercial del receptor. Prioriza siempre el Domicilio Comercial sobre el Legal o Fiscal.",
                                },
                                "condicion_iva": {
                                    "type": "string",
                                    "enum": [
                                        "Responsable Inscripto",
                                        "Consumidor Final",
                                        "Desconocido",
                                    ],
                                    "description": "Condición frente al IVA del receptor.",
                                },
                            },
                            "required": ["nombre"],
                            "additionalProperties": False,
                        },
                        "otros": {
                            "type": "object",
                            "description": "Otros datos relevantes del comprobante.",
                            "properties": {
                                "forma_pago": {
                                    "type": "string",
                                    "description": "Método de pago utilizado. Ejemplos: 'Efectivo', 'Transferencia', 'Tarjeta de crédito'.",
                                    "nullable": True,
                                },
                                "CAE": {
                                    "type": "string",
                                    "description": "Código de Autorización Electrónico emitido por AFIP.",
                                    "nullable": True,
                                },
                                "vencimiento_CAE": {
                                    "type": "string",
                                    "format": "date",
                                    "description": "Fecha de vencimiento del CAE en formato YYYY-MM-DD.",
                                    "nullable": True,
                                },
                            },
                            "additionalProperties": False,
                        },
                    },
                    "required": ["comprobante", "emisor", "receptor"],
                    "additionalProperties": False,
                },
            },
        },
    },
    {
        "prompt": "Extrae los detalles estructurados de la factura",
        "data": {
            "type": "function",
            "function": {
                "name": "detalle_de_items_facturados",
                "description": "Extrae y valida los detalles de ítems facturados junto con totales e impuestos.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "detalles": {
                            "type": "array",
                            "description": "Lista de ítems facturados en la factura.",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "descripcion": {
                                        "type": "string",
                                        "description": "Descripción del producto o servicio facturado.",
                                    },
                                    "cantidad": {
                                        "type": "number",
                                        "description": "Cantidad facturada.",
                                    },
                                    "precio_unitario": {
                                        "type": "number",
                                        "description": "Precio por unidad antes de impuestos.",
                                    },
                                    "precio_total": {
                                        "type": "number",
                                        "description": "Total de la línea (cantidad x precio unitario).",
                                    },
                                },
                                "required": [
                                    "descripcion",
                                    "cantidad",
                                    "precio_unitario",
                                    "precio_total",
                                ],
                                "additionalProperties": False,
                            },
                        },
                        "subtotal": {
                            "type": "number",
                            "description": "Suma de importes sin incluir impuestos.",
                        },
                        "total": {
                            "type": "number",
                            "description": "Importe total del comprobante, calculado como subtotal + impuestos - retenciones. Ejemplo: 4130.00",
                        },
                        "observaciones": {
                            "type": "string",
                            "description": "Notas o comentarios adicionales sobre el comprobante.",
                        },
                    },
                    "required": ["detalles", "subtotal", "total"],
                    "additionalProperties": False,
                },
            },
        },
    },
    {
        "prompt": (
            "Extrae y clasifica todos los cargos que aparezcan en la factura por encima del subtotal. "
            "Incluye impuestos como IVA o impuestos a las ventas, y detalla cualquier cargo adicional o recargo con su descripción y monto. "
            "Las retenciones deben corresponder exclusivamente a conceptos fiscales o tributarios (como Ganancias, IVA, IIBB). "
            "No incluyas descuentos comerciales, promociones ni bonificaciones bajo la categoría de retenciones. "
            "Estos deben clasificarse por separado o ser ignorados si no corresponden a un cargo sobre el subtotal."
        ),
        "data": {
            "type": "function",
            "function": {
                "name": "impuestos_y_retenciones_de_la_factura",
                "description": "Extrae, valida y estructura la información de impuestos y retenciones de una factura.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "impuestos": {
                            "type": "array",
                            "description": "Lista de impuestos aplicados en la factura.",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "tipo": {
                                        "type": "string",
                                        "description": "Tipo de impuesto aplicado (e.g., IVA, Percepción, Impuesto Municipal).",
                                    },
                                    "descripcion": {
                                        "type": "string",
                                        "description": "Descripción detallada del impuesto.",
                                        "nullable": True,
                                    },
                                    "base_imponible": {
                                        "type": "number",
                                        "description": "Monto base sobre el que se calcula el impuesto.",
                                    },
                                    "alicuota": {
                                        "type": "number",
                                        "description": "Porcentaje de alícuota aplicada.",
                                        "nullable": True,
                                    },
                                    "importe": {
                                        "type": "number",
                                        "description": "Importe del impuesto calculado.",
                                    },
                                },
                                "required": [
                                    "tipo",
                                    "base_imponible",
                                    "importe",
                                ],
                                "additionalProperties": False,
                            },
                        },
                        "retenciones": {
                            "type": "array",
                            "description": "Lista de retenciones aplicadas. No incluir descuentos ni promociones; estos deben clasificarse por separado.",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "tipo": {
                                        "type": "string",
                                        "description": "Tipo de retención (e.g., Ganancias, IVA, IIBB). No usar para descuentos o promociones.",
                                    },
                                    "description": {
                                        "type": "string",
                                        "description": "Detalle adicional de la retención. No incluir información de descuentos ni promociones.",
                                        "nullable": True,
                                    },
                                    "base_imponible": {
                                        "type": "number",
                                        "description": "Monto base sobre el que se aplica la retención.",
                                    },
                                },
                                "required": ["tipo", "base_imponible"],
                                "additionalProperties": False,
                            },
                        },
                    },
                    "required": ["impuestos", "retenciones"],
                    "additionalProperties": False,
                },
            },
        },
    },
]
