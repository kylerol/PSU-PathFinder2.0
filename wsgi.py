"""
wsgi.py
-------
Alternative entry point for a WSGI server. Render uses `app:app` (see render.yaml),
which works the same way; this file is kept for hosts that expect `wsgi:app`:

    gunicorn --bind 0.0.0.0:$PORT wsgi:app

The database location comes from the DATABASE_PATH environment variable, which
must point onto the persistent disk (Render: /var/data/psu_pathfinder.db).
"""
from app import create_app

app = create_app()
