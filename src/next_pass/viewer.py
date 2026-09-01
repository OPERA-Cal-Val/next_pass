import argparse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path

DATA_ROUTE = "/data/upcoming_passes.parquet"
META_ROUTE = "/data/upcoming_passes.meta.json"


def make_handler(data_path: Path):
    assets = files("next_pass") / "viewer_assets"

    class ViewerHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            route = self.path.split("?")[0]
            if route in ("/", "/index.html"):
                self._send(
                    (assets / "index.html").read_bytes(), "text/html; charset=utf-8"
                )
            elif route == DATA_ROUTE:
                self._send(data_path.read_bytes(), "application/octet-stream")
            elif route == META_ROUTE:
                sidecar = data_path.with_suffix(".meta.json")
                if sidecar.exists():
                    self._send(sidecar.read_bytes(), "application/json")
                else:
                    self._send(b"{}", "application/json")
            else:
                self.send_error(404)

        def _send(self, body: bytes, content_type: str):
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *log_args):
            pass

    return ViewerHandler


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Serve the interactive overpass map for a schedule parquet file."
    )
    parser.add_argument(
        "--data",
        type=Path,
        required=True,
        help="Path to a GeoParquet file produced by next-pass-schedule.",
    )
    parser.add_argument("--port", type=int, default=8471)
    parser.add_argument("--no-browser", action="store_true")
    return parser


def main(cli_args: list[str] | None = None) -> None:
    args = create_parser().parse_args(cli_args)
    data_path = args.data.resolve()
    if not data_path.exists():
        raise SystemExit(f"Data file not found: {data_path}")

    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(data_path))
    url = f"http://127.0.0.1:{args.port}/"
    print(f"Serving {data_path.name} at {url} (Ctrl+C to stop)")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
