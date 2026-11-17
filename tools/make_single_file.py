"""Builds dist/legal_cms.py: the whole app (Python, HTML pages and CSS) in one
file that can be double-clicked or sent to someone. Run from anywhere:

    python tools/make_single_file.py
"""
import os
P = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # project root
src=open(f'{P}/app.py', encoding='utf-8').read()
def rep(a,b):
    global src
    assert src.count(a)==1,a
    src=src.replace(a,b)

rep('''Run with:   python app.py
Then open:  https://127.0.0.1:5000

Options:    python app.py --network    (let other computers on the same Wi-Fi connect)
            python app.py --no-https   (plain HTTP, for testing only)''',
'''This is the single-file edition: the Python code, the HTML pages and the CSS
are all in this one file.

HOW TO RUN: double-click this file (or open it in IDLE and press F5).
The first run installs the two libraries it needs, then the system opens in
your browser at https://127.0.0.1:5000

Options (from a terminal):
            py legal_cms.py --network    (let other computers on the same Wi-Fi connect)
            py legal_cms.py --no-https   (plain HTTP, for testing only)''')

rep('''from flask import (''','''

def ensure_installed(*packages):
    """First run only: install the libraries this program needs."""
    import importlib
    import importlib.util
    import subprocess
    import sys
    if getattr(sys, "frozen", False):
        return                      # the packaged .exe already contains them
    missing = [p for p in packages if importlib.util.find_spec(p) is None]
    if missing:
        print("First run: installing", ", ".join(missing), "(needs internet, takes a minute)...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", *missing])
        importlib.invalidate_caches()


ensure_installed("flask", "cryptography")

from jinja2 import DictLoader
from flask import (''')
rep('from flask import (Flask, abort,','from flask import (Flask, Response, abort,')

names=sorted(os.listdir(f'{P}/templates'))
t='\n# --------------------------------------------------------- HTML templates --\n# Each page of the system. {{ ... }} and {% ... %} are filled in by Python.\n\nTEMPLATES = {}\n'
for n in names:
    body=open(f'{P}/templates/{n}', encoding='utf-8').read(); assert "'''" not in body
    if n=="base.html":   # install-as-app pieces are only for the hosted web version
        import re
        body=re.sub(r'    <!-- Lets the site be installed.*?apple-touch-icon[^\n]*\n','',body,flags=re.S)
        body=re.sub(r'<button id="install-button".*?</script>\n','',body,flags=re.S)
        assert 'manifest' not in body and 'install' not in body, body
    t+=f"\nTEMPLATES[\"{n}\"] = r'''{body}'''\n"
css=open(f'{P}/static/style.css', encoding='utf-8').read()
t+=f"\n# --------------------------------------------------------------------- CSS --\n\nSTYLESHEET = r'''{css}'''\n"

rep('''app = Flask(__name__)
''', t+'''

app = Flask(__name__, static_folder=None)
app.jinja_loader = DictLoader(TEMPLATES)   # pages come from the dictionary above


@app.route("/static/<path:filename>", endpoint="static")
def stylesheet(filename):
    if filename != "style.css":
        abort(404)
    return Response(STYLESHEET, mimetype="text/css")

''')

j=src.index('# ------------------------------------------------------------- install app --')
k=src.index('# ------------------------------------------------------------ login/logout --')
src=src[:j]+src[k:]
i=src.index('if __name__ == "__main__":')
src=src[:i]+'''def main():
    parser = argparse.ArgumentParser(description="Legal Client Management System")
    parser.add_argument("--network", action="store_true",
                        help="allow other computers on the same network to connect")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--no-https", action="store_true",
                        help="run without encryption (not recommended)")
    parser.add_argument("--demo", action="store_true",
                        help="add a demo account with made-up sample records")
    args = parser.parse_args()

    if args.demo:
        seed_demo()
        print(f"\\n  Demo account ->  username: {DEMO_USERNAME}   password: {DEMO_PASSWORD}")

    host = "0.0.0.0" if args.network else "127.0.0.1"
    shown = local_ip() if args.network else "127.0.0.1"
    scheme = "http" if args.no_https else "https"
    url = f"{scheme}://{shown}:{args.port}"

    print("\\n  Legal Client Management System is running.")
    print(f"  Open {url} in your browser.")
    print("  Keep this window open while you use it. Close it to stop.\\n")
    threading.Timer(1.5, webbrowser.open, args=(url,)).start()   # open the browser

    if args.no_https:
        app.run(host=host, port=args.port)
    else:
        # Session cookie is only ever sent over the encrypted connection
        app.config.update(SESSION_COOKIE_SECURE=True, SESSION_COOKIE_HTTPONLY=True,
                          SESSION_COOKIE_SAMESITE="Lax")
        app.run(host=host, port=args.port, ssl_context=ensure_certificate())


if __name__ == "__main__":
    try:
        main()
    except Exception as error:      # keep the window open so the message can be read
        print("\\nThe system could not start:", error)
        input("Press Enter to close...")
'''
rep('import socket\n','import socket\nimport threading\nimport webbrowser\n')
os.makedirs(f'{P}/dist', exist_ok=True)
open(f'{P}/dist/legal_cms.py','w', encoding='utf-8').write(src)
print('Created', os.path.join(P, 'dist', 'legal_cms.py'))
