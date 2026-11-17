"""Efficiency test: times the main pages with 500 clients, 2,000 cases and
6,000 hearings. Uses a temporary folder, so real records are never touched.
Run from this folder with:  python perf_test.py
"""
import os, sys, tempfile, time, random, sqlite3
os.environ["LEGAL_CMS_DATA"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app as A
from datetime import date, timedelta
from werkzeug.security import generate_password_hash
db = sqlite3.connect(A.DB_PATH)
uid = db.execute("INSERT INTO users(username,password_hash) VALUES('perf',?)", (generate_password_hash('perfpass1'),)).lastrowid
random.seed(1)
t = date.today()
for i in range(500):
    cid = db.execute("INSERT INTO clients(user_id,name,cnic,phone,created_on) VALUES(?,?,?,?,?)",
        (uid, f"Client {i:03d}", f"{35200+i%99:05d}-{i:07d}-{i%10}", f"0300-{i:07d}", t.isoformat())).lastrowid
    for j in range(4):
        kid = db.execute("INSERT INTO cases(client_id,title,case_number,court,case_type,status,description,opened_on) VALUES(?,?,?,?,?,?,?,?)",
            (cid, f"Case {i}-{j}", f"CS-{i*4+j}/2026", "Civil Court", random.choice(A.CASE_TYPES), random.choice(A.CASE_STATUSES), "sample description", (t-timedelta(days=random.randint(0,700))).isoformat())).lastrowid
        for k in range(3):
            db.execute("INSERT INTO hearings(case_id,hearing_date,purpose) VALUES(?,?,?)", (kid, (t+timedelta(days=random.randint(-300,90))).isoformat(), "Hearing"))
db.commit()
print("rows:", [db.execute(f"select count(*) from {x}").fetchone()[0] for x in ("clients","cases","hearings")])
c = A.app.test_client()
c.get('/login')
with c.session_transaction() as s: tok = s['csrf_token']
c.post('/login', data={'username':'perf','password':'perfpass1','csrf_token':tok})
for name,u in [('Dashboard','/'),('Search by name','/search?q=Client%20123'),('Search by case number','/search?q=CS-1500'),('Search by CNIC','/search?q=0000321'),('Case list, combined filters','/cases?status=active&type=Civil&from=2026-11-01&to=2026-12-31'),('Clients list','/clients'),('Report','/reports')]:
    times=[]
    for _ in range(5):
        s=time.perf_counter(); r=c.get(u); times.append(time.perf_counter()-s); assert r.status_code==200
    print(f"{name:32s} median {sorted(times)[2]*1000:6.0f} ms")
