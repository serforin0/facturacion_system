"""Botella de catálogo para productos que no tienen foto real.

Dibuja una botella con el nombre en la etiqueta y la guarda en
web_static/barra/{codigo}.jpg. No pisa una foto que ya exista.
"""

import os
import re
import sqlite3

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.abspath(__file__))
SALIDA = os.path.join(ROOT, "web_static", "barra")
FONDO = (244, 243, 240)
ETIQUETA = (252, 250, 246)
TINTA = (28, 26, 22)

# Líquido, cápsula, barra de la etiqueta.
_POR_CATEGORIA = {
    "aguardiente": ((214, 206, 186), (28, 28, 28), (90, 78, 52)),
    "aguas": ((186, 214, 228), (40, 90, 120), (36, 110, 150)),
    "vermuth": ((176, 48, 52), (92, 18, 22), (120, 24, 28)),
    "zumos": ((232, 140, 48), (120, 52, 16), (176, 78, 18)),
    "vodkas": ((214, 226, 232), (36, 48, 58), (48, 72, 88)),
    "whisky": ((196, 122, 42), (62, 32, 14), (110, 58, 16)),
    "tequila": ((214, 186, 96), (48, 56, 28), (92, 78, 22)),
    "licores": ((212, 168, 64), (48, 28, 16), (120, 72, 18)),
    "rones": ((176, 96, 36), (42, 22, 12), (110, 48, 14)),
    "ginebra": ((198, 214, 196), (28, 48, 36), (36, 78, 52)),
    "cervezas": ((214, 158, 42), (36, 48, 28), (92, 58, 12)),
    "brandy": ((168, 86, 32), (48, 22, 12), (110, 42, 14)),
    "coñac": ((150, 72, 28), (36, 16, 10), (98, 36, 12)),
    "aperitivos": ((196, 78, 36), (42, 16, 12), (140, 42, 18)),
    "refrescos": ((64, 128, 72), (18, 48, 28), (20, 90, 40)),
    "hielo": ((210, 226, 234), (70, 100, 120), (80, 120, 140)),
}

_POR_NOMBRE = (
    ("blue", ((46, 108, 196), (16, 36, 88), (24, 72, 150))),
    ("aperol", ((240, 112, 28), (140, 42, 8), (200, 72, 12))),
    ("gold", ((214, 164, 48), (72, 48, 10), (150, 100, 16))),
    ("vainilla", ((224, 196, 140), (72, 48, 24), (140, 96, 36))),
    ("vanilla", ((224, 196, 140), (72, 48, 24), (140, 96, 36))),
    ("citron", ((214, 206, 96), (64, 72, 18), (110, 110, 24))),
    ("limon", ((214, 206, 96), (64, 72, 18), (110, 110, 24))),
    ("raspberry", ((196, 48, 78), (72, 12, 28), (140, 24, 48))),
    ("razz", ((196, 48, 78), (72, 12, 28), (140, 24, 48))),
    ("rojo", ((176, 36, 36), (64, 10, 12), (130, 20, 22))),
    ("roja", ((176, 36, 36), (64, 10, 12), (130, 20, 22))),
    (" red", ((186, 32, 40), (72, 10, 16), (140, 18, 24))),
    ("rosso", ((168, 28, 36), (64, 8, 12), (120, 16, 20))),
    ("rosé", ((214, 120, 140), (96, 32, 48), (160, 60, 80))),
    ("rose", ((214, 120, 140), (96, 32, 48), (160, 60, 80))),
    ("blanco", ((226, 228, 222), (36, 40, 36), (70, 78, 72))),
    ("silver", ((210, 216, 220), (40, 48, 54), (80, 90, 98))),
    ("dry", ((214, 206, 176), (48, 42, 28), (100, 84, 48))),
    ("añejo", ((168, 88, 32), (48, 22, 10), (110, 48, 14))),
    ("anejo", ((168, 88, 32), (48, 22, 10), (110, 48, 14))),
    ("dark", ((72, 44, 28), (20, 12, 8), (48, 28, 16))),
    ("negro", ((64, 40, 28), (16, 10, 8), (40, 24, 16))),
    ("negra", ((64, 40, 28), (16, 10, 8), (40, 24, 16))),
    ("onyx", ((48, 44, 42), (12, 10, 10), (28, 26, 24))),
    ("verde", ((96, 150, 64), (24, 48, 18), (40, 90, 28))),
    ("menta", ((64, 140, 96), (16, 48, 32), (24, 96, 58))),
    ("red bull", ((20, 40, 90), (8, 12, 28), (180, 150, 40))),
    ("coca", ((140, 24, 24), (40, 8, 8), (110, 16, 16))),
    ("sprite", ((72, 160, 72), (16, 48, 20), (24, 110, 36))),
    ("pepsi", ((24, 64, 140), (8, 16, 48), (16, 40, 110))),
)


def _fuente(negrita, tamano):
    nombre = "segoeuib.ttf" if negrita else "segoeui.ttf"
    ruta = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", nombre)
    if not os.path.isfile(ruta):
        ruta = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "arial.ttf")
    return ImageFont.truetype(ruta, tamano)


def colores(nombre, categoria):
    texto = (nombre or "").lower()
    for clave, trio in _POR_NOMBRE:
        if clave in texto:
            return trio
    return _POR_CATEGORIA.get((categoria or "").lower(), ((196, 140, 64), (40, 28, 16), (110, 64, 18)))


def _partir_nombre(nombre):
    texto = re.sub(r"\s+", " ", (nombre or "").strip())
    volumen = ""
    coincidencia = re.search(
        r"(\d+(?:[.,]\d+)?\s*(?:ml|lt|litro|litros|oz|cl|galón|galon|lb))\b",
        texto,
        flags=re.I,
    )
    if coincidencia:
        volumen = coincidencia.group(1).replace(" ", " ")
        texto = (texto[: coincidencia.start()] + texto[coincidencia.end() :]).strip(" -")
    for prefijo in (
        "Licor de ",
        "Licor ",
        "Vodka ",
        "Whisky ",
        "Tequila ",
        "Ginebra ",
        "Cerveza ",
        "Brandy ",
        "Coñac ",
        "Aguardiente ",
        "Vermouth ",
        "Refresco ",
        "Agua ",
        "Ron ",
        "Soda ",
        "Jugo ",
    ):
        if texto.lower().startswith(prefijo.lower()):
            texto = texto[len(prefijo) :]
            break
    return texto.strip() or (nombre or "").strip(), volumen


def _lineas(dibujo, texto, fuente, ancho):
    palabras = texto.split()
    if not palabras:
        return [""]
    lineas = []
    actual = palabras[0]
    for palabra in palabras[1:]:
        prueba = actual + " " + palabra
        if dibujo.textlength(prueba, font=fuente) <= ancho:
            actual = prueba
        else:
            lineas.append(actual)
            actual = palabra
    lineas.append(actual)
    return lineas


def _texto_que_cabe(dibujo, texto, ancho, alto, negrita):
    for tamano in range(120, 33, -2):
        fuente = _fuente(negrita, tamano)
        lineas = _lineas(dibujo, texto, fuente, ancho)
        if len(lineas) > 4:
            continue
        alto_linea = tamano + 8
        if alto_linea * len(lineas) <= alto and all(dibujo.textlength(linea, font=fuente) <= ancho for linea in lineas):
            return fuente, lineas, alto_linea
    fuente = _fuente(negrita, 28)
    return fuente, _lineas(dibujo, texto, fuente, ancho)[:4], 36


def _botella(dibujo, caja, liquido, capsula, barra):
    """Silueta de botella. Las medidas son de la imagen final, ya escaladas."""
    izq, arriba, der, abajo = caja
    cx = (izq + der) // 2
    ancho = der - izq
    cuello = max(18, ancho // 9)
    hombro = arriba + int((abajo - arriba) * 0.22)
    boca = arriba + int((abajo - arriba) * 0.08)
    dibujo.rounded_rectangle((izq, hombro, der, abajo), radius=ancho // 5, fill=liquido)
    dibujo.polygon(
        [
            (cx - cuello, boca + cuello),
            (izq + ancho // 10, hombro + ancho // 8),
            (der - ancho // 10, hombro + ancho // 8),
            (cx + cuello, boca + cuello),
        ],
        fill=liquido,
    )
    dibujo.rectangle((cx - cuello, boca, cx + cuello, hombro), fill=liquido)
    dibujo.rounded_rectangle(
        (cx - cuello - 6, arriba, cx + cuello + 6, boca + 8),
        radius=8,
        fill=capsula,
    )
    dibujo.rectangle((cx - cuello - 6, arriba + 16, cx + cuello + 6, arriba + 26), fill=barra)
    return hombro


def dibujar(nombre, categoria, destino):
    escala = 2
    ancho, alto = 640 * escala, 800 * escala
    imagen = Image.new("RGB", (ancho, alto), FONDO)
    dibujo = ImageDraw.Draw(imagen)
    liquido, capsula, barra = colores(nombre, categoria)
    margen_x = 48 * escala
    caja = (margen_x, 28 * escala, ancho - margen_x, alto - 28 * escala)
    _botella(dibujo, caja, liquido, capsula, barra)

    brillo = Image.new("RGBA", (ancho, alto), (0, 0, 0, 0))
    ImageDraw.Draw(brillo).rounded_rectangle(
        (caja[0] + 28 * escala, caja[1] + 210 * escala, caja[0] + 58 * escala, caja[3] - 80 * escala),
        radius=16 * escala,
        fill=(255, 255, 255, 90),
    )
    imagen = Image.alpha_composite(imagen.convert("RGBA"), brillo).convert("RGB")
    dibujo = ImageDraw.Draw(imagen)

    etiqueta = (
        caja[0] + 36 * escala,
        300 * escala,
        caja[2] - 36 * escala,
        690 * escala,
    )
    dibujo.rounded_rectangle(etiqueta, radius=22 * escala, fill=ETIQUETA)
    dibujo.rounded_rectangle(
        (etiqueta[0] + 18 * escala, etiqueta[1] + 16 * escala, etiqueta[2] - 18 * escala, etiqueta[1] + 28 * escala),
        radius=4 * escala,
        fill=barra,
    )

    marca, volumen = _partir_nombre(nombre)
    cx = ancho // 2
    zona_ancho = etiqueta[2] - etiqueta[0] - 48 * escala
    zona_alto = etiqueta[3] - etiqueta[1] - (150 * escala if volumen else 70 * escala)
    fuente, lineas, alto_linea = _texto_que_cabe(dibujo, marca, zona_ancho, zona_alto, True)
    bloque = alto_linea * len(lineas)
    y = etiqueta[1] + 48 * escala + max(0, (zona_alto - bloque) // 2)
    for linea in lineas:
        largo = dibujo.textlength(linea, font=fuente)
        dibujo.text((cx - largo / 2, y), linea, font=fuente, fill=TINTA)
        y += alto_linea
    if volumen:
        fuente_vol = _fuente(True, 34 * escala)
        largo = dibujo.textlength(volumen, font=fuente_vol)
        dibujo.text((cx - largo / 2, etiqueta[3] - 78 * escala), volumen, font=fuente_vol, fill=barra)

    final = imagen.resize((640, 800), Image.Resampling.LANCZOS)
    os.makedirs(os.path.dirname(destino), exist_ok=True)
    final.save(destino, "JPEG", quality=88, optimize=True)


def _tiene_foto(ruta):
    if not ruta:
        return False
    completa = ruta.replace("/", os.sep)
    if not os.path.isabs(completa):
        completa = os.path.join(ROOT, completa)
    return os.path.isfile(completa) and os.path.getsize(completa) > 2000


def generar(solo=None):
    conexion = sqlite3.connect(os.path.join(ROOT, "bar_inventory.db"))
    filas = conexion.execute(
        """
        SELECT p.codigo_producto, p.nombre, IFNULL(c.nombre, ''), IFNULL(p.imagen_path, '')
        FROM productos p
        LEFT JOIN categorias c ON c.id = p.categoria_id
        WHERE p.activo = 1 AND IFNULL(p.bodega_codigo, '') = 'BARRA'
        ORDER BY p.nombre COLLATE NOCASE
        """
    ).fetchall()
    hechas = 0
    for codigo, nombre, categoria, imagen in filas:
        if solo and codigo not in solo:
            continue
        destino = os.path.join(SALIDA, f"{codigo}.jpg")
        if not solo and _tiene_foto(imagen) and os.path.isfile(destino):
            continue
        if not solo and os.path.isfile(destino) and os.path.getsize(destino) > 2000 and _tiene_foto(imagen):
            continue
        dibujar(nombre, categoria, destino)
        ruta = f"web_static/barra/{codigo}.jpg"
        conexion.execute(
            "UPDATE productos SET imagen_path = ? WHERE codigo_producto = ?",
            (ruta, codigo),
        )
        hechas += 1
    conexion.commit()
    conexion.close()
    return hechas


if __name__ == "__main__":
    import sys

    codigos = sys.argv[1:]
    print(generar(set(codigos) if codigos else None))
