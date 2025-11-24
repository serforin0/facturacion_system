import sqlite3
import os
from datetime import datetime

class Database:
    def __init__(self, db_name="bar_inventory.db"):
        self.db_name = db_name
        self.init_database()
    
    def get_connection(self):
        return sqlite3.connect(self.db_name)
    
    def init_database(self):
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # Tabla de categorías
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS categorias (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre TEXT UNIQUE NOT NULL
            )
        ''')
        
        # Tabla de productos - AGREGADO CAMPO codigo_barras
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS productos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre TEXT NOT NULL,
                descripcion TEXT,
                precio REAL NOT NULL,
                stock INTEGER NOT NULL DEFAULT 0,
                categoria_id INTEGER,
                stock_minimo INTEGER DEFAULT 5,
                imagen_path TEXT,        -- Ruta de la imagen
                codigo_barras TEXT,      -- NUEVO: código de barras
                activo BOOLEAN DEFAULT 1,
                fecha_creacion TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (categoria_id) REFERENCES categorias (id)
            )
        ''')
        
        # ✅ Verificar si la columna 'codigo_barras' existe
        cursor.execute("PRAGMA table_info(productos)")
        columnas = [col[1] for col in cursor.fetchall()]
        
        if "codigo_barras" not in columnas:
            # No se puede agregar con UNIQUE → se agrega simple
            cursor.execute("ALTER TABLE productos ADD COLUMN codigo_barras TEXT")
            print("✅ Columna 'codigo_barras' añadida a la tabla productos")

        # Insertar categorías por defecto
        categorias = ['Cerveza', 'Vino', 'Licor', 'Refresco', 'Agua', 'Otros']
        for categoria in categorias:
            cursor.execute(
                "INSERT OR IGNORE INTO categorias (nombre) VALUES (?)", 
                (categoria,)
            )
        
        conn.commit()
        conn.close()
        print("✅ Base de datos inicializada correctamente")

# Test de la base de datos
if __name__ == "__main__":
    db = Database()
