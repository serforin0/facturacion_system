"""
Vista previa de PDF dentro de la app (requiere pymupdf). Si no está instalado, ofrece alternativas.
"""
import os
import subprocess
import sys
import tempfile
from tkinter import messagebox

import customtkinter as ctk
from PIL import Image

try:
    import fitz  # PyMuPDF

    _HAS_FITZ = True
except ImportError:
    _HAS_FITZ = False


def _print_pdf_path(path: str) -> bool:
    try:
        if sys.platform == "darwin":
            subprocess.run(["lpr", path], check=False)
            return True
        if sys.platform.startswith("win"):
            os.startfile(path, "print")  # noqa: S606
            return True
        subprocess.run(["lpr", path], check=False)
        return True
    except Exception as e:
        messagebox.showerror("Imprimir", f"No se pudo enviar a la impresora:\n{e}")
        return False


def _open_system_viewer(path: str):
    try:
        if sys.platform == "darwin":
            subprocess.run(["open", path], check=False)
        elif sys.platform.startswith("win"):
            os.startfile(path)  # noqa: S606
        else:
            subprocess.run(["xdg-open", path], check=False)
    except Exception as e:
        messagebox.showerror("Abrir", str(e))


class ReportPreviewWindow(ctk.CTkToplevel):
    """Visor de PDF con zoom y navegación por páginas."""

    def __init__(self, master, pdf_bytes: bytes, *, title="Vista previa del reporte"):
        super().__init__(master)
        self.title(title)
        self.geometry("920x720")
        self._pdf_bytes = pdf_bytes
        self._doc = None
        self._page_index = 0
        self._zoom = 1.15
        self._photo = None

        self.configure(fg_color=("#2B2B2B", "#1a1a1a"))
        self.transient(master.winfo_toplevel())
        self.grab_set()

        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", padx=8, pady=6)

        ctk.CTkButton(bar, text="◀", width=36, command=self._prev).pack(side="left", padx=2)
        ctk.CTkButton(bar, text="▶", width=36, command=self._next).pack(side="left", padx=2)
        self.lbl_page = ctk.CTkLabel(bar, text="Pág. —", width=100)
        self.lbl_page.pack(side="left", padx=8)

        ctk.CTkButton(bar, text="−", width=32, command=lambda: self._set_zoom(-0.15)).pack(
            side="left", padx=4
        )
        ctk.CTkButton(bar, text="+", width=32, command=lambda: self._set_zoom(0.15)).pack(
            side="left", padx=2
        )
        self.lbl_zoom = ctk.CTkLabel(bar, text="115%", width=56)
        self.lbl_zoom.pack(side="left", padx=6)

        ctk.CTkButton(
            bar, text="Guardar PDF…", fg_color="#2563EB", command=self._guardar
        ).pack(side="right", padx=4)
        ctk.CTkButton(
            bar, text="Imprimir", fg_color="#059669", command=self._imprimir
        ).pack(side="right", padx=4)
        ctk.CTkButton(
            bar, text="Visor externo", fg_color="#64748B", command=self._externo
        ).pack(side="right", padx=4)
        ctk.CTkButton(bar, text="Cerrar", fg_color="#6B7280", command=self.destroy).pack(
            side="right", padx=4
        )

        self.scroll = ctk.CTkScrollableFrame(self, fg_color="#3B3B3B")
        self.scroll.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        self.lbl_img = ctk.CTkLabel(self.scroll, text="")
        self.lbl_img.pack(pady=8)

        self._temp_path = None

        if not _HAS_FITZ:
            self.lbl_img.configure(
                text=(
                    "Instale PyMuPDF para vista previa integrada:\n"
                    "  python3 -m pip install pymupdf\n\n"
                    "Mientras tanto use «Visor externo» o «Guardar PDF»."
                ),
                font=("Arial", 13),
                text_color="white",
            )
            return

        try:
            self._doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        except Exception as e:
            self.lbl_img.configure(text=f"No se pudo leer el PDF:\n{e}")
            return

        self._render()

    def _set_zoom(self, delta: float):
        self._zoom = max(0.5, min(2.5, self._zoom + delta))
        self.lbl_zoom.configure(text=f"{int(self._zoom * 100)}%")
        self._render()

    def _prev(self):
        if self._doc and self._page_index > 0:
            self._page_index -= 1
            self._render()

    def _next(self):
        if self._doc and self._page_index < len(self._doc) - 1:
            self._page_index += 1
            self._render()

    def _render(self):
        if not self._doc:
            return
        page = self._doc[self._page_index]
        mat = fitz.Matrix(self._zoom, self._zoom)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        self._photo = ctk.CTkImage(light_image=img, dark_image=img, size=img.size)
        self.lbl_img.configure(image=self._photo, text="")
        self.lbl_page.configure(
            text=f"Pág. {self._page_index + 1} / {len(self._doc)}"
        )

    def _ensure_temp_file(self) -> str:
        if self._temp_path and os.path.isfile(self._temp_path):
            return self._temp_path
        fd, path = tempfile.mkstemp(suffix=".pdf")
        os.close(fd)
        with open(path, "wb") as f:
            f.write(self._pdf_bytes)
        self._temp_path = path
        return path

    def _guardar(self):
        from tkinter import filedialog
        from datetime import datetime

        path = filedialog.asksaveasfilename(
            defaultextension=".pdf",
            filetypes=[("PDF", "*.pdf")],
            initialfile=f"Valor_inventario_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf",
        )
        if path:
            try:
                with open(path, "wb") as f:
                    f.write(self._pdf_bytes)
                messagebox.showinfo("Guardar", f"Archivo guardado:\n{path}")
            except OSError as e:
                messagebox.showerror("Guardar", str(e))

    def _imprimir(self):
        path = self._ensure_temp_file()
        if _print_pdf_path(path):
            messagebox.showinfo("Imprimir", "Trabajo enviado a la impresora predeterminada.")

    def _externo(self):
        path = self._ensure_temp_file()
        _open_system_viewer(path)

    def destroy(self):
        if self._doc:
            try:
                self._doc.close()
            except Exception:
                pass
        if self._temp_path and os.path.isfile(self._temp_path):
            try:
                os.remove(self._temp_path)
            except OSError:
                pass
        super().destroy()
