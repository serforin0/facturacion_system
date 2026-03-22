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
