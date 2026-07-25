import json
import os
import sqlite3
from datetime import datetime
from functools import wraps
from pathlib import Path
from uuid import uuid4

from flask import Flask, flash, jsonify, redirect, render_template, request, session, url_for
from werkzeug.utils import secure_filename

BASE_DIR = Path(__file__).resolve().parent
DATABASE = Path(os.environ.get("DATABASE_PATH", BASE_DIR / "gaon_delivery.db"))
UPLOAD_FOLDER = BASE_DIR / "static" / "uploads"
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}
DELIVERY_RATE = 0.10

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-this-before-public-deploy")
app.config.update(
    UPLOAD_FOLDER=str(UPLOAD_FOLDER),
    MAX_CONTENT_LENGTH=5 * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "1234")


def get_database():
    DATABASE.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATABASE, timeout=20)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def column_names(connection, table_name):
    return {row["name"] for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()}


def create_database():
    UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)
    connection = get_database()
    connection.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            price INTEGER NOT NULL CHECK(price > 0),
            shop_name TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT 'Kirana',
            image_url TEXT DEFAULT '',
            is_available INTEGER NOT NULL DEFAULT 1
        )
    """)
    columns = column_names(connection, "products")
    if "image_url" not in columns:
        connection.execute("ALTER TABLE products ADD COLUMN image_url TEXT DEFAULT ''")
    if "is_available" not in columns:
        connection.execute("ALTER TABLE products ADD COLUMN is_available INTEGER NOT NULL DEFAULT 1")
    if "category" not in columns:
        connection.execute("ALTER TABLE products ADD COLUMN category TEXT NOT NULL DEFAULT 'Kirana'")

    connection.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    order_columns = column_names(connection, "orders")
    if "subtotal" not in order_columns:
        connection.execute("ALTER TABLE orders ADD COLUMN subtotal INTEGER NOT NULL DEFAULT 0")
    if "delivery_fee" not in order_columns:
        connection.execute("ALTER TABLE orders ADD COLUMN delivery_fee INTEGER NOT NULL DEFAULT 0")

    count = connection.execute("SELECT COUNT(*) AS total FROM products").fetchone()["total"]
    if count == 0:
        connection.executemany(
            "INSERT INTO products (name, price, shop_name, category) VALUES (?, ?, ?, ?)",
            [
                ("Aata 5 KG", 220, "Gaon Kirana Store", "Kirana"),
                ("Chawal 5 KG", 300, "Gaon Kirana Store", "Kirana"),
                ("Cheeni 1 KG", 50, "Gaon Kirana Store", "Kirana"),
                ("Doodh 1 Litre", 65, "Dairy Shop", "Dairy"),
                ("Biscuit Packet", 20, "Gaon Kirana Store", "Snacks"),
            ],
        )
    connection.commit()
    connection.close()


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
    new_filename = f"{uuid4().hex}.{extension}"
    uploaded_file.save(UPLOAD_FOLDER / new_filename)
    return new_filename


@app.context_processor
def inject_globals():
    return {"delivery_rate": int(DELIVERY_RATE * 100)}


@app.route("/")
def home():
    connection = get_database()
    products = connection.execute("""
        SELECT * FROM products WHERE is_available = 1
        ORDER BY category, shop_name, name
    """).fetchall()
    categories = sorted({row["category"] for row in products})
    connection.close()
    return render_template("index.html", products=products, categories=categories)


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
    products = connection.execute("SELECT * FROM products WHERE is_available = 1").fetchall()
    selected_items, subtotal = [], 0
    for product in products:
        try:
            quantity = int(request.form.get(f"quantity_{product['id']}", "0"))
        except (TypeError, ValueError):
            quantity = 0
        quantity = max(0, min(quantity, 20))
        if quantity:
            item_subtotal = product["price"] * quantity
            selected_items.append({
                "name": product["name"], "shop_name": product["shop_name"],
                "price": product["price"], "quantity": quantity, "subtotal": item_subtotal,
            })
            subtotal += item_subtotal

    if not selected_items:
        connection.close()
        flash("Pehle cart me koi product add karo.", "error")
        return redirect(url_for("home"))

    delivery_fee = max(1, round(subtotal * DELIVERY_RATE))
    total = subtotal + delivery_fee
    cursor = connection.execute("""
        INSERT INTO orders
        (customer_name, phone, address, items, subtotal, delivery_fee, total, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'Pending', ?)
    """, (
        customer_name, phone, address, json.dumps(selected_items, ensure_ascii=False),
        subtotal, delivery_fee, total, datetime.now().strftime("%d-%m-%Y %I:%M %p"),
    ))
    connection.commit()
    order_id = cursor.lastrowid
    connection.close()
    return render_template("success.html", order_id=order_id, subtotal=subtotal,
                           delivery_fee=delivery_fee, total=total, phone=phone)


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
            row = connection.execute(
                "SELECT * FROM orders WHERE id = ? AND phone = ?", (int(order_id), phone)
            ).fetchone()
            connection.close()
            if row:
                order = dict(row)
                try:
                    order["items_list"] = json.loads(order["items"])
                except (TypeError, json.JSONDecodeError):
                    order["items_list"] = []

                progress_steps = ["Pending", "Accepted", "Out for Delivery", "Delivered"]
                order["progress_index"] = (
                    progress_steps.index(order["status"])
                    if order["status"] in progress_steps
                    else -1
                )
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
        SUM(CASE WHEN status='Pending' THEN 1 ELSE 0 END) AS pending_orders
        FROM orders
    """).fetchone()
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
    return render_template("admin.html", orders=orders, statuses=statuses, stats=stats)


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


@app.route("/admin/products")
@admin_required
def admin_products():
    connection = get_database()
    products = connection.execute("SELECT * FROM products ORDER BY id DESC").fetchall()
    connection.close()
    return render_template("products.html", products=products)


@app.route("/admin/products/add", methods=["POST"])
@admin_required
def add_product():
    name = request.form.get("name", "").strip()[:100]
    shop_name = request.form.get("shop_name", "").strip()[:100]
    category = request.form.get("category", "Kirana").strip()[:50] or "Kirana"
    try: price = int(request.form.get("price", "0"))
    except ValueError: price = 0
    if not name or not shop_name or price <= 0:
        flash("Product naam, shop aur sahi price bharo.", "error")
        return redirect(url_for("admin_products"))
    try: image_filename = save_image(request.files.get("image"))
    except ValueError as error:
        flash(str(error), "error"); return redirect(url_for("admin_products"))
    connection = get_database()
    connection.execute("""INSERT INTO products
        (name, price, shop_name, category, image_url, is_available) VALUES (?, ?, ?, ?, ?, 1)
    """, (name, price, shop_name, category, image_filename))
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
        try: price = int(request.form.get("price", "0"))
        except ValueError: price = 0
        if not name or not shop_name or price <= 0:
            connection.close(); flash("Product details sahi bharo.", "error"); return redirect(request.url)
        image_filename = product["image_url"] or ""
        uploaded_file = request.files.get("image")
        if uploaded_file and uploaded_file.filename:
            try: image_filename = save_image(uploaded_file)
            except ValueError as error:
                connection.close(); flash(str(error), "error"); return redirect(request.url)
        connection.execute("""UPDATE products SET name=?, price=?, shop_name=?, category=?, image_url=? WHERE id=?""",
                           (name, price, shop_name, category, image_filename, product_id))
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
    if product and product["image_url"]:
        try: (UPLOAD_FOLDER / product["image_url"]).unlink(missing_ok=True)
        except OSError: pass
    flash("Product delete ho gaya.", "success")
    return redirect(url_for("admin_products"))


create_database()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=os.environ.get("FLASK_DEBUG") == "1")
