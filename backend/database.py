import psycopg2
import psycopg2.extras
import os
import time

DATABASE_URL = os.environ.get('DATABASE_URL')


def get_connection():
    conn = psycopg2.connect(DATABASE_URL)
    return conn


def init_db():
    # Aguarda o PostgreSQL ficar pronto (útil no Docker)
    retries = 10
    while retries > 0:
        try:
            conn = get_connection()
            break
        except psycopg2.OperationalError:
            retries -= 1
            print(f"Aguardando banco de dados... ({retries} tentativas restantes)")
            time.sleep(2)
    else:
        raise Exception("Não foi possível conectar ao banco de dados.")

    c = conn.cursor()

    c.execute('''CREATE TABLE IF NOT EXISTS categorias (
        id SERIAL PRIMARY KEY,
        nome TEXT NOT NULL,
        ordem INTEGER DEFAULT 0,
        ativa INTEGER DEFAULT 1
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS produtos (
        id SERIAL PRIMARY KEY,
        categoria_id INTEGER NOT NULL REFERENCES categorias(id),
        nome TEXT NOT NULL,
        descricao TEXT,
        preco REAL NOT NULL,
        disponivel INTEGER DEFAULT 1,
        foto_url TEXT
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS pedidos (
        id SERIAL PRIMARY KEY,
        nome_cliente TEXT NOT NULL,
        telefone TEXT,
        observacao TEXT,
        total REAL NOT NULL,
        status TEXT DEFAULT 'novo',
        criado_em TEXT DEFAULT to_char(NOW(), 'YYYY-MM-DD HH24:MI:SS')
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS itens_pedido (
        id SERIAL PRIMARY KEY,
        pedido_id INTEGER NOT NULL REFERENCES pedidos(id),
        produto_id INTEGER NOT NULL REFERENCES produtos(id),
        quantidade INTEGER NOT NULL,
        preco_unit REAL NOT NULL
    )''')

    # Inserir dados iniciais se o banco estiver vazio
    c.execute("SELECT COUNT(*) FROM categorias")
    if c.fetchone()[0] == 0:
        c.execute("INSERT INTO categorias (nome, ordem) VALUES ('Lanches', 1) RETURNING id")
        cat1 = c.fetchone()[0]
        c.execute("INSERT INTO categorias (nome, ordem) VALUES ('Bebidas', 2) RETURNING id")
        cat2 = c.fetchone()[0]
        c.execute("INSERT INTO categorias (nome, ordem) VALUES ('Sobremesas', 3) RETURNING id")
        cat3 = c.fetchone()[0]

        c.executemany(
            "INSERT INTO produtos (categoria_id, nome, descricao, preco) VALUES (%s, %s, %s, %s)",
            [
                (cat1, "X-Burguer",       "Pão, hambúrguer artesanal, queijo e alface", 18.90),
                (cat1, "X-Bacon",         "Pão, hambúrguer, bacon crocante e queijo",   22.90),
                (cat1, "X-Frango",        "Pão, frango grelhado, queijo e tomate",      19.90),
                (cat2, "Coca-Cola",       "Lata 350ml",                                  6.00),
                (cat2, "Suco de laranja", "Natural 400ml gelado",                         9.00),
                (cat2, "Água mineral",    "Garrafa 500ml",                               4.00),
                (cat3, "Pudim",           "Pudim de leite condensado caseiro",            8.50),
                (cat3, "Brownie",         "Brownie de chocolate com nozes",               9.90),
            ]
        )

    conn.commit()
    conn.close()
