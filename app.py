import streamlit as st
import sqlite3, hashlib
import pandas as pd
from datetime import datetime, date
from io import BytesIO

# ---------- OPTIONAL LIBRARIES ----------
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

# ---------- CONFIG ----------
DB = "erp_complete.db"
SALT = "secure2025"
st.set_page_config("ERP System", "🏢", layout="wide")

# ---------- DATABASE ----------
def db():
    conn = sqlite3.connect(DB, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def hash_pw(pw):
    return hashlib.sha256((SALT + pw).encode()).hexdigest()

# ---------- INIT DB ----------
def init_db():
    c = db().cursor()

    c.execute("""CREATE TABLE IF NOT EXISTS users(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE,
        password TEXT,
        role TEXT
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS customers(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        mobile TEXT UNIQUE,
        email TEXT,
        address TEXT
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS products(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        price REAL,
        stock REAL
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS invoices(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        invoice_no TEXT,
        date TEXT,
        customer TEXT,
        total REAL,
        paid REAL DEFAULT 0
    )""")

    # default users
    c.execute(
        "INSERT OR IGNORE INTO users (username,password,role) VALUES (?,?,?)",
        ("admin", hash_pw("admin123"), "admin")
    )
    c.execute(
        "INSERT OR IGNORE INTO users (username,password,role) VALUES (?,?,?)",
        ("staff", hash_pw("staff123"), "staff")
    )

    db().commit()

# ---------- HELPERS ----------
def is_admin():
    return "user" in st.session_state and st.session_state.user["role"] == "admin"

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

# ---------- LOGIN ----------
def login():
    st.title("🔐 Login")
    u = st.text_input("Username")
    p = st.text_input("Password", type="password")

    if st.button("Login"):
        user = db().execute(
            "SELECT id,password,role FROM users WHERE username=?",
            (u.strip(),)
        ).fetchone()

        if not user:
            st.error("User does not exist")
            return

        if hash_pw(p) != user[1]:
            st.error("Invalid password")
            return

        st.session_state.user = {"id": user[0], "role": user[2]}
        st.success("Login successful")
        st.rerun()

# ---------- DASHBOARD ----------
def dashboard():
    st.header("📊 Dashboard")
    d = db()

    f1, f2 = st.columns(2)
    fd = f1.date_input("From", date.today().replace(day=1))
    td = f2.date_input("To", date.today())

    df = pd.read_sql("""
        SELECT DATE(date) day, SUM(total) sales
        FROM invoices
        WHERE DATE(date) BETWEEN ? AND ?
        GROUP BY DATE(date)
    """, d, params=(fd, td))

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

# ---------- CUSTOMERS ----------
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

# ---------- PRODUCTS ----------
def products_page():
    st.header("📦 Products")
    with st.form("prod"):
        n = st.text_input("Product Name")
        p = st.number_input("Price", 0.0)
        s = st.number_input("Opening Stock", 0.0)
        if st.form_submit_button("Add"):
            db().execute(
                "INSERT INTO products(name,price,stock) VALUES(?,?,?)",
                (n,p,s)
            )
            db().commit()
            st.success("Product added")

    st.dataframe(pd.read_sql("SELECT * FROM products", db()))

# ---------- SALES ----------
def sales_page():
    st.header("🧾 Sales / Invoice")
    prods = pd.read_sql("SELECT * FROM products", db())
    if prods.empty:
        st.info("Add products first")
        return

    p = st.selectbox("Product", prods.name)
    q = st.number_input("Quantity", 1.0)

    if st.button("Create Invoice"):
        row = prods[prods.name==p].iloc[0]
        if q > row.stock:
            st.error("Not enough stock")
            return

        total = row.price * q
        inv = f"INV-{int(datetime.now().timestamp())}"

        db().execute(
            "INSERT INTO invoices(invoice_no,date,customer,total) VALUES(?,?,?,?)",
            (inv, datetime.now().isoformat(), "Walk-in", total)
        )
        db().execute(
            "UPDATE products SET stock=stock-? WHERE id=?",
            (q, row.id)
        )
        db().commit()
        st.success(f"Invoice {inv} created")

    st.subheader("Invoice History")
    st.dataframe(pd.read_sql("SELECT * FROM invoices", db()))

# ---------- REPORTS ----------
def reports_page():
    st.header("📑 Reports")
    df = pd.read_sql("SELECT * FROM invoices", db())
    st.dataframe(df)
    if not df.empty:
        export_excel(df, "sales_report.xlsx")
        export_pdf(df, "sales_report.pdf")

# ---------- MAIN ----------
def main():
    init_db()

    if "user" not in st.session_state:
        login()
        return

    st.sidebar.success(f"Logged in as {st.session_state.user['role'].upper()}")

    if is_admin():
        menu = st.sidebar.radio(
            "Menu",
            ["Dashboard","Customers","Products","Sales","Reports","Logout"]
        )
    else:
        menu = st.sidebar.radio(
            "Menu",
            ["Dashboard","Customers","Sales","Logout"]
        )

    if menu == "Dashboard":
        dashboard()
    elif menu == "Customers":
        customers_page()
    elif menu == "Products":
        products_page()
    elif menu == "Sales":
        sales_page()
    elif menu == "Reports":
        reports_page()
    elif menu == "Logout":
        st.session_state.clear()
        st.rerun()

main()
