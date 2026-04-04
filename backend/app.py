from flask import Flask, jsonify, request, send_from_directory, session
from flask_cors import CORS
from database import get_connection, init_db, fetchall, fetchone
import os, sys, base64, json, hashlib, functools, time, secrets, re

sys.path.insert(0, os.path.dirname(__file__))
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

app = Flask(__name__,
    static_folder=os.path.join(BASE, 'frontend', 'static'),
    template_folder=os.path.join(BASE, 'frontend', 'templates'))

app.secret_key = os.environ.get('SECRET_KEY', 'cardapio-saas-secret-key-2024-xpto')
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['PERMANENT_SESSION_LIFETIME'] = 86400
CORS(app, supports_credentials=True, origins='*',
     allow_headers=['Content-Type'], expose_headers=['Set-Cookie'])

UPLOADS_DIR = os.path.join(BASE, 'uploads')
os.makedirs(UPLOADS_DIR, exist_ok=True)
init_db()

# Tokens de recuperação de senha em memória
reset_tokens = {}

# ── HELPERS ───────────────────────────────────────────────────────────────────
def slugify(text):
    text = text.lower().strip()
    text = re.sub(r'[àáâãä]','a', text)
    text = re.sub(r'[èéêë]','e', text)
    text = re.sub(r'[ìíîï]','i', text)
    text = re.sub(r'[òóôõö]','o', text)
    text = re.sub(r'[ùúûü]','u', text)
    text = re.sub(r'[ç]','c', text)
    text = re.sub(r'[^a-z0-9]+','-', text)
    return text.strip('-')

def get_platform_config():
    try:
        conn = get_connection(); cur = conn.cursor()
        cur.execute("SELECT * FROM platform_config WHERE id=1")
        cfg = fetchone(cur); conn.close()
        return cfg or {}
    except: return {}

def detect_mime(b64_data):
    """Detect MIME type from base64 data URI or raw bytes prefix."""
    if b64_data.startswith('data:'):
        mime = b64_data.split(';')[0].split(':')[1]
        return mime
    try:
        header = base64.b64decode(b64_data[:16])
        if header[:4] == b'\x89PNG': return 'image/png'
        if header[:2] in (b'\xff\xd8',): return 'image/jpeg'
        if header[:4] == b'GIF8': return 'image/gif'
        if header[:4] == b'RIFF' and header[8:12] == b'WEBP': return 'image/webp'
    except: pass
    return 'image/jpeg'

def upload_to_supabase(b64_data, file_path):
    """Upload base64 image to Supabase Storage. Returns public URL."""
    try:
        import requests as req
        pcfg = get_platform_config()
        supa_url = (pcfg.get('supabase_url') or '').rstrip('/')
        supa_key = pcfg.get('supabase_key') or ''
        bucket   = pcfg.get('supabase_bucket') or 'cardapio'
        print(f"[Supabase] url={supa_url} bucket={bucket} key={'***' if supa_key else 'EMPTY'}")
        if not supa_url or not supa_key:
            print("[Supabase] Sem configuração — salvando local")
            return save_image_local(b64_data, os.path.basename(file_path))
        mime = detect_mime(b64_data)
        if ',' in b64_data: b64_data = b64_data.split(',')[1]
        img_bytes = base64.b64decode(b64_data)
        upload_url = f"{supa_url}/storage/v1/object/{bucket}/{file_path}"
        headers = {
            'Authorization': f'Bearer {supa_key}',
            'Content-Type': mime,
            'x-upsert': 'true'
        }
        print(f"[Supabase] PUT {upload_url} ({mime}, {len(img_bytes)} bytes)")
        r = req.put(upload_url, data=img_bytes, headers=headers, timeout=30)
        print(f"[Supabase] Response: {r.status_code} — {r.text[:300]}")
        if r.status_code in (200, 201):
            pub_url = f"{supa_url}/storage/v1/object/public/{bucket}/{file_path}"
            print(f"[Supabase] Upload OK: {pub_url}")
            return pub_url
        print(f"[Supabase] Upload falhou ({r.status_code}) — salvando local")
        return save_image_local(b64_data, os.path.basename(file_path))
    except Exception as e:
        print(f"[Supabase] Erro: {e}")
        return save_image_local(b64_data, os.path.basename(file_path))

def save_image_local(b64, filename):
    if not b64: return None
    if ',' in b64: b64 = b64.split(',')[1]
    path = os.path.join(UPLOADS_DIR, filename)
    with open(path, 'wb') as f: f.write(base64.b64decode(b64))
    return f'/uploads/{filename}'

def save_image(b64, filename, cliente_id=None):
    """Save image to Supabase if configured, else local."""
    if not b64: return None
    if cliente_id:
        file_path = f"uploads/cliente_{cliente_id}/{filename}"
    else:
        file_path = f"uploads/{filename}"
    return upload_to_supabase(b64, file_path)

# ── DECORATORS ────────────────────────────────────────────────────────────────
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

def super_required(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('super_logged'):
            return jsonify({'erro': 'Acesso não autorizado'}), 401
        return f(*args, **kwargs)
    return decorated

def get_cliente_id():
    """Get cliente_id from current session (tenant context)."""
    return session.get('cliente_id')

def tenant_check(slug):
    """Verify tenant is active. Returns (cliente_saas_row, error_response)."""
    conn = get_connection(); cur = conn.cursor()
    cur.execute("SELECT * FROM clientes_saas WHERE slug=%s", (slug,))
    tenant = fetchone(cur); conn.close()
    if not tenant:
        return None, (jsonify({'erro': 'Tenant não encontrado'}), 404)
    if tenant['status'] == 'suspenso':
        return None, (jsonify({'erro': 'Assinatura suspensa. Entre em contato.'}), 403)
    if tenant['status'] == 'inativo':
        return None, (jsonify({'erro': 'Conta inativa.'}), 403)
    return tenant, None

# ── FRONTEND ROUTES ───────────────────────────────────────────────────────────
@app.route('/')
def index():
    return send_from_directory(os.path.join(BASE, 'frontend', 'templates'), 'cardapio.html')

@app.route('/Admin')
@app.route('/admin')
def super_admin_page():
    return send_from_directory(os.path.join(BASE, 'frontend', 'templates'), 'superadmin.html')

@app.route('/login')
def tenant_login_page():
    return send_from_directory(os.path.join(BASE, 'frontend', 'templates'), 'admin.html')

_RESERVED = {'uploads', 'static', 'api', 'favicon.ico', 'Admin', 'admin', 'login'}

@app.route('/<slug>')
def tenant_cardapio(slug):
    if slug in _RESERVED:
        return ('', 404)
    return send_from_directory(os.path.join(BASE, 'frontend', 'templates'), 'cardapio.html')

@app.route('/<slug>/painel')
def tenant_admin(slug):
    if slug in _RESERVED:
        return ('', 404)
    return send_from_directory(os.path.join(BASE, 'frontend', 'templates'), 'admin.html')

@app.route('/uploads/<path:filename>')
def uploads(filename):
    return send_from_directory(UPLOADS_DIR, filename)

# ── VISITAS ───────────────────────────────────────────────────────────────────
VISITAS_FILE = os.path.join(BASE, 'visitas.json')

def load_visitas():
    if os.path.exists(VISITAS_FILE):
        with open(VISITAS_FILE, 'r') as f: return json.load(f)
    return {'total': 0, 'ips': [], 'datas': {}}

def save_visitas(data):
    with open(VISITAS_FILE, 'w') as f: json.dump(data, f)

@app.route('/api/visita', methods=['POST'])
def registrar_visita():
    import datetime
    ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    if ip: ip = ip.split(',')[0].strip()
    v = load_visitas()
    if 'datas' not in v: v['datas'] = {}
    if ip and ip not in v['ips']:
        v['ips'].append(ip)
        v['datas'][ip] = datetime.date.today().isoformat()
        v['total'] = len(v['ips'])
        save_visitas(v)
    return jsonify({'total': v['total']})

@app.route('/api/visitas')
def get_visitas():
    v = load_visitas()
    ini = request.args.get('ini')
    fim = request.args.get('fim')
    if ini and fim and v.get('datas'):
        total = sum(1 for d in v['datas'].values() if ini <= d <= fim)
    else:
        total = v['total']
    return jsonify({'total': total})

# ── SUPER ADMIN AUTH ──────────────────────────────────────────────────────────
@app.route('/api/super/auth/login', methods=['POST'])
def super_login():
    d = request.get_json()
    senha = hashlib.sha256((d.get('senha') or '').encode()).hexdigest()
    conn = get_connection(); cur = conn.cursor()
    cur.execute("SELECT * FROM super_admin WHERE usuario=%s AND senha=%s",
                (d.get('usuario','').strip(), senha))
    adm = fetchone(cur); conn.close()
    if not adm: return jsonify({'erro': 'Usuário ou senha incorretos'}), 401
    session.permanent = True
    session['super_logged'] = True
    session['super_usuario'] = adm['usuario']
    session['super_nome'] = adm.get('nome') or adm['usuario']
    return jsonify({'ok': True, 'nome': session['super_nome']})

@app.route('/api/super/auth/logout', methods=['POST'])
def super_logout():
    session.pop('super_logged', None)
    session.pop('super_usuario', None)
    session.pop('super_nome', None)
    return jsonify({'ok': True})

@app.route('/api/super/auth/check')
def super_check():
    return jsonify({
        'logado': bool(session.get('super_logged')),
        'nome': session.get('super_nome', '')
    })

@app.route('/api/super/auth/senha', methods=['POST'])
@super_required
def super_alterar_senha():
    d = request.get_json()
    nova = hashlib.sha256((d.get('nova','') or '').encode()).hexdigest()
    conn = get_connection(); cur = conn.cursor()
    cur.execute("UPDATE super_admin SET senha=%s WHERE usuario=%s",
                (nova, session.get('super_usuario')))
    conn.commit(); conn.close()
    return jsonify({'ok': True})

# ── SUPER ADMIN: PLATAFORMA CONFIG ───────────────────────────────────────────
@app.route('/api/super/config')
@super_required
def super_get_config():
    pcfg = get_platform_config()
    # Nunca expor senha SMTP
    safe = {k: v for k, v in (pcfg or {}).items() if k != 'smtp_password'}
    return jsonify(safe)

@app.route('/api/super/config', methods=['POST'])
@super_required
def super_set_config():
    d = request.get_json()
    conn = get_connection(); cur = conn.cursor()
    cur.execute("SELECT id FROM platform_config WHERE id=1")
    exists = cur.fetchone()
    fields = ['smtp_host','smtp_port','smtp_user','smtp_from',
              'supabase_url','supabase_key','supabase_bucket',
              'webhook_secret','webhook_url']
    if d.get('smtp_password'):
        fields.append('smtp_password')
    set_parts = ', '.join(f"{f}=%s" for f in fields)
    vals = [d.get(f) for f in fields]
    if exists:
        cur.execute(f"UPDATE platform_config SET {set_parts} WHERE id=1", vals)
    else:
        all_fields = ', '.join(fields) + ', id'
        placeholders = ', '.join(['%s']*len(fields)) + ', 1'
        cur.execute(f"INSERT INTO platform_config ({all_fields}) VALUES ({placeholders})", vals)
    conn.commit(); conn.close()
    return jsonify({'ok': True})

@app.route('/api/super/config/test-supabase', methods=['POST'])
@super_required
def super_test_supabase():
    """Testa conectividade com Supabase Storage."""
    try:
        import requests as req
        pcfg = get_platform_config()
        supa_url = (pcfg.get('supabase_url') or '').rstrip('/')
        supa_key = pcfg.get('supabase_key') or ''
        bucket   = pcfg.get('supabase_bucket') or 'cardapio'
        if not supa_url or not supa_key:
            return jsonify({'ok': False, 'erro': 'URL ou chave não configurados'})
        # Tentar listar objetos do bucket
        r = req.get(f"{supa_url}/storage/v1/bucket/{bucket}",
                    headers={'Authorization': f'Bearer {supa_key}'}, timeout=10)
        if r.status_code == 200:
            return jsonify({'ok': True, 'msg': f'Bucket "{bucket}" acessível'})
        return jsonify({'ok': False, 'erro': f'HTTP {r.status_code}: {r.text[:200]}'})
    except Exception as e:
        return jsonify({'ok': False, 'erro': str(e)})

# ── SUPER ADMIN: CLIENTES/ASSINANTES ─────────────────────────────────────────
@app.route('/api/super/clientes')
@super_required
def super_listar_clientes():
    conn = get_connection(); cur = conn.cursor()
    cur.execute("""SELECT cs.*,
        (SELECT COUNT(*) FROM pedidos p WHERE p.cliente_id=cs.id) as total_pedidos,
        (SELECT COUNT(*) FROM admin a WHERE a.cliente_id=cs.id) as total_usuarios
        FROM clientes_saas cs ORDER BY cs.criado_em DESC""")
    result = fetchall(cur); conn.close()
    return jsonify(result)

@app.route('/api/super/clientes', methods=['POST'])
@super_required
def super_criar_cliente():
    d = request.get_json()
    nome = (d.get('nome') or '').strip()
    email = (d.get('email') or '').strip() or None
    telefone = (d.get('telefone') or '').strip() or None
    cpf_cnpj = (d.get('cpf_cnpj') or '').strip() or None
    plano = d.get('plano', 'basico')
    senha_plain = (d.get('senha') or '').strip()
    observacao = (d.get('observacao') or '').strip() or None
    data_vencimento = d.get('data_vencimento') or None

    if not nome: return jsonify({'erro': 'Nome obrigatório'}), 400
    if not email: return jsonify({'erro': 'E-mail obrigatório (usado como login)'}), 400
    if not senha_plain: return jsonify({'erro': 'Senha obrigatória'}), 400

    slug = slugify(nome)
    senha = hashlib.sha256(senha_plain.encode()).hexdigest()
    data_entrada = time.strftime('%Y-%m-%d')

    conn = get_connection(); cur = conn.cursor()
    # Garantir slug único
    base_slug = slug
    for i in range(1, 100):
        cur.execute("SELECT id FROM clientes_saas WHERE slug=%s", (slug,))
        if not cur.fetchone(): break
        slug = f"{base_slug}-{i}"

    try:
        cur.execute("""INSERT INTO clientes_saas
            (nome,email,telefone,cpf_cnpj,slug,senha,status,plano,data_entrada,data_vencimento,observacao)
            VALUES (%s,%s,%s,%s,%s,%s,'ativo',%s,%s,%s,%s) RETURNING id""",
            (nome,email,telefone,cpf_cnpj,slug,senha,plano,data_entrada,data_vencimento,observacao))
        cid = cur.fetchone()[0]

        # Criar config_tenant padrão
        cur.execute("""INSERT INTO config_tenant (cliente_id,nome_loja,tipos_pagamento)
            VALUES (%s,%s,'Dinheiro,PIX,Cartão de Débito,Cartão de Crédito')""",
            (cid, nome))

        # Criar admin padrão para o tenant (login = email do assinante)
        cur.execute("""INSERT INTO admin (cliente_id,usuario,senha,role,nome,email)
            VALUES (%s,%s,%s,'admin',%s,%s)""",
            (cid, email, senha, nome, email))

        # Criar categorias demo
        cur.execute("INSERT INTO categorias (cliente_id,nome,ordem) VALUES (%s,'Lanches',1) RETURNING id", (cid,))
        cat1 = cur.fetchone()[0]
        cur.execute("INSERT INTO categorias (cliente_id,nome,ordem) VALUES (%s,'Bebidas',2) RETURNING id", (cid,))
        cat2 = cur.fetchone()[0]
        cur.executemany("INSERT INTO produtos (cliente_id,categoria_id,nome,descricao,preco) VALUES (%s,%s,%s,%s,%s)",[
            (cid,cat1,"Produto Demo","Descrição do produto",10.00),
            (cid,cat2,"Bebida Demo","Lata 350ml",5.00)])

        conn.commit(); conn.close()
        return jsonify({'id': cid, 'slug': slug}), 201
    except Exception as e:
        conn.close()
        return jsonify({'erro': f'Erro ao criar cliente: {str(e)}'}), 409

@app.route('/api/super/clientes/<int:cid>', methods=['GET'])
@super_required
def super_get_cliente(cid):
    conn = get_connection(); cur = conn.cursor()
    cur.execute("SELECT * FROM clientes_saas WHERE id=%s", (cid,))
    c = fetchone(cur)
    if not c: conn.close(); return jsonify({'erro': 'Não encontrado'}), 404
    cur.execute("SELECT * FROM config_tenant WHERE cliente_id=%s", (cid,))
    cfg = fetchone(cur)
    cur.execute("SELECT usuario FROM admin WHERE cliente_id=%s AND role='admin' LIMIT 1", (cid,))
    adm = cur.fetchone()
    conn.close()
    if c: c['admin_usuario'] = adm[0] if adm else 'admin'
    return jsonify({'cliente': c, 'config': cfg})

@app.route('/api/super/clientes/<int:cid>', methods=['PATCH'])
@super_required
def super_editar_cliente(cid):
    d = request.get_json()
    conn = get_connection(); cur = conn.cursor()
    cols = ['nome','email','telefone','cpf_cnpj','status','plano','data_vencimento','observacao','webhook_ref']
    for col in cols:
        if col in d:
            cur.execute(f"UPDATE clientes_saas SET {col}=%s WHERE id=%s", (d[col] or None, cid))
    if d.get('senha'):
        senha = hashlib.sha256(d['senha'].encode()).hexdigest()
        cur.execute("UPDATE clientes_saas SET senha=%s WHERE id=%s", (senha, cid))
        # Atualizar senha do admin principal junto
        cur.execute("UPDATE admin SET senha=%s WHERE cliente_id=%s AND role='admin'", (senha, cid))
    # Sincronizar login do admin quando email muda
    if d.get('email'):
        novo_email = d['email'].strip()
        if novo_email:
            cur.execute("UPDATE admin SET usuario=%s, email=%s WHERE cliente_id=%s AND role='admin'", (novo_email, novo_email, cid))
    # Atualizar slug se nome mudou
    if d.get('nome'):
        new_slug = slugify(d['nome'])
        cur.execute("SELECT id FROM clientes_saas WHERE slug=%s AND id!=%s", (new_slug, cid))
        if not cur.fetchone():
            cur.execute("UPDATE clientes_saas SET slug=%s WHERE id=%s", (new_slug, cid))
    conn.commit(); conn.close()
    return jsonify({'ok': True})

@app.route('/api/super/clientes/<int:cid>', methods=['DELETE'])
@super_required
def super_deletar_cliente(cid):
    conn = get_connection(); cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM pedidos WHERE cliente_id=%s", (cid,))
    if cur.fetchone()[0] > 0:
        conn.close()
        return jsonify({'erro': 'Não é possível excluir: o cliente possui pedidos vinculados.'}), 400
    # Cascade via FK, mas garantir ordem
    try:
        cur.execute("DELETE FROM financeiro WHERE cliente_id=%s", (cid,))
        cur.execute("DELETE FROM itens_pedido WHERE cliente_id=%s", (cid,))
        cur.execute("DELETE FROM pedidos WHERE cliente_id=%s", (cid,))
        cur.execute("DELETE FROM produtos WHERE cliente_id=%s", (cid,))
        cur.execute("DELETE FROM categorias WHERE cliente_id=%s", (cid,))
        cur.execute("DELETE FROM clientes WHERE cliente_id=%s", (cid,))
        cur.execute("DELETE FROM admin WHERE cliente_id=%s", (cid,))
        cur.execute("DELETE FROM config_tenant WHERE cliente_id=%s", (cid,))
        cur.execute("DELETE FROM clientes_saas WHERE id=%s", (cid,))
        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        return jsonify({'erro': str(e)}), 400
    conn.close()
    return jsonify({'ok': True})

# ── SUPER ADMIN: DASHBOARD ────────────────────────────────────────────────────
@app.route('/api/super/dashboard')
@super_required
def super_dashboard():
    conn = get_connection(); cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM clientes_saas")
    total = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM clientes_saas WHERE status='ativo'")
    ativos = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM clientes_saas WHERE status='suspenso'")
    suspensos = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM clientes_saas WHERE status='inativo'")
    inativos = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM clientes_saas WHERE criado_em >= to_char(NOW() - INTERVAL '30 days', 'YYYY-MM-DD')")
    novos_30d = cur.fetchone()[0]
    # Novos por mês (últimos 6 meses)
    cur.execute("""SELECT to_char(criado_em::date,'YYYY-MM') as mes, COUNT(*) as qtd
        FROM clientes_saas WHERE criado_em >= to_char(NOW() - INTERVAL '6 months','YYYY-MM-DD')
        GROUP BY mes ORDER BY mes""")
    por_mes = fetchall(cur)
    conn.close()
    return jsonify({
        'total': total, 'ativos': ativos, 'suspensos': suspensos,
        'inativos': inativos, 'novos_30d': novos_30d, 'por_mes': por_mes
    })

# ── SUPER ADMIN: WEBHOOK ──────────────────────────────────────────────────────
@app.route('/api/webhook/pagamento', methods=['POST'])
def webhook_pagamento():
    pcfg = get_platform_config()
    secret = pcfg.get('webhook_secret') or ''
    # Verificar assinatura se configurado
    if secret:
        sig = request.headers.get('X-Webhook-Signature') or request.headers.get('X-Signature','')
        import hmac as _hmac
        expected = _hmac.new(secret.encode(), request.data, hashlib.sha256).hexdigest()
        if not _hmac.compare_digest(sig, expected):
            return jsonify({'erro': 'Assinatura inválida'}), 401

    d = request.get_json(silent=True) or {}
    event = d.get('event') or d.get('type') or ''
    ref = d.get('reference') or d.get('id') or d.get('external_id') or ''
    status_map = {
        'payment.approved': 'ativo',
        'payment.refunded': 'suspenso',
        'payment.failed': 'suspenso',
        'subscription.canceled': 'inativo',
        'subscription.activated': 'ativo',
        'charge.paid': 'ativo',
        'charge.failed': 'suspenso',
    }
    new_status = status_map.get(event)
    if new_status and ref:
        conn = get_connection(); cur = conn.cursor()
        cur.execute("UPDATE clientes_saas SET status=%s WHERE webhook_ref=%s OR email=%s",
                    (new_status, ref, ref))
        conn.commit(); conn.close()
    return jsonify({'ok': True})

# ── TENANT AUTH ───────────────────────────────────────────────────────────────
@app.route('/api/auth/login', methods=['POST'])
def login():
    d = request.get_json()
    senha = hashlib.sha256((d.get('senha','') or '').encode()).hexdigest()
    slug = d.get('slug') or session.get('tenant_slug') or ''
    conn = get_connection(); cur = conn.cursor()

    if slug:
        cur.execute("SELECT * FROM clientes_saas WHERE slug=%s", (slug,))
        tenant = fetchone(cur)
        if not tenant:
            conn.close(); return jsonify({'erro': 'Loja não encontrada'}), 404
        if tenant['status'] in ('suspenso', 'inativo'):
            conn.close(); return jsonify({'erro': 'Acesso bloqueado. Verifique sua assinatura.'}), 403
        cur.execute("SELECT * FROM admin WHERE usuario=%s AND senha=%s AND cliente_id=%s",
                    (d.get('usuario','').strip(), senha, tenant['id']))
    else:
        # Fallback: buscar sem slug (compatibilidade)
        cur.execute("SELECT * FROM admin WHERE usuario=%s AND senha=%s",
                    (d.get('usuario','').strip(), senha))

    adm = fetchone(cur)
    if not adm:
        conn.close(); return jsonify({'erro': 'Usuário ou senha incorretos'}), 401

    # Buscar tenant do admin
    cid = adm.get('cliente_id')
    if cid:
        cur.execute("SELECT * FROM clientes_saas WHERE id=%s", (cid,))
        tenant = fetchone(cur)
        if tenant and tenant['status'] in ('suspenso','inativo'):
            conn.close(); return jsonify({'erro': 'Acesso bloqueado. Verifique sua assinatura.'}), 403
        tenant_slug = tenant['slug'] if tenant else ''
    else:
        tenant_slug = slug

    conn.close()
    session.permanent = True
    session['admin_logged'] = True
    session['admin_usuario'] = d.get('usuario','').strip()
    session['admin_role'] = adm.get('role') or 'admin'
    session['admin_nome'] = adm.get('nome') or adm.get('usuario','')
    session['cliente_id'] = cid
    session['tenant_slug'] = tenant_slug
    perms_raw = adm.get('permissions') or '{}'
    try: session['admin_permissions'] = json.loads(perms_raw) if isinstance(perms_raw, str) else perms_raw
    except: session['admin_permissions'] = {}
    return jsonify({
        'ok': True,
        'role': session['admin_role'],
        'nome': session['admin_nome'],
        'permissions': session['admin_permissions'],
        'slug': tenant_slug,
        'cliente_id': cid
    })

@app.route('/api/auth/logout', methods=['POST'])
def logout():
    session.clear(); return jsonify({'ok': True})

@app.route('/api/auth/check')
def check_auth():
    return jsonify({
        'logado': bool(session.get('admin_logged')),
        'role': session.get('admin_role', 'admin'),
        'nome': session.get('admin_nome', ''),
        'permissions': session.get('admin_permissions', {}),
        'slug': session.get('tenant_slug', ''),
        'cliente_id': session.get('cliente_id')
    })

@app.route('/api/auth/senha', methods=['POST'])
@login_required
def alterar_senha():
    d = request.get_json()
    nova = hashlib.sha256((d.get('nova','') or '').encode()).hexdigest()
    conn = get_connection(); cur = conn.cursor()
    cur.execute("UPDATE admin SET senha=%s WHERE usuario=%s AND cliente_id=%s",
                (nova, session.get('admin_usuario'), get_cliente_id()))
    conn.commit(); conn.close()
    return jsonify({'ok': True})

@app.route('/api/auth/forgot-password', methods=['POST'])
def forgot_password():
    d = request.get_json()
    email = (d.get('email') or '').strip()
    if not email: return jsonify({'erro': 'Email obrigatório'}), 400
    conn = get_connection(); cur = conn.cursor()
    cur.execute("SELECT id, usuario, cliente_id FROM admin WHERE email=%s", (email,))
    user = fetchone(cur); conn.close()
    if not user: return jsonify({'ok': True})
    import smtplib
    from email.mime.text import MIMEText
    token = secrets.token_urlsafe(32)
    expired = [k for k, v in reset_tokens.items() if v['expires'] < time.time()]
    for k in expired: del reset_tokens[k]
    reset_tokens[token] = {'user_id': user['id'], 'usuario': user['usuario'],
                           'cliente_id': user.get('cliente_id'), 'expires': time.time() + 3600}
    # Buscar slug para montar link correto
    slug = ''
    if user.get('cliente_id'):
        conn2 = get_connection(); cur2 = conn2.cursor()
        cur2.execute("SELECT slug FROM clientes_saas WHERE id=%s", (user['cliente_id'],))
        row = cur2.fetchone()
        if row: slug = row[0]
        conn2.close()
    if slug:
        link = f"{request.host_url}{slug}/painel?reset_token={token}"
    else:
        link = f"{request.host_url}admin?reset_token={token}"
    pcfg = get_platform_config()
    smtp_cfg = pcfg or {}
    msg = MIMEText(f"Clique no link para redefinir sua senha:\n{link}\n\nLink válido por 1 hora.")
    msg['Subject'] = 'Recuperação de senha — Cardápio Digital'
    msg['From'] = smtp_cfg.get('smtp_from') or smtp_cfg.get('smtp_user', '')
    msg['To'] = email
    try:
        s = smtplib.SMTP(smtp_cfg.get('smtp_host','smtp.gmail.com'), int(smtp_cfg.get('smtp_port',587)))
        s.starttls()
        s.login(smtp_cfg.get('smtp_user',''), smtp_cfg.get('smtp_password',''))
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

# ── USUÁRIOS (TENANT) ─────────────────────────────────────────────────────────
@app.route('/api/admin/usuarios')
@admin_required
def listar_usuarios():
    cid = get_cliente_id()
    conn = get_connection(); cur = conn.cursor()
    cur.execute("SELECT id,usuario,nome,role,permissions,email FROM admin WHERE cliente_id=%s ORDER BY id", (cid,))
    users = fetchall(cur); conn.close()
    return jsonify(users)

@app.route('/api/admin/usuarios', methods=['POST'])
@admin_required
def criar_usuario():
    d = request.get_json(); cid = get_cliente_id()
    usuario = (d.get('usuario') or '').strip()
    senha_plain = (d.get('senha') or '').strip()
    nome = (d.get('nome') or '').strip()
    role = d.get('role', 'staff')
    permissions = json.dumps(d.get('permissions', {}))
    email = (d.get('email') or '').strip() or None
    if not usuario or not senha_plain:
        return jsonify({'erro': 'Usuário e senha obrigatórios'}), 400
    senha = hashlib.sha256(senha_plain.encode()).hexdigest()
    conn = get_connection(); cur = conn.cursor()
    try:
        cur.execute("INSERT INTO admin (cliente_id,usuario,senha,nome,role,permissions,email) VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                    (cid,usuario,senha,nome,role,permissions,email))
        nid = cur.fetchone()[0]; conn.commit(); conn.close()
        return jsonify({'id': nid}), 201
    except Exception:
        conn.close(); return jsonify({'erro': 'Usuário já existe'}), 409

@app.route('/api/admin/usuarios/<int:uid>', methods=['PATCH'])
@admin_required
def editar_usuario(uid):
    d = request.get_json(); cid = get_cliente_id()
    conn = get_connection(); cur = conn.cursor()
    if d.get('nome') is not None:
        cur.execute("UPDATE admin SET nome=%s WHERE id=%s AND cliente_id=%s", (d['nome'],uid,cid))
    if d.get('role'):
        cur.execute("UPDATE admin SET role=%s WHERE id=%s AND cliente_id=%s", (d['role'],uid,cid))
    if 'permissions' in d:
        cur.execute("UPDATE admin SET permissions=%s WHERE id=%s AND cliente_id=%s", (json.dumps(d['permissions']),uid,cid))
    if d.get('senha'):
        senha = hashlib.sha256(d['senha'].encode()).hexdigest()
        cur.execute("UPDATE admin SET senha=%s WHERE id=%s AND cliente_id=%s", (senha,uid,cid))
    if 'email' in d:
        cur.execute("UPDATE admin SET email=%s WHERE id=%s AND cliente_id=%s", ((d['email'] or None),uid,cid))
    conn.commit(); conn.close(); return jsonify({'ok': True})

@app.route('/api/admin/usuarios/<int:uid>', methods=['DELETE'])
@admin_required
def deletar_usuario(uid):
    cid = get_cliente_id()
    conn = get_connection(); cur = conn.cursor()
    cur.execute("SELECT usuario FROM admin WHERE id=%s AND cliente_id=%s", (uid,cid))
    row = cur.fetchone()
    if row and row[0] == session.get('admin_usuario'):
        conn.close(); return jsonify({'erro': 'Não pode excluir o próprio usuário'}), 400
    cur.execute("DELETE FROM admin WHERE id=%s AND cliente_id=%s", (uid,cid))
    conn.commit(); conn.close(); return jsonify({'ok': True})

# ── CONFIG (TENANT) ───────────────────────────────────────────────────────────
def load_config(cid=None):
    """Load tenant config from DB, fallback to config.json for legacy."""
    if cid:
        try:
            conn = get_connection(); cur = conn.cursor()
            cur.execute("SELECT * FROM config_tenant WHERE cliente_id=%s", (cid,))
            row = fetchone(cur)
            # Get tenant slug
            cur.execute("SELECT slug FROM clientes_saas WHERE id=%s", (cid,))
            slug_row = cur.fetchone()
            conn.close()
            if row:
                cfg = {}
                for k,v in row.items():
                    if k in ('horarios','cupons','smtp') and isinstance(v, str):
                        try: cfg[k] = json.loads(v)
                        except: cfg[k] = {} if k != 'cupons' else []
                    elif k not in ('id','criado_em'):
                        cfg[k] = v
                if slug_row: cfg['slug'] = slug_row[0]
                if cfg.get('tipos_pagamento') and isinstance(cfg['tipos_pagamento'], str):
                    cfg['tipos_pagamento'] = [t.strip() for t in cfg['tipos_pagamento'].split(',') if t.strip()]
                return cfg
        except: pass
    # Fallback legado
    CONFIG_FILE = os.path.join(BASE, 'config.json')
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f: return json.load(f)
    return {}

def save_config(data, cid=None):
    """Save tenant config to DB."""
    if not cid: return
    conn = get_connection(); cur = conn.cursor()
    cur.execute("SELECT id FROM config_tenant WHERE cliente_id=%s", (cid,))
    exists = cur.fetchone()
    horarios = json.dumps(data.get('horarios', {}))
    cupons = json.dumps(data.get('cupons', []))
    smtp_str = json.dumps(data.get('smtp', {}))
    tipos = ','.join(data['tipos_pagamento']) if isinstance(data.get('tipos_pagamento'), list) else (data.get('tipos_pagamento') or '')
    fields = dict(
        nome_loja=data.get('nome_loja'),
        wpp=data.get('wpp'),
        frete=float(data.get('frete',0) or 0),
        frete_min=float(data.get('frete_min',0) or 0),
        tipos_pagamento=tipos,
        logo_url=data.get('logo_url'),
        banner_url=data.get('banner_url'),
        horarios=horarios,
        cupons=cupons,
        impressora=data.get('impressora',''),
        auto_impressao=1 if data.get('auto_impressao') else 0,
        papel=data.get('papel','80mm'),
        smtp=smtp_str
    )
    if exists:
        set_parts = ', '.join(f"{k}=%s" for k in fields)
        vals = list(fields.values()) + [cid]
        cur.execute(f"UPDATE config_tenant SET {set_parts} WHERE cliente_id=%s", vals)
    else:
        cols = ', '.join(fields.keys()) + ', cliente_id'
        phs  = ', '.join(['%s']*len(fields)) + ', %s'
        cur.execute(f"INSERT INTO config_tenant ({cols}) VALUES ({phs})", list(fields.values()) + [cid])
    conn.commit(); conn.close()

@app.route('/api/config')
def get_config():
    cid = get_cliente_id()
    # Também aceitar slug via query param (cardápio público)
    slug = request.args.get('slug') or ''
    if not cid and slug:
        try:
            conn = get_connection(); cur = conn.cursor()
            cur.execute("SELECT id FROM clientes_saas WHERE slug=%s AND status='ativo'", (slug,))
            row = cur.fetchone()
            if row: cid = row[0]
            conn.close()
        except: pass
    return jsonify(load_config(cid))

@app.route('/api/cupom/validar', methods=['POST'])
def validar_cupom():
    d = request.get_json()
    codigo = (d.get('codigo') or '').strip().upper()
    telefone = (d.get('telefone') or '').strip()
    cid = get_cliente_id()
    slug = d.get('slug') or ''
    if not cid and slug:
        try:
            conn = get_connection(); cur = conn.cursor()
            cur.execute("SELECT id FROM clientes_saas WHERE slug=%s", (slug,))
            row = cur.fetchone()
            if row: cid = row[0]
            conn.close()
        except: pass
    cfg = load_config(cid)
    cupons = cfg.get('cupons', [])
    for cup in cupons:
        if cup.get('codigo','').upper() == codigo and cup.get('ativo', True):
            if cup.get('tipo') == 'novo_cliente' and telefone and cid:
                conn = get_connection(); cur = conn.cursor()
                cur.execute("SELECT COUNT(*) FROM pedidos WHERE telefone=%s AND cliente_id=%s", (telefone, cid))
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
    d = request.get_json(); cid = get_cliente_id()
    cfg = load_config(cid)
    for k in ['nome_loja','wpp']:
        if d.get(k): cfg[k] = d[k]
    for k in ['frete','frete_min']:
        if k in d: cfg[k] = float(d[k])
    if 'horarios'        in d: cfg['horarios']        = d['horarios']
    if 'tipos_pagamento' in d: cfg['tipos_pagamento'] = d['tipos_pagamento']
    if 'cupons'          in d: cfg['cupons']          = d['cupons']
    if 'smtp'            in d: cfg['smtp']            = d['smtp']
    if 'impressora'      in d: cfg['impressora']      = d['impressora']
    if 'auto_impressao'  in d: cfg['auto_impressao']  = bool(d['auto_impressao'])
    if 'papel'           in d: cfg['papel']           = d['papel']
    if d.get('logo_base64'):
        cfg['logo_url'] = save_image(d['logo_base64'], 'logo.jpg', cid)
    if d.get('banner_base64'):
        cfg['banner_url'] = save_image(d['banner_base64'], 'banner.jpg', cid)
    # Atualizar slug da loja (rota do cardápio)
    if d.get('slug') and cid:
        new_slug = slugify(d['slug'])
        if new_slug:
            try:
                conn2 = get_connection(); cur2 = conn2.cursor()
                cur2.execute("SELECT id FROM clientes_saas WHERE slug=%s AND id!=%s", (new_slug, cid))
                if not cur2.fetchone():
                    cur2.execute("UPDATE clientes_saas SET slug=%s WHERE id=%s", (new_slug, cid))
                    conn2.commit()
                    session['tenant_slug'] = new_slug
                    cfg['slug'] = new_slug
                conn2.close()
            except: pass
    save_config(cfg, cid)
    return jsonify({'ok': True, 'cfg': cfg})

# ── TENANT: INFO PÚBLICA ──────────────────────────────────────────────────────
@app.route('/api/tenant/<slug>/info')
def tenant_info(slug):
    conn = get_connection(); cur = conn.cursor()
    cur.execute("SELECT id,nome,slug,status FROM clientes_saas WHERE slug=%s", (slug,))
    tenant = fetchone(cur); conn.close()
    if not tenant: return jsonify({'erro': 'Não encontrado'}), 404
    if tenant['status'] in ('suspenso','inativo'):
        return jsonify({'erro': 'Loja indisponível', 'status': tenant['status']}), 403
    cfg = load_config(tenant['id'])
    return jsonify({**cfg, 'cliente_id': tenant['id'], 'slug': slug,
                    'tenant_status': tenant['status']})

# ── CATEGORIAS ────────────────────────────────────────────────────────────────
@app.route('/api/categorias')
def get_categorias():
    cid = get_cliente_id()
    slug = request.args.get('slug') or ''
    if not cid and slug:
        try:
            conn = get_connection(); cur = conn.cursor()
            cur.execute("SELECT id FROM clientes_saas WHERE slug=%s", (slug,))
            row = cur.fetchone()
            if row: cid = row[0]
            conn.close()
        except: pass
    conn = get_connection(); cur = conn.cursor()
    if cid:
        cur.execute("SELECT * FROM categorias WHERE ativa=1 AND cliente_id=%s ORDER BY ordem", (cid,))
    else:
        cur.execute("SELECT * FROM categorias WHERE ativa=1 ORDER BY ordem")
    cats = fetchall(cur); conn.close()
    return jsonify(cats)

@app.route('/api/admin/categoria', methods=['POST'])
@login_required
def criar_categoria():
    d = request.get_json(); cid = get_cliente_id()
    conn = get_connection(); cur = conn.cursor()
    cur.execute("SELECT COALESCE(MAX(ordem),0)+1 FROM categorias WHERE cliente_id=%s", (cid,))
    ordem = cur.fetchone()[0]
    cur.execute("INSERT INTO categorias (cliente_id,nome,ordem) VALUES (%s,%s,%s) RETURNING id",
                (cid, d['nome'], ordem))
    nid = cur.fetchone()[0]; conn.commit(); conn.close()
    return jsonify({'id': nid}), 201

@app.route('/api/admin/categoria/<int:cid_cat>', methods=['PATCH'])
@login_required
def editar_categoria(cid_cat):
    d = request.get_json(); cid = get_cliente_id()
    conn = get_connection(); cur = conn.cursor()
    if 'nome'  in d: cur.execute("UPDATE categorias SET nome=%s  WHERE id=%s AND cliente_id=%s", (d['nome'],cid_cat,cid))
    if 'ativa' in d: cur.execute("UPDATE categorias SET ativa=%s WHERE id=%s AND cliente_id=%s", (1 if d['ativa'] else 0,cid_cat,cid))
    if 'ordem' in d: cur.execute("UPDATE categorias SET ordem=%s WHERE id=%s AND cliente_id=%s", (d['ordem'],cid_cat,cid))
    conn.commit(); conn.close(); return jsonify({'ok': True})

@app.route('/api/admin/categoria/<int:cid_cat>', methods=['DELETE'])
@login_required
def deletar_categoria(cid_cat):
    cid = get_cliente_id()
    conn = get_connection(); cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM produtos WHERE categoria_id=%s AND cliente_id=%s", (cid_cat,cid))
    if cur.fetchone()[0] > 0:
        conn.close(); return jsonify({'erro': 'Categoria possui produtos'}), 400
    cur.execute("DELETE FROM categorias WHERE id=%s AND cliente_id=%s", (cid_cat,cid))
    conn.commit(); conn.close(); return jsonify({'ok': True})

# ── CARDÁPIO PÚBLICO ──────────────────────────────────────────────────────────
@app.route('/api/cardapio')
def get_cardapio():
    cid = get_cliente_id()
    slug = request.args.get('slug') or ''
    if not cid and slug:
        try:
            conn = get_connection(); cur = conn.cursor()
            cur.execute("SELECT id FROM clientes_saas WHERE slug=%s AND status='ativo'", (slug,))
            row = cur.fetchone()
            if row: cid = row[0]
            conn.close()
        except: pass
    conn = get_connection(); cur = conn.cursor()
    if cid:
        cur.execute("SELECT * FROM categorias WHERE ativa=1 AND cliente_id=%s ORDER BY ordem", (cid,))
    else:
        cur.execute("SELECT * FROM categorias WHERE ativa=1 ORDER BY ordem")
    cats = fetchall(cur); resultado = []
    for cat in cats:
        if cid:
            cur.execute("SELECT * FROM produtos WHERE categoria_id=%s AND disponivel=1 AND cliente_id=%s", (cat['id'],cid))
        else:
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
    slug = dados.get('slug') or session.get('tenant_slug') or ''
    cid  = get_cliente_id()

    if not cid and slug:
        try:
            conn_t = get_connection(); cur_t = conn_t.cursor()
            cur_t.execute("SELECT id FROM clientes_saas WHERE slug=%s AND status='ativo'", (slug,))
            row = cur_t.fetchone()
            if row: cid = row[0]
            conn_t.close()
        except: pass

    if not nome or not itens:
        return jsonify({'erro': 'Nome e itens são obrigatórios'}), 400
    if tipo == 'entrega' and not end:
        return jsonify({'erro': 'Endereço obrigatório para entrega'}), 400

    cfg = load_config(cid)
    frete_override = dados.get('frete_override')
    if frete_override is not None:
        frete = float(frete_override) if tipo == 'entrega' else 0.0
    else:
        frete = float(cfg.get('frete', 0)) if tipo == 'entrega' else 0.0

    conn = get_connection(); cur = conn.cursor()
    subtotal = 0.0; valids = []
    for item in itens:
        if cid:
            cur.execute("SELECT * FROM produtos WHERE id=%s AND disponivel=1 AND cliente_id=%s", (item['produto_id'],cid))
        else:
            cur.execute("SELECT * FROM produtos WHERE id=%s AND disponivel=1", (item['produto_id'],))
        p = fetchone(cur)
        if not p: conn.close(); return jsonify({'erro': 'Produto indisponível'}), 400
        qty = int(item.get('quantidade', 1))
        preco = float(p.get('preco_promo') or p['preco']) if p.get('em_promo') and p.get('preco_promo') else float(p['preco'])
        subtotal += preco * qty
        valids.append((p['id'], qty, preco))

    desconto = float(dados.get('desconto_override', 0) or 0)
    total = round(max(0, subtotal + frete - desconto), 2)
    cur.execute(
        "INSERT INTO pedidos (cliente_id,nome_cliente,telefone,observacao,total,subtotal,frete,tipo_entrega,endereco,forma_pagamento,status) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'pendente') RETURNING id",
        (cid,nome,tel,obs,total,round(subtotal,2),frete,tipo,end,fpag))
    pid = cur.fetchone()[0]
    cur.executemany("INSERT INTO itens_pedido (cliente_id,pedido_id,produto_id,quantidade,preco_unit) VALUES (%s,%s,%s,%s,%s)",
        [(cid,pid,p,q,pr) for p,q,pr in valids])
    if tel and cid:
        cur.execute("SELECT id FROM clientes WHERE telefone=%s AND cliente_id=%s", (tel,cid))
        if not cur.fetchone():
            cur.execute("INSERT INTO clientes (cliente_id,telefone,nome,tipo) VALUES (%s,%s,%s,'cliente')", (cid,tel,nome))
    conn.commit(); conn.close()
    return jsonify({'pedido_id': pid,'total': total,'subtotal': round(subtotal,2),'frete': frete,'desconto': desconto}), 201

@app.route('/api/admin/pedidos')
@login_required
def listar_pedidos():
    cid = get_cliente_id()
    sf = request.args.get('status',''); nf = request.args.get('nome','').strip(); df = request.args.get('data','').strip()
    conn = get_connection(); cur = conn.cursor()
    q = "SELECT * FROM pedidos WHERE cliente_id=%s"; params = [cid]
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
    d = request.get_json(); cid = get_cliente_id()
    conn = get_connection(); cur = conn.cursor()
    # Verificar que o pedido pertence ao tenant
    cur.execute("SELECT id FROM pedidos WHERE id=%s AND cliente_id=%s", (pid,cid))
    if not cur.fetchone(): conn.close(); return jsonify({'erro': 'Não encontrado'}), 404
    for col in ['nome_cliente','telefone','observacao','endereco','forma_pagamento']:
        if col in d: cur.execute(f"UPDATE pedidos SET {col}=%s WHERE id=%s", (d[col], pid))
    if 'itens' in d:
        novos_itens = d['itens']; subtotal = 0.0
        cur.execute("DELETE FROM itens_pedido WHERE pedido_id=%s", (pid,))
        for item in novos_itens:
            cur.execute("SELECT preco,preco_promo,em_promo FROM produtos WHERE id=%s AND cliente_id=%s", (item['produto_id'],cid))
            p = cur.fetchone()
            if p:
                preco = float(p[1] or p[0]) if p[2] else float(p[0])
                qty = int(item['quantidade']); subtotal += preco * qty
                cur.execute("INSERT INTO itens_pedido (cliente_id,pedido_id,produto_id,quantidade,preco_unit) VALUES (%s,%s,%s,%s,%s)",
                            (cid,pid,item['produto_id'],qty,preco))
        frete = float(d.get('frete', 0))
        desconto = float(d.get('desconto_override', 0) or 0)
        total = round(max(0, subtotal + frete - desconto), 2)
        cur.execute("UPDATE pedidos SET subtotal=%s,frete=%s,total=%s WHERE id=%s",
                    (round(subtotal,2),frete,total,pid))
    elif 'frete' in d:
        frete = float(d['frete'])
        cur.execute("SELECT subtotal FROM pedidos WHERE id=%s", (pid,))
        row = cur.fetchone()
        if row:
            cur.execute("UPDATE pedidos SET frete=%s,total=%s WHERE id=%s",
                        (frete, round((float(row[0] or 0))+frete,2), pid))
    conn.commit(); conn.close(); return jsonify({'ok': True})

@app.route('/api/admin/pedidos/<int:pid>', methods=['DELETE'])
@login_required
def deletar_pedido(pid):
    cid = get_cliente_id()
    conn = get_connection(); cur = conn.cursor()
    cur.execute("SELECT id FROM pedidos WHERE id=%s AND cliente_id=%s", (pid,cid))
    if not cur.fetchone(): conn.close(); return jsonify({'erro': 'Não encontrado'}), 404
    cur.execute("DELETE FROM itens_pedido WHERE pedido_id=%s", (pid,))
    cur.execute("DELETE FROM financeiro    WHERE pedido_id=%s", (pid,))
    cur.execute("DELETE FROM pedidos       WHERE id=%s",        (pid,))
    conn.commit(); conn.close(); return jsonify({'ok': True})

@app.route('/api/admin/pedidos/<int:pid>/status', methods=['PATCH'])
@login_required
def atualizar_status(pid):
    d = request.get_json(); cid = get_cliente_id()
    status = d.get('status'); fp = d.get('forma_pagamento')
    if status not in ('pendente','aceito','em_preparo','pronto','em_rota','entregue','cancelado'):
        return jsonify({'erro': 'Status inválido'}), 400
    conn = get_connection(); cur = conn.cursor()
    if fp: cur.execute("UPDATE pedidos SET status=%s,forma_pagamento=%s WHERE id=%s AND cliente_id=%s", (status,fp,pid,cid))
    else:  cur.execute("UPDATE pedidos SET status=%s WHERE id=%s AND cliente_id=%s", (status,pid,cid))
    if status == 'aceito':
        cur.execute("SELECT * FROM pedidos WHERE id=%s AND cliente_id=%s", (pid,cid))
        pedido = fetchone(cur)
        if pedido:
            fpag = fp or pedido.get('forma_pagamento') or 'Não informado'
            cliente_fin_id = None
            if pedido.get('telefone'):
                cur.execute("SELECT id FROM clientes WHERE telefone=%s AND cliente_id=%s", (pedido['telefone'],cid))
                cl = cur.fetchone()
                if cl: cliente_fin_id = cl[0]
            cur.execute("SELECT id FROM financeiro WHERE pedido_id=%s", (pid,))
            if not cur.fetchone():
                import re as _re
                partes = [p.strip() for p in fpag.split('+')]
                total_ped = pedido['total']
                if len(partes) > 1:
                    for parte in partes:
                        m = _re.match(r'^(.+?)\s+R\$([\d,.]+)$', parte)
                        if m:
                            tipo_pgto = m.group(1).strip(); val = float(m.group(2).replace(',','.'))
                        else:
                            tipo_pgto = parte; val = total_ped / len(partes)
                        cur.execute("INSERT INTO financeiro (cliente_id,pedido_id,cli_id,valor,tipo,forma_pagamento,descricao,pago) VALUES (%s,%s,%s,%s,'entrada',%s,%s,1)",
                            (cid,pid,cliente_fin_id,round(val,2),tipo_pgto,f'Pedido {pid} - {tipo_pgto}'))
                else:
                    cur.execute("INSERT INTO financeiro (cliente_id,pedido_id,cli_id,valor,tipo,forma_pagamento,descricao,pago) VALUES (%s,%s,%s,%s,'entrada',%s,%s,1)",
                        (cid,pid,cliente_fin_id,total_ped,fpag,f'Pedido {pid}'))
    conn.commit(); conn.close(); return jsonify({'ok': True})

# ── PRODUTOS ──────────────────────────────────────────────────────────────────
@app.route('/api/admin/produtos')
@login_required
def listar_produtos():
    cid = get_cliente_id()
    conn = get_connection(); cur = conn.cursor()
    cur.execute("SELECT * FROM categorias WHERE cliente_id=%s ORDER BY ordem", (cid,)); cats = fetchall(cur)
    cur.execute("SELECT * FROM produtos WHERE cliente_id=%s", (cid,)); prods = fetchall(cur)
    conn.close(); return jsonify({'categorias': cats, 'produtos': prods})

@app.route('/api/admin/produto', methods=['POST'])
@login_required
def criar_produto():
    d = request.get_json(); cid = get_cliente_id()
    nome_safe = (d.get('nome') or 'prod').replace(' ','_')
    foto = save_image(d.get('foto_base64'), f"prod_{nome_safe}.jpg", cid) if d.get('foto_base64') else None
    conn = get_connection(); cur = conn.cursor()
    cur.execute("INSERT INTO produtos (cliente_id,categoria_id,nome,descricao,preco,foto_url,preco_promo,em_promo) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
        (cid,d['categoria_id'],d['nome'],d.get('descricao',''),float(d['preco']),foto,
         float(d['preco_promo']) if d.get('preco_promo') else None, 1 if d.get('em_promo') else 0))
    nid = cur.fetchone()[0]; conn.commit(); conn.close()
    return jsonify({'id': nid}), 201

@app.route('/api/admin/produto/<int:pid>', methods=['PATCH'])
@login_required
def editar_produto(pid):
    d = request.get_json(); cid = get_cliente_id()
    conn = get_connection(); cur = conn.cursor()
    for col in ['disponivel','preco','nome','descricao','categoria_id']:
        if col in d:
            val = (1 if d[col] else 0) if col=='disponivel' else (float(d[col]) if col=='preco' else d[col])
            cur.execute(f"UPDATE produtos SET {col}=%s WHERE id=%s AND cliente_id=%s", (val,pid,cid))
    if 'em_promo'    in d: cur.execute("UPDATE produtos SET em_promo=%s    WHERE id=%s AND cliente_id=%s", (1 if d['em_promo'] else 0,pid,cid))
    if 'preco_promo' in d: cur.execute("UPDATE produtos SET preco_promo=%s WHERE id=%s AND cliente_id=%s",
        (float(d['preco_promo']) if d['preco_promo'] else None, pid, cid))
    if d.get('foto_base64'):
        url = save_image(d['foto_base64'], f"prod_{pid}.jpg", cid)
        cur.execute("UPDATE produtos SET foto_url=%s WHERE id=%s AND cliente_id=%s", (url,pid,cid))
    conn.commit(); conn.close(); return jsonify({'ok': True})

@app.route('/api/admin/produto/<int:pid>', methods=['DELETE'])
@login_required
def deletar_produto(pid):
    cid = get_cliente_id()
    conn = get_connection(); cur = conn.cursor()
    cur.execute("DELETE FROM produtos WHERE id=%s AND cliente_id=%s", (pid,cid))
    conn.commit(); conn.close(); return jsonify({'ok': True})

# ── FINANCEIRO ────────────────────────────────────────────────────────────────
@app.route('/api/admin/financeiro')
@login_required
def listar_financeiro():
    cid = get_cliente_id()
    tp=request.args.get('tipo',''); pg=request.args.get('pago','')
    di=request.args.get('data_ini',''); df_=request.args.get('data_fim','')
    emp=request.args.get('empresa_id','')
    conn = get_connection(); cur = conn.cursor()
    q = '''SELECT f.*,
        c.nome as cliente_nome, c.telefone as cliente_tel,
        e.nome as empresa_nome, e.tipo as empresa_tipo
        FROM financeiro f
        LEFT JOIN clientes c ON c.id=f.cli_id
        LEFT JOIN clientes e ON e.id=f.empresa_id
        WHERE f.cliente_id=%s'''
    params = [cid]
    if tp:  q += " AND f.tipo=%s";    params.append(tp)
    if pg != '': q += " AND f.pago=%s"; params.append(int(pg))
    if di:  q += " AND COALESCE(f.data_lancamento,f.criado_em) >= %s"; params.append(di)
    if df_: q += " AND COALESCE(f.data_lancamento,f.criado_em) <= %s"; params.append(df_+' 23:59:59')
    if emp: q += " AND f.empresa_id=%s"; params.append(int(emp))
    q += " ORDER BY COALESCE(f.data_lancamento,f.criado_em) DESC"
    cur.execute(q, params); result = fetchall(cur); conn.close()
    return jsonify(result)

@app.route('/api/admin/financeiro', methods=['POST'])
@login_required
def criar_lancamento():
    d = request.get_json(); cid = get_cliente_id()
    conn = get_connection(); cur = conn.cursor()
    dl = d.get('data_lancamento') or None
    emp_id = d.get('empresa_id') or None
    if dl:
        cur.execute("INSERT INTO financeiro (cliente_id,valor,tipo,forma_pagamento,descricao,observacao,pago,data_lancamento,criado_em,empresa_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (cid,float(d['valor']),d.get('tipo','entrada'),d.get('forma_pagamento',''),d.get('descricao',''),d.get('observacao',''),1 if d.get('pago',True) else 0,dl,dl,emp_id))
    else:
        cur.execute("INSERT INTO financeiro (cliente_id,valor,tipo,forma_pagamento,descricao,observacao,pago,empresa_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (cid,float(d['valor']),d.get('tipo','entrada'),d.get('forma_pagamento',''),d.get('descricao',''),d.get('observacao',''),1 if d.get('pago',True) else 0,emp_id))
    nid = cur.fetchone()[0]; conn.commit(); conn.close()
    return jsonify({'id': nid}), 201

@app.route('/api/admin/financeiro/<int:fid>', methods=['PATCH'])
@login_required
def editar_lancamento(fid):
    d = request.get_json(); cid = get_cliente_id()
    conn = get_connection(); cur = conn.cursor()
    for col in ['tipo','forma_pagamento','descricao','observacao','data_lancamento','empresa_id']:
        if col in d: cur.execute(f"UPDATE financeiro SET {col}=%s WHERE id=%s AND cliente_id=%s", (d[col],fid,cid))
    if 'valor' in d: cur.execute("UPDATE financeiro SET valor=%s WHERE id=%s AND cliente_id=%s", (float(d['valor']),fid,cid))
    if 'pago'  in d: cur.execute("UPDATE financeiro SET pago=%s  WHERE id=%s AND cliente_id=%s", (1 if d['pago'] else 0,fid,cid))
    conn.commit(); conn.close(); return jsonify({'ok': True})

@app.route('/api/admin/financeiro/<int:fid>', methods=['DELETE'])
@login_required
def deletar_lancamento(fid):
    cid = get_cliente_id()
    conn = get_connection(); cur = conn.cursor()
    cur.execute("DELETE FROM financeiro WHERE id=%s AND pedido_id IS NULL AND cliente_id=%s", (fid,cid))
    conn.commit(); conn.close(); return jsonify({'ok': True})

# ── CLIENTES / EMPRESAS ───────────────────────────────────────────────────────
@app.route('/api/admin/clientes')
@login_required
def listar_clientes():
    cid = get_cliente_id()
    tipo = request.args.get('tipo', '')
    conn = get_connection(); cur = conn.cursor()
    if tipo:
        cur.execute("SELECT * FROM clientes WHERE tipo=%s AND cliente_id=%s ORDER BY nome", (tipo,cid))
    else:
        cur.execute("SELECT * FROM clientes WHERE cliente_id=%s ORDER BY nome", (cid,))
    result = fetchall(cur); conn.close(); return jsonify(result)

@app.route('/api/admin/empresa', methods=['POST'])
@login_required
def criar_empresa():
    d = request.get_json(); cid = get_cliente_id()
    nome = (d.get('nome') or '').strip()
    if not nome: return jsonify({'erro': 'Nome obrigatório'}), 400
    tel  = (d.get('telefone') or '').strip() or None
    tipo = d.get('tipo', 'cliente')
    cpf_cnpj = (d.get('cpf_cnpj') or '').strip() or None
    email_emp = (d.get('email') or '').strip() or None
    conn = get_connection(); cur = conn.cursor()
    if tel:
        cur.execute("SELECT id FROM clientes WHERE telefone=%s AND cliente_id=%s", (tel,cid))
        existing = cur.fetchone()
        if existing:
            cur.execute("UPDATE clientes SET nome=%s,tipo=%s,cpf_cnpj=%s WHERE id=%s", (nome,tipo,cpf_cnpj,existing[0]))
            nid = existing[0]
        else:
            cur.execute("INSERT INTO clientes (cliente_id,telefone,nome,tipo,cpf_cnpj,email) VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
                (cid,tel,nome,tipo,cpf_cnpj,email_emp))
            nid = cur.fetchone()[0]
    else:
        cur.execute("INSERT INTO clientes (cliente_id,telefone,nome,tipo,cpf_cnpj,email) VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
            (cid,f'manual_{int(time.time())}',nome,tipo,cpf_cnpj,email_emp))
        nid = cur.fetchone()[0]
    conn.commit(); conn.close()
    return jsonify({'id': nid}), 201

@app.route('/api/admin/cliente/<int:cid_cli>', methods=['PATCH'])
@login_required
def editar_cliente(cid_cli):
    d = request.get_json(); cid = get_cliente_id()
    conn = get_connection(); cur = conn.cursor()
    for col in ['nome','email','endereco','tipo','cpf_cnpj']:
        if col in d: cur.execute(f"UPDATE clientes SET {col}=%s WHERE id=%s AND cliente_id=%s", (d[col],cid_cli,cid))
    conn.commit(); conn.close(); return jsonify({'ok': True})

@app.route('/api/admin/cliente/<int:cid_cli>', methods=['DELETE'])
@login_required
def deletar_cliente(cid_cli):
    cid = get_cliente_id()
    conn = get_connection(); cur = conn.cursor()
    cur.execute("UPDATE financeiro SET empresa_id=NULL WHERE empresa_id=%s AND cliente_id=%s", (cid_cli,cid))
    cur.execute("DELETE FROM clientes WHERE id=%s AND cliente_id=%s AND telefone LIKE 'manual_%'", (cid_cli,cid))
    conn.commit(); conn.close(); return jsonify({'ok': True})


if __name__ == '__main__':
    print("\n  Super Admin: http://localhost:5000/admin")
    print("  Painel tenant: http://localhost:5000/<slug>/painel")
    print("  Cardápio: http://localhost:5000/<slug>\n")
    app.run(debug=True, port=5000)
