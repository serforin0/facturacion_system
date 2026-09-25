# 👥 Badoo Multi-Cuenta Auto-Login & Auto-Clicker Pro

Sistema de automatización para Badoo con soporte completo para **múltiples cuentas** (gestión de lista de correos y contraseñas).

---

## 🌟 Novedades Multi-Cuenta

1. **Gestor de Cuentas:** Guarda y administra tus diferentes correos y contraseñas en `cuentas.json`.
2. **Selector en la Interfaz (Dropdown):** Selecciona cualquier correo registrado para auto-rellenar sus datos y ejecutar el bot al instante.
3. **Botones 💾 Guardar / 🗑️ Eliminar:** Agrega o borra cuentas fácilmente directamente desde la app.
4. **Sesiones Aisladas:** Cada cuenta guarda su perfil y cookies en su propia carpeta en `.badoo_sessions/`, evitando que una cuenta cierre la sesión de otra.
5. **Ejecución Secuencial Automatizada:** Opción *"Ejecutar TODAS las cuentas en secuencia"* para automatizar likes consecutivamente con cada cuenta de tu lista.

---

## 🚀 Cómo Ejecutar la Aplicación de Escritorio

```bash
.venv/bin/python badoo_gui_app.py
```

### Pasos para usar el Multi-Cuenta en la App:
1. Abre la aplicación.
2. Ingresa un **Correo** y una **Contraseña** y haz clic en **💾 Guardar/Añadir**.
3. Repite para todas las cuentas que desees agregar.
4. Selecciona la cuenta que deseas usar desde el menú desplegable **👤 Seleccionar Cuenta**.
5. *(Opcional)* Marca la casilla **🔁 Ejecutar TODAS las cuentas en secuencia** si deseas procesar toda tu lista automáticamente.
6. Haz clic en **▶ INICIAR BOT**.

---

## 💻 Ejecución por Terminal (CLI Multi-Cuenta)

Para ejecutar todas las cuentas guardadas en `cuentas.json` desde la consola:

```bash
.venv/bin/python badoo_bot.py --all-accounts
```

O para una sola cuenta específica:

```bash
.venv/bin/python badoo_bot.py --email "correo1@ejemplo.com" --password "clave1"
```
