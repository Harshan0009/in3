import streamlit as st
import sqlite3, hashlib
import pandas as pd
from datetime import date, datetime
from io import BytesIO

# -------- SAFE OPTIONAL IMPORTS --------
try:
    import matplotlib.pyplot as plt
    MATPLOTLIB = True
except:
    MATPLOTLIB = False

try:
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    PDF_OK = True
except:
    PDF_OK = False

# -------- CONFIG --------
DB = "erp_full_app.db"
SALT = "secure2025"
st.set_page_config("ERP Application", "🏢", layout="wide")

# -------- DATABASE --------
def db():
    conn = sqlite3.connect(DB, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def hash_pw(pw):
    return hashlib.sha256((SALT + pw).encode()).hexdigest()

def init_db():
    c = db().cursor()

    c.execute("""CREATE TABLE IF NOT EXISTS users(
        id INTEGER PRIMARY KEY,
        username TEXT UNIQUE,
        password TEXT,
        role TEXT
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS customers(
        id INTEGER PRIMARY KEY,
        name TEXT,
        mobile TEXT UNIQUE,
        email TEXT,
        address TEXT
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS products(
        id INTEGER PRIMARY KEY,
        name TEXT,
        price REAL,
        stock REAL
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS invoices(
        id INTEGER PRIMARY KEY,
        invoice_no TEXT,
        date TEXT,
        customer TEXT,
        total REAL
    )""")

    # default users
    if not c.execute("SELECT 1 FROM users WHERE username='admin'").fetchone():
        c.execute("INSERT INTO users VALUES(NULL,'admin',?, 'admin')",
                  (hash_pw("admin123"),))
    if not c.execute("SELECT 1 FROM users WHERE username='staff'").fetchone():
        c.execute("INSERT INTO users VALUES(NULL,'staff',?, 'staff')",
                  (hash_pw("staff123"),))

    db().commit()

# -------- LOGIN --------
def login():
    st.title("🔐 Login")
    u = st.text_input("Username")
    p = st.text_input("Password", type="password")

    if st.button("Login"):
        r = db().execute(
            "SELECT id, password, role FROM users WHERE username=?",
            (u,)
        ).fetchone()

        if r and hash_pw(p) == r[1]:
            st.session_state.user = {"id": r[0], "role": r[2]}
            st.rerun()
        else:
            st.error("Invalid login")

def is_admin():
    return st.session_state.user["role"] == "admin"

# -------- EXPORT HELPERS --------
def export_excel(df, name):
    buf = BytesIO()
    df.to_excel(buf, index=False)
    buf.seek(0)
    st.download_button("⬇ Excel", buf, name,
                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

def export_pdf(df, name):
    if not PDF_OK:
        st.warning("PDF export not available")
        return
    buf = BytesIO()
    pdf = SimpleDocTemplate(buf, pagesize=A4)
    table = Table([df.columns.tolist()] + df.values.tolist())
    table.setStyle(TableStyle([
        ("GRID",(0,0),(-1,-1),1,colors.black),
        ("BACKGROUND",(0,0),(-1,0),colors.lightgrey)
    ]))
    pdf.build([table])
    buf.seek(0)
    st.download_button("⬇ PDF", buf, name, "application/pdf")

# -------- DASHBOARD --------
def dashboard():
    st.header("📊 Dashboard")
    d = db()

    f1, f2 = st.columns(2)
    from_d = f1.date_input("From Date", date.today().replace(day=1))
    to_d = f2.date_input("To Date", date.today())

    df = pd.read_sql("""
        SELECT DATE(date) as day, SUM(total) as sales
        FROM invoices
        WHERE DATE(date) BETWEEN ? AND ?
        GROUP BY DATE(date)
    """, d, params=(from_d, to_d))

    st.metric("Total Sales", f"₹ {df.sales.sum() if not df.empty else 0:,.2f}")

    if MATPLOTLIB and not df.empty:
        fig, ax = plt.subplots()
        ax.plot(df.day, df.sales, marker="o")
        st.pyplot(fig)
    else:
        st.line_chart(df.set_index("day")["sales"] if not df.empty else [])

    if not df.empty:
        export_excel(df, "dashboard_sales.xlsx")
        export_pdf(df, "dashboard_sales.pdf")

# -------- CUSTOMERS --------
def customers_page():
    st.header("👥 Customers")
    with st.form("cust"):
        n = st.text_input("Name")
        m = st.text_input("Mobile")
        e = st.text_input("Email")
        a = st.text_area("Address")
        if st.form_submit_button("Add"):
            try:
                db().execute(
                    "INSERT INTO customers(name,mobile,email,address) VALUES(?,?,?,?)",
                    (n,m,e,a)
                )
                db().commit()
                st.success("Customer added")
            except:
                st.error("Mobile already exists")
    st.dataframe(pd.read_sql("SELECT * FROM customers", db()))

# -------- PRODUCTS --------
def products_page():
    st.header("📦 Products")
    with st.form("prod"):
        n = st.text_input("Product Name")
        p = st.number_input("Price", 0.0)
        s = st.number_input("Stock", 0.0)
        if st.form_submit_button("Add"):
            db().execute(
                "INSERT INTO products(name,price,stock) VALUES(?,?,?)",
                (n,p,s)
            )
            db().commit()
            st.success("Product added")
    st.dataframe(pd.read_sql("SELECT * FROM products", db()))

# -------- SALES --------
def sales_page():
    st.header("🧾 Sales / Invoice")
    prods = pd.read_sql("SELECT * FROM products", db())
    if prods.empty:
        st.info("Add products first")
        return
    p = st.selectbox("Product", prods.name)
    q = st.number_input("Quantity", 1.0)
    if st.button("Create Invoice"):
        price = prods[prods.name==p].price.iloc[0]
        total = price*q
        inv = f"INV-{int(datetime.now().timestamp())}"
        db().execute(
            "INSERT INTO invoices(invoice_no,date,customer,total) VALUES(?,?,?,?)",
            (inv, datetime.now().isoformat(), "Walk-in", total)
        )
        db().execute(
            "UPDATE products SET stock=stock-? WHERE name=?",
            (q,p)
        )
        db().commit()
        st.success(f"Invoice {inv} created")

# -------- REPORTS --------
def reports_page():
    st.header("📑 Reports")
    df = pd.read_sql("SELECT * FROM invoices", db())
    st.dataframe(df)
    if not df.empty:
        export_excel(df, "sales_report.xlsx")
        export_pdf(df, "sales_report.pdf")

# -------- MAIN --------
def main():
    init_db()

    if "user" not in st.session_state:
        login()
        return

    st.sidebar.success(f"Logged in as {st.session_state.user['role'].upper()}")

    if is_admin():
        menu = st.sidebar.radio("Menu", [
            "Dashboard","Customers","Products",
            "Sales / Invoices","Reports","Logout"
        ])
    else:
        menu = st.sidebar.radio("Menu", [
            "Dashboard","Customers","Sales / Invoices","Logout"
        ])

    if menu == "Dashboard":
        dashboard()
    elif menu == "Customers":
        customers_page()
    elif menu == "Products":
        products_page()
    elif menu == "Sales / Invoices":
        sales_page()
    elif menu == "Reports":
        reports_page()
    elif menu == "Logout":
        st.session_state.clear()
        st.rerun()

main()
