"""Integración del e-CF con la base del sistema: emitir, firmar, guardar, enviar y consultar."""
from __future__ import annotations

import json
import threading
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from . import dgii

_bloqueo_envio = threading.Lock()


def ruta_certificado(base) -> Path:
    """El certificado vive junto a la base de datos, fuera del código."""
    carpeta = Path(base.db_name).resolve().parent / "certificados"
    carpeta.mkdir(parents=True, exist_ok=True)
    return carpeta / "emisor.p12"


def config(base) -> dict:
    return {
        "activo": (base.get_config("ecf_activo", "0") or "0") == "1",
        "ambiente": base.get_config("ecf_ambiente", "testecf") or "testecf",
        "rnc": dgii.solo_digitos(base.get_config("empresa_rnc", "")),
        "tiene_certificado": ruta_certificado(base).exists(),
    }


def firmador(base) -> dgii.Firmador | None:
    ruta = ruta_certificado(base)
    if not ruta.exists():
        return None
    return dgii.Firmador(ruta, base.get_config("ecf_clave", "") or "")


def _empresa(base) -> tuple[str, str, str]:
    info = base.get_empresa_info()
    return dgii.solo_digitos(base.get_config("empresa_rnc", "")), info.get("nombre") or "", info.get("direccion") or ""


def _guardar(cur, **campos) -> int:
    columnas = ", ".join(campos)
    marcas = ", ".join("?" for _ in campos)
    cur.execute(f"INSERT INTO ecf_documentos ({columnas}) VALUES ({marcas})", tuple(campos.values()))
    return int(cur.lastrowid)


def _firmar_y_guardar(base, conn, datos: dgii.DatosECF, factura_id: int | None, nota_id: int | None) -> dict:
    cur = conn.cursor()
    cfg = config(base)
    fecha_firma = datetime.now().strftime("%d-%m-%Y %H:%M:%S")
    tot = dgii.calcular_totales(datos)
    registro = {"factura_id": factura_id, "nota_credito_id": nota_id, "tipo": datos.tipo, "encf": datos.encf,
                "total": float(tot.total), "ambiente": cfg["ambiente"]}
    if len(datos.rnc_emisor) not in (9, 11):
        registro.update(estado="error", mensaje="Configura el RNC de la empresa (9 u 11 dígitos) en Empresa y correo")
        doc_id = _guardar(cur, **registro)
        return {"id": doc_id, **registro}
    raiz = dgii.construir_ecf(datos, fecha_firma)
    try:
        firma = firmador(base)
    except Exception as exc:  # certificado dañado o clave incorrecta
        firma = None
        registro["mensaje"] = f"No se pudo abrir el certificado: {exc}"
    if firma is None:
        registro.update(estado="sin_certificado", xml=dgii.a_bytes(raiz).decode(), fecha_firma=fecha_firma,
                        mensaje=registro.get("mensaje") or "Falta el certificado digital: el comprobante quedó sin firmar y no se envió")
        doc_id = _guardar(cur, **registro)
        return {"id": doc_id, **registro}
    firmado = firma.firmar(raiz)
    errores = dgii.validar(firmado, datos.tipo)
    codigo = dgii.codigo_seguridad(firmado)
    registro.update(xml=dgii.a_bytes(firmado).decode(), codigo_seguridad=codigo, fecha_firma=fecha_firma,
                    url_qr=dgii.url_qr(cfg["ambiente"], datos, tot.total, fecha_firma, codigo))
    if errores:
        registro.update(estado="error", mensaje="No cumple el formato DGII: " + "; ".join(errores[:3]))
    elif datos.tipo == 32 and tot.total < dgii.TOPE_CONSUMO:
        rfce = firma.firmar(dgii.construir_rfce(datos, codigo))
        err_rfce = dgii.validar(rfce, "rfce")
        registro.update(xml_envio=dgii.a_bytes(rfce).decode(),
                        estado="error" if err_rfce else "pendiente_envio",
                        mensaje=("Resumen RFCE inválido: " + "; ".join(err_rfce[:3])) if err_rfce else None)
    else:
        registro.update(estado="pendiente_envio", xml_envio=registro["xml"])
    doc_id = _guardar(cur, **registro)
    return {"id": doc_id, **registro}


def _con_transaccion(base, trabajo, *args):
    conn = base.get_connection()
    try:
        resultado = trabajo(base, conn, *args)
        conn.commit()
        return resultado
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def emitir_factura(base, factura_id: int) -> dict | None:
    """Firma y guarda el e-CF de una factura que ya tiene e-NCF. Devuelve el registro o None."""
    return _con_transaccion(base, _emitir_factura, factura_id)


def _emitir_factura(base, conn, factura_id: int) -> dict | None:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT f.encf, f.tipo_comprobante, f.fecha, f.numero, IFNULL(f.descuento_total,0), IFNULL(f.precio_incluye_itbis,0),
               f.fecha_vencimiento, f.total, IFNULL(c.documento,''), IFNULL(c.nombre,''),
               IFNULL((SELECT SUM(p.monto) FROM pagos_factura p WHERE p.factura_id = f.id), 0)
        FROM facturas f LEFT JOIN clientes c ON c.id = f.cliente_id WHERE f.id = ?
        """,
        (factura_id,),
    )
    fila = cur.fetchone()
    if not fila or not fila[0]:
        return None
    encf, comprobante, fecha, numero, desc, incluido, vence, total, doc, nombre, pagado = fila
    tipo = int(encf[1:3])
    cur.execute("SELECT vencimiento FROM ecf_secuencias WHERE tipo = ?", (tipo,))
    venc = (cur.fetchone() or [None])[0]
    cur.execute("SELECT descripcion, cantidad, precio_unitario, IFNULL(descuento_item,0), IFNULL(impuesto_item,0) FROM factura_detalle WHERE factura_id = ?", (factura_id,))
    lineas = [dgii.LineaECF(n, float(c or 0), float(p or 0), float(d or 0), float(i or 0) > 0) for n, c, p, d, i in cur.fetchall()]
    cur.execute("SELECT tipo_pago, SUM(monto) FROM pagos_factura WHERE factura_id = ? GROUP BY tipo_pago", (factura_id,))
    pagos = [(t, float(m or 0)) for t, m in cur.fetchall()]
    pendiente = round(float(total or 0) - float(pagado or 0), 2)
    if pendiente > 0.009:
        pagos.append(("credito", pendiente))
    rnc, razon, direccion = _empresa(base)
    rnc_comprador = dgii.solo_digitos(doc)
    datos = dgii.DatosECF(
        tipo=tipo, encf=encf, fecha_emision=dgii.fecha_dgii(fecha), rnc_emisor=rnc, razon_social_emisor=razon,
        direccion_emisor=direccion, lineas=lineas, vencimiento_secuencia=venc,
        rnc_comprador=rnc_comprador if len(rnc_comprador) in (9, 11) else None, razon_social_comprador=nombre,
        pagos=pagos, fecha_limite_pago=dgii.fecha_dgii(vence) if vence else None,
        precios_incluyen_itbis=bool(int(incluido or 0)), descuento_global=float(desc or 0), numero_interno=numero,
    )
    registro = _firmar_y_guardar(base, conn, datos, factura_id, None)
    cur.execute("UPDATE facturas SET ecf_estado = ? WHERE id = ?", (registro["estado"], factura_id))
    return registro


def emitir_nota_credito(base, nota_id: int) -> dict | None:
    """e-CF 34 para una nota de crédito sobre una factura electrónica."""
    return _con_transaccion(base, _emitir_nota_credito, nota_id)


def _emitir_nota_credito(base, conn, nota_id: int) -> dict | None:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT nc.factura_original_id, nc.motivo, f.encf, f.fecha, IFNULL(c.documento,''), IFNULL(c.nombre,'')
        FROM notas_credito nc JOIN facturas f ON f.id = nc.factura_original_id
        LEFT JOIN clientes c ON c.id = f.cliente_id WHERE nc.id = ?
        """,
        (nota_id,),
    )
    fila = cur.fetchone()
    if not fila or not fila[2]:
        return None
    reserva = base.tomar_siguiente_encf(34, conn)
    if reserva is None:
        cur.execute(
            "INSERT INTO ecf_documentos (nota_credito_id, factura_id, tipo, encf, estado, mensaje) VALUES (?,?,34,?, 'error', ?)",
            (nota_id, fila[0], f"SIN-E34-{nota_id}", "No hay secuencia e-NCF tipo 34 para la nota de crédito"),
        )
        return {"estado": "error", "mensaje": "No hay secuencia e-NCF tipo 34"}
    encf, _venc = reserva
    cur.execute(
        """
        SELECT IFNULL(fd.descripcion, p.nombre), ncd.cantidad, ncd.monto, IFNULL(fd.impuesto_item, 0)
        FROM notas_credito_detalle ncd
        LEFT JOIN factura_detalle fd ON fd.id = ncd.factura_detalle_id
        LEFT JOIN productos p ON p.id = ncd.producto_id
        WHERE ncd.nota_credito_id = ?
        """,
        (nota_id,),
    )
    lineas = []
    for nombre, cant, monto, imp in cur.fetchall():
        cant = float(cant or 0) or 1.0
        lineas.append(dgii.LineaECF(nombre or "Devolución", cant, float(monto or 0) / cant, 0.0, float(imp or 0) > 0))
    rnc, razon, direccion = _empresa(base)
    rnc_comprador = dgii.solo_digitos(fila[4])
    datos = dgii.DatosECF(
        tipo=34, encf=encf, fecha_emision=datetime.now().strftime("%d-%m-%Y"), rnc_emisor=rnc,
        razon_social_emisor=razon, direccion_emisor=direccion, lineas=lineas,
        rnc_comprador=rnc_comprador if len(rnc_comprador) in (9, 11) else None, razon_social_comprador=fila[5],
        precios_incluyen_itbis=True, ncf_modificado=fila[2], fecha_ncf_modificado=dgii.fecha_dgii(fila[3]),
        razon_modificacion=fila[1],
    )
    return _firmar_y_guardar(base, conn, datos, fila[0], nota_id)


def enviar(base, doc_id: int) -> dict:
    """Envía a la DGII un documento firmado y guarda la respuesta."""
    with _bloqueo_envio:
        conn = base.get_connection()
        cur = conn.cursor()
        cur.execute("SELECT tipo, encf, total, estado, xml_envio, factura_id, ambiente FROM ecf_documentos WHERE id = ?", (doc_id,))
        fila = cur.fetchone()
        conn.close()
        if not fila:
            return {"estado": "error", "mensaje": "Documento no encontrado"}
        tipo, encf, total, estado, xml_envio, factura_id, ambiente = fila
        if not xml_envio or estado in ("sin_certificado",):
            return {"estado": estado, "mensaje": "El documento no está firmado: carga el certificado y vuelve a emitir"}
        cfg = config(base)
        resultado: dict
        try:
            cliente = dgii.ClienteDGII(ambiente or cfg["ambiente"], firmador(base))
            nombre = f"{cfg['rnc']}{encf}.xml"
            if tipo == 32 and Decimal(str(total)) < dgii.TOPE_CONSUMO:
                resp = cliente.enviar_rfce(nombre, xml_envio.encode())
            else:
                resp = cliente.enviar_ecf(nombre, xml_envio.encode())
            nuevo = dgii.estado_de_respuesta(resp)
            mensajes = resp.get("mensajes") or resp.get("texto") or ""
            resultado = {"estado": nuevo, "track_id": resp.get("trackId") or resp.get("TrackId"),
                         "mensaje": json.dumps(mensajes, ensure_ascii=False)[:500] if mensajes else None}
        except dgii.ErrorDGII as exc:
            resultado = {"estado": "error_envio", "track_id": None, "mensaje": str(exc)[:500]}
        except Exception as exc:  # noqa: BLE001
            resultado = {"estado": "error_envio", "track_id": None, "mensaje": f"{type(exc).__name__}: {exc}"[:500]}
        conn = base.get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE ecf_documentos SET estado = ?, track_id = COALESCE(?, track_id), mensaje = ?,
                   intentos = intentos + 1, actualizado = CURRENT_TIMESTAMP WHERE id = ?
            """,
            (resultado["estado"], resultado["track_id"], resultado["mensaje"], doc_id),
        )
        if factura_id:
            cur.execute("UPDATE facturas SET ecf_estado = ? WHERE id = ? AND encf = ?", (resultado["estado"], factura_id, encf))
        conn.commit()
        conn.close()
        return resultado


def enviar_en_segundo_plano(base_factory, doc_id: int) -> None:
    threading.Thread(target=lambda: enviar(base_factory(), doc_id), daemon=True).start()


def consultar(base, doc_id: int) -> dict:
    conn = base.get_connection()
    cur = conn.cursor()
    cur.execute("SELECT track_id, ambiente, factura_id, encf FROM ecf_documentos WHERE id = ?", (doc_id,))
    fila = cur.fetchone()
    conn.close()
    if not fila or not fila[0]:
        return {"estado": "sin_track", "mensaje": "Este documento no tiene TrackId de la DGII"}
    try:
        resp = dgii.ClienteDGII(fila[1] or config(base)["ambiente"], firmador(base)).consultar(fila[0])
    except dgii.ErrorDGII as exc:
        return {"estado": "error_envio", "mensaje": str(exc)}
    nuevo = dgii.estado_de_respuesta(resp)
    conn = base.get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE ecf_documentos SET estado = ?, mensaje = ?, actualizado = CURRENT_TIMESTAMP WHERE id = ?",
        (nuevo, json.dumps(resp.get("mensajes") or "", ensure_ascii=False)[:500], doc_id),
    )
    if fila[2]:
        cur.execute("UPDATE facturas SET ecf_estado = ? WHERE id = ? AND encf = ?", (nuevo, fila[2], fila[3]))
    conn.commit()
    conn.close()
    return {"estado": nuevo, "respuesta": resp}
