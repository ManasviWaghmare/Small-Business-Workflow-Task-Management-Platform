"""Small Business Workflow & Task Management Platform
Focus: micro-grocery retail workflow engine (mom's grocery store use-case)."""
import os
import re
import sqlite3
from collections import Counter
from datetime import date, datetime, timedelta
from flask import Flask, g, redirect, render_template, request, url_for, jsonify, session, send_from_directory
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ON_VERCEL = bool(os.getenv("VERCEL"))
if ON_VERCEL:
    DATABASE = os.getenv("DATABASE_PATH", "/tmp/workflow.db")
elif os.getenv("K_SERVICE") or os.getenv("PORT"):
    DATABASE = os.getenv("DATABASE_PATH", "/tmp/workflow.db")
else:
    DATABASE = os.getenv("DATABASE_PATH", os.path.join(BASE_DIR, "workflow.db"))

PRIORITIES = ["Low", "Medium", "High", "Urgent"]
STATUSES = ["Todo", "In Progress", "Done"]
PO_STATUSES = ["PO Raised", "Shipped", "Received & Verified"]
ORDER_STATUSES = ["New", "Picking", "Packed", "Delivered"]
# Smart bundling rules: trigger keyword -> (suggest item, offer text)
BUNDLE_RULES = [
    ("tea", "Sugar + Milk", "10% off combo with tea"),
    ("atta", "Mustard Oil 1L", "Rs 20 off oil with atta"),
    ("poha", "Jaggery 500g", "Free sample: jaggery with poha"),
    ("milk", "Bread", "Rs 10 off bread with milk"),
    ("rice", "Dal 1kg", "Rs 15 off dal with rice"),
    ("maggie", "Masala combo", "Buy 4 get 1 free topper"),
]

CHECKLIST_TEMPLATES = {
    "Morning Opening": ["Open shutters & switch on lights", "Clean floors & counters", "Check dairy shelf stock", "Switch on deep freezers & note temp", "Keep change box ready at counter"],
    "Freezer Temperature Log": ["Freezer 1 temp (ideal -18C)", "Freezer 2 temp (ideal -18C)", "Dairy fridge temp (ideal 4C)", "Report any frost/door issue"],
    "End-of-Day Cash": ["Count cash drawer", "Match UPI/card settlements", "Note pending customer udhaar", "Lock shutters & switch off extra lights"],
}

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev-key-change-me")
app.config["MAX_CONTENT_LENGTH"] = 4 * 1024 * 1024

UPLOAD_DIR = os.getenv("UPLOAD_DIR", os.path.join(BASE_DIR, "static", "uploads"))
os.makedirs(UPLOAD_DIR, exist_ok=True)
ALLOWED_EXT = {"png", "jpg", "jpeg", "webp"}


def save_photo(f):
    """Save an uploaded list/bill photo, return its filename (or '')."""
    if not f or not getattr(f, "filename", ""):
        return ""
    ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
    if ext not in ALLOWED_EXT:
        return ""
    name = datetime.now().strftime("%Y%m%d%H%M%S%f") + "_" + secure_filename(f.filename)[-30:]
    try:
        f.save(os.path.join(UPLOAD_DIR, name))
    except OSError:
        return ""
    return name

PUBLIC_PATHS = ("/login", "/healthz")


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(DATABASE)
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS employees (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL, role TEXT DEFAULT '', phone TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
            description TEXT DEFAULT '', employee_id INTEGER REFERENCES employees(id) ON DELETE SET NULL,
            priority TEXT DEFAULT 'Medium', status TEXT DEFAULT 'Todo',
            deadline TEXT DEFAULT '', created_at TEXT DEFAULT CURRENT_TIMESTAMP, completed_at TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
            category TEXT DEFAULT '', stock_qty REAL DEFAULT 0, unit TEXT DEFAULT 'pcs',
            low_threshold REAL DEFAULT 5, expiry_date TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS vendors (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
            category TEXT DEFAULT '', phone TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS purchase_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT, vendor_id INTEGER REFERENCES vendors(id) ON DELETE SET NULL,
            item TEXT NOT NULL, qty TEXT DEFAULT '', status TEXT DEFAULT 'PO Raised',
            due_date TEXT DEFAULT '', created_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS customer_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT, customer_name TEXT DEFAULT 'Walk-in',
            phone TEXT DEFAULT '', items_text TEXT DEFAULT '', status TEXT DEFAULT 'New',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS checklist_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT, template TEXT NOT NULL,
            item TEXT NOT NULL, done INTEGER DEFAULT 0, day TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS customers (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
            phone TEXT DEFAULT '', address TEXT DEFAULT '',
            khata_balance REAL DEFAULT 0, last_order TEXT DEFAULT '',
            total_orders INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS promotions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
            trigger_item TEXT DEFAULT '', suggest_item TEXT DEFAULT '',
            offer TEXT DEFAULT '', active INTEGER DEFAULT 1);
        CREATE TABLE IF NOT EXISTS flash_sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT, item TEXT NOT NULL,
            expiry TEXT DEFAULT '', discount TEXT DEFAULT '30% OFF',
            message TEXT DEFAULT '', active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS group_buys (
            id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
            item TEXT NOT NULL, target_qty TEXT DEFAULT '',
            joined_qty REAL DEFAULT 0, price TEXT DEFAULT '',
            status TEXT DEFAULT 'Open', created_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS group_members (
            id INTEGER PRIMARY KEY AUTOINCREMENT, group_id INTEGER REFERENCES group_buys(id) ON DELETE CASCADE,
            customer_name TEXT DEFAULT '', qty TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS bills (
            id INTEGER PRIMARY KEY AUTOINCREMENT, customer_name TEXT DEFAULT 'Walk-in',
            phone TEXT DEFAULT '', subtotal REAL DEFAULT 0, discount REAL DEFAULT 0,
            total REAL DEFAULT 0, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS bill_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT, bill_id INTEGER REFERENCES bills(id) ON DELETE CASCADE,
            item_name TEXT NOT NULL, qty REAL DEFAULT 1, unit TEXT DEFAULT 'pcs',
            price REAL DEFAULT 0, amount REAL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS wholesaler_bills (
            id INTEGER PRIMARY KEY AUTOINCREMENT, vendor_id INTEGER REFERENCES vendors(id) ON DELETE SET NULL,
            bill_no TEXT DEFAULT '', bill_date TEXT DEFAULT '', subtotal REAL DEFAULT 0,
            total REAL DEFAULT 0, photo TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS wholesaler_bill_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT, bill_id INTEGER REFERENCES wholesaler_bills(id) ON DELETE CASCADE,
            item_name TEXT NOT NULL, qty REAL DEFAULT 0, unit TEXT DEFAULT 'pcs',
            rate REAL DEFAULT 0, amount REAL DEFAULT 0);
        """
    )
    db.commit()
    # lightweight migration for DBs created before growth layer
    for stmt in [
        "ALTER TABLE customer_orders ADD COLUMN customer_id INTEGER",
        "ALTER TABLE inventory ADD COLUMN price REAL DEFAULT 0",
        "ALTER TABLE customer_orders ADD COLUMN photo TEXT DEFAULT ''",
        "ALTER TABLE bills ADD COLUMN photo TEXT DEFAULT ''",
    ]:
        try: db.execute(stmt)
        except sqlite3.OperationalError: pass
    db.commit()
    cur = db.execute("SELECT COUNT(*) c FROM employees")
    if cur.fetchone()[0] == 0:
        demo_emp = [("Aarav Sharma", "Shop Manager", "98765 43210"),
                    ("Priya Patel", "Sales Staff", "98220 11223"),
                    ("Rohan Deshmukh", "Delivery", "97654 32109")]
        db.executemany("INSERT INTO employees (name, role, phone) VALUES (?,?,?)", demo_emp)
        db.commit()
        emps = db.execute("SELECT id FROM employees").fetchall()
        today = date.today()
        def d(o): return (today + timedelta(days=o)).isoformat()
        demo_tasks = [
            ("Restock dairy shelf", "Milk, curd, paneer running low. Call supplier.", emps[0][0], "High", "In Progress", d(1), ""),
            ("Pay electricity bill", "Shop bill due, ~Rs 2400.", emps[0][0], "Urgent", "Todo", d(-1), ""),
            ("Deliver order #142 to Baner", "2kg atta, oil, sugar. COD Rs 1450.", emps[2][0], "Medium", "Todo", d(0), ""),
            ("Festival discount banner", "Diwali offer poster.", emps[1][0], "Low", "Done", d(-3), d(-2)),
            ("Follow up with supplier", "Rice bag wholesale rate.", emps[1][0], "Medium", "Todo", d(2), ""),
        ]
        db.executemany("INSERT INTO tasks (title, description, employee_id, priority, status, deadline, completed_at) VALUES (?,?,?,?,?,?,?)", demo_tasks)
        db.commit()
    if db.execute("SELECT COUNT(*) c FROM vendors").fetchone()[0] == 0:
        db.executemany("INSERT INTO vendors (name, category, phone) VALUES (?,?,?)", [
            ("Shree Dairy Suppliers", "Dairy", "98100 11111"),
            ("Fresh Veg Mandai", "Vegetables", "98200 22222"),
            ("Agrawal Kirana Wholesale", "Dry goods", "98300 33333")])
        db.commit()
    if db.execute("SELECT COUNT(*) c FROM inventory").fetchone()[0] == 0:
        today = date.today()
        db.executemany("INSERT INTO inventory (name, category, stock_qty, unit, low_threshold, expiry_date, price) VALUES (?,?,?,?,?,?,?)", [
            ("Amul Milk 500ml", "Dairy", 8, "packets", 20, (today + timedelta(days=2)).isoformat(), 27),
            ("Curd Cup 400g", "Dairy", 25, "cups", 15, (today + timedelta(days=3)).isoformat(), 35),
            ("Wheat Atta 5kg", "Dry goods", 4, "bags", 10, "", 240),
            ("Basmati Rice 1kg", "Dry goods", 30, "packets", 10, "", 85),
            ("Tomato", "Vegetables", 6, "kg", 8, (today + timedelta(days=1)).isoformat(), 40)])
        db.commit()
    if db.execute("SELECT COUNT(*) c FROM purchase_orders").fetchone()[0] == 0:
        v = db.execute("SELECT id FROM vendors LIMIT 1").fetchone()[0]
        db.execute("INSERT INTO purchase_orders (vendor_id, item, qty, status, due_date) VALUES (?,?,?,?,?)",
                   (v, "Amul Milk 500ml x 40 packets", "40 packets", "PO Raised", date.today().isoformat()))
        db.commit()
    if db.execute("SELECT COUNT(*) c FROM customer_orders").fetchone()[0] == 0:
        db.execute("INSERT INTO customer_orders (customer_name, phone, items_text, status) VALUES (?,?,?,?)",
                   ("Sunita Joshi", "97650 12345", "2kg atta\n1L oil\n1kg sugar", "New"))
        db.commit()
    if db.execute("SELECT COUNT(*) c FROM customers").fetchone()[0] == 0:
        db.executemany("INSERT INTO customers (name, phone, address, khata_balance, last_order, total_orders) VALUES (?,?,?,?,?,?)", [
            ("Sunita Joshi", "9765012345", "Baner Rd", 450, (date.today() - timedelta(days=20)).isoformat(), 6),
            ("Ramesh Gupta", "9822011223", "Shop Lane", 0, date.today().isoformat(), 12)])
        db.commit()
    if db.execute("SELECT COUNT(*) c FROM promotions").fetchone()[0] == 0:
        db.executemany("INSERT INTO promotions (title, trigger_item, suggest_item, offer, active) VALUES (?,?,?,?,1)", [
            ("Chai combo", "tea", "Sugar + Milk", "10% off combo"),
            ("Atta + Oil", "atta", "Mustard Oil 1L", "Rs 20 off oil")])
        db.commit()
    if db.execute("SELECT COUNT(*) c FROM group_buys").fetchone()[0] == 0:
        db.execute("INSERT INTO group_buys (title, item, target_qty, joined_qty, price, status) VALUES (?,?,?,?,?,?)",
                   ("Society Rice Deal", "Basmati 25kg sack", "10 sacks", 4, "Rs 1450/sack (save Rs 150)", "Open"))
        db.commit()
    if db.execute("SELECT COUNT(*) c FROM users").fetchone()[0] == 0:
        db.execute("INSERT INTO users (username, password_hash) VALUES (?,?)",
                   ("admin", generate_password_hash("admin123")))
        db.commit()
    db.close()


def today_str(): return date.today().isoformat()


def is_overdue(t): return t["status"] != "Done" and t["deadline"] and t["deadline"] < today_str()


def run_stock_alerts(db):
    """Auto-create High/Urgent tasks for low stock + near expiry. Returns new alerts count."""
    today = date.today()
    created = 0
    for r in db.execute("SELECT * FROM inventory").fetchall():
        if r["stock_qty"] is not None and r["low_threshold"] is not None and r["stock_qty"] <= r["low_threshold"]:
            title = f"Restock: {r['name']} (stock {r['stock_qty']}{r['unit']})"
            ex = db.execute("SELECT id FROM tasks WHERE title=? AND status!='Done'", (title,)).fetchone()
            if not ex:
                db.execute("INSERT INTO tasks (title, description, priority, status, deadline) VALUES (?,?,?,?,?)",
                           (title, f"Low stock alert: only {r['stock_qty']} {r['unit']} left (min {r['low_threshold']}). Raise PO to vendor.", "Urgent", "Todo", today_str()))
                created += 1
        if r["expiry_date"]:
            try: exp = date.fromisoformat(r["expiry_date"])
            except ValueError: continue
            days = (exp - today).days
            if 0 <= days <= 7:
                title = f"Expiry alert: {r['name']} expires {r['expiry_date']}"
                ex = db.execute("SELECT id FROM tasks WHERE title=? AND status!='Done'", (title,)).fetchone()
                if not ex:
                    db.execute("INSERT INTO tasks (title, description, priority, status, deadline) VALUES (?,?,?,?,?)",
                               (title, f"Perishable item expiring in {days} day(s). Push sale / return to vendor.", "High", "Todo", r["expiry_date"]))
                    created += 1
    if created: db.commit()
    return created


def parse_whatsapp(text):
    """Parse a pasted WhatsApp customer/vendor message into a structured order."""
    lines = [l.strip(" -•*") for l in text.strip().splitlines() if l.strip()]
    phone = ""
    m = re.search(r"(\+?91[\s-]?)?([6-9]\d{4}[\s-]?\d{5})", text)
    if m: phone = re.sub(r"\D", "", m.group(0))[-10:]
    name = "WhatsApp Customer"
    if lines:
        first = re.sub(r"(hi+|hello|namaste|order|please|plz)", "", lines[0], flags=re.I).strip(" ,:-")
        if first and len(first) < 40 and not re.search(r"\d+\s*(kg|g|ml|l|pcs|packet|x)", first, re.I):
            name = first.title()[:40]
    items = [l for l in lines if re.search(r"\d", l) or re.search(r"(kg|g|ml|ltr|packet|pcs|atta|oil|sugar|milk|rice|dal)", l, re.I)]
    if not items: items = lines[1:] if len(lines) > 1 else lines
    return {"customer_name": name, "phone": phone, "items_text": "\n".join(items[:20])}


def forecast_restock(db):
    """Simple demand forecast: most-mentioned order items + current low stock."""
    rows = db.execute("SELECT items_text FROM customer_orders ORDER BY id DESC LIMIT 30").fetchall()
    words = []
    for r in rows:
        for line in (r["items_text"] or "").splitlines():
            w = re.sub(r"\d+[.\d]*\s*(kg|g|ml|ltr?|packets?|pcs?|x)?", "", line, flags=re.I).strip().lower()
            w = re.sub(r"[^a-z ]", "", w).strip()
            if len(w) > 2: words.append(w[:30])
    top = Counter(words).most_common(5)
    low = db.execute("SELECT name, stock_qty, unit FROM inventory WHERE stock_qty <= low_threshold ORDER BY stock_qty ASC LIMIT 5").fetchall()
    return {"top_items": top, "low_stock": low}


def upsert_customer(db, name, phone):
    """Create/update customer on every order; track visits for retention."""
    if not name: return
    row = None
    if phone:
        row = db.execute("SELECT * FROM customers WHERE phone=?", (phone,)).fetchone()
    if not row:
        row = db.execute("SELECT * FROM customers WHERE name=?", (name,)).fetchone()
    if row:
        db.execute("UPDATE customers SET last_order=?, total_orders=total_orders+1 WHERE id=?", (today_str(), row["id"]))
    else:
        db.execute("INSERT INTO customers (name, phone, last_order, total_orders) VALUES (?,?,?,1)", (name, phone or "", today_str()))


def bundle_suggestions(db, items_text):
    """Match order text against active promos + default rules."""
    text = (items_text or "").lower()
    out = []
    for p in db.execute("SELECT * FROM promotions WHERE active=1").fetchall():
        if p["trigger_item"] and p["trigger_item"].lower() in text:
            out.append({"title": p["title"], "suggest": p["suggest_item"], "offer": p["offer"]})
    for trig, sug, offer in BUNDLE_RULES:
        if trig in text and not any(o["suggest"] == sug for o in out):
            out.append({"title": f"{trig.title()} combo", "suggest": sug, "offer": offer})
    return out[:3]


def run_flash_sales(db):
    """Expiry ≤2 days -> flash sale + task + WhatsApp broadcast draft."""
    today = date.today(); created = 0
    for r in db.execute("SELECT * FROM inventory WHERE expiry_date!=''").fetchall():
        try: exp = date.fromisoformat(r["expiry_date"])
        except ValueError: continue
        days = (exp - today).days
        if 0 <= days <= 2:
            ex = db.execute("SELECT id FROM flash_sales WHERE item=? AND active=1", (r["name"],)).fetchone()
            if not ex:
                msg = f"Flash Sale: 30% OFF on {r['name']} today only! Expires {r['expiry_date']}. Visit us / reply to order. - Your neighbourhood store"
                db.execute("INSERT INTO flash_sales (item, expiry, discount, message, active) VALUES (?,?,?,?,1)",
                           (r["name"], r["expiry_date"], "30% OFF", msg))
                t = f"Flash sale push: {r['name']}"
                if not db.execute("SELECT id FROM tasks WHERE title=? AND status!='Done'", (t,)).fetchone():
                    db.execute("INSERT INTO tasks (title, description, priority, status, deadline) VALUES (?,?,?,?,?)",
                               (t, msg + " Share in customer WhatsApp groups.", "High", "Todo", today_str()))
                created += 1
    if created: db.commit()
    return created


def retention_due(db, days=15):
    """Regulars with no order in `days` -> retention task candidates."""
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    return db.execute("SELECT * FROM customers WHERE (last_order='' OR last_order<?) ORDER BY last_order ASC LIMIT 10", (cutoff,)).fetchall()


def run_retention_tasks(db, days=15):
    n = 0
    for c in retention_due(db, days):
        t = f"Retention: check in with {c['name']}"
        if not db.execute("SELECT id FROM tasks WHERE title=? AND status!='Done'", (t,)).fetchone():
            db.execute("INSERT INTO tasks (title, description, priority, status, deadline) VALUES (?,?,?,?,?)",
                       (t, f"No order since {c['last_order'] or 'long ago'}. Phone: {c['phone']}. Send friendly WhatsApp + loyalty offer.", "Medium", "Todo", today_str()))
            n += 1
    if n: db.commit()
    return n


@app.route("/healthz")
def healthz(): return "ok", 200


@app.route("/uploads/<path:name>")
def uploaded(name):
    return send_from_directory(UPLOAD_DIR, name)


@app.route("/")
def dashboard():
    db = get_db()
    run_stock_alerts(db); run_flash_sales(db); run_retention_tasks(db)
    tasks = db.execute("SELECT t.*, e.name AS employee_name FROM tasks t LEFT JOIN employees e ON t.employee_id=e.id ORDER BY t.deadline ASC").fetchall()
    employees = db.execute("SELECT * FROM employees ORDER BY name").fetchall()
    total = len(tasks); done = sum(1 for t in tasks if t["status"] == "Done"); pending = total - done
    overdue = [t for t in tasks if is_overdue(t)]
    due_today = [t for t in tasks if t["deadline"] == today_str() and t["status"] != "Done"]
    completion = round(done / total * 100) if total else 0
    by_priority = {p: sum(1 for t in tasks if t["priority"] == p and t["status"] != "Done") for p in PRIORITIES}
    by_status = {s: sum(1 for t in tasks if t["status"] == s) for s in STATUSES}
    per_employee = []
    for e in employees:
        et = [t for t in tasks if t["employee_id"] == e["id"]]
        edone = sum(1 for t in et if t["status"] == "Done")
        eover = sum(1 for t in et if is_overdue(t))
        per_employee.append({"id": e["id"], "name": e["name"], "role": e["role"], "total": len(et),
                             "done": edone, "pending": len(et) - edone, "overdue": eover,
                             "rate": round(edone / len(et) * 100) if et else 0})
    recent = db.execute("SELECT t.*, e.name AS employee_name FROM tasks t LEFT JOIN employees e ON t.employee_id=e.id ORDER BY t.id DESC LIMIT 8").fetchall()
    upcoming = db.execute("SELECT t.*, e.name AS employee_name FROM tasks t LEFT JOIN employees e ON t.employee_id=e.id WHERE t.status!='Done' ORDER BY CASE WHEN t.deadline='' THEN 1 ELSE 0 END, t.deadline ASC LIMIT 5").fetchall()
    low_stock = db.execute("SELECT * FROM inventory WHERE stock_qty <= low_threshold ORDER BY stock_qty ASC").fetchall()
    expiring = db.execute("SELECT * FROM inventory WHERE expiry_date!='' ORDER BY expiry_date ASC LIMIT 5").fetchall()
    forecast = forecast_restock(db)
    new_orders = db.execute("SELECT COUNT(*) c FROM customer_orders WHERE status!='Delivered'").fetchone()["c"]
    open_pos = db.execute("SELECT COUNT(*) c FROM purchase_orders WHERE status!='Received & Verified'").fetchone()["c"]
    cl_done = db.execute("SELECT COUNT(*) c FROM checklist_items WHERE day=? AND done=1", (today_str(),)).fetchone()["c"]
    cl_total = db.execute("SELECT COUNT(*) c FROM checklist_items WHERE day=?", (today_str(),)).fetchone()["c"]
    flash = db.execute("SELECT * FROM flash_sales WHERE active=1 ORDER BY id DESC LIMIT 3").fetchall()
    reten = retention_due(db)
    groups = db.execute("SELECT * FROM group_buys WHERE status='Open' ORDER BY id DESC").fetchall()
    khata_total = db.execute("SELECT COALESCE(SUM(khata_balance),0) s FROM customers").fetchone()["s"]
    ts = db.execute("SELECT COALESCE(SUM(total),0) s, COUNT(*) c FROM bills WHERE date(created_at)=?", (today_str(),)).fetchone()
    today_sales, today_bills = ts["s"], ts["c"]
    return render_template("dashboard.html", total=total, done=done, pending=pending, overdue=overdue,
                           due_today=due_today, completion=completion, by_priority=by_priority, by_status=by_status,
                           per_employee=per_employee, recent=recent, today=today_str(), low_stock=low_stock,
                           expiring=expiring, forecast=forecast, new_orders=new_orders, open_pos=open_pos,
                           cl_done=cl_done, cl_total=cl_total, flash=flash, reten=reten,
                           groups=groups, khata_total=khata_total, upcoming=upcoming,
                           today_sales=today_sales, today_bills=today_bills)


@app.route("/tasks")
def task_list():
    db = get_db()
    q = request.args.get("q", "").strip(); f_status = request.args.get("status", "")
    f_priority = request.args.get("priority", ""); f_emp = request.args.get("employee", "")
    sql = "SELECT t.*, e.name AS employee_name FROM tasks t LEFT JOIN employees e ON t.employee_id=e.id WHERE 1=1"
    params = []
    if q: sql += " AND (t.title LIKE ? OR t.description LIKE ?)"; params += [f"%{q}%", f"%{q}%"]
    if f_status in STATUSES: sql += " AND t.status=?"; params.append(f_status)
    if f_priority in PRIORITIES: sql += " AND t.priority=?"; params.append(f_priority)
    if f_emp: sql += " AND t.employee_id=?"; params.append(f_emp)
    sql += " ORDER BY CASE WHEN t.status='Done' THEN 1 ELSE 0 END, t.deadline ASC"
    tasks = db.execute(sql, params).fetchall()
    employees = db.execute("SELECT * FROM employees ORDER BY name").fetchall()
    allrows = db.execute("SELECT status, deadline FROM tasks").fetchall()
    t = today_str()
    counts = {"total": len(allrows),
              "todo": sum(1 for r in allrows if r["status"] == "Todo"),
              "prog": sum(1 for r in allrows if r["status"] == "In Progress"),
              "done": sum(1 for r in allrows if r["status"] == "Done"),
              "overdue": sum(1 for r in allrows if r["status"] != "Done" and r["deadline"] and r["deadline"] < t)}
    edit_task = db.execute("SELECT * FROM tasks WHERE id=?", (request.args.get("edit"),)).fetchone() if request.args.get("edit") else None
    return render_template("tasks.html", tasks=tasks, employees=employees, edit_task=edit_task, q=q,
                           f_status=f_status, f_priority=f_priority, f_emp=f_emp, priorities=PRIORITIES,
                           statuses=STATUSES, today=today_str(), is_overdue=is_overdue, counts=counts)


@app.route("/tasks/add", methods=["POST"])
def task_add():
    db = get_db()
    title = request.form.get("title", "").strip()
    if not title: return redirect(url_for("task_list"))
    emp = request.form.get("employee_id") or None
    priority = request.form.get("priority", "Medium"); status = request.form.get("status", "Todo")
    deadline = request.form.get("deadline", "")
    if priority not in PRIORITIES: priority = "Medium"
    if status not in STATUSES: status = "Todo"
    db.execute("INSERT INTO tasks (title, description, employee_id, priority, status, deadline, completed_at) VALUES (?,?,?,?,?,?,?)",
               (title, request.form.get("description", "").strip(), emp, priority, status, deadline, today_str() if status == "Done" else ""))
    db.commit(); return redirect(url_for("task_list"))


@app.route("/tasks/<int:tid>/update", methods=["POST"])
def task_update(tid):
    db = get_db()
    status = request.form.get("status", "Todo"); priority = request.form.get("priority", "Medium")
    if status not in STATUSES: status = "Todo"
    if priority not in PRIORITIES: priority = "Medium"
    old = db.execute("SELECT status FROM tasks WHERE id=?", (tid,)).fetchone()
    completed_at = None
    if old:
        if status == "Done" and old["status"] != "Done": completed_at = today_str()
        elif status != "Done": completed_at = ""
    base = (request.form.get("title", "").strip(), request.form.get("description", ""), request.form.get("employee_id") or None, priority, status, request.form.get("deadline", ""))
    if completed_at is None:
        db.execute("UPDATE tasks SET title=?, description=?, employee_id=?, priority=?, status=?, deadline=? WHERE id=?", (*base, tid))
    else:
        db.execute("UPDATE tasks SET title=?, description=?, employee_id=?, priority=?, status=?, deadline=?, completed_at=? WHERE id=?", (*base, completed_at, tid))
    db.commit(); return redirect(url_for("task_list"))


@app.route("/tasks/<int:tid>/status", methods=["POST"])
def task_status(tid):
    db = get_db()
    ns = request.form.get("status", "Done")
    if ns not in STATUSES: ns = "Done"
    db.execute("UPDATE tasks SET status=?, completed_at=? WHERE id=?", (ns, today_str() if ns == "Done" else "", tid))
    db.commit(); return redirect(request.form.get("next") or url_for("task_list"))


@app.route("/tasks/<int:tid>/delete", methods=["POST"])
def task_delete(tid):
    db = get_db(); db.execute("DELETE FROM tasks WHERE id=?", (tid,)); db.commit()
    return redirect(url_for("task_list"))


@app.route("/employees")
def employee_list():
    db = get_db()
    employees = db.execute("SELECT * FROM employees ORDER BY name").fetchall()
    counts = {}
    for e in employees:
        t = db.execute("SELECT COUNT(*) c FROM tasks WHERE employee_id=? AND status!='Done'", (e["id"],)).fetchone()["c"]
        o = db.execute("SELECT COUNT(*) c FROM tasks WHERE employee_id=? AND status!='Done' AND deadline!='' AND deadline<?", (e["id"], today_str())).fetchone()["c"]
        counts[e["id"]] = (t, o)
    return render_template("employees.html", employees=employees, counts=counts)


@app.route("/employees/add", methods=["POST"])
def employee_add():
    db = get_db()
    name = request.form.get("name", "").strip()
    if name:
        db.execute("INSERT INTO employees (name, role, phone) VALUES (?,?,?)", (name, request.form.get("role", "").strip(), request.form.get("phone", "").strip()))
        db.commit()
    return redirect(url_for("employee_list"))


@app.route("/employees/<int:eid>/delete", methods=["POST"])
def employee_delete(eid):
    db = get_db()
    db.execute("UPDATE tasks SET employee_id=NULL WHERE employee_id=?", (eid,))
    db.execute("DELETE FROM employees WHERE id=?", (eid,)); db.commit()
    return redirect(url_for("employee_list"))


# ---- Grocery modules ----
@app.route("/inventory")
def inventory_list():
    db = get_db()
    items = db.execute("SELECT * FROM inventory ORDER BY expiry_date ASC, stock_qty ASC").fetchall()
    return render_template("inventory.html", items=items, today=today_str())


@app.route("/inventory/add", methods=["POST"])
def inventory_add():
    db = get_db()
    name = request.form.get("name", "").strip()
    if name:
        try: price = float(request.form.get("price") or 0)
        except ValueError: price = 0
        db.execute("INSERT INTO inventory (name, category, stock_qty, unit, low_threshold, expiry_date, price) VALUES (?,?,?,?,?,?,?)",
                   (name, request.form.get("category", ""), float(request.form.get("stock_qty") or 0),
                    request.form.get("unit", "pcs"), float(request.form.get("low_threshold") or 5), request.form.get("expiry_date", ""), price))
        db.commit(); run_stock_alerts(db)
    return redirect(url_for("inventory_list"))


@app.route("/inventory/<int:iid>/price", methods=["POST"])
def inventory_price(iid):
    db = get_db()
    try: price = float(request.form.get("price") or 0)
    except ValueError: price = 0
    db.execute("UPDATE inventory SET price=? WHERE id=?", (price, iid))
    db.commit()
    return redirect(url_for("inventory_list"))


@app.route("/inventory/<int:iid>/stock", methods=["POST"])
def inventory_stock(iid):
    db = get_db()
    try: delta = float(request.form.get("delta") or 0)
    except ValueError: delta = 0
    db.execute("UPDATE inventory SET stock_qty = stock_qty + ? WHERE id=?", (delta, iid))
    db.commit(); run_stock_alerts(db)
    return redirect(url_for("inventory_list"))


@app.route("/inventory/<int:iid>/delete", methods=["POST"])
def inventory_delete(iid):
    db = get_db(); db.execute("DELETE FROM inventory WHERE id=?", (iid,)); db.commit()
    return redirect(url_for("inventory_list"))


@app.route("/procurement", methods=["GET"])
def procurement():
    db = get_db()
    vendors = db.execute("SELECT * FROM vendors ORDER BY name").fetchall()
    pos = db.execute("SELECT p.*, v.name AS vendor_name FROM purchase_orders p LEFT JOIN vendors v ON p.vendor_id=v.id ORDER BY p.id DESC").fetchall()
    cols = {s: [p for p in pos if p["status"] == s] for s in PO_STATUSES}
    return render_template("procurement.html", vendors=vendors, cols=cols, po_statuses=PO_STATUSES)


@app.route("/vendors/add", methods=["POST"])
def vendor_add():
    db = get_db()
    if request.form.get("name", "").strip():
        db.execute("INSERT INTO vendors (name, category, phone) VALUES (?,?,?)",
                   (request.form.get("name").strip(), request.form.get("category", ""), request.form.get("phone", "")))
        db.commit()
    return redirect(url_for("procurement"))


@app.route("/po/add", methods=["POST"])
def po_add():
    db = get_db()
    item = request.form.get("item", "").strip()
    if item:
        db.execute("INSERT INTO purchase_orders (vendor_id, item, qty, status, due_date) VALUES (?,?,?,?,?)",
                   (request.form.get("vendor_id") or None, item, request.form.get("qty", ""), "PO Raised", request.form.get("due_date", "")))
        db.commit()
        db.execute("INSERT INTO tasks (title, description, priority, status, deadline) VALUES (?,?,?,?,?)",
                   (f"Follow up PO: {item}", "Vendor procurement pipeline: PO Raised. Confirm shipment.", "Medium", "Todo", request.form.get("due_date", "")))
        db.commit()
    return redirect(url_for("procurement"))


@app.route("/po/<int:pid>/advance", methods=["POST"])
def po_advance(pid):
    db = get_db()
    po = db.execute("SELECT * FROM purchase_orders WHERE id=?", (pid,)).fetchone()
    if po:
        idx = PO_STATUSES.index(po["status"]) if po["status"] in PO_STATUSES else 0
        nxt = PO_STATUSES[min(idx + 1, len(PO_STATUSES) - 1)]
        db.execute("UPDATE purchase_orders SET status=? WHERE id=?", (nxt, pid))
        if nxt == "Received & Verified":
            db.execute("UPDATE tasks SET status='Done', completed_at=? WHERE title=? AND status!='Done'", (today_str(), f"Follow up PO: {po['item']}"))
        db.commit()
    return redirect(url_for("procurement"))


@app.route("/po/<int:pid>/delete", methods=["POST"])
def po_delete(pid):
    db = get_db(); db.execute("DELETE FROM purchase_orders WHERE id=?", (pid,)); db.commit()
    return redirect(url_for("procurement"))


@app.route("/orders")
def orders():
    db = get_db()
    orders = db.execute("SELECT * FROM customer_orders ORDER BY CASE WHEN status='Delivered' THEN 1 ELSE 0 END, id DESC").fetchall()
    employees = db.execute("SELECT * FROM employees ORDER BY name").fetchall()
    bundles = {o["id"]: bundle_suggestions(db, o["items_text"]) for o in orders if o["status"] != "Delivered"}
    return render_template("orders.html", orders=orders, employees=employees, order_statuses=ORDER_STATUSES, bundles=bundles)


@app.route("/orders/add", methods=["POST"])
def order_add():
    db = get_db()
    items = request.form.get("items_text", "").strip()
    wa = request.form.get("whatsapp_text", "").strip()
    parsed = parse_whatsapp(wa) if wa and not items else None
    name = request.form.get("customer_name", "").strip() or (parsed["customer_name"] if parsed else "Walk-in")
    phone = request.form.get("phone", "").strip() or (parsed["phone"] if parsed else "")
    if parsed: items = parsed["items_text"]
    if not items and not request.files.get("photo"): return redirect(url_for("orders"))
    photo = save_photo(request.files.get("photo"))
    cur = db.execute("INSERT INTO customer_orders (customer_name, phone, items_text, status, photo) VALUES (?,?,?,?,?)", (name, phone, items, "New", photo))
    oid = cur.lastrowid
    emp = request.form.get("employee_id") or None
    ups = bundle_suggestions(db, items)
    extra = ("\nUpsell: " + "; ".join(f"{u['suggest']} ({u['offer']})" for u in ups)) if ups else ""
    db.execute("INSERT INTO tasks (title, description, employee_id, priority, status, deadline) VALUES (?,?,?,?,?,?)",
               (f"Pack order #{oid} for {name}", f"Picking checklist:\n{items}\nPhone: {phone}{extra}", emp, "High", "Todo", today_str()))
    upsert_customer(db, name, phone)
    db.commit()
    return redirect(url_for("orders"))


@app.route("/orders/<int:oid>/advance", methods=["POST"])
def order_advance(oid):
    db = get_db()
    o = db.execute("SELECT * FROM customer_orders WHERE id=?", (oid,)).fetchone()
    if o:
        idx = ORDER_STATUSES.index(o["status"]) if o["status"] in ORDER_STATUSES else 0
        nxt = ORDER_STATUSES[min(idx + 1, len(ORDER_STATUSES) - 1)]
        db.execute("UPDATE customer_orders SET status=? WHERE id=?", (nxt, oid))
        if nxt == "Delivered":
            db.execute("UPDATE tasks SET status='Done', completed_at=? WHERE title=? AND status!='Done'", (today_str(), f"Pack order #{oid} for {o['customer_name']}"))
        db.commit()
    return redirect(url_for("orders"))


@app.route("/orders/<int:oid>/delete", methods=["POST"])
def order_delete(oid):
    db = get_db(); db.execute("DELETE FROM customer_orders WHERE id=?", (oid,)); db.commit()
    return redirect(url_for("orders"))


@app.route("/checklists")
def checklists():
    db = get_db()
    day = request.args.get("day", today_str())
    items = db.execute("SELECT * FROM checklist_items WHERE day=? ORDER BY template, id", (day,)).fetchall()
    grouped = {}
    for i in items: grouped.setdefault(i["template"], []).append(i)
    done = sum(1 for i in items if i["done"]); total = len(items)
    return render_template("checklists.html", grouped=grouped, templates=CHECKLIST_TEMPLATES, day=day, done=done, total=total, today=today_str())


@app.route("/checklists/generate", methods=["POST"])
def checklist_generate():
    db = get_db()
    template = request.form.get("template", "")
    day = request.form.get("day", today_str()) or today_str()
    if template in CHECKLIST_TEMPLATES:
        for item in CHECKLIST_TEMPLATES[template]:
            ex = db.execute("SELECT id FROM checklist_items WHERE day=? AND template=? AND item=?", (day, template, item)).fetchone()
            if not ex:
                db.execute("INSERT INTO checklist_items (template, item, done, day) VALUES (?,?,0,?)", (template, item, day))
        db.commit()
    return redirect(url_for("checklists", day=day))


@app.route("/checklists/<int:cid>/toggle", methods=["POST"])
def checklist_toggle(cid):
    db = get_db()
    r = db.execute("SELECT * FROM checklist_items WHERE id=?", (cid,)).fetchone()
    if r: db.execute("UPDATE checklist_items SET done=? WHERE id=?", (0 if r["done"] else 1, cid)); db.commit()
    return redirect(url_for("checklists", day=r["day"] if r else today_str()))


@app.route("/checklists/clear", methods=["POST"])
def checklist_clear():
    db = get_db()
    day = request.form.get("day", today_str())
    db.execute("DELETE FROM checklist_items WHERE day=?", (day,)); db.commit()
    return redirect(url_for("checklists", day=day))


# ---- Growth engine: customers/khata, promos, flash sales, group buying ----
@app.route("/customers")
def customers():
    db = get_db()
    rows = db.execute("SELECT * FROM customers ORDER BY total_orders DESC").fetchall()
    return render_template("customers.html", customers=rows)


@app.route("/customers/add", methods=["POST"])
def customer_add():
    db = get_db()
    if request.form.get("name", "").strip():
        db.execute("INSERT INTO customers (name, phone, address) VALUES (?,?,?)",
                   (request.form.get("name").strip(), request.form.get("phone", ""), request.form.get("address", "")))
        db.commit()
    return redirect(url_for("customers"))


@app.route("/customers/<int:cid>/khata", methods=["POST"])
def customer_khata(cid):
    db = get_db()
    try: amt = float(request.form.get("amount") or 0)
    except ValueError: amt = 0
    kind = request.form.get("kind", "credit")
    if amt:
        db.execute("UPDATE customers SET khata_balance = khata_balance + ? WHERE id=?", (amt if kind == "credit" else -amt, cid))
        db.commit()
    return redirect(url_for("customers"))


@app.route("/growth")
def growth():
    db = get_db()
    run_flash_sales(db)
    promos = db.execute("SELECT * FROM promotions ORDER BY id DESC").fetchall()
    flashes = db.execute("SELECT * FROM flash_sales ORDER BY id DESC LIMIT 10").fetchall()
    groups = db.execute("SELECT * FROM group_buys ORDER BY id DESC").fetchall()
    members = {}
    for gm in db.execute("SELECT * FROM group_members").fetchall():
        members.setdefault(gm["group_id"], []).append(gm)
    reten = retention_due(db)
    total_orders = db.execute("SELECT COUNT(*) c FROM customer_orders").fetchone()["c"]
    delivered = db.execute("SELECT COUNT(*) c FROM customer_orders WHERE status='Delivered'").fetchone()["c"]
    return render_template("growth.html", promos=promos, flashes=flashes, groups=groups,
                           members=members, reten=reten, total_orders=total_orders, delivered=delivered)


@app.route("/promotions/add", methods=["POST"])
def promo_add():
    db = get_db()
    if request.form.get("title", "").strip():
        db.execute("INSERT INTO promotions (title, trigger_item, suggest_item, offer, active) VALUES (?,?,?,?,1)",
                   (request.form.get("title").strip(), request.form.get("trigger_item", "").lower(),
                    request.form.get("suggest_item", ""), request.form.get("offer", "")))
        db.commit()
    return redirect(url_for("growth"))


@app.route("/promotions/<int:pid>/toggle", methods=["POST"])
def promo_toggle(pid):
    db = get_db()
    r = db.execute("SELECT active FROM promotions WHERE id=?", (pid,)).fetchone()
    if r: db.execute("UPDATE promotions SET active=? WHERE id=?", (0 if r["active"] else 1, pid)); db.commit()
    return redirect(url_for("growth"))


@app.route("/flash/<int:fid>/toggle", methods=["POST"])
def flash_toggle(fid):
    db = get_db()
    r = db.execute("SELECT active FROM flash_sales WHERE id=?", (fid,)).fetchone()
    if r: db.execute("UPDATE flash_sales SET active=? WHERE id=?", (0 if r["active"] else 1, fid)); db.commit()
    return redirect(url_for("growth"))


@app.route("/groups/add", methods=["POST"])
def group_add():
    db = get_db()
    if request.form.get("title", "").strip():
        db.execute("INSERT INTO group_buys (title, item, target_qty, price, status) VALUES (?,?,?,?,?)",
                   (request.form.get("title").strip(), request.form.get("item", ""),
                    request.form.get("target_qty", ""), request.form.get("price", ""), "Open"))
        db.commit()
    return redirect(url_for("growth"))


@app.route("/groups/<int:gid>/join", methods=["POST"])
def group_join(gid):
    db = get_db()
    name = request.form.get("customer_name", "").strip() or "Neighbour"
    qty = request.form.get("qty", "1")
    db.execute("INSERT INTO group_members (group_id, customer_name, qty) VALUES (?,?,?)", (gid, name, qty))
    try: db.execute("UPDATE group_buys SET joined_qty = joined_qty + ?", (float(qty.split()[0]),)); 
    except Exception: pass
    db.execute("UPDATE group_buys SET joined_qty = (SELECT COUNT(*) FROM group_members WHERE group_id=?) WHERE id=?", (gid, gid))
    db.commit()
    return redirect(url_for("growth"))


@app.route("/groups/<int:gid>/close", methods=["POST"])
def group_close(gid):
    db = get_db()
    db.execute("UPDATE group_buys SET status='Closed' WHERE id=?", (gid,))
    db.execute("INSERT INTO tasks (title, description, priority, status, deadline) VALUES (?,?,?,?,?)",
               (f"Fulfil group buy #{gid}", "Bulk order closed. Raise vendor PO and split for members.", "High", "Todo", today_str()))
    db.commit()
    return redirect(url_for("growth"))


# ---- Billing: customer bills + printable invoices ----
@app.route("/billing")
def billing():
    db = get_db()
    items = db.execute("SELECT * FROM inventory ORDER BY name").fetchall()
    bills = db.execute("SELECT * FROM bills ORDER BY id DESC LIMIT 20").fetchall()
    return render_template("billing.html", items=items, bills=bills)


@app.route("/billing/create", methods=["POST"])
def bill_create():
    db = get_db()
    name = request.form.get("customer_name", "").strip() or "Walk-in"
    phone = request.form.get("phone", "").strip()
    try: discount = float(request.form.get("discount") or 0)
    except ValueError: discount = 0
    lines = []
    for it in db.execute("SELECT * FROM inventory").fetchall():
        try: q = float(request.form.get(f"qty_{it['id']}") or 0)
        except ValueError: q = 0
        if q > 0:
            q = min(q, it["stock_qty"] or 0)
            if q <= 0: continue
            price = it["price"] or 0
            lines.append((it, q, price, round(q * price, 2)))
    if not lines:
        return redirect(url_for("billing"))
    subtotal = round(sum(l[3] for l in lines), 2)
    total = round(max(subtotal - discount, 0), 2)
    photo = save_photo(request.files.get("photo"))
    cur = db.execute("INSERT INTO bills (customer_name, phone, subtotal, discount, total, photo) VALUES (?,?,?,?,?,?)",
                     (name, phone, subtotal, discount, total, photo))
    bid = cur.lastrowid
    for it, q, price, amt in lines:
        db.execute("INSERT INTO bill_items (bill_id, item_name, qty, unit, price, amount) VALUES (?,?,?,?,?,?)",
                   (bid, it["name"], q, it["unit"], price, amt))
        db.execute("UPDATE inventory SET stock_qty = stock_qty - ? WHERE id=?", (q, it["id"]))
    upsert_customer(db, name, phone)
    db.commit()
    run_stock_alerts(db)
    return redirect(url_for("bill_view", bid=bid))


@app.route("/bills/<int:bid>")
def bill_view(bid):
    db = get_db()
    bill = db.execute("SELECT * FROM bills WHERE id=?", (bid,)).fetchone()
    if not bill:
        return redirect(url_for("billing"))
    lines = db.execute("SELECT * FROM bill_items WHERE bill_id=?", (bid,)).fetchall()
    return render_template("bill.html", bill=bill, lines=lines)


@app.route("/bills/<int:bid>/delete", methods=["POST"])
def bill_delete(bid):
    db = get_db()
    db.execute("DELETE FROM bill_items WHERE bill_id=?", (bid,))
    db.execute("DELETE FROM bills WHERE id=?", (bid,))
    db.commit()
    return redirect(url_for("billing"))


# ---- Wholesaler bills: purchase entries with auto rate calc ----
@app.route("/wholesale")
def wholesale():
    db = get_db()
    vendors = db.execute("SELECT * FROM vendors ORDER BY name").fetchall()
    wbills = db.execute("SELECT w.*, v.name AS vendor_name FROM wholesaler_bills w LEFT JOIN vendors v ON w.vendor_id=v.id ORDER BY w.id DESC LIMIT 20").fetchall()
    return render_template("wholesale.html", vendors=vendors, wbills=wbills)


@app.route("/wholesale/create", methods=["POST"])
def wholesale_create():
    db = get_db()
    vendor_id = request.form.get("vendor_id") or None
    bill_no = request.form.get("bill_no", "").strip()
    bill_date = request.form.get("bill_date", "") or today_str()
    names = request.form.getlist("item_name")
    qtys = request.form.getlist("qty")
    units = request.form.getlist("unit")
    rates = request.form.getlist("rate")
    amounts = request.form.getlist("amount")
    lines = []
    for n, q, u, r, a in zip(names, qtys, units, rates, amounts):
        n = (n or "").strip()
        if not n: continue
        try: q = float(q or 0)
        except ValueError: q = 0
        try: r = float(r or 0)
        except ValueError: r = 0
        try: a = float(a or 0)
        except ValueError: a = 0
        # auto-calculate whichever of rate/amount is missing
        if r and not a: a = round(q * r, 2)
        elif a and not r and q: r = round(a / q, 2)
        elif q and r: a = round(q * r, 2)
        lines.append({"name": n, "qty": q, "unit": u or "pcs", "rate": r, "amount": a})
    if not lines:
        return redirect(url_for("wholesale"))
    subtotal = round(sum(l["amount"] for l in lines), 2)
    photo = save_photo(request.files.get("photo"))
    cur = db.execute("INSERT INTO wholesaler_bills (vendor_id, bill_no, bill_date, subtotal, total, photo) VALUES (?,?,?,?,?,?)",
                     (vendor_id, bill_no, bill_date, subtotal, subtotal, photo))
    wid = cur.lastrowid
    for l in lines:
        db.execute("INSERT INTO wholesaler_bill_items (bill_id, item_name, qty, unit, rate, amount) VALUES (?,?,?,?,?,?)",
                   (wid, l["name"], l["qty"], l["unit"], l["rate"], l["amount"]))
        # auto-update shop rates + stock from wholesaler bill
        row = db.execute("SELECT * FROM inventory WHERE lower(name)=lower(?)", (l["name"],)).fetchone()
        if row:
            db.execute("UPDATE inventory SET stock_qty = stock_qty + ?, price = ? WHERE id=?",
                       (l["qty"], l["rate"] if l["rate"] else row["price"], row["id"]))
        elif l["qty"] or l["rate"]:
            db.execute("INSERT INTO inventory (name, category, stock_qty, unit, low_threshold, price) VALUES (?,?,?,?,?,?)",
                       (l["name"], "", l["qty"], l["unit"], 5, l["rate"]))
    db.commit()
    run_stock_alerts(db)
    return redirect(url_for("wholesale_view", wid=wid))


@app.route("/wholesale/<int:wid>")
def wholesale_view(wid):
    db = get_db()
    w = db.execute("SELECT w.*, v.name AS vendor_name FROM wholesaler_bills w LEFT JOIN vendors v ON w.vendor_id=v.id WHERE w.id=?", (wid,)).fetchone()
    if not w:
        return redirect(url_for("wholesale"))
    lines = db.execute("SELECT * FROM wholesaler_bill_items WHERE bill_id=?", (wid,)).fetchall()
    return render_template("wholesale_bill.html", w=w, lines=lines)


@app.route("/wholesale/<int:wid>/delete", methods=["POST"])
def wholesale_delete(wid):
    db = get_db()
    db.execute("DELETE FROM wholesaler_bill_items WHERE bill_id=?", (wid,))
    db.execute("DELETE FROM wholesaler_bills WHERE id=?", (wid,))
    db.commit()
    return redirect(url_for("wholesale"))


@app.route("/api/tasks")
def api_tasks():
    db = get_db()
    rows = db.execute("SELECT t.*, e.name AS employee_name FROM tasks t LEFT JOIN employees e ON t.employee_id=e.id").fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/login", methods=["GET", "POST"])
def login():
    err = ""
    if request.method == "POST":
        u = request.form.get("username", "").strip()
        p = request.form.get("password", "")
        row = get_db().execute("SELECT * FROM users WHERE username=?", (u,)).fetchone()
        if row and check_password_hash(row["password_hash"], p):
            session["user"] = row["username"]
            return redirect(request.args.get("next") or url_for("dashboard"))
        err = "Wrong username or password."
    return render_template("login.html", err=err)


@app.route("/logout")
def logout():
    session.pop("user", None)
    return redirect(url_for("login"))


init_db()


@app.before_request
def _ensure_db():
    # Self-heal if the DB file was deleted/replaced while the server runs
    db = get_db()
    n = db.execute(
        "SELECT COUNT(*) c FROM sqlite_master WHERE type='table' AND name IN "
        "('employees','tasks','customer_orders','inventory','vendors','purchase_orders',"
        "'checklist_items','customers','promotions','flash_sales','group_buys','users',"
        "'bills','bill_items','wholesaler_bills','wholesaler_bill_items')"
    ).fetchone()["c"]
    if n < 16:
        g.pop("db", None)
        try: db.close()
        except Exception: pass
        init_db()
    # Login required everywhere except login/health/static
    if request.path not in PUBLIC_PATHS and not request.path.startswith("/static"):
        if "user" not in session:
            return redirect(url_for("login", next=request.path))


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, debug=True)
