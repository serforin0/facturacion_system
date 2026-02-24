import sqlite3
import os
from datetime import datetime


class Database:
    def __init__(self, db_name="bar_inventory.db"):
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

        conn.commit()
        conn.close()
        print("✅ Base de datos inicializada correctamente")

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

    def update_user(self, user_id: int, username: str, password: str | None, role: str):
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
    def get_config(self, clave: str, default: str | None = None) -> str | None:
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
        width_movil_58: int | None = None,
        width_epson_80: int | None = None
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


# Test de la base de datos
if __name__ == "__main__":
    db = Database()
