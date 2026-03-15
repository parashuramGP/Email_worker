"""wsgi.py — Gunicorn entry point for production."""
from app import app

if __name__ == "__main__":
    app.run()
