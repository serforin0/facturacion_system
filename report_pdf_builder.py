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
               COALESCE(c.nombre,'Consumidor final'), COALESCE(TRIM(c.documento),'')
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
        f"<font size=9>{(emp.get('direccion') or '').replace(chr(10), '<br/>')}</font>"
    )
    meta = (
        f"<b>Nº:</b> {numero or '—'}<br/>"
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
