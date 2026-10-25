"""Reglas de mostrador que no dependen de la base."""


def limpiar_nota(nota: str | None) -> str:
    return " ".join((nota or "").split())[:80]


def propina_legal(total: float, activa: bool, porcentaje: float = 10) -> float:
    if not activa:
        return 0.0
    base = max(0.0, float(total or 0))
    return round(base * float(porcentaje) / 100.0, 2)


def partir_lineas(lineas: list[dict], cobrar: list[int]) -> tuple[list[dict], list[dict]]:
    elegidas = {int(i) for i in cobrar}
    van = [linea for i, linea in enumerate(lineas) if i in elegidas]
    quedan = [linea for i, linea in enumerate(lineas) if i not in elegidas]
    if not van or not quedan:
        raise ValueError("Elige una parte para cobrar y deja otra en la mesa")
    return van, quedan


def aviso_stock(stock, minimo) -> bool:
    if stock is None:
        return False
    return float(stock) <= float(minimo or 0)


def puntos_ganados(total: float, cada: float = 100) -> int:
    if total <= 0 or cada <= 0:
        return 0
    return int(float(total) // float(cada))


def rebaja_puntos(puntos: int, usar: int, valor: float = 1) -> tuple[int, float]:
    usados = max(0, min(int(puntos or 0), int(usar or 0)))
    return usados, round(usados * float(valor), 2)


def precio_con_extras(precio: float, extras: list[dict]) -> tuple[float, str]:
    extra = round(sum(float(item.get("precio") or 0) for item in extras), 2)
    nombres = [str(item.get("nombre") or "").strip() for item in extras if str(item.get("nombre") or "").strip()]
    return round(float(precio or 0) + extra, 2), ", ".join(nombres)


def asiento_venta(*, caja: float, banco: float, saldo: float, fiado: float, ventas: float, itbis: float, propina: float) -> list[tuple[str, float, float]]:
    """Débito y crédito por código de cuenta. Caja, banco, saldo a favor y fiado contra ventas, ITBIS y propina."""
    lineas: list[tuple[str, float, float]] = []

    def agregar(codigo: str, debito: float, credito: float) -> None:
        debito, credito = round(float(debito or 0), 2), round(float(credito or 0), 2)
        if abs(debito) < 0.004 and abs(credito) < 0.004:
            return
        lineas.append((codigo, debito, credito))

    agregar("1.1", caja, 0)
    agregar("1.2", banco, 0)
    agregar("2.3", saldo, 0)
    agregar("1.3", fiado, 0)
    agregar("4.1", 0, ventas)
    agregar("2.1", 0, itbis)
    agregar("2.2", 0, propina)
    debe = round(sum(item[1] for item in lineas), 2)
    haber = round(sum(item[2] for item in lineas), 2)
    if abs(debe - haber) > 0.05:
        raise ValueError(f"Asiento descuadrado: debe {debe} y haber {haber}")
    return lineas


def asiento_simple(debito: str, credito: str, monto: float) -> list[tuple[str, float, float]]:
    monto = round(float(monto or 0), 2)
    if monto <= 0:
        raise ValueError("El monto tiene que ser mayor que cero")
    return [(debito, monto, 0), (credito, 0, monto)]


def autorizacion_ok(monto_tarjeta: float, codigo: str) -> str:
    if float(monto_tarjeta or 0) <= 0:
        return ""
    limpio = "".join(ch for ch in (codigo or "") if ch.isalnum())
    if len(limpio) < 4:
        raise ValueError("Escribe la autorización de la tarjeta, la del voucher")
    return limpio[:20]
