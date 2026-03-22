@echo off
REM Ejecutar en Windows desde la raíz del repositorio (o con cd aquí).
cd /d "%~dp0.."

python -m pip install -U pip
python -m pip install -r requirements-build.txt

if exist dist\SistemaFacturacion.exe del /q dist\SistemaFacturacion.exe
if exist build rmdir /s /q build

python -m PyInstaller --noconfirm main.spec

echo.
echo Listo: dist\SistemaFacturacion.exe
echo Copie tambien una carpeta vacia o deje que el programa cree: facturas, product_images
pause
