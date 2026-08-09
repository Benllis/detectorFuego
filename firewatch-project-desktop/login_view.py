import customtkinter as ctk
from database import init_db, verify_user

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")


class LoginWindow(ctk.CTk):
    def __init__(self, on_login_success):
        super().__init__()
        
        # Callback para abrir la ventana principal tras iniciar sesión
        self.on_login_success = on_login_success

        self.title("FireWatch Central — Inicio de Sesión")
        self.geometry("400x500")
        self.resizable(False, False)

        # Contenedor principal
        self.card = ctk.CTkFrame(self, corner_radius=15)
        self.card.pack(padx=30, pady=30, fill="both", expand=True)

        # Encabezado
        self.lbl_title = ctk.CTkLabel(
            self.card, 
            text="🔥 FireWatch", 
            font=("Arial", 28, "bold"),
            text_color="#FF4D4D"
        )
        self.lbl_title.pack(pady=(30, 5))

        self.lbl_subtitle = ctk.CTkLabel(
            self.card, 
            text="Central de Monitoreo de Incendios", 
            font=("Arial", 12)
        )
        self.lbl_subtitle.pack(pady=(0, 25))

        # Campo Usuario
        self.txt_user = ctk.CTkEntry(
            self.card, 
            placeholder_text="Usuario", 
            width=280, 
            height=40
        )
        self.txt_user.pack(pady=10)

        # Campo Contraseña
        self.txt_pass = ctk.CTkEntry(
            self.card, 
            placeholder_text="Contraseña", 
            show="*", 
            width=280, 
            height=40
        )
        self.txt_pass.pack(pady=10)
        self.txt_pass.bind("<Return>", lambda event: self.attempt_login())

        # Etiqueta de Error
        self.lbl_error = ctk.CTkLabel(
            self.card, 
            text="", 
            text_color="#FF3333", 
            font=("Arial", 11)
        )
        self.lbl_error.pack(pady=5)

        # Botón Iniciar Sesión
        self.btn_login = ctk.CTkButton(
            self.card, 
            text="Iniciar Sesión", 
            command=self.attempt_login,
            width=280, 
            height=40,
            font=("Arial", 14, "bold")
        )
        self.btn_login.pack(pady=15)

    def attempt_login(self):
        username = self.txt_user.get().strip()
        password = self.txt_pass.get().strip()

        if not username or not password:
            self.lbl_error.configure(text="Por favor ingresa usuario y contraseña.")
            return

        user_data = verify_user(username, password)

        if user_data:
            self.lbl_error.configure(text="")
            self.destroy()  # Cierra la ventana de login
            self.on_login_success(user_data)  # Pasa la información del usuario a la app principal
        else:
            self.lbl_error.configure(text="Usuario o contraseña incorrectos.")


if __name__ == "__main__":
    init_db()  # Asegurar que la base de datos existe

    def demo_success(user):
        print(f"Sesión iniciada con éxito por: {user['nombre_completo']} ({user['rol']})")

    app = LoginWindow(on_login_success=demo_success)
    app.mainloop()