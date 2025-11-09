
# Enhanced Inventory App

Single-file Streamlit app (app.py) — enhanced features:
- Multi-line invoices (invoices + invoice_items)
- Customers table
- Cart-based invoice creation and PDF invoice (ReportLab)
- Backup & restore (upload .db)
- Product duplicate handling (no silent INSERT OR IGNORE)
- Password-protected admin with password change UI

## Setup
1. Create a virtual environment (recommended):
   ```bash
   python -m venv venv
   source venv/bin/activate  # or venv\\Scripts\\activate on Windows
   pip install streamlit pandas reportlab xlsxwriter
   ```
   *Note:* `reportlab` is optional — without it you won't be able to create PDFs.

2. Save `app.py` in a folder and run:
   ```bash
   streamlit run app.py
   ```

3. First login password is: `admin123`. Change it immediately under **Settings > Admin > Change Admin Password**.

## Notes & Tips
- Invoices are created with a generated invoice number. If you want stricter uniqueness under concurrent usage, consider migrating DB to Postgres.
- The app stores the admin password hash in the `settings` table. Passwords are hashed with SHA256 + salt. For stronger security use a proper hashing library (bcrypt) when deploying.
- CSV / Excel exports are provided in Reports and Stock tabs.

Enjoy — ask me to tweak any behavior or split files into modules.
