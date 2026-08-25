from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
import os
from urllib.parse import urlsplit


PROJECT_ROOT = Path(__file__).resolve().parent.parent
HLS_DIR = PROJECT_ROOT / "hls_output"

HOST = "0.0.0.0"
PORT = 8000


class HlsRequestHandler(SimpleHTTPRequestHandler):
    extensions_map = {
        **SimpleHTTPRequestHandler.extensions_map,
        ".m3u8": "application/vnd.apple.mpegurl",
        ".ts": "video/mp2t",
        ".m4s": "video/iso.segment",
        ".mp4": "video/mp4",
    }

    def end_headers(self):
        path = urlsplit(self.path).path.lower()
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS")
        if path.endswith(".m3u8"):
            self.send_header(
                "Cache-Control",
                "no-store, no-cache, must-revalidate, max-age=0",
            )
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
        super().end_headers()


def main():

    os.chdir(HLS_DIR)

    server = ThreadingHTTPServer(
        (HOST, PORT),
        HlsRequestHandler,
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
