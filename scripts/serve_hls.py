from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
import os


PROJECT_ROOT = Path(__file__).resolve().parent.parent
HLS_DIR = PROJECT_ROOT / "hls_output"

HOST = "0.0.0.0"
PORT = 8000


def main():

    os.chdir(HLS_DIR)

    server = ThreadingHTTPServer(
        (HOST, PORT),
        SimpleHTTPRequestHandler
    )

    print(f"Serving directory:")
    print(HLS_DIR)

    print()
    print(f"HLS server running:")
    print(f"http://127.0.0.1:{PORT}")

    print()
    print("Press Ctrl+C to stop.")

    try:
        server.serve_forever()

    except KeyboardInterrupt:
        print("\nStopping server...")

    finally:
        server.server_close()


if __name__ == "__main__":
    main()