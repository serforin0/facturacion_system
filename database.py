import sqlite3
import os
from datetime import datetime

from app_paths import is_frozen, data_directory


def _tipo_codigo_default(tipo_movimiento: str) -> str:
    if not tipo_movimiento:
        return ""
    t = tipo_movimiento.lower()
    if t == "venta":
        return "FA"
    if t in ("ingreso", "compra"):
        return "CO"
    if t in ("retiro", "salida_manual", "ajuste"):
        return "RT"
    return tipo_movimiento[:2].upper() if len(tipo_movimiento) >= 2 else tipo_movimiento.upper()


class Database:
    def __init__(self, db_name="bar_inventory.db"):
        if is_frozen():
            self.db_name = os.path.join(data_directory(), db_name)
        else:
            self.db_name = db_name
        self.init_database()
    
    def get_connection(self):
        conn = sqlite3.connect(self.db_name)
        # Activar claves foráneas en SQLite
        conn.execute("PRAGMA foreign_keys = ON")
        return conn
    
    def init_database(self):
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # =========================
        #   TABLA CATEGORÍAS
        # =========================
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS categorias (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre TEXT UNIQUE NOT NULL
            )
        ''')
        
        # =========================
        #   TABLA PRODUCTOS
        # =========================
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS productos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre TEXT NOT NULL,
                descripcion TEXT,
                -- Precio de venta actual (el que se usa en la factura)
                precio REAL NOT NULL,
                -- Precio normal / de lista (para comparar descuentos)
                precio_base REAL,
                -- Precio mínimo permitido (tope de rebaja)
                precio_minimo REAL,
                stock INTEGER NOT NULL DEFAULT 0,
                categoria_id INTEGER,
                stock_minimo INTEGER DEFAULT 5,
                imagen_path TEXT,        -- Ruta de la imagen
                codigo_barras TEXT,      -- Código de barras
                activo BOOLEAN DEFAULT 1,
                fecha_creacion TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (categoria_id) REFERENCES categorias (id)
            )
        ''')

        # ✅ Compatibilidad con BD viejas (que no tenían las nuevas columnas)
        cursor.execute("PRAGMA table_info(productos)")
        columnas = [col[1] for col in cursor.fetchall()]

        # codigo_barras
        if "codigo_barras" not in columnas:
            cursor.execute("ALTER TABLE productos ADD COLUMN codigo_barras TEXT")
            print("✅ Columna 'codigo_barras' añadida a la tabla productos")

        # precio_base
        if "precio_base" not in columnas:
            cursor.execute("ALTER TABLE productos ADD COLUMN precio_base REAL")
            print("✅ Columna 'precio_base' añadida a la tabla productos")

        # precio_minimo
        if "precio_minimo" not in columnas:
            cursor.execute("ALTER TABLE productos ADD COLUMN precio_minimo REAL")
            print("✅ Columna 'precio_minimo' añadida a la tabla productos")

        self._migrate_productos_monica_fields(cursor)
        self._migrate_productos_extended(cursor)

        # =========================
        #   TABLA USUARIOS (LOGIN)
        # =========================
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                role TEXT NOT NULL
            )
        """)

        # 🔹 USUARIOS POR DEFECTO
        default_users = [
            ("admin", "admin07!", "admin"),
            ("user", "usuario07!", "user"),
            ("empleado", "empreado07!", "empleado"),
        ]

        for username, password, role in default_users:
            cursor.execute("SELECT 1 FROM users WHERE username = ?", (username,))
            if cursor.fetchone() is None:
                cursor.execute(
                    "INSERT INTO users (username, password, role) VALUES (?, ?, ?)",
                    (username, password, role)
                )

        # =========================
        #   TABLA CLIENTES
        # =========================
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS clientes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre TEXT NOT NULL,
                documento TEXT,              -- cédula/RNC/NIF, etc.
                tipo_documento TEXT,         -- 'cedula', 'rnc', etc.
                telefono TEXT,
                email TEXT,
                direccion TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # =========================
        #   TABLA FACTURAS (ENCABEZADO)
        # =========================
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS facturas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                numero TEXT UNIQUE,              -- número de comprobante
                fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                tipo_comprobante TEXT NOT NULL,  -- 'consumidor_final', 'credito_fiscal', 'nota_credito', etc.
                cliente_id INTEGER,              -- NULL si consumidor final
                subtotal REAL NOT NULL DEFAULT 0,
                descuento_total REAL NOT NULL DEFAULT 0,
                impuesto_total REAL NOT NULL DEFAULT 0,
                total REAL NOT NULL DEFAULT 0,
                estado TEXT NOT NULL DEFAULT 'emitida',  -- 'emitida', 'anulada'
                usuario TEXT,                    -- username que emitió
                caja TEXT,                       -- opcional: caja/terminal
                FOREIGN KEY (cliente_id) REFERENCES clientes (id)
            )
        """)

        # =========================
        #   TABLA DETALLE FACTURA
        # =========================
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS factura_detalle (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                factura_id INTEGER NOT NULL,
                producto_id INTEGER,         -- puede ser NULL si es ítem manual
                descripcion TEXT NOT NULL,
                cantidad REAL NOT NULL,
                precio_unitario REAL NOT NULL,
                descuento_item REAL NOT NULL DEFAULT 0,
                impuesto_item REAL NOT NULL DEFAULT 0,
                total_linea REAL NOT NULL,
                FOREIGN KEY (factura_id) REFERENCES facturas (id) ON DELETE CASCADE,
                FOREIGN KEY (producto_id) REFERENCES productos (id)
            )
        """)

        # =========================
        #   TABLA PAGOS DE FACTURA
        #   (permite pagos mixtos)
        # =========================
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS pagos_factura (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                factura_id INTEGER NOT NULL,
                tipo_pago TEXT NOT NULL,         -- 'efectivo', 'tarjeta', 'transferencia', 'credito_cliente'
                monto REAL NOT NULL,
                referencia TEXT,                 -- referencia de tarjeta, banco, etc.
                FOREIGN KEY (factura_id) REFERENCES facturas (id) ON DELETE CASCADE
            )
        """)

        # =========================
        #   LOG DE ÍTEMS ELIMINADOS / EDITADOS
        # =========================
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS log_items_factura (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                factura_id INTEGER NOT NULL,
                producto_id INTEGER,
                descripcion TEXT,
                cantidad REAL,
                precio_unitario REAL,
                usuario TEXT,
                motivo TEXT,
                tipo_accion TEXT NOT NULL,   -- 'eliminado', 'modificado'
                fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (factura_id) REFERENCES facturas (id),
                FOREIGN KEY (producto_id) REFERENCES productos (id)
            )
        """)

        # =========================
        #   NOTAS DE CRÉDITO / DEVOLUCIONES
        # =========================
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS notas_credito (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                factura_original_id INTEGER NOT NULL,   -- factura a la que referencia
                factura_nota_id INTEGER,               -- si también guardas la NC como factura
                motivo TEXT,
                fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                monto_total REAL NOT NULL DEFAULT 0,
                FOREIGN KEY (factura_original_id) REFERENCES facturas (id),
                FOREIGN KEY (factura_nota_id) REFERENCES facturas (id)
            )
        """)

        # Detalle de la nota de crédito (por ítem)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS notas_credito_detalle (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nota_credito_id INTEGER NOT NULL,
                factura_detalle_id INTEGER,        -- ítem original
                producto_id INTEGER,
                cantidad REAL NOT NULL,
                monto REAL NOT NULL,
                FOREIGN KEY (nota_credito_id) REFERENCES notas_credito (id) ON DELETE CASCADE,
                FOREIGN KEY (factura_detalle_id) REFERENCES factura_detalle (id),
                FOREIGN KEY (producto_id) REFERENCES productos (id)
            )
        """)

        # =========================
        #   MOVIMIENTOS DE INVENTARIO (KARDEX)
        # =========================
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS movimientos_inventario (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                producto_id INTEGER NOT NULL,
                tipo_movimiento TEXT NOT NULL,     -- 'venta', 'devolucion', 'ajuste', 'ingreso'
                cantidad REAL NOT NULL,
                referencia TEXT,                  -- ej: número de factura, nota de crédito, etc.
                factura_id INTEGER,
                fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                usuario TEXT,
                FOREIGN KEY (producto_id) REFERENCES productos (id),
                FOREIGN KEY (factura_id) REFERENCES facturas (id)
            )
        """)

        # =========================
        #   TABLA PROMOCIONES
        # =========================
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS promociones (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre TEXT NOT NULL,
                descripcion TEXT,
                tipo_descuento TEXT NOT NULL,      -- 'porcentaje', 'fijo', '2x1', '3x2'
                valor REAL,                        -- % o monto según tipo
                fecha_inicio TIMESTAMP,
                fecha_fin TIMESTAMP,
                aplica_por TEXT,                   -- 'producto', 'categoria', 'monto_total', 'cliente'
                activo BOOLEAN DEFAULT 1
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS promociones_detalle (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                promocion_id INTEGER NOT NULL,
                producto_id INTEGER,
                categoria_id INTEGER,
                cliente_id INTEGER,
                FOREIGN KEY (promocion_id) REFERENCES promociones (id) ON DELETE CASCADE,
                FOREIGN KEY (producto_id) REFERENCES productos (id),
                FOREIGN KEY (categoria_id) REFERENCES categorias (id),
                FOREIGN KEY (cliente_id) REFERENCES clientes (id)
            )
        """)

        # =========================
        #   TABLA COMBOS (AGRUPAR PRODUCTOS)
        # =========================
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS combos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre TEXT NOT NULL,              -- Ej: Combo Cubetazo, Combo 2x1 Ron
                descripcion TEXT,
                precio_combo REAL NOT NULL,        -- precio total del combo
                activo BOOLEAN DEFAULT 1,
                fecha_creacion TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS combos_detalle (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                combo_id INTEGER NOT NULL,
                producto_id INTEGER NOT NULL,
                cantidad REAL NOT NULL DEFAULT 1,
                FOREIGN KEY (combo_id) REFERENCES combos (id) ON DELETE CASCADE,
                FOREIGN KEY (producto_id) REFERENCES productos (id)
            )
        """)

        # =========================
        #   TABLA CIERRES DE CAJA
        # =========================
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS cierres_caja (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre_caja TEXT,                      -- ej: 'Caja 1', 'Barra', etc.
                fecha_apertura TIMESTAMP,              -- cuándo se abrió el turno
                fecha_cierre TIMESTAMP,                -- cuándo se cerró
                usuario_apertura TEXT,                 -- quién abrió la caja
                usuario_cierre TEXT,                   -- quién cerró (puede ser el mismo u otro)
                monto_inicial REAL NOT NULL DEFAULT 0, -- fondo inicial en efectivo
                total_ventas REAL NOT NULL DEFAULT 0,  -- total facturado (todas las formas de pago)
                total_efectivo_sistema REAL NOT NULL DEFAULT 0,  -- lo que el sistema espera en efectivo
                total_tarjeta_sistema REAL NOT NULL DEFAULT 0,   -- tarjeta/otros
                total_otros_sistema REAL NOT NULL DEFAULT 0,     -- transferencias, etc.
                efectivo_contado REAL NOT NULL DEFAULT 0,        -- lo que contó el cajero
                diferencia_efectivo REAL NOT NULL DEFAULT 0,     -- contado - esperado
                observaciones TEXT,
                estado TEXT NOT NULL DEFAULT 'abierto'  -- 'abierto', 'cerrado'
            )
        """)

        # =========================
        #   TABLA CONFIGURACIÓN
        # =========================
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS config (
                clave TEXT PRIMARY KEY,
                valor TEXT
            )
        """)

        # Valor por defecto: ancho del ticket (en caracteres) para versiones viejas
        cursor.execute("""
            INSERT OR IGNORE INTO config (clave, valor)
            VALUES ('ticket_width_chars', '32')
        """)

        # Valores por defecto para perfiles de impresora
        cursor.execute("""
            INSERT OR IGNORE INTO config (clave, valor)
            VALUES ('printer_profile', 'movil_58')
        """)
        cursor.execute("""
            INSERT OR IGNORE INTO config (clave, valor)
            VALUES ('printer_width_movil_58', '32')
        """)
        cursor.execute("""
            INSERT OR IGNORE INTO config (clave, valor)
            VALUES ('printer_width_epson_80', '42')
        """)
        cursor.execute("""
            INSERT OR IGNORE INTO config (clave, valor)
            VALUES ('empresa_nombre', 'Mi empresa')
        """)
        cursor.execute("""
            INSERT OR IGNORE INTO config (clave, valor)
            VALUES ('empresa_direccion', '')
        """)
        cursor.execute("""
            INSERT OR IGNORE INTO config (clave, valor)
            VALUES ('app_logo_path', '')
        """)

        # =========================
        #   (Opcional) ÍNDICES ÚTILES
        # =========================
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_productos_categoria ON productos(categoria_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_productos_codigo_barras ON productos(codigo_barras)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_facturas_numero ON facturas(numero)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_facturas_cliente ON facturas(cliente_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_facturas_fecha ON facturas(fecha)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_factura_detalle_factura ON factura_detalle(factura_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_movimientos_producto ON movimientos_inventario(producto_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_movimientos_factura ON movimientos_inventario(factura_id)")
        self._migrate_movimientos_kardex(cursor)
        self._migrate_facturas_anulacion(cursor)
        self._migrate_facturacion_extendida(cursor)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_pagos_factura_factura ON pagos_factura(factura_id)")

        # =========================
        #   CATEGORÍAS POR DEFECTO
        # =========================
        categorias = ['Cerveza', 'Vino', 'Licor', 'Refresco', 'Agua', 'Otros']
        for categoria in categorias:
            cursor.execute(
                "INSERT OR IGNORE INTO categorias (nombre) VALUES (?)", 
                (categoria,)
            )

        try:
            from demo_seed import apply_demo_seed_if_empty

            apply_demo_seed_if_empty(cursor)
        except Exception as e:
            print("⚠️ No se pudieron cargar datos demo:", e)

        conn.commit()
        conn.close()
        print("✅ Base de datos inicializada correctamente")

    def _migrate_productos_monica_fields(self, cursor):
        """Campos extra para UI tipo ERP (código interno, tipo, precios 2–4, etc.)."""
        cursor.execute("PRAGMA table_info(productos)")
        cols = {row[1] for row in cursor.fetchall()}
        specs = [
            ("codigo_producto", "TEXT"),
            ("tipo_producto", "TEXT DEFAULT 'Físico'"),
            ("unidad_medida", "TEXT DEFAULT 'Unidad'"),
            ("precio_2", "REAL"),
            ("precio_3", "REAL"),
            ("precio_4", "REAL"),
            ("ubicacion", "TEXT"),
            ("notas_internas", "TEXT"),
            ("aplica_itbis", "INTEGER DEFAULT 1"),
            ("costo_usd", "REAL"),
            ("subcategoria_codigo", "TEXT"),
            ("bodega_codigo", "TEXT"),
            ("facturar_nivel_precio", "INTEGER DEFAULT 1"),
            ("aplica_itbis_compras", "INTEGER DEFAULT 0"),
            ("contenido_unidad_venta", "REAL DEFAULT 1"),
            ("contenido_unidad_compra", "REAL DEFAULT 1"),
            ("unidad_compra", "TEXT DEFAULT 'Unidad'"),
        ]
        for name, decl in specs:
            if name not in cols:
                cursor.execute(f"ALTER TABLE productos ADD COLUMN {name} {decl}")
        cursor.execute(
            """
            UPDATE productos
            SET codigo_producto = printf('P-%05d', id)
            WHERE codigo_producto IS NULL OR TRIM(codigo_producto) = ''
            """
        )

    def _migrate_productos_extended(self, cursor):
        """Detalle tipo MONICA: factura, fabricante, proveedor, cuentas, otro impuesto, relaciones."""
        cursor.execute("PRAGMA table_info(productos)")
        cols = {row[1] for row in cursor.fetchall()}
        specs = [
            ("descripcion_en_factura", "INTEGER DEFAULT 0"),
            ("codigo_fabricante", "TEXT"),
            ("facturar_sin_stock", "INTEGER DEFAULT 1"),
            ("proveedor_id", "INTEGER"),
            ("cuenta_ventas", "TEXT"),
            ("cuenta_gastos", "TEXT"),
            ("cuenta_inventario", "TEXT"),
            ("tiene_control_lotes", "INTEGER DEFAULT 0"),
            ("tiene_numero_serie", "INTEGER DEFAULT 0"),
            ("otro_impuesto", "TEXT DEFAULT ''"),
            ("otro_impuesto_ventas", "INTEGER DEFAULT 0"),
            ("otro_impuesto_compras", "INTEGER DEFAULT 0"),
        ]
        for name, decl in specs:
            if name not in cols:
                cursor.execute(f"ALTER TABLE productos ADD COLUMN {name} {decl}")

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS proveedores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre TEXT NOT NULL,
                documento TEXT,
                telefono TEXT,
                notas TEXT,
                activo INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS producto_proveedores_sec (
                producto_id INTEGER NOT NULL,
                proveedor_id INTEGER NOT NULL,
                PRIMARY KEY (producto_id, proveedor_id),
                FOREIGN KEY (producto_id) REFERENCES productos(id) ON DELETE CASCADE,
                FOREIGN KEY (proveedor_id) REFERENCES proveedores(id) ON DELETE CASCADE
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS producto_componentes (
                producto_id INTEGER NOT NULL,
                componente_id INTEGER NOT NULL,
                cantidad REAL NOT NULL DEFAULT 1,
                PRIMARY KEY (producto_id, componente_id),
                FOREIGN KEY (producto_id) REFERENCES productos(id) ON DELETE CASCADE,
                FOREIGN KEY (componente_id) REFERENCES productos(id) ON DELETE CASCADE
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS producto_equivalentes (
                producto_id INTEGER NOT NULL,
                equivalente_id INTEGER NOT NULL,
                PRIMARY KEY (producto_id, equivalente_id),
                FOREIGN KEY (producto_id) REFERENCES productos(id) ON DELETE CASCADE,
                FOREIGN KEY (equivalente_id) REFERENCES productos(id) ON DELETE CASCADE
            )
            """
        )
        cursor.execute("SELECT COUNT(*) FROM proveedores")
        if cursor.fetchone()[0] == 0:
            cursor.execute(
                "INSERT INTO proveedores (nombre, activo) VALUES (?, 1)",
                ("(Sin proveedor asignado)",),
            )

    def _migrate_facturas_anulacion(self, cursor):
        """Motivo y auditoría de anulación (estilo MONICA)."""
        cursor.execute("PRAGMA table_info(facturas)")
        cols = {row[1] for row in cursor.fetchall()}
        specs = [
            ("anulacion_motivo", "TEXT"),
            ("anulacion_usuario", "TEXT"),
            ("anulacion_fecha", "TIMESTAMP"),
        ]
        for name, decl in specs:
            if name not in cols:
                cursor.execute(f"ALTER TABLE facturas ADD COLUMN {name} {decl}")

    def _migrate_facturacion_extendida(self, cursor):
        """Condiciones de pago, vencimiento, presupuestos y metadatos de documento."""
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS condiciones_pago (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                codigo TEXT UNIQUE NOT NULL,
                nombre TEXT NOT NULL,
                dias_credito INTEGER NOT NULL DEFAULT 0,
                es_contado INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        cursor.execute("SELECT COUNT(*) FROM condiciones_pago")
        if cursor.fetchone()[0] == 0:
            for cod, nom, dias, cont in (
                ("CONT", "Contado al momento", 0, 1),
                ("C7", "Crédito 7 días", 7, 0),
                ("C15", "Crédito 15 días", 15, 0),
                ("C30", "Crédito 30 días", 30, 0),
                ("C45", "Crédito 45 días", 45, 0),
            ):
                cursor.execute(
                    """
                    INSERT INTO condiciones_pago (codigo, nombre, dias_credito, es_contado)
                    VALUES (?, ?, ?, ?)
                    """,
                    (cod, nom, dias, cont),
                )

        cursor.execute("PRAGMA table_info(facturas)")
        fcols = {row[1] for row in cursor.fetchall()}
        for name, decl in (
            ("fecha_vencimiento", "TEXT"),
            ("condicion_pago_id", "INTEGER"),
            ("observaciones", "TEXT"),
            ("referencia_entrega", "TEXT"),
            ("moneda", "TEXT DEFAULT 'DOP'"),
            ("tasa_cambio", "REAL DEFAULT 1.0"),
            ("precio_incluye_itbis", "INTEGER DEFAULT 0"),
        ):
            if name not in fcols:
                cursor.execute(f"ALTER TABLE facturas ADD COLUMN {name} {decl}")

        cursor.execute("PRAGMA table_info(notas_credito)")
        ncols = {row[1] for row in cursor.fetchall()}
        for name, decl in (
            ("numero", "TEXT"),
            ("usuario", "TEXT"),
            ("estado", "TEXT DEFAULT 'emitida'"),
        ):
            if name not in ncols:
                cursor.execute(f"ALTER TABLE notas_credito ADD COLUMN {name} {decl}")

    def _migrate_movimientos_kardex(self, cursor):
        cursor.execute("PRAGMA table_info(movimientos_inventario)")
        cols = {row[1] for row in cursor.fetchall()}
        specs = [
            ("descripcion_mov", "TEXT"),
            ("tipo_codigo", "TEXT"),
            ("entidad_nombre", "TEXT"),
            ("bodega_codigo", "TEXT"),
            ("precio_unitario", "REAL"),
        ]
        for name, decl in specs:
            if name not in cols:
                cursor.execute(
                    f"ALTER TABLE movimientos_inventario ADD COLUMN {name} {decl}"
                )

    def list_proveedores(self, solo_activos=True):
        conn = self.get_connection()
        cur = conn.cursor()
        q = "SELECT id, nombre, documento, telefono FROM proveedores"
        if solo_activos:
            q += " WHERE IFNULL(activo,1) = 1"
        q += " ORDER BY nombre"
        cur.execute(q)
        rows = cur.fetchall()
        conn.close()
        return rows

    def crear_proveedor(self, nombre: str, documento=None, telefono=None):
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO proveedores (nombre, documento, telefono, activo) VALUES (?,?,?,1)",
            (nombre.strip(), documento, telefono),
        )
        new_id = cur.lastrowid
        conn.commit()
        conn.close()
        return new_id

    def get_proveedores_secundarios_producto(self, producto_id: int):
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT proveedor_id FROM producto_proveedores_sec WHERE producto_id = ?",
            (producto_id,),
        )
        ids = [r[0] for r in cur.fetchall()]
        conn.close()
        return ids

    def set_proveedores_secundarios_producto(self, producto_id: int, proveedor_ids):
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM producto_proveedores_sec WHERE producto_id = ?", (producto_id,)
        )
        for pid in proveedor_ids:
            try:
                cur.execute(
                    "INSERT INTO producto_proveedores_sec (producto_id, proveedor_id) VALUES (?,?)",
                    (producto_id, int(pid)),
                )
            except sqlite3.IntegrityError:
                pass
        conn.commit()
        conn.close()

    def get_componentes_producto(self, producto_id: int):
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT c.componente_id, c.cantidad, p.nombre, IFNULL(p.codigo_producto,'')
            FROM producto_componentes c
            JOIN productos p ON p.id = c.componente_id
            WHERE c.producto_id = ?
            ORDER BY p.nombre
            """,
            (producto_id,),
        )
        rows = cur.fetchall()
        conn.close()
        return rows

    def set_componentes_producto(self, producto_id: int, lista_tuplas):
        """lista_tuplas: [(componente_id, cantidad), ...]"""
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM producto_componentes WHERE producto_id = ?", (producto_id,)
        )
        for comp_id, cant in lista_tuplas:
            if int(comp_id) == int(producto_id):
                continue
            try:
                qty = float(cant) if cant else 1.0
                if qty <= 0:
                    qty = 1.0
                cur.execute(
                    """
                    INSERT INTO producto_componentes (producto_id, componente_id, cantidad)
                    VALUES (?,?,?)
                    """,
                    (producto_id, int(comp_id), qty),
                )
            except sqlite3.IntegrityError:
                pass
        conn.commit()
        conn.close()

    def get_equivalentes_producto(self, producto_id: int):
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT e.equivalente_id, p.nombre, IFNULL(p.codigo_producto,'')
            FROM producto_equivalentes e
            JOIN productos p ON p.id = e.equivalente_id
            WHERE e.producto_id = ?
            ORDER BY p.nombre
            """,
            (producto_id,),
        )
        rows = cur.fetchall()
        conn.close()
        return rows

    def set_equivalentes_producto(self, producto_id: int, equivalente_ids):
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM producto_equivalentes WHERE producto_id = ?", (producto_id,)
        )
        for eid in equivalente_ids:
            if int(eid) == int(producto_id):
                continue
            try:
                cur.execute(
                    """
                    INSERT INTO producto_equivalentes (producto_id, equivalente_id)
                    VALUES (?,?)
                    """,
                    (producto_id, int(eid)),
                )
            except sqlite3.IntegrityError:
                pass
        conn.commit()
        conn.close()

    # ==========================
    #   KARDEX / MOVIMIENTOS
    # ==========================

    def insert_movimiento_kardex(
        self,
        producto_id: int,
        tipo_movimiento: str,
        cantidad: float,
        *,
        ajustar_stock: bool = True,
        referencia: str = None,
        factura_id: int = None,
        usuario: str = None,
        tipo_codigo: str = None,
        entidad_nombre: str = None,
        bodega_codigo: str = None,
        precio_unitario: float = None,
        descripcion_mov: str = None,
        conn=None,
    ):
        """
        Registra un movimiento en movimientos_inventario.
        cantidad > 0 entrada, < 0 salida.
        Si ajustar_stock es True, actualiza productos.stock en la misma operación.
        Si conn se pasa (misma transacción externa), no hace commit ni cierra.
        """
        close_after = conn is None
        if conn is None:
            conn = self.get_connection()
        cur = conn.cursor()
        try:
            if ajustar_stock:
                cur.execute(
                    "UPDATE productos SET stock = IFNULL(stock,0) + ? WHERE id = ?",
                    (cantidad, producto_id),
                )
            cur.execute(
                """
                INSERT INTO movimientos_inventario (
                    producto_id, tipo_movimiento, cantidad, referencia, factura_id,
                    usuario, descripcion_mov, tipo_codigo, entidad_nombre,
                    bodega_codigo, precio_unitario
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    producto_id,
                    tipo_movimiento,
                    float(cantidad),
                    referencia,
                    factura_id,
                    usuario,
                    descripcion_mov,
                    tipo_codigo,
                    entidad_nombre,
                    bodega_codigo,
                    precio_unitario,
                ),
            )
            if close_after:
                conn.commit()
        finally:
            if close_after:
                conn.close()

    def list_bodegas_codigos(self):
        """Códigos de bodega usados en productos + 'Principal' por defecto."""
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT DISTINCT TRIM(bodega_codigo) AS b
            FROM productos
            WHERE bodega_codigo IS NOT NULL AND TRIM(bodega_codigo) != ''
            ORDER BY b COLLATE NOCASE
            """
        )
        rows = [r[0] for r in cur.fetchall() if r[0]]
        conn.close()
        if not rows:
            rows = ["Principal"]
        return rows

    def list_ids_productos_activos(self):
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT id FROM productos WHERE IFNULL(activo,1) = 1 ORDER BY nombre COLLATE NOCASE"
        )
        ids = [r[0] for r in cur.fetchall()]
        conn.close()
        return ids

    def buscar_primer_producto_por_codigo(self, texto: str):
        """
        Devuelve id del producto por código/barras exacto o id numérico;
        si no, primer código_producto que empiece por texto (activos).
        """
        if texto is None:
            return None
        c = (texto or "").strip()
        if not c:
            return None
        conn = self.get_connection()
        cur = conn.cursor()
        if c.isdigit():
            cur.execute(
                "SELECT id FROM productos WHERE id = ? AND IFNULL(activo,1) = 1",
                (int(c),),
            )
            row = cur.fetchone()
            conn.close()
            return row[0] if row else None
        cur.execute(
            """
            SELECT id FROM productos
            WHERE IFNULL(activo,1) = 1
              AND (
                TRIM(IFNULL(codigo_producto,'')) = ?
                OR TRIM(IFNULL(codigo_barras,'')) = ?
              )
            LIMIT 1
            """,
            (c, c),
        )
        row = cur.fetchone()
        if row:
            conn.close()
            return row[0]
        like = f"{c}%"
        cur.execute(
            """
            SELECT id FROM productos
            WHERE IFNULL(activo,1) = 1
              AND TRIM(IFNULL(codigo_producto,'')) LIKE ?
            ORDER BY LENGTH(TRIM(IFNULL(codigo_producto,''))), id
            LIMIT 1
            """,
            (like,),
        )
        row = cur.fetchone()
        if row:
            conn.close()
            return row[0]
        # Nombre (exacto o contiene) — para kardex cuando el usuario escribe la descripción
        cur.execute(
            """
            SELECT id FROM productos
            WHERE IFNULL(activo,1) = 1 AND TRIM(nombre) = ? COLLATE NOCASE
            LIMIT 1
            """,
            (c,),
        )
        row = cur.fetchone()
        if row:
            conn.close()
            return row[0]
        cur.execute(
            """
            SELECT id FROM productos
            WHERE IFNULL(activo,1) = 1 AND nombre LIKE ? COLLATE NOCASE
            ORDER BY LENGTH(nombre), id
            LIMIT 1
            """,
            (f"%{c}%",),
        )
        row = cur.fetchone()
        conn.close()
        return row[0] if row else None

    def get_producto_kardex_resumen(self, producto_id: int):
        """Datos de cabecera kardex + unidades vendidas acumuladas (movimientos tipo venta/FA)."""
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT p.nombre,
                   COALESCE(NULLIF(TRIM(p.codigo_producto), ''),
                            NULLIF(TRIM(p.codigo_barras), ''),
                            printf('P-%05d', p.id)),
                   IFNULL(p.stock, 0),
                   IFNULL(p.precio_base, 0),
                   IFNULL(p.precio, 0),
                   IFNULL(c.nombre, '—'),
                   IFNULL(p.stock_minimo, 0),
                   IFNULL(NULLIF(TRIM(p.bodega_codigo), ''), '')
            FROM productos p
            LEFT JOIN categorias c ON c.id = p.categoria_id
            WHERE p.id = ?
            """,
            (producto_id,),
        )
        row = cur.fetchone()
        if not row:
            conn.close()
            return None
        cur.execute(
            """
            SELECT COALESCE(SUM(ABS(cantidad)), 0)
            FROM movimientos_inventario
            WHERE producto_id = ?
              AND cantidad < 0
              AND (tipo_movimiento = 'venta' OR IFNULL(tipo_codigo,'') = 'FA')
            """,
            (producto_id,),
        )
        uv = float(cur.fetchone()[0] or 0)
        conn.close()
        (
            nombre,
            codigo,
            stock,
            costo,
            precio,
            categoria,
            stock_min,
            bodega_def,
        ) = row
        return {
            "nombre": nombre,
            "codigo": codigo,
            "stock": float(stock or 0),
            "costo": float(costo or 0),
            "precio": float(precio or 0),
            "categoria": categoria,
            "stock_minimo": float(stock_min or 0),
            "bodega_default": (bodega_def or "").strip(),
            "unids_vendidas": uv,
        }

    def get_kardex_filas_con_saldo(
        self,
        producto_id: int,
        fecha_desde=None,
        fecha_hasta=None,
        bodega_filtro=None,
    ):
        """
        Lista de dicts listos para grilla: fecha, descripcion, tipo_codigo, entidad,
        bodega, unidades, balance, precio.
        bodega_filtro None o 'TODOS'/'TODAS': sin filtrar por bodega.
        Saldo inicial en ventana = stock_actual - sum(cantidad en ventana).
        """
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT IFNULL(stock,0), IFNULL(NULLIF(TRIM(bodega_codigo),''), '') FROM productos WHERE id = ?",
            (producto_id,),
        )
        r0 = cur.fetchone()
        if not r0:
            conn.close()
            return []
        stock_now = float(r0[0] or 0)
        bodega_prod = (r0[1] or "").strip()

        q = """
            SELECT mi.id, mi.fecha, mi.descripcion_mov, mi.tipo_codigo, mi.tipo_movimiento,
                   mi.entidad_nombre, mi.bodega_codigo, mi.cantidad, mi.precio_unitario
            FROM movimientos_inventario mi
            WHERE mi.producto_id = ?
        """
        params = [producto_id]
        if fecha_desde:
            q += " AND date(mi.fecha) >= date(?)"
            params.append(fecha_desde)
        if fecha_hasta:
            q += " AND date(mi.fecha) <= date(?)"
            params.append(fecha_hasta)
        q += " ORDER BY datetime(mi.fecha) ASC, mi.id ASC"
        cur.execute(q, params)
        raw = cur.fetchall()
        conn.close()

        def _bodega_efectiva(bod_mov):
            b = (bod_mov or "").strip()
            if b:
                return b
            return bodega_prod

        filas = []
        for tup in raw:
            _id, fecha, desc, tcod, tmov, ent, bod, cant, precio = tup
            eff = _bodega_efectiva(bod)
            filas.append(
                {
                    "id": _id,
                    "fecha": fecha,
                    "descripcion": desc or "",
                    "tipo_codigo": (tcod or _tipo_codigo_default(tmov)) or "",
                    "entidad": ent or "",
                    "bodega": eff,
                    "cantidad": float(cant or 0),
                    "precio": precio,
                }
            )

        bf = (bodega_filtro or "").strip()
        bodega_filtrada = bf and bf.upper() not in ("TODOS", "TODAS")
        if bodega_filtrada:
            filas = [x for x in filas if x["bodega"] == bf]

        out = []
        if bodega_filtrada:
            # Stock es global; con filtro por bodega el saldo es relativo a las filas mostradas.
            balance = 0.0
            for x in filas:
                balance += x["cantidad"]
                row = dict(x)
                row["balance"] = balance
                out.append(row)
        else:
            suma_win = sum(x["cantidad"] for x in filas)
            balance = stock_now - suma_win
            for x in filas:
                balance += x["cantidad"]
                row = dict(x)
                row["balance"] = balance
                out.append(row)
        return out

    def list_productos_picker(self, exclude_id=None):
        """Lista compacta para diálogos Asignar (id, codigo, nombre)."""
        conn = self.get_connection()
        cur = conn.cursor()
        if exclude_id is not None:
            cur.execute(
                """
                SELECT id, IFNULL(codigo_producto,''), nombre FROM productos
                WHERE activo = 1 AND id != ?
                ORDER BY nombre
                """,
                (exclude_id,),
            )
        else:
            cur.execute(
                """
                SELECT id, IFNULL(codigo_producto,''), nombre FROM productos
                WHERE activo = 1 ORDER BY nombre
                """
            )
        rows = cur.fetchall()
        conn.close()
        return rows

    # 🔹 MÉTODO PARA VALIDAR LOGIN
    def validate_user(self, username: str, password: str):
        """
        Devuelve el rol del usuario si las credenciales son correctas,
        si no, devuelve None.
        """
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT role FROM users WHERE username = ? AND password = ?",
            (username, password)
        )
        row = cursor.fetchone()
        conn.close()
        if row:
            return row[0]  # role
        return None

    # ==========================
    #   USUARIOS: CRUD BÁSICO
    # ==========================
    def get_users(self):
        """
        Devuelve lista de usuarios como:
        [(id, username, role), ...]
        """
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id, username, role FROM users ORDER BY id")
        rows = cursor.fetchall()
        conn.close()
        return rows

    def create_user(self, username: str, password: str, role: str):
        """
        Crea un nuevo usuario. Lanza ValueError si el username ya existe.
        ⚠️ En producción deberías guardar password hasheado.
        """
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                "INSERT INTO users (username, password, role) VALUES (?, ?, ?)",
                (username, password, role)
            )
            conn.commit()
        except sqlite3.IntegrityError:
            conn.close()
            raise ValueError("El nombre de usuario ya existe")
        conn.close()

    def update_user(self, user_id: int, username: str, password: str, role: str):
        """
        Actualiza los datos de un usuario.
        Si password es None o cadena vacía, NO se modifica la contraseña.
        """
        conn = self.get_connection()
        cursor = conn.cursor()

        campos = []
        params = []

        if username:
            campos.append("username = ?")
            params.append(username)

        if password:  # solo si no está vacío
            campos.append("password = ?")
            params.append(password)

        if role:
            campos.append("role = ?")
            params.append(role)

        if not campos:
            conn.close()
            return  # Nada que actualizar

        params.append(user_id)
        query = f"UPDATE users SET {', '.join(campos)} WHERE id = ?"
        cursor.execute(query, params)
        conn.commit()
        conn.close()

    def delete_user(self, user_id: int):
        """
        Elimina un usuario por ID.
        """
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()
        conn.close()

    # ==========================
    #   CONFIGURACIÓN GENERAL
    # ==========================
    def get_config(self, clave: str, default: str = None) -> str:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT valor FROM config WHERE clave = ?", (clave,))
        row = cursor.fetchone()
        conn.close()
        if row and row[0] is not None:
            return row[0]
        return default

    def set_config(self, clave: str, valor: str):
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO config (clave, valor)
            VALUES (?, ?)
            ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor
            """,
            (clave, valor)
        )
        conn.commit()
        conn.close()

    def get_inventory_valuation(self):
        """
        Retorna la valorización del inventario activo.
        Devuelve una lista de tuplas: (nombre, categoria, stock, precio_base, precio_venta, valor_costo, valor_venta)
        Y además el gran total de (total_costo, total_venta).
        """
        conn = self.get_connection()
        cursor = conn.cursor()
        
        query = """
            SELECT 
                p.nombre, 
                c.nombre as categoria, 
                p.stock, 
                p.precio_base, 
                p.precio,
                (p.stock * p.precio_base) as valor_costo,
                (p.stock * p.precio) as valor_venta
            FROM productos p
            LEFT JOIN categorias c ON p.categoria_id = c.id
            WHERE p.activo = 1 AND p.stock > 0
            ORDER BY c.nombre, p.nombre
        """
        
        cursor.execute(query)
        rows = cursor.fetchall()
        
        # Calcular los grandes totales
        total_costo = sum(row[5] for row in rows if row[5] is not None)
        total_venta = sum(row[6] for row in rows if row[6] is not None)
        
        conn.close()
        
        return rows, total_costo, total_venta

    def get_empresa_info(self):
        """Datos de cabecera para reportes y tickets."""
        return {
            "nombre": self.get_config("empresa_nombre", "Mi empresa"),
            "direccion": self.get_config("empresa_direccion", "") or "",
        }

    def get_app_logo_path(self):
        """Ruta absoluta a imagen de logo si existe; si no, None (usar assets por defecto)."""
        p = (self.get_config("app_logo_path", "") or "").strip()
        if p and os.path.isfile(p):
            return p
        return None

    def set_empresa_info(self, nombre: str, direccion: str):
        self.set_config("empresa_nombre", (nombre or "").strip() or "Mi empresa")
        self.set_config("empresa_direccion", (direccion or "").strip())

    def get_inventory_valuation_report(self, include_zero_stock: bool = False):
        """
        Filas para reporte «Valor del inventario» (estilo MONICA).
        Cada fila: código, descripción, costo_unit, precio_unit, stock, valor_inventario, valor_venta.
        Retorna (filas, total_valor_costo, total_valor_venta, total_cantidad_stock, n_items).
        """
        conn = self.get_connection()
        cursor = conn.cursor()
        stock_cond = "" if include_zero_stock else " AND IFNULL(p.stock, 0) > 0 "
        query = f"""
            SELECT
                COALESCE(
                    NULLIF(TRIM(p.codigo_producto), ''),
                    NULLIF(TRIM(p.codigo_barras), ''),
                    printf('P-%05d', p.id)
                ),
                p.nombre,
                IFNULL(p.precio_base, 0),
                IFNULL(p.precio, 0),
                IFNULL(p.stock, 0),
                (IFNULL(p.stock, 0) * IFNULL(p.precio_base, 0)),
                (IFNULL(p.stock, 0) * IFNULL(p.precio, 0))
            FROM productos p
            LEFT JOIN categorias c ON p.categoria_id = c.id
            WHERE p.activo = 1
            {stock_cond}
            ORDER BY p.nombre
        """
        cursor.execute(query)
        rows = cursor.fetchall()
        total_costo = sum(float(r[5] or 0) for r in rows)
        total_venta = sum(float(r[6] or 0) for r in rows)
        total_qty = sum(float(r[4] or 0) for r in rows)
        n_items = len(rows)
        conn.close()
        return rows, total_costo, total_venta, total_qty, n_items

    def get_kardex_resumen_mensual(self, dias: int = 90):
        """
        Agrupa movimientos de inventario por mes calendario (YYYY-MM) y tipo.
        Útil para reporte de actividad de inventario en ventana reciente.
        Retorna filas: (ym, tipo_movimiento, tipo_codigo, n_movs, sum_cant, sum_abs).
        """
        d = max(1, min(int(dias), 3660))
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT
                strftime('%Y-%m', mi.fecha, 'localtime') AS ym,
                mi.tipo_movimiento,
                IFNULL(NULLIF(TRIM(mi.tipo_codigo), ''), mi.tipo_movimiento) AS tipo_codigo,
                COUNT(*) AS n_movs,
                SUM(mi.cantidad) AS sum_cant,
                SUM(ABS(mi.cantidad)) AS sum_abs
            FROM movimientos_inventario mi
            WHERE date(mi.fecha, 'localtime') >= date('now', 'localtime', '-{d} days')
            GROUP BY ym, mi.tipo_movimiento, mi.tipo_codigo
            ORDER BY ym ASC, mi.tipo_movimiento, tipo_codigo
            """
        )
        rows = cur.fetchall()
        conn.close()
        return rows

    def get_total_inventory_count(
        self,
        search_text=None,
        category_filter=None,
        search_mode="todos",
        solo_activos=True,
    ) -> int:
        """Devuelve el total de productos para la paginación (por defecto solo activos)."""
        conn = self.get_connection()
        cursor = conn.cursor()

        query = (
            "SELECT COUNT(*) FROM productos p "
            "LEFT JOIN categorias c ON p.categoria_id = c.id WHERE 1=1"
        )
        params = []
        if solo_activos:
            query += " AND p.activo = 1"

        if search_text:
            like = f"%{search_text.strip()}%"
            sm = (search_mode or "todos").lower()
            if sm == "codigo":
                query += (
                    " AND (p.codigo_producto LIKE ? OR IFNULL(p.codigo_barras,'') LIKE ?)"
                )
                params.extend([like, like])
            elif sm == "nombre":
                query += " AND p.nombre LIKE ?"
                params.append(like)
            elif sm == "descripcion":
                query += " AND IFNULL(p.descripcion,'') LIKE ?"
                params.append(like)
            else:
                query += (
                    " AND (p.nombre LIKE ? OR IFNULL(p.descripcion,'') LIKE ? "
                    "OR IFNULL(p.codigo_barras,'') LIKE ? "
                    "OR IFNULL(p.codigo_producto,'') LIKE ?)"
                )
                params.extend([like, like, like, like])

        if category_filter and category_filter != "Todas":
            query += " AND c.nombre = ?"
            params.append(category_filter)

        cursor.execute(query, params)
        count = cursor.fetchone()[0]
        conn.close()
        return count

    def get_inventory_valuation_by_category(self):
        """Para el gráfico de Pie: Devuelve lista de (categoria, total_valor_costo)"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        query = """
            SELECT c.nombre, SUM(p.stock * p.precio_base)
            FROM productos p
            LEFT JOIN categorias c ON p.categoria_id = c.id
            WHERE p.activo = 1 AND p.stock > 0
            GROUP BY c.nombre
            HAVING SUM(p.stock * p.precio_base) > 0
        """
        cursor.execute(query)
        rows = cursor.fetchall()
        
        # Filtrar Null y formatear
        data = []
        for row in rows:
            cat = row[0] if row[0] else "Sin categoría"
            val = row[1] if row[1] else 0
            data.append((cat, val))
            
        conn.close()
        return data

    def get_sales_last_7_days(self):
        """Para el gráfico de Barras: Devuelve lista de (fecha_str, total_venta)."""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # SQLite date() function for last 7 days aggregation
        # Tomar los últimos 7 días considerando la fecha local
        query = """
            SELECT date(fecha, 'localtime') as dia, SUM(total)
            FROM facturas
            WHERE date(fecha, 'localtime') >= date('now', '-6 days', 'localtime')
            GROUP BY dia
            ORDER BY dia ASC
        """
        cursor.execute(query)
        rows = cursor.fetchall()
        conn.close()
        return rows

    def get_total_facturas_count(self, fecha_inicio=None, fecha_fin=None, cajero=None) -> int:
        """Devuelve el total de facturas para la paginación."""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        query = "SELECT COUNT(*) FROM facturas WHERE 1=1"
        params = []
        
        if fecha_inicio and fecha_fin:
            query += " AND date(fecha) BETWEEN date(?) AND date(?)"
            params.extend([fecha_inicio, fecha_fin])
            
        if cajero and cajero != "Todos":
            query += " AND usuario = ?"
            params.append(cajero)
            
        cursor.execute(query, params)
        count = cursor.fetchone()[0]
        conn.close()
        return count

    def fetch_facturas_historial_reporte(
        self,
        *,
        fecha_desde: str | None = None,
        fecha_hasta: str | None = None,
        usuario: str | None = None,
        estado: str = "todos",
        texto: str | None = None,
        limit: int = 5000,
    ) -> list[tuple]:
        """
        Listado para reporte / historial de facturas (Reportería).
        estado: 'todos' | 'emitida' | 'anulada'
        Incluye cliente, formas de pago agregadas y montos.
        """
        conn = self.get_connection()
        cur = conn.cursor()
        q = """
            SELECT
                f.id,
                f.numero,
                f.fecha,
                COALESCE(c.nombre, 'Consumidor final'),
                COALESCE(c.documento, ''),
                f.usuario,
                f.tipo_comprobante,
                IFNULL(GROUP_CONCAT(DISTINCT p.tipo_pago), '') AS formas_pago,
                f.subtotal,
                f.descuento_total,
                f.impuesto_total,
                f.total,
                f.estado
            FROM facturas f
            LEFT JOIN clientes c ON c.id = f.cliente_id
            LEFT JOIN pagos_factura p ON p.factura_id = f.id
            WHERE 1=1
        """
        params: list = []

        fd = (fecha_desde or "").strip()
        fh = (fecha_hasta or "").strip()
        if fd:
            q += " AND date(f.fecha) >= date(?)"
            params.append(fd)
        if fh:
            q += " AND date(f.fecha) <= date(?)"
            params.append(fh)

        us = (usuario or "").strip()
        if us:
            q += " AND IFNULL(f.usuario, '') LIKE ?"
            params.append(f"%{us}%")

        ed = (estado or "todos").lower()
        if ed == "emitida":
            q += " AND LOWER(IFNULL(f.estado, '')) = 'emitida'"
        elif ed == "anulada":
            q += " AND LOWER(IFNULL(f.estado, '')) = 'anulada'"

        tx = (texto or "").strip()
        if tx:
            like = f"%{tx}%"
            digits = "".join(ch for ch in tx if ch.isdigit())
            parts = [
                "f.numero LIKE ?",
                "COALESCE(c.nombre, '') LIKE ?",
                "COALESCE(c.documento, '') LIKE ?",
            ]
            more = [like, like, like]
            if digits:
                parts.append(
                    "REPLACE(REPLACE(IFNULL(f.numero,''),'-',''),' ','') LIKE ?"
                )
                more.append(f"%{digits}%")
            q += " AND (" + " OR ".join(parts) + ")"
            params.extend(more)

        q += """
            GROUP BY
                f.id, f.numero, f.fecha, c.nombre, c.documento,
                f.usuario, f.tipo_comprobante,
                f.subtotal, f.descuento_total, f.impuesto_total, f.total, f.estado
            ORDER BY datetime(f.fecha) DESC, f.id DESC
            LIMIT ?
        """
        params.append(int(limit))

        cur.execute(q, params)
        rows = cur.fetchall()
        conn.close()
        return rows

    def get_lineas_factura_para_devolucion(self, factura_id: int) -> list[tuple]:
        """
        Por línea con producto: id detalle, descripción, cant. facturada,
        cant. aún disponible para devolver, producto_id.
        """
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT fd.id, fd.descripcion, fd.cantidad, fd.producto_id,
                   fd.cantidad - IFNULL((
                       SELECT SUM(ncd2.cantidad)
                       FROM notas_credito_detalle ncd2
                       JOIN notas_credito nc2 ON nc2.id = ncd2.nota_credito_id
                       WHERE ncd2.factura_detalle_id = fd.id
                         AND nc2.factura_original_id = fd.factura_id
                   ), 0) AS disponible
            FROM factura_detalle fd
            WHERE fd.factura_id = ? AND fd.producto_id IS NOT NULL
            ORDER BY fd.id
            """,
            (int(factura_id),),
        )
        rows = cur.fetchall()
        conn.close()
        out = []
        for rid, desc, cv, pid, disp in rows:
            d = float(disp or 0)
            if d > 0.0001:
                out.append((int(rid), desc or "", float(cv or 0), d, int(pid)))
        return out

    def list_condiciones_pago(self) -> list[tuple]:
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, codigo, nombre, dias_credito, es_contado
            FROM condiciones_pago
            ORDER BY es_contado DESC, dias_credito ASC, id ASC
            """
        )
        rows = cur.fetchall()
        conn.close()
        return rows

    def get_condicion_pago(self, condicion_id: int) -> tuple | None:
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, codigo, nombre, dias_credito, es_contado
            FROM condiciones_pago WHERE id = ?
            """,
            (int(condicion_id),),
        )
        row = cur.fetchone()
        conn.close()
        return row

    def buscar_clientes(self, texto: str, limit: int = 50) -> list[tuple]:
        q = (texto or "").strip()
        conn = self.get_connection()
        cur = conn.cursor()
        if not q:
            cur.execute(
                """
                SELECT id, nombre, documento, telefono
                FROM clientes
                ORDER BY nombre
                LIMIT ?
                """,
                (int(limit),),
            )
        else:
            like = f"%{q}%"
            cur.execute(
                """
                SELECT id, nombre, documento, telefono
                FROM clientes
                WHERE nombre LIKE ? OR IFNULL(documento,'') LIKE ?
                ORDER BY nombre
                LIMIT ?
                """,
                (like, like, int(limit)),
            )
        rows = cur.fetchall()
        conn.close()
        return rows

    def crear_cliente_rapido(
        self, nombre: str, documento: str | None = None, telefono: str | None = None
    ) -> int:
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO clientes (nombre, documento, telefono)
            VALUES (?, ?, ?)
            """,
            ((nombre or "").strip() or "Cliente", (documento or "").strip() or None, (telefono or "").strip() or None),
        )
        new_id = cur.lastrowid
        conn.commit()
        conn.close()
        return int(new_id)

    def get_factura_cliente_contacto(self, factura_id: int) -> tuple[str | None, str | None]:
        """Email y nombre del cliente vinculado a la factura (si existe)."""
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT TRIM(IFNULL(c.email,'')), TRIM(IFNULL(c.nombre,''))
            FROM facturas f
            LEFT JOIN clientes c ON c.id = f.cliente_id
            WHERE f.id = ?
            """,
            (int(factura_id),),
        )
        row = cur.fetchone()
        conn.close()
        if not row:
            return None, None
        em, nom = row
        return ((em or None), (nom or None))

    def actualizar_factura_notas(
        self,
        factura_id: int,
        observaciones: str | None,
        referencia_entrega: str | None = None,
    ) -> bool:
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE facturas
            SET observaciones = ?, referencia_entrega = ?
            WHERE id = ?
            """,
            (
                (observaciones or "").strip() or None,
                (referencia_entrega or "").strip() or None,
                int(factura_id),
            ),
        )
        conn.commit()
        conn.close()
        return cur.rowcount > 0

    def _cantidad_ya_devuelta_linea(self, cur, factura_detalle_id: int, factura_id: int) -> float:
        cur.execute(
            """
            SELECT IFNULL(SUM(ncd.cantidad), 0)
            FROM notas_credito_detalle ncd
            JOIN notas_credito nc ON nc.id = ncd.nota_credito_id
            WHERE ncd.factura_detalle_id = ?
              AND nc.factura_original_id = ?
            """,
            (int(factura_detalle_id), int(factura_id)),
        )
        r = cur.fetchone()
        return float(r[0] or 0)

    def registrar_devolucion_nota_credito(
        self,
        factura_id: int,
        lineas: list[tuple[int, float]],
        motivo: str,
        usuario: str | None,
    ) -> tuple[bool, str]:
        motivo = (motivo or "").strip()
        if len(motivo) < 3:
            return False, "Indique el motivo (mínimo 3 caracteres)."
        if not lineas:
            return False, "No hay líneas para devolver."

        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT IFNULL(estado,''), numero FROM facturas WHERE id = ?",
            (int(factura_id),),
        )
        row = cur.fetchone()
        if not row:
            conn.close()
            return False, "Factura no encontrada."
        estado, numero_fac = row
        if (estado or "").lower() != "emitida":
            conn.close()
            return False, "Solo se devuelve sobre facturas emitidas."

        detalles_proc = []
        monto_total = 0.0
        for det_id, qty_ret in lineas:
            qty_ret = float(qty_ret or 0)
            if qty_ret <= 0:
                continue
            cur.execute(
                """
                SELECT id, producto_id, cantidad, total_linea, descripcion
                FROM factura_detalle
                WHERE id = ? AND factura_id = ?
                """,
                (int(det_id), int(factura_id)),
            )
            dr = cur.fetchone()
            if not dr:
                conn.close()
                return False, f"Línea #{det_id} no pertenece a la factura."
            _id, pid, cant_orig, total_linea, desc = dr
            cant_orig = float(cant_orig or 0)
            if pid is None:
                conn.close()
                return False, "Las líneas sin producto no generan movimiento de inventario."
            ya = self._cantidad_ya_devuelta_linea(cur, int(det_id), int(factura_id))
            max_q = cant_orig - ya
            if qty_ret > max_q + 0.0001:
                conn.close()
                return False, f"Cantidad a devolver excede lo disponible en la línea ({max_q:.2f})."
            frac = qty_ret / cant_orig if cant_orig else 0
            monto_linea = float(total_linea or 0) * frac
            monto_total += monto_linea
            detalles_proc.append((int(det_id), int(pid), qty_ret, monto_linea, desc))

        if not detalles_proc:
            conn.close()
            return False, "Nada que procesar."

        from datetime import datetime

        nro = f"NC-{datetime.now().strftime('%Y%m%d%H%M%S')}"

        try:
            cur.execute(
                """
                INSERT INTO notas_credito (
                    factura_original_id, factura_nota_id, motivo, monto_total,
                    numero, usuario, estado
                )
                VALUES (?, NULL, ?, ?, ?, ?, 'emitida')
                """,
                (
                    int(factura_id),
                    motivo,
                    round(monto_total, 2),
                    nro,
                    (usuario or "").strip() or None,
                ),
            )
            nc_id = cur.lastrowid

            for det_id, pid, qty_ret, monto_linea, desc in detalles_proc:
                cur.execute(
                    """
                    INSERT INTO notas_credito_detalle (
                        nota_credito_id, factura_detalle_id, producto_id,
                        cantidad, monto
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        nc_id,
                        det_id,
                        pid,
                        qty_ret,
                        round(monto_linea, 2),
                    ),
                )
                cur.execute(
                    """
                    SELECT IFNULL(NULLIF(TRIM(bodega_codigo), ''), '')
                    FROM productos WHERE id = ?
                    """,
                    (pid,),
                )
                br = cur.fetchone()
                bod = (br[0] or "").strip() or None
                self.insert_movimiento_kardex(
                    pid,
                    "devolucion_cliente",
                    qty_ret,
                    ajustar_stock=True,
                    referencia=nro,
                    factura_id=int(factura_id),
                    usuario=usuario,
                    tipo_codigo="NC",
                    entidad_nombre=(desc or "")[:80],
                    bodega_codigo=bod,
                    precio_unitario=None,
                    descripcion_mov=f"Devolución {nro} — fact. {numero_fac}",
                    conn=conn,
                )

            conn.commit()
        except Exception as e:
            conn.rollback()
            conn.close()
            return False, str(e)
        conn.close()
        return True, f"Nota de crédito {nro} registrada. Inventario actualizado."

    def convertir_presupuesto_a_venta(
        self,
        factura_id: int,
        usuario: str | None,
        pagos: list[dict],
    ) -> tuple[bool, str]:
        """pagos: [{'tipo': 'efectivo', 'monto': 100.0}, ...]"""
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT IFNULL(estado,''), numero, total
            FROM facturas WHERE id = ?
            """,
            (int(factura_id),),
        )
        row = cur.fetchone()
        if not row:
            conn.close()
            return False, "Documento no encontrado."
        estado, numero, total = row
        if (estado or "").lower() != "cotizacion":
            conn.close()
            return False, "Solo los presupuestos pendientes pueden confirmarse como venta."

        cur.execute(
            """
            SELECT id, producto_id, descripcion, cantidad, precio_unitario
            FROM factura_detalle
            WHERE factura_id = ? AND producto_id IS NOT NULL
            """,
            (int(factura_id),),
        )
        det = cur.fetchall()
        if not det:
            conn.close()
            return False, "El presupuesto no tiene líneas con producto."

        try:
            for _did, pid, descripcion, cant, pu in det:
                qty = float(cant or 0)
                if qty <= 0:
                    continue
                cur.execute(
                    "UPDATE productos SET stock = IFNULL(stock,0) - ? WHERE id = ?",
                    (qty, int(pid)),
                )
                cur.execute(
                    """
                    SELECT IFNULL(NULLIF(TRIM(bodega_codigo), ''), '')
                    FROM productos WHERE id = ?
                    """,
                    (int(pid),),
                )
                br = cur.fetchone()
                bod = (br[0] or "").strip() or None
                self.insert_movimiento_kardex(
                    int(pid),
                    "venta",
                    -qty,
                    ajustar_stock=False,
                    referencia=str(numero),
                    factura_id=int(factura_id),
                    usuario=usuario,
                    tipo_codigo="FA",
                    entidad_nombre="Confirmación presupuesto",
                    bodega_codigo=bod,
                    precio_unitario=float(pu or 0),
                    descripcion_mov=f"Venta confirmada: {numero}",
                    conn=conn,
                )

            pago_sum = sum(float(p.get("monto") or 0) for p in pagos)
            if abs(pago_sum - float(total or 0)) > 0.05:
                conn.rollback()
                conn.close()
                return (
                    False,
                    f"Los pagos ({pago_sum:.2f}) deben igualar el total ({float(total):.2f}).",
                )

            for p in pagos:
                m = round(float(p.get("monto") or 0), 2)
                if m <= 0:
                    continue
                cur.execute(
                    """
                    INSERT INTO pagos_factura (factura_id, tipo_pago, monto)
                    VALUES (?, ?, ?)
                    """,
                    (int(factura_id), (p.get("tipo") or "efectivo").strip(), m),
                )

            cur.execute(
                "UPDATE facturas SET estado = 'emitida' WHERE id = ?",
                (int(factura_id),),
            )
            conn.commit()
        except Exception as e:
            conn.rollback()
            conn.close()
            return False, str(e)
        conn.close()
        return True, f"Presupuesto {numero} confirmado como venta."

    def importar_lote_presupuestos(
        self, entradas: list[dict], usuario: str | None
    ) -> tuple[int, list[str]]:
        """
        entradas: [{'grupo': '1', 'documento_cliente': 'RNC...', 'lineas': [(codigo, cant), ...]}, ...]
        Crea un presupuesto (sin movimiento de inventario) por cada entrada valida.
        """
        from datetime import datetime

        ok = 0
        errs: list[str] = []
        for entry in entradas:
            g = str(entry.get("grupo", "") or "?")
            doc = (entry.get("documento_cliente") or "").strip()
            lineas = entry.get("lineas") or []
            if not lineas:
                errs.append(f"Bloque {g}: sin lineas.")
                continue

            conn = self.get_connection()
            cur = conn.cursor()
            try:
                cliente_id = None
                if doc:
                    cur.execute(
                        "SELECT id FROM clientes WHERE TRIM(IFNULL(documento,'')) = ? LIMIT 1",
                        (doc,),
                    )
                    cr = cur.fetchone()
                    if not cr:
                        errs.append(f"Bloque {g}: cliente documento {doc} no encontrado.")
                        continue
                    cliente_id = cr[0]

                det_rows = []
                subtotal = 0.0
                imp = 0.0
                abort = False
                for cod, cant in lineas:
                    cod = (str(cod) or "").strip()
                    cant = float(cant or 0)
                    if not cod or cant <= 0:
                        continue
                    cur.execute(
                        """
                        SELECT id, nombre, precio, IFNULL(aplica_itbis,1)
                        FROM productos
                        WHERE TRIM(IFNULL(codigo_producto,'')) = ? OR CAST(id AS TEXT) = ?
                        LIMIT 1
                        """,
                        (cod, cod),
                    )
                    pr = cur.fetchone()
                    if not pr:
                        errs.append(f"Bloque {g}: producto {cod} no encontrado.")
                        abort = True
                        break
                    pid, nombre, precio, aplica_itb = pr
                    precio = float(precio or 0)
                    sb = round(precio * cant, 2)
                    it = round(sb * 0.18, 2) if int(aplica_itb or 1) else 0.0
                    tl = round(sb + it, 2)
                    subtotal += sb
                    imp += it
                    det_rows.append((pid, nombre, cant, precio, sb, it, tl))

                if abort or not det_rows:
                    continue

                nro = f"P-{datetime.now().strftime('%Y%m%d%H%M%S')}-{ok}-{g}"[:48]
                total = round(subtotal + imp, 2)
                cur.execute(
                    """
                    INSERT INTO facturas (
                        numero, tipo_comprobante, cliente_id, subtotal,
                        descuento_total, impuesto_total, total, estado, usuario,
                        condicion_pago_id, fecha_vencimiento, observaciones
                    )
                    VALUES (?, 'consumidor_final', ?, ?, 0, ?, ?, 'cotizacion', ?, NULL, NULL,
                            'Importacion en lote (presupuesto)')
                    """,
                    (
                        nro,
                        cliente_id,
                        round(subtotal, 2),
                        round(imp, 2),
                        total,
                        (usuario or "").strip() or None,
                    ),
                )
                fid = cur.lastrowid
                for pid, nombre, cant, precio, _sb, it, tl in det_rows:
                    cur.execute(
                        """
                        INSERT INTO factura_detalle (
                            factura_id, producto_id, descripcion, cantidad,
                            precio_unitario, descuento_item, impuesto_item, total_linea
                        )
                        VALUES (?, ?, ?, ?, ?, 0, ?, ?)
                        """,
                        (fid, pid, nombre, cant, precio, it, tl),
                    )
                conn.commit()
                ok += 1
            except Exception as e:
                conn.rollback()
                errs.append(f"Bloque {g}: {e}")
            finally:
                conn.close()
        return ok, errs


    def list_facturas_modulo_erp(
        self,
        estado_docs: str = "todos",
        modo_filtro: str = "fecha",
        numero_buscar: str | None = None,
        fecha_desde: str | None = None,
        fecha_hasta: str | None = None,
        cliente_buscar: str | None = None,
        monto_min: float | None = None,
        monto_max: float | None = None,
        producto_buscar: str | None = None,
        terminos_modo: str | None = None,
        bodega_filtro: str | None = None,
        ultimos_n: int | None = None,
        limit: int = 5000,
    ):
        """
        Listado módulo Facturación (vista tipo ERP / MONICA).
        estado_docs: 'todos' | 'emitidas' | 'anuladas' | 'presupuestos'
        modo_filtro: 'fecha' | 'numero' | 'cliente' | 'monto' | 'producto' | 'terminos' | 'bodega' | 'todos'
        terminos_modo: 'efectivo' | 'credito' (solo si modo_filtro=='terminos')
        ultimos_n: si > 0, limita a los N documentos más recientes (ignora otros criterios opcionales salvo estado).

        Cada fila: id, numero, fecha, cliente_nombre, documento_cliente, total, estado,
        usuario, suma_pagos, fecha_vencimiento, moneda.
        """
        conn = self.get_connection()
        cur = conn.cursor()
        q = """
            SELECT f.id, f.numero, f.fecha,
                   COALESCE(c.nombre, 'Consumidor final'),
                   COALESCE(c.documento, ''),
                   f.total, f.estado, f.usuario,
                   IFNULL((SELECT SUM(monto) FROM pagos_factura p
                           WHERE p.factura_id = f.id), 0),
                   IFNULL(f.fecha_vencimiento, ''),
                   IFNULL(f.moneda, 'DOP')
            FROM facturas f
            LEFT JOIN clientes c ON c.id = f.cliente_id
            WHERE 1=1
        """
        params: list = []
        ed = (estado_docs or "todos").lower()
        if ed == "emitidas":
            q += " AND IFNULL(f.estado,'') = 'emitida'"
        elif ed == "anuladas":
            q += " AND IFNULL(f.estado,'') = 'anulada'"
        elif ed == "presupuestos":
            q += " AND IFNULL(f.estado,'') = 'cotizacion'"

        modo = (modo_filtro or "todos").lower()
        if ultimos_n and int(ultimos_n) > 0:
            q += " ORDER BY datetime(f.fecha) DESC, f.id DESC LIMIT ?"
            params.append(int(ultimos_n))
            cur.execute(q, params)
            rows = cur.fetchall()
            conn.close()
            return rows

        fd = (fecha_desde or "").strip()
        fh = (fecha_hasta or "").strip()
        if modo == "fecha" and fd and fh:
            q += " AND date(f.fecha) >= date(?) AND date(f.fecha) <= date(?)"
            params.extend([fd, fh])

        nb = (numero_buscar or "").strip()
        if modo == "numero" and nb:
            digits = "".join(ch for ch in nb if ch.isdigit())
            if digits:
                q += " AND REPLACE(REPLACE(IFNULL(f.numero,''),'-',''),' ','') LIKE ?"
                params.append(f"%{digits}%")
            else:
                q += " AND f.numero LIKE ?"
                params.append(f"%{nb}%")

        cb = (cliente_buscar or "").strip()
        if modo == "cliente" and cb:
            like = f"%{cb}%"
            q += (
                " AND (COALESCE(c.nombre,'') LIKE ? OR COALESCE(c.documento,'') LIKE ?)"
            )
            params.extend([like, like])

        if modo == "monto":
            if monto_min is not None:
                q += " AND f.total >= ?"
                params.append(float(monto_min))
            if monto_max is not None:
                q += " AND f.total <= ?"
                params.append(float(monto_max))

        pb = (producto_buscar or "").strip()
        if modo == "producto" and pb:
            like = f"%{pb}%"
            q += """
                AND EXISTS (
                    SELECT 1 FROM factura_detalle fd
                    LEFT JOIN productos p ON p.id = fd.producto_id
                    WHERE fd.factura_id = f.id
                      AND (IFNULL(fd.descripcion,'') LIKE ?
                           OR IFNULL(p.nombre,'') LIKE ?)
                )
            """
            params.extend([like, like])

        tm = (terminos_modo or "").lower()
        if modo == "terminos" and tm == "efectivo":
            q += """
                AND (f.total - IFNULL((SELECT SUM(monto) FROM pagos_factura px
                     WHERE px.factura_id = f.id), 0)) <= 0.02
            """
        elif modo == "terminos" and tm == "credito":
            q += """
                AND (f.total - IFNULL((SELECT SUM(monto) FROM pagos_factura px
                     WHERE px.factura_id = f.id), 0)) > 0.02
            """

        bf = (bodega_filtro or "").strip()
        if modo == "bodega" and bf and bf.upper() not in ("TODOS", "TODAS"):
            q += """
                AND EXISTS (
                    SELECT 1 FROM factura_detalle fd2
                    JOIN productos p2 ON p2.id = fd2.producto_id
                    WHERE fd2.factura_id = f.id
                      AND TRIM(IFNULL(p2.bodega_codigo,'')) = ?
                )
            """
            params.append(bf)

        q += " ORDER BY datetime(f.fecha) DESC LIMIT ?"
        params.append(int(limit))
        cur.execute(q, params)
        rows = cur.fetchall()
        conn.close()
        return rows

    def get_factura_cobro_resumen(self, factura_id: int) -> dict | None:
        """Totales de cobro para diálogo 'Pagar documento'."""
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT numero, total, estado,
                   IFNULL((SELECT SUM(monto) FROM pagos_factura p
                           WHERE p.factura_id = f.id), 0)
            FROM facturas f WHERE f.id = ?
            """,
            (int(factura_id),),
        )
        row = cur.fetchone()
        conn.close()
        if not row:
            return None
        numero, total, estado, pagado = row
        total = float(total or 0)
        pagado = float(pagado or 0)
        balance = round(total - pagado, 2)
        return {
            "numero": numero,
            "total": total,
            "pagado": pagado,
            "balance": balance,
            "estado": estado or "",
        }

    def fetch_caja_abierta_row(self) -> tuple | None:
        """Fila del turno de caja abierto (misma forma que usa CajaManager)."""
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, nombre_caja, fecha_apertura, fecha_cierre,
                   usuario_apertura, usuario_cierre,
                   monto_inicial, total_ventas,
                   total_efectivo_sistema, total_tarjeta_sistema,
                   total_otros_sistema, efectivo_contado,
                   diferencia_efectivo, observaciones, estado
            FROM cierres_caja
            WHERE estado = 'abierto'
            ORDER BY fecha_apertura DESC
            LIMIT 1
            """
        )
        row = cur.fetchone()
        conn.close()
        return row

    def get_factura_para_duplicar(self, factura_id: int) -> dict | None:
        """
        Datos para abrir el POS con las mismas líneas (solo ítems con producto_id).
        Documentos en estado emitida o cotización (presupuesto).
        """
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT f.estado, f.tipo_comprobante, f.cliente_id,
                   c.documento, c.nombre
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
        estado, tipo_comp, cliente_id, doc, nombre = row
        if (estado or "").lower() not in ("emitida", "cotizacion"):
            conn.close()
            return None
        cur.execute(
            """
            SELECT producto_id, descripcion, cantidad, precio_unitario,
                   descuento_item, impuesto_item, total_linea
            FROM factura_detalle
            WHERE factura_id = ?
            ORDER BY id
            """,
            (int(factura_id),),
        )
        det_rows = cur.fetchall()
        conn.close()
        lines = []
        for r in det_rows:
            pid, descripcion, cantidad, punit, ditem, imp, tline = r
            if pid is None:
                continue
            lines.append(
                {
                    "producto_id": int(pid),
                    "descripcion": descripcion or "",
                    "cantidad": float(cantidad or 0),
                    "precio_unitario": float(punit or 0),
                    "descuento_item": float(ditem or 0),
                    "impuesto_item": float(imp or 0),
                    "total_linea": float(tline or 0),
                }
            )
        if not lines:
            return None
        labels = {
            "consumidor_final": "Consumidor final",
            "credito_fiscal": "Crédito fiscal",
            "gubernamental": "Gubernamental",
            "especial": "Especial",
        }
        comp_label = labels.get((tipo_comp or "").lower(), "Consumidor final")
        if cliente_id:
            codigo_cli = (doc or "").strip() or (nombre or "").strip() or "MOSTRADOR"
        else:
            codigo_cli = "MOSTRADOR"
        return {
            "cliente_id": int(cliente_id) if cliente_id else None,
            "cliente_codigo": codigo_cli,
            "documento_cliente": (doc or "").strip(),
            "comprobante_label": comp_label,
            "lines": lines,
        }

    def registrar_pago_factura(
        self, factura_id: int, tipo_pago: str, monto: float
    ) -> tuple[bool, str]:
        """Registra un pago. Devuelve (ok, mensaje)."""
        if monto <= 0:
            return False, "El monto debe ser mayor que cero."
        res = self.get_factura_cobro_resumen(factura_id)
        if not res:
            return False, "Factura no encontrada."
        if (res["estado"] or "").lower() == "anulada":
            return False, "No se puede cobrar una factura anulada."
        if (res["estado"] or "").lower() == "cotizacion":
            return False, "Confirme el presupuesto como venta desde Facturación (acción «Confirmar venta»)."
        if res["balance"] <= 0:
            return False, "La factura ya está saldada."
        if round(monto, 2) > res["balance"] + 0.009:
            return False, f"El monto supera el balance ({res['balance']:.2f})."
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO pagos_factura (factura_id, tipo_pago, monto)
            VALUES (?, ?, ?)
            """,
            (int(factura_id), tipo_pago, round(float(monto), 2)),
        )
        conn.commit()
        conn.close()
        return True, "Pago registrado."

    def anular_factura(
        self, factura_id: int, motivo: str, usuario: str | None
    ) -> tuple[bool, str]:
        """
        Marca la factura como anulada y devuelve al inventario las cantidades vendidas.
        """
        motivo = (motivo or "").strip()
        if len(motivo) < 3:
            return False, "Ingrese el motivo de la anulación (mínimo 3 caracteres)."

        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT IFNULL(estado,''), numero FROM facturas WHERE id = ?",
            (int(factura_id),),
        )
        row = cur.fetchone()
        if not row:
            conn.close()
            return False, "Factura no encontrada."
        estado, numero = row
        est = (estado or "").lower()
        if est == "anulada":
            conn.close()
            return False, "El documento ya está anulado."
        if est not in ("emitida", "cotizacion"):
            conn.close()
            return False, "Estado de documento no admite anulación así."

        cur.execute(
            """
            SELECT producto_id, cantidad
            FROM factura_detalle
            WHERE factura_id = ? AND producto_id IS NOT NULL
            """,
            (int(factura_id),),
        )
        detalles = cur.fetchall()

        try:
            if est == "cotizacion":
                cur.execute(
                    """
                    UPDATE facturas
                    SET estado = 'anulada',
                        anulacion_motivo = ?,
                        anulacion_usuario = ?,
                        anulacion_fecha = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (motivo, (usuario or "").strip() or None, int(factura_id)),
                )
                conn.commit()
                conn.close()
                return True, f"Presupuesto {numero} cancelado (sin movimiento de inventario)."

            for pid, cant in detalles:
                qty = float(cant or 0)
                if qty <= 0:
                    continue
                cur.execute(
                    """
                    SELECT IFNULL(NULLIF(TRIM(bodega_codigo), ''), '')
                    FROM productos WHERE id = ?
                    """,
                    (int(pid),),
                )
                br = cur.fetchone()
                bod = (br[0] or "").strip() or None
                self.insert_movimiento_kardex(
                    int(pid),
                    "anulacion_venta",
                    qty,
                    ajustar_stock=True,
                    referencia=str(numero),
                    factura_id=int(factura_id),
                    usuario=usuario,
                    tipo_codigo="AN",
                    entidad_nombre="Anulación de factura",
                    bodega_codigo=bod,
                    precio_unitario=None,
                    descripcion_mov=(
                        f"Anulación factura {numero}: {motivo[:120]}"
                    ),
                    conn=conn,
                )

            cur.execute(
                """
                UPDATE facturas
                SET estado = 'anulada',
                    anulacion_motivo = ?,
                    anulacion_usuario = ?,
                    anulacion_fecha = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (motivo, (usuario or "").strip() or None, int(factura_id)),
            )
            conn.commit()
        except Exception as e:
            conn.rollback()
            conn.close()
            return False, str(e)
        conn.close()
        return True, f"Factura {numero} anulada. El inventario fue revertido."

    def generar_ticket_texto_factura(self, factura_id: int) -> str | None:
        """Texto del ticket térmico reconstruido desde BD (misma idea que historial)."""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT numero, fecha, subtotal, descuento_total, impuesto_total, total, usuario,
                   IFNULL(estado,''), IFNULL(anulacion_motivo,'')
            FROM facturas
            WHERE id = ?
            """,
            (int(factura_id),),
        )
        row = cursor.fetchone()
        if not row:
            conn.close()
            return None
        (
            numero,
            fecha,
            subtotal,
            descuento_total,
            impuesto_total,
            total,
            usuario,
            estado_fac,
            motivo_anul,
        ) = row
        cursor.execute(
            """
            SELECT descripcion, cantidad, precio_unitario, total_linea
            FROM factura_detalle
            WHERE factura_id = ?
            """,
            (int(factura_id),),
        )
        detalles = cursor.fetchall()
        conn.close()

        emp = self.get_empresa_info()
        nombre_emp = (emp.get("nombre") or "EMPRESA").strip()
        dir_emp = (emp.get("direccion") or "").strip().split("\n")[0] if emp.get("direccion") else ""

        subtotal = float(subtotal or 0)
        descuento_total = float(descuento_total or 0)
        impuesto_total = float(impuesto_total or 0)
        total = float(total or 0)
        subtotal_bruto = subtotal + descuento_total

        ticket_width = self.get_ticket_width()
        lines = []

        def center(text):
            return text[:ticket_width].center(ticket_width)

        def sep(char="-"):
            return char * ticket_width

        lines.append(center(nombre_emp[:40]))
        if dir_emp:
            lines.append(center(dir_emp[:ticket_width]))
        lines.append(sep())
        if (estado_fac or "").lower() == "anulada":
            lines.append(center("*** DOCUMENTO ANULADO ***"))
            if (motivo_anul or "").strip():
                m = str(motivo_anul).strip()
                while m:
                    lines.append(center(m[:ticket_width]))
                    m = m[ticket_width:].lstrip()
            lines.append(sep())
        lines.append(f"Factura: {numero}")
        lines.append(f"Fecha : {fecha}")
        if usuario:
            lines.append(f"Cajero: {usuario}")
        lines.append(sep())
        lines.append("DESCRIPCIÓN")
        lines.append("CANT x P.U" + " " * max(1, ticket_width - len("CANT x P.U") - 7) + "IMPORTE")
        lines.append(sep())

        for desc, cant, pu, total_linea in detalles:
            desc = str(desc)
            while len(desc) > ticket_width:
                lines.append(desc[:ticket_width])
                desc = desc[ticket_width:]
            if desc:
                lines.append(desc)
            left = f"{float(cant):.2f} x {float(pu):.2f}"
            right = f"{float(total_linea):.2f}"
            spaces = max(1, ticket_width - len(left) - len(right))
            lines.append(left + " " * spaces + right)

        lines.append(sep())
        lines.append(f"SUBTOTAL:".ljust(ticket_width - 10) + f"{subtotal_bruto:10.2f}")
        lines.append(f"DESCUENTO:".ljust(ticket_width - 10) + f"{descuento_total:10.2f}")
        if impuesto_total and abs(impuesto_total) > 0.0001:
            lines.append(f"ITBIS:".ljust(ticket_width - 10) + f"{impuesto_total:10.2f}")
        lines.append(f"TOTAL:".ljust(ticket_width - 10) + f"{total:10.2f}")
        lines.append(sep())
        lines.append(center("GRACIAS POR SU COMPRA"))
        lines.append("\n\n\n")
        return "\n".join(lines)

    # --------- PERFIL DE IMPRESORA ---------
    def get_printer_profile(self) -> tuple[str, int]:
        """
        Devuelve (profile, width)

        profile: 'movil_58' o 'epson_80'
        width: número de caracteres por línea
        """
        profile = self.get_config("printer_profile", None)
        if profile not in ("movil_58", "epson_80"):
            profile = "movil_58"

        if profile == "epson_80":
            # Para 80mm, si no hay valor, usamos ticket_width_chars o 42
            width_str = self.get_config(
                "printer_width_epson_80",
                self.get_config("ticket_width_chars", "42")
            )
        else:
            # Para 58mm, si no hay valor, usamos ticket_width_chars o 32
            width_str = self.get_config(
                "printer_width_movil_58",
                self.get_config("ticket_width_chars", "32")
            )

        try:
            width = int(width_str)
        except (TypeError, ValueError):
            width = 42 if profile == "epson_80" else 32

        return profile, width

    def set_printer_profile(
        self,
        profile: str,
        width_movil_58: int = None,
        width_epson_80: int = None
    ):
        """
        Guarda el perfil activo y, opcionalmente, los anchos para cada impresora.
        profile: 'movil_58' o 'epson_80'
        """
        if profile not in ("movil_58", "epson_80"):
            profile = "movil_58"

        self.set_config("printer_profile", profile)

        if width_movil_58 is not None:
            self.set_config("printer_width_movil_58", str(int(width_movil_58)))

        if width_epson_80 is not None:
            self.set_config("printer_width_epson_80", str(int(width_epson_80)))

    def get_ticket_width(self, default: int = 32) -> int:
        """
        Devuelve el ancho de ticket según el perfil de impresora activo.
        Se mantiene el parámetro 'default' para compatibilidad,
        pero normalmente no hace falta usarlo.
        """
        _, width = self.get_printer_profile()
        if width <= 0:
            return default
        return width

    # ==========================
    #   BORRADO DE HISTORIAL
    # ==========================
    def clear_billing_history(self):
        """
        Borra absolutamente todo el historial de facturación para
        iniciar desde 0. Esto incluye:
        facturas, factura_detalle, pagos_factura,
        log_items_factura, notas_credito, notas_credito_detalle,
        movimientos_inventario (asociados a ventas/devoluciones), 
        cierres_caja.
        """
        conn = self.get_connection()
        cursor = conn.cursor()
        
        try:
            # Eliminar datos de tablas de facturación y movimientos
            cursor.execute("DELETE FROM factura_detalle")
            cursor.execute("DELETE FROM pagos_factura")
            cursor.execute("DELETE FROM log_items_factura")
            cursor.execute("DELETE FROM notas_credito_detalle")
            cursor.execute("DELETE FROM notas_credito")
            cursor.execute("DELETE FROM movimientos_inventario WHERE tipo_movimiento IN ('venta', 'devolucion', 'ajuste', 'ingreso') AND factura_id IS NOT NULL")
            # Eliminar también movimientos donde factura_id sea NULL si es que se quiere borrar TODO el historial de kardex,
            # pero el requerimiento es borrar historial de *facturación*, e iniciar desde 0. 
            # Borrar todos los movimientos tiene sentido para un reinicio completo del sistema de transacciones.
            # Según el prompt: "iniciar desde 0 con un nuevo cajero o un nuevo inventario" 
            # Borramos todos los movimientos de inventario:
            cursor.execute("DELETE FROM movimientos_inventario")
            
            cursor.execute("DELETE FROM facturas")
            cursor.execute("DELETE FROM cierres_caja")

            # Reiniciar secuencias de auto-incrementos (para sqlite)
            tablas_a_resetear = [
                'factura_detalle', 'pagos_factura', 'log_items_factura',
                'notas_credito_detalle', 'notas_credito', 'movimientos_inventario',
                'facturas', 'cierres_caja'
            ]
            
            for tabla in tablas_a_resetear:
                cursor.execute("UPDATE sqlite_sequence SET seq = 0 WHERE name = ?", (tabla,))

            conn.commit()
            return True, "Historial de facturación borrado con éxito."
            
        except Exception as e:
            conn.rollback()
            return False, f"Error al borrar el historial: {str(e)}"
            
        finally:
            conn.close()


# Test de la base de datos
if __name__ == "__main__":
    db = Database()
