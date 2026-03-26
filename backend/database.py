import psycopg2
import os
import time

DATABASE_URL = os.environ.get('DATABASE_URL')

def get_connection():
    conn = psycopg2.connect(DATABASE_URL)
    return conn

def fetchall(cursor):
    cols = [desc[0] for desc in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]

def fetchone(cursor):
    cols = [desc[0] for desc in cursor.description]
    row = cursor.fetchone()
    return dict(zip(cols, row)) if row else None

def init_db():
    retries = 10
    while retries > 0:
        try:
            conn = get_connection(); break
        except psycopg2.OperationalError:
            retries -= 1
            print(f"Aguardando banco... ({retries})")
            time.sleep(2)
    else:
        raise Exception("Não foi possível conectar ao banco.")

    c = conn.cursor()

    c.execute('''CREATE TABLE IF NOT EXISTS categorias (
        id SERIAL PRIMARY KEY, nome TEXT NOT NULL,
        ordem INTEGER DEFAULT 0, ativa INTEGER DEFAULT 1)''')

    c.execute('''CREATE TABLE IF NOT EXISTS produtos (
        id SERIAL PRIMARY KEY, categoria_id INTEGER NOT NULL REFERENCES categorias(id),
        nome TEXT NOT NULL, descricao TEXT, preco REAL NOT NULL,
        disponivel INTEGER DEFAULT 1, foto_url TEXT)''')

    c.execute('''CREATE TABLE IF NOT EXISTS pedidos (
        id SERIAL PRIMARY KEY, nome_cliente TEXT NOT NULL,
        telefone TEXT, observacao TEXT, total REAL NOT NULL,
        subtotal REAL NOT NULL DEFAULT 0, frete REAL NOT NULL DEFAULT 0,
        tipo_entrega TEXT DEFAULT 'retirada', endereco TEXT,
        forma_pagamento TEXT, status TEXT DEFAULT 'novo',
        criado_em TEXT DEFAULT to_char(NOW(), 'YYYY-MM-DD HH24:MI:SS'))''')

    c.execute('''CREATE TABLE IF NOT EXISTS itens_pedido (
        id SERIAL PRIMARY KEY, pedido_id INTEGER NOT NULL REFERENCES pedidos(id),
        produto_id INTEGER NOT NULL REFERENCES produtos(id),
        quantidade INTEGER NOT NULL, preco_unit REAL NOT NULL)''')

    c.execute('''CREATE TABLE IF NOT EXISTS clientes (
        id SERIAL PRIMARY KEY, telefone TEXT UNIQUE NOT NULL,
        nome TEXT, email TEXT, endereco TEXT,
        criado_em TEXT DEFAULT to_char(NOW(), 'YYYY-MM-DD HH24:MI:SS'))''')

    c.execute('''CREATE TABLE IF NOT EXISTS financeiro (
        id SERIAL PRIMARY KEY,
        pedido_id INTEGER REFERENCES pedidos(id),
        cliente_id INTEGER REFERENCES clientes(id),
        valor REAL NOT NULL,
        tipo TEXT NOT NULL DEFAULT 'entrada',
        forma_pagamento TEXT,
        descricao TEXT,
        observacao TEXT,
        pago INTEGER DEFAULT 1,
        criado_em TEXT DEFAULT to_char(NOW(), 'YYYY-MM-DD HH24:MI:SS'))''')

    c.execute('''CREATE TABLE IF NOT EXISTS admin (
        id SERIAL PRIMARY KEY, usuario TEXT UNIQUE NOT NULL, senha TEXT NOT NULL)''')

    # Migrações seguras
    migrations = [
        "ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS subtotal REAL NOT NULL DEFAULT 0",
        "ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS frete REAL NOT NULL DEFAULT 0",
        "ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS tipo_entrega TEXT DEFAULT 'retirada'",
        "ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS endereco TEXT",
        "ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS forma_pagamento TEXT",
        "ALTER TABLE financeiro ADD COLUMN IF NOT EXISTS tipo TEXT NOT NULL DEFAULT 'entrada'",
        "ALTER TABLE financeiro ADD COLUMN IF NOT EXISTS descricao TEXT",
        "ALTER TABLE financeiro ADD COLUMN IF NOT EXISTS observacao TEXT",
        "ALTER TABLE financeiro ADD COLUMN IF NOT EXISTS pago INTEGER DEFAULT 1",
    ]
    for m in migrations:
        try: c.execute(m)
        except: pass

    # Dados iniciais
    c.execute("SELECT COUNT(*) FROM categorias")
    if c.fetchone()[0] == 0:
        c.execute("INSERT INTO categorias (nome, ordem) VALUES ('Lanches', 1) RETURNING id")
        cat1 = c.fetchone()[0]
        c.execute("INSERT INTO categorias (nome, ordem) VALUES ('Bebidas', 2) RETURNING id")
        cat2 = c.fetchone()[0]
        c.execute("INSERT INTO categorias (nome, ordem) VALUES ('Sobremesas', 3) RETURNING id")
        cat3 = c.fetchone()[0]
        c.executemany("INSERT INTO produtos (categoria_id, nome, descricao, preco) VALUES (%s,%s,%s,%s)", [
            (cat1,"X-Burguer","Pão, hambúrguer artesanal, queijo e alface",18.90),
            (cat1,"X-Bacon","Pão, hambúrguer, bacon crocante e queijo",22.90),
            (cat2,"Coca-Cola","Lata 350ml",6.00),
            (cat3,"Pudim","Pudim de leite condensado caseiro",8.50),
        ])

    c.execute("SELECT COUNT(*) FROM admin")
    if c.fetchone()[0] == 0:
        import hashlib
        senha = hashlib.sha256("admin123".encode()).hexdigest()
        c.execute("INSERT INTO admin (usuario, senha) VALUES (%s,%s)", ("admin", senha))

    conn.commit(); conn.close()
