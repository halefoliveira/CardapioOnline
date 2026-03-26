from flask import Flask, jsonify, request, send_from_directory, session
from flask_cors import CORS
from database import get_connection, init_db, fetchall, fetchone
import os, sys, base64, json, hashlib, functools

sys.path.insert(0, os.path.dirname(__file__))
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

app = Flask(__name__,
    static_folder=os.path.join(BASE, 'frontend', 'static'),
    template_folder=os.path.join(BASE, 'frontend', 'templates'))
app.secret_key = os.environ.get('SECRET_KEY', 'cardapio-secret-2024')
CORS(app, supports_credentials=True)

UPLOADS_DIR = os.path.join(BASE, 'uploads')
CONFIG_FILE  = os.path.join(BASE, 'config.json')
os.makedirs(UPLOADS_DIR, exist_ok=True)
init_db()

def save_image(base64_str, filename):
    if not base64_str: return None
    if ',' in base64_str: base64_str = base64_str.split(',')[1]
    path = os.path.join(UPLOADS_DIR, filename)
    with open(path, 'wb') as f: f.write(base64.b64decode(base64_str))
    return f'/uploads/{filename}'

def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f: return json.load(f)
    return {}

def save_config(data):
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def login_required(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('admin_logged'):
            return jsonify({'erro': 'Não autorizado'}), 401
        return f(*args, **kwargs)
    return decorated

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

# ── AUTH ──────────────────────────────────────────────────────────────────────
@app.route('/api/auth/login', methods=['POST'])
def login():
    d = request.get_json()
    usuario = d.get('usuario','').strip()
    senha   = hashlib.sha256(d.get('senha','').encode()).hexdigest()
    conn = get_connection(); cur = conn.cursor()
    cur.execute("SELECT * FROM admin WHERE usuario=%s AND senha=%s", (usuario, senha))
    adm = fetchone(cur); conn.close()
    if not adm: return jsonify({'erro': 'Usuário ou senha incorretos'}), 401
    session['admin_logged'] = True; session['admin_usuario'] = usuario
    return jsonify({'ok': True, 'usuario': usuario})

@app.route('/api/auth/logout', methods=['POST'])
def logout():
    session.clear(); return jsonify({'ok': True})

@app.route('/api/auth/check')
def check_auth():
    return jsonify({'logado': bool(session.get('admin_logged'))})

@app.route('/api/auth/senha', methods=['POST'])
@login_required
def alterar_senha():
    d = request.get_json()
    nova = hashlib.sha256(d.get('nova','').encode()).hexdigest()
    usuario = session.get('admin_usuario')
    conn = get_connection(); cur = conn.cursor()
    cur.execute("UPDATE admin SET senha=%s WHERE usuario=%s", (nova, usuario))
    conn.commit(); conn.close(); return jsonify({'ok': True})

# ── CONFIG ────────────────────────────────────────────────────────────────────
@app.route('/api/config')
def get_config():
    return jsonify(load_config())

@app.route('/api/admin/config', methods=['POST'])
@login_required
def set_config():
    d = request.get_json(); cfg = load_config()
    if d.get('nome_loja'):    cfg['nome_loja']   = d['nome_loja']
    if d.get('wpp'):          cfg['wpp']         = d['wpp']
    if 'frete'     in d:      cfg['frete']       = float(d['frete'])
    if 'frete_min' in d:      cfg['frete_min']   = float(d['frete_min'])
    if d.get('logo_base64'):  cfg['logo_url']    = save_image(d['logo_base64'], 'logo.jpg')
    if d.get('banner_base64'):cfg['banner_url']  = save_image(d['banner_base64'], 'banner.jpg')
    save_config(cfg); return jsonify({'ok': True, 'cfg': cfg})

# ── CATEGORIAS ────────────────────────────────────────────────────────────────
@app.route('/api/categorias')
def get_categorias():
    conn = get_connection(); cur = conn.cursor()
    cur.execute("SELECT * FROM categorias ORDER BY ordem")
    cats = fetchall(cur); conn.close(); return jsonify(cats)

@app.route('/api/admin/categoria', methods=['POST'])
@login_required
def criar_categoria():
    d = request.get_json(); conn = get_connection(); cur = conn.cursor()
    cur.execute("SELECT COALESCE(MAX(ordem),0)+1 FROM categorias")
    ordem = cur.fetchone()[0]
    cur.execute("INSERT INTO categorias (nome, ordem) VALUES (%s,%s) RETURNING id", (d['nome'], ordem))
    new_id = cur.fetchone()[0]; conn.commit(); conn.close()
    return jsonify({'id': new_id}), 201

@app.route('/api/admin/categoria/<int:cid>', methods=['PATCH'])
@login_required
def editar_categoria(cid):
    d = request.get_json(); conn = get_connection(); cur = conn.cursor()
    if 'nome'  in d: cur.execute("UPDATE categorias SET nome=%s WHERE id=%s",  (d['nome'], cid))
    if 'ativa' in d: cur.execute("UPDATE categorias SET ativa=%s WHERE id=%s", (1 if d['ativa'] else 0, cid))
    if 'ordem' in d: cur.execute("UPDATE categorias SET ordem=%s WHERE id=%s", (d['ordem'], cid))
    conn.commit(); conn.close(); return jsonify({'ok': True})

@app.route('/api/admin/categoria/<int:cid>', methods=['DELETE'])
@login_required
def deletar_categoria(cid):
    conn = get_connection(); cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM produtos WHERE categoria_id=%s", (cid,))
    if cur.fetchone()[0] > 0:
        conn.close(); return jsonify({'erro': 'Categoria possui produtos'}), 400
    cur.execute("DELETE FROM categorias WHERE id=%s", (cid,))
    conn.commit(); conn.close(); return jsonify({'ok': True})

# ── CARDÁPIO ──────────────────────────────────────────────────────────────────
@app.route('/api/cardapio')
def get_cardapio():
    conn = get_connection(); cur = conn.cursor()
    cur.execute("SELECT * FROM categorias WHERE ativa=1 ORDER BY ordem")
    cats = fetchall(cur); resultado = []
    for cat in cats:
        cur.execute("SELECT * FROM produtos WHERE categoria_id=%s AND disponivel=1", (cat['id'],))
        prods = fetchall(cur)
        resultado.append({'id':cat['id'],'nome':cat['nome'],'produtos':[{
            'id':p['id'],'nome':p['nome'],'descricao':p['descricao'],
            'preco':p['preco'],'foto_url':p['foto_url']} for p in prods]})
    conn.close(); return jsonify(resultado)

# ── PEDIDOS ───────────────────────────────────────────────────────────────────
@app.route('/api/pedido', methods=['POST'])
def criar_pedido():
    dados = request.get_json()
    nome  = dados.get('nome_cliente','').strip()
    tel   = dados.get('telefone','').strip()
    obs   = dados.get('observacao','').strip()
    itens = dados.get('itens', [])
    tipo  = dados.get('tipo_entrega', 'retirada')
    end   = dados.get('endereco','').strip()
    fpag  = dados.get('forma_pagamento','').strip()
    if not nome or not itens:
        return jsonify({'erro': 'Nome e itens são obrigatórios'}), 400
    if tipo == 'entrega' and not end:
        return jsonify({'erro': 'Endereço obrigatório para entrega'}), 400
    cfg = load_config()
    frete = float(cfg.get('frete', 0)) if tipo == 'entrega' else 0.0
    conn = get_connection(); cur = conn.cursor()
    subtotal = 0.0; valids = []
    for item in itens:
        cur.execute("SELECT * FROM produtos WHERE id=%s AND disponivel=1", (item['produto_id'],))
        p = fetchone(cur)
        if not p: conn.close(); return jsonify({'erro': 'Produto indisponível'}), 400
        qty = int(item.get('quantidade', 1))
        subtotal += p['preco'] * qty
        valids.append((p['id'], qty, p['preco']))
    total = round(subtotal + frete, 2)
    cur.execute(
        "INSERT INTO pedidos (nome_cliente,telefone,observacao,total,subtotal,frete,tipo_entrega,endereco,forma_pagamento) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
        (nome, tel, obs, total, round(subtotal,2), frete, tipo, end, fpag))
    pid = cur.fetchone()[0]
    cur.executemany("INSERT INTO itens_pedido (pedido_id,produto_id,quantidade,preco_unit) VALUES (%s,%s,%s,%s)",
        [(pid, p, q, pr) for p,q,pr in valids])
    if tel:
        cur.execute("INSERT INTO clientes (telefone, nome) VALUES (%s,%s) ON CONFLICT (telefone) DO UPDATE SET nome=EXCLUDED.nome", (tel, nome))
    conn.commit(); conn.close()
    return jsonify({'pedido_id': pid, 'total': total, 'subtotal': round(subtotal,2), 'frete': frete}), 201

@app.route('/api/admin/pedidos')
@login_required
def listar_pedidos():
    status_filter = request.args.get('status')
    nome_filter   = request.args.get('nome','').strip()
    data_filter   = request.args.get('data','').strip()
    conn = get_connection(); cur = conn.cursor()
    q = "SELECT * FROM pedidos WHERE 1=1"
    params = []
    if status_filter: q += " AND status=%s"; params.append(status_filter)
    if nome_filter:   q += " AND (nome_cliente ILIKE %s OR CAST(id AS TEXT)=%s)"; params += [f'%{nome_filter}%', nome_filter]
    if data_filter:   q += " AND criado_em LIKE %s"; params.append(f'{data_filter}%')
    q += " ORDER BY criado_em DESC"
    cur.execute(q, params); pedidos = fetchall(cur); result = []
    for p in pedidos:
        cur.execute('''SELECT ip.id, ip.quantidade, ip.preco_unit, pr.nome, pr.id as produto_id
            FROM itens_pedido ip JOIN produtos pr ON pr.id=ip.produto_id WHERE ip.pedido_id=%s''', (p['id'],))
        itens = fetchall(cur)
        result.append({**p, 'itens': itens})
    conn.close(); return jsonify(result)

@app.route('/api/admin/pedidos/<int:pid>', methods=['PATCH'])
@login_required
def editar_pedido(pid):
    d = request.get_json(); conn = get_connection(); cur = conn.cursor()
    if 'nome_cliente'    in d: cur.execute("UPDATE pedidos SET nome_cliente=%s WHERE id=%s",    (d['nome_cliente'], pid))
    if 'telefone'        in d: cur.execute("UPDATE pedidos SET telefone=%s WHERE id=%s",        (d['telefone'], pid))
    if 'observacao'      in d: cur.execute("UPDATE pedidos SET observacao=%s WHERE id=%s",      (d['observacao'], pid))
    if 'endereco'        in d: cur.execute("UPDATE pedidos SET endereco=%s WHERE id=%s",        (d['endereco'], pid))
    if 'forma_pagamento' in d: cur.execute("UPDATE pedidos SET forma_pagamento=%s WHERE id=%s", (d['forma_pagamento'], pid))
    conn.commit(); conn.close(); return jsonify({'ok': True})

@app.route('/api/admin/pedidos/<int:pid>', methods=['DELETE'])
@login_required
def deletar_pedido(pid):
    conn = get_connection(); cur = conn.cursor()
    cur.execute("DELETE FROM itens_pedido WHERE pedido_id=%s", (pid,))
    cur.execute("DELETE FROM financeiro WHERE pedido_id=%s", (pid,))
    cur.execute("DELETE FROM pedidos WHERE id=%s", (pid,))
    conn.commit(); conn.close(); return jsonify({'ok': True})

@app.route('/api/admin/pedidos/<int:pid>/status', methods=['PATCH'])
@login_required
def atualizar_status(pid):
    d = request.get_json(); status = d.get('status'); forma_pagamento = d.get('forma_pagamento')
    if status not in ('novo','em_preparo','em_rota','entregue','cancelado'):
        return jsonify({'erro': 'Status inválido'}), 400
    conn = get_connection(); cur = conn.cursor()
    if forma_pagamento:
        cur.execute("UPDATE pedidos SET status=%s, forma_pagamento=%s WHERE id=%s", (status, forma_pagamento, pid))
    else:
        cur.execute("UPDATE pedidos SET status=%s WHERE id=%s", (status, pid))
    if status == 'entregue':
        cur.execute("SELECT * FROM pedidos WHERE id=%s", (pid,)); pedido = fetchone(cur)
        fpag = forma_pagamento or pedido.get('forma_pagamento') or 'Não informado'
        cliente_id = None
        if pedido.get('telefone'):
            cur.execute("SELECT id FROM clientes WHERE telefone=%s", (pedido['telefone'],))
            cl = cur.fetchone()
            if cl: cliente_id = cl[0]
        cur.execute("SELECT id FROM financeiro WHERE pedido_id=%s", (pid,))
        if not cur.fetchone():
            cur.execute("INSERT INTO financeiro (pedido_id, cliente_id, valor, tipo, forma_pagamento, descricao, pago) VALUES (%s,%s,%s,'entrada',%s,'Pedido #'||%s,1)",
                (pid, cliente_id, pedido['total'], fpag, pid))
    conn.commit(); conn.close(); return jsonify({'ok': True})

# ── PRODUTOS ──────────────────────────────────────────────────────────────────
@app.route('/api/admin/produtos')
@login_required
def listar_produtos():
    conn = get_connection(); cur = conn.cursor()
    cur.execute("SELECT * FROM categorias ORDER BY ordem"); cats = fetchall(cur)
    cur.execute("SELECT * FROM produtos"); prods = fetchall(cur); conn.close()
    return jsonify({'categorias': cats, 'produtos': prods})

@app.route('/api/admin/produto', methods=['POST'])
@login_required
def criar_produto():
    d = request.get_json()
    foto = save_image(d.get('foto_base64'), f"prod_{d['nome'].replace(' ','_')}.jpg") if d.get('foto_base64') else None
    conn = get_connection(); cur = conn.cursor()
    cur.execute("INSERT INTO produtos (categoria_id,nome,descricao,preco,foto_url) VALUES (%s,%s,%s,%s,%s) RETURNING id",
        (d['categoria_id'], d['nome'], d.get('descricao',''), float(d['preco']), foto))
    new_id = cur.fetchone()[0]; conn.commit(); conn.close()
    return jsonify({'id': new_id}), 201

@app.route('/api/admin/produto/<int:pid>', methods=['PATCH'])
@login_required
def editar_produto(pid):
    d = request.get_json(); conn = get_connection(); cur = conn.cursor()
    if 'disponivel'   in d: cur.execute("UPDATE produtos SET disponivel=%s WHERE id=%s",   (1 if d['disponivel'] else 0, pid))
    if 'preco'        in d: cur.execute("UPDATE produtos SET preco=%s WHERE id=%s",         (float(d['preco']), pid))
    if 'nome'         in d: cur.execute("UPDATE produtos SET nome=%s WHERE id=%s",          (d['nome'], pid))
    if 'descricao'    in d: cur.execute("UPDATE produtos SET descricao=%s WHERE id=%s",     (d['descricao'], pid))
    if 'categoria_id' in d: cur.execute("UPDATE produtos SET categoria_id=%s WHERE id=%s", (d['categoria_id'], pid))
    if d.get('foto_base64'):
        url = save_image(d['foto_base64'], f"prod_{pid}.jpg")
        cur.execute("UPDATE produtos SET foto_url=%s WHERE id=%s", (url, pid))
    conn.commit(); conn.close(); return jsonify({'ok': True})

@app.route('/api/admin/produto/<int:pid>', methods=['DELETE'])
@login_required
def deletar_produto(pid):
    conn = get_connection(); cur = conn.cursor()
    cur.execute("DELETE FROM produtos WHERE id=%s", (pid,))
    conn.commit(); conn.close(); return jsonify({'ok': True})

# ── FINANCEIRO ────────────────────────────────────────────────────────────────
@app.route('/api/admin/financeiro')
@login_required
def listar_financeiro():
    tipo  = request.args.get('tipo','')
    pago  = request.args.get('pago','')
    data_ini = request.args.get('data_ini','')
    data_fim = request.args.get('data_fim','')
    conn = get_connection(); cur = conn.cursor()
    q = '''SELECT f.*, c.nome as cliente_nome, c.telefone as cliente_tel
        FROM financeiro f LEFT JOIN clientes c ON c.id=f.cliente_id WHERE 1=1'''
    params = []
    if tipo: q += " AND f.tipo=%s"; params.append(tipo)
    if pago != '': q += " AND f.pago=%s"; params.append(int(pago))
    if data_ini: q += " AND f.criado_em >= %s"; params.append(data_ini)
    if data_fim: q += " AND f.criado_em <= %s"; params.append(data_fim + ' 23:59:59')
    q += " ORDER BY f.criado_em DESC"
    cur.execute(q, params); result = fetchall(cur); conn.close()
    return jsonify(result)

@app.route('/api/admin/financeiro', methods=['POST'])
@login_required
def criar_lancamento():
    d = request.get_json(); conn = get_connection(); cur = conn.cursor()
    cur.execute(
        "INSERT INTO financeiro (valor, tipo, forma_pagamento, descricao, observacao, pago) VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
        (float(d['valor']), d.get('tipo','entrada'), d.get('forma_pagamento',''),
         d.get('descricao',''), d.get('observacao',''), 1 if d.get('pago',True) else 0))
    new_id = cur.fetchone()[0]; conn.commit(); conn.close()
    return jsonify({'id': new_id}), 201

@app.route('/api/admin/financeiro/<int:fid>', methods=['PATCH'])
@login_required
def editar_lancamento(fid):
    d = request.get_json(); conn = get_connection(); cur = conn.cursor()
    if 'valor'          in d: cur.execute("UPDATE financeiro SET valor=%s WHERE id=%s",          (float(d['valor']), fid))
    if 'tipo'           in d: cur.execute("UPDATE financeiro SET tipo=%s WHERE id=%s",           (d['tipo'], fid))
    if 'forma_pagamento'in d: cur.execute("UPDATE financeiro SET forma_pagamento=%s WHERE id=%s",(d['forma_pagamento'], fid))
    if 'descricao'      in d: cur.execute("UPDATE financeiro SET descricao=%s WHERE id=%s",      (d['descricao'], fid))
    if 'observacao'     in d: cur.execute("UPDATE financeiro SET observacao=%s WHERE id=%s",     (d['observacao'], fid))
    if 'pago'           in d: cur.execute("UPDATE financeiro SET pago=%s WHERE id=%s",           (1 if d['pago'] else 0, fid))
    conn.commit(); conn.close(); return jsonify({'ok': True})

@app.route('/api/admin/financeiro/<int:fid>', methods=['DELETE'])
@login_required
def deletar_lancamento(fid):
    conn = get_connection(); cur = conn.cursor()
    cur.execute("DELETE FROM financeiro WHERE id=%s AND pedido_id IS NULL", (fid,))
    conn.commit(); conn.close(); return jsonify({'ok': True})

# ── CLIENTES ──────────────────────────────────────────────────────────────────
@app.route('/api/admin/clientes')
@login_required
def listar_clientes():
    conn = get_connection(); cur = conn.cursor()
    cur.execute("SELECT * FROM clientes ORDER BY criado_em DESC")
    result = fetchall(cur); conn.close(); return jsonify(result)

@app.route('/api/admin/cliente/<int:cid>', methods=['PATCH'])
@login_required
def editar_cliente(cid):
    d = request.get_json(); conn = get_connection(); cur = conn.cursor()
    if 'nome'     in d: cur.execute("UPDATE clientes SET nome=%s WHERE id=%s",     (d['nome'], cid))
    if 'email'    in d: cur.execute("UPDATE clientes SET email=%s WHERE id=%s",    (d['email'], cid))
    if 'endereco' in d: cur.execute("UPDATE clientes SET endereco=%s WHERE id=%s", (d['endereco'], cid))
    conn.commit(); conn.close(); return jsonify({'ok': True})

if __name__ == '__main__':
    print("\n  Cardapio Digital em http://localhost:5000")
    print("  Admin em http://localhost:5000/admin\n")
    app.run(debug=True, port=5000)
