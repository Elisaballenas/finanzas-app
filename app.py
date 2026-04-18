from flask import Flask, jsonify, request, send_from_directory, session
from flask_cors import CORS
from fintoc import Fintoc
import os, re, json, sqlite3
from datetime import date, timedelta
from collections import defaultdict
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__, static_folder='static', static_url_path='')
app.secret_key = os.getenv("SECRET_KEY", "wallet-secret-2026")
CORS(app, supports_credentials=True)
from datetime import timedelta as td
app.config['PERMANENT_SESSION_LIFETIME'] = td(days=30)
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = True

FINTOC_SECRET_KEY = os.getenv("FINTOC_SECRET_KEY")
FINTOC_LINK_TOKEN = os.getenv("FINTOC_LINK_TOKEN")
APP_PASSWORD      = os.getenv("APP_PASSWORD", "felipe2026")
DB_PATH           = os.getenv("DB_PATH", "data.db")
ALERTA_GASTO_MES  = int(os.getenv("ALERTA_GASTO_MES", "1000000"))
META_AHORRO_MES   = int(os.getenv("META_AHORRO_MES", "1200000"))

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.execute("""CREATE TABLE IF NOT EXISTS inversiones (
        id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT NOT NULL,
        saldo REAL DEFAULT 0, aportado REAL DEFAULT 0,
        rentabilidad REAL DEFAULT 0, color TEXT DEFAULT '#3B9EE8',
        updated_at TEXT DEFAULT (date('now')))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS categorias_custom (
        tx_id TEXT PRIMARY KEY, categoria TEXT NOT NULL, mes INTEGER, ano INTEGER)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS deudas (
        id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT NOT NULL,
        monto REAL NOT NULL, descripcion TEXT, tipo TEXT DEFAULT 'debo',
        created_at TEXT DEFAULT (date('now')), pagada INTEGER DEFAULT 0)""")
    count = conn.execute("SELECT COUNT(*) FROM inversiones").fetchone()[0]
    if count == 0:
        for n, s, a, r, c in [
            ("Fondos a 2 años", 3264631, 2702272, 562359, "#9B59B6"),
            ("Inversión corto plazo", 2955435, 2949000, 6435, "#3B9EE8"),
            ("Racional", 0, 0, 0, "#2ECC71"),
        ]:
            conn.execute("INSERT INTO inversiones (nombre,saldo,aportado,rentabilidad,color) VALUES(?,?,?,?,?)", (n,s,a,r,c))
    conn.commit(); conn.close()

init_db()

def check_auth(): return session.get('autenticado') == True

@app.route('/api/login', methods=['POST'])
def login():
    if request.json.get('password') == APP_PASSWORD:
        session['autenticado'] = True
        session.permanent = False
        return jsonify({"ok": True})
    return jsonify({"ok": False}), 401

@app.route('/api/logout', methods=['POST'])
def logout(): session.clear(); return jsonify({"ok": True})

@app.route('/api/check_auth')
def check_auth_route(): return jsonify({"autenticado": check_auth()})

REGLAS = [
    (r'remuneraci', '💰 Sueldo'),
    (r'isapre|vida tres|chilena con|devolucion.*vida|pago proveedor.*chilena', '🏥 Reembolso Isapre'),
    (r'jumbo|lider|santa isabel|unimarc|tottus|supermercado|walmart|abarrotes', '🛒 Supermercado'),
    (r'rappi|uber eats|pedidosya|mcdonalds|burger|subway|sushi|pizza|jalape|restaurant|cafe|cafeter|dulcer|bonappetit|casa matriz|quinchamali|express pza|san damian|las pataguas|wally|toronto', '🍔 Comida'),
    (r'copec|shell|petrobras|repsol|bencin|aramco', '⛽ Bencina'),
    (r'parking|estacionamiento|republic parking|net parking|tuu\*parking|payscan', '🅿️ Estacionamiento'),
    (r'uber|cabify|bliq|bip!|metro|transantiago', '🚗 Transporte'),
    (r'salcobrand|cruzverde|ahumada|farmacia|clinica|hospital|medico|dentist|optica|clinica univ|encuadrado', '💊 Salud'),
    (r'easycancha|deporte|gimnasio|durazno', '🏃 Deporte'),
    (r'netflix|spotify|disney|hbo|amazon prime|youtube|steam|playstation|xbox|cine|teatro|masstimes', '🎬 Entretención'),
    (r'falabella|ripley|paris|zara|h&m|nike|adidas|corona|la polar', '👗 Ropa'),
    (r'apple|aliexpress|pcfactory|abcdin|google play|mercadopago|kindle|amazon', '💻 Tecnología'),
    (r'entel|wom|claro|movistar', '📱 Telecom'),
    (r'fintual|agf sa|vector capital|racional', '📈 Inversiones'),
    (r'trabun|donacion|fundacion|iglesia|catholic', '🙏 Donaciones'),
    (r'cajero|atm|giro', '💵 Efectivo'),
    (r'com\.mantencion|comision|impuesto|mantencion plan', '🏦 Comisiones'),
    (r'juan.*fontaine|fontaine.*juan|juan cristobal', '🏠 Aporte casa'),
    (r'traspaso.*t.*credito|pago.*tarjeta', '💳 Pago Tarjeta'),
    (r'cae|deuda', '📋 CAE / Deuda'),
]
CATS_AHORRO = {'📈 Inversiones'}
CAT_REEMBOLSO = '🏥 Reembolso Isapre'
CAT_SALUD = '💊 Salud'
CAT_NETEO = '🔄 Neteo'

def categorizar(desc):
    d = desc.lower()
    for patron, cat in REGLAS:
        if re.search(patron, d): return cat
    return '❓ Otros'

_cache = {}

def get_cat_custom(tx_id, mes, ano):
    conn = get_db()
    row = conn.execute("SELECT categoria FROM categorias_custom WHERE tx_id=? AND mes=? AND ano=?", (tx_id, mes, ano)).fetchone()
    conn.close()
    return row['categoria'] if row else None

def obtener_movimientos(mes, ano):
    cache_key = f"{ano}-{mes}"
    if cache_key in _cache: return _cache[cache_key]
    hoy = date.today()
    desde = date(ano, mes, 1)
    hasta = (date(ano, mes % 12 + 1, 1) - timedelta(days=1)) if mes < 12 else date(ano, 12, 31)
    hasta = min(hasta, hoy)
    client = Fintoc(FINTOC_SECRET_KEY)
    link = client.links.get(FINTOC_LINK_TOKEN)
    movimientos = []
    for account in link.accounts.all():
        if account.type not in ("checking_account", "credit_card"): continue
        fuente = "Cuenta Corriente" if account.type == "checking_account" else "Tarjeta Crédito"
        try:
            for m in account.movements.all(since=desde.isoformat(), until=hasta.isoformat()):
                cat_custom = get_cat_custom(m.id, mes, ano)
                cat = cat_custom if cat_custom else categorizar(m.description)
                movimientos.append({"id": m.id, "fecha": str(m.post_date)[:10],
                    "descripcion": m.description, "monto": m.amount, "categoria": cat, "fuente": fuente})
        except Exception as e:
            print(f"Error {fuente}: {e}")
    _cache[cache_key] = movimientos
    return movimientos

def calcular_resumen(movs, mes, ano):
    ingresos = sum(m['monto'] for m in movs if m['monto'] > 0 and m['categoria'] not in {CAT_REEMBOLSO, CAT_NETEO})
    reembolsos = sum(m['monto'] for m in movs if m['categoria'] == CAT_REEMBOLSO and m['monto'] > 0)
    gastos_salud = sum(abs(m['monto']) for m in movs if m['categoria'] == CAT_SALUD and m['monto'] < 0)
    salud_neto = max(gastos_salud - reembolsos, 0)
    inversiones = sum(abs(m['monto']) for m in movs if m['monto'] < 0 and m['categoria'] in CATS_AHORRO)
    por_cat = defaultdict(int)
    for m in movs:
        if m['monto'] < 0 and m['categoria'] not in CATS_AHORRO and m['categoria'] not in {CAT_SALUD, CAT_NETEO}:
            por_cat[m['categoria']] += abs(m['monto'])
    if salud_neto > 0: por_cat[CAT_SALUD] = salud_neto
    gastos = sum(por_cat.values())
    return {
        "ingresos": ingresos, "reembolsos": reembolsos, "gastos": gastos,
        "inversiones": inversiones, "ahorro": inversiones,
        "flujo_neto": ingresos - gastos - inversiones,
        "tasa_ahorro": round(inversiones / ingresos * 100, 1) if ingresos else 0,
        "por_categoria": dict(sorted(por_cat.items(), key=lambda x: -x[1])),
        "alerta_gasto": gastos > ALERTA_GASTO_MES, "limite_gasto": ALERTA_GASTO_MES,
        "meta_ahorro": META_AHORRO_MES,
        "pct_meta": round(inversiones / META_AHORRO_MES * 100, 1) if META_AHORRO_MES else 0,
        "mes": mes, "ano": ano,
    }

@app.route('/')
def index(): return send_from_directory('static', 'index.html')

@app.route('/api/resumen')
def resumen():
    if not check_auth(): return jsonify({"error": "no_auth"}), 401
    mes = int(request.args.get('mes', date.today().month))
    ano = int(request.args.get('ano', date.today().year))
    return jsonify(calcular_resumen(obtener_movimientos(mes, ano), mes, ano))

@app.route('/api/resumen_anterior')
def resumen_anterior():
    if not check_auth(): return jsonify({"error": "no_auth"}), 401
    mes = int(request.args.get('mes', date.today().month))
    ano = int(request.args.get('ano', date.today().year))
    mes_ant = mes - 1 if mes > 1 else 12
    ano_ant = ano if mes > 1 else ano - 1
    return jsonify(calcular_resumen(obtener_movimientos(mes_ant, ano_ant), mes_ant, ano_ant))

@app.route('/api/resumen_anual')
def resumen_anual():
    if not check_auth(): return jsonify({"error": "no_auth"}), 401
    ano = int(request.args.get('ano', date.today().year))
    hoy = date.today()
    resultado = []; ahorro_acum = 0
    for m in range(1, 13):
        if date(ano, m, 1) > hoy: break
        r = calcular_resumen(obtener_movimientos(m, ano), m, ano)
        ahorro_acum += r['inversiones']
        resultado.append({"mes": m, "mes_nombre": ["Ene","Feb","Mar","Abr","May","Jun","Jul","Ago","Sep","Oct","Nov","Dic"][m-1],
            "ingresos": r['ingresos'], "gastos": r['gastos'], "inversiones": r['inversiones'],
            "ahorro": r['inversiones'], "flujo_neto": r['flujo_neto'],
            "ahorro_acumulado": ahorro_acum, "tasa_ahorro": r['tasa_ahorro'], "meta_ahorro": META_AHORRO_MES})
    return jsonify(resultado)

@app.route('/api/transacciones')
def transacciones():
    if not check_auth(): return jsonify({"error": "no_auth"}), 401
    mes = int(request.args.get('mes', date.today().month))
    ano = int(request.args.get('ano', date.today().year))
    cat_filter = request.args.get('categoria')
    movs = obtener_movimientos(mes, ano)
    if cat_filter: movs = [m for m in movs if m['categoria'] == cat_filter]
    return jsonify(sorted(movs, key=lambda x: x['fecha'], reverse=True))

@app.route('/api/recategorizar', methods=['POST'])
def recategorizar():
    if not check_auth(): return jsonify({"error": "no_auth"}), 401
    data = request.json
    conn = get_db()
    conn.execute("INSERT OR REPLACE INTO categorias_custom (tx_id,categoria,mes,ano) VALUES(?,?,?,?)",
        (data['id'], data['categoria'], data.get('mes'), data.get('ano')))
    conn.commit(); conn.close()
    cache_key = f"{data.get('ano')}-{data.get('mes')}"
    if cache_key in _cache:
        for m in _cache[cache_key]:
            if m['id'] == data['id']: m['categoria'] = data['categoria']; break
    return jsonify({"ok": True})

@app.route('/api/categorias')
def categorias():
    if not check_auth(): return jsonify({"error": "no_auth"}), 401
    cats = list(set(c for _, c in REGLAS)) + ['❓ Otros', CAT_NETEO]
    return jsonify(sorted(cats))

@app.route('/api/todas_categorias')
def todas_categorias():
    if not check_auth(): return jsonify({"error": "no_auth"}), 401
    return jsonify(sorted(set(c for _, c in REGLAS if c not in {CAT_REEMBOLSO, '💰 Sueldo'})))

@app.route('/api/invalidar_cache', methods=['POST'])
def invalidar_cache():
    if not check_auth(): return jsonify({"error": "no_auth"}), 401
    _cache.clear(); return jsonify({"ok": True})

@app.route('/api/refresh_fintoc', methods=['POST'])
def refresh_fintoc():
    if not check_auth(): return jsonify({"error": "no_auth"}), 401
    try:
        import requests as req
        req.post(f'https://api.fintoc.com/v1/refresh_intents?link_token={FINTOC_LINK_TOKEN}',
            headers={'Authorization': FINTOC_SECRET_KEY, 'Content-Type': 'application/json'})
    except: pass
    _cache.clear(); return jsonify({"ok": True})

@app.route('/api/tendencia_categorias')
def tendencia_categorias():
    if not check_auth(): return jsonify({"error": "no_auth"}), 401
    ano = int(request.args.get('ano', date.today().year))
    cats_filter = request.args.getlist('cat')
    hoy = date.today(); resultado = []
    for m in range(1, 13):
        if date(ano, m, 1) > hoy: break
        movs = obtener_movimientos(m, ano)
        reembolsos = sum(x['monto'] for x in movs if x['categoria'] == CAT_REEMBOLSO and x['monto'] > 0)
        por_cat = defaultdict(int)
        for mov in movs:
            if mov['monto'] < 0 and mov['categoria'] not in CATS_AHORRO and mov['categoria'] != CAT_NETEO:
                por_cat[mov['categoria']] += abs(mov['monto'])
        if CAT_SALUD in por_cat: por_cat[CAT_SALUD] = max(0, por_cat[CAT_SALUD] - reembolsos)
        mes_data = {"mes": m, "mes_nombre": ["Ene","Feb","Mar","Abr","May","Jun","Jul","Ago","Sep","Oct","Nov","Dic"][m-1]}
        for cat in cats_filter: mes_data[cat] = por_cat.get(cat, 0)
        resultado.append(mes_data)
    return jsonify(resultado)

@app.route('/api/inversiones')
def get_inversiones():
    if not check_auth(): return jsonify({"error": "no_auth"}), 401
    conn = get_db()
    rows = [dict(r) for r in conn.execute("SELECT * FROM inversiones ORDER BY saldo DESC").fetchall()]
    conn.close(); return jsonify(rows)

@app.route('/api/inversiones', methods=['POST'])
def crear_inversion():
    if not check_auth(): return jsonify({"error": "no_auth"}), 401
    d = request.json; conn = get_db()
    cur = conn.execute("INSERT INTO inversiones (nombre,saldo,aportado,rentabilidad,color) VALUES(?,?,?,?,?)",
        (d['nombre'], d.get('saldo',0), d.get('aportado',0), d.get('rentabilidad',0), d.get('color','#3B9EE8')))
    conn.commit()
    row = dict(conn.execute("SELECT * FROM inversiones WHERE id=?", (cur.lastrowid,)).fetchone())
    conn.close(); return jsonify(row)

@app.route('/api/inversiones/<int:inv_id>', methods=['PUT'])
def actualizar_inversion(inv_id):
    if not check_auth(): return jsonify({"error": "no_auth"}), 401
    d = request.json; conn = get_db()
    conn.execute("UPDATE inversiones SET nombre=?,saldo=?,aportado=?,rentabilidad=?,color=?,updated_at=date('now') WHERE id=?",
        (d['nombre'], d['saldo'], d['aportado'], d['rentabilidad'], d.get('color','#3B9EE8'), inv_id))
    conn.commit()
    row = dict(conn.execute("SELECT * FROM inversiones WHERE id=?", (inv_id,)).fetchone())
    conn.close(); return jsonify(row)

@app.route('/api/inversiones/<int:inv_id>', methods=['DELETE'])
def eliminar_inversion(inv_id):
    if not check_auth(): return jsonify({"error": "no_auth"}), 401
    conn = get_db(); conn.execute("DELETE FROM inversiones WHERE id=?", (inv_id,))
    conn.commit(); conn.close(); return jsonify({"ok": True})

@app.route('/api/deudas')
def get_deudas():
    if not check_auth(): return jsonify({"error": "no_auth"}), 401
    conn = get_db()
    rows = [dict(r) for r in conn.execute("SELECT * FROM deudas WHERE pagada=0 ORDER BY id DESC").fetchall()]
    conn.close(); return jsonify(rows)

@app.route('/api/deudas', methods=['POST'])
def crear_deuda():
    if not check_auth(): return jsonify({"error": "no_auth"}), 401
    d = request.json; conn = get_db()
    cur = conn.execute("INSERT INTO deudas (nombre,monto,descripcion,tipo) VALUES(?,?,?,?)",
        (d['nombre'], d['monto'], d.get('descripcion',''), d.get('tipo','debo')))
    conn.commit()
    row = dict(conn.execute("SELECT * FROM deudas WHERE id=?", (cur.lastrowid,)).fetchone())
    conn.close(); return jsonify(row)

@app.route('/api/deudas/<int:did>', methods=['PUT'])
def actualizar_deuda(did):
    if not check_auth(): return jsonify({"error": "no_auth"}), 401
    d = request.json; conn = get_db()
    conn.execute("UPDATE deudas SET nombre=?,monto=?,descripcion=?,tipo=?,pagada=? WHERE id=?",
        (d['nombre'], d['monto'], d.get('descripcion',''), d['tipo'], d.get('pagada',0), did))
    conn.commit()
    row = conn.execute("SELECT * FROM deudas WHERE id=?", (did,)).fetchone()
    conn.close(); return jsonify(dict(row) if row else {})

@app.route('/api/deudas/<int:did>', methods=['DELETE'])
def eliminar_deuda(did):
    if not check_auth(): return jsonify({"error": "no_auth"}), 401
    conn = get_db(); conn.execute("DELETE FROM deudas WHERE id=?", (did,))
    conn.commit(); conn.close(); return jsonify({"ok": True})

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
