import base64
import json
import os
import sqlite3
from datetime import datetime
from functools import wraps
from pathlib import Path
from uuid import uuid4

from flask import Flask, flash, jsonify, redirect, render_template, request, send_file, session, url_for
from werkzeug.utils import secure_filename

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:
    psycopg2 = None
    RealDictCursor = None

BASE_DIR = Path(__file__).resolve().parent
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
DATABASE = Path(os.environ.get("DATABASE_PATH", BASE_DIR / "gaon_delivery.db"))
USE_POSTGRES = bool(DATABASE_URL)
UPLOAD_FOLDER = BASE_DIR / "static" / "uploads"
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-this-before-public-deploy")
app.config.update(
    UPLOAD_FOLDER=str(UPLOAD_FOLDER),
    MAX_CONTENT_LENGTH=5 * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "1234")

DEFAULT_SETTINGS = {
    "shop_name": "Gaon Ghar Delivery",
    "short_name": "Gaon Ghar",
    "location_name": "Apna Gaon",
    "hero_title": "Gaon ki dukaan, ab aapke phone par.",
    "hero_subtitle": "Kirana, dairy, snacks aur daily needs — sab ghar baithe order karein.",
    "delivery_minutes": "30–45",
    "delivery_type": "percent",
    "delivery_value": "10",
    "minimum_delivery_fee": "1",
    "upi_id": "",
    "support_phone": "",
    "announcement": "",
    "logo_image": "",
    "banner_image": "",
    "qr_image": "",
    "cash_on_delivery": "1",
    "orders_enabled": "1",
    "developer_name": "Himanshu",
    "developer_photo": "",
}


class DatabaseConnection:
    def __init__(self):
        self.is_postgres = USE_POSTGRES
        if self.is_postgres:
            if psycopg2 is None:
                raise RuntimeError("PostgreSQL driver missing. Run: pip install psycopg2-binary")
            self.connection = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
        else:
            DATABASE.parent.mkdir(parents=True, exist_ok=True)
            self.connection = sqlite3.connect(DATABASE, timeout=20)
            self.connection.row_factory = sqlite3.Row
            self.connection.execute("PRAGMA foreign_keys = ON")

    def execute(self, query, params=()):
        if self.is_postgres:
            query = query.replace("?", "%s")
            cursor = self.connection.cursor()
            cursor.execute(query, params)
            return cursor
        return self.connection.execute(query, params)

    def executemany(self, query, params):
        if self.is_postgres:
            query = query.replace("?", "%s")
            cursor = self.connection.cursor()
            cursor.executemany(query, params)
            return cursor
        return self.connection.executemany(query, params)

    def commit(self):
        self.connection.commit()

    def rollback(self):
        self.connection.rollback()

    def close(self):
        self.connection.close()


def get_database():
    return DatabaseConnection()


def upsert_setting(connection, key, value):
    if connection.is_postgres:
        connection.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            (key, value),
        )
    else:
        connection.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))


def create_database():
    UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)
    connection = get_database()
    id_type = "SERIAL PRIMARY KEY" if connection.is_postgres else "INTEGER PRIMARY KEY AUTOINCREMENT"
    connection.execute(f"""
        CREATE TABLE IF NOT EXISTS products (
            id {id_type},
            name TEXT NOT NULL,
            price INTEGER NOT NULL CHECK(price > 0),
            shop_name TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT 'Kirana',
            image_url TEXT DEFAULT '',
            is_available INTEGER NOT NULL DEFAULT 1,
            stock INTEGER NOT NULL DEFAULT 100,
            is_featured INTEGER NOT NULL DEFAULT 0
        )
    """)
    connection.execute(f"""
        CREATE TABLE IF NOT EXISTS orders (
            id {id_type},
            customer_name TEXT NOT NULL,
            phone TEXT NOT NULL,
            address TEXT NOT NULL,
            items TEXT NOT NULL,
            subtotal INTEGER NOT NULL DEFAULT 0,
            delivery_fee INTEGER NOT NULL DEFAULT 0,
            total INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'Pending',
            created_at TEXT NOT NULL
        )
    """)
    connection.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL DEFAULT ''
        )
    """)

    # Upgrade older SQLite databases without deleting existing products/orders.
    # CREATE TABLE IF NOT EXISTS does not add newly introduced columns, so we
    # add them safely one by one during every startup.
    if not connection.is_postgres:
        product_columns = {row[1] for row in connection.execute("PRAGMA table_info(products)").fetchall()}
        product_migrations = {
            "category": "TEXT NOT NULL DEFAULT 'Kirana'",
            "is_available": "INTEGER NOT NULL DEFAULT 1",
            "stock": "INTEGER NOT NULL DEFAULT 100",
            "is_featured": "INTEGER NOT NULL DEFAULT 0",
        }
        for column, definition in product_migrations.items():
            if column not in product_columns:
                connection.execute(f"ALTER TABLE products ADD COLUMN {column} {definition}")

        order_columns = {row[1] for row in connection.execute("PRAGMA table_info(orders)").fetchall()}
        order_migrations = {
            "subtotal": "INTEGER NOT NULL DEFAULT 0",
            "delivery_fee": "INTEGER NOT NULL DEFAULT 0",
        }
        for column, definition in order_migrations.items():
            if column not in order_columns:
                connection.execute(f"ALTER TABLE orders ADD COLUMN {column} {definition}")

        # Old orders only had total; preserve them by using total as subtotal.
        connection.execute("UPDATE orders SET subtotal = total WHERE subtotal = 0 AND total > 0")
    for key, value in DEFAULT_SETTINGS.items():
        if connection.is_postgres:
            connection.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT (key) DO NOTHING",
                (key, value),
            )
        else:
            connection.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, value))

    count = connection.execute("SELECT COUNT(*) AS total FROM products").fetchone()["total"]
    if count == 0:
        connection.executemany(
            "INSERT INTO products (name, price, shop_name, category, stock, is_featured) VALUES (?, ?, ?, ?, ?, ?)",
            [
                ("Aata 5 KG", 220, "Gaon Kirana Store", "Kirana", 20, 1),
                ("Chawal 5 KG", 300, "Gaon Kirana Store", "Kirana", 20, 1),
                ("Cheeni 1 KG", 50, "Gaon Kirana Store", "Kirana", 30, 0),
                ("Doodh 1 Litre", 65, "Dairy Shop", "Dairy", 15, 1),
                ("Biscuit Packet", 20, "Gaon Kirana Store", "Snacks", 50, 0),
            ],
        )
    connection.commit()
    connection.close()

def get_settings(connection=None):
    own = connection is None
    connection = connection or get_database()
    values = DEFAULT_SETTINGS.copy()
    values.update({row["key"]: row["value"] for row in connection.execute("SELECT key, value FROM settings")})
    if own:
        connection.close()
    return values


def admin_required(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        if not session.get("admin_logged_in"):
            return redirect(url_for("admin_login", next=request.path))
        return function(*args, **kwargs)
    return wrapper


def save_image(uploaded_file):
    if not uploaded_file or not uploaded_file.filename:
        return ""
    filename = secure_filename(uploaded_file.filename)
    if "." not in filename:
        raise ValueError("Photo file sahi select karo.")
    extension = filename.rsplit(".", 1)[1].lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise ValueError("Sirf PNG, JPG, JPEG ya WEBP photo upload karo.")
    raw = uploaded_file.read()
    if len(raw) > app.config["MAX_CONTENT_LENGTH"]:
        raise ValueError("Photo 5 MB se chhoti rakho.")
    mime = "image/jpeg" if extension in {"jpg", "jpeg"} else f"image/{extension}"
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


def image_src(value):
    if not value:
        return ""
    if value.startswith("data:image/"):
        return value
    return url_for("static", filename="uploads/" + value)

app.jinja_env.globals["image_src"] = image_src

def delivery_fee_for(subtotal, settings):
    try:
        value = max(0, float(settings.get("delivery_value", "10")))
        minimum = max(0, int(float(settings.get("minimum_delivery_fee", "1"))))
    except ValueError:
        value, minimum = 10, 1
    if subtotal <= 0:
        return 0
    fee = round(subtotal * value / 100) if settings.get("delivery_type") == "percent" else round(value)
    return max(minimum, fee)


@app.context_processor
def inject_globals():
    settings = get_settings()
    return {"site_settings": settings}


@app.route("/")
def home():
    connection = get_database()
    settings = get_settings(connection)
    products = connection.execute("""
        SELECT * FROM products WHERE is_available = 1 AND stock > 0
        ORDER BY is_featured DESC, category, shop_name, name
    """).fetchall()
    categories = sorted({row["category"] for row in products})
    connection.close()
    return render_template("index.html", products=products, categories=categories, settings=settings)


@app.route("/place-order", methods=["POST"])
def place_order():
    customer_name = request.form.get("customer_name", "").strip()[:80]
    phone = request.form.get("phone", "").strip()
    address = request.form.get("address", "").strip()[:500]
    if not customer_name or not phone or not address:
        flash("Naam, phone aur address bharna zaroori hai.", "error")
        return redirect(url_for("home"))
    if not phone.isdigit() or len(phone) != 10:
        flash("Mobile number 10 digits ka hona chahiye.", "error")
        return redirect(url_for("home"))

    connection = get_database()
    settings = get_settings(connection)
    if settings.get("orders_enabled") != "1":
        connection.close()
        flash("Abhi naye orders temporarily band hain.", "error")
        return redirect(url_for("home"))

    products = connection.execute("SELECT * FROM products WHERE is_available = 1 AND stock > 0").fetchall()
    selected_items, subtotal = [], 0
    stock_updates = []
    for product in products:
        try:
            quantity = int(request.form.get(f"quantity_{product['id']}", "0"))
        except (TypeError, ValueError):
            quantity = 0
        quantity = max(0, min(quantity, 20, product["stock"]))
        if quantity:
            item_subtotal = product["price"] * quantity
            selected_items.append({
                "product_id": product["id"], "name": product["name"], "shop_name": product["shop_name"],
                "price": product["price"], "quantity": quantity, "subtotal": item_subtotal,
            })
            stock_updates.append((quantity, product["id"]))
            subtotal += item_subtotal

    if not selected_items:
        connection.close()
        flash("Pehle cart me koi product add karo.", "error")
        return redirect(url_for("home"))

    delivery_fee = delivery_fee_for(subtotal, settings)
    total = subtotal + delivery_fee
    order_query = """
        INSERT INTO orders
        (customer_name, phone, address, items, subtotal, delivery_fee, total, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'Pending', ?)
    """ + (" RETURNING id" if connection.is_postgres else "")
    cursor = connection.execute(order_query, (
        customer_name, phone, address, json.dumps(selected_items, ensure_ascii=False),
        subtotal, delivery_fee, total, datetime.now().strftime("%d-%m-%Y %I:%M %p"),
    ))
    for quantity, product_id in stock_updates:
        connection.execute(("UPDATE products SET stock = GREATEST(0, stock - ?) WHERE id = ?" if connection.is_postgres else "UPDATE products SET stock = MAX(0, stock - ?) WHERE id = ?"), (quantity, product_id))
    connection.commit()
    order_id = cursor.fetchone()["id"] if connection.is_postgres else cursor.lastrowid
    connection.close()
    return render_template("success.html", order_id=order_id, subtotal=subtotal,
                           delivery_fee=delivery_fee, total=total, phone=phone, settings=settings)


@app.route("/track", methods=["GET", "POST"])
def track_order():
    order = None
    error = None
    if request.method == "POST":
        order_id = request.form.get("order_id", "").strip().lstrip("#")
        phone = request.form.get("phone", "").strip()
        if not order_id.isdigit() or not phone.isdigit():
            error = "Order number aur mobile number sahi bharo."
        else:
            connection = get_database()
            row = connection.execute("SELECT * FROM orders WHERE id = ? AND phone = ?", (int(order_id), phone)).fetchone()
            connection.close()
            if row:
                order = dict(row)
                try:
                    order["items_list"] = json.loads(order["items"])
                except (TypeError, json.JSONDecodeError):
                    order["items_list"] = []
                steps = ["Pending", "Accepted", "Out for Delivery", "Delivered"]
                order["progress_index"] = steps.index(order["status"]) if order["status"] in steps else -1
            else:
                error = "Is order number aur mobile se koi order nahi mila."
    return render_template("track.html", order=order, error=error)


@app.route("/health")
def health():
    return jsonify(status="ok"), 200


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    error = None
    if request.method == "POST":
        if request.form.get("password", "") == ADMIN_PASSWORD:
            session.clear()
            session["admin_logged_in"] = True
            return redirect(url_for("admin"))
        error = "Password galat hai."
    return render_template("login.html", error=error)


@app.route("/admin/logout", methods=["POST"])
def admin_logout():
    session.clear()
    return redirect(url_for("admin_login"))


@app.route("/admin")
@admin_required
def admin():
    connection = get_database()
    rows = connection.execute("SELECT * FROM orders ORDER BY id DESC").fetchall()
    stats = connection.execute("""
        SELECT COUNT(*) AS total_orders,
        COALESCE(SUM(CASE WHEN status='Delivered' THEN total ELSE 0 END),0) AS delivered_sales,
        SUM(CASE WHEN status='Pending' THEN 1 ELSE 0 END) AS pending_orders,
        SUM(CASE WHEN status='Delivered' THEN 1 ELSE 0 END) AS delivered_orders
        FROM orders
    """).fetchone()
    low_stock = connection.execute("SELECT COUNT(*) AS total FROM products WHERE stock <= 5").fetchone()["total"]
    connection.close()
    orders = []
    for row in rows:
        order = dict(row)
        try:
            order["items_list"] = json.loads(order["items"])
        except (TypeError, json.JSONDecodeError):
            order["items_list"] = []
        orders.append(order)
    statuses = ["Pending", "Accepted", "Out for Delivery", "Delivered", "Cancelled"]
    return render_template("admin.html", orders=orders, statuses=statuses, stats=stats, low_stock=low_stock)


@app.route("/update-status/<int:order_id>", methods=["POST"])
@admin_required
def update_status(order_id):
    allowed = ["Pending", "Accepted", "Out for Delivery", "Delivered", "Cancelled"]
    new_status = request.form.get("status", "Pending")
    if new_status not in allowed:
        return "Invalid status", 400
    connection = get_database()
    connection.execute("UPDATE orders SET status = ? WHERE id = ?", (new_status, order_id))
    connection.commit(); connection.close()
    flash(f"Order #{order_id} ka status update ho gaya.", "success")
    return redirect(url_for("admin"))


@app.route("/admin/orders/delete/<int:order_id>", methods=["POST"])
@admin_required
def delete_order(order_id):
    connection = get_database()
    connection.execute("DELETE FROM orders WHERE id = ?", (order_id,))
    connection.commit(); connection.close()
    flash(f"Order #{order_id} delete ho gaya.", "success")
    return redirect(url_for("admin"))


@app.route("/admin/products")
@admin_required
def admin_products():
    connection = get_database()
    products = connection.execute("SELECT * FROM products ORDER BY id DESC").fetchall()
    categories = [row["category"] for row in connection.execute("SELECT DISTINCT category FROM products ORDER BY category")]
    connection.close()
    return render_template("products.html", products=products, categories=categories)


@app.route("/admin/products/add", methods=["POST"])
@admin_required
def add_product():
    name = request.form.get("name", "").strip()[:100]
    shop_name = request.form.get("shop_name", "").strip()[:100]
    category = request.form.get("category", "Kirana").strip()[:50] or "Kirana"
    try:
        price = int(request.form.get("price", "0")); stock = max(0, int(request.form.get("stock", "0")))
    except ValueError:
        price, stock = 0, 0
    if not name or not shop_name or price <= 0:
        flash("Product naam, shop aur sahi price bharo.", "error")
        return redirect(url_for("admin_products"))
    try:
        image_filename = save_image(request.files.get("image"))
    except ValueError as error:
        flash(str(error), "error"); return redirect(url_for("admin_products"))
    connection = get_database()
    connection.execute("""INSERT INTO products
        (name, price, shop_name, category, image_url, is_available, stock, is_featured)
        VALUES (?, ?, ?, ?, ?, 1, ?, ?)
    """, (name, price, shop_name, category, image_filename, stock, 1 if request.form.get("is_featured") else 0))
    connection.commit(); connection.close()
    flash("Product successfully add ho gaya.", "success")
    return redirect(url_for("admin_products"))


@app.route("/admin/products/edit/<int:product_id>", methods=["GET", "POST"])
@admin_required
def edit_product(product_id):
    connection = get_database()
    product = connection.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    if product is None:
        connection.close(); return "Product nahi mila.", 404
    if request.method == "POST":
        name = request.form.get("name", "").strip()[:100]
        shop_name = request.form.get("shop_name", "").strip()[:100]
        category = request.form.get("category", "Kirana").strip()[:50] or "Kirana"
        try:
            price = int(request.form.get("price", "0")); stock = max(0, int(request.form.get("stock", "0")))
        except ValueError:
            price, stock = 0, 0
        if not name or not shop_name or price <= 0:
            connection.close(); flash("Product details sahi bharo.", "error"); return redirect(request.url)
        image_filename = product["image_url"] or ""
        uploaded_file = request.files.get("image")
        if uploaded_file and uploaded_file.filename:
            try:
                new_image = save_image(uploaded_file)
                image_filename = new_image
            except ValueError as error:
                connection.close(); flash(str(error), "error"); return redirect(request.url)
        connection.execute("""UPDATE products SET name=?, price=?, shop_name=?, category=?, image_url=?,
                           stock=?, is_featured=? WHERE id=?""",
                           (name, price, shop_name, category, image_filename, stock,
                            1 if request.form.get("is_featured") else 0, product_id))
        connection.commit(); connection.close()
        flash("Product update ho gaya.", "success")
        return redirect(url_for("admin_products"))
    connection.close()
    return render_template("edit_product.html", product=product)


@app.route("/admin/products/toggle/<int:product_id>", methods=["POST"])
@admin_required
def toggle_product(product_id):
    connection = get_database()
    connection.execute("UPDATE products SET is_available = 1-is_available WHERE id = ?", (product_id,))
    connection.commit(); connection.close()
    return redirect(url_for("admin_products"))


@app.route("/admin/products/delete/<int:product_id>", methods=["POST"])
@admin_required
def delete_product(product_id):
    connection = get_database()
    product = connection.execute("SELECT image_url FROM products WHERE id=?", (product_id,)).fetchone()
    connection.execute("DELETE FROM products WHERE id = ?", (product_id,))
    connection.commit(); connection.close()
    flash("Product delete ho gaya.", "success")
    return redirect(url_for("admin_products"))


@app.route("/admin/settings", methods=["GET", "POST"])
@admin_required
def admin_settings():
    connection = get_database()
    settings = get_settings(connection)
    if request.method == "POST":
        editable = ["shop_name", "short_name", "location_name", "hero_title", "hero_subtitle",
                    "delivery_minutes", "delivery_type", "delivery_value", "minimum_delivery_fee",
                    "upi_id", "support_phone", "announcement", "developer_name"]
        for key in editable:
            value = request.form.get(key, "").strip()[:500]
            upsert_setting(connection, key, value)
        for key in ["cash_on_delivery", "orders_enabled"]:
            upsert_setting(connection, key, "1" if request.form.get(key) else "0")
        for field, key in [("logo_image", "logo_image"), ("banner_image", "banner_image"), ("qr_image", "qr_image"), ("developer_photo", "developer_photo")]:
            uploaded = request.files.get(field)
            if uploaded and uploaded.filename:
                try:
                    new_name = save_image(uploaded)
                    upsert_setting(connection, key, new_name)
                except ValueError as error:
                    connection.close(); flash(str(error), "error"); return redirect(request.url)
        connection.commit(); connection.close()
        flash("App settings save ho gayi.", "success")
        return redirect(url_for("admin_settings"))
    connection.close()
    return render_template("settings.html", settings=settings)


@app.route("/admin/backup")
@admin_required
def download_backup():
    connection = get_database()
    backup = {
        "exported_at": datetime.now().isoformat(),
        "products": [dict(row) for row in connection.execute("SELECT * FROM products ORDER BY id").fetchall()],
        "orders": [dict(row) for row in connection.execute("SELECT * FROM orders ORDER BY id").fetchall()],
        "settings": [dict(row) for row in connection.execute("SELECT * FROM settings ORDER BY key").fetchall()],
    }
    connection.close()
    payload = json.dumps(backup, ensure_ascii=False, indent=2).encode("utf-8")
    from io import BytesIO
    return send_file(BytesIO(payload), as_attachment=True,
                     download_name=f"gaon-delivery-backup-{datetime.now():%Y-%m-%d}.json",
                     mimetype="application/json")


create_database()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=os.environ.get("FLASK_DEBUG") == "1")
