"""
Comprobantes fiscales electrónicos (e-CF) según el formato DGII v1.0.

Construye el XML en el orden de los XSD oficiales (carpeta ecf/xsd), lo firma con el
certificado digital del emisor (XMLDSig enveloped, RSA-SHA256), lo valida contra el XSD
y se comunica con los servicios web de la DGII (autenticación, recepción, consulta).
"""
from __future__ import annotations

import json
import secrets
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from urllib.parse import quote

from lxml import etree

XSD_DIR = Path(__file__).resolve().parent / "xsd"
AMBIENTES = ("testecf", "certecf", "ecf")
TASA_ITBIS = Decimal("0.18")
TOPE_CONSUMO = Decimal("250000")
TIPO_POR_COMPROBANTE = {"credito_fiscal": 31, "consumidor_final": 32}
FORMA_PAGO = {"efectivo": 1, "transferencia": 2, "cheque": 2, "deposito": 2, "tarjeta": 3,
              "credito": 4, "nota_credito": 7, "saldo_favor": 7}
_ESQUEMAS: dict[str, etree.XMLSchema] = {}


def dinero(valor) -> Decimal:
    return Decimal(str(valor or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def txt_monto(valor) -> str:
    return f"{dinero(valor):.2f}"


def txt_precio(valor) -> str:
    return f"{Decimal(str(valor or 0)).quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP):.4f}"


def txt_cantidad(valor) -> str:
    d = Decimal(str(valor or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"{d:.2f}"


def fecha_dgii(valor: str | datetime) -> str:
    """dd-MM-AAAA a partir de 'AAAA-MM-DD...' o datetime."""
    if isinstance(valor, datetime):
        return valor.strftime("%d-%m-%Y")
    texto = str(valor or "")[:10]
    return datetime.strptime(texto, "%Y-%m-%d").strftime("%d-%m-%Y")


def solo_digitos(texto: str | None) -> str:
    return "".join(c for c in str(texto or "") if c.isdigit())


def recortar(texto: str | None, largo: int) -> str:
    return " ".join(str(texto or "").split())[:largo]


def tipo_ecf_de(comprobante: str) -> int | None:
    return TIPO_POR_COMPROBANTE.get((comprobante or "").strip().lower())


def armar_encf(tipo: int, secuencial: int) -> str:
    return f"E{int(tipo):02d}{int(secuencial):010d}"


@dataclass
class LineaECF:
    nombre: str
    cantidad: float
    precio: float
    descuento: float = 0.0
    gravado: bool = True


@dataclass
class DatosECF:
    tipo: int
    encf: str
    fecha_emision: str
    rnc_emisor: str
    razon_social_emisor: str
    direccion_emisor: str
    lineas: list[LineaECF]
    vencimiento_secuencia: str | None = None
    rnc_comprador: str | None = None
    razon_social_comprador: str | None = None
    pagos: list[tuple[str, float]] = field(default_factory=list)
    fecha_limite_pago: str | None = None
    precios_incluyen_itbis: bool = False
    descuento_global: float = 0.0
    numero_interno: str | None = None
    ncf_modificado: str | None = None
    fecha_ncf_modificado: str | None = None
    razon_modificacion: str | None = None


@dataclass
class Totales:
    gravado: Decimal
    exento: Decimal
    itbis: Decimal
    total: Decimal
    items: list[dict]


def calcular_totales(datos: DatosECF) -> Totales:
    """Precios unitarios sin ITBIS y descuento global repartido en las líneas, para que todo cuadre."""
    brutos = []
    for ln in datos.lineas:
        precio = Decimal(str(ln.precio or 0))
        if datos.precios_incluyen_itbis and ln.gravado:
            precio = precio / (1 + TASA_ITBIS)
        bruto = Decimal(str(ln.cantidad)) * precio
        brutos.append((ln, precio, bruto))
    base_global = sum((b - Decimal(str(ln.descuento or 0)) for ln, _p, b in brutos), Decimal("0"))
    global_desc = Decimal(str(datos.descuento_global or 0))
    if datos.precios_incluyen_itbis and global_desc > 0:
        bruto_con = sum(((b - Decimal(str(ln.descuento or 0))) * (1 + TASA_ITBIS if ln.gravado else 1)
                         for ln, _p, b in brutos), Decimal("0"))
        global_desc = global_desc * (base_global / bruto_con) if bruto_con else Decimal("0")
    items, gravado, exento, repartido = [], Decimal("0"), Decimal("0"), Decimal("0")
    for i, (ln, precio, bruto) in enumerate(brutos, start=1):
        desc_linea = Decimal(str(ln.descuento or 0))
        if datos.precios_incluyen_itbis and ln.gravado and desc_linea:
            desc_linea = desc_linea / (1 + TASA_ITBIS)
        if global_desc and base_global:
            if i == len(brutos):
                parte = dinero(global_desc) - repartido
            else:
                parte = dinero(global_desc * (bruto - desc_linea) / base_global)
                repartido += parte
            desc_linea += parte
        desc_linea = dinero(desc_linea)
        monto = dinero(bruto - desc_linea)
        if ln.gravado:
            gravado += monto
        else:
            exento += monto
        items.append({"linea": i, "nombre": recortar(ln.nombre, 80) or "Artículo", "cantidad": ln.cantidad,
                      "precio": precio, "descuento": desc_linea, "monto": monto, "gravado": ln.gravado})
    itbis = dinero(gravado * TASA_ITBIS)
    return Totales(gravado, exento, itbis, dinero(gravado + exento + itbis), items)


def _sub(padre, nombre: str, valor=None):
    el = etree.SubElement(padre, nombre)
    if valor is not None:
        el.text = str(valor)
    return el


def construir_ecf(datos: DatosECF, fecha_firma: str) -> etree._Element:
    tot = calcular_totales(datos)
    raiz = etree.Element("ECF")
    enc = _sub(raiz, "Encabezado")
    _sub(enc, "Version", "1.0")
    iddoc = _sub(enc, "IdDoc")
    _sub(iddoc, "TipoeCF", datos.tipo)
    _sub(iddoc, "eNCF", datos.encf)
    if datos.tipo == 34:
        dias = 0
        if datos.fecha_ncf_modificado:
            dias = (datetime.strptime(datos.fecha_emision, "%d-%m-%Y")
                    - datetime.strptime(datos.fecha_ncf_modificado, "%d-%m-%Y")).days
        _sub(iddoc, "IndicadorNotaCredito", 1 if dias > 30 else 0)
    elif datos.tipo != 32 and datos.vencimiento_secuencia:
        _sub(iddoc, "FechaVencimientoSecuencia", datos.vencimiento_secuencia)
    _sub(iddoc, "IndicadorMontoGravado", 0)
    _sub(iddoc, "TipoIngresos", "01")
    credito = any(c == "credito" for c, _m in datos.pagos)
    _sub(iddoc, "TipoPago", 2 if credito else 1)
    if credito and datos.fecha_limite_pago and datos.tipo != 34:
        _sub(iddoc, "FechaLimitePago", datos.fecha_limite_pago)
    formas: dict[int, Decimal] = {}
    for codigo, monto in datos.pagos:
        if dinero(monto) > 0:
            forma = FORMA_PAGO.get((codigo or "").lower(), 8)
            formas[forma] = formas.get(forma, Decimal("0")) + dinero(monto)
    if formas and datos.tipo != 34:
        tabla = _sub(iddoc, "TablaFormasPago")
        for forma, monto in list(formas.items())[:7]:
            fp = _sub(tabla, "FormaDePago")
            _sub(fp, "FormaPago", forma)
            _sub(fp, "MontoPago", txt_monto(monto))
    emisor = _sub(enc, "Emisor")
    _sub(emisor, "RNCEmisor", datos.rnc_emisor)
    _sub(emisor, "RazonSocialEmisor", recortar(datos.razon_social_emisor, 150))
    _sub(emisor, "DireccionEmisor", recortar(datos.direccion_emisor, 100) or "Sin dirección")
    if datos.numero_interno:
        _sub(emisor, "NumeroFacturaInterna", recortar(datos.numero_interno, 20))
    _sub(emisor, "FechaEmision", datos.fecha_emision)
    comprador = _sub(enc, "Comprador")
    if datos.rnc_comprador:
        _sub(comprador, "RNCComprador", datos.rnc_comprador)
        _sub(comprador, "RazonSocialComprador", recortar(datos.razon_social_comprador, 150) or "Cliente")
    totales = _sub(enc, "Totales")
    if tot.gravado > 0:
        _sub(totales, "MontoGravadoTotal", txt_monto(tot.gravado))
        _sub(totales, "MontoGravadoI1", txt_monto(tot.gravado))
    if tot.exento > 0:
        _sub(totales, "MontoExento", txt_monto(tot.exento))
    if tot.gravado > 0:
        _sub(totales, "ITBIS1", 18)
        _sub(totales, "TotalITBIS", txt_monto(tot.itbis))
        _sub(totales, "TotalITBIS1", txt_monto(tot.itbis))
    _sub(totales, "MontoTotal", txt_monto(tot.total))
    detalles = _sub(raiz, "DetallesItems")
    for it in tot.items:
        item = _sub(detalles, "Item")
        _sub(item, "NumeroLinea", it["linea"])
        _sub(item, "IndicadorFacturacion", 1 if it["gravado"] else 4)
        _sub(item, "NombreItem", it["nombre"])
        _sub(item, "IndicadorBienoServicio", 1)
        _sub(item, "CantidadItem", txt_cantidad(it["cantidad"]))
        _sub(item, "PrecioUnitarioItem", txt_precio(it["precio"]))
        if it["descuento"] > 0:
            _sub(item, "DescuentoMonto", txt_monto(it["descuento"]))
            tabla = _sub(item, "TablaSubDescuento")
            sub = _sub(tabla, "SubDescuento")
            _sub(sub, "TipoSubDescuento", "$")
            _sub(sub, "MontoSubDescuento", txt_monto(it["descuento"]))
        _sub(item, "MontoItem", txt_monto(it["monto"]))
    if datos.tipo == 34 and datos.ncf_modificado:
        ref = _sub(raiz, "InformacionReferencia")
        _sub(ref, "NCFModificado", datos.ncf_modificado)
        _sub(ref, "FechaNCFModificado", datos.fecha_ncf_modificado)
        _sub(ref, "CodigoModificacion", 3)
        if datos.razon_modificacion:
            _sub(ref, "RazonModificacion", recortar(datos.razon_modificacion, 90))
    _sub(raiz, "FechaHoraFirma", fecha_firma)
    return raiz


def construir_rfce(datos: DatosECF, codigo_seguridad: str) -> etree._Element:
    """Resumen de factura de consumo menor a RD$250,000 (se envía en lugar del e-CF completo)."""
    tot = calcular_totales(datos)
    raiz = etree.Element("RFCE")
    enc = _sub(raiz, "Encabezado")
    _sub(enc, "Version", "1.0")
    iddoc = _sub(enc, "IdDoc")
    _sub(iddoc, "TipoeCF", 32)
    _sub(iddoc, "eNCF", datos.encf)
    _sub(iddoc, "TipoIngresos", "01")
    credito = any(c == "credito" for c, _m in datos.pagos)
    _sub(iddoc, "TipoPago", 2 if credito else 1)
    formas: dict[int, Decimal] = {}
    for codigo, monto in datos.pagos:
        if dinero(monto) > 0:
            forma = FORMA_PAGO.get((codigo or "").lower(), 8)
            formas[forma] = formas.get(forma, Decimal("0")) + dinero(monto)
    if formas:
        tabla = _sub(iddoc, "TablaFormasPago")
        for forma, monto in list(formas.items())[:7]:
            fp = _sub(tabla, "FormaDePago")
            _sub(fp, "FormaPago", forma)
            _sub(fp, "MontoPago", txt_monto(monto))
    emisor = _sub(enc, "Emisor")
    _sub(emisor, "RNCEmisor", datos.rnc_emisor)
    _sub(emisor, "RazonSocialEmisor", recortar(datos.razon_social_emisor, 150))
    _sub(emisor, "FechaEmision", datos.fecha_emision)
    comprador = _sub(enc, "Comprador")
    if datos.rnc_comprador:
        _sub(comprador, "RNCComprador", datos.rnc_comprador)
        _sub(comprador, "RazonSocialComprador", recortar(datos.razon_social_comprador, 150) or "Cliente")
    totales = _sub(enc, "Totales")
    if tot.gravado > 0:
        _sub(totales, "MontoGravadoTotal", txt_monto(tot.gravado))
        _sub(totales, "MontoGravadoI1", txt_monto(tot.gravado))
    if tot.exento > 0:
        _sub(totales, "MontoExento", txt_monto(tot.exento))
    if tot.gravado > 0:
        _sub(totales, "TotalITBIS", txt_monto(tot.itbis))
        _sub(totales, "TotalITBIS1", txt_monto(tot.itbis))
    _sub(totales, "MontoTotal", txt_monto(tot.total))
    _sub(enc, "CodigoSeguridadeCF", codigo_seguridad)
    return raiz


def esquema(nombre: str) -> etree.XMLSchema:
    """Carga el XSD oficial; si referencia tipos que la DGII olvidó definir, los toma del XSD 32."""
    if nombre in _ESQUEMAS:
        return _ESQUEMAS[nombre]
    xs = "http://www.w3.org/2001/XMLSchema"
    texto = (XSD_DIR / nombre).read_bytes()
    # Correcciones a expresiones del XSD oficial que no son XML Schema válido (sin cambiar lo que aceptan)
    texto = texto.replace(b"[$0-9]", b"[0-9]").replace(b"(?:", b"(")
    doc = etree.ElementTree(etree.fromstring(texto))
    definidos = {t.get("name") for t in doc.getroot() if t.get("name")}
    usados = {e.get("type") for e in doc.iter(f"{{{xs}}}element") if e.get("type") and ":" not in e.get("type")}
    faltan = usados - definidos
    if faltan and nombre != "ecf32.xsd":
        donante = etree.parse(str(XSD_DIR / "ecf32.xsd")).getroot()
        for tipo in donante:
            if tipo.get("name") in faltan:
                doc.getroot().append(tipo)
    _ESQUEMAS[nombre] = etree.XMLSchema(doc)
    return _ESQUEMAS[nombre]


def validar(raiz: etree._Element, tipo: int | str) -> list[str]:
    archivo = {31: "ecf31.xsd", 32: "ecf32.xsd", 34: "ecf34.xsd", "rfce": "rfce32.xsd"}[tipo]
    sch = esquema(archivo)
    if sch.validate(raiz):
        return []
    return [f"línea {e.line}: {e.message}" for e in sch.error_log][:10]


class Firmador:
    """Firma XMLDSig enveloped con el certificado .p12 del emisor."""

    def __init__(self, ruta_p12: str | Path, clave: str):
        from cryptography.hazmat.primitives.serialization import pkcs12

        datos = Path(ruta_p12).read_bytes()
        llave, cert, _extra = pkcs12.load_key_and_certificates(datos, (clave or "").encode() or None)
        if llave is None or cert is None:
            raise ValueError("El certificado no trae la llave privada")
        self.llave = llave
        self.certificado = cert

    @property
    def vence(self) -> datetime:
        cert = self.certificado
        return getattr(cert, "not_valid_after_utc", None) or cert.not_valid_after

    @property
    def titular(self) -> str:
        return self.certificado.subject.rfc4514_string()

    def firmar(self, raiz: etree._Element) -> etree._Element:
        """Firma enveloped con la misma forma que SignedXml de .NET: Reference URI="", C14N 1.0, RSA-SHA256."""
        import base64
        import hashlib

        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding

        ds = "http://www.w3.org/2000/09/xmldsig#"
        raiz = etree.fromstring(etree.tostring(raiz))
        for vieja in raiz.findall(f"{{{ds}}}Signature"):
            raiz.remove(vieja)
        digest = base64.b64encode(hashlib.sha256(etree.tostring(raiz, method="c14n")).digest()).decode()

        def nodo(padre, nombre, texto=None, **attrs):
            el = etree.SubElement(padre, f"{{{ds}}}{nombre}", **attrs)
            if texto is not None:
                el.text = texto
            return el

        firma = etree.SubElement(raiz, f"{{{ds}}}Signature", nsmap={None: ds})
        info = nodo(firma, "SignedInfo")
        nodo(info, "CanonicalizationMethod", Algorithm="http://www.w3.org/TR/2001/REC-xml-c14n-20010315")
        nodo(info, "SignatureMethod", Algorithm="http://www.w3.org/2001/04/xmldsig-more#rsa-sha256")
        ref = nodo(info, "Reference", URI="")
        transforms = nodo(ref, "Transforms")
        nodo(transforms, "Transform", Algorithm="http://www.w3.org/2000/09/xmldsig#enveloped-signature")
        nodo(ref, "DigestMethod", Algorithm="http://www.w3.org/2001/04/xmlenc#sha256")
        nodo(ref, "DigestValue", digest)
        firma_bytes = self.llave.sign(etree.tostring(info, method="c14n"), padding.PKCS1v15(), hashes.SHA256())
        nodo(firma, "SignatureValue", base64.b64encode(firma_bytes).decode())
        datos_x509 = nodo(nodo(firma, "KeyInfo"), "X509Data")
        nodo(datos_x509, "X509Certificate", base64.b64encode(self.certificado.public_bytes(serialization.Encoding.DER)).decode())
        return etree.fromstring(etree.tostring(raiz))


def codigo_seguridad(firmado: etree._Element) -> str:
    valor = firmado.find(".//{http://www.w3.org/2000/09/xmldsig#}SignatureValue")
    texto = "".join((valor.text or "").split()) if valor is not None else ""
    return texto[:6]


def a_bytes(raiz: etree._Element) -> bytes:
    return etree.tostring(raiz, xml_declaration=True, encoding="UTF-8")


def url_qr(ambiente: str, datos: DatosECF, total: Decimal, fecha_firma: str, codigo: str) -> str:
    if datos.tipo == 32 and total < TOPE_CONSUMO:
        return (f"https://fc.dgii.gov.do/{ambiente}/ConsultaTimbreFC?RncEmisor={datos.rnc_emisor}"
                f"&ENCF={datos.encf}&MontoTotal={txt_monto(total)}&CodigoSeguridad={quote(codigo)}")
    return (f"https://ecf.dgii.gov.do/{ambiente}/ConsultaTimbre?RncEmisor={datos.rnc_emisor}"
            f"&RncComprador={datos.rnc_comprador or ''}&ENCF={datos.encf}&FechaEmision={datos.fecha_emision}"
            f"&MontoTotal={txt_monto(total)}&FechaFirma={quote(fecha_firma)}&CodigoSeguridad={quote(codigo)}")


def qr_svg(texto: str) -> str:
    import qrcode
    import qrcode.image.svg

    img = qrcode.make(texto, image_factory=qrcode.image.svg.SvgPathImage, box_size=8, border=2)
    return img.to_string(encoding="unicode")


class ErrorDGII(Exception):
    pass


class ClienteDGII:
    """Servicios web REST de la DGII: semilla → token, recepción y consulta."""

    def __init__(self, ambiente: str, firmador: Firmador, tiempo: int = 30):
        if ambiente not in AMBIENTES:
            raise ValueError("Ambiente no válido")
        self.ambiente = ambiente
        self.firmador = firmador
        self.tiempo = tiempo
        self.token: str | None = None
        self.base_ecf = f"https://ecf.dgii.gov.do/{ambiente}"
        self.base_fc = f"https://fc.dgii.gov.do/{ambiente}"

    def _pedir(self, url: str, metodo: str = "GET", cuerpo: bytes | None = None, tipo: str | None = None) -> tuple[int, bytes]:
        req = urllib.request.Request(url, data=cuerpo, method=metodo)
        req.add_header("Accept", "application/json")
        if tipo:
            req.add_header("Content-Type", tipo)
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(req, timeout=self.tiempo) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise ErrorDGII(f"Sin conexión con la DGII: {e}") from e

    @staticmethod
    def _multipart(nombre_archivo: str, contenido: bytes) -> tuple[bytes, str]:
        limite = "----ecf" + secrets.token_hex(12)
        cuerpo = (
            f"--{limite}\r\nContent-Disposition: form-data; name=\"xml\"; filename=\"{nombre_archivo}\"\r\n"
            f"Content-Type: text/xml\r\n\r\n"
        ).encode() + contenido + f"\r\n--{limite}--\r\n".encode()
        return cuerpo, f"multipart/form-data; boundary={limite}"

    def autenticar(self) -> str:
        self.token = None
        codigo, semilla = self._pedir(f"{self.base_ecf}/autenticacion/api/autenticacion/semilla")
        if codigo != 200:
            raise ErrorDGII(f"La DGII no entregó la semilla (HTTP {codigo})")
        raiz = etree.fromstring(semilla)
        firmada = a_bytes(self.firmador.firmar(raiz))
        cuerpo, tipo = self._multipart("semilla.xml", firmada)
        codigo, resp = self._pedir(f"{self.base_ecf}/autenticacion/api/autenticacion/validarsemilla", "POST", cuerpo, tipo)
        if codigo != 200:
            detalle = resp[:300].decode("utf-8", "replace").strip().strip('"')
            if "certificado no admitido" in detalle.lower():
                detalle += ". Usa el certificado emitido por una entidad autorizada por INDOTEL"
            raise ErrorDGII(f"La DGII no aceptó la autenticación: {detalle}")
        datos = json.loads(resp or b"{}")
        self.token = datos.get("token")
        if not self.token:
            raise ErrorDGII("La DGII no devolvió token")
        return self.token

    def enviar_ecf(self, nombre_archivo: str, xml: bytes) -> dict:
        if not self.token:
            self.autenticar()
        cuerpo, tipo = self._multipart(nombre_archivo, xml)
        codigo, resp = self._pedir(f"{self.base_ecf}/recepcion/api/facturaselectronicas", "POST", cuerpo, tipo)
        return {"http": codigo, **(json.loads(resp) if resp and resp.strip().startswith(b"{") else {"texto": resp.decode(errors="replace")[:500]})}

    def enviar_rfce(self, nombre_archivo: str, xml: bytes) -> dict:
        if not self.token:
            self.autenticar()
        cuerpo, tipo = self._multipart(nombre_archivo, xml)
        codigo, resp = self._pedir(f"{self.base_fc}/recepcionfc/api/recepcion/ecf", "POST", cuerpo, tipo)
        return {"http": codigo, **(json.loads(resp) if resp and resp.strip().startswith(b"{") else {"texto": resp.decode(errors="replace")[:500]})}

    def consultar(self, track_id: str) -> dict:
        if not self.token:
            self.autenticar()
        codigo, resp = self._pedir(f"{self.base_ecf}/consultaresultado/api/consultas/estado?trackid={quote(track_id)}")
        return {"http": codigo, **(json.loads(resp) if resp and resp.strip().startswith(b"{") else {"texto": resp.decode(errors="replace")[:500]})}


def estado_de_respuesta(resp: dict) -> str:
    """Normaliza la respuesta de la DGII a: aceptado, aceptado_condicional, rechazado, en_proceso o error."""
    texto = str(resp.get("estado") or resp.get("Estado") or "").lower()
    if "condicional" in texto:
        return "aceptado_condicional"
    if "aceptado" in texto:
        return "aceptado"
    if "rechazado" in texto:
        return "rechazado"
    if resp.get("trackId") or resp.get("TrackId") or "proceso" in texto:
        return "en_proceso"
    return "error" if int(resp.get("http") or 0) >= 400 else "en_proceso"
