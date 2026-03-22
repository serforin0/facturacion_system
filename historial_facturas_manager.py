import customtkinter as ctk
from tkinter import messagebox, ttk
import os
import sys
import tempfile
import subprocess

from app_paths import data_directory, is_frozen
from database import Database


class HistorialFacturasManager:
    """
    Módulo para ver todas las facturas y reimprimir tickets.
    - Lista facturas desde la base de datos.
    - Permite filtrar por número de factura o usuario.
    - Permite reimprimir leyendo el archivo guardado en /facturas
      o reconstruyendo el ticket desde la BD si el archivo no existe.
    """

    def __init__(self, parent, current_role=None):
        self.parent = parent
        self.db = Database()
        self.current_role = current_role

        base_dir = (
            data_directory()
            if is_frozen()
            else os.path.dirname(os.path.abspath(__file__))
        )
        self.facturas_dir = os.path.join(base_dir, "facturas")
        os.makedirs(self.facturas_dir, exist_ok=True)

        self.tree = None
        self.entry_buscar = None
        self.combo_filtro = None

        self._setup_ui()
        self._cargar_facturas()

    # ==============================
    #           UI
    # ==============================

    def _setup_ui(self):
        main_frame = ctk.CTkFrame(self.parent, fg_color="#2B2B2B")
        main_frame.pack(fill="both", expand=True, padx=5, pady=5)

        # ----- Filtro / búsqueda -----
        search_frame = ctk.CTkFrame(main_frame, fg_color="#1F1F1F")
        search_frame.pack(fill="x", padx=5, pady=(0, 5))

        ctk.CTkLabel(
            search_frame,
            text="Buscar:",
            font=("Arial", 12)
        ).grid(row=0, column=0, padx=6, pady=6, sticky="w")

        self.entry_buscar = ctk.CTkEntry(search_frame, width=200)
        self.entry_buscar.grid(row=0, column=1, padx=5, pady=6, sticky="w")

        self.combo_filtro = ctk.CTkComboBox(
            search_frame,
            values=["Número", "Usuario"],
            width=120
        )
        self.combo_filtro.set("Número")
        self.combo_filtro.grid(row=0, column=2, padx=5, pady=6)

        btn_buscar = ctk.CTkButton(
            search_frame,
            text="Filtrar",
            width=80,
            command=self._cargar_facturas
        )
        btn_buscar.grid(row=0, column=3, padx=6, pady=6)

        btn_limpiar = ctk.CTkButton(
            search_frame,
            text="Limpiar",
            width=80,
            fg_color="#7f8c8d",
            command=self._limpiar_filtros
        )
        btn_limpiar.grid(row=0, column=4, padx=6, pady=6)

        # ----- Tabla de facturas -----
        table_frame = ctk.CTkFrame(main_frame, fg_color="#1F1F1F")
        table_frame.pack(fill="both", expand=True, padx=5, pady=5)

        lbl = ctk.CTkLabel(
            table_frame,
            text="Historial de facturas",
            font=("Arial", 13, "bold"),
            text_color="white"
        )
        lbl.pack(pady=(4, 2))

        tree_container = ctk.CTkFrame(table_frame, fg_color="#1F1F1F")
        tree_container.pack(fill="both", expand=True, padx=4, pady=(0, 4))

        style = ttk.Style()
        style.theme_use("default")
        style.configure(
            "HistFacturas.Treeview",
            background="#2a2d2e",
            foreground="white",
            rowheight=22,
            fieldbackground="#2a2d2e",
            borderwidth=0
        )
        style.configure(
            "HistFacturas.Treeview.Heading",
            background="#3B3B3B",
            foreground="white",
            relief="flat",
            font=("Arial", 10, "bold")
        )
        style.map(
            "HistFacturas.Treeview",
            background=[("selected", "#22559b")]
        )

        self.tree = ttk.Treeview(
            tree_container,
            columns=("Numero", "Fecha", "Total", "Estado", "Usuario"),
            show="headings",
            style="HistFacturas.Treeview"
        )

        cols = [
            ("Numero", 130),
            ("Fecha", 160),
            ("Total", 90),
            ("Estado", 90),
            ("Usuario", 120),
        ]
        for col, width in cols:
            self.tree.heading(col, text=col)
            self.tree.column(col, width=width, anchor="center")

        scrollbar = ttk.Scrollbar(
            tree_container,
            orient="vertical",
            command=self.tree.yview
        )
        self.tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")

        self.tree.pack(side="left", fill="both", expand=True)

        # ----- Botones de acciones -----
        btn_frame = ctk.CTkFrame(main_frame, fg_color="#1F1F1F")
        btn_frame.pack(fill="x", padx=5, pady=(0, 5))

        ctk.CTkButton(
            btn_frame,
            text="🔄 Reimprimir factura",
            width=180,
            fg_color="#2B5F87",
            command=self._reimprimir_seleccionada
        ).pack(side="left", padx=6, pady=6)

        ctk.CTkButton(
            btn_frame,
            text="Ver ticket (texto)",
            width=150,
            fg_color="#34495e",
            command=self._ver_ticket_seleccionado
        ).pack(side="left", padx=6, pady=6)

        if self.current_role == "admin":
            btn_eliminar = ctk.CTkButton(
                btn_frame,
                text="🗑️ Eliminar Historial completo",
                width=200,
                fg_color="#c0392b",
                hover_color="#a53125",
                command=self._eliminar_historial_completo
            )
            # pack a la derecha para separarlo de los otros botones
            btn_eliminar.pack(side="right", padx=6, pady=6)

    # ==============================
    #      ELIMINAR HISTORIAL
    # ==============================
    def _eliminar_historial_completo(self):
        # 1. Confirmación de seguridad
        respuesta = messagebox.askyesno(
            "¡ADVERTENCIA DE SEGURIDAD!",
            "Estás a punto de ELIMINAR TODO EL HISTORIAL DE FACTURACIÓN.\n\n"
            "Esto borrará de forma irreversible todas las facturas, pagos, "
            "notas de crédito y movimientos de kardex asociados a ventas.\n"
            "También se borrarán los tickets generados y se reiniciará la numeración.\n\n"
            "Solo mantendrás usuarios, productos y configuraciones.\n\n"
            "¿Estás completamente seguro de que deseas iniciar desde 0?",
            icon="warning"
        )
        
        if not respuesta:
            return
            
        # 2. Proceder con el borrado en la BD
        exito, msj = self.db.clear_billing_history()
        
        if exito:
            # 3. Borrar los archivos de tickets (.txt)
            try:
                for filename in os.listdir(self.facturas_dir):
                    if filename.endswith(".txt"):
                        file_path = os.path.join(self.facturas_dir, filename)
                        os.remove(file_path)
            except Exception as e:
                print("Error al borrar archivos de tickets:", e)
                
            # 4. Refrescar la tabla
            self._cargar_facturas()
            
            messagebox.showinfo("Éxito", "El historial de facturación ha sido eliminado por completo.")
        else:
            messagebox.showerror("Error", msj)

    # ==============================
    #        CARGAR FACTURAS
    # ==============================

    def _limpiar_filtros(self):
        self.entry_buscar.delete(0, "end")
        self.combo_filtro.set("Número")
        self._cargar_facturas()

    def _cargar_facturas(self):
        """
        Carga las facturas desde la BD, con filtro opcional.
        """
        filtro_texto = self.entry_buscar.get().strip()
        filtro_tipo = self.combo_filtro.get()

        conn = self.db.get_connection()
        cursor = conn.cursor()

        base_query = """
            SELECT id, numero, fecha, total, estado, usuario
            FROM facturas
        """
        params = []

        if filtro_texto:
            if filtro_tipo == "Número":
                base_query += " WHERE numero LIKE ?"
                params.append(f"%{filtro_texto}%")
            elif filtro_tipo == "Usuario":
                base_query += " WHERE usuario LIKE ?"
                params.append(f"%{filtro_texto}%")

        base_query += " ORDER BY fecha DESC LIMIT 300"

        cursor.execute(base_query, params)
        rows = cursor.fetchall()
        conn.close()

        # Limpiar tabla
        for item in self.tree.get_children():
            self.tree.delete(item)

        # Insertar filas
        for row in rows:
            fid, numero, fecha, total, estado, usuario = row
            self.tree.insert(
                "",
                "end",
                iid=str(fid),  # usamos el ID de factura como iid
                values=(
                    numero,
                    fecha,
                    f"{total:.2f}",
                    estado,
                    usuario or ""
                )
            )

    # ==============================
    #       REIMPRESIÓN
    # ==============================

    def _reimprimir_seleccionada(self):
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("Selecciona una factura", "Selecciona una factura para reimprimir.")
            return

        fid = int(selected[0])
        values = self.tree.item(selected[0], "values")
        numero = values[0]

        # Intentar leer ticket desde archivo
        ticket_text = self._leer_ticket_archivado(numero)
        if ticket_text is None:
            # reconstruir desde la BD
            if not messagebox.askyesno(
                "Ticket no encontrado",
                "No se encontró el archivo del ticket.\n"
                "¿Quieres reconstruirlo desde la base de datos?"
            ):
                return
            ticket_text = self._generar_ticket_desde_bd(fid)
            if ticket_text is None:
                messagebox.showerror(
                    "Error",
                    "No se pudo generar el ticket desde la base de datos."
                )
                return

            # guardar nuevamente
            self._guardar_ticket_archivado(numero, ticket_text)

        # Enviar a la impresora
        self._send_to_printer(ticket_text)
        messagebox.showinfo("Reimpresión", f"Se envió a imprimir la factura {numero}.")

    def _leer_ticket_archivado(self, numero):
        """
        Lee el ticket desde la carpeta /facturas si existe.
        """
        try:
            path = os.path.join(self.facturas_dir, f"{numero}.txt")
            if not os.path.exists(path):
                return None
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception as e:
            print("Error leyendo ticket archivado:", e)
            return None

    def _guardar_ticket_archivado(self, numero, ticket_text):
        """
        Guarda el ticket en la carpeta /facturas.
        """
        try:
            path = os.path.join(self.facturas_dir, f"{numero}.txt")
            with open(path, "w", encoding="utf-8") as f:
                f.write(ticket_text)
        except Exception as e:
            print("Error guardando ticket archivado:", e)

    # ==============================
    #  RECONSTRUIR TICKET DESDE BD
    # ==============================

    def _generar_ticket_desde_bd(self, factura_id):
        """
        Vuelve a construir el ticket usando la misma lógica que FacturaManager,
        leyendo datos de facturas + factura_detalle.
        """
        conn = self.db.get_connection()
        cursor = conn.cursor()

        # Encabezado
        cursor.execute("""
            SELECT numero, fecha, subtotal, descuento_total, total, usuario
            FROM facturas
            WHERE id = ?
        """, (factura_id,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            return None

        numero, fecha, subtotal, descuento_total, total, usuario = row

        # Detalles
        cursor.execute("""
            SELECT descripcion, cantidad, precio_unitario, total_linea
            FROM factura_detalle
            WHERE factura_id = ?
        """, (factura_id,))
        detalles = cursor.fetchall()
        conn.close()

        # Subtotal bruto = subtotal neto + descuento_total
        subtotal_bruto = float(subtotal) + float(descuento_total)

        # ancho de ticket desde config
        ticket_width = self.db.get_ticket_width()
        lines = []

        def center(text):
            return text.center(ticket_width)

        def sep(char="-"):
            return char * ticket_width

        # Encabezado
        lines.append(center("ESQUINA TROPICAL"))
        lines.append(center("RNC: N/A"))
        lines.append(center("Tel: N/A"))
        lines.append(sep())
        lines.append(f"Factura: {numero}")
        lines.append(f"Fecha : {fecha}")
        if usuario:
            lines.append(f"Cajero: {usuario}")
        lines.append(sep())

        # Detalles
        lines.append("DESCRIPCIÓN")
        lines.append("CANT x P.U" + " " * (ticket_width - len("CANT x P.U") - 7) + "IMPORTE")
        lines.append(sep())

        for desc, cant, pu, total_linea in detalles:
            desc = str(desc)
            # cortar descripción si excede el ancho
            while len(desc) > ticket_width:
                lines.append(desc[:ticket_width])
                desc = desc[ticket_width:]
            if desc:
                lines.append(desc)

            left = f"{cant:.2f} x {pu:.2f}"
            right = f"{total_linea:.2f}"

            spaces = ticket_width - len(left) - len(right)
            if spaces < 1:
                spaces = 1
            lines.append(left + " " * spaces + right)

        # Totales
        lines.append(sep())
        lines.append(f"SUBTOTAL:".ljust(ticket_width - 10) + f"{subtotal_bruto:10.2f}")
        lines.append(f"DESCUENTO:".ljust(ticket_width - 10) + f"{descuento_total:10.2f}")
        lines.append(f"TOTAL:".ljust(ticket_width - 10) + f"{total:10.2f}")
        lines.append(sep())
        lines.append(center("GRACIAS POR SU COMPRA"))
        lines.append("\n\n\n")

        return "\n".join(lines)

    # ==============================
    #   VER TICKET EN VENTANA
    # ==============================

    def _ver_ticket_seleccionado(self):
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("Selecciona una factura", "Selecciona una factura para ver el ticket.")
            return

        fid = int(selected[0])
        values = self.tree.item(selected[0], "values")
        numero = values[0]

        ticket_text = self._leer_ticket_archivado(numero)
        if ticket_text is None:
            ticket_text = self._generar_ticket_desde_bd(fid)
            if ticket_text is None:
                messagebox.showerror("Error", "No se pudo obtener el ticket.")
                return

        win = ctk.CTkToplevel(self.parent)
        win.title(f"Ticket factura {numero}")
        win.geometry("420x500")

        txt = ctk.CTkTextbox(win, font=("Consolas", 11))
        txt.pack(fill="both", expand=True, padx=5, pady=5)
        txt.insert("1.0", ticket_text)
        txt.configure(state="disabled")

    # ==============================
    #   IMPRESIÓN (igual que antes)
    # ==============================

    def _send_to_printer(self, ticket_text):
        """
        Imprime usando Notepad /p en Windows.
        """
        try:
            if sys.platform.startswith("win"):
                with tempfile.NamedTemporaryFile(
                    delete=False,
                    suffix=".txt",
                    mode="w",
                    encoding="utf-8"
                ) as tmp:
                    tmp.write(ticket_text)
                    tmp_path = tmp.name

                subprocess.run(["notepad.exe", "/p", tmp_path], check=True)
            else:
                with tempfile.NamedTemporaryFile(
                    delete=False,
                    suffix=".txt",
                    mode="w",
                    encoding="utf-8"
                ) as tmp:
                    tmp.write(ticket_text)
                    tmp_path = tmp.name

                messagebox.showinfo(
                    "Impresión",
                    f"Ticket generado en:\n{tmp_path}\nImprímelo manualmente."
                )
        except Exception as e:
            messagebox.showerror("Error de impresión", f"No se pudo imprimir:\n{e}")
