
import streamlit as st
import sqlite3
import pandas as pd
from datetime import datetime, date
from io import BytesIO

# Password hashing (pure Python)
import hashlib

# Optional PDF libs
try:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.lib.utils import ImageReader
    REPORTLAB_OK = True
except Exception:
    REPORTLAB_OK = False

DB_PATH = "inventory.db"

# ----------------------------
# DB Helpers & Auth
# ----------------------------
def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

def column_exists(conn, table, column):
    cur = conn.execute(f"PRAGMA table_info({table})")
    return any(r[1] == column for r in cur.fetchall())

def init_db():
    """Create base tables and apply idempotent migrations safely."""
    conn = get_conn()
    cur = conn.cursor()

    # Users (for login)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );""")

    # Create default admin user if not exists
    cur = conn.execute("SELECT COUNT(*) FROM users")
    if (cur.fetchone()[0] or 0) == 0:
        import hashlib
        default_password = hashlib.sha256("admin123".encode()).hexdigest()
        conn.execute(
            "INSERT INTO users(username, password_hash) VALUES(?,?)",
            ("admin", default_password)
        )

    conn.commit()
    conn.close()


    # App settings (invoice/company)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS settings(
        id INTEGER PRIMARY KEY CHECK (id = 1),
        company_name TEXT,
        company_address TEXT,
        company_phone TEXT,
        company_email TEXT,
        company_gstin TEXT,
        invoice_footer TEXT,
        logo BLOB,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    );""")
    cur.execute("INSERT OR IGNORE INTO settings(id, company_name, company_address) VALUES(1, 'Your Company', 'Street, City, State, Pincode')")

    # Products
    cur.execute("""
    CREATE TABLE IF NOT EXISTS products(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        category TEXT,
        unit TEXT DEFAULT 'pcs',
        selling_price REAL DEFAULT 0.0,
        tax_rate REAL DEFAULT 0.0,
        barcode TEXT,
        low_stock_threshold REAL DEFAULT 0,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );""")
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_products_barcode ON products(barcode) WHERE barcode IS NOT NULL")

    # Purchases
    cur.execute("""
    CREATE TABLE IF NOT EXISTS purchases(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
        qty REAL NOT NULL,
        cost_price REAL DEFAULT 0.0,
        bill_no TEXT,
        supplier TEXT,
        purchased_at TEXT DEFAULT CURRENT_TIMESTAMP,
        notes TEXT
    );""")

    # Customers (rich details)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS customers(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        phone TEXT,
        email TEXT,
        gstin TEXT,
        pan TEXT,
        address TEXT,
        city TEXT,
        state TEXT,
        pincode TEXT,
        credit_limit REAL DEFAULT 0,
        opening_balance REAL DEFAULT 0,
        notes TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );""")

    # Ledger (A/R)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS ledger(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
        entry_type TEXT NOT NULL, -- 'sale', 'payment', 'adjustment'
        ref_id INTEGER,           -- sale_master id if entry_type='sale'
        amount REAL NOT NULL,     -- debit positive (sale), credit negative (payment)
        entry_date TEXT DEFAULT CURRENT_TIMESTAMP,
        note TEXT
    );""")

    # Multi-item sales
    cur.execute("""
    CREATE TABLE IF NOT EXISTS sale_master(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        invoice_no TEXT UNIQUE,
        customer_id INTEGER REFERENCES customers(id),
        sold_at TEXT DEFAULT CURRENT_TIMESTAMP,
        subtotal REAL DEFAULT 0,
        tax_amount REAL DEFAULT 0,
        total_amount REAL DEFAULT 0,
        notes TEXT
    );""")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS sale_items(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sale_id INTEGER NOT NULL REFERENCES sale_master(id) ON DELETE CASCADE,
        product_id INTEGER NOT NULL REFERENCES products(id),
        description TEXT,
        qty REAL NOT NULL,
        unit_price REAL NOT NULL,
        gst_rate REAL DEFAULT 0,
        line_tax REAL DEFAULT 0,
        line_total REAL DEFAULT 0
    );""")

    # Default admin user
    cur = conn.execute("SELECT COUNT(*) FROM users")
    if (cur.fetchone()[0] or 0) == 0:
        conn.execute("INSERT INTO users(username, password_hash) VALUES(?,?)", ("admin", bcrypt.hash("admin123")))

    conn.commit()
    conn.close()

def verify_login(username, password):
    import hashlib
    with get_conn() as conn:
        row = conn.execute("SELECT id, password_hash FROM users WHERE username=?", (username,)).fetchone()
    if not row:
        return None
    uid, stored_hash = row
    hashed = hashlib.sha256(password.encode()).hexdigest()
    return int(uid) if hashed == stored_hash else None

def change_password(user_id, new_password):
    hashed = hashlib.sha256(new_password.encode()).hexdigest()
    with get_conn() as conn:
        conn.execute("UPDATE users SET password_hash=? WHERE id=?", (hashed, user_id))
        conn.commit()

# ----------------------------
# Loaders/Utils
# ----------------------------
@st.cache_data(ttl=60)
def load_products():
    with get_conn() as conn:
        return pd.read_sql_query("SELECT * FROM products ORDER BY name", conn)

@st.cache_data(ttl=60)
def load_customers():
    with get_conn() as conn:
        return pd.read_sql_query("SELECT * FROM customers ORDER BY name", conn)

def load_settings():
    with get_conn() as conn:
        row = conn.execute("""SELECT company_name, company_address, company_phone, company_email, company_gstin, invoice_footer, logo
                              FROM settings WHERE id=1""").fetchone()
    keys = ["company_name","company_address","company_phone","company_email","company_gstin","invoice_footer","logo"]
    if row:
        return dict(zip(keys, row))
    return {k: (None if k=="logo" else "") for k in keys}

def save_settings(data):
    with get_conn() as conn:
        conn.execute("""UPDATE settings SET company_name=?, company_address=?, company_phone=?, company_email=?, company_gstin=?, invoice_footer=?, logo=?, updated_at=CURRENT_TIMESTAMP
                        WHERE id=1""",
                     (data.get("company_name"), data.get("company_address"), data.get("company_phone"),
                      data.get("company_email"), data.get("company_gstin"), data.get("invoice_footer"),
                      data.get("logo")))
        conn.commit()

def get_or_create_customer(name, **fields):
    name = (name or "").strip()
    if not name: 
        return None
    with get_conn() as conn:
        cur = conn.execute("SELECT id FROM customers WHERE name=?", (name,)).fetchone()
        if cur:
            return int(cur[0])
        cols = ["name","phone","email","gstin","pan","address","city","state","pincode","credit_limit","opening_balance","notes"]
        vals = [name] + [fields.get(c) for c in cols[1:]]
        conn.execute(f"INSERT INTO customers({','.join(cols)}) VALUES({','.join(['?']*len(cols))})", vals)
        conn.commit()
        cur = conn.execute("SELECT id FROM customers WHERE name=?", (name,)).fetchone()
        return int(cur[0])

def get_customer_balance(customer_id, as_of=None):
    with get_conn() as conn:
        ob_row = conn.execute("SELECT COALESCE(opening_balance,0) FROM customers WHERE id=?", (customer_id,)).fetchone()
        ob = float(ob_row[0]) if ob_row else 0.0
        if as_of:
            lg = conn.execute("SELECT COALESCE(SUM(amount),0) FROM ledger WHERE customer_id=? AND DATE(entry_date) <= DATE(?)",
                              (customer_id, as_of)).fetchone()[0]
        else:
            lg = conn.execute("SELECT COALESCE(SUM(amount),0) FROM ledger WHERE customer_id=?",
                              (customer_id,)).fetchone()[0]
        return round(ob + float(lg or 0), 2)

def get_stock(product_id):
    with get_conn() as conn:
        p = conn.execute("SELECT COALESCE(SUM(qty),0) FROM purchases WHERE product_id=?", (product_id,)).fetchone()[0]
        s = conn.execute("SELECT COALESCE(SUM(qty),0) FROM sale_items WHERE product_id=?", (product_id,)).fetchone()[0]
    return (p or 0) - (s or 0)

def stock_df():
    prods = load_products()
    if prods.empty:
        return pd.DataFrame(columns=["Product","Category","Unit","In Stock","Selling Price","Barcode","Low Stock Threshold","GST %","Low?"])
    prods["In Stock"] = prods["id"].apply(get_stock)
    prods["Low?"] = prods.apply(lambda r: "YES" if (float(r.get("low_stock_threshold") or 0) > 0 and r["In Stock"] <= float(r.get("low_stock_threshold") or 0)) else "", axis=1)
    prods["GST %"] = prods["tax_rate"].fillna(0)
    return prods.rename(columns={
        "name":"Product","category":"Category","unit":"Unit","selling_price":"Selling Price",
        "barcode":"Barcode","low_stock_threshold":"Low Stock Threshold"
    })[["Product","Category","Unit","In Stock","Selling Price","Barcode","Low Stock Threshold","GST %","Low?"]]

def save_product(name, category, unit, price, barcode, low_thr, tax_rate):
    with get_conn() as conn:
        conn.execute("""INSERT OR IGNORE INTO products(name, category, unit, selling_price, barcode, low_stock_threshold, tax_rate)
                        VALUES(?,?,?,?,?,?,?)""",
                     (name.strip(), category.strip() if category else None, unit.strip() if unit else "pcs",
                      float(price or 0), barcode.strip() if barcode else None, float(low_thr or 0), float(tax_rate or 0)))
        conn.commit()

def update_product(product_id, name, category, unit, price, barcode, low_thr, tax_rate):
    with get_conn() as conn:
        conn.execute("""UPDATE products SET name=?, category=?, unit=?, selling_price=?, barcode=?, low_stock_threshold=?, tax_rate=?
                        WHERE id=?""",
                     (name, category, unit, float(price or 0), barcode if barcode else None, float(low_thr or 0), float(tax_rate or 0), product_id))
        conn.commit()

def delete_product(product_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM products WHERE id=?", (product_id,))
        conn.commit()

def add_purchase(product_id, qty, cp, bill_no, supplier, when, notes):
    with get_conn() as conn:
        conn.execute("""INSERT INTO purchases(product_id, qty, cost_price, bill_no, supplier, purchased_at, notes)
                        VALUES(?,?,?,?,?,?,?)""",
                     (product_id, float(qty), float(cp or 0), bill_no, supplier, when, notes))
        conn.commit()

def next_invoice_no():
    with get_conn() as conn:
        cur = conn.execute("SELECT COUNT(*) FROM sale_master").fetchone()[0]
    now = datetime.now().strftime("%Y%m")
    return f"INV-{now}-{int(cur)+1:04d}"

def add_sale_multi(customer_id, sold_at, items, invoice_no, notes, payment_received):
    with get_conn() as conn:
        # compute and validate
        subtotal = 0.0; tax_total = 0.0; total_amount = 0.0
        for it in items:
            qty = float(it["qty"]); price = float(it["unit_price"]); gst = float(it["gst_rate"])
            if qty <= 0: raise ValueError("Quantity must be > 0")
            current = get_stock(int(it["product_id"]))
            if qty > current:
                raise ValueError(f"Not enough stock for product_id {it['product_id']}. Available: {current:.2f}")
            line_sub = qty * price
            line_tax = round(line_sub * gst / 100.0, 2)
            line_total = round(line_sub + line_tax, 2)
            subtotal += line_sub; tax_total += line_tax; total_amount += line_total

        # header
        conn.execute("""INSERT INTO sale_master(invoice_no, customer_id, sold_at, subtotal, tax_amount, total_amount, notes)
                        VALUES(?,?,?,?,?,?,?)""",
                     (invoice_no, customer_id, sold_at, round(subtotal,2), round(tax_total,2), round(total_amount,2), notes))
        sale_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        # lines
        for it in items:
            qty = float(it["qty"]); price = float(it["unit_price"]); gst = float(it["gst_rate"])
            line_sub = qty * price
            line_tax = round(line_sub * gst / 100.0, 2)
            line_total = round(line_sub + line_tax, 2)
            desc = it.get("description") or ""
            conn.execute("""INSERT INTO sale_items(sale_id, product_id, description, qty, unit_price, gst_rate, line_tax, line_total)
                            VALUES(?,?,?,?,?,?,?,?)""",
                         (sale_id, int(it["product_id"]), desc, qty, price, gst, line_tax, line_total))

        # ledger
        conn.execute("""INSERT INTO ledger(customer_id, entry_type, ref_id, amount, entry_date, note)
                        VALUES(?,?,?,?,?,?)""",
                     (customer_id, "sale", sale_id, round(total_amount,2), sold_at, f"Invoice {invoice_no}"))
        if payment_received and float(payment_received) > 0:
            amt = -abs(float(payment_received))
            conn.execute("""INSERT INTO ledger(customer_id, entry_type, ref_id, amount, entry_date, note)
                            VALUES(?,?,?,?,?,?)""",
                         (customer_id, "payment", sale_id, amt, sold_at, f"Against {invoice_no}"))
        conn.commit()
        return int(sale_id), round(subtotal,2), round(tax_total,2), round(total_amount,2)

def list_purchases(date_from=None, date_to=None):
    query = """SELECT p.id, pr.name as product, p.qty, p.cost_price, p.bill_no, p.supplier, p.purchased_at, p.notes
               FROM purchases p JOIN products pr ON pr.id=p.product_id"""
    filters, params = [], []
    if date_from:
        filters.append("DATE(p.purchased_at) >= DATE(?)"); params.append(date_from)
    if date_to:
        filters.append("DATE(p.purchased_at) <= DATE(?)"); params.append(date_to)
    if filters: query += " WHERE " + " AND ".join(filters)
    query += " ORDER BY p.purchased_at DESC"
    with get_conn() as conn:
        return pd.read_sql_query(query, conn, params=params)

def list_sales_master(date_from=None, date_to=None):
    query = """SELECT sm.id, sm.invoice_no, c.name as customer, sm.sold_at, sm.subtotal, sm.tax_amount, sm.total_amount, sm.notes
               FROM sale_master sm LEFT JOIN customers c ON c.id=sm.customer_id"""
    filters, params = [], []
    if date_from:
        filters.append("DATE(sm.sold_at) >= DATE(?)"); params.append(date_from)
    if date_to:
        filters.append("DATE(sm.sold_at) <= DATE(?)"); params.append(date_to)
    if filters: query += " WHERE " + " AND ".join(filters)
    query += " ORDER BY sm.sold_at DESC"
    with get_conn() as conn:
        return pd.read_sql_query(query, conn, params=params)

def list_sales_items(sale_id=None):
    base = """SELECT si.id, si.sale_id, pr.name as product, si.description, si.qty, si.unit_price, si.gst_rate, si.line_tax, si.line_total
              FROM sale_items si JOIN products pr ON pr.id=si.product_id"""
    params = []
    if sale_id:
        base += " WHERE si.sale_id=?"
        params.append(sale_id)
    base += " ORDER BY si.sale_id, si.id"
    with get_conn() as conn:
        return pd.read_sql_query(base, conn, params=params)

def simple_kpis():
    sd = stock_df()
    total_items = len(sd)
    total_qty = sd["In Stock"].sum() if not sd.empty else 0
    inventory_value = (sd["In Stock"] * sd["Selling Price"].fillna(0)).sum() if not sd.empty else 0
    low_items = (sd["Low?"]=="YES").sum() if not sd.empty else 0
    return total_items, total_qty, inventory_value, low_items

def reset_cache():
    load_products.clear()
    load_customers.clear()

# ----------------------------
# PDF Builders
# ----------------------------
def make_invoice_pdf_multi(sale_row, items_df, settings):
    if not REPORTLAB_OK:
        return b""
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    W, H = A4
    x = 20*mm; y = H - 20*mm

    # Logo if present
    logo_bytes = settings.get("logo")
    x_text = x
    if logo_bytes:
        try:
            img = ImageReader(BytesIO(logo_bytes))
            c.drawImage(img, x, y-15*mm, width=25*mm, height=15*mm, preserveAspectRatio=True, mask='auto')
            x_text = x + 30*mm
        except Exception:
            x_text = x

    c.setFont("Helvetica-Bold", 16); c.drawString(x_text, y, settings.get("company_name","Your Company"))
    c.setFont("Helvetica", 10); y -= 12; c.drawString(x_text, y, settings.get("company_address",""))
    y -= 12; c.drawString(x_text, y, f"Phone: {settings.get('company_phone','')}  GSTIN: {settings.get('company_gstin','')}  Email: {settings.get('company_email','')}")
    y -= 20

    c.setFont("Helvetica-Bold", 14); c.drawString(x, y, "TAX INVOICE"); y -= 16
    c.setFont("Helvetica", 10)
    c.drawString(x, y, f"Invoice No: {sale_row.get('invoice_no','')}"); y -= 12
    c.drawString(x, y, f"Date: {str(sale_row.get('sold_at',''))[:10]}"); y -= 12
    c.drawString(x, y, f"Bill To: {sale_row.get('customer','Walk-in')}"); y -= 16

    # Table header
    c.setFont("Helvetica-Bold", 10)
    c.drawString(x, y, "Item")
    c.drawRightString(W-130, y, "Qty")
    c.drawRightString(W-100, y, "Rate")
    c.drawRightString(W-70, y, "GST%")
    c.drawRightString(W-30, y, "Amount")
    y -= 10; c.setStrokeColor(colors.grey); c.line(x, y, W-15*mm, y); y -= 10

    c.setFont("Helvetica", 10)
    for _, r in items_df.iterrows():
        if y < 60*mm:  # new page
            c.showPage(); y = H - 20*mm
            c.setFont("Helvetica-Bold", 10)
            c.drawString(x, y, "Item"); c.drawRightString(W-130, y, "Qty"); c.drawRightString(W-100, y, "Rate"); c.drawRightString(W-70, y, "GST%"); c.drawRightString(W-30, y, "Amount")
            y -= 10; c.setStrokeColor(colors.grey); c.line(x, y, W-15*mm, y); y -= 10; c.setFont("Helvetica", 10)
        c.drawString(x, y, str(r.get("product",""))[:40])
        c.drawRightString(W-130, y, f"{float(r.get('qty',0)):.2f}")
        c.drawRightString(W-100, y, f"{float(r.get('unit_price',0)):.2f}")
        c.drawRightString(W-70, y, f"{float(r.get('gst_rate',0)):.0f}")
        amount = float(r.get("qty",0))*float(r.get("unit_price",0))
        c.drawRightString(W-30, y, f"{amount:.2f}")
        y -= 14

    y -= 6; c.setStrokeColor(colors.black); c.line(x, y, W-15*mm, y); y -= 14
    c.setFont("Helvetica-Bold", 10)
    c.drawRightString(W-80, y, "Subtotal:"); c.setFont("Helvetica", 10); c.drawRightString(W-30, y, f"{float(sale_row.get('subtotal',0)):.2f}")
    y -= 14; c.setFont("Helvetica-Bold", 10); c.drawRightString(W-80, y, "GST:"); c.setFont("Helvetica", 10); c.drawRightString(W-30, y, f"{float(sale_row.get('tax_amount',0)):.2f}")
    y -= 14; c.setFont("Helvetica-Bold", 11); c.drawRightString(W-80, y, "Total:"); c.setFont("Helvetica-Bold", 11); c.drawRightString(W-30, y, f"{float(sale_row.get('total_amount',0)):.2f}")
    y -= 16
    footer = settings.get("invoice_footer") or ""
    if footer:
        c.setFont("Helvetica", 9); c.drawString(x, y, footer)

    c.showPage(); c.save(); buf.seek(0)
    return buf.getvalue()

def customer_statement_pdf(customer_row, ledger_df, opening_balance, settings, dfrom, dto):
    if not REPORTLAB_OK:
        return b""
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    W, H = A4
    x = 20*mm; y = H - 20*mm

    # Header with logo
    logo_bytes = settings.get("logo")
    x_text = x
    if logo_bytes:
        try:
            img = ImageReader(BytesIO(logo_bytes))
            c.drawImage(img, x, y-15*mm, width=25*mm, height=15*mm, preserveAspectRatio=True, mask='auto')
            x_text = x + 30*mm
        except Exception:
            x_text = x

    c.setFont("Helvetica-Bold", 16); c.drawString(x_text, y, settings.get("company_name","Your Company"))
    c.setFont("Helvetica", 10); y -= 12; c.drawString(x_text, y, settings.get("company_address",""))
    y -= 12; c.drawString(x_text, y, f"Phone: {settings.get('company_phone','')}  GSTIN: {settings.get('company_gstin','')}")
    y -= 18
    c.setFont("Helvetica-Bold", 14); c.drawString(x, y, "Customer Statement"); y -= 14
    c.setFont("Helvetica", 10)
    c.drawString(x, y, f"Customer: {customer_row.get('name')}"); y -= 12
    c.drawString(x, y, f"Period: {dfrom} to {dto}"); y -= 12
    c.drawString(x, y, f"Opening Balance: ₹ {opening_balance:.2f}"); y -= 14

    # Table header
    c.setFont("Helvetica-Bold", 10)
    c.drawString(x, y, "Date")
    c.drawString(x+80, y, "Type")
    c.drawString(x+150, y, "Ref/Note")
    c.drawRightString(W-60, y, "Amount")
    c.drawRightString(W-20, y, "Balance")
    y -= 10; c.setStrokeColor(colors.grey); c.line(x, y, W-15*mm, y); y -= 10
    c.setFont("Helvetica", 10)

    balance = opening_balance
    for _, r in ledger_df.iterrows():
        if y < 30*mm:
            c.showPage(); y = H - 20*mm
            c.setFont("Helvetica-Bold", 10)
            c.drawString(x, y, "Date"); c.drawString(x+80, y, "Type"); c.drawString(x+150, y, "Ref/Note")
            c.drawRightString(W-60, y, "Amount"); c.drawRightString(W-20, y, "Balance")
            y -= 10; c.setStrokeColor(colors.grey); c.line(x, y, W-15*mm, y); y -= 10; c.setFont("Helvetica", 10)
        amt = float(r["amount"])
        balance = round(balance + amt, 2)
        c.drawString(x, y, str(r["entry_date"])[:10])
        c.drawString(x+80, y, r["entry_type"])
        c.drawString(x+150, y, (r["note"] or "")[:40])
        c.drawRightString(W-60, y, f"{amt:.2f}")
        c.drawRightString(W-20, y, f"{balance:.2f}")
        y -= 12

    footer = settings.get("invoice_footer") or ""
    if footer:
        y -= 10; c.setFont("Helvetica", 8); c.drawString(x, y, footer)

    c.showPage(); c.save(); buf.seek(0)
    return buf.getvalue()

def export_reports_excel(dfp, sales_master, sales_items, stock, balances):
    out = BytesIO()
    with pd.ExcelWriter(out, engine="xlsxwriter") as writer:
        if dfp is not None and not dfp.empty:
            dfp.to_excel(writer, index=False, sheet_name="Purchases")
        if sales_master is not None and not sales_master.empty:
            sales_master.to_excel(writer, index=False, sheet_name="SalesMaster")
        if sales_items is not None and not sales_items.empty:
            sales_items.to_excel(writer, index=False, sheet_name="SalesItems")
        if stock is not None and not stock.empty:
            stock.to_excel(writer, index=False, sheet_name="Stock")
        if balances is not None and not balances.empty:
            balances.to_excel(writer, index=False, sheet_name="CustomerBalances")
    out.seek(0)
    return out

# ----------------------------
# LOGIN GATE
# ----------------------------
def login_gate():
    st.title("🔒 Login")
    st.caption("Default admin: **admin / admin123** (change it in Settings).")
    u = st.text_input("Username")
    p = st.text_input("Password", type="password")
    if st.button("Sign in"):
        uid = verify_login(u.strip(), p)
        if uid:
            st.session_state.user_id = uid
            st.session_state.username = u.strip()
            st.rerun()
        else:
            st.error("Invalid credentials")

# ----------------------------
# UI
# ----------------------------
def main():
    st.set_page_config(page_title="Inventory (Login + Multi-item + PDFs)", page_icon="📦", layout="wide")
    init_db()

    # auth
    if "user_id" not in st.session_state:
        login_gate()
        return

    st.sidebar.success(f"Logged in as {st.session_state.get('username','')}")
    if st.sidebar.button("Logout"):
        for k in ["user_id","username","cart"]:
            if k in st.session_state: del st.session_state[k]
        st.rerun()

    st.title("📦 Simple Inventory App")
    tabs = st.tabs(["1) Dashboard", "2) Products", "3) Customers", "4) Purchase", "5) Sales", "6) Stock", "7) Reports", "8) Settings"])

    # 1) Dashboard
    with tabs[0]:
        items_cnt, qty, value, low_items = simple_kpis()
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total Products", items_cnt)
        c2.metric("Total Stock (all items)", f"{qty:.2f}")
        c3.metric("Est. Inventory Value", f"₹ {value:,.2f}")
        c4.metric("Low-stock Items", low_items)
        st.subheader("Quick Stock Snapshot")
        df = stock_df()
        only_low = st.toggle("Show only Low-stock items", value=False)
        if only_low: df = df[df["Low?"]=="YES"]
        st.dataframe(df, use_container_width=True)

    # 2) Products
    with tabs[1]:
        st.subheader("Add / Edit Products")
        prods = load_products()
        with st.form("add_prod"):
            c1, c2, c3, c4 = st.columns(4)
            name = c1.text_input("Product name*")
            category = c2.text_input("Category")
            unit = c3.text_input("Unit", value="pcs")
            price = c4.number_input("Selling price (pre-tax)", min_value=0.0, step=1.0)
            c5, c6, c7 = st.columns(3)
            barcode = c5.text_input("Barcode (optional)")
            low_thr = c6.number_input("Low Stock Threshold", min_value=0.0, step=1.0)
            tax_rate = c7.number_input("GST %", min_value=0.0, step=1.0, help="e.g., 0, 5, 12, 18, 28")
            submitted = st.form_submit_button("Save Product")
            if submitted:
                if not name.strip():
                    st.error("Name is required.")
                else:
                    save_product(name, category, unit, price, barcode, low_thr, tax_rate)
                    st.success("Saved."); reset_cache()
        st.divider()

        if not prods.empty:
            st.write("Existing Products")
            st.dataframe(prods[["id","name","category","unit","selling_price","tax_rate","barcode","low_stock_threshold","created_at"]], use_container_width=True)
            st.write("Edit / Delete")
            sel = st.selectbox("Choose product", options=prods["id"], format_func=lambda i: prods.set_index("id").loc[i, "name"])
            if sel:
                rec = prods[prods["id"]==sel].iloc[0]
                n = st.text_input("Name", rec["name"])
                cat = st.text_input("Category", rec["category"] or "")
                un = st.text_input("Unit", rec["unit"] or "pcs")
                pr = st.number_input("Selling Price (pre-tax)", min_value=0.0, value=float(rec["selling_price"] or 0), step=1.0)
                bc = st.text_input("Barcode", rec.get("barcode") or "")
                lt = st.number_input("Low Stock Threshold", min_value=0.0, value=float(rec.get("low_stock_threshold") or 0), step=1.0)
                tr = st.number_input("GST %", min_value=0.0, value=float(rec.get("tax_rate") or 0), step=1.0)
                colA, colB = st.columns(2)
                if colA.button("Update"):
                    update_product(int(sel), n, cat, un, pr, bc, lt, tr)
                    st.success("Updated."); reset_cache()
                if colB.button("Delete", type="primary"):
                    delete_product(int(sel))
                    st.success("Deleted."); reset_cache()

    # 3) Customers
    with tabs[2]:
        st.subheader("Add / Edit Customers")
        custs = load_customers()
        with st.form("add_cust"):
            c1, c2 = st.columns(2)
            cname = c1.text_input("Customer name*")
            cphone = c2.text_input("Phone")
            cemail = c2.text_input("Email")
            caddr = st.text_area("Address")
            c3, c4, c5 = st.columns(3)
            city = c3.text_input("City")
            state = c4.text_input("State")
            pin = c5.text_input("Pincode")
            c6, c7 = st.columns(2)
            gstin = c6.text_input("GSTIN")
            pan = c7.text_input("PAN")
            c8, c9 = st.columns(2)
            credit = c8.number_input("Credit limit", min_value=0.0, step=1.0)
            cob = c9.number_input("Opening balance (positive = receivable)", min_value=0.0, step=1.0)
            notes = st.text_area("Notes")
            if st.form_submit_button("Save Customer"):
                if not cname.strip():
                    st.error("Name required")
                else:
                    with get_conn() as conn:
                        cur = conn.execute("SELECT id FROM customers WHERE name=?", (cname.strip(),)).fetchone()
                        if cur:
                            conn.execute("""UPDATE customers SET phone=?, email=?, gstin=?, pan=?, address=?, city=?, state=?, pincode=?, credit_limit=?, opening_balance=?, notes=? WHERE id=?""",
                                         (cphone, cemail, gstin, pan, caddr, city, state, pin, credit, cob, notes, int(cur[0])))
                        else:
                            conn.execute("""INSERT INTO customers(name, phone, email, gstin, pan, address, city, state, pincode, credit_limit, opening_balance, notes)
                                            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                                         (cname.strip(), cphone, cemail, gstin, pan, caddr, city, state, pin, credit, cob, notes))
                        conn.commit()
                    st.success("Customer saved."); reset_cache()
        st.divider()
        if not custs.empty:
            st.write("Customers")
            st.dataframe(custs, use_container_width=True)

    # 4) Purchase
    with tabs[3]:
        st.subheader("Record Purchase")
        prods = load_products()
        if prods.empty:
            st.info("Add a product first in the Products tab.")
        else:
            c1, c2 = st.columns(2)
            prod = c1.selectbox("Product", options=prods["id"], format_func=lambda i: prods.set_index("id").loc[i, "name"])
            qty = c2.number_input("Quantity (+)", min_value=0.0, step=1.0)
            c3, c4, c5 = st.columns(3)
            cp = c3.number_input("Cost price (per unit)", min_value=0.0, step=1.0)
            bill = c4.text_input("Bill No.")
            supplier = c5.text_input("Supplier")
            c6, c7 = st.columns(2)
            when = c6.date_input("Purchased on", value=date.today())
            notes = c7.text_input("Notes")
            if st.button("Add Purchase"):
                if qty <= 0:
                    st.error("Quantity must be > 0")
                else:
                    add_purchase(int(prod), qty, cp, bill, supplier, when.isoformat(), notes)
                    st.success("Purchase recorded. Stock increased."); reset_cache()
        st.divider()
        st.subheader("Recent Purchases")
        dfp = list_purchases()
        st.dataframe(dfp, use_container_width=True)

    # 5) Sales (Multi-item)
    with tabs[4]:
        st.subheader("Create Invoice (Multi-item)")
        prods = load_products(); custs = load_customers()

        # Customer selection
        csel1, csel2 = st.columns(2)
        mode = csel1.radio("Customer mode", ["Select existing","Type new"], horizontal=True)
        if mode == "Select existing" and not custs.empty:
            cust_id = csel1.selectbox("Customer", options=custs["id"], format_func=lambda i: custs.set_index("id").loc[i, "name"])
            cust_name = custs.set_index("id").loc[cust_id, "name"]
        else:
            cust_name = csel2.text_input("New Customer Name")
            cust_id = None

        # Balance
        last_balance = 0.0
        if mode == "Select existing" and custs is not None and not custs.empty:
            last_balance = get_customer_balance(int(cust_id))
        st.info(f"Customer last balance: ₹ {last_balance:,.2f} (positive = receivable)")

        # Cart
        if "cart" not in st.session_state:
            st.session_state.cart = []

        st.markdown("**Add Item**")
        if prods.empty:
            st.warning("Add products first.")
        else:
            p1, p2, p3, p4 = st.columns(4)
            pid = p1.selectbox("Product", options=prods["id"], format_func=lambda i: prods.set_index("id").loc[i, "name"])
            default_sp = float(prods.set_index("id").loc[pid,"selling_price"] or 0)
            default_gst = float(prods.set_index("id").loc[pid,"tax_rate"] or 0)
            qty = p2.number_input("Qty", min_value=0.0, step=1.0)
            price = p3.number_input("Unit Price (pre-tax)", min_value=0.0, step=1.0, value=default_sp)
            gst = p4.number_input("GST %", min_value=0.0, step=1.0, value=default_gst)
            desc = st.text_input("Description (optional)")
            if st.button("Add to Cart"):
                st.session_state.cart.append({"product_id": int(pid), "qty": float(qty), "unit_price": float(price), "gst_rate": float(gst), "description": desc})
                st.success("Added to cart.")

        if st.session_state.cart:
            cart_rows = []
            for idx, it in enumerate(st.session_state.cart, start=1):
                name = prods.set_index("id").loc[it["product_id"], "name"] if not prods.empty else str(it["product_id"])
                line_sub = it["qty"]*it["unit_price"]
                line_tax = round(line_sub * it["gst_rate"]/100.0, 2)
                line_total = round(line_sub + line_tax, 2)
                cart_rows.append([idx, name, it["qty"], it["unit_price"], it["gst_rate"], line_tax, line_total])
            df_cart = pd.DataFrame(cart_rows, columns=["#","Product","Qty","Unit Price","GST %","Line Tax","Line Total"])
            st.dataframe(df_cart, use_container_width=True)
            subtotal = (df_cart["Qty"]*df_cart["Unit Price"]).sum()
            tax_total = df_cart["Line Tax"].sum()
            total_amount = df_cart["Line Total"].sum()
            c1, c2, c3 = st.columns(3)
            c1.metric("Subtotal", f"₹ {subtotal:,.2f}")
            c2.metric("GST", f"₹ {tax_total:,.2f}")
            c3.metric("Total", f"₹ {total_amount:,.2f}")
        else:
            st.info("Cart is empty.")

        # Invoice meta
        inv1, inv2 = st.columns(2)
        invoice_no = inv1.text_input("Invoice No.", value=next_invoice_no())
        sold_on = inv2.date_input("Invoice date", value=date.today())
        notes = st.text_input("Notes")
        paid_now = st.number_input("Amount received now (optional)", min_value=0.0, step=1.0, value=0.0)

        colA, colB = st.columns(2)
        if colA.button("Save Invoice"):
            if mode == "Type new":
                if not cust_name.strip():
                    st.error("Enter customer name or select existing.")
                    st.stop()
                customer_id = get_or_create_customer(cust_name.strip())
            else:
                customer_id = int(cust_id)

            if not st.session_state.cart:
                st.error("Cart is empty."); st.stop()
            try:
                sale_id, sub, tax, tot = add_sale_multi(
                    customer_id=customer_id,
                    sold_at=sold_on.isoformat(),
                    items=st.session_state.cart,
                    invoice_no=invoice_no,
                    notes=notes,
                    payment_received=paid_now
                )
                st.success(f"Invoice saved (#{sale_id}).")
                st.session_state.cart = []
                reset_cache()
            except Exception as e:
                st.error(f"Failed to save sale: {e}")

        if colB.button("Clear Cart"):
            st.session_state.cart = []
            st.success("Cart cleared.")

        st.divider()
        st.subheader("Recent Invoices")
        sm = list_sales_master()
        st.dataframe(sm, use_container_width=True)

        st.markdown("### Generate PDF Invoice")
        settings = load_settings()
        if sm is not None and not sm.empty:
            sale_ids = sm["id"].tolist()
            sel_sale = st.selectbox("Select invoice", options=sale_ids, format_func=lambda i: f"{int(i)} — {sm.set_index('id').loc[i, 'invoice_no']}")
            if st.button("Create PDF Invoice"):
                row = sm.set_index("id").loc[sel_sale].to_dict()
                items = list_sales_items(sel_sale)
                pdf_bytes = make_invoice_pdf_multi(row, items, settings)
                st.download_button("Download Invoice PDF", data=pdf_bytes, file_name=f"{row.get('invoice_no','invoice')}.pdf", mime="application/pdf")

    # 6) Stock
    with tabs[5]:
        st.subheader("Current Stock")
        s = stock_df()
        st.dataframe(s, use_container_width=True)
        st.download_button("Download Stock CSV", data=s.to_csv(index=False).encode("utf-8"), file_name="stock.csv", mime="text/csv")

    # 7) Reports
    with tabs[6]:
        st.subheader("Reports & Exports")
        r1, r2 = st.columns(2)
        dfrom = r1.date_input("From", value=date.today().replace(day=1))
        dto = r2.date_input("To", value=date.today())

        dfp = list_purchases(dfrom.isoformat(), dto.isoformat())
        sm = list_sales_master(dfrom.isoformat(), dto.isoformat())

        st.markdown("**Purchases**"); st.dataframe(dfp, use_container_width=True)
        st.markdown("**Sales (Invoices)**"); st.dataframe(sm, use_container_width=True)

        st.markdown("**Sales Items (choose invoice)**")
        if sm is not None and not sm.empty:
            sid = st.selectbox("Invoice", options=sm["id"], format_func=lambda i: sm.set_index("id").loc[i, "invoice_no"], key="rep_items_sel")
            si = list_sales_items(int(sid))
            st.dataframe(si, use_container_width=True)
        else:
            si = pd.DataFrame()

        st.markdown("**Customer Balances (as of To date)**")
        custs = load_customers()
        rows = []
        for _, r in custs.iterrows():
            bal = get_customer_balance(int(r["id"]), as_of=dto.isoformat())
            rows.append([r["id"], r["name"], r["phone"], r["email"], r["gstin"], r["city"], r["state"], r["pincode"], bal])
        balances = pd.DataFrame(rows, columns=["CustomerID","Name","Phone","Email","GSTIN","City","State","Pincode","Balance"])
        st.dataframe(balances, use_container_width=True)

        # Downloads
        stock = stock_df()
        excel_bytes = export_reports_excel(dfp, sm, si, stock, balances)
        st.download_button("Download reports.xlsx", data=excel_bytes, file_name="reports.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        st.download_button("Download purchases.csv", data=dfp.to_csv(index=False).encode("utf-8"), file_name="purchases.csv", mime="text/csv")
        st.download_button("Download sales_master.csv", data=sm.to_csv(index=False).encode("utf-8"), file_name="sales_master.csv", mime="text/csv")
        st.download_button("Download sales_items.csv", data=si.to_csv(index=False).encode("utf-8"), file_name="sales_items.csv", mime="text/csv")
        st.download_button("Download customer_balances.csv", data=balances.to_csv(index=False).encode("utf-8"), file_name="customer_balances.csv", mime="text/csv")

        st.divider()
        st.subheader("Customer Statement PDF")
        if not custs.empty:
            cust_for_stmt = st.selectbox("Customer", options=custs["id"], format_func=lambda i: custs.set_index("id").loc[i, "name"])
            opening = get_customer_balance(int(cust_for_stmt), as_of=dfrom.isoformat())
            with get_conn() as conn:
                ledger_df = pd.read_sql_query(
                    "SELECT entry_date, entry_type, note, amount FROM ledger WHERE customer_id=? AND DATE(entry_date) >= DATE(?) AND DATE(entry_date) <= DATE(?) ORDER BY entry_date",
                    conn, params=(int(cust_for_stmt), dfrom.isoformat(), dto.isoformat())
                )
            settings = load_settings()
            if st.button("Download Statement PDF"):
                cust_row = custs[custs["id"]==int(cust_for_stmt)].iloc[0].to_dict()
                pdf = customer_statement_pdf(cust_row, ledger_df, opening, settings, dfrom.isoformat(), dto.isoformat())
                st.download_button("Save statement.pdf", data=pdf, file_name=f"statement_{cust_row.get('name','customer')}.pdf", mime="application/pdf")

    # 8) Settings
    with tabs[7]:
        st.subheader("Company & Invoice Settings")
        settings = load_settings()
        with st.form("settings_form"):
            c1, c2 = st.columns(2)
            company_name = c1.text_input("Company Name", value=settings.get("company_name",""))
            company_phone = c2.text_input("Phone", value=settings.get("company_phone",""))
            company_email = c2.text_input("Email", value=settings.get("company_email",""))
            company_gstin = c1.text_input("GSTIN", value=settings.get("company_gstin",""))
            company_address = st.text_area("Address", value=settings.get("company_address",""))
            invoice_footer = st.text_area("Invoice Footer (notes/terms)", value=settings.get("invoice_footer",""))
            logo_file = st.file_uploader("Logo (PNG/JPG)", type=["png","jpg","jpeg"])
            logo_bytes = settings.get("logo")
            if logo_file is not None:
                logo_bytes = logo_file.read()
            if st.form_submit_button("Save Settings"):
                save_settings({
                    "company_name": company_name,
                    "company_address": company_address,
                    "company_phone": company_phone,
                    "company_email": company_email,
                    "company_gstin": company_gstin,
                    "invoice_footer": invoice_footer,
                    "logo": logo_bytes
                })
                st.success("Settings saved.")

        st.subheader("Security")
        with st.form("pwd_change"):
            newpwd = st.text_input("New password", type="password")
            newpwd2 = st.text_input("Confirm password", type="password")
            if st.form_submit_button("Change Password"):
                if not newpwd or newpwd != newpwd2:
                    st.error("Passwords do not match")
                else:
                    change_password(st.session_state["user_id"], newpwd)
                    st.success("Password updated.")

        st.caption("SQLite DB: inventory.db")
        try:
            with open(DB_PATH, "rb") as f:
                st.download_button("Download Database (inventory.db)", f, file_name="inventory.db")
        except FileNotFoundError:
            st.info("DB will be created on first write.")

if __name__ == "__main__":
    main()
