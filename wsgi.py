"""
Entry point for hosting the Legal Client Management System on a web server
(for example PythonAnywhere, or any server that runs WSGI apps such as gunicorn).

The hosting service provides the HTTPS certificate, so this file only:
  * loads the app,
  * tells it to trust the HTTPS information sent by the hosting proxy,
  * makes the login cookie HTTPS-only.

Local use does not need this file: run  python app.py  instead.
"""

from werkzeug.middleware.proxy_fix import ProxyFix

from app import app

# The hosting service sits in front of the app and handles HTTPS; ProxyFix lets
# the app see that the original request was HTTPS (used for secure headers).
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

app.config.update(
    SESSION_COOKIE_SECURE=True,     # the login cookie is only sent over HTTPS
    SESSION_COOKIE_HTTPONLY=True,   # page scripts cannot read the login cookie
    SESSION_COOKIE_SAMESITE="Lax",
)

application = app   # the name WSGI servers look for
