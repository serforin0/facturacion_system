"""Carga el conteo de la barra: costo de mercado en precio_base y precio de venta en cero."""
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from io import BytesIO

from PIL import Image

from database import Database
from inventario_barra import PRODUCTOS

CARPETA = os.path.join("web_static", "barra")
AGENTE = "FacturacionBarra/1.0 (inventario local de barra)"


def _categoria(cur, nombre: str) -> int:
    cur.execute("SELECT id FROM categorias WHERE nombre=?", (nombre,))
    fila = cur.fetchone()
    if fila:
        return int(fila[0])
    cur.execute("INSERT INTO categorias (nombre) VALUES (?)", (nombre,))
    return int(cur.lastrowid)


GENERICOS = {
    "bottle", "bottles", "botella", "can", "lata", "glass", "ml", "the", "and",
    "with", "photograph", "photo", "sparkling", "liter", "litre",
    "gallon", "punnet", "paquete", "vodka", "whisky", "whiskey", "tequila",
    "water", "rum", "gin", "beer", "liqueur", "cognac", "brandy", "amaro",
    "syrup", "juice", "soda", "ron", "licor", "cerveza", "agua",
}
VARIANTES = {
    "vanilia", "vanilla", "citron", "raspberry", "raspberri", "mandrin", "mandarin",
    "citrus", "poire", "orange", "lemon", "pear", "apple", "coffee", "cafe", "gold",
    "blue", "rosso", "bianco", "rose", "dry", "anejo", "reposado", "blanco", "silver",
    "razz", "titanium", "onyx", "leyenda", "siglo", "imperial", "premium", "light",
    "zero", "brune", "blonde", "negra", "xo", "vsop", "melograno", "aranciata",
    "essenza", "honey", "extra", "viejo", "menta", "dulce", "seco", "1888",
    "fino", "medium", "especial", "peach", "cherry", "mango", "lime",
}


def _normalizar(texto: str) -> str:
    texto = texto.lower()
    for origen, destino in (
        ("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u"),
        ("ñ", "n"), ("ü", "u"), ("î", "i"), ("ì", "i"),
    ):
        texto = texto.replace(origen, destino)
    texto = texto.replace("'", " ").replace("’", " ").replace("`", " ")
    texto = re.sub(r"\.(?:jpe?g|png|webp|gif|tiff?|svg)\b", " ", texto)
    texto = re.sub(r"\.(?=[a-z])", "", texto)
    return texto.replace("-", " ").replace(".", " ")


def _tokens(texto: str) -> set[str]:
    base = _normalizar(texto)
    palabras = set(re.findall(r"[a-z0-9]{3,}", base))
    palabras.update(re.findall(r"\b(?:xo|vs)\b", base))
    return palabras


def _cubre(esperados: set[str], hallados: set[str]) -> bool:
    pegados = "".join(hallados)
    for token in esperados:
        if token in hallados:
            continue
        if len(token) >= 5 and token in pegados:
            continue
        return False
    return True


def _puntaje(titulo: str, consulta: str) -> int:
    plano = _normalizar(titulo)
    if "coat of arms" in plano or any(
        re.search(rf"\b{malo}\b", plano)
        for malo in (
            "logo", "icon", "flag", "map", "cartoon", "drawing", "illustration",
            "clipart", "pictogram", "symbol", "wordmark", "shelf", "svg",
            "collection", "collage", "assortment", "lineup", "geograph",
            "cordillera", "panorama", "landscape", "fresco", "painting",
            "museo", "museum", "cathedral", "church", "terme", "ponte",
            "bridge", "grower", "farmer", "harvest", "advertisement",
            "poster", "billboard", "ad", "scorcio", "emilia", "piazza",
            "plaza", "village", "duomo", "basilica", "street", "houses",
            "mojito", "cocktail", "preparado", "decapsuleur", "opener", "milk",
            "car", "distillerie", "distillery", "nara", "playa", "beach",
        )
    ):
        return -1
    if re.search(r"\b\d{4}\s\d{2}\s\d{2}\b", plano) or len(titulo) > 110:
        return -1
    esperados = _tokens(consulta) - GENERICOS
    hallados = _tokens(titulo)
    if not esperados or not _cubre(esperados, hallados):
        return -1
    orden = [
        palabra for palabra in re.findall(r"[a-z0-9]+", _normalizar(consulta))
        if palabra in esperados
    ]
    if len(orden) >= 2:
        patron = r".{0,18}".join(re.escape(token) for token in orden)
        plano_espaciado = re.sub(r"[^a-z0-9]+", " ", plano)
        if not (
            re.search(patron, plano_espaciado)
            or re.search(patron, plano_espaciado.replace(" ", ""))
        ):
            return -1
    if _tokens(consulta) & {
        "bottle", "can", "water", "beer", "rum", "vodka", "whisky", "whiskey",
        "gin", "tequila", "liqueur", "cognac", "brandy", "juice", "soda", "ron",
    } and not (hallados & {
        "bottle", "botella", "can", "lata", "vodka", "whisky", "whiskey", "rum",
        "gin", "beer", "tequila", "water", "liqueur", "cognac", "brandy",
        "cerveza", "agua", "soda", "juice", "ron", "licor", "jugo",
    }):
        return -1
    if {"brandy", "cognac"} & _tokens(consulta) and not (
        {"brandy", "cognac", "conac", "bottle", "botella"} & hallados
    ):
        return -1
    if len(orden) < 2 and orden and orden[0] not in {"apple", "strawberry", "fresa", "chinola", "manzana", "pulpa"}:
        contexto = {
            "bottle", "botella", "can", "lata", "vodka", "whisky", "whiskey",
            "rum", "gin", "beer", "tequila", "water", "liqueur", "cognac",
            "brandy", "cerveza", "agua", "soda", "juice", "ron", "licor",
        }
        if not (hallados & contexto):
            return -1
    if (hallados & VARIANTES) - _tokens(consulta):
        return -1
    edades = {"7", "8", "10", "12", "13", "15", "18", "21", "23", "1888"}
    numeros_consulta = set(re.findall(r"\d+", consulta)) & edades
    numeros_titulo = set(re.findall(r"\d+", titulo)) & edades
    if numeros_consulta and not numeros_consulta <= numeros_titulo:
        return -1
    for edad in numeros_titulo - numeros_consulta:
        if re.search(
            rf"(\b{edad}\b\W{{0,8}}(?:year|years|ano|anos|yo|old)\b)"
            rf"|(\b(?:year|years|ano|anos|yo|old)\b\W{{0,8}}\b{edad}\b)",
            _normalizar(titulo),
        ):
            return -1
    nota = len(esperados) * 10
    if {"bottle", "botella", "can", "lata"} & hallados:
        nota += 8
    return nota


def _foto(consulta: str, destino: str) -> bool:
    if os.path.isfile(destino) and os.path.getsize(destino) > 2000:
        return True
    busqueda = " ".join(
        palabra for palabra in consulta.split()
        if _normalizar(palabra).strip(".,") not in GENERICOS
    ) or consulta
    url = "https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode(
        {
            "action": "query",
            "format": "json",
            "generator": "search",
            "gsrsearch": f"filetype:bitmap {busqueda}",
            "gsrnamespace": "6",
            "gsrlimit": "10",
            "prop": "imageinfo",
            "iiprop": "url|mime|extmetadata",
            "iiextmetadatafilter": "ObjectName",
            "iiurlwidth": "640",
        }
    )
    req = urllib.request.Request(url, headers={"User-Agent": AGENTE})
    data = None
    for intento in range(5):
        try:
            with urllib.request.urlopen(req, timeout=25) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as exc:
            if exc.code != 429 or intento == 4:
                raise
            time.sleep(30 + intento * 15)
    if not data:
        return False
    paginas = (data.get("query") or {}).get("pages") or {}
    candidatos = []
    for pagina in paginas.values():
        info = (pagina.get("imageinfo") or [None])[0] or {}
        mime = info.get("mime") or ""
        if mime not in ("image/jpeg", "image/png", "image/webp", "image/tiff"):
            continue
        enlace = info.get("thumburl") or info.get("url")
        meta = info.get("extmetadata") or {}
        nombre_objeto = re.sub(r"<[^>]+>", " ", (meta.get("ObjectName") or {}).get("value") or "")
        nota = _puntaje(f"{pagina.get('title') or ''} {nombre_objeto}", consulta)
        if enlace and nota > 0:
            candidatos.append((nota, enlace, pagina.get("title") or ""))
    if not candidatos:
        return False
    _, enlace, titulo = max(candidatos, key=lambda item: item[0])
    img = urllib.request.Request(enlace, headers={"User-Agent": AGENTE})
    with urllib.request.urlopen(img, timeout=25) as bajada:
        cuerpo = bajada.read()
    if len(cuerpo) < 2000:
        return False
    foto = Image.open(BytesIO(cuerpo))
    foto = foto.convert("RGB")
    foto.thumbnail((640, 640))
    foto.save(destino, "JPEG", quality=82)
    print("foto", os.path.basename(destino), titulo, flush=True)
    return True


def cargar(con_fotos: bool = True) -> None:
    os.makedirs(CARPETA, exist_ok=True)
    db = Database()
    conn = db.get_connection()
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO bodegas (codigo) VALUES ('BARRA')")
    nuevos = 0
    fotos = 0
    sin_foto = []
    for codigo, unidad, nombre, stock, costo, categoria, consulta in PRODUCTOS:
        cat_id = _categoria(cur, categoria)
        cur.execute("SELECT id, precio FROM productos WHERE codigo_producto=?", (codigo,))
        existe = cur.fetchone()
        ruta = os.path.join(CARPETA, f"{codigo}.jpg").replace("\\", "/")
        imagen = ""
        if con_fotos:
            try:
                ya_estaba = os.path.isfile(ruta) and os.path.getsize(ruta) > 2000
                if _foto(consulta, ruta):
                    imagen = ruta
                    fotos += 1
                else:
                    sin_foto.append(nombre)
            except Exception:
                sin_foto.append(nombre)
                ya_estaba = False
            if not ya_estaba:
                time.sleep(1.6)
        if existe:
            pid = int(existe[0])
            cur.execute(
                """
                UPDATE productos
                SET nombre=?, descripcion=?, precio_base=?, stock=?, categoria_id=?,
                    unidad_medida=?, bodega_codigo=?, codigo_barras=?,
                    imagen_path=CASE WHEN ? != '' THEN ? ELSE NULL END
                WHERE id=?
                """,
                (
                    nombre,
                    f"Conteo de barra. Unidad {unidad}. Precio inicial de mercado, sin precio de venta.",
                    float(costo),
                    float(stock),
                    cat_id,
                    unidad,
                    "BARRA",
                    codigo,
                    imagen,
                    imagen,
                    pid,
                ),
            )
        else:
            cur.execute(
                """
                INSERT INTO productos (
                    nombre, descripcion, precio, precio_base, precio_minimo, stock,
                    categoria_id, stock_minimo, imagen_path, codigo_barras, activo,
                    codigo_producto, unidad_medida, bodega_codigo, aplica_itbis, facturar_sin_stock
                ) VALUES (?, ?, 0, ?, 0, ?, ?, 1, ?, ?, 1, ?, ?, 'BARRA', 1, 1)
                """,
                (
                    nombre,
                    f"Conteo de barra. Unidad {unidad}. Precio inicial de mercado, sin precio de venta.",
                    float(costo),
                    float(stock),
                    cat_id,
                    imagen or None,
                    codigo,
                    codigo,
                    unidad,
                ),
            )
            pid = int(cur.lastrowid)
            nuevos += 1
        cur.execute(
            "SELECT 1 FROM stock_bodega WHERE producto_id=? AND bodega='BARRA'",
            (pid,),
        )
        if cur.fetchone():
            cur.execute(
                "UPDATE stock_bodega SET cantidad=? WHERE producto_id=? AND bodega='BARRA'",
                (float(stock), pid),
            )
        else:
            cur.execute(
                "INSERT INTO stock_bodega (producto_id, bodega, cantidad) VALUES (?, 'BARRA', ?)",
                (pid, float(stock)),
            )
    conn.commit()
    conn.close()
    print(f"productos {len(PRODUCTOS)} nuevos {nuevos} fotos {fotos} sin_foto {len(sin_foto)}")
    for nombre in sin_foto:
        print("sin foto:", nombre)


if __name__ == "__main__":
    cargar()
