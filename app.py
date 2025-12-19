# app_production_cart.py
# ============================================================
# REAL-WORLD SAFE INVENTORY & GST BILLING SYSTEM
# WITH PERSISTENT CART (STREAMLIT-SAFE)
# ============================================================

import streamlit as st
import sqlite3
import pandas as pd
from datetime import datetime
import hashlib

# ---------------- CONFIG ----------------
DB_PATH = "inventory.db"
DEFAULT_ADMIN_PASSWORD = "admin123"
SALT = "prod_inventory_salt_v1"

# ---------------- PASSWORD ----------------

def hash_password(pw: str) -> str:
    return hashlib.sha256((SALT + (pw or "")).encode()).hexdigest()


def check_password(pw: str, h: str) -> bool:
    return hash_password(pw) == h

# ---------------- DB ----------------

@st.cache_resource
def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_conn()
    c = conn.cursor()

    c.execute("""
    CREATE TABLE IF NOT EXISTS products(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL,
        selling_price REAL DEFAULT 0,
        gst_rate REAL DEFAULT 0
    )""")

    c.execute("""
    CREATE TABLE IF NOT EXISTS purchases(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id INTEGER NOT NULL,
        qty REAL NOT NULL,
        created_at TEXT,
        FOREIGN KEY(product_id) REFERENCES products(id)
    )""")

    c.execute("""
    CREATE TABLE IF NOT EXISTS invoice_sequence(
        ym TEXT PRIMARY KEY,
        last_no INTEGER
    )""")

    c.execute("""
    CREATE TABLE IF NOT EXISTS invoices(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        invoice_no TEXT UNIQUE,
        date TEXT,
        subtotal REAL,
        tax REAL,
        total REAL,
        supply_type TEXT
    )""")

    c.execute("""
    CREATE TABLE IF NOT EXISTS invoice_items(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        invoice_id INTEGER,
        product_id INTEGER,
        qty REAL,
        price REAL,
        gst REAL,
        tax REAL,
        cgst REAL,
        sgst REAL,
        igst REAL,
        line_total REAL
    )""")

    c.execute("""
    CREATE TABLE IF NOT EXISTS settings(
        k TEXT PRIMARY KEY,
        v TEXT
    )""")

    if not conn.execute("SELECT 1 FROM settings WHERE k='admin'").fetchone():
        c.execute("INSERT INTO settings VALUES(?,?)", ('admin', hash_password(DEFAULT_ADMIN_PASSWORD)))

    conn.commit()

# ---------------- HELPERS ----------------

@st.cache_data(ttl=60)
def load_products():
    return pd.read_sql_query("SELECT * FROM products ORDER BY name", get_conn())


def reset_cache():
    load_products.clear()


def stock(pid: int) -> float:
    conn = get_conn()
    p = conn.execute("SELECT COALESCE(SUM(qty),0) FROM purchases WHERE product_id=?", (pid,)).fetchone()[0]
    s = conn.execute("SELECT COALESCE(SUM(qty),0) FROM invoice_items WHERE product_id=?", (pid,)).fetchone()[0]
    return float(p or 0) - float(s or 0)


def next_invoice_no():
    conn = get_conn()
    ym = datetime.now().strftime('%Y%m')
    row = conn.execute("SELECT last_no FROM invoice_sequence WHERE ym=?", (ym,)).fetchone()
    if row:
        no = row[0] + 1
        conn.execute("UPDATE invoice_sequence SET last_no=? WHERE ym=?", (no, ym))
    else:
        no = 1
        conn.execute("INSERT INTO invoice_sequence VALUES(?,?)", (ym, no))
    conn.commit()
    return f"INV-{ym}-{no:04d}"

# ---------------- INVOICE ----------------

def create_invoice(cart, supply_type):
    conn = get_conn()
    cur = conn.cursor()

    subtotal = tax = 0.0
    rows = []

    for it in cart:
        base = it['qty'] * it['price']
        t = base * it['gst'] / 100
        subtotal += base
        tax += t
        if supply_type == 'INTRA':
            cgst = sgst = t / 2
            igst = 0
        else:
            cgst = sgst = 0
            igst = t
        rows.append((it['pid'], it['qty'], it['price'], it['gst'], t, cgst, sgst, igst, base + t))

    total = round(subtotal + tax, 2)
    inv_no = next_invoice_no()

    cur.execute(
        "INSERT INTO invoices(invoice_no,date,subtotal,tax,total,supply_type) VALUES(?,?,?,?,?,?)",
        (inv_no, datetime.now().isoformat(), subtotal, tax, total, supply_type)
    )
    inv_id = cur.lastrowid

    for r in rows:
        cur.execute(
            "INSERT INTO invoice_items(invoice_id,product_id,qty,price,gst,tax,cgst,sgst,igst,line_total) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (inv_id, *r)
        )

    conn.commit()
    reset_cache()
    return inv_no

# ---------------- AUTH ----------------

def login():
    if st.session_state.get('logged_in'):
        return True

    st.markdown("""
        <style>
        .login-box {
            max-width: 380px;
            margin: 120px auto;
            padding: 30px;
            border-radius: 12px;
            background-color: rgba(255,255,255,0.03);
            box-shadow: 0 0 20px rgba(0,0,0,0.4);
        }
        </style>
    """, unsafe_allow_html=True)

    st.markdown('<div class="login-box">', unsafe_allow_html=True)
    st.title("Login")

    pw = st.text_input("Password", type="password")
    if st.button("Login", use_container_width=True):
        h = get_conn().execute("SELECT v FROM settings WHERE k='admin'").fetchone()[0]
        if check_password(pw, h):
            st.session_state.logged_in = True
            st.session_state.cart = []
            st.rerun()
        else:
            st.error("Incorrect password")

    st.markdown('</div>', unsafe_allow_html=True)
    return False

# ---------------- UI ----------------

def main():
    st.set_page_config("Inventory GST (Persistent Cart)", "📦", layout="wide")
    init_db()

    if not login():
        st.stop()

    if 'cart' not in st.session_state:
        st.session_state.cart = []

    menu = st.sidebar.radio("Menu", ["Dashboard", "Products", "Purchase", "Sales", "Reports"])

    if menu == "Dashboard":
        st.title("Dashboard")
        df = load_products()
        if not df.empty:
            df['Stock'] = df['id'].apply(stock)
            st.dataframe(df, use_container_width=True)
        else:
            st.info("No products yet")

    elif menu == "Products":
        st.header("Products")
        name = st.text_input("Product name")
        price = st.number_input("Selling price", min_value=0.0)
        gst = st.number_input("GST %", min_value=0.0)
        if st.button("Add product"):
            if name.strip():
                get_conn().execute("INSERT INTO products(name,selling_price,gst_rate) VALUES(?,?,?)",
                                   (name.strip(), price, gst))
                get_conn().commit()
                reset_cache()
                st.success("Product added")
        st.dataframe(load_products(), use_container_width=True)

    elif menu == "Purchase":
        st.header("Purchase")
        df = load_products()
        if df.empty:
            st.info("Add products first")
        else:
            pid = st.selectbox("Product", df['id'], format_func=lambda i: df.set_index('id').loc[i,'name'])
            qty = st.number_input("Quantity", min_value=1.0)
            if st.button("Add stock"):
                get_conn().execute("INSERT INTO purchases(product_id,qty,created_at) VALUES(?,?,?)",
                                   (pid, qty, datetime.now().isoformat()))
                get_conn().commit()
                st.success("Stock updated")

    elif menu == "Sales":
        st.header("Sales / Invoice")
        df = load_products()
        if df.empty:
            st.info("Add products first")
        else:
            pid = st.selectbox("Product", df['id'], format_func=lambda i: df.set_index('id').loc[i,'name'])
            qty = st.number_input("Qty", min_value=1.0)
            if st.button("Add to cart"):
                r = df.set_index('id').loc[pid]
                st.session_state.cart.append({
                    'pid': pid,
                    'name': r['name'],
                    'qty': qty,
                    'price': r['selling_price'],
                    'gst': r['gst_rate']
                })
                st.success("Added to cart")

            if st.session_state.cart:
                cart_df = pd.DataFrame(st.session_state.cart)
                st.subheader("Cart")
                st.dataframe(cart_df, use_container_width=True)

                supply = st.selectbox("Supply type", ['INTRA','INTER'])
                if st.button("Create invoice"):
                    inv = create_invoice(st.session_state.cart, supply)
                    st.success(f"Invoice {inv} created")
                    st.session_state.cart = []

    elif menu == "Reports":
        st.header("GST Summary")
        df = pd.read_sql_query("SELECT gst, SUM(tax) AS tax FROM invoice_items GROUP BY gst", get_conn())
        st.dataframe(df, use_container_width=True)


if __name__ == '__main__':
    main()
