from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from database import get_connection, init_db
import os, sys, base64, json
import psycopg2.extras

sys.path.insert(0, os.path.dirname(__file__))
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

app = Flask(__name__,
            static_folder=os.path.join(BASE, 'frontend', 'static'),
            template_folder=os.path.join(BASE, 'frontend', 'templates'))
CORS(app)

UPLOADS_DIR = os.path.join(BASE, 'uploads')
CONFIG_FILE  = os.path.join(BASE, 'config.json')
os.makedirs(UPLOADS_DIR, exist_ok=True)

init_db()


def save_image(base64_str, filename):
    if not base64_str:
        return None
    if ',' in base64_str:
        base64_str = base64_str.split(',')[1]
    img_bytes = base64.b64decode(base64_str)
    path = os.path.join(UPLOADS_DIR, filename)
    with open(path, 'wb') as f:
        f.write(img_bytes)
    return f'/uploads/{filename}'


def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}


def save_config(data):
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def fetchall(cursor):
    cols = [desc[0] for desc in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


def fetchone(cursor):
    cols = [desc[0] for desc in cursor.description]
    row  = cursor.fetchone()
    return dict(zip(cols, row)) if row else None


# ── FRONTEND ──────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return send_from_directory(os.path.join(BASE, 'frontend', 'templates'), 'cardapio.html')

@app.route('/admin')
def admin():
    return send_from_directory(os.path.join(BASE, 'frontend', 'templates'), 'admin.html')

@app.route('/uploads/<path:filename>')
def uploads(filename):
    return send_from_directory(UPLOADS_DIR, filename)


# ── CONFIG ────────────────────────────────────────────────────────────────────

@app.route('/api/config')
def get_config():
    return jsonify(load_config())

@app.route('/api/admin/config', methods=['POST'])
def set_config():
    d   = request.get_json()
    cfg = load_config()
    if d.get('nome_loja'): cfg['nome_loja'] = d['nome_loja']
    if d.get('wpp'):       cfg['wpp']       = d['wpp']
    if d.get('logo_base64'):
        url = save_image(d['logo_base64'], 'logo.jpg')
        cfg['logo_url'] = url
    save_config(cfg)
    return jsonify({'ok': True})


# ── CARDÁPIO ──────────────────────────────────────────────────────────────────

@app.route('/api/cardapio')
def get_cardapio():
    conn = get_connection()
    cur  = conn.cursor()
    cur.execute("SELECT * FROM categorias WHERE ativa=1 ORDER BY ordem")
    cats = fetchall(cur)
    resultado = []
    for cat in cats:
        cur.execute(
            "SELECT * FROM produtos WHERE categoria_id=%s AND disponivel=1", (cat['id'],)
        )
        prods = fetchall(cur)
        resultado.append({
            'id': cat['id'], 'nome': cat['nome'],
            'produtos': [{
                'id': p['id'], 'nome': p['nome'],
                'descricao': p['descricao'], 'preco': p['preco'],
                'foto_url': p['foto_url']
            } for p in prods]
        })
    conn.close()
    return jsonify(resultado)


# ── PEDIDOS ───────────────────────────────────────────────────────────────────

@app.route('/api/pedido', methods=['POST'])
def criar_pedido():
    dados = request.get_json()
    nome  = dados.get('nome_cliente', '').strip()
    tel   = dados.get('telefone', '').strip()
    obs   = dados.get('observacao', '').strip()
    itens = dados.get('itens', [])
    if not nome or not itens:
        return jsonify({'erro': 'Nome e itens são obrigatórios'}), 400
    conn   = get_connection()
    cur    = conn.cursor()
    total  = 0.0
    valids = []
    for item in itens:
        cur.execute("SELECT * FROM produtos WHERE id=%s AND disponivel=1", (item['produto_id'],))
        p = fetchone(cur)
        if not p:
            conn.close()
            return jsonify({'erro': 'Produto indisponível'}), 400
        qty = int(item.get('quantidade', 1))
        total += p['preco'] * qty
        valids.append((p['id'], qty, p['preco']))
    cur.execute(
        "INSERT INTO pedidos (nome_cliente, telefone, observacao, total) VALUES (%s,%s,%s,%s) RETURNING id",
        (nome, tel, obs, round(total, 2))
    )
    pid = cur.fetchone()[0]
    cur.executemany(
        "INSERT INTO itens_pedido (pedido_id, produto_id, quantidade, preco_unit) VALUES (%s,%s,%s,%s)",
        [(pid, p, q, pr) for p, q, pr in valids]
    )
    conn.commit()
    conn.close()
    return jsonify({'pedido_id': pid, 'total': round(total, 2)}), 201


# ── ADMIN: PEDIDOS ────────────────────────────────────────────────────────────

@app.route('/api/admin/pedidos')
def listar_pedidos():
    conn = get_connection()
    cur  = conn.cursor()
    cur.execute("SELECT * FROM pedidos ORDER BY criado_em DESC")
    pedidos = fetchall(cur)
    result  = []
    for p in pedidos:
        cur.execute('''
            SELECT ip.quantidade, ip.preco_unit, pr.nome
            FROM itens_pedido ip JOIN produtos pr ON pr.id = ip.produto_id
            WHERE ip.pedido_id = %s
        ''', (p['id'],))
        itens = fetchall(cur)
        result.append({
            'id': p['id'], 'nome_cliente': p['nome_cliente'],
            'telefone': p['telefone'], 'observacao': p['observacao'],
            'total': p['total'], 'status': p['status'], 'criado_em': p['criado_em'],
            'itens': [{'nome': i['nome'], 'quantidade': i['quantidade'], 'preco_unit': i['preco_unit']} for i in itens]
        })
    conn.close()
    return jsonify(result)

@app.route('/api/admin/pedidos/<int:pid>/status', methods=['PATCH'])
def atualizar_status(pid):
    status = request.get_json().get('status')
    if status not in ('novo', 'em_preparo', 'pronto', 'entregue'):
        return jsonify({'erro': 'Status inválido'}), 400
    conn = get_connection()
    cur  = conn.cursor()
    cur.execute("UPDATE pedidos SET status=%s WHERE id=%s", (status, pid))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})


# ── ADMIN: PRODUTOS ───────────────────────────────────────────────────────────

@app.route('/api/admin/produtos')
def listar_produtos():
    conn = get_connection()
    cur  = conn.cursor()
    cur.execute("SELECT * FROM categorias ORDER BY ordem")
    cats = fetchall(cur)
    cur.execute("SELECT * FROM produtos")
    prods = fetchall(cur)
    conn.close()
    return jsonify({
        'categorias': [{'id': c['id'], 'nome': c['nome']} for c in cats],
        'produtos': [{
            'id': p['id'], 'categoria_id': p['categoria_id'], 'nome': p['nome'],
            'descricao': p['descricao'], 'preco': p['preco'],
            'disponivel': bool(p['disponivel']), 'foto_url': p['foto_url']
        } for p in prods]
    })

@app.route('/api/admin/produto', methods=['POST'])
def criar_produto():
    d    = request.get_json()
    foto = save_image(d.get('foto_base64'), f"prod_{d['nome'].replace(' ','_')}.jpg") if d.get('foto_base64') else None
    conn = get_connection()
    cur  = conn.cursor()
    cur.execute(
        "INSERT INTO produtos (categoria_id, nome, descricao, preco, foto_url) VALUES (%s,%s,%s,%s,%s) RETURNING id",
        (d['categoria_id'], d['nome'], d.get('descricao', ''), float(d['preco']), foto)
    )
    new_id = cur.fetchone()[0]
    conn.commit()
    conn.close()
    return jsonify({'id': new_id}), 201

@app.route('/api/admin/produto/<int:pid>', methods=['PATCH'])
def editar_produto(pid):
    d    = request.get_json()
    conn = get_connection()
    cur  = conn.cursor()
    if 'disponivel' in d: cur.execute("UPDATE produtos SET disponivel=%s WHERE id=%s", (1 if d['disponivel'] else 0, pid))
    if 'preco'      in d: cur.execute("UPDATE produtos SET preco=%s WHERE id=%s",      (float(d['preco']), pid))
    if 'nome'       in d: cur.execute("UPDATE produtos SET nome=%s WHERE id=%s",       (d['nome'], pid))
    if 'descricao'  in d: cur.execute("UPDATE produtos SET descricao=%s WHERE id=%s",  (d['descricao'], pid))
    if d.get('foto_base64'):
        url = save_image(d['foto_base64'], f"prod_{pid}.jpg")
        cur.execute("UPDATE produtos SET foto_url=%s WHERE id=%s", (url, pid))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

@app.route('/api/admin/produto/<int:pid>', methods=['DELETE'])
def deletar_produto(pid):
    conn = get_connection()
    cur  = conn.cursor()
    cur.execute("DELETE FROM produtos WHERE id=%s", (pid,))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})


# ── START ─────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    print("\n  Cardapio Digital em http://localhost:5000")
    print("  Admin em http://localhost:5000/admin\n")
    app.run(debug=True, port=5000)
