from database import Database

def agregar_productos_iniciales():
    db = Database()
    conn = db.get_connection()
    cursor = conn.cursor()
    
    # Productos a agregar
    productos = [
        {
            'nombre': 'Presidente (regular) 355ml',
            'descripcion': 'Cerveza Presidente regular 355 ml (≈12 oz)',
            'precio': 125.00,
            'stock': 100,
            'categoria': 'Cerveza',
            'stock_minimo': 10
        },
        {
            'nombre': 'Presidente (regular) 650ml',
            'descripcion': 'Cerveza Presidente regular 650 ml / 22 oz',
            'precio': 145.00,
            'stock': 100,
            'categoria': 'Cerveza',
            'stock_minimo': 10
        },
        {
            'nombre': 'Presidente Light Lata 8oz',
            'descripcion': 'Presidente Light (lata 8 oz)',
            'precio': 67.00,
            'stock': 100,
            'categoria': 'Cerveza',
            'stock_minimo': 10
        },
        {
            'nombre': 'Presidente Light Lata 12oz',
            'descripcion': 'Presidente Light (lata 12 oz)',
            'precio': 104.00,
            'stock': 100,
            'categoria': 'Cerveza',
            'stock_minimo': 10
        },
        {
            'nombre': 'República La Tuya 330ml',
            'descripcion': 'Marca República La Tuya 330 ml (botella)',
            'precio': 85.00,
            'stock': 100,
            'categoria': 'Cerveza',
            'stock_minimo': 10
        },
        {
            'nombre': 'República La Tuya Lata 10oz',
            'descripcion': 'República La Tuya (lata 10 oz)',
            'precio': 65.00,
            'stock': 100,
            'categoria': 'Cerveza',
            'stock_minimo': 10
        }
    ]
    
    try:
        for producto in productos:
            # Obtener ID de la categoría
            cursor.execute("SELECT id FROM categorias WHERE nombre = ?", 
                         (producto['categoria'],))
            categoria_id = cursor.fetchone()[0]
            
            # Verificar si el producto ya existe
            cursor.execute("SELECT id FROM productos WHERE nombre = ?", 
                         (producto['nombre'],))
            existe = cursor.fetchone()
            
            if not existe:
                # Insertar producto
                cursor.execute('''
                    INSERT INTO productos (nombre, descripcion, precio, stock, categoria_id, stock_minimo)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (
                    producto['nombre'],
                    producto['descripcion'],
                    producto['precio'],
                    producto['stock'],
                    categoria_id,
                    producto['stock_minimo']
                ))
                print(f"✅ Agregado: {producto['nombre']}")
            else:
                print(f"⚠️ Ya existe: {producto['nombre']}")
        
        conn.commit()
        print("\n🎉 Todos los productos han sido agregados exitosamente!")
        
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        conn.rollback()
    
    finally:
        conn.close()

if __name__ == "__main__":
    agregar_productos_iniciales()