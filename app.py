
import streamlit as st
import sqlite3
import pandas as pd
from datetime import datetime, date
from io import BytesIO

# Optional PDF libs
try:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    REPORTLAB_OK = True
except Exception:
    REPORTLAB_OK = False

DB_PATH = "inventory.db"

# ----------------------------
# DB Helpers
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

    # Base tables
    cur.execute("""
    CREATE TABLE IF NOT EXISTS products(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        category TEXT,
        unit TEXT DEFAULT 'pcs',
        selling_price REAL DEFAULT 0.0,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );""")

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

    cur.execute("""
    CREATE TABLE IF NOT EXISTS sales(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
        qty REAL NOT NULL,
        selling_price REAL,
        invoice_no TEXT,
        customer TEXT,
        sold_at TEXT DEFAULT CURRENT_TIMESTAMP,
        notes TEXT
    );""")

    # --- Schema upgrades (safe & idempotent) ---
    if not column_exists(conn, "products", "barcode"):
        conn.execute("ALTER TABLE products ADD COLUMN barcode TEXT")
    if not column_exists(conn, "products", "low_stock_threshold"):
        conn.execute("ALTER TABLE products ADD COLUMN low_stock_threshold REAL DEFAULT 0")
    if not column_exists(conn, "products", "tax_rate"):
        conn.execute("ALTER TABLE products ADD COLUMN tax_rate REAL DEFAULT 0")

    if not column_exists(conn, "sales", "gst_rate"):
        conn.execute("ALTER TABLE sales ADD COLUMN gst_rate REAL DEFAULT 0")
    if not column_exists(conn, "sales", "tax_amount"):
        conn.execute("ALTER TABLE sales ADD COLUMN tax_amount REAL DEFAULT 0")
    if not column_exists(conn, "sales", "total_amount"):
        conn.execute("ALTER TABLE sales ADD COLUMN total_amount REAL DEFAULT 0")

    # Unique index on barcode (allows multiple NULLs, unique when not NULL)
    conn.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_products_barcode
                    ON products(barcode) WHERE barcode IS NOT NULL""")

    conn.commit()
    conn.close()

# ----------------------------
# Data Loaders & Utilities
# ----------------------------
@st.cache_data(ttl=60)
def load_products():
    with get_conn() as conn:
        return pd.read_sql_query("SELECT * FROM products ORDER BY name", conn)

def get_stock(product_id: int) -> float:
    with get_conn() as conn:
        p = conn.execute("SELECT COALESCE(SUM(qty),0) FROM purchases WHERE product_id=?", (product_id,)).fetchone()[0]
        s = conn.execute("SELECT COALESCE(SUM(qty),0) FROM sales WHERE product_id=?", (product_id,)).fetchone()[0]
        return (p or 0) - (s or 0)

def stock_df():
    prods = load_products()
    if prods.empty:
        return pd.DataFrame(columns=["Product","Category","Unit","In Stock","Selling Price","Barcode","Low Stock Threshold","GST %","Low?"])
    prods["In Stock"] = prods["id"].apply(get_stock)
    prods["Low?"] = prods.apply(
        lambda r: "YES" if (float(r.get("low_stock_threshold") or 0) > 0 and r["In Stock"] <= float(r.get("low_stock_threshold") or 0))
        else "", axis=1
    )
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

def add_sale(product_id, qty, sp, gst_rate, invoice_no, customer, when, notes):
    sp = float(sp or 0); qty = float(qty or 0); gst_rate = float(gst_rate or 0)
    subtotal = sp * qty
    tax_amount = round(subtotal * gst_rate / 100.0, 2)
    total_amount = round(subtotal + tax_amount, 2)
    with get_conn() as conn:
        conn.execute("""INSERT INTO sales(product_id, qty, selling_price, gst_rate, tax_amount, total_amount, invoice_no, customer, sold_at, notes)
                        VALUES(?,?,?,?,?,?,?,?,?,?)""",
                     (product_id, qty, sp, gst_rate, tax_amount, total_amount, invoice_no, customer, when, notes))
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
    with get_conn() as conn:
        return pd.read_sql_query(query, conn, params=params)

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
    with get_conn() as conn:
        return pd.read_sql_query(query, conn, params=params)

def simple_kpis():
    sd = stock_df()
    total_items = len(sd)
    total_qty = sd["In Stock"].sum() if not sd.empty else 0
    inventory_value = (sd["In Stock"] * sd["Selling Price"].fillna(0)).sum() if not sd.empty else 0
    low_items = (sd["Low?"]=="YES").sum() if not sd.empty else 0
    return total_items, total_qty, inventory_value, low_items

def reset_cache():
    load_products.clear()

def next_invoice_no():
    with get_conn() as conn:
        cur = conn.execute("SELECT COUNT(*) FROM sales")
        count = (cur.fetchone() or [0])[0] + 1
    now = datetime.now().strftime("%Y%m")
    return f"INV-{now}-{count:04d}"

def make_invoice_pdf(rec, company):
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
    c.drawString(x_margin, y, f"Invoice No: {rec.get('invoice_no','')}"); y -= 12
    c.drawString(x_margin, y, f"Date: {rec.get('sold_at','')[:10]}"); y -= 12
    c.drawString(x_margin, y, f"Bill To: {rec.get('customer','Walk-in')}"); y -= 20

    c.setFont("Helvetica-Bold", 10)
    c.drawString(x_margin, y, "Item")
    c.drawRightString(W-110, y, "Qty")
    c.drawRightString(W-80, y, "Rate")
    c.drawRightString(W-50, y, "GST%")
    c.drawRightString(W-20, y, "Amount")
    y -= 10; c.setStrokeColor(colors.grey); c.line(x_margin, y, W-15*mm, y); y -= 12

    c.setFont("Helvetica", 10)
    item_total = float(rec.get("selling_price",0)) * float(rec.get("qty",0))
    c.drawString(x_margin, y, rec.get("product",""))
    c.drawRightString(W-110, y, f"{rec.get('qty',0):.2f}")
    c.drawRightString(W-80, y, f"{float(rec.get('selling_price',0)):.2f}")
    c.drawRightString(W-50, y, f"{float(rec.get('gst_rate',0)):.2f}")
    c.drawRightString(W-20, y, f"{item_total:.2f}")
    y -= 18

    c.setFont("Helvetica-Bold", 10); c.drawRightString(W-80, y, "Subtotal:")
    c.setFont("Helvetica", 10); c.drawRightString(W-20, y, f"{item_total:.2f}"); y -= 14
    c.setFont("Helvetica-Bold", 10); c.drawRightString(W-80, y, "GST:")
    c.setFont("Helvetica", 10); c.drawRightString(W-20, y, f"{float(rec.get('tax_amount',0)):.2f}"); y -= 14
    c.setFont("Helvetica-Bold", 11); c.drawRightString(W-80, y, "Total:")
    c.setFont("Helvetica-Bold", 11); c.drawRightString(W-20, y, f"{float(rec.get('total_amount',0)):.2f}"); y -= 30

    c.setFont("Helvetica", 8); c.drawString(x_margin, y, "Thank you for your business!")
    c.showPage(); c.save(); buf.seek(0)
    return buf.getvalue()

def export_reports_to_excel(dfp, dfs):
    output = BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        if dfp is not None and not dfp.empty:
            dfp.to_excel(writer, index=False, sheet_name="Purchases")
        if dfs is not None and not dfs.empty:
            dfs.to_excel(writer, index=False, sheet_name="Sales")
    output.seek(0)
    return output

# ----------------------------
# UI
# ----------------------------
def main():
    st.set_page_config(page_title="Simple Inventory (GST + PDF + Excel)", page_icon="📦", layout="wide")
    init_db()
    st.title("📦 Simple Inventory App")
    st.caption("GST on sales, PDF invoices, Excel exports.")

    tabs = st.tabs(["1) Dashboard", "2) Products", "3) Purchase", "4) Sales", "5) Stock", "6) Reports", "7) Settings"])

    # 1) Dashboard
    with tabs[0]:
        items, qty, value, low_items = simple_kpis()
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total Products", items)
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

    # 3) Purchase
    with tabs[2]:
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

    # 4) Sales
    with tabs[3]:
        st.subheader("Record Sale (with GST)")
        prods = load_products()
        if prods.empty:
            st.info("Add a product first.")
        else:
            c1, c2 = st.columns(2)
            prod = c1.selectbox("Product", options=prods["id"], format_func=lambda i: prods.set_index("id").loc[i, "name"], key="sale_prod")
            qty = c2.number_input("Quantity (-)", min_value=0.0, step=1.0, key="sale_qty")

            if prod:
                rec = prods.set_index("id").loc[int(prod)]
                default_sp = float(rec["selling_price"] or 0)
                default_gst = float(rec.get("tax_rate") or 0)
            else:
                default_sp = 0.0; default_gst = 0.0

            c3, c4, c5 = st.columns(3)
            sp = c3.number_input("Selling price per unit (pre-tax)", min_value=0.0, step=1.0, value=default_sp, key="sale_sp")
            gst = c4.number_input("GST %", min_value=0.0, step=1.0, value=default_gst, key="sale_gst")
            cust = c5.text_input("Customer", key="sale_cust")

            c6, c7 = st.columns(2)
            when = c6.date_input("Sold on", value=date.today(), key="sale_date")
            notes = c7.text_input("Notes", key="sale_notes")

            subtotal = sp * qty
            tax_amount = round(subtotal * gst / 100.0, 2)
            total_amount = round(subtotal + tax_amount, 2)
            st.info(f"Subtotal ₹{subtotal:.2f}  |  GST ₹{tax_amount:.2f}  |  Total ₹{total_amount:.2f}")

            inv_col1, inv_col2 = st.columns(2)
            invoice_no = inv_col1.text_input("Invoice No.", value=next_invoice_no())
            if st.button("Add Sale"):
                current = get_stock(int(prod))
                if qty <= 0:
                    st.error("Quantity must be > 0")
                elif qty > current:
                    st.warning(f"Not enough stock. Available: {current:.2f}")
                else:
                    add_sale(int(prod), qty, sp, gst, invoice_no, cust, when.isoformat(), notes)
                    st.success("Sale recorded. Stock decreased."); reset_cache()

        st.divider()
        st.subheader("Recent Sales")
        dfs = list_sales()
        st.dataframe(dfs, use_container_width=True)

        st.markdown("### Generate PDF Invoice")
        if not dfs.empty:
            sale_ids = dfs["id"].tolist()
            sel_sale = st.selectbox("Select a sale", options=sale_ids, format_func=lambda i: f"{int(i)} — {dfs.set_index('id').loc[i, 'invoice_no']}")
            company_name = st.text_input("Company Name", value="Your Company")
            company_address = st.text_area("Address", value="Street, City, State, Pincode")
            company_phone = st.text_input("Phone", value="")
            company_gstin = st.text_input("GSTIN", value="")
            if st.button("Create PDF Invoice"):
                if not REPORTLAB_OK:
                    st.error("ReportLab is not installed. Run: pip install reportlab")
                else:
                    row = dfs.set_index("id").loc[sel_sale].to_dict()
                    pdf_bytes = make_invoice_pdf(row, {
                        "name": company_name,
                        "address": company_address,
                        "phone": company_phone,
                        "gstin": company_gstin
                    })
                    st.download_button("Download Invoice PDF", data=pdf_bytes, file_name=f"{row.get('invoice_no','invoice')}.pdf", mime="application/pdf")

    # 5) Stock
    with tabs[4]:
        st.subheader("Current Stock")
        s = stock_df()
        st.dataframe(s, use_container_width=True)
        csv = s.to_csv(index=False).encode("utf-8")
        st.download_button("Download Stock CSV", data=csv, file_name="stock.csv", mime="text/csv")

    # 6) Reports
    with tabs[5]:
        st.subheader("Simple Reports & Export")
        col1, col2 = st.columns(2)
        dfrom = col1.date_input("From", value=date.today().replace(day=1))
        dto = col2.date_input("To", value=date.today())
        st.write("**Purchases**")
        dfp = list_purchases(dfrom.isoformat(), dto.isoformat())
        st.dataframe(dfp, use_container_width=True)
        st.write("**Sales**")
        dfs = list_sales(dfrom.isoformat(), dto.isoformat())
        st.dataframe(dfs, use_container_width=True)

        st.write("**Summary**")
        total_purchase_amount = (dfp["qty"] * dfp["cost_price"]).sum() if not dfp.empty else 0
        total_sales_subtotal = (dfs["qty"] * dfs["selling_price"]).sum() if not dfs.empty else 0
        total_tax = dfs["tax_amount"].sum() if not dfs.empty else 0
        total_sales_amount = dfs["total_amount"].sum() if not dfs.empty else 0
        c1, c2, c3 = st.columns(3)
        c1.metric("Purchased Amount", f"₹ {total_purchase_amount:,.2f}")
        c2.metric("Sales (Subtotal + Tax)", f"₹ {total_sales_subtotal:,.2f} + ₹ {total_tax:,.2f}")
        c3.metric("Sales Total", f"₹ {total_sales_amount:,.2f}")

        st.markdown("#### Export to Excel")
        if st.button("Download Excel (Purchases & Sales)"):
            xls = export_reports_to_excel(dfp, dfs)
            st.download_button("Save reports.xlsx", data=xls, file_name="reports.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    # 7) Settings
    with tabs[6]:
        st.subheader("Settings & Utilities")
        st.caption("SQLite database is stored locally as inventory.db in the same folder.")
        s1, s2 = st.columns(2)
        try:
            with open(DB_PATH, "rb") as f:
                s1.download_button("Download Database (inventory.db)", f, file_name="inventory.db")
        except FileNotFoundError:
            st.info("DB will be created on first write.")
        warn = s2.checkbox("I understand this will erase all data.")
        if st.button("Erase ALL data"):
            if warn:
                with get_conn() as conn:
                    conn.execute("DELETE FROM purchases")
                    conn.execute("DELETE FROM sales")
                    conn.execute("DELETE FROM products")
                    conn.commit()
                reset_cache()
                st.success("All data erased.")
            else:
                st.warning("Please tick the checkbox to confirm.")

if __name__ == "__main__":
    main()
