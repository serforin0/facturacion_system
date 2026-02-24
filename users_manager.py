import customtkinter as ctk
from tkinter import messagebox
from tkinter import ttk

from database import Database


class UsersManager(ctk.CTkFrame):
    def __init__(self, parent, current_user: str, current_role: str):
        super().__init__(parent, fg_color="#2B2B2B")
        self.pack(fill="both", expand=True)

        self.db = Database()
        self.current_user = current_user
        self.current_role = current_role

        self.selected_user_id = None

        self._build_ui()
        self.load_users()

    # ==========================
    #       UI / LAYOUT
    # ==========================
    def _build_ui(self):
        # Título
        title_label = ctk.CTkLabel(
            self,
            text="Gestión de Usuarios",
            font=("Arial", 22, "bold"),
            text_color="white"
        )
        title_label.pack(pady=10)

        main_frame = ctk.CTkFrame(self, fg_color="#2B2B2B")
        main_frame.pack(fill="both", expand=True, padx=10, pady=10)

        # ---- Panel izquierdo: formulario ----
        form_frame = ctk.CTkFrame(main_frame, fg_color="#333333")
        form_frame.pack(side="left", fill="y", padx=(0, 10), pady=5)

        ctk.CTkLabel(
            form_frame,
            text="Usuario:",
            font=("Arial", 14),
        ).grid(row=0, column=0, padx=10, pady=(15, 5), sticky="e")

        self.entry_username = ctk.CTkEntry(form_frame, width=220)
        self.entry_username.grid(row=0, column=1, padx=10, pady=(15, 5))

        ctk.CTkLabel(
            form_frame,
            text="Contraseña:",
            font=("Arial", 14),
        ).grid(row=1, column=0, padx=10, pady=5, sticky="e")

        self.entry_password = ctk.CTkEntry(form_frame, width=220, show="*")
        self.entry_password.grid(row=1, column=1, padx=10, pady=5)

        ctk.CTkLabel(
            form_frame,
            text="Rol:",
            font=("Arial", 14),
        ).grid(row=2, column=0, padx=10, pady=5, sticky="e")

        self.combo_role = ctk.CTkComboBox(
            form_frame,
            values=["admin", "user", "empleado"],
            width=220
        )
        self.combo_role.set("user")
        self.combo_role.grid(row=2, column=1, padx=10, pady=5)

        # Botones de acción
        buttons_frame = ctk.CTkFrame(form_frame, fg_color="#333333")
        buttons_frame.grid(row=3, column=0, columnspan=2, pady=15)

        btn_create = ctk.CTkButton(
            buttons_frame,
            text="➕ Crear",
            width=80,
            fg_color="#2B5F87",
            hover_color="#1D4C6B",
            command=self.handle_create_user
        )
        btn_create.pack(side="left", padx=5)

        btn_update = ctk.CTkButton(
            buttons_frame,
            text="🔄 Actualizar",
            width=100,
            fg_color="#4B7F2B",
            hover_color="#3A641F",
            command=self.handle_update_user
        )
        btn_update.pack(side="left", padx=5)

        btn_delete = ctk.CTkButton(
            buttons_frame,
            text="🗑 Eliminar",
            width=90,
            fg_color="#8B1E1E",
            hover_color="#6A1515",
            command=self.handle_delete_user
        )
        btn_delete.pack(side="left", padx=5)

        btn_clear = ctk.CTkButton(
            form_frame,
            text="Limpiar formulario",
            width=200,
            command=self.clear_form
        )
        btn_clear.grid(row=4, column=0, columnspan=2, pady=(0, 15))

        # ---- Panel derecho: tabla de usuarios ----
        table_frame = ctk.CTkFrame(main_frame, fg_color="#2B2B2B")
        table_frame.pack(side="left", fill="both", expand=True, pady=5)

        style = ttk.Style()
        style.theme_use("default")
        style.configure(
            "Treeview",
            background="#2B2B2B",
            foreground="white",
            fieldbackground="#2B2B2B",
            rowheight=26
        )
        style.configure("Treeview.Heading", font=("Arial", 11, "bold"))

        columns = ("id", "username", "role")
        self.table = ttk.Treeview(
            table_frame,
            columns=columns,
            show="headings",
            selectmode="browse"
        )
        self.table.heading("id", text="ID")
        self.table.heading("username", text="Usuario")
        self.table.heading("role", text="Rol")

        self.table.column("id", width=50, anchor="center")
        self.table.column("username", width=160, anchor="w")
        self.table.column("role", width=100, anchor="center")

        vsb = ttk.Scrollbar(table_frame, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=vsb.set)
        self.table.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")

        table_frame.grid_rowconfigure(0, weight=1)
        table_frame.grid_columnconfigure(0, weight=1)

        self.table.bind("<<TreeviewSelect>>", self.on_row_select)

    # ==========================
    #       LÓGICA CRUD
    # ==========================
    def load_users(self):
        for row in self.table.get_children():
            self.table.delete(row)

        users = self.db.get_users()
        for uid, username, role in users:
            self.table.insert("", "end", values=(uid, username, role))

    def clear_form(self):
        self.selected_user_id = None
        self.entry_username.delete(0, "end")
        self.entry_password.delete(0, "end")
        self.combo_role.set("user")
        # Deseleccionar fila en la tabla
        for item in self.table.selection():
            self.table.selection_remove(item)

    def on_row_select(self, event):
        selected = self.table.selection()
        if not selected:
            return
        item = self.table.item(selected[0])
        uid, username, role = item["values"]

        self.selected_user_id = uid
        self.entry_username.delete(0, "end")
        self.entry_username.insert(0, username)

        # Nunca mostramos la password actual
        self.entry_password.delete(0, "end")
        self.combo_role.set(role)

    def handle_create_user(self):
        username = self.entry_username.get().strip()
        password = self.entry_password.get().strip()
        role = self.combo_role.get().strip()

        if not username or not password:
            messagebox.showwarning("Aviso", "Usuario y contraseña son obligatorios")
            return

        try:
            self.db.create_user(username, password, role)
        except ValueError as e:
            messagebox.showerror("Error", str(e))
            return

        messagebox.showinfo("Éxito", "Usuario creado correctamente")
        self.clear_form()
        self.load_users()

    def handle_update_user(self):
        if self.selected_user_id is None:
            messagebox.showwarning("Aviso", "Selecciona un usuario de la lista")
            return

        username = self.entry_username.get().strip()
        password = self.entry_password.get().strip()
        role = self.combo_role.get().strip()

        if not username:
            messagebox.showwarning("Aviso", "El usuario no puede estar vacío")
            return

        # Si password está vacío, NO se cambia
        password_to_set = password if password else None

        # Evitar que un usuario no admin se suba a admin (opcional)
        # Aquí asumo que solo un admin puede entrar a este módulo,
        # pero dejamos el chequeo por seguridad.
        if self.current_role != "admin" and role == "admin":
            messagebox.showerror("Error", "Solo un administrador puede asignar rol admin")
            return

        self.db.update_user(self.selected_user_id, username, password_to_set, role)
        messagebox.showinfo("Éxito", "Usuario actualizado")
        self.load_users()

    def handle_delete_user(self):
        if self.selected_user_id is None:
            messagebox.showwarning("Aviso", "Selecciona un usuario de la lista")
            return

        item = self.table.item(self.table.selection()[0])
        uid, username, role = item["values"]

        # No permitir borrarse a uno mismo
        if username == self.current_user:
            messagebox.showerror("Error", "No puedes eliminar tu propio usuario")
            return

        if role == "admin":
            resp = messagebox.askyesno(
                "Confirmar",
                f"Vas a eliminar un usuario ADMIN ('{username}'). ¿Seguro?"
            )
            if not resp:
                return
        else:
            resp = messagebox.askyesno(
                "Confirmar",
                f"¿Eliminar al usuario '{username}'?"
            )
            if not resp:
                return

        self.db.delete_user(uid)
        messagebox.showinfo("Éxito", "Usuario eliminado")
        self.clear_form()
        self.load_users()
