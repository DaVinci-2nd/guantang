import threading
import webbrowser

import uvicorn

from guantang.api import create_app
from guantang.config import Config

HOST = "127.0.0.1"
PORT = 8688


def main():
    cfg = Config()
    app = create_app(cfg)
    threading.Timer(0.8, lambda: webbrowser.open(f"http://{HOST}:{PORT}/")).start()
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
