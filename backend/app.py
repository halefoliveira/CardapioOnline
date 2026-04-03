from flask import Flask, jsonify, request, send_from_directory, session
from flask_cors import CORS
from database import get_connection, init_db, fetchall, fetchone
import os, sys, base64, json, hashlib, functools, time, secrets

sys.path.insert(0, os.path.dirname(__file__))
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

app = Flask(__name__,
    static_folder=os.path.join(BASE, 'frontend', 'static'),
    template_folder=os.path.join(BASE, 'frontend', 'templates'))

app.secret_key = os.environ.get('SECRET_KEY', 'cardapio-secret-key-2024-xpto')
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['PERMANENT_SESSION_LIFETIME'] = 86400
CORS(app, supports_credentials=True, origins='*',
     allow_headers=['Content-Type'], expose_headers=['Set-Cookie'])

UPLOADS_DIR = os.path.join(BASE, 'uploads')
CONFIG_FILE  = os.path.join(BASE, 'config.json')
os.makedirs(UPLOADS_DIR, exist_ok=True)
init_db()

# Tokens de recuperação de senha em memória {token: {user_id, usuario, expires}}
reset_tokens = {}

def save_image(b64, filename):
    if not b64: return None
    if ',' in b64: b64 = b64.split(',')[1]
    with open(os.path.join(UPLOADS_DIR, filename), 'wb') as f:
        f.write(base64.b64decode(b64))
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

def admin_required(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('admin_logged'):
            return jsonify({'erro': 'Não autorizado'}), 401
        role = session.get('admin_role')
        if role not in ('admin', None, ''):
            return jsonify({'erro': 'Acesso restrito ao administrador'}), 403
        return f(*args, **kwargs)
    return decorated

# ── FRONTEND ──────────────────────────────────────────────────────────────────
@app.route('/')
def index():
    return send_from_directory(os.path.join(BASE, 'frontend', 'templates'), 'cardapio.html')

@app.route('/admin')
def admin_page():
    return send_from_directory(os.path.join(BASE, 'frontend', 'templates'), 'admin.html')

@app.route('/uploads/<path:filename>')
def uploads(filename):
    return send_from_directory(UPLOADS_DIR, filename)

# ── VISITAS ───────────────────────────────────────────────────────────────────
VISITAS_FILE = os.path.join(BASE, 'visitas.json')

def load_visitas():
    if os.path.exists(VISITAS_FILE):
        with open(VISITAS_FILE, 'r') as f: return json.load(f)
    return {'total': 0, 'ips': []}

def save_visitas(data):
    with open(VISITAS_FILE, 'w') as f: json.dump(data, f)

@app.route('/api/visita', methods=['POST'])
def registrar_visita():
    ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    if ip: ip = ip.split(',')[0].strip()
    v = load_visitas()
    if ip and ip not in v['ips']:
        v['ips'].append(ip)
        v['total'] = len(v['ips'])
        save_visitas(v)
    return jsonify({'total': v['total']})

@app.route('/api/visitas')
def get_visitas():
    v = load_visitas()
    return jsonify({'total': v['total']})

# ── AUTH ──────────────────────────────────────────────────────────────────────
@app.route('/api/auth/login', methods=['POST'])
def login():
    d = request.get_json()
    senha = hashlib.sha256(d.get('senha','').encode()).hexdigest()
    conn = get_connection(); cur = conn.cursor()
    cur.execute("SELECT * FROM admin WHERE usuario=%s AND senha=%s", (d.get('usuario','').strip(), senha))
    adm = fetchone(cur); conn.close()
    if not adm: return jsonify({'erro': 'Usuário ou senha incorretos'}), 401
    session.permanent = True
    session['admin_logged'] = True
    session['admin_usuario'] = d.get('usuario','').strip()
    session['admin_role'] = adm.get('role') or 'admin'
    session['admin_nome'] = adm.get('nome') or adm.get('usuario','')
    perms_raw = adm.get('permissions') or '{}'
    try: session['admin_permissions'] = json.loads(perms_raw) if isinstance(perms_raw, str) else perms_raw
    except: session['admin_permissions'] = {}
    return jsonify({'ok': True, 'role': session['admin_role'], 'nome': session['admin_nome'], 'permissions': session['admin_permissions']})

@app.route('/api/auth/logout', methods=['POST'])
def logout():
    session.clear(); return jsonify({'ok': True})

@app.route('/api/auth/check')
def check_auth():
    return jsonify({
        'logado': bool(session.get('admin_logged')),
        'role': session.get('admin_role', 'admin'),
        'nome': session.get('admin_nome', ''),
        'permissions': session.get('admin_permissions', {})
    })

@app.route('/api/auth/senha', methods=['POST'])
@login_required
def alterar_senha():
    d = request.get_json()
    nova = hashlib.sha256(d.get('nova','').encode()).hexdigest()
    conn = get_connection(); cur = conn.cursor()
    cur.execute("UPDATE admin SET senha=%s WHERE usuario=%s", (nova, session.get('admin_usuario')))
    conn.commit(); conn.close(); return jsonify({'ok': True})

@app.route('/api/auth/forgot-password', methods=['POST'])
def forgot_password():
    d = request.get_json()
    email = (d.get('email') or '').strip()
    if not email: return jsonify({'erro': 'Email obrigatório'}), 400
    conn = get_connection(); cur = conn.cursor()
    cur.execute("SELECT id, usuario FROM admin WHERE email=%s", (email,))
    user = fetchone(cur); conn.close()
    if not user: return jsonify({'ok': True})  # não revelar se existe
    import smtplib
    from email.mime.text import MIMEText
    token = secrets.token_urlsafe(32)
    cfg = load_config()
    smtp_cfg = cfg.get('smtp', {})
    # Limpar tokens expirados antes de inserir novo
    expired = [k for k, v in reset_tokens.items() if v['expires'] < time.time()]
    for k in expired: del reset_tokens[k]
    reset_tokens[token] = {'user_id': user['id'], 'usuario': user['usuario'], 'expires': time.time() + 3600}
    link = f"{request.host_url}admin?reset_token={token}"
    msg = MIMEText(f"Clique no link para redefinir sua senha:\n{link}\n\nLink válido por 1 hora.")
    msg['Subject'] = 'Recuperação de senha — Cardápio Digital'
    msg['From'] = smtp_cfg.get('from') or smtp_cfg.get('user', '')
    msg['To'] = email
    try:
        s = smtplib.SMTP(smtp_cfg.get('host', 'smtp.gmail.com'), int(smtp_cfg.get('port', 587)))
        s.starttls()
        s.login(smtp_cfg.get('user', ''), smtp_cfg.get('password', ''))
        s.send_message(msg); s.quit()
    except Exception as e:
        return jsonify({'erro': f'Erro ao enviar email: {str(e)}'}), 500
    return jsonify({'ok': True})

@app.route('/api/auth/reset-password', methods=['POST'])
def reset_password():
    d = request.get_json()
    token = (d.get('token') or '').strip()
    nova = (d.get('senha') or '').strip()
    if not token or not nova: return jsonify({'erro': 'Token e senha obrigatórios'}), 400
    info = reset_tokens.get(token)
    if not info: return jsonify({'erro': 'Token inválido ou expirado'}), 400
    if info['expires'] < time.time():
        del reset_tokens[token]
        return jsonify({'erro': 'Token expirado'}), 400
    senha_hash = hashlib.sha256(nova.encode()).hexdigest()
    conn = get_connection(); cur = conn.cursor()
    cur.execute("UPDATE admin SET senha=%s WHERE id=%s", (senha_hash, info['user_id']))
    conn.commit(); conn.close()
    del reset_tokens[token]
    return jsonify({'ok': True})

# ── USUÁRIOS ──────────────────────────────────────────────────────────────────
@app.route('/api/admin/usuarios')
@admin_required
def listar_usuarios():
    conn = get_connection(); cur = conn.cursor()
    cur.execute("SELECT id, usuario, nome, role, permissions, email FROM admin ORDER BY id")
    users = fetchall(cur); conn.close()
    return jsonify(users)

@app.route('/api/admin/usuarios', methods=['POST'])
@admin_required
def criar_usuario():
    d = request.get_json()
    usuario = (d.get('usuario') or '').strip()
    senha_plain = (d.get('senha') or '').strip()
    nome = (d.get('nome') or '').strip()
    role = d.get('role', 'staff')
    permissions = json.dumps(d.get('permissions', {}))
    if not usuario or not senha_plain:
        return jsonify({'erro': 'Usuário e senha obrigatórios'}), 400
    senha = hashlib.sha256(senha_plain.encode()).hexdigest()
    conn = get_connection(); cur = conn.cursor()
    email = (d.get('email') or '').strip() or None
    try:
        cur.execute("INSERT INTO admin (usuario, senha, nome, role, permissions, email) VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
                    (usuario, senha, nome, role, permissions, email))
        nid = cur.fetchone()[0]; conn.commit(); conn.close()
        return jsonify({'id': nid}), 201
    except Exception:
        conn.close(); return jsonify({'erro': 'Usuário já existe'}), 409

@app.route('/api/admin/usuarios/<int:uid>', methods=['PATCH'])
@admin_required
def editar_usuario(uid):
    d = request.get_json(); conn = get_connection(); cur = conn.cursor()
    if d.get('nome') is not None:
        cur.execute("UPDATE admin SET nome=%s WHERE id=%s", (d['nome'], uid))
    if d.get('role'):
        cur.execute("UPDATE admin SET role=%s WHERE id=%s", (d['role'], uid))
    if 'permissions' in d:
        cur.execute("UPDATE admin SET permissions=%s WHERE id=%s", (json.dumps(d['permissions']), uid))
    if d.get('senha'):
        senha = hashlib.sha256(d['senha'].encode()).hexdigest()
        cur.execute("UPDATE admin SET senha=%s WHERE id=%s", (senha, uid))
    if 'email' in d:
        cur.execute("UPDATE admin SET email=%s WHERE id=%s", ((d['email'] or None), uid))
    conn.commit(); conn.close(); return jsonify({'ok': True})

@app.route('/api/admin/usuarios/<int:uid>', methods=['DELETE'])
@admin_required
def deletar_usuario(uid):
    conn = get_connection(); cur = conn.cursor()
    cur.execute("SELECT usuario FROM admin WHERE id=%s", (uid,))
    row = cur.fetchone()
    if row and row[0] == session.get('admin_usuario'):
        conn.close(); return jsonify({'erro': 'Não pode excluir o próprio usuário'}), 400
    cur.execute("DELETE FROM admin WHERE id=%s", (uid,))
    conn.commit(); conn.close(); return jsonify({'ok': True})

# ── CONFIG ────────────────────────────────────────────────────────────────────
@app.route('/api/config')
def get_config():
    return jsonify(load_config())

@app.route('/api/cupom/validar', methods=['POST'])
def validar_cupom():
    d = request.get_json()
    codigo = (d.get('codigo') or '').strip().upper()
    telefone = (d.get('telefone') or '').strip()
    cfg = load_config()
    cupons = cfg.get('cupons', [])
    for cup in cupons:
        if cup.get('codigo','').upper() == codigo and cup.get('ativo', True):
            # Cupom para cliente novo: verificar se telefone já tem pedido
            if cup.get('tipo') == 'novo_cliente' and telefone:
                conn = get_connection(); cur = conn.cursor()
                cur.execute("SELECT COUNT(*) FROM pedidos WHERE telefone=%s", (telefone,))
                qtd = cur.fetchone()[0]; conn.close()
                if qtd > 0:
                    return jsonify({'erro': 'Este cupom é válido apenas para novos clientes'}), 400
            return jsonify({'ok': True, 'desconto': cup.get('desconto', 0),
                           'tipo_desconto': cup.get('tipo_desconto', 'pct'),
                           'descricao': cup.get('descricao', ''), 'codigo': codigo})
    return jsonify({'erro': 'Cupom inválido ou expirado'}), 400

@app.route('/api/admin/config', methods=['POST'])
@login_required
def set_config():
    d = request.get_json(); cfg = load_config()
    for k in ['nome_loja','wpp']:
        if d.get(k): cfg[k] = d[k]
    for k in ['frete','frete_min']:
        if k in d: cfg[k] = float(d[k])
    if 'horarios'         in d: cfg['horarios']         = d['horarios']
    if 'tipos_pagamento'  in d: cfg['tipos_pagamento']  = d['tipos_pagamento']
    if 'cupons'           in d: cfg['cupons']           = d['cupons']
    if 'smtp'             in d: cfg['smtp']             = d['smtp']
    if 'impressora'       in d: cfg['impressora']       = d['impressora']
    if 'auto_impressao'   in d: cfg['auto_impressao']   = bool(d['auto_impressao'])
    if 'papel'            in d: cfg['papel']            = d['papel']
    if d.get('logo_base64'):   cfg['logo_url']   = save_image(d['logo_base64'],   'logo.jpg')
    if d.get('banner_base64'): cfg['banner_url'] = save_image(d['banner_base64'], 'banner.jpg')
    save_config(cfg)
    return jsonify({'ok': True, 'cfg': cfg})

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
    cur.execute("INSERT INTO categorias (nome,ordem) VALUES (%s,%s) RETURNING id", (d['nome'], ordem))
    nid = cur.fetchone()[0]; conn.commit(); conn.close()
    return jsonify({'id': nid}), 201

@app.route('/api/admin/categoria/<int:cid>', methods=['PATCH'])
@login_required
def editar_categoria(cid):
    d = request.get_json(); conn = get_connection(); cur = conn.cursor()
    if 'nome'  in d: cur.execute("UPDATE categorias SET nome=%s  WHERE id=%s", (d['nome'], cid))
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
            'preco':p['preco'],'foto_url':p['foto_url'],
            'preco_promo':p.get('preco_promo'),'em_promo':bool(p.get('em_promo'))} for p in prods]})
    conn.close(); return jsonify(resultado)

# ── PEDIDOS ───────────────────────────────────────────────────────────────────
@app.route('/api/pedido', methods=['POST'])
def criar_pedido():
    dados = request.get_json()
    nome = dados.get('nome_cliente','').strip()
    tel  = dados.get('telefone','').strip()
    obs  = dados.get('observacao','').strip()
    itens = dados.get('itens', [])
    tipo = dados.get('tipo_entrega', 'retirada')
    end  = dados.get('endereco','').strip()
    fpag = dados.get('forma_pagamento','').strip()
    if not nome or not itens:
        return jsonify({'erro': 'Nome e itens são obrigatórios'}), 400
    if tipo == 'entrega' and not end:
        return jsonify({'erro': 'Endereço obrigatório para entrega'}), 400
    cfg = load_config()
    # frete_override permite que o PDV envie um frete personalizado
    frete_override = dados.get('frete_override')
    if frete_override is not None:
        frete = float(frete_override) if tipo == 'entrega' else 0.0
    else:
        frete = float(cfg.get('frete', 0)) if tipo == 'entrega' else 0.0
    conn = get_connection(); cur = conn.cursor()
    subtotal = 0.0; valids = []
    for item in itens:
        cur.execute("SELECT * FROM produtos WHERE id=%s AND disponivel=1", (item['produto_id'],))
        p = fetchone(cur)
        if not p: conn.close(); return jsonify({'erro': 'Produto indisponível'}), 400
        qty = int(item.get('quantidade', 1))
        # Usa preço promocional se em promoção
        preco = float(p.get('preco_promo') or p['preco']) if p.get('em_promo') and p.get('preco_promo') else float(p['preco'])
        subtotal += preco * qty
        valids.append((p['id'], qty, preco))
    # desconto_override: valor de desconto enviado pelo frontend (cupom/pdv)
    desconto_override = dados.get('desconto_override', 0)
    desconto = float(desconto_override) if desconto_override else 0.0
    total = round(max(0, subtotal + frete - desconto), 2)
    cur.execute(
        "INSERT INTO pedidos (nome_cliente,telefone,observacao,total,subtotal,frete,tipo_entrega,endereco,forma_pagamento,status) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'pendente') RETURNING id",
        (nome, tel, obs, total, round(subtotal,2), frete, tipo, end, fpag))
    pid = cur.fetchone()[0]
    cur.executemany("INSERT INTO itens_pedido (pedido_id,produto_id,quantidade,preco_unit) VALUES (%s,%s,%s,%s)",
        [(pid, p, q, pr) for p,q,pr in valids])
    if tel:
        cur.execute("INSERT INTO clientes (telefone,nome,tipo) VALUES (%s,%s,'cliente') ON CONFLICT (telefone) DO UPDATE SET nome=EXCLUDED.nome", (tel, nome))
    conn.commit(); conn.close()
    return jsonify({'pedido_id': pid, 'total': total, 'subtotal': round(subtotal,2), 'frete': frete, 'desconto': desconto}), 201

@app.route('/api/admin/pedidos')
@login_required
def listar_pedidos():
    sf = request.args.get('status','')
    nf = request.args.get('nome','').strip()
    df = request.args.get('data','').strip()
    conn = get_connection(); cur = conn.cursor()
    q = "SELECT * FROM pedidos WHERE 1=1"; params = []
    if sf: q += " AND status=%s"; params.append(sf)
    if nf: q += " AND (nome_cliente ILIKE %s OR CAST(id AS TEXT)=%s)"; params += [f'%{nf}%', nf]
    if df: q += " AND criado_em LIKE %s"; params.append(f'{df}%')
    q += " ORDER BY criado_em DESC"
    cur.execute(q, params); pedidos = fetchall(cur); result = []
    for p in pedidos:
        cur.execute('''SELECT ip.id,ip.quantidade,ip.preco_unit,pr.nome,pr.id as produto_id
            FROM itens_pedido ip JOIN produtos pr ON pr.id=ip.produto_id WHERE ip.pedido_id=%s''', (p['id'],))
        result.append({**p, 'itens': fetchall(cur)})
    conn.close(); return jsonify(result)

@app.route('/api/admin/pedidos/<int:pid>', methods=['PATCH'])
@login_required
def editar_pedido(pid):
    d = request.get_json()
    conn = get_connection()
    cur = conn.cursor()

    for col in ['nome_cliente','telefone','observacao','endereco','forma_pagamento']:
        if col in d:
            cur.execute(f"UPDATE pedidos SET {col}=%s WHERE id=%s", (d[col], pid))

    # Edição de itens do pedido
    if 'itens' in d:
        novos_itens = d['itens']
        subtotal = 0.0

        cur.execute("DELETE FROM itens_pedido WHERE pedido_id=%s", (pid,))

        for item in novos_itens:
            cur.execute("SELECT preco, preco_promo, em_promo FROM produtos WHERE id=%s", (item['produto_id'],))
            p = cur.fetchone()

            if p:
                preco = float(p[1] or p[0]) if p[2] else float(p[0])
                qty = int(item['quantidade'])
                subtotal += preco * qty

                cur.execute(
                    "INSERT INTO itens_pedido (pedido_id,produto_id,quantidade,preco_unit) VALUES (%s,%s,%s,%s)",
                    (pid, item['produto_id'], qty, preco)
                )

        frete = float(d.get('frete', 0))

        # desconto_override: valor de desconto enviado pelo frontend (cupom/pdv)
        desconto_override = d.get('desconto_override', 0)
        desconto = float(desconto_override) if desconto_override else 0.0

        total = round(max(0, subtotal + frete - desconto), 2)

        cur.execute(
            "UPDATE pedidos SET subtotal=%s, frete=%s, total=%s WHERE id=%s",
            (round(subtotal,2), frete, total, pid)
        )

    elif 'frete' in d:
        frete = float(d['frete'])

        cur.execute("SELECT subtotal FROM pedidos WHERE id=%s", (pid,))
        row = cur.fetchone()

        if row:
            subtotal = float(row[0] or 0)

            cur.execute(
                "UPDATE pedidos SET frete=%s, total=%s WHERE id=%s",
                (frete, round(subtotal+frete,2), pid)
            )

    conn.commit()
    conn.close()

    return jsonify({'ok': True})

@app.route('/api/admin/pedidos/<int:pid>', methods=['DELETE'])
@login_required
def deletar_pedido(pid):
    conn = get_connection(); cur = conn.cursor()
    cur.execute("DELETE FROM itens_pedido WHERE pedido_id=%s", (pid,))
    cur.execute("DELETE FROM financeiro    WHERE pedido_id=%s", (pid,))
    cur.execute("DELETE FROM pedidos       WHERE id=%s",        (pid,))
    conn.commit(); conn.close(); return jsonify({'ok': True})

@app.route('/api/admin/pedidos/<int:pid>/status', methods=['PATCH'])
@login_required
def atualizar_status(pid):
    d = request.get_json()
    status = d.get('status'); fp = d.get('forma_pagamento')
    if status not in ('pendente','aceito','em_preparo','pronto','em_rota','entregue','cancelado'):
        return jsonify({'erro': 'Status inválido'}), 400
    conn = get_connection(); cur = conn.cursor()
    if fp:
        cur.execute("UPDATE pedidos SET status=%s, forma_pagamento=%s WHERE id=%s", (status, fp, pid))
    else:
        cur.execute("UPDATE pedidos SET status=%s WHERE id=%s", (status, pid))
    # Gera financeiro ao ACEITAR
    if status == 'aceito':
        cur.execute("SELECT * FROM pedidos WHERE id=%s", (pid,)); pedido = fetchone(cur)
        fpag = fp or pedido.get('forma_pagamento') or 'Não informado'
        cliente_id = None
        if pedido.get('telefone'):
            cur.execute("SELECT id FROM clientes WHERE telefone=%s", (pedido['telefone'],))
            cl = cur.fetchone()
            if cl: cliente_id = cl[0]
        cur.execute("SELECT id FROM financeiro WHERE pedido_id=%s", (pid,))
        if not cur.fetchone():
            # Suporte a múltiplos pagamentos: "Dinheiro R$50.00 + PIX R$30.00"
            import re as _re
            partes = [p.strip() for p in fpag.split('+')]
            total_ped = pedido['total']
            if len(partes) > 1:
                # Múltiplos: inserir um registro por forma
                for parte in partes:
                    m = _re.match(r'^(.+?)\s+R\$([\d,.]+)$', parte)
                    if m:
                        tipo_pgto = m.group(1).strip()
                        val = float(m.group(2).replace(',', '.'))
                    else:
                        tipo_pgto = parte
                        val = total_ped / len(partes)
                    cur.execute(
                        "INSERT INTO financeiro (pedido_id,cliente_id,valor,tipo,forma_pagamento,descricao,pago) VALUES (%s,%s,%s,'entrada',%s,%s,1)",
                        (pid, cliente_id, round(val, 2), tipo_pgto, f'Pedido {pid} - {tipo_pgto}'))
            else:
                cur.execute(
                    "INSERT INTO financeiro (pedido_id,cliente_id,valor,tipo,forma_pagamento,descricao,pago) VALUES (%s,%s,%s,'entrada',%s,%s,1)",
                    (pid, cliente_id, total_ped, fpag, f'Pedido {pid}'))
    conn.commit(); conn.close(); return jsonify({'ok': True})

# ── PRODUTOS ──────────────────────────────────────────────────────────────────
@app.route('/api/admin/produtos')
@login_required
def listar_produtos():
    conn = get_connection(); cur = conn.cursor()
    cur.execute("SELECT * FROM categorias ORDER BY ordem"); cats = fetchall(cur)
    cur.execute("SELECT * FROM produtos");                  prods = fetchall(cur)
    conn.close(); return jsonify({'categorias': cats, 'produtos': prods})

@app.route('/api/admin/produto', methods=['POST'])
@login_required
def criar_produto():
    d = request.get_json()
    foto = save_image(d.get('foto_base64'), f"prod_{d['nome'].replace(' ','_')}.jpg") if d.get('foto_base64') else None
    conn = get_connection(); cur = conn.cursor()
    cur.execute("INSERT INTO produtos (categoria_id,nome,descricao,preco,foto_url,preco_promo,em_promo) VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id",
        (d['categoria_id'],d['nome'],d.get('descricao',''),float(d['preco']),foto,
         float(d['preco_promo']) if d.get('preco_promo') else None, 1 if d.get('em_promo') else 0))
    nid = cur.fetchone()[0]; conn.commit(); conn.close()
    return jsonify({'id': nid}), 201

@app.route('/api/admin/produto/<int:pid>', methods=['PATCH'])
@login_required
def editar_produto(pid):
    d = request.get_json(); conn = get_connection(); cur = conn.cursor()
    for col in ['disponivel','preco','nome','descricao','categoria_id']:
        if col in d:
            val = (1 if d[col] else 0) if col == 'disponivel' else (float(d[col]) if col in ('preco',) else d[col])
            cur.execute(f"UPDATE produtos SET {col}=%s WHERE id=%s", (val, pid))
    if 'em_promo'    in d: cur.execute("UPDATE produtos SET em_promo=%s    WHERE id=%s", (1 if d['em_promo'] else 0, pid))
    if 'preco_promo' in d: cur.execute("UPDATE produtos SET preco_promo=%s WHERE id=%s",
        (float(d['preco_promo']) if d['preco_promo'] else None, pid))
    if d.get('foto_base64'):
        cur.execute("UPDATE produtos SET foto_url=%s WHERE id=%s", (save_image(d['foto_base64'], f"prod_{pid}.jpg"), pid))
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
    tp=request.args.get('tipo',''); pg=request.args.get('pago','')
    di=request.args.get('data_ini',''); df=request.args.get('data_fim','')
    emp=request.args.get('empresa_id','')
    conn = get_connection(); cur = conn.cursor()
    q = '''SELECT f.*,
        c.nome as cliente_nome,c.telefone as cliente_tel,
        e.nome as empresa_nome,e.tipo as empresa_tipo
        FROM financeiro f
        LEFT JOIN clientes c ON c.id=f.cliente_id
        LEFT JOIN empresas e ON e.id=f.empresa_id
        WHERE 1=1'''
    params = []
    if tp: q += " AND f.tipo=%s"; params.append(tp)
    if pg != '': q += " AND f.pago=%s"; params.append(int(pg))
    if di: q += " AND COALESCE(f.data_lancamento,f.criado_em) >= %s"; params.append(di)
    if df: q += " AND COALESCE(f.data_lancamento,f.criado_em) <= %s"; params.append(df+' 23:59:59')
    if emp: q += " AND f.empresa_id=%s"; params.append(int(emp))
    q += " ORDER BY COALESCE(f.data_lancamento,f.criado_em) DESC"
    cur.execute(q, params); result = fetchall(cur); conn.close()
    return jsonify(result)

@app.route('/api/admin/financeiro', methods=['POST'])
@login_required
def criar_lancamento():
    d = request.get_json(); conn = get_connection(); cur = conn.cursor()
    dl = d.get('data_lancamento') or None
    emp_id = d.get('empresa_id') or None
    if dl:
        cur.execute("INSERT INTO financeiro (valor,tipo,forma_pagamento,descricao,observacao,pago,data_lancamento,criado_em,empresa_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (float(d['valor']),d.get('tipo','entrada'),d.get('forma_pagamento',''),d.get('descricao',''),d.get('observacao',''),1 if d.get('pago',True) else 0,dl,dl,emp_id))
    else:
        cur.execute("INSERT INTO financeiro (valor,tipo,forma_pagamento,descricao,observacao,pago,empresa_id) VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (float(d['valor']),d.get('tipo','entrada'),d.get('forma_pagamento',''),d.get('descricao',''),d.get('observacao',''),1 if d.get('pago',True) else 0,emp_id))
    nid = cur.fetchone()[0]; conn.commit(); conn.close()
    return jsonify({'id': nid}), 201

@app.route('/api/admin/financeiro/<int:fid>', methods=['PATCH'])
@login_required
def editar_lancamento(fid):
    d = request.get_json(); conn = get_connection(); cur = conn.cursor()
    for col in ['tipo','forma_pagamento','descricao','observacao','data_lancamento','empresa_id']:
        if col in d: cur.execute(f"UPDATE financeiro SET {col}=%s WHERE id=%s", (d[col], fid))
    if 'valor' in d: cur.execute("UPDATE financeiro SET valor=%s WHERE id=%s", (float(d['valor']), fid))
    if 'pago'  in d: cur.execute("UPDATE financeiro SET pago=%s  WHERE id=%s", (1 if d['pago'] else 0, fid))
    conn.commit(); conn.close(); return jsonify({'ok': True})

@app.route('/api/admin/financeiro/<int:fid>', methods=['DELETE'])
@login_required
def deletar_lancamento(fid):
    conn = get_connection(); cur = conn.cursor()
    cur.execute("DELETE FROM financeiro WHERE id=%s AND pedido_id IS NULL", (fid,))
    conn.commit(); conn.close(); return jsonify({'ok': True})

# ── EMPRESAS / CLIENTES / FORNECEDORES ───────────────────────────────────────
# Todas as rotas usam a tabela 'clientes' com coluna 'tipo'

@app.route('/api/admin/clientes')
@login_required
def listar_clientes():
    tipo = request.args.get('tipo', '')
    conn = get_connection(); cur = conn.cursor()
    if tipo:
        cur.execute("SELECT * FROM clientes WHERE tipo=%s ORDER BY nome", (tipo,))
    else:
        cur.execute("SELECT * FROM clientes ORDER BY nome")
    result = fetchall(cur); conn.close(); return jsonify(result)

@app.route('/api/admin/empresa', methods=['POST'])
@login_required
def criar_empresa():
    d = request.get_json()
    nome = (d.get('nome') or '').strip()
    if not nome: return jsonify({'erro': 'Nome obrigatório'}), 400
    tel  = (d.get('telefone') or '').strip() or None
    tipo = d.get('tipo', 'cliente')
    conn = get_connection(); cur = conn.cursor()
    cpf_cnpj = (d.get('cpf_cnpj') or '').strip() or None
    if tel:
        cur.execute(
            "INSERT INTO clientes (telefone,nome,tipo,cpf_cnpj) VALUES (%s,%s,%s,%s) "
            "ON CONFLICT (telefone) DO UPDATE SET nome=EXCLUDED.nome,tipo=EXCLUDED.tipo,cpf_cnpj=EXCLUDED.cpf_cnpj RETURNING id",
            (tel, nome, tipo, cpf_cnpj))
    else:
        import time
        cur.execute(
            "INSERT INTO clientes (telefone,nome,tipo,cpf_cnpj) VALUES (%s,%s,%s,%s) RETURNING id",
            (f'manual_{int(time.time())}', nome, tipo, cpf_cnpj))
    nid = cur.fetchone()[0]; conn.commit(); conn.close()
    return jsonify({'id': nid}), 201

@app.route('/api/admin/cliente/<int:cid>', methods=['PATCH'])
@login_required
def editar_cliente(cid):
    d = request.get_json(); conn = get_connection(); cur = conn.cursor()
    for col in ['nome', 'email', 'endereco', 'tipo', 'cpf_cnpj']:
        if col in d: cur.execute(f"UPDATE clientes SET {col}=%s WHERE id=%s", (d[col], cid))
    conn.commit(); conn.close(); return jsonify({'ok': True})

@app.route('/api/admin/cliente/<int:cid>', methods=['DELETE'])
@login_required
def deletar_cliente(cid):
    conn = get_connection(); cur = conn.cursor()
    cur.execute("UPDATE financeiro SET empresa_id=NULL WHERE empresa_id=%s", (cid,))
    cur.execute("DELETE FROM clientes WHERE id=%s AND telefone LIKE 'manual_%'", (cid,))
    conn.commit(); conn.close(); return jsonify({'ok': True})


if __name__ == '__main__':
    print("\n  Cardapio Digital em http://localhost:5000")
    print("  Admin em http://localhost:5000/admin\n")
    app.run(debug=True, port=5000)
