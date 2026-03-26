"""
VentasFlow - Backend FastAPI
Compatible con Python 3.13 + Pydantic v2
"""

import os
import io
import json
import time
import sqlite3
import random
import string
import smtplib
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional, List
from contextlib import contextmanager

from fastapi import FastAPI, HTTPException, Depends, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
# --- CAMBIO 1: NUEVAS IMPORTACIONES PARA EL FRONTEND ---
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
# ------------------------------------------------------
from pydantic import BaseModel
from passlib.hash import pbkdf2_sha256
from jose import JWTError, jwt

# ─── CONFIG ───────────────────────────────────────────────────────────────────
SECRET_KEY = os.getenv("SECRET_KEY", "ventasflow-super-secret-key-2024")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 8

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
SMTP_USER = os.getenv("SMTP_USER", "ventasflow.demo@gmail.com")
SMTP_PASS = os.getenv("SMTP_PASS", "tu_app_password_aqui")
SMTP_ENABLED = os.getenv("SMTP_ENABLED", "false").lower() == "true"

DB_PATH = "ventasflow.db"

# ─── BASE DE DATOS ────────────────────────────────────────────────────────────
@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

# ─── PLANES DE LICENCIA ───────────────────────────────────────────────────────
def get_license_config(license_type: str):
    license_type = license_type.upper()

    plans = {
        "BASIC": {
            "max_users": 3,
            "dashboard_enabled": 0,
            "reports_enabled": 0
        },
        "PRO": {
            "max_users": 6,
            "dashboard_enabled": 1,
            "reports_enabled": 1
        },
        "ENTERPRISE": {
            "max_users": 10,
            "dashboard_enabled": 1,
            "reports_enabled": 1
        }
    }

    if license_type not in plans:
        raise HTTPException(
            status_code=400,
            detail="Tipo de licencia inválido. Use BASIC, PRO o ENTERPRISE"
        )

    return plans[license_type]

# ─── INIT DB / SEED ───────────────────────────────────────────────────────────
def init_db():
    with get_db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            username    TEXT UNIQUE NOT NULL,
            email       TEXT UNIQUE NOT NULL,
            password    TEXT NOT NULL,
            role        TEXT NOT NULL DEFAULT 'CAJERO',
            license     TEXT NOT NULL DEFAULT 'PRO',
            photo_url   TEXT DEFAULT '',
            temp_pass   TEXT DEFAULT '',
            reset_code  TEXT DEFAULT '',
            reset_exp   INTEGER DEFAULT 0,
            active      INTEGER NOT NULL DEFAULT 1,
            created_at  TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS licenses (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            license_type        TEXT NOT NULL,
            start_date          TEXT NOT NULL,
            end_date            TEXT NOT NULL,
            max_users           INTEGER NOT NULL,
            dashboard_enabled   INTEGER NOT NULL DEFAULT 0,
            reports_enabled     INTEGER NOT NULL DEFAULT 0,
            status              INTEGER NOT NULL DEFAULT 1,
            created_at          TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS products (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL,
            sku         TEXT UNIQUE NOT NULL,
            category    TEXT NOT NULL,
            cost_price  REAL NOT NULL,
            sale_price  REAL NOT NULL,
            stock       INTEGER NOT NULL DEFAULT 0,
            active      INTEGER NOT NULL DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS sales (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            cashier_id      INTEGER NOT NULL,
            cashier_name    TEXT NOT NULL,
            customer_name   TEXT DEFAULT 'Consumidor Final',
            customer_nit    TEXT DEFAULT 'CF',
            payment_method  TEXT DEFAULT 'EFECTIVO',
            total           REAL NOT NULL,
            items_json      TEXT NOT NULL,
            created_at      TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS sale_items (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            sale_id      INTEGER NOT NULL,
            product_id   INTEGER NOT NULL,
            product_name TEXT NOT NULL,
            quantity     INTEGER NOT NULL,
            unit_price   REAL NOT NULL,
            subtotal     REAL NOT NULL
        );
        """)

        # SUPERADMIN por defecto
        superadmin_exists = conn.execute(
            "SELECT id FROM users WHERE username='superadmin'"
        ).fetchone()
        if not superadmin_exists:
            conn.execute(
                "INSERT INTO users (username,email,password,role,license,active) VALUES (?,?,?,'SUPERADMIN','ENTERPRISE',1)",
                ("superadmin", "superadmin@ventasflow.com", pbkdf2_sha256.hash("super123"))
            )

        # Admin / gerente / cajero por defecto
        admin_exists = conn.execute(
            "SELECT id FROM users WHERE username='admin'"
        ).fetchone()
        if not admin_exists:
            conn.execute(
                "INSERT INTO users (username,email,password,role,license,active) VALUES (?,?,?,'ADMIN','ENTERPRISE',1)",
                ("admin", "admin@ventasflow.com", pbkdf2_sha256.hash("admin123"))
            )
            conn.execute(
                "INSERT INTO users (username,email,password,role,license,active) VALUES (?,?,?,'GERENTE','PRO',1)",
                ("gerente", "gerente@ventasflow.com", pbkdf2_sha256.hash("gerente123"))
            )
            conn.execute(
                "INSERT INTO users (username,email,password,role,license,active) VALUES (?,?,?,'CAJERO','PRO',1)",
                ("cajero", "cajero@ventasflow.com", pbkdf2_sha256.hash("cajero123"))
            )

        # Licencia inicial del sistema
        license_exists = conn.execute(
            "SELECT id FROM licenses LIMIT 1"
        ).fetchone()

        if not license_exists:
            config = get_license_config("PRO")
            conn.execute("""
                INSERT INTO licenses (
                    license_type,
                    start_date,
                    end_date,
                    max_users,
                    dashboard_enabled,
                    reports_enabled,
                    status
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                "PRO",
                "2026-03-01",
                "2027-03-01",
                config["max_users"],
                config["dashboard_enabled"],
                config["reports_enabled"],
                1
            ))

        prod_count = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
        if prod_count < 100:
            _seed_products(conn)

        sales_count = conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0]
        if sales_count < 10:
            _seed_sales(conn)

def _seed_products(conn):
    categories = {
        "Lácteos":    ["Leche Entera","Leche Descremada","Yogurt Natural","Yogurt de Fresa","Mantequilla","Queso Fresco","Queso Mozarela","Crema de Leche","Leche de Almendras","Queso Cheddar"],
        "Carnes":     ["Pechuga de Pollo","Muslo de Pollo","Carne Molida","Bistec de Res","Costilla de Cerdo","Chorizo","Salchicha","Jamón Cocido","Filete de Pescado","Camarón Limpio"],
        "Abarrotes":  ["Arroz Blanco","Frijol Negro","Frijol Rojo","Azúcar Refinada","Sal de Mesa","Aceite Vegetal","Vinagre Blanco","Sopa de Pollo","Sopa de Tomate","Avena en Hojuelas"],
        "Bebidas":    ["Agua Pura 500ml","Agua Pura 1L","Jugo de Naranja","Jugo de Manzana","Refresco Cola","Refresco Naranja","Cerveza Lager","Cerveza Oscura","Té Frío Limón","Energizante"],
        "Panadería":  ["Pan Blanco","Pan Integral","Pan Dulce","Croissant","Galletas de Avena","Galletas de Chocolate","Bizcocho","Tortillas","Baguette","Pan de Ajo"],
        "Frutas":     ["Manzana Roja","Manzana Verde","Plátano","Sandía","Melón","Mango","Papaya","Piña","Fresa","Uva"],
        "Verduras":   ["Tomate","Cebolla Blanca","Cebolla Morada","Zanahoria","Papa","Brócoli","Coliflor","Lechuga","Pepino","Chile Pimiento"],
        "Limpieza":   ["Detergente en Polvo","Jabón de Platos","Limpiador Multiusos","Desinfectante","Suavizante","Blanqueador","Escoba","Trapeador","Esponja","Bolsas de Basura"],
        "Higiene":    ["Shampoo","Acondicionador","Jabón de Baño","Pasta Dental","Cepillo de Dientes","Desodorante","Papel Higiénico","Toallas Húmedas","Pañuelos","Crema Corporal"],
        "Congelados": ["Pizza Congelada","Nuggets de Pollo","Papa Francesa","Burrito","Lasaña","Helado Vainilla","Helado Chocolate","Paletas","Empanadas","Mariscos Mixtos"],
        "Snacks":     ["Papas Fritas","Chitos","Palomitas","Maní Salado","Gomitas","Chocolate","Barra Energética","Pretzels","Galletas Saladas","Cacahuates"],
        "Cereales":   ["Corn Flakes","Granola","Choco Krispis","Zucaritas","All Bran","Muesli","Avena Instantánea","Cereal de Arroz","Cheerios","Froot Loops"],
        "Condimentos":["Ketchup","Mostaza","Mayonesa","Salsa Inglesa","Salsa de Soya","Salsa Picante","Vinagreta","Salsa BBQ","Pasta de Tomate","Mole en Pasta"],
        "Mascotas":   ["Croquetas Perro Adulto","Croquetas Cachorro","Croquetas Gato","Lata Perro Pollo","Lata Gato Atún","Arena para Gatos","Hueso de Cuero","Juguete Ratón","Collar Antipulgas","Champú Mascota"],
        "Bebé":       ["Pañales Talla S","Pañales Talla M","Pañales Talla G","Toallitas Bebé","Fórmula Infantil","Papilla Manzana","Papilla Pera","Crema Pañal","Biberón","Chupón"],
    }
    sizes = ["200g","500g","1kg","2kg","250ml","500ml","1L","2L","6 Pack","Unidad","Bolsa","Caja"]
    brands = ["Del Monte","Nestlé","La Selecta","Dos Pinos","Bimbo","Colgate","Lala","Sabritas","Kellogg's","Unilever","P&G","Marca Propia"]
    products = []
    counter = 1

    for cat, base_names in categories.items():
        for base in base_names:
            for brand in random.sample(brands, k=random.randint(4, 7)):
                size = random.choice(sizes)
                name = f"{base} {brand} {size}"
                sku = f"{cat[:3].upper()}-{counter:04d}"
                cost = round(random.uniform(5, 150), 2)
                margin = random.uniform(0.15, 0.45)
                sale_price = round(cost * (1 + margin), 2)
                stock = random.randint(10, 500)
                products.append((name, sku, cat, cost, sale_price, stock))
                counter += 1
                if counter > 1100:
                    break
            if counter > 1100:
                break
        if counter > 1100:
            break

    conn.executemany(
        "INSERT OR IGNORE INTO products (name,sku,category,cost_price,sale_price,stock) VALUES (?,?,?,?,?,?)",
        products
    )

def _seed_sales(conn):
    now = datetime.now()
    for day_offset in range(7):
        day = now - timedelta(days=day_offset)
        for _ in range(random.randint(8, 25)):
            sale_time = day.replace(hour=random.randint(8, 20), minute=random.randint(0, 59))
            total = round(random.uniform(50, 800), 2)
            conn.execute("""
                INSERT INTO sales (cashier_id,cashier_name,customer_name,customer_nit,payment_method,total,items_json,created_at)
                VALUES (?,?,?,?,?,?,?,?)
            """, (
                4,
                "cajero",
                "Consumidor Final",
                "CF",
                random.choice(["EFECTIVO", "TARJETA", "QR"]),
                total,
                "[]",
                sale_time.strftime("%Y-%m-%d %H:%M:%S")
            ))

# ─── APP ──────────────────────────────────────────────────────────────────────
app = FastAPI(title="VentasFlow API", version="1.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

@app.on_event("startup")
def startup():
    init_db()

# ─── MODELOS ──────────────────────────────────────────────────────────────────
class UserCreate(BaseModel):
    username: str
    email: str
    role: str = "CAJERO"

class PasswordChange(BaseModel):
    current_password: str
    new_password: str

class ProfileUpdate(BaseModel):
    photo_url: Optional[str] = None

class RequestAccessModel(BaseModel):
    email: str

class ResetCodeModel(BaseModel):
    email: str
    code: str
    new_password: str

class ForgotPasswordModel(BaseModel):
    email: str

class SaleItem(BaseModel):
    product_id: int
    product_name: str
    quantity: int
    unit_price: float
    subtotal: float

class SaleCreate(BaseModel):
    customer_name: str = "Consumidor Final"
    customer_nit: str = "CF"
    payment_method: str = "EFECTIVO"
    total: float
    items: List[SaleItem]

class LicenseUpdate(BaseModel):
    license_type: str
    start_date: str
    end_date: str
    status: int = 1

# ─── JWT ──────────────────────────────────────────────────────────────────────
def create_access_token(data: dict, expires_delta: timedelta = None):
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def get_current_user(token: str = Depends(oauth2_scheme)):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
        if not username:
            raise HTTPException(status_code=401, detail="Token inválido")
    except JWTError:
        raise HTTPException(status_code=401, detail="Token inválido o expirado")

    with get_db() as conn:
        user = conn.execute(
            "SELECT * FROM users WHERE username=? AND active=1",
            (username,)
        ).fetchone()

    if not user:
        raise HTTPException(status_code=401, detail="Usuario no encontrado")

    return dict(user)

def require_role(*roles):
    def checker(current_user: dict = Depends(get_current_user)):
        if current_user["role"] not in roles:
            raise HTTPException(status_code=403, detail="Permiso denegado")
        return current_user
    return checker

# ─── LICENCIAS ────────────────────────────────────────────────────────────────
def get_active_license():
    with get_db() as conn:
        lic = conn.execute("""
            SELECT * FROM licenses
            WHERE status = 1
            ORDER BY id DESC
            LIMIT 1
        """).fetchone()
    return dict(lic) if lic else None

def validate_license():
    lic = get_active_license()

    if not lic:
        raise HTTPException(status_code=403, detail="No hay una licencia activa configurada")

    if lic["status"] != 1:
        raise HTTPException(status_code=403, detail="La licencia del sistema está inactiva")

    today = datetime.now().date()
    end_date = datetime.strptime(lic["end_date"], "%Y-%m-%d").date()

    if today > end_date:
        raise HTTPException(status_code=403, detail="La licencia del sistema está vencida")

    return lic

def validate_dashboard_license():
    lic = validate_license()
    if lic["dashboard_enabled"] != 1:
        raise HTTPException(status_code=403, detail="El dashboard no está habilitado en la licencia actual")
    return lic

def validate_reports_license():
    lic = validate_license()
    if lic["reports_enabled"] != 1:
        raise HTTPException(status_code=403, detail="Los reportes no están habilitados en la licencia actual")
    return lic

# ─── SMTP ─────────────────────────────────────────────────────────────────────
def build_email_template(title: str, content: str, footer: str = "") -> str:
    """Genera un HTML de correo profesional con el diseño de VentasFlow."""
    return f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"></head>
    <body style="margin:0;padding:0;background:#030712;font-family:'Segoe UI',Arial,sans-serif;">
      <table width="100%" cellpadding="0" cellspacing="0" style="background:#030712;padding:40px 0;">
        <tr><td align="center">
          <table width="580" cellpadding="0" cellspacing="0" style="background:#0d1117;border-radius:16px;overflow:hidden;border:1px solid rgba(99,102,241,0.3);box-shadow:0 0 40px rgba(99,102,241,0.15);">
            <tr><td style="background:linear-gradient(135deg,#6366f1,#8b5cf6);padding:28px 36px;text-align:center;">
              <p style="margin:0;font-size:28px;">🛒</p>
              <h1 style="margin:8px 0 4px;color:#ffffff;font-size:22px;font-weight:700;letter-spacing:1px;">VentasFlow</h1>
              <p style="margin:0;color:rgba(255,255,255,0.7);font-size:13px;">Plataforma SaaS · Supermercado</p>
            </td></tr>
            <tr><td style="padding:36px;">
              <h2 style="margin:0 0 20px;color:#e2e8f0;font-size:18px;">{title}</h2>
              {content}
            </td></tr>
            <tr><td style="background:#0f172a;padding:20px 36px;border-top:1px solid rgba(255,255,255,0.05);">
              <p style="margin:0;color:#475569;font-size:12px;text-align:center;">
                {footer if footer else "Este es un correo automático de VentasFlow. No respondas a este mensaje."}
              </p>
              <p style="margin:6px 0 0;color:#334155;font-size:11px;text-align:center;">© 2025 VentasFlow SaaS · ventasflow.com</p>
            </td></tr>
          </table>
        </td></tr>
      </table>
    </body>
    </html>"""

def send_email(to: str, subject: str, html_body: str, attachment_bytes: bytes = None, attachment_name: str = None):
    if not SMTP_ENABLED:
        print(f"\n[SMTP SIMULADO] ━━━━━━━━━━━━━━━━━━━━━━")
        print(f"Para: {to} | Asunto: {subject}")
        print(html_body[:400])
        print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")
        return True
    try:
        from email.mime.base import MIMEBase
        from email import encoders
        msg = MIMEMultipart("mixed")
        msg["Subject"] = subject
        msg["From"]    = f"VentasFlow <{SMTP_USER}>"
        msg["To"]      = to
        alt = MIMEMultipart("alternative")
        alt.attach(MIMEText(html_body, "html", "utf-8"))
        msg.attach(alt)
        if attachment_bytes and attachment_name:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(attachment_bytes)
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", f"attachment; filename={attachment_name}")
            msg.attach(part)
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(SMTP_USER, to, msg.as_string())
        return True
    except Exception as e:
        print(f"[SMTP ERROR] {e}")
        return False

def gen_temp_password(length=10):
    return "".join(random.choices(string.ascii_letters + string.digits, k=length))

def gen_reset_code():
    return str(random.randint(100000, 999999))

# ─── AUTH ─────────────────────────────────────────────────────────────────────
@app.post("/api/auth/login")
def login(form_data: OAuth2PasswordRequestForm = Depends()):
    validate_license()

    with get_db() as conn:
        user = conn.execute(
            "SELECT * FROM users WHERE username=? AND active=1",
            (form_data.username,)
        ).fetchone()

    if not user or not pbkdf2_sha256.verify(form_data.password, user["password"]):
        raise HTTPException(status_code=401, detail="Credenciales incorrectas")

    token = create_access_token({"sub": user["username"], "role": user["role"]})
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": user["id"],
            "username": user["username"],
            "email": user["email"],
            "role": user["role"],
            "license": user["license"],
            "photo_url": user["photo_url"]
        }
    }

@app.post("/api/auth/request-access")
def request_access(data: RequestAccessModel, bg: BackgroundTasks):
    validate_license()

    with get_db() as conn:
        user = conn.execute("SELECT * FROM users WHERE email=?", (data.email,)).fetchone()

    if not user:
        raise HTTPException(status_code=404, detail="Correo no registrado en el sistema")

    temp = gen_temp_password()

    with get_db() as conn:
        conn.execute(
            "UPDATE users SET password=? WHERE email=?",
            (pbkdf2_sha256.hash(temp), data.email)
        )

    content_html = f"""
    <p style="color:#94a3b8;font-size:14px;margin:0 0 24px;">Hola <b style="color:#e2e8f0">{user['username']}</b>, el administrador te ha registrado en el sistema.</p>
    <div style="background:#0f172a;border-radius:12px;padding:20px;border:1px solid rgba(99,102,241,0.3);margin-bottom:20px;">
      <table width="100%" cellpadding="6">
        <tr><td style="color:#64748b;font-size:13px;width:120px;">👤 Usuario</td><td style="color:#e2e8f0;font-size:13px;font-weight:600;">{user['username']}</td></tr>
        <tr><td style="color:#64748b;font-size:13px;">🔑 Contraseña</td><td style="color:#818cf8;font-size:16px;font-weight:700;letter-spacing:2px;">{temp}</td></tr>
        <tr><td style="color:#64748b;font-size:13px;">🎭 Rol</td><td style="color:#e2e8f0;font-size:13px;">{user['role']}</td></tr>
        <tr><td style="color:#64748b;font-size:13px;">📦 Licencia</td><td style="color:#e2e8f0;font-size:13px;">{user['license']}</td></tr>
      </table>
    </div>
    <div style="background:rgba(234,179,8,0.1);border:1px solid rgba(234,179,8,0.3);border-radius:10px;padding:14px;">
      <p style="margin:0;color:#fbbf24;font-size:13px;">⚠️ <b>Importante:</b> Cambia tu contraseña al ingresar por primera vez por seguridad.</p>
    </div>"""
    html = build_email_template("¡Bienvenido a VentasFlow! Tus credenciales de acceso", content_html)
    bg.add_task(send_email, data.email, "VentasFlow - Tus credenciales de acceso", html)
    return {"message": f"Credenciales enviadas a {data.email}"}

@app.post("/api/auth/forgot-password")
def forgot_password(data: ForgotPasswordModel, bg: BackgroundTasks):
    validate_license()

    with get_db() as conn:
        user = conn.execute("SELECT * FROM users WHERE email=?", (data.email,)).fetchone()

    if not user:
        raise HTTPException(status_code=404, detail="Correo no registrado")

    code = gen_reset_code()
    exp = int(time.time()) + 600

    with get_db() as conn:
        conn.execute(
            "UPDATE users SET reset_code=?, reset_exp=? WHERE email=?",
            (code, exp, data.email)
        )

    content_html = f"""
    <p style="color:#94a3b8;font-size:14px;margin:0 0 24px;">Recibimos una solicitud para restablecer la contraseña de tu cuenta.</p>
    <div style="text-align:center;background:#0f172a;border-radius:12px;padding:32px;border:1px solid rgba(99,102,241,0.3);margin-bottom:20px;">
      <p style="margin:0 0 8px;color:#64748b;font-size:13px;text-transform:uppercase;letter-spacing:2px;">Tu código de verificación</p>
      <p style="margin:0;font-size:44px;font-weight:700;letter-spacing:12px;color:#818cf8;font-family:monospace;">{code}</p>
    </div>
    <div style="display:flex;gap:12px;">
      <div style="background:rgba(16,185,129,0.1);border:1px solid rgba(16,185,129,0.3);border-radius:8px;padding:12px;flex:1;">
        <p style="margin:0;color:#34d399;font-size:12px;">⏱️ <b>Válido por 10 minutos</b></p>
      </div>
      <div style="background:rgba(239,68,68,0.1);border:1px solid rgba(239,68,68,0.3);border-radius:8px;padding:12px;flex:1;">
        <p style="margin:0;color:#f87171;font-size:12px;">🔒 <b>No compartas este código</b></p>
      </div>
    </div>
    <p style="color:#475569;font-size:12px;margin-top:20px;">Si no solicitaste este código, ignora este correo.</p>"""
    html = build_email_template("Código para restablecer contraseña", content_html)
    bg.add_task(send_email, data.email, "VentasFlow - Código de verificación", html)
    return {"message": "Código enviado al correo"}

@app.post("/api/auth/reset-password")
def reset_password(data: ResetCodeModel):
    validate_license()

    with get_db() as conn:
        user = conn.execute("SELECT * FROM users WHERE email=?", (data.email,)).fetchone()

    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    if user["reset_code"] != data.code:
        raise HTTPException(status_code=400, detail="Código incorrecto")
    if int(time.time()) > user["reset_exp"]:
        raise HTTPException(status_code=400, detail="Código expirado")

    with get_db() as conn:
        conn.execute(
            "UPDATE users SET password=?, reset_code='', reset_exp=0 WHERE email=?",
            (pbkdf2_sha256.hash(data.new_password), data.email)
        )

    return {"message": "Contraseña actualizada exitosamente"}

# ─── USUARIO ──────────────────────────────────────────────────────────────────
@app.get("/api/users/me")
def get_me(current_user: dict = Depends(get_current_user)):
    return current_user

@app.put("/api/users/me/password")
def change_password(data: PasswordChange, current_user: dict = Depends(get_current_user)):
    with get_db() as conn:
        user = conn.execute("SELECT * FROM users WHERE id=?", (current_user["id"],)).fetchone()

    if not pbkdf2_sha256.verify(data.current_password, user["password"]):
        raise HTTPException(status_code=400, detail="Contraseña actual incorrecta")

    with get_db() as conn:
        conn.execute(
            "UPDATE users SET password=? WHERE id=?",
            (pbkdf2_sha256.hash(data.new_password), current_user["id"])
        )

    return {"message": "Contraseña actualizada"}

@app.put("/api/users/me/profile")
def update_profile(data: ProfileUpdate, current_user: dict = Depends(get_current_user)):
    with get_db() as conn:
        conn.execute(
            "UPDATE users SET photo_url=? WHERE id=?",
            (data.photo_url, current_user["id"])
        )

    return {"message": "Perfil actualizado"}

# ─── SUPERADMIN ───────────────────────────────────────────────────────────────
@app.get("/api/superadmin/license")
def get_system_license(current_user=Depends(require_role("SUPERADMIN"))):
    lic = get_active_license()
    if not lic:
        raise HTTPException(status_code=404, detail="No hay licencia configurada")
    return lic

@app.put("/api/superadmin/license")
def update_system_license(data: LicenseUpdate, current_user=Depends(require_role("SUPERADMIN"))):
    config = get_license_config(data.license_type)

    with get_db() as conn:
        active_license = conn.execute("""
            SELECT id FROM licenses
            ORDER BY id DESC
            LIMIT 1
        """).fetchone()

        if not active_license:
            conn.execute("""
                INSERT INTO licenses (
                    license_type,
                    start_date,
                    end_date,
                    max_users,
                    dashboard_enabled,
                    reports_enabled,
                    status
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                data.license_type.upper(),
                data.start_date,
                data.end_date,
                config["max_users"],
                config["dashboard_enabled"],
                config["reports_enabled"],
                data.status
            ))
        else:
            conn.execute("""
                UPDATE licenses
                SET license_type = ?,
                    start_date = ?,
                    end_date = ?,
                    max_users = ?,
                    dashboard_enabled = ?,
                    reports_enabled = ?,
                    status = ?
                WHERE id = ?
            """, (
                data.license_type.upper(),
                data.start_date,
                data.end_date,
                config["max_users"],
                config["dashboard_enabled"],
                config["reports_enabled"],
                data.status,
                active_license["id"]
            ))

    return {
        "message": "Licencia actualizada correctamente",
        "license_type": data.license_type.upper(),
        "config_applied": config
    }

@app.get("/api/superadmin/demo-credentials")
def demo_credentials(current_user=Depends(require_role("SUPERADMIN", "ADMIN"))):
    return {
        "message": "Credenciales demo del sistema",
        "users": [
            {"role": "SUPERADMIN", "username": "superadmin", "password": "super123"},
            {"role": "ADMIN", "username": "admin", "password": "admin123"},
            {"role": "GERENTE", "username": "gerente", "password": "gerente123"},
            {"role": "CAJERO", "username": "cajero", "password": "cajero123"},
        ]
    }

# ─── ADMIN ────────────────────────────────────────────────────────────────────
@app.get("/api/admin/users")
def list_users(current_user=Depends(require_role("ADMIN", "SUPERADMIN"))):
    with get_db() as conn:
        users = conn.execute("""
            SELECT id,username,email,role,license,active,created_at,photo_url
            FROM users
        """).fetchall()

    return [dict(u) for u in users]

@app.post("/api/admin/users")
def create_user(data: UserCreate, bg: BackgroundTasks, current_user=Depends(require_role("ADMIN", "SUPERADMIN"))):
    lic = validate_license()

    with get_db() as conn:
        total_users = conn.execute(
            "SELECT COUNT(*) FROM users WHERE active=1"
        ).fetchone()[0]

    if total_users >= lic["max_users"]:
        raise HTTPException(
            status_code=403,
            detail="Se alcanzó el límite máximo de usuarios permitido por la licencia"
        )

    temp = gen_temp_password()

    try:
        with get_db() as conn:
            conn.execute(
                "INSERT INTO users (username,email,password,role,license,active) VALUES (?,?,?, ?, ?, 1)",
                (
                    data.username,
                    data.email,
                    pbkdf2_sha256.hash(temp),
                    data.role.upper(),
                    lic["license_type"]
                )
            )
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Usuario o correo ya existe")

    content_html = f"""
    <p style="color:#94a3b8;font-size:14px;margin:0 0 24px;">El administrador ha creado tu cuenta en <b style="color:#818cf8">VentasFlow</b>. Aquí están tus credenciales de acceso:</p>
    <div style="background:#0f172a;border-radius:12px;padding:20px;border:1px solid rgba(99,102,241,0.3);margin-bottom:20px;">
      <table width="100%" cellpadding="6">
        <tr><td style="color:#64748b;font-size:13px;width:120px;">👤 Usuario</td><td style="color:#e2e8f0;font-size:13px;font-weight:600;">{data.username}</td></tr>
        <tr><td style="color:#64748b;font-size:13px;">🔑 Contraseña</td><td style="color:#818cf8;font-size:16px;font-weight:700;letter-spacing:2px;">{temp}</td></tr>
        <tr><td style="color:#64748b;font-size:13px;">🎭 Rol</td><td style="color:#e2e8f0;font-size:13px;">{data.role.upper()}</td></tr>
        <tr><td style="color:#64748b;font-size:13px;">📦 Licencia</td><td style="color:#e2e8f0;font-size:13px;">PRO</td></tr>
      </table>
    </div>
    <div style="background:rgba(234,179,8,0.1);border:1px solid rgba(234,179,8,0.3);border-radius:10px;padding:14px;">
      <p style="margin:0;color:#fbbf24;font-size:13px;">⚠️ <b>Importante:</b> Cambia tu contraseña al primer ingreso desde Mi Perfil.</p>
    </div>"""
    html = build_email_template("¡Tu cuenta ha sido creada en VentasFlow!", content_html)
    bg.add_task(send_email, data.email, "VentasFlow - ¡Bienvenido! Tu acceso está listo", html)
    return {"message": f"Usuario {data.username} creado exitosamente"}

@app.delete("/api/admin/users/{user_id}")
def deactivate_user(user_id: int, current_user=Depends(require_role("ADMIN", "SUPERADMIN"))):
    if user_id == current_user["id"]:
        raise HTTPException(status_code=400, detail="No puedes desactivarte a ti mismo")

    with get_db() as conn:
        conn.execute("UPDATE users SET active=0 WHERE id=?", (user_id,))

    return {"message": "Usuario desactivado"}

@app.get("/api/admin/metrics")
def admin_metrics(current_user=Depends(require_role("ADMIN", "SUPERADMIN"))):
    with get_db() as conn:
        total_users = conn.execute("SELECT COUNT(*) FROM users WHERE active=1").fetchone()[0]
        roles = conn.execute("SELECT role, COUNT(*) as cnt FROM users WHERE active=1 GROUP BY role").fetchall()
        licenses_by_user = conn.execute("SELECT license, COUNT(*) as cnt FROM users WHERE active=1 GROUP BY license").fetchall()
        total_sales = conn.execute("SELECT COALESCE(SUM(total),0) FROM sales").fetchone()[0]
        total_prods = conn.execute("SELECT COUNT(*) FROM products WHERE active=1").fetchone()[0]
        active_license = conn.execute("""
            SELECT license_type, start_date, end_date, max_users, dashboard_enabled, reports_enabled, status
            FROM licenses
            ORDER BY id DESC
            LIMIT 1
        """).fetchone()

    return {
        "total_users": total_users,
        "roles": [dict(r) for r in roles],
        "licenses": [dict(l) for l in licenses_by_user],
        "total_sales": round(total_sales, 2),
        "total_products": total_prods,
        "system_license": dict(active_license) if active_license else None
    }

# ─── GERENTE ──────────────────────────────────────────────────────────────────
@app.get("/api/manager/kpis")
def manager_kpis(current_user=Depends(require_role("GERENTE", "ADMIN", "SUPERADMIN"))):
    validate_dashboard_license()

    with get_db() as conn:
        total_revenue = conn.execute("SELECT COALESCE(SUM(total),0) FROM sales").fetchone()[0]
        today_str = datetime.now().strftime("%Y-%m-%d")
        day_sales = conn.execute(
            "SELECT COALESCE(SUM(total),0) FROM sales WHERE created_at LIKE ?",
            (f"{today_str}%",)
        ).fetchone()[0]
        avg_ticket = conn.execute("SELECT COALESCE(AVG(total),0) FROM sales").fetchone()[0]
        total_txns = conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0]

        weekly = []
        for i in range(6, -1, -1):
            d = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
            total = conn.execute(
                "SELECT COALESCE(SUM(total),0) FROM sales WHERE created_at LIKE ?",
                (f"{d}%",)
            ).fetchone()[0]
            weekly.append({"date": d, "total": round(total, 2)})

        last_txns = conn.execute("""
            SELECT id,cashier_name,customer_name,payment_method,total,created_at
            FROM sales
            ORDER BY id DESC
            LIMIT 20
        """).fetchall()

    return {
        "total_revenue": round(total_revenue, 2),
        "day_sales": round(day_sales, 2),
        "avg_ticket": round(avg_ticket, 2),
        "total_transactions": total_txns,
        "weekly_sales": weekly,
        "last_transactions": [dict(t) for t in last_txns]
    }

@app.get("/api/manager/report-pdf")
def generate_pdf_report(current_user=Depends(require_role("GERENTE", "ADMIN", "SUPERADMIN"))):
    validate_reports_license()
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches
        import numpy as np
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle, Spacer, Image, HRFlowable
        from reportlab.lib import colors
        from reportlab.lib.units import inch
        from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT

        now   = datetime.now()
        today = now.strftime("%Y-%m-%d")
        month_start = now.replace(day=1).strftime("%Y-%m-%d")
        prev_month_start = (now.replace(day=1) - timedelta(days=1)).replace(day=1).strftime("%Y-%m-%d")
        prev_month_end   = (now.replace(day=1) - timedelta(days=1)).strftime("%Y-%m-%d")

        with get_db() as conn:
            total_rev   = conn.execute("SELECT COALESCE(SUM(total),0) FROM sales").fetchone()[0]
            day_rev     = conn.execute("SELECT COALESCE(SUM(total),0) FROM sales WHERE created_at LIKE ?", (f"{today}%",)).fetchone()[0]
            avg_ticket  = conn.execute("SELECT COALESCE(AVG(total),0) FROM sales").fetchone()[0]
            total_txns  = conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0]
            this_month  = conn.execute("SELECT COALESCE(SUM(total),0) FROM sales WHERE created_at >= ?", (month_start,)).fetchone()[0]
            prev_month  = conn.execute("SELECT COALESCE(SUM(total),0) FROM sales WHERE DATE(created_at) BETWEEN ? AND ?", (prev_month_start, prev_month_end)).fetchone()[0]
            total_users = conn.execute("SELECT COUNT(*) FROM users WHERE active=1").fetchone()[0]
            total_prods = conn.execute("SELECT COUNT(*) FROM products WHERE active=1").fetchone()[0]

            # Ventas 7 días
            weekly_data = []
            weekly_labels = []
            for i in range(6, -1, -1):
                d = (now - timedelta(days=i)).strftime("%Y-%m-%d")
                v = conn.execute("SELECT COALESCE(SUM(total),0) FROM sales WHERE created_at LIKE ?", (f"{d}%",)).fetchone()[0]
                weekly_data.append(round(v, 2))
                weekly_labels.append((now - timedelta(days=i)).strftime("%a %d"))

            # Métodos de pago
            pay_rows = conn.execute("SELECT payment_method, COUNT(*) as cnt, SUM(total) as tot FROM sales GROUP BY payment_method").fetchall()
            pay_labels = [r["payment_method"] for r in pay_rows]
            pay_counts = [r["cnt"] for r in pay_rows]
            pay_totals = [r["tot"] for r in pay_rows]

            # Top 5 productos
            top_prods = conn.execute("""
                SELECT si.product_name, SUM(si.quantity) as qty, SUM(si.subtotal) as rev
                FROM sale_items si GROUP BY si.product_name ORDER BY qty DESC LIMIT 5
            """).fetchall()

            # Ventas por hora
            hourly = []
            for h in range(24):
                cnt = conn.execute("SELECT COUNT(*) FROM sales WHERE strftime('%H',created_at)=?", (f"{h:02d}",)).fetchone()[0]
                hourly.append(cnt)

            # Últimas 20 transacciones
            last_txns = conn.execute("""
                SELECT id,cashier_name,customer_name,payment_method,total,created_at
                FROM sales ORDER BY id DESC LIMIT 20
            """).fetchall()

        # ─── COLORES VENTASFLOW ───────────────────────────────────────────────
        C_VIOLET  = "#6366f1"
        C_CYAN    = "#22d3ee"
        C_PINK    = "#ec4899"
        C_GREEN   = "#10b981"
        C_AMBER   = "#f59e0b"
        C_BG      = "#0d1117"
        C_PANEL   = "#0f172a"
        C_TEXT    = "#e2e8f0"
        C_MUTED   = "#64748b"

        plt.rcParams.update({
            "figure.facecolor":  C_BG,
            "axes.facecolor":    C_PANEL,
            "axes.edgecolor":    "#1e293b",
            "axes.labelcolor":   C_MUTED,
            "xtick.color":       C_MUTED,
            "ytick.color":       C_MUTED,
            "text.color":        C_TEXT,
            "grid.color":        "#1e293b",
            "grid.alpha":        0.8,
            "font.family":       "DejaVu Sans",
        })

        chart_imgs = {}

        # ── Gráfica 1: Ventas 7 días (área) ──────────────────────────────────
        fig, ax = plt.subplots(figsize=(6.5, 2.8))
        x = np.arange(len(weekly_labels))
        ax.fill_between(x, weekly_data, alpha=0.25, color=C_VIOLET)
        ax.plot(x, weekly_data, color=C_VIOLET, linewidth=2.5, marker="o",
                markersize=6, markerfacecolor=C_VIOLET, markeredgecolor="white", markeredgewidth=1.5)
        for xi, yi in zip(x, weekly_data):
            ax.annotate(f"Q{yi:,.0f}", (xi, yi), textcoords="offset points", xytext=(0,8),
                        ha="center", fontsize=7.5, color=C_TEXT, fontweight="bold")
        ax.set_xticks(x); ax.set_xticklabels(weekly_labels, fontsize=8)
        ax.set_title("Ventas — Últimos 7 Días", color=C_TEXT, fontsize=10, fontweight="bold", pad=10)
        ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v,_: f"Q{v/1000:.0f}k" if v>=1000 else f"Q{v:.0f}"))
        ax.grid(axis="y", linestyle="--")
        ax.spines[["top","right"]].set_visible(False)
        fig.tight_layout()
        buf1 = io.BytesIO(); fig.savefig(buf1, format="png", dpi=130, bbox_inches="tight"); buf1.seek(0); plt.close(fig)
        chart_imgs["weekly"] = buf1

        # ── Gráfica 2: Métodos de pago (donut) ───────────────────────────────
        fig, ax = plt.subplots(figsize=(3.2, 2.8))
        pal = [C_GREEN, C_VIOLET, C_CYAN, C_PINK, C_AMBER][:len(pay_counts)]
        wedges, texts, autotexts = ax.pie(pay_counts if pay_counts else [1],
            labels=pay_labels if pay_labels else ["Sin datos"],
            autopct="%1.0f%%" if pay_counts else None,
            colors=pal, startangle=90,
            wedgeprops={"linewidth":2, "edgecolor":C_BG},
            pctdistance=0.78)
        for t in autotexts: t.set(color="white", fontsize=8, fontweight="bold")
        for t in texts:     t.set(color=C_MUTED, fontsize=8)
        centre = plt.Circle((0,0), 0.55, color=C_PANEL)
        ax.add_patch(centre)
        ax.set_title("Métodos de Pago", color=C_TEXT, fontsize=10, fontweight="bold", pad=6)
        fig.tight_layout()
        buf2 = io.BytesIO(); fig.savefig(buf2, format="png", dpi=130, bbox_inches="tight"); buf2.seek(0); plt.close(fig)
        chart_imgs["payment"] = buf2

        # ── Gráfica 3: Top 5 productos (barras horizontales) ─────────────────
        if top_prods:
            names  = [r["product_name"][:22] + ("…" if len(r["product_name"])>22 else "") for r in top_prods]
            qtys   = [r["qty"] for r in top_prods]
            bar_colors = [C_VIOLET, C_CYAN, C_PINK, C_GREEN, C_AMBER][:len(names)]
            fig, ax = plt.subplots(figsize=(4.5, 2.6))
            bars = ax.barh(names[::-1], qtys[::-1], color=bar_colors[::-1], height=0.55,
                           edgecolor=C_BG, linewidth=0.5)
            for bar, v in zip(bars, qtys[::-1]):
                ax.text(bar.get_width() + max(qtys)*0.01, bar.get_y()+bar.get_height()/2,
                        f"{v} uds", va="center", ha="left", fontsize=7.5, color=C_TEXT)
            ax.set_title("Top 5 Productos Más Vendidos", color=C_TEXT, fontsize=10, fontweight="bold", pad=8)
            ax.set_xlabel("Unidades vendidas", fontsize=8)
            ax.tick_params(axis="y", labelsize=7.5)
            ax.grid(axis="x", linestyle="--"); ax.spines[["top","right"]].set_visible(False)
            ax.set_xlim(0, max(qtys)*1.2 if qtys else 1)
            fig.tight_layout()
            buf3 = io.BytesIO(); fig.savefig(buf3, format="png", dpi=130, bbox_inches="tight"); buf3.seek(0); plt.close(fig)
            chart_imgs["top_prods"] = buf3

        # ── Gráfica 4: Mapa de calor horario ─────────────────────────────────
        fig, ax = plt.subplots(figsize=(6.5, 1.5))
        hourly_arr = np.array(hourly).reshape(1, -1)
        im = ax.imshow(hourly_arr, aspect="auto", cmap="RdPu", vmin=0, vmax=max(max(hourly),1))
        ax.set_xticks(range(24))
        ax.set_xticklabels([f"{h}h" for h in range(24)], fontsize=6.5, rotation=45)
        ax.set_yticks([])
        ax.set_title("Horas Pico — Distribución de Ventas", color=C_TEXT, fontsize=9, fontweight="bold", pad=6)
        for h, v in enumerate(hourly):
            if v > 0:
                ax.text(h, 0, str(v), ha="center", va="center", fontsize=6.5,
                        color="white" if v > max(hourly)*0.5 else C_MUTED, fontweight="bold")
        fig.tight_layout()
        buf4 = io.BytesIO(); fig.savefig(buf4, format="png", dpi=130, bbox_inches="tight"); buf4.seek(0); plt.close(fig)
        chart_imgs["heatmap"] = buf4

        # ─── CONSTRUIR PDF ────────────────────────────────────────────────────
        pdf_buf = io.BytesIO()
        doc = SimpleDocTemplate(pdf_buf, pagesize=letter,
                                topMargin=0.5*inch, bottomMargin=0.5*inch,
                                leftMargin=0.6*inch, rightMargin=0.6*inch)
        styles = getSampleStyleSheet()
        C_RL_VIOLET = colors.HexColor(C_VIOLET)
        C_RL_BG     = colors.HexColor(C_BG)
        C_RL_PANEL  = colors.HexColor(C_PANEL)
        C_RL_CYAN   = colors.HexColor(C_CYAN)
        C_RL_PINK   = colors.HexColor(C_PINK)
        C_RL_GREEN  = colors.HexColor(C_GREEN)
        C_RL_MUTED  = colors.HexColor(C_MUTED)
        C_RL_TEXT   = colors.HexColor(C_TEXT)
        WHITE       = colors.white

        title_style = ParagraphStyle("title", parent=styles["Normal"],
            fontSize=22, textColor=WHITE, fontName="Helvetica-Bold",
            spaceAfter=4, alignment=TA_CENTER)
        sub_style = ParagraphStyle("sub", parent=styles["Normal"],
            fontSize=10, textColor=C_RL_MUTED, alignment=TA_CENTER)
        sec_style = ParagraphStyle("sec", parent=styles["Normal"],
            fontSize=12, textColor=WHITE, fontName="Helvetica-Bold",
            spaceBefore=14, spaceAfter=8)
        kpi_val_style = ParagraphStyle("kpiv", parent=styles["Normal"],
            fontSize=18, textColor=C_RL_VIOLET, fontName="Helvetica-Bold", alignment=TA_CENTER)
        kpi_lbl_style = ParagraphStyle("kpil", parent=styles["Normal"],
            fontSize=8, textColor=C_RL_MUTED, alignment=TA_CENTER)
        body_style = ParagraphStyle("body", parent=styles["Normal"],
            fontSize=9, textColor=C_RL_TEXT)
        note_style = ParagraphStyle("note", parent=styles["Normal"],
            fontSize=8, textColor=C_RL_MUTED, alignment=TA_RIGHT)

        elems = []

        # ── Portada / Header ──────────────────────────────────────────────────
        header_data = [[
            Paragraph("🛒  VentasFlow — Dashboard Gerencial", title_style),
        ]]
        ht = Table(header_data, colWidths=[7.3*inch])
        ht.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,-1), C_RL_VIOLET),
            ("ROUNDEDCORNERS", [8]),
            ("TOPPADDING", (0,0), (-1,-1), 18),
            ("BOTTOMPADDING", (0,0), (-1,-1), 18),
        ]))
        elems.append(ht)
        elems.append(Spacer(1, 6))
        elems.append(Paragraph(f"Período: {now.strftime('%B %Y')}  ·  Generado: {now.strftime('%d/%m/%Y %H:%M')}  ·  Por: {current_user['username']}", sub_style))
        elems.append(Spacer(1, 14))

        # ── KPI Cards ─────────────────────────────────────────────────────────
        month_diff = ((this_month - prev_month) / prev_month * 100) if prev_month > 0 else 0
        trend = f"{'↑' if month_diff>=0 else '↓'} {abs(month_diff):.1f}% vs mes anterior"

        def kpi_cell(label, value, sub=""):
            return [Paragraph(label, kpi_lbl_style), Paragraph(value, kpi_val_style), Spacer(1, 12), Paragraph(sub, kpi_lbl_style)]

        kpi_table = Table([
            [kpi_cell("INGRESOS TOTALES", f"Q{total_rev:,.0f}", "Acumulado histórico"),
             kpi_cell("VENTAS DEL DÍA", f"Q{day_rev:,.0f}", now.strftime("%d/%m/%Y")),
             kpi_cell("TICKET PROMEDIO", f"Q{avg_ticket:,.0f}", "Por transacción"),
             kpi_cell("TRANSACCIONES", f"{total_txns:,}", "Total histórico")],
        ], colWidths=[1.825*inch]*4)
        kpi_table.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,-1), C_RL_PANEL),
            ("BOX", (0,0), (0,-1), 0.5, C_RL_VIOLET),
            ("BOX", (1,0), (1,-1), 0.5, C_RL_CYAN),
            ("BOX", (2,0), (2,-1), 0.5, C_RL_GREEN),
            ("BOX", (3,0), (3,-1), 0.5, C_RL_PINK),
            ("TOPPADDING",    (0,0),(-1,-1), 12),
            ("BOTTOMPADDING", (0,0),(-1,-1), 12),
            ("LEFTPADDING",   (0,0),(-1,-1), 8),
            ("RIGHTPADDING",  (0,0),(-1,-1), 8),
            ("VALIGN", (0,0),(-1,-1), "MIDDLE"),
        ]))
        elems.append(kpi_table)
        elems.append(Spacer(1, 10))

        # Comparativa mensual
        comp_data = [[
            Paragraph(f"📅  Mes Actual: <b>Q{this_month:,.2f}</b>", body_style),
            Paragraph(f"📅  Mes Anterior: <b>Q{prev_month:,.2f}</b>", body_style),
            Paragraph(f"{'📈' if month_diff>=0 else '📉'}  {trend}", body_style),
        ]]
        ct = Table(comp_data, colWidths=[2.43*inch]*3)
        ct.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,-1), C_RL_PANEL),
            ("BOX", (0,0), (-1,-1), 0.5, colors.HexColor("#1e293b")),
            ("INNERGRID", (0,0), (-1,-1), 0.3, colors.HexColor("#1e293b")),
            ("TOPPADDING", (0,0),(-1,-1), 9),
            ("BOTTOMPADDING", (0,0),(-1,-1), 9),
            ("LEFTPADDING", (0,0),(-1,-1), 10),
        ]))
        elems.append(ct)
        elems.append(Spacer(1, 14))
        elems.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#1e293b")))

        # ── Gráficas ──────────────────────────────────────────────────────────
        elems.append(Paragraph("📊  Análisis de Ventas", sec_style))

        # Fila 1: Línea semanal + Donut pagos
        row1 = [[
            Image(chart_imgs["weekly"],  width=4.5*inch, height=2.0*inch),
            Image(chart_imgs["payment"], width=2.6*inch, height=2.0*inch),
        ]]
        rt1 = Table(row1, colWidths=[4.6*inch, 2.7*inch])
        rt1.setStyle(TableStyle([
            ("BACKGROUND", (0,0),(-1,-1), C_RL_PANEL),
            ("BOX", (0,0),(-1,-1), 0.3, colors.HexColor("#1e293b")),
            ("INNERGRID", (0,0),(-1,-1), 0.3, colors.HexColor("#1e293b")),
            ("TOPPADDING",    (0,0),(-1,-1), 8),
            ("BOTTOMPADDING", (0,0),(-1,-1), 8),
            ("LEFTPADDING",   (0,0),(-1,-1), 8),
            ("RIGHTPADDING",  (0,0),(-1,-1), 8),
        ]))
        elems.append(rt1)
        elems.append(Spacer(1, 8))

        # Fila 2: Top productos + Heatmap
        row2_items = [Image(chart_imgs["heatmap"], width=7.3*inch, height=1.1*inch)]
        if "top_prods" in chart_imgs:
            row2 = [[
                Image(chart_imgs["top_prods"], width=3.5*inch, height=2.0*inch),
                Image(chart_imgs["heatmap"],   width=3.6*inch, height=2.0*inch),
            ]]
            rt2 = Table(row2, colWidths=[3.6*inch, 3.7*inch])
        else:
            row2 = [[Image(chart_imgs["heatmap"], width=7.3*inch, height=1.5*inch)]]
            rt2 = Table(row2, colWidths=[7.3*inch])
        rt2.setStyle(TableStyle([
            ("BACKGROUND", (0,0),(-1,-1), C_RL_PANEL),
            ("BOX", (0,0),(-1,-1), 0.3, colors.HexColor("#1e293b")),
            ("INNERGRID", (0,0),(-1,-1), 0.3, colors.HexColor("#1e293b")),
            ("TOPPADDING",    (0,0),(-1,-1), 8),
            ("BOTTOMPADDING", (0,0),(-1,-1), 8),
            ("LEFTPADDING",   (0,0),(-1,-1), 8),
            ("RIGHTPADDING",  (0,0),(-1,-1), 8),
        ]))
        elems.append(rt2)
        elems.append(Spacer(1, 12))
        elems.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#1e293b")))

        # ── Resumen de métodos de pago ────────────────────────────────────────
        if pay_rows:
            elems.append(Paragraph("💳  Resumen por Método de Pago", sec_style))
            pay_table_data = [["Método", "Transacciones", "Ingresos", "% del Total"]]
            total_pay = sum(pay_totals) or 1
            pay_pal = [C_RL_GREEN, C_RL_VIOLET, C_RL_CYAN]
            for i, (lbl, cnt, tot) in enumerate(zip(pay_labels, pay_counts, pay_totals)):
                pay_table_data.append([lbl, str(cnt), f"Q{tot:,.2f}", f"{tot/total_pay*100:.1f}%"])
            pt = Table(pay_table_data, colWidths=[2*inch, 1.5*inch, 2*inch, 1.8*inch])
            pt.setStyle(TableStyle([
                ("BACKGROUND", (0,0), (-1,0), C_RL_PANEL),
                ("TEXTCOLOR",  (0,0), (-1,0), C_RL_MUTED),
                ("FONTNAME",   (0,0), (-1,0), "Helvetica-Bold"),
                ("FONTSIZE",   (0,0), (-1,-1), 9),
                ("TEXTCOLOR",  (0,1), (-1,-1), C_RL_TEXT),
                ("BACKGROUND", (0,1), (-1,-1), colors.HexColor("#090c16")),
                ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.HexColor("#090c16"), C_RL_PANEL]),
                ("GRID", (0,0), (-1,-1), 0.3, colors.HexColor("#1e293b")),
                ("TOPPADDING",    (0,0),(-1,-1), 8),
                ("BOTTOMPADDING", (0,0),(-1,-1), 8),
                ("LEFTPADDING",   (0,0),(-1,-1), 10),
            ]))
            elems.append(pt)
            elems.append(Spacer(1, 12))
            elems.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#1e293b")))

        # ── Top productos tabla ───────────────────────────────────────────────
        if top_prods:
            elems.append(Paragraph("🏆  Top 5 Productos Más Vendidos", sec_style))
            tp_data = [["#", "Producto", "Unidades Vendidas", "Ingresos Generados"]]
            for i, r in enumerate(top_prods, 1):
                tp_data.append([str(i), r["product_name"][:40], str(r["qty"]), f"Q{r['rev']:,.2f}"])
            tpt = Table(tp_data, colWidths=[0.3*inch, 3.5*inch, 1.7*inch, 1.8*inch])
            tpt.setStyle(TableStyle([
                ("BACKGROUND", (0,0), (-1,0), C_RL_VIOLET),
                ("TEXTCOLOR",  (0,0), (-1,0), WHITE),
                ("FONTNAME",   (0,0), (-1,0), "Helvetica-Bold"),
                ("FONTSIZE",   (0,0), (-1,-1), 9),
                ("TEXTCOLOR",  (0,1), (-1,-1), C_RL_TEXT),
                ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.HexColor("#090c16"), C_RL_PANEL]),
                ("GRID", (0,0), (-1,-1), 0.3, colors.HexColor("#1e293b")),
                ("TOPPADDING",    (0,0),(-1,-1), 8),
                ("BOTTOMPADDING", (0,0),(-1,-1), 8),
                ("LEFTPADDING",   (0,0),(-1,-1), 8),
            ]))
            elems.append(tpt)
            elems.append(Spacer(1, 12))
            elems.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#1e293b")))

        # ── Últimas 20 transacciones ──────────────────────────────────────────
        elems.append(Paragraph("📋  Últimas 20 Transacciones", sec_style))
        tx_data = [["#", "Cajero", "Cliente", "Método", "Total", "Fecha/Hora"]]
        for t in last_txns:
            tx_data.append([str(t["id"]), t["cashier_name"], t["customer_name"][:18],
                            t["payment_method"], f"Q{t['total']:.2f}", t["created_at"][:16]])
        txt = Table(tx_data, colWidths=[0.4*inch, 1.0*inch, 1.5*inch, 0.9*inch, 0.9*inch, 1.2*inch])
        txt.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#1e293b")),
            ("TEXTCOLOR",  (0,0), (-1,0), WHITE),
            ("FONTNAME",   (0,0), (-1,0), "Helvetica-Bold"),
            ("FONTSIZE",   (0,0), (-1,-1), 8),
            ("TEXTCOLOR",  (0,1), (-1,-1), C_RL_TEXT),
            ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.HexColor("#090c16"), C_RL_PANEL]),
            ("GRID", (0,0), (-1,-1), 0.3, colors.HexColor("#1e293b")),
            ("TOPPADDING",    (0,0),(-1,-1), 6),
            ("BOTTOMPADDING", (0,0),(-1,-1), 6),
            ("LEFTPADDING",   (0,0),(-1,-1), 6),
        ]))
        elems.append(txt)
        elems.append(Spacer(1, 16))

        # ── Resumen del sistema ───────────────────────────────────────────────
        sys_data = [[
            Paragraph(f"👥 Usuarios activos: <b>{total_users}</b>", body_style),
            Paragraph(f"📦 Productos activos: <b>{total_prods:,}</b>", body_style),
            Paragraph(f"📊 Promedio diario: <b>Q{total_rev/max(total_txns,1)*total_txns/30:,.0f}</b>", body_style),
        ]]
        st = Table(sys_data, colWidths=[2.43*inch]*3)
        st.setStyle(TableStyle([
            ("BACKGROUND", (0,0),(-1,-1), C_RL_PANEL),
            ("INNERGRID", (0,0),(-1,-1), 0.3, colors.HexColor("#1e293b")),
            ("BOX", (0,0),(-1,-1), 0.5, C_RL_VIOLET),
            ("TOPPADDING",    (0,0),(-1,-1), 10),
            ("BOTTOMPADDING", (0,0),(-1,-1), 10),
            ("LEFTPADDING",   (0,0),(-1,-1), 12),
        ]))
        elems.append(st)
        elems.append(Spacer(1, 10))
        elems.append(Paragraph(f"Dashboard generado por VentasFlow SaaS v3.0  ·  {now.strftime('%d/%m/%Y %H:%M:%S')}  ·  ventasflow.com", note_style))

        # ── Renderizar con fondo oscuro ───────────────────────────────────────
        def draw_bg(canvas, doc):
            canvas.saveState()
            canvas.setFillColor(C_RL_BG)
            canvas.rect(0, 0, letter[0], letter[1], fill=1, stroke=0)
            canvas.restoreState()

        doc.build(elems, onFirstPage=draw_bg, onLaterPages=draw_bg)
        pdf_buf.seek(0)

        fname = f"dashboard_ventasflow_{now.strftime('%Y%m%d_%H%M')}.pdf"
        return StreamingResponse(pdf_buf, media_type="application/pdf",
                                 headers={"Content-Disposition": f"attachment; filename={fname}"})

    except ImportError as e:
        raise HTTPException(status_code=500, detail=f"Dependencia faltante: {e}. Instala: pip install reportlab matplotlib")
    except Exception as e:
        import traceback
        raise HTTPException(status_code=500, detail=f"Error generando PDF: {str(e)}")

# ─── PRODUCTOS ────────────────────────────────────────────────────────────────
@app.get("/api/products")
def get_products(search: str = "", category: str = "", skip: int = 0, limit: int = 50, current_user=Depends(get_current_user)):
    query = "SELECT * FROM products WHERE active=1"
    params = []

    if search:
        query += " AND (name LIKE ? OR sku LIKE ?)"
        params += [f"%{search}%", f"%{search}%"]

    if category:
        query += " AND category=?"
        params.append(category)

    query += f" LIMIT {limit} OFFSET {skip}"

    with get_db() as conn:
        prods = conn.execute(query, params).fetchall()

    return [dict(p) for p in prods]

@app.get("/api/products/categories")
def get_categories(current_user=Depends(get_current_user)):
    with get_db() as conn:
        cats = conn.execute("SELECT DISTINCT category FROM products WHERE active=1 ORDER BY category").fetchall()
    return [c["category"] for c in cats]

# ─── VENTAS ───────────────────────────────────────────────────────────────────
@app.post("/api/sales")
def create_sale(data: SaleCreate, current_user=Depends(require_role("CAJERO", "ADMIN", "GERENTE", "SUPERADMIN"))):
    validate_license()

    with get_db() as conn:
        for item in data.items:
            prod = conn.execute("SELECT stock FROM products WHERE id=?", (item.product_id,)).fetchone()
            if not prod or prod["stock"] < item.quantity:
                raise HTTPException(status_code=400, detail=f"Stock insuficiente para producto ID {item.product_id}")

        cur = conn.execute("""
            INSERT INTO sales (cashier_id,cashier_name,customer_name,customer_nit,payment_method,total,items_json)
            VALUES (?,?,?,?,?,?,?)
        """, (
            current_user["id"],
            current_user["username"],
            data.customer_name,
            data.customer_nit,
            data.payment_method,
            data.total,
            json.dumps([i.model_dump() for i in data.items])
        ))

        sale_id = cur.lastrowid

        for item in data.items:
            conn.execute("""
                INSERT INTO sale_items (sale_id,product_id,product_name,quantity,unit_price,subtotal)
                VALUES (?,?,?,?,?,?)
            """, (
                sale_id,
                item.product_id,
                item.product_name,
                item.quantity,
                item.unit_price,
                item.subtotal
            ))

            conn.execute(
                "UPDATE products SET stock=stock-? WHERE id=?",
                (item.quantity, item.product_id)
            )

    return {"message": "Venta procesada exitosamente", "sale_id": sale_id}

@app.get("/api/sales")
def list_sales(skip: int = 0, limit: int = 50, current_user=Depends(require_role("GERENTE", "ADMIN", "SUPERADMIN"))):
    validate_license()

    with get_db() as conn:
        sales = conn.execute(
            "SELECT * FROM sales ORDER BY id DESC LIMIT ? OFFSET ?",
            (limit, skip)
        ).fetchall()

    return [dict(s) for s in sales]

# ─── FRONT / APOYO DEMO ───────────────────────────────────────────────────────
@app.get("/api/front/demo-users")
def front_demo_users():
    return {
        "title": "Usuarios demo visibles en frontend",
        "users": [
            {"role": "SUPERADMIN", "username": "superadmin", "password": "super123"},
            {"role": "ADMIN", "username": "admin", "password": "admin123"},
            {"role": "GERENTE", "username": "gerente", "password": "gerente123"},
            {"role": "CAJERO", "username": "cajero", "password": "cajero123"}
        ]
    }

@app.get("/api/health")
def health():
    lic = get_active_license()
    return {
        "status": "ok",
        "app": "VentasFlow",
        "version": "1.1.0",
        "license": lic["license_type"] if lic else "NO_LICENSE"
    }

# ─── CAMBIO 2: RUTAS MÁGICAS PARA LA INTERFAZ (INDEX.HTML) ───
# Esto le dice a Render que el link principal debe mostrar tu login
app.mount("/", StaticFiles(directory=".", html=True), name="static")

@app.get("/")
async def read_index():
    return FileResponse("index.html")
# ─────────────────────────────────────────────────────────────
