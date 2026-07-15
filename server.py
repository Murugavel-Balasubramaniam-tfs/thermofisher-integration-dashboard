#!/usr/bin/env python3
import json
import mimetypes
import os
import re
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlencode, urlparse, urlsplit, urlunsplit

try:
    import psycopg
    from psycopg.types.json import Jsonb
except ImportError:
    psycopg = None
    Jsonb = None

try:
    import yaml
except ImportError:
    yaml = None


ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
INDEX_HTML = STATIC_DIR / "index.html"
LOCAL_STORE = ROOT / "data" / "scripts.json"
TABLE_NAME = "integration_dashboard_scripts"
APPLICATION_CONFIG = ROOT / "application.yml"


def dotenv_values(path):
    values = {}
    if not path.exists():
        return values
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def load_environment():
    """Load base and NODE_ENV profile files while preserving runtime secrets."""
    runtime_keys = set(os.environ)
    base_values = dotenv_values(ROOT / ".env")
    profile = (
        os.environ.get("SPRING_PROFILES_ACTIVE")
        or os.environ.get("NODE_ENV")
        or base_values.get("SPRING_PROFILES_ACTIVE")
        or base_values.get("NODE_ENV")
        or "local"
    )
    profile_values = dotenv_values(ROOT / f".env.{profile}")
    merged_values = {**base_values, **profile_values}
    for key, value in merged_values.items():
        if key not in runtime_keys:
            os.environ[key] = value
    os.environ.setdefault("NODE_ENV", profile)
    return profile


CONFIG_PROFILE = load_environment()
BASE_PATH = "/" + os.environ.get("BASE_PATH", "").strip("/") if os.environ.get("BASE_PATH", "").strip("/") else ""


def load_cart_database_profiles(path=APPLICATION_CONFIG):
    if yaml is None:
        raise RuntimeError("YAML configuration support is not installed. Run pip install -r requirements.txt.")
    if not path.exists():
        raise RuntimeError(f"Application configuration is missing: {path}")

    profiles = {}
    for document in yaml.safe_load_all(path.read_text()):
        if not isinstance(document, dict):
            continue
        spring = document.get("spring") or {}
        profile = (((spring.get("config") or {}).get("activate") or {}).get("on-profile"))
        datasource = spring.get("datasource") or {}
        jdbc_url = datasource.get("url")
        username = datasource.get("username")
        if not profile or not jdbc_url or not username:
            continue
        parsed = urlsplit(str(jdbc_url).removeprefix("jdbc:"))
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        schema = query.get("currentSchema")
        profiles[str(profile)] = {
            "url": str(jdbc_url),
            "username": str(username),
            "schema": schema,
        }
    return profiles


CART_DATABASE_PROFILES = load_cart_database_profiles()


def database_url():
    return os.environ.get("DATABASE_URL")


def use_postgres():
    return bool(database_url()) and psycopg is not None


def connect():
    if not use_postgres():
        raise RuntimeError("PostgreSQL is not configured. Set DATABASE_URL and install requirements.txt.")
    return psycopg.connect(database_url())


def read_first_secret(paths):
    for path in paths:
        secret_path = Path(path)
        if secret_path.is_file():
            value = secret_path.read_text().strip()
            if value:
                return value
    return None


def cart_database_settings():
    profile = os.environ.get("SPRING_PROFILES_ACTIVE") or CONFIG_PROFILE
    profile_config = CART_DATABASE_PROFILES.get(profile)
    configured_url = os.environ.get("FSCOM_CART_DATABASE_URL") or os.environ.get("SPRING_DATASOURCE_URL")
    source = "environment" if configured_url else f"fscom-cart profile: {profile}"

    if configured_url:
        jdbc_url = configured_url.removeprefix("jdbc:")
        parsed = urlsplit(jdbc_url)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        schema = os.environ.get("FSCOM_CART_DATABASE_SCHEMA") or query.pop("currentSchema", None)
        database_url_value = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))
        username = os.environ.get("FSCOM_CART_DATABASE_USERNAME") or os.environ.get("SPRING_DATASOURCE_USERNAME")
    elif profile_config:
        jdbc_url = profile_config["url"].removeprefix("jdbc:")
        parsed = urlsplit(jdbc_url)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        schema = query.pop("currentSchema", None)
        database_url_value = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))
        username = profile_config["username"]
    else:
        raise RuntimeError(
            f"No fscom-cart database mapping exists for profile '{profile}'. Set FSCOM_CART_DATABASE_URL explicitly."
        )

    if not schema or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", schema):
        raise RuntimeError("A valid FSCOM_CART_DATABASE_SCHEMA/currentSchema is required.")

    password = os.environ.get("FSCOM_CART_DATABASE_PASSWORD") or os.environ.get("SPRING_DATASOURCE_PASSWORD")
    password_file = os.environ.get("FSCOM_CART_DATABASE_PASSWORD_FILE")
    if not password:
        secret_name = "secret.spring.datasource.password.eu" if profile.endswith("_eu") else "secret.spring.datasource.password"
        candidates = [password_file] if password_file else []
        candidates.extend(
            [
                f"/etc/secrets/fscart-secret/{secret_name}",
                f"/etc/secrets/fscart-secret/{secret_name.replace('.', '/')}",
            ]
        )
        password = read_first_secret(candidates)
    if not password:
        raise RuntimeError(
            "The fscom-cart datasource password is not configured. Set FSCOM_CART_DATABASE_PASSWORD, "
            "SPRING_DATASOURCE_PASSWORD, or FSCOM_CART_DATABASE_PASSWORD_FILE."
        )

    return {
        "profile": profile,
        "source": source,
        "url": database_url_value,
        "username": username,
        "password": password,
        "schema": schema,
    }


def connect_cart_database(settings):
    if psycopg is None:
        raise RuntimeError("The PostgreSQL driver is not installed. Run pip install -r requirements.txt.")
    return psycopg.connect(
        settings["url"],
        user=settings["username"],
        password=settings["password"],
        options=f"-c search_path={settings['schema']}",
        connect_timeout=5,
    )


def script_id(script, index):
    return (
        script.get("id")
        or "::".join(
            str(script.get(key, "")).strip()
            for key in ("file", "app", "group")
            if str(script.get(key, "")).strip()
        )
        or f"script-{index + 1}"
    )


def extract_seed_scripts():
    html = INDEX_HTML.read_text()
    match = re.search(r"let scripts = (\[[\s\S]*?\n\]);\n\n    const sharedData = \[", html)
    if not match:
        raise RuntimeError("Unable to find embedded dashboard scripts in static/index.html.")
    return json.loads(match.group(1))


def init_db():
    if not use_postgres():
        init_local_store()
        return
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
                  id TEXT PRIMARY KEY,
                  payload JSONB NOT NULL,
                  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            cur.execute(f"SELECT COUNT(*) FROM {TABLE_NAME}")
            count = cur.fetchone()[0]
            if count == 0:
                scripts = extract_seed_scripts()
                rows = [(script_id(script, index), Jsonb(script)) for index, script in enumerate(scripts)]
                cur.executemany(
                    f"""
                    INSERT INTO {TABLE_NAME} (id, payload)
                    VALUES (%s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                      payload = EXCLUDED.payload,
                      updated_at = now()
                    """,
                    rows,
                )
        conn.commit()


def get_scripts():
    if not use_postgres():
        return get_local_scripts()
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT payload FROM {TABLE_NAME} ORDER BY id")
            return [row[0] for row in cur.fetchall()]


def test_database_connection(limit=10):
    """Verify PostgreSQL access and return a small, read-only table preview."""
    if not database_url():
        raise RuntimeError("PostgreSQL is not configured. Set DATABASE_URL in .env and restart the dashboard.")
    if psycopg is None:
        raise RuntimeError("The PostgreSQL driver is not installed. Run pip install -r requirements.txt.")

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT current_database()")
            database = cur.fetchone()[0]
            cur.execute(f"SELECT COUNT(*) FROM {TABLE_NAME}")
            row_count = cur.fetchone()[0]
            cur.execute(
                f"""
                SELECT
                  id,
                  payload ->> 'file' AS file,
                  payload ->> 'app' AS app,
                  payload ->> 'region' AS region,
                  payload ->> 'environment' AS environment,
                  updated_at
                FROM {TABLE_NAME}
                ORDER BY updated_at DESC, id
                LIMIT %s
                """,
                (limit,),
            )
            rows = [
                {
                    "id": row[0],
                    "file": row[1] or "",
                    "app": row[2] or "",
                    "region": row[3] or "",
                    "environment": row[4] or "",
                    "updated_at": row[5].isoformat(),
                }
                for row in cur.fetchall()
            ]
    return {
        "ok": True,
        "profile": CONFIG_PROFILE,
        "database": database,
        "table": TABLE_NAME,
        "row_count": row_count,
        "rows": rows,
    }


def test_cart_database_connection(limit=10):
    """Read a small, non-customer preview from the fscom-cart ORDERITEMS table."""
    settings = cart_database_settings()
    with connect_cart_database(settings) as conn:
        with conn.cursor() as cur:
            cur.execute("SET TRANSACTION READ ONLY")
            cur.execute("SELECT current_database(), current_schema()")
            database, schema = cur.fetchone()
            cur.execute(
                """
                SELECT partnumber, quantity::text, uom, status, lastupdate
                FROM orderitems
                ORDER BY lastupdate DESC NULLS LAST
                LIMIT %s
                """,
                (limit,),
            )
            rows = [
                {
                    "partnumber": row[0] or "",
                    "quantity": row[1] or "",
                    "uom": row[2] or "",
                    "status": row[3] or "",
                    "lastupdate": row[4].isoformat() if row[4] else "",
                }
                for row in cur.fetchall()
            ]
    return {
        "ok": True,
        "profile": settings["profile"],
        "configuration_source": settings["source"],
        "database": database,
        "schema": schema,
        "table": "ORDERITEMS",
        "read_only": True,
        "rows": rows,
    }


def replace_scripts(scripts):
    if not isinstance(scripts, list):
        raise ValueError("Request body must be a JSON array.")
    if not use_postgres():
        return replace_local_scripts(scripts)
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(f"DELETE FROM {TABLE_NAME}")
            rows = [(script_id(script, index), Jsonb(script)) for index, script in enumerate(scripts)]
            cur.executemany(
                f"INSERT INTO {TABLE_NAME} (id, payload) VALUES (%s, %s)",
                rows,
            )
        conn.commit()
    return {"saved": len(scripts)}


def init_local_store():
    LOCAL_STORE.parent.mkdir(parents=True, exist_ok=True)
    if not LOCAL_STORE.exists():
        LOCAL_STORE.write_text(json.dumps(extract_seed_scripts(), indent=2))


def get_local_scripts():
    init_local_store()
    return json.loads(LOCAL_STORE.read_text())


def replace_local_scripts(scripts):
    LOCAL_STORE.parent.mkdir(parents=True, exist_ok=True)
    LOCAL_STORE.write_text(json.dumps(scripts, indent=2))
    return {"saved": len(scripts), "storage": "local-json"}


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "IntegrationDashboardBackend/1.0"

    def route_path(self):
        path = urlparse(self.path).path
        if not BASE_PATH:
            return path
        if path == BASE_PATH or path == f"{BASE_PATH}/":
            return "/"
        if path.startswith(f"{BASE_PATH}/"):
            return path[len(BASE_PATH):]
        return None

    def do_GET(self):
        path = self.route_path()
        if path is None:
            self.send_error_json(HTTPStatus.NOT_FOUND, "Not found")
            return
        if path == "/api/health":
            self.send_json({"ok": True})
            return
        if path == "/api/scripts":
            try:
                self.send_json(get_scripts())
            except Exception as exc:
                self.send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
            return
        if path == "/api/database/test":
            try:
                self.send_json(test_database_connection())
            except RuntimeError as exc:
                self.send_error_json(HTTPStatus.SERVICE_UNAVAILABLE, str(exc))
            except Exception as exc:
                print(f"Database connection test failed: {exc}")
                self.send_error_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    "Unable to connect to PostgreSQL or query the dashboard table. Check DATABASE_URL and the server log.",
                )
            return
        if path == "/api/cart-database/test":
            try:
                self.send_json(test_cart_database_connection())
            except RuntimeError as exc:
                self.send_error_json(HTTPStatus.SERVICE_UNAVAILABLE, str(exc))
            except Exception as exc:
                print(f"fscom-cart database connection test failed: {exc}")
                self.send_error_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    "Unable to query the fscom-cart database. Check the selected profile, network/VPN, and datasource secret.",
                )
            return
        self.serve_static(path)

    def do_PUT(self):
        path = self.route_path()
        if path != "/api/scripts":
            self.send_error_json(HTTPStatus.NOT_FOUND, "Not found")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode("utf-8")
            scripts = json.loads(body)
            self.send_json(replace_scripts(scripts))
        except Exception as exc:
            self.send_error_json(HTTPStatus.BAD_REQUEST, str(exc))

    def do_OPTIONS(self):
        self.send_response(HTTPStatus.NO_CONTENT)
        self.add_common_headers()
        self.end_headers()

    def serve_static(self, request_path):
        path = unquote(request_path.lstrip("/")) or "index.html"
        file_path = (STATIC_DIR / path).resolve()
        if not str(file_path).startswith(str(STATIC_DIR.resolve())) or not file_path.exists() or not file_path.is_file():
            self.send_error_json(HTTPStatus.NOT_FOUND, "Not found")
            return
        content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        data = file_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.add_common_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, payload, status=HTTPStatus.OK):
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.add_common_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_error_json(self, status, message):
        self.send_json({"error": message}, status)

    def add_common_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, PUT, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def log_message(self, fmt, *args):
        print("%s - - %s" % (self.address_string(), fmt % args))


def main():
    init_db()
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    server = ThreadingHTTPServer((host, port), DashboardHandler)
    storage = "postgresql" if use_postgres() else f"local-json ({LOCAL_STORE})"
    print(f"Integration dashboard backend running at http://{host}:{port}{BASE_PATH}/")
    print(f"Configuration profile: {CONFIG_PROFILE}")
    print(f"Storage mode: {storage}")
    server.serve_forever()


if __name__ == "__main__":
    main()
