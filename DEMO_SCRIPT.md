# Eight-minute demo script

## Before recording

1. Start the application with `docker compose up --build` or the VS Code **Ctrl+Shift+B** task.
2. In a fresh demo database, run `python manage.py migrate` and `python manage.py seed_benchmark`. For a repeatable replay on the local Compose database, explicitly configure the demo reset settings and run `python manage.py reset_benchmark --confirm`.
3. Sign in with `admin` for setup/review and `sales-a` / `sales-b` for the two order submissions. Seeded demo accounts use `demo123`.
4. Django Admin accepts only Admin/Owner staff accounts; StockMovement and AuditLog are read-only there. Keep Products & BOM, Customers, Stock, Production, Orders, Invoices, Payments, and Reports ready in separate browser tabs.

## Walkthrough

### 0:00-0:45 — Problem and architecture

**Say:** “This prototype connects purchasing and production to sales and audit. Django serves the mobile-friendly pages and API actions; PostgreSQL is the deployment database, and SQLite is available for local development.”

Show the Stock page and explain that current quantities are calculated from `StockMovement`, rather than kept in a separately editable balance.
Optionally open Products & BOM to compare the reusable Murukku 200 g (200 packets) and 500 g (80 packets) templates and their variant-specific packaging.

### 0:45-1:35 — Inventory and purchase

Show the benchmark items, base units, and seeded opening balances. Post the benchmark purchase of 60 kg flour at ₹45/kg.

**Expected:** flour increases from 100 kg to 160 kg. The movement records the base-unit quantity and source purchase.

### 1:35-2:50 — BOM-based production

Show the Murukku BOM, then post one run with 40 kg flour, 10.5 L oil, 1.2 kg spice, 194 pouches issued plus 6 damaged, and 9 cartons used. Enter 188 good packets and ₹1,200 labour plus ₹600 gas/power.

**Expected balances:** flour 120 kg, oil 19.5 L, spice 3.8 kg, pouches 306, cartons 21, finished goods 238 packets.

**Expected alerts:** yield variance -6% and oil variance +5%, both flagged. Explain that damaged pouches are a separate wastage movement and finished stock includes only good packets.

### 2:50-4:20 — Tamil Nadu order and invoice

Sign in as `sales-a` and submit 150 packets for Retailer A. As Admin, approve the order, create the invoice, and dispatch it.

**Expected invoice:** ₹7,500 taxable + ₹187.50 CGST + ₹187.50 SGST = ₹7,875.

**Expected stock:** finished goods decrease from 238 to 88 packets. The invoice uses FY numbering, and dispatch creates an outbound movement.

### 4:20-5:30 — Interstate shortage and GST

Sign in as `sales-b` and submit 120 packets for Distributor B. Review the shortage as Admin, then approve 88 packets.

**Expected:** the requested quantity is short by 32 packets. The approved 88 packets produce ₹4,400 taxable plus ₹220 IGST; CGST and SGST are zero.

### 5:30-6:20 — Collections and valuation

Record a ₹4,000 payment against Retailer A's ₹7,875 invoice.

**Expected outstanding:** ₹3,875. Show the production run's ₹5,855 total cost, ₹31.14 cost per good packet, and ₹18.86 margin at ₹50 selling price. This uses moving weighted-average material valuation and includes the run overheads.

### 6:20-7:20 — Reports, company settings, and audit

Show the GST/HSN report, stock ledger, production variance report, and outstanding-ageing report. Export one report to CSV or Excel, then show the invoice PDF with configured company name/address/GSTIN/contact, the amount in words, and audit history.

Explain that posted movements remain in the ledger; corrections use reversals rather than editing history.

### 7:20-8:00 — Mobile and close

Resize to a phone-width viewport and show the salesman order form. Explain that the IndexedDB outbox retries queued submissions when connectivity returns and uses a client UUID to avoid duplicate orders.

**Close:** “The prototype demonstrates the stock-to-cash path. Benchmark reset is explicitly limited to a configured local demo database. Offline entry requires the form to have been loaded while online; automated refunds and push/email low-stock notifications are not implemented.”
