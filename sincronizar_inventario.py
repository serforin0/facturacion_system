"""Sincroniza productos y ventas entre el escritorio y la web.

No borra nada. Agrega el producto que falta en un lado. El stock que ya
está en el conteo no se vuelve a rebajar: solo una venta posterior al
primer cruce descuenta en el otro equipo. Si después de eso el stock
sigue distinto, hay que elegir el valor real.
"""
from __future__ import annotations

import base64
import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime
from http.cookiejar import CookieJar

from cuenta_servidor import ServidorInalcanzable, url_servidor

CLAVE_CODIGO = """
TRIM(IFNULL(NULLIF(TRIM(codigo_producto), ''), IFNULL(codigo_barras, '')))
""".strip()


def _ahora() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _momento(valor: str) -> str:
    texto = (valor or "").strip().replace("T", " ")
    return texto[:19]


def _codigo_archivo(codigo: str) -> str:
    limpio = re.sub(r"[^A-Za-z0-9._-]", "_", codigo.strip())
    return limpio or "producto"


def preparar(cur) -> None:
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS sync_estado (
            clave TEXT PRIMARY KEY,
            valor TEXT
        )
        """
    )


def _corte(cur) -> str:
    cur.execute("SELECT valor FROM sync_estado WHERE clave='corte'")
    fila = cur.fetchone()
    return (fila[0] or "").strip() if fila else ""


def _guardar_corte(cur, momento: str) -> None:
    cur.execute(
        """
        INSERT INTO sync_estado (clave, valor) VALUES ('corte', ?)
        ON CONFLICT(clave) DO UPDATE SET valor=excluded.valor
        """,
        (momento,),
    )


def leer_productos(cur, base_dir: str = "", con_imagen: bool = False) -> list[dict]:
    cur.execute(
        f"""
        SELECT {CLAVE_CODIGO}, p.nombre, IFNULL(p.precio,0), IFNULL(p.precio_base,0),
               IFNULL(p.stock,0), IFNULL(p.unidad_medida,''),
               IFNULL(NULLIF(TRIM(p.bodega_codigo),''), 'Principal'),
               IFNULL(c.nombre,''), IFNULL(p.imagen_path,'')
        FROM productos p
        LEFT JOIN categorias c ON c.id = p.categoria_id
        WHERE {CLAVE_CODIGO} != ''
        ORDER BY p.id
        """
    )
    vistos: set[str] = set()
    salida = []
    for codigo, nombre, precio, costo, stock, unidad, bodega, categoria, imagen in cur.fetchall():
        codigo = (codigo or "").strip()
        if not codigo or codigo in vistos or codigo.upper().startswith("DEMO-"):
            continue
        vistos.add(codigo)
        item = {
            "codigo": codigo,
            "nombre": nombre or codigo,
            "precio": float(precio or 0),
            "precio_base": float(costo or 0),
            "stock": float(stock or 0),
            "unidad": unidad or "",
            "bodega": bodega or "Principal",
            "categoria": categoria or "",
        }
        if con_imagen:
            item["imagen"] = _leer_imagen(base_dir, imagen)
        salida.append(item)
    return salida


def responder(conn, base_dir: str, cuerpo: dict, usuario: str) -> dict:
    resumen = incorporar(
        conn,
        base_dir,
        cuerpo.get("productos") or [],
        cuerpo.get("ventas") or [],
        cuerpo.get("stocks") or [],
        usuario or "sync",
    )
    cur = conn.cursor()
    conocidos = {(codigo or "").strip() for codigo in (cuerpo.get("codigos") or [])}
    numeros = {(numero or "").strip() for numero in (cuerpo.get("numeros") or [])}
    productos = leer_productos(cur, base_dir, con_imagen=False)
    for producto in productos:
        if producto["codigo"] in conocidos:
            continue
        cur.execute(
            f"""
            SELECT IFNULL(imagen_path,'') FROM productos
            WHERE {CLAVE_CODIGO} = ? ORDER BY id LIMIT 1
            """,
            (producto["codigo"],),
        )
        fila = cur.fetchone()
        producto["imagen"] = _leer_imagen(base_dir, fila[0] if fila else "")
    ventas = [venta for venta in leer_ventas(cur) if venta["numero"] not in numeros]
    return {"resumen": resumen, "productos": productos, "ventas": ventas}


def leer_ventas(cur) -> list[dict]:
    cur.execute(
        """
        SELECT f.numero, IFNULL(f.fecha,''), IFNULL(f.subtotal,0), IFNULL(f.impuesto_total,0),
               IFNULL(f.total,0), IFNULL(f.usuario,''),
               TRIM(IFNULL(NULLIF(TRIM(p.codigo_producto), ''), IFNULL(p.codigo_barras, ''))),
               d.descripcion, IFNULL(d.cantidad,0), IFNULL(d.precio_unitario,0), IFNULL(d.total_linea,0)
        FROM facturas f
        JOIN factura_detalle d ON d.factura_id = f.id
        LEFT JOIN productos p ON p.id = d.producto_id
        WHERE f.estado = 'emitida' AND TRIM(IFNULL(f.numero,'')) != ''
        ORDER BY f.fecha, f.id, d.id
        """
    )
    grupos: dict[str, dict] = {}
    orden: list[str] = []
    for numero, fecha, subtotal, itbis, total, usuario, codigo, nombre, cantidad, precio, linea in cur.fetchall():
        numero = (numero or "").strip()
        if not numero or numero.upper().startswith("DEMO-") or numero.upper().startswith("P-"):
            continue
        if numero not in grupos:
            grupos[numero] = {
                "numero": numero,
                "fecha": _momento(fecha),
                "subtotal": float(subtotal or 0),
                "itbis": float(itbis or 0),
                "total": float(total or 0),
                "usuario": usuario or "",
                "lineas": [],
            }
            orden.append(numero)
        grupos[numero]["lineas"].append(
            {
                "codigo": (codigo or "").strip(),
                "nombre": nombre or "",
                "cantidad": float(cantidad or 0),
                "precio": float(precio or 0),
                "total": float(linea or 0),
            }
        )
    return [grupos[numero] for numero in orden if not _solo_demostracion(grupos[numero])]


def _solo_demostracion(venta: dict) -> bool:
    lineas = venta.get("lineas") or []
    if not lineas:
        return True
    for linea in lineas:
        codigo = (linea.get("codigo") or "").strip().upper()
        if codigo and not codigo.startswith("DEMO-"):
            return False
    return True


def _leer_imagen(base_dir: str, ruta: str) -> str:
    if not ruta:
        return ""
    camino = ruta if os.path.isabs(ruta) else os.path.join(base_dir, ruta)
    if not os.path.isfile(camino):
        return ""
    if os.path.getsize(camino) > 500_000:
        return ""
    with open(camino, "rb") as archivo:
        return base64.b64encode(archivo.read()).decode("ascii")


def _guardar_imagen(base_dir: str, codigo: str, imagen: str) -> str:
    if not imagen or not base_dir:
        return ""
    try:
        crudo = base64.b64decode(imagen, validate=True)
    except (ValueError, TypeError):
        return ""
    if len(crudo) < 500 or len(crudo) > 500_000:
        return ""
    carpeta = os.path.join(base_dir, "web_static", "barra")
    os.makedirs(carpeta, exist_ok=True)
    nombre = _codigo_archivo(codigo) + ".jpg"
    with open(os.path.join(carpeta, nombre), "wb") as archivo:
        archivo.write(crudo)
    return f"web_static/barra/{nombre}"


def _id_categoria(cur, nombre: str):
    nombre = (nombre or "").strip()
    if not nombre:
        return None
    cur.execute("SELECT id FROM categorias WHERE nombre=?", (nombre,))
    fila = cur.fetchone()
    if fila:
        return fila[0]
    cur.execute("INSERT INTO categorias (nombre) VALUES (?)", (nombre,))
    return cur.lastrowid


def _id_por_codigo(cur, codigo: str):
    cur.execute(
        f"""
        SELECT id, IFNULL(NULLIF(TRIM(bodega_codigo),''), 'Principal'), IFNULL(stock,0)
        FROM productos
        WHERE {CLAVE_CODIGO} = ?
        ORDER BY id LIMIT 1
        """,
        (codigo,),
    )
    return cur.fetchone()


def _asegurar_bodega(cur, producto_id: int, bodega: str, cantidad: float) -> None:
    bodega = (bodega or "Principal").strip() or "Principal"
    try:
        cur.execute("INSERT OR IGNORE INTO bodegas (codigo) VALUES (?)", (bodega,))
    except Exception:
        pass
    cur.execute(
        "SELECT 1 FROM stock_bodega WHERE producto_id=? AND bodega=?",
        (producto_id, bodega),
    )
    if cur.fetchone():
        cur.execute(
            "UPDATE stock_bodega SET cantidad=? WHERE producto_id=? AND bodega=?",
            (cantidad, producto_id, bodega),
        )
    else:
        cur.execute(
            "INSERT INTO stock_bodega (producto_id, bodega, cantidad) VALUES (?,?,?)",
            (producto_id, bodega, cantidad),
        )


def _kardex(cur, producto_id: int, tipo: str, cantidad: float, referencia: str, factura_id, usuario: str, precio: float, texto: str) -> None:
    cur.execute(
        """
        INSERT INTO movimientos_inventario (
            producto_id, tipo_movimiento, cantidad, referencia, factura_id, usuario, descripcion_mov
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (producto_id, tipo, float(cantidad), referencia, factura_id, usuario, texto),
    )


def _agregar_producto(cur, base_dir: str, producto: dict) -> bool:
    codigo = (producto.get("codigo") or "").strip()
    if not codigo or _id_por_codigo(cur, codigo):
        return False
    categoria = _id_categoria(cur, producto.get("categoria") or "")
    imagen = _guardar_imagen(base_dir, codigo, producto.get("imagen") or "")
    bodega = (producto.get("bodega") or "Principal").strip() or "Principal"
    stock = float(producto.get("stock") or 0)
    cur.execute(
        """
        INSERT INTO productos (
            nombre, descripcion, precio, precio_base, precio_minimo, stock,
            categoria_id, stock_minimo, imagen_path, codigo_barras, activo,
            codigo_producto, unidad_medida, bodega_codigo, aplica_itbis, facturar_sin_stock
        ) VALUES (?, ?, ?, ?, 0, ?, ?, 1, ?, ?, 1, ?, ?, ?, 1, 1)
        """,
        (
            (producto.get("nombre") or codigo).strip(),
            "Llegó por sincronización. No se borró ningún producto existente.",
            float(producto.get("precio") or 0),
            float(producto.get("precio_base") or 0),
            stock,
            categoria,
            imagen or None,
            codigo,
            codigo,
            (producto.get("unidad") or "").strip(),
            bodega,
        ),
    )
    _asegurar_bodega(cur, int(cur.lastrowid), bodega, stock)
    return True


def _venta_existe(cur, numero: str) -> bool:
    cur.execute("SELECT 1 FROM facturas WHERE numero=?", (numero,))
    return cur.fetchone() is not None


def _aplicar_venta(cur, venta: dict, descontar: bool, recien: set[str], usuario_sync: str) -> tuple[bool, int]:
    numero = (venta.get("numero") or "").strip()
    if not numero or numero.upper().startswith("DEMO-") or _venta_existe(cur, numero):
        return False, 0
    cur.execute(
        """
        INSERT INTO facturas (
            numero, fecha, tipo_comprobante, subtotal, descuento_total, impuesto_total,
            total, estado, usuario, observaciones
        ) VALUES (?, ?, 'consumidor_final', ?, 0, ?, ?, 'emitida', ?, ?)
        """,
        (
            numero,
            _momento(venta.get("fecha") or "") or _ahora(),
            float(venta.get("subtotal") or 0),
            float(venta.get("itbis") or 0),
            float(venta.get("total") or 0),
            (venta.get("usuario") or usuario_sync or "sync").strip(),
            "Sincronizada desde el otro equipo. No entra otra vez a la caja.",
        ),
    )
    factura_id = int(cur.lastrowid)
    descontadas = 0
    for linea in venta.get("lineas") or []:
        codigo = (linea.get("codigo") or "").strip()
        nombre = (linea.get("nombre") or codigo or "Producto").strip()
        cantidad = float(linea.get("cantidad") or 0)
        precio = float(linea.get("precio") or 0)
        total = float(linea.get("total") or 0)
        hallado = _id_por_codigo(cur, codigo) if codigo else None
        producto_id = int(hallado[0]) if hallado else None
        cur.execute(
            """
            INSERT INTO factura_detalle (
                factura_id, producto_id, descripcion, cantidad, precio_unitario,
                descuento_item, impuesto_item, total_linea
            ) VALUES (?, ?, ?, ?, ?, 0, 0, ?)
            """,
            (factura_id, producto_id, nombre, cantidad, precio, total),
        )
        if descontar and producto_id and codigo not in recien and cantidad:
            cur.execute(
                "UPDATE productos SET stock = IFNULL(stock,0) - ? WHERE id=?",
                (cantidad, producto_id),
            )
            cur.execute("SELECT IFNULL(stock,0) FROM productos WHERE id=?", (producto_id,))
            queda = float(cur.fetchone()[0] or 0)
            _asegurar_bodega(cur, producto_id, hallado[1], queda)
            _kardex(
                cur, producto_id, "venta", -cantidad, numero, factura_id, usuario_sync, precio,
                f"Venta sincronizada {numero}",
            )
            descontadas += 1
    return True, descontadas


def fijar_stock(cur, codigo: str, stock: float, usuario: str) -> bool:
    hallado = _id_por_codigo(cur, codigo)
    if not hallado:
        return False
    producto_id, bodega, anterior = hallado
    nuevo = float(stock)
    cur.execute("UPDATE productos SET stock=? WHERE id=?", (nuevo, producto_id))
    _asegurar_bodega(cur, int(producto_id), bodega, nuevo)
    delta = nuevo - float(anterior or 0)
    if abs(delta) > 0.0001:
        _kardex(
            cur, int(producto_id), "ajuste", delta, "sync-stock", None, usuario, 0,
            "Stock real elegido al sincronizar",
        )
    return True


def incorporar(conn, base_dir: str, productos: list[dict], ventas: list[dict], stocks: list[dict] | None = None, usuario: str = "sync") -> dict:
    cur = conn.cursor()
    preparar(cur)
    corte = _corte(cur)
    recien: set[str] = set()
    nuevos = 0
    for producto in productos or []:
        if _agregar_producto(cur, base_dir, producto):
            nuevos += 1
            recien.add((producto.get("codigo") or "").strip())
    ventas_nuevas = 0
    lineas_descontadas = 0
    for venta in ventas or []:
        fecha = _momento(venta.get("fecha") or "")
        descontar = bool(corte) and bool(fecha) and fecha > corte
        creo, descontadas = _aplicar_venta(cur, venta, descontar, recien, usuario)
        if creo:
            ventas_nuevas += 1
            lineas_descontadas += descontadas
    if not corte:
        _guardar_corte(cur, _ahora())
    fijados = 0
    for item in stocks or []:
        codigo = (item.get("codigo") or "").strip()
        if codigo and fijar_stock(cur, codigo, float(item.get("stock") or 0), usuario):
            fijados += 1
    conn.commit()
    return {
        "productos_nuevos": nuevos,
        "ventas_nuevas": ventas_nuevas,
        "ventas_descontadas": lineas_descontadas,
        "stocks": fijados,
    }


def paquete(conn, base_dir: str, con_imagen: bool) -> dict:
    cur = conn.cursor()
    preparar(cur)
    conn.commit()
    return {
        "productos": leer_productos(cur, base_dir, con_imagen),
        "ventas": leer_ventas(cur),
    }


def diferencias(local: dict[str, float], remoto: dict[str, float], nombres: dict[str, str]) -> list[dict]:
    cruce = []
    for codigo in sorted(set(local) & set(remoto)):
        if abs(float(local[codigo]) - float(remoto[codigo])) <= 0.001:
            continue
        cruce.append(
            {
                "codigo": codigo,
                "nombre": nombres.get(codigo) or codigo,
                "escritorio": float(local[codigo]),
                "web": float(remoto[codigo]),
            }
        )
    return cruce


def _post(opener, url: str, cuerpo: dict) -> dict:
    data = json.dumps(cuerpo).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with opener.open(req, timeout=180) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detalle = exc.read().decode("utf-8", errors="replace")[:240]
        if exc.code in (401, 403):
            raise ServidorInalcanzable("La web no aceptó el usuario o no tiene permiso de inventario.") from exc
        if exc.code == 404:
            raise ServidorInalcanzable("La web todavía no tiene la sincronización. Hay que publicar esta versión.") from exc
        raise ServidorInalcanzable(f"La web respondió {exc.code}. {detalle}") from exc
    except urllib.error.URLError as exc:
        raise ServidorInalcanzable("No se pudo conectar con la web.") from exc
    except (TimeoutError, json.JSONDecodeError) as exc:
        raise ServidorInalcanzable("La web no devolvió la sincronización.") from exc


def sincronizar(db, usuario: str, clave: str, acuerdos: list[dict] | None = None, base_dir: str | None = None) -> dict:
    """Empuja y trae. `acuerdos` fija el stock real en los dos lados."""
    base = base_dir or os.path.dirname(os.path.abspath(__file__))
    destino = url_servidor(db).rstrip("/")
    jar = CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    login = json.dumps({"username": usuario, "password": clave}).encode("utf-8")
    pedido = urllib.request.Request(
        destino + "/api/login",
        data=login,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with opener.open(pedido, timeout=20) as resp:
            resp.read()
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise ServidorInalcanzable("Usuario o contraseña rechazados por la web.") from exc
        raise ServidorInalcanzable(f"No se pudo entrar a la web ({exc.code}).") from exc
    except urllib.error.URLError as exc:
        raise ServidorInalcanzable("No se pudo conectar con la web.") from exc

    conn = db.get_connection()
    local = paquete(conn, base, con_imagen=not acuerdos)
    envio = {
        "productos": local["productos"],
        "ventas": local["ventas"],
        "codigos": [p["codigo"] for p in local["productos"]],
        "numeros": [v["numero"] for v in local["ventas"]],
        "stocks": acuerdos or [],
    }
    remoto = _post(opener, destino + "/api/sync/inventario", envio)
    traidos = incorporar(
        conn,
        base,
        remoto.get("productos") or [],
        remoto.get("ventas") or [],
        acuerdos or [],
        usuario or "sync",
    )
    cur = conn.cursor()
    propios = {p["codigo"]: p["stock"] for p in leer_productos(cur)}
    nombres = {p["codigo"]: p["nombre"] for p in leer_productos(cur)}
    ajenos = {}
    for producto in remoto.get("productos") or []:
        codigo = (producto.get("codigo") or "").strip()
        if not codigo:
            continue
        ajenos[codigo] = float(producto.get("stock") or 0)
        nombres.setdefault(codigo, producto.get("nombre") or codigo)
    for item in acuerdos or []:
        codigo = (item.get("codigo") or "").strip()
        if codigo:
            ajenos[codigo] = float(item.get("stock") or 0)
            propios[codigo] = float(item.get("stock") or 0)
    conn.close()
    web = remoto.get("resumen") or {}
    return {
        "web_productos": int(web.get("productos_nuevos") or 0),
        "escritorio_productos": int(traidos["productos_nuevos"]),
        "web_ventas": int(web.get("ventas_nuevas") or 0),
        "escritorio_ventas": int(traidos["ventas_nuevas"]),
        "web_descontadas": int(web.get("ventas_descontadas") or 0),
        "escritorio_descontadas": int(traidos["ventas_descontadas"]),
        "stocks": int(web.get("stocks") or 0) + int(traidos["stocks"]),
        "conflictos": [] if acuerdos else diferencias(propios, ajenos, nombres),
    }


def abrir_dialogo(app) -> None:
    import customtkinter as ctk
    from tkinter import messagebox

    usuario = getattr(app, "current_user", "") or ""
    clave = getattr(app, "_clave_sesion", "") or ""
    if not usuario or not clave:
        messagebox.showwarning(
            "Sincronizar",
            "Entra de nuevo con tu usuario y contraseña para poder hablar con la web.",
        )
        return

    ventana = ctk.CTkToplevel(app.root)
    ventana.title("Sincronizar inventario")
    ventana.geometry("760x560")
    ventana.grab_set()
    texto = ctk.CTkLabel(
        ventana,
        text="Comparando el escritorio con la web…",
        font=("Arial", 16, "bold"),
    )
    texto.pack(padx=20, pady=20)

    def terminar(resultado: dict | None, error: str) -> None:
        if not ventana.winfo_exists():
            return
        for hijo in ventana.winfo_children():
            hijo.destroy()
        if error:
            ctk.CTkLabel(ventana, text=error, wraplength=680, font=("Arial", 14)).pack(padx=20, pady=20)
            ctk.CTkButton(ventana, text="Cerrar", command=ventana.destroy).pack(pady=10)
            return
        _pintar_resultado(ventana, app, resultado)

    def trabajo():
        try:
            resultado = sincronizar(app.db, usuario, clave)
        except ServidorInalcanzable as exc:
            app.root.after(0, lambda: terminar(None, str(exc)))
            return
        except Exception as exc:
            app.root.after(0, lambda: terminar(None, f"No se pudo sincronizar. {exc}"))
            return
        app.root.after(0, lambda: terminar(resultado, ""))

    import threading
    threading.Thread(target=trabajo, daemon=True).start()


def _pintar_resultado(ventana, app, resultado: dict) -> None:
    import customtkinter as ctk

    lineas = [
        f"Productos agregados a la web: {resultado['web_productos']}",
        f"Productos agregados al escritorio: {resultado['escritorio_productos']}",
        f"Ventas del escritorio copiadas a la web: {resultado['web_ventas']}",
        f"Ventas de la web copiadas al escritorio: {resultado['escritorio_ventas']}",
        f"Líneas de venta descontadas en la web: {resultado['web_descontadas']}",
        f"Líneas de venta descontadas en el escritorio: {resultado['escritorio_descontadas']}",
    ]
    ctk.CTkLabel(
        ventana,
        text="\n".join(lineas),
        justify="left",
        font=("Arial", 14),
    ).pack(anchor="w", padx=20, pady=(16, 8))
    conflictos = resultado.get("conflictos") or []
    if not conflictos:
        ctk.CTkLabel(
            ventana,
            text="El stock coincide en los productos que están en los dos lados.",
            font=("Arial", 13),
        ).pack(anchor="w", padx=20)
        ctk.CTkButton(ventana, text="Cerrar", command=ventana.destroy).pack(pady=16)
        return
    ctk.CTkLabel(
        ventana,
        text="Estos productos están en los dos lados y el stock no coincide. Elige el valor real.",
        wraplength=700,
        font=("Arial", 13, "bold"),
    ).pack(anchor="w", padx=20, pady=(8, 4))
    lista = ctk.CTkScrollableFrame(ventana, height=300)
    lista.pack(fill="both", expand=True, padx=16, pady=8)
    entradas = []
    for fila in conflictos:
        caja = ctk.CTkFrame(lista, fg_color="transparent")
        caja.pack(fill="x", pady=4)
        ctk.CTkLabel(
            caja,
            text=f"{fila['nombre']}   escritorio {fila['escritorio']:g}   web {fila['web']:g}",
            anchor="w",
        ).pack(side="left", padx=4)
        valor = ctk.CTkEntry(caja, width=90, placeholder_text="real")
        valor.pack(side="right", padx=4)
        entradas.append((fila, valor))
        ctk.CTkButton(
            caja, text="Web", width=52,
            command=lambda e=valor, n=fila["web"]: e.delete(0, "end") or e.insert(0, f"{n:g}"),
        ).pack(side="right", padx=2)
        ctk.CTkButton(
            caja, text="Escritorio", width=80,
            command=lambda e=valor, n=fila["escritorio"]: e.delete(0, "end") or e.insert(0, f"{n:g}"),
        ).pack(side="right", padx=2)

    def aplicar():
        acuerdos = []
        for fila, entrada in entradas:
            texto = entrada.get().strip().replace(",", ".")
            if texto == "":
                continue
            try:
                numero = float(texto)
            except ValueError:
                from tkinter import messagebox
                messagebox.showwarning("Stock", f"El valor de {fila['nombre']} no es un número.")
                return
            acuerdos.append({"codigo": fila["codigo"], "stock": numero})
        if len(acuerdos) != len(entradas):
            from tkinter import messagebox
            messagebox.showwarning("Stock", "Elige el valor real de cada producto de la lista.")
            return
        usuario = app.current_user
        clave = app._clave_sesion

        def trabajo():
            try:
                sincronizar(app.db, usuario, clave, acuerdos)
            except ServidorInalcanzable as exc:
                app.root.after(0, lambda: _aviso(ventana, str(exc)))
                return
            app.root.after(0, ventana.destroy)

        import threading
        threading.Thread(target=trabajo, daemon=True).start()

    ctk.CTkButton(ventana, text="Guardar el valor real en los dos lados", command=aplicar).pack(pady=10)


def _aviso(ventana, texto: str) -> None:
    from tkinter import messagebox
    if ventana.winfo_exists():
        messagebox.showerror("Sincronizar", texto)
