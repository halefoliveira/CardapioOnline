import psycopg2, os, time

DATABASE_URL = os.environ.get('DATABASE_URL')

def get_connection():
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute("SET TIME ZONE 'America/Sao_Paulo'")
    conn.commit()
    return conn

def fetchall(cur):
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]

def fetchone(cur):
    cols = [d[0] for d in cur.description]
    r = cur.fetchone()
    return dict(zip(cols, r)) if r else None

def init_db():
    for _ in range(10):
        try:
            conn = get_connection(); break
        except psycopg2.OperationalError:
            print("Aguardando banco..."); time.sleep(2)
    else:
        raise Exception("Banco indisponível")

    c = conn.cursor()

    # ── TABELAS SAAS ─────────────────────────────────────────────────────────────
    c.execute('''CREATE TABLE IF NOT EXISTS clientes_saas (
        id SERIAL PRIMARY KEY,
        nome TEXT NOT NULL,
        email TEXT UNIQUE,
        telefone TEXT,
        cpf_cnpj TEXT,
        slug TEXT UNIQUE NOT NULL,
        senha TEXT NOT NULL,
        status TEXT DEFAULT 'ativo',
        plano TEXT DEFAULT 'basico',
        data_entrada TEXT,
        data_vencimento TEXT,
        webhook_ref TEXT,
        observacao TEXT,
        criado_em TEXT DEFAULT to_char(NOW() AT TIME ZONE 'America/Sao_Paulo', 'YYYY-MM-DD HH24:MI:SS'))''')

    c.execute('''CREATE TABLE IF NOT EXISTS platform_config (
        id INTEGER PRIMARY KEY DEFAULT 1,
        smtp_host TEXT DEFAULT 'smtp.gmail.com',
        smtp_port INTEGER DEFAULT 587,
        smtp_user TEXT,
        smtp_password TEXT,
        smtp_from TEXT,
        supabase_url TEXT DEFAULT 'https://isfndtvadwpxzvrtmtfy.supabase.co',
        supabase_key TEXT DEFAULT 'sb_publishable_-iRb2BiZV3I6WBlbJzbaOg_2nXo8_72',
        supabase_bucket TEXT DEFAULT 'cardapio',
        webhook_secret TEXT,
        webhook_url TEXT,
        criado_em TEXT DEFAULT to_char(NOW() AT TIME ZONE 'America/Sao_Paulo', 'YYYY-MM-DD HH24:MI:SS'))''')

    c.execute('''CREATE TABLE IF NOT EXISTS super_admin (
        id SERIAL PRIMARY KEY,
        usuario TEXT UNIQUE NOT NULL,
        senha TEXT NOT NULL,
        nome TEXT,
        email TEXT,
        criado_em TEXT DEFAULT to_char(NOW() AT TIME ZONE 'America/Sao_Paulo', 'YYYY-MM-DD HH24:MI:SS'))''')

    c.execute('''CREATE TABLE IF NOT EXISTS config_tenant (
        id SERIAL PRIMARY KEY,
        cliente_id INTEGER UNIQUE REFERENCES clientes_saas(id) ON DELETE CASCADE,
        nome_loja TEXT,
        wpp TEXT,
        frete REAL DEFAULT 0,
        frete_min REAL DEFAULT 0,
        tipos_pagamento TEXT DEFAULT 'Dinheiro,PIX',
        logo_url TEXT,
        banner_url TEXT,
        horarios TEXT DEFAULT '{}',
        cupons TEXT DEFAULT '[]',
        impressora TEXT,
        auto_impressao INTEGER DEFAULT 0,
        papel TEXT DEFAULT '80mm',
        smtp TEXT DEFAULT '{}',
        criado_em TEXT DEFAULT to_char(NOW() AT TIME ZONE 'America/Sao_Paulo', 'YYYY-MM-DD HH24:MI:SS'))''')

    # ── TABELAS CORE (com cliente_id) ────────────────────────────────────────────
    c.execute('''CREATE TABLE IF NOT EXISTS categorias (
        id SERIAL PRIMARY KEY,
        cliente_id INTEGER REFERENCES clientes_saas(id) ON DELETE CASCADE,
        nome TEXT NOT NULL,
        ordem INTEGER DEFAULT 0,
        ativa INTEGER DEFAULT 1)''')

    c.execute('''CREATE TABLE IF NOT EXISTS produtos (
        id SERIAL PRIMARY KEY,
        cliente_id INTEGER REFERENCES clientes_saas(id) ON DELETE CASCADE,
        categoria_id INTEGER NOT NULL REFERENCES categorias(id),
        nome TEXT NOT NULL,
        descricao TEXT,
        preco REAL NOT NULL,
        disponivel INTEGER DEFAULT 1,
        foto_url TEXT,
        preco_promo REAL,
        em_promo INTEGER DEFAULT 0)''')

    c.execute('''CREATE TABLE IF NOT EXISTS pedidos (
        id SERIAL PRIMARY KEY,
        cliente_id INTEGER REFERENCES clientes_saas(id),
        nome_cliente TEXT NOT NULL,
        telefone TEXT,
        observacao TEXT,
        total REAL NOT NULL,
        subtotal REAL NOT NULL DEFAULT 0,
        frete REAL NOT NULL DEFAULT 0,
        tipo_entrega TEXT DEFAULT 'retirada',
        endereco TEXT,
        forma_pagamento TEXT,
        status TEXT DEFAULT 'pendente',
        criado_em TEXT DEFAULT to_char(NOW() AT TIME ZONE 'America/Sao_Paulo', 'YYYY-MM-DD HH24:MI:SS'))''')

    c.execute('''CREATE TABLE IF NOT EXISTS itens_pedido (
        id SERIAL PRIMARY KEY,
        cliente_id INTEGER REFERENCES clientes_saas(id),
        pedido_id INTEGER NOT NULL REFERENCES pedidos(id),
        produto_id INTEGER NOT NULL REFERENCES produtos(id),
        quantidade INTEGER NOT NULL,
        preco_unit REAL NOT NULL)''')

    c.execute('''CREATE TABLE IF NOT EXISTS clientes (
        id SERIAL PRIMARY KEY,
        cliente_id INTEGER REFERENCES clientes_saas(id),
        telefone TEXT,
        nome TEXT,
        email TEXT,
        endereco TEXT,
        tipo TEXT DEFAULT 'cliente',
        cpf_cnpj TEXT,
        criado_em TEXT DEFAULT to_char(NOW() AT TIME ZONE 'America/Sao_Paulo', 'YYYY-MM-DD HH24:MI:SS'))''')

    c.execute('''CREATE TABLE IF NOT EXISTS financeiro (
        id SERIAL PRIMARY KEY,
        cliente_id INTEGER REFERENCES clientes_saas(id),
        pedido_id INTEGER REFERENCES pedidos(id),
        cli_id INTEGER REFERENCES clientes(id),
        empresa_id INTEGER REFERENCES clientes(id),
        valor REAL NOT NULL,
        tipo TEXT NOT NULL DEFAULT 'entrada',
        forma_pagamento TEXT,
        descricao TEXT,
        observacao TEXT,
        pago INTEGER DEFAULT 1,
        data_lancamento TEXT,
        criado_em TEXT DEFAULT to_char(NOW() AT TIME ZONE 'America/Sao_Paulo', 'YYYY-MM-DD HH24:MI:SS'))''')

    c.execute('''CREATE TABLE IF NOT EXISTS admin (
        id SERIAL PRIMARY KEY,
        cliente_id INTEGER REFERENCES clientes_saas(id) ON DELETE CASCADE,
        usuario TEXT NOT NULL,
        senha TEXT NOT NULL,
        role TEXT DEFAULT 'admin',
        nome TEXT,
        permissions TEXT DEFAULT '{}',
        email TEXT,
        UNIQUE(cliente_id, usuario))''')

    # ── MIGRATIONS ────────────────────────────────────────────────────────────────
    migrations = [
        # clientes_saas extras
        "ALTER TABLE clientes_saas ADD COLUMN IF NOT EXISTS observacao TEXT",
        "ALTER TABLE clientes_saas ADD COLUMN IF NOT EXISTS webhook_ref TEXT",
        # platform_config extras
        "ALTER TABLE platform_config ADD COLUMN IF NOT EXISTS supabase_bucket TEXT DEFAULT 'cardapio'",
        "ALTER TABLE platform_config ADD COLUMN IF NOT EXISTS webhook_url TEXT",
        # config_tenant extras
        "ALTER TABLE config_tenant ADD COLUMN IF NOT EXISTS smtp TEXT DEFAULT '{}'",
        # tabelas core: garantir cliente_id
        "ALTER TABLE categorias ADD COLUMN IF NOT EXISTS cliente_id INTEGER",
        "ALTER TABLE produtos ADD COLUMN IF NOT EXISTS cliente_id INTEGER",
        "ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS cliente_id INTEGER",
        "ALTER TABLE itens_pedido ADD COLUMN IF NOT EXISTS cliente_id INTEGER",
        "ALTER TABLE clientes ADD COLUMN IF NOT EXISTS cliente_id INTEGER",
        "ALTER TABLE financeiro ADD COLUMN IF NOT EXISTS cliente_id INTEGER",
        "ALTER TABLE financeiro ADD COLUMN IF NOT EXISTS cli_id INTEGER",
        "ALTER TABLE admin ADD COLUMN IF NOT EXISTS cliente_id INTEGER",
        # financeiro antigas colunas
        "ALTER TABLE financeiro ADD COLUMN IF NOT EXISTS tipo TEXT NOT NULL DEFAULT 'entrada'",
        "ALTER TABLE financeiro ADD COLUMN IF NOT EXISTS descricao TEXT",
        "ALTER TABLE financeiro ADD COLUMN IF NOT EXISTS observacao TEXT",
        "ALTER TABLE financeiro ADD COLUMN IF NOT EXISTS pago INTEGER DEFAULT 1",
        "ALTER TABLE financeiro ADD COLUMN IF NOT EXISTS data_lancamento TEXT",
        "ALTER TABLE financeiro ADD COLUMN IF NOT EXISTS empresa_id INTEGER",
        # produtos antigas colunas
        "ALTER TABLE produtos ADD COLUMN IF NOT EXISTS preco_promo REAL",
        "ALTER TABLE produtos ADD COLUMN IF NOT EXISTS em_promo INTEGER DEFAULT 0",
        # clientes antigas colunas
        "ALTER TABLE clientes ADD COLUMN IF NOT EXISTS tipo TEXT DEFAULT 'cliente'",
        "ALTER TABLE clientes ADD COLUMN IF NOT EXISTS cpf_cnpj TEXT",
        "ALTER TABLE clientes ALTER COLUMN telefone DROP NOT NULL",
        # admin antigas colunas
        "ALTER TABLE admin ADD COLUMN IF NOT EXISTS role TEXT DEFAULT 'admin'",
        "ALTER TABLE admin ADD COLUMN IF NOT EXISTS nome TEXT",
        "ALTER TABLE admin ADD COLUMN IF NOT EXISTS permissions TEXT DEFAULT '{}'",
        "ALTER TABLE admin ADD COLUMN IF NOT EXISTS email TEXT",
        "UPDATE admin SET role='admin' WHERE role IS NULL",
        # Remover constraint única apenas em usuario (legado mono-tenant)
        # A constraint correta é (cliente_id, usuario) já definida no CREATE TABLE
        "ALTER TABLE admin DROP CONSTRAINT IF EXISTS admin_usuario_key",
        # pedidos antigas colunas
        "ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS subtotal REAL NOT NULL DEFAULT 0",
        "ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS frete REAL NOT NULL DEFAULT 0",
        "ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS tipo_entrega TEXT DEFAULT 'retirada'",
        "ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS endereco TEXT",
        "ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS forma_pagamento TEXT",
        "UPDATE pedidos SET status='pendente' WHERE status='novo'",
    ]
    for m in migrations:
        try: c.execute(m)
        except: pass

    # ── PLATFORM CONFIG padrão ───────────────────────────────────────────────────
    c.execute("SELECT COUNT(*) FROM platform_config")
    if c.fetchone()[0] == 0:
        c.execute("""INSERT INTO platform_config (id, supabase_url, supabase_key, supabase_bucket)
            VALUES (1, 'https://isfndtvadwpxzvrtmtfy.supabase.co',
                    'sb_publishable_-iRb2BiZV3I6WBlbJzbaOg_2nXo8_72', 'cardapio')""")

    # ── SUPER ADMIN padrão ───────────────────────────────────────────────────────
    c.execute("SELECT COUNT(*) FROM super_admin")
    if c.fetchone()[0] == 0:
        import hashlib
        c.execute("INSERT INTO super_admin (usuario, senha, nome) VALUES (%s,%s,%s)",
            ("superadmin", hashlib.sha256("super123".encode()).hexdigest(), "Super Admin"))

    # ── TENANT padrão (migração de dados legados) ────────────────────────────────
    c.execute("SELECT COUNT(*) FROM clientes_saas")
    if c.fetchone()[0] == 0:
        import hashlib, time as _time
        c.execute("""INSERT INTO clientes_saas (nome, email, slug, senha, status, plano, data_entrada)
            VALUES (%s,%s,%s,%s,'ativo','basico',%s) RETURNING id""",
            ("Loja Padrão", "admin@cardapio.local", "cardapio",
             hashlib.sha256("admin123".encode()).hexdigest(),
             _time.strftime('%Y-%m-%d')))
        default_cid = c.fetchone()[0]

        # Migrar dados legados para o tenant padrão
        for tbl in ['categorias','produtos','pedidos','itens_pedido','clientes','financeiro']:
            try:
                c.execute(f"UPDATE {tbl} SET cliente_id=%s WHERE cliente_id IS NULL", (default_cid,))
            except: pass

        # Criar config_tenant para o tenant padrão
        c.execute("""INSERT INTO config_tenant (cliente_id, nome_loja, tipos_pagamento)
            VALUES (%s, 'Cardápio Digital', 'Dinheiro,PIX,Cartão de Débito,Cartão de Crédito')""",
            (default_cid,))

        # Migrar admin existentes para o tenant padrão
        try:
            c.execute("UPDATE admin SET cliente_id=%s WHERE cliente_id IS NULL", (default_cid,))
        except: pass

        # Se não houver admin, criar um
        c.execute("SELECT COUNT(*) FROM admin WHERE cliente_id=%s", (default_cid,))
        if c.fetchone()[0] == 0:
            import hashlib as _h
            c.execute("INSERT INTO admin (cliente_id, usuario, senha, role) VALUES (%s,%s,%s,'admin')",
                (default_cid, "admin", _h.sha256("admin123".encode()).hexdigest()))

        # Criar categorias/produtos demo se não existirem
        c.execute("SELECT COUNT(*) FROM categorias WHERE cliente_id=%s", (default_cid,))
        if c.fetchone()[0] == 0:
            c.execute("INSERT INTO categorias (cliente_id,nome,ordem) VALUES (%s,'Lanches',1) RETURNING id", (default_cid,)); cat1=c.fetchone()[0]
            c.execute("INSERT INTO categorias (cliente_id,nome,ordem) VALUES (%s,'Bebidas',2) RETURNING id", (default_cid,)); cat2=c.fetchone()[0]
            c.execute("INSERT INTO categorias (cliente_id,nome,ordem) VALUES (%s,'Sobremesas',3) RETURNING id", (default_cid,)); cat3=c.fetchone()[0]
            c.executemany("INSERT INTO produtos (cliente_id,categoria_id,nome,descricao,preco) VALUES (%s,%s,%s,%s,%s)",[
                (default_cid,cat1,"X-Burguer","Pão, hambúrguer artesanal, queijo e alface",18.90),
                (default_cid,cat1,"X-Bacon","Pão, hambúrguer, bacon crocante e queijo",22.90),
                (default_cid,cat2,"Coca-Cola","Lata 350ml",6.00),
                (default_cid,cat3,"Pudim","Pudim de leite condensado",8.50)])

    conn.commit(); conn.close()
