#!/usr/bin/env python3
"""Brigadá backend: cardápio, pedidos, orçamentos de eventos e painel admin.
Só biblioteca padrão do Python 3.9+ (http.server + sqlite3). Sem pip install.

Variáveis de ambiente:
  PORT=8000  DB_PATH=brigada.db  ADMIN_TOKEN=sua-senha  WEBHOOK_SECRET=outro-segredo
  ALLOWED_ORIGIN=*  (em produção, coloque o domínio do seu site)
"""
import hashlib, hmac, json, os, re, secrets, sqlite3, threading, time
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

PORT = int(os.getenv("PORT", 8000))
DB_PATH = os.getenv("DB_PATH", "brigada.db")
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN") or secrets.token_urlsafe(12)
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET") or secrets.token_urlsafe(24)
ORIGIN = os.getenv("ALLOWED_ORIGIN", "*")
MIN_ITEMS = 4          # pedido mínimo (docinhos)
LEAD_DAYS = 1          # antecedência mínima da entrega
ORDER_STATUS = ("aguardando_pagamento", "pago", "em_producao", "entregue", "cancelado")
QUOTE_STATUS = ("novo", "contatado", "fechado", "perdido")

lock = threading.Lock()
db = sqlite3.connect(DB_PATH, check_same_thread=False)
db.row_factory = sqlite3.Row
db.executescript("""
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS menu(id INTEGER PRIMARY KEY, name TEXT NOT NULL, description TEXT,
  price_cents INTEGER NOT NULL CHECK(price_cents>=0), active INTEGER NOT NULL DEFAULT 1, color TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY, code TEXT UNIQUE, customer TEXT NOT NULL, phone TEXT,
  delivery_date TEXT NOT NULL, payment_method TEXT NOT NULL, total_cents INTEGER NOT NULL,
  status TEXT NOT NULL, pix_code TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS order_items(order_id INTEGER, menu_id INTEGER, name TEXT, qty INTEGER, unit_cents INTEGER);
CREATE TABLE IF NOT EXISTS quotes(id INTEGER PRIMARY KEY, event_type TEXT, guests INTEGER, event_date TEXT,
  budget_cents INTEGER, phone TEXT, name TEXT, score INTEGER, qualified INTEGER, reason TEXT, status TEXT DEFAULT 'novo', created_at TEXT);
""")
if not db.execute("SELECT 1 FROM menu").fetchone():
    db.executemany("INSERT INTO menu(name,description,price_cents,color,updated_at) VALUES(?,?,?,?,datetime('now'))", [
        ("Brigadeiro tradicional", "Chocolate 50%, granulado belga", 350, "#4A1F14"),
        ("Beijinho de coco", "Coco fresco e cravo", 350, "#f6efe0"),
        ("Ninho com Nutella", "Leite ninho, creme de avelã", 500, "#e9c98c"),
        ("Pistache da casa", "Pasta pura, sem corante", 700, "#9cc46b"),
        ("Morango do amor", "Brigadeiro de morango real", 550, "#ff6b8e")])
    db.commit()

class ApiError(Exception):
    def __init__(self, status, msg): self.status, self.msg = status, msg

def now(): return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
def clean(s, n=120): return re.sub(r"\s+", " ", str(s or "")).strip()[:n]
def digits(s): return re.sub(r"\D", "", str(s or ""))
def parse_date(s):
    try: return date.fromisoformat(str(s))
    except ValueError: raise ApiError(400, "Data inválida (use AAAA-MM-DD).")
def rows(cur): return [dict(r) for r in cur.fetchall()]

# ---------- lógica de negócio ----------
def score_quote(guests, budget_cents, days, phone_ok):
    """Mesma regra da demo: separa curiosos de compradores reais (0-100)."""
    ideal = guests * 18 * 100
    s = min(45, budget_cents / ideal * 45) if budget_cents else 0
    s += 25 if days >= 14 else 12 if days >= 5 else 0
    s += 20 if phone_ok else 0
    s += 10 if guests >= 30 else 4
    s = round(min(100, s))
    if not budget_cents: why = "Sem faixa de investimento informada."
    elif not phone_ok: why = "WhatsApp incompleto."
    elif days < 14: why = "Pouco prazo para produção."
    elif s < 65: why = "Verba abaixo do usual para o número de convidados."
    else: why = "Data, verba e contato completos."
    return s, s >= 65, why

def make_pix(code, cents):
    # Placeholder. Troque por Mercado Pago / Asaas / Pagar.me: crie a cobrança e salve o "copia e cola".
    return f"PIX-DEMO-{code}-{cents}"

def create_order(b):
    name, phone = clean(b.get("customer"), 80), digits(b.get("phone"))
    if len(name) < 2: raise ApiError(400, "Informe seu nome.")
    d = parse_date(b.get("delivery_date"))
    if d < date.today() + timedelta(days=LEAD_DAYS): raise ApiError(400, f"Entrega precisa de {LEAD_DAYS} dia(s) de antecedência.")
    method = b.get("payment_method")
    if method not in ("Pix", "Cartão"): raise ApiError(400, "Pagamento deve ser Pix ou Cartão.")
    items = b.get("items")
    if not isinstance(items, list) or not items: raise ApiError(400, "Escolha ao menos um docinho.")
    with lock:
        total, lines, qty = 0, [], 0
        for it in items:  # o preço vem SEMPRE do banco, nunca do navegador
            try: mid, q = int(it["menu_id"]), int(it["qty"])
            except (KeyError, TypeError, ValueError): raise ApiError(400, "Item inválido.")
            if not 0 < q <= 500: raise ApiError(400, "Quantidade inválida.")
            m = db.execute("SELECT * FROM menu WHERE id=?", (mid,)).fetchone()
            if not m or not m["active"]: raise ApiError(409, f"Item indisponível: {m['name'] if m else mid}.")
            total += q * m["price_cents"]; qty += q; lines.append((mid, m["name"], q, m["price_cents"]))
        if qty < MIN_ITEMS: raise ApiError(400, f"Pedido mínimo: {MIN_ITEMS} docinhos.")
        code = secrets.token_hex(3).upper()
        pix = make_pix(code, total) if method == "Pix" else None
        cur = db.execute("INSERT INTO orders(code,customer,phone,delivery_date,payment_method,total_cents,status,pix_code,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                         (code, name, phone, d.isoformat(), method, total, "aguardando_pagamento", pix, now()))
        db.executemany("INSERT INTO order_items VALUES(?,?,?,?,?)", [(cur.lastrowid, *l) for l in lines])
        db.commit()
    return {"code": code, "total_cents": total, "status": "aguardando_pagamento", "pix_code": pix,
            "delivery_date": d.isoformat(), "items": qty}

def create_quote(b):
    guests = int(b.get("guests") or 0)
    if not 10 <= guests <= 2000: raise ApiError(400, "Convidados entre 10 e 2000.")
    d = parse_date(b.get("event_date"))
    days = (d - date.today()).days
    if days < 0: raise ApiError(400, "A data do evento já passou.")
    ev = b.get("event_type")
    if ev not in ("Casamento", "Aniversário", "Corporativo"): raise ApiError(400, "Tipo de evento inválido.")
    budget = max(0, int(b.get("budget_cents") or 0))
    phone = digits(b.get("phone"))
    s, ok, why = score_quote(guests, budget, days, len(phone) >= 10)
    with lock:
        cur = db.execute("INSERT INTO quotes(event_type,guests,event_date,budget_cents,phone,name,score,qualified,reason,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                         (ev, guests, d.isoformat(), budget, phone, clean(b.get("name"), 80), s, int(ok), why, now()))
        db.commit()
    return {"id": cur.lastrowid, "score": s, "qualified": ok, "reason": why,
            "message": "Recebido com prioridade. Respondemos em até 2 horas." if ok else "Recebido! Enviaremos o cardápio de eventos e opções para sua verba."}

# ---------- rotas ----------
def route(method, path, q, body, headers):
    p = path.rstrip("/") or "/"
    admin = p.startswith("/api/admin")
    if admin and not hmac.compare_digest(headers.get("Authorization", ""), f"Bearer {ADMIN_TOKEN}"):
        raise ApiError(401, "Não autorizado.")
    if (method, p) == ("GET", "/api/health"): return 200, {"ok": True}
    if (method, p) == ("GET", "/api/menu"):
        return 200, {"items": rows(db.execute("SELECT id,name,description,price_cents,active,color FROM menu ORDER BY id")), "updated": now()}
    if (method, p) == ("POST", "/api/orders"): return 201, create_order(body)
    if (method, p) == ("POST", "/api/quotes"): return 201, create_quote(body)
    m = re.fullmatch(r"/api/orders/([A-F0-9]{6})", p)
    if method == "GET" and m:
        o = db.execute("SELECT code,customer,delivery_date,total_cents,status,payment_method FROM orders WHERE code=?", (m[1],)).fetchone()
        if not o: raise ApiError(404, "Pedido não encontrado.")
        return 200, dict(o)
    if (method, p) == ("POST", "/api/webhooks/payment"):  # o provedor de pagamento chama aqui
        sig = headers.get("X-Signature", "")
        if not hmac.compare_digest(sig, hmac.new(WEBHOOK_SECRET.encode(), body["_raw"], hashlib.sha256).hexdigest()):
            raise ApiError(401, "Assinatura inválida.")
        with lock:
            db.execute("UPDATE orders SET status='pago' WHERE code=? AND status='aguardando_pagamento'", (clean(body.get("code"), 6),)); db.commit()
        return 200, {"ok": True}
    # admin
    if (method, p) == ("GET", "/api/admin/orders"):
        st = q.get("status", [None])[0]
        sql, args = "SELECT * FROM orders" + (" WHERE status=?" if st else "") + " ORDER BY delivery_date, id", ((st,) if st else ())
        out = rows(db.execute(sql, args))
        for o in out: o["items"] = rows(db.execute("SELECT name,qty,unit_cents FROM order_items WHERE order_id=?", (o["id"],)))
        return 200, {"orders": out}
    if (method, p) == ("GET", "/api/admin/quotes"):
        return 200, {"quotes": rows(db.execute("SELECT * FROM quotes ORDER BY qualified DESC, score DESC, id DESC"))}
    if (method, p) == ("POST", "/api/admin/menu"):
        name, price = clean(body.get("name"), 80), int(body.get("price_cents", -1))
        if not name or price < 0: raise ApiError(400, "Nome e preço são obrigatórios.")
        with lock:
            cur = db.execute("INSERT INTO menu(name,description,price_cents,color,updated_at) VALUES(?,?,?,?,?)",
                             (name, clean(body.get("description"), 200), price, clean(body.get("color"), 9) or "#4A1F14", now())); db.commit()
        return 201, {"id": cur.lastrowid}
    m = re.fullmatch(r"/api/admin/menu/(\d+)", p)
    if method == "PATCH" and m:  # troca preço / esgota em segundos
        sets, args = [], []
        if "price_cents" in body:
            if int(body["price_cents"]) < 0: raise ApiError(400, "Preço inválido.")
            sets.append("price_cents=?"); args.append(int(body["price_cents"]))
        if "active" in body: sets.append("active=?"); args.append(1 if body["active"] else 0)
        for f in ("name", "description"):
            if f in body: sets.append(f"{f}=?"); args.append(clean(body[f], 200))
        if not sets: raise ApiError(400, "Nada para atualizar.")
        with lock:
            cur = db.execute(f"UPDATE menu SET {','.join(sets)},updated_at=? WHERE id=?", (*args, now(), int(m[1]))); db.commit()
        if not cur.rowcount: raise ApiError(404, "Item não encontrado.")
        return 200, {"ok": True}
    for kind, table, allowed in (("orders", "orders", ORDER_STATUS), ("quotes", "quotes", QUOTE_STATUS)):
        m = re.fullmatch(rf"/api/admin/{kind}/(\d+)", p)
        if method == "PATCH" and m:
            if body.get("status") not in allowed: raise ApiError(400, f"Status válidos: {', '.join(allowed)}.")
            with lock:
                cur = db.execute(f"UPDATE {table} SET status=? WHERE id=?", (body["status"], int(m[1]))); db.commit()
            if not cur.rowcount: raise ApiError(404, "Não encontrado.")
            return 200, {"ok": True}
    raise ApiError(404, "Rota não encontrada.")

hits = {}
def limited(ip):  # 60 requisições por minuto por IP
    t = time.time(); h = [x for x in hits.get(ip, []) if t - x < 60]; h.append(t); hits[ip] = h
    return len(h) > 60

class H(BaseHTTPRequestHandler):
    server_version = "Brigada/1.0"
    def log_message(self, *a): pass
    def send(self, status, data, ctype="application/json; charset=utf-8"):
        raw = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        for k, v in (("Content-Type", ctype), ("Content-Length", len(raw)), ("Access-Control-Allow-Origin", ORIGIN),
                     ("Access-Control-Allow-Headers", "Content-Type, Authorization"), ("Access-Control-Allow-Methods", "GET,POST,PATCH,OPTIONS"),
                     ("X-Content-Type-Options", "nosniff"), ("Cache-Control", "no-store")):
            self.send_header(k, str(v))
        self.end_headers(); self.wfile.write(raw)
    def handle_any(self, method):
        u = urlparse(self.path)
        try:
            if method == "OPTIONS": return self.send(204, b"")
            if not u.path.startswith("/api"):  # serve o site estático
                page = "admin.html" if u.path.rstrip("/") == "/admin" else "index.html"
                f = os.path.join(os.path.dirname(os.path.abspath(__file__)), "public", page)
                return self.send(200, open(f, "rb").read(), "text/html; charset=utf-8") if os.path.exists(f) else self.send(404, {"error": "Coloque o site em backend/public/index.html"})
            if limited(self.client_address[0]): raise ApiError(429, "Muitas requisições. Aguarde um minuto.")
            n = int(self.headers.get("Content-Length") or 0)
            if n > 100_000: raise ApiError(413, "Corpo grande demais.")
            raw = self.rfile.read(n) if n else b""
            try: body = json.loads(raw) if raw else {}
            except ValueError: raise ApiError(400, "JSON inválido.")
            if not isinstance(body, dict): raise ApiError(400, "Envie um objeto JSON.")
            body["_raw"] = raw
            status, data = route(method, u.path, parse_qs(u.query), body, self.headers)
            self.send(status, data)
        except ApiError as e: self.send(e.status, {"error": e.msg})
        except (TypeError, ValueError): self.send(400, {"error": "Dados inválidos."})
        except Exception as e: print("ERRO", repr(e)); self.send(500, {"error": "Erro interno."})
    do_GET = lambda s: s.handle_any("GET"); do_POST = lambda s: s.handle_any("POST")
    do_PATCH = lambda s: s.handle_any("PATCH"); do_OPTIONS = lambda s: s.handle_any("OPTIONS")

if __name__ == "__main__":
    if not os.getenv("ADMIN_TOKEN"): print(f"AVISO: ADMIN_TOKEN não definido. Senha temporária do painel: {ADMIN_TOKEN}")
    print(f"Brigadá rodando em http://localhost:{PORT}")
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
