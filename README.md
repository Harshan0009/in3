
# Simple Inventory App (Streamlit) — Enhanced
Seven tabs: Dashboard, Products, Purchase, Sales, Stock, Reports, Settings.

New:
- Barcode & Low Stock Threshold per product
- Low-stock alerts and filter on Dashboard
- Import/Export products CSV

## Run
1) Install Python 3.10+
2) `pip install -r requirements.txt`
3) `streamlit run app.py`

SQLite DB file `inventory.db` will be created next to app.py on first run.


## GST, PDF, and Excel
- Add products with GST% (tax_rate). Sales show tax breakup and store gst_rate, tax_amount, total_amount.
- Generate a PDF invoice per sale (uses ReportLab). If you see an error, install dependencies with `pip install -r requirements.txt`.
- Export Purchases and Sales to a single Excel with two sheets.


## Troubleshooting (uv / Streamlit Cloud)
- If install hangs on `pandas==2.2.2`, upgrade to `pandas>=2.2.3` (already set here).
- If the app still doesn't boot, try Python 3.12 by changing `runtime.txt` to `python-3.12`.
- Local run: `uv pip install -r requirements.txt && streamlit run app.py`.


### Note on barcode UNIQUE
SQLite doesn't allow `ALTER TABLE ... ADD COLUMN ... UNIQUE`. This app creates a UNIQUE INDEX instead:
`CREATE UNIQUE INDEX IF NOT EXISTS idx_products_barcode ON products(barcode) WHERE barcode IS NOT NULL`.
