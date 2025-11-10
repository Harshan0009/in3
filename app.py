import streamlit as st
import sqlite3
import pandas as pd
from datetime import datetime, date
from io import BytesIO
import hashlib
import os

# Optional PDF libs
try:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    REPORTLAB_OK = True
except Exception:
    REPORTLAB_OK = False

# ----------------------------
# Config
# ----------------------------
DB_PATH = "inventory.db"
DEFAULT_ADMIN_PASSWORD = "admin123"  # change after first login

# ----------------------------
# Utilities: password hashing and auth
# ----------------------------
SALT = "simple_inventory_salt_v3"  # bump salt version if you want to force-reset

def hash_password(pw: str) -> str:
    if pw is None:
        pw = ""
    s = (SALT + pw).encode("utf-8")
    return hashlib.sha256(s).hexdigest()

def check_password_hash(pw: str, h: str) -> bool:
    return hash_password(pw) == h

# ----------------------------
# DB Helpers & migrations
# ----------------------------
@st.cache_resource
def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, detect_types=sqlite3.PARSE_DECLTYPES|sqlite3.PARSE_COLNAMES)
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn

def column_exists(conn, table, column):
    cur = conn.execute(f"PRAGMA table_info({table})")
    return any(r[1] == column for r in cur.fetchall())


def init_db():
    conn = get_conn()
    cur = conn.cursor()

    # base tables
    cur.execute("""CREATE TABLE IF NOT EXISTS products(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        category TEXT,
        unit TEXT DEFAULT 'pcs',
        selling_price REAL DEFAULT 0.0,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        barcode TEXT,
        low_stock_threshold REAL DEFAULT 0,
        tax_rate REAL DEFAULT 0
    );""")

    cur.execute("""CREATE TABLE IF NOT EXISTS purchases(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
        qty REAL NOT NULL,
        cost_price REAL DEFAULT 0.0,
        bill_no TEXT,
        supplier TEXT,
        purchased_at TEXT DEFAULT CURRENT_TIMESTAMP,
        notes TEXT
    );""")

    cur.execute("""CREATE TABLE IF NOT EXISTS invoices(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        invoice_no TEXT UNIQUE,
        customer_id INTEGER REFERENCES customers(id) ON DELETE SET NULL,
        date TEXT DEFAULT CURRENT_TIMESTAMP,
        total_tax REAL DEFAULT 0,
        subtotal REAL DEFAULT 0,
        total_amount REAL DEFAULT 0,
        supply_type TEXT DEFAULT 'INTRA',
        notes TEXT
    );""")

    cur.execute("""CREATE TABLE IF NOT EXISTS invoice_items(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        invoice_id INTEGER NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
        product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE SET NULL,
        qty REAL NOT NULL,
        selling_price REAL DEFAULT 0,
        gst_rate REAL DEFAULT 0,
        tax_amount REAL DEFAULT 0,
        cgst_rate REAL DEFAULT 0,
        sgst_rate REAL DEFAULT 0,
        igst_rate REAL DEFAULT 0,
        cgst_amount REAL DEFAULT 0,
        sgst_amount REAL DEFAULT 0,
        igst_amount REAL DEFAULT 0,
        line_total REAL DEFAULT 0
    );""")

    cur.execute("""CREATE TABLE IF NOT EXISTS sales(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
        qty REAL NOT NULL,
        selling_price REAL,
        gst_rate REAL DEFAULT 0,
        tax_amount REAL DEFAULT 0,
        total_amount REAL DEFAULT 0,
        invoice_no TEXT,
        customer TEXT,
        sold_at TEXT DEFAULT CURRENT_TIMESTAMP,
        notes TEXT
    );""")

    cur.execute("""CREATE TABLE IF NOT EXISTS customers(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        phone TEXT,
        gstin TEXT,
        address TEXT,
        opening_balance REAL DEFAULT 0,
        credit_limit REAL DEFAULT 0,
        state TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );""")

    # migrations
    if not column_exists(conn, 'customers', 'opening_balance'):
        cur.execute("ALTER TABLE customers ADD COLUMN opening_balance REAL DEFAULT 0")
    if not column_exists(conn, 'customers', 'credit_limit'):
        cur.execute("ALTER TABLE customers ADD COLUMN credit_limit REAL DEFAULT 0")
    if not column_exists(conn, 'customers', 'state'):
        cur.execute("ALTER TABLE customers ADD COLUMN state TEXT")
    if not column_exists(conn, 'invoices', 'supply_type'):
        cur.execute("ALTER TABLE invoices ADD COLUMN supply_type TEXT DEFAULT 'INTRA'")

    # add GST split columns on invoice_items if old DB
    for col in [
        ('cgst_rate','REAL DEFAULT 0'),('sgst_rate','REAL DEFAULT 0'),('igst_rate','REAL DEFAULT 0'),
        ('cgst_amount','REAL DEFAULT 0'),('sgst_amount','REAL DEFAULT 0'),('igst_amount','REAL DEFAULT 0')
    ]:
        if not column_exists(conn, 'invoice_items', col[0]):
            cur.execute(f"ALTER TABLE invoice_items ADD COLUMN {col[0]} {col[1]}")

    # stock adjustments (manual corrections)
    cur.execute("""CREATE TABLE IF NOT EXISTS stock_adjustments(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
        qty_delta REAL NOT NULL,
        reason TEXT,
        adjusted_at TEXT DEFAULT CURRENT_TIMESTAMP
    );""")

    # payments (for receivables/credits)
    cur.execute("""CREATE TABLE IF NOT EXISTS payments(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_id INTEGER REFERENCES customers(id) ON DELETE CASCADE,
        invoice_id INTEGER REFERENCES invoices(id) ON DELETE SET NULL,
        amount REAL NOT NULL,
        paid_at TEXT DEFAULT CURRENT_TIMESTAMP,
        method TEXT,
        notes TEXT
    );""")

    cur.execute("""CREATE TABLE IF NOT EXISTS settings(
        k TEXT PRIMARY KEY,
        v TEXT
    );""")

    # ensure admin password exists
    cur.execute("SELECT v FROM settings WHERE k='admin_password_hash'")
    row = cur.fetchone()
    if not row:
        cur.execute("INSERT OR REPLACE INTO settings(k,v) VALUES(?,?)", ("admin_password_hash", hash_password(DEFAULT_ADMIN_PASSWORD)))

    # company settings for GST
    if not conn.execute("SELECT 1 FROM settings WHERE k='company_state'").fetchone():
        cur.execute("INSERT OR REPLACE INTO settings(k,v) VALUES(?,?)", ('company_state','Telangana'))
    if not conn.execute("SELECT 1 FROM settings WHERE k='company_gstin'").fetchone():
        cur.execute("INSERT OR REPLACE INTO settings(k,v) VALUES(?,?)", ('company_gstin',''))
    if not conn.execute("SELECT 1 FROM settings WHERE k='company_name'").fetchone():
        cur.execute("INSERT OR REPLACE INTO settings(k,v) VALUES(?,?)", ('company_name','Your Company'))

    # index for barcode uniqueness when not null (sqlite 3.8+)
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_products_barcode ON products(barcode) WHERE barcode IS NOT NULL")

    conn.commit()

# ----------------------------
# Data Layer
# ----------------------------
@st.cache_data(ttl=60)
def load_products():
    conn = get_conn()
    return pd.read_sql_query("SELECT * FROM products ORDER BY name", conn)


def reset_cache():
    load_products.clear()


def get_stock(product_id: int) -> float:
    conn = get_conn()
    p = conn.execute("SELECT COALESCE(SUM(qty),0) FROM purchases WHERE product_id=?", (product_id,)).fetchone()[0]
    s = conn.execute("SELECT COALESCE(SUM(qty),0) FROM invoice_items WHERE product_id=?", (product_id,)).fetchone()[0]
    s2 = conn.execute("SELECT COALESCE(SUM(qty),0) FROM sales WHERE product_id=?", (product_id,)).fetchone()[0]
    adj = conn.execute("SELECT COALESCE(SUM(qty_delta),0) FROM stock_adjustments WHERE product_id=?", (product_id,)).fetchone()[0]
    total_s = (s or 0) + (s2 or 0)
    return (p or 0) - total_s + (adj or 0)


def save_product(name, category, unit, price, barcode, low_thr, tax_rate):
    conn = get_conn()
    cur = conn.cursor()
    # check duplicates
    if barcode:
        exists = cur.execute("SELECT id FROM products WHERE barcode=?", (barcode.strip(),)).fetchone()
        if exists:
            return False, "Barcode already used by another product."
    exists_name = cur.execute("SELECT id FROM products WHERE name=?", (name.strip(),)).fetchone()
    if exists_name:
        return False, "Product name already exists. Use Update instead."
    cur.execute("INSERT INTO products(name, category, unit, selling_price, barcode, low_stock_threshold, tax_rate) VALUES(?,?,?,?,?,?,?)",
                (name.strip(), category.strip() if category else None, unit.strip() if unit else "pcs", float(price or 0), barcode.strip() if barcode else None, float(low_thr or 0), float(tax_rate or 0)))
    conn.commit()
    return True, "Inserted"


def update_product(product_id, name, category, unit, price, barcode, low_thr, tax_rate):
    conn = get_conn()
    cur = conn.cursor()
    # check barcode collision
    if barcode:
        row = cur.execute("SELECT id FROM products WHERE barcode=? AND id!=?", (barcode.strip(), product_id)).fetchone()
        if row:
            return False, "Barcode already used by another product."
    # check name collision
    row = cur.execute("SELECT id FROM products WHERE name=? AND id!=?", (name.strip(), product_id)).fetchone()
    if row:
        return False, "Another product has this name."
    cur.execute("UPDATE products SET name=?, category=?, unit=?, selling_price=?, barcode=?, low_stock_threshold=?, tax_rate=? WHERE id=?",
                (name.strip(), category.strip() if category else None, unit.strip() if unit else "pcs", float(price or 0), barcode.strip() if barcode else None, float(low_thr or 0), float(tax_rate or 0), product_id))
    conn.commit()
    return True, "Updated"


def delete_product(product_id):
    conn = get_conn()
    conn.execute("DELETE FROM products WHERE id=?", (product_id,))
    conn.commit()


def add_purchase(product_id, qty, cp, bill_no, supplier, when, notes):
    conn = get_conn()
    conn.execute("INSERT INTO purchases(product_id, qty, cost_price, bill_no, supplier, purchased_at, notes) VALUES(?,?,?,?,?,?,?)",
                 (product_id, float(qty), float(cp or 0), bill_no, supplier, when, notes))
    conn.commit()


def create_invoice(items: list, customer_id, notes, invoice_no=None, when=None, supply_type='INTRA'):
    # items = list of dicts: {product_id, qty, selling_price, gst_rate}
    conn = get_conn()
    cur = conn.cursor()
    subtotal = 0.0
    total_tax = 0.0
    gst_split_items = []
    for it in items:
        line_base = float(it['selling_price']) * float(it['qty'])
        subtotal += line_base
        rate = float(it.get('gst_rate',0))
        tax_amount = round(line_base * rate/100.0, 2)
        total_tax += tax_amount
        if supply_type == 'INTRA':
            cgst_rate = sgst_rate = rate/2.0
            igst_rate = 0.0
            cgst_amount = round(tax_amount/2.0, 2)
            sgst_amount = tax_amount - cgst_amount
            igst_amount = 0.0
        else:
            cgst_rate = sgst_rate = 0.0
            igst_rate = rate
            cgst_amount = sgst_amount = 0.0
            igst_amount = tax_amount
        gst_split_items.append({
            **it,
            'tax_amount': tax_amount,
            'cgst_rate': cgst_rate,
            'sgst_rate': sgst_rate,
            'igst_rate': igst_rate,
            'cgst_amount': cgst_amount,
            'sgst_amount': sgst_amount,
            'igst_amount': igst_amount,
        })
    total_amount = round(subtotal + total_tax,2)
    if not invoice_no:
        invoice_no = next_invoice_no()
    if not when:
        when = datetime.now().isoformat()
    cur.execute("INSERT INTO invoices(invoice_no, customer_id, date, total_tax, subtotal, total_amount, supply_type, notes) VALUES(?,?,?,?,?,?,?,?)",
                (invoice_no, customer_id, when, total_tax, subtotal, total_amount, supply_type, notes))
    inv_id = cur.lastrowid
    for it in gst_split_items:
        line_total = round((float(it['selling_price']) * float(it['qty'])) + float(it['tax_amount']), 2)
        cur.execute("""
            INSERT INTO invoice_items(
                invoice_id, product_id, qty, selling_price, gst_rate, tax_amount,
                cgst_rate, sgst_rate, igst_rate, cgst_amount, sgst_amount, igst_amount, line_total
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (inv_id, int(it['product_id']), float(it['qty']), float(it['selling_price']), float(it.get('gst_rate',0)), float(it['tax_amount']),
         float(it['cgst_rate']), float(it['sgst_rate']), float(it['igst_rate']), float(it['cgst_amount']), float(it['sgst_amount']), float(it['igst_amount']), line_total))
        # also write legacy sales row for compatibility and reports
        cur.execute("INSERT INTO sales(product_id, qty, selling_price, gst_rate, tax_amount, total_amount, invoice_no, customer, sold_at, notes) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (int(it['product_id']), float(it['qty']), float(it['selling_price']), float(it.get('gst_rate',0)), float(it['tax_amount']), line_total, invoice_no, '', when, notes))
    conn.commit()
    reset_cache()
    return inv_id, invoice_no


def add_payment(customer_id, invoice_id, amount, when, method, notes):
    conn = get_conn()
    conn.execute(
        "INSERT INTO payments(customer_id, invoice_id, amount, paid_at, method, notes) VALUES(?,?,?,?,?,?)",
        (customer_id, invoice_id, float(amount), when, method, notes)
    )
    conn.commit()


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
    conn = get_conn()
    return pd.read_sql_query(query, conn, params=params)


def list_invoices(date_from=None, date_to=None):
    query = "SELECT i.id, i.invoice_no, c.name as customer, i.date, i.subtotal, i.total_tax, i.total_amount, i.notes FROM invoices i LEFT JOIN customers c ON c.id=i.customer_id"
    filters, params = [], []
    if date_from:
        filters.append("DATE(i.date) >= DATE(?)"); params.append(date_from)
    if date_to:
        filters.append("DATE(i.date) <= DATE(?)"); params.append(date_to)
    if filters: query += " WHERE " + " AND ".join(filters)
    query += " ORDER BY i.date DESC"
    conn = get_conn()
    return pd.read_sql_query(query, conn, params=params)


def get_invoice(inv_id):
    conn = get_conn()
    inv = conn.execute("SELECT * FROM invoices WHERE id=?", (inv_id,)).fetchone()
    items = conn.execute("SELECT ii.*, p.name as product FROM invoice_items ii LEFT JOIN products p ON p.id=ii.product_id WHERE ii.invoice_id=?", (inv_id,)).fetchall()
    return inv, items


def list_sales(date_from=None, date_to=None):
    query = """SELECT s.id, pr.name as product, s.qty, s.selling_price, s.gst_rate, s.tax_amount, s.total_amount,
                      s.invoice_no, s.customer, s.sold_at, s.notes
               FROM sales s JOIN products pr ON pr.id=s.product_id"""
    filters, params = [], []
    if date_from:
        filters.append("DATE(s.sold_at) >= DATE(?)"); params.append(date_from)
    if date_to:
        filters.append("DATE(s.sold_at) <= DATE(?)"); params.append(date_to)
    if filters: query += " WHERE " + " AND ".join(filters)
    query += " ORDER BY s.sold_at DESC"
    conn = get_conn()
    return pd.read_sql_query(query, conn, params=params)


def invoice_outstanding(inv_id: int) -> float:
    conn = get_conn()
    total = conn.execute("SELECT total_amount FROM invoices WHERE id=?", (inv_id,)).fetchone()
    total = float(total[0]) if total else 0.0
    paid = conn.execute("SELECT COALESCE(SUM(amount),0) FROM payments WHERE invoice_id=?", (inv_id,)).fetchone()[0]
    return round(total - float(paid or 0), 2)


def customer_balances():
    conn = get_conn()
    q = """
    SELECT c.id, c.name, c.phone, c.credit_limit,
           COALESCE(SUM(i.total_amount),0) AS billed,
           COALESCE((SELECT SUM(p.amount) FROM payments p WHERE p.customer_id=c.id),0) AS paid,
           COALESCE(c.opening_balance,0) AS opening_balance
    FROM customers c
    LEFT JOIN invoices i ON i.customer_id=c.id
    GROUP BY c.id
    ORDER BY billed - paid + opening_balance DESC
    """
    df = pd.read_sql_query(q, conn)
    if df.empty:
        return df
    df["balance"] = df["billed"] - df["paid"] + df["opening_balance"]
    df["over_limit"] = (df["credit_limit"] > 0) & (df["balance"] > df["credit_limit"])
    return df


def simple_kpis():
    sd = stock_df()
    total_items = len(sd)
    total_qty = sd["In Stock"].sum() if not sd.empty else 0
    inventory_value = (sd["In Stock"] * sd["Selling Price"].fillna(0)).sum() if not sd.empty else 0
    low_items = (sd["Low?"]=="YES").sum() if not sd.empty else 0
    return total_items, total_qty, inventory_value, low_items


def next_invoice_no():
    conn = get_conn()
    cur = conn.execute("SELECT COUNT(*) FROM invoices")
    count = (cur.fetchone() or [0])[0] + 1
    now = datetime.now().strftime("%Y%m")
    return f"INV-{now}-{count:04d}"

# PDF invoice maker (multi-line)

def make_invoice_pdf_multi(inv_row, items, company):
    if not REPORTLAB_OK:
        return b""
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    W, H = A4
    x_margin = 20*mm
    y = H - 20*mm

    c.setFont("Helvetica-Bold", 16)
    c.drawString(x_margin, y, company.get("name","Your Company"))
    c.setFont("Helvetica", 10)
    y -= 12; c.drawString(x_margin, y, company.get("address",""))
    y -= 12; c.drawString(x_margin, y, f"Phone: {company.get('phone','')}  GSTIN: {company.get('gstin','')}")
    y -= 20

    c.setFont("Helvetica-Bold", 14); c.drawString(x_margin, y, "TAX INVOICE"); y -= 16
    c.setFont("Helvetica", 10)
    c.drawString(x_margin, y, f"Invoice No: {inv_row['invoice_no']}"); y -= 12
    c.drawString(x_margin, y, f"Date: {inv_row['date'][:10]}"); y -= 12
    c.drawString(x_margin, y, f"Supply Type: {'Intra-State' if inv_row.get('supply_type','INTRA')=='INTRA' else 'Inter-State'}"); y -= 12
    c.drawString(x_margin, y, f"Bill To: {inv_row.get('customer','Walk-in')} "); y -= 20

    c.setFont("Helvetica-Bold", 10)
    c.drawString(x_margin, y, "Item")
    c.drawRightString(W-110, y, "Qty")
    c.drawRightString(W-80, y, "Rate")
    c.drawRightString(W-50, y, "GST%")
    c.drawRightString(W-20, y, "Amount")
    y -= 10; c.setStrokeColor(colors.grey); c.line(x_margin, y, W-15*mm, y); y -= 12

    c.setFont("Helvetica", 10)
    cgst_total = sgst_total = igst_total = 0.0
    for it in items:
        item_total = float(it['selling_price']) * float(it['qty'])
        cgst_total += float(it.get('cgst_amount',0) or 0)
        sgst_total += float(it.get('sgst_amount',0) or 0)
        igst_total += float(it.get('igst_amount',0) or 0)
        c.drawString(x_margin, y, it.get('product',''))
        c.drawRightString(W-110, y, f"{it.get('qty',0):.2f}")
        c.drawRightString(W-80, y, f"{float(it.get('selling_price',0)):.2f}")
        c.drawRightString(W-50, y, f"{float(it.get('gst_rate',0)):.2f}")
        c.drawRightString(W-20, y, f"{(item_total):.2f}")
        y -= 12
        if y < 60*mm:
            c.showPage(); y = H - 20*mm

    y -= 6
    c.setFont("Helvetica-Bold", 10); c.drawRightString(W-80, y, "Subtotal:")
    c.setFont("Helvetica", 10); c.drawRightString(W-20, y, f"{inv_row.get('subtotal',0):.2f}"); y -= 14

    # GST split totals
    c.setFont("Helvetica-Bold", 10); c.drawRightString(W-80, y, "CGST:")
    c.setFont("Helvetica", 10); c.drawRightString(W-20, y, f"{cgst_total:.2f}"); y -= 12
    c.setFont("Helvetica-Bold", 10); c.drawRightString(W-80, y, "SGST:")
    c.setFont("Helvetica", 10); c.drawRightString(W-20, y, f"{sgst_total:.2f}"); y -= 12
    c.setFont("Helvetica-Bold", 10); c.drawRightString(W-80, y, "IGST:")
    c.setFont("Helvetica", 10); c.drawRightString(W-20, y, f"{igst_total:.2f}"); y -= 14

    c.setFont("Helvetica-Bold", 11); c.drawRightString(W-80, y, "Total:")
    c.setFont("Helvetica-Bold", 11); c.drawRightString(W-20, y, f"{inv_row.get('total_amount',0):.2f}"); y -= 30

    c.setFont("Helvetica", 8); c.drawString(x_margin, y, "Thank you for your business!")
    c.showPage(); c.save(); buf.seek(0)
    return buf.getvalue()


def export_reports_to_excel(dfp, df_inv, df_bal):
    output = BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        if dfp is not None and not dfp.empty:
            dfp.to_excel(writer, index=False, sheet_name="Purchases")
        if df_inv is not None and not df_inv.empty:
            df_inv.to_excel(writer, index=False, sheet_name="Invoices")
        if df_bal is not None and not df_bal.empty:
            df_bal.to_excel(writer, index=False, sheet_name="CustomerBalances")
    output.seek(0)
    return output

# ----------------------------
# UI helpers
# ----------------------------

def stock_df():
    prods = load_products()
    if prods.empty:
        return pd.DataFrame(columns=["Product","Category","Unit","In Stock","Selling Price","Barcode","Low Stock Threshold","GST %","Low?"])
    prods = prods.copy()
    prods["In Stock"] = prods["id"].apply(get_stock)
    prods["Low?"] = prods.apply(lambda r: "YES" if (float(r.get("low_stock_threshold") or 0) > 0 and r["In Stock"] <= float(r.get("low_stock_threshold") or 0)) else "", axis=1)
    prods["GST %"] = prods["tax_rate"].fillna(0)
    return prods.rename(columns={"name":"Product","category":"Category","unit":"Unit","selling_price":"Selling Price","barcode":"Barcode","low_stock_threshold":"Low Stock Threshold"})[["id","Product","Category","Unit","In Stock","Selling Price","Barcode","Low Stock Threshold","GST %","Low?"]]

# ----------------------------
# Settings helpers
# ----------------------------

def get_setting(k, default=None):
    conn = get_conn()
    row = conn.execute("SELECT v FROM settings WHERE k=?", (k,)).fetchone()
    return row[0] if row else default


def set_setting(k,v):
    conn = get_conn()
    conn.execute("INSERT OR REPLACE INTO settings(k,v) VALUES(?,?)", (k,str(v)))
    conn.commit()

# ----------------------------
# Auth
# ----------------------------

def verify_login():
    if 'logged_in' in st.session_state and st.session_state.logged_in:
        return True
    st.sidebar.title("Login")
    pw = st.sidebar.text_input("Password", type='password')
    show = st.sidebar.checkbox("Show password")
    if show:
        st.sidebar.caption(pw)
    if st.sidebar.button("Login", type="primary"):
        h = get_setting('admin_password_hash')
        if check_password_hash(pw, h):
            st.session_state.logged_in = True
            st.rerun()
        else:
            st.sidebar.error("Incorrect password")
    return False


def logout():
    st.session_state.logged_in = False
    st.rerun()


def change_password_ui():
    st.subheader("Change Admin Password")
    old = st.text_input("Current password", type='password')
    new = st.text_input("New password", type='password')
    new2 = st.text_input("Repeat new password", type='password')
    if st.button("Change password", type="primary"):
        h = get_setting('admin_password_hash')
        if not check_password_hash(old, h):
            st.error("Current password is incorrect")
            return
        if new.strip() == "":
            st.error("New password cannot be empty")
            return
        if new != new2:
            st.error("New passwords do not match")
            return
        set_setting('admin_password_hash', hash_password(new))
        st.success("Password changed successfully")

# ----------------------------
# Pages
# ----------------------------

def page_dashboard():
    st.title("📦 Enhanced Inventory App")
    st.caption("Sidebar navigation • multi-line invoices • customers • backups • exports • payments & credits • stock adjustments • barcode quick add")

    items, qty, value, low_items = simple_kpis()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Products", items)
    c2.metric("Total Stock (all items)", f"{qty:.2f}")
    c3.metric("Est. Inventory Value", f"₹ {value:,.2f}")
    c4.metric("Low-stock Items", low_items)

    st.subheader("Top Customers by Outstanding (High Credit)")
    bal = customer_balances()
    if bal is None or bal.empty:
        st.info("No customers/balances yet.")
    else:
        top = bal.sort_values("balance", ascending=False).head(5)
        st.dataframe(top[["id","name","phone","credit_limit","billed","paid","opening_balance","balance","over_limit"]].rename(columns={"id":"Customer ID","name":"Customer","phone":"Phone","credit_limit":"Credit Limit","billed":"Billed","paid":"Paid","opening_balance":"Opening","balance":"Outstanding","over_limit":"Over Limit?"}), use_container_width=True)

    st.subheader("Least Stock Items")
    df = stock_df()
    if df.empty:
        st.info("No products yet.")
    else:
        least = df.sort_values("In Stock", ascending=True).head(10)
        st.dataframe(least.drop(columns=['id'], errors='ignore'), use_container_width=True)


def page_products():
    st.header("Products")
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
        submitted = st.form_submit_button("Save Product", type="primary")
        if submitted:
            if not name.strip():
                st.error("Name is required.")
            else:
                ok, msg = save_product(name, category, unit, price, barcode, low_thr, tax_rate)
                if not ok:
                    st.error(msg)
                else:
                    st.success("Saved."); reset_cache()
    st.divider()

    prods = load_products()
    if not prods.empty:
        st.write("Existing Products")
        st.dataframe(prods[['id','name','category','unit','selling_price','tax_rate','barcode','low_stock_threshold','created_at']], use_container_width=True)
        st.write("Edit / Delete")
        sel = st.selectbox("Choose product", options=prods['id'], format_func=lambda i: prods.set_index('id').loc[i, 'name'])
        if sel:
            rec = prods[prods['id']==sel].iloc[0]
            n = st.text_input("Name", rec['name'])
            cat = st.text_input("Category", rec['category'] or "")
            un = st.text_input("Unit", rec['unit'] or "pcs")
            pr = st.number_input("Selling Price (pre-tax)", min_value=0.0, value=float(rec['selling_price'] or 0), step=1.0)
            bc = st.text_input("Barcode", rec.get('barcode') or "")
            lt = st.number_input("Low Stock Threshold", min_value=0.0, value=float(rec.get('low_stock_threshold') or 0), step=1.0)
            tr = st.number_input("GST %", min_value=0.0, value=float(rec.get('tax_rate') or 0), step=1.0)
            colA, colB = st.columns(2)
            if colA.button("Update"):
                ok, msg = update_product(int(sel), n, cat, un, pr, bc, lt, tr)
                if not ok:
                    st.error(msg)
                else:
                    st.success("Updated."); reset_cache()
            if colB.button("Delete", type="primary"):
                delete_product(int(sel))
                st.success("Deleted."); reset_cache()

    st.markdown("### Import Products from CSV")
    st.caption("Columns supported: name, category, unit, selling_price, barcode, low_stock_threshold, tax_rate")
    up = st.file_uploader("Upload CSV")
    if up is not None:
        df_up = pd.read_csv(up)
        ok_cnt, dup_cnt, err_cnt = 0, 0, 0
        for _, r in df_up.iterrows():
            nm = str(r.get('name','')).strip()
            if not nm:
                err_cnt += 1; continue
            ok, msg = save_product(
                nm,
                str(r.get('category','')),
                str(r.get('unit','pcs')),
                float(r.get('selling_price',0) or 0),
                str(r.get('barcode','')) or None,
                float(r.get('low_stock_threshold',0) or 0),
                float(r.get('tax_rate',0) or 0)
            )
            if ok: ok_cnt += 1
            else:
                if 'exists' in msg.lower() or 'barcode' in msg.lower(): dup_cnt += 1
                else: err_cnt += 1
        reset_cache()
        st.success(f"Imported {ok_cnt} products. Duplicates: {dup_cnt}. Errors: {err_cnt}.")


def page_purchase():
    st.header("Purchases")
    st.subheader("Record Purchase")
    prods = load_products()
    if prods.empty:
        st.info("Add a product first in the Products page.")
    else:
        c1, c2 = st.columns(2)
        prod = c1.selectbox("Product", options=prods['id'], format_func=lambda i: prods.set_index('id').loc[i, 'name'])
        qty = c2.number_input("Quantity (+)", min_value=0.0, step=1.0)
        c3, c4, c5 = st.columns(3)
        cp = c3.number_input("Cost price (per unit)", min_value=0.0, step=1.0)
        bill = c4.text_input("Bill No.")
        supplier = c5.text_input("Supplier")
        c6, c7 = st.columns(2)
        when = c6.date_input("Purchased on", value=date.today())
        notes = c7.text_input("Notes")
        if st.button("Add Purchase", type="primary"):
            if qty <= 0:
                st.error("Quantity must be > 0")
            else:
                add_purchase(int(prod), qty, cp, bill, supplier, when.isoformat(), notes)
                st.success("Purchase recorded. Stock increased."); reset_cache()
    st.divider()
    st.subheader("Recent Purchases")
    dfp = list_purchases()
    st.dataframe(dfp, use_container_width=True)


def page_sales_invoices():
    st.header("Sales / Invoices")
    prods = load_products()
    customers = pd.read_sql_query("SELECT * FROM customers ORDER BY name", get_conn()) if True else pd.DataFrame()
    cart = st.session_state.get('cart', [])

    # Company/state settings for GST
    company_state = get_setting('company_state','Telangana')

    # Quick add via barcode
    st.markdown("**Scan/Enter Barcode to add to cart**")
    bc_in = st.text_input("Barcode", key="scan_barcode")
    if bc_in:
        try:
            pr = load_products()
            match = pr[pr['barcode'].fillna('').astype(str) == str(bc_in)]
            if not match.empty:
                pid = int(match.iloc[0]['id'])
                cart.append({'product_id': pid, 'qty': 1.0, 'selling_price': float(match.iloc[0]['selling_price'] or 0), 'gst_rate': float(match.iloc[0]['tax_rate'] or 0)})
                st.session_state.cart = cart
                st.success(f"Added {match.iloc[0]['name']} (qty 1)")
                st.experimental_set_query_params()  # clear focus
            else:
                st.warning("No product with this barcode")
        except Exception as e:
            st.error(f"Barcode error: {e}")

    colP, colC = st.columns([2,1])
    with colP:
        if prods.empty:
            st.info("Add a product first.")
        else:
            psel = st.selectbox("Product", options=prods['id'], format_func=lambda i: prods.set_index('id').loc[i, 'name'], key='cart_prod')
            rec = prods.set_index('id').loc[int(psel)]
            default_sp = float(rec['selling_price'] or 0)
            default_gst = float(rec.get('tax_rate') or 0)
            qty = st.number_input("Quantity", min_value=0.0, step=1.0, key='cart_qty')
            sp = st.number_input("Selling price per unit (pre-tax)", min_value=0.0, step=1.0, value=default_sp, key='cart_sp')
            gst = st.number_input("GST %", min_value=0.0, step=1.0, value=default_gst, key='cart_gst')
            if st.button("Add to cart"):
                if qty <= 0:
                    st.error("Quantity must be > 0")
                else:
                    # check stock
                    available = get_stock(int(psel))
                    if qty > available:
                        st.warning(f"Not enough stock. Available: {available:.2f}")
                    else:
                        cart.append({'product_id': int(psel), 'qty': float(qty), 'selling_price': float(sp), 'gst_rate': float(gst)})
                        st.session_state.cart = cart
                        st.success("Added to cart")
    with colC:
        st.write("Customer")
        cust_sel = st.selectbox("Choose Customer (or blank)", options=[0] + customers['id'].tolist() if not customers.empty else [0], format_func=lambda i: ("- Select -" if i==0 else customers.set_index('id').loc[i,'name'] if i in customers.values else "- Select -"))
        cust_name = st.text_input("Customer Name (if not selecting)")
        supply_type = st.selectbox("Supply Type (GST)", ["INTRA","INTER"], format_func=lambda s: "Intra-State (CGST+SGST)" if s=="INTRA" else "Inter-State (IGST)")

    # Credit limit warning
    if cust_sel and cust_sel != 0:
        bal = customer_balances()
        row = bal[bal['id']==int(cust_sel)] if bal is not None and not bal.empty else pd.DataFrame()
        if not row.empty:
            cl = float(row.iloc[0]['credit_limit'] or 0)
            curr = float(row.iloc[0]['balance'] or 0)
            # approximate cart total
            cart_sub = sum([c['qty']*c['selling_price'] for c in cart])
            cart_tax = sum([c['qty']*c['selling_price']*c.get('gst_rate',0)/100 for c in cart])
            cart_total = cart_sub + cart_tax
            if cl > 0 and curr + cart_total > cl:
                st.warning(f"Credit limit will be exceeded. Limit: ₹{cl:.2f}, Current: ₹{curr:.2f}, Cart: ₹{cart_total:.2f}")

    st.write("### Cart")
    if cart:
        df_cart = pd.DataFrame(cart)
        df_cart = df_cart.join(load_products().set_index('id')['name'], on='product_id')
        df_cart = df_cart.rename(columns={'name':'product'})
        st.dataframe(df_cart[['product','qty','selling_price','gst_rate']])
        if st.button("Clear cart"):
            st.session_state.cart = []
            st.rerun()

        invoice_no = st.text_input("Invoice No.", value=next_invoice_no())
        notes = st.text_area("Notes")
        if st.button("Create Invoice", type="primary"):
            # determine customer id
            cid = None
            if cust_sel and cust_sel!=0:
                cid = int(cust_sel)
            elif cust_name.strip():
                # create customer quickly
                cur = get_conn().cursor()
                cur.execute("INSERT INTO customers(name) VALUES(?)", (cust_name.strip(),))
                get_conn().commit()
                cid = cur.lastrowid
            inv_id, inv_no = create_invoice(st.session_state.cart, cid, notes, invoice_no, supply_type=supply_type)
            st.success(f"Invoice {inv_no} created (id={inv_id})")
            st.session_state.cart = []
            reset_cache()
            st.rerun()
    else:
        st.info("Cart is empty")

    st.divider()
    st.subheader("Recent Invoices & Payments")
    df_inv = list_invoices()
    if not df_inv.empty:
        df_inv = df_inv.copy()
        df_inv['outstanding'] = df_inv['id'].apply(invoice_outstanding)
        st.dataframe(df_inv, use_container_width=True)
        sel = st.selectbox("Select invoice to view", options=df_inv['id'].tolist(), format_func=lambda i: f"{int(i)} - {df_inv.set_index('id').loc[i,'invoice_no']}")
        if sel:
            inv_row = df_inv.set_index('id').loc[sel].to_dict()
            items = pd.read_sql_query("SELECT ii.*, p.name as product FROM invoice_items ii LEFT JOIN products p ON p.id=ii.product_id WHERE invoice_id=?", get_conn(), params=(sel,)).to_dict(orient='records')
            st.write(inv_row)
            st.dataframe(pd.DataFrame(items))
            company_name = st.text_input("Company Name", value="Your Company")
            company_address = st.text_area("Address", value="Street, City, State, Pincode")
            company_phone = st.text_input("Phone", value="")
            company_gstin = st.text_input("GSTIN", value="")
            if REPORTLAB_OK:
                pdf_bytes = make_invoice_pdf_multi(inv_row, items, {"name":company_name, "address":company_address, "phone":company_phone, "gstin":company_gstin})
                st.download_button("Download Invoice PDF", data=pdf_bytes, file_name=f"{inv_row.get('invoice_no','invoice')}.pdf", mime="application/pdf")
            else:
                st.info("Install ReportLab to enable PDF invoices: pip install reportlab")

            # Quick receive full payment
            out = float(inv_row.get('outstanding', invoice_outstanding(int(sel))))
            st.markdown(f"**Outstanding:** ₹{out:.2f}")
            if out > 0:
                if st.button("Receive full payment for this invoice"):
                    # find customer id
                    cid = pd.read_sql_query("SELECT customer_id FROM invoices WHERE id=?", get_conn(), params=(int(sel),)).iloc[0]['customer_id']
                    if cid:
                        add_payment(int(cid), int(sel), out, date.today().isoformat(), "Cash", "Full settlement")
                        st.success("Payment recorded.")
                        st.rerun()

    st.divider()
    st.subheader("Record Payment (generic)")
    conn = get_conn()
    cust_df = pd.read_sql_query("SELECT id, name FROM customers ORDER BY name", conn)
    if cust_df.empty:
        st.info("Add a customer first (create via invoice or Customers page).")
    else:
        cc1, cc2, cc3 = st.columns(3)
        cust = cc1.selectbox("Customer", options=cust_df['id'], format_func=lambda i: cust_df.set_index('id').loc[i,'name'])
        inv_options = pd.read_sql_query("SELECT id, invoice_no FROM invoices WHERE customer_id=? ORDER BY date DESC", conn, params=(int(cust),))
        inv_sel = cc2.selectbox("Invoice (optional)", options=[0] + inv_options['id'].tolist() if not inv_options.empty else [0], format_func=lambda i: ("— none —" if i==0 else inv_options.set_index('id').loc[i,'invoice_no']))
        amt = cc3.number_input("Amount", min_value=0.0, step=1.0)
        c4, c5, c6 = st.columns(3)
        when = c4.date_input("Paid on", value=date.today())
        method = c5.selectbox("Method", options=["Cash","UPI","Card","Bank","Other"])
        notes = c6.text_input("Notes")
        if st.button("Save Payment", type="primary"):
            if amt <= 0:
                st.error("Amount must be > 0")
            else:
                add_payment(int(cust), int(inv_sel) if inv_sel else None, amt, when.isoformat(), method, notes)
                st.success("Payment saved.")


def page_stock():
    st.header("Current Stock & Adjustments")
    s = stock_df()
    st.dataframe(s.drop(columns=['id'], errors='ignore'), use_container_width=True)
    csv = s.drop(columns=['id'], errors='ignore').to_csv(index=False).encode("utf-8")
    st.download_button("Download Stock CSV", data=csv, file_name="stock.csv", mime="text/csv")

    st.divider()
    st.subheader("Manual Stock Adjustment")
    prods = load_products()
    if prods.empty:
        st.info("No products to adjust.")
    else:
        a1, a2, a3 = st.columns(3)
        pid = a1.selectbox("Product", options=prods['id'], format_func=lambda i: prods.set_index('id').loc[i,'name'])
        delta = a2.number_input("Qty change (+/-)", step=1.0)
        reason = a3.text_input("Reason", value="Correction")
        if st.button("Apply Adjustment", type="primary"):
            conn = get_conn()
            conn.execute("INSERT INTO stock_adjustments(product_id, qty_delta, reason, adjusted_at) VALUES(?,?,?,?)", (int(pid), float(delta), reason, datetime.now().isoformat()))
            conn.commit()
            reset_cache()
            st.success("Adjustment applied")

    st.markdown("### Recent Adjustments")
    conn = get_conn()
    adj = pd.read_sql_query("SELECT sa.id, p.name as product, sa.qty_delta, sa.reason, sa.adjusted_at FROM stock_adjustments sa JOIN products p ON p.id=sa.product_id ORDER BY sa.adjusted_at DESC LIMIT 200", conn)
    st.dataframe(adj, use_container_width=True)


def gst_summary(date_from=None, date_to=None):
    conn = get_conn()
    q = """
    SELECT i.date as inv_date, i.invoice_no, ii.gst_rate,
           (ii.qty * ii.selling_price) as taxable_value,
           ii.cgst_amount, ii.sgst_amount, ii.igst_amount,
           i.supply_type
    FROM invoice_items ii
    JOIN invoices i ON i.id = ii.invoice_id
    """
    params = []
    conds = []
    if date_from:
        conds.append("DATE(i.date) >= DATE(?)")
        params.append(date_from)
    if date_to:
        conds.append("DATE(i.date) <= DATE(?)")
        params.append(date_to)
    if conds:
        q += " WHERE " + " AND ".join(conds)
    df = pd.read_sql_query(q, conn, params=params)
    if df.empty:
        return df, pd.DataFrame()
    # grouped summary by GST rate and supply type
    grp = df.groupby(['gst_rate','supply_type'], as_index=False).agg({
        'taxable_value':'sum','cgst_amount':'sum','sgst_amount':'sum','igst_amount':'sum'
    })
    return df, grp


def page_reports():
    st.header("Reports & Export")
    col1, col2 = st.columns(2)
    dfrom = col1.date_input("From", value=date.today().replace(day=1))
    dto = col2.date_input("To", value=date.today())
    st.write("**Purchases**")
    dfp = list_purchases(dfrom.isoformat(), dto.isoformat())
    st.dataframe(dfp, use_container_width=True)
    st.write("**Invoices**")
    df_inv = list_invoices(dfrom.isoformat(), dto.isoformat())
    st.dataframe(df_inv, use_container_width=True)

    st.write("**Customer Balances**")
    df_bal = customer_balances()
    st.dataframe(df_bal, use_container_width=True)

    st.subheader("GST Summary")
    raw, gst = gst_summary(dfrom.isoformat(), dto.isoformat())
    if gst is None or gst.empty:
        st.info("No GST data in this range.")
    else:
        st.dataframe(gst.rename(columns={'gst_rate':'GST %','supply_type':'Supply','taxable_value':'Taxable Value','cgst_amount':'CGST','sgst_amount':'SGST','igst_amount':'IGST'}), use_container_width=True)
        st.markdown("**Totals**")
        tot = {
            'Taxable Value': float(gst['taxable_value'].sum()),
            'CGST': float(gst['cgst_amount'].sum()),
            'SGST': float(gst['sgst_amount'].sum()),
            'IGST': float(gst['igst_amount'].sum()),
        }
        st.write(tot)

    st.markdown("#### Export to Excel")
    xls = export_reports_to_excel(dfp, df_inv, df_bal)
    st.download_button("Download reports.xlsx", data=xls, file_name="reports.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


def page_customers():
    st.header("Customers & Ledger")
    conn = get_conn()
    c1, c2 = st.columns(2)
    with c1.form("add_cust"):
        name = st.text_input("Name*")
        phone = st.text_input("Phone")
        gstin = st.text_input("GSTIN")
        address = st.text_area("Address")
        state = st.text_input("State", placeholder="e.g., Telangana")
        opening = st.number_input("Opening Balance (receivable)", min_value=0.0, step=1.0, help="Positive means customer owes you")
        limitv = st.number_input("Credit Limit (₹)", min_value=0.0, step=1.0)
        if st.form_submit_button("Save Customer", type="primary"):
            if not name.strip():
                st.error("Name is required")
            else:
                conn.execute("INSERT INTO customers(name, phone, gstin, address, state, opening_balance, credit_limit) VALUES(?,?,?,?,?,?,?)", (name.strip(), phone.strip(), gstin.strip(), address.strip(), state.strip(), float(opening or 0), float(limitv or 0)))
                conn.commit()
                st.success("Customer saved")
    dfc = pd.read_sql_query("SELECT * FROM customers ORDER BY name", conn)
    st.dataframe(dfc, use_container_width=True)

    st.markdown("### Customer Ledger")
    if dfc.empty:
        st.info("Add a customer to view ledger.")
    else:
        cust = st.selectbox("Select customer", options=dfc['id'], format_func=lambda i: dfc.set_index('id').loc[i,'name'])
        inv = pd.read_sql_query("SELECT id, invoice_no, date, total_amount FROM invoices WHERE customer_id=? ORDER BY date", conn, params=(int(cust),))
        pay = pd.read_sql_query("SELECT invoice_id, amount, paid_at, method, notes FROM payments WHERE customer_id=? ORDER BY paid_at", conn, params=(int(cust),))
        bal = customer_balances()
        row = bal[bal['id']==int(cust)].iloc[0] if bal is not None and not bal.empty else None
        st.write({"Opening": float(row['opening_balance']) if row is not None else 0.0, "Billed": float(row['billed']) if row is not None else 0.0, "Paid": float(row['paid']) if row is not None else 0.0, "Outstanding": float(row['balance']) if row is not None else 0.0, "Credit Limit": float(row['credit_limit']) if row is not None else 0.0})
        st.write("Invoices")
        st.dataframe(inv, use_container_width=True)
        st.write("Payments")
        st.dataframe(pay, use_container_width=True)


def page_settings():
    st.header("Settings & Utilities")
    st.caption("Database stored locally as inventory.db")
    s1, s2 = st.columns(2)
    try:
        with open(DB_PATH, "rb") as f:
            s1.download_button("Download Database (inventory.db)", f, file_name="inventory.db")
    except FileNotFoundError:
        st.info("DB will be created on first write.")

    st.markdown("### Company & GST Settings")
    cs1, cs2, cs3 = st.columns(3)
    company_name = cs1.text_input("Company Name", value=get_setting('company_name','Your Company'))
    company_state = cs2.text_input("Company State", value=get_setting('company_state','Telangana'))
    company_gstin = cs3.text_input("Company GSTIN", value=get_setting('company_gstin',''))
    caddr = st.text_area("Company Address", value=get_setting('company_address',''))
    if st.button("Save Company Settings"):
        set_setting('company_name', company_name)
        set_setting('company_state', company_state)
        set_setting('company_gstin', company_gstin)
        set_setting('company_address', caddr)
        st.success("Saved company settings")

    st.markdown("### Backup / Restore")
    uploaded = st.file_uploader("Restore from .db file (this will replace current DB)")
    if uploaded:
        bytes_data = uploaded.getvalue()
        with open(DB_PATH, "wb") as f:
            f.write(bytes_data)
        st.success("Database restored. The app will reload.")
        st.rerun()

    st.markdown("### Admin")
    cL, cR = st.columns([1,1])
    with cL:
        if st.button("Logout", type="secondary"):
            logout()
    with cR:
        pass
    change_password_ui()

    st.markdown("### Danger Zone")
    warn = st.checkbox("I understand this will erase all data.")
    if st.button("Erase ALL data"):
        if warn:
            conn = get_conn()
            conn.execute("DELETE FROM purchases")
            conn.execute("DELETE FROM sales")
            conn.execute("DELETE FROM products")
            conn.execute("DELETE FROM invoices")
            conn.execute("DELETE FROM invoice_items")
            conn.execute("DELETE FROM payments")
            conn.execute("DELETE FROM stock_adjustments")
            conn.execute("DELETE FROM customers")
            conn.commit()
            reset_cache()
            st.success("All data erased.")
        else:
            st.warning("Please tick the checkbox to confirm.")

# ----------------------------
# App entry
# ----------------------------

def main():
    st.set_page_config(page_title="Enhanced Inventory (GST + Invoices)", page_icon="📦", layout="wide")
    init_db()

    # Sidebar nav (top-left) + auth in sidebar
    if not verify_login():
        st.title("Please login to the Inventory App")
        st.write("Use the admin password set on first run. Change it in Settings after login.")
        return

    st.sidebar.markdown("## Navigation")
    page = st.sidebar.radio(
        "Go to",
        ["Dashboard","Products","Purchase","Sales/Invoices","Stock","Reports","Customers","Settings"],
        index=0,
        key="nav_radio"
    )

    if page == "Dashboard":
        page_dashboard()
    elif page == "Products":
        page_products()
    elif page == "Purchase":
        page_purchase()
    elif page == "Sales/Invoices":
        page_sales_invoices()
    elif page == "Stock":
        page_stock()
    elif page == "Reports":
        page_reports()
    elif page == "Customers":
        page_customers()
    elif page == "Settings":
        page_settings()


if __name__ == "__main__":
    main()
