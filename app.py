import streamlit as st
import sqlite3, hashlib
import pandas as pd
from datetime import date

# ---------- OPTIONAL LIBRARIES (SAFE IMPORTS) ----------
try:
    import matplotlib.pyplot as plt
    from io import BytesIO
    MATPLOTLIB = True
except:
    MATPLOTLIB = False

try:
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    PDF_AVAILABLE = True
except:
    PDF_AVAILABLE = False

# ---------- CONFIG ----------
DB = "erp_export_safe.db"
SALT = "secure2025"
st.set_page_config("ERP Dashboard", "📊", layout="wide")

# ---------- DATABASE ----------
def db():
    c = sqlite3.connect(DB, check_same_thread=False)
    c.execute("PRAGMA foreign_keys = ON")
    return c

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
    d.execute("""CREATE TABLE IF NOT EXISTS invoices(
        id INTEGER PRIMARY KEY,
        invoice_no TEXT,
        date TEXT,
        total REAL,
        paid REAL DEFAULT 0
    )""")
    if not d.execute("SELECT 1 FROM users").fetchone():
        d.execute("INSERT INTO users VALUES(NULL,'admin',?, 'admin')",
                  (hash_pw("admin123"),))
        d.execute("INSERT INTO users VALUES(NULL,'staff',?, 'staff')",
                  (hash_pw("staff123"),))
    db().commit()

# ---------- LOGIN ----------
def login():
    st.title("🔐 ERP Login")
    u = st.text_input("Username")
    p = st.text_input("Password", type="password")
    if st.button("Login"):
        r = db().execute("SELECT * FROM users WHERE username=?", (u,)).fetchone()
        if r and hash_pw(p) == r[2]:
            st.session_state.user = {"role": r[3]}
            st.rerun()
        else:
            st.error("Invalid login")

# ---------- EXPORT HELPERS ----------
def export_excel(df, filename):
    buffer = BytesIO()
    df.to_excel(buffer, index=False)
    buffer.seek(0)
    st.download_button(
        "⬇ Download Excel",
        buffer,
        file_name=filename,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

def export_pdf(df, filename, title):
    if not PDF_AVAILABLE:
        st.warning("PDF export not available (reportlab not installed)")
        return

    buffer = BytesIO()
    pdf = SimpleDocTemplate(buffer, pagesize=A4)
    table_data = [df.columns.tolist()] + df.values.tolist()

    table = Table(table_data)
    table.setStyle(TableStyle([
        ("GRID", (0,0), (-1,-1), 1, colors.black),
        ("BACKGROUND", (0,0), (-1,0), colors.lightgrey)
    ]))

    pdf.build([table])
    buffer.seek(0)

    st.download_button(
        "⬇ Download PDF",
        buffer,
        file_name=filename,
        mime="application/pdf"
    )

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

    # ----- CHART -----
    st.subheader("Sales Trend")

    if MATPLOTLIB and not df.empty:
        fig, ax = plt.subplots()
        ax.plot(df["Date"], df["Sales"], marker="o")
        ax.set_xlabel("Date")
        ax.set_ylabel("Sales")
        st.pyplot(fig)
    else:
        st.line_chart(df.set_index("Date")["Sales"] if not df.empty else [])

    # ----- EXPORT BUTTONS -----
    st.subheader("📤 Export Dashboard Data")

    if not df.empty:
        export_excel(df, "dashboard_sales.xlsx")
        export_pdf(df, "dashboard_sales.pdf", "Dashboard Sales Report")
    else:
        st.info("No data to export")

# ---------- MAIN ----------
def main():
    init_db()

    if "user" not in st.session_state:
        login()
        return

    menu = st.sidebar.radio("Menu", ["Dashboard", "Logout"])

    if menu == "Dashboard":
        dashboard()
    else:
        st.session_state.clear()
        st.rerun()

main()
