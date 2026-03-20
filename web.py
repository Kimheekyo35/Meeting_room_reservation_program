import json
import os
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urlparse

import psycopg2
from psycopg2.extras import RealDictCursor


def load_env_file(path: str = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


load_env_file()

DB_HOST = os.getenv("PG_HOST")
DB_PORT = os.getenv("PG_PORT", "5432")
DB_DATABASE = os.getenv("PG_DATABASE")
DB_USER = os.getenv("PG_USER")
DB_PASSWORD = os.getenv("PG_PASSWORD")

WEB_PORT = int(os.getenv("WEB_PORT", "8000"))
ROW_LIMIT = int(os.getenv("WEB_ROW_LIMIT", "200"))


def resolve_web_hosts() -> tuple[str, str]:
    configured_host = os.getenv("WEB_HOST", "127.0.0.1").strip()
    bind_host = os.getenv("WEB_BIND_HOST", "").strip()
    public_host = os.getenv("WEB_PUBLIC_HOST", configured_host).strip() or configured_host

    if bind_host:
        return bind_host, public_host

    if configured_host in {"127.0.0.1", "localhost", "0.0.0.0", "::"}:
        return configured_host, public_host

    try:
        parsed_ip = ip_address(configured_host)
    except ValueError:
        return "0.0.0.0", public_host

    if parsed_ip.is_loopback or parsed_ip.is_unspecified:
        return configured_host, public_host

    return "0.0.0.0", public_host


WEB_BIND_HOST, WEB_PUBLIC_HOST = resolve_web_hosts()


def fetch_bookings(limit: int = ROW_LIMIT):
    if not all([DB_HOST, DB_DATABASE, DB_USER, DB_PASSWORD]):
        missing = [
            name
            for name, value in {
                "PG_HOST": DB_HOST,
                "PG_DATABASE": DB_DATABASE,
                "PG_USER": DB_USER,
                "PG_PASSWORD": DB_PASSWORD,
            }.items()
            if not value
        ]
        raise RuntimeError(f"Missing DB env vars: {', '.join(missing)}")

    query = """
        SELECT
            id,
            company_id,
            reserve_day,
            reserve_time,
            floor,
            room_id,
            user_id,
            user_email,
            user_nickname,
            booking_status,
            created_at,
            cancelled_at
        FROM meeting_room_booking.room_booking
        ORDER BY created_at DESC, id DESC
        LIMIT %s
    """

    with psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        database=DB_DATABASE,
        user=DB_USER,
        password=DB_PASSWORD,
    ) as connection:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(query, (limit,))
            rows = cursor.fetchall()

    for row in rows:
        for field in ("created_at", "cancelled_at"):
            value = row.get(field)
            if isinstance(value, datetime):
                row[field] = value.isoformat()

    return rows


HTML_PAGE = """<!doctype html>
<html lang=\"ko\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>Meeting Room DB Viewer</title>
  <style>
    :root {
      --bg: #f4f7fb;
      --card: #ffffff;
      --text: #152238;
      --muted: #58657a;
      --line: #d8e0ec;
      --head: #e9eff8;
      --accent: #0069b9;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "Segoe UI", "Noto Sans KR", sans-serif;
      background: radial-gradient(circle at 20% 0%, #e9f3ff 0%, var(--bg) 40%);
      color: var(--text);
    }
    .wrap { max-width: 1200px; margin: 24px auto; padding: 0 16px; }
    .card {
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 12px;
      box-shadow: 0 10px 30px rgba(0, 37, 77, 0.08);
      overflow: hidden;
    }
    .top {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 10px;
      padding: 14px 16px;
      border-bottom: 1px solid var(--line);
    }
    h1 { margin: 0; font-size: 18px; }
    .meta { color: var(--muted); font-size: 14px; }
    .table-wrap { overflow: auto; }
    table { width: 100%; border-collapse: collapse; min-width: 980px; }
    th, td { padding: 10px 12px; border-bottom: 1px solid var(--line); text-align: left; font-size: 13px; }
    th { background: var(--head); position: sticky; top: 0; z-index: 1; }
    tr:hover td { background: #f7fbff; }
    .error {
      margin: 16px;
      padding: 10px 12px;
      border-radius: 8px;
      background: #ffe9ea;
      border: 1px solid #ffc9cc;
      color: #8c1f28;
      display: none;
    }
  </style>
</head>
<body>
  <div class=\"wrap\">
    <div class=\"card\">
      <div class=\"top\">
        <h1>회의실 예약 DB (5초 자동 새로고침)</h1>
        <div class=\"meta\">마지막 조회: <span id=\"updatedAt\">-</span> | 행 수: <span id=\"count\">0</span></div>
      </div>
      <div id=\"error\" class=\"error\"></div>
      <div class=\"table-wrap\">
        <table>
          <thead>
            <tr>
              <th>ID</th>
              <th>COMPANY_ID</th>
              <th>RESERVE_DAY</th>
              <th>RESERVE_TIME</th>
              <th>FLOOR</th>
              <th>ROOM_ID</th>
              <th>USER_ID</th>
              <th>USER_EMAIL</th>
              <th>USER_NICKNAME</th>
              <th>BOOKING_STATUS</th>
              <th>CREATED_AT</th>
              <th>CANCELLED_AT</th>
            </tr>
          </thead>
          <tbody id=\"rows\"></tbody>
        </table>
      </div>
    </div>
  </div>

<script>
const tbody = document.getElementById('rows');
const updatedAt = document.getElementById('updatedAt');
const count = document.getElementById('count');
const errorBox = document.getElementById('error');

const columns = [
  'id', 'company_id', 'reserve_day', 'reserve_time', 'floor',
  'room_id', 'user_id', 'user_email', 'user_nickname',
  'booking_status', 'created_at', 'cancelled_at'
];

function makeCell(text) {
  const td = document.createElement('td');
  td.textContent = text ?? '';
  return td;
}

function renderRows(rows) {
  tbody.innerHTML = '';
  for (const row of rows) {
    const tr = document.createElement('tr');
    for (const key of columns) {
      tr.appendChild(makeCell(row[key]));
    }
    tbody.appendChild(tr);
  }
}

function showError(msg) {
  errorBox.style.display = 'block';
  errorBox.textContent = msg;
}

function hideError() {
  errorBox.style.display = 'none';
  errorBox.textContent = '';
}

async function refreshData() {
  try {
    const res = await fetch('/api/bookings', { cache: 'no-store' });
    if (!res.ok) {
      throw new Error('HTTP ' + res.status);
    }
    const data = await res.json();
    renderRows(data.rows || []);
    updatedAt.textContent = new Date(data.updated_at).toLocaleString('ko-KR');
    count.textContent = String((data.rows || []).length);
    hideError();
  } catch (err) {
    showError('DB 조회 실패: ' + err.message);
  }
}

refreshData();
setInterval(refreshData, 5000);
</script>
</body>
</html>
"""


class RequestHandler(BaseHTTPRequestHandler):
    def _send_json(self, body: dict, status: int = 200) -> None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _send_html(self, html: str, status: int = 200) -> None:
        payload = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        path = urlparse(self.path).path

        if path == "/":
            self._send_html(HTML_PAGE)
            return

        if path == "/api/bookings":
            try:
                rows = fetch_bookings()
                self._send_json(
                    {
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                        "rows": rows,
                    }
                )
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=500)
            return

        self._send_json({"error": "Not Found"}, status=404)

    def log_message(self, fmt: str, *args):
        return


if __name__ == "__main__":
    server = ThreadingHTTPServer((WEB_BIND_HOST, WEB_PORT), RequestHandler)
    print(f"Web viewer started: http://{WEB_PUBLIC_HOST}:{WEB_PORT} (bind: {WEB_BIND_HOST})")
    server.serve_forever()
