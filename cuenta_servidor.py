"""Consulta el servidor web cuando el usuario no existe en la base local."""
import json
import urllib.error
import urllib.request

SERVIDOR_POR_DEFECTO = "https://mdalliancegroup.online"


class ServidorInalcanzable(Exception):
    pass


def url_servidor(db) -> str:
    guardada = ""
    if db is not None:
        guardada = (db.get_config("servidor_facturacion", "") or "").strip()
    return (guardada or SERVIDOR_POR_DEFECTO).rstrip("/")


def consultar_usuario(url: str, username: str, password: str) -> str | None:
    """Devuelve el rol si el servidor acepta las credenciales.

    None significa que el usuario no está registrado o la contraseña no coincide.
    ServidorInalcanzable si no hay respuesta.
    """
    destino = url.rstrip("/") + "/api/login"
    cuerpo = json.dumps({"username": username, "password": password}).encode("utf-8")
    req = urllib.request.Request(
        destino,
        data=cuerpo,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            return None
        raise ServidorInalcanzable(f"El servidor respondió {exc.code}.") from exc
    except urllib.error.URLError as exc:
        raise ServidorInalcanzable("No se pudo conectar con el servidor.") from exc
    except (TimeoutError, json.JSONDecodeError) as exc:
        raise ServidorInalcanzable("El servidor no respondió con un usuario válido.") from exc
    rol = (data.get("rol") or "").strip()
    return rol or None
