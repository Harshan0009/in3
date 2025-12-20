import streamlit as st
import sqlite3, hashlib
import pandas as pd
from datetime import date
from io import BytesIO

# ---------- CONFIG ----------
DB = "erp_export_safe.db"
SALT = "secure2025"

st.set_page_config("ERP Dashboard", "📊", layout="wide")

# ---------- DATABASE ----------
def db():
    conn = sqlite3.connect(DB, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def hash_pw(pw):
    return hashlib.sha256((SALT + pw).encode()).hexdigest()

def init_db():
    conn = db()
    c = conn.cursor()

    # Users
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            password TEXT,
            role TEXT
        )
    """)

    # Invoices (demo for dashboard)
    c.execute("""
        CREATE TABLE IF NOT EXISTS invoices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_no TEXT,
            date TEXT,
            total REAL,
            paid REAL DEFAULT 0
        )
    """)

    # Insert default users ONLY if not exists
    admin = c.execute(
        "SELECT 1 FROM users WHERE username='admin'"
    ).fetchone()

    staff = c.execute(
        "SELECT 1 FROM users WHERE username='staff'"
    ).fetchone()

    if not admin:
        c.execute(
            "INSERT INTO users (username,password,role) VALUES (?,?,?)",
            ("admin", hash_pw("admin123"), "admin")
        )

    if not staff:
        c.execute(
            "INSERT INTO users (username,password,role) VALUES (?,?,?)",
            ("staff", hash_pw("staff123"), "staff")
        )

    conn.commit()

# ---------- LOGIN ----------
def login():
    st.title("🔐 ERP Login")

    username = st.text_input("Username")
    password = st.text_input("Password", type="password")

    if st.button("Login"):
        conn = db()
        user = conn.execute(
            "SELECT id, username, password, role FROM users WHERE username=?",
            (username,)
        ).fetchone()

        if user and hash_pw(password) == user[2]:
            st.session_state.user = {
                "id": user[0],
                "username": user[1],
                "role": user[3]
            }
            st.success("Login successful")
            st.rerun()
        else:
            st.error("Invalid username or password")

# ---------- DASHBOARD ----------
def dashboard():
    st.title("📊 Dashboard")

    d = db()

    with st.expander("🔍 Filters", expanded=True):
        c1, c2 = st.columns(2)
        from_date = c1.date_input("From Date", date.today().replace(day=1))
        to_date = c2.date_input("To Date", date.today())

    df = pd.read_sql("""
        SELECT DATE(date) AS Date, SUM(total) AS Sales
        FROM invoices
        WHERE DATE(date) BETWEEN ? AND ?
        GROUP BY DATE(date)
        ORDER BY DATE(date)
    """, d, params=(from_date, to_date))

    total_sales = df["Sales"].sum() if not df.empty else 0
    st.metric("Total Sales", f"₹ {total_sales:,.2f}")

    st.subheader("Sales Trend")
    if not df.empty:
        st.line_chart(df.set_index("Date")["Sales"])
    else:
        st.info("No sales data")

# ---------- MAIN ----------
def main():
    init_db()  # 🔥 VERY IMPORTANT

    if "user" not in st.session_state:
        login()
        return

    st.sidebar.success(
        f"Logged in as {st.session_state.user['role'].upper()}"
    )

    menu = st.sidebar.radio(
        "Menu",
        ["Dashboard", "Logout"]
    )

    if menu == "Dashboard":
        dashboard()
    else:
        st.session_state.clear()
        st.rerun()

main()
