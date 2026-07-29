"""Punto de entrada para la app de escritorio (empaquetada con PyInstaller).

Levanta el servidor Flask en segundo plano y abre el navegador
automáticamente, para que se sienta como una aplicación normal de Windows.
"""

import threading
import time
import webbrowser

from app import app

HOST = "127.0.0.1"
PORT = 8000


def _open_browser():
    time.sleep(1.5)
    webbrowser.open(f"http://{HOST}:{PORT}")


if __name__ == "__main__":
    threading.Thread(target=_open_browser, daemon=True).start()
    app.run(host=HOST, port=PORT, debug=False)
