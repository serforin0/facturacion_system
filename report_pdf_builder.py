"""
Generación de PDFs para reportes (ReportLab → bytes).
"""
from datetime import datetime
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from database import Database


def build_inventory_valuation_pdf(
    db: Database,
    *,
    include_zero_stock: bool = False,
    metodo: str = "Promedio ponderado",
) -> bytes:
    """
    PDF «Valor del inventario» alineado a columnas tipo MONICA.
    """
    emp = db.get_empresa_info()
    rows, total_costo, total_venta, total_qty, n_items = db.get_inventory_valuation_report(
        include_zero_stock=include_zero_stock
    )

    bio = BytesIO()
    doc = SimpleDocTemplate(
        bio,
        pagesize=landscape(letter),
        leftMargin=28,
        rightMargin=28,
        topMargin=36,
        bottomMargin=36,
    )
    styles = getSampleStyleSheet()
    elems = []

    fecha = datetime.now().strftime("%d/%m/%Y")
    hdr_l = f"<b>{emp['nombre']}</b><br/>{emp['direccion'].replace(chr(10), '<br/>')}"
    elems.append(
        Table(
            [
                [
                    Paragraph(hdr_l, styles["Normal"]),
                    Paragraph(
                        f"Fecha: {fecha}<br/>Método: {metodo}",
                        styles["Normal"],
                    ),
                ]
            ],
            colWidths=[380, 320],
        )
    )
    elems.append(Spacer(1, 8))
    elems.append(
        Paragraph(
            f"<para align=center><b><u>Valor del Inventario al {fecha}</u></b></para>",
            styles["Title"],
        )
    )
    elems.append(
        Paragraph(
            "<para align=center><font size=9>"
            "Búsqueda por código de productos, desde 000 hasta ZZZ — "
            f"(En RD$ — {metodo})</font></para>",
            styles["Normal"],
        )
    )
    elems.append(Spacer(1, 14))

    data = [
        [
            "Código",
            "Descripción",
            "Costo $",
            "Precio $",
            "En almacén",
            "Valor invent.",
            "Valor venta",
        ]
    ]
    for cod, nom, c_u, p_u, stk, v_c, v_v in rows:
        data.append(
            [
                str(cod)[:18],
                str(nom)[:42],
                f"{float(c_u):,.2f}",
                f"{float(p_u):,.2f}",
                f"{float(stk):,.2f}",
                f"{float(v_c):,.2f}",
                f"{float(v_v):,.2f}",
            ]
        )

    data.append(
        [
            "TOTAL ÍTEMS",
            str(n_items),
            "",
            "",
            f"{float(total_qty):,.2f}",
            f"{float(total_costo):,.2f}",
            f"{float(total_venta):,.2f}",
        ]
    )

    tbl = Table(data, repeatRows=1, colWidths=[72, 200, 62, 62, 72, 80, 80])
    tbl.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#333333")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 9),
                ("ALIGN", (2, 0), (-1, -1), "RIGHT"),
                ("ALIGN", (0, 0), (1, -1), "LEFT"),
                ("FONTNAME", (0, 1), (-1, -2), "Helvetica"),
                ("FONTSIZE", (0, 1), (-1, -2), 8),
                ("GRID", (0, 0), (-1, -2), 0.5, colors.grey),
                ("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.white, colors.HexColor("#f5f5f5")]),
                ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#dddddd")),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                ("GRID", (0, -1), (-1, -1), 0.5, colors.black),
            ]
        )
    )
    elems.append(tbl)
    doc.build(elems)
    return bio.getvalue()


def build_facturas_historial_pdf(
    db: Database,
    rows: list[tuple],
    *,
    subtitulo_filtros: str = "",
) -> bytes:
    """
    PDF listado de facturas (historial) para Reportería.
    rows: tuplas como devuelve fetch_facturas_historial_reporte.
    """
    emp = db.get_empresa_info()
    bio = BytesIO()
    doc = SimpleDocTemplate(
        bio,
        pagesize=landscape(letter),
        leftMargin=24,
        rightMargin=24,
        topMargin=32,
        bottomMargin=32,
    )
    styles = getSampleStyleSheet()
    elems = []

    fecha = datetime.now().strftime("%d/%m/%Y %H:%M")
    hdr_l = f"<b>{emp['nombre']}</b><br/>{emp['direccion'].replace(chr(10), '<br/>')}"
    elems.append(
        Table(
            [
                [
                    Paragraph(hdr_l, styles["Normal"]),
                    Paragraph(f"Generado: {fecha}", styles["Normal"]),
                ]
            ],
            colWidths=[420, 280],
        )
    )
    elems.append(Spacer(1, 6))
    elems.append(
        Paragraph(
            "<para align=center><b><u>Historial de facturas</u></b></para>",
            styles["Title"],
        )
    )
    if subtitulo_filtros:
        elems.append(
            Paragraph(
                f"<para align=center><font size=9>{subtitulo_filtros}</font></para>",
                styles["Normal"],
            )
        )
    elems.append(Spacer(1, 10))

    data = [
        [
            "Número",
            "Fecha",
            "Cliente",
            "Doc.",
            "Usuario",
            "Tipo",
            "Pago",
            "Subtotal",
            "Desc.",
            "ITBIS",
            "Total",
            "Estado",
        ]
    ]
    sum_total = 0.0
    n_emit = 0
    for row in rows:
        (
            _fid,
            numero,
            fecha_f,
            cliente,
            doc_c,
            usuario,
            tipo,
            pago,
            sub,
            desc,
            itb,
            total,
            estado,
        ) = row
        st = (estado or "").lower()
        if st == "emitida":
            sum_total += float(total or 0)
            n_emit += 1
        fv = str(fecha_f or "")[:19]
        data.append(
            [
                str(numero)[:14],
                fv,
                str(cliente)[:28],
                str(doc_c)[:14],
                str(usuario or "")[:12],
                str(tipo or "")[:14],
                str(pago or "")[:14],
                f"{float(sub or 0):,.2f}",
                f"{float(desc or 0):,.2f}",
                f"{float(itb or 0):,.2f}",
                f"{float(total or 0):,.2f}",
                str(estado or "")[:10],
            ]
        )

    data.append(
        [
            f"REGISTROS: {len(rows)}",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "Σ emitidas:",
            f"{sum_total:,.2f}",
            f"({n_emit} doc.)",
        ]
    )

    cw = [54, 72, 118, 52, 54, 58, 58, 58, 44, 44, 58, 48]
    tbl = Table(data, repeatRows=1, colWidths=cw)
    tbl.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#333333")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 7),
                ("ALIGN", (7, 0), (-2, -1), "RIGHT"),
                ("ALIGN", (-1, 0), (-1, -1), "CENTER"),
                ("FONTNAME", (0, 1), (-1, -2), "Helvetica"),
                ("FONTSIZE", (0, 1), (-1, -2), 6),
                ("GRID", (0, 0), (-1, -2), 0.35, colors.grey),
                ("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.white, colors.HexColor("#f8f8f8")]),
                ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#e8e8e8")),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, -1), (-1, -1), 7),
                ("SPAN", (0, -1), (6, -1)),
            ]
        )
    )
    elems.append(tbl)
    doc.build(elems)
    return bio.getvalue()


def build_factura_comprobante_pdf(db: Database, factura_id: int) -> bytes | None:
    """
    Comprobante A4 claro (marca propia): encabezado empresa, cliente, líneas y totales.
    """
    conn = db.get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT f.numero, f.fecha, IFNULL(f.estado,''), f.tipo_comprobante,
               f.subtotal, f.descuento_total, f.impuesto_total, f.total,
               IFNULL(f.observaciones,''), IFNULL(f.referencia_entrega,''),
               IFNULL(f.fecha_vencimiento,''), IFNULL(f.moneda,'DOP'),
               COALESCE(c.nombre,'Consumidor final'), COALESCE(TRIM(c.documento),''),
               IFNULL(f.ncf,'')
        FROM facturas f
        LEFT JOIN clientes c ON c.id = f.cliente_id
        WHERE f.id = ?
        """,
        (int(factura_id),),
    )
    row = cur.fetchone()
    if not row:
        conn.close()
        return None
    cur.execute(
        """
        SELECT descripcion, cantidad, precio_unitario, descuento_item, impuesto_item, total_linea
        FROM factura_detalle
        WHERE factura_id = ?
        ORDER BY id
        """,
        (int(factura_id),),
    )
    dets = cur.fetchall()
    conn.close()

    (
        numero,
        fecha,
        estado,
        tipo_comp,
        subtotal,
        desc_tot,
        imp_tot,
        total,
        obs,
        ref_ent,
        fv,
        moneda,
        cli_nom,
        cli_doc,
        ncf,
    ) = row

    emp = db.get_empresa_info()
    bio = BytesIO()
    doc = SimpleDocTemplate(
        bio,
        pagesize=letter,
        leftMargin=40,
        rightMargin=40,
        topMargin=42,
        bottomMargin=42,
    )
    styles = getSampleStyleSheet()
    elems = []

    tipo_txt = (tipo_comp or "").replace("_", " ").title() or "—"
    st = (estado or "").lower()
    etiqueta = (
        "PRESUPUESTO (no fiscal)"
        if st == "cotizacion"
        else ("ANULADO" if st == "anulada" else "DOCUMENTO DE VENTA")
    )

    hdr = (
        f"<b><font size=14>{emp.get('nombre') or 'Empresa'}</font></b><br/>"
        f"<font size=9>{(emp.get('direccion') or '').replace(chr(10), '<br/>')}</font><br/>"
        f"<font size=9>RNC {emp.get('rnc') or '—'}</font>"
    )
    meta = (
        f"<b>Nº:</b> {numero or '—'}<br/>"
        f"<b>NCF:</b> {ncf or '—'}<br/>"
        f"<b>Fecha:</b> {str(fecha or '')[:19]}<br/>"
        f"<b>Estado:</b> {etiqueta}<br/>"
        f"<b>Comprobante:</b> {tipo_txt}"
    )
    elems.append(
        Table(
            [
                [
                    Paragraph(hdr, styles["Normal"]),
                    Paragraph(meta, styles["Normal"]),
                ]
            ],
            colWidths=[320, 200],
        )
    )
    elems.append(Spacer(1, 14))
    elems.append(
        Paragraph(
            f"<b>Cliente:</b> {cli_nom} &nbsp; <b>Doc.:</b> {cli_doc or '—'}",
            styles["Normal"],
        )
    )
    if fv:
        elems.append(
            Paragraph(f"<b>Vence:</b> {fv}", styles["Normal"]),
        )
    if ref_ent:
        elems.append(Paragraph(f"<b>Ref. entrega:</b> {ref_ent}", styles["Normal"]))
    elems.append(Spacer(1, 10))

    data = [
        ["Descripción", "Cant.", "P. unit.", "Desc.", "ITBIS", "Total"],
    ]
    for des, cant, pu, di, ii, tl in dets:
        data.append(
            [
                str(des or "")[:46],
                f"{float(cant or 0):,.2f}",
                f"{float(pu or 0):,.2f}",
                f"{float(di or 0):,.2f}",
                f"{float(ii or 0):,.2f}",
                f"{float(tl or 0):,.2f}",
            ]
        )

    tbl = Table(
        data,
        repeatRows=1,
        colWidths=[200, 52, 62, 52, 52, 62],
    )
    tbl.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e3a5f")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 9),
                ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
                ("ALIGN", (0, 0), (0, -1), "LEFT"),
                ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 1), (-1, -1), 8),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
            ]
        )
    )
    elems.append(tbl)
    elems.append(Spacer(1, 12))

    m = moneda or "DOP"
    totales_txt = (
        f"<b>Subtotal ({m}):</b> {float(subtotal or 0):,.2f} &nbsp; "
        f"<b>Descuento:</b> {float(desc_tot or 0):,.2f} &nbsp; "
        f"<b>ITBIS:</b> {float(imp_tot or 0):,.2f} &nbsp; "
        f"<b>Total:</b> {float(total or 0):,.2f}"
    )
    elems.append(Paragraph(totales_txt, styles["Normal"]))
    if obs and str(obs).strip():
        elems.append(Spacer(1, 8))
        elems.append(
            Paragraph(f"<b>Notas:</b> {str(obs).strip()[:800]}", styles["Normal"]),
        )
    elems.append(Spacer(1, 16))
    elems.append(
        Paragraph(
            "<font size=8 color='#64748b'>Documento generado desde el sistema de facturación. "
            "No sustituye requisitos fiscales oficiales si aplica.</font>",
            styles["Normal"],
        )
    )
    doc.build(elems)
    return bio.getvalue()


def _rd(valor) -> str:
    return f"RD$ {float(valor or 0):,.2f}"


def _xml(texto) -> str:
    from xml.sax.saxutils import escape

    return escape(str(texto or ""))


def _tabla_reporte(data: list[list], anchos: list[float]) -> Table:
    tabla = Table(data, repeatRows=1, colWidths=anchos)
    tabla.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e3a5f")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 9),
                ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 1), (-1, -1), 8),
                ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return tabla


def build_cuadre_caja_pdf(datos: dict, empresa: dict) -> bytes:
    """Cuadre de caja en carta, para archivar, comparar e imprimir."""
    bio = BytesIO()
    doc = SimpleDocTemplate(
        bio,
        pagesize=letter,
        leftMargin=40,
        rightMargin=40,
        topMargin=36,
        bottomMargin=36,
    )
    styles = getSampleStyleSheet()
    normal = styles["Normal"]
    elems = []
    cerrado = (datos.get("estado") or "") == "cerrado"
    diferencia = float(datos.get("diferencia") or 0)
    if not cerrado:
        resultado = "Pendiente de conteo"
        contado = "Pendiente"
        diferencia_txt = "-"
    elif abs(diferencia) < 0.05:
        resultado = "Cuadrado"
        contado = _rd(datos.get("contado"))
        diferencia_txt = _rd(0)
    elif diferencia > 0:
        resultado = f"Sobrante {_rd(diferencia)}"
        contado = _rd(datos.get("contado"))
        diferencia_txt = _rd(diferencia)
    else:
        resultado = f"Faltante {_rd(abs(diferencia))}"
        contado = _rd(datos.get("contado"))
        diferencia_txt = _rd(diferencia)

    nombre = _xml(empresa.get("nombre") or "Empresa")
    direccion = _xml(empresa.get("direccion") or "").replace("\n", "<br/>")
    rnc = _xml(empresa.get("rnc") or "—")
    titulo = "CUADRE DE CAJA" if cerrado else "PRE-CUADRE DE CAJA"
    hdr = f"<b><font size=14>{nombre}</font></b><br/><font size=9>{direccion}</font><br/><font size=9>RNC {rnc}</font>"
    meta = (
        f"<b>{titulo}</b><br/>"
        f"Cuadre nº {datos.get('id')}<br/>"
        f"Turno: {_xml(datos.get('nombre') or 'Caja')}<br/>"
        f"<b>{_xml(resultado)}</b>"
    )
    elems.append(
        Table(
            [[Paragraph(hdr, normal), Paragraph(meta, normal)]],
            colWidths=[340, 192],
        )
    )
    elems.append(Spacer(1, 10))
    elems.append(
        Paragraph(
            f"Abrió {_xml(datos.get('usuario_apertura') or '—')} el {_xml(str(datos.get('apertura') or '')[:16])}<br/>"
            f"Cerró {_xml(datos.get('usuario_cierre') or '—')} el {_xml(str(datos.get('cierre') or 'turno abierto')[:16])}<br/>"
            f"Facturas emitidas: {int(datos.get('n_facturas') or 0)} · "
            f"anuladas: {int(datos.get('n_anuladas') or 0)} · "
            f"venta total {_rd(datos.get('ventas_total'))}",
            normal,
        )
    )
    elems.append(Spacer(1, 12))
    elems.append(Paragraph("<b>Efectivo</b>", normal))
    elems.append(Spacer(1, 4))
    elems.append(
        _tabla_reporte(
            [
                ["Concepto", "Monto"],
                ["Fondo inicial", _rd(datos.get("fondo"))],
                ["Ventas en efectivo", _rd(datos.get("ventas_efectivo"))],
                ["Ingresos de efectivo", _rd(datos.get("ingresos"))],
                ["Retiros de efectivo", _rd(datos.get("retiros"))],
                ["Efectivo esperado", _rd(datos.get("esperado"))],
                ["Efectivo contado", contado],
                ["Diferencia", diferencia_txt],
            ],
            [340, 192],
        )
    )

    pagos = datos.get("pagos") or []
    elems.append(Spacer(1, 12))
    elems.append(Paragraph("<b>Ventas por forma de pago</b>", normal))
    elems.append(Spacer(1, 4))
    if pagos:
        filas_pago = [["Forma de pago", "Monto"]]
        filas_pago.extend(
            [
                [_xml((p.get("tipo") or "otro").replace("_", " ")).capitalize(), _rd(p.get("monto"))]
                for p in pagos
            ]
        )
        filas_pago.append(["Total cobrado", _rd(sum(float(p.get("monto") or 0) for p in pagos))])
        elems.append(_tabla_reporte(filas_pago, [340, 192]))
    else:
        elems.append(Paragraph("Este turno no tiene cobros registrados.", normal))

    movimientos = datos.get("movimientos") or []
    elems.append(Spacer(1, 12))
    elems.append(Paragraph("<b>Movimientos de efectivo</b>", normal))
    elems.append(Spacer(1, 4))
    if movimientos:
        filas_mov = [["Fecha", "Tipo", "Monto", "Motivo", "Usuario"]]
        for m in movimientos:
            filas_mov.append(
                [
                    str(m.get("fecha") or "")[:16],
                    _xml(m.get("tipo") or ""),
                    _rd(m.get("monto")),
                    _xml(m.get("motivo") or "")[:42],
                    _xml(m.get("usuario") or "—")[:18],
                ]
            )
        elems.append(_tabla_reporte(filas_mov, [100, 70, 90, 172, 100]))
    else:
        elems.append(Paragraph("Este turno no tiene ingresos ni retiros.", normal))

    facturas = datos.get("facturas") or []
    elems.append(Spacer(1, 12))
    elems.append(Paragraph("<b>Facturas del turno</b>", normal))
    elems.append(Spacer(1, 4))
    if facturas:
        filas_fac = [["Número", "Fecha", "Cliente", "Estado", "Total"]]
        for f in facturas:
            filas_fac.append(
                [
                    _xml(f.get("numero") or "")[:18],
                    str(f.get("fecha") or "")[:16],
                    _xml(f.get("cliente") or "")[:28],
                    _xml(f.get("estado") or ""),
                    _rd(f.get("total")),
                ]
            )
        elems.append(_tabla_reporte(filas_fac, [90, 100, 170, 80, 92]))
        if len(facturas) >= 300:
            elems.append(Spacer(1, 4))
            elems.append(Paragraph("Se listan las primeras 300 facturas del turno.", normal))
    else:
        elems.append(Paragraph("Este turno no tiene facturas.", normal))

    obs = str(datos.get("observaciones") or "").strip()
    elems.append(Spacer(1, 12))
    elems.append(Paragraph(f"<b>Observación:</b> {_xml(obs) if obs else 'Sin observación.'}", normal))
    elems.append(Spacer(1, 22))
    elems.append(
        Table(
            [
                [
                    Paragraph("Cajero<br/>______________________________", normal),
                    Paragraph("Revisado por<br/>______________________________", normal),
                ]
            ],
            colWidths=[266, 266],
        )
    )
    elems.append(Spacer(1, 14))
    elems.append(
        Paragraph(
            f"<font size=8 color='#64748b'>Generado el {datetime.now().strftime('%d/%m/%Y %H:%M')} "
            "para archivar y comparar cuadres. Documento interno de caja.</font>",
            normal,
        )
    )
    doc.build(elems)
    return bio.getvalue()
