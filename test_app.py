"""Automated test plan for the Legal Client Management System (60 checks).
Run from this folder with:  python test_app.py
It uses a temporary data folder, so real records are never touched.
"""
import io, os, sys, sqlite3, tempfile
from datetime import date, timedelta
os.environ["LEGAL_CMS_DATA"] = tempfile.mkdtemp(prefix="legal_cms_test_")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app as A
results = []
def check(sc, name, cond):
    results.append((sc, name, bool(cond)))
    if not cond: print("FAIL", sc, name)
def mk():
    c = A.app.test_client()
    def tok():
        with c.session_transaction() as s: return s['csrf_token']
    def post(u, d, **k):
        c.get('/login'); d['csrf_token'] = tok()
        return c.post(u, data=d, follow_redirects=True, **k)
    return c, post
D = lambda n: (date.today() + timedelta(days=n)).isoformat()
client = lambda **k: {'name':'Ali Khan','cnic':'35202-1234567-1','phone':'0300-1111111','email':'ali@example.com','address':'Lahore','notes':'', **k}
case = lambda **k: {'client_id':'1','title':'Khan v. Ahmed','case_number':'CS-12/2026','court':'Civil Court','case_type':'Civil','status':'Ongoing','description':'Recovery suit','opened_on':D(-40), **k}

a, pa = mk(); b, pb = mk()
# --- SC13 accounts
check(13, 'Register lawyer A', 'Account created' in pa('/register', {'username':'dad','password':'secret123','confirm':'secret123'}).text)
check(13, 'Duplicate username rejected', 'already taken' in pb('/register', {'username':'dad','password':'secret123','confirm':'secret123'}).text)
check(13, 'Mismatched passwords rejected', 'do not match' in pb('/register', {'username':'x','password':'secret123','confirm':'other1234'}).text)
check(13, 'Short password rejected', 'at least 8' in pb('/register', {'username':'x','password':'short','confirm':'short'}).text)
pb('/register', {'username':'uncle','password':'password9','confirm':'password9'})
check(9, 'Wrong password refused', 'Incorrect' in pa('/login', {'username':'dad','password':'wrong'}).text)
check(9, 'Pages need sign-in', a.get('/clients').status_code == 302)
pa('/login', {'username':'dad','password':'secret123'}); pb('/login', {'username':'uncle','password':'password9'})
# --- SC1, SC2 clients
check(2, 'Invalid CNIC rejected', 'CNIC must be' in pa('/clients/new', client(cnic='12345')).text)
check(1, 'Client name required', 'name is required' in pa('/clients/new', client(name='')).text)
r = pa('/clients/new', client())
check(1, 'Add client', 'Client added' in r.text)
check(2, 'CNIC, phone, address stored', all(x in a.get('/clients/1').text for x in ['35202-1234567-1','0300-1111111','Lahore']))
r = pa('/clients/1/edit', client(phone='0321-2222222'))
check(1, 'Edit client', 'Client updated' in r.text and '0321-2222222' in r.text)
pa('/clients/new', client(name='Sara Malik', cnic='', phone='0333-3333333'))
# --- SC3, SC6 cases
check(3, 'Open case', 'Case opened' in pa('/cases/new', case()).text)
pa('/cases/new', case(client_id='2', title='Malik Property Transfer', case_number='PT-5', case_type='Property', status='Pending', opened_on=D(-5)))
pa('/cases/new', case(title='Khan v. Builders', case_number='CS-99/2024', status='Completed', opened_on=D(-400)))
r = pa('/cases/3/edit', case(title='Khan v. Builders', case_number='CS-99/2024', status='Closed', opened_on=D(-400)))
check(3, 'Update case', 'Case updated' in r.text)
check(6, 'Status shown as badge', 'badge closed' in r.text)
check(6, 'Only the four statuses offered', all(s in a.get('/cases/new').text for s in A.CASE_STATUSES) and 'Adjourned' not in a.get('/cases/new').text)
# --- hearings + SC7 reminders
pa('/cases/1/hearings', {'hearing_date':D(2),'purpose':'Evidence','outcome':''})
pa('/cases/2/hearings', {'hearing_date':D(20),'purpose':'Verification','outcome':''})
pa('/cases/1/hearings', {'hearing_date':D(-10),'purpose':'First hearing','outcome':'Notice issued'})
check(7, 'Invalid hearing date rejected', 'valid hearing date' in pa('/cases/1/hearings', {'hearing_date':'2026-02-30','purpose':'','outcome':''}).text)
d = a.get('/').text
check(7, 'Hearing in 2 days shown as reminder', 'In 2 days' in d and 'Reminders' in d)
check(7, 'Hearing in 20 days not in reminders', d.count('Verification') == 1)
# boundary: exactly 7 days is a reminder, 8 days is not; a busy week (12 hearings) is not cut off
pa('/cases/2/hearings', {'hearing_date':D(7),'purpose':'Seven days','outcome':''})
pa('/cases/2/hearings', {'hearing_date':D(8),'purpose':'Eight days','outcome':''})
d7 = a.get('/').text.split('Upcoming hearings</h2>')[0]
check(7, 'Boundary: hearing in exactly 7 days is a reminder, 8 days is not', 'Seven days' in d7 and 'Eight days' not in d7)
for i in range(12):
    pa('/cases/2/hearings', {'hearing_date':D(3),'purpose':f'Busy {i:02d}','outcome':''})
d7 = a.get('/').text.split('Upcoming hearings</h2>')[0]
check(7, 'Busy week: all 12 extra hearings appear as reminders', all(f'Busy {i:02d}' in d7 for i in range(12)))
for hid in range(4, 18):
    pa(f'/hearings/{hid}/delete', {})
# --- SC8 dashboard
import re
nums = re.findall(r'<strong>(\d+)</strong>', d)[:4]
check(8, 'Dashboard counts: 2 clients, 2 active, 1 completed, 2 upcoming', nums == ['2','2','1','2'])
# --- SC5 documents
r = pa('/cases/1/documents', {'title':'Affidavit','file':(io.BytesIO(b'%PDF-1.4 test'),'affidavit.pdf')}, content_type='multipart/form-data')
check(5, 'Upload document', 'Document uploaded' in r.text)
check(5, 'Download returns same bytes', a.get('/documents/1/download').data == b'%PDF-1.4 test')
check(5, 'Disallowed file type rejected', 'not allowed' in pa('/cases/1/documents', {'title':'','file':(io.BytesIO(b'x'),'virus.exe')}, content_type='multipart/form-data').text)
r = pa('/cases/1/documents', {'title':'','file':(io.BytesIO(b'urdu'),'عرضی.pdf')}, content_type='multipart/form-data')
check(5, 'Non-English file name accepted', 'Document uploaded' in r.text and a.get('/documents/2/download').data == b'urdu')
# --- SC4 search
check(4, 'Search by client name', 'Ali Khan' in a.get('/search?q=Khan').text)
check(4, 'Search by case number', 'Khan v. Ahmed' in a.get('/search?q=CS-12').text)
check(4, 'Search by keyword in description', 'Khan v. Ahmed' in a.get('/search?q=Recovery').text)
check(4, 'Search by CNIC', 'Ali Khan' in a.get('/search?q=35202').text)
check(4, 'Search finds documents', 'Affidavit' in a.get('/search?q=affid').text)
# --- SC11 filters, SC10 history
t = a.get('/clients?q=Sara').text; check(11, 'Filter clients by name', 'Sara Malik' in t and 'Ali Khan' not in t)
t = a.get('/cases?status=Pending').text; check(11, 'Filter by status', 'Malik Property' in t and 'Khan v. Ahmed' not in t)
t = a.get('/cases?type=Property').text; check(11, 'Filter by category', 'Malik Property' in t and 'Khan v. Ahmed' not in t)
t = a.get(f'/cases?from={D(15)}&to={D(25)}').text; check(11, 'Filter by hearing date range', 'Malik Property' in t and 'Khan v. Ahmed' not in t)
t = a.get('/cases?q=Sara').text; check(11, 'Filter cases by client name', 'Malik Property' in t and 'Khan v. Ahmed' not in t)
t = a.get('/cases?status=history').text; check(10, 'History shows completed/closed cases', 'Khan v. Builders' in t and 'Khan v. Ahmed' not in t)
t = a.get('/cases/3').text; check(10, 'Closed case still fully viewable', 'CS-99/2024' in t)
# --- SC12 reports
t = a.get(f'/reports?from={D(-30)}&to={D(0)}').text
summ = re.findall(r'<td>(\d+)</td>', t.split('Case summary')[1].split('</table>')[0]) + re.findall(r'<td><strong>(\d+)</strong></td>', t.split('Case summary')[1].split('</table>')[0])
check(12, 'Report: case summary by status (1 Ongoing, 1 Pending, 0 Completed, 1 Closed, total 3)', summ == ['1','1','0','1','3'])
check(12, 'Report: cases opened in period', 'Malik Property' in t and 'Khan v. Builders' not in t.split('Hearings held')[0].split('Cases opened')[1])
check(12, 'Report: hearings held in period', 'First hearing' in t and 'Evidence' not in t.split('Hearings held')[1].split('Upcoming schedule')[0])
check(12, 'Report: upcoming schedule', 'Evidence' in t and 'Verification' in t)
check(12, 'Report: client list with CNIC', '35202-1234567-1' in t and 'Sara Malik' in t)
check(12, 'Report is printable', 'window.print()' in t)
# --- SC9 privacy between lawyers
for u in ['/clients/1','/cases/1','/documents/1/download','/cases/1/edit']:
    check(13, f'Other lawyer gets 404 for {u}', b.get(u).status_code == 404)
for u in ['/','/clients','/cases','/search?q=Khan','/reports','/cases?status=history']:
    check(13, f'Other lawyer sees none of A\'s data on {u}', 'Ali Khan' not in b.get(u).text and 'Khan v.' not in b.get(u).text)
check(13, 'Other lawyer cannot delete A\'s client', pb('/clients/1/delete', {}).status_code == 404)
check(9, 'Form without CSRF token refused', b.post('/clients/new', data=client()).status_code == 400)
db = sqlite3.connect(A.DB_PATH)
check(9, 'Password stored as hash', db.execute("select password_hash from users where username='dad'").fetchone()[0].startswith(('scrypt:','pbkdf2:')))
check(9, "SQL in search is treated as text", a.get("/search?q=' OR 1=1 --").status_code == 200 and 'Sara' not in a.get("/search?q=' OR 1=1 --").text)
check(9, 'HTML in notes is escaped', '&lt;script&gt;' in pa('/clients/new', client(name='<script>x</script>', cnic='')).text)
# --- SC1, SC3 delete
pa('/cases/2/delete', {}); check(3, 'Delete case', 'Malik Property' not in a.get('/cases').text)
n = len(os.listdir(A.UPLOAD_DIR)); pa('/clients/1/delete', {})
check(1, 'Delete client removes its cases and files', len(os.listdir(A.UPLOAD_DIR)) == n - 2 and db.execute('select count(*) from cases').fetchone()[0] == 0)
ok = sum(r[2] for r in results); print(f"{ok}/{len(results)} passed")
for sc, name, passed in results:
    print(f"  SC{sc:<3} {'PASS' if passed else 'FAIL'}  {name}")
sys.exit(0 if ok == len(results) else 1)
