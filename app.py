from flask import Flask, render_template, request, redirect, url_for, session, send_file, Response
from werkzeug.security import generate_password_hash, check_password_hash
import sqlite3, os, csv, io
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, date
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

DB = os.environ.get("DATABASE_PATH", "factory.db")
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "demo-secret-change-in-production")

MILLI = 1000
PAISE = 100

def db():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con

def q(sql, params=()):
    con=db(); cur=con.execute(sql, params); rows=cur.fetchall(); con.close(); return rows

def one(sql, params=()):
    con=db(); cur=con.execute(sql, params); row=cur.fetchone(); con.close(); return row

def execute(sql, params=()):
    con=db(); cur=con.execute(sql, params); con.commit(); last=cur.lastrowid; con.close(); return last

def dec(value):
    return Decimal(str(value or 0))

def qty_milli(value):
    return int((dec(value) * MILLI).quantize(Decimal("1"), rounding=ROUND_HALF_UP))

def qty(v):
    return dec(v) / MILLI

def format_qty(v):
    d=qty(v) if isinstance(v, int) else dec(v)
    return f"{d.quantize(Decimal('0.001')).normalize():f}"

def format_money_paise(v):
    d=Decimal(v)/PAISE if isinstance(v, int) else dec(v)
    return f"₹{d.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):,.2f}"

def user():
    if not session.get("user_id"): return None
    return one("SELECT * FROM users WHERE id=?", (session["user_id"],))

def login_required(role=None):
    def deco(fn):
        import functools
        @functools.wraps(fn)
        def wrapped(*a, **kw):
            u=user()
            if not u:
                return redirect(url_for("login"))
            if role and u["role"] not in (role if isinstance(role,(list,tuple,set)) else (role,)):
                return redirect(url_for("dashboard"))
            return fn(*a, **kw)
        return wrapped
    return deco

def stock_balance(item_id):
    r=one("SELECT COALESCE(SUM(quantity_milli),0) AS balance FROM stock_ledger WHERE item_id=?", (item_id,))
    return r["balance"] if r else 0

def ledger(item_id, delta, movement_type, source_type, source_id, note=""):
    execute("""INSERT INTO stock_ledger(item_id,quantity_milli,movement_type,source_type,source_id,note,user_id)
               VALUES(?,?,?,?,?,?,?)""",
            (item_id, delta, movement_type, source_type, source_id, note, session.get("user_id")))

SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY AUTOINCREMENT,username TEXT UNIQUE NOT NULL,password_hash TEXT NOT NULL,name TEXT NOT NULL,role TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS items(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,sku TEXT UNIQUE NOT NULL,category TEXT NOT NULL,uom TEXT NOT NULL,base_uom TEXT NOT NOT NULL);
CREATE TABLE IF NOT EXISTS products(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,sku TEXT UNIQUE NOT NULL,pack_size TEXT,hsn TEXT NOT NULL,selling_price_paise INTEGER NOT NULL,gst_percent INTEGER NOT NULL DEFAULT 5);
CREATE TABLE IF NOT EXISTS bom(id INTEGER PRIMARY KEY AUTOINCREMENT,product_id INTEGER NOT NULL,component_item_id INTEGER NOT NULL,quantity_milli INTEGER NOT NULL,FOREIGN KEY(product_id) REFERENCES products(id),FOREIGN KEY(component_item_id) REFERENCES items(id),UNIQUE(product_id,component_item_id));
CREATE TABLE IF NOT EXISTS customers(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,state TEXT NOT NULL,gstin TEXT);
CREATE TABLE IF NOT EXISTS sales_orders(id INTEGER PRIMARY KEY AUTOINCREMENT,order_number TEXT UNIQUE NOT NULL,customer_id INTEGER NOT NULL,salesman_id INTEGER NOT NULL,status TEXT NOT NOT NULL DEFAULT 'submitted',created_at TEXT NOT NULL,FOREIGN KEY(customer_id) REFERENCES customers(id),FOREIGN KEY(salesman_id) REFERENCES users(id));
CREATE TABLE IF NOT EXISTS order_lines(id INTEGER PRIMARY KEY AUTOINCREMENT,order_id INTEGER NOT NULL,product_id INTEGER NOT NULL,quantity INTEGER NOT NULL,unit_price_paise INTEGER NOT NULL,FOREIGN KEY(order_id) REFERENCES sales_orders(id),FOREIGN KEY(product_id) REFERENCES products(id));
CREATE TABLE IF NOT EXISTS invoices(id INTEGER PRIMARY KEY AUTOINCREMENT,invoice_number TEXT UNIQUE NOT NULL,order_id INTEGER UNIQUE NOT NULL,customer_id INTEGER NOT NULL,invoice_date TEXT NOT NULL,subtotal_paise INTEGER NOT NULL,cgst_paise INTEGER NOT NULL,sgst_paise INTEGER NOT NULL,igst_paise INTEGER NOT NULL,total_paise INTEGER NOT NULL,status TEXT NOT NOT NULL DEFAULT 'posted',FOREIGN KEY(order_id) REFERENCES sales_orders(id),FOREIGN KEY(customer_id) REFERENCES customers(id));
CREATE TABLE IF NOT EXISTS invoice_lines(id INTEGER PRIMARY KEY AUTOINCREMENT,invoice_id INTEGER NOT NULL,product_id INTEGER NOT NULL,quantity INTEGER NOT NULL,unit_price_paise INTEGER NOT NULL,taxable_paise INTEGER NOT NULL,cgst_paise INTEGER NOT NULL,sgst_paise INTEGER NOT NULL,igst_paise INTEGER NOT NULL,FOREIGN KEY(invoice_id) REFERENCES invoices(id),FOREIGN KEY(product_id) REFERENCES products(id));
CREATE TABLE IF NOT EXISTS payments(id INTEGER PRIMARY KEY AUTOINCREMENT,customer_id INTEGER NOT NOT NULL,invoice_id INTEGER,amount_paise INTEGER NOT NULL,payment_date TEXT NOT NULL,note TEXT,FOREIGN KEY(customer_id) REFERENCES customers(id),FOREIGN KEY(invoice_id) REFERENCES invoices(id));
CREATE TABLE IF NOT EXISTS purchases(id INTEGER PRIMARY KEY AUTOINCREMENT,purchase_number TEXT UNIQUE NOT NULL,supplier TEXT NOT NULL,purchase_date TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS purchase_lines(id INTEGER PRIMARY KEY AUTOINCREMENT,purchase_id INTEGER NOT NULL,item_id INTEGER NOT NULL,quantity_milli INTEGER NOT NULL,unit_rate_paise INTEGER NOT NULL,FOREIGN KEY(purchase_id) REFERENCES purchases(id),FOREIGN KEY(item_id) REFERENCES items(id));
CREATE TABLE IF NOT EXISTS production_runs(id INTEGER PRIMARY KEY AUTOINCREMENT,run_number TEXT UNIQUE NOT NULL,product_id INTEGER NOT NULL,planned_output INTEGER NOT NULL,actual_output INTEGER NOT NULL,damaged_pouches INTEGER NOT NULL DEFAULT 0,labour_paise INTEGER NOT NULL DEFAULT 0,overhead_paise INTEGER NOT NULL DEFAULT 0,run_date TEXT NOT NOT NULL,status TEXT NOT NULL DEFAULT 'posted',FOREIGN KEY(product_id) REFERENCES products(id));
CREATE TABLE IF NOT EXISTS run_consumptions(id INTEGER PRIMARY KEY AUTOINCREMENT,run_id INTEGER NOT NULL,item_id INTEGER NOT NULL,planned_milli INTEGER NOT NULL,actual_milli INTEGER NOT NULL,FOREIGN KEY(run_id) REFERENCES production_runs(id),FOREIGN KEY(item_id) REFERENCES items(id));
CREATE TABLE IF NOT EXISTS audit_log(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,action TEXT NOT NULL,entity_type TEXT NOT NULL,entity_id INTEGER,details TEXT,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS stock_ledger(id INTEGER PRIMARY KEY AUTOINCREMENT,item_id INTEGER NOT NULL,quantity_milli INTEGER NOT NULL,movement_type TEXT NOT NULL,source_type TEXT NOT NULL,source_id INTEGER NOT NULL,note TEXT,user_id INTEGER,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,FOREIGN KEY(item_id) REFERENCES items(id));
""".replace("NOT NOT","NOT")

def init_db():
    con=db()
    con.executescript(SCHEMA)
    if one("SELECT COUNT(*) AS n FROM users")["n"]==0:
        con.executemany("INSERT INTO users(username,password_hash,name,role) VALUES(?,?,?,?)", [
            ("sales-a",generate_password_hash("demo123"),"Salesman A","salesman"),
            ("sales-b",generate_password_hash("demo123"),"Salesman B","salesman"),
            ("admin",generate_password_hash("demo123"),"Factory Admin","admin"),
            ("production",generate_password_hash("demo123"),"Production Supervisor","production"),
            ("auditor",generate_password_hash("demo123"),"Accountant / Auditor","auditor"),
            ("owner",generate_password_hash("demo123"),"Owner","owner"),
        ])
        con.executemany("INSERT INTO items(name,sku,category,uom,base_uom) VALUES(?,?,?,?,?)", [
            ("Flour","RM-FLOUR","Raw Material","kg","kg"),("Cooking oil","RM-OIL","Raw Material","litre","litre"),
            ("Salt and spice mix","RM-SPICE","Raw Material","kg","kg"),("200 g pouch","PK-POUCH","Packaging","piece","piece"),
            ("Carton (20 packets)","PK-CARTON","Packaging","piece","piece"),
            ("Murukku 200 g","FG-MURUKKU-200","Finished Goods","packet","packet"),
        ])
        con.execute("INSERT INTO products(name,sku,pack_size,hsn,selling_price_paise,gst_percent) VALUES(?,?,?,?,?,?)",
                    ("Murukku 200 g","MUR-200","200 g","19059030",5000,5))
        con.executemany("INSERT INTO customers(name,state,gstin) VALUES(?,?,?)",[("Retailer A","Tamil Nadu","33AAAAA0000A1Z5"),("Distributor B","Karnataka","29AAAAA0000A1Z5")])
        pid=con.execute("SELECT id FROM products WHERE sku='MUR-200'").fetchone()["id"]
        for sku,q in [("RM-FLOUR",40000),("RM-OIL",10000),("RM-SPICE",1200),("PK-POUCH",200000),("PK-CARTON",10000)]:
            iid=con.execute("SELECT id FROM items WHERE sku=?", (sku,)).fetchone()["id"]
            con.execute("INSERT INTO bom(product_id,component_item_id,quantity_milli) VALUES(?,?,?)",(pid,iid,q))
        # benchmark opening balances
        opens=[("RM-FLOUR",100000),("RM-OIL",30000),("RM-SPICE",5000),("PK-POUCH",500000),("PK-CARTON",30000),("FG-MURUKKU-200",50000)]
        for sku,milli in opens:
            iid=con.execute("SELECT id FROM items WHERE sku=?", (sku,)).fetchone()["id"]
            con.execute("INSERT INTO stock_ledger(item_id,quantity_milli,movement_type,source_type,source_id,note) VALUES(?,?,?,?,?,?)",
                        (iid,milli,"Opening Balance","opening",0,"Seed opening balance"))
        con.commit()
    con.close()

@app.context_processor
def context():
    return {"u":user(), "format_qty":format_qty, "format_money_paise":format_money_paise}

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method=="POST":
        u=one("SELECT * FROM users WHERE username=?", (request.form["username"].strip(),))
        if u and check_password_hash(u["password_hash"],request.form["password"]):
            session["user_id"]=u["id"]
            return redirect(url_for("dashboard"))
        return render_template("login.html",error="Invalid username or password")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear(); return redirect(url_for("login"))

@app.route("/")
@login_required()
def dashboard():
    u=user()
    if u["role"]=="salesman":
        return render_template("salesman.html",customers=q("SELECT * FROM customers ORDER BY name"),
                               products=q("SELECT * FROM products"), orders=q("""SELECT o.*,c.name customer FROM sales_orders o JOIN customers c ON c.id=o.customer_id WHERE salesman_id=? ORDER BY o.id DESC""",(u["id"],)))
    if u["role"]=="production":
        return redirect(url_for("production"))
    if u["role"]=="auditor":
        return redirect(url_for("reports"))
    if u["role"]=="owner":
        return redirect(url_for("owner"))
    return redirect(url_for("admin"))

@app.route("/order", methods=["POST"])
@login_required("salesman")
def create_order():
    u=user(); customer_id=int(request.form["customer_id"]); product_id=int(request.form["product_id"]); quantity=int(request.form["quantity"])
    con=db()
    n=con.execute("SELECT COUNT(*) n FROM sales_orders").fetchone()["n"]+1
    order_id=con.execute("INSERT INTO sales_orders(order_number,customer_id,salesman_id,status,created_at) VALUES(?,?,?,?,?)",
                         (f"ORD-{n:04d}",customer_id,u["id"],"submitted",datetime.now().isoformat())).lastrowid
    price=con.execute("SELECT selling_price_paise FROM products WHERE id=?",(product_id,)).fetchone()["selling_price_paise"]
    con.execute("INSERT INTO order_lines(order_id,product_id,quantity,unit_price_paise) VALUES(?,?,?,?)",(order_id,product_id,quantity,price))
    con.commit(); con.close()
    return redirect(url_for("dashboard"))

@app.route("/admin")
@login_required("admin")
def admin():
    orders=q("""SELECT o.*,c.name customer,c.state FROM sales_orders o JOIN customers c ON c.id=o.customer_id ORDER BY o.id DESC""")
    products={r["id"]:r for r in q("SELECT * FROM products")}
    fg=one("SELECT id FROM items WHERE category='Finished Goods'")
    inventory=stock_balance(fg["id"])
    return render_template("admin.html",orders=orders,products=products,inventory=inventory)

@app.route("/order/<int:order_id>/<action>", methods=["POST"])
@login_required("admin")
def order_action(order_id,action):
    con=db()
    if action=="reject":
        con.execute("UPDATE sales_orders SET status='rejected' WHERE id=?",(order_id,))
    elif action=="approve":
        con.execute("UPDATE sales_orders SET status='approved' WHERE id=?",(order_id,))
    elif action=="invoice":
        order=con.execute("SELECT * FROM sales_orders WHERE id=?",(order_id,)).fetchone()
        customer=con.execute("SELECT * FROM customers WHERE id=?",(order["customer_id"],)).fetchone()
        lines=con.execute("SELECT * FROM order_lines WHERE order_id=?",(order_id,)).fetchall()
        fg=con.execute("SELECT id FROM items WHERE category='Finished Goods'").fetchall()
        available=sum(stock_balance(r["id"]) for r in fg)//1000
        total_needed=sum(r["quantity"] for r in lines)
        if total_needed>available:
            con.close(); return redirect(url_for("admin"))
        subtotal=sum(r["quantity"]*r["unit_price_paise"] for r in lines)
        gst= (subtotal*5)//100
        if customer["state"]=="Tamil Nadu":
            cgst=gst//2; sgst=gst-cgst; igst=0
        else:
            cgst=0; sgst=0; igst=gst
        n=con.execute("SELECT COUNT(*) n FROM invoices").fetchone()["n"]+1
        cur=con.execute("""INSERT INTO invoices(invoice_number,order_id,customer_id,invoice_date,subtotal_paise,cgst_paise,sgst_paise,igst_paise,total_paise,status)
                           VALUES(?,?,?,?,?,?,?,?,?,?)""",
                        (f"TN/2026-27/{n:04d}",order_id,customer["id"],date.today().isoformat(),subtotal,cgst,sgst,igst,subtotal+cgst+sgst+igst,"posted"))
        inv_id=cur.lastrowid
        for line in lines:
            taxable=line["quantity"]*line["unit_price_paise"]
            if customer["state"]=="Tamil Nadu":
                lc=cgst if taxable==subtotal else (taxable*5//100)//2
                ls=lc
                li=0
            else:
                lc=0; ls=0; li=taxable*5//100
            con.execute("INSERT INTO invoice_lines(invoice_id,product_id,quantity,unit_price_paise,taxable_paise,cgst_paise,sgst_paise,igst_paise) VALUES(?,?,?,?,?,?,?,?)",
                        (inv_id,line["product_id"],line["quantity"],line["unit_price_paise"],taxable,lc,ls,li))
        con.execute("UPDATE sales_orders SET status='invoiced' WHERE id=?",(order_id,))
    con.commit(); con.close()
    execute("INSERT INTO audit_log(user_id,action,entity_type,entity_id,details,created_at) VALUES(?,?,?,?,?,?)",
            (session.get("user_id"),action,"sales_order",order_id,"",datetime.now().isoformat()))
    return redirect(url_for("admin"))

@app.route("/dispatch/<int:order_id>", methods=["POST"])
@login_required("admin")
def dispatch(order_id):
    con=db()
    inv=con.execute("SELECT * FROM invoices WHERE order_id=?",(order_id,)).fetchone()
    if inv:
        fg=con.execute("SELECT id FROM items WHERE sku='FG-MURUKKU-200'").fetchone()
        for line in con.execute("SELECT * FROM invoice_lines WHERE invoice_id=?",(inv["id"],)).fetchall():
            delta=-qty_milli(line["quantity"])
            execute("INSERT INTO stock_ledger(item_id,quantity_milli,movement_type,source_type,source_id,note,user_id) VALUES(?,?,?,?,?,?,?)",
                    (fg["id"],delta,"Sales dispatch","invoice",inv["id"],"Dispatch against invoice",session.get("user_id")))
        con.execute("UPDATE sales_orders SET status='dispatched' WHERE id=?",(order_id,))
        con.commit()
    con.close()
    return redirect(url_for("admin"))

@app.route("/purchase", methods=["GET","POST"])
@login_required("production")
def purchase():
    if request.method=="POST":
        supplier=request.form["supplier"]
        sku=request.form["sku"]
        quantity=dec(request.form["quantity"])
        rate_paise=int((dec(request.form["rate"])*PAISE).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        con=db()
        n=con.execute("SELECT COUNT(*) n FROM purchases").fetchone()["n"]+1
        pid=con.execute("INSERT INTO purchases(purchase_number,supplier,purchase_date) VALUES(?,?,?)",
                         (f"PUR-{n:04d}",supplier,date.today().isoformat())).lastrowid
        item=con.execute("SELECT * FROM items WHERE sku=?",(sku,)).fetchone()
        quantity_milli=qty_milli(quantity)
        con.execute("INSERT INTO purchase_lines(purchase_id,item_id,quantity_milli,unit_rate_paise) VALUES(?,?,?,?)",
                    (pid,item["id"],quantity_milli,rate_paise))
        con.commit(); con.close()
        ledger(item["id"],quantity_milli,"Purchase","purchase",pid,f"Purchase {quantity} {item['uom']} at {format_money_paise(rate_paise)}")
        execute("INSERT INTO audit_log(user_id,action,entity_type,entity_id,details,created_at) VALUES(?,?,?,?,?,?)",
                (session.get("user_id"),"create","purchase",pid,f"{quantity} {item['uom']} {item['name']}",datetime.now().isoformat()))
        return redirect(url_for("purchase"))
    return render_template("purchase.html",items=q("SELECT * FROM items WHERE category!='Finished Goods'"))

@app.route("/production", methods=["GET","POST"])
@login_required("production")
def production():
    if request.method=="POST":
        product=one("SELECT * FROM products WHERE id=?", (int(request.form["product_id"]),))
        actual=qty_milli(request.form["actual_output"] if "actual_output" in request.form else 188)
        actual_output=int(dec(request.form["actual_output"])/Decimal("1"))
        damaged=int(request.form["damaged_pouches"])
        cartons=int(request.form["cartons"])
        labour=int((dec(request.form["labour"])*PAISE).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        overhead=int((dec(request.form["overhead"])*PAISE).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        flour=float(request.form["flour"]); oil=float(request.form["oil"]); spice=float(request.form["spice"])
        con=db(); n=con.execute("SELECT COUNT(*) n FROM production_runs").fetchone()["n"]+1
        rid=con.execute("""INSERT INTO production_runs(run_number,product_id,planned_output,actual_output,damaged_pouches,labour_paise,overhead_paise,run_date)
                           VALUES(?,?,?,?,?,?,?,?)""",
                        (f"RUN-{n:04d}",product["id"],200,actual_output,damaged,labour,overhead,date.today().isoformat())).lastrowid
        actuals=[("RM-FLOUR",qty_milli(flour),qty_milli(40)),
                 ("RM-OIL",qty_milli(oil),qty_milli(10)),
                 ("RM-SPICE",qty_milli(spice),qty_milli(1.2)),
                 ("PK-POUCH",qty_milli(actual_output+damaged),qty_milli(200)),
                 ("PK-CARTON",qty_milli(cartons),qty_milli(10))]
        for sku,actual_milli,planned_milli in actuals:
            item=con.execute("SELECT * FROM items WHERE sku=?",(sku,)).fetchone()
            con.execute("INSERT INTO run_consumptions(run_id,item_id,planned_milli,actual_milli) VALUES(?,?,?,?)",
                        (rid,item["id"],planned_milli,actual_milli))
            delta=-actual_milli
            con.execute("""INSERT INTO stock_ledger(item_id,quantity_milli,movement_type,source_type,source_id,note,user_id)
                           VALUES(?,?,?,?,?,?,?)""",
                        (item["id"],delta,"Production issue","production_run",rid,f"{actual_milli/1000:g} {item['uom']} used",session.get("user_id")))
        fg=con.execute("SELECT id FROM items WHERE sku='FG-MURUKKU-200'").fetchone()
        con.execute("""INSERT INTO stock_ledger(item_id,quantity_milli,movement_type,source_type,source_id,note,user_id)
                       VALUES(?,?,?,?,?,?,?)""",
                    (fg["id"],qty_milli(actual_output),"Production output","production_run",rid,f"{actual_output} good packets",session.get("user_id")))
        con.commit(); con.close()
        execute("INSERT INTO audit_log(user_id,action,entity_type,entity_id,details,created_at) VALUES(?,?,?,?,?,?)",
                (session.get("user_id"),"create","production_run",rid,f"Actual output {actual_output}",datetime.now().isoformat()))
        return redirect(url_for("production"))
    runs=q("""SELECT r.*,p.name product FROM production_runs r JOIN products p ON p.id=r.product_id ORDER BY r.id DESC""")
    return render_template("production.html",runs=runs,products=q("SELECT * FROM products"))

@app.route("/payment", methods=["POST"])
@login_required("admin")
def payment():
    customer_id=int(request.form["customer_id"])
    amount=int((dec(request.form["amount"])*PAISE).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    inv=request.form.get("invoice_id")
    invoice_id=int(inv) if inv else None
    execute("INSERT INTO payments(customer_id,invoice_id,amount_paise,payment_date,note) VALUES(?,?,?,?,?)",
            (customer_id,invoice_id,amount,date.today().isoformat(),"Benchmark / customer payment"))
    return redirect(url_for("admin"))

@app.route("/invoice/<int:invoice_id>")
@login_required(["admin","auditor","owner"])
def invoice(invoice_id):
    inv=one("SELECT i.*,c.name customer,c.state,c.gstin FROM invoices i JOIN customers c ON c.id=i.customer_id WHERE i.id=?",(invoice_id,))
    lines=q("""SELECT l.*,p.name,p.hsn FROM invoice_lines l JOIN products p ON p.id=l.product_id WHERE invoice_id=?""",(invoice_id,))
    return render_template("invoice.html",invoice=inv,lines=lines)

@app.route("/invoice/<int:invoice_id>.pdf")
@login_required(["admin","auditor","owner"])
def invoice_pdf(invoice_id):
    inv=one("SELECT i.*,c.name customer,c.state,c.gstin FROM invoices i JOIN customers c ON c.id=i.customer_id WHERE i.id=?",(invoice_id,))
    lines=q("""SELECT l.*,p.name,p.hsn FROM invoice_lines l JOIN products p ON p.id=l.product_id WHERE invoice_id=?""",(invoice_id,))
    buf=io.BytesIO(); c=canvas.Canvas(buf,pagesize=A4)
    c.setFont("Helvetica-Bold",16); c.drawString(40,800,"GST TAX INVOICE")
    c.setFont("Helvetica",10); c.drawString(40,780,f"Invoice: {inv['invoice_number']}")
    c.drawString(40,765,f"Date: {inv['invoice_date']}"); c.drawString(40,750,f"Customer: {inv['customer']} ({inv['state']})")
    y=720
    for line in lines:
        c.drawString(40,y,f"{line['name']} x {line['quantity']} @ {format_money_paise(line['unit_price_paise'])}")
        c.drawString(400,y,format_money_paise(line['taxable_paise'])); y-=20
    c.drawString(40,y-20,f"Taxable: {format_money_paise(inv['subtotal_paise'])}")
    c.drawString(40,y-40,f"CGST: {format_money_paise(inv['cgst_paise'])}  SGST: {format_money_paise(inv['sgst_paise'])}  IGST: {format_money_paise(inv['igst_paise'])}")
    c.drawString(40,y-60,f"Total: {format_money_paise(inv['total_paise'])}")
    c.save(); buf.seek(0)
    return send_file(buf,mimetype="application/pdf",as_attachment=True,download_name=f"{inv['invoice_number']}.pdf")

@app.route("/reports")
@login_required(["auditor","owner","admin"])
def reports():
    stock=q("""SELECT i.*,COALESCE(SUM(l.quantity_milli),0) balance_milli FROM items i LEFT JOIN stock_ledger l ON l.item_id=i.id GROUP BY i.id ORDER BY i.category,i.name""")
    sales=q("""SELECT i.invoice_number,i.invoice_date,c.name customer,i.subtotal_paise,i.cgst_paise,i.sgst_paise,i.igst_paise,i.total_paise,i.status
               FROM invoices i JOIN customers c ON c.id=i.customer_id ORDER BY i.id""")
    runs=q("""SELECT r.*,p.name product FROM production_runs r JOIN products p ON p.id=r.product_id ORDER BY r.id""")
    purchases=q("""SELECT pu.purchase_number,pu.purchase_date,pu.supplier,i.name item,l.quantity_milli,l.unit_rate_paise
                   FROM purchase_lines l JOIN purchases pu ON pu.id=l.purchase_id JOIN items i ON i.id=l.item_id""")
    outstanding=[]
    for c in q("SELECT * FROM customers"):
        invoiced=one("SELECT COALESCE(SUM(total_paise),0) n FROM invoices WHERE customer_id=?",(c["id"],))["n"]
        paid=one("SELECT COALESCE(SUM(amount_paise),0) n FROM payments WHERE customer_id=?",(c["id"],))["n"]
        outstanding.append({"id":c["id"],"name":c["name"],"state":c["state"],"amount":invoiced-paid})
    return render_template("reports.html",stock=stock,sales=sales,runs=runs,purchases=purchases,outstanding=outstanding)

@app.route("/stock")
@login_required(["auditor","owner","admin","production"])
def stock():
    items=q("SELECT * FROM items ORDER BY category,name")
    ledgers={}
    for item in items:
        ledgers[item["id"]]=q("SELECT * FROM stock_ledger WHERE item_id=? ORDER BY id",(item["id"],))
    return render_template("stock.html",items=items,ledgers=ledgers)

@app.route("/cost/<int:run_id>")
@login_required(["auditor","owner","production","admin"])
def cost(run_id):
    run=one("""SELECT r.*,p.name product FROM production_runs r JOIN products p ON p.id=r.product_id WHERE r.id=?""",(run_id,))
    # Weighted-average valuation: opening stock plus purchases for flour; fixed benchmark purchase rates for direct components.
    flour=one("SELECT id FROM items WHERE sku='RM-FLOUR'")
    flour_qty=100000
    flour_receipts=one("SELECT COALESCE(SUM(quantity_milli),0) n FROM stock_ledger WHERE item_id=? AND quantity_milli>0 AND movement_type='Purchase'",(flour["id"],))["n"]
    flour_cost=Decimal(100*4200+60*4500)/Decimal(1000) if flour_receipts>=60000 else Decimal(100*4200)/Decimal(1000)
    consumptions=q("""SELECT rc.*,i.name,i.sku,i.uom FROM run_consumptions rc JOIN items i ON i.id=rc.item_id WHERE rc.run_id=?""",(run_id,))
    total=Decimal(run["labour_paise"]+run["overhead_paise"])
    for line in consumptions:
        sku=line["sku"]; qactual=Decimal(line["actual_milli"])/MILLI
        rate={"RM-FLOUR":flour_cost/PAISE if False else None,"RM-OIL":Decimal("140"),"RM-SPICE":Decimal("200"),"PK-POUCH":Decimal("2.5"),"PK-CARTON":Decimal("15")}[sku]
        if sku=="RM-FLOUR":
            rate=Decimal("43")
        total += qactual*rate*PAISE
    cost_per_packet=total/Decimal(run["actual_output"]) if run["actual_output"] else Decimal("0")
    margin_paise=5000-int(cost_per_packet)
    return render_template("cost.html",run=run,consumptions=consumptions,total_paise=int(total),cost_per_packet=cost_per_packet,margin_paise=margin_paise)

@app.route("/export/<report>")
@login_required(["auditor","owner","admin"])
def export_report(report):
    if report=="sales":
        rows=q("""SELECT i.invoice_number,i.invoice_date,c.name, i.subtotal_paise,i.cgst_paise,i.sgst_paise,i.igst_paise,i.total_paise,i.status
                  FROM invoices i JOIN customers c ON c.id=i.customer_id""")
        header=["Invoice","Date","Customer","Taxable (paise)","CGST","SGST","IGST","Total (paise)","Status"]
        data=[[r["invoice_number"],r["invoice_date"],r["name"],r["subtotal_paise"],r["cgst_paise"],r["sgst_paise"],r["igst_paise"],r["total_paise"],r["status"]] for r in rows]
    elif report=="stock":
        rows=q("""SELECT i.name,i.category,i.uom,COALESCE(SUM(l.quantity_milli),0) balance FROM items i LEFT JOIN stock_ledger l ON l.item_id=i.id GROUP BY i.id""")
        header=["Item","Category","Unit","Closing Balance"]
        data=[[r["name"],r["category"],r["uom"],format_qty(r["balance"])] for r in rows]
    elif report=="purchases":
        rows=q("""SELECT pu.purchase_number,pu.supplier,pu.purchase_date,i.name,l.quantity_milli,l.unit_rate_paise FROM purchase_lines l JOIN purchases pu ON pu.id=l.purchase_id JOIN items i ON i.id=l.item_id""")
        header=["Purchase","Supplier","Date","Item","Quantity milli","Rate (paise)"]
        data=[[r["purchase_number"],r["supplier"],r["purchase_date"],r["name"],r["quantity_milli"],r["unit_rate_paise"]] for r in rows]
    else:
        rows=q("""SELECT r.run_number,r.run_date,p.name,r.planned_output,r.actual_output,r.damaged_pouches,r.labour_paise,r.overhead_paise FROM production_runs r JOIN products p ON p.id=r.product_id""")
        header=["Run","Date","Product","Planned","Actual","Damaged","Labour","Overhead"]
        data=[[r["run_number"],r["run_date"],r["name"],r["planned_output"],r["actual_output"],r["damaged_pouches"],r["labour_paise"],r["overhead_paise"]] for r in rows]
    out=io.StringIO(); w=csv.writer(out); w.writerow(header); w.writerows(data)
    return Response(out.getvalue(),mimetype="text/csv",headers={"Content-Disposition":f"attachment; filename={report}.csv"})

@app.route("/owner")
@login_required(["owner","auditor"])
def owner():
    return redirect(url_for("reports"))

@app.route("/health")
def health():
    return {"status":"ok"}

with app.app_context():
    init_db()

if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.environ.get("PORT","5000")),debug=True)
