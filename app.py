import streamlit as st
import sqlite3, hashlib
import pandas as pd
from datetime import datetime, date, timedelta
import matplotlib.pyplot as plt
from io import BytesIO

# ================= CONFIG =================
DB = "final_erp.db"
SALT = "secure2025"
st.set_page_config("ERP System", "🏢", layout="wide")

# ================= DATABASE =================
def db():
    conn = sqlite3.connect(DB, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def hash_pw(pw):
    return hashlib.sha256((SALT + pw).encode()).hexdigest()

def is_admin():
    return st.session_state.user["role"] == "admin"

def init_db():
    d = db().cursor()

    d.execute("""CREATE TABLE IF NOT EXISTS users(
        id INTEGER PRIMARY KEY,
        username TEXT UNIQUE,
        password TEXT,
        role TEXT
    )""")

    d.execute("""CREATE TABLE IF NOT EXISTS customers(
        id INTEGER PRIMARY KEY,
        name TEXT,
        mobile TEXT UNIQUE,
        email TEXT,
        address TEXT,
        credit_limit REAL DEFAULT 0
    )""")

    d.execute("""CREATE TABLE IF NOT EXISTS products(
        id INTEGER PRIMARY KEY,
        name TEXT,
        gst REAL,
        min_stock REAL
    )""")

    d.execute("""CREATE TABLE IF NOT EXISTS purchase_batches(
        id INTEGER PRIMARY KEY,
        product_id INTEGER,
        batch_no TEXT,
        expiry TEXT,
        qty REAL,
        rate REAL
    )""")

    d.execute("""CREATE TABLE IF NOT EXISTS invoices(
        id INTEGER PRIMARY KEY,
        invoice_no TEXT,
        date TEXT,
        customer_id INTEGER,
        total REAL,
        paid REAL DEFAULT 0,
        due_date TEXT
    )""")

    d.execute("""CREATE TABLE IF NOT EXISTS invoice_items(
        id INTEGER PRIMARY KEY,
        invoice_id INTEGER,
        product TEXT,
        qty REAL,
        buy REAL,
        sell REAL
    )""")

    d.execute("""CREATE TABLE IF NOT EXISTS payments(
        id INTEGER PRIMARY KEY,
        invoice_id INTEGER,
        customer_id INTEGER,
        amount REAL,
        mode TEXT,
        ref_no TEXT,
        ref_date TEXT,
        cheque_status TEXT,
        date TEXT
    )""")

    if not d.execute("SELECT 1 FROM users").fetchone():
        d.execute("INSERT INTO users VALUES(NULL,'admin',?, 'admin')",
                  (hash_pw("admin123"),))
        d.execute("INSERT INTO users VALUES(NULL,'staff',?, 'staff')",
                  (hash_pw("staff123"),))

    db().commit()

# ================= HELPERS =================
def download_chart(fig, name):
    buf = BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight")
    buf.seek(0)
    st.download_button("⬇ Download Chart", buf, name, "image/png")

# ================= LOGIN =================
def login():
    st.title("🔐 ERP Login")
    u = st.text_input("Username")
    p = st.text_input("Password", type="password")
    if st.button("Login"):
        r = db().execute(
            "SELECT * FROM users WHERE username=?", (u,)
        ).fetchone()
        if r and hash_pw(p) == r[2]:
            st.session_state.user = {"id": r[0], "role": r[3]}
            st.rerun()
        else:
            st.error("Invalid login")

# ================= DASHBOARD =================
def dashboard():
    st.title("📊 Dashboard")
    d = db()

    with st.expander("🔍 Filters", expanded=True):
        c1, c2 = st.columns(2)
        fd = c1.date_input("From", date.today().replace(day=1))
        td = c2.date_input("To", date.today())

    kpi = pd.read_sql("""
        SELECT SUM(total) sales, SUM(total-paid) outstanding
        FROM invoices
        WHERE DATE(date) BETWEEN ? AND ?
    """, d, params=(fd, td))

    c1, c2, c3 = st.columns(3)
    c1.metric("Sales", f"₹ {kpi.sales[0] or 0:,.2f}")

    if is_admin():
        c2.metric("Outstanding", f"₹ {kpi.outstanding[0] or 0:,.2f}")
        prof = pd.read_sql("""
            SELECT SUM((sell-buy)*qty) profit
            FROM invoice_items ii JOIN invoices i ON ii.invoice_id=i.id
            WHERE DATE(i.date) BETWEEN ? AND ?
        """, d, params=(fd, td))
        c3.metric("Profit", f"₹ {prof.profit[0] or 0:,.2f}")

    sales = pd.read_sql("""
        SELECT DATE(date) d, SUM(total) s
        FROM invoices
        WHERE DATE(date) BETWEEN ? AND ?
        GROUP BY d ORDER BY d
    """, d, params=(fd, td))

    fig, ax = plt.subplots()
    ax.plot(sales.d, sales.s, marker="o")
    ax.set_title("Sales Trend")
    st.pyplot(fig)
    download_chart(fig, "sales_trend.png")

# ================= MAIN =================
def main():
    init_db()

    if "user" not in st.session_state:
        login()
        return

    if is_admin():
        menu = st.sidebar.radio("Menu", [
            "Dashboard","Customers","Products",
            "New Invoice","Payments",
            "Customer Ledger","Outstanding",
            "Low Stock Alert","Logout"
        ])
    else:
        menu = st.sidebar.radio("Menu", [
            "Dashboard","Customers","New Invoice","Logout"
        ])

    if menu == "Dashboard":
        dashboard()

    elif menu == "Customers":
        st.header("👥 Customers")
        with st.form("cust"):
            n = st.text_input("Name")
            m = st.text_input("Mobile")
            e = st.text_input("Email")
            a = st.text_area("Address")
            c = st.number_input("Credit Limit", 0.0)
            s = st.form_submit_button("Save")
        if s:
            try:
                db().execute("""
                    INSERT INTO customers(name,mobile,email,address,credit_limit)
                    VALUES(?,?,?,?,?)
                """,(n,m,e,a,c))
                db().commit()
                st.success("Customer added")
            except:
                st.error("Mobile already exists")
        st.dataframe(pd.read_sql("SELECT * FROM customers", db()))

    elif menu == "Products":
        st.header("📦 Products")
        n = st.text_input("Product Name")
        g = st.number_input("GST %")
        m = st.number_input("Min Stock")
        if st.button("Add"):
            db().execute("INSERT INTO products VALUES(NULL,?,?,?)",(n,g,m))
            db().commit()
        st.dataframe(pd.read_sql("SELECT * FROM products", db()))

    elif menu == "Logout":
        st.session_state.clear()
        st.rerun()

main()
