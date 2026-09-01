from flask import Flask, render_template, request, redirect, session, send_file, flash, url_for, jsonify
from flask_socketio import SocketIO, emit
from reportlab.pdfgen import canvas
from datetime import date, timedelta, datetime
import mysql.connector
import json
from functools import wraps
import os
import pytesseract
from PIL import Image, ImageEnhance, ImageFilter
import re
import traceback

# ============================================================
# DISABLE EXCESSIVE LOGGING
# ============================================================
import logging
logging.basicConfig(level=logging.ERROR)
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "pantrypulse123")
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size

# Ensure upload folder exists
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Enable WebSocket
socketio = SocketIO(app, cors_allowed_origins="*")

# ============================================================
# TESSERACT OCR CONFIGURATION
# ============================================================

# Set Tesseract path - use env var if provided, otherwise rely on system PATH
# (On Linux/Docker/Render, tesseract-ocr is installed via apt and is already on PATH)
_tesseract_cmd = os.environ.get("TESSERACT_CMD")
if _tesseract_cmd:
    pytesseract.pytesseract.tesseract_cmd = _tesseract_cmd
elif os.name == "nt":
    # Local Windows dev fallback
    pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

# ============================================================
# DATABASE CONNECTION
# ============================================================

def get_db_connection():
    try:
        db = mysql.connector.connect(
            host=os.environ.get("DB_HOST", "localhost"),
            port=int(os.environ.get("DB_PORT", "3306")),
            user=os.environ.get("DB_USER", "root"),
            password=os.environ.get("DB_PASSWORD", ""),
            database=os.environ.get("DB_NAME", "pantrypluse")
        )
        return db
    except mysql.connector.Error as err:
        print(f"Database connection error: {err}")
        return None

# ============================================================
# HELPER FUNCTIONS
# ============================================================

def to_float(value):
    if value is None:
        return 0
    return float(value)

def validate_product_name(name):
    if not name or len(name.strip()) < 1:
        return False
    name = name.strip()
    if not re.search(r'[a-zA-Z]', name):
        return False
    if len(name) < 2:
        return False
    if re.match(r'^[\d\s\.\,\-\/]+$', name):
        return False
    return True

def clean_product_name(name):
    if not name:
        return ""
    name = re.sub(r'[^\w\s\.\-\(\)]', '', name)
    name = ' '.join(name.split())
    name = name.title()
    return name

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "user_id" not in session:
            flash("Please login to access this page!", "error")
            return redirect("/login")
        return f(*args, **kwargs)
    return decorated_function

@app.template_filter('fromjson')
def fromjson_filter(value):
    try:
        return json.loads(value) if value else []
    except:
        return []

@app.template_filter('format_date')
def format_date_filter(value):
    if value:
        return value.strftime('%d %b %Y')
    return ''

# ============================================================
# AUTO CATEGORY DETECTION
# ============================================================

def detect_category(product_name):
    name = product_name.lower()
    categories = {
        'Dairy': ['milk', 'butter', 'cheese', 'curd', 'yogurt', 'paneer', 'ghee', 'cream', 'buttermilk', 'lassi'],
        'Fruits': ['apple', 'banana', 'orange', 'mango', 'grapes', 'watermelon', 'lemon', 'strawberry', 'kiwi', 'pear', 'pineapple', 'papaya', 'pomegranate', 'guava', 'muskmelon', 'sapota', 'litchi', 'dragon fruit'],
        'Vegetables': ['tomato', 'potato', 'onion', 'carrot', 'cabbage', 'cauliflower', 'broccoli', 'spinach', 'capsicum', 'cucumber', 'pumpkin', 'garlic', 'ginger', 'peas', 'corn', 'beans', 'brinjal', 'beetroot', 'ladies finger', 'radish'],
        'Bakery': ['bread', 'bun', 'cake', 'biscuit', 'cookie', 'pastry', 'doughnut', 'pizza base', 'croissant'],
        'Grains': ['atta', 'rice', 'wheat', 'flour', 'pasta', 'noodle', 'cereal', 'oats', 'semolina', 'maida', 'besan', 'cornflakes'],
        'Poultry': ['egg', 'chicken', 'turkey', 'duck', 'meat'],
        'Snacks': ['maggi', 'chips', 'biscuit', 'chocolate', 'candy', 'namkeen', 'mixture', 'kurkure', 'lays'],
        'Beverages': ['tea', 'coffee', 'juice', 'soda', 'water', 'milk shake', 'smoothie', 'coconut water'],
        'Oils': ['sunflower oil', 'olive oil', 'coconut oil', 'mustard oil', 'groundnut oil', 'ghee'],
        'Spices': ['salt', 'sugar', 'jaggery', 'spice', 'masala', 'chilli', 'turmeric', 'cumin', 'coriander', 'pepper', 'cardamom', 'cinnamon', 'clove'],
        'Sauces': ['sauce', 'ketchup', 'mayonnaise', 'mustard', 'pickle', 'vinegar', 'soy sauce'],
        'Canned': ['canned', 'tin', 'preserved', 'pickle', 'jam', 'jelly', 'honey'],
        'Frozen': ['frozen', 'ice cream', 'popsicle', 'frozen vegetables'],
        'Meat': ['chicken', 'mutton', 'pork', 'beef', 'fish', 'prawns', 'crab'],
        'Baking': ['baking soda', 'baking powder', 'vanilla essence', 'cocoa powder', 'icing sugar', 'sprinklers']
    }
    for category, keywords in categories.items():
        for keyword in keywords:
            if keyword in name:
                return category
    return 'Pantry'

def detect_expiry_days(product_name):
    name = product_name.lower()
    expiry_mapping = {
        'Dairy': 7, 'Fruits': 10, 'Vegetables': 7, 'Bakery': 5,
        'Grains': 365, 'Poultry': 15, 'Snacks': 180, 'Beverages': 180,
        'Oils': 365, 'Spices': 1095, 'Sauces': 365, 'Canned': 365,
        'Frozen': 180, 'Meat': 3, 'Baking': 365, 'Pantry': 30
    }
    specific_expiry = {
        'milk': 7, 'bread': 5, 'egg': 15, 'tomato': 7, 'potato': 45,
        'onion': 60, 'apple': 15, 'banana': 5, 'orange': 10, 'grapes': 7,
        'carrot': 15, 'cabbage': 7, 'spinach': 5, 'capsicum': 10,
        'cucumber': 7, 'garlic': 60, 'ginger': 30, 'lemon': 15,
        'mango': 7, 'watermelon': 5, 'paneer': 5, 'ghee': 180,
        'butter': 30, 'cheese': 60, 'yogurt': 5, 'curd': 5,
        'chicken': 3, 'fish': 2, 'mutton': 3, 'pasta': 365,
        'noodle': 180, 'cereal': 365, 'oats': 365, 'honey': 1095,
        'jam': 365, 'peanut butter': 180, 'ketchup': 180,
        'mayonnaise': 180, 'pickle': 365, 'vinegar': 1095,
        'soy sauce': 365, 'salt': 1095, 'sugar': 1095,
        'jaggery': 1095, 'tea': 365, 'coffee': 365, 'juice': 90,
        'soda': 180, 'water': 365, 'ice cream': 180,
        'frozen vegetables': 180, 'chips': 90, 'chocolate': 180,
        'biscuit': 90, 'cake': 7, 'bun': 3, 'croissant': 3,
        'pizza base': 7, 'doughnut': 3, 'pastry': 3, 'maggi': 180
    }
    for keyword, days in specific_expiry.items():
        if keyword in name:
            return days
    category = detect_category(product_name)
    return expiry_mapping.get(category, 30)

# ============================================================
# HOME & AUTH ROUTES
# ============================================================

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    # If user is already logged in, redirect to dashboard
    if "user_id" in session:
        return redirect("/welcome")
    
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "").strip()
        
        if not email or not password:
            flash("Please enter both email and password!", "error")
            return redirect("/login")
        
        db = get_db_connection()
        if not db:
            flash("Database connection error!", "error")
            return redirect("/login")
        
        cursor = db.cursor()
        cursor.execute("SELECT * FROM users WHERE email=%s", (email,))
        user = cursor.fetchone()
        cursor.close()
        db.close()
        
        if user:
            if user[3] == password:
                session["user_id"] = user[0]
                session["fullname"] = user[1]
                session["email"] = user[2]
                flash("Login successful! Welcome back!", "success")
                return redirect("/welcome")
            else:
                flash("Invalid email or password!", "error")
        else:
            flash("Invalid email or password!", "error")
        
        return redirect("/login")
    
    return render_template("login.html")

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        fullname = request.form.get("fullname", "").strip()
        email = request.form.get("email", "").strip()
        phone = request.form.get("phone", "").strip()
        password = request.form.get("password", "").strip()
        
        if not fullname or not email or not password:
            flash("Please fill all required fields!", "error")
            return redirect("/register")
        
        if phone and not phone.isdigit():
            flash("Phone number must contain only digits!", "error")
            return redirect("/register")
        
        if phone and len(phone) != 10:
            flash("Please enter a valid 10-digit phone number!", "error")
            return redirect("/register")
        
        db = get_db_connection()
        if not db:
            flash("Database connection error!", "error")
            return redirect("/register")
        
        cursor = db.cursor()
        try:
            cursor.execute("""
                INSERT INTO users (fullname, email, phone, password) 
                VALUES (%s, %s, %s, %s)
            """, (fullname, email, phone, password))
            db.commit()
            flash("Registration successful! Please login.", "success")
            cursor.close()
            db.close()
            return redirect("/login")
        except mysql.connector.IntegrityError:
            flash("Email already exists!", "error")
            cursor.close()
            db.close()
            return redirect("/register")
        except Exception as e:
            flash(f"Error: {str(e)}", "error")
            cursor.close()
            db.close()
            return redirect("/register")
    
    return render_template("register.html")

@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out successfully!", "info")
    return redirect("/login")

# ============================================================
# DASHBOARD / WELCOME
# ============================================================

@app.route("/welcome")
@login_required
def welcome():
    user_id = session["user_id"]
    today = date.today()
    db = get_db_connection()
    if not db:
        flash("Database connection error!", "error")
        return redirect("/login")
    cursor = db.cursor()
    
    cursor.execute("SELECT COUNT(*) FROM pantry WHERE user_id=%s", (user_id,))
    pantry_count = cursor.fetchone()[0]
    
    cursor.execute("""
        SELECT COUNT(*) FROM pantry 
        WHERE user_id=%s 
        AND expiry_date BETWEEN %s AND DATE_ADD(%s, INTERVAL 3 DAY)
    """, (user_id, today, today))
    expiring_count = cursor.fetchone()[0]
    
    cursor.execute("""
        SELECT COUNT(*) FROM pantry 
        WHERE user_id=%s AND expiry_date < %s
    """, (user_id, today))
    expired_count = cursor.fetchone()[0]
    
    safe_count = pantry_count - expiring_count - expired_count
    sustainability_score = int((safe_count / pantry_count) * 100) if pantry_count > 0 else 0
    
    cursor.execute("""
        SELECT products.product_name, pantry.expiry_date,
               DATEDIFF(pantry.expiry_date, %s) as days_left
        FROM pantry
        JOIN products ON pantry.product_id = products.id
        WHERE pantry.user_id=%s 
        AND pantry.expiry_date BETWEEN %s AND DATE_ADD(%s, INTERVAL 7 DAY)
        ORDER BY pantry.expiry_date ASC
        LIMIT 5
    """, (today, user_id, today, today))
    expiring_notifications = cursor.fetchall()
    
    cursor.execute("SELECT COUNT(*) FROM purchase_logs WHERE user_id=%s", (user_id,))
    total_logs = cursor.fetchone()[0]
    
    cursor.execute("""
        SELECT SUM(products.price * pantry.quantity)
        FROM pantry
        JOIN products ON pantry.product_id = products.id
        WHERE pantry.user_id=%s
    """, (user_id,))
    total_value = cursor.fetchone()[0]
    total_value = to_float(total_value)
    
    cursor.execute("""
        SELECT id, store_name, purchase_date, total_amount, items
        FROM purchase_logs
        WHERE user_id=%s
        ORDER BY id DESC
        LIMIT 5
    """, (user_id,))
    recent_logs = cursor.fetchall()
    
    parsed_logs = []
    for log in recent_logs:
        log_list = list(log)
        try:
            items = json.loads(log[4]) if log[4] else []
            log_list[4] = items
        except:
            log_list[4] = []
        parsed_logs.append(tuple(log_list))
    
    cursor.execute("""
        SELECT products.product_name 
        FROM pantry
        JOIN products ON pantry.product_id = products.id
        WHERE pantry.user_id=%s
    """, (user_id,))
    pantry_items = [item[0].lower() for item in cursor.fetchall()]
    
    cursor.execute("SELECT * FROM recipes")
    all_recipes = cursor.fetchall()
    recipe_count = 0
    for recipe in all_recipes:
        recipe_items = recipe[2].lower().split(",")
        if all(ingredient.strip() in pantry_items for ingredient in recipe_items):
            recipe_count += 1
    
    cursor.close()
    db.close()
    
    return render_template("dashboard.html", 
                          pantry_count=pantry_count,
                          expiring_count=expiring_count,
                          expired_count=expired_count,
                          safe_count=safe_count,
                          sustainability_score=sustainability_score,
                          expiring_notifications=expiring_notifications,
                          total_logs=total_logs,
                          total_value=total_value,
                          recent_logs=parsed_logs,
                          recipe_count=recipe_count,
                          pantry_items=pantry_items,
                          today=today)

# ============================================================
# PANTRY ROUTES
# ============================================================

@app.route("/pantry")
@login_required
def pantry():
    try:
        db = get_db_connection()
        if not db:
            flash("Database connection error!", "error")
            return render_template("pantry.html", items=[], today=date.today())
        
        cursor = db.cursor(dictionary=True)
        
        cursor.execute("""
            SELECT 
                pantry.id as pantry_id,
                products.id as product_id,
                products.product_name,
                products.category,
                products.price,
                pantry.quantity,
                pantry.purchase_date,
                pantry.expiry_date
            FROM pantry
            JOIN products ON pantry.product_id = products.id
            WHERE pantry.user_id = %s
            ORDER BY pantry.expiry_date ASC
        """, (session["user_id"],))
        
        items = cursor.fetchall()
        cursor.close()
        db.close()
        
        return render_template("pantry.html", items=items, today=date.today())
        
    except Exception as e:
        print(f"Error in pantry route: {str(e)}")
        flash(f"Error loading pantry: {str(e)}", "error")
        return render_template("pantry.html", items=[], today=date.today())

@app.route("/consume_item/<int:pantry_id>", methods=["POST"])
@login_required
def consume_item(pantry_id):
    try:
        db = get_db_connection()
        if not db:
            return jsonify({"success": False, "error": "Database connection error"}), 500
        
        cursor = db.cursor()
        
        cursor.execute("""
            SELECT products.product_name, products.price, pantry.quantity 
            FROM pantry 
            JOIN products ON pantry.product_id = products.id
            WHERE pantry.id=%s AND pantry.user_id=%s
        """, (pantry_id, session["user_id"]))
        item = cursor.fetchone()
        
        if item:
            product_name = item[0]
            price = to_float(item[1])
            quantity = int(item[2]) if item[2] else 1
            total_value = price * quantity
            
            cursor.execute("""
                UPDATE users 
                SET eaten_items = COALESCE(eaten_items, 0) + 1,
                    money_saved = COALESCE(money_saved, 0) + %s
                WHERE id=%s
            """, (total_value, session["user_id"]))
            db.commit()
            
            cursor.execute("""
                DELETE FROM pantry 
                WHERE id=%s AND user_id=%s
            """, (pantry_id, session["user_id"]))
            db.commit()
            
            cursor.close()
            db.close()
            
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify({
                    "success": True,
                    "message": f"🍽️ '{product_name}' consumed! Saved ₹{total_value:.2f}",
                    "action": "eat"
                })
            
            flash(f"🍽️ '{product_name}' consumed! Saved ₹{total_value:.2f}", "success")
        else:
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify({"success": False, "error": "Item not found"}), 404
            flash("Item not found!", "error")
        
        return redirect("/pantry")
        
    except Exception as e:
        print(f"Error in consume_item: {str(e)}")
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({"success": False, "error": str(e)}), 500
        flash(f"Error: {str(e)}", "error")
        return redirect("/pantry")

@app.route("/remove_item/<int:pantry_id>", methods=["POST"])
@login_required
def remove_item(pantry_id):
    try:
        db = get_db_connection()
        if not db:
            return jsonify({"success": False, "error": "Database connection error"}), 500
        
        cursor = db.cursor()
        
        cursor.execute("""
            SELECT products.product_name, products.price, pantry.quantity 
            FROM pantry 
            JOIN products ON pantry.product_id = products.id
            WHERE pantry.id=%s AND pantry.user_id=%s
        """, (pantry_id, session["user_id"]))
        item = cursor.fetchone()
        
        if item:
            product_name = item[0]
            price = to_float(item[1])
            quantity = int(item[2]) if item[2] else 1
            total_value = price * quantity
            
            cursor.execute("""
                UPDATE users 
                SET wasted_items = COALESCE(wasted_items, 0) + 1,
                    money_wasted = COALESCE(money_wasted, 0) + %s
                WHERE id=%s
            """, (total_value, session["user_id"]))
            db.commit()
            
            cursor.execute("""
                DELETE FROM pantry 
                WHERE id=%s AND user_id=%s
            """, (pantry_id, session["user_id"]))
            db.commit()
            
            cursor.close()
            db.close()
            
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify({
                    "success": True,
                    "message": f"🗑️ '{product_name}' discarded! Wasted ₹{total_value:.2f}",
                    "action": "discard"
                })
            
            flash(f"🗑️ '{product_name}' discarded! Wasted ₹{total_value:.2f}", "warning")
        else:
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify({"success": False, "error": "Item not found"}), 404
            flash("Item not found!", "error")
        
        return redirect("/pantry")
        
    except Exception as e:
        print(f"Error in remove_item: {str(e)}")
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({"success": False, "error": str(e)}), 500
        flash(f"Error: {str(e)}", "error")
        return redirect("/pantry")

@app.route("/add_pantry", methods=["GET", "POST"])
@login_required
def add_pantry():
    if request.method == "POST":
        product_id = request.form.get("product_id")
        custom_product_name = request.form.get("custom_product_name", "").strip()
        quantity = request.form.get("quantity")
        purchase_date = request.form.get("purchase_date")
        expiry_date = request.form.get("expiry_date")
        
        if not quantity or not purchase_date or not expiry_date:
            flash("Please fill all required fields!", "error")
            return redirect("/add_pantry")
        
        db = get_db_connection()
        if not db:
            flash("Database connection error!", "error")
            return redirect("/add_pantry")
        
        cursor = db.cursor()
        
        if custom_product_name:
            if not validate_product_name(custom_product_name):
                flash("Please enter a valid product name (must contain letters, not just numbers)", "error")
                cursor.close()
                db.close()
                return redirect("/add_pantry")
            custom_product_name = clean_product_name(custom_product_name)
            cursor.execute(
                "SELECT id FROM products WHERE LOWER(product_name) = LOWER(%s)",
                (custom_product_name,)
            )
            existing = cursor.fetchone()
            if existing:
                product_id = existing[0]
            else:
                cursor.execute("""
                    INSERT INTO products (product_name, category, price, image, stock, expiry_days)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (custom_product_name, "Custom", 0, "default.jpg", 1, 30))
                db.commit()
                product_id = cursor.lastrowid
                flash(f"New product '{custom_product_name}' added!", "success")
        elif product_id and product_id != "":
            product_id = int(product_id)
        else:
            flash("Please select or type a product name!", "error")
            cursor.close()
            db.close()
            return redirect("/add_pantry")
        
        try:
            cursor.execute("""
                INSERT INTO pantry (user_id, product_id, quantity, purchase_date, expiry_date)
                VALUES (%s, %s, %s, %s, %s)
            """, (session["user_id"], product_id, quantity, purchase_date, expiry_date))
            db.commit()
            flash("Item added to pantry successfully!", "success")
            cursor.close()
            db.close()
            return redirect("/pantry")
        except Exception as e:
            db.rollback()
            flash(f"Error adding item: {str(e)}", "error")
            cursor.close()
            db.close()
            return redirect("/add_pantry")
    
    db = get_db_connection()
    if not db:
        flash("Database connection error!", "error")
        return render_template("add_pantry.html", products=[])
    
    cursor = db.cursor()
    cursor.execute("SELECT id, product_name FROM products ORDER BY product_name")
    products = cursor.fetchall()
    cursor.close()
    db.close()
    
    return render_template("add_pantry.html", products=products)

# ============================================================
# PURCHASE ROUTES
# ============================================================

@app.route("/purchase")
@login_required
def purchase():
    try:
        db = get_db_connection()
        if not db:
            flash("Database connection error!", "error")
            return render_template("purchase.html", logs=[])
        
        cursor = db.cursor()
        cursor.execute("""
            SELECT id, store_name, purchase_date, total_amount, items
            FROM purchase_logs
            WHERE user_id=%s
            ORDER BY id DESC
        """, (session["user_id"],))
        
        logs = cursor.fetchall()
        cursor.close()
        db.close()
        
        parsed_logs = []
        for log in logs:
            log_list = list(log)
            try:
                items = json.loads(log[4]) if log[4] else []
                log_list[4] = items
            except:
                log_list[4] = []
            parsed_logs.append(tuple(log_list))
        
        return render_template("purchase.html", logs=parsed_logs)
        
    except Exception as e:
        print(f"Error in purchase route: {str(e)}")
        flash(f"Error loading purchases: {str(e)}", "error")
        return render_template("purchase.html", logs=[])

@app.route("/add_purchase", methods=["GET", "POST"])
@login_required
def add_purchase():
    if request.method == "POST":
        store_name = request.form.get("store_name", "").strip()
        purchase_date = request.form.get("purchase_date")
        total_amount = request.form.get("total_amount", 0)
        
        if not store_name or not purchase_date:
            flash("Please fill all required fields!", "error")
            return redirect("/add_purchase")
        
        item_names = request.form.getlist("item_name[]")
        item_qtys = request.form.getlist("item_qty[]")
        item_prices = request.form.getlist("item_price[]")
        item_expiries = request.form.getlist("item_expiry[]")
        
        items = []
        for i in range(len(item_names)):
            name = item_names[i].strip() if i < len(item_names) else ""
            if name:
                if not validate_product_name(name):
                    flash(f"'{name}' is not a valid product name (must contain letters)", "error")
                    return redirect("/add_purchase")
                name = clean_product_name(name)
                qty = 1
                if i < len(item_qtys) and item_qtys[i] and item_qtys[i].strip().isdigit():
                    qty = int(item_qtys[i])
                price = 0
                if i < len(item_prices) and item_prices[i] and item_prices[i].strip():
                    try:
                        price = float(item_prices[i])
                    except:
                        price = 0
                expiry_days = 30
                if i < len(item_expiries) and item_expiries[i] and item_expiries[i].strip().isdigit():
                    expiry_days = int(item_expiries[i])
                items.append({
                    "name": name,
                    "quantity": qty,
                    "price": price,
                    "expiry_days": expiry_days
                })
        
        if not items:
            flash("Please add at least one valid item!", "error")
            return redirect("/add_purchase")
        
        try:
            total_amount = float(total_amount) if total_amount else 0
        except:
            total_amount = 0
        
        items_json = json.dumps(items)
        db = get_db_connection()
        if not db:
            flash("Database connection error!", "error")
            return redirect("/add_purchase")
        
        cursor = db.cursor()
        try:
            cursor.execute("""
                INSERT INTO purchase_logs (user_id, store_name, purchase_date, total_amount, items)
                VALUES (%s, %s, %s, %s, %s)
            """, (session["user_id"], store_name, purchase_date, total_amount, items_json))
            db.commit()
            
            today = date.today()
            added_count = 0
            
            for item in items:
                product_name = item.get("name")
                quantity = int(item.get("quantity", 1))
                price = float(item.get("price", 0))
                expiry_days = int(item.get("expiry_days", 30))
                expiry_date = today + timedelta(days=expiry_days)
                category = detect_category(product_name)
                
                cursor.execute(
                    "SELECT id FROM products WHERE LOWER(product_name) = LOWER(%s)",
                    (product_name,)
                )
                existing = cursor.fetchone()
                if existing:
                    product_id = existing[0]
                else:
                    cursor.execute("""
                        INSERT INTO products (product_name, category, price, image, stock, expiry_days)
                        VALUES (%s, %s, %s, %s, %s, %s)
                    """, (product_name, category, price, "default.jpg", 100, expiry_days))
                    db.commit()
                    product_id = cursor.lastrowid
                
                cursor.execute("""
                    INSERT INTO pantry (user_id, product_id, quantity, purchase_date, expiry_date)
                    VALUES (%s, %s, %s, %s, %s)
                """, (session["user_id"], product_id, quantity, purchase_date, expiry_date))
                added_count += 1
            
            db.commit()
            flash(f"✅ Purchase logged successfully! {len(items)} items added to pantry.", "success")
            return redirect("/purchase")
            
        except Exception as e:
            db.rollback()
            flash(f"Error: {str(e)}", "error")
            return redirect("/add_purchase")
        finally:
            cursor.close()
            db.close()
    
    return render_template("add_purchase.html")

@app.route("/delete_purchase/<int:log_id>", methods=["POST"])
@login_required
def delete_purchase(log_id):
    db = get_db_connection()
    if not db:
        flash("Database connection error!", "error")
        return redirect("/purchase")
    
    cursor = db.cursor()
    try:
        cursor.execute("""
            DELETE FROM purchase_logs 
            WHERE id=%s AND user_id=%s
        """, (log_id, session["user_id"]))
        db.commit()
        flash("Purchase log deleted successfully!", "success")
    except Exception as e:
        db.rollback()
        flash(f"Error deleting purchase: {str(e)}", "error")
    finally:
        cursor.close()
        db.close()
    
    return redirect("/purchase")

# ============================================================
# SCAN RECEIPT - OCR (FIXED)
# ============================================================

@app.route("/scan_receipt", methods=["POST"])
@login_required
def scan_receipt():
    try:
        if "receipt" not in request.files:
            return jsonify({"success": False, "error": "No file uploaded"}), 400
        
        file = request.files["receipt"]
        if file.filename == "":
            return jsonify({"success": False, "error": "No file selected"}), 400
        
        allowed_extensions = {'png', 'jpg', 'jpeg', 'gif', 'bmp', 'tiff', 'pdf', 'txt'}
        file_ext = file.filename.lower().split('.')[-1] if '.' in file.filename else ''
        
        if file_ext not in allowed_extensions:
            return jsonify({"success": False, "error": "Unsupported file format. Please upload JPG, PNG, or PDF"}), 400
        
        filename = f"{session['user_id']}_{datetime.now().strftime('%Y%m%d%H%M%S')}_{file.filename}"
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        
        items = []
        store_name = "Unknown Store"
        total_amount = 0
        
        if file_ext == 'txt':
            with open(filepath, 'r', encoding='utf-8') as f:
                text = f.read()
            
            lines = text.strip().split('\n')
            seen_product_names = set()
            
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                if 'MART' in line.upper() or 'MARKET' in line.upper() or 'STORE' in line.upper():
                    if len(line) > 3 and len(line) < 100:
                        store_name = line
                if '₹' in line or 'Rs' in line:
                    match = re.search(r'(.+?)\s*[–\-]\s*[₹RsINR\s]+([\d.]+)', line)
                    if match:
                        name = match.group(1).strip()
                        price = float(match.group(2))
                        if validate_product_name(name):
                            name = clean_product_name(name)
                            if name.lower() not in seen_product_names:
                                seen_product_names.add(name.lower())
                                category = detect_category(name)
                                expiry_days = detect_expiry_days(name)
                                items.append({
                                    "name": name,
                                    "category": category,
                                    "quantity": 1,
                                    "price": price,
                                    "expiry_days": expiry_days
                                })
                                total_amount += price
        
        else:
            try:
                import cv2
                import numpy as np
                
                image = Image.open(filepath)
                if image.mode != 'RGB':
                    image = image.convert('RGB')
                
                max_size = 2000
                if max(image.size) > max_size:
                    ratio = max_size / max(image.size)
                    new_size = (int(image.size[0] * ratio), int(image.size[1] * ratio))
                    image = image.resize(new_size, Image.Resampling.LANCZOS)
                
                img_array = np.array(image)
                gray = cv2.cvtColor(img_array, cv2.COLOR_RGB2GRAY)
                _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
                denoised = cv2.medianBlur(thresh, 1)
                processed_image = Image.fromarray(denoised)
                
                text = ""
                try:
                    text = pytesseract.image_to_string(processed_image, config='--psm 6 --oem 3')
                except:
                    pass
                if not text.strip():
                    try:
                        text = pytesseract.image_to_string(image, config='--psm 6 --oem 3')
                    except:
                        pass
                if not text.strip():
                    enhancer = ImageEnhance.Contrast(image)
                    enhanced = enhancer.enhance(2.0)
                    text = pytesseract.image_to_string(enhanced, config='--psm 6 --oem 3')
                
                print("=== EXTRACTED TEXT ===")
                print(text)
                print("======================")
                
                if not text.strip():
                    return jsonify({"success": False, "error": "No text found in image. Please try a clearer photo."}), 400
                
                lines = text.strip().split('\n')
                food_keywords = [
                    'milk', 'bread', 'egg', 'butter', 'cheese', 'curd', 'yogurt',
                    'tomato', 'potato', 'onion', 'carrot', 'cabbage', 'spinach',
                    'apple', 'banana', 'orange', 'mango', 'grapes',
                    'rice', 'wheat', 'flour', 'sugar', 'salt', 'oil',
                    'pasta', 'noodle', 'cereal', 'oats', 'maggi',
                    'tea', 'coffee', 'juice', 'water', 'dal', 'pulses',
                    'paneer', 'ghee', 'cream', 'honey', 'jam'
                ]
                non_food_keywords = [
                    'toothpaste', 'soap', 'shampoo', 'detergent', 'dishwash',
                    'sanitizer', 'broom', 'mop', 'trash', 'battery', 'tissue',
                    'paper', 'pen', 'pencil'
                ]
                seen_product_names = set()
                
                for line in lines:
                    line = line.strip()
                    if not line:
                        continue
                    if 'MART' in line.upper() or 'MARKET' in line.upper() or 'STORE' in line.upper():
                        if len(line) > 3 and len(line) < 100:
                            store_name = line
                    
                    price_patterns = [
                        r'(.+?)\s*[–\-]\s*[₹RsINR\s]+([\d.]+)',
                        r'(.+?)\s+[₹RsINR]+([\d.]+)',
                        r'(.+?)\s+([\d]+\.[\d]{2})$',
                        r'(.+?)\s+([\d]+)$'
                    ]
                    
                    for pattern in price_patterns:
                        match = re.search(pattern, line)
                        if match:
                            name = match.group(1).strip()
                            price = float(match.group(2))
                            if not validate_product_name(name):
                                break
                            name = clean_product_name(name)
                            is_food = False
                            for keyword in food_keywords:
                                if keyword.lower() in name.lower():
                                    is_food = True
                                    break
                            is_non_food = False
                            for keyword in non_food_keywords:
                                if keyword.lower() in name.lower():
                                    is_non_food = True
                                    break
                            if not is_food or is_non_food:
                                break
                            if not (price > 0 and price < 10000):
                                break
                            name = re.sub(r'[^a-zA-Z0-9\s\(\)\.\-]', '', name)
                            if re.match(r'^[\d\s\.\,]+$', name):
                                break
                            if len(name) < 2:
                                break
                            if name.lower() in seen_product_names:
                                break
                            seen_product_names.add(name.lower())
                            category = detect_category(name)
                            expiry_days = detect_expiry_days(name)
                            items.append({
                                "name": name,
                                "category": category,
                                "quantity": 1,
                                "price": price,
                                "expiry_days": expiry_days
                            })
                            total_amount += price
                            break
                
                if not items:
                    for line in lines:
                        line = line.strip()
                        if not line:
                            continue
                        is_food = False
                        for keyword in food_keywords:
                            if keyword.lower() in line.lower():
                                is_food = True
                                break
                        if is_food and validate_product_name(line):
                            name = clean_product_name(line)
                            if re.match(r'^[\d\s\.\,]+$', name):
                                continue
                            if len(name) >= 2 and name.lower() not in seen_product_names:
                                seen_product_names.add(name.lower())
                                category = detect_category(name)
                                expiry_days = detect_expiry_days(name)
                                items.append({
                                    "name": name,
                                    "category": category,
                                    "quantity": 1,
                                    "price": 0,
                                    "expiry_days": expiry_days
                                })
                
            except Exception as e:
                return jsonify({"success": False, "error": f"Error processing image: {str(e)}"}), 400
        
        if not items:
            return jsonify({
                "success": False,
                "error": "No valid food items found in the receipt. Please upload a grocery bill."
            }), 400
        
        items_json = json.dumps(items)
        today = date.today()
        db = get_db_connection()
        if not db:
            return jsonify({"success": False, "error": "Database connection error"}), 500
        
        cursor = db.cursor()
        try:
            cursor.execute("""
                INSERT INTO purchase_logs (user_id, store_name, purchase_date, total_amount, items)
                VALUES (%s, %s, %s, %s, %s)
            """, (session["user_id"], store_name, today, total_amount, items_json))
            db.commit()
            cursor.close()
            db.close()
            
            return jsonify({
                "success": True,
                "store_name": store_name,
                "total_amount": total_amount,
                "items": items,
                "message": f"✅ {len(items)} items extracted successfully!"
            })
            
        except Exception as e:
            db.rollback()
            cursor.close()
            db.close()
            return jsonify({"success": False, "error": str(e)}), 500
        
    except Exception as e:
        print(f"Error in scan_receipt: {str(e)}")
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500

# ============================================================
# ADD SCANNED ITEMS TO PANTRY
# ============================================================

@app.route("/add_scanned_items", methods=["POST"])
@login_required
def add_scanned_items():
    try:
        items_json = request.form.get("items", "[]")
        items = json.loads(items_json)
        
        if not items:
            return jsonify({"success": False, "error": "No items selected"}), 400
        
        today = date.today()
        added_count = 0
        skipped_count = 0
        
        db = get_db_connection()
        if not db:
            return jsonify({"success": False, "error": "Database connection error"}), 500
        
        cursor = db.cursor()
        
        for item in items:
            product_name = item.get("name", "").strip()
            if not validate_product_name(product_name):
                skipped_count += 1
                continue
            product_name = clean_product_name(product_name)
            if not product_name:
                skipped_count += 1
                continue
            
            quantity = int(item.get("quantity", 1))
            price = float(item.get("price", 0))
            category = item.get("category", "Pantry")
            expiry_date_str = item.get("expiry_date")
            
            if expiry_date_str:
                expiry_date = datetime.strptime(expiry_date_str, "%Y-%m-%d").date()
            else:
                expiry_days = int(item.get("expiry_days", 30))
                expiry_date = today + timedelta(days=expiry_days)
            
            cursor.execute(
                "SELECT id FROM products WHERE LOWER(product_name) = LOWER(%s)",
                (product_name,)
            )
            existing = cursor.fetchone()
            if existing:
                product_id = existing[0]
            else:
                cursor.execute("""
                    INSERT INTO products (product_name, category, price, image, stock, expiry_days)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (product_name, category, price, "default.jpg", 100, (expiry_date - today).days))
                db.commit()
                product_id = cursor.lastrowid
            
            cursor.execute("""
                INSERT INTO pantry (user_id, product_id, quantity, purchase_date, expiry_date)
                VALUES (%s, %s, %s, %s, %s)
            """, (session["user_id"], product_id, quantity, today, expiry_date))
            added_count += 1
        
        db.commit()
        cursor.close()
        db.close()
        
        db2 = get_db_connection()
        cursor2 = db2.cursor()
        cursor2.execute("SELECT COUNT(*) FROM pantry WHERE user_id=%s", (session["user_id"],))
        pantry_count = cursor2.fetchone()[0]
        cursor2.close()
        db2.close()
        
        message = f"✅ {added_count} items added to your pantry!"
        if skipped_count > 0:
            message += f" ⚠️ {skipped_count} items skipped (invalid names)"
        
        return jsonify({
            "success": True,
            "message": message,
            "added_count": added_count,
            "skipped_count": skipped_count,
            "pantry_count": pantry_count
        })
        
    except Exception as e:
        print(f"Error in add_scanned_items: {str(e)}")
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500

# ============================================================
# ANALYTICS
# ============================================================

@app.route("/analytics")
@login_required
def analytics():
    try:
        user_id = session["user_id"]
        db = get_db_connection()
        if not db:
            flash("Database connection error!", "error")
            return render_template("analytics.html", 
                                  total_items=0, expired_items=0, expiring_items=0, 
                                  safe_items=0, total_purchases=0, total_spent=0, 
                                  money_saved=0, money_wasted=0, category_data=[],
                                  monthly_data=[], top_purchased=[], top_discarded=[])
        
        cursor = db.cursor()
        
        cursor.execute("SELECT COALESCE(COUNT(*), 0) FROM pantry WHERE user_id=%s", (user_id,))
        total_items = cursor.fetchone()[0]
        
        today = date.today()
        cursor.execute("SELECT COALESCE(COUNT(*), 0) FROM pantry WHERE user_id=%s AND expiry_date < %s", (user_id, today))
        expired_items = cursor.fetchone()[0]
        
        cursor.execute("SELECT COALESCE(COUNT(*), 0) FROM pantry WHERE user_id=%s AND expiry_date BETWEEN %s AND DATE_ADD(%s, INTERVAL 3 DAY)", (user_id, today, today))
        expiring_items = cursor.fetchone()[0]
        
        safe_items = total_items - expired_items - expiring_items
        
        cursor.execute("SELECT COALESCE(COUNT(*), 0) FROM purchase_logs WHERE user_id=%s", (user_id,))
        total_purchases = cursor.fetchone()[0]
        
        cursor.execute("SELECT COALESCE(SUM(total_amount), 0) FROM purchase_logs WHERE user_id=%s", (user_id,))
        total_spent = cursor.fetchone()[0]
        total_spent = to_float(total_spent)
        
        cursor.execute("SELECT COALESCE(money_saved, 0), COALESCE(money_wasted, 0) FROM users WHERE id=%s", (user_id,))
        user_money = cursor.fetchone()
        if user_money:
            money_saved = to_float(user_money[0]) if user_money[0] else 0
            money_wasted = to_float(user_money[1]) if user_money[1] else 0
        else:
            money_saved = 0
            money_wasted = 0
        
        cursor.execute("""
            SELECT COALESCE(products.category, 'Pantry') as category, COUNT(*) as count
            FROM pantry
            JOIN products ON pantry.product_id = products.id
            WHERE pantry.user_id=%s
            GROUP BY products.category
            ORDER BY count DESC
        """, (user_id,))
        category_data = cursor.fetchall()
        
        # Monthly data
        monthly_data = []
        months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
        
        for i in range(6):
            month_idx = (today.month - 6 + i) % 12
            if month_idx <= 0:
                month_idx += 12
            month_name = months[month_idx - 1]
            
            year = today.year
            if month_idx > today.month:
                year -= 1
            
            start_date = date(year, month_idx, 1)
            if month_idx == 12:
                end_date = date(year + 1, 1, 1) - timedelta(days=1)
            else:
                end_date = date(year, month_idx + 1, 1) - timedelta(days=1)
            
            cursor.execute("""
                SELECT COALESCE(SUM(total_amount), 0) 
                FROM purchase_logs 
                WHERE user_id=%s AND purchase_date BETWEEN %s AND %s
            """, (user_id, start_date, end_date))
            shopping = cursor.fetchone()[0]
            
            monthly_data.append({
                'month': month_name,
                'shopping': float(shopping),
                'waste': 0
            })
        
        # Top purchased
        cursor.execute("""
            SELECT products.product_name, COUNT(*) as count
            FROM pantry
            JOIN products ON pantry.product_id = products.id
            WHERE pantry.user_id=%s
            GROUP BY products.product_name
            ORDER BY count DESC
            LIMIT 5
        """, (user_id,))
        top_purchased = cursor.fetchall()
        
        # Top discarded
        cursor.execute("""
            SELECT products.product_name, COUNT(*) as count
            FROM pantry
            JOIN products ON pantry.product_id = products.id
            WHERE pantry.user_id=%s
            GROUP BY products.product_name
            ORDER BY count DESC
            LIMIT 5
        """, (user_id,))
        top_discarded = cursor.fetchall()
        
        cursor.close()
        db.close()
        
        return render_template("analytics.html", 
                              total_items=total_items,
                              expired_items=expired_items,
                              expiring_items=expiring_items,
                              safe_items=safe_items,
                              total_purchases=total_purchases,
                              total_spent=total_spent,
                              money_saved=money_saved,
                              money_wasted=money_wasted,
                              category_data=category_data,
                              monthly_data=monthly_data,
                              top_purchased=top_purchased,
                              top_discarded=top_discarded)
                              
    except Exception as e:
        print(f"Error in analytics: {str(e)}")
        traceback.print_exc()
        flash(f"Error loading analytics: {str(e)}", "error")
        return render_template("analytics.html", 
                              total_items=0, expired_items=0, expiring_items=0, 
                              safe_items=0, total_purchases=0, total_spent=0, 
                              money_saved=0, money_wasted=0, category_data=[],
                              monthly_data=[], top_purchased=[], top_discarded=[])

# ============================================================
# GET ANALYTICS DATA (AJAX)
# ============================================================

@app.route("/get_analytics_data")
@login_required
def get_analytics_data():
    try:
        user_id = session["user_id"]
        db = get_db_connection()
        if not db:
            return jsonify({"success": False, "error": "Database connection error"}), 500
        
        cursor = db.cursor()
        
        cursor.execute("SELECT COALESCE(COUNT(*), 0) FROM pantry WHERE user_id=%s", (user_id,))
        total_items = cursor.fetchone()[0]
        
        today = date.today()
        cursor.execute("SELECT COALESCE(COUNT(*), 0) FROM pantry WHERE user_id=%s AND expiry_date < %s", (user_id, today))
        expired_items = cursor.fetchone()[0]
        
        cursor.execute("SELECT COALESCE(COUNT(*), 0) FROM pantry WHERE user_id=%s AND expiry_date BETWEEN %s AND DATE_ADD(%s, INTERVAL 3 DAY)", (user_id, today, today))
        expiring_items = cursor.fetchone()[0]
        
        cursor.execute("SELECT COALESCE(COUNT(*), 0) FROM purchase_logs WHERE user_id=%s", (user_id,))
        total_purchases = cursor.fetchone()[0]
        
        cursor.execute("SELECT COALESCE(SUM(total_amount), 0) FROM purchase_logs WHERE user_id=%s", (user_id,))
        total_spent = cursor.fetchone()[0]
        total_spent = to_float(total_spent)
        
        cursor.execute("SELECT COALESCE(money_saved, 0), COALESCE(money_wasted, 0) FROM users WHERE id=%s", (user_id,))
        user_money = cursor.fetchone()
        if user_money:
            money_saved = to_float(user_money[0]) if user_money[0] else 0
            money_wasted = to_float(user_money[1]) if user_money[1] else 0
        else:
            money_saved = 0
            money_wasted = 0
        
        cursor.execute("""
            SELECT COALESCE(products.category, 'Pantry') as category, COUNT(*) as count
            FROM pantry
            JOIN products ON pantry.product_id = products.id
            WHERE pantry.user_id=%s
            GROUP BY products.category
            ORDER BY count DESC
        """, (user_id,))
        category_data = cursor.fetchall()
        
        # Monthly data
        monthly_data = []
        months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
        
        for i in range(6):
            month_idx = (today.month - 6 + i) % 12
            if month_idx <= 0:
                month_idx += 12
            month_name = months[month_idx - 1]
            
            year = today.year
            if month_idx > today.month:
                year -= 1
            
            start_date = date(year, month_idx, 1)
            if month_idx == 12:
                end_date = date(year + 1, 1, 1) - timedelta(days=1)
            else:
                end_date = date(year, month_idx + 1, 1) - timedelta(days=1)
            
            cursor.execute("""
                SELECT COALESCE(SUM(total_amount), 0) 
                FROM purchase_logs 
                WHERE user_id=%s AND purchase_date BETWEEN %s AND %s
            """, (user_id, start_date, end_date))
            shopping = cursor.fetchone()[0]
            
            monthly_data.append({
                'month': month_name,
                'shopping': float(shopping),
                'waste': 0
            })
        
        # Top purchased
        cursor.execute("""
            SELECT products.product_name, COUNT(*) as count
            FROM pantry
            JOIN products ON pantry.product_id = products.id
            WHERE pantry.user_id=%s
            GROUP BY products.product_name
            ORDER BY count DESC
            LIMIT 5
        """, (user_id,))
        top_purchased = cursor.fetchall()
        
        # Top discarded
        cursor.execute("""
            SELECT products.product_name, COUNT(*) as count
            FROM pantry
            JOIN products ON pantry.product_id = products.id
            WHERE pantry.user_id=%s
            GROUP BY products.product_name
            ORDER BY count DESC
            LIMIT 5
        """, (user_id,))
        top_discarded = cursor.fetchall()
        
        cursor.close()
        db.close()
        
        fresh = total_items - expired_items - expiring_items
        score = int((fresh / total_items) * 100) if total_items > 0 else 0
        
        return jsonify({
            "success": True,
            "total_items": total_items,
            "expired_items": expired_items,
            "expiring_items": expiring_items,
            "fresh": fresh,
            "total_purchases": total_purchases,
            "total_spent": total_spent,
            "money_saved": money_saved,
            "money_wasted": money_wasted,
            "sustainability_score": score,
            "category_data": category_data,
            "monthly_data": monthly_data,
            "top_purchased": top_purchased,
            "top_discarded": top_discarded
        })
        
    except Exception as e:
        print(f"Error in get_analytics_data: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500

# ============================================================
# EXPIRY ALERTS
# ============================================================

@app.route("/expiry")
@app.route("/expiry_alerts")
@login_required
def expiry_alerts():
    try:
        db = get_db_connection()
        if not db:
            flash("Database connection error!", "error")
            return render_template("expiry_alerts.html", alerts=[])
        
        cursor = db.cursor()
        today = date.today()
        
        cursor.execute("""
            SELECT products.product_name, pantry.expiry_date,
                   DATEDIFF(pantry.expiry_date, %s) as days_left
            FROM pantry
            JOIN products ON pantry.product_id = products.id
            WHERE pantry.user_id=%s
            ORDER BY pantry.expiry_date ASC
        """, (today, session["user_id"]))
        
        items = cursor.fetchall()
        cursor.close()
        db.close()
        
        alerts = []
        for item in items:
            product_name = item[0]
            expiry_date = item[1]
            days = int(item[2]) if item[2] else 0
            
            if days < 0:
                alerts.append(f"❌ {product_name} - Expired {abs(days)} days ago on {expiry_date.strftime('%d %b %Y')}")
            elif days == 0:
                alerts.append(f"🔴 {product_name} - Expires Today! ({expiry_date.strftime('%d %b %Y')})")
            elif days <= 3:
                alerts.append(f"🟡 {product_name} - Expires in {days} days ({expiry_date.strftime('%d %b %Y')})")
            elif days <= 7:
                alerts.append(f"🟢 {product_name} - Expires in {days} days ({expiry_date.strftime('%d %b %Y')})")
        
        return render_template("expiry_alerts.html", alerts=alerts)
        
    except Exception as e:
        print(f"Error in expiry_alerts: {str(e)}")
        traceback.print_exc()
        flash(f"Error loading expiry alerts: {str(e)}", "error")
        return render_template("expiry_alerts.html", alerts=[])

# ============================================================
# RECIPES
# ============================================================
@app.route("/recipes")
@login_required
def recipes():
    import re
    db = get_db_connection()
    if not db:
        flash("Database connection error!", "error")
        return render_template("recipes.html", suggestions=[], pantry_items=[])
    
    cursor = db.cursor(dictionary=True)
    
    # Get pantry items
    cursor.execute("""
        SELECT DISTINCT LOWER(products.product_name) as product_name
        FROM pantry
        JOIN products ON pantry.product_id = products.id
        WHERE pantry.user_id=%s
    """, (session["user_id"],))
    
    raw_items = cursor.fetchall()
    pantry_items = []
    
    for item in raw_items:
        name = item['product_name']
        name = re.sub(r'\b\d+[kg]?\b', '', name, flags=re.IGNORECASE)
        name = re.sub(r'\b\d+[ml]?\b', '', name, flags=re.IGNORECASE)
        name = re.sub(r'\b\d+[lL]\b', '', name)
        name = re.sub(r'\b\d+\s*[gG]\b', '', name)
        name = re.sub(r'\d+\.\d{2}', '', name)
        name = re.sub(r'\b\d+\b', '', name)
        name = re.sub(r'[^a-zA-Z\s]', '', name)
        name = ' '.join(name.split()).strip()
        
        if name and len(name) > 1:
            pantry_items.append(name.lower())
    
    pantry_items = list(set(pantry_items))
    pantry_items.sort()
    
    # Get all recipes - DISTINCT to remove duplicates
    cursor.execute("SELECT DISTINCT * FROM recipes ORDER BY id")
    all_recipes = cursor.fetchall()
    
    # Keep track of seen recipe names to remove duplicates
    seen_names = set()
    unique_recipes = []
    
    for recipe in all_recipes:
        recipe_name = recipe['recipe_name'].lower().strip()
        if recipe_name not in seen_names:
            seen_names.add(recipe_name)
            unique_recipes.append(recipe)
    
    suggestions = []
    for recipe in unique_recipes:
        ingredients_list = [ing.strip().lower() for ing in recipe['ingredients'].split(',')]
        available = 0
        for ing in ingredients_list:
            matched = False
            for pantry_item in pantry_items:
                if ing in pantry_item or pantry_item in ing:
                    matched = True
                    break
            if matched:
                available += 1
        
        total = len(ingredients_list)
        match_percent = int((available / total) * 100) if total > 0 else 0
        
        recipe['match_percent'] = match_percent
        recipe['available'] = available
        recipe['total'] = total
        suggestions.append(recipe)
    
    suggestions.sort(key=lambda x: x['match_percent'], reverse=True)
    
    cursor.close()
    db.close()
    
    return render_template("recipes.html", 
                          suggestions=suggestions,
                          pantry_items=pantry_items)

# ============================================================
# PURCHASE LOGS - SEPARATE PAGE
# ============================================================

@app.route("/purchase_logs")
@login_required
def purchase_logs():
    try:
        db = get_db_connection()
        if not db:
            flash("Database connection error!", "error")
            return render_template("purchase_logs.html", logs=[])
        
        cursor = db.cursor()
        cursor.execute("""
            SELECT id, store_name, purchase_date, total_amount, items
            FROM purchase_logs
            WHERE user_id=%s
            ORDER BY id DESC
        """, (session["user_id"],))
        
        logs = cursor.fetchall()
        cursor.close()
        db.close()
        
        # Parse items JSON
        parsed_logs = []
        for log in logs:
            log_list = list(log)
            try:
                items = json.loads(log[4]) if log[4] else []
                log_list[4] = items
            except:
                log_list[4] = []
            parsed_logs.append(tuple(log_list))
        
        return render_template("purchase_logs.html", logs=parsed_logs)
        
    except Exception as e:
        print(f"Error in purchase_logs: {str(e)}")
        flash(f"Error loading purchase logs: {str(e)}", "error")
        return render_template("purchase_logs.html", logs=[])

# ============================================================
# OTHER ROUTES
# ============================================================

@app.route("/feedback", methods=["GET", "POST"])
@login_required
def feedback():
    if request.method == "POST":
        subject = request.form.get("subject")
        message = request.form.get("message")
        rating = request.form.get("rating")
        db = get_db_connection()
        if not db:
            flash("Database connection error!", "error")
            return redirect("/feedback")
        cursor = db.cursor()
        try:
            cursor.execute("""
                INSERT INTO feedback (user_id, subject, message, rating, created_at)
                VALUES (%s, %s, %s, %s, NOW())
            """, (session["user_id"], subject, message, rating))
            db.commit()
            flash("Thank you for your feedback!", "success")
        except Exception as e:
            db.rollback()
            flash(f"Error submitting feedback: {str(e)}", "error")
        finally:
            cursor.close()
            db.close()
        return redirect("/feedback")
    
    db = get_db_connection()
    if not db:
        flash("Database connection error!", "error")
        return render_template("feedback.html", feedbacks=[])
    
    cursor = db.cursor()
    cursor.execute("""
        SELECT subject, message, rating, created_at
        FROM feedback
        WHERE user_id=%s
        ORDER BY created_at DESC
    """, (session["user_id"],))
    feedbacks = cursor.fetchall()
    cursor.close()
    db.close()
    
    return render_template("feedback.html", feedbacks=feedbacks)

@app.route("/profile")
@login_required
def profile():
    db = get_db_connection()
    if not db:
        flash("Database connection error!", "error")
        return redirect("/welcome")
    
    cursor = db.cursor()
    cursor.execute("SELECT fullname, email, phone FROM users WHERE id=%s", (session["user_id"],))
    user = cursor.fetchone()
    
    cursor.execute("SELECT COUNT(*) FROM pantry WHERE user_id=%s", (session["user_id"],))
    pantry_count = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM purchase_logs WHERE user_id=%s", (session["user_id"],))
    total_purchases = cursor.fetchone()[0]
    
    cursor.execute("SELECT SUM(total_amount) FROM purchase_logs WHERE user_id=%s", (session["user_id"],))
    total_spent = cursor.fetchone()[0]
    total_spent = to_float(total_spent)
    
    cursor.close()
    db.close()
    
    return render_template("profile.html", 
                          user=user,
                          pantry_count=pantry_count,
                          total_purchases=total_purchases,
                          total_spent=total_spent)

@app.route("/update_profile", methods=["POST"])
@login_required
def update_profile():
    fullname = request.form.get("fullname")
    email = request.form.get("email")
    
    db = get_db_connection()
    if not db:
        flash("Database connection error!", "error")
        return redirect("/profile")
    
    cursor = db.cursor()
    try:
        cursor.execute("""
            UPDATE users SET fullname=%s, email=%s
            WHERE id=%s
        """, (fullname, email, session["user_id"]))
        db.commit()
        session["fullname"] = fullname
        session["email"] = email
        flash("Profile updated successfully!", "success")
    except Exception as e:
        db.rollback()
        flash(f"Error updating profile: {str(e)}", "error")
    finally:
        cursor.close()
        db.close()
    
    return redirect("/profile")

@app.route("/update_password", methods=["POST"])
@login_required
def update_password():
    current_password = request.form.get("current_password")
    new_password = request.form.get("new_password")
    
    if not current_password or not new_password:
        flash("Please fill all password fields!", "error")
        return redirect("/profile")
    
    if len(new_password) < 6:
        flash("New password must be at least 6 characters long!", "error")
        return redirect("/profile")
    
    db = get_db_connection()
    if not db:
        flash("Database connection error!", "error")
        return redirect("/profile")
    
    cursor = db.cursor()
    cursor.execute("SELECT password FROM users WHERE id=%s", (session["user_id"],))
    user = cursor.fetchone()
    
    if user and user[0] == current_password:
        cursor.execute("""
            UPDATE users SET password=%s WHERE id=%s
        """, (new_password, session["user_id"]))
        db.commit()
        flash("Password updated successfully!", "success")
    else:
        flash("Current password is incorrect!", "error")
    
    cursor.close()
    db.close()
    return redirect("/profile")

@app.route("/contact")
def contact():
    return render_template("contact.html")

# ============================================================
# WEBSOCKET EVENTS
# ============================================================

@socketio.on('connect')
def handle_connect():
    print('Client connected')

@socketio.on('disconnect')
def handle_disconnect():
    print('Client disconnected')

@socketio.on('check_stock')
def handle_stock(data):
    product_id = data.get('product_id')
    db = get_db_connection()
    if db:
        cursor = db.cursor()
        cursor.execute("SELECT stock FROM products WHERE id=%s", (product_id,))
        stock = cursor.fetchone()
        cursor.close()
        db.close()
        emit('stock_update', {'stock': stock[0] if stock else 0})
    else:
        emit('stock_update', {'stock': 0})

# ============================================================
# RUN APP
# ============================================================

if __name__ == "__main__":
    debug_mode = os.environ.get("FLASK_DEBUG", "true").lower() == "true"
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, debug=debug_mode, host="0.0.0.0", port=port)