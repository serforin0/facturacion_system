"""
Mostrador web. Usa la misma base SQLite que el programa de escritorio.
"""
from __future__ import annotations

import json
import os
import secrets
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from database import Database

TASA_ITBIS = 0.18
SECRET_PATH = Path(os.environ.get("FACTURACION_SECRET", ".session_secret"))
app = FastAPI(title="Facturación")
_sessions: dict[str, str] = {}
_STATIC = Path(__file__).resolve().parent / "web_static"
_FONDOS = (
    ("agua", "agua.jpg"),
    ("hielo", "hielo.jpg"),
    ("cerveza", "cerveza.jpg"),
    ("papa", "papas.jpg"),
    ("refresco", "refresco.jpg"),
    ("cola", "refresco.jpg"),
    ("ron", "ron.jpg"),
    ("vino", "vino.jpg"),
)


def _imagen_de(nombre: str) -> str:
    texto = (nombre or "").lower()
    for clave, archivo in _FONDOS:
        if clave in texto:
            return f"/estaticos/productos/{archivo}"
    return "/estaticos/productos/cerveza.jpg"


def db() -> Database:
    return Database()


def _pdf_etiquetas(productos: list[dict]) -> bytes:
    from io import BytesIO

    from reportlab.graphics.barcode import code128
    from reportlab.pdfgen import canvas as pdf_canvas

    bio = BytesIO()
    lienzo = pdf_canvas.Canvas(bio, pagesize=(612, 792))
    x0, y = 36, 740
    for i, producto in enumerate(productos):
        col = i % 2
        if i and col == 0:
            y -= 120
        if y < 80:
            lienzo.showPage()
            y = 740
        x = x0 + col * 280
        nombre = (producto.get("nombre") or "")[:32]
        codigo = (producto.get("codigo") or "")[:40]
        precio = float(producto.get("precio") or 0)
        lienzo.setFont("Helvetica-Bold", 10)
        lienzo.drawString(x, y, nombre)
        lienzo.setFont("Helvetica", 9)
        lienzo.drawString(x, y - 14, f"RD$ {precio:.2f}")
        if codigo:
            try:
                barra = code128.Code128(codigo, barHeight=28, barWidth=0.8)
                barra.drawOn(lienzo, x, y - 58)
            except Exception:
                lienzo.drawString(x, y - 32, codigo)
            lienzo.drawString(x, y - 70, codigo)
    lienzo.save()
    return bio.getvalue()


def _enviar_pdf(destino: str, asunto: str, cuerpo: str, pdf: bytes, archivo: str) -> None:
    import smtplib
    from email.message import EmailMessage

    base = db()
    host = (base.get_config("smtp_host", "") or "").strip()
    if not host:
        raise HTTPException(status_code=400, detail="Configura el servidor de correo en Apariencia")
    try:
        port = int(base.get_config("smtp_port", "587") or "587")
    except ValueError:
        port = 587
    usuario = (base.get_config("smtp_user", "") or "").strip()
    clave = base.get_config("smtp_password", "") or ""
    remitente = (base.get_config("smtp_from", "") or usuario).strip()
    if not remitente:
        raise HTTPException(status_code=400, detail="Indica el correo remitente en Apariencia")
    mensaje = EmailMessage()
    mensaje["Subject"] = asunto
    mensaje["From"] = remitente
    mensaje["To"] = destino
    mensaje.set_content(cuerpo)
    mensaje.add_attachment(pdf, maintype="application", subtype="pdf", filename=archivo)
    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=20) as smtp:
                if usuario:
                    smtp.login(usuario, clave)
                smtp.send_message(mensaje)
        else:
            with smtplib.SMTP(host, port, timeout=20) as smtp:
                smtp.ehlo()
                smtp.starttls()
                smtp.ehlo()
                if usuario:
                    smtp.login(usuario, clave)
                smtp.send_message(mensaje)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"No se pudo enviar el correo: {exc}") from exc


def _secret() -> str:
    if SECRET_PATH.exists():
        return SECRET_PATH.read_text().strip()
    value = secrets.token_hex(32)
    SECRET_PATH.write_text(value)
    return value


_secret()


def _user(request: Request) -> str:
    token = request.cookies.get("sesion") or ""
    user = _sessions.get(token)
    if not user:
        raise HTTPException(status_code=401, detail="Inicia sesión")
    return user


def _permisos(username: str) -> list[str]:
    conn = db().get_connection()
    cur = conn.cursor()
    cur.execute("SELECT role FROM users WHERE username=?", (username,))
    row = cur.fetchone()
    role = row[0] if row else ""
    if role == "admin":
        conn.close()
        return list(MODULOS)
    cur.execute("SELECT modulo FROM usuario_permisos WHERE username=? ORDER BY modulo", (username,))
    mods = [r[0] for r in cur.fetchall() if r[0] in MODULOS]
    conn.close()
    return mods or ["mostrador"]


def _exigir(request: Request, modulo: str) -> str:
    user = _user(request)
    if modulo not in _permisos(user):
        raise HTTPException(status_code=403, detail="No tienes permiso para este módulo")
    return user


def _exigir_alguno(request: Request, modulos: tuple[str, ...]) -> str:
    user = _user(request)
    tiene = _permisos(user)
    if not any(modulo in tiene for modulo in modulos):
        raise HTTPException(status_code=403, detail="No tienes permiso para este módulo")
    return user


def _armar_lineas(cur, items: list[ItemIn]):
    subtotal = 0.0
    impuesto = 0.0
    det = []
    for item in items:
        if item.combo or item.cantidad <= 0:
            continue
        cur.execute(SQL_PRODUCTOS + " WHERE id=?", (item.id,))
        row = cur.fetchone()
        if not row:
            continue
        precio = round(float(item.precio), 2) if item.precio is not None else _precio(row, item.nivel)
        bruto = round(precio * item.cantidad, 2)
        itbis = round(bruto * TASA_ITBIS, 2) if int(row[8] or 1) else 0.0
        subtotal += bruto
        impuesto += itbis
        det.append((row[0], row[1], item.cantidad, precio, itbis, round(bruto + itbis, 2)))
    return det, subtotal, impuesto


def _descuento_promo(cur, producto_id: int, cantidad: float, precio: float, bruto: float) -> float:
    cur.execute(
        """
        SELECT p.tipo_descuento, IFNULL(p.valor, 0)
        FROM promociones p
        JOIN promociones_detalle d ON d.promocion_id = p.id
        WHERE IFNULL(p.activo, 1) = 1 AND d.producto_id = ?
        ORDER BY p.id DESC
        LIMIT 1
        """,
        (producto_id,),
    )
    row = cur.fetchone()
    if not row:
        return 0.0
    tipo, valor = row[0], float(row[1] or 0)
    if tipo == "porcentaje":
        return round(bruto * valor / 100.0, 2)
    if tipo == "fijo":
        return round(min(bruto, valor), 2)
    if tipo == "2x1":
        return round(int(cantidad // 2) * precio, 2)
    if tipo == "3x2":
        return round(int(cantidad // 3) * precio, 2)
    return 0.0


SQL_PRODUCTOS = """
    SELECT id, nombre, precio, precio_base, precio_minimo,
           stock, codigo_barras, imagen_path,
           IFNULL(aplica_itbis, 1),
           IFNULL(facturar_sin_stock, 1),
           IFNULL(descripcion, ''),
           IFNULL(descripcion_en_factura, 0),
           IFNULL(precio_2, 0), IFNULL(precio_3, 0), IFNULL(precio_4, 0),
           IFNULL(facturar_nivel_precio, 1),
           categoria_id,
           IFNULL(stock_minimo, 0)
    FROM productos
"""

MODULOS = [
    "mostrador", "inventario", "kardex", "historial", "cotizaciones", "indicadores",
    "clientes", "usuarios", "apariencia", "caja", "reportes", "compras",
    "promociones", "devoluciones", "transferir", "ncf", "seguimiento", "metodos",
]


def _precio(row, nivel: int) -> float:
    precios = [row[2], row[12], row[13], row[14]]
    try:
        p = float(precios[max(1, min(4, nivel)) - 1] or 0)
    except (TypeError, ValueError, IndexError):
        p = 0.0
    if p <= 0:
        p = float(row[2] or 0)
    return p


class LoginIn(BaseModel):
    username: str
    password: str


class AbrirCajaIn(BaseModel):
    nombre: str = "Caja 1"
    fondo: float = 0


class ItemIn(BaseModel):
    id: int
    cantidad: float
    nivel: int = 1
    combo: bool = False
    precio: float | None = None


class PagoIn(BaseModel):
    codigo: str
    monto: float


class VentaIn(BaseModel):
    items: list[ItemIn]
    efectivo: float = 0
    tarjeta: float = 0
    transferencia: float = 0
    pagos: list[PagoIn] = []
    lista: str = "Público"
    comprobante: str = "consumidor_final"
    cliente_id: int | None = None
    descuento: float = 0


class MovimientoIn(BaseModel):
    tipo: str
    monto: float
    motivo: str


class CerrarCajaIn(BaseModel):
    contado: float
    observaciones: str = ""


class LineaCompra(BaseModel):
    producto_id: int
    cantidad: float
    costo: float


class CompraIn(BaseModel):
    proveedor: str
    producto_id: int = 0
    cantidad: float = 0
    costo: float = 0
    bodega: str = "Principal"
    lineas: list[LineaCompra] = []


class PromoIn(BaseModel):
    nombre: str
    tipo: str
    valor: float
    producto_id: int


class DevolucionIn(BaseModel):
    factura: str
    motivo: str
    lineas: list[dict]
    reembolso_efectivo: bool = False


class MetodoIn(BaseModel):
    codigo: str
    nombre: str
    afecta_caja: bool = False
    activo: bool = True


class TransferIn(BaseModel):
    producto_id: int
    origen: str
    destino: str
    cantidad: float


@app.get("/", response_class=HTMLResponse)
def inicio():
    return HTML


@app.post("/api/login")
def login(body: LoginIn):
    role = db().validate_user(body.username.strip(), body.password)
    if not role:
        raise HTTPException(status_code=401, detail="Usuario o contraseña incorrectos")
    token = secrets.token_urlsafe(32)
    _sessions[token] = body.username.strip()
    resp = JSONResponse({"ok": True, "usuario": body.username.strip(), "rol": role})
    resp.set_cookie("sesion", token, httponly=True, samesite="lax")
    return resp


@app.post("/api/logout")
def logout(request: Request):
    _sessions.pop(request.cookies.get("sesion") or "", None)
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("sesion")
    return resp


@app.get("/api/estado")
def estado(request: Request):
    usuario = _user(request)
    base = db()
    caja = base.fetch_caja_abierta_row()
    conn = base.get_connection()
    cur = conn.cursor()
    cur.execute("SELECT role FROM users WHERE username=?", (usuario,))
    role = (cur.fetchone() or ["user"])[0]
    conn.close()
    return {
        "usuario": usuario,
        "rol": role,
        "permisos": _permisos(usuario),
        "empresa": base.get_empresa_info(),
        "caja": None
        if caja is None
        else {
            "id": caja[0],
            "nombre": caja[1],
            "apertura": caja[2],
            "fondo": caja[6],
        },
    }


@app.get("/api/catalogo")
def catalogo(request: Request, q: str = "", categoria: int | None = None):
    _user(request)
    conn = db().get_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, nombre FROM categorias ORDER BY nombre COLLATE NOCASE")
    categorias = [{"id": i, "nombre": n} for i, n in cur.fetchall()]
    sql = SQL_PRODUCTOS + " WHERE IFNULL(activo,1)=1"
    params: list = []
    if categoria:
        sql += " AND categoria_id=?"
        params.append(categoria)
    if q.strip():
        sql += " AND (nombre LIKE ? OR IFNULL(codigo_barras,'') LIKE ?)"
        params.extend([f"%{q.strip()}%", f"%{q.strip()}%"])
    sql += " ORDER BY nombre COLLATE NOCASE LIMIT 80"
    cur.execute(sql, params)
    productos = []
    for row in cur.fetchall():
        productos.append(
            {
                "id": row[0],
                "nombre": row[1],
                "precio": float(row[2] or 0),
                "precio_2": float(row[12] or 0),
                "precio_3": float(row[13] or 0),
                "precio_4": float(row[14] or 0),
                "stock": row[5],
                "codigo": row[6] or "",
                "itbis": bool(int(row[8] or 1)),
                "imagen": _imagen_de(row[1]),
                "minimo": float(row[17] or 0),
                "combo": False,
            }
        )
    combos = [
        {"id": i, "nombre": n, "precio": float(p), "combo": True, "stock": None, "imagen": _imagen_de(n), "itbis": True, "codigo": "", "minimo": 0}
        for i, n, p in db().listar_combos_venta()
    ]
    conn.close()
    return {"categorias": categorias, "productos": productos, "combos": combos}


@app.post("/api/caja/abrir")
def abrir_caja(request: Request, body: AbrirCajaIn):
    usuario = _exigir(request, "caja")
    base = db()
    if base.fetch_caja_abierta_row() is not None:
        raise HTTPException(status_code=400, detail="Ya hay un turno abierto")
    conn = base.get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO cierres_caja
            (nombre_caja, fecha_apertura, usuario_apertura, monto_inicial,
             total_ventas, total_efectivo_sistema, total_tarjeta_sistema,
             total_otros_sistema, efectivo_contado, diferencia_efectivo,
             observaciones, estado)
        VALUES (?, ?, ?, ?, 0, 0, 0, 0, 0, 0, NULL, 'abierto')
        """,
        (body.nombre.strip() or "Caja 1", datetime.now().strftime("%Y-%m-%d %H:%M:%S"), usuario, float(body.fondo or 0)),
    )
    conn.commit()
    conn.close()
    return {"ok": True}


@app.post("/api/vender")
def vender(request: Request, body: VentaIn):
    usuario = _exigir(request, "mostrador")
    base = db()
    caja = base.fetch_caja_abierta_row()
    if caja is None:
        raise HTTPException(status_code=400, detail="Abre el turno de caja antes de cobrar")
    if not body.items:
        raise HTTPException(status_code=400, detail="El ticket está vacío")

    lineas = []
    subtotal = 0.0
    impuesto = 0.0
    cliente_nombre = "Consumidor final"
    conn = base.get_connection()
    cur = conn.cursor()
    try:
        for item in body.items:
            if item.cantidad <= 0:
                raise HTTPException(status_code=400, detail="Cantidad inválida")
            if item.combo:
                cur.execute("SELECT nombre, IFNULL(precio_combo,0) FROM combos WHERE id=? AND IFNULL(activo,1)=1", (item.id,))
                combo = cur.fetchone()
                if not combo:
                    raise HTTPException(status_code=400, detail="Combo no disponible")
                comps = base.componentes_combo(item.id)
                for pid, cant_c, nombre_c, stock_c, sin_stock in comps:
                    necesidad = float(cant_c) * item.cantidad
                    if int(sin_stock or 1) == 0 and necesidad > float(stock_c or 0):
                        raise HTTPException(status_code=400, detail=f"Sin stock de {nombre_c} para el combo")
                precio = float(combo[1] or 0)
                bruto = round(precio * item.cantidad, 2)
                desc = 0.0
                neto = bruto
                itbis = round(neto * TASA_ITBIS, 2)
                lineas.append((None, combo[0], item.cantidad, precio, neto, itbis, desc, comps))
                subtotal += neto
                impuesto += itbis
                continue
            cur.execute(SQL_PRODUCTOS + " WHERE id=?", (item.id,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=400, detail=f"Producto {item.id} no existe")
            precio = _precio(row, item.nivel)
            bruto = round(precio * item.cantidad, 2)
            desc = _descuento_promo(cur, row[0], item.cantidad, precio, bruto)
            neto = round(max(0.0, bruto - desc), 2)
            itbis = round(neto * TASA_ITBIS, 2) if int(row[8] or 1) else 0.0
            if int(row[9] or 1) == 0 and item.cantidad > float(row[5] or 0):
                raise HTTPException(status_code=400, detail=f"Sin stock de {row[1]}")
            lineas.append((row, row[1], item.cantidad, precio, neto, itbis, desc, None))
            subtotal += neto
            impuesto += itbis
        descuento = round(max(0.0, float(body.descuento or 0)), 2)
        if descuento > subtotal:
            descuento = subtotal
        if subtotal > 0 and descuento > 0:
            factor = (subtotal - descuento) / subtotal
            impuesto = round(impuesto * factor, 2)
            subtotal = round(subtotal - descuento, 2)
        total = round(subtotal + impuesto, 2)
        afectan = base.codigos_afectan_caja()
        activos = {codigo for codigo, *_rest in base.listar_metodos_pago(True)}
        if body.pagos:
            entradas = [
                (p.codigo.strip().lower(), round(float(p.monto or 0), 2))
                for p in body.pagos
                if round(float(p.monto or 0), 2) > 0
            ]
        else:
            entradas = [
                (codigo, round(monto, 2))
                for codigo, monto in (
                    ("efectivo", body.efectivo),
                    ("tarjeta", body.tarjeta),
                    ("transferencia", body.transferencia),
                )
                if round(float(monto or 0), 2) > 0
            ]
        for codigo, monto in entradas:
            if monto < 0:
                raise HTTPException(status_code=400, detail="Montos inválidos")
            if codigo not in activos:
                raise HTTPException(status_code=400, detail=f"El método {codigo} no está activo")
        fuera = round(sum(monto for codigo, monto in entradas if codigo not in afectan), 2)
        entregado_caja = round(sum(monto for codigo, monto in entradas if codigo in afectan), 2)
        if fuera > total + 0.02:
            raise HTTPException(status_code=400, detail="Los pagos que no entran a caja no pueden superar el total")
        if fuera + entregado_caja < total - 0.01:
            raise HTTPException(status_code=400, detail="El pago no cubre el total")
        ef_guardar = round(max(0.0, total - fuera), 2)
        cambio = round(max(0.0, entregado_caja - ef_guardar), 2)

        tipo = body.comprobante if body.comprobante in (
            "consumidor_final", "credito_fiscal", "gubernamental", "especial"
        ) else "consumidor_final"
        if tipo == "credito_fiscal":
            if not body.cliente_id:
                raise HTTPException(status_code=400, detail="El crédito fiscal necesita un cliente")
            cur.execute(
                "SELECT TRIM(IFNULL(documento,'')) FROM clientes WHERE id=?",
                (body.cliente_id,),
            )
            doc = cur.fetchone()
            if not doc or not doc[0]:
                raise HTTPException(status_code=400, detail="El cliente necesita RNC o cédula para el crédito fiscal")
        if body.cliente_id:
            cur.execute("SELECT nombre FROM clientes WHERE id=?", (body.cliente_id,))
            crow = cur.fetchone()
            if crow:
                cliente_nombre = crow[0]
        ncf = base.tomar_siguiente_ncf(tipo, conn=conn)
        base_num = "F-" + datetime.now().strftime("%Y%m%d%H%M%S")
        numero = base_num
        n = 2
        while True:
            cur.execute("SELECT 1 FROM facturas WHERE numero=?", (numero,))
            if cur.fetchone() is None:
                break
            numero = f"{base_num}-{n}"
            n += 1
        cur.execute(
            """
            INSERT INTO facturas
                (numero, tipo_comprobante, cliente_id, subtotal, descuento_total,
                 impuesto_total, total, estado, usuario, caja, condicion_pago_id,
                 fecha_vencimiento, moneda, tasa_cambio, cierre_id, ncf, lista_precio)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'emitida', ?, NULL, NULL, NULL, 'DOP', 1, ?, ?, ?)
            """,
            (numero, tipo, body.cliente_id, round(subtotal, 2), descuento, round(impuesto, 2), total, usuario, caja[0], ncf, body.lista),
        )
        factura_id = cur.lastrowid
        for row, descripcion, cant, precio, neto, itbis, desc, comps in lineas:
            cur.execute(
                """
                INSERT INTO factura_detalle
                    (factura_id, producto_id, descripcion, cantidad, precio_unitario,
                     descuento_item, impuesto_item, total_linea)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (factura_id, None if row is None else row[0], descripcion, cant, precio, desc, itbis, round(neto + itbis, 2)),
            )
            objetivos = [(row[0], cant)] if row is not None else [
                (int(pid), float(cant_c) * cant) for pid, cant_c, *_rest in comps
            ]
            for pid, qty in objetivos:
                cur.execute("UPDATE productos SET stock = stock - ? WHERE id=?", (qty, pid))
                cur.execute(
                    "SELECT IFNULL(NULLIF(TRIM(bodega_codigo),''),'') FROM productos WHERE id=?",
                    (pid,),
                )
                bod = (cur.fetchone()[0] or "").strip() or None
                base.ajustar_stock_bodega(pid, -float(qty), bod, conn=conn)
                base.insert_movimiento_kardex(
                    pid, "venta", -float(qty), ajustar_stock=False, referencia=numero,
                    factura_id=factura_id, usuario=usuario, tipo_codigo="FA",
                    bodega_codigo=bod, precio_unitario=precio,
                    descripcion_mov=f"Venta web: {numero}", conn=conn,
                )
        guardados = [(codigo, monto) for codigo, monto in entradas if codigo not in afectan]
        if ef_guardar > 0:
            codigo_caja = next((codigo for codigo, _monto in entradas if codigo in afectan), "efectivo")
            guardados.append((codigo_caja, ef_guardar))
        for tipo_pago, monto in guardados:
            if monto > 0:
                cur.execute(
                    "INSERT INTO pagos_factura (factura_id, tipo_pago, monto) VALUES (?,?,?)",
                    (factura_id, tipo_pago, round(monto, 2)),
                )
        conn.commit()
    except HTTPException:
        conn.rollback()
        conn.close()
        raise
    except Exception as exc:
        conn.rollback()
        conn.close()
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    conn.close()
    return {
        "ok": True,
        "numero": numero,
        "ncf": ncf,
        "subtotal": round(subtotal, 2),
        "itbis": round(impuesto, 2),
        "cliente": cliente_nombre,
        "total": total,
        "cambio": cambio,
        "lineas": [
            {"nombre": d, "cantidad": c, "total": round(n + i, 2)}
            for _row, d, c, _p, n, i, _desc, _comps in lineas
        ],
    }


def _efectivo_ventas(base: Database, fecha: str, cierre_id: int) -> float:
    conn = base.get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT p.tipo_pago, SUM(p.monto)
        FROM pagos_factura p
        JOIN facturas f ON f.id = p.factura_id
        WHERE f.estado = 'emitida'
          AND (f.cierre_id = ? OR (f.cierre_id IS NULL AND datetime(f.fecha) >= datetime(?)))
        GROUP BY p.tipo_pago
        """,
        (cierre_id, fecha),
    )
    rows = cur.fetchall()
    conn.close()
    afecta = {c.lower() for c in base.codigos_afectan_caja()}
    return round(sum(float(m or 0) for t, m in rows if (t or "").lower() in afecta), 2)


@app.post("/api/caja/movimiento")
def movimiento(request: Request, body: MovimientoIn):
    usuario = _exigir(request, "caja")
    base = db()
    caja = base.fetch_caja_abierta_row()
    if caja is None:
        raise HTTPException(status_code=400, detail="No hay turno abierto")
    tipo = (body.tipo or "").lower()
    if tipo not in ("ingreso", "retiro"):
        raise HTTPException(status_code=400, detail="El tipo debe ser ingreso o retiro")
    if body.monto <= 0 or len((body.motivo or "").strip()) < 3:
        raise HTTPException(status_code=400, detail="Indica monto y un motivo")
    if tipo == "retiro":
        ingresos, retiros = base.totales_movimientos_efectivo(caja[0])
        esperado = float(caja[6] or 0) + _efectivo_ventas(base, caja[2], caja[0]) + ingresos - retiros
        if body.monto > esperado + 0.01:
            raise HTTPException(status_code=400, detail=f"El retiro supera el efectivo esperado ({esperado:.2f})")
    base.registrar_movimiento_efectivo(caja[0], tipo, body.monto, body.motivo, usuario)
    return {"ok": True}


@app.post("/api/caja/cerrar")
def cerrar_caja(request: Request, body: CerrarCajaIn):
    usuario = _exigir(request, "caja")
    base = db()
    caja = base.fetch_caja_abierta_row()
    if caja is None:
        raise HTTPException(status_code=400, detail="No hay turno abierto")
    ingresos, retiros = base.totales_movimientos_efectivo(caja[0])
    esperado = float(caja[6] or 0) + _efectivo_ventas(base, caja[2], caja[0]) + ingresos - retiros
    diff = round(float(body.contado) - esperado, 2)
    if abs(diff) > 0.05 and len((body.observaciones or "").strip()) < 3:
        raise HTTPException(status_code=400, detail="Si no cuadra, escribe una observación")
    conn = base.get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE cierres_caja
        SET fecha_cierre=?, usuario_cierre=?, efectivo_contado=?, diferencia_efectivo=?,
            observaciones=?, estado='cerrado'
        WHERE id=? AND fecha_cierre IS NULL
        """,
        (
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            usuario,
            round(float(body.contado), 2),
            diff,
            (body.observaciones or "").strip() or None,
            caja[0],
        ),
    )
    conn.commit()
    conn.close()
    return {"ok": True, "esperado": round(esperado, 2), "diferencia": diff}


def _filtro_fecha(desde: str | None, hasta: str | None, dias: int, columna: str) -> tuple[str, list]:
    if (desde or "").strip() and (hasta or "").strip():
        return f"date({columna}) >= date(?) AND date({columna}) <= date(?)", [desde.strip(), hasta.strip()]
    return f"datetime({columna}) >= datetime('now', ?)", [f"-{int(dias)} days"]


@app.get("/api/reportes")
def reportes(request: Request, dias: int = 30, desde: str = "", hasta: str = ""):
    _exigir(request, "reportes")
    conn = db().get_connection()
    cur = conn.cursor()
    donde, params = _filtro_fecha(desde, hasta, dias, "f.fecha")
    cur.execute(
        f"""
        SELECT d.descripcion, SUM(d.cantidad), SUM(d.total_linea)
        FROM factura_detalle d
        JOIN facturas f ON f.id = d.factura_id
        WHERE f.estado = 'emitida' AND {donde}
        GROUP BY d.descripcion
        ORDER BY SUM(d.cantidad) DESC
        LIMIT 20
        """,
        params,
    )
    vendidos = [{"nombre": n, "cantidad": c, "total": t} for n, c, t in cur.fetchall()]
    donde_f, params_f = _filtro_fecha(desde, hasta, dias, "fecha")
    cur.execute(
        f"""
        SELECT IFNULL(usuario, '—'), COUNT(*), SUM(total)
        FROM facturas
        WHERE estado = 'emitida' AND {donde_f}
        GROUP BY usuario
        ORDER BY SUM(total) DESC
        """,
        params_f,
    )
    cajeros = [{"usuario": u, "facturas": n, "total": t} for u, n, t in cur.fetchall()]
    cur.execute(
        f"""
        SELECT p.tipo_pago, COUNT(*), SUM(p.monto)
        FROM pagos_factura p
        JOIN facturas f ON f.id = p.factura_id
        WHERE f.estado = 'emitida' AND {donde}
        GROUP BY p.tipo_pago
        ORDER BY SUM(p.monto) DESC
        """,
        params,
    )
    pagos = [{"metodo": m, "pagos": n, "total": t} for m, n, t in cur.fetchall()]
    conn.close()
    return {"vendidos": vendidos, "cajeros": cajeros, "pagos": pagos}


@app.post("/api/compras")
def compras(request: Request, body: CompraIn):
    usuario = _exigir(request, "compras")
    base = db()
    nombre = body.proveedor.strip()
    if len(nombre) < 2:
        raise HTTPException(status_code=400, detail="Indica el proveedor")
    conn = base.get_connection()
    cur = conn.cursor()
    cur.execute("SELECT id FROM proveedores WHERE nombre=? AND IFNULL(activo,1)=1", (nombre,))
    row = cur.fetchone()
    conn.close()
    prov_id = row[0] if row else base.crear_proveedor(nombre)
    lineas = list(body.lineas) or [
        LineaCompra(producto_id=body.producto_id, cantidad=body.cantidad, costo=body.costo)
    ]
    if not lineas or any(ln.cantidad <= 0 for ln in lineas):
        raise HTTPException(status_code=400, detail="Cada línea necesita una cantidad mayor que cero")
    bodega = (body.bodega or "Principal").strip() or "Principal"
    conn = base.get_connection()
    cur = conn.cursor()
    try:
        total = round(sum(ln.cantidad * ln.costo for ln in lineas), 2)
        cur.execute(
            "INSERT INTO compras (proveedor_id, total, usuario, nota) VALUES (?,?,?,?)",
            (prov_id, total, usuario, nombre),
        )
        compra_id = cur.lastrowid
        cur.execute("SELECT nombre FROM proveedores WHERE id=?", (prov_id,))
        prov = cur.fetchone()
        for ln in lineas:
            cur.execute(
                """
                INSERT INTO compra_detalle (compra_id, producto_id, cantidad, costo, bodega)
                VALUES (?,?,?,?,?)
                """,
                (compra_id, ln.producto_id, ln.cantidad, ln.costo, bodega),
            )
            cur.execute(
                "UPDATE productos SET stock = IFNULL(stock,0) + ? WHERE id=?",
                (ln.cantidad, ln.producto_id),
            )
            base.ajustar_stock_bodega(ln.producto_id, ln.cantidad, bodega, conn=conn)
            base.insert_movimiento_kardex(
                ln.producto_id, "compra", ln.cantidad, ajustar_stock=False,
                usuario=usuario, bodega_codigo=bodega, precio_unitario=ln.costo,
                tipo_codigo="CO", entidad_nombre=(prov[0] if prov else None),
                referencia=f"COMPRA-{compra_id}",
                descripcion_mov=f"Compra {compra_id}", conn=conn,
            )
        conn.commit()
    except Exception as exc:
        conn.rollback()
        conn.close()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    conn.close()
    return {"ok": True, "compra": compra_id, "lineas": len(lineas)}


@app.get("/api/promociones")
def promociones(request: Request):
    _exigir(request, "promociones")
    return {
        "items": [
            {"id": i, "nombre": n, "tipo": t, "valor": v, "activa": bool(a)}
            for i, n, t, v, a, _c in db().listar_promociones()
        ]
    }


@app.post("/api/promociones")
def crear_promo(request: Request, body: PromoIn):
    _exigir(request, "promociones")
    if body.tipo not in ("porcentaje", "fijo", "2x1", "3x2"):
        raise HTTPException(status_code=400, detail="Tipo de promoción inválido")
    pid = db().guardar_promocion(body.nombre, body.tipo, body.valor, producto_id=body.producto_id)
    return {"ok": True, "id": pid}


@app.get("/api/devolucion")
def ver_devolucion(request: Request, q: str):
    _exigir(request, "devoluciones")
    conn = db().get_connection()
    cur = conn.cursor()
    if q.strip().isdigit():
        cur.execute(
            "SELECT id, numero, total FROM facturas WHERE id=? AND estado='emitida'",
            (int(q),),
        )
    else:
        cur.execute(
            "SELECT id, numero, total FROM facturas WHERE numero LIKE ? AND estado='emitida' ORDER BY id DESC LIMIT 1",
            (f"%{q.strip()}%",),
        )
    fac = cur.fetchone()
    if not fac:
        conn.close()
        raise HTTPException(status_code=404, detail="Factura no encontrada")
    cur.execute(
        "SELECT id, descripcion, cantidad FROM factura_detalle WHERE factura_id=?",
        (fac[0],),
    )
    lineas = [{"id": i, "nombre": n, "cantidad": c} for i, n, c in cur.fetchall()]
    conn.close()
    return {"id": fac[0], "numero": fac[1], "total": fac[2], "lineas": lineas}


@app.post("/api/devolucion")
def devolver(request: Request, body: DevolucionIn):
    usuario = _exigir(request, "devoluciones")
    data = ver_devolucion(request, body.factura)
    lineas = []
    for ln in body.lineas:
        qty = float(ln.get("cantidad") or 0)
        if qty > 0:
            lineas.append((int(ln["id"]), qty))
    ok, msg = db().registrar_devolucion_nota_credito(data["id"], lineas, body.motivo, usuario)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    if body.reembolso_efectivo:
        base = db()
        conn = base.get_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT monto_total FROM notas_credito WHERE factura_original_id=? ORDER BY id DESC LIMIT 1",
            (data["id"],),
        )
        nota = cur.fetchone()
        conn.close()
        caja = base.fetch_caja_abierta_row()
        monto = float(nota[0] or 0) if nota else 0
        if caja and monto > 0:
            base.registrar_movimiento_efectivo(
                caja[0], "retiro", monto, f"Devolución {data['numero']}", usuario
            )
            msg += " El reembolso salió del efectivo del turno."
        else:
            msg += " La caja está cerrada, así que el reembolso quedó solo como nota de crédito."
    return {"ok": True, "mensaje": msg}


@app.post("/api/transferir")
def transferir(request: Request, body: TransferIn):
    usuario = _exigir(request, "transferir")
    try:
        db().transferir_entre_bodegas(
            body.producto_id, body.origen, body.destino, body.cantidad, usuario
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


@app.get("/api/espera")
def listar_espera(request: Request):
    _exigir(request, "mostrador")
    return {
        "items": [
            {"id": i, "etiqueta": e, "usuario": u, "total": t, "fecha": f}
            for i, e, u, t, f, _c in db().listar_ventas_espera()
        ]
    }


@app.post("/api/espera")
def crear_espera(request: Request, body: EsperaIn):
    usuario = _exigir(request, "mostrador")
    if not body.items:
        raise HTTPException(status_code=400, detail="El ticket está vacío")
    nuevo = db().guardar_venta_espera(
        body.etiqueta.strip() or "Espera",
        usuario,
        body.cliente_id,
        "",
        "",
        None,
        "consumidor_final",
        0,
        json.dumps(body.items),
        body.total,
    )
    return {"ok": True, "id": nuevo}


@app.post("/api/espera/{espera_id}/tomar")
def tomar_espera(request: Request, espera_id: int):
    _exigir(request, "mostrador")
    row = db().obtener_venta_espera(espera_id)
    if not row:
        raise HTTPException(status_code=404, detail="Esa venta en espera ya no está")
    items = json.loads(row[9] or "[]")
    db().eliminar_venta_espera(espera_id)
    return {"etiqueta": row[1], "items": items, "cliente_id": row[3]}


@app.get("/api/factura/{factura_id}")
def ver_factura(request: Request, factura_id: int):
    _exigir_alguno(request, ("historial", "cotizaciones"))
    conn = db().get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT f.id, f.numero, f.fecha, f.total, f.estado, f.ncf, f.usuario,
               COALESCE(c.nombre, 'Consumidor final'), f.subtotal, f.impuesto_total,
               f.cliente_id
        FROM facturas f
        LEFT JOIN clientes c ON c.id = f.cliente_id
        WHERE f.id=?
        """,
        (factura_id,),
    )
    fac = cur.fetchone()
    if not fac:
        conn.close()
        raise HTTPException(status_code=404, detail="Factura no encontrada")
    cur.execute(
        """
        SELECT descripcion, cantidad, precio_unitario, total_linea, producto_id, impuesto_item
        FROM factura_detalle WHERE factura_id=?
        """,
        (factura_id,),
    )
    lineas = [
        {"nombre": n, "cantidad": c, "precio": p, "total": t, "producto_id": pid, "itbis": imp}
        for n, c, p, t, pid, imp in cur.fetchall()
    ]
    conn.close()
    return {
        "id": fac[0], "numero": fac[1], "fecha": fac[2], "total": fac[3],
        "estado": fac[4], "ncf": fac[5], "usuario": fac[6], "cliente": fac[7],
        "subtotal": fac[8], "itbis": fac[9], "cliente_id": fac[10], "lineas": lineas,
    }


def _pdf_factura(factura_id: int) -> tuple[bytes, str]:
    from report_pdf_builder import build_factura_comprobante_pdf

    base = db()
    try:
        pdf = build_factura_comprobante_pdf(base, factura_id)
    except ImportError as exc:
        raise HTTPException(status_code=500, detail="Falta reportlab en el servidor") from exc
    if not pdf:
        raise HTTPException(status_code=404, detail="Factura no encontrada")
    conn = base.get_connection()
    cur = conn.cursor()
    cur.execute("SELECT numero FROM facturas WHERE id=?", (factura_id,))
    row = cur.fetchone()
    conn.close()
    numero = (row[0] if row else str(factura_id)) or str(factura_id)
    archivo = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(numero))
    return pdf, f"{archivo}.pdf"


@app.get("/api/factura/{factura_id}/pdf")
def pdf_factura(request: Request, factura_id: int):
    _exigir(request, "historial")
    pdf, archivo = _pdf_factura(factura_id)
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={archivo}"},
    )


class CorreoIn(BaseModel):
    email: str


@app.post("/api/factura/{factura_id}/correo")
def correo_factura(request: Request, factura_id: int, body: CorreoIn):
    _exigir(request, "historial")
    email = body.email.strip()
    if "@" not in email or "." not in email.split("@")[-1] or " " in email:
        raise HTTPException(status_code=400, detail="Correo inválido")
    pdf, archivo = _pdf_factura(factura_id)
    base = db()
    conn = base.get_connection()
    cur = conn.cursor()
    cur.execute("SELECT numero, cliente_id FROM facturas WHERE id=?", (factura_id,))
    fac = cur.fetchone()
    if fac and fac[1]:
        cur.execute("UPDATE clientes SET email=? WHERE id=?", (email, fac[1]))
        conn.commit()
    conn.close()
    numero = fac[0] if fac else archivo
    empresa = base.get_empresa_info()
    _enviar_pdf(
        email,
        f"Comprobante {numero}",
        f"{empresa.get('nombre') or 'Factura'} te envía el comprobante {numero}.",
        pdf,
        archivo,
    )
    return {"ok": True, "mensaje": f"Comprobante enviado a {email}"}


@app.post("/api/historial/anular")
def anular(request: Request, body: AnularIn):
    usuario = _exigir(request, "historial")
    ok, msg = db().anular_factura(body.factura_id, body.motivo, usuario)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"ok": True, "mensaje": msg}


@app.post("/api/cotizaciones")
def crear_cotizacion(request: Request, body: VentaIn):
    usuario = _exigir(request, "cotizaciones")
    if not body.items:
        raise HTTPException(status_code=400, detail="El ticket está vacío")
    base = db()
    conn = base.get_connection()
    cur = conn.cursor()
    det, subtotal, impuesto = _armar_lineas(cur, body.items)
    if not det:
        conn.close()
        raise HTTPException(status_code=400, detail="La cotización necesita productos")
    numero = "P-" + datetime.now().strftime("%Y%m%d%H%M%S")
    total = round(subtotal + impuesto, 2)
    cur.execute(
        """
        INSERT INTO facturas (
            numero, tipo_comprobante, cliente_id, subtotal, descuento_total,
            impuesto_total, total, estado, usuario, observaciones, moneda, lista_precio
        ) VALUES (?, 'consumidor_final', ?, ?, 0, ?, ?, 'cotizacion', ?, 'Cotización web', 'DOP', ?)
        """,
        (numero, body.cliente_id, round(subtotal, 2), round(impuesto, 2), total, usuario, body.lista),
    )
    fid = cur.lastrowid
    for pid, nombre, cant, precio, itbis, tl in det:
        cur.execute(
            """
            INSERT INTO factura_detalle (
                factura_id, producto_id, descripcion, cantidad, precio_unitario,
                descuento_item, impuesto_item, total_linea
            ) VALUES (?,?,?,?,?,0,?,?)
            """,
            (fid, pid, nombre, cant, precio, itbis, tl),
        )
    conn.commit()
    conn.close()
    return {"ok": True, "numero": numero, "id": fid, "total": total}


@app.post("/api/cotizaciones/{factura_id}")
def editar_cotizacion(request: Request, factura_id: int, body: VentaIn):
    _exigir(request, "cotizaciones")
    base = db()
    conn = base.get_connection()
    cur = conn.cursor()
    cur.execute("SELECT estado FROM facturas WHERE id=?", (factura_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Cotización no encontrada")
    if row[0] != "cotizacion":
        conn.close()
        raise HTTPException(status_code=400, detail="La factura emitida se consulta. Solo la cotización se edita.")
    det, subtotal, impuesto = _armar_lineas(cur, body.items)
    if not det:
        conn.close()
        raise HTTPException(status_code=400, detail="La cotización necesita productos")
    total = round(subtotal + impuesto, 2)
    cur.execute("DELETE FROM factura_detalle WHERE factura_id=?", (factura_id,))
    for pid, nombre, cant, precio, itbis, tl in det:
        cur.execute(
            """
            INSERT INTO factura_detalle (
                factura_id, producto_id, descripcion, cantidad, precio_unitario,
                descuento_item, impuesto_item, total_linea
            ) VALUES (?,?,?,?,?,0,?,?)
            """,
            (factura_id, pid, nombre, cant, precio, itbis, tl),
        )
    cur.execute(
        """
        UPDATE facturas
        SET cliente_id=?, subtotal=?, impuesto_total=?, total=?
        WHERE id=? AND estado='cotizacion'
        """,
        (body.cliente_id, round(subtotal, 2), round(impuesto, 2), total, factura_id),
    )
    conn.commit()
    conn.close()
    return {"ok": True, "id": factura_id, "total": total}


@app.post("/api/cotizaciones/confirmar")
def confirmar_cotizacion(request: Request, body: ConvertirIn):
    usuario = _exigir(request, "cotizaciones")
    base = db()
    if base.fetch_caja_abierta_row() is None:
        raise HTTPException(status_code=400, detail="Abre el turno de caja antes de confirmar")
    conn = base.get_connection()
    cur = conn.cursor()
    cur.execute("SELECT total FROM facturas WHERE id=? AND estado='cotizacion'", (body.factura_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Cotización no encontrada")
    ok, msg = base.convertir_presupuesto_a_venta(
        body.factura_id, usuario, [{"tipo": "efectivo", "monto": float(row[0] or 0)}]
    )
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"ok": True, "mensaje": msg}


@app.get("/api/compras/lista")
def listar_compras(request: Request):
    _exigir(request, "compras")
    conn = db().get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT c.id, c.fecha, IFNULL(pr.nombre, 'Proveedor'), c.total, IFNULL(c.nota, '')
        FROM compras c
        LEFT JOIN proveedores pr ON pr.id = c.proveedor_id
        ORDER BY c.id DESC
        LIMIT 30
        """
    )
    rows = [{"id": i, "fecha": f, "proveedor": p, "total": t, "nota": n} for i, f, p, t, n in cur.fetchall()]
    cur.execute("SELECT id, nombre FROM proveedores WHERE IFNULL(activo,1)=1 ORDER BY nombre")
    provs = [{"id": i, "nombre": n} for i, n in cur.fetchall()]
    cur.execute("SELECT codigo FROM bodegas ORDER BY codigo COLLATE NOCASE")
    bodegas_nombres = [r[0] for r in cur.fetchall()] or ["Principal"]
    conn.close()
    return {"items": rows, "proveedores": provs, "bodegas": bodegas_nombres}


@app.get("/api/bodegas")
def bodegas(request: Request):
    _exigir(request, "transferir")
    conn = db().get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT p.nombre, s.bodega, s.cantidad
        FROM stock_bodega s
        JOIN productos p ON p.id = s.producto_id
        ORDER BY s.bodega, p.nombre
        LIMIT 200
        """
    )
    rows = [{"producto": n, "bodega": b, "cantidad": c} for n, b, c in cur.fetchall()]
    cur.execute("SELECT codigo FROM bodegas ORDER BY codigo COLLATE NOCASE")
    nombres = [r[0] for r in cur.fetchall()]
    conn.close()
    return {"items": rows, "bodegas": nombres}


class BodegaIn(BaseModel):
    nombre: str


@app.post("/api/bodegas")
def crear_bodega(request: Request, body: BodegaIn):
    _exigir(request, "transferir")
    nombre = body.nombre.strip()
    if len(nombre) < 2:
        raise HTTPException(status_code=400, detail="Indica el nombre de la bodega")
    conn = db().get_connection()
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO bodegas (codigo) VALUES (?)", (nombre,))
    conn.commit()
    conn.close()
    return {"ok": True}


@app.get("/api/metodos")
def metodos(request: Request):
    _user(request)
    return {
        "items": [
            {"codigo": c, "nombre": n, "afecta_caja": bool(a), "activo": bool(act)}
            for c, n, a, act in db().listar_metodos_pago(False)
        ]
    }


@app.post("/api/metodos")
def guardar_metodo(request: Request, body: MetodoIn):
    _exigir(request, "metodos")
    if len(body.codigo.strip()) < 2 or len(body.nombre.strip()) < 2:
        raise HTTPException(status_code=400, detail="Indica código y nombre")
    db().guardar_metodo_pago(body.codigo, body.nombre, body.afecta_caja, body.activo)
    return {"ok": True}


@app.get("/api/seguimiento")
def seguimiento(request: Request):
    _exigir(request, "seguimiento")
    base = db()
    cajeros = [
        {"usuario": u, "facturas": n, "total": t or 0}
        for u, n, t in base.reporte_por_cajero(30)
    ]
    conn = base.get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, IFNULL(usuario_cierre, usuario_apertura), fecha_apertura, fecha_cierre,
               IFNULL(monto_inicial,0), IFNULL(efectivo_contado,0), IFNULL(diferencia_efectivo,0),
               IFNULL(observaciones,''), IFNULL(seguimiento_estado,'pendiente'),
               IFNULL(seguimiento_nota,''), estado
        FROM cierres_caja
        ORDER BY id DESC
        LIMIT 40
        """
    )
    turnos = [
        {
            "id": i, "usuario": u, "apertura": a, "cierre": c, "fondo": f,
            "contado": cont, "diferencia": d, "observaciones": o,
            "seguimiento": s or ("ok" if abs(float(d or 0)) < 0.05 else "pendiente"),
            "nota": nota, "estado": est,
        }
        for i, u, a, c, f, cont, d, o, s, nota, est in cur.fetchall()
    ]
    conn.close()
    return {"cajeros": cajeros, "turnos": turnos}


@app.post("/api/seguimiento")
def guardar_seguimiento(request: Request, body: SeguimientoIn):
    usuario = _exigir(request, "seguimiento")
    estado = body.estado if body.estado in ("pendiente", "en_seguimiento", "resuelto") else "en_seguimiento"
    conn = db().get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE cierres_caja
        SET seguimiento_estado=?, seguimiento_nota=?, seguimiento_usuario=?, seguimiento_fecha=?
        WHERE id=?
        """,
        (estado, body.nota.strip(), usuario, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), body.cierre_id),
    )
    conn.commit()
    conn.close()
    return {"ok": True}


@app.post("/api/ncf")
def guardar_ncf(request: Request, body: SecuenciaIn):
    _exigir(request, "ncf")
    db().guardar_secuencia_ncf(body.tipo, body.prefijo, body.siguiente, body.hasta)
    return {"ok": True}


@app.get("/api/ncf")
def ncf(request: Request):
    _exigir(request, "ncf")
    return {
        "secuencias": [
            {"tipo": t, "prefijo": p, "siguiente": s, "hasta": h}
            for t, p, s, h in db().listar_secuencias_ncf()
        ]
    }


class ProductoIn(BaseModel):
    nombre: str
    precio: float
    stock: float = 0
    codigo: str = ""
    bodega: str = "Principal"
    categoria: str = ""


class UsuarioIn(BaseModel):
    username: str
    password: str
    role: str = "user"
    modulos: list[str] = []


class PermisosIn(BaseModel):
    username: str
    modulos: list[str]


class SeguimientoIn(BaseModel):
    cierre_id: int
    estado: str
    nota: str = ""


class EsperaIn(BaseModel):
    etiqueta: str
    items: list[dict]
    total: float
    cliente_id: int | None = None


class AnularIn(BaseModel):
    factura_id: int
    motivo: str


class ConvertirIn(BaseModel):
    factura_id: int


class SecuenciaIn(BaseModel):
    tipo: str
    prefijo: str
    siguiente: int
    hasta: int


class ClienteIn(BaseModel):
    nombre: str
    documento: str = ""
    telefono: str = ""
    email: str = ""


class EtiquetasIn(BaseModel):
    ids: list[int]


@app.post("/api/clientes/rapido")
def cliente_rapido(request: Request, body: ClienteIn):
    _exigir(request, "mostrador")
    if len(body.nombre.strip()) < 2:
        raise HTTPException(status_code=400, detail="Indica el nombre")
    nuevo = db().crear_cliente_rapido(
        body.nombre.strip(),
        body.documento.strip() or None,
        body.telefono.strip() or None,
        body.email.strip() or None,
    )
    return {"ok": True, "id": nuevo}


class EmpresaIn(BaseModel):
    nombre: str
    direccion: str = ""
    rnc: str = ""
    smtp_host: str = ""
    smtp_port: str = "587"
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""


@app.get("/api/inventario")
def inventario(request: Request, q: str = ""):
    _exigir(request, "inventario")
    conn = db().get_connection()
    cur = conn.cursor()
    sql = """
        SELECT p.id, p.nombre, IFNULL(p.precio,0), IFNULL(p.stock,0),
               IFNULL(p.codigo_barras,''), IFNULL(p.bodega_codigo,'Principal'),
               IFNULL(c.nombre,''), IFNULL(p.stock_minimo,0), IFNULL(p.activo,1)
        FROM productos p
        LEFT JOIN categorias c ON c.id = p.categoria_id
        WHERE 1=1
    """
    params: list = []
    if q.strip():
        sql += " AND (p.nombre LIKE ? OR IFNULL(p.codigo_barras,'') LIKE ?)"
        params.extend([f"%{q.strip()}%", f"%{q.strip()}%"])
    sql += " ORDER BY p.nombre COLLATE NOCASE LIMIT 200"
    cur.execute(sql, params)
    rows = [
        {
            "id": i, "nombre": n, "precio": p, "stock": s, "codigo": cb,
            "bodega": b or "Principal", "categoria": cat, "minimo": mn, "activo": bool(a),
        }
        for i, n, p, s, cb, b, cat, mn, a in cur.fetchall()
    ]
    conn.close()
    return {"items": rows}


@app.post("/api/inventario")
def crear_producto(request: Request, body: ProductoIn):
    _exigir(request, "inventario")
    nombre = body.nombre.strip()
    if len(nombre) < 2:
        raise HTTPException(status_code=400, detail="Indica el nombre del producto")
    base = db()
    conn = base.get_connection()
    cur = conn.cursor()
    cat_id = None
    if body.categoria.strip():
        cur.execute("SELECT id FROM categorias WHERE nombre=?", (body.categoria.strip(),))
        row = cur.fetchone()
        if row:
            cat_id = row[0]
        else:
            cur.execute("INSERT INTO categorias (nombre) VALUES (?)", (body.categoria.strip(),))
            cat_id = cur.lastrowid
    cur.execute(
        """
        INSERT INTO productos (
            nombre, precio, precio_base, precio_minimo, stock, categoria_id,
            stock_minimo, codigo_barras, activo, bodega_codigo, aplica_itbis, facturar_sin_stock
        ) VALUES (?, ?, ?, 0, ?, ?, 0, ?, 1, ?, 1, 1)
        """,
        (nombre, body.precio, body.precio, body.stock, cat_id, body.codigo.strip() or None, body.bodega.strip() or "Principal"),
    )
    pid = cur.lastrowid
    conn.commit()
    conn.close()
    base.ajustar_stock_bodega(pid, 0, body.bodega.strip() or "Principal")
    return {"ok": True, "id": pid}


@app.post("/api/etiquetas")
def etiquetas_pdf(request: Request, body: EtiquetasIn):
    _exigir(request, "inventario")
    ids = []
    for valor in body.ids:
        try:
            numero = int(valor)
        except (TypeError, ValueError):
            continue
        if numero > 0 and numero not in ids:
            ids.append(numero)
    if not ids:
        raise HTTPException(status_code=400, detail="Marca al menos un producto")
    if len(ids) > 200:
        raise HTTPException(status_code=400, detail="Máximo 200 etiquetas a la vez")
    conn = db().get_connection()
    cur = conn.cursor()
    marcas = ",".join("?" * len(ids))
    cur.execute(
        f"""
        SELECT id, nombre,
               IFNULL(NULLIF(TRIM(codigo_barras), ''), IFNULL(codigo_producto, '')),
               IFNULL(precio, 0)
        FROM productos
        WHERE id IN ({marcas})
        """,
        ids,
    )
    por_id = {i: {"nombre": n, "codigo": c or "", "precio": p} for i, n, c, p in cur.fetchall()}
    conn.close()
    productos = [por_id[i] for i in ids if i in por_id]
    if not productos:
        raise HTTPException(status_code=404, detail="No hay productos para etiquetar")
    try:
        pdf = _pdf_etiquetas(productos)
    except ImportError as exc:
        raise HTTPException(status_code=500, detail="Falta reportlab en el servidor") from exc
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=etiquetas.pdf"},
    )


@app.get("/api/kardex")
def kardex(request: Request, producto_id: int):
    _exigir(request, "kardex")
    filas = db().get_kardex_filas_con_saldo(producto_id)
    return {"items": filas[-80:]}


@app.get("/api/historial")
def historial(request: Request, estado: str = "todos"):
    _exigir(request, "historial")
    modo = {"presupuestos": "presupuestos", "anuladas": "anuladas", "emitidas": "emitidas"}.get(estado, "todos")
    filas = db().list_facturas_modulo_erp(estado_docs=modo, ultimos_n=80)
    correos: dict[int, str] = {}
    ids = [int(r[0]) for r in filas]
    if ids:
        conn = db().get_connection()
        cur = conn.cursor()
        marcas = ",".join("?" * len(ids))
        cur.execute(
            f"""
            SELECT f.id, TRIM(IFNULL(c.email, ''))
            FROM facturas f
            LEFT JOIN clientes c ON c.id = f.cliente_id
            WHERE f.id IN ({marcas})
            """,
            ids,
        )
        correos = {int(i): em or "" for i, em in cur.fetchall()}
        conn.close()
    return {
        "items": [
            {
                "id": r[0], "numero": r[1], "fecha": r[2], "cliente": r[3],
                "total": r[5], "estado": r[6], "usuario": r[7],
                "email": correos.get(int(r[0]), ""),
            }
            for r in filas
        ]
    }


def _periodo_dashboard(rango: str):
    hoy = datetime.now().date()
    if rango == "dia":
        inicio = fin = hoy
        previo_fin = hoy - timedelta(days=1)
        previo_inicio = previo_fin
        etiqueta = hoy.strftime("%d/%m/%Y")
    elif rango == "mes":
        inicio = hoy.replace(day=1)
        fin = hoy
        previo_fin = inicio - timedelta(days=1)
        previo_inicio = previo_fin.replace(day=1)
        etiqueta = inicio.strftime("%d/%m") + " – " + fin.strftime("%d/%m/%Y")
    else:
        inicio = hoy - timedelta(days=6)
        fin = hoy
        previo_fin = inicio - timedelta(days=1)
        previo_inicio = previo_fin - timedelta(days=6)
        etiqueta = inicio.strftime("%d/%m") + " – " + fin.strftime("%d/%m/%Y")
    return inicio.isoformat(), fin.isoformat(), previo_inicio.isoformat(), previo_fin.isoformat(), etiqueta


def _suma_periodo(cur, inicio: str, fin: str) -> tuple[float, float, int]:
    cur.execute(
        """
        SELECT IFNULL(SUM(total),0), IFNULL(SUM(impuesto_total),0), COUNT(*)
        FROM facturas
        WHERE estado = 'emitida' AND date(fecha) >= date(?) AND date(fecha) <= date(?)
        """,
        (inicio, fin),
    )
    total, itbis, n = cur.fetchone()
    return float(total or 0), float(itbis or 0), int(n or 0)


def _delta(actual: float, previo: float) -> float | None:
    if previo <= 0:
        return None
    return round((actual - previo) / previo * 100, 1)


@app.get("/api/indicadores")
def indicadores(request: Request, rango: str = "semana"):
    _exigir(request, "indicadores")
    if rango not in ("dia", "semana", "mes"):
        rango = "semana"
    inicio, fin, prev_ini, prev_fin, etiqueta = _periodo_dashboard(rango)
    base = db()
    conn = base.get_connection()
    cur = conn.cursor()
    ventas, itbis, facturas_n = _suma_periodo(cur, inicio, fin)
    ventas_prev, itbis_prev, facturas_prev = _suma_periodo(cur, prev_ini, prev_fin)
    ticket = round(ventas / facturas_n, 2) if facturas_n else 0.0
    ticket_prev = round(ventas_prev / facturas_prev, 2) if facturas_prev else 0.0
    if rango == "dia":
        cur.execute(
            """
            SELECT strftime('%H', fecha), IFNULL(SUM(total),0)
            FROM facturas
            WHERE estado = 'emitida' AND date(fecha) = date(?)
            GROUP BY 1
            """,
            (inicio,),
        )
        por_hora = {h: float(t or 0) for h, t in cur.fetchall()}
        serie = [{"etiqueta": f"{h:02d}h", "total": por_hora.get(f"{h:02d}", 0.0)} for h in range(8, 22)]
    else:
        cur.execute(
            """
            SELECT date(fecha), IFNULL(SUM(total),0)
            FROM facturas
            WHERE estado = 'emitida' AND date(fecha) >= date(?) AND date(fecha) <= date(?)
            GROUP BY 1
            """,
            (inicio, fin),
        )
        por_dia = {d: float(t or 0) for d, t in cur.fetchall()}
        cursor_dia = datetime.fromisoformat(inicio).date()
        ultimo = datetime.fromisoformat(fin).date()
        serie = []
        while cursor_dia <= ultimo:
            clave = cursor_dia.isoformat()
            serie.append({"etiqueta": cursor_dia.strftime("%d/%m"), "total": por_dia.get(clave, 0.0)})
            cursor_dia += timedelta(days=1)
    cur.execute(
        """
        SELECT d.descripcion, SUM(d.cantidad), SUM(d.total_linea)
        FROM factura_detalle d
        JOIN facturas f ON f.id = d.factura_id
        WHERE f.estado = 'emitida' AND date(f.fecha) >= date(?) AND date(f.fecha) <= date(?)
        GROUP BY d.descripcion
        ORDER BY SUM(d.total_linea) DESC
        LIMIT 6
        """,
        (inicio, fin),
    )
    productos = [{"nombre": n, "cantidad": c, "total": t} for n, c, t in cur.fetchall()]
    cur.execute(
        """
        SELECT IFNULL(usuario, '—'), COUNT(*), SUM(total)
        FROM facturas
        WHERE estado = 'emitida' AND date(fecha) >= date(?) AND date(fecha) <= date(?)
        GROUP BY usuario
        ORDER BY SUM(total) DESC
        LIMIT 6
        """,
        (inicio, fin),
    )
    cajeros = [{"usuario": u, "facturas": n, "total": t} for u, n, t in cur.fetchall()]
    cur.execute(
        """
        SELECT p.tipo_pago, COUNT(*), SUM(p.monto)
        FROM pagos_factura p
        JOIN facturas f ON f.id = p.factura_id
        WHERE f.estado = 'emitida' AND date(f.fecha) >= date(?) AND date(f.fecha) <= date(?)
        GROUP BY p.tipo_pago
        ORDER BY SUM(p.monto) DESC
        """,
        (inicio, fin),
    )
    pagos = [{"metodo": m, "pagos": n, "total": t} for m, n, t in cur.fetchall()]
    cur.execute(
        """
        SELECT COUNT(*), IFNULL(SUM(monto_total),0)
        FROM notas_credito
        WHERE date(fecha) >= date(?) AND date(fecha) <= date(?)
        """,
        (inicio, fin),
    )
    dev_n, dev_total = cur.fetchone()
    cur.execute(
        """
        SELECT COUNT(*) FROM facturas
        WHERE estado = 'anulada' AND date(fecha) >= date(?) AND date(fecha) <= date(?)
        """,
        (inicio, fin),
    )
    anuladas = int(cur.fetchone()[0] or 0)
    cur.execute(
        """
        SELECT nombre_caja, IFNULL(usuario_apertura,''), IFNULL(monto_inicial,0), fecha_apertura
        FROM cierres_caja WHERE estado = 'abierto' ORDER BY id DESC LIMIT 1
        """
    )
    caja = cur.fetchone()
    conn.close()
    _rows, costo, valor_venta = base.get_inventory_valuation()
    return {
        "rango": rango,
        "etiqueta": etiqueta,
        "ventas": round(ventas, 2),
        "facturas": facturas_n,
        "ticket": ticket,
        "itbis": round(itbis, 2),
        "delta_ventas": _delta(ventas, ventas_prev),
        "delta_facturas": _delta(facturas_n, facturas_prev),
        "delta_ticket": _delta(ticket, ticket_prev),
        "delta_itbis": _delta(itbis, itbis_prev),
        "serie": serie,
        "productos": productos,
        "cajeros": cajeros,
        "pagos": pagos,
        "devoluciones": int(dev_n or 0),
        "devoluciones_total": float(dev_total or 0),
        "anuladas": anuladas,
        "valor_costo": costo,
        "valor_venta": valor_venta,
        "caja": None if not caja else {
            "nombre": caja[0], "usuario": caja[1], "fondo": caja[2], "apertura": caja[3],
        },
    }


def _guardar_permisos(username: str, modulos: list[str], role: str):
    if role == "admin":
        return
    limpios = [m for m in modulos if m in MODULOS and m not in ("seguimiento", "metodos")]
    if "mostrador" not in limpios:
        limpios.insert(0, "mostrador")
    conn = db().get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM usuario_permisos WHERE username=?", (username,))
    for modulo in limpios:
        cur.execute(
            "INSERT INTO usuario_permisos (username, modulo) VALUES (?,?)",
            (username, modulo),
        )
    conn.commit()
    conn.close()


@app.get("/api/usuarios")
def usuarios(request: Request):
    _exigir(request, "usuarios")
    items = []
    for i, u, r in db().get_users():
        items.append({"id": i, "username": u, "role": r, "modulos": _permisos(u)})
    return {"items": items, "modulos": [m for m in MODULOS if m not in ("seguimiento", "metodos")]}


@app.post("/api/usuarios")
def crear_usuario(request: Request, body: UsuarioIn):
    _exigir(request, "usuarios")
    if body.role not in ("admin", "user", "empleado"):
        raise HTTPException(status_code=400, detail="El rol debe ser admin, user o empleado")
    try:
        db().create_user(body.username.strip(), body.password, body.role)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _guardar_permisos(body.username.strip(), body.modulos, body.role)
    return {"ok": True}


@app.post("/api/usuarios/permisos")
def actualizar_permisos(request: Request, body: PermisosIn):
    _exigir(request, "usuarios")
    conn = db().get_connection()
    cur = conn.cursor()
    cur.execute("SELECT role FROM users WHERE username=?", (body.username.strip(),))
    row = cur.fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    _guardar_permisos(body.username.strip(), body.modulos, row[0])
    return {"ok": True}


@app.get("/api/clientes")
def clientes(request: Request, q: str = ""):
    _exigir_alguno(request, ("clientes", "cotizaciones", "mostrador"))
    return {
        "items": [
            {"id": i, "nombre": n, "documento": d or "", "telefono": t or "", "email": em or ""}
            for i, n, d, t, em in db().buscar_clientes(q, 80)
        ]
    }


@app.post("/api/clientes")
def crear_cliente(request: Request, body: ClienteIn):
    _exigir(request, "clientes")
    if len(body.nombre.strip()) < 2:
        raise HTTPException(status_code=400, detail="Indica el nombre")
    nuevo = db().crear_cliente_rapido(
        body.nombre.strip(),
        body.documento.strip() or None,
        body.telefono.strip() or None,
        body.email.strip() or None,
    )
    return {"ok": True, "id": nuevo}


@app.get("/api/empresa")
def empresa(request: Request):
    _exigir(request, "apariencia")
    base = db()
    info = base.get_empresa_info()
    info["smtp_host"] = base.get_config("smtp_host", "") or ""
    info["smtp_port"] = base.get_config("smtp_port", "587") or "587"
    info["smtp_user"] = base.get_config("smtp_user", "") or ""
    info["smtp_from"] = base.get_config("smtp_from", "") or ""
    info["smtp_listo"] = bool(info["smtp_host"] and (info["smtp_from"] or info["smtp_user"]))
    return info


@app.post("/api/empresa")
def guardar_empresa(request: Request, body: EmpresaIn):
    _exigir(request, "apariencia")
    base = db()
    base.set_empresa_info(body.nombre.strip(), body.direccion.strip())
    base.set_config("empresa_rnc", body.rnc.strip())
    try:
        puerto = int((body.smtp_port or "587").strip())
    except ValueError:
        raise HTTPException(status_code=400, detail="El puerto de correo no es válido")
    if puerto < 1 or puerto > 65535:
        raise HTTPException(status_code=400, detail="El puerto de correo no es válido")
    base.set_config("smtp_host", body.smtp_host.strip())
    base.set_config("smtp_port", str(puerto))
    base.set_config("smtp_user", body.smtp_user.strip())
    base.set_config("smtp_from", body.smtp_from.strip())
    if body.smtp_password.strip():
        base.set_config("smtp_password", body.smtp_password)
    return {"ok": True}


if _STATIC.is_dir():
    app.mount("/estaticos", StaticFiles(directory=str(_STATIC)), name="estaticos")


HTML = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Facturación</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin:0; font-family: "Segoe UI", system-ui, sans-serif; background:#0b0f17; color:#e8edf7; }
  button, input, select { font: inherit; }
  button { background:#f5a524; color:#1a1305; border:0; border-radius:8px; padding:10px 14px; cursor:pointer; font-weight:700; }
  button.sec { background:#232a3a; color:#e8edf7; font-weight:600; }
  button.nav { width:100%; text-align:left; background:transparent; color:#b7c0d4; font-weight:600; border-radius:10px; }
  button.nav:hover, button.nav.on { background:#5b4ce6; color:white; }
  input, select { background:#121826; color:#e8edf7; border:1px solid #2a3348; border-radius:10px; padding:10px 12px; }
  #login { max-width:380px; margin:12vh auto; background:#161c28; padding:28px; border-radius:16px; display:grid; gap:12px; border:1px solid #2a3348; }
  #app { display:none; min-height:100vh; }
  .shell { display:flex; min-height:100vh; }
  .side { width:220px; background:#10151f; padding:22px 14px; display:flex; flex-direction:column; gap:6px; border-right:1px solid #1d2433; max-height:100vh; overflow-y:auto; position:sticky; top:0; }
  .brand { font-weight:800; letter-spacing:.04em; padding:8px 10px 18px; }
  .brand small { display:block; color:#8b95a8; font-weight:500; letter-spacing:0; margin-top:4px; }
  .work { flex:1; display:flex; min-width:0; }
  .catalog { flex:1; padding:22px; min-width:0; }
  .toolbar { display:flex; gap:10px; margin-bottom:16px; flex-wrap:wrap; align-items:center; }
  .toolbar input { flex:1; min-width:180px; }
  .grid { display:grid; grid-template-columns: repeat(auto-fill, minmax(200px,1fr)); gap:14px; align-content:start; }
  .card { height:auto; border-radius:14px; padding:0; border:1px solid #2a3348; background:#1a2130; display:flex; flex-direction:column; align-items:stretch; overflow:hidden; text-align:left; color:#fff; }
  .card .foto { display:block; height:148px; background:#121826 center/cover no-repeat; }
  .card .info { width:100%; padding:12px; background:#1a2130; color:#fff; }
  .card .info b { display:block; margin-bottom:4px; color:#fff; }
  .rail { width:340px; background:linear-gradient(180deg,#6d5ef5 0%, #4c3fd4 100%); padding:22px 18px; color:white; }
  .rail h3 { margin:0 0 8px; }
  ul { list-style:none; padding:0; margin:0; max-height:280px; overflow:auto; }
  li { display:flex; justify-content:space-between; gap:8px; padding:8px 0; border-bottom:1px solid rgba(255,255,255,.18); }
  .pagos { display:grid; gap:10px; margin-top:12px; }
  .pagos label { display:flex; flex-direction:column; gap:6px; font-size:13px; color:#efeaff; }
  .pagos input { background:rgba(12,16,32,.45); border-color:rgba(255,255,255,.25); color:white; }
  #tot { font-size:26px; font-weight:800; margin:14px 0 4px; }
  .msg { color:#ffe08a; min-height:1.2em; }
  #top { color:#efeaff; font-size:13px; margin-bottom:10px; }
  .panel { background:#161c28; border:1px solid #2a3348; border-radius:16px; padding:22px; }
  .panel h2 { margin:0 0 6px; }
  .panel p.hint { color:#8b95a8; margin:0 0 16px; }
  .form { display:grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap:12px; align-items:end; }
  .form label { display:flex; flex-direction:column; gap:6px; font-size:13px; color:#b7c0d4; }
  .form .wide { grid-column: 1 / -1; }
  table { width:100%; border-collapse:collapse; margin-top:16px; }
  th, td { text-align:left; padding:10px 8px; border-bottom:1px solid #2a3348; }
  th { color:#8b95a8; font-weight:600; }
  .bajo { outline: 1px solid #f5a524; }
  .doc { background:#141a27; border:1px solid #2a3348; border-radius:14px; padding:14px; margin:0 0 14px; }
  .doc h3 { margin:0 0 6px; }
  .dash-head { display:flex; justify-content:space-between; align-items:flex-end; gap:16px; margin-bottom:16px; flex-wrap:wrap; }
  .dash-head h2 { margin:0; font-size:28px; }
  .dash-head p { margin:4px 0 0; color:#8b95a8; }
  .rangos { display:flex; gap:8px; }
  .rangos button { background:#1a2130; color:#c9d2e3; }
  .rangos button.on { background:#5b4ce6; color:white; }
  .kpis { display:grid; grid-template-columns:repeat(4, minmax(0,1fr)); gap:12px; }
  .kpi { border-radius:16px; padding:16px 16px 14px; color:white; min-height:108px; display:flex; flex-direction:column; justify-content:space-between; }
  .kpi span { font-size:13px; opacity:.9; }
  .kpi b { font-size:26px; letter-spacing:-.03em; }
  .kpi em { font-style:normal; font-size:12px; opacity:.85; }
  .kpi.violeta { background:linear-gradient(160deg,#6d5ef5,#4338ca); }
  .kpi.azul { background:linear-gradient(160deg,#38bdf8,#2563eb); }
  .kpi.verde { background:linear-gradient(160deg,#34d399,#059669); }
  .kpi.rosa { background:linear-gradient(160deg,#fb7185,#e11d48); }
  .dash-grid { display:grid; grid-template-columns: 1.5fr .9fr; gap:12px; margin-top:12px; }
  .dash-card { background:#141a27; border:1px solid #2a3348; border-radius:16px; padding:16px; }
  .dash-card h3 { margin:0 0 12px; font-size:15px; }
  .dash-card svg { width:100%; height:210px; display:block; }
  .barrow { display:grid; grid-template-columns: minmax(0,140px) 1fr auto; gap:8px; align-items:center; margin:8px 0; font-size:13px; }
  .barrow span { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .track { height:8px; background:#222a3c; border-radius:99px; overflow:hidden; }
  .track div { height:100%; border-radius:99px; }
  .dona { width:132px; height:132px; border-radius:50%; margin:8px auto; }
  .leyenda { display:flex; flex-wrap:wrap; gap:8px 14px; font-size:12px; color:#b7c0d4; margin-top:8px; }
  .side-stats { display:grid; gap:10px; }
  .side-stats div { background:#101722; border-radius:12px; padding:12px; }
  .side-stats b { display:block; font-size:18px; margin-top:4px; }
  .side-stats span { color:#8b95a8; font-size:12px; }
  #view-form.es-dash { background:transparent; border:0; padding:0; }
  #view-form.es-dash > h2, #view-form.es-dash > .hint, #view-form.es-dash > #formMsg { display:none; }
  #recibo { display:none; }
  @media print {
    body * { visibility: hidden; }
    #recibo, #recibo * { visibility: visible; }
    #recibo { display:block; position:absolute; left:0; top:0; width:80mm; color:#111; background:#fff; padding:12px; }
  }
  @media (max-width: 900px) {
    .shell, .work { flex-direction:column; }
    .side, .rail { width:auto; }
    .kpis, .dash-grid { grid-template-columns:1fr; }
  }
</style>
</head>
<body>
<div id="login">
  <h2>Facturación</h2>
  <input id="user" placeholder="Usuario" autocomplete="username"/>
  <input id="pass" placeholder="Contraseña" type="password" autocomplete="current-password"/>
  <button onclick="entrar()">Entrar</button>
  <div class="msg" id="loginMsg"></div>
</div>
<main id="app">
  <div class="shell">
    <aside class="side">
      <div class="brand">MD Alliance<small>Punto de venta</small></div>
      <button class="nav on" data-view="venta" data-mod="mostrador" onclick="ver('venta')">Mostrador</button>
      <button class="nav" data-view="inventario" data-mod="inventario" onclick="ver('inventario')">Inventario</button>
      <button class="nav" data-view="kardex" data-mod="kardex" onclick="ver('kardex')">Kardex</button>
      <button class="nav" data-view="historial" data-mod="historial" onclick="ver('historial')">Historial</button>
      <button class="nav" data-view="cotizaciones" data-mod="cotizaciones" onclick="ver('cotizaciones')">Cotizaciones</button>
      <button class="nav" data-view="indicadores" data-mod="indicadores" onclick="ver('indicadores')">Dashboard</button>
      <button class="nav" data-view="clientes" data-mod="clientes" onclick="ver('clientes')">Clientes</button>
      <button class="nav" data-view="usuarios" data-mod="usuarios" onclick="ver('usuarios')">Usuarios</button>
      <button class="nav" data-view="seguimiento" data-mod="seguimiento" onclick="ver('seguimiento')">Seguimiento</button>
      <button class="nav" data-view="empresa" data-mod="apariencia" onclick="ver('empresa')">Apariencia</button>
      <button class="nav" data-view="caja" data-mod="caja" onclick="ver('caja')" id="btnCaja">Abrir caja</button>
      <button class="nav" data-view="ingreso" data-mod="caja" onclick="ver('ingreso')">Ingreso</button>
      <button class="nav" data-view="retiro" data-mod="caja" onclick="ver('retiro')">Retiro</button>
      <button class="nav" data-view="cerrar" data-mod="caja" onclick="ver('cerrar')">Cerrar caja</button>
      <button class="nav" data-view="reportes" data-mod="reportes" onclick="ver('reportes')">Reportes</button>
      <button class="nav" data-view="compra" data-mod="compras" onclick="ver('compra')">Compras</button>
      <button class="nav" data-view="promo" data-mod="promociones" onclick="ver('promo')">Promociones</button>
      <button class="nav" data-view="dev" data-mod="devoluciones" onclick="ver('dev')">Devoluciones</button>
      <button class="nav" data-view="tr" data-mod="transferir" onclick="ver('tr')">Transferir</button>
      <button class="nav" data-view="ncf" data-mod="ncf" onclick="ver('ncf')">NCF</button>
      <button class="nav" data-view="metodos" data-mod="metodos" onclick="ver('metodos')">Métodos</button>
      <div id="top"></div>
      <button class="sec" onclick="salir()">Salir</button>
    </aside>
    <div class="work">
      <section class="catalog">
        <div id="view-venta">
          <div class="toolbar">
            <input id="q" placeholder="Buscar o escanear"/>
            <select id="cat"></select>
            <select id="lista">
              <option value="1">Público</option>
              <option value="2">Mayorista</option>
              <option value="3">VIP</option>
              <option value="4">Especial</option>
            </select>
            <button class="sec" onclick="cargar()">Buscar</button>
          </div>
          <div class="grid" id="grid"></div>
        </div>
        <section id="view-form" class="panel" style="display:none">
          <h2 id="formTitle"></h2>
          <p class="hint" id="formHint"></p>
          <div id="formBody"></div>
          <div class="msg" id="formMsg"></div>
        </section>
      </section>
      <aside class="rail">
        <h3>Ticket</h3>
        <ul id="lines"></ul>
        <p id="tot">Total: RD$ 0.00</p>
        <div class="pagos">
          <label>Cliente<select id="cliVenta"><option value="">Consumidor final</option></select></label>
          <label>Comprobante<select id="tipoComp"><option value="consumidor_final">Consumidor final</option><option value="credito_fiscal">Crédito fiscal</option></select></label>
          <label>Nombre<input id="cliNuevo" placeholder="Alta rápida"/></label>
          <label>Documento<input id="cliNuevoDoc" placeholder="RNC o cédula"/></label>
          <label>Correo<input id="cliNuevoMail" placeholder="cliente@correo.com"/></label>
          <button class="sec" onclick="crearClienteTicket()">Agregar cliente</button>
          <label>Descuento<input id="desc" type="number" step="0.01" value="0"/></label>
          <div id="pagosDyn"></div>
          <label>Ancho<select id="anchoTicket"><option value="80">80 mm</option><option value="58">58 mm</option></select></label>
          <p id="cambio">Cambio: RD$ 0.00</p>
          <button onclick="cobrar()">Cobrar</button>
          <label>Apartar como<input id="esperaNom" placeholder="Mesa 1"/></label>
          <button class="sec" onclick="apartar()">Apartar</button>
          <button class="sec" onclick="guardarCotizacion()">Cotizar</button>
        </div>
        <div id="esperas"></div>
        <div class="msg" id="msg"></div>
      </aside>
    </div>
  </div>
</main>
<div id="recibo"></div>
<script>
const cart = [];
function money(n){ return "RD$ " + Number(n||0).toFixed(2); }
async function api(url, opt){
  const r = await fetch(url, Object.assign({headers:{"Content-Type":"application/json"}}, opt||{}));
  const data = await r.json().catch(()=>({}));
  if(!r.ok){
    const d = data.detail;
    const texto = typeof d === "string" ? d : (Array.isArray(d) ? d.map(x => x.msg || JSON.stringify(x)).join(", ") : "Error");
    throw new Error(texto || "Error");
  }
  return data;
}
async function entrar(){
  try {
    await api("/api/login", {method:"POST", body: JSON.stringify({username:user.value, password:pass.value})});
    login.style.display="none"; app.style.display="block";
    await refrescar(); await cargar();
  } catch(e){ loginMsg.textContent = e.message; }
}
let permisos = [];
function aplicarPermisos(mods){
  permisos = mods || [];
  document.querySelectorAll(".nav").forEach(b => {
    const m = b.dataset.mod;
    b.style.display = !m || permisos.includes(m) ? "" : "none";
  });
}
async function refrescar(){
  const e = await api("/api/estado");
  top.textContent = e.usuario + " · " + e.rol + (e.caja ? " · " + e.caja.nombre : " · caja cerrada");
  aplicarPermisos(e.permisos);
  window._empresa = e.empresa || {};
  const btn = document.getElementById("btnCaja");
  if (btn) btn.style.display = e.caja || !permisos.includes("caja") ? "none" : "";
  if (permisos.includes("mostrador")) {
    await cargarClientesVenta();
    await cargarMetodos();
    await cargarEsperas();
  }
}
async function cargarClientesVenta(){
  try {
    const d = await api("/api/clientes");
    const actual = cliVenta.value;
    cliVenta.innerHTML = '<option value="">Consumidor final</option>' + d.items.map(c => `<option value="${c.id}">${String(c.nombre).replaceAll("<","&lt;")}</option>`).join("");
    cliVenta.value = actual;
  } catch(e) { /* el cajero básico no lista clientes */ }
}
async function salir(){ await api("/api/logout", {method:"POST"}); location.reload(); }
async function cargar(){
  const data = await api("/api/catalogo?q=" + encodeURIComponent(q.value) + (cat.value ? "&categoria="+cat.value : ""));
  cat.innerHTML = '<option value="">Todas</option>' + data.categorias.map(c=>`<option value="${c.id}">${c.nombre}</option>`).join("");
  if (window._cat) cat.value = window._cat;
  const tarjetas = data.productos.concat(data.combos || []);
  const qv = q.value.trim().toLowerCase();
  const exacto = data.productos.find(p => (p.codigo || "").toLowerCase() === qv && qv);
  grid.innerHTML = tarjetas.map(p=>{
    const nivel = Number(lista.value);
    const precio = p.combo ? p.precio : ([p.precio, p.precio_2, p.precio_3, p.precio_4][nivel-1] || p.precio);
    const nombre = String(p.nombre).replaceAll("<","&lt;");
    const bajo = !p.combo && Number(p.stock) <= Number(p.minimo || 0) ? " bajo" : "";
    const extra = p.combo ? "Combo" : ("stock " + p.stock);
    return `<button class="card${bajo}" onclick='add(${JSON.stringify(p).replaceAll("'","&#39;")}, ${precio})'><span class="foto" style="background-image:url('${p.imagen}')"></span><span class="info"><b>${nombre}</b><br>${money(precio)} · ${extra}</span></button>`;
  }).join("");
  if (exacto) {
    const nivel = Number(lista.value);
    const precio = [exacto.precio, exacto.precio_2, exacto.precio_3, exacto.precio_4][nivel-1] || exacto.precio;
    q.value = "";
    add(exacto, precio);
    return cargar();
  }
}
cat.onchange = ()=>{ window._cat = cat.value; cargar(); };
lista.onchange = cargar;
function add(p, precio){
  const hit = cart.find(x=>x.id===p.id && !!x.combo===!!p.combo && x.precio===precio);
  if(hit) hit.cantidad += 1; else cart.push({id:p.id, nombre:p.nombre, precio, cantidad:1, itbis:p.itbis!==false, combo:!!p.combo});
  pintar();
}
function pagosActuales(){
  return [...document.querySelectorAll(".pago")].map(el => ({
    codigo: el.dataset.codigo,
    monto: Number(el.value||0),
    caja: el.dataset.caja === "1",
    el
  }));
}
function totalTicket(){
  const sub = cart.reduce((s,l)=>s+l.precio*l.cantidad,0);
  const rebaja = Math.min(sub, Math.max(0, Number(document.getElementById("desc").value||0)));
  const base = Math.max(0, sub-rebaja);
  const itbis = cart.reduce((s,l)=>s+(l.itbis?l.precio*l.cantidad*0.18:0),0) * (sub ? base/sub : 0);
  return {sub, rebaja, itbis, total: base+itbis};
}
function pintar(){
  lines.innerHTML = cart.map((l,i)=>`<li><span><button class="sec" onclick="cambiarCant(${i},-1)">-</button> ${l.cantidad} <button class="sec" onclick="cambiarCant(${i},1)">+</button> ${l.nombre}</span><span>${money(l.precio*l.cantidad)}</span></li>`).join("");
  const t = totalTicket();
  tot.textContent = "Total: " + money(t.total);
  const pagos = pagosActuales();
  const cajaPago = pagos.find(p => p.caja);
  if (cajaPago && pagos.every(p => !p.monto)) {
    cajaPago.el.value = t.total.toFixed(2);
    cajaPago.monto = t.total;
  }
  const fuera = pagos.filter(p => !p.caja).reduce((s,p)=>s+p.monto,0);
  const enCaja = pagos.filter(p => p.caja).reduce((s,p)=>s+p.monto,0);
  cambio.textContent = "Cambio: " + money(Math.max(0, enCaja - Math.max(0, t.total-fuera)));
}
function cambiarCant(i, delta){
  cart[i].cantidad += delta;
  if(cart[i].cantidad <= 0) cart.splice(i,1);
  pintar();
}
desc.oninput = pintar;
let productosCache = [];
async function productos(){
  if(!productosCache.length){
    const data = await api("/api/catalogo");
    productosCache = data.productos;
  }
  return productosCache;
}
function opcionesProductos(lista){
  return lista.map(p => `<option value="${p.id}">${String(p.nombre).replaceAll("<","&lt;")}</option>`).join("");
}
function tabla(headers, filas){
  if(!filas.length) return "<p class='hint'>No hay datos en este período.</p>";
  return `<table><thead><tr>${headers.map(h=>`<th>${h}</th>`).join("")}</tr></thead><tbody>${
    filas.map(f=>`<tr>${f.map(c=>`<td>${c}</td>`).join("")}</tr>`).join("")
  }</tbody></table>`;
}
function ver(nombre){
  document.querySelectorAll(".nav").forEach(b => b.classList.toggle("on", b.dataset.view===nombre));
  document.getElementById("view-venta").style.display = nombre==="venta" ? "block" : "none";
  const formulario = document.getElementById("view-form");
  formulario.style.display = nombre==="venta" ? "none" : "block";
  formulario.classList.toggle("es-dash", nombre==="indicadores");
  const rail = document.querySelector(".rail");
  if (rail) rail.style.display = nombre==="indicadores" ? "none" : "";
  formMsg.textContent = "";
  if(nombre!=="venta") pintarForm(nombre);
}
async function pintarForm(nombre){
  const lista = await productos();
  const opts = opcionesProductos(lista);
  const forms = {
    caja: ["Abrir caja", "Indica el fondo con el que empieza el turno.",
      `<div class="form"><label>Nombre<input id="cajaNombre" value="Caja 1"/></label><label>Fondo<input id="cajaFondo" type="number" step="0.01" value="0"/></label><button onclick="guardarCaja()">Abrir turno</button></div>`],
    ingreso: ["Ingreso de efectivo", "El monto entra al efectivo esperado del turno.",
      `<div class="form"><label>Monto<input id="movMonto" type="number" step="0.01"/></label><label class="wide">Motivo<input id="movMotivo"/></label><button onclick="guardarMov('ingreso')">Registrar ingreso</button></div>`],
    retiro: ["Retiro de efectivo", "No puede superar el efectivo esperado del turno.",
      `<div class="form"><label>Monto<input id="movMonto" type="number" step="0.01"/></label><label class="wide">Motivo<input id="movMotivo"/></label><button onclick="guardarMov('retiro')">Registrar retiro</button></div>`],
    cerrar: ["Cerrar caja", "Cuenta el efectivo del cajón. Si no cuadra, escribe por qué.",
      `<div class="form"><label>Efectivo contado<input id="cajaContado" type="number" step="0.01"/></label><label class="wide">Observación<input id="cajaObs"/></label><button onclick="guardarCierre()">Cerrar turno</button></div>`],
    compra: ["Compras", "Varias líneas entran en la misma compra. Suman existencia y quedan en el kardex.",
      `<div class="form"><label>Proveedor<input id="compProv"/></label><label>Bodega<select id="compBod"><option>Principal</option></select></label><label>Producto<select id="compProd">${opts}</select></label><label>Cantidad<input id="compCant" type="number" step="0.01"/></label><label>Costo<input id="compCosto" type="number" step="0.01"/></label><button class="sec" onclick="agregarLineaCompra()">Agregar línea</button><button onclick="guardarCompra()">Registrar compra</button></div><div id="compLineas"></div>`],
    promo: ["Promociones", "Se aplican en el mostrador cuando el producto está en la promoción.",
      `<div class="form"><label>Nombre<input id="promoNom"/></label><label>Tipo<select id="promoTipo"><option>porcentaje</option><option>fijo</option><option>2x1</option><option>3x2</option></select></label><label>Valor<input id="promoVal" type="number" step="0.01" value="10"/></label><label>Producto<select id="promoProd">${opts}</select></label><button onclick="guardarPromo()">Crear promoción</button></div><div id="promoLista"></div>`],
    dev: ["Devoluciones", "Busca la factura, indica cuánto vuelve y si el dinero sale de la caja.",
      `<div class="form"><label class="wide">Número o id de factura<input id="devQ"/></label><button class="sec" onclick="buscarDev()">Cargar factura</button></div><div id="devLineas"></div>`],
    tr: ["Transferir", "Mueve existencia de una bodega a otra. El stock total no cambia.",
      `<div class="form"><label>Nueva bodega<input id="bodNueva"/></label><button class="sec" onclick="crearBodega()">Crear bodega</button><label>Producto<select id="trProd">${opts}</select></label><label>Origen<select id="trOri"><option>Principal</option></select></label><label>Destino<select id="trDes"><option>Principal</option></select></label><label>Cantidad<input id="trCant" type="number" step="0.01"/></label><button onclick="guardarTr()">Transferir</button></div>`],
    reportes: ["Reportes", "Elige el rango. Si lo dejas vacío, usa los últimos 30 días.",
      `<div class="form"><label>Desde<input id="repDesde" type="date"/></label><label>Hasta<input id="repHasta" type="date"/></label><button class="sec" onclick="pintarReportes()">Ver</button></div><div id="repTabla"></div>`],
    metodos: ["Métodos de pago", "Los activos salen en el ticket. Si afecta caja, el vuelto se calcula sobre ese monto.",
      `<div class="form"><label>Código<input id="metCod"/></label><label>Nombre<input id="metNom"/></label><label>Afecta caja<select id="metCaja"><option value="0">No</option><option value="1">Sí</option></select></label><button onclick="guardarMetodo()">Guardar</button></div><div id="metTabla"></div>`],
    ncf: ["Comprobantes NCF", "Secuencias que se imprimen en cada venta.", ""],
    inventario: ["Inventario", "Marca productos y descarga el PDF de etiquetas con código de barras.",
      `<div class="form"><label class="wide">Buscar<input id="invQ" placeholder="Nombre o código"/></label><button class="sec" onclick="cargarInventario()">Buscar</button><button class="sec" onclick="etiquetasPdf()">Etiquetas PDF</button></div><div class="form" style="margin-top:14px"><label>Nombre<input id="invNom"/></label><label>Precio<input id="invPrecio" type="number" step="0.01"/></label><label>Stock<input id="invStock" type="number" step="0.01" value="0"/></label><label>Código<input id="invCod"/></label><label>Bodega<input id="invBod" value="Principal"/></label><label>Categoría<input id="invCat"/></label><button onclick="guardarProducto()">Crear producto</button></div><div id="invTabla"></div>`],
    kardex: ["Kardex", "Movimientos de un producto: ventas, compras, transferencias y devoluciones.",
      `<div class="form"><label>Producto<select id="kxProd">${opts}</select></label><button class="sec" onclick="cargarKardex()">Ver movimientos</button></div><div id="kxTabla"></div>`],
    historial: ["Historial", "Abre la factura para verla. También puedes imprimir, descargar o anular.",
      `<div class="form"><label>Estado<select id="histEstado"><option value="emitidas">Emitidas</option><option value="anuladas">Anuladas</option><option value="todos">Todas</option></select></label><label class="wide">Motivo de anulación<input id="motivoAnula"/></label><button class="sec" onclick="cargarHistorial()">Cargar</button></div><div id="docVista"></div><div id="histTabla"></div>`],
    cotizaciones: ["Cotizaciones", "Abre el presupuesto para verlo o cambiar cantidades, productos y cliente.", ""],
    indicadores: ["Dashboard", "Ventas, cajeros, pagos e inventario.", ""],
    clientes: ["Clientes", "Nombre, documento, teléfono y correo. Ese correo recibe el comprobante.",
      `<div class="form"><label>Nombre<input id="cliNom"/></label><label>Documento<input id="cliDoc"/></label><label>Teléfono<input id="cliTel"/></label><label>Correo<input id="cliMail"/></label><button onclick="guardarCliente()">Crear cliente</button></div><div id="cliTabla"></div>`],
    usuarios: ["Usuarios", "El administrador ve todo. Un cajero solo entra a los módulos que marques.",
      `<div class="form"><label>Usuario<input id="usuNom"/></label><label>Contraseña<input id="usuPass" type="password"/></label><label>Rol<select id="usuRol"><option>empleado</option><option>user</option><option>admin</option></select></label><button onclick="guardarUsuario()">Crear usuario</button></div><div id="usuMods" class="form"></div><div id="usuTabla"></div>`],
    seguimiento: ["Seguimiento de cajeros", "Cuánto vendió cada cajero y qué turnos descuadraron. Deja una nota de seguimiento.", ""],
    empresa: ["Empresa", "Datos del ticket y el servidor que envía el comprobante por correo.",
      `<div class="form"><label>Nombre<input id="empNom"/></label><label class="wide">Dirección<input id="empDir"/></label><label>RNC<input id="empRnc"/></label><label>Servidor SMTP<input id="smtpHost" placeholder="smtp.hostinger.com"/></label><label>Puerto<input id="smtpPort" value="587"/></label><label>Usuario<input id="smtpUser"/></label><label>Contraseña<input id="smtpPass" type="password" placeholder="Vacía para no cambiarla"/></label><label>Remitente<input id="smtpFrom" placeholder="facturas@tudominio.com"/></label><button onclick="guardarEmpresa()">Guardar</button></div>`]
  };
  const item = forms[nombre];
  if(!item) return;
  formTitle.textContent = item[0];
  formHint.textContent = item[1];
  formBody.innerHTML = item[2];
  if(nombre==="reportes") await pintarReportes();
  if(nombre==="ncf") await pintarNcf();
  if(nombre==="promo") await pintarPromos();
  if(nombre==="inventario") await cargarInventario();
  if(nombre==="historial") await cargarHistorial();
  if(nombre==="cotizaciones") await cargarCotizaciones();
  if(nombre==="indicadores") await cargarIndicadores();
  if(nombre==="clientes") await cargarClientes();
  if(nombre==="usuarios") await cargarUsuarios();
  if(nombre==="empresa") await cargarEmpresa();
  if(nombre==="seguimiento") await cargarSeguimiento();
  if(nombre==="compra"){ lineasCompra = []; await cargarCompras(); }
  if(nombre==="tr") await cargarBodegas();
  if(nombre==="metodos") await cargarPantallaMetodos();
  if(nombre==="dev" && window._devNumero){ devQ.value = window._devNumero; window._devNumero = ""; buscarDev(); }
}
async function guardarCaja(){
  try {
    await api("/api/caja/abrir", {method:"POST", body: JSON.stringify({nombre: cajaNombre.value, fondo: Number(cajaFondo.value||0)})});
    formMsg.style.color = "#86efac";
    formMsg.textContent = "Turno abierto.";
    await refrescar();
  } catch(e){ formMsg.style.color="#fca5a5"; formMsg.textContent = e.message; }
}
async function guardarMov(tipo){
  try {
    await api("/api/caja/movimiento", {method:"POST", body: JSON.stringify({tipo, monto: Number(movMonto.value), motivo: movMotivo.value})});
    formMsg.style.color = "#86efac";
    formMsg.textContent = "Movimiento guardado.";
    await refrescar();
  } catch(e){ formMsg.style.color="#fca5a5"; formMsg.textContent = e.message; }
}
async function guardarCierre(){
  try {
    const r = await api("/api/caja/cerrar", {method:"POST", body: JSON.stringify({contado: Number(cajaContado.value), observaciones: cajaObs.value})});
    formMsg.style.color = "#86efac";
    formMsg.textContent = "Cerrado. Esperado " + money(r.esperado) + " · diferencia " + money(r.diferencia);
    await refrescar();
  } catch(e){ formMsg.style.color="#fca5a5"; formMsg.textContent = e.message; }
}
let lineasCompra = [];
function agregarLineaCompra(){
  const cant = Number(compCant.value);
  const costo = Number(compCosto.value);
  if(!compProd.value || cant<=0) return;
  lineasCompra.push({producto_id: Number(compProd.value), nombre: compProd.selectedOptions[0].textContent, cantidad: cant, costo});
  compLineas.innerHTML = tabla(["Producto","Cantidad","Costo"], lineasCompra.map(l => [l.nombre, l.cantidad, money(l.costo)]));
}
async function guardarCompra(){
  if(!lineasCompra.length) agregarLineaCompra();
  if(!lineasCompra.length) return;
  try {
    const r = await api("/api/compras", {method:"POST", body: JSON.stringify({
      proveedor: compProv.value, bodega: compBod.value,
      lineas: lineasCompra.map(l => ({producto_id:l.producto_id, cantidad:l.cantidad, costo:l.costo}))
    })});
    lineasCompra = [];
    compLineas.innerHTML = "";
    formMsg.style.color = "#86efac";
    formMsg.textContent = "Compra " + r.compra + " registrada, " + r.lineas + " líneas.";
    productosCache = [];
    await cargarCompras();
  } catch(e){ formMsg.style.color="#fca5a5"; formMsg.textContent = e.message; }
}
async function guardarPromo(){
  try {
    await api("/api/promociones", {method:"POST", body: JSON.stringify({
      nombre: promoNom.value, tipo: promoTipo.value, valor: Number(promoVal.value), producto_id: Number(promoProd.value)
    })});
    formMsg.style.color = "#86efac";
    formMsg.textContent = "Promoción creada.";
    await pintarPromos();
  } catch(e){ formMsg.style.color="#fca5a5"; formMsg.textContent = e.message; }
}
async function pintarPromos(){
  const data = await api("/api/promociones");
  promoLista.innerHTML = tabla(["Nombre","Tipo","Valor","Estado"], data.items.map(p => [p.nombre, p.tipo, p.valor, p.activa ? "Activa" : "Inactiva"]));
}
async function buscarDev(){
  try {
    const fac = await api("/api/devolucion?q="+encodeURIComponent(devQ.value));
    devLineas.innerHTML = `<p>${fac.numero} · ${money(fac.total)}</p>` + fac.lineas.map(l =>
      `<div class="form" style="margin-top:8px"><label>${l.nombre}<input data-line="${l.id}" type="number" step="0.01" value="0" max="${l.cantidad}" placeholder="máx ${l.cantidad}"/></label></div>`
    ).join("") + `<div class="form" style="margin-top:8px"><label class="wide">Motivo<input id="devMotivo" value="Devolución en mostrador"/></label><label>Sale de caja<select id="devCaja"><option value="1">Sí, reembolso en efectivo</option><option value="0">No, solo nota de crédito</option></select></label><button onclick="guardarDev()">Registrar nota de crédito</button></div>`;
    formMsg.textContent = "";
  } catch(e){ formMsg.style.color="#fca5a5"; formMsg.textContent = e.message; }
}
async function guardarDev(){
  const lineas = [...devLineas.querySelectorAll("[data-line]")].map(el => ({id: Number(el.dataset.line), cantidad: Number(el.value||0)})).filter(l => l.cantidad>0);
  try {
    const r = await api("/api/devolucion", {method:"POST", body: JSON.stringify({factura: devQ.value, motivo: devMotivo.value, lineas, reembolso_efectivo: devCaja.value==="1"})});
    formMsg.style.color = "#86efac";
    formMsg.textContent = r.mensaje;
    productosCache = [];
  } catch(e){ formMsg.style.color="#fca5a5"; formMsg.textContent = e.message; }
}
async function guardarTr(){
  try {
    await api("/api/transferir", {method:"POST", body: JSON.stringify({
      producto_id: Number(trProd.value), origen: trOri.value, destino: trDes.value, cantidad: Number(trCant.value)
    })});
    formMsg.style.color = "#86efac";
    formMsg.textContent = "Transferencia registrada.";
  } catch(e){ formMsg.style.color="#fca5a5"; formMsg.textContent = e.message; }
}
async function pintarReportes(){
  const desde = document.getElementById("repDesde");
  const hasta = document.getElementById("repHasta");
  const q = (desde && desde.value && hasta && hasta.value) ? ("&desde="+desde.value+"&hasta="+hasta.value) : "";
  const d = await api("/api/reportes?dias=30"+q);
  const html =
    "<h3>Más vendidos</h3>" + tabla(["Producto","Cantidad","Total"], d.vendidos.map(x => [x.nombre, x.cantidad, money(x.total)])) +
    "<h3>Por cajero</h3>" + tabla(["Cajero","Facturas","Total"], d.cajeros.map(x => [x.usuario, x.facturas, money(x.total)])) +
    "<h3>Por forma de pago</h3>" + tabla(["Método","Pagos","Total"], d.pagos.map(x => [x.metodo, x.pagos, money(x.total)]));
  const caja = document.getElementById("repTabla");
  if (caja) caja.innerHTML = html; else formBody.innerHTML = html;
}
async function pintarNcf(){
  const d = await api("/api/ncf");
  formBody.innerHTML = `<div class="form"><label>Tipo<input id="ncfTipo"/></label><label>Prefijo<input id="ncfPref"/></label><label>Siguiente<input id="ncfSig" type="number"/></label><label>Hasta<input id="ncfHasta" type="number"/></label><button onclick="guardarNcf()">Guardar secuencia</button></div>` +
    tabla(["Tipo","Prefijo","Siguiente","Hasta"], d.secuencias.map(x => [x.tipo, x.prefijo, x.siguiente, x.hasta]));
}
async function guardarNcf(){
  try {
    await api("/api/ncf", {method:"POST", body: JSON.stringify({tipo:ncfTipo.value, prefijo:ncfPref.value, siguiente:Number(ncfSig.value), hasta:Number(ncfHasta.value)})});
    formMsg.style.color = "#86efac";
    formMsg.textContent = "Secuencia guardada.";
    await pintarNcf();
  } catch(e){ formMsg.style.color="#fca5a5"; formMsg.textContent = e.message; }
}
async function cargarInventario(){
  const qv = document.getElementById("invQ");
  const d = await api("/api/inventario?q=" + encodeURIComponent(qv ? qv.value : ""));
  invTabla.innerHTML = tabla(["","Producto","Precio","Stock","Código","Bodega","Categoría"], d.items.map(p => [`<input class="etiq" type="checkbox" value="${p.id}"/>`, p.nombre, money(p.precio), p.stock, p.codigo || "—", p.bodega, p.categoria || "—"]));
}
async function bajarPdf(url, nombre, opt){
  const r = await fetch(url, Object.assign({credentials:"same-origin"}, opt||{}));
  if(!r.ok){
    const data = await r.json().catch(()=>({}));
    const d = data.detail;
    throw new Error(typeof d === "string" ? d : "No se pudo generar el PDF");
  }
  const blob = await r.blob();
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = nombre;
  a.click();
  URL.revokeObjectURL(a.href);
}
async function etiquetasPdf(){
  const ids = [...document.querySelectorAll(".etiq:checked")].map(x => Number(x.value));
  if(!ids.length){
    formMsg.style.color = "#fca5a5";
    formMsg.textContent = "Marca al menos un producto.";
    return;
  }
  try {
    await bajarPdf("/api/etiquetas", "etiquetas.pdf", {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({ids})});
    formMsg.style.color = "#86efac";
    formMsg.textContent = "PDF de etiquetas descargado.";
  } catch(e){ formMsg.style.color="#fca5a5"; formMsg.textContent = e.message; }
}
async function guardarProducto(){
  try {
    await api("/api/inventario", {method:"POST", body: JSON.stringify({
      nombre: invNom.value, precio: Number(invPrecio.value), stock: Number(invStock.value||0),
      codigo: invCod.value, bodega: invBod.value, categoria: invCat.value
    })});
    formMsg.style.color = "#86efac";
    formMsg.textContent = "Producto creado.";
    productosCache = [];
    await cargarInventario();
  } catch(e){ formMsg.style.color="#fca5a5"; formMsg.textContent = e.message; }
}
async function cargarKardex(){
  const d = await api("/api/kardex?producto_id=" + Number(kxProd.value));
  kxTabla.innerHTML = tabla(["Fecha","Movimiento","Tipo","Cantidad","Saldo","Bodega"], d.items.map(x => [String(x.fecha||"").slice(0,16), x.descripcion, x.tipo_codigo, x.cantidad, x.balance, x.bodega]));
}
async function cargarHistorial(){
  const est = document.getElementById("histEstado");
  const d = await api("/api/historial?estado=" + encodeURIComponent(est ? est.value : "emitidas"));
  histTabla.innerHTML = tabla(["Número","Fecha","Cliente","Total","Estado","Usuario",""], d.items.map(f => {
    const acciones = `<button class="sec" onclick="verDocumento(${f.id})">Ver</button> <button class="sec" onclick="bajarFactura(${f.id})">PDF</button> <button class="sec" onclick="enviarFactura(${f.id}, '${String(f.email||"").replaceAll("'","")}')">Correo</button> <button class="sec" onclick="reimprimirFactura(${f.id})">Imprimir</button> <button class="sec" onclick="repetirFactura(${f.id})">Repetir</button>` +
      (f.estado==="emitida" ? ` <button class="sec" onclick="anularFactura(${f.id})">Anular</button> <button class="sec" onclick="irDevolver('${f.numero}')">Devolver</button>` : "");
    return [f.numero, String(f.fecha||"").slice(0,16), f.cliente, money(f.total), f.estado, f.usuario || "—", acciones];
  }));
}
async function bajarFactura(id){
  try {
    await bajarPdf("/api/factura/"+id+"/pdf", "comprobante-"+id+".pdf");
    formMsg.style.color = "#86efac";
    formMsg.textContent = "PDF descargado.";
  } catch(e){ formMsg.style.color="#fca5a5"; formMsg.textContent = e.message; }
}
async function enviarFactura(id, emailActual){
  const email = prompt("Correo del cliente", emailActual || "");
  if(!email) return;
  try {
    const r = await api("/api/factura/"+id+"/correo", {method:"POST", body: JSON.stringify({email})});
    formMsg.style.color = "#86efac";
    formMsg.textContent = r.mensaje;
    await cargarHistorial();
  } catch(e){ formMsg.style.color="#fca5a5"; formMsg.textContent = e.message; }
}
async function reimprimirFactura(id){
  const f = await api("/api/factura/"+id);
  imprimirRecibo(f);
}
async function repetirFactura(id){
  const f = await api("/api/factura/"+id);
  cart.length = 0;
  f.lineas.filter(l => l.producto_id).forEach(l => cart.push({id:l.producto_id, nombre:l.nombre, precio:l.precio, cantidad:l.cantidad, itbis:true, combo:false}));
  ver("venta");
  pintar();
}
function irDevolver(numero){
  window._devNumero = numero;
  ver("dev");
}
async function anularFactura(id){
  const motivo = document.getElementById("motivoAnula");
  const texto = motivo ? motivo.value : "";
  if((texto||"").trim().length < 3){
    formMsg.style.color = "#fca5a5";
    formMsg.textContent = "Escribe el motivo en el campo de anulación, mínimo 3 caracteres.";
    return;
  }
  try {
    const r = await api("/api/historial/anular", {method:"POST", body: JSON.stringify({factura_id:id, motivo:texto})});
    formMsg.style.color = "#86efac";
    formMsg.textContent = r.mensaje;
    await cargarHistorial();
  } catch(e){ formMsg.style.color="#fca5a5"; formMsg.textContent = e.message; }
}
let editLineas = [];
function htmlDocumento(f){
  const lineas = tabla(["Producto","Cantidad","Precio","ITBIS","Total"], (f.lineas||[]).map(l => [escDash(l.nombre), l.cantidad, money(l.precio), money(l.itbis), money(l.total)]));
  const editar = f.estado==="cotizacion" ? `<button onclick="editarCotizacion(${f.id})">Editar</button>` : "";
  return `<div class="doc"><h3>${escDash(f.numero)}</h3><p>${escDash(f.cliente)} · ${escDash(f.estado)}${f.ncf ? " · "+escDash(f.ncf) : ""}</p>${lineas}<p>Subtotal ${money(f.subtotal)} · ITBIS ${money(f.itbis)} · <b>Total ${money(f.total)}</b></p>${editar}</div>`;
}
async function verDocumento(id){
  try {
    const f = await api("/api/factura/"+id);
    let caja = document.getElementById("docVista");
    if (!caja) {
      formBody.insertAdjacentHTML("afterbegin", '<div id="docVista"></div>');
      caja = document.getElementById("docVista");
    }
    caja.innerHTML = htmlDocumento(f);
    caja.scrollIntoView({block:"nearest"});
  } catch(e){ formMsg.style.color="#fca5a5"; formMsg.textContent = e.message; }
}
async function editarCotizacion(id){
  try {
    const f = await api("/api/factura/"+id);
    editLineas = (f.lineas||[]).filter(l => l.producto_id).map(l => ({id:l.producto_id, nombre:l.nombre, cantidad:l.cantidad, precio:l.precio}));
    window._editId = id;
    window._editCliente = f.cliente_id || "";
    await pintarEditor();
  } catch(e){ formMsg.style.color="#fca5a5"; formMsg.textContent = e.message; }
}
async function pintarEditor(){
  const lista = await productos();
  let clientes = [];
  try { clientes = (await api("/api/clientes")).items || []; } catch(e) { clientes = []; }
  const opciones = lista.map(p => `<option value="${p.id}">${escDash(p.nombre)}</option>`).join("");
  const clientesHtml = `<option value="">Consumidor final</option>` + clientes.map(c => `<option value="${c.id}" ${String(c.id)===String(window._editCliente)?"selected":""}>${escDash(c.nombre)}</option>`).join("");
  const filas = editLineas.map((l,i) => `<div class="form"><span>${escDash(l.nombre)} · ${money(l.precio)}</span><label>Cantidad<input data-edit="${i}" type="number" step="0.01" value="${l.cantidad}"/></label><button class="sec" onclick="quitarLineaEdit(${i})">Quitar</button></div>`).join("");
  document.getElementById("docVista").innerHTML = `<div class="doc"><h3>Editar cotización</h3><div class="form"><label>Cliente<select id="editCli">${clientesHtml}</select></label><label>Producto<select id="editProd">${opciones}</select></label><label>Cantidad<input id="editCant" type="number" step="0.01" value="1"/></label><button class="sec" onclick="agregarLineaEdit()">Agregar</button></div>${filas}<button onclick="guardarCotizacion()">Guardar cambios</button> <button class="sec" onclick="verDocumento(${window._editId})">Cancelar</button></div>`;
  document.querySelectorAll("[data-edit]").forEach(el => el.oninput = () => { editLineas[Number(el.dataset.edit)].cantidad = Number(el.value||0); });
}
function quitarLineaEdit(i){
  editLineas.splice(i, 1);
  pintarEditor();
}
async function agregarLineaEdit(){
  const lista = await productos();
  const id = Number(editProd.value);
  const cant = Number(editCant.value||0);
  const prod = lista.find(p => p.id===id);
  if (!prod || cant<=0) return;
  const ya = editLineas.find(l => l.id===id);
  if (ya) ya.cantidad = Number(ya.cantidad) + cant;
  else editLineas.push({id, nombre: prod.nombre, cantidad: cant, precio: prod.precio});
  await pintarEditor();
}
async function guardarCotizacion(){
  document.querySelectorAll("[data-edit]").forEach(el => { editLineas[Number(el.dataset.edit)].cantidad = Number(el.value||0); });
  const cliente = document.getElementById("editCli");
  try {
    await api("/api/cotizaciones/"+window._editId, {method:"POST", body: JSON.stringify({
      items: editLineas.filter(l => l.cantidad>0).map(l => ({id:l.id, cantidad:Number(l.cantidad), precio:Number(l.precio)})),
      cliente_id: cliente && cliente.value ? Number(cliente.value) : null,
      lista: "Público"
    })});
    formMsg.style.color = "#86efac";
    formMsg.textContent = "Cotización actualizada.";
    await cargarCotizaciones();
    await verDocumento(window._editId);
  } catch(e){ formMsg.style.color="#fca5a5"; formMsg.textContent = e.message; }
}
async function cargarCotizaciones(){
  const d = await api("/api/historial?estado=presupuestos");
  formBody.innerHTML = `<div id="docVista"></div>` + tabla(["Número","Fecha","Cliente","Total","Estado",""], d.items.map(f => [f.numero, String(f.fecha||"").slice(0,16), f.cliente, money(f.total), f.estado, f.estado==="cotizacion" ? `<button class="sec" onclick="verDocumento(${f.id})">Ver</button> <button onclick="editarCotizacion(${f.id})">Editar</button> <button class="sec" onclick="confirmarCoti(${f.id})">Confirmar</button>` : `<button class="sec" onclick="verDocumento(${f.id})">Ver</button>`]));
}
async function confirmarCoti(id){
  try {
    const r = await api("/api/cotizaciones/confirmar", {method:"POST", body: JSON.stringify({factura_id:id})});
    formMsg.style.color = "#86efac";
    formMsg.textContent = r.mensaje;
    await cargarCotizaciones();
  } catch(e){ formMsg.style.color="#fca5a5"; formMsg.textContent = e.message; }
}
let rangoDash = "semana";
function variacion(n){
  if (n === null || n === undefined) return "Sin período anterior";
  const signo = n > 0 ? "+" : "";
  return signo + n + "% vs período anterior";
}
function graficaVentas(serie){
  const w = 640, h = 220, pad = 28;
  const vals = serie.map(s => Number(s.total) || 0);
  const max = Math.max(...vals, 1);
  const paso = vals.length <= 1 ? 0 : (w - pad * 2) / (vals.length - 1);
  const pts = vals.map((v, i) => {
    const x = pad + (vals.length <= 1 ? (w - pad * 2) / 2 : i * paso);
    const y = h - pad - (v / max) * (h - pad * 2);
    return [x, y];
  });
  const linea = pts.map((p, i) => (i ? "L" : "M") + p[0].toFixed(1) + "," + p[1].toFixed(1)).join(" ");
  const area = linea + ` L${pts[pts.length-1][0].toFixed(1)},${h-pad} L${pts[0][0].toFixed(1)},${h-pad} Z`;
  const marcas = serie.map((s, i) => {
    if (serie.length > 16 && i % 2) return "";
    return `<text x="${pts[i][0].toFixed(1)}" y="${h-8}" text-anchor="middle" fill="#8b95a8" font-size="11">${s.etiqueta}</text>`;
  }).join("");
  return `<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="Ventas del período">
    <defs><linearGradient id="gVentas" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#8b7cf7" stop-opacity="0.45"/><stop offset="100%" stop-color="#8b7cf7" stop-opacity="0"/></linearGradient></defs>
    <path d="${area}" fill="url(#gVentas)"/>
    <path d="${linea}" fill="none" stroke="#c4b5fd" stroke-width="3" stroke-linejoin="round"/>
    ${marcas}
  </svg>`;
}
function escDash(texto){
  return String(texto || "").replaceAll("&","&amp;").replaceAll("<","&lt;");
}
function barrasDash(items, color){
  if (!items.length) return "<p class='hint'>No hay datos en este período.</p>";
  const max = Math.max(...items.map(i => Number(i.total) || 0), 1);
  return items.map(i => `<div class="barrow"><span>${escDash(i.nombre)}</span><div class="track"><div style="width:${Math.max(4, (Number(i.total)||0)/max*100)}%;background:${color}"></div></div><b>${money(i.total)}</b></div>`).join("");
}
function donaPagos(pagos){
  const colores = ["#6d5ef5", "#38bdf8", "#34d399", "#fb7185", "#f5a524", "#94a3b8"];
  const total = pagos.reduce((s, p) => s + (Number(p.total) || 0), 0);
  if (!total) return "<p class='hint'>No hay cobros en este período.</p>";
  let cursor = 0;
  const partes = pagos.map((p, i) => {
    const pct = (Number(p.total) || 0) / total * 100;
    const inicio = cursor;
    cursor += pct;
    return `${colores[i % colores.length]} ${inicio.toFixed(2)}% ${cursor.toFixed(2)}%`;
  });
  const leyenda = pagos.map((p, i) => `<span><b style="color:${colores[i % colores.length]}">●</b> ${escDash(p.metodo)} ${money(p.total)}</span>`).join("");
  return `<div class="dona" style="background:radial-gradient(circle,#141a27 0 46%,transparent 47%),conic-gradient(${partes.join(",")})"></div><div class="leyenda">${leyenda}</div>`;
}
function dashRango(rango){
  rangoDash = rango;
  cargarIndicadores();
}
async function cargarIndicadores(){
  const d = await api("/api/indicadores?rango=" + encodeURIComponent(rangoDash));
  const activo = (id) => rangoDash === id ? "on" : "";
  const caja = d.caja
    ? `${d.caja.nombre} abierta por ${d.caja.usuario || "—"} · fondo ${money(d.caja.fondo)}`
    : "No hay turno abierto";
  formBody.innerHTML = `
    <div class="dash-head">
      <div><h2>Dashboard</h2><p>${d.etiqueta}</p></div>
      <div class="rangos">
        <button class="${activo("dia")}" onclick="dashRango('dia')">Día</button>
        <button class="${activo("semana")}" onclick="dashRango('semana')">Semana</button>
        <button class="${activo("mes")}" onclick="dashRango('mes')">Mes</button>
      </div>
    </div>
    <div class="kpis">
      <div class="kpi violeta"><span>Ventas</span><b>${money(d.ventas)}</b><em>${variacion(d.delta_ventas)}</em></div>
      <div class="kpi azul"><span>Facturas</span><b>${d.facturas}</b><em>${variacion(d.delta_facturas)}</em></div>
      <div class="kpi verde"><span>Ticket promedio</span><b>${money(d.ticket)}</b><em>${variacion(d.delta_ticket)}</em></div>
      <div class="kpi rosa"><span>ITBIS</span><b>${money(d.itbis)}</b><em>${variacion(d.delta_itbis)}</em></div>
    </div>
    <div class="dash-grid">
      <div class="dash-card"><h3>Ventas en el tiempo</h3>${graficaVentas(d.serie || [])}</div>
      <div class="dash-card"><h3>Formas de pago</h3>${donaPagos(d.pagos || [])}</div>
      <div class="dash-card"><h3>Más vendidos</h3>${barrasDash((d.productos||[]).map(p => ({nombre:p.nombre, total:p.total})), "#6d5ef5")}</div>
      <div class="dash-card"><h3>Por cajero</h3>${barrasDash((d.cajeros||[]).map(c => ({nombre:c.usuario, total:c.total})), "#38bdf8")}</div>
    </div>
    <div class="dash-grid">
      <div class="dash-card side-stats">
        <h3>Operación</h3>
        <div><span>Devoluciones</span><b>${d.devoluciones} · ${money(d.devoluciones_total)}</b></div>
        <div><span>Facturas anuladas</span><b>${d.anuladas}</b></div>
        <div><span>Caja</span><b>${caja}</b></div>
      </div>
      <div class="dash-card side-stats">
        <h3>Inventario</h3>
        <div><span>Valor al costo</span><b>${money(d.valor_costo)}</b></div>
        <div><span>Valor a precio de venta</span><b>${money(d.valor_venta)}</b></div>
      </div>
    </div>`;
}
async function cargarClientes(){
  const d = await api("/api/clientes");
  cliTabla.innerHTML = tabla(["Nombre","Documento","Teléfono","Correo"], d.items.map(c => [c.nombre, c.documento || "—", c.telefono || "—", c.email || "—"]));
}
async function guardarCliente(){
  try {
    await api("/api/clientes", {method:"POST", body: JSON.stringify({nombre: cliNom.value, documento: cliDoc.value, telefono: cliTel.value, email: cliMail.value})});
    formMsg.style.color = "#86efac";
    formMsg.textContent = "Cliente creado.";
    await cargarClientes();
  } catch(e){ formMsg.style.color="#fca5a5"; formMsg.textContent = e.message; }
}
async function cargarUsuarios(){
  const d = await api("/api/usuarios");
  usuMods.innerHTML = d.modulos.map(m => `<label><input type="checkbox" class="mod" value="${m}" ${m==="mostrador"?"checked":""}/> ${m}</label>`).join("");
  usuTabla.innerHTML = tabla(["Usuario","Rol","Módulos"], d.items.map(u => [u.username, u.role, (u.modulos||[]).join(", ")]));
}
async function guardarUsuario(){
  const modulos = [...document.querySelectorAll(".mod:checked")].map(x => x.value);
  try {
    await api("/api/usuarios", {method:"POST", body: JSON.stringify({username: usuNom.value, password: usuPass.value, role: usuRol.value, modulos})});
    formMsg.style.color = "#86efac";
    formMsg.textContent = "Usuario creado con sus módulos.";
    await cargarUsuarios();
  } catch(e){ formMsg.style.color="#fca5a5"; formMsg.textContent = e.message; }
}
async function cargarSeguimiento(){
  const d = await api("/api/seguimiento");
  const cajeros = tabla(["Cajero","Facturas","Generado"], d.cajeros.map(c => [c.usuario, c.facturas, money(c.total)]));
  const turnos = tabla(["Turno","Cajero","Estado","Diferencia","Seguimiento","Nota"], d.turnos.map(t => [
    t.id, t.usuario || "—", t.estado, money(t.diferencia), t.seguimiento, t.nota || t.observaciones || "—"
  ]));
  formBody.innerHTML = "<h3>Lo que generó cada cajero en 30 días</h3>" + cajeros +
    "<h3>Turnos y descuadre</h3>" + turnos +
    `<div class="form"><label>Turno<input id="segId" type="number"/></label><label>Estado<select id="segEst"><option>pendiente</option><option>en_seguimiento</option><option>resuelto</option></select></label><label class="wide">Nota<input id="segNota"/></label><button onclick="guardarSeguimiento()">Guardar seguimiento</button></div>`;
}
async function guardarSeguimiento(){
  try {
    await api("/api/seguimiento", {method:"POST", body: JSON.stringify({cierre_id:Number(segId.value), estado:segEst.value, nota:segNota.value})});
    formMsg.style.color = "#86efac";
    formMsg.textContent = "Seguimiento guardado.";
    await cargarSeguimiento();
  } catch(e){ formMsg.style.color="#fca5a5"; formMsg.textContent = e.message; }
}
async function cargarCompras(){
  const d = await api("/api/compras/lista");
  const sel = document.getElementById("compProv");
  if (sel && d.proveedores.length) {
    sel.outerHTML = `<select id="compProv">${d.proveedores.map(p => `<option>${String(p.nombre).replaceAll("<","&lt;")}</option>`).join("")}</select>`;
  }
  const bod = document.getElementById("compBod");
  if (bod && d.bodegas) bod.innerHTML = d.bodegas.map(b => `<option>${b}</option>`).join("");
  const extra = document.getElementById("compLista");
  if (!extra) {
    formBody.insertAdjacentHTML("beforeend", '<div id="compLista"></div>');
  }
  document.getElementById("compLista").innerHTML = tabla(["Fecha","Proveedor","Total","Nota"], d.items.map(c => [String(c.fecha||"").slice(0,16), c.proveedor, money(c.total), c.nota || "—"]));
}
function opcionesBodega(nombres, actual){
  const lista = nombres && nombres.length ? nombres : ["Principal"];
  return lista.map(b => `<option ${b===actual?"selected":""}>${b}</option>`).join("");
}
async function cargarBodegas(){
  const d = await api("/api/bodegas");
  if (document.getElementById("trOri")) trOri.innerHTML = opcionesBodega(d.bodegas, "Principal");
  if (document.getElementById("trDes")) trDes.innerHTML = opcionesBodega(d.bodegas, d.bodegas.find(b => b!=="Principal") || "Principal");
  const extra = document.getElementById("bodLista");
  if (!extra) formBody.insertAdjacentHTML("beforeend", '<div id="bodLista"></div>');
  document.getElementById("bodLista").innerHTML = tabla(["Producto","Bodega","Cantidad"], d.items.map(x => [x.producto, x.bodega, x.cantidad]));
}
async function crearBodega(){
  try {
    await api("/api/bodegas", {method:"POST", body: JSON.stringify({nombre: bodNueva.value})});
    bodNueva.value = "";
    formMsg.style.color = "#86efac";
    formMsg.textContent = "Bodega creada.";
    await cargarBodegas();
  } catch(e){ formMsg.style.color="#fca5a5"; formMsg.textContent = e.message; }
}
async function cargarPantallaMetodos(){
  const d = await api("/api/metodos");
  metTabla.innerHTML = tabla(["Código","Nombre","Afecta caja","Activo"], d.items.map(m => [m.codigo, m.nombre, m.afecta_caja ? "Sí" : "No", m.activo ? "Sí" : "No"]));
}
async function guardarMetodo(){
  try {
    await api("/api/metodos", {method:"POST", body: JSON.stringify({codigo: metCod.value, nombre: metNom.value, afecta_caja: metCaja.value==="1", activo: true})});
    formMsg.style.color = "#86efac";
    formMsg.textContent = "Método guardado.";
    await cargarPantallaMetodos();
    await cargarMetodos();
  } catch(e){ formMsg.style.color="#fca5a5"; formMsg.textContent = e.message; }
}
async function cargarEmpresa(){
  const d = await api("/api/empresa");
  empNom.value = d.nombre || "";
  empDir.value = d.direccion || "";
  empRnc.value = d.rnc || "";
  smtpHost.value = d.smtp_host || "";
  smtpPort.value = d.smtp_port || "587";
  smtpUser.value = d.smtp_user || "";
  smtpFrom.value = d.smtp_from || "";
  smtpPass.value = "";
}
async function guardarEmpresa(){
  try {
    await api("/api/empresa", {method:"POST", body: JSON.stringify({
      nombre: empNom.value, direccion: empDir.value, rnc: empRnc.value,
      smtp_host: smtpHost.value, smtp_port: smtpPort.value, smtp_user: smtpUser.value,
      smtp_password: smtpPass.value, smtp_from: smtpFrom.value
    })});
    formMsg.style.color = "#86efac";
    formMsg.textContent = "Datos de empresa guardados.";
  } catch(e){ formMsg.style.color="#fca5a5"; formMsg.textContent = e.message; }
}
async function cobrar(){
  msg.textContent = "";
  try {
    const nivel = Number(lista.value);
    const nombres = ["","Público","Mayorista","VIP","Especial"];
    const payload = {
      items: cart.map(l=>({id:l.id, cantidad:l.cantidad, nivel, combo:!!l.combo})),
      pagos: pagosActuales().filter(p => p.monto>0).map(p => ({codigo:p.codigo, monto:p.monto})),
      lista: nombres[nivel], descuento: Number(desc.value||0),
      cliente_id: cliVenta.value ? Number(cliVenta.value) : null,
      comprobante: tipoComp.value
    };
    const data = await api("/api/vender", {method:"POST", body: JSON.stringify(payload)});
    imprimirRecibo(data);
    cart.length = 0; desc.value = 0; pintar();
    msg.style.color = "#86efac";
    msg.textContent = data.numero + (data.ncf? " · "+data.ncf : "") + " · " + money(data.total) + " · cambio " + money(data.cambio);
    await cargar();
  } catch(e){ msg.style.color="#fca5a5"; msg.textContent = e.message; }
}
q.addEventListener("keydown", ev=>{ if(ev.key==="Enter") cargar(); });
function imprimirRecibo(data){
  const emp = window._empresa || {};
  const ancho = (document.getElementById("anchoTicket") || {}).value || "80";
  recibo.style.width = ancho === "58" ? "58mm" : "80mm";
  const lineas = (data.lineas||[]).map(l => `<tr><td>${l.cantidad} ${l.nombre}</td><td>${money(l.total)}</td></tr>`).join("");
  recibo.innerHTML = `<h2>${emp.nombre || "Factura"}</h2><p>${emp.direccion || ""}<br>RNC ${emp.rnc || "—"}</p><p>${data.numero}<br>${data.ncf || ""}<br>${data.cliente || ""}</p><table>${lineas}</table><p>ITBIS ${money(data.itbis)}<br><b>Total ${money(data.total)}</b><br>Cambio ${money(data.cambio||0)}</p>`;
  window.print();
}
async function cargarMetodos(){
  const d = await api("/api/metodos");
  const activos = d.items.filter(m => m.activo);
  pagosDyn.innerHTML = activos.map(m => `<label>${m.nombre}<input class="pago" data-codigo="${m.codigo}" data-caja="${m.afecta_caja ? 1 : 0}" type="number" step="0.01" value="0"/></label>`).join("");
  document.querySelectorAll(".pago").forEach(el => el.oninput = pintar);
  pintar();
}
async function crearClienteTicket(){
  try {
    const r = await api("/api/clientes/rapido", {method:"POST", body: JSON.stringify({nombre: cliNuevo.value, documento: cliNuevoDoc.value, telefono: "", email: cliNuevoMail.value})});
    await cargarClientesVenta();
    cliVenta.value = String(r.id);
    cliNuevo.value = "";
    cliNuevoDoc.value = "";
    cliNuevoMail.value = "";
    msg.style.color = "#86efac";
    msg.textContent = "Cliente agregado al ticket.";
  } catch(e){ msg.style.color="#fca5a5"; msg.textContent = e.message; }
}
async function apartar(){
  if(!cart.length) return;
  const etiqueta = (esperaNom.value || "Mesa").trim();
  if(!etiqueta) return;
  try {
    await api("/api/espera", {method:"POST", body: JSON.stringify({
      etiqueta, items: cart, total: Number(String(tot.textContent).replace(/[^0-9.]/g,"")) || 0,
      cliente_id: cliVenta.value ? Number(cliVenta.value) : null
    })});
    cart.length = 0; pintar();
    msg.style.color = "#86efac";
    msg.textContent = "Ticket apartado: " + etiqueta;
    await cargarEsperas();
  } catch(e){ msg.style.color="#fca5a5"; msg.textContent = e.message; }
}
async function cargarEsperas(){
  try {
    const d = await api("/api/espera");
    esperas.innerHTML = d.items.map(x => `<button class="sec" onclick="tomarEspera(${x.id})">${x.etiqueta} · ${money(x.total)}</button>`).join(" ");
  } catch(e) { esperas.innerHTML = ""; }
}
async function tomarEspera(id){
  const d = await api("/api/espera/"+id+"/tomar", {method:"POST"});
  cart.length = 0;
  (d.items||[]).forEach(it => cart.push(it));
  if(d.cliente_id) cliVenta.value = String(d.cliente_id);
  pintar();
  await cargarEsperas();
}
async function guardarCotizacion(){
  if(!cart.length) return;
  try {
    const nivel = Number(lista.value);
    const nombres = ["","Público","Mayorista","VIP","Especial"];
    const data = await api("/api/cotizaciones", {method:"POST", body: JSON.stringify({
      items: cart.map(l=>({id:l.id, cantidad:l.cantidad, nivel, combo:!!l.combo})),
      lista: nombres[nivel], cliente_id: cliVenta.value ? Number(cliVenta.value) : null
    })});
    cart.length = 0; pintar();
    msg.style.color = "#86efac";
    msg.textContent = "Cotización " + data.numero;
  } catch(e){ msg.style.color="#fca5a5"; msg.textContent = e.message; }
}
</script>
</body>
</html>
"""
