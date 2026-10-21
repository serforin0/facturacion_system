"""
Mostrador web. Usa la misma base SQLite que el programa de escritorio.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import sqlite3
import secrets
import time
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
_SESION_SEGUNDOS = 12 * 3600


def _token_sesion(usuario: str) -> str:
    expira = int(time.time()) + _SESION_SEGUNDOS
    cuerpo = base64.urlsafe_b64encode(
        json.dumps({"u": usuario, "e": expira}, separators=(",", ":")).encode()
    ).decode().rstrip("=")
    firma = hmac.new(_secret().encode(), cuerpo.encode(), hashlib.sha256).hexdigest()
    return f"{cuerpo}.{firma}"


def _usuario_de_token(token: str) -> str | None:
    guardado = _sessions.get(token)
    if guardado:
        return guardado
    if "." not in token:
        return None
    cuerpo, firma = token.rsplit(".", 1)
    buena = hmac.new(_secret().encode(), cuerpo.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(firma, buena):
        return None
    relleno = "=" * (-len(cuerpo) % 4)
    try:
        data = json.loads(base64.urlsafe_b64decode(cuerpo + relleno))
        if int(data["e"]) < time.time():
            return None
        usuario = str(data["u"]).strip()
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    return usuario or None


def _user(request: Request) -> str:
    token = request.cookies.get("sesion") or ""
    user = _usuario_de_token(token)
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


def _itbis_incluido(base: Database) -> bool:
    return (base.get_config("precios_incluyen_itbis", "0") or "0") == "1"


def _partir_itbis(monto: float, aplica: bool, incluido: bool) -> tuple[float, float]:
    """Devuelve (base sin ITBIS, ITBIS) de un monto de línea."""
    monto = round(max(0.0, float(monto or 0)), 2)
    if not aplica:
        return monto, 0.0
    if incluido:
        neto = round(monto / (1 + TASA_ITBIS), 2)
        return neto, round(monto - neto, 2)
    return monto, round(monto * TASA_ITBIS, 2)


def _armar_lineas(cur, items: list[ItemIn], descuentos: tuple[float, float, float] = (2.0, 5.0, 10.0), incluido: bool = False):
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
        precio = round(float(item.precio), 2) if item.precio is not None else _precio(row, item.nivel, descuentos)
        bruto = round(precio * item.cantidad, 2)
        neto, itbis = _partir_itbis(bruto, bool(int(row[8] or 1)), incluido)
        subtotal += neto
        impuesto += itbis
        det.append((row[0], row[1], item.cantidad, precio, itbis, round(neto + itbis, 2)))
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
    "cobros", "vendedores", "terminales",
]


def _precio(row, nivel: int, descuentos: tuple[float, float, float] = (2.0, 5.0, 10.0)) -> float:
    base = float(row[2] or 0)
    guardados = [base, row[12], row[13], row[14]]
    nivel = max(1, min(4, int(nivel or 1)))
    try:
        p = float(guardados[nivel - 1] or 0)
    except (TypeError, ValueError, IndexError):
        p = 0.0
    if p <= 0 and nivel > 1 and base > 0:
        p = round(base * (1 - descuentos[nivel - 2] / 100.0), 2)
    if p <= 0:
        p = base
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
    a_credito: bool = False
    vendedor_id: int | None = None
    observaciones: str = ""
    id_local: str = ""
    terminal_id: int | None = None
    fecha_local: str = ""


class MovimientoIn(BaseModel):
    tipo: str
    monto: float
    motivo: str
    id: int | None = None


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
    activa: bool = True


class DevolucionIn(BaseModel):
    factura: str
    motivo: str
    lineas: list[dict]
    reembolso_efectivo: bool = False
    destino: str = ""


class MotivoIn(BaseModel):
    motivo: str


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


SW_JS = r"""
const CACHE = "pos-v1";
const API_GUARDADAS = ["/api/estado", "/api/catalogo", "/api/metodos", "/api/clientes", "/api/vendedores", "/api/terminales", "/api/espera"];
self.addEventListener("install", ev => {
  ev.waitUntil(caches.open(CACHE).then(c => c.add("/")).then(() => self.skipWaiting()));
});
self.addEventListener("activate", ev => {
  ev.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener("message", ev => {
  if (ev.data === "limpiar") {
    caches.open(CACHE).then(c => c.keys().then(ks => ks.forEach(r => { if (new URL(r.url).pathname.startsWith("/api/")) c.delete(r); })));
  }
});
async function redPrimero(req, ignorarBusqueda) {
  const cache = await caches.open(CACHE);
  try {
    const resp = await fetch(req);
    if (resp.ok) cache.put(req, resp.clone());
    return resp;
  } catch (e) {
    const guardada = await cache.match(req) || (ignorarBusqueda ? await cache.match(req, {ignoreSearch: true}) : null);
    if (guardada) return guardada;
    throw e;
  }
}
async function cachePrimero(req) {
  const cache = await caches.open(CACHE);
  const guardada = await cache.match(req);
  if (guardada) return guardada;
  const resp = await fetch(req);
  if (resp.ok) cache.put(req, resp.clone());
  return resp;
}
self.addEventListener("fetch", ev => {
  const req = ev.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;
  if (req.mode === "navigate" || url.pathname === "/") {
    ev.respondWith(redPrimero(new Request("/"), false));
  } else if (url.pathname.startsWith("/estaticos/")) {
    ev.respondWith(cachePrimero(req));
  } else if (API_GUARDADAS.includes(url.pathname)) {
    ev.respondWith(redPrimero(req, url.pathname === "/api/catalogo"));
  }
});
"""


@app.get("/sw.js")
def service_worker():
    return Response(SW_JS, media_type="application/javascript", headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})


@app.post("/api/login")
def login(body: LoginIn):
    role = db().validate_user(body.username.strip(), body.password)
    if not role:
        raise HTTPException(status_code=401, detail="Usuario o contraseña incorrectos")
    token = _token_sesion(body.username.strip())
    _sessions[token] = body.username.strip()
    resp = JSONResponse({"ok": True, "usuario": body.username.strip(), "rol": role})
    resp.set_cookie(
        "sesion",
        token,
        httponly=True,
        samesite="lax",
        path="/",
        max_age=_SESION_SEGUNDOS,
    )
    return resp


@app.post("/api/logout")
def logout(request: Request):
    _sessions.pop(request.cookies.get("sesion") or "", None)
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("sesion", path="/")
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
        "itbis_incluido": _itbis_incluido(base),
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
    base = db()
    d2, d3, d4 = _descuentos_mayor(base)
    conn = base.get_connection()
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

    def _nivel(base_precio: float, guardado, porcentaje: float) -> float:
        monto = float(guardado or 0)
        if monto > 0:
            return monto
        return round(base_precio * (1 - porcentaje / 100.0), 2)

    productos = []
    for row in cur.fetchall():
        base_precio = float(row[2] or 0)
        productos.append(
            {
                "id": row[0],
                "nombre": row[1],
                "precio": base_precio,
                "precio_2": _nivel(base_precio, row[12], d2),
                "precio_3": _nivel(base_precio, row[13], d3),
                "precio_4": _nivel(base_precio, row[14], d4),
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
    return {
        "categorias": categorias,
        "productos": productos,
        "combos": combos,
        "descuentos": {"p2": d2, "p3": d3, "p4": d4},
    }


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


def _venta_previa(base: Database, id_local: str) -> dict | None:
    """Una venta hecha sin conexión puede llegar dos veces: se devuelve la ya guardada."""
    conn = base.get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT f.id, f.numero, IFNULL(f.ncf,''), f.subtotal, f.impuesto_total, f.total, IFNULL(c.nombre,'Consumidor final'),
               f.fecha_vencimiento, IFNULL((SELECT SUM(p.monto) FROM pagos_factura p WHERE p.factura_id=f.id),0)
        FROM facturas f LEFT JOIN clientes c ON c.id=f.cliente_id WHERE f.id_local=?
        """,
        (id_local,),
    )
    fila = cur.fetchone()
    if not fila:
        conn.close()
        return None
    cur.execute("SELECT descripcion, cantidad, total_linea FROM factura_detalle WHERE factura_id=?", (fila[0],))
    lineas = [{"nombre": d, "cantidad": c, "total": t} for d, c, t in cur.fetchall()]
    conn.close()
    return {
        "ok": True, "duplicada": True, "id": fila[0], "numero": fila[1], "ncf": fila[2],
        "ecf": _ecf_de_factura(fila[0]), "subtotal": fila[3], "itbis": fila[4], "total": fila[5],
        "cliente": fila[6], "cambio": 0, "pendiente": round(max(0.0, float(fila[5] or 0) - float(fila[8] or 0)), 2),
        "vencimiento": fila[7], "lineas": lineas,
    }


def _terminal_para_vender(base: Database, terminal_id: int | None, usuario: str) -> dict | None:
    if not terminal_id:
        return None
    conn = base.get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, nombre, prefijo, IFNULL(bodega,''), vendedor_id, IFNULL(usuarios,''), activo FROM terminales WHERE id=?",
        (terminal_id,),
    )
    fila = cur.fetchone()
    conn.close()
    if not fila or not int(fila[6]):
        raise HTTPException(status_code=400, detail="Esta terminal no existe o está desactivada. Elige otra en el mostrador")
    permitidos = [u.strip().lower() for u in fila[5].split(",") if u.strip()]
    if permitidos and usuario.lower() not in permitidos:
        raise HTTPException(status_code=403, detail=f"Tu usuario no está autorizado para vender en {fila[1]}")
    return {"id": fila[0], "nombre": fila[1], "prefijo": fila[2], "bodega": fila[3] or None, "vendedor_id": fila[4]}


@app.post("/api/vender")
def vender(request: Request, body: VentaIn):
    usuario = _exigir(request, "mostrador")
    base = db()
    caja = base.fetch_caja_abierta_row()
    if caja is None:
        raise HTTPException(status_code=400, detail="Abre el turno de caja antes de cobrar")
    if not body.items:
        raise HTTPException(status_code=400, detail="El ticket está vacío")
    id_local = (body.id_local or "").strip()[:64] or None
    if id_local:
        previa = _venta_previa(base, id_local)
        if previa:
            return previa
    terminal = _terminal_para_vender(base, body.terminal_id, usuario)

    lineas = []
    subtotal = 0.0
    impuesto = 0.0
    cliente_nombre = "Consumidor final"
    descuentos = _descuentos_mayor(base)
    incluido = _itbis_incluido(base)
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
                neto, itbis = _partir_itbis(bruto, True, incluido)
                lineas.append((None, combo[0], item.cantidad, precio, neto, itbis, desc, comps))
                subtotal += neto
                impuesto += itbis
                continue
            cur.execute(SQL_PRODUCTOS + " WHERE id=?", (item.id,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=400, detail=f"Producto {item.id} no existe")
            precio = _precio(row, item.nivel, descuentos)
            bruto = round(precio * item.cantidad, 2)
            desc = _descuento_promo(cur, row[0], item.cantidad, precio, bruto)
            neto, itbis = _partir_itbis(bruto - desc, bool(int(row[8] or 1)), incluido)
            if int(row[9] or 1) == 0 and item.cantidad > float(row[5] or 0):
                raise HTTPException(status_code=400, detail=f"Sin stock de {row[1]}")
            lineas.append((row, row[1], item.cantidad, precio, neto, itbis, desc, None))
            subtotal += neto
            impuesto += itbis
        descuento = round(max(0.0, float(body.descuento or 0)), 2)
        if incluido:
            bruto_total = round(subtotal + impuesto, 2)
            descuento = min(descuento, bruto_total)
            if bruto_total > 0 and descuento > 0:
                factor = (bruto_total - descuento) / bruto_total
                impuesto = round(impuesto * factor, 2)
                subtotal = round(bruto_total - descuento - impuesto, 2)
        else:
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
            if codigo != "saldo_favor" and codigo not in activos:
                raise HTTPException(status_code=400, detail=f"El método {codigo} no está activo")
        cliente_row = None
        if body.cliente_id:
            cur.execute(
                "SELECT nombre, IFNULL(activo,1), IFNULL(limite_credito,0), IFNULL(dias_credito,30) FROM clientes WHERE id=?",
                (body.cliente_id,),
            )
            cliente_row = cur.fetchone()
            if not cliente_row:
                raise HTTPException(status_code=400, detail="El cliente no existe")
            if not int(cliente_row[1]):
                raise HTTPException(status_code=400, detail="El cliente está desactivado")
        usar_saldo = round(sum(monto for codigo, monto in entradas if codigo == "saldo_favor"), 2)
        if usar_saldo > 0:
            if not body.cliente_id:
                raise HTTPException(status_code=400, detail="Para usar saldo a favor elige el cliente")
            disponible = base.saldo_favor_cliente(body.cliente_id, conn=conn)
            if usar_saldo > disponible + 0.009:
                raise HTTPException(status_code=400, detail=f"El cliente solo tiene {disponible:.2f} de saldo a favor")
        fuera = round(sum(monto for codigo, monto in entradas if codigo not in afectan), 2)
        entregado_caja = round(sum(monto for codigo, monto in entradas if codigo in afectan), 2)
        if fuera > total + 0.02:
            raise HTTPException(status_code=400, detail="Los pagos que no entran a caja no pueden superar el total")
        por_cubrir_caja = round(max(0.0, total - fuera), 2)
        ef_guardar = round(min(entregado_caja, por_cubrir_caja), 2)
        cambio = round(max(0.0, entregado_caja - por_cubrir_caja), 2)
        pendiente = round(max(0.0, total - fuera - ef_guardar), 2)
        vencimiento = None
        if pendiente > 0.009:
            if not body.a_credito:
                raise HTTPException(status_code=400, detail="El pago no cubre el total. Si es fiado, marca «Venta a crédito»")
            if not cliente_row:
                raise HTTPException(status_code=400, detail="La venta a crédito necesita un cliente")
            limite = float(cliente_row[2] or 0)
            if limite <= 0:
                raise HTTPException(status_code=400, detail=f"{cliente_row[0]} no tiene crédito aprobado. Ponle un límite en Clientes")
            deuda = base.deuda_cliente(body.cliente_id, conn=conn)
            if deuda + pendiente > limite + 0.009:
                raise HTTPException(
                    status_code=400,
                    detail=f"Supera el límite de crédito: debe {deuda:,.2f}, esta venta deja {pendiente:,.2f} y el límite es {limite:,.2f}",
                )
            vencimiento = (datetime.now() + timedelta(days=int(cliente_row[3] or 30))).strftime("%Y-%m-%d")
        vendedor_id = None
        pedido_vendedor = body.vendedor_id or (terminal["vendedor_id"] if terminal else None)
        if pedido_vendedor:
            cur.execute("SELECT id FROM vendedores WHERE id=? AND IFNULL(activo,1)=1", (pedido_vendedor,))
            if cur.fetchone():
                vendedor_id = int(pedido_vendedor)
            elif body.vendedor_id:
                raise HTTPException(status_code=400, detail="El vendedor no existe o está inactivo")

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
        if cliente_row:
            cliente_nombre = cliente_row[0]
        encf = None
        tipo_electronico = None
        if (base.get_config("ecf_activo", "0") or "0") == "1":
            from ecf import dgii as _dgii
            tipo_electronico = _dgii.tipo_ecf_de(tipo)
        if tipo_electronico:
            reserva = base.tomar_siguiente_encf(tipo_electronico, conn)
            if reserva is None:
                raise HTTPException(status_code=400, detail=f"No hay e-NCF disponibles del tipo {tipo_electronico}. Cárgalos en Facturación electrónica")
            encf = reserva[0]
            ncf = encf
        else:
            ncf = base.tomar_siguiente_ncf(tipo, conn=conn)
        if terminal:
            while True:
                cur.execute("SELECT siguiente FROM terminales WHERE id=?", (terminal["id"],))
                consecutivo = int(cur.fetchone()[0] or 1)
                cur.execute("UPDATE terminales SET siguiente=? WHERE id=?", (consecutivo + 1, terminal["id"]))
                numero = f"{terminal['prefijo']}-{consecutivo:06d}"
                cur.execute("SELECT 1 FROM facturas WHERE numero=?", (numero,))
                if cur.fetchone() is None:
                    break
        else:
            base_num = "F-" + datetime.now().strftime("%Y%m%d%H%M%S")
            numero = base_num
            n = 2
            while True:
                cur.execute("SELECT 1 FROM facturas WHERE numero=?", (numero,))
                if cur.fetchone() is None:
                    break
                numero = f"{base_num}-{n}"
                n += 1
        observaciones = (body.observaciones or "").strip()
        if body.fecha_local.strip():
            observaciones = f"Vendida sin conexión el {body.fecha_local.strip()[:19]}" + (f". {observaciones}" if observaciones else "")
        cur.execute(
            """
            INSERT INTO facturas
                (numero, tipo_comprobante, cliente_id, subtotal, descuento_total,
                 impuesto_total, total, estado, usuario, caja, condicion_pago_id,
                 fecha_vencimiento, moneda, tasa_cambio, cierre_id, ncf, lista_precio,
                 vendedor_id, observaciones, encf, precio_incluye_itbis, id_local, terminal_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'emitida', ?, NULL, NULL, ?, 'DOP', 1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (numero, tipo, body.cliente_id, round(subtotal, 2), descuento, round(impuesto, 2), total, usuario,
             vencimiento, caja[0], ncf, body.lista, vendedor_id, observaciones[:500] or None,
             encf, 1 if incluido else 0, id_local, terminal["id"] if terminal else None),
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
                bod = (terminal and terminal["bodega"]) or (cur.fetchone()[0] or "").strip() or None
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
        ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for tipo_pago, monto in guardados:
            if monto > 0:
                cur.execute(
                    """
                    INSERT INTO pagos_factura (factura_id, tipo_pago, monto, cierre_id, fecha, usuario, es_abono)
                    VALUES (?,?,?,?,?,?,0)
                    """,
                    (factura_id, tipo_pago, round(monto, 2), caja[0], ahora, usuario),
                )
        if usar_saldo > 0:
            cur.execute(
                "INSERT INTO saldos_cliente (cliente_id, monto, concepto, factura_id, usuario) VALUES (?,?,?,?,?)",
                (body.cliente_id, -usar_saldo, f"Usado en {numero}", factura_id, usuario),
            )
        conn.commit()
    except HTTPException:
        conn.rollback()
        conn.close()
        raise
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        conn.close()
        previa = _venta_previa(base, id_local) if id_local else None
        if previa:
            return previa
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        conn.rollback()
        conn.close()
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    conn.close()
    electronico = None
    if encf:
        try:
            from ecf import servicio as ecf_srv
            reg = ecf_srv.emitir_factura(base, factura_id) or {}
            if reg.get("estado") == "pendiente_envio":
                ecf_srv.enviar_en_segundo_plano(db, reg["id"])
            electronico = {k: reg.get(k) for k in ("encf", "estado", "codigo_seguridad", "fecha_firma", "url_qr", "mensaje")}
        except Exception as exc:  # la venta ya quedó guardada; el e-CF se puede reemitir
            electronico = {"encf": encf, "estado": "error", "mensaje": str(exc)[:300]}
    return {
        "ok": True,
        "id": factura_id,
        "numero": numero,
        "ncf": ncf,
        "ecf": electronico,
        "subtotal": round(subtotal, 2),
        "itbis": round(impuesto, 2),
        "cliente": cliente_nombre,
        "total": total,
        "cambio": cambio,
        "pendiente": pendiente,
        "vencimiento": vencimiento,
        "lineas": [
            {"nombre": d, "cantidad": c, "total": round(n + i, 2)}
            for _row, d, c, _p, n, i, _desc, _comps in lineas
        ],
    }


class AbonoIn(BaseModel):
    pagos: list[PagoIn]
    nota: str = ""


SQL_PENDIENTES = """
    SELECT f.id, f.numero, f.fecha, IFNULL(f.ncf,''), f.cliente_id, IFNULL(c.nombre,'Consumidor final'),
           IFNULL(c.telefono,''), f.total,
           IFNULL((SELECT SUM(p.monto) FROM pagos_factura p WHERE p.factura_id = f.id), 0) AS pagado,
           f.fecha_vencimiento
    FROM facturas f
    LEFT JOIN clientes c ON c.id = f.cliente_id
    WHERE f.estado = 'emitida'
      AND f.total - IFNULL((SELECT SUM(p.monto) FROM pagos_factura p WHERE p.factura_id = f.id), 0) > 0.009
"""


@app.get("/api/cobros")
def listar_cobros(request: Request, q: str = "", cliente_id: int | None = None):
    _exigir_alguno(request, ("cobros", "clientes"))
    conn = db().get_connection()
    cur = conn.cursor()
    sql = SQL_PENDIENTES
    params: list = []
    if cliente_id:
        sql += " AND f.cliente_id = ?"
        params.append(cliente_id)
    if q.strip():
        sql += " AND (f.numero LIKE ? OR IFNULL(c.nombre,'') LIKE ? OR IFNULL(c.documento,'') LIKE ?)"
        params.extend([f"%{q.strip()}%"] * 3)
    sql += " ORDER BY COALESCE(f.fecha_vencimiento, date(f.fecha)), f.id LIMIT 300"
    cur.execute(sql, params)
    hoy = datetime.now().strftime("%Y-%m-%d")
    items = []
    for fid, numero, fecha, ncf, cid, cliente, tel, total, pagado, vence in cur.fetchall():
        pendiente = round(float(total or 0) - float(pagado or 0), 2)
        items.append({
            "id": fid, "numero": numero, "fecha": fecha, "ncf": ncf, "cliente_id": cid, "cliente": cliente,
            "telefono": tel, "total": round(float(total or 0), 2), "pagado": round(float(pagado or 0), 2),
            "pendiente": pendiente, "vence": vence, "vencida": bool(vence and vence < hoy),
        })
    conn.close()
    total = round(sum(i["pendiente"] for i in items), 2)
    vencido = round(sum(i["pendiente"] for i in items if i["vencida"]), 2)
    return {"items": items, "total": total, "vencido": vencido, "clientes": len({i["cliente_id"] for i in items})}


@app.post("/api/cobros/{factura_id}")
def abonar(request: Request, factura_id: int, body: AbonoIn):
    usuario = _exigir_alguno(request, ("cobros", "mostrador"))
    base = db()
    caja = base.fetch_caja_abierta_row()
    if caja is None:
        raise HTTPException(status_code=400, detail="Abre el turno de caja para recibir el abono")
    conn = base.get_connection()
    cur = conn.cursor()
    try:
        cur.execute(SQL_PENDIENTES + " AND f.id = ?", (factura_id,))
        fila = cur.fetchone()
        if not fila:
            raise HTTPException(status_code=404, detail="Esa factura no tiene saldo pendiente")
        pendiente = round(float(fila[7] or 0) - float(fila[8] or 0), 2)
        activos = {codigo for codigo, *_rest in base.listar_metodos_pago(True)}
        afectan = base.codigos_afectan_caja()
        entradas = [(p.codigo.strip().lower(), round(float(p.monto or 0), 2)) for p in body.pagos if round(float(p.monto or 0), 2) > 0]
        if not entradas:
            raise HTTPException(status_code=400, detail="Indica el monto del abono")
        for codigo, _monto in entradas:
            if codigo != "saldo_favor" and codigo not in activos:
                raise HTTPException(status_code=400, detail=f"El método {codigo} no está activo")
        usar_saldo = round(sum(m for c, m in entradas if c == "saldo_favor"), 2)
        if usar_saldo > 0:
            if not fila[4]:
                raise HTTPException(status_code=400, detail="Esta factura no tiene cliente para usar saldo a favor")
            disponible = base.saldo_favor_cliente(fila[4], conn=conn)
            if usar_saldo > disponible + 0.009:
                raise HTTPException(status_code=400, detail=f"El cliente solo tiene {disponible:.2f} de saldo a favor")
        fuera = round(sum(m for c, m in entradas if c not in afectan), 2)
        en_caja = round(sum(m for c, m in entradas if c in afectan), 2)
        if fuera > pendiente + 0.009:
            raise HTTPException(status_code=400, detail=f"El abono supera lo pendiente ({pendiente:,.2f})")
        caja_guardar = round(min(en_caja, pendiente - fuera), 2)
        cambio = round(max(0.0, en_caja - caja_guardar), 2)
        ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        registrados = [(c, m) for c, m in entradas if c not in afectan]
        if caja_guardar > 0:
            registrados.append((next(c for c, _m in entradas if c in afectan), caja_guardar))
        for codigo, monto in registrados:
            cur.execute(
                """
                INSERT INTO pagos_factura (factura_id, tipo_pago, monto, referencia, cierre_id, fecha, usuario, es_abono)
                VALUES (?,?,?,?,?,?,?,1)
                """,
                (factura_id, codigo, monto, (body.nota or "").strip()[:200] or None, caja[0], ahora, usuario),
            )
        if usar_saldo > 0:
            cur.execute(
                "INSERT INTO saldos_cliente (cliente_id, monto, concepto, factura_id, usuario) VALUES (?,?,?,?,?)",
                (fila[4], -usar_saldo, f"Abono a {fila[1]}", factura_id, usuario),
            )
        conn.commit()
    except HTTPException:
        conn.rollback()
        conn.close()
        raise
    abonado = round(sum(m for _c, m in registrados), 2)
    conn.close()
    return {
        "ok": True, "numero": fila[1], "cliente": fila[5], "abonado": abonado,
        "pendiente": round(pendiente - abonado, 2), "cambio": cambio,
        "mensaje": f"Abono de {abonado:,.2f} a {fila[1]}. Queda {max(0.0, pendiente - abonado):,.2f}.",
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
          AND (COALESCE(p.cierre_id, f.cierre_id) = ?
               OR (COALESCE(p.cierre_id, f.cierre_id) IS NULL AND datetime(COALESCE(p.fecha, f.fecha)) >= datetime(?)))
        GROUP BY p.tipo_pago
        """,
        (cierre_id, fecha),
    )
    rows = cur.fetchall()
    conn.close()
    afecta = {c.lower() for c in base.codigos_afectan_caja()}
    return round(sum(float(m or 0) for t, m in rows if (t or "").lower() in afecta), 2)


def _efectivo_del_turno(base: Database, cierre_id: int) -> float:
    conn = base.get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT p.tipo_pago, SUM(p.monto)
        FROM pagos_factura p
        JOIN facturas f ON f.id = p.factura_id
        WHERE f.estado = 'emitida' AND COALESCE(p.cierre_id, f.cierre_id) = ?
        GROUP BY p.tipo_pago
        """,
        (cierre_id,),
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
    if body.id:
        conn = base.get_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT tipo, monto, cierre_id FROM movimientos_efectivo WHERE id=?",
            (body.id,),
        )
        previo = cur.fetchone()
        if not previo or int(previo[2]) != int(caja[0]):
            conn.close()
            raise HTTPException(status_code=400, detail="Ese movimiento no está en el turno abierto")
        if tipo == "retiro":
            ingresos, retiros = base.totales_movimientos_efectivo(caja[0])
            esperado = float(caja[6] or 0) + _efectivo_ventas(base, caja[2], caja[0]) + ingresos - retiros
            if (previo[0] or "").lower() == "retiro":
                esperado += float(previo[1] or 0)
            if body.monto > esperado + 0.01:
                conn.close()
                raise HTTPException(status_code=400, detail=f"El retiro supera el efectivo esperado ({esperado:.2f})")
        cur.execute(
            "UPDATE movimientos_efectivo SET tipo=?, monto=?, motivo=? WHERE id=?",
            (tipo, round(float(body.monto), 2), body.motivo.strip(), body.id),
        )
        conn.commit()
        conn.close()
        return {"ok": True, "id": body.id}
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
    return {"ok": True, "id": caja[0], "esperado": round(esperado, 2), "diferencia": diff}


def _panel_caja(base: Database, desde: str = "", hasta: str = "") -> dict:
    caja = base.fetch_caja_abierta_row()
    abierta = None
    if caja is not None:
        ingresos, retiros = base.totales_movimientos_efectivo(caja[0])
        ventas = _efectivo_ventas(base, caja[2], caja[0])
        fondo = float(caja[6] or 0)
        abierta = {
            "id": caja[0],
            "nombre": caja[1],
            "apertura": caja[2],
            "usuario": caja[4],
            "fondo": fondo,
            "ventas_efectivo": ventas,
            "ingresos": ingresos,
            "retiros": retiros,
            "esperado": round(fondo + ventas + ingresos - retiros, 2),
            "movimientos": [
                {"id": i, "tipo": t, "monto": m, "motivo": mot, "usuario": u, "fecha": f}
                for i, t, m, mot, u, f in base.listar_movimientos_efectivo(caja[0])
            ],
        }
    conn = base.get_connection()
    cur = conn.cursor()
    donde = "WHERE fecha_cierre IS NOT NULL"
    params: list = []
    if (desde or "").strip() and (hasta or "").strip():
        donde += " AND date(fecha_cierre) >= date(?) AND date(fecha_cierre) <= date(?)"
        params = [desde.strip(), hasta.strip()]
    else:
        donde += " AND datetime(fecha_cierre) >= datetime('now', '-30 days')"
    cur.execute(
        f"""
        SELECT id, IFNULL(nombre_caja,'Caja'), fecha_apertura, fecha_cierre,
               IFNULL(usuario_apertura,''), IFNULL(usuario_cierre, usuario_apertura),
               IFNULL(monto_inicial,0), IFNULL(efectivo_contado,0),
               IFNULL(diferencia_efectivo,0), IFNULL(observaciones,''), estado
        FROM cierres_caja
        {donde}
        ORDER BY datetime(fecha_cierre) DESC
        LIMIT 200
        """,
        params,
    )
    cierres = []
    for i, n, a, c, ua, uc, f, cont, d, o, e in cur.fetchall():
        contado = float(cont or 0)
        diferencia = float(d or 0)
        cierres.append(
            {
                "id": i, "nombre": n, "apertura": a, "cierre": c,
                "usuario": uc or ua, "usuario_apertura": ua,
                "fondo": float(f or 0), "contado": contado, "diferencia": diferencia,
                "esperado": round(contado - diferencia, 2),
                "observaciones": o, "estado": e,
            }
        )
    cur.execute(
        """
        SELECT m.id, m.tipo, m.monto, IFNULL(m.motivo,''), IFNULL(m.usuario,''), m.fecha,
               CASE WHEN c.fecha_cierre IS NULL THEN 1 ELSE 0 END
        FROM movimientos_efectivo m
        JOIN cierres_caja c ON c.id = m.cierre_id
        ORDER BY m.id DESC
        LIMIT 40
        """
    )
    recientes = [
        {"id": i, "tipo": t, "monto": m, "motivo": mot, "usuario": u, "fecha": f, "abierto": bool(ab)}
        for i, t, m, mot, u, f, ab in cur.fetchall()
    ]
    conn.close()
    return {"abierta": abierta, "cierres": cierres, "recientes": recientes}


@app.get("/api/caja/panel")
def panel_caja(request: Request, desde: str = "", hasta: str = ""):
    _exigir(request, "caja")
    return _panel_caja(db(), desde, hasta)


@app.get("/api/caja/cierre/{cierre_id}")
def ver_cuadre(request: Request, cierre_id: int):
    _exigir_alguno(request, ("caja", "historial"))
    base = db()
    conn = base.get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, IFNULL(nombre_caja,'Caja'), fecha_apertura, fecha_cierre,
               IFNULL(usuario_apertura,''), IFNULL(usuario_cierre,''),
               IFNULL(monto_inicial,0), IFNULL(efectivo_contado,0),
               IFNULL(diferencia_efectivo,0), IFNULL(observaciones,''), IFNULL(estado,'')
        FROM cierres_caja WHERE id=?
        """,
        (cierre_id,),
    )
    row = cur.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="No se encontró ese cuadre")
    i, n, a, c, ua, uc, fondo, cont, diff, obs, estado = row
    fin = c or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur.execute(
        """
        SELECT f.id, f.numero, f.fecha, f.total, f.estado, IFNULL(f.ncf,''),
               COALESCE(cl.nombre, 'Consumidor final'), IFNULL(f.subtotal,0), IFNULL(f.impuesto_total,0)
        FROM facturas f
        LEFT JOIN clientes cl ON cl.id = f.cliente_id
        WHERE f.cierre_id = ?
           OR (f.cierre_id IS NULL AND datetime(f.fecha) >= datetime(?) AND datetime(f.fecha) <= datetime(?))
        ORDER BY datetime(f.fecha), f.id
        LIMIT 300
        """,
        (i, a or fin, fin),
    )
    fac_rows = cur.fetchall()
    lineas_por_factura: dict[int, list] = {}
    if fac_rows:
        marcas = ",".join("?" * len(fac_rows))
        cur.execute(
            f"""
            SELECT factura_id, descripcion, cantidad, precio_unitario, total_linea, producto_id, impuesto_item
            FROM factura_detalle
            WHERE factura_id IN ({marcas})
            """,
            [r[0] for r in fac_rows],
        )
        for fid, nom, cant, precio, total, pid, imp in cur.fetchall():
            lineas_por_factura.setdefault(fid, []).append(
                {"nombre": nom, "cantidad": cant, "precio": precio, "total": total, "producto_id": pid, "itbis": imp}
            )
    conn.close()
    facturas = [
        {
            "id": fid, "numero": num, "fecha": fec, "total": tot, "estado": est, "ncf": ncf,
            "cliente": cli, "subtotal": sub, "itbis": itbis,
            "lineas": lineas_por_factura.get(fid, []),
        }
        for fid, num, fec, tot, est, ncf, cli, sub, itbis in fac_rows
    ]
    ingresos, retiros = base.totales_movimientos_efectivo(i)
    ventas = _efectivo_ventas(base, a or "", i) if (estado or "") != "cerrado" else _efectivo_del_turno(base, i)
    movimientos = [
        {"id": mid, "tipo": t, "monto": m, "motivo": mot, "usuario": u, "fecha": f}
        for mid, t, m, mot, u, f in base.listar_movimientos_efectivo(i)
    ]
    return {
        "id": i,
        "nombre": n,
        "apertura": a,
        "cierre": c,
        "usuario_apertura": ua,
        "usuario_cierre": uc,
        "fondo": float(fondo or 0),
        "ventas_efectivo": ventas,
        "ingresos": ingresos,
        "retiros": retiros,
        "esperado": round(float(cont or 0) - float(diff or 0), 2) if (estado or "") == "cerrado" else round(float(fondo or 0) + ventas + ingresos - retiros, 2),
        "contado": float(cont or 0),
        "diferencia": float(diff or 0),
        "observaciones": obs,
        "estado": estado,
        "movimientos": movimientos,
        "facturas": facturas,
    }


class TurnoEditIn(BaseModel):
    nombre: str
    fondo: float


@app.post("/api/caja/turno/{cierre_id}")
def editar_turno(request: Request, cierre_id: int, body: TurnoEditIn):
    _exigir(request, "caja")
    conn = db().get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE cierres_caja
        SET nombre_caja=?, monto_inicial=?
        WHERE id=? AND fecha_cierre IS NULL
        """,
        (body.nombre.strip() or "Caja 1", float(body.fondo or 0), cierre_id),
    )
    if cur.rowcount == 0:
        conn.close()
        raise HTTPException(status_code=400, detail="Solo se edita el turno que está abierto")
    conn.commit()
    conn.close()
    return {"ok": True}


@app.post("/api/caja/cierre/{cierre_id}")
def editar_cierre(request: Request, cierre_id: int, body: CerrarCajaIn):
    _exigir(request, "caja")
    conn = db().get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT IFNULL(efectivo_contado,0), IFNULL(diferencia_efectivo,0), estado
        FROM cierres_caja WHERE id=?
        """,
        (cierre_id,),
    )
    row = cur.fetchone()
    if not row or row[2] != "cerrado":
        conn.close()
        raise HTTPException(status_code=400, detail="Ese turno no está cerrado")
    esperado = round(float(row[0] or 0) - float(row[1] or 0), 2)
    diff = round(float(body.contado) - esperado, 2)
    if abs(diff) > 0.05 and len((body.observaciones or "").strip()) < 3:
        conn.close()
        raise HTTPException(status_code=400, detail="Si no cuadra, escribe una observación")
    cur.execute(
        """
        UPDATE cierres_caja
        SET efectivo_contado=?, diferencia_efectivo=?, observaciones=?
        WHERE id=?
        """,
        (round(float(body.contado), 2), diff, (body.observaciones or "").strip() or None, cierre_id),
    )
    conn.commit()
    conn.close()
    return {"ok": True, "esperado": esperado, "diferencia": diff}


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
        SELECT IFNULL(v.nombre, 'Sin vendedor'), COUNT(*), SUM(f.total)
        FROM facturas f
        LEFT JOIN vendedores v ON v.id = f.vendedor_id
        WHERE f.estado = 'emitida' AND {donde_f.replace("fecha", "f.fecha")}
        GROUP BY f.vendedor_id
        ORDER BY SUM(f.total) DESC
        """,
        params_f,
    )
    por_vendedor = [{"vendedor": n, "facturas": c, "total": tt} for n, c, tt in cur.fetchall()]
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
    cur.execute(
        f"""
        SELECT IFNULL(t.nombre, 'Sin terminal'), COUNT(*), SUM(f.total)
        FROM facturas f
        LEFT JOIN terminales t ON t.id = f.terminal_id
        WHERE f.estado = 'emitida' AND {donde_f.replace("fecha", "f.fecha")}
        GROUP BY f.terminal_id
        ORDER BY SUM(f.total) DESC
        """,
        params_f,
    )
    por_terminal = [{"terminal": n, "facturas": c, "total": tt} for n, c, tt in cur.fetchall()]
    conn.close()
    return {"vendidos": vendidos, "cajeros": cajeros, "pagos": pagos, "vendedores": por_vendedor, "terminales": por_terminal}


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


class NotaCompraIn(BaseModel):
    nota: str


@app.post("/api/compras/{compra_id}")
def editar_compra_nota(request: Request, compra_id: int, body: NotaCompraIn):
    _exigir(request, "compras")
    conn = db().get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE compras SET nota=? WHERE id=?", (body.nota.strip(), compra_id))
    if cur.rowcount == 0:
        conn.close()
        raise HTTPException(status_code=404, detail="Compra no encontrada")
    conn.commit()
    conn.close()
    return {"ok": True}


@app.get("/api/compras/{compra_id}/lineas")
def ver_compra(request: Request, compra_id: int):
    _exigir(request, "compras")
    conn = db().get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT d.cantidad, d.costo, IFNULL(p.nombre,'Producto'), IFNULL(d.bodega,'Principal')
        FROM compra_detalle d
        LEFT JOIN productos p ON p.id = d.producto_id
        WHERE d.compra_id=?
        """,
        (compra_id,),
    )
    lineas = [{"cantidad": c, "costo": k, "nombre": n, "bodega": b} for c, k, n, b in cur.fetchall()]
    conn.close()
    if not lineas:
        raise HTTPException(status_code=404, detail="Compra no encontrada")
    return {"lineas": lineas}


@app.get("/api/promociones")
def promociones(request: Request):
    _exigir(request, "promociones")
    return {
        "items": [
            {
                "id": i, "nombre": n, "tipo": t, "valor": v, "activa": bool(a),
                "producto_id": pid or 0,
            }
            for i, n, t, v, a, pid in _promos_web()
        ]
    }


def _promos_web():
    conn = db().get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT p.id, p.nombre, p.tipo_descuento, IFNULL(p.valor,0), IFNULL(p.activo,1),
               IFNULL((SELECT d.producto_id FROM promociones_detalle d
                       WHERE d.promocion_id = p.id LIMIT 1), 0)
        FROM promociones p
        ORDER BY p.id DESC
        """
    )
    rows = cur.fetchall()
    conn.close()
    return rows


@app.post("/api/promociones")
def crear_promo(request: Request, body: PromoIn):
    _exigir(request, "promociones")
    if body.tipo not in ("porcentaje", "fijo", "2x1", "3x2"):
        raise HTTPException(status_code=400, detail="Tipo de promoción inválido")
    pid = db().guardar_promocion(body.nombre, body.tipo, body.valor, producto_id=body.producto_id)
    return {"ok": True, "id": pid}


@app.post("/api/promociones/{promo_id}")
def editar_promo(request: Request, promo_id: int, body: PromoIn):
    _exigir(request, "promociones")
    if body.tipo not in ("porcentaje", "fijo", "2x1", "3x2"):
        raise HTTPException(status_code=400, detail="Tipo de promoción inválido")
    conn = db().get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE promociones SET nombre=?, tipo_descuento=?, valor=?, activo=? WHERE id=?",
        (body.nombre.strip(), body.tipo, body.valor, 1 if body.activa else 0, promo_id),
    )
    if cur.rowcount == 0:
        conn.close()
        raise HTTPException(status_code=404, detail="Promoción no encontrada")
    producto_id = body.producto_id or None
    cur.execute("SELECT id FROM promociones_detalle WHERE promocion_id=? LIMIT 1", (promo_id,))
    if cur.fetchone():
        cur.execute(
            "UPDATE promociones_detalle SET producto_id=? WHERE promocion_id=?",
            (producto_id, promo_id),
        )
    else:
        cur.execute(
            "INSERT INTO promociones_detalle (promocion_id, producto_id) VALUES (?,?)",
            (promo_id, producto_id),
        )
    conn.commit()
    conn.close()
    return {"ok": True}


@app.get("/api/devolucion")
def ver_devolucion(request: Request, q: str):
    _exigir(request, "devoluciones")
    conn = db().get_connection()
    cur = conn.cursor()
    columnas = """f.id, f.numero, f.total, f.cliente_id, IFNULL(c.nombre,'Consumidor final'),
               IFNULL((SELECT SUM(p.monto) FROM pagos_factura p WHERE p.factura_id = f.id), 0)"""
    if q.strip().isdigit():
        cur.execute(
            f"SELECT {columnas} FROM facturas f LEFT JOIN clientes c ON c.id=f.cliente_id WHERE f.id=? AND f.estado='emitida'",
            (int(q),),
        )
    else:
        cur.execute(
            f"SELECT {columnas} FROM facturas f LEFT JOIN clientes c ON c.id=f.cliente_id WHERE (f.numero = ? OR IFNULL(f.ncf,'') = ?) AND f.estado='emitida' LIMIT 1",
            (q.strip(), q.strip()),
        )
        if cur.fetchone() is None:
            cur.execute(
                f"SELECT {columnas} FROM facturas f LEFT JOIN clientes c ON c.id=f.cliente_id WHERE f.numero LIKE ? AND f.estado='emitida' ORDER BY f.id DESC LIMIT 1",
                (f"%{q.strip()}%",),
            )
        else:
            cur.execute(
                f"SELECT {columnas} FROM facturas f LEFT JOIN clientes c ON c.id=f.cliente_id WHERE (f.numero = ? OR IFNULL(f.ncf,'') = ?) AND f.estado='emitida' LIMIT 1",
                (q.strip(), q.strip()),
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
    return {
        "id": fac[0], "numero": fac[1], "total": fac[2], "lineas": lineas,
        "cliente_id": fac[3], "cliente": fac[4],
        "pendiente": round(max(0.0, float(fac[2] or 0) - float(fac[5] or 0)), 2),
    }


@app.post("/api/devolucion")
def devolver(request: Request, body: DevolucionIn):
    usuario = _exigir(request, "devoluciones")
    data = ver_devolucion(request, body.factura)
    lineas = []
    for ln in body.lineas:
        qty = float(ln.get("cantidad") or 0)
        if qty > 0:
            lineas.append((int(ln["id"]), qty))
    destino = (body.destino or ("efectivo" if body.reembolso_efectivo else "nota")).strip().lower()
    if destino not in ("efectivo", "saldo_favor", "abono", "nota"):
        raise HTTPException(status_code=400, detail="Destino de la devolución no válido")
    if destino in ("saldo_favor", "abono") and not data.get("cliente_id"):
        raise HTTPException(status_code=400, detail="La factura no tiene cliente: devuelve en efectivo o como nota de crédito")
    base = db()
    caja = base.fetch_caja_abierta_row()
    if destino == "efectivo" and caja is None:
        raise HTTPException(status_code=400, detail="Abre el turno de caja para devolver en efectivo")
    if destino == "abono" and data.get("pendiente", 0) <= 0.009:
        raise HTTPException(status_code=400, detail="Esa factura ya está saldada: usa saldo a favor o efectivo")
    ok, msg = base.registrar_devolucion_nota_credito(data["id"], lineas, body.motivo, usuario)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    conn = base.get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, monto_total FROM notas_credito WHERE factura_original_id=? ORDER BY id DESC LIMIT 1",
        (data["id"],),
    )
    nota = cur.fetchone()
    nota_id, monto = (int(nota[0]), round(float(nota[1] or 0), 2)) if nota else (None, 0.0)
    ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur.execute("SELECT encf FROM facturas WHERE id=?", (data["id"],))
    if nota_id and (cur.fetchone() or [None])[0]:
        try:
            from ecf import servicio as ecf_srv
            reg = ecf_srv.emitir_nota_credito(base, nota_id) or {}
            if reg.get("estado") == "pendiente_envio":
                ecf_srv.enviar_en_segundo_plano(db, reg["id"])
            if reg.get("encf"):
                msg += f" Nota de crédito electrónica {reg['encf']}: {reg.get('estado')}."
        except Exception as exc:  # noqa: BLE001
            msg += f" No se pudo generar la nota electrónica: {exc}"
    if destino == "efectivo" and monto > 0:
        conn.close()
        base.registrar_movimiento_efectivo(caja[0], "retiro", monto, f"Devolución {data['numero']}", usuario)
        msg += f" Se devolvieron {monto:,.2f} del efectivo del turno."
        return {"ok": True, "mensaje": msg}
    if destino == "abono" and monto > 0:
        aplicado = round(min(monto, float(data["pendiente"])), 2)
        cur.execute(
            """
            INSERT INTO pagos_factura (factura_id, tipo_pago, monto, referencia, cierre_id, fecha, usuario, es_abono)
            VALUES (?, 'nota_credito', ?, ?, ?, ?, ?, 1)
            """,
            (data["id"], aplicado, f"Nota de crédito {nota_id}", caja[0] if caja else None, ahora, usuario),
        )
        sobrante = round(monto - aplicado, 2)
        msg += f" Se rebajaron {aplicado:,.2f} de lo pendiente de {data['numero']}."
        if sobrante > 0:
            cur.execute(
                "INSERT INTO saldos_cliente (cliente_id, monto, concepto, nota_credito_id, factura_id, usuario) VALUES (?,?,?,?,?,?)",
                (data["cliente_id"], sobrante, f"Sobrante de devolución {data['numero']}", nota_id, data["id"], usuario),
            )
            msg += f" Los {sobrante:,.2f} restantes quedaron como saldo a favor."
    elif destino == "saldo_favor" and monto > 0:
        cur.execute(
            "INSERT INTO saldos_cliente (cliente_id, monto, concepto, nota_credito_id, factura_id, usuario) VALUES (?,?,?,?,?,?)",
            (data["cliente_id"], monto, f"Devolución {data['numero']}", nota_id, data["id"], usuario),
        )
        msg += f" {data['cliente']} tiene {monto:,.2f} de saldo a favor para su próxima compra."
    conn.commit()
    conn.close()
    return {"ok": True, "mensaje": msg}


@app.get("/api/clientes/{cliente_id}/saldo")
def saldo_cliente(request: Request, cliente_id: int):
    _exigir_alguno(request, ("mostrador", "clientes", "cobros"))
    base = db()
    conn = base.get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT fecha, monto, concepto, IFNULL(usuario,'') FROM saldos_cliente WHERE cliente_id=? ORDER BY id DESC LIMIT 30",
        (cliente_id,),
    )
    movimientos = [{"fecha": f, "monto": m, "concepto": c, "usuario": u} for f, m, c, u in cur.fetchall()]
    cur.execute(
        "SELECT IFNULL(limite_credito,0), IFNULL(dias_credito,30), IFNULL(lista_precio,1), nombre FROM clientes WHERE id=?",
        (cliente_id,),
    )
    fila = cur.fetchone()
    conn.close()
    if not fila:
        raise HTTPException(status_code=404, detail="Cliente no encontrado")
    deuda = base.deuda_cliente(cliente_id)
    limite = round(float(fila[0] or 0), 2)
    return {
        "saldo_favor": base.saldo_favor_cliente(cliente_id), "deuda": deuda, "limite_credito": limite,
        "credito_disponible": round(max(0.0, limite - deuda), 2), "dias_credito": int(fila[1] or 30),
        "lista_precio": int(fila[2] or 1), "nombre": fila[3], "movimientos": movimientos,
    }


@app.get("/api/devoluciones")
def listar_devoluciones(request: Request):
    _exigir(request, "devoluciones")
    conn = db().get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT nc.id, nc.fecha, IFNULL(nc.motivo,''), nc.monto_total, IFNULL(f.numero,'')
        FROM notas_credito nc
        LEFT JOIN facturas f ON f.id = nc.factura_original_id
        ORDER BY nc.id DESC
        LIMIT 30
        """
    )
    items = [
        {"id": i, "fecha": f, "motivo": m, "total": t, "factura": n}
        for i, f, m, t, n in cur.fetchall()
    ]
    conn.close()
    return {"items": items}


@app.post("/api/devolucion/{nota_id}")
def editar_devolucion(request: Request, nota_id: int, body: MotivoIn):
    _exigir(request, "devoluciones")
    motivo = body.motivo.strip()
    if len(motivo) < 3:
        raise HTTPException(status_code=400, detail="Escribe el motivo")
    conn = db().get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE notas_credito SET motivo=? WHERE id=?", (motivo, nota_id))
    if cur.rowcount == 0:
        conn.close()
        raise HTTPException(status_code=404, detail="Devolución no encontrada")
    conn.commit()
    conn.close()
    return {"ok": True}


@app.get("/api/devoluciones/{nota_id}")
def ver_nota_credito(request: Request, nota_id: int):
    _exigir(request, "devoluciones")
    conn = db().get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT IFNULL(p.nombre, 'Producto'), ncd.cantidad, ncd.monto
        FROM notas_credito_detalle ncd
        LEFT JOIN productos p ON p.id = ncd.producto_id
        WHERE ncd.nota_credito_id=?
        """,
        (nota_id,),
    )
    lineas = [{"nombre": n, "cantidad": c, "monto": m} for n, c, m in cur.fetchall()]
    conn.close()
    return {"lineas": lineas}


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


def _ecf_de_factura(factura_id: int) -> dict | None:
    conn = db().get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT encf, estado, IFNULL(codigo_seguridad,''), IFNULL(fecha_firma,''), IFNULL(url_qr,''), IFNULL(mensaje,'')
        FROM ecf_documentos WHERE factura_id=? AND nota_credito_id IS NULL ORDER BY id DESC LIMIT 1
        """,
        (factura_id,),
    )
    fila = cur.fetchone()
    conn.close()
    if not fila:
        return None
    return dict(zip(("encf", "estado", "codigo_seguridad", "fecha_firma", "url_qr", "mensaje"), fila))


@app.get("/api/factura/{factura_id}")
def ver_factura(request: Request, factura_id: int):
    _exigir_alguno(request, ("historial", "cotizaciones"))
    conn = db().get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT f.id, f.numero, f.fecha, f.total, f.estado, f.ncf, f.usuario,
               COALESCE(c.nombre, 'Consumidor final'), f.subtotal, f.impuesto_total,
               f.cliente_id, f.cierre_id, IFNULL(v.nombre,''), IFNULL(f.observaciones,''), f.fecha_vencimiento,
               IFNULL((SELECT SUM(p.monto) FROM pagos_factura p WHERE p.factura_id = f.id), 0)
        FROM facturas f
        LEFT JOIN clientes c ON c.id = f.cliente_id
        LEFT JOIN vendedores v ON v.id = f.vendedor_id
        WHERE f.id=?
        """,
        (factura_id,),
    )
    fac = cur.fetchone()
    if not fac:
        conn.close()
        raise HTTPException(status_code=404, detail="Factura no encontrada")
    cierre_id = fac[11]
    if not cierre_id and fac[2]:
        cur.execute(
            """
            SELECT id FROM cierres_caja
            WHERE datetime(fecha_apertura) <= datetime(?)
              AND (fecha_cierre IS NULL OR datetime(fecha_cierre) >= datetime(?))
            ORDER BY datetime(fecha_apertura) DESC
            LIMIT 1
            """,
            (fac[2], fac[2]),
        )
        hallado = cur.fetchone()
        cierre_id = hallado[0] if hallado else None
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
        "subtotal": fac[8], "itbis": fac[9], "cliente_id": fac[10],
        "vendedor": fac[12], "observaciones": fac[13] if fac[13] not in ("Cotización web",) else "", "vence": fac[14],
        "ecf": _ecf_de_factura(factura_id),
        "pagado": round(float(fac[15] or 0), 2), "pendiente": round(max(0.0, float(fac[3] or 0) - float(fac[15] or 0)), 2) if fac[4] == "emitida" else 0,
        "cierre_id": cierre_id, "lineas": lineas,
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
    descuentos = _descuentos_mayor(base)
    conn = base.get_connection()
    cur = conn.cursor()
    det, subtotal, impuesto = _armar_lineas(cur, body.items, descuentos, _itbis_incluido(base))
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
    descuentos = _descuentos_mayor(base)
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
    det, subtotal, impuesto = _armar_lineas(cur, body.items, descuentos, _itbis_incluido(base))
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
    cur.execute(
        """
        SELECT m.id, m.producto_id, p.nombre, IFNULL(m.bodega_codigo,''), m.cantidad,
               IFNULL(m.descripcion_mov,''), m.fecha
        FROM movimientos_inventario m
        JOIN productos p ON p.id = m.producto_id
        WHERE m.tipo_movimiento = 'transferencia' AND m.cantidad < 0
        ORDER BY m.id DESC
        LIMIT 20
        """
    )
    transferencias = [
        {
            "id": i, "producto_id": pid, "producto": n, "origen": o,
            "cantidad": abs(float(c or 0)),
            "destino": (d or "").replace("Sale hacia ", ""), "fecha": f,
        }
        for i, pid, n, o, c, d, f in cur.fetchall()
    ]
    conn.close()
    return {"items": rows, "bodegas": nombres, "transferencias": transferencias}


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
    precio_original: float = 0
    precio_2: float | None = None
    precio_3: float | None = None
    precio_4: float | None = None
    stock: float = 0
    codigo: str = ""
    bodega: str = "Principal"
    categoria: str = ""


class DescuentosMayorIn(BaseModel):
    p2: float = 2
    p3: float = 5
    p4: float = 10


def _descuentos_mayor(base: Database) -> tuple[float, float, float]:
    conn = base.get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT clave, valor FROM config WHERE clave IN ('desc_precio_2','desc_precio_3','desc_precio_4')"
    )
    mapa = {clave: valor for clave, valor in cur.fetchall()}
    conn.close()

    def uno(clave: str, default: str) -> float:
        try:
            valor = float(mapa.get(clave, default) or default)
        except (TypeError, ValueError):
            valor = float(default)
        return min(90.0, max(0.0, valor))

    return uno("desc_precio_2", "2"), uno("desc_precio_3", "5"), uno("desc_precio_4", "10")


def _monto_mayor(precio_minimo: float, porcentaje: float, explicito: float | None) -> float:
    if explicito is not None:
        return round(float(explicito), 2)
    return round(float(precio_minimo) * (1 - porcentaje / 100.0), 2)


def _validar_precios(body: ProductoIn, base: Database) -> tuple[float, float, float, float, float]:
    original = round(float(body.precio_original or 0), 2)
    minimo = round(float(body.precio or 0), 2)
    if original <= 0:
        raise HTTPException(status_code=400, detail="Indica el precio original")
    if minimo <= 0:
        raise HTTPException(status_code=400, detail="Indica el precio mínimo de venta")
    d2, d3, d4 = _descuentos_mayor(base)
    return (
        original,
        minimo,
        _monto_mayor(minimo, d2, body.precio_2),
        _monto_mayor(minimo, d3, body.precio_3),
        _monto_mayor(minimo, d4, body.precio_4),
    )


class UsuarioIn(BaseModel):
    username: str
    password: str = ""
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
    limite_credito: float | None = None
    dias_credito: int | None = None
    lista_precio: int | None = None
    activo: bool | None = None


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
    precios_incluyen_itbis: bool | None = None


@app.get("/api/inventario")
def inventario(request: Request, q: str = ""):
    _exigir(request, "inventario")
    conn = db().get_connection()
    cur = conn.cursor()
    sql = """
        SELECT p.id, p.nombre, IFNULL(p.precio,0), IFNULL(p.precio_base,0),
               IFNULL(p.precio_2,0), IFNULL(p.precio_3,0), IFNULL(p.precio_4,0),
               IFNULL(p.stock,0),
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
            "id": i, "nombre": n, "precio": p, "precio_original": origen,
            "precio_2": p2, "precio_3": p3, "precio_4": p4,
            "stock": s, "codigo": cb, "bodega": b or "Principal", "categoria": cat,
            "minimo": mn, "activo": bool(a),
        }
        for i, n, p, origen, p2, p3, p4, s, cb, b, cat, mn, a in cur.fetchall()
    ]
    conn.close()
    d2, d3, d4 = _descuentos_mayor(db())
    return {"items": rows, "descuentos": {"p2": d2, "p3": d3, "p4": d4}}


@app.post("/api/inventario")
def crear_producto(request: Request, body: ProductoIn):
    _exigir(request, "inventario")
    nombre = body.nombre.strip()
    if len(nombre) < 2:
        raise HTTPException(status_code=400, detail="Indica el nombre del producto")
    base = db()
    original, minimo, p2, p3, p4 = _validar_precios(body, base)
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
            nombre, precio, precio_base, precio_minimo, precio_2, precio_3, precio_4,
            stock, categoria_id, stock_minimo, codigo_barras, activo, bodega_codigo,
            aplica_itbis, facturar_sin_stock
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, 1, ?, 1, 1)
        """,
        (nombre, minimo, original, minimo, p2, p3, p4, body.stock, cat_id, body.codigo.strip() or None, body.bodega.strip() or "Principal"),
    )
    pid = cur.lastrowid
    conn.commit()
    conn.close()
    base.ajustar_stock_bodega(pid, 0, body.bodega.strip() or "Principal")
    return {"ok": True, "id": pid}


def _categoria_id(cur, nombre: str):
    if not nombre.strip():
        return None
    cur.execute("SELECT id FROM categorias WHERE nombre=?", (nombre.strip(),))
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute("INSERT INTO categorias (nombre) VALUES (?)", (nombre.strip(),))
    return cur.lastrowid


@app.post("/api/inventario/{producto_id}")
def editar_producto(request: Request, producto_id: int, body: ProductoIn):
    _exigir(request, "inventario")
    nombre = body.nombre.strip()
    if len(nombre) < 2:
        raise HTTPException(status_code=400, detail="Indica el nombre del producto")
    base = db()
    original, minimo, p2, p3, p4 = _validar_precios(body, base)
    conn = base.get_connection()
    cur = conn.cursor()
    cat_id = _categoria_id(cur, body.categoria)
    bodega = body.bodega.strip() or "Principal"
    cur.execute(
        """
        UPDATE productos
        SET nombre=?, precio=?, precio_base=?, precio_minimo=?, precio_2=?, precio_3=?, precio_4=?,
            stock=?, codigo_barras=?, bodega_codigo=?, categoria_id=?
        WHERE id=?
        """,
        (nombre, minimo, original, minimo, p2, p3, p4, body.stock, body.codigo.strip() or None, bodega, cat_id, producto_id),
    )
    if cur.rowcount == 0:
        conn.close()
        raise HTTPException(status_code=404, detail="Producto no encontrado")
    cur.execute(
        "SELECT 1 FROM stock_bodega WHERE producto_id=? AND bodega=?",
        (producto_id, bodega),
    )
    if cur.fetchone():
        cur.execute(
            "UPDATE stock_bodega SET cantidad=? WHERE producto_id=? AND bodega=?",
            (body.stock, producto_id, bodega),
        )
    else:
        cur.execute(
            "INSERT INTO stock_bodega (producto_id, bodega, cantidad) VALUES (?,?,?)",
            (producto_id, bodega, body.stock),
        )
    conn.commit()
    conn.close()
    return {"ok": True}


@app.get("/api/precios-mayor")
def ver_precios_mayor(request: Request):
    _exigir(request, "inventario")
    d2, d3, d4 = _descuentos_mayor(db())
    return {"p2": d2, "p3": d3, "p4": d4}


@app.post("/api/precios-mayor")
def guardar_precios_mayor(request: Request, body: DescuentosMayorIn):
    _exigir(request, "inventario")
    d2 = min(90.0, max(0.0, float(body.p2)))
    d3 = min(90.0, max(0.0, float(body.p3)))
    d4 = min(90.0, max(0.0, float(body.p4)))
    base = db()
    base.set_config("desc_precio_2", str(d2))
    base.set_config("desc_precio_3", str(d3))
    base.set_config("desc_precio_4", str(d4))
    conn = base.get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE productos
        SET precio_2 = ROUND(IFNULL(precio, 0) * (1 - ? / 100.0), 2),
            precio_3 = ROUND(IFNULL(precio, 0) * (1 - ? / 100.0), 2),
            precio_4 = ROUND(IFNULL(precio, 0) * (1 - ? / 100.0), 2)
        """,
        (d2, d3, d4),
    )
    cambiados = cur.rowcount
    conn.commit()
    conn.close()
    return {"ok": True, "p2": d2, "p3": d3, "p4": d4, "productos": cambiados}


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
    stock = filas[-1]["balance"] if filas else None
    if stock is None:
        conn = db().get_connection()
        cur = conn.cursor()
        cur.execute("SELECT IFNULL(stock,0) FROM productos WHERE id=?", (producto_id,))
        row = cur.fetchone()
        conn.close()
        stock = float(row[0] or 0) if row else 0
    return {"items": filas[-80:], "stock": stock}


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
    if len((body.password or "").strip()) < 1:
        raise HTTPException(status_code=400, detail="Indica una contraseña")
    try:
        db().create_user(body.username.strip(), body.password, body.role)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _guardar_permisos(body.username.strip(), body.modulos, body.role)
    return {"ok": True}


@app.post("/api/usuarios/editar")
def editar_usuario(request: Request, body: UsuarioIn):
    _exigir(request, "usuarios")
    if body.role not in ("admin", "user", "empleado"):
        raise HTTPException(status_code=400, detail="El rol debe ser admin, user o empleado")
    usuario = body.username.strip()
    conn = db().get_connection()
    cur = conn.cursor()
    cur.execute("SELECT id FROM users WHERE username=?", (usuario,))
    row = cur.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    if (body.password or "").strip():
        cur.execute("UPDATE users SET password=?, role=? WHERE username=?", (body.password, body.role, usuario))
    else:
        cur.execute("UPDATE users SET role=? WHERE username=?", (body.role, usuario))
    conn.commit()
    conn.close()
    _guardar_permisos(usuario, body.modulos, body.role)
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
    _exigir_alguno(request, ("clientes", "cotizaciones", "mostrador", "cobros"))
    conn = db().get_connection()
    cur = conn.cursor()
    sql = """
        SELECT c.id, c.nombre, IFNULL(c.documento,''), IFNULL(c.telefono,''), IFNULL(c.email,''),
               IFNULL(c.limite_credito,0), IFNULL(c.dias_credito,30), IFNULL(c.lista_precio,1), IFNULL(c.activo,1),
               IFNULL((SELECT SUM(s.monto) FROM saldos_cliente s WHERE s.cliente_id = c.id), 0),
               IFNULL((SELECT SUM(f.total - IFNULL((SELECT SUM(p.monto) FROM pagos_factura p WHERE p.factura_id = f.id), 0))
                       FROM facturas f
                       WHERE f.cliente_id = c.id AND f.estado = 'emitida'
                         AND f.total - IFNULL((SELECT SUM(p.monto) FROM pagos_factura p WHERE p.factura_id = f.id), 0) > 0.009), 0)
        FROM clientes c
    """
    params: list = []
    if q.strip():
        sql += " WHERE c.nombre LIKE ? OR IFNULL(c.documento,'') LIKE ?"
        params = [f"%{q.strip()}%", f"%{q.strip()}%"]
    sql += " ORDER BY IFNULL(c.activo,1) DESC, c.nombre COLLATE NOCASE LIMIT 200"
    cur.execute(sql, params)
    items = [
        {"id": i, "nombre": n, "documento": d, "telefono": t, "email": em,
         "limite_credito": round(float(lim or 0), 2), "dias_credito": int(dias or 30), "lista_precio": int(lista or 1),
         "activo": bool(int(act)), "saldo_favor": round(float(saldo or 0), 2), "deuda": round(float(deuda or 0), 2)}
        for i, n, d, t, em, lim, dias, lista, act, saldo, deuda in cur.fetchall()
    ]
    conn.close()
    return {"items": items}


def _guardar_credito_cliente(cur, cliente_id: int, body: ClienteIn) -> None:
    campos, valores = [], []
    if body.limite_credito is not None:
        campos.append("limite_credito=?")
        valores.append(round(max(0.0, float(body.limite_credito)), 2))
    if body.dias_credito is not None:
        campos.append("dias_credito=?")
        valores.append(max(0, min(365, int(body.dias_credito))))
    if body.lista_precio is not None:
        campos.append("lista_precio=?")
        valores.append(max(1, min(4, int(body.lista_precio))))
    if body.activo is not None:
        campos.append("activo=?")
        valores.append(1 if body.activo else 0)
    if campos:
        cur.execute(f"UPDATE clientes SET {', '.join(campos)} WHERE id=?", (*valores, cliente_id))


@app.post("/api/clientes")
def crear_cliente(request: Request, body: ClienteIn):
    _exigir(request, "clientes")
    if len(body.nombre.strip()) < 2:
        raise HTTPException(status_code=400, detail="Indica el nombre")
    base = db()
    nuevo = base.crear_cliente_rapido(
        body.nombre.strip(),
        body.documento.strip() or None,
        body.telefono.strip() or None,
        body.email.strip() or None,
    )
    conn = base.get_connection()
    cur = conn.cursor()
    _guardar_credito_cliente(cur, int(nuevo), body)
    conn.commit()
    conn.close()
    return {"ok": True, "id": nuevo}


@app.post("/api/clientes/{cliente_id}")
def editar_cliente(request: Request, cliente_id: int, body: ClienteIn):
    _exigir(request, "clientes")
    if len(body.nombre.strip()) < 2:
        raise HTTPException(status_code=400, detail="Indica el nombre")
    conn = db().get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE clientes
        SET nombre=?, documento=?, telefono=?, email=?
        WHERE id=?
        """,
        (
            body.nombre.strip(),
            body.documento.strip() or None,
            body.telefono.strip() or None,
            body.email.strip() or None,
            cliente_id,
        ),
    )
    if cur.rowcount == 0:
        conn.close()
        raise HTTPException(status_code=404, detail="Cliente no encontrado")
    _guardar_credito_cliente(cur, cliente_id, body)
    conn.commit()
    conn.close()
    return {"ok": True}


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
    info["precios_incluyen_itbis"] = _itbis_incluido(base)
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
    if body.precios_incluyen_itbis is not None:
        base.set_config("precios_incluyen_itbis", "1" if body.precios_incluyen_itbis else "0")
    return {"ok": True}


class EcfConfigIn(BaseModel):
    activo: bool
    ambiente: str = "testecf"


class EcfCertificadoIn(BaseModel):
    archivo_base64: str
    clave: str


class EcfSecuenciaIn(BaseModel):
    tipo: int
    siguiente: int
    hasta: int
    vencimiento: str = ""


@app.get("/api/ecf/estado")
def ecf_estado(request: Request):
    _exigir(request, "ncf")
    from ecf import servicio as ecf_srv
    base = db()
    cfg = ecf_srv.config(base)
    certificado = None
    if cfg["tiene_certificado"]:
        try:
            f = ecf_srv.firmador(base)
            certificado = {"titular": f.titular, "vence": f.vence.strftime("%Y-%m-%d"), "ok": True}
        except Exception as exc:  # noqa: BLE001
            certificado = {"ok": False, "error": str(exc)[:200]}
    conn = base.get_connection()
    cur = conn.cursor()
    cur.execute("SELECT tipo, siguiente, hasta, IFNULL(vencimiento,'') FROM ecf_secuencias ORDER BY tipo")
    secuencias = [{"tipo": t, "siguiente": s, "hasta": h, "vencimiento": v, "disponibles": max(0, int(h) - int(s) + 1)} for t, s, h, v in cur.fetchall()]
    cur.execute("SELECT estado, COUNT(*) FROM ecf_documentos GROUP BY estado")
    conteo = {e: n for e, n in cur.fetchall()}
    conn.close()
    return {**cfg, "certificado": certificado, "secuencias": secuencias, "conteo": conteo, "plazo": "2026-11-15"}


@app.post("/api/ecf/config")
def ecf_guardar_config(request: Request, body: EcfConfigIn):
    _exigir(request, "ncf")
    from ecf import dgii as _dgii
    if body.ambiente not in _dgii.AMBIENTES:
        raise HTTPException(status_code=400, detail="Ambiente no válido")
    base = db()
    if body.activo:
        rnc = _dgii.solo_digitos(base.get_config("empresa_rnc", ""))
        if len(rnc) not in (9, 11):
            raise HTTPException(status_code=400, detail="Antes de activar, pon el RNC de la empresa (9 u 11 dígitos) en Empresa y correo")
    base.set_config("ecf_activo", "1" if body.activo else "0")
    base.set_config("ecf_ambiente", body.ambiente)
    return {"ok": True}


@app.post("/api/ecf/certificado")
def ecf_certificado(request: Request, body: EcfCertificadoIn):
    _exigir(request, "ncf")
    from ecf import dgii as _dgii
    from ecf import servicio as ecf_srv
    try:
        contenido = base64.b64decode(body.archivo_base64.split(",")[-1])
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="El archivo no se pudo leer") from exc
    base = db()
    temporal = ecf_srv.ruta_certificado(base).with_suffix(".nuevo")
    temporal.write_bytes(contenido)
    try:
        firma = _dgii.Firmador(temporal, body.clave)
    except Exception as exc:  # noqa: BLE001
        temporal.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=f"No se pudo abrir el certificado con esa clave: {exc}") from exc
    temporal.replace(ecf_srv.ruta_certificado(base))
    os.chmod(ecf_srv.ruta_certificado(base), 0o600)
    base.set_config("ecf_clave", body.clave)
    return {"ok": True, "titular": firma.titular, "vence": firma.vence.strftime("%Y-%m-%d")}


@app.post("/api/ecf/secuencias")
def ecf_secuencia(request: Request, body: EcfSecuenciaIn):
    _exigir(request, "ncf")
    if body.tipo not in (31, 32, 33, 34, 41, 43, 44, 45, 46, 47):
        raise HTTPException(status_code=400, detail="Tipo de e-CF no válido")
    if body.siguiente < 1 or body.hasta < body.siguiente:
        raise HTTPException(status_code=400, detail="El rango no es válido")
    venc = body.vencimiento.strip()
    if venc:
        try:
            venc = datetime.strptime(venc[:10], "%Y-%m-%d").strftime("%d-%m-%Y")
        except ValueError:
            try:
                datetime.strptime(venc, "%d-%m-%Y")
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="Fecha de vencimiento no válida") from exc
    conn = db().get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO ecf_secuencias (tipo, siguiente, hasta, vencimiento) VALUES (?,?,?,?)
        ON CONFLICT(tipo) DO UPDATE SET siguiente=excluded.siguiente, hasta=excluded.hasta, vencimiento=excluded.vencimiento
        """,
        (body.tipo, body.siguiente, body.hasta, venc or None),
    )
    conn.commit()
    conn.close()
    return {"ok": True}


@app.get("/api/ecf/documentos")
def ecf_documentos(request: Request, estado: str = ""):
    _exigir(request, "ncf")
    conn = db().get_connection()
    cur = conn.cursor()
    sql = """
        SELECT d.id, d.creado, d.tipo, d.encf, d.total, d.estado, IFNULL(d.mensaje,''), IFNULL(d.track_id,''),
               IFNULL(f.numero,''), d.intentos, IFNULL(d.codigo_seguridad,'')
        FROM ecf_documentos d LEFT JOIN facturas f ON f.id = d.factura_id
    """
    params: list = []
    if estado.strip():
        sql += " WHERE d.estado = ?"
        params.append(estado.strip())
    sql += " ORDER BY d.id DESC LIMIT 200"
    cur.execute(sql, params)
    items = [
        {"id": i, "fecha": f, "tipo": t, "encf": e, "total": tot, "estado": est, "mensaje": m, "track_id": tr,
         "factura": n, "intentos": it, "codigo": cod}
        for i, f, t, e, tot, est, m, tr, n, it, cod in cur.fetchall()
    ]
    conn.close()
    return {"items": items}


@app.post("/api/ecf/documentos/{doc_id}/enviar")
def ecf_reenviar(request: Request, doc_id: int):
    _exigir(request, "ncf")
    from ecf import servicio as ecf_srv
    return ecf_srv.enviar(db(), doc_id)


@app.post("/api/ecf/documentos/{doc_id}/consultar")
def ecf_consultar(request: Request, doc_id: int):
    _exigir(request, "ncf")
    from ecf import servicio as ecf_srv
    return ecf_srv.consultar(db(), doc_id)


@app.get("/api/ecf/documentos/{doc_id}/xml")
def ecf_xml(request: Request, doc_id: int):
    _exigir(request, "ncf")
    conn = db().get_connection()
    cur = conn.cursor()
    cur.execute("SELECT encf, xml FROM ecf_documentos WHERE id=?", (doc_id,))
    fila = cur.fetchone()
    conn.close()
    if not fila or not fila[1]:
        raise HTTPException(status_code=404, detail="Este documento no tiene XML")
    rnc = "".join(c for c in (db().get_config("empresa_rnc", "") or "") if c.isdigit())
    return Response(fila[1], media_type="application/xml",
                    headers={"Content-Disposition": f'attachment; filename="{rnc}{fila[0]}.xml"'})


@app.post("/api/ecf/probar")
def ecf_probar(request: Request):
    _exigir(request, "ncf")
    from ecf import dgii as _dgii
    from ecf import servicio as ecf_srv
    base = db()
    try:
        firma = ecf_srv.firmador(base)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"El certificado no abre: {exc}") from exc
    if firma is None:
        raise HTTPException(status_code=400, detail="Primero carga el certificado digital")
    cliente = _dgii.ClienteDGII(ecf_srv.config(base)["ambiente"], firma)
    try:
        cliente.autenticar()
    except _dgii.ErrorDGII as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "mensaje": f"Conexión correcta con la DGII ({cliente.ambiente}): token recibido."}


@app.get("/api/ecf/qr/{factura_id}")
def ecf_qr(request: Request, factura_id: int):
    _user(request)
    from ecf import dgii as _dgii
    conn = db().get_connection()
    cur = conn.cursor()
    cur.execute("SELECT url_qr FROM ecf_documentos WHERE factura_id=? AND nota_credito_id IS NULL AND url_qr IS NOT NULL ORDER BY id DESC LIMIT 1", (factura_id,))
    fila = cur.fetchone()
    conn.close()
    if not fila:
        raise HTTPException(status_code=404, detail="Sin QR")
    return Response(_dgii.qr_svg(fila[0]), media_type="image/svg+xml")


class TerminalIn(BaseModel):
    nombre: str
    prefijo: str
    siguiente: int = 1
    bodega: str = ""
    lista_precio: int = 1
    comprobante: str = "consumidor_final"
    vendedor_id: int | None = None
    usuarios: list[str] = []
    activo: bool = True


@app.get("/api/terminales")
def listar_terminales(request: Request):
    usuario = _user(request)
    admin = "terminales" in _permisos(usuario)
    conn = db().get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT t.id, t.nombre, t.prefijo, t.siguiente, IFNULL(t.bodega,''), t.lista_precio, t.comprobante,
               t.vendedor_id, IFNULL(v.nombre,''), IFNULL(t.usuarios,''), t.activo,
               (SELECT COUNT(*) FROM facturas f WHERE f.terminal_id=t.id AND f.estado='emitida' AND date(f.fecha)=date('now','localtime')),
               (SELECT IFNULL(SUM(f.total),0) FROM facturas f WHERE f.terminal_id=t.id AND f.estado='emitida' AND date(f.fecha)=date('now','localtime'))
        FROM terminales t LEFT JOIN vendedores v ON v.id=t.vendedor_id
        ORDER BY t.nombre COLLATE NOCASE
        """
    )
    items = []
    for fila in cur.fetchall():
        usuarios = [u for u in fila[9].split(",") if u]
        disponible = bool(fila[10]) and (not usuarios or usuario.lower() in [u.lower() for u in usuarios])
        if not admin and not disponible:
            continue
        items.append({
            "id": fila[0], "nombre": fila[1], "prefijo": fila[2], "siguiente": fila[3], "bodega": fila[4],
            "lista_precio": fila[5], "comprobante": fila[6], "vendedor_id": fila[7], "vendedor": fila[8],
            "usuarios": usuarios, "activo": bool(fila[10]), "disponible": disponible,
            "facturas_hoy": fila[11], "ventas_hoy": round(float(fila[12] or 0), 2),
        })
    extra = {}
    if admin:
        cur.execute("SELECT codigo FROM bodegas ORDER BY codigo COLLATE NOCASE")
        extra["bodegas"] = [r[0] for r in cur.fetchall()]
        cur.execute("SELECT username FROM users ORDER BY username COLLATE NOCASE")
        extra["usuarios"] = [r[0] for r in cur.fetchall()]
    conn.close()
    return {"items": items, **extra}


def _guardar_terminal(terminal_id: int | None, body: TerminalIn) -> dict:
    nombre = body.nombre.strip()
    prefijo = "".join(c for c in body.prefijo.strip().upper() if c.isalnum())[:6]
    if len(nombre) < 2:
        raise HTTPException(status_code=400, detail="Escribe el nombre de la terminal")
    if not prefijo:
        raise HTTPException(status_code=400, detail="El prefijo usa letras o números, por ejemplo C1")
    if body.siguiente < 1:
        raise HTTPException(status_code=400, detail="La numeración empieza en 1 o más")
    comprobante = body.comprobante if body.comprobante in ("consumidor_final", "credito_fiscal") else "consumidor_final"
    lista = max(1, min(4, int(body.lista_precio or 1)))
    usuarios = ",".join(sorted({u.strip() for u in body.usuarios if u.strip()}))
    conn = db().get_connection()
    cur = conn.cursor()
    if body.bodega.strip():
        cur.execute("SELECT 1 FROM bodegas WHERE codigo=?", (body.bodega.strip(),))
        if not cur.fetchone():
            conn.close()
            raise HTTPException(status_code=400, detail="Esa bodega no existe")
    valores = (nombre, prefijo, body.siguiente, body.bodega.strip() or None, lista, comprobante, body.vendedor_id or None, usuarios, 1 if body.activo else 0)
    try:
        if terminal_id is None:
            cur.execute(
                """INSERT INTO terminales (nombre, prefijo, siguiente, bodega, lista_precio, comprobante, vendedor_id, usuarios, activo)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                valores,
            )
            terminal_id = cur.lastrowid
        else:
            cur.execute(
                """UPDATE terminales SET nombre=?, prefijo=?, siguiente=?, bodega=?, lista_precio=?, comprobante=?,
                   vendedor_id=?, usuarios=?, activo=? WHERE id=?""",
                valores + (terminal_id,),
            )
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="Terminal no encontrada")
        conn.commit()
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        raise HTTPException(status_code=400, detail="Ya existe una terminal con ese nombre o prefijo") from exc
    finally:
        conn.close()
    return {"ok": True, "id": terminal_id}


@app.post("/api/terminales")
def crear_terminal(request: Request, body: TerminalIn):
    _exigir(request, "terminales")
    return _guardar_terminal(None, body)


@app.post("/api/terminales/{terminal_id}")
def editar_terminal(request: Request, terminal_id: int, body: TerminalIn):
    _exigir(request, "terminales")
    return _guardar_terminal(terminal_id, body)


class VendedorIn(BaseModel):
    nombre: str
    documento: str = ""
    activo: bool = True


@app.get("/api/vendedores")
def listar_vendedores(request: Request):
    _exigir_alguno(request, ("mostrador", "vendedores", "reportes"))
    conn = db().get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT v.id, v.nombre, IFNULL(v.documento,''), IFNULL(v.activo,1),
               (SELECT COUNT(*) FROM facturas f WHERE f.vendedor_id = v.id AND f.estado='emitida'
                  AND date(f.fecha) >= date('now','start of month')),
               (SELECT IFNULL(SUM(f.total),0) FROM facturas f WHERE f.vendedor_id = v.id AND f.estado='emitida'
                  AND date(f.fecha) >= date('now','start of month'))
        FROM vendedores v
        ORDER BY IFNULL(v.activo,1) DESC, v.nombre COLLATE NOCASE
        """
    )
    items = [
        {"id": i, "nombre": n, "documento": d, "activo": bool(int(a)), "facturas_mes": int(fm or 0), "ventas_mes": round(float(vm or 0), 2)}
        for i, n, d, a, fm, vm in cur.fetchall()
    ]
    conn.close()
    return {"items": items}


@app.post("/api/vendedores")
def crear_vendedor(request: Request, body: VendedorIn):
    _exigir(request, "vendedores")
    if len(body.nombre.strip()) < 2:
        raise HTTPException(status_code=400, detail="Indica el nombre del vendedor")
    conn = db().get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO vendedores (nombre, documento, activo) VALUES (?,?,?)",
        (body.nombre.strip(), body.documento.strip() or None, 1 if body.activo else 0),
    )
    nuevo = cur.lastrowid
    conn.commit()
    conn.close()
    return {"ok": True, "id": nuevo}


@app.post("/api/vendedores/{vendedor_id}")
def editar_vendedor(request: Request, vendedor_id: int, body: VendedorIn):
    _exigir(request, "vendedores")
    if len(body.nombre.strip()) < 2:
        raise HTTPException(status_code=400, detail="Indica el nombre del vendedor")
    conn = db().get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE vendedores SET nombre=?, documento=?, activo=? WHERE id=?",
        (body.nombre.strip(), body.documento.strip() or None, 1 if body.activo else 0, vendedor_id),
    )
    if cur.rowcount == 0:
        conn.close()
        raise HTTPException(status_code=404, detail="Vendedor no encontrado")
    conn.commit()
    conn.close()
    return {"ok": True}


if _STATIC.is_dir():
    app.mount("/estaticos", StaticFiles(directory=str(_STATIC)), name="estaticos")


HTML = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Facturación</title>
<script>document.documentElement.dataset.tema = localStorage.getItem("tema") || (matchMedia("(prefers-color-scheme: light)").matches ? "dia" : "noche");</script>
<style>
  @font-face {
    font-family: "Archivo";
    src: url("/estaticos/fonts/archivo-wdth.woff2") format("woff2-variations"), url("/estaticos/fonts/archivo-wdth.woff2") format("woff2");
    font-weight: 100 900; font-stretch: 62% 125%; font-style: normal; font-display: swap;
  }
  :root {
    color-scheme: dark;
    --ground:#0e0d0c; --surface:#181716; --raised:#242220; --active:#2c2a27;
    --rule:#262422; --control:#4a4743;
    --text:#f3f2f0; --muted:#b4b0a8; --faint:#97938b;
    --r-venta:#ffd23f; --r-venta-ink:#1f1803;
    --r-caja:#2ec4b6;  --r-caja-ink:#03201c;
    --r-inv:#5aa9e6;   --r-inv-ink:#061a2c;
    --r-ana:#ff7a45;   --r-ana-ink:#2a0e02;
    --r-admin:#f472b6; --r-admin-ink:#2b0717;
    --data: var(--r-venta);
    --serie-2:#e8e5df; --serie-3:#8f8b84; --serie-4:#5a5752;
    --modal-fondo: rgba(5,5,4,.72); --flotante:#2a2826;
    --positive:#5fd68f; --negative:#ff6b6b; --warning:#f5a524;
    --ok: var(--positive); --danger: var(--negative); --accent: var(--r-venta);
    --pill-warning-bg:#2b2414; --pill-warning-ink:#f5a524;
    --radius-card:16px; --radius-inner:10px; --radius-control:999px;
    --gap:14px; --pad:22px;
    --font: "Archivo", system-ui, -apple-system, "Segoe UI", sans-serif;
    --ease-out: cubic-bezier(0.23, 1, 0.32, 1);
    --ease-in-out: cubic-bezier(0.77, 0, 0.175, 1);
    --ease-drawer: cubic-bezier(0.32, 0.72, 0, 1);
  }
  [data-grupo="venta"] { --g:var(--r-venta); --g-ink:var(--r-venta-ink); }
  [data-grupo="caja"] { --g:var(--r-caja); --g-ink:var(--r-caja-ink); }
  [data-grupo="inventario"] { --g:var(--r-inv); --g-ink:var(--r-inv-ink); }
  [data-grupo="analisis"] { --g:var(--r-ana); --g-ink:var(--r-ana-ink); }
  [data-grupo="admin"] { --g:var(--r-admin); --g-ink:var(--r-admin-ink); }

  * { box-sizing: border-box; }
  html { scrollbar-color: #3a3733 transparent; scrollbar-width: thin; }
  ::-webkit-scrollbar { width:10px; height:10px; }
  ::-webkit-scrollbar-thumb { background:#3a3733; border-radius:99px; border:3px solid var(--ground); }
  ::-webkit-scrollbar-track { background:transparent; }
  ::selection { background:var(--g, var(--r-venta)); color:var(--g-ink, var(--r-venta-ink)); }
  body { margin:0; font-family:var(--font); font-stretch:100%; background:var(--ground); color:var(--text); font-size:14px; line-height:1.45; -webkit-font-smoothing:antialiased; }
  button, input, select { font:inherit; }
  input, select { caret-color:var(--g, var(--r-venta)); }
  input[type="checkbox"], input[type="radio"] { accent-color:var(--g, var(--r-venta)); height:auto; }
  .num, td, #tot, #cambio, .kpi b, .card .precio, .barrow b { font-variant-numeric: tabular-nums; }

  button { background:var(--text); color:var(--ground); border:0; border-radius:var(--radius-control); padding:0 18px; height:36px; cursor:pointer; font-weight:600; transition: transform 140ms var(--ease-out), background-color 160ms ease, color 160ms ease, opacity 160ms ease; }
  button:active { transform: scale(0.97); }
  button.sec { background:var(--raised); color:var(--text); }
  button:disabled, button:disabled:active { opacity:.35; cursor:not-allowed; transform:none; }
  @media (hover:hover) and (pointer:fine) {
    button:hover { background:#ffffff; }
    button.sec:hover { background:var(--active); }
  }
  :focus-visible { outline:2px solid var(--g, var(--r-venta)); outline-offset:2px; }
  input:focus-visible, select:focus-visible { outline:2px solid var(--g, var(--r-venta)); outline-offset:0; border-color:transparent; }
  input, select { background:var(--raised); color:var(--text); border:1px solid transparent; border-radius:var(--radius-inner); height:40px; padding:0 12px; transition: border-color 160ms ease, background-color 160ms ease; }
  @media (hover:hover) and (pointer:fine) { input:hover, select:hover { border-color:var(--control); } }
  input::placeholder { color:var(--faint); }

  /* Login */
  #login { width:min(380px, calc(100% - 32px)); margin:14vh auto; background:var(--surface); padding:32px 28px 28px; border-radius:var(--radius-card); display:grid; gap:14px; }
  #login .marca { display:flex; align-items:center; gap:12px; margin-bottom:6px; }
  #login h2 { margin:0; font-size:20px; font-weight:700; }
  #login p { margin:0; color:var(--muted); }
  #login label { display:grid; gap:6px; font-size:13px; color:var(--muted); }
  #login button { height:44px; margin-top:6px; }
  #login { animation: llegar 520ms var(--ease-out) both; }
  #login .mono { animation: pintar 560ms var(--ease-in-out) 140ms both; }
  @keyframes llegar { from { opacity:0; transform: translateY(12px) scale(0.98); } }
  @keyframes pintar { from { clip-path: inset(0 100% 0 0 round 10px); } to { clip-path: inset(0 0 0 0 round 10px); } }
  .mono { flex:none; display:grid; place-items:center; width:40px; height:40px; border-radius:var(--radius-inner); background:var(--r-venta); color:var(--r-venta-ink); font-weight:800; font-stretch:125%; font-size:15px; letter-spacing:0; }

  /* Shell: barra lateral y barra superior sobre el mismo fondo, divididas por reglas de 1 px */
  #app { display:none; min-height:100vh; }
  .shell { display:flex; min-height:100vh; }
  .side { width:224px; flex-shrink:0; padding:14px 12px 16px; display:flex; flex-direction:column; gap:1px; border-right:1px solid var(--rule); max-height:100vh; overflow-y:auto; position:sticky; top:0; background:var(--ground); }
  .brand { display:flex; align-items:center; gap:10px; padding:4px 8px 10px; }
  .brand b { display:block; font-size:14px; font-weight:600; }
  .brand small { display:block; color:var(--faint); font-size:12px; }
  .nav-grupo { display:flex; align-items:center; gap:8px; font-size:12px; font-weight:500; color:var(--faint); padding:14px 10px 4px; }
  .nav-grupo::before { content:""; width:8px; height:8px; border-radius:2px; background:var(--g); }
  button.nav { width:100%; height:34px; text-align:left; background:transparent; color:#bbb7af; font-weight:500; border-radius:8px; padding:0 10px; }
  button.nav:active { transform:none; }
  @media (hover:hover) and (pointer:fine) { button.nav:hover { background:var(--surface); color:var(--text); } }
  button.nav.on { color:var(--g-ink); background:var(--g); font-weight:600; }
  #view-form button:not(.sec):not(.nav), .modal-card button:not(.sec) { background:var(--g); color:var(--g-ink); }
  @media (hover:hover) and (pointer:fine) { #view-form button:not(.sec):not(.nav):hover, .modal-card button:not(.sec):hover { background:var(--g); filter:brightness(1.08); } }

  .main { flex:1; min-width:0; display:flex; flex-direction:column; }
  .topbar { height:64px; flex-shrink:0; display:flex; align-items:center; gap:14px; padding:0 22px; border-bottom:1px solid var(--rule); position:sticky; top:0; z-index:20; background:var(--ground); }
  .rotulo { margin:0; display:inline-flex; align-items:center; height:40px; padding:0 16px; border-radius:var(--radius-inner); background:var(--g); color:var(--g-ink); font-size:19px; font-weight:800; font-stretch:125%; text-transform:uppercase; line-height:1; white-space:nowrap; transition: background-color 200ms ease, color 200ms ease; }
  .topbar-der { margin-left:auto; display:flex; align-items:center; gap:12px; min-width:0; }
  #top { color:var(--muted); font-size:13px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  #top .estado { display:inline-flex; align-items:center; gap:6px; }
  #top .estado::before { content:""; width:7px; height:7px; border-radius:50%; background:var(--faint); }
  #top .estado.abierta::before { background:var(--positive); }
  #btnMenu { display:none; }
  .salir-menu { display:none; }

  .work { flex:1; display:flex; gap:var(--gap); min-width:0; padding:var(--gap) 22px 22px; align-items:flex-start; }
  .catalog { flex:1; min-width:0; }
  .toolbar { display:flex; gap:8px; margin-bottom:var(--gap); flex-wrap:wrap; align-items:center; }
  .toolbar input { flex:1; min-width:200px; height:44px; font-size:15px; padding:0 16px; }
  .toolbar select { height:44px; }
  .toolbar button { height:44px; }

  /* Catálogo */
  .grid { display:grid; grid-template-columns: repeat(auto-fill, minmax(176px,1fr)); gap:var(--gap); align-content:start; }
  .card { position:relative; height:auto; border-radius:var(--radius-card); padding:0; background:var(--surface); color:var(--text); display:flex; flex-direction:column; align-items:stretch; overflow:hidden; text-align:left; font-weight:400; transition: transform 160ms var(--ease-out), background-color 160ms ease; }
  .card:active { transform: scale(0.98); }
  @media (hover:hover) and (pointer:fine) { .card:hover { background:var(--raised); } }
  .card .foto { display:block; height:128px; background:var(--raised) center/cover no-repeat; }
  .card .info { padding:10px 14px 14px; display:grid; gap:2px; }
  .card .info b { font-weight:600; line-height:1.3; }
  .card .precio { font-weight:600; font-size:15px; }
  .card .stock { color:var(--faint); font-size:12px; }
  .card.bajo .stock { color:var(--warning); }
  .badge { position:absolute; top:8px; left:8px; background:var(--pill-warning-bg); color:var(--pill-warning-ink); font-size:12px; font-weight:600; height:22px; display:inline-flex; align-items:center; padding:0 9px; border-radius:99px; }
  .vacio { color:var(--muted); padding:24px 0; grid-column:1 / -1; margin:0; }
  .bajo { outline:0; }

  /* Ticket */
  .rail { width:340px; flex-shrink:0; background:var(--surface); border-radius:var(--radius-card); padding:18px 16px; position:sticky; top:calc(64px + var(--gap)); max-height:calc(100vh - 64px - var(--gap) * 2); overflow-y:auto; display:flex; flex-direction:column; }
  .rail > * { flex-shrink:0; }
  .rail h3 { margin:0 0 4px; font-size:16px; font-weight:600; }
  ul { list-style:none; padding:0; margin:0; max-height:280px; overflow:auto; }
  li { display:flex; justify-content:space-between; align-items:center; gap:8px; padding:8px 0; border-bottom:1px solid var(--rule); }
  #lines { overflow-x:hidden; }
  #lines li:last-child { border-bottom:0; }
  #lines li.vacio { display:block; border:0; padding:18px 0; text-align:center; font-size:13px; color:var(--faint); }
  #lines li > span:first-child { display:flex; align-items:center; gap:6px; min-width:0; flex:1; line-height:1.3; }
  #lines li .nombre { min-width:0; overflow-wrap:anywhere; margin-left:2px; }
  #lines li > span:last-child { white-space:nowrap; font-weight:600; }
  #lines li button.sec { width:26px; height:26px; padding:0; border-radius:8px; flex:none; display:inline-grid; place-items:center; }
  #lines li .num { display:inline-block; min-width:1.4em; text-align:center; }
  .desglose { display:grid; margin-top:4px; }
  .desglose div { display:flex; justify-content:space-between; gap:8px; padding:7px 0; border-top:1px solid var(--rule); font-size:13px; color:var(--muted); }
  .desglose b { color:var(--text); font-weight:600; font-variant-numeric:tabular-nums; }
  .placa-total { position:relative; overflow:hidden; background:var(--r-venta); color:var(--r-venta-ink); border-radius:12px; padding:12px 16px 14px; margin:12px 0 6px; }
  .placa-total small { display:block; font-size:12px; font-weight:600; }
  #tot { font-size:34px; font-weight:800; font-stretch:118%; letter-spacing:-0.01em; margin:2px 0 0; line-height:1.05; white-space:nowrap; }
  #tot.largo, .placa-cobrado b.largo { font-size:28px; }
  .placa-cobrado { position:absolute; inset:0; background:var(--r-caja); color:var(--r-caja-ink); padding:12px 16px 14px; clip-path: inset(0 100% 0 0); pointer-events:none; }
  .placa-cobrado small { display:block; font-size:12px; font-weight:600; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .placa-cobrado b { display:block; font-size:34px; font-weight:800; font-stretch:118%; letter-spacing:-0.01em; line-height:1.05; margin-top:2px; white-space:nowrap; }
  .pagos { display:grid; grid-template-columns:1fr 1fr; gap:10px; margin-top:8px; }
  .pagos .ancho { grid-column:1 / -1; }
  .pagos label, .campo { display:flex; flex-direction:column; gap:6px; font-size:12px; color:var(--muted); min-width:0; }
  .pagos select, .pagos input { font-size:13px; min-width:0; width:100%; }
  .campo-cab { display:flex; justify-content:space-between; align-items:center; gap:8px; }
  button.mini { height:24px; padding:0 10px; font-size:12px; }
  #pagosDyn { display:contents; }
  .info-cliente { font-size:12px; color:var(--muted); display:flex; flex-wrap:wrap; gap:4px 12px; }
  .info-cliente b { color:var(--text); font-weight:600; font-variant-numeric:tabular-nums; }
  .info-cliente .alerta { color:var(--negative); }
  label.check, .form label.check { flex-direction:row; align-items:center; gap:8px; font-size:13px; color:var(--text); cursor:pointer; }
  label.check input { width:18px; height:18px; flex:none; }
  #cambio.pendiente { color:var(--negative); font-weight:600; }
  .pill { display:inline-flex; align-items:center; height:22px; padding:0 9px; border-radius:99px; font-size:12px; font-weight:600; white-space:nowrap; background:var(--raised); color:var(--muted); }
  .pill.mal { background:color-mix(in srgb, var(--negative) 16%, transparent); color:var(--negative); }
  .pill.bien { background:color-mix(in srgb, var(--positive) 16%, transparent); color:var(--positive); }
  .pill.aviso { background:color-mix(in srgb, var(--warning) 18%, transparent); color:var(--warning); }
  a.boton-link { display:inline-flex; align-items:center; height:30px; padding:0 12px; border-radius:999px; background:var(--raised); color:var(--text); font-size:12px; font-weight:600; text-decoration:none; }
  .faltan { margin-top:16px; padding:16px 18px; border-radius:14px; background:var(--raised); }
  .faltan ol { margin:6px 0 0; padding-left:20px; color:var(--muted); line-height:1.6; }
  #barraRed { position:fixed; left:50%; bottom:18px; transform:translateX(-50%); z-index:60; display:flex; align-items:center; gap:12px;
    max-width:calc(100vw - 32px); padding:10px 12px 10px 16px; border-radius:999px; background:var(--raised); color:var(--text);
    font-size:13px; box-shadow:0 10px 30px rgba(0,0,0,.35); }
  #barraRed[hidden] { display:none; }
  #barraRed.sin-red { background:var(--warning); color:#1b1300; }
  #barraRed.con-error { background:var(--negative); color:#1a0505; }
  #barraRed button { height:30px; padding:0 12px; font-size:12px; }
  .sin-corte { white-space:nowrap; }
  .estado-ecf { display:grid; gap:4px; justify-items:start; min-width:200px; }
  .estado-ecf .hint { font-size:12px; line-height:1.4; }
  .faltan li { display:list-item; padding:2px 0; border:0; }
  .faltan b { color:var(--text); }
  .form input[type=file] { padding:9px 10px; font-size:12px; }
  #view-form h3 { margin:22px 0 4px; font-size:15px; }
  #cambio { margin:2px 0; color:var(--muted); }
  #btnCobrar { height:52px; font-size:17px; font-weight:700; position:relative; }
  #btnCobrar.cobrando { color:transparent; }
  #btnCobrar.cobrando::after { content:"Cobrando…"; position:absolute; inset:0; display:grid; place-items:center; color:var(--ground); }
  .opciones { font-size:12px; color:var(--faint); }
  .opciones summary { cursor:pointer; padding:4px 0; }
  .opciones label { margin-top:8px; }
  .opciones[open] summary { color:var(--text); }
  .ticket-acciones { display:grid; grid-template-columns:1fr 1fr; gap:8px; margin-top:8px; }
  .ticket-acciones button { width:100%; }
  #esperas { margin-top:14px; display:flex; flex-direction:column; gap:6px; max-height:220px; overflow-y:auto; }
  #esperas h4 { margin:0 0 2px; font-size:12px; font-weight:500; color:var(--faint); }
  #esperas button.sec { width:100%; text-align:left; border-radius:var(--radius-inner); height:40px; }
  .msg { color:var(--warning); min-height:1.2em; font-size:13px; }
  #msg, #formMsg { position:absolute; width:1px; height:1px; overflow:hidden; clip:rect(0 0 0 0); white-space:nowrap; min-height:0; }

  /* Vistas de trabajo */
  .panel { background:var(--surface); border-radius:var(--radius-card); padding:var(--pad); }
  #formTitle { display:none; }
  .panel p.hint, p.hint { color:var(--muted); margin:0 0 16px; max-width:75ch; }
  .form { display:grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap:12px; align-items:end; }
  .form label { display:flex; flex-direction:column; gap:6px; font-size:12px; color:var(--muted); }
  .form .wide { grid-column: 1 / -1; }
  .form > button { width:auto; justify-self:start; min-width:140px; }
  .tabla-marco { max-width:100%; overflow-x:auto; }
  table { width:100%; border-collapse:collapse; margin-top:16px; }
  th, td { text-align:left; padding:11px 8px; border-bottom:1px solid var(--rule); vertical-align:top; }
  th { color:var(--faint); font-weight:500; font-size:12px; border-bottom-color:var(--control); }
  td.num, th.num { text-align:right; white-space:nowrap; }
  tbody tr { transition: background-color 120ms ease; }
  @media (hover:hover) and (pointer:fine) { tbody tr:hover { background:rgba(255,255,255,.025); } }
  .acciones { display:flex; flex-wrap:wrap; gap:6px; max-width:280px; }
  td:last-child .acciones { margin-left:auto; justify-content:flex-end; }
  .acciones { align-items:center; }
  details.mas { display:inline-block; }
  details.mas > summary { list-style:none; cursor:pointer; height:30px; padding:0 12px; border-radius:999px; background:var(--raised); color:var(--text); display:inline-flex; align-items:center; font-size:12px; font-weight:600; transition: background-color 160ms ease, transform 140ms var(--ease-out); }
  details.mas > summary::-webkit-details-marker { display:none; }
  details.mas > summary:active { transform:scale(0.97); }
  @media (hover:hover) and (pointer:fine) { details.mas > summary:hover { background:var(--active); } }
  details.mas .menu { display:none; }
  details.mas[open] .menu { display:grid; position:fixed; z-index:45; min-width:180px; background:#2a2826; border-radius:12px; padding:6px; box-shadow:0 16px 36px rgba(0,0,0,.45); /* taste-skill-ignore: menú flotante */ transform-origin:top right; animation: menuEntra 160ms var(--ease-out); }
  @keyframes menuEntra { from { opacity:0; transform:scale(0.96) translateY(-4px); } }
  details.mas .menu button { background:transparent; color:var(--text); text-align:left; height:36px; border-radius:8px; width:100%; font-weight:500; font-size:13px; }
  @media (hover:hover) and (pointer:fine) { details.mas .menu button:hover { background:var(--raised); } }
  button.peligro, .menu button.peligro { color:var(--negative); }
  #view-form button.peligro:not(.sec), .modal-card button.peligro { background:var(--negative); color:#1a0505; }
  td:last-child:has(.acciones) { text-align:right; }
  .acciones button { height:30px; padding:0 12px; font-size:12px; }
  #histTabla, #kxTabla, #cliTabla, #cuadreTabla { overflow-x:auto; }
  .doc { background:var(--raised); border-radius:12px; padding:16px; margin:0 0 16px; }
  .doc h3 { margin:0 0 6px; }
  .esqueleto { display:grid; gap:10px; padding-top:4px; }
  .esqueleto span { display:block; height:14px; border-radius:6px; background:var(--raised); animation: latido 1.1s ease-in-out infinite alternate; }
  .esqueleto span:nth-child(1) { width:38%; height:20px; }
  .esqueleto span:nth-child(2) { width:72%; }
  .esqueleto span:nth-child(3) { width:64%; }
  .esqueleto span:nth-child(4) { width:80%; }
  @keyframes latido { from { opacity:.55; } to { opacity:1; } }

  /* Indicadores */
  .dash-head { display:flex; justify-content:space-between; align-items:center; gap:16px; margin-bottom:var(--gap); flex-wrap:wrap; }
  .dash-head p { margin:0; color:var(--muted); }
  .rangos { display:flex; gap:2px; background:var(--surface); border-radius:999px; padding:3px; }
  .rangos button { background:transparent; color:var(--muted); height:30px; padding:0 14px; }
  .rangos button.on { background:var(--g); color:var(--g-ink); }
  #view-form .rangos button:not(.on) { background:transparent; color:var(--muted); }
  @media (hover:hover) and (pointer:fine) { #view-form .rangos button:not(.on):hover { background:var(--raised); color:var(--text); filter:none; } }
  .kpis { display:grid; grid-template-columns:repeat(4, minmax(0,1fr)); background:var(--surface); border-radius:var(--radius-card); }
  .kpi { padding:18px 20px; display:flex; flex-direction:column; gap:6px; min-height:104px; }
  .kpi + .kpi { border-left:1px solid var(--rule); }
  .kpi span { font-size:13px; color:var(--muted); }
  .kpi b { font-size:24px; font-weight:600; letter-spacing:-0.01em; white-space:nowrap; }
  .kpi:first-child b { font-size:32px; font-weight:700; font-stretch:112%; }
  .kpi em { font-style:normal; font-size:12px; color:var(--faint); }
  .kpi em .pos { color:var(--positive); }
  .kpi em .neg, .kpi b.neg { color:var(--negative); }
  .kpis.tres { grid-template-columns:repeat(3, minmax(0,1fr)); }
  .dash-fila { display:grid; grid-template-columns: 1.5fr .9fr; gap:var(--gap); margin-top:var(--gap); align-items:start; }
  .dash-fila.par { grid-template-columns: 1fr 1fr; }
  .dash-col { display:grid; gap:var(--gap); }
  .operacion { margin-top:var(--gap); grid-template-columns: repeat(3, minmax(0,1fr)); }
  .operacion h3 { grid-column:1 / -1; margin-bottom:0; }
  .dash-card { background:var(--surface); border-radius:var(--radius-card); padding:18px 20px; }
  .dash-card h3 { margin:0 0 12px; font-size:15px; font-weight:600; }
  .dash-card svg { width:100%; height:auto; display:block; overflow:visible; }
  .barrow { display:grid; grid-template-columns: minmax(0,1.15fr) minmax(48px,1.2fr) auto; gap:10px; align-items:center; margin:10px 0; font-size:13px; }
  .barrow span { line-height:1.3; }
  .barrow b { font-weight:600; }
  .track { height:6px; background:var(--raised); border-radius:99px; overflow:hidden; }
  .track div { height:100%; border-radius:99px; transform-origin:left center; }
  .anillo { display:flex; align-items:center; gap:18px; flex-wrap:wrap; }
  .anillo svg { width:120px; height:120px; flex:none; }
  .leyenda { display:grid; gap:6px; font-size:13px; color:var(--muted); flex:1; min-width:150px; }
  .leyenda span { display:flex; align-items:center; gap:8px; }
  .leyenda i { width:8px; height:8px; border-radius:2px; flex:none; }
  .leyenda b { margin-left:auto; color:var(--text); font-weight:600; font-variant-numeric:tabular-nums; }
  .side-stats { display:grid; gap:10px; }
  .side-stats div { padding:10px 0; border-top:1px solid var(--rule); }
  .operacion div { border-top:0; border-left:1px solid var(--rule); padding:2px 16px; }
  .operacion h3 + div { border-left:0; padding-left:0; }
  .side-stats b { display:block; font-size:17px; margin-top:2px; font-weight:600; }
  .side-stats span { color:var(--muted); font-size:12px; }
  #view-form.es-dash { background:transparent; padding:0; }
  #view-form.es-dash > .hint, #view-form.es-dash > #formMsg { display:none; }

  /* Ventana modal */
  .modal { display:none; position:fixed; inset:0; z-index:50; background:var(--modal-fondo); align-items:flex-start; justify-content:center; padding:56px 16px 24px; overflow:auto; opacity:0; transition: opacity 160ms ease, display 160ms allow-discrete; }
  .modal.on { display:flex; opacity:1; transition: opacity 220ms var(--ease-out), display 220ms allow-discrete; }
  @starting-style { .modal.on { opacity:0; } }
  .modal-card { width:min(760px, 100%); background:var(--surface); border-radius:var(--radius-card); padding:22px 22px 10px; box-shadow:0 24px 48px rgba(0,0,0,.45); /* taste-skill-ignore: diálogo flotante */ transform: translateY(8px) scale(0.97); opacity:0; transition: transform 160ms ease, opacity 140ms ease; }
  .modal.on .modal-card { transform:none; opacity:1; transition: transform 260ms var(--ease-out), opacity 200ms var(--ease-out); }
  @starting-style { .modal.on .modal-card { transform: translateY(8px) scale(0.97); opacity:0; } }
  .modal-head { display:flex; align-items:center; justify-content:space-between; gap:12px; margin-bottom:14px; }
  .modal-head h3 { margin:0; font-size:18px; }
  #modalCuerpo .form { margin-top:0; }

  /* Avisos */
  #avisos { position:fixed; left:calc(224px + 22px); bottom:20px; z-index:60; display:flex; flex-direction:column-reverse; gap:8px; width:min(360px, calc(100% - 40px)); pointer-events:none; }
  .aviso { pointer-events:auto; display:flex; gap:10px; align-items:flex-start; background:#2a2826; color:var(--text); border-radius:12px; padding:12px 14px; font-size:13px; box-shadow:0 12px 32px rgba(0,0,0,.4); /* taste-skill-ignore: aviso flotante */ transform:none; opacity:1; transition: transform 400ms ease, opacity 400ms ease; }
  @starting-style { .aviso { transform: translateY(100%); opacity:0; } }
  .aviso::before { content:""; width:8px; height:8px; margin-top:5px; border-radius:2px; flex:none; background:var(--faint); }
  .aviso[data-tipo="ok"]::before { background:var(--positive); }
  .aviso[data-tipo="error"]::before { background:var(--negative); }
  .aviso.saliendo { transform: translateY(8px); opacity:0; transition: transform 200ms var(--ease-out), opacity 180ms ease; }

  #recibo { display:none; }
  @media print {
    body * { visibility: hidden; }
    #recibo, #recibo * { visibility: visible; }
    #recibo { display:block; position:absolute; left:0; top:0; width:80mm; color:#111; background:#fff; padding:12px; }
    #recibo .qr svg { width:26mm; height:26mm; display:block; margin:6px 0; }
  }

  /* Tablet y teléfono: la barra lateral se pliega en una hoja de menú */
  .barra-ticket { display:none; }
  @media (max-width: 1100px) {
    #avisos { left:16px; right:16px; bottom:84px; width:auto; max-width:420px; margin:0 auto; }
    .barra-ticket { display:flex; position:fixed; left:16px; right:16px; bottom:12px; z-index:30; height:60px; align-items:center; justify-content:space-between; gap:12px; padding:0 8px 0 18px; border-radius:14px; background:var(--r-venta); color:var(--r-venta-ink); font-weight:700; font-stretch:112%; font-variant-numeric:tabular-nums; box-shadow:0 10px 30px rgba(0,0,0,.45); /* taste-skill-ignore: barra fija flotante */ transform: translateY(calc(100% + 24px)); visibility:hidden; transition: transform 240ms var(--ease-out), visibility 0s linear 240ms; }
    .barra-ticket.on { transform:none; visibility:visible; transition: transform 340ms var(--ease-drawer); }
    .barra-ticket button { background:var(--r-venta-ink); color:var(--r-venta); height:44px; }
    body.con-barra .work { padding-bottom:88px; }
    #btnMenu { display:inline-flex; align-items:center; }
    .salir-menu { display:block; margin:12px 10px 0; width:calc(100% - 20px); }
    .side { position:fixed; inset:0 auto 0 0; z-index:40; width:min(300px, 86vw); max-height:none; transform: translateX(-100%); transition: transform 300ms var(--ease-drawer), visibility 0s linear 300ms; visibility:hidden; box-shadow:0 0 48px rgba(0,0,0,.5); /* taste-skill-ignore: hoja de menú flotante */ }
    body.menu-abierto .side { transform:none; visibility:visible; transition: transform 340ms var(--ease-drawer); }
    .velo { position:fixed; inset:0; z-index:35; background:rgba(5,5,4,.6); opacity:0; visibility:hidden; pointer-events:none; transition: opacity 200ms ease, visibility 0s linear 200ms; }
    body.menu-abierto .velo { opacity:1; visibility:visible; pointer-events:auto; transition: opacity 240ms ease; }
    .work { flex-direction:column; align-items:stretch; }
    .rail { width:auto; position:static; max-height:none; }
    .kpis { grid-template-columns:repeat(2, minmax(0,1fr)); }
    .kpi:nth-child(3) { border-left:0; }
    .kpi:nth-child(n+3) { border-top:1px solid var(--rule); }
    .kpis.tres .kpi:nth-child(3) { border-left:1px solid var(--rule); }
    .kpis.tres .kpi:nth-child(n+3) { border-top:0; }
    .dash-fila, .dash-fila.par, .operacion { grid-template-columns:1fr; }
    .operacion div, .operacion h3 + div { border-left:0; border-top:1px solid var(--rule); padding:10px 0; }
  }
  @media (max-width: 640px) {
    .topbar { padding:0 16px; gap:10px; height:56px; }
    .rotulo { font-size:15px; height:34px; padding:0 12px; min-width:0; overflow:hidden; text-overflow:ellipsis; display:block; line-height:34px; }
    #top { font-size:12px; }
    .rotulo { flex:0 1 auto; }
    .topbar-der { flex:none; }
    #top .quien, .salir-barra, .tema-barra { display:none; }
    .toolbar { display:grid; grid-template-columns:1fr 1fr; }
    .toolbar input { grid-column:1 / -1; min-width:0; }
    .work { padding:12px 16px 18px; }
    .grid { grid-template-columns: repeat(2, minmax(0,1fr)); gap:10px; }
    .card .foto { height:104px; }
    .kpis, .kpis.tres { grid-template-columns:1fr; }
    .kpis.tres .kpi:nth-child(n) { border-left:0; }
    .kpis.tres .kpi + .kpi { border-top:1px solid var(--rule); }
    .kpi { min-height:0; flex-direction:row; flex-wrap:wrap; align-items:baseline; justify-content:space-between; padding:14px 16px; gap:4px 12px; }
    .kpi + .kpi { border-left:0; border-top:1px solid var(--rule); }
    .kpi em { flex-basis:100%; }
    .kpi:first-child b { font-size:28px; }
    .panel { padding:16px; }
    .tabla-marco td, .tabla-marco th { white-space:nowrap; }
  }
  @media (pointer: coarse) {
    button { min-height:44px; }
    button.nav { height:44px; }
    button.mini { min-height:32px; height:32px; }
    #lines li button.sec { width:40px; height:40px; min-height:40px; }
    .rangos button { min-height:36px; }
    input, select { height:44px; }
  }
  ::view-transition-old(root), ::view-transition-new(root) { animation:none; mix-blend-mode:normal; }

  /* Modo día: estructura de los estilos claros de Taste con la identidad del rótulo */
  :root[data-tema="dia"] {
    color-scheme: light;
    --ground:#f1f0ed; --surface:#ffffff; --raised:#f4f3f0; --active:#e9e7e3;
    --rule:#ebe9e5; --control:#cfccc6; --borde:#e4e2de;
    --text:#151412; --muted:#5c5953; --faint:#6b6862;
    --data:#b7791f; --serie-2:#151412; --serie-3:#8a8781; --serie-4:#c9c6c0;
    --positive:#1e7b4f; --negative:#c4242a; --warning:#92400e;
    --pill-warning-bg:#fffbeb; --pill-warning-ink:#92400e;
    --modal-fondo: rgba(21,20,18,.38); --flotante:#ffffff;
  }
  :root[data-tema="dia"] html, :root[data-tema="dia"] { scrollbar-color:#cfccc6 transparent; }
  :root[data-tema="dia"] ::-webkit-scrollbar-thumb { background:#cfccc6; border-color:var(--ground); }
  :root[data-tema="dia"] body { -webkit-font-smoothing:auto; }
  :root[data-tema="dia"] button.sec { background:var(--raised); color:var(--text); }
  @media (hover:hover) and (pointer:fine) {
    :root[data-tema="dia"] button:hover { background:#2c2a27; }
    :root[data-tema="dia"] button.sec:hover { background:var(--active); }
    :root[data-tema="dia"] .card:hover { background:var(--surface); border-color:var(--control); }
    :root[data-tema="dia"] button.nav:hover { background:var(--active); color:var(--text); }
  }
  :root[data-tema="dia"] input, :root[data-tema="dia"] select { background:var(--surface); border-color:var(--control); }
  :root[data-tema="dia"] #login, :root[data-tema="dia"] .card, :root[data-tema="dia"] .rail, :root[data-tema="dia"] .panel,
  :root[data-tema="dia"] .kpis, :root[data-tema="dia"] .dash-card, :root[data-tema="dia"] .rangos, :root[data-tema="dia"] .modal-card { border:1px solid var(--borde); }
  :root[data-tema="dia"] button.nav { color:#4a4741; }
  :root[data-tema="dia"] button.nav.on { color:var(--g-ink); }
  :root[data-tema="dia"] .nav-grupo { color:var(--muted); }
  :root[data-tema="dia"] #view-form.es-dash { border:0; }
  :root[data-tema="dia"] .modal { background:var(--modal-fondo); }
  :root[data-tema="dia"] .modal-card { box-shadow:0 24px 48px rgba(21,20,18,.16); /* taste-skill-ignore: diálogo flotante */ }
  :root[data-tema="dia"] details.mas .menu { background:var(--surface); border:1px solid var(--borde); box-shadow:0 16px 36px rgba(21,20,18,.14); /* taste-skill-ignore: menú flotante */ }
  :root[data-tema="dia"] .aviso { background:#151412; color:#f3f2f0; }
  :root[data-tema="dia"] .side { box-shadow:none; }
  :root[data-tema="dia"] .velo { background:rgba(21,20,18,.32); }
  :root[data-tema="dia"] tbody tr:hover { background:rgba(0,0,0,.025); }
  @media (max-width: 1100px) { :root[data-tema="dia"] .side { box-shadow:0 0 48px rgba(21,20,18,.18); /* taste-skill-ignore: hoja de menú flotante */ } }

  @media (prefers-reduced-motion: reduce) {
    *, *::before, *::after { transition-property: opacity, background-color, color, border-color !important; animation:none !important; }
    button:active, .card:active { transform:none; }
  }
</style>
</head>
<body>
<form id="login" onsubmit="event.preventDefault(); entrar();">
  <div class="marca"><span class="mono" aria-hidden="true">MD</span><div><h2>MD Alliance</h2><p>Punto de venta</p></div></div>
  <label>Usuario<input id="user" autocomplete="username" required/></label>
  <label>Contraseña<input id="pass" type="password" autocomplete="current-password" required/></label>
  <button type="submit">Entrar</button>
  <div class="msg" id="loginMsg" role="alert"></div>
</form>
<main id="app">
  <div class="shell">
    <aside class="side" id="side" aria-label="Navegación">
      <div class="brand"><span class="mono" aria-hidden="true">MD</span><div><b>MD Alliance</b><small>Punto de venta</small></div></div>
      <div class="nav-grupo" data-grupo="venta">Venta</div>
      <button class="nav on" data-view="venta" data-mod="mostrador" onclick="ver('venta')">Mostrador</button>
      <button class="nav" data-view="cotizaciones" data-mod="cotizaciones" onclick="ver('cotizaciones')">Cotizaciones</button>
      <button class="nav" data-view="historial" data-mod="historial" onclick="ver('historial')">Facturas emitidas</button>
      <button class="nav" data-view="cobros" data-mod="cobros" onclick="ver('cobros')">Cuentas por cobrar</button>
      <button class="nav" data-view="dev" data-mod="devoluciones" onclick="ver('dev')">Devoluciones</button>
      <div class="nav-grupo" data-grupo="caja">Caja</div>
      <button class="nav" data-view="caja" data-mod="caja" onclick="ver('caja')" id="btnCaja">Abrir caja</button>
      <button class="nav" data-view="ingreso" data-mod="caja" onclick="ver('ingreso')">Ingreso de efectivo</button>
      <button class="nav" data-view="retiro" data-mod="caja" onclick="ver('retiro')">Retiro de efectivo</button>
      <button class="nav" data-view="cerrar" data-mod="caja" onclick="ver('cerrar')">Cuadre y cierre</button>
      <div class="nav-grupo" data-grupo="inventario">Inventario</div>
      <button class="nav" data-view="inventario" data-mod="inventario" onclick="ver('inventario')">Productos</button>
      <button class="nav" data-view="compra" data-mod="compras" onclick="ver('compra')">Compras</button>
      <button class="nav" data-view="kardex" data-mod="kardex" onclick="ver('kardex')">Kardex</button>
      <button class="nav" data-view="tr" data-mod="transferir" onclick="ver('tr')">Bodegas y traslados</button>
      <button class="nav" data-view="promo" data-mod="promociones" onclick="ver('promo')">Promociones</button>
      <div class="nav-grupo" data-grupo="analisis">Análisis</div>
      <button class="nav" data-view="indicadores" data-mod="indicadores" onclick="ver('indicadores')">Indicadores</button>
      <button class="nav" data-view="reportes" data-mod="reportes" onclick="ver('reportes')">Reportes</button>
      <button class="nav" data-view="seguimiento" data-mod="seguimiento" onclick="ver('seguimiento')">Seguimiento de cajeros</button>
      <div class="nav-grupo" data-grupo="admin">Administración</div>
      <button class="nav" data-view="clientes" data-mod="clientes" onclick="ver('clientes')">Clientes</button>
      <button class="nav" data-view="usuarios" data-mod="usuarios" onclick="ver('usuarios')">Usuarios y permisos</button>
      <button class="nav" data-view="vendedores" data-mod="vendedores" onclick="ver('vendedores')">Vendedores</button>
      <button class="nav" data-view="terminales" data-mod="terminales" onclick="ver('terminales')">Terminales</button>
      <button class="nav" data-view="ncf" data-mod="ncf" onclick="ver('ncf')">Comprobantes NCF</button>
      <button class="nav" data-view="ecf" data-mod="ncf" onclick="ver('ecf')">Facturación electrónica</button>
      <button class="nav" data-view="metodos" data-mod="metodos" onclick="ver('metodos')">Métodos de pago</button>
      <button class="nav" data-view="empresa" data-mod="apariencia" onclick="ver('empresa')">Empresa y correo</button>
      <button class="sec salir-menu" type="button" data-tema-btn onclick="cambiarTema(event)">Modo día</button>
      <button class="sec salir-menu" type="button" onclick="salir()">Salir</button>
    </aside>
    <div class="velo" onclick="menu(false)"></div>
    <div class="main">
      <header class="topbar">
        <button class="sec" id="btnMenu" type="button" onclick="menu(true)" aria-controls="side" aria-expanded="false">Menú</button>
        <h1 class="rotulo" id="rotulo" data-grupo="venta">Mostrador</h1>
        <div class="topbar-der">
          <div id="top"></div>
          <button class="sec tema-barra" type="button" data-tema-btn onclick="cambiarTema(event)">Modo día</button>
          <button class="sec salir-barra" type="button" onclick="salir()">Salir</button>
        </div>
      </header>
      <div class="work">
        <section class="catalog">
          <div id="view-venta">
            <div class="toolbar">
              <input id="q" type="search" placeholder="Buscar por nombre o escanear código" aria-label="Buscar producto" autocomplete="off"/>
              <select id="cat" aria-label="Categoría"></select>
              <select id="lista" aria-label="Lista de precios">
                <option value="1">Mínimo de venta</option>
                <option value="2">Mayorista</option>
                <option value="3">VIP</option>
                <option value="4">Especial</option>
              </select>
            </div>
            <div class="grid" id="grid" aria-live="polite"><p class="vacio">Cargando productos…</p></div>
          </div>
          <section id="view-form" class="panel" style="display:none">
            <h2 id="formTitle"></h2>
            <p class="hint" id="formHint"></p>
            <div id="formBody"></div>
            <div class="msg" id="formMsg" role="status"></div>
          </section>
        </section>
        <aside class="rail" aria-label="Ticket">
          <h3>Ticket</h3>
          <ul id="lines"><li class="vacio">Toca un producto o escanea un código para empezar.</li></ul>
          <div class="desglose" id="desglose"></div>
          <div class="placa-total" id="placaTotal">
            <small>Total a cobrar</small>
            <p id="tot">RD$ 0.00</p>
            <div class="placa-cobrado" id="placaCobrado" aria-hidden="true"><small></small><b></b></div>
          </div>
          <div class="pagos">
            <div class="campo ancho">
              <div class="campo-cab"><label for="cliVenta">Cliente</label><button class="sec mini" type="button" onclick="popupCliente()">Nuevo cliente</button></div>
              <select id="cliVenta" onchange="actualizarClienteVenta()"><option value="">Consumidor final</option></select>
              <div class="info-cliente" id="infoCliente" hidden></div>
            </div>
            <label>Comprobante<select id="tipoComp"><option value="consumidor_final">Consumidor final</option><option value="credito_fiscal">Crédito fiscal</option></select></label>
            <label>Descuento (RD$)<input id="desc" type="number" step="0.01" min="0" value="0"/></label>
            <div id="pagosDyn"></div>
            <label class="check ancho" id="filaCredito"><input type="checkbox" id="aCredito" onchange="pintar()"/> Venta a crédito (fiado)</label>
            <p id="cambio" class="ancho">Cambio: RD$ 0.00</p>
            <button id="btnCobrar" class="ancho" onclick="cobrar()" disabled>Cobrar</button>
            <details class="opciones ancho">
              <summary>Más opciones: terminal, vendedor, notas e impresión</summary>
              <label id="filaTerminal" hidden>Terminal<select id="termVenta" onchange="elegirTerminal(true)"><option value="">Sin terminal</option></select></label>
              <label id="filaVendedor" hidden>Vendedor<select id="vendVenta"><option value="">Sin vendedor</option></select></label>
              <label>Observaciones<input id="obsVenta" maxlength="500" placeholder="Ej.: entregar en la casa, pagó con cheque"/></label>
              <label>Ancho del ticket<select id="anchoTicket"><option value="80">80 mm</option><option value="58">58 mm</option></select></label>
            </details>
          </div>
          <div class="ticket-acciones">
            <button class="sec" onclick="popupApartar()">Apartar</button>
            <button class="sec" onclick="guardarCotizacion()">Cotizar</button>
          </div>
          <div id="esperas"></div>
          <div class="msg" id="msg" role="status"></div>
        </aside>
      </div>
    </div>
  </div>
</main>
<div id="modal" class="modal" onclick="if(event.target===this)cerrarModal()">
  <div class="modal-card" role="dialog" aria-modal="true" aria-labelledby="modalTitulo">
    <div class="modal-head">
      <h3 id="modalTitulo"></h3>
      <button class="sec" type="button" onclick="cerrarModal()">Cerrar</button>
    </div>
    <div id="modalCuerpo"></div>
  </div>
</div>
<div class="barra-ticket" id="barraTicket" aria-hidden="true"><span id="barraResumen"></span><button type="button" onclick="irAlTicket()">Ver ticket</button></div>
<div id="avisos" aria-live="polite"></div>
<div id="barraRed" role="status" aria-live="polite" hidden></div>
<div id="recibo"></div>
<script>
const cart = [];
function money(n){ return "RD$ " + Number(n||0).toLocaleString("en-US", {minimumFractionDigits: 2, maximumFractionDigits: 2}); }
function abrirModal(titulo, html){
  modalTitulo.textContent = titulo;
  modalCuerpo.innerHTML = html;
  const aviso = document.createElement("div");
  aviso.className = "msg";
  aviso.id = "modalMsg";
  modalCuerpo.appendChild(aviso);
  modal.classList.add("on");
}
function cerrarModal(){
  modal.classList.remove("on");
  const turno = window._modalTurno = (window._modalTurno || 0) + 1;
  setTimeout(() => { if (window._modalTurno === turno && !modal.classList.contains("on")) modalCuerpo.innerHTML = ""; }, 180);
}
const reducido = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
function animar(el, frames, opciones){
  if (!el || !el.animate) return null;
  if (reducido()) frames = frames.map(f => ({opacity: f.opacity === undefined ? 1 : f.opacity}));
  return el.animate(frames, Object.assign({easing: "cubic-bezier(0.23, 1, 0.32, 1)", fill: "backwards"}, opciones));
}
function aviso(texto, tipo){
  if (!texto) return;
  const caja = document.getElementById("avisos");
  const el = document.createElement("div");
  el.className = "aviso";
  el.dataset.tipo = tipo || "info";
  el.setAttribute("role", tipo === "error" ? "alert" : "status");
  el.textContent = texto;
  caja.appendChild(el);
  while (caja.children.length > 3) caja.firstElementChild.remove();
  let restante = tipo === "error" ? 6000 : 3200, desde = Date.now(), reloj;
  const alCambiarPestana = () => document.hidden ? pausar() : correr();
  const quitar = () => {
    clearTimeout(reloj);
    document.removeEventListener("visibilitychange", alCambiarPestana);
    el.classList.add("saliendo");
    setTimeout(() => el.remove(), 220);
  };
  const correr = () => { clearTimeout(reloj); desde = Date.now(); reloj = setTimeout(quitar, restante); };
  const pausar = () => { clearTimeout(reloj); restante -= Date.now() - desde; };
  el.addEventListener("mouseenter", pausar);
  el.addEventListener("mouseleave", correr);
  el.addEventListener("click", quitar);
  document.addEventListener("visibilitychange", alCambiarPestana);
  correr();
}
function vigilarMensajes(){
  ["msg", "formMsg"].forEach(id => {
    const nodo = document.getElementById(id);
    if (!nodo) return;
    new MutationObserver(() => {
      const texto = nodo.textContent.trim();
      if (!texto) return;
      const c = nodo.style.color || "";
      aviso(texto, c.includes("danger") ? "error" : (c.includes("ok") ? "ok" : "info"));
    }).observe(nodo, {childList: true, characterData: true, subtree: true});
  });
}
vigilarMensajes();
function aplicarTema(tema){
  document.documentElement.dataset.tema = tema;
  document.querySelectorAll("[data-tema-btn]").forEach(b => b.textContent = tema === "dia" ? "Modo noche" : "Modo día");
}
function cambiarTema(ev){
  const nuevo = document.documentElement.dataset.tema === "dia" ? "noche" : "dia";
  localStorage.setItem("tema", nuevo);
  if (!document.startViewTransition || reducido()) { aplicarTema(nuevo); return; }
  const r = ev && ev.currentTarget ? ev.currentTarget.getBoundingClientRect() : {left: innerWidth, top: 0, width: 0, height: 0};
  const x = Math.round(r.left + r.width / 2), y = Math.round(r.top + r.height / 2);
  const radio = Math.hypot(Math.max(x, innerWidth - x), Math.max(y, innerHeight - y));
  const vt = document.startViewTransition(() => aplicarTema(nuevo));
  vt.ready.then(() => document.documentElement.animate(
    {clipPath: [`circle(0px at ${x}px ${y}px)`, `circle(${radio}px at ${x}px ${y}px)`]},
    {duration: 520, easing: "cubic-bezier(0.77, 0, 0.175, 1)", pseudoElement: "::view-transition-new(root)"}
  ));
}
aplicarTema(document.documentElement.dataset.tema || "noche");
document.body.dataset.grupo = "venta";
document.querySelectorAll(".side .nav").forEach(b => {
  let g = b.previousElementSibling;
  while (g && !g.classList.contains("nav-grupo")) g = g.previousElementSibling;
  if (g) b.dataset.grupo = g.dataset.grupo;
});
document.addEventListener("click", ev => {
  document.querySelectorAll("details.mas[open]").forEach(d => { if (!d.contains(ev.target)) d.removeAttribute("open"); });
});
document.addEventListener("toggle", ev => {
  const d = ev.target;
  if (!(d instanceof HTMLDetailsElement) || !d.classList.contains("mas") || !d.open) return;
  document.querySelectorAll("details.mas[open]").forEach(o => { if (o !== d) o.removeAttribute("open"); });
  const r = d.querySelector("summary").getBoundingClientRect();
  const menuEl = d.querySelector(".menu");
  menuEl.style.top = Math.round(r.bottom + 6) + "px";
  menuEl.style.right = Math.round(innerWidth - r.right) + "px";
}, true);
addEventListener("scroll", () => document.querySelectorAll("details.mas[open]").forEach(d => d.removeAttribute("open")), {passive: true, capture: true});
function menu(abrir){
  document.body.classList.toggle("menu-abierto", !!abrir);
  const b = document.getElementById("btnMenu");
  if (b) b.setAttribute("aria-expanded", abrir ? "true" : "false");
}
function errorModal(e){
  const texto = (e && e.message) ? e.message : "No se pudo guardar";
  const m = document.getElementById("modalMsg");
  if (m) { m.style.color = "var(--danger)"; m.textContent = texto; }
  if (document.getElementById("formMsg")) { formMsg.style.color = "var(--danger)"; formMsg.textContent = texto; }
}
function okModal(texto){
  cerrarModal();
  if (document.getElementById("formMsg")) { formMsg.style.color = "var(--ok)"; formMsg.textContent = texto; }
}
document.addEventListener("keydown", function(ev){ if(ev.key==="Escape") cerrarModal(); });
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
async function sesionInicial(){
  try {
    await api("/api/estado");
  } catch(e) {
    return;
  }
  login.style.display = "none";
  app.style.display = "block";
  try {
    await refrescar();
    await cargar();
  } catch(e) {}
  sincronizarVentas();
}
sesionInicial();
let permisos = [];
function aplicarPermisos(mods){
  permisos = mods || [];
  document.querySelectorAll(".nav").forEach(b => {
    const m = b.dataset.mod;
    b.style.display = !m || permisos.includes(m) ? "" : "none";
  });
  actualizarGrupos();
}
function actualizarGrupos(){
  document.querySelectorAll(".nav-grupo").forEach(g => {
    let el = g.nextElementSibling, visible = false;
    while (el && el.classList.contains("nav")) {
      if (el.style.display !== "none") { visible = true; break; }
      el = el.nextElementSibling;
    }
    g.style.display = visible ? "" : "none";
  });
}
async function refrescar(){
  const e = await api("/api/estado");
  const esc = s => String(s || "").replaceAll("&","&amp;").replaceAll("<","&lt;");
  document.getElementById("top").innerHTML = `<span class="quien">${esc(e.usuario)} · ${esc(e.rol)} · </span><span class="estado${e.caja ? " abierta" : ""}">${e.caja ? esc(e.caja.nombre) + " abierta" : "Caja cerrada"}</span>`;
  aplicarPermisos(e.permisos);
  window._empresa = e.empresa || {};
  window._itbisIncluido = !!e.itbis_incluido;
  const btn = document.getElementById("btnCaja");
  if (btn) btn.style.display = e.caja || !permisos.includes("caja") ? "none" : "";
  actualizarGrupos();
  if (permisos.includes("mostrador")) {
    await cargarClientesVenta();
    await cargarMetodos();
    await cargarEsperas();
    await cargarVendedoresVenta();
    await cargarTerminalesVenta();
  }
  pintarBarraRed();
}
function idLocal(){
  if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
  return "v-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 10);
}
const COLA_VENTAS = "ventas_sin_conexion";
function colaVentas(){ try { return JSON.parse(localStorage.getItem(COLA_VENTAS) || "[]"); } catch(e) { return []; } }
function guardarCola(cola){ localStorage.setItem(COLA_VENTAS, JSON.stringify(cola)); pintarBarraRed(); }
function guardarVentaSinConexion(payload){
  if (payload.a_credito) throw new Error("Sin conexión no se puede fiar: hace falta revisar el límite del cliente.");
  if (payload.pagos.some(p => p.codigo === "saldo_favor")) throw new Error("Sin conexión no se puede usar saldo a favor.");
  const t = totalTicket();
  const pagado = payload.pagos.reduce((s, p) => s + p.monto, 0);
  if (pagado + 0.009 < t.total) throw new Error("Sin conexión la venta tiene que quedar pagada completa.");
  const ahora = new Date();
  const fecha = ahora.getFullYear() + "-" + String(ahora.getMonth()+1).padStart(2,"0") + "-" + String(ahora.getDate()).padStart(2,"0") + " " + ahora.toTimeString().slice(0,8);
  const cola = colaVentas();
  cola.push({payload: Object.assign({}, payload, {fecha_local: fecha}), total: t.total, creada: fecha, error: ""});
  guardarCola(cola);
  return {
    provisional: true, numero: payload.id_local.slice(0, 8).toUpperCase(), ncf: "", total: t.total, itbis: t.itbis,
    cambio: Math.max(0, Math.round((pagado - t.total) * 100) / 100), cliente: cliVenta.selectedOptions[0]?.textContent || "",
    lineas: cart.map(l => ({nombre: l.nombre, cantidad: l.cantidad, total: Math.round(l.precio * l.cantidad * 100) / 100})),
  };
}
let sincronizando = false;
async function sincronizarVentas(){
  if (sincronizando || !colaVentas().length) return;
  sincronizando = true;
  let enviadas = 0;
  try {
    for (const venta of colaVentas()) {
      if (venta.error) continue;
      try {
        await api("/api/vender", {method:"POST", body: JSON.stringify(venta.payload)});
        guardarCola(colaVentas().filter(v => v.payload.id_local !== venta.payload.id_local));
        enviadas++;
      } catch(e) {
        if (e instanceof TypeError) break;
        guardarCola(colaVentas().map(v => v.payload.id_local === venta.payload.id_local ? Object.assign(v, {error: e.message}) : v));
      }
    }
  } finally { sincronizando = false; }
  if (enviadas) {
    aviso(enviadas === 1 ? "Se facturó 1 venta hecha sin conexión." : `Se facturaron ${enviadas} ventas hechas sin conexión.`);
    if (document.getElementById("view-venta").style.display !== "none") cargar();
  }
}
function pintarBarraRed(){
  const barra = document.getElementById("barraRed");
  if (!barra) return;
  const cola = colaVentas();
  const conError = cola.filter(v => v.error).length;
  barra.className = "";
  if (!navigator.onLine) {
    barra.classList.add("sin-red");
    barra.innerHTML = `<span>Sin conexión. Puedes seguir vendiendo al contado${cola.length ? ` · ${cola.length} ${cola.length === 1 ? "venta guardada" : "ventas guardadas"} en este equipo` : ""}.</span>`;
  } else if (conError) {
    barra.classList.add("con-error");
    barra.innerHTML = `<span>${conError === 1 ? "Una venta sin conexión no se pudo facturar" : conError + " ventas sin conexión no se pudieron facturar"}.</span><button class="sec" onclick="verVentasPendientes()">Revisar</button>`;
  } else if (cola.length) {
    barra.innerHTML = `<span>${cola.length} ${cola.length === 1 ? "venta" : "ventas"} por sincronizar.</span><button class="sec" onclick="sincronizarVentas()">Sincronizar ahora</button>`;
  } else {
    barra.hidden = true;
    return;
  }
  barra.hidden = false;
}
function verVentasPendientes(){
  const cola = colaVentas();
  abrirModal("Ventas hechas sin conexión", `<p class="hint">Estas ventas se cobraron sin internet. Corrige el problema (por ejemplo, abre la caja o repón el stock) y vuelve a intentarlo.</p>` +
    tabla(["Hora","Total","Problema",""], cola.map(v => [escDash(v.creada), money(v.total), escDash(v.error || "Por enviar"),
      `<div class="acciones"><button class="sec" onclick="reintentarVenta('${v.payload.id_local}')">Reintentar</button><button class="sec peligro" onclick="descartarVenta('${v.payload.id_local}')">Descartar</button></div>`]), "No hay ventas pendientes."));
}
async function reintentarVenta(id){
  guardarCola(colaVentas().map(v => v.payload.id_local === id ? Object.assign(v, {error: ""}) : v));
  await sincronizarVentas();
  verVentasPendientes();
}
function descartarVenta(id){
  if (!confirm("¿Descartar esta venta? No se facturará ni se descontará del inventario.")) return;
  guardarCola(colaVentas().filter(v => v.payload.id_local !== id));
  verVentasPendientes();
}
window.addEventListener("online", () => { pintarBarraRed(); sincronizarVentas(); });
window.addEventListener("offline", pintarBarraRed);
setInterval(() => { if (navigator.onLine) sincronizarVentas(); }, 30000);
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
async function cargarTerminalesVenta(){
  try {
    const d = await api("/api/terminales");
    window._terminales = (d.items || []).filter(x => x.disponible);
    const guardada = localStorage.getItem("terminal") || "";
    termVenta.innerHTML = '<option value="">Sin terminal</option>' + window._terminales.map(x => `<option value="${x.id}">${escDash(x.nombre)} · ${escDash(x.prefijo)}</option>`).join("");
    termVenta.value = window._terminales.some(x => String(x.id) === guardada) ? guardada : (window._terminales.length === 1 ? String(window._terminales[0].id) : "");
    document.getElementById("filaTerminal").hidden = !window._terminales.length;
    elegirTerminal(false);
  } catch(e) { document.getElementById("filaTerminal").hidden = true; }
}
function elegirTerminal(avisar){
  const x = (window._terminales || []).find(t => String(t.id) === termVenta.value);
  localStorage.setItem("terminal", termVenta.value);
  if (!x) return;
  if (String(lista.value) !== String(x.lista_precio)) { lista.value = String(x.lista_precio); if (avisar) cargar(); }
  tipoComp.value = x.comprobante;
  if (x.vendedor_id && !vendVenta.value) vendVenta.value = String(x.vendedor_id);
  if (avisar) aviso(`Vendiendo en ${x.nombre}: facturas ${x.prefijo}-${String(x.siguiente).padStart(6, "0")} en adelante.`);
}
async function cargarTerminales(){
  const d = await api("/api/terminales");
  window._termAdmin = d;
  const nombres = nombresLista();
  termTabla.innerHTML = tabla(["Terminal","Próxima factura","Bodega","Precios","Cajeros","Hoy","Estado",""], (d.items || []).map(x => [
    `<b>${escDash(x.nombre)}</b>${x.vendedor ? `<br><span class="hint">Vendedor: ${escDash(x.vendedor)}</span>` : ""}`,
    `<span class="sin-corte">${escDash(x.prefijo)}-${String(x.siguiente).padStart(6, "0")}</span>`, escDash(x.bodega || "La del producto"), escDash(nombres[x.lista_precio] || x.lista_precio),
    x.usuarios.length ? escDash(x.usuarios.join(", ")) : "Todos", `<span class="sin-corte">${money(x.ventas_hoy)}</span><br><span class="hint">${x.facturas_hoy} ${x.facturas_hoy === 1 ? "factura" : "facturas"}</span>`,
    `<span class="pill ${x.activo ? "bien" : "mal"}">${x.activo ? "Activa" : "Inactiva"}</span>`,
    `<button class="sec" onclick="formTerminal(${x.id})">Editar</button>`]), "Todavía no hay terminales. Crea una por cada caja.");
}
async function formTerminal(id){
  const d = window._termAdmin || await api("/api/terminales");
  const x = (d.items || []).find(t => t.id === id) || {nombre:"", prefijo:"", siguiente:1, bodega:"", lista_precio:1, comprobante:"consumidor_final", vendedor_id:null, usuarios:[], activo:true};
  let vendedores = [];
  try { vendedores = ((await api("/api/vendedores")).items || []).filter(v => v.activo); } catch(e) {}
  const nombres = nombresLista();
  abrirModal(id ? "Editar terminal" : "Nueva terminal", `<div class="form">
    <label>Nombre<input id="tNom" value="${escDash(x.nombre)}" placeholder="Caja 1"/></label>
    <label>Prefijo de facturas<input id="tPre" value="${escDash(x.prefijo)}" maxlength="6" placeholder="C1"/></label>
    <label>Próximo número<input id="tSig" type="number" min="1" value="${x.siguiente}"/></label>
    <label>Bodega que descuenta<select id="tBod"><option value="">La del producto</option>${(d.bodegas || []).map(b => `<option ${b===x.bodega?"selected":""}>${escDash(b)}</option>`).join("")}</select></label>
    <label>Lista de precios<select id="tLis">${[1,2,3,4].map(n => `<option value="${n}" ${n===x.lista_precio?"selected":""}>${escDash(nombres[n] || "Lista " + n)}</option>`).join("")}</select></label>
    <label>Comprobante por defecto<select id="tCom"><option value="consumidor_final">Consumidor final</option><option value="credito_fiscal" ${x.comprobante==="credito_fiscal"?"selected":""}>Crédito fiscal</option></select></label>
    <label>Vendedor por defecto<select id="tVen"><option value="">Ninguno</option>${vendedores.map(v => `<option value="${v.id}" ${v.id===x.vendedor_id?"selected":""}>${escDash(v.nombre)}</option>`).join("")}</select></label>
    <label class="check"><input type="checkbox" id="tAct" ${x.activo ? "checked" : ""}/> Terminal activa</label>
  </div>
  <p class="hint" style="margin-top:14px">Cajeros que pueden vender aquí (si no marcas ninguno, pueden todos):</p>
  <div class="form">${(d.usuarios || []).map(u => `<label class="check"><input type="checkbox" class="tUsu" value="${escDash(u)}" ${x.usuarios.includes(u)?"checked":""}/> ${escDash(u)}</label>`).join("")}</div>
  <div class="form" style="margin-top:14px"><button onclick="guardarTerminal(${id || "null"})">Guardar terminal</button></div>`);
}
async function guardarTerminal(id){
  const body = {nombre: tNom.value, prefijo: tPre.value, siguiente: Number(tSig.value || 1), bodega: tBod.value, lista_precio: Number(tLis.value),
    comprobante: tCom.value, vendedor_id: tVen.value ? Number(tVen.value) : null, activo: tAct.checked,
    usuarios: [...document.querySelectorAll(".tUsu:checked")].map(c => c.value)};
  try {
    await api(id ? "/api/terminales/" + id : "/api/terminales", {method:"POST", body: JSON.stringify(body)});
    okModal("Terminal guardada.");
    await cargarTerminales();
    if (typeof cargarTerminalesVenta === "function") cargarTerminalesVenta();
  } catch(e){ errorModal(e); }
}
async function cargarVendedoresVenta(){
  try {
    const d = await api("/api/vendedores");
    const activos = (d.items || []).filter(v => v.activo);
    const actual = vendVenta.value;
    vendVenta.innerHTML = '<option value="">Sin vendedor</option>' + activos.map(v => `<option value="${v.id}">${escDash(v.nombre)}</option>`).join("");
    vendVenta.value = actual;
    document.getElementById("filaVendedor").hidden = !activos.length;
  } catch(e) { document.getElementById("filaVendedor").hidden = true; }
}
async function cargarClientesVenta(){
  try {
    const d = await api("/api/clientes");
    const actual = cliVenta.value;
    cliVenta.innerHTML = '<option value="">Consumidor final</option>' + d.items.filter(c => c.activo !== false).map(c => `<option value="${c.id}">${String(c.nombre).replaceAll("<","&lt;")}</option>`).join("");
    cliVenta.value = actual;
  } catch(e) { /* el cajero básico no lista clientes */ }
}
async function salir(){
  const pendientes = colaVentas().length;
  if (pendientes && !confirm(`Hay ${pendientes} ${pendientes === 1 ? "venta" : "ventas"} sin conexión en este equipo. Se enviarán cuando vuelvas a entrar. ¿Salir de todos modos?`)) return;
  if (navigator.serviceWorker && navigator.serviceWorker.controller) navigator.serviceWorker.controller.postMessage("limpiar");
  await api("/api/logout", {method:"POST"});
  location.reload();
}
function etiquetasLista(d){
  window._descuentos = d || window._descuentos || {p2:2, p3:5, p4:10};
  const x = window._descuentos;
  const actual = lista.value || "1";
  lista.innerHTML = `<option value="1">Mínimo de venta</option><option value="2">Por mayor ${x.p2}%</option><option value="3">Por mayor ${x.p3}%</option><option value="4">Por mayor ${x.p4}%</option>`;
  lista.value = actual;
}
function nombresLista(){
  const x = window._descuentos || {p2:2, p3:5, p4:10};
  return ["", "Mínimo de venta", "Por mayor "+x.p2+"%", "Por mayor "+x.p3+"%", "Por mayor "+x.p4+"%"];
}
async function cargar(){
  grid.setAttribute("aria-busy", "true");
  let data;
  try {
    data = await api("/api/catalogo?q=" + encodeURIComponent(q.value) + (cat.value ? "&categoria="+cat.value : ""));
  } catch(e) {
    grid.innerHTML = `<p class="vacio">No se pudo cargar el catálogo: ${String(e.message).replaceAll("<","&lt;")}</p>`;
    return;
  } finally {
    grid.removeAttribute("aria-busy");
  }
  if (data.descuentos) etiquetasLista(data.descuentos);
  cat.innerHTML = '<option value="">Todas</option>' + data.categorias.map(c=>`<option value="${c.id}">${c.nombre}</option>`).join("");
  if (window._cat) cat.value = window._cat;
  const qv = q.value.trim().toLowerCase();
  if (!navigator.onLine && qv) {
    data.productos = data.productos.filter(p => String(p.nombre).toLowerCase().includes(qv) || String(p.codigo || "").toLowerCase().includes(qv));
    data.combos = (data.combos || []).filter(c => String(c.nombre).toLowerCase().includes(qv));
  }
  const tarjetas = data.productos.concat(data.combos || []);
  const exacto = data.productos.find(p => (p.codigo || "").toLowerCase() === qv && qv);
  grid.innerHTML = tarjetas.map(p=>{
    const nivel = Number(lista.value);
    const precio = p.combo ? p.precio : ([p.precio, p.precio_2, p.precio_3, p.precio_4][nivel-1] || p.precio);
    const nombre = String(p.nombre).replaceAll("<","&lt;");
    const esBajo = !p.combo && Number(p.stock) <= Number(p.minimo || 0);
    const extra = p.combo ? "Combo" : ("Stock: " + p.stock);
    const badge = esBajo ? '<span class="badge">Stock bajo</span>' : "";
    return `<button class="card${esBajo ? " bajo" : ""}" onclick='add(${JSON.stringify(p).replaceAll("'","&#39;")}, ${precio})'>${badge}<span class="foto" style="background-image:url('${p.imagen}')"></span><span class="info"><b>${nombre}</b><span class="precio">${money(precio)}</span><span class="stock">${extra}</span></span></button>`;
  }).join("") || `<p class="vacio">${q.value.trim() ? "Ningún producto coincide con «" + q.value.trim().replaceAll("<","&lt;") + "»." : "No hay productos en esta categoría."}</p>`;
  if (!window._catalogoVisto) {
    window._catalogoVisto = true;
    grid.querySelectorAll(".card").forEach((c, i) => animar(c, [{opacity:0, transform:"translateY(10px)"}, {opacity:1, transform:"none"}], {duration:320, delay: Math.min(i * 30, 240)}));
  }
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
  window._lineaTocada = {indice: cart.indexOf(hit || cart[cart.length - 1]), nueva: !hit};
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
  const pedido = Math.max(0, Number(document.getElementById("desc").value||0));
  if (window._itbisIncluido) {
    let bruto = 0, base = 0;
    cart.forEach(l => {
      const g = Math.round(l.precio * l.cantidad * 100) / 100;
      const n = l.itbis ? Math.round(g / 1.18 * 100) / 100 : g;
      bruto += g; base += n;
    });
    const rebaja = Math.min(bruto, pedido);
    const factor = bruto ? (bruto - rebaja) / bruto : 0;
    const itbis = Math.round((bruto - base) * factor * 100) / 100;
    const total = Math.round((bruto - rebaja) * 100) / 100;
    return {sub: total - itbis, rebaja, itbis, total, incluido: true};
  }
  const sub = cart.reduce((s,l)=>s+l.precio*l.cantidad,0);
  const rebaja = Math.min(sub, pedido);
  const base = Math.max(0, sub-rebaja);
  const itbis = cart.reduce((s,l)=>s+(l.itbis?l.precio*l.cantidad*0.18:0),0) * (sub ? base/sub : 0);
  return {sub, rebaja, itbis, total: base+itbis};
}
const ICONO_MENOS = '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M3.5 8h9" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round"/></svg>';
const ICONO_MAS = '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M3.5 8h9M8 3.5v9" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round"/></svg>';
function pintar(){
  lines.innerHTML = cart.map((l,i)=>`<li><span><button class="sec" onclick="cambiarCant(${i},-1)" aria-label="Quitar uno">${ICONO_MENOS}</button> <span class="num">${l.cantidad}</span> <button class="sec" onclick="cambiarCant(${i},1)" aria-label="Agregar uno">${ICONO_MAS}</button><span class="nombre">${String(l.nombre).replaceAll("<","&lt;")}</span></span><span class="num">${money(l.precio*l.cantidad)}</span></li>`).join("")
    || '<li class="vacio">Toca un producto o escanea un código para empezar.</li>';
  const btnCobrar = document.getElementById("btnCobrar");
  if (btnCobrar && !btnCobrar.classList.contains("cobrando")) btnCobrar.disabled = !cart.length;
  const tocada = window._lineaTocada;
  window._lineaTocada = null;
  if (tocada && lines.children[tocada.indice]) {
    const li = lines.children[tocada.indice];
    if (tocada.nueva) {
      animar(li, [{opacity:0, transform:"translateY(-6px)"}, {opacity:1, transform:"none"}], {duration:200});
      lines.scrollTop = lines.scrollHeight;
    } else {
      animar(li.querySelector(".num"), [{transform:"scale(1.25)"}, {transform:"none"}], {duration:180});
    }
  }
  const t = totalTicket();
  document.getElementById("desglose").innerHTML = cart.length
    ? (t.incluido
        ? (t.rebaja > 0 ? `<div><span>Descuento aplicado</span><b>−${money(t.rebaja)}</b></div>` : "") + `<div><span>Subtotal sin ITBIS</span><b>${money(t.sub)}</b></div><div><span>ITBIS 18% incluido</span><b>${money(t.itbis)}</b></div>`
        : `<div><span>Subtotal</span><b>${money(t.sub)}</b></div>` + (t.rebaja > 0 ? `<div><span>Descuento</span><b>−${money(t.rebaja)}</b></div>` : "") + `<div><span>ITBIS 18%</span><b>${money(t.itbis)}</b></div>`)
    : "";
  tot.textContent = money(t.total);
  tot.classList.toggle("largo", tot.textContent.length > 12);
  const unidades = cart.reduce((s, l) => s + Number(l.cantidad || 0), 0);
  document.getElementById("barraResumen").textContent = `${unidades} ${unidades === 1 ? "artículo" : "artículos"} · ${money(t.total)}`;
  actualizarBarraTicket();
  const pagos = pagosActuales();
  const cajaPago = pagos.find(p => p.caja);
  const autoCaja = cajaPago && (cajaPago.el.dataset.auto === "1" || !cajaPago.monto);
  if (cajaPago && autoCaja && pagos.every(p => p === cajaPago || !p.monto)) {
    cajaPago.el.value = t.total ? t.total.toFixed(2) : "0";
    cajaPago.el.dataset.auto = "1";
    cajaPago.monto = t.total;
  }
  const fuera = pagos.filter(p => !p.caja).reduce((s,p)=>s+p.monto,0);
  const enCaja = pagos.filter(p => p.caja).reduce((s,p)=>s+p.monto,0);
  const falta = Math.max(0, t.total - fuera - enCaja);
  const fiado = document.getElementById("aCredito").checked;
  cambio.classList.toggle("pendiente", fiado && falta > 0.009);
  cambio.textContent = fiado && falta > 0.009
    ? "Queda pendiente: " + money(falta)
    : "Cambio: " + money(Math.max(0, enCaja - Math.max(0, t.total-fuera)));
}
async function actualizarClienteVenta(){
  const caja = document.getElementById("infoCliente");
  const saldoLbl = document.getElementById("pagoSaldoLbl");
  window._cliVenta = null;
  if (!cliVenta.value) {
    caja.hidden = true;
    if (saldoLbl) { saldoLbl.hidden = true; saldoLbl.querySelector("input").value = 0; }
    pintar();
    return;
  }
  try {
    const d = await api("/api/clientes/" + cliVenta.value + "/saldo");
    window._cliVenta = d;
    const partes = [];
    if (d.deuda > 0) partes.push(`Debe <b>${money(d.deuda)}</b>`);
    partes.push(d.limite_credito > 0 ? `Crédito disponible <b>${money(d.credito_disponible)}</b>` : `<span>Sin crédito aprobado</span>`);
    if (d.saldo_favor > 0) partes.push(`Saldo a favor <b>${money(d.saldo_favor)}</b>`);
    caja.innerHTML = partes.map(x => `<span>${x}</span>`).join("");
    caja.hidden = false;
    if (saldoLbl) {
      saldoLbl.hidden = !(d.saldo_favor > 0);
      saldoLbl.querySelector("span").textContent = "Saldo a favor (hasta " + money(d.saldo_favor) + ")";
      if (saldoLbl.hidden) saldoLbl.querySelector("input").value = 0;
    }
    if (d.lista_precio && String(d.lista_precio) !== lista.value) {
      lista.value = String(d.lista_precio);
      await cargar();
    }
  } catch(e) { caja.hidden = true; }
  pintar();
}
let placaVisible = false;
function actualizarBarraTicket(){
  const barra = document.getElementById("barraTicket");
  if (!barra) return;
  const enVenta = (window._vistaActual || "venta") === "venta";
  const ver = cart.length > 0 && !placaVisible && enVenta;
  barra.classList.toggle("on", !!ver);
  barra.setAttribute("aria-hidden", ver ? "false" : "true");
  document.body.classList.toggle("con-barra", !!ver);
}
if ("IntersectionObserver" in window) {
  new IntersectionObserver(entradas => {
    placaVisible = entradas.some(e => e.isIntersecting);
    actualizarBarraTicket();
  }).observe(document.getElementById("placaTotal"));
}
function irAlTicket(){
  document.getElementById("btnCobrar").scrollIntoView({behavior: reducido() ? "auto" : "smooth", block: "center"});
  setTimeout(() => document.getElementById("btnCobrar").focus({preventScroll: true}), reducido() ? 0 : 400);
}
function cambiarCant(i, delta){
  cart[i].cantidad += delta;
  if(cart[i].cantidad <= 0) cart.splice(i,1);
  else window._lineaTocada = {indice: i, nueva: false};
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
function tabla(headers, filas, vacio){
  if(!filas.length) return "<p class='hint'>" + (vacio || "No hay datos en este período.") + "</p>";
  const esNum = c => /^\\s*(RD\\$\\s?)?[-+−]?[\\d.,]+(\\s?%)?\\s*$/.test(String(c));
  const vacia = c => c === undefined || c === null || String(c).trim() === "" || String(c).trim() === "—";
  const numerica = headers.map((_, j) => filas.some(f => esNum(f[j])) && filas.every(f => vacia(f[j]) || esNum(f[j])));
  const cl = j => numerica[j] ? ' class="num"' : "";
  return `<div class="tabla-marco"><table><thead><tr>${headers.map((h, j)=>`<th${cl(j)}>${h}</th>`).join("")}</tr></thead><tbody>${
    filas.map(f=>`<tr>${f.map((c, j)=>`<td${cl(j)}>${c}</td>`).join("")}</tr>`).join("")
  }</tbody></table></div>`;
}
async function ver(nombre){
  document.querySelectorAll(".nav").forEach(b => b.classList.toggle("on", b.dataset.view===nombre));
  document.getElementById("view-venta").style.display = nombre==="venta" ? "block" : "none";
  const formulario = document.getElementById("view-form");
  formulario.style.display = nombre==="venta" ? "none" : "block";
  formulario.classList.toggle("es-dash", nombre==="indicadores");
  const rail = document.querySelector(".rail");
  const ancho = nombre !== "venta";
  if (rail) rail.style.display = ancho ? "none" : "";
  formMsg.textContent = "";
  menu(false);
  const etiqueta = document.querySelector(`.nav[data-view="${nombre}"]`);
  pintarRotulo(etiqueta);
  const vista = nombre === "venta" ? document.getElementById("view-venta") : formulario;
  if (window._vistaActual !== nombre) {
    animar(vista, [{opacity:0, transform:"translateY(6px)"}, {opacity:1, transform:"none"}], {duration:200});
    window._vistaActual = nombre;
  }
  actualizarBarraTicket();
  if(nombre!=="venta") {
    formTitle.textContent = etiqueta ? etiqueta.textContent : "";
    formHint.textContent = "";
    formBody.innerHTML = "<div class='esqueleto' aria-label='Cargando'><span></span><span></span><span></span><span></span></div>";
    await pintarForm(nombre);
  }
}
function pintarRotulo(boton){
  const r = document.getElementById("rotulo");
  if (!r || !boton) return;
  let g = boton.previousElementSibling;
  while (g && !g.classList.contains("nav-grupo")) g = g.previousElementSibling;
  const grupo = g ? g.dataset.grupo : "venta";
  const texto = boton.textContent.trim();
  document.body.dataset.grupo = grupo;
  if (r.textContent === texto && r.dataset.grupo === grupo) return;
  r.textContent = texto;
  r.dataset.grupo = grupo;
  animar(r, [{clipPath:"inset(0 100% 0 0 round 10px)"}, {clipPath:"inset(0 0 0 0 round 10px)"}], {duration:260, easing:"cubic-bezier(0.77, 0, 0.175, 1)"});
}
async function pintarForm(nombre){
  const lista = await productos();
  const opts = opcionesProductos(lista);
  window._optsProd = opts;
  const forms = {
    caja: ["Abrir caja", "Pulsa Abrir turno para indicar el nombre y el fondo. Si ya hay uno abierto, edítalo desde la lista.",
      `<div class="form"><button onclick="abrirTurno()">Abrir turno</button></div><div id="cajaInfo"></div>`],
    ingreso: ["Ingreso de efectivo", "El monto entra al efectivo esperado del turno. Abajo están los ingresos de este turno.",
      `<div class="form"><button onclick="nuevoMov('ingreso')">Registrar ingreso</button></div><div id="movLista"></div>`],
    retiro: ["Retiro de efectivo", "No puede superar el efectivo esperado del turno. Abajo están los retiros de este turno.",
      `<div class="form"><button onclick="nuevoMov('retiro')">Registrar retiro</button></div><div id="movLista"></div>`],
    cerrar: ["Cuadre y cierre", "Cierra el turno cuando cuentes el efectivo. El historial se filtra por fechas; si las dejas vacías, muestra los últimos 30 días.",
      `<div class="form"><button onclick="nuevoCierre()">Cerrar turno</button><label>Desde<input id="cuadreDesde" type="date"/></label><label>Hasta<input id="cuadreHasta" type="date"/></label><button class="sec" onclick="cargarPanelCaja('cerrar')">Ver historial</button></div><div id="cajaInfo"></div>`],
    compra: ["Compras", "Pulsa Registrar compra. Varias líneas entran en la misma compra y suman existencia.",
      `<div class="form"><button onclick="nuevaCompra()">Registrar compra</button></div><div id="compLista"></div>`],
    promo: ["Promociones", "Se aplican en el mostrador cuando el producto está en la promoción.",
      `<div class="form"><button onclick="nuevaPromo()">Crear promoción</button></div><div id="promoLista"></div>`],
    dev: ["Devoluciones", "Pulsa Nueva devolución, busca la factura e indica cuánto vuelve. Las notas ya hechas se editan abajo.",
      `<div class="form"><button onclick="nuevaDevolucion()">Nueva devolución</button></div><div id="devLista"></div>`],
    tr: ["Bodegas y traslados", "Crea una bodega o mueve existencia de una a otra. El stock total no cambia.",
      `<div class="form"><button onclick="nuevaBodega()">Crear bodega</button><button onclick="nuevaTransfer()">Transferir</button></div><div id="bodLista"></div>`],
    reportes: ["Reportes", "Elige el rango. Si lo dejas vacío, usa los últimos 30 días.",
      `<div class="form"><label>Desde<input id="repDesde" type="date"/></label><label>Hasta<input id="repHasta" type="date"/></label><button class="sec" onclick="pintarReportes()">Ver</button></div><div id="repTabla"></div>`],
    metodos: ["Métodos de pago", "Los activos salen en el ticket. Si afecta caja, el vuelto se calcula sobre ese monto.",
      `<div class="form"><button onclick="nuevoMetodo()">Nuevo método</button></div><div id="metTabla"></div>`],
    ncf: ["Comprobantes NCF", "Secuencias que se imprimen en cada venta.", ""],
    inventario: ["Productos", "Primero va el precio original. El mínimo de venta es el precio de público. Los porcentajes se aplican a ese mínimo, y el dueño puede cambiarlos.",
      `<div class="form"><label class="wide">Buscar<input id="invQ" placeholder="Nombre o código"/></label><button class="sec" onclick="cargarInventario()">Buscar</button><button class="sec" onclick="etiquetasPdf()">Etiquetas PDF</button><button onclick="nuevoProducto()">Crear producto</button></div><div class="form" style="margin-top:14px"><label>1er por mayor %<input id="descP2" type="number" step="0.01" value="2"/></label><label>2do por mayor %<input id="descP3" type="number" step="0.01" value="5"/></label><label>3er por mayor %<input id="descP4" type="number" step="0.01" value="10"/></label><button class="sec" onclick="guardarDescuentos()">Guardar porcentajes</button></div><div id="invTabla"></div>`],
    kardex: ["Kardex", "Movimientos de un producto: ventas, compras, transferencias y devoluciones.",
      `<div class="form"><label>Producto<select id="kxProd" onchange="cargarKardex()">${opts}</select></label><button class="sec" onclick="cargarKardex()">Ver movimientos</button></div><div id="kxTabla"></div>`],
    historial: ["Facturas emitidas", "Abre la factura para verla. También puedes imprimir, descargar o anular.",
      `<div class="form"><label>Estado<select id="histEstado"><option value="emitidas">Emitidas</option><option value="anuladas">Anuladas</option><option value="todos">Todas</option></select></label><button class="sec" onclick="cargarHistorial()">Cargar</button></div><div id="docVista"></div><div id="histTabla"></div>`],
    cotizaciones: ["Cotizaciones", "Abre el presupuesto para verlo o cambiar cantidades, productos y cliente.", ""],
    indicadores: ["Indicadores", "Ventas, cajeros, pagos e inventario.", ""],
    terminales: ["Terminales", "Cada caja o punto de venta con su numeración, bodega, lista de precios y cajeros. Se elige en el mostrador, en «Más opciones».",
      `<div class="form"><button onclick="formTerminal()">Nueva terminal</button></div><div id="termTabla"><div class='esqueleto'><span></span><span></span><span></span></div></div>`],
    ecf: ["Facturación electrónica", "Comprobantes e-CF de la DGII (Ley 32-23). Para pequeños, micro y no clasificados el plazo vence el 15 de noviembre de 2026.",
      `<div id="ecfPanel"><div class='esqueleto'><span></span><span></span><span></span></div></div>`],
    vendedores: ["Vendedores", "Quién atendió cada venta, aparte del cajero que cobra. Sirve para comisiones y para el reporte por vendedor.",
      `<div class="form"><button onclick="nuevoVendedor()">Crear vendedor</button></div><div id="vendTabla"></div>`],
    cobros: ["Cuentas por cobrar", "Ventas fiadas con saldo pendiente. Cada abono entra al turno de caja abierto.",
      `<div class="form"><label class="wide">Buscar<input id="cobQ" placeholder="Cliente, documento o número de factura" onkeydown="if(event.key==='Enter')cargarCobros()"/></label><button class="sec" onclick="cargarCobros()">Buscar</button></div><div id="cobResumen"></div><div id="cobTabla"></div>`],
    clientes: ["Clientes", "Pulsa Crear cliente. El correo recibe el comprobante. Cada fila se puede editar.",
      `<div class="form"><button onclick="nuevoCliente()">Crear cliente</button></div><div id="cliTabla"></div>`],
    usuarios: ["Usuarios y permisos", "El administrador ve todo. Un cajero solo entra a los módulos que marques. La contraseña vacía, al editar, no la cambia.",
      `<div class="form"><button onclick="nuevoUsuario()">Crear usuario</button></div><div id="usuTabla"></div>`],
    seguimiento: ["Seguimiento de cajeros", "Cuánto vendió cada cajero y qué turnos descuadraron. Deja una nota de seguimiento.", ""],
    empresa: ["Empresa y correo", "Datos del ticket y el servidor que envía el comprobante por correo.",
      `<div class="form"><label>Nombre<input id="empNom"/></label><label class="wide">Dirección<input id="empDir"/></label><label>RNC<input id="empRnc"/></label><label>Servidor SMTP<input id="smtpHost" placeholder="smtp.hostinger.com"/></label><label>Puerto<input id="smtpPort" value="587"/></label><label>Usuario<input id="smtpUser"/></label><label>Contraseña<input id="smtpPass" type="password" placeholder="Vacía para no cambiarla"/></label><label>Remitente<input id="smtpFrom" placeholder="facturas@tudominio.com"/></label><label class="check wide"><input type="checkbox" id="empItbisIncl"/> Los precios del catálogo ya incluyen el ITBIS (lo normal en colmado y tienda)</label><button onclick="guardarEmpresa()">Guardar</button></div>`]
  };
  const item = forms[nombre];
  if(!item) return;
  formTitle.textContent = item[0];
  formHint.textContent = item[1];
  formBody.innerHTML = item[2];
  window._movId = null;
  window._turnoId = null;
  window._cierreEdit = null;
  window._prodId = null;
  window._cliId = null;
  window._promoId = null;
  window._usuEdit = false;
  if(nombre==="ingreso" || nombre==="retiro") await cargarMovimientos(nombre);
  if(nombre==="caja" || nombre==="cerrar") await cargarPanelCaja(nombre);
  if(nombre==="reportes") await pintarReportes();
  if(nombre==="ncf") await pintarNcf();
  if(nombre==="promo") await pintarPromos();
  if(nombre==="inventario") await cargarInventario();
  if(nombre==="historial") await cargarHistorial();
  if(nombre==="kardex") await cargarKardex();
  if(nombre==="cotizaciones") await cargarCotizaciones();
  if(nombre==="indicadores") await cargarIndicadores();
  if(nombre==="clientes") await cargarClientes();
  if(nombre==="cobros") await cargarCobros();
  if(nombre==="vendedores") await cargarVendedores();
  if(nombre==="ecf") await cargarEcf();
  if(nombre==="terminales") await cargarTerminales();
  if(nombre==="usuarios") await cargarUsuarios();
  if(nombre==="empresa") await cargarEmpresa();
  if(nombre==="seguimiento") await cargarSeguimiento();
  if(nombre==="compra"){ lineasCompra = []; await cargarCompras(); }
  if(nombre==="tr") await cargarBodegas();
  if(nombre==="metodos") await cargarPantallaMetodos();
  if(nombre==="dev"){
    await cargarDevoluciones();
    if(window._devNumero){
      const n = window._devNumero;
      window._devNumero = "";
      nuevaDevolucion();
      devQ.value = n;
      buscarDev();
    }
  }
}
async function cargarPanelCaja(nombre){
  let q = "";
  if (nombre === "cerrar") {
    const desde = document.getElementById("cuadreDesde");
    const hasta = document.getElementById("cuadreHasta");
    if ((desde && desde.value) || (hasta && hasta.value)) {
      if (!desde.value || !hasta.value) {
        formMsg.style.color = "var(--danger)";
        formMsg.textContent = "Indica la fecha desde y la fecha hasta.";
        return;
      }
      q = "?desde=" + desde.value + "&hasta=" + hasta.value;
    }
  }
  const d = await api("/api/caja/panel" + q);
  window._cajaPanel = d;
  const box = document.getElementById("cajaInfo");
  if (!box) return;
  if (nombre === "caja") {
    const a = d.abierta;
    box.innerHTML = a
      ? `<div class="doc"><p>Turno abierto: ${escDash(a.nombre)} · ${escDash(a.usuario || "—")} · ${escDash(String(a.apertura||"").slice(0,16))}</p><p>Fondo ${money(a.fondo)} · ventas en efectivo ${money(a.ventas_efectivo)} · ingresos ${money(a.ingresos)} · retiros ${money(a.retiros)} · esperado ${money(a.esperado)}</p><button class="sec" onclick="editarTurno()">Editar turno</button></div>`
      : "<p class='hint'>No hay turno abierto.</p>";
    return;
  }
  const a = d.abierta;
  const resumen = a
    ? `<div class="doc"><p>Turno abierto: ${escDash(a.nombre)} · efectivo esperado ${money(a.esperado)}</p><p>Fondo ${money(a.fondo)} · ventas en efectivo ${money(a.ventas_efectivo)} · ingresos ${money(a.ingresos)} · retiros ${money(a.retiros)}</p></div>`
    : "<p class='hint'>No hay turno abierto.</p>";
  const filas = (d.cierres || []).filter(c => c.estado === "cerrado");
  const rango = q ? "en esas fechas" : "en los últimos 30 días";
  box.innerHTML = resumen + `<div id="cuadreVista"></div><h3>Historial de cuadres</h3><div id="cuadreTabla">` + tabla(
    ["Cierre","Turno","Cajero","Fondo","Esperado","Contado","Diferencia",""],
    filas.map(c => [
      String(c.cierre||"").slice(0,16), escDash(c.nombre), escDash(c.usuario || "—"),
      money(c.fondo), money(c.esperado), money(c.contado), money(c.diferencia),
      `<div class="acciones"><button class="sec" onclick="verCuadre(${c.id})">Ver</button><button class="sec" onclick="imprimirCierre(${c.id})">Imprimir</button><button class="sec" onclick="editarCierre(${c.id})">Editar</button></div>`
    ]),
    "No hay cuadres " + rango + "."
  ) + `</div>`;
}
function htmlCuadre(d){
  const montos = tabla(["Concepto","Monto"], [
    ["Fondo", money(d.fondo)],
    ["Ventas en efectivo", money(d.ventas_efectivo)],
    ["Ingresos", money(d.ingresos)],
    ["Retiros", money(d.retiros)],
    ["Esperado", money(d.esperado)],
    ["Contado", money(d.contado)],
    ["Diferencia", money(d.diferencia)]
  ]);
  const facturas = tabla(["Número","Fecha","Cliente","Total","Estado",""], (d.facturas||[]).map(f => [
    escDash(f.numero), String(f.fecha||"").slice(0,16), escDash(f.cliente), money(f.total), escDash(f.estado),
    `<button class="sec" onclick="verFacturaDelCuadre(${f.id})">Ver</button>`
  ]), "Este turno no tiene facturas.");
  const movs = tabla(
    ["Fecha","Tipo","Monto","Motivo","Usuario"],
    (d.movimientos || []).map(m => [String(m.fecha||"").slice(0,16), m.tipo, money(m.monto), escDash(m.motivo), escDash(m.usuario || "—")]),
    "Este turno no tiene ingresos ni retiros."
  );
  return `<div class="doc"><h3>Cuadre · ${escDash(d.nombre)}</h3><p>Abrió ${escDash(d.usuario_apertura || "—")} el ${escDash(String(d.apertura||"").slice(0,16))}<br>Cerró ${escDash(d.usuario_cierre || "—")} el ${escDash(String(d.cierre||"").slice(0,16))}</p>${montos}<p>${escDash(d.observaciones || "Sin observación")}</p><button onclick="imprimirCierre(${d.id})">Imprimir cierre</button></div><h3>Facturas del turno</h3>${facturas}<h3>Movimientos del turno</h3>${movs}`;
}
async function verCuadre(id){
  const d = await api("/api/caja/cierre/" + id);
  window._cuadreDoc = d;
  const vista = document.getElementById("cuadreVista");
  if (!vista) return;
  vista.innerHTML = htmlCuadre(d);
  vista.scrollIntoView({block:"nearest"});
}
function verFacturaDelCuadre(id){
  const f = ((window._cuadreDoc && window._cuadreDoc.facturas) || []).find(x => x.id === id);
  const vista = document.getElementById("cuadreVista");
  if (!f || !vista) return;
  vista.innerHTML = htmlDocumento(f) + `<button class="sec" onclick="verCuadre(${window._cuadreDoc.id})">Volver al cuadre</button>`;
  vista.scrollIntoView({block:"nearest"});
}
async function verCuadreDesdeFactura(id){
  await ver("cerrar");
  await verCuadre(id);
}
async function imprimirCierre(id){
  const d = await api("/api/caja/cierre/" + id);
  const emp = window._empresa || {};
  const ancho = (document.getElementById("anchoTicket") || {}).value || "80";
  recibo.style.width = ancho === "58" ? "58mm" : "80mm";
  const movs = (d.movimientos || []).map(m => `<tr><td>${escDash(m.tipo)} ${escDash(m.motivo)}</td><td>${money(m.monto)}</td></tr>`).join("");
  const nums = (d.facturas || []).map(f => escDash(f.numero)).join(", ");
  recibo.innerHTML = `<h2>${escDash(emp.nombre || "Cierre de caja")}</h2><p>${escDash(emp.direccion || "")}<br>RNC ${escDash(emp.rnc || "—")}</p><p><b>CIERRE DE CAJA</b><br>${escDash(d.nombre)}<br>Abrió ${escDash(d.usuario_apertura || "—")}<br>${escDash(String(d.apertura||"").slice(0,16))}<br>Cerró ${escDash(d.usuario_cierre || "—")}<br>${escDash(String(d.cierre||"").slice(0,16))}</p><p>Fondo ${money(d.fondo)}<br>Ventas en efectivo ${money(d.ventas_efectivo)}<br>Ingresos ${money(d.ingresos)}<br>Retiros ${money(d.retiros)}</p><p>Esperado ${money(d.esperado)}<br>Contado ${money(d.contado)}<br><b>Diferencia ${money(d.diferencia)}</b></p><p>${escDash(d.observaciones || "")}</p>${nums ? `<p>Facturas<br>${nums}</p>` : ""}${movs ? `<table>${movs}</table>` : ""}`;
  window.print();
}
function abrirTurno(){
  window._turnoId = null;
  abrirModal("Abrir turno", `<div class="form"><label>Nombre<input id="cajaNombre" value="Caja 1"/></label><label>Fondo<input id="cajaFondo" type="number" step="0.01" value="0"/></label><button id="btnCajaAbrir" onclick="guardarCaja()">Abrir turno</button></div>`);
}
function editarTurno(){
  const a = window._cajaPanel && window._cajaPanel.abierta;
  if (!a) return;
  abrirTurno();
  window._turnoId = a.id;
  cajaNombre.value = a.nombre || "Caja 1";
  cajaFondo.value = a.fondo;
  modalTitulo.textContent = "Editar turno";
  btnCajaAbrir.textContent = "Guardar cambios";
}
function nuevoCierre(){
  window._cierreEdit = null;
  abrirModal("Cerrar turno", `<div class="form"><label>Efectivo contado<input id="cajaContado" type="number" step="0.01"/></label><label class="wide">Observación<input id="cajaObs"/></label><button id="btnCerrar" onclick="guardarCierre()">Cerrar turno</button></div>`);
}
function editarCierre(id){
  const c = ((window._cajaPanel && window._cajaPanel.cierres) || []).find(x => x.id === id);
  if (!c) return;
  nuevoCierre();
  window._cierreEdit = id;
  cajaContado.value = c.contado;
  cajaObs.value = c.observaciones || "";
  modalTitulo.textContent = "Corregir cierre";
  btnCerrar.textContent = "Guardar cambios";
}
async function guardarCaja(){
  try {
    const editando = window._turnoId;
    if (editando) {
      await api("/api/caja/turno/" + window._turnoId, {method:"POST", body: JSON.stringify({nombre: cajaNombre.value, fondo: Number(cajaFondo.value||0)})});
    } else {
      await api("/api/caja/abrir", {method:"POST", body: JSON.stringify({nombre: cajaNombre.value, fondo: Number(cajaFondo.value||0)})});
    }
    window._turnoId = null;
    okModal(editando ? "Turno actualizado." : "Turno abierto.");
    await refrescar();
    await cargarPanelCaja("caja");
  } catch(e){ errorModal(e); }
}
async function cargarMovimientos(tipo){
  const d = await api("/api/caja/panel");
  const box = document.getElementById("movLista");
  if (!box) return;
  const a = d.abierta;
  const resumen = a
    ? `<div class="doc"><p>${escDash(a.nombre)} · efectivo esperado ${money(a.esperado)}</p><p>Fondo ${money(a.fondo)} · ventas en efectivo ${money(a.ventas_efectivo)} · ingresos ${money(a.ingresos)} · retiros ${money(a.retiros)}</p></div>`
    : "<p class='hint'>No hay turno abierto. Los movimientos de turnos cerrados se consultan; solo se editan los del turno abierto.</p>";
  window._movs = (d.recientes || []).filter(m => m.tipo === tipo);
  box.innerHTML = resumen + tabla(["Fecha","Monto","Motivo","Usuario",""], window._movs.map(m => [
    String(m.fecha||"").slice(0,16), money(m.monto), escDash(m.motivo), m.usuario || "—",
      m.abierto ? `<button class="sec" onclick="editarMov(${m.id})">Editar</button>` : "Turno cerrado"
  ]), "Todavía no hay movimientos en este turno.");
}
function nuevoMov(tipo){
  window._movId = null;
  const titulo = tipo === "ingreso" ? "Registrar ingreso" : "Registrar retiro";
  abrirModal(titulo, `<div class="form"><label>Monto<input id="movMonto" type="number" step="0.01"/></label><label class="wide">Motivo<input id="movMotivo"/></label><button id="btnMov" onclick="guardarMov('${tipo}')">${titulo}</button></div>`);
}
function editarMov(id){
  const m = (window._movs || []).find(x => x.id === id);
  if (!m) return;
  nuevoMov(m.tipo);
  window._movId = id;
  movMonto.value = m.monto;
  movMotivo.value = m.motivo || "";
  modalTitulo.textContent = "Editar movimiento";
  btnMov.textContent = "Guardar cambios";
}
async function guardarMov(tipo){
  try {
    const editando = window._movId;
    const body = {tipo, monto: Number(movMonto.value), motivo: movMotivo.value};
    if (editando) body.id = editando;
    await api("/api/caja/movimiento", {method:"POST", body: JSON.stringify(body)});
    window._movId = null;
    okModal(editando ? "Movimiento actualizado." : "Movimiento guardado.");
    await refrescar();
    await cargarMovimientos(tipo);
  } catch(e){ errorModal(e); }
}
async function guardarCierre(){
  try {
    const editando = window._cierreEdit;
    const payload = {contado: Number(cajaContado.value), observaciones: cajaObs.value};
    const url = editando ? "/api/caja/cierre/" + editando : "/api/caja/cerrar";
    const r = await api(url, {method:"POST", body: JSON.stringify(payload)});
    const cerradoId = editando || r.id;
    window._cierreEdit = null;
    okModal((editando ? "Cierre actualizado. " : "Cerrado. ") + "Esperado " + money(r.esperado) + " · diferencia " + money(r.diferencia));
    await refrescar();
    await cargarPanelCaja("cerrar");
    if (!editando && cerradoId) await imprimirCierre(cerradoId);
  } catch(e){ errorModal(e); }
}
let lineasCompra = [];
function nuevaCompra(){
  lineasCompra = [];
  const provs = window._provs || [];
  const bods = window._bods || ["Principal"];
  const provHtml = provs.length
    ? provs.map(p => `<option>${String(p.nombre).replaceAll("<","&lt;")}</option>`).join("")
    : `<option value=""></option>`;
  abrirModal("Registrar compra", `<div class="form"><label>Proveedor<select id="compProv">${provHtml}</select></label><label>Bodega<select id="compBod">${bods.map(b => `<option>${b}</option>`).join("")}</select></label><label>Producto<select id="compProd">${window._optsProd || ""}</select></label><label>Cantidad<input id="compCant" type="number" step="0.01"/></label><label>Costo<input id="compCosto" type="number" step="0.01"/></label><button class="sec" onclick="agregarLineaCompra()">Agregar línea</button><button onclick="guardarCompra()">Registrar compra</button></div><div id="compLineas"></div>`);
}
function agregarLineaCompra(){
  const cant = Number(compCant.value);
  const costo = Number(compCosto.value);
  if(!compProd.value || cant<=0) return;
  lineasCompra.push({producto_id: Number(compProd.value), nombre: compProd.selectedOptions[0].textContent, cantidad: cant, costo});
  compLineas.innerHTML = tabla(["Producto","Cantidad","Costo"], lineasCompra.map(l => [l.nombre, l.cantidad, money(l.costo)]));
}
async function guardarCompra(){
  if(!lineasCompra.length) agregarLineaCompra();
  if(!lineasCompra.length) { errorModal({message:"Agrega al menos una línea."}); return; }
  try {
    const r = await api("/api/compras", {method:"POST", body: JSON.stringify({
      proveedor: compProv.value, bodega: compBod.value,
      lineas: lineasCompra.map(l => ({producto_id:l.producto_id, cantidad:l.cantidad, costo:l.costo}))
    })});
    lineasCompra = [];
    okModal("Compra " + r.compra + " registrada, " + r.lineas + " líneas.");
    productosCache = [];
    await cargarCompras();
  } catch(e){ errorModal(e); }
}
function formPromo(){
  return `<div class="form"><label>Nombre<input id="promoNom"/></label><label>Tipo<select id="promoTipo"><option>porcentaje</option><option>fijo</option><option>2x1</option><option>3x2</option></select></label><label>Valor<input id="promoVal" type="number" step="0.01" value="10"/></label><label>Producto<select id="promoProd">${window._optsProd || ""}</select></label><label>Estado<select id="promoActiva"><option value="1">Activa</option><option value="0">Inactiva</option></select></label><button id="btnPromo" onclick="guardarPromo()">Crear promoción</button></div>`;
}
function nuevaPromo(){
  window._promoId = null;
  abrirModal("Crear promoción", formPromo());
}
async function guardarPromo(){
  try {
    const editando = window._promoId;
    const body = {
      nombre: promoNom.value, tipo: promoTipo.value, valor: Number(promoVal.value),
      producto_id: Number(promoProd.value), activa: promoActiva.value === "1"
    };
    const url = editando ? "/api/promociones/" + editando : "/api/promociones";
    await api(url, {method:"POST", body: JSON.stringify(body)});
    window._promoId = null;
    okModal(editando ? "Promoción actualizada." : "Promoción creada.");
    await pintarPromos();
  } catch(e){ errorModal(e); }
}
function editarPromo(id){
  const p = (window._promos || []).find(x => x.id === id);
  if (!p) return;
  nuevaPromo();
  window._promoId = id;
  promoNom.value = p.nombre;
  promoTipo.value = p.tipo;
  promoVal.value = p.valor;
  if (p.producto_id) promoProd.value = String(p.producto_id);
  promoActiva.value = p.activa ? "1" : "0";
  modalTitulo.textContent = "Editar promoción";
  btnPromo.textContent = "Guardar cambios";
}
async function pintarPromos(){
  const data = await api("/api/promociones");
  window._promos = data.items || [];
  promoLista.innerHTML = tabla(["Nombre","Tipo","Valor","Estado",""], window._promos.map(p => [
    escDash(p.nombre), p.tipo, p.valor, p.activa ? "Activa" : "Inactiva",
    `<button class="sec" onclick="editarPromo(${p.id})">Editar</button>`
  ]));
}
function nuevaDevolucion(){
  abrirModal("Nueva devolución", `<div class="form"><label class="wide">Número o id de factura<input id="devQ"/></label><button class="sec" onclick="buscarDev()">Cargar factura</button></div><div id="devLineas"></div>`);
}
async function buscarDev(){
  try {
    const fac = await api("/api/devolucion?q="+encodeURIComponent(devQ.value));
    window._devFac = fac;
    const opciones = `<option value="efectivo">Devolver efectivo (sale del turno)</option>`
      + (fac.cliente_id ? `<option value="saldo_favor">Saldo a favor de ${escDash(fac.cliente)}</option>` : "")
      + (fac.cliente_id && fac.pendiente > 0 ? `<option value="abono" selected>Rebajar de lo pendiente (${money(fac.pendiente)})</option>` : "")
      + `<option value="nota">Solo nota de crédito</option>`;
    devLineas.innerHTML = `<p>${fac.numero} · ${escDash(fac.cliente)} · ${money(fac.total)}${fac.pendiente > 0 ? ` · <span class="pill mal">pendiente ${money(fac.pendiente)}</span>` : ""}</p>` + fac.lineas.map(l =>
      `<div class="form" style="margin-top:8px"><label>${l.nombre}<input data-line="${l.id}" type="number" step="0.01" value="0" max="${l.cantidad}" placeholder="máx ${l.cantidad}"/></label></div>`
    ).join("") + `<div class="form" style="margin-top:8px"><label class="wide">Motivo<input id="devMotivo" value="Devolución en mostrador"/></label><label>Qué recibe el cliente<select id="devDestino">${opciones}</select></label><button onclick="guardarDev()">Registrar devolución</button></div>`;
    const m = document.getElementById("modalMsg");
    if (m) m.textContent = "";
  } catch(e){ errorModal(e); }
}
async function guardarDev(){
  const lineas = [...devLineas.querySelectorAll("[data-line]")].map(el => ({id: Number(el.dataset.line), cantidad: Number(el.value||0)})).filter(l => l.cantidad>0);
  try {
    const r = await api("/api/devolucion", {method:"POST", body: JSON.stringify({factura: (window._devFac && window._devFac.numero) || devQ.value, motivo: devMotivo.value, lineas, destino: devDestino.value})});
    okModal(r.mensaje);
    productosCache = [];
    await cargarDevoluciones();
  } catch(e){ errorModal(e); }
}
async function cargarDevoluciones(){
  const d = await api("/api/devoluciones");
  window._devs = d.items || [];
  const box = document.getElementById("devLista");
  if (!box) return;
  box.innerHTML = "<h3>Notas de crédito</h3>" + tabla(["Fecha","Factura","Total","Motivo",""], window._devs.map(n => [
    String(n.fecha||"").slice(0,16), n.factura || "—", money(n.total), escDash(n.motivo),
    `<button class="sec" onclick="verDevolucion(${n.id})">Ver</button> <button class="sec" onclick="editarDevolucion(${n.id})">Editar</button>`
  ]), "Todavía no hay notas de crédito.") + `<div id="devVista"></div>`;
}
async function verDevolucion(id){
  const d = await api("/api/devoluciones/" + id);
  const vista = document.getElementById("devVista");
  if (vista) vista.innerHTML = tabla(["Producto","Cantidad","Monto"], (d.lineas||[]).map(l => [escDash(l.nombre), l.cantidad, money(l.monto)]));
}
function editarDevolucion(id){
  const n = (window._devs || []).find(x => x.id === id);
  if (!n) return;
  abrirModal("Editar devolución", `<div class="form"><label class="wide">Motivo<input id="devMotivoEdit"/></label><button onclick="guardarMotivoDev(${id})">Guardar cambios</button></div>`);
  devMotivoEdit.value = n.motivo || "";
}
async function guardarMotivoDev(id){
  try {
    await api("/api/devolucion/" + id, {method:"POST", body: JSON.stringify({motivo: devMotivoEdit.value})});
    okModal("Motivo actualizado.");
    await cargarDevoluciones();
  } catch(e){ errorModal(e); }
}
function nuevaTransfer(){
  const bods = window._bods || ["Principal"];
  const dest = bods.find(b => b !== "Principal") || "Principal";
  abrirModal("Transferir", `<div class="form"><label>Producto<select id="trProd">${window._optsProd || ""}</select></label><label>Origen<select id="trOri">${opcionesBodega(bods, "Principal")}</select></label><label>Destino<select id="trDes">${opcionesBodega(bods, dest)}</select></label><label>Cantidad<input id="trCant" type="number" step="0.01"/></label><button onclick="guardarTr()">Transferir</button></div>`);
}
async function guardarTr(){
  try {
    await api("/api/transferir", {method:"POST", body: JSON.stringify({
      producto_id: Number(trProd.value), origen: trOri.value, destino: trDes.value, cantidad: Number(trCant.value)
    })});
    okModal("Transferencia registrada.");
    await cargarBodegas();
  } catch(e){ errorModal(e); }
}
async function pintarReportes(){
  const desde = document.getElementById("repDesde");
  const hasta = document.getElementById("repHasta");
  const q = (desde && desde.value && hasta && hasta.value) ? ("&desde="+desde.value+"&hasta="+hasta.value) : "";
  const d = await api("/api/reportes?dias=30"+q);
  const html =
    "<h3>Más vendidos</h3>" + tabla(["Producto","Cantidad","Total"], d.vendidos.map(x => [x.nombre, x.cantidad, money(x.total)])) +
    "<h3>Por cajero</h3>" + tabla(["Cajero","Facturas","Total"], d.cajeros.map(x => [x.usuario, x.facturas, money(x.total)])) +
    ((d.vendedores || []).some(x => x.vendedor !== "Sin vendedor") ? "<h3>Por vendedor</h3>" + tabla(["Vendedor","Facturas","Total"], d.vendedores.map(x => [escDash(x.vendedor), x.facturas, money(x.total)])) : "") +
    ((d.terminales || []).some(x => x.terminal !== "Sin terminal") ? "<h3>Por terminal</h3>" + tabla(["Terminal","Facturas","Total"], d.terminales.map(x => [escDash(x.terminal), x.facturas, money(x.total)])) : "") +
    "<h3>Por forma de pago</h3>" + tabla(["Método","Pagos","Total"], d.pagos.map(x => [x.metodo, x.pagos, money(x.total)]));
  const caja = document.getElementById("repTabla");
  if (caja) caja.innerHTML = html; else formBody.innerHTML = html;
}
async function pintarNcf(){
  const d = await api("/api/ncf");
  window._ncfs = d.secuencias || [];
  formBody.innerHTML = `<div class="form"><button onclick="nuevoNcf()">Nueva secuencia</button></div>` +
    tabla(["Tipo","Prefijo","Siguiente","Hasta",""], window._ncfs.map((x, i) => [escDash(x.tipo), escDash(x.prefijo), x.siguiente, x.hasta, `<button class="sec" onclick="editarNcf(${i})">Editar</button>`]));
}
function formNcf(){
  return `<div class="form"><label>Tipo<input id="ncfTipo"/></label><label>Prefijo<input id="ncfPref"/></label><label>Siguiente<input id="ncfSig" type="number"/></label><label>Hasta<input id="ncfHasta" type="number"/></label><button onclick="guardarNcf()">Guardar secuencia</button></div>`;
}
function nuevoNcf(){ abrirModal("Nueva secuencia", formNcf()); }
function editarNcf(i){
  const x = (window._ncfs || [])[i];
  if (!x) return;
  nuevoNcf();
  modalTitulo.textContent = "Editar secuencia";
  ncfTipo.value = x.tipo;
  ncfPref.value = x.prefijo;
  ncfSig.value = x.siguiente;
  ncfHasta.value = x.hasta;
}
async function guardarNcf(){
  try {
    await api("/api/ncf", {method:"POST", body: JSON.stringify({tipo:ncfTipo.value, prefijo:ncfPref.value, siguiente:Number(ncfSig.value), hasta:Number(ncfHasta.value)})});
    okModal("Secuencia guardada.");
    await pintarNcf();
  } catch(e){ errorModal(e); }
}
function precioConDescuento(base, pct){
  return (Number(base || 0) * (1 - Number(pct || 0) / 100)).toFixed(2);
}
function precioNivel(guardado, base, pct){
  return Number(guardado || 0) > 0 ? guardado : precioConDescuento(base, pct);
}
function pintarEtiquetasMayor(){
  if (document.getElementById("etiqP2")) etiqP2.textContent = descP2.value;
  if (document.getElementById("etiqP3")) etiqP3.textContent = descP3.value;
  if (document.getElementById("etiqP4")) etiqP4.textContent = descP4.value;
}
function formProducto(){
  const x = window._descuentos || {p2:2, p3:5, p4:10};
  return `<div class="form"><label>Nombre<input id="invNom"/></label><label>Precio original<input id="invOriginal" type="number" step="0.01"/></label><label>Precio mínimo de venta<input id="invPrecio" type="number" step="0.01" oninput="recalcularPreciosMayor()"/></label><label>Al ${x.p2}%<input id="invP2" type="number" step="0.01"/></label><label>Al ${x.p3}%<input id="invP3" type="number" step="0.01"/></label><label>Al ${x.p4}%<input id="invP4" type="number" step="0.01"/></label><label>Stock<input id="invStock" type="number" step="0.01" value="0"/></label><label>Código<input id="invCod"/></label><label>Bodega<input id="invBod" value="Principal"/></label><label>Categoría<input id="invCat"/></label><button id="btnProd" onclick="guardarProducto()">Crear producto</button></div>`;
}
function nuevoProducto(){
  window._prodId = null;
  abrirModal("Crear producto", formProducto());
}
function recalcularPreciosMayor(){
  if (!document.getElementById("invPrecio")) return;
  const base = Number(invPrecio.value || 0);
  invP2.value = precioConDescuento(base, descP2.value);
  invP3.value = precioConDescuento(base, descP3.value);
  invP4.value = precioConDescuento(base, descP4.value);
  pintarEtiquetasMayor();
}
async function cargarInventario(){
  const qv = document.getElementById("invQ");
  const d = await api("/api/inventario?q=" + encodeURIComponent(qv ? qv.value : ""));
  window._prods = d.items || [];
  if (d.descuentos && document.getElementById("descP2")) {
    descP2.value = d.descuentos.p2;
    descP3.value = d.descuentos.p3;
    descP4.value = d.descuentos.p4;
    etiquetasLista(d.descuentos);
    pintarEtiquetasMayor();
  }
  const x = window._descuentos || {p2:2, p3:5, p4:10};
  invTabla.innerHTML = tabla(["","Producto","","Original","Mínimo", x.p2+"%", x.p3+"%", x.p4+"%", "Stock","Código","Bodega","Categoría"], window._prods.map(p => [
    `<input class="etiq" type="checkbox" value="${p.id}"/>`, escDash(p.nombre),
    `<button class="sec" onclick="editarProducto(${p.id})">Editar</button>`,
    money(p.precio_original), money(p.precio), money(precioNivel(p.precio_2, p.precio, x.p2)), money(precioNivel(p.precio_3, p.precio, x.p3)), money(precioNivel(p.precio_4, p.precio, x.p4)),
    p.stock, escDash(p.codigo || "—"), escDash(p.bodega), escDash(p.categoria || "—")
  ]));
  if (document.getElementById("invPrecio")) invPrecio.oninput = recalcularPreciosMayor;
  [descP2, descP3, descP4].forEach(el => { if (el) el.oninput = recalcularPreciosMayor; });
}
function editarProducto(id){
  const p = (window._prods || []).find(x => x.id === id);
  if (!p) return;
  const d2 = document.getElementById("descP2");
  const d3 = document.getElementById("descP3");
  const d4 = document.getElementById("descP4");
  nuevoProducto();
  window._prodId = id;
  invNom.value = p.nombre || "";
  invOriginal.value = Number(p.precio_original || 0) > 0 ? p.precio_original : "";
  invPrecio.value = p.precio;
  invP2.value = Number(p.precio_2 || 0) > 0 ? p.precio_2 : precioConDescuento(p.precio, d2 ? d2.value : 2);
  invP3.value = Number(p.precio_3 || 0) > 0 ? p.precio_3 : precioConDescuento(p.precio, d3 ? d3.value : 5);
  invP4.value = Number(p.precio_4 || 0) > 0 ? p.precio_4 : precioConDescuento(p.precio, d4 ? d4.value : 10);
  invStock.value = p.stock;
  invCod.value = p.codigo || "";
  invBod.value = p.bodega || "Principal";
  invCat.value = p.categoria || "";
  modalTitulo.textContent = "Editar producto";
  btnProd.textContent = "Guardar cambios";
  invNom.focus();
}
async function guardarDescuentos(){
  try {
    const r = await api("/api/precios-mayor", {method:"POST", body: JSON.stringify({
      p2: Number(descP2.value), p3: Number(descP3.value), p4: Number(descP4.value)
    })});
    etiquetasLista(r);
    formMsg.style.color = "var(--ok)";
    formMsg.textContent = "Porcentajes guardados. Se recalcularon " + r.productos + " productos.";
    productosCache = [];
    await cargarInventario();
  } catch(e){ formMsg.style.color="var(--danger)"; formMsg.textContent = e.message; }
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
    formMsg.style.color = "var(--danger)";
    formMsg.textContent = "Marca al menos un producto.";
    return;
  }
  try {
    await bajarPdf("/api/etiquetas", "etiquetas.pdf", {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({ids})});
    formMsg.style.color = "var(--ok)";
    formMsg.textContent = "PDF de etiquetas descargado.";
  } catch(e){ formMsg.style.color="var(--danger)"; formMsg.textContent = e.message; }
}
async function guardarProducto(){
  try {
    const editando = window._prodId;
    const body = {
      nombre: invNom.value, precio_original: Number(invOriginal.value), precio: Number(invPrecio.value),
      precio_2: Number(invP2.value), precio_3: Number(invP3.value), precio_4: Number(invP4.value),
      stock: Number(invStock.value||0), codigo: invCod.value, bodega: invBod.value, categoria: invCat.value
    };
    const url = editando ? "/api/inventario/" + editando : "/api/inventario";
    await api(url, {method:"POST", body: JSON.stringify(body)});
    window._prodId = null;
    okModal(editando ? "Producto actualizado." : "Producto creado.");
    productosCache = [];
    await cargarInventario();
  } catch(e){ errorModal(e); }
}
async function cargarKardex(){
  if (!document.getElementById("kxProd") || !kxProd.value) return;
  const d = await api("/api/kardex?producto_id=" + Number(kxProd.value));
  const nombre = kxProd.selectedOptions[0] ? kxProd.selectedOptions[0].textContent : "Producto";
  kxTabla.innerHTML = `<p class="hint">${escDash(nombre)} · existencia ${d.stock}</p>` +
    tabla(["Fecha","Movimiento","Tipo","Cantidad","Saldo","Bodega"], (d.items||[]).map(x => [String(x.fecha||"").slice(0,16), escDash(x.descripcion), x.tipo_codigo, x.cantidad, x.balance, escDash(x.bodega)]), "Este producto no tiene movimientos.");
}
async function cargarHistorial(){
  const est = document.getElementById("histEstado");
  const d = await api("/api/historial?estado=" + encodeURIComponent(est ? est.value : "emitidas"));
  histTabla.innerHTML = tabla(["Número","Fecha","Cliente","Total","Estado","Usuario",""], d.items.map(f => {
    const acciones = `<div class="acciones"><button class="sec" onclick="verDocumento(${f.id})">Ver</button><button class="sec" onclick="reimprimirFactura(${f.id})">Imprimir</button><details class="mas"><summary aria-label="Más acciones">Más</summary><div class="menu"><button type="button" onclick="bajarFactura(${f.id})">Descargar PDF</button><button type="button" onclick="enviarFactura(${f.id}, '${String(f.email||"").replaceAll("'","")}')">Enviar por correo</button><button type="button" onclick="repetirFactura(${f.id})">Repetir venta</button>` +
      (f.estado==="emitida" ? `<button type="button" onclick="irDevolver('${String(f.numero).replaceAll("'","")}')">Devolver</button><button type="button" class="peligro" onclick="pedirAnulacion(${f.id}, '${String(f.numero).replaceAll("'","")}')">Anular factura</button>` : "") + "</div></details></div>";
    const fecha = String(f.fecha||"");
    return [escDash(f.numero), fecha.slice(0,10)+"<br>"+fecha.slice(11,16), escDash(f.cliente), money(f.total), f.estado, escDash(f.usuario || "—"), acciones];
  }));
}
async function bajarFactura(id){
  try {
    await bajarPdf("/api/factura/"+id+"/pdf", "comprobante-"+id+".pdf");
    formMsg.style.color = "var(--ok)";
    formMsg.textContent = "PDF descargado.";
  } catch(e){ formMsg.style.color="var(--danger)"; formMsg.textContent = e.message; }
}
async function enviarFactura(id, emailActual){
  const email = prompt("Correo del cliente", emailActual || "");
  if(!email) return;
  try {
    const r = await api("/api/factura/"+id+"/correo", {method:"POST", body: JSON.stringify({email})});
    formMsg.style.color = "var(--ok)";
    formMsg.textContent = r.mensaje;
    await cargarHistorial();
  } catch(e){ formMsg.style.color="var(--danger)"; formMsg.textContent = e.message; }
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
function pedirAnulacion(id, numero){
  abrirModal("Anular " + numero, `<p class="hint">La factura queda anulada y el inventario vuelve a su existencia. Esta acción no se puede deshacer.</p><div class="form"><label class="wide">Motivo de la anulación<input id="motivoAnulaModal" placeholder="Ej.: cobro duplicado"/></label><button type="button" class="peligro" onclick="anularFactura(${id})">Anular factura</button></div>`);
  setTimeout(() => { const m = document.getElementById("motivoAnulaModal"); if (m) m.focus(); }, 60);
}
async function anularFactura(id){
  const motivo = document.getElementById("motivoAnulaModal");
  const texto = motivo ? motivo.value : "";
  if((texto||"").trim().length < 3){
    errorModal({message: "Escribe el motivo, mínimo 3 caracteres."});
    return;
  }
  try {
    const r = await api("/api/historial/anular", {method:"POST", body: JSON.stringify({factura_id:id, motivo:texto})});
    okModal(r.mensaje);
    await cargarHistorial();
  } catch(e){ errorModal(e); }
}
let editLineas = [];
function htmlDocumento(f){
  const lineas = tabla(["Producto","Cantidad","Precio","ITBIS","Total"], (f.lineas||[]).map(l => [escDash(l.nombre), l.cantidad, money(l.precio), money(l.itbis), money(l.total)]));
  const editar = f.estado==="cotizacion" ? `<button onclick="editarCotizacion(${f.id})">Editar</button>` : "";
  const cuadre = f.cierre_id ? `<button class="sec" onclick="verCuadreDesdeFactura(${f.cierre_id})">Ver cuadre</button>` : "";
  return `<div class="doc"><h3>${escDash(f.numero)}</h3><p>${escDash(f.cliente)} · ${escDash(f.estado)}${f.ncf ? " · "+escDash(f.ncf) : ""}</p>${lineas}<p>Subtotal ${money(f.subtotal)} · ITBIS ${money(f.itbis)} · <b>Total ${money(f.total)}</b>${f.pendiente > 0 ? ` · <span class="pill mal">Pendiente ${money(f.pendiente)}${f.vence ? ", vence " + f.vence : ""}</span>` : ""}</p>${f.vendedor ? `<p class="hint">Vendedor: ${escDash(f.vendedor)}</p>` : ""}${f.observaciones ? `<p class="hint">Observaciones: ${escDash(f.observaciones)}</p>` : ""}${editar} ${cuadre}</div>`;
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
  } catch(e){ formMsg.style.color="var(--danger)"; formMsg.textContent = e.message; }
}
async function editarCotizacion(id){
  try {
    const f = await api("/api/factura/"+id);
    editLineas = (f.lineas||[]).filter(l => l.producto_id).map(l => ({id:l.producto_id, nombre:l.nombre, cantidad:l.cantidad, precio:l.precio}));
    window._editId = id;
    window._editCliente = f.cliente_id || "";
    await pintarEditor();
  } catch(e){ formMsg.style.color="var(--danger)"; formMsg.textContent = e.message; }
}
async function pintarEditor(){
  const lista = await productos();
  let clientes = [];
  try { clientes = (await api("/api/clientes")).items || []; } catch(e) { clientes = []; }
  const opciones = lista.map(p => `<option value="${p.id}">${escDash(p.nombre)}</option>`).join("");
  const clientesHtml = `<option value="">Consumidor final</option>` + clientes.map(c => `<option value="${c.id}" ${String(c.id)===String(window._editCliente)?"selected":""}>${escDash(c.nombre)}</option>`).join("");
  const filas = editLineas.map((l,i) => `<div class="form"><span>${escDash(l.nombre)} · ${money(l.precio)}</span><label>Cantidad<input data-edit="${i}" type="number" step="0.01" value="${l.cantidad}"/></label><button class="sec" onclick="quitarLineaEdit(${i})">Quitar</button></div>`).join("");
  document.getElementById("docVista").innerHTML = `<div class="doc"><h3>Editar cotización</h3><div class="form"><label>Cliente<select id="editCli">${clientesHtml}</select></label><label>Producto<select id="editProd">${opciones}</select></label><label>Cantidad<input id="editCant" type="number" step="0.01" value="1"/></label><button class="sec" onclick="agregarLineaEdit()">Agregar</button></div>${filas}<button onclick="guardarCotizacionEditada()">Guardar cambios</button> <button class="sec" onclick="verDocumento(${window._editId})">Cancelar</button></div>`;
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
async function guardarCotizacionEditada(){
  document.querySelectorAll("[data-edit]").forEach(el => { editLineas[Number(el.dataset.edit)].cantidad = Number(el.value||0); });
  const cliente = document.getElementById("editCli");
  try {
    await api("/api/cotizaciones/"+window._editId, {method:"POST", body: JSON.stringify({
      items: editLineas.filter(l => l.cantidad>0).map(l => ({id:l.id, cantidad:Number(l.cantidad), precio:Number(l.precio)})),
      cliente_id: cliente && cliente.value ? Number(cliente.value) : null,
      lista: "Mínimo de venta"
    })});
    formMsg.style.color = "var(--ok)";
    formMsg.textContent = "Cotización actualizada.";
    await cargarCotizaciones();
    await verDocumento(window._editId);
  } catch(e){ formMsg.style.color="var(--danger)"; formMsg.textContent = e.message; }
}
async function cargarCotizaciones(){
  const d = await api("/api/historial?estado=presupuestos");
  formBody.innerHTML = `<div id="docVista"></div>` + tabla(["Número","Fecha","Cliente","Total","Estado",""], d.items.map(f => [f.numero, String(f.fecha||"").slice(0,16), f.cliente, money(f.total), f.estado, f.estado==="cotizacion" ? `<button class="sec" onclick="verDocumento(${f.id})">Ver</button> <button onclick="editarCotizacion(${f.id})">Editar</button> <button class="sec" onclick="confirmarCoti(${f.id})">Confirmar</button>` : `<button class="sec" onclick="verDocumento(${f.id})">Ver</button>`]));
}
async function confirmarCoti(id){
  try {
    const r = await api("/api/cotizaciones/confirmar", {method:"POST", body: JSON.stringify({factura_id:id})});
    formMsg.style.color = "var(--ok)";
    formMsg.textContent = r.mensaje;
    await cargarCotizaciones();
  } catch(e){ formMsg.style.color="var(--danger)"; formMsg.textContent = e.message; }
}
let rangoDash = "semana";
function variacion(n){
  if (n === null || n === undefined) return "<span>— sin período anterior</span>";
  if (Number(n) === 0) return "<span>0% vs período anterior</span>";
  const clase = n > 0 ? "pos" : "neg";
  return `<span class="${clase}">${n > 0 ? "+" : ""}${n}%</span> vs período anterior`;
}
function pasoRedondo(max){
  const crudo = max / 4;
  const mag = Math.pow(10, Math.floor(Math.log10(crudo || 1)));
  const n = crudo / mag;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10) * mag;
}
function graficaVentas(serie){
  const w = 640, h = 220, izq = 52, der = 12, arr = 10, aba = 28;
  const vals = serie.map(s => Number(s.total) || 0);
  const techo = Math.max(...vals, 100);
  const paso = pasoRedondo(techo);
  const tope = paso * Math.max(1, Math.ceil(techo / paso));
  const ancho = w - izq - der, alto = h - arr - aba;
  const pts = vals.map((v, i) => [izq + (vals.length <= 1 ? ancho / 2 : i * ancho / (vals.length - 1)), arr + alto - (v / tope) * alto]);
  const linea = pts.map((p, i) => (i ? "L" : "M") + p[0].toFixed(1) + "," + p[1].toFixed(1)).join(" ");
  const fmt = v => v >= 1000 ? (v / 1000).toLocaleString("es-DO", {maximumFractionDigits: 1}) + " mil" : v.toLocaleString("es-DO");
  let rejilla = "";
  for (let v = 0; v <= tope + 1e-9; v += paso) {
    const y = (arr + alto - (v / tope) * alto).toFixed(1);
    rejilla += `<line x1="${izq}" x2="${w - der}" y1="${y}" y2="${y}" style="stroke:${v === 0 ? "var(--control)" : "var(--rule)"}" stroke-width="1"/><text x="${izq - 8}" y="${y}" dy="4" text-anchor="end" style="fill:var(--faint)" font-size="11">${fmt(v)}</text>`;
  }
  const marcas = serie.map((s, i) => {
    if (serie.length > 8 && i % 2 && i !== serie.length - 1) return "";
    return `<text x="${pts[i][0].toFixed(1)}" y="${h - 8}" text-anchor="middle" style="fill:var(--faint)" font-size="11">${escDash(s.etiqueta)}</text>`;
  }).join("");
  const ultimo = pts[pts.length - 1];
  return `<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="Ventas del período en RD$">
    ${rejilla}
    <path class="trazo" d="${linea}" pathLength="1" fill="none" style="stroke:var(--data)" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
    ${ultimo ? `<circle class="punto" cx="${ultimo[0].toFixed(1)}" cy="${ultimo[1].toFixed(1)}" r="4" style="fill:var(--data)"/>` : ""}
    ${marcas}
  </svg>`;
}
function escDash(texto){
  return String(texto || "").replaceAll("&","&amp;").replaceAll("<","&lt;");
}
function barrasDash(items){
  if (!items.length) return "<p class='hint'>No hay datos en este período.</p>";
  const max = Math.max(...items.map(i => Number(i.total) || 0), 1);
  return items.map((i, n) => `<div class="barrow"><span>${escDash(i.nombre)}</span><div class="track"><div class="barra" style="width:${Math.max(3, (Number(i.total)||0)/max*100)}%;background:${n === 0 ? "var(--text)" : "var(--faint)"}"></div></div><b>${money(i.total)}</b></div>`).join("");
}
function donaPagos(pagos){
  const colores = ["var(--data)", "var(--serie-2)", "var(--serie-3)", "var(--r-caja)", "var(--serie-4)"];
  const total = pagos.reduce((s, p) => s + (Number(p.total) || 0), 0);
  if (!total) return "<p class='hint'>No hay cobros en este período.</p>";
  const r = 52, c = 2 * Math.PI * r;
  let cursor = 0;
  const arcos = pagos.slice(0, 5).map((p, i) => {
    const parte = (Number(p.total) || 0) / total;
    const largo = Math.max(0, parte * c - (pagos.length > 1 ? 2 : 0));
    const arco = `<circle class="arco" cx="60" cy="60" r="${r}" fill="none" style="stroke:${colores[i]}" stroke-width="10" stroke-dasharray="${largo.toFixed(2)} ${c.toFixed(2)}" stroke-dashoffset="${(-cursor * c).toFixed(2)}" transform="rotate(-90 60 60)"/>`;
    cursor += parte;
    return arco;
  }).join("");
  const leyenda = pagos.slice(0, 5).map((p, i) => `<span><i style="background:${colores[i]}"></i>${escDash(p.metodo)}<b>${money(p.total)}</b></span>`).join("");
  return `<div class="anillo"><svg viewBox="0 0 120 120" role="img" aria-label="Cobros por forma de pago"><circle cx="60" cy="60" r="${r}" fill="none" style="stroke:var(--raised)" stroke-width="10"/>${arcos}<text x="60" y="58" text-anchor="middle" style="fill:var(--faint)" font-size="10">Total</text><text x="60" y="74" text-anchor="middle" style="fill:var(--text)" font-size="13" font-weight="600">${money(total).replace("RD$ ","")}</text></svg><div class="leyenda">${leyenda}</div></div>`;
}
function animarIndicadores(){
  formBody.querySelectorAll(".kpi").forEach((k, i) => animar(k, [{opacity:0, transform:"translateY(8px)"}, {opacity:1, transform:"none"}], {duration:320, delay:i * 40}));
  formBody.querySelectorAll(".dash-card").forEach((k, i) => animar(k, [{opacity:0, transform:"translateY(10px)"}, {opacity:1, transform:"none"}], {duration:360, delay:120 + i * 50}));
  const trazo = formBody.querySelector(".trazo");
  if (trazo && !reducido()) trazo.animate([{strokeDasharray:"1 1", strokeDashoffset:1}, {strokeDasharray:"1 1", strokeDashoffset:0}], {duration:700, delay:220, easing:"cubic-bezier(0.23, 1, 0.32, 1)", fill:"backwards"});
  formBody.querySelectorAll(".punto").forEach(pt => animar(pt, [{opacity:0}, {opacity:1}], {duration:200, delay:860}));
  formBody.querySelectorAll(".barra").forEach((b, i) => animar(b, [{transform:"scaleX(0)"}, {transform:"scaleX(1)"}], {duration:480, delay:260 + i * 45}));
  formBody.querySelectorAll(".anillo svg").forEach(s => animar(s, [{opacity:0, transform:"rotate(-30deg) scale(.94)"}, {opacity:1, transform:"none"}], {duration:520, delay:260}));
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
      <div><p>${escDash(d.etiqueta)} · ventas ${money(d.ventas)} en ${d.facturas} ${Number(d.facturas) === 1 ? "factura" : "facturas"}</p></div>
      <div class="rangos">
        <button class="${activo("dia")}" onclick="dashRango('dia')">Día</button>
        <button class="${activo("semana")}" onclick="dashRango('semana')">Semana</button>
        <button class="${activo("mes")}" onclick="dashRango('mes')">Mes</button>
      </div>
    </div>
    <div class="kpis">
      <div class="kpi"><span>Ventas</span><b>${money(d.ventas)}</b><em>${variacion(d.delta_ventas)}</em></div>
      <div class="kpi"><span>Facturas</span><b>${d.facturas}</b><em>${variacion(d.delta_facturas)}</em></div>
      <div class="kpi"><span>Ticket promedio</span><b>${money(d.ticket)}</b><em>${variacion(d.delta_ticket)}</em></div>
      <div class="kpi"><span>ITBIS</span><b>${money(d.itbis)}</b><em>${variacion(d.delta_itbis)}</em></div>
    </div>
    <div class="dash-fila">
      <div class="dash-card"><h3>Ventas en el tiempo</h3>${graficaVentas(d.serie || [])}</div>
      <div class="dash-col">
        <div class="dash-card"><h3>Formas de pago</h3>${donaPagos(d.pagos || [])}</div>
        <div class="dash-card side-stats">
          <h3>Inventario</h3>
          <div><span>Valor al costo</span><b>${money(d.valor_costo)}</b></div>
          <div><span>Valor a precio de venta</span><b>${money(d.valor_venta)}</b></div>
        </div>
      </div>
    </div>
    <div class="dash-fila par">
      <div class="dash-card"><h3>Más vendidos</h3>${barrasDash((d.productos||[]).map(p => ({nombre:p.nombre, total:p.total})))}</div>
      <div class="dash-card"><h3>Por cajero</h3>${barrasDash((d.cajeros||[]).map(c => ({nombre:c.usuario, total:c.total})))}</div>
    </div>
    <div class="dash-card side-stats operacion">
      <h3>Operación</h3>
      <div><span>Devoluciones</span><b>${d.devoluciones} · ${money(d.devoluciones_total)}</b></div>
      <div><span>Facturas anuladas</span><b>${d.anuladas}</b></div>
      <div><span>Caja</span><b>${caja}</b></div>
    </div>`;
  animarIndicadores();
}
const TIPOS_ECF = {31:"Crédito fiscal", 32:"Consumo", 33:"Nota de débito", 34:"Nota de crédito", 44:"Regímenes especiales", 45:"Gubernamental"};
const AMBIENTES_ECF = {testecf:"Pre-certificación (pruebas)", certecf:"Certificación", ecf:"Producción"};
const ESTADOS_ECF = {aceptado:["Aceptado","bien"], aceptado_condicional:["Aceptado con observación","aviso"], rechazado:["Rechazado","mal"],
  error:["Error","mal"], error_envio:["No se pudo enviar","mal"], pendiente_envio:["Por enviar",""], en_proceso:["En proceso",""], sin_certificado:["Sin firma",""]};
function pillEcf(estado){
  const [txt, cls] = ESTADOS_ECF[estado] || [estado, ""];
  return `<span class="pill ${cls}">${escDash(txt)}</span>`;
}
async function cargarEcf(){
  const [e, docs] = await Promise.all([api("/api/ecf/estado"), api("/api/ecf/documentos")]);
  window._ecf = e;
  const cert = e.certificado;
  const pend = (e.conteo.pendiente_envio || 0) + (e.conteo.en_proceso || 0);
  const malos = (e.conteo.rechazado || 0) + (e.conteo.error || 0) + (e.conteo.error_envio || 0);
  const pasos = [];
  if (!e.rnc || ![9,11].includes(e.rnc.length)) pasos.push("Escribe el RNC de la empresa en <b>Empresa y correo</b>.");
  if (!cert || !cert.ok) pasos.push("Carga el certificado digital (.p12) del negocio. La DGII lo entrega sin costo a MIPYMES; también lo emiten entidades autorizadas por INDOTEL.");
  if (!e.secuencias.length) pasos.push("Solicita en la Oficina Virtual de la DGII las secuencias e-NCF autorizadas (31, 32 y 34) y regístralas abajo.");
  if (e.ambiente !== "ecf") pasos.push("Prueba en pre-certificación y certificación; al aprobar, cambia el ambiente a Producción.");
  ecfPanel.innerHTML = `
    <div class="kpis tres">
      <div class="kpi"><span>Estado</span><b class="${e.activo ? "" : "neg"}">${e.activo ? "Activa" : "Inactiva"}</b><em>${escDash(AMBIENTES_ECF[e.ambiente] || e.ambiente)}</em></div>
      <div class="kpi"><span>Certificado digital</span><b>${cert && cert.ok ? "Cargado" : "Falta"}</b><em>${cert && cert.ok ? "Vence " + cert.vence : (cert && cert.error ? escDash(cert.error) : "Sin certificado no se puede firmar")}</em></div>
      <div class="kpi"><span>Comprobantes</span><b>${e.conteo.aceptado || 0} aceptados</b><em>${pend} por enviar · ${malos} con error</em></div>
    </div>
    ${pasos.length ? `<div class="faltan"><h3>Lo que falta</h3><ol>${pasos.map(x => `<li>${x}</li>`).join("")}</ol></div>` : ""}
    <h3>Configuración</h3>
    <div class="form">
      <label class="check"><input type="checkbox" id="ecfActivo" ${e.activo ? "checked" : ""}/> Emitir comprobantes electrónicos en cada venta</label>
      <label>Ambiente DGII<select id="ecfAmbiente">${Object.entries(AMBIENTES_ECF).map(([k,v]) => `<option value="${k}" ${k===e.ambiente?"selected":""}>${v}</option>`).join("")}</select></label>
      <button onclick="guardarEcfConfig()">Guardar</button>
    </div>
    <h3>Certificado digital</h3>
    <div class="form">
      <label>Archivo .p12 o .pfx<input id="ecfArchivo" type="file" accept=".p12,.pfx"/></label>
      <label>Clave del certificado<input id="ecfClave" type="password" autocomplete="new-password"/></label>
      <button class="sec" onclick="subirCertificado()">Cargar certificado</button>
      <button class="sec" onclick="probarEcf()" ${cert && cert.ok ? "" : "disabled"}>Probar con la DGII</button>
    </div>
    ${cert && cert.ok ? `<p class="hint">Titular: ${escDash(((cert.titular||"").match(/CN=([^,]+)/)||[,cert.titular])[1])}</p>` : ""}
    <h3>Secuencias e-NCF autorizadas</h3>
    <div class="form"><button class="sec" onclick="nuevaSecuenciaEcf()">Registrar secuencia</button></div>
    ${tabla(["Tipo","Próximo","Hasta","Disponibles","Vence",""], e.secuencias.map(s => [
      `${s.tipo} · ${escDash(TIPOS_ECF[s.tipo] || "")}`, "E" + s.tipo + String(s.siguiente).padStart(10, "0"), s.hasta, s.disponibles, s.vencimiento || "—",
      `<button class="sec" onclick='nuevaSecuenciaEcf(${JSON.stringify(s)})'>Editar</button>`]), "Todavía no hay secuencias e-NCF.")}
    <h3>Bitácora de comprobantes</h3>
    ${tabla(["Comprobante","Factura","Total","Estado",""], (docs.items || []).map(d => [
      `<b>${escDash(d.encf)}</b><br><span class="hint">${escDash(TIPOS_ECF[d.tipo] || d.tipo)} · ${String(d.fecha||"").slice(0,16).replace("T"," ")}</span>`,
      escDash(d.factura || "—"), money(d.total),
      `<div class="estado-ecf">${pillEcf(d.estado)}${d.mensaje ? `<span class="hint">${escDash(d.mensaje).slice(0,160)}</span>` : ""}</div>`,
      `<div class="acciones"><a class="boton-link" href="/api/ecf/documentos/${d.id}/xml">XML</a>${["pendiente_envio","error_envio","rechazado"].includes(d.estado) ? `<button class="sec" onclick="reenviarEcf(${d.id})">Enviar</button>` : ""}${d.track_id ? `<button class="sec" onclick="consultarEcf(${d.id})">Consultar</button>` : ""}</div>`
    ]), "Aún no se ha emitido ningún comprobante electrónico.")}`;
}
async function guardarEcfConfig(){
  try {
    await api("/api/ecf/config", {method:"POST", body: JSON.stringify({activo: ecfActivo.checked, ambiente: ecfAmbiente.value})});
    formMsg.style.color = "var(--ok)"; formMsg.textContent = ecfActivo.checked ? "Facturación electrónica activada." : "Facturación electrónica desactivada.";
    await cargarEcf();
  } catch(e){ formMsg.style.color="var(--danger)"; formMsg.textContent = e.message; }
}
async function subirCertificado(){
  const archivo = ecfArchivo.files[0];
  if (!archivo) { formMsg.style.color="var(--danger)"; formMsg.textContent = "Elige el archivo del certificado."; return; }
  const datos = await new Promise((ok, mal) => { const r = new FileReader(); r.onload = () => ok(r.result); r.onerror = mal; r.readAsDataURL(archivo); });
  try {
    const r = await api("/api/ecf/certificado", {method:"POST", body: JSON.stringify({archivo_base64: datos, clave: ecfClave.value})});
    formMsg.style.color = "var(--ok)"; formMsg.textContent = "Certificado cargado. Vence " + r.vence + ".";
    await cargarEcf();
  } catch(e){ formMsg.style.color="var(--danger)"; formMsg.textContent = e.message; }
}
async function probarEcf(){
  try {
    const r = await api("/api/ecf/probar", {method:"POST"});
    formMsg.style.color = "var(--ok)"; formMsg.textContent = r.mensaje;
  } catch(e){ formMsg.style.color="var(--danger)"; formMsg.textContent = e.message; }
}
function nuevaSecuenciaEcf(s){
  const tipos = [31,32,34,33,44,45].map(n => `<option value="${n}" ${s && s.tipo===n ? "selected" : ""}>${n} · ${TIPOS_ECF[n]}</option>`).join("");
  const venc = s && s.vencimiento ? s.vencimiento.split("-").reverse().join("-") : "";
  abrirModal(s ? "Editar secuencia e-NCF" : "Registrar secuencia e-NCF", `<p class="hint">Usa exactamente el rango que autorizó la DGII en la Oficina Virtual.</p>
    <div class="form"><label>Tipo<select id="secTipo" ${s ? "disabled" : ""}>${tipos}</select></label><label>Desde (próximo número)<input id="secDesde" type="number" min="1" value="${s ? s.siguiente : 1}"/></label>
    <label>Hasta<input id="secHasta" type="number" min="1" value="${s ? s.hasta : ""}"/></label><label>Vencimiento<input id="secVence" type="date" value="${venc}"/></label>
    <button onclick="guardarSecuenciaEcf()">Guardar secuencia</button></div>`);
}
async function guardarSecuenciaEcf(){
  try {
    await api("/api/ecf/secuencias", {method:"POST", body: JSON.stringify({tipo: Number(secTipo.value), siguiente: Number(secDesde.value||0), hasta: Number(secHasta.value||0), vencimiento: secVence.value})});
    okModal("Secuencia guardada.");
    await cargarEcf();
  } catch(e){ errorModal(e); }
}
async function reenviarEcf(id){
  try {
    const r = await api("/api/ecf/documentos/" + id + "/enviar", {method:"POST"});
    formMsg.style.color = ["aceptado","aceptado_condicional","en_proceso"].includes(r.estado) ? "var(--ok)" : "var(--danger)";
    formMsg.textContent = "DGII: " + ((ESTADOS_ECF[r.estado] || [r.estado])[0]) + (r.mensaje ? " · " + r.mensaje : "");
    await cargarEcf();
  } catch(e){ formMsg.style.color="var(--danger)"; formMsg.textContent = e.message; }
}
async function consultarEcf(id){
  try {
    const r = await api("/api/ecf/documentos/" + id + "/consultar", {method:"POST"});
    formMsg.style.color = "var(--ok)"; formMsg.textContent = "Estado en la DGII: " + ((ESTADOS_ECF[r.estado] || [r.estado])[0]);
    await cargarEcf();
  } catch(e){ formMsg.style.color="var(--danger)"; formMsg.textContent = e.message; }
}
async function cargarVendedores(){
  const d = await api("/api/vendedores");
  window._vends = d.items || [];
  vendTabla.innerHTML = tabla(["Nombre","Documento","Facturas del mes","Ventas del mes","Estado",""], window._vends.map(v => [
    escDash(v.nombre), v.documento || "—", v.facturas_mes, money(v.ventas_mes),
    v.activo ? '<span class="pill bien">Activo</span>' : '<span class="pill">Inactivo</span>',
    `<button class="sec" onclick="editarVendedor(${v.id})">Editar</button>`
  ]), "Todavía no hay vendedores. Si solo cobra el cajero, no hace falta crear ninguno.");
}
function nuevoVendedor(v){
  window._vendId = v ? v.id : null;
  abrirModal(v ? "Editar vendedor" : "Crear vendedor", `<div class="form"><label>Nombre<input id="vendNom" value="${v ? escDash(v.nombre) : ""}"/></label><label>Documento<input id="vendDoc" value="${v ? escDash(v.documento) : ""}" placeholder="Opcional"/></label><label>Estado<select id="vendAct"><option value="1">Activo</option><option value="0" ${v && !v.activo ? "selected" : ""}>Inactivo</option></select></label><button onclick="guardarVendedor()">${v ? "Guardar cambios" : "Crear vendedor"}</button></div>`);
}
function editarVendedor(id){ nuevoVendedor((window._vends || []).find(x => x.id === id)); }
async function guardarVendedor(){
  try {
    const url = window._vendId ? "/api/vendedores/" + window._vendId : "/api/vendedores";
    await api(url, {method:"POST", body: JSON.stringify({nombre: vendNom.value, documento: vendDoc.value, activo: vendAct.value === "1"})});
    okModal(window._vendId ? "Vendedor actualizado." : "Vendedor creado.");
    await cargarVendedores();
    cargarVendedoresVenta();
  } catch(e){ errorModal(e); }
}
async function cargarCobros(){
  const q = document.getElementById("cobQ");
  const d = await api("/api/cobros" + (q && q.value.trim() ? "?q=" + encodeURIComponent(q.value.trim()) : ""));
  window._cobros = d.items || [];
  document.getElementById("cobResumen").innerHTML = `<div class="kpis tres" style="margin-top:16px">
    <div class="kpi"><span>Por cobrar</span><b>${money(d.total)}</b><em>${window._cobros.length} ${window._cobros.length === 1 ? "factura" : "facturas"}</em></div>
    <div class="kpi"><span>Vencido</span><b class="${d.vencido > 0 ? "neg" : ""}">${money(d.vencido)}</b><em>${window._cobros.filter(x => x.vencida).length} vencidas</em></div>
    <div class="kpi"><span>Clientes con deuda</span><b>${d.clientes}</b><em>&nbsp;</em></div></div>`;
  cobTabla.innerHTML = tabla(["Factura","Cliente","Fecha","Vence","Total","Abonado","Pendiente",""], window._cobros.map(f => [
    escDash(f.numero), escDash(f.cliente) + (f.telefono ? `<br><span class="hint">${escDash(f.telefono)}</span>` : ""),
    String(f.fecha||"").slice(0,10), (f.vence || "—") + (f.vencida ? ' <span class="pill mal">Vencida</span>' : ""),
    money(f.total), money(f.pagado), money(f.pendiente),
    `<div class="acciones"><button onclick="abrirAbono(${f.id})">Abonar</button></div>`
  ]), "No hay ventas fiadas pendientes.");
}
async function abrirAbono(id){
  const f = (window._cobros || []).find(x => x.id === id);
  if (!f) return;
  let saldo = 0;
  if (f.cliente_id) { try { saldo = (await api("/api/clientes/" + f.cliente_id + "/saldo")).saldo_favor || 0; } catch(e) {} }
  const metodos = (window._metodos || [{codigo:"efectivo", nombre:"Efectivo"}]).map(m => `<option value="${m.codigo}">${escDash(m.nombre)}</option>`).join("")
    + (saldo > 0 ? `<option value="saldo_favor">Saldo a favor (hasta ${money(saldo)})</option>` : "");
  abrirModal("Abono a " + f.numero, `<p class="hint">${escDash(f.cliente)} · pendiente <b>${money(f.pendiente)}</b>${f.vence ? " · vence " + f.vence : ""}</p>
    <div class="form"><label>Monto<input id="abMonto" type="number" step="0.01" min="0" value="${f.pendiente.toFixed(2)}"/></label><label>Forma de pago<select id="abMetodo">${metodos}</select></label><label class="wide">Nota (opcional)<input id="abNota" placeholder="Ej.: pagó la mitad"/></label><button onclick="registrarAbono(${id})">Registrar abono</button></div>`);
  setTimeout(() => { const m = document.getElementById("abMonto"); if (m) { m.focus(); m.select(); } }, 60);
}
async function registrarAbono(id){
  try {
    const r = await api("/api/cobros/" + id, {method:"POST", body: JSON.stringify({pagos:[{codigo: abMetodo.value, monto: Number(abMonto.value||0)}], nota: abNota.value})});
    okModal(r.mensaje + (r.cambio > 0 ? " Cambio " + money(r.cambio) + "." : ""));
    imprimirReciboAbono(r);
    await cargarCobros();
  } catch(e){ errorModal(e); }
}
function imprimirReciboAbono(r){
  const emp = window._empresa || {};
  recibo.innerHTML = `<h2>${escDash(emp.nombre || "Recibo")}</h2><p>Recibo de abono<br>${new Date().toLocaleString("es-DO")}</p><p>${escDash(r.numero)}<br>${escDash(r.cliente || "")}</p><p>Abono <b>${money(r.abonado)}</b><br>Queda ${money(Math.max(0, r.pendiente))}${r.cambio > 0 ? "<br>Cambio " + money(r.cambio) : ""}</p>`;
  window.print();
}
async function cargarClientes(){
  const d = await api("/api/clientes");
  window._clis = d.items || [];
  const nombres = nombresLista();
  cliTabla.innerHTML = tabla(["Nombre","Documento","Teléfono","Lista","Límite","Debe","Saldo a favor","Estado",""], window._clis.map(c => [
    escDash(c.nombre), c.documento || "—", c.telefono || "—", escDash(nombres[c.lista_precio] || "—"),
    c.limite_credito > 0 ? money(c.limite_credito) : "—", c.deuda > 0 ? money(c.deuda) : "—", c.saldo_favor > 0 ? money(c.saldo_favor) : "—",
    c.activo ? '<span class="pill bien">Activo</span>' : '<span class="pill">Inactivo</span>',
    `<div class="acciones"><button class="sec" onclick="editarCliente(${c.id})">Editar</button>${c.deuda > 0 ? `<button class="sec" onclick="verDeudaCliente('${escDash(c.documento || c.nombre).replaceAll("'","")}')">Ver deuda</button>` : ""}</div>`
  ]));
}
async function verDeudaCliente(texto){
  await ver("cobros");
  const q = document.getElementById("cobQ");
  if (q) { q.value = texto; await cargarCobros(); }
}
function formCliente(){
  const nombres = nombresLista();
  const listas = [1,2,3,4].map(n => `<option value="${n}">${escDash(nombres[n])}</option>`).join("");
  return `<div class="form"><label>Nombre<input id="cliNom"/></label><label>Documento<input id="cliDoc" placeholder="RNC o cédula"/></label><label>Teléfono<input id="cliTel"/></label><label>Correo<input id="cliMail"/></label>
    <label>Límite de crédito (RD$)<input id="cliLim" type="number" step="0.01" min="0" value="0" placeholder="0 = no se le fía"/></label><label>Días para pagar<input id="cliDias" type="number" min="0" max="365" value="30"/></label>
    <label>Lista de precios<select id="cliLista">${listas}</select></label><label>Estado<select id="cliAct"><option value="1">Activo</option><option value="0">Inactivo</option></select></label>
    <button id="btnCli" onclick="guardarCliente()">Crear cliente</button></div>`;
}
function nuevoCliente(){
  window._cliId = null;
  abrirModal("Crear cliente", formCliente());
}
function editarCliente(id){
  const c = (window._clis || []).find(x => x.id === id);
  if (!c) return;
  nuevoCliente();
  window._cliId = id;
  cliNom.value = c.nombre || "";
  cliDoc.value = c.documento || "";
  cliTel.value = c.telefono || "";
  cliMail.value = c.email || "";
  cliLim.value = c.limite_credito || 0;
  cliDias.value = c.dias_credito || 30;
  cliLista.value = String(c.lista_precio || 1);
  cliAct.value = c.activo ? "1" : "0";
  modalTitulo.textContent = "Editar cliente";
  btnCli.textContent = "Guardar cambios";
}
async function guardarCliente(){
  try {
    const editando = window._cliId;
    const body = {nombre: cliNom.value, documento: cliDoc.value, telefono: cliTel.value, email: cliMail.value,
      limite_credito: Number(cliLim.value||0), dias_credito: Number(cliDias.value||30), lista_precio: Number(cliLista.value||1), activo: cliAct.value === "1"};
    const url = editando ? "/api/clientes/" + editando : "/api/clientes";
    await api(url, {method:"POST", body: JSON.stringify(body)});
    window._cliId = null;
    okModal(editando ? "Cliente actualizado." : "Cliente creado.");
    await cargarClientes();
    cargarClientesVenta();
  } catch(e){ errorModal(e); }
}
function formUsuario(marcar){
  const mods = (window._modulosLista || []).map(m => `<label><input type="checkbox" class="mod" value="${m}" ${marcar ? (marcar.includes(m) ? "checked" : "") : (m==="mostrador" ? "checked" : "")}/> ${m}</label>`).join("");
  return `<div class="form"><label>Usuario<input id="usuNom"/></label><label>Contraseña<input id="usuPass" type="password" placeholder="Obligatoria al crear"/></label><label>Rol<select id="usuRol"><option>empleado</option><option>user</option><option>admin</option></select></label><div class="wide" id="usuMods">${mods}</div><button id="btnUsu" onclick="guardarUsuario()">Crear usuario</button></div>`;
}
function nuevoUsuario(){
  window._usuEdit = false;
  abrirModal("Crear usuario", formUsuario(null));
}
async function cargarUsuarios(){
  const d = await api("/api/usuarios");
  window._usus = d.items || [];
  window._modulosLista = d.modulos || [];
  usuTabla.innerHTML = tabla(["Usuario","Rol","Módulos",""], window._usus.map((u, i) => [
    escDash(u.username), u.role, escDash((u.modulos||[]).join(", ")),
    `<button class="sec" onclick="editarUsuario(${i})">Editar</button>`
  ]));
}
function editarUsuario(i){
  const u = (window._usus || [])[i];
  if (!u) return;
  abrirModal("Editar usuario", formUsuario(u.modulos || []));
  window._usuEdit = true;
  usuNom.value = u.username;
  usuNom.readOnly = true;
  usuPass.value = "";
  usuPass.placeholder = "Vacía para no cambiarla";
  usuRol.value = u.role;
  btnUsu.textContent = "Guardar cambios";
}
async function guardarUsuario(){
  const modulos = [...document.querySelectorAll("#modal .mod:checked")].map(x => x.value);
  try {
    const editando = window._usuEdit;
    const url = editando ? "/api/usuarios/editar" : "/api/usuarios";
    await api(url, {method:"POST", body: JSON.stringify({username: usuNom.value, password: usuPass.value, role: usuRol.value, modulos})});
    window._usuEdit = false;
    okModal(editando ? "Usuario actualizado." : "Usuario creado con sus módulos.");
    await cargarUsuarios();
  } catch(e){ errorModal(e); }
}
async function cargarSeguimiento(){
  const d = await api("/api/seguimiento");
  const cajeros = tabla(["Cajero","Facturas","Generado"], d.cajeros.map(c => [c.usuario, c.facturas, money(c.total)]));
  window._turnos = d.turnos || [];
  const turnos = tabla(["Turno","Cajero","Estado","Diferencia","Seguimiento","Nota",""], window._turnos.map(t => [
    t.id, t.usuario || "—", t.estado, money(t.diferencia), t.seguimiento, escDash(t.nota || t.observaciones || "—"),
    `<button class="sec" onclick="editarSeg(${t.id})">Editar</button>`
  ]));
  formBody.innerHTML = "<h3>Lo que generó cada cajero en 30 días</h3>" + cajeros +
    "<h3>Turnos y descuadre</h3>" + turnos;
}
function editarSeg(id){
  const t = (window._turnos || []).find(x => x.id === id);
  if (!t) return;
  abrirModal("Seguimiento del turno", `<div class="form"><label>Turno<input id="segId" type="number" readonly/></label><label>Estado<select id="segEst"><option>pendiente</option><option>en_seguimiento</option><option>resuelto</option></select></label><label class="wide">Nota<input id="segNota"/></label><button onclick="guardarSeguimiento()">Guardar seguimiento</button></div>`);
  segId.value = t.id;
  if ([...segEst.options].some(o => o.value === t.seguimiento)) segEst.value = t.seguimiento;
  segNota.value = t.nota || t.observaciones || "";
}
async function guardarSeguimiento(){
  try {
    await api("/api/seguimiento", {method:"POST", body: JSON.stringify({cierre_id:Number(segId.value), estado:segEst.value, nota:segNota.value})});
    okModal("Seguimiento guardado.");
    await cargarSeguimiento();
  } catch(e){ errorModal(e); }
}
async function cargarCompras(){
  const d = await api("/api/compras/lista");
  window._provs = d.proveedores || [];
  window._bods = d.bodegas || ["Principal"];
  const extra = document.getElementById("compLista");
  if (!extra) {
    formBody.insertAdjacentHTML("beforeend", '<div id="compLista"></div>');
  }
  window._compras = d.items || [];
  document.getElementById("compLista").innerHTML = "<h3>Compras recientes</h3>" + tabla(["Fecha","Proveedor","Total","Nota",""], window._compras.map(c => [
    String(c.fecha||"").slice(0,16), escDash(c.proveedor), money(c.total), escDash(c.nota || "—"),
    `<button class="sec" onclick="verCompra(${c.id})">Ver</button> <button class="sec" onclick="editarCompra(${c.id})">Editar</button>`
  ]), "Todavía no hay compras.") + `<div id="compVista"></div>`;
}
async function verCompra(id){
  const d = await api("/api/compras/" + id + "/lineas");
  const vista = document.getElementById("compVista");
  if (vista) vista.innerHTML = tabla(["Producto","Cantidad","Costo","Bodega"], (d.lineas||[]).map(l => [escDash(l.nombre), l.cantidad, money(l.costo), escDash(l.bodega)]));
}
function editarCompra(id){
  const c = (window._compras || []).find(x => x.id === id);
  if (!c) return;
  abrirModal("Nota de la compra", `<div class="form"><label class="wide">Nota<input id="compNota"/></label><button onclick="guardarNotaCompra(${id})">Guardar cambios</button></div>`);
  compNota.value = c.nota || "";
}
async function guardarNotaCompra(id){
  try {
    await api("/api/compras/" + id, {method:"POST", body: JSON.stringify({nota: compNota.value})});
    okModal("Nota actualizada.");
    await cargarCompras();
  } catch(e){ errorModal(e); }
}
function opcionesBodega(nombres, actual){
  const lista = nombres && nombres.length ? nombres : ["Principal"];
  return lista.map(b => `<option ${b===actual?"selected":""}>${b}</option>`).join("");
}
function nuevaBodega(){
  abrirModal("Crear bodega", `<div class="form"><label class="wide">Nombre<input id="bodNueva"/></label><button onclick="crearBodega()">Crear bodega</button></div>`);
}
async function cargarBodegas(){
  const d = await api("/api/bodegas");
  window._bods = d.bodegas || ["Principal"];
  const extra = document.getElementById("bodLista");
  if (!extra) formBody.insertAdjacentHTML("beforeend", '<div id="bodLista"></div>');
  window._trs = d.transferencias || [];
  document.getElementById("bodLista").innerHTML = tabla(["Producto","Bodega","Cantidad"], d.items.map(x => [escDash(x.producto), escDash(x.bodega), x.cantidad])) +
    "<h3>Transferencias recientes</h3>" + tabla(["Fecha","Producto","Origen","Destino","Cantidad",""], window._trs.map(t => [
      String(t.fecha||"").slice(0,16), escDash(t.producto), escDash(t.origen), escDash(t.destino), t.cantidad,
      `<button class="sec" onclick="editarTransfer(${t.id})">Editar</button>`
    ]), "Todavía no hay transferencias.");
}
function editarTransfer(id){
  const t = (window._trs || []).find(x => x.id === id);
  if (!t) return;
  nuevaTransfer();
  trProd.value = String(t.producto_id);
  trOri.value = t.origen;
  trDes.value = t.destino;
  trCant.value = t.cantidad;
  modalTitulo.textContent = "Repetir transferencia";
  const m = document.getElementById("modalMsg");
  if (m) { m.style.color = "var(--text)"; m.textContent = "Al guardar se registra un movimiento nuevo."; }
}
async function crearBodega(){
  try {
    await api("/api/bodegas", {method:"POST", body: JSON.stringify({nombre: bodNueva.value})});
    okModal("Bodega creada.");
    await cargarBodegas();
  } catch(e){ errorModal(e); }
}
async function cargarPantallaMetodos(){
  const d = await api("/api/metodos");
  window._mets = d.items || [];
  metTabla.innerHTML = tabla(["Código","Nombre","Afecta caja","Activo",""], window._mets.map((m, i) => [
    escDash(m.codigo), escDash(m.nombre), m.afecta_caja ? "Sí" : "No", m.activo ? "Sí" : "No",
    `<button class="sec" onclick="editarMetodo(${i})">Editar</button>`
  ]));
}
function formMetodo(){
  return `<div class="form"><label>Código<input id="metCod"/></label><label>Nombre<input id="metNom"/></label><label>Afecta caja<select id="metCaja"><option value="0">No</option><option value="1">Sí</option></select></label><label>Activo<select id="metActivo"><option value="1">Sí</option><option value="0">No</option></select></label><button onclick="guardarMetodo()">Guardar</button></div>`;
}
function nuevoMetodo(){ abrirModal("Nuevo método", formMetodo()); }
function editarMetodo(i){
  const m = (window._mets || [])[i];
  if (!m) return;
  nuevoMetodo();
  modalTitulo.textContent = "Editar método";
  metCod.value = m.codigo;
  metNom.value = m.nombre;
  metCaja.value = m.afecta_caja ? "1" : "0";
  metActivo.value = m.activo ? "1" : "0";
}
async function guardarMetodo(){
  try {
    await api("/api/metodos", {method:"POST", body: JSON.stringify({codigo: metCod.value, nombre: metNom.value, afecta_caja: metCaja.value==="1", activo: metActivo.value==="1"})});
    okModal("Método guardado.");
    await cargarPantallaMetodos();
    await cargarMetodos();
  } catch(e){ errorModal(e); }
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
  empItbisIncl.checked = !!d.precios_incluyen_itbis;
}
async function guardarEmpresa(){
  try {
    await api("/api/empresa", {method:"POST", body: JSON.stringify({
      nombre: empNom.value, direccion: empDir.value, rnc: empRnc.value,
      smtp_host: smtpHost.value, smtp_port: smtpPort.value, smtp_user: smtpUser.value,
      smtp_password: smtpPass.value, smtp_from: smtpFrom.value,
      precios_incluyen_itbis: empItbisIncl.checked
    })});
    window._itbisIncluido = empItbisIncl.checked;
    pintar();
    formMsg.style.color = "var(--ok)";
    formMsg.textContent = "Datos de empresa guardados.";
  } catch(e){ formMsg.style.color="var(--danger)"; formMsg.textContent = e.message; }
}
function repintarPlaca(data){
  const capa = document.getElementById("placaCobrado");
  if (!capa) return;
  const cambioVenta = Number(data.cambio || 0);
  const fiado = Number(data.pendiente || 0) > 0.004;
  const grande = fiado ? money(data.pendiente) : (cambioVenta > 0.004 ? money(cambioVenta) : money(data.total));
  capa.querySelector("small").textContent = fiado
    ? "Fiado · queda pendiente, vence " + (data.vencimiento || "")
    : (cambioVenta > 0.004 ? "Cambio a devolver · cobrado " + money(data.total) : "Cobrado exacto · " + data.numero);
  capa.querySelector("b").textContent = grande;
  capa.querySelector("b").classList.toggle("largo", grande.length > 12);
  capa.getAnimations().forEach(a => a.cancel());
  if (reducido()) {
    capa.animate([{clipPath:"inset(0 0 0 0)", opacity:0}, {clipPath:"inset(0 0 0 0)", opacity:1, offset:.15}, {clipPath:"inset(0 0 0 0)", opacity:1, offset:.85}, {clipPath:"inset(0 0 0 0)", opacity:0}], {duration:2600});
    return;
  }
  capa.animate([
    {clipPath:"inset(0 100% 0 0)"},
    {clipPath:"inset(0 0 0 0)", offset:.16, easing:"ease"},
    {clipPath:"inset(0 0 0 0)", offset:.86, easing:"cubic-bezier(0.77, 0, 0.175, 1)"},
    {clipPath:"inset(0 0 0 100%)"}
  ], {duration:2600, easing:"cubic-bezier(0.77, 0, 0.175, 1)"});
}
async function cobrar(){
  msg.textContent = "";
  const boton = document.getElementById("btnCobrar");
  boton.classList.add("cobrando");
  boton.disabled = true;
  try {
    const nivel = Number(lista.value);
    const nombres = nombresLista();
    const payload = {
      items: cart.map(l=>({id:l.id, cantidad:l.cantidad, nivel, combo:!!l.combo})),
      pagos: pagosActuales().filter(p => p.monto>0).map(p => ({codigo:p.codigo, monto:p.monto})),
      lista: nombres[nivel], descuento: Number(desc.value||0),
      cliente_id: cliVenta.value ? Number(cliVenta.value) : null,
      comprobante: tipoComp.value,
      a_credito: document.getElementById("aCredito").checked,
      vendedor_id: vendVenta.value ? Number(vendVenta.value) : null,
      observaciones: obsVenta.value,
      terminal_id: termVenta.value ? Number(termVenta.value) : null,
      id_local: idLocal()
    };
    let data;
    try {
      data = await api("/api/vender", {method:"POST", body: JSON.stringify(payload)});
    } catch(e) {
      if (!(e instanceof TypeError)) throw e;
      data = guardarVentaSinConexion(payload);
    }
    boton.classList.remove("cobrando");
    imprimirRecibo(data);
    repintarPlaca(data);
    cart.length = 0; desc.value = 0;
    document.getElementById("aCredito").checked = false;
    obsVenta.value = "";
    pintar();
    msg.style.color = "var(--ok)";
    msg.textContent = data.provisional
      ? `Sin conexión: la venta quedó guardada en este equipo (${money(data.total)}, cambio ${money(data.cambio)}). Se factura al volver la conexión.`
      : data.pendiente > 0
      ? `${data.numero} fiado a ${data.cliente}: queda ${money(data.pendiente)}, vence ${data.vencimiento}.`
      : data.numero + (data.ncf? " · "+data.ncf : "") + " · " + money(data.total) + " · cambio " + money(data.cambio);
    if (data.provisional) return;
    await cargar();
    if (cliVenta.value) actualizarClienteVenta();
  } catch(e){
    boton.classList.remove("cobrando");
    pintar();
    msg.style.color="var(--danger)"; msg.textContent = e.message;
  }
}
q.addEventListener("keydown", ev=>{ if(ev.key==="Enter") { clearTimeout(window._qT); cargar(); } });
q.addEventListener("input", ()=>{ clearTimeout(window._qT); window._qT = setTimeout(cargar, 250); });
async function imprimirRecibo(data){
  const emp = window._empresa || {};
  let bloqueEcf = "";
  if (data.ecf && data.ecf.encf) {
    let qr = "";
    if (data.ecf.codigo_seguridad && data.id) {
      try { qr = await (await fetch("/api/ecf/qr/" + data.id)).text(); } catch(e) { qr = ""; }
    }
    bloqueEcf = `<p><b>e-NCF ${escDash(data.ecf.encf)}</b>${data.ecf.codigo_seguridad ? `<br>Código de seguridad: ${escDash(data.ecf.codigo_seguridad)}<br>Fecha de firma: ${escDash(data.ecf.fecha_firma)}` : "<br>Comprobante electrónico pendiente de firma"}</p>${qr ? `<div class="qr">${qr}</div>` : ""}`;
  }
  const ancho = (document.getElementById("anchoTicket") || {}).value || "80";
  recibo.style.width = ancho === "58" ? "58mm" : "80mm";
  const lineas = (data.lineas||[]).map(l => `<tr><td>${l.cantidad} ${l.nombre}</td><td>${money(l.total)}</td></tr>`).join("");
  if (data.provisional) bloqueEcf = `<p><b>Venta sin conexión</b><br>Ref. ${escDash(data.numero)}<br>El comprobante fiscal se emite al sincronizar.</p>` + bloqueEcf;
  recibo.innerHTML = `<h2>${emp.nombre || "Factura"}</h2><p>${emp.direccion || ""}<br>RNC ${emp.rnc || "—"}</p><p>${data.numero}<br>${data.ncf || ""}<br>${data.cliente || ""}</p><table>${lineas}</table><p>ITBIS ${money(data.itbis)}<br><b>Total ${money(data.total)}</b><br>Cambio ${money(data.cambio||0)}${Number(data.pendiente||0) > 0 ? `<br><b>Pendiente ${money(data.pendiente)}</b><br>Vence ${data.vencimiento || ""}` : ""}</p>${bloqueEcf}`;
  window.print();
}
async function cargarMetodos(){
  const d = await api("/api/metodos");
  const activos = d.items.filter(m => m.activo);
  window._metodos = activos;
  pagosDyn.innerHTML = activos.map(m => `<label>${m.nombre}<input class="pago" data-codigo="${m.codigo}" data-caja="${m.afecta_caja ? 1 : 0}" type="number" step="0.01" min="0" value="0"/></label>`).join("")
    + `<label id="pagoSaldoLbl" hidden><span>Saldo a favor</span><input class="pago" data-codigo="saldo_favor" data-caja="0" type="number" step="0.01" min="0" value="0"/></label>`;
  document.querySelectorAll(".pago").forEach(el => el.oninput = () => { el.dataset.auto = "0"; pintar(); });
  pintar();
}
function popupCliente(){
  abrirModal("Agregar cliente", `<div class="form"><label>Nombre<input id="cliNuevo" placeholder="Alta rápida"/></label><label>Documento<input id="cliNuevoDoc" placeholder="RNC o cédula"/></label><label>Correo<input id="cliNuevoMail" placeholder="cliente@correo.com"/></label><button onclick="crearClienteTicket()">Agregar</button></div>`);
}
function popupApartar(){
  if(!cart.length){ msg.style.color="var(--danger)"; msg.textContent = "El ticket está vacío."; return; }
  abrirModal("Apartar ticket", `<div class="form"><label class="wide">Nombre<input id="esperaNom" placeholder="Mesa 1"/></label><button onclick="apartar()">Apartar</button></div>`);
}
async function crearClienteTicket(){
  try {
    const r = await api("/api/clientes/rapido", {method:"POST", body: JSON.stringify({nombre: cliNuevo.value, documento: cliNuevoDoc.value, telefono: "", email: cliNuevoMail.value})});
    await cargarClientesVenta();
    cliVenta.value = String(r.id);
    cerrarModal();
    msg.style.color = "var(--ok)";
    msg.textContent = "Cliente agregado al ticket.";
  } catch(e){ errorModal(e); }
}
async function apartar(){
  if(!cart.length) return;
  const etiqueta = (esperaNom.value || "").trim();
  if(!etiqueta){ errorModal({message:"Escribe un nombre para el apartado."}); return; }
  try {
    await api("/api/espera", {method:"POST", body: JSON.stringify({
      etiqueta, items: cart, total: Number(String(tot.textContent).replace(/[^0-9.]/g,"")) || 0,
      cliente_id: cliVenta.value ? Number(cliVenta.value) : null
    })});
    cart.length = 0; pintar();
    cerrarModal();
    msg.style.color = "var(--ok)";
    msg.textContent = "Ticket apartado: " + etiqueta;
    await cargarEsperas();
  } catch(e){ errorModal(e); }
}
async function cargarEsperas(){
  try {
    const d = await api("/api/espera");
    esperas.innerHTML = d.items.length ? "<h4>Apartados</h4>" + d.items.map(x => `<button class="sec" onclick="tomarEspera(${x.id})">${x.etiqueta} · ${money(x.total)}</button>`).join("") : "";
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
    const nombres = nombresLista();
    const data = await api("/api/cotizaciones", {method:"POST", body: JSON.stringify({
      items: cart.map(l=>({id:l.id, cantidad:l.cantidad, nivel, combo:!!l.combo})),
      lista: nombres[nivel], cliente_id: cliVenta.value ? Number(cliVenta.value) : null
    })});
    cart.length = 0; pintar();
    msg.style.color = "var(--ok)";
    msg.textContent = "Cotización " + data.numero;
  } catch(e){ msg.style.color="var(--danger)"; msg.textContent = e.message; }
}
</script>
</body>
</html>
"""
