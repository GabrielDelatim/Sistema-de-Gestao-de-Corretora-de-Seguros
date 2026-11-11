# =============================================================================
# data.py — CAMADA DE DADOS E PERSISTÊNCIA RELACIONAL (SQLite)
# -----------------------------------------------------------------------------
# Implementação sobre SQLite ('corretora.db') do Modelo Entidade-Relacionamento.
# Mantém compatibilidade 100% com a interface do Flask e dos templates.
# =============================================================================

import json
from datetime import date, datetime, timedelta
from werkzeug.security import generate_password_hash

import database

# Garante que o banco de dados e as tabelas estejam criados ao carregar o módulo
database.init_db()

# Dias de antecedência que disparam a criação automática do card de renovação (RF09)
DIAS_PARA_RENOVACAO = 30


def listar_comerciais():
    """Retorna a lista de nomes de usuários que atuam como comercial ou administrador."""
    try:
        conn = database.get_db()
        rows = conn.execute(
            "SELECT nome FROM usuarios WHERE status = 'Ativo' AND perfil IN ('Comercial', 'Administrador') ORDER BY nome ASC;"
        ).fetchall()
        conn.close()
        nomes = [r["nome"] for r in rows]
        return nomes if nomes else ["Administrador"]
    except Exception:
        return ["Administrador"]


def registrar_auditoria(usuario, perfil, acao, detalhes="", ip=""):
    """Grava um registro na tabela de logs de auditoria."""
    try:
        conn = database.get_db()
        conn.execute("""
            INSERT INTO logs_auditoria (usuario, perfil, acao, detalhes, ip)
            VALUES (?, ?, ?, ?, ?);
        """, (str(usuario or "SISTEMA"), str(perfil or ""), str(acao), str(detalhes or ""), str(ip or "")))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[ERRO LOG AUDITORIA] {e}")


def listar_logs_auditoria(limite=100):
    """Consulta os últimos registros de auditoria."""
    try:
        conn = database.get_db()
        rows = conn.execute("SELECT * FROM logs_auditoria ORDER BY data DESC LIMIT ?;", (limite,)).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []

COLUNAS_SEGUROS = [
    "A renovar",
    "Em cotação",
    "Calculados",
    "Em negociação",
    "Em alteração",
    "Fechado",
    "Lista de transmissão",
    "Transmitido",
    "Acompanhamento pagamento/pendência",
    "Emitido/aguardando assinatura",
    "Finalizado",
    "Perdido",
]

COLUNAS_SINISTRO = [
    "Sinistro aberto",
    "Em regulação",
    "Em acompanhamento",
    "Finalizado"
]

COLUNAS_FINAIS = {"Finalizado", "Perdido"}
PERFIS = ["Administrador", "Comercial", "Assistente", "Área Sinistros"]

TIPOS_CARD = {
    "Renovação": "seguro",
    "Novo": "seguro",
    "Endosso": "seguro",
    "Cancelamento": "seguro",
    "Sinistro": "sinistro",
}

TIPOS_TAREFA = ["Cotação", "Cálculo", "Vistoria", "Transmissão", "Emissão", "Sinistro"]
URGENCIAS = ["Baixa", "Média", "Alta"]


# -----------------------------------------------------------------------------
# UTILITÁRIOS INTERNOS
# -----------------------------------------------------------------------------
def dias_restantes(data_alvo):
    """Calcula a diferença em dias entre a data alvo e hoje."""
    if not data_alvo:
        return 0
    if isinstance(data_alvo, str):
        data_alvo = date.fromisoformat(data_alvo)
    return (data_alvo - date.today()).days


def em_negociacao(status):
    return (status or "").endswith("em negociação")


def _parse_date(val):
    if not val:
        return None
    if isinstance(val, (date, datetime)):
        return val if isinstance(val, date) else val.date()
    try:
        return date.fromisoformat(str(val))
    except Exception:
        return None


def _parse_json(val):
    if not val:
        return []
    if isinstance(val, (list, dict)):
        return val
    try:
        return json.loads(val)
    except Exception:
        return []


def _float(val):
    if not val:
        return 0.0
    try:
        return float(str(val).replace(",", "."))
    except (ValueError, TypeError):
        return 0.0


# -----------------------------------------------------------------------------
# USUÁRIOS
# -----------------------------------------------------------------------------
def _usuario_from_row(row):
    if not row:
        return None
    return dict(row)


def buscar_usuario(id_usuario):
    conn = database.get_db()
    row = conn.execute("SELECT * FROM usuarios WHERE id = ?;", (id_usuario,)).fetchone()
    conn.close()
    return _usuario_from_row(row)


def buscar_usuario_por_login(login):
    conn = database.get_db()
    row = conn.execute("SELECT * FROM usuarios WHERE login = ?;", (login,)).fetchone()
    conn.close()
    return _usuario_from_row(row)


def listar_usuarios():
    conn = database.get_db()
    rows = conn.execute("SELECT * FROM usuarios ORDER BY id ASC;").fetchall()
    conn.close()
    return [_usuario_from_row(r) for r in rows]


def garantir_quadros_do_comercial(nome_comercial):
    """
    Cria automaticamente os quadros de Seguros e Sinistros vinculados ao usuário Comercial.
    Não existem quadros gerais: cada comercial possui seu par de quadros de negociação.
    """
    if not nome_comercial:
        return
    conn = database.get_db()
    cursor = conn.cursor()

    # Quadro de Seguros do Comercial
    q_seg = cursor.execute("SELECT id FROM quadros WHERE comercial = ? AND tipo = 'Seguros';", (nome_comercial,)).fetchone()
    if not q_seg:
        cursor.execute("""
            INSERT INTO quadros (nome, comercial, tipo, colunas_json)
            VALUES (?, ?, 'Seguros', ?);
        """, (f"Seguros — {nome_comercial}", nome_comercial, json.dumps(COLUNAS_SEGUROS)))

    # Quadro de Sinistros do Comercial
    q_sin = cursor.execute("SELECT id FROM quadros WHERE comercial = ? AND tipo = 'Sinistro';", (nome_comercial,)).fetchone()
    if not q_sin:
        cursor.execute("""
            INSERT INTO quadros (nome, comercial, tipo, colunas_json)
            VALUES (?, ?, 'Sinistro', ?);
        """, (f"Sinistros — {nome_comercial}", nome_comercial, json.dumps(COLUNAS_SINISTRO)))

    conn.commit()
    conn.close()


def excluir_quadros_do_comercial(nome_comercial):
    """
    Exclui os quadros e todos os respectivos cartões do usuário Comercial removido.
    """
    if not nome_comercial:
        return
    conn = database.get_db()
    cursor = conn.cursor()
    quadros = cursor.execute("SELECT id FROM quadros WHERE comercial = ?;", (nome_comercial,)).fetchall()
    for q in quadros:
        cursor.execute("DELETE FROM cards WHERE quadro_id = ?;", (q["id"],))
    cursor.execute("DELETE FROM quadros WHERE comercial = ?;", (nome_comercial,))
    conn.commit()
    conn.close()


def criar_usuario(dados):
    conn = database.get_db()
    senha = dados.get("senha") or "123456"
    senha_hash = generate_password_hash(senha)
    nome = dados.get("nome", "").strip()
    perfil = dados.get("perfil", "Assistente")

    cursor = conn.execute("""
        INSERT INTO usuarios (nome, login, senha_hash, perfil, status)
        VALUES (?, ?, ?, ?, ?);
    """, (
        nome,
        dados.get("login", "").strip(),
        senha_hash,
        perfil,
        dados.get("status", "Ativo")
    ))
    novo_id = cursor.lastrowid
    conn.commit()
    conn.close()

    # Regra: se o usuário criado for Comercial, gera os 2 quadros exclusivos dele
    if perfil == "Comercial":
        garantir_quadros_do_comercial(nome)

    return buscar_usuario(novo_id)


def atualizar_usuario(id_usuario, dados):
    usuario_antigo = buscar_usuario(id_usuario)
    if not usuario_antigo:
        return None

    nome_antigo = usuario_antigo["nome"]
    perfil_antigo = usuario_antigo["perfil"]

    novo_nome = dados.get("nome", nome_antigo).strip()
    novo_login = dados.get("login", usuario_antigo["login"]).strip()
    novo_perfil = dados.get("perfil", perfil_antigo)
    novo_status = dados.get("status", usuario_antigo["status"])

    conn = database.get_db()
    if dados.get("senha"):
        senha_hash = generate_password_hash(dados["senha"])
        conn.execute("""
            UPDATE usuarios SET nome = ?, login = ?, perfil = ?, status = ?, senha_hash = ?
            WHERE id = ?;
        """, (novo_nome, novo_login, novo_perfil, novo_status, senha_hash, id_usuario))
    else:
        conn.execute("""
            UPDATE usuarios SET nome = ?, login = ?, perfil = ?, status = ?
            WHERE id = ?;
        """, (novo_nome, novo_login, novo_perfil, novo_status, id_usuario))

    conn.commit()
    conn.close()

    # Ajusta os quadros se houve mudança de perfil ou nome
    if perfil_antigo == "Comercial" and novo_perfil != "Comercial":
        excluir_quadros_do_comercial(nome_antigo)
    elif novo_perfil == "Comercial":
        if nome_antigo != novo_nome:
            conn = database.get_db()
            conn.execute("UPDATE quadros SET comercial = ?, nome = REPLACE(nome, ?, ?) WHERE comercial = ?;",
                         (novo_nome, nome_antigo, novo_nome, nome_antigo))
            conn.commit()
            conn.close()
        garantir_quadros_do_comercial(novo_nome)

    return buscar_usuario(id_usuario)


def deletar_usuario(id_usuario):
    usuario = buscar_usuario(id_usuario)
    if usuario:
        # Se for comercial, exclui seus quadros de negociação e respectivos cards
        if usuario["perfil"] == "Comercial":
            excluir_quadros_do_comercial(usuario["nome"])

        conn = database.get_db()
        conn.execute("DELETE FROM usuarios WHERE id = ?;", (id_usuario,))
        conn.commit()
        conn.close()
    return True


def admin_usa_senha_padrao():
    """Verifica se o usuário admin ainda está com a senha padrão inicial."""
    try:
        conn = database.get_db()
        row = conn.execute("SELECT senha_hash, precisa_trocar_senha FROM usuarios WHERE login = 'admin';").fetchone()
        conn.close()
        if not row:
            return False
        if row["precisa_trocar_senha"] == 1:
            return True
        from werkzeug.security import check_password_hash
        return check_password_hash(row["senha_hash"], "admin123")
    except Exception:
        return False


def alterar_senha_usuario(id_usuario, nova_senha):
    """Altera a senha de um usuário e zera a flag de troca obrigatória."""
    try:
        conn = database.get_db()
        senha_hash = generate_password_hash(nova_senha)
        conn.execute("""
            UPDATE usuarios
            SET senha_hash = ?, precisa_trocar_senha = 0
            WHERE id = ?;
        """, (senha_hash, id_usuario))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"[ERRO ALTERAR SENHA] {e}")
        return False


# -----------------------------------------------------------------------------
# CONFIGURAÇÕES DA CORRETORA (Nome da Corretora, etc.)
# -----------------------------------------------------------------------------
def obter_configuracao(chave, padrao=""):
    try:
        conn = database.get_db()
        row = conn.execute("SELECT valor FROM configuracoes WHERE chave = ?;", (chave,)).fetchone()
        conn.close()
        return row["valor"] if row else padrao
    except Exception:
        return padrao


def salvar_configuracao(chave, valor):
    try:
        conn = database.get_db()
        conn.execute("""
            INSERT INTO configuracoes (chave, valor) VALUES (?, ?)
            ON CONFLICT(chave) DO UPDATE SET valor = excluded.valor;
        """, (chave, str(valor or "").strip()))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"[ERRO CONFIG] {e}")
        return False


def nome_corretora():
    return obter_configuracao("nome_corretora", "Corretora de Seguros")


# -----------------------------------------------------------------------------
# QUADROS KANBAN
# -----------------------------------------------------------------------------
def _quadro_from_row(row):
    if not row:
        return None
    d = dict(row)
    d["colunas"] = _parse_json(d.get("colunas_json"))
    return d


def listar_quadros():
    conn = database.get_db()
    rows = conn.execute("SELECT * FROM quadros ORDER BY id ASC;").fetchall()
    conn.close()
    return [_quadro_from_row(r) for r in rows]


QUADROS = listar_quadros()


def buscar_quadro(id_quadro):
    conn = database.get_db()
    row = conn.execute("SELECT * FROM quadros WHERE id = ?;", (id_quadro,)).fetchone()
    conn.close()
    return _quadro_from_row(row)


def quadro_do_comercial(nome_comercial, tipo):
    conn = database.get_db()
    row = conn.execute("SELECT * FROM quadros WHERE comercial = ? AND tipo = ?;", (nome_comercial, tipo)).fetchone()
    conn.close()
    if not row and nome_comercial:
        garantir_quadros_do_comercial(nome_comercial)
        conn = database.get_db()
        row = conn.execute("SELECT * FROM quadros WHERE comercial = ? AND tipo = ?;", (nome_comercial, tipo)).fetchone()
        conn.close()
    return _quadro_from_row(row)


def quadros_do_usuario(usuario):
    """Regra de negócio central de visibilidade dos quadros conforme o perfil."""
    todos = listar_quadros()
    perfil = usuario["perfil"]
    if perfil == "Administrador":
        return todos
    if perfil == "Comercial":
        return [q for q in todos if q["comercial"] == usuario["nome"]]
    if perfil == "Assistente":
        return [q for q in todos if q["tipo"] == "Seguros"]
    if perfil == "Área Sinistros":
        return [q for q in todos if q["tipo"] == "Sinistro"]
    return []


# -----------------------------------------------------------------------------
# CADASTROS (Clientes)
# -----------------------------------------------------------------------------
def _cadastro_from_row(row):
    if not row:
        return None
    d = dict(row)
    if d.get("data_cadastro"):
        d["data_cadastro"] = _parse_date(d["data_cadastro"])
    return d


def listar_cadastros():
    conn = database.get_db()
    rows = conn.execute("SELECT * FROM cadastros ORDER BY nome ASC;").fetchall()
    conn.close()
    return [_cadastro_from_row(r) for r in rows]


def buscar_cadastro(id_cadastro):
    conn = database.get_db()
    row = conn.execute("SELECT * FROM cadastros WHERE id = ?;", (id_cadastro,)).fetchone()
    conn.close()
    return _cadastro_from_row(row)


def clientes_do_comercial(nome_comercial):
    conn = database.get_db()
    rows = conn.execute("SELECT * FROM cadastros WHERE comercial = ? AND status = 'Ativo' ORDER BY nome ASC;", (nome_comercial,)).fetchall()
    conn.close()
    return [_cadastro_from_row(r) for r in rows]


def pesquisar_cadastros(termo, modo, filtros):
    """Pesquisa RF03 com filtros dinâmicos."""
    conn = database.get_db()
    sql = "SELECT * FROM cadastros WHERE 1=1"
    params = []

    termo = (termo or "").strip().lower()
    if termo:
        if modo == "comeca_com":
            sql += " AND (LOWER(nome) LIKE ? OR LOWER(documento) LIKE ?)"
            params.extend([f"{termo}%", f"{termo}%"])
        else:
            sql += " AND (LOWER(nome) LIKE ? OR LOWER(documento) LIKE ?)"
            params.extend([f"%{termo}%", f"%{termo}%"])

    if filtros.get("tipo"):
        sql += " AND tipo = ?"
        params.append(filtros["tipo"])

    if filtros.get("status"):
        sql += " AND status = ?"
        params.append(filtros["status"])

    if filtros.get("origem"):
        sql += " AND origem = ?"
        params.append(filtros["origem"])

    if filtros.get("comercial"):
        sql += " AND comercial = ?"
        params.append(filtros["comercial"])

    if filtros.get("vinculos"):
        sql += " AND LOWER(vinculos) LIKE ?"
        params.append(f"%{filtros['vinculos'].lower()}%")

    sql += " ORDER BY nome ASC;"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [_cadastro_from_row(r) for r in rows]


def criar_cadastro(dados):
    conn = database.get_db()
    cursor = conn.execute("""
        INSERT INTO cadastros (nome, documento, tipo, vinculos, descricao, comercial, status, origem, endereco, telefone, email, data_cadastro)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
    """, (
        dados.get("nome", "").strip(),
        dados.get("documento", "").strip(),
        dados.get("tipo", "PF"),
        dados.get("vinculos", "").strip(),
        dados.get("descricao", "").strip(),
        dados.get("comercial", "").strip(),
        dados.get("status", "Ativo"),
        dados.get("origem", "").strip(),
        dados.get("endereco", "").strip(),
        dados.get("telefone", "").strip(),
        dados.get("email", "").strip(),
        date.today().isoformat()
    ))
    novo_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return buscar_cadastro(novo_id)


def atualizar_cadastro(id_cadastro, dados):
    conn = database.get_db()
    conn.execute("""
        UPDATE cadastros SET
            nome = ?, documento = ?, tipo = ?, vinculos = ?, descricao = ?,
            comercial = ?, status = ?, origem = ?, endereco = ?, telefone = ?, email = ?
        WHERE id = ?;
    """, (
        dados.get("nome", "").strip(),
        dados.get("documento", "").strip(),
        dados.get("tipo", "PF"),
        dados.get("vinculos", "").strip(),
        dados.get("descricao", "").strip(),
        dados.get("comercial", "").strip(),
        dados.get("status", "Ativo"),
        dados.get("origem", "").strip(),
        dados.get("endereco", "").strip(),
        dados.get("telefone", "").strip(),
        dados.get("email", "").strip(),
        id_cadastro
    ))
    conn.commit()
    conn.close()
    return buscar_cadastro(id_cadastro)


def deletar_cadastro(id_cadastro):
    conn = database.get_db()
    conn.execute("DELETE FROM cadastros WHERE id = ?;", (id_cadastro,))
    conn.commit()
    conn.close()
    return True


# -----------------------------------------------------------------------------
# SEGUROS (Apólices)
# -----------------------------------------------------------------------------
def _seguro_from_row(row):
    if not row:
        return None
    d = dict(row)
    d["vigencia_inicio"] = _parse_date(d.get("vigencia_inicio"))
    d["vigencia_fim"] = _parse_date(d.get("vigencia_fim"))
    d["anexos"] = _parse_json(d.get("anexos_json"))
    return d


def listar_seguros():
    conn = database.get_db()
    rows = conn.execute("SELECT * FROM seguros ORDER BY id ASC;").fetchall()
    conn.close()
    return [_seguro_from_row(r) for r in rows]


def buscar_seguro(id_seguro):
    conn = database.get_db()
    row = conn.execute("SELECT * FROM seguros WHERE id = ?;", (id_seguro,)).fetchone()
    conn.close()
    return _seguro_from_row(row)


def seguros_do_cadastro(id_cadastro):
    conn = database.get_db()
    rows = conn.execute("SELECT * FROM seguros WHERE cadastro_id = ? ORDER BY id DESC;", (id_cadastro,)).fetchall()
    conn.close()
    return [_seguro_from_row(r) for r in rows]


def apolices_ativas(comercial=None, com_cliente=False):
    conn = database.get_db()
    if comercial:
        sql = """
            SELECT s.* FROM seguros s
            JOIN cadastros c ON s.cadastro_id = c.id
            WHERE s.status = 'Ativo' AND c.comercial = ?
            ORDER BY s.id DESC;
        """
        rows = conn.execute(sql, (comercial,)).fetchall()
    else:
        sql = "SELECT * FROM seguros WHERE status = 'Ativo' ORDER BY id DESC;"
        rows = conn.execute(sql).fetchall()

    resultado = []
    for r in rows:
        seg = _seguro_from_row(r)
        if com_cliente:
            seg["cadastro"] = buscar_cadastro(seg["cadastro_id"])
        resultado.append(seg)
    conn.close()
    return resultado


def seguros_a_vencer(dias_antecedencia=DIAS_PARA_RENOVACAO):
    conn = database.get_db()
    hoje = date.today()
    limite = hoje + timedelta(days=dias_antecedencia)
    sql = """
        SELECT * FROM seguros
        WHERE status = 'Ativo' AND vigencia_fim BETWEEN ? AND ?
        ORDER BY vigencia_fim ASC;
    """
    rows = conn.execute(sql, (hoje.isoformat(), limite.isoformat())).fetchall()
    resultado = []
    for r in rows:
        seg = _seguro_from_row(r)
        seg["cadastro"] = buscar_cadastro(seg["cadastro_id"])
        resultado.append(seg)
    conn.close()
    return resultado


def criar_seguro(id_cadastro, dados):
    conn = database.get_db()
    cursor = conn.execute("""
        INSERT INTO seguros (
            cadastro_id, origem_id, ramo, item, vigencia_inicio, vigencia_fim,
            seguradora, premio, comissao, forma_pagamento, numero_apolice, status, anexos_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
    """, (
        id_cadastro,
        dados.get("origem_id"),
        dados.get("ramo", "").strip(),
        dados.get("item", "").strip(),
        _parse_date(dados.get("vigencia_inicio")).isoformat() if dados.get("vigencia_inicio") else date.today().isoformat(),
        _parse_date(dados.get("vigencia_fim")).isoformat() if dados.get("vigencia_fim") else (date.today() + timedelta(days=365)).isoformat(),
        dados.get("seguradora", "").strip(),
        _float(dados.get("premio")),
        _float(dados.get("comissao")),
        dados.get("forma_pagamento", "").strip(),
        dados.get("numero_apolice", "").strip(),
        dados.get("status", "Ativo"),
        json.dumps([])
    ))
    novo_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return buscar_seguro(novo_id)


def atualizar_seguro(id_seguro, dados):
    conn = database.get_db()
    seguro = buscar_seguro(id_seguro)
    if not seguro:
        conn.close()
        return None

    inicio = _parse_date(dados.get("vigencia_inicio")) or seguro["vigencia_inicio"]
    fim = _parse_date(dados.get("vigencia_fim")) or seguro["vigencia_fim"]

    conn.execute("""
        UPDATE seguros SET
            ramo = ?, item = ?, vigencia_inicio = ?, vigencia_fim = ?,
            seguradora = ?, premio = ?, comissao = ?, forma_pagamento = ?,
            numero_apolice = ?
        WHERE id = ?;
    """, (
        dados.get("ramo", seguro["ramo"]).strip(),
        dados.get("item", seguro["item"]).strip(),
        inicio.isoformat(),
        fim.isoformat(),
        dados.get("seguradora", seguro["seguradora"]).strip(),
        _float(dados.get("premio") if "premio" in dados else seguro["premio"]),
        _float(dados.get("comissao") if "comissao" in dados else seguro["comissao"]),
        dados.get("forma_pagamento", seguro["forma_pagamento"]).strip(),
        dados.get("numero_apolice", seguro["numero_apolice"]).strip(),
        id_seguro
    ))
    conn.commit()
    conn.close()
    return buscar_seguro(id_seguro)


def deletar_seguro(id_seguro):
    conn = database.get_db()
    conn.execute("DELETE FROM seguros WHERE id = ?;", (id_seguro,))
    conn.commit()
    conn.close()
    return True


def anexar_no_seguro(id_seguro, nome_arquivo, importante=False):
    conn = database.get_db()
    seguro = buscar_seguro(id_seguro)
    if not seguro:
        conn.close()
        return False
    anexos = seguro["anexos"]
    anexos.append({"nome": nome_arquivo, "importante": importante})
    conn.execute("UPDATE seguros SET anexos_json = ? WHERE id = ?;", (json.dumps(anexos), id_seguro))
    conn.commit()
    conn.close()
    return True


# -----------------------------------------------------------------------------
# SINISTROS
# -----------------------------------------------------------------------------
def _sinistro_from_row(row):
    if not row:
        return None
    d = dict(row)
    d["data_ocorrencia"] = _parse_date(d.get("data_ocorrencia"))
    d["anexos"] = _parse_json(d.get("anexos_json"))
    return d


def listar_sinistros():
    conn = database.get_db()
    rows = conn.execute("SELECT * FROM sinistros ORDER BY id ASC;").fetchall()
    conn.close()
    return [_sinistro_from_row(r) for r in rows]


def buscar_sinistro(id_sinistro):
    conn = database.get_db()
    row = conn.execute("SELECT * FROM sinistros WHERE id = ?;", (id_sinistro,)).fetchone()
    conn.close()
    return _sinistro_from_row(row)


def sinistros_do_seguro(id_seguro):
    conn = database.get_db()
    rows = conn.execute("SELECT * FROM sinistros WHERE seguro_id = ? ORDER BY id DESC;", (id_seguro,)).fetchall()
    conn.close()
    return [_sinistro_from_row(r) for r in rows]


def criar_sinistro(id_seguro, dados):
    conn = database.get_db()
    data_oc = _parse_date(dados.get("data_ocorrencia")) or date.today()
    cursor = conn.execute("""
        INSERT INTO sinistros (seguro_id, numero, data_ocorrencia, terceiros, status, anexos_json)
        VALUES (?, ?, ?, ?, ?, ?);
    """, (
        id_seguro,
        dados.get("numero", "").strip(),
        data_oc.isoformat(),
        dados.get("terceiros", "").strip(),
        dados.get("status", "Sinistro aberto"),
        json.dumps([])
    ))
    novo_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return buscar_sinistro(novo_id)


def deletar_sinistro(id_sinistro):
    conn = database.get_db()
    conn.execute("DELETE FROM sinistros WHERE id = ?;", (id_sinistro,))
    conn.commit()
    conn.close()
    return True


def anexar_no_sinistro(id_sinistro, nome_arquivo, importante=False):
    conn = database.get_db()
    sinistro = buscar_sinistro(id_sinistro)
    if not sinistro:
        conn.close()
        return False
    anexos = sinistro["anexos"]
    anexos.append({"nome": nome_arquivo, "importante": importante})
    conn.execute("UPDATE sinistros SET anexos_json = ? WHERE id = ?;", (json.dumps(anexos), id_sinistro))
    conn.commit()
    conn.close()
    return True


# -----------------------------------------------------------------------------
# CARDS KANBAN
# -----------------------------------------------------------------------------
def _card_from_row(row):
    if not row:
        return None
    d = dict(row)
    d["data_criacao"] = _parse_date(d.get("data_criacao"))
    d["comentarios"] = _parse_json(d.get("comentarios_json"))
    d["anexos"] = _parse_json(d.get("anexos_json"))
    return d


def listar_cards():
    conn = database.get_db()
    rows = conn.execute("SELECT * FROM cards ORDER BY id ASC;").fetchall()
    conn.close()
    return [_card_from_row(r) for r in rows]


def buscar_card(id_card):
    conn = database.get_db()
    row = conn.execute("SELECT * FROM cards WHERE id = ?;", (id_card,)).fetchone()
    conn.close()
    return _card_from_row(row)


def cards_do_quadro(id_quadro):
    conn = database.get_db()
    rows = conn.execute("SELECT * FROM cards WHERE quadro_id = ? ORDER BY id ASC;", (id_quadro,)).fetchall()
    conn.close()
    return [_card_from_row(r) for r in rows]


def referencia_do_card(card):
    if not card:
        return None, None
    if card["referencia_tipo"] == "seguro":
        seguro = buscar_seguro(card["referencia_id"])
        if seguro:
            return seguro, buscar_cadastro(seguro["cadastro_id"])
        return None, None
    else:
        sinistro = buscar_sinistro(card["referencia_id"])
        if not sinistro:
            return None, None
        seguro = buscar_seguro(sinistro["seguro_id"])
        return sinistro, buscar_cadastro(seguro["cadastro_id"]) if seguro else None


def card_da_referencia(referencia_tipo, referencia_id):
    conn = database.get_db()
    row = conn.execute("""
        SELECT * FROM cards
        WHERE referencia_tipo = ? AND referencia_id = ?
        LIMIT 1;
    """, (referencia_tipo, referencia_id)).fetchone()
    conn.close()
    return _card_from_row(row)


def criar_card(tipo, id_cadastro, seguro_base_id=None, criado_por="Sistema", dados=None):
    """
    Cria o card e o respectivo seguro/sinistro em negociação + documento de negociação.
    """
    dados = dados or {}
    conn = database.get_db()
    cursor = conn.cursor()

    cliente = buscar_cadastro(id_cadastro)
    if not cliente:
        conn.close()
        return None

    tipo_ref = TIPOS_CARD.get(tipo, "seguro")
    quadro = quadro_do_comercial(cliente["comercial"], "Sinistro" if tipo == "Sinistro" else "Seguros")

    if tipo_ref == "seguro":
        seguro_base = buscar_seguro(seguro_base_id) if seguro_base_id else None
        vig_ini = (date.today() + timedelta(days=1)).isoformat()
        vig_fim = (date.today() + timedelta(days=366)).isoformat()
        if seguro_base and seguro_base.get("vigencia_fim"):
            vig_ini = seguro_base["vigencia_fim"].isoformat()
            vig_fim = (seguro_base["vigencia_fim"] + timedelta(days=365)).isoformat()

        cursor.execute("""
            INSERT INTO seguros (
                cadastro_id, origem_id, ramo, item, vigencia_inicio, vigencia_fim,
                seguradora, premio, comissao, forma_pagamento, numero_apolice, status, anexos_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """, (
            id_cadastro,
            seguro_base_id,
            seguro_base["ramo"] if seguro_base else dados.get("ramo", ""),
            seguro_base["item"] if seguro_base else dados.get("item", ""),
            vig_ini,
            vig_fim,
            seguro_base["seguradora"] if seguro_base else dados.get("seguradora", ""),
            seguro_base["premio"] if seguro_base else _float(dados.get("premio", 0)),
            seguro_base["comissao"] if seguro_base else _float(dados.get("comissao", 0)),
            seguro_base["forma_pagamento"] if seguro_base else dados.get("forma_pagamento", ""),
            seguro_base["numero_apolice"] if seguro_base else dados.get("numero_apolice", ""),
            f"{tipo} em negociação",
            json.dumps([])
        ))
        id_referencia = cursor.lastrowid
        primeira_coluna = COLUNAS_SEGUROS[0]
        desc_doc = f"{tipo} — {seguro_base['ramo'] if seguro_base else 'Novo seguro'}"
    else:
        cursor.execute("""
            INSERT INTO sinistros (seguro_id, numero, data_ocorrencia, terceiros, status, anexos_json)
            VALUES (?, ?, ?, ?, ?, ?);
        """, (
            seguro_base_id,
            dados.get("numero", "").strip(),
            date.today().isoformat(),
            dados.get("terceiros", "").strip(),
            "Sinistro em negociação",
            json.dumps([])
        ))
        id_referencia = cursor.lastrowid
        primeira_coluna = COLUNAS_SINISTRO[0]
        desc_doc = f"Sinistro nº {dados.get('numero', '')} — em negociação"

    # Cria o card
    cursor.execute("""
        INSERT INTO cards (
            tipo, referencia_tipo, referencia_id, quadro_id, coluna,
            data_criacao, criado_por, motivo_perda, comentarios_json, anexos_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
    """, (
        tipo,
        tipo_ref,
        id_referencia,
        quadro["id"],
        primeira_coluna,
        date.today().isoformat(),
        criado_por,
        "",
        json.dumps([]),
        json.dumps([])
    ))
    id_card = cursor.lastrowid

    # Cria o documento de negociação no cadastro
    cursor.execute("""
        INSERT INTO documentos_negociacao (
            cadastro_id, card_id, descricao, status, anexos_json, data_atualizacao
        ) VALUES (?, ?, ?, ?, ?, ?);
    """, (
        id_cadastro,
        id_card,
        desc_doc,
        primeira_coluna,
        json.dumps([]),
        date.today().isoformat()
    ))

    conn.commit()
    conn.close()
    return buscar_card(id_card)


def mover_card(id_card, coluna_destino):
    conn = database.get_db()
    card = buscar_card(id_card)
    if not card:
        conn.close()
        return False

    quadro = buscar_quadro(card["quadro_id"])
    if not quadro or coluna_destino not in quadro["colunas"]:
        conn.close()
        return False

    conn.execute("UPDATE cards SET coluna = ? WHERE id = ?;", (coluna_destino, id_card))
    conn.execute("UPDATE documentos_negociacao SET status = ?, data_atualizacao = ? WHERE card_id = ?;", (coluna_destino, date.today().isoformat(), id_card))
    conn.commit()
    conn.close()
    return True


def comentar_card(id_card, autor, texto):
    if not (texto or "").strip():
        return False
    conn = database.get_db()
    card = buscar_card(id_card)
    if not card:
        conn.close()
        return False

    comentarios = card["comentarios"]
    comentarios.append({
        "autor": autor,
        "texto": texto.strip(),
        "data": date.today().isoformat(),
    })
    conn.execute("UPDATE cards SET comentarios_json = ? WHERE id = ?;", (json.dumps(comentarios), id_card))
    conn.commit()
    conn.close()
    return True


def anexar_no_card(id_card, nome_arquivo, importante=False):
    conn = database.get_db()
    card = buscar_card(id_card)
    if not card:
        conn.close()
        return False

    anexos = card["anexos"]
    anexos.append({"nome": nome_arquivo, "importante": importante})
    conn.execute("UPDATE cards SET anexos_json = ? WHERE id = ?;", (json.dumps(anexos), id_card))
    conn.commit()
    conn.close()
    return True


def alternar_importancia_anexo(id_card, indice):
    conn = database.get_db()
    card = buscar_card(id_card)
    if not card or indice >= len(card["anexos"]):
        conn.close()
        return False

    anexos = card["anexos"]
    anexos[indice]["importante"] = not anexos[indice]["importante"]
    conn.execute("UPDATE cards SET anexos_json = ? WHERE id = ?;", (json.dumps(anexos), id_card))
    conn.commit()
    conn.close()
    return True


def deletar_card(id_card):
    conn = database.get_db()
    conn.execute("DELETE FROM documentos_negociacao WHERE card_id = ?;", (id_card,))
    conn.execute("DELETE FROM cards WHERE id = ?;", (id_card,))
    conn.commit()
    conn.close()
    return True


def finalizar_card(id_card, resultado, motivo_perda=""):
    """Finaliza o ciclo do card e grava no histórico de métricas."""
    conn = database.get_db()
    card = buscar_card(id_card)
    if not card:
        conn.close()
        return False

    coluna_final = "Finalizado" if resultado == "Renovado" else "Perdido"
    conn.execute("""
        UPDATE cards SET coluna = ?, motivo_perda = ? WHERE id = ?;
    """, (coluna_final, motivo_perda if resultado == "Perdido" else "", id_card))

    # Anexos importantes vão para o documento de negociação
    anexos_imp = [a for a in card["anexos"] if a.get("importante")]
    conn.execute("""
        UPDATE documentos_negociacao
        SET status = ?, anexos_json = ?, data_atualizacao = ?
        WHERE card_id = ?;
    """, (coluna_final, json.dumps(anexos_imp), date.today().isoformat(), id_card))

    if card["referencia_tipo"] == "seguro":
        seguro_atual = buscar_seguro(card["referencia_id"])
        if seguro_atual:
            novo_status = "Ativo" if resultado == "Renovado" else "Não renovado"
            conn.execute("UPDATE seguros SET status = ? WHERE id = ?;", (novo_status, seguro_atual["id"]))

            # Apólice anterior vira 'Renovado'
            if seguro_atual.get("origem_id") and resultado == "Renovado":
                conn.execute("UPDATE seguros SET status = 'Renovado' WHERE id = ?;", (seguro_atual["origem_id"],))

            # Registra no histórico de eventos (alimenta métricas)
            cliente = buscar_cadastro(seguro_atual["cadastro_id"])
            premio_ev = seguro_atual["premio"] if resultado == "Renovado" else 0.0
            comissao_ev = (seguro_atual["premio"] * seguro_atual["comissao"] / 100.0) if resultado == "Renovado" else 0.0

            conn.execute("""
                INSERT INTO historico_eventos (
                    resultado, data, comercial, ramo, seguradora, premio, comissao, motivo_perda
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
            """, (
                resultado,
                date.today().isoformat(),
                cliente["comercial"] if cliente else "Comercial",
                seguro_atual["ramo"],
                seguro_atual["seguradora"],
                premio_ev,
                comissao_ev,
                motivo_perda if resultado == "Perdido" else ""
            ))
    else:
        sinistro = buscar_sinistro(card["referencia_id"])
        if sinistro:
            conn.execute("UPDATE sinistros SET status = 'Finalizado' WHERE id = ?;", (sinistro["id"],))

    conn.commit()
    conn.close()
    return True


def verificar_renovacoes():
    """Rotina de automação periódica (RF09) que cria cards a 30 dias do vencimento."""
    conn = database.get_db()
    criados = []
    seguros_ativos = conn.execute("SELECT * FROM seguros WHERE status = 'Ativo';").fetchall()
    conn.close()

    for s_row in seguros_ativos:
        seguro = _seguro_from_row(s_row)
        dias = dias_restantes(seguro["vigencia_fim"])
        if not (0 <= dias <= DIAS_PARA_RENOVACAO):
            continue

        # Verifica se já existe seguro em negociação derivado desta apólice
        conn = database.get_db()
        existe = conn.execute("""
            SELECT id FROM seguros
            WHERE origem_id = ? AND status LIKE '%em negociação%'
            LIMIT 1;
        """, (seguro["id"],)).fetchone()
        conn.close()

        if existe:
            continue

        card = criar_card("Renovação", seguro["cadastro_id"], seguro_base_id=seguro["id"], criado_por="Sistema")
        if card:
            criados.append(card)

    return criados


# -----------------------------------------------------------------------------
# TAREFAS
# -----------------------------------------------------------------------------
def _tarefa_from_row(row):
    if not row:
        return None
    d = dict(row)
    d["prazo"] = _parse_date(d.get("prazo"))
    d["data_pedido"] = _parse_date(d.get("data_pedido"))
    d["data_entrega"] = _parse_date(d.get("data_entrega"))
    return d


def listar_tarefas():
    conn = database.get_db()
    rows = conn.execute("SELECT * FROM tarefas ORDER BY id ASC;").fetchall()
    conn.close()
    return [_tarefa_from_row(r) for r in rows]


def tarefas_do_card(id_card):
    conn = database.get_db()
    rows = conn.execute("SELECT * FROM tarefas WHERE card_id = ? ORDER BY id DESC;", (id_card,)).fetchall()
    conn.close()
    return [_tarefa_from_row(r) for r in rows]


def buscar_tarefa(id_tarefa):
    conn = database.get_db()
    row = conn.execute("SELECT * FROM tarefas WHERE id = ?;", (id_tarefa,)).fetchone()
    conn.close()
    return _tarefa_from_row(row)


def tarefas_pendentes(nome_usuario=None, perfil=None):
    conn = database.get_db()
    if nome_usuario and perfil not in ("Assistente", "Área Sinistros", "Administrador"):
        sql = "SELECT * FROM tarefas WHERE status = 'Pendente' AND (assistente = ? OR comercial = ?) ORDER BY prazo ASC;"
        rows = conn.execute(sql, (nome_usuario, nome_usuario)).fetchall()
    else:
        sql = "SELECT * FROM tarefas WHERE status = 'Pendente' ORDER BY prazo ASC;"
        rows = conn.execute(sql).fetchall()
    conn.close()
    return [_tarefa_from_row(r) for r in rows]


def criar_tarefa(id_card, dados, nome_comercial):
    conn = database.get_db()
    prazo = _parse_date(dados.get("prazo")) or (date.today() + timedelta(days=2))
    cursor = conn.execute("""
        INSERT INTO tarefas (
            card_id, tipo, urgencia, prazo, descricao, comercial,
            assistente, status, data_pedido, data_entrega
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
    """, (
        id_card,
        dados.get("tipo", "").strip(),
        dados.get("urgencia", "Média"),
        prazo.isoformat(),
        dados.get("descricao", "").strip(),
        nome_comercial,
        dados.get("assistente", "").strip(),
        "Pendente",
        date.today().isoformat(),
        None
    ))
    novo_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return buscar_tarefa(novo_id)


def entregar_tarefa(id_tarefa):
    conn = database.get_db()
    conn.execute("""
        UPDATE tarefas SET status = 'Entregue', data_entrega = ? WHERE id = ?;
    """, (date.today().isoformat(), id_tarefa))
    conn.commit()
    conn.close()
    return True


# -----------------------------------------------------------------------------
# DOCUMENTOS DE NEGOCIAÇÃO
# -----------------------------------------------------------------------------
def _documento_from_row(row):
    if not row:
        return None
    d = dict(row)
    d["anexos"] = _parse_json(d.get("anexos_json"))
    d["data_atualizacao"] = _parse_date(d.get("data_atualizacao"))
    return d


def listar_documentos():
    conn = database.get_db()
    rows = conn.execute("SELECT * FROM documentos_negociacao ORDER BY id ASC;").fetchall()
    conn.close()
    return [_documento_from_row(r) for r in rows]


def documentos_do_cadastro(id_cadastro):
    conn = database.get_db()
    rows = conn.execute("SELECT * FROM documentos_negociacao WHERE cadastro_id = ? ORDER BY id DESC;", (id_cadastro,)).fetchall()
    conn.close()
    return [_documento_from_row(r) for r in rows]


def documento_do_card(id_card):
    conn = database.get_db()
    row = conn.execute("SELECT * FROM documentos_negociacao WHERE card_id = ? LIMIT 1;", (id_card,)).fetchone()
    conn.close()
    return _documento_from_row(row)


# -----------------------------------------------------------------------------
# MÉTRICAS E RELATÓRIOS
# -----------------------------------------------------------------------------
def _historico_from_row(row):
    if not row:
        return None
    d = dict(row)
    d["data"] = _parse_date(d.get("data"))
    return d


def listar_historico():
    conn = database.get_db()
    rows = conn.execute("SELECT * FROM historico_eventos ORDER BY data DESC;").fetchall()
    conn.close()
    return [_historico_from_row(r) for r in rows]


def calcular_metricas(inicio=None, fim=None):
    eventos = listar_historico()
    if inicio:
        eventos = [e for e in eventos if e["data"] and e["data"] >= inicio]
    if fim:
        eventos = [e for e in eventos if e["data"] and e["data"] <= fim]

    renovados = sum(1 for e in eventos if e["resultado"] == "Renovado")
    perdidos = sum(1 for e in eventos if e["resultado"] == "Perdido")
    cancelados = sum(1 for e in eventos if e["resultado"] == "Cancelado")
    total_renovaveis = renovados + perdidos

    taxa_renovacao = round((renovados / total_renovaveis * 100), 1) if total_renovaveis > 0 else 0.0
    total_geral = len(eventos)
    taxa_conversao = round((renovados / total_geral * 100), 1) if total_geral > 0 else 0.0

    producao = sum(e["premio"] for e in eventos if e["resultado"] == "Renovado")
    ganhos = sum(e["comissao"] for e in eventos if e["resultado"] == "Renovado")

    return {
        "taxa_renovacao": taxa_renovacao,
        "taxa_conversao": taxa_conversao,
        "seguros_renovados": renovados,
        "seguros_cancelados": cancelados,
        "perdidos": perdidos,
        "producao": producao,
        "ganhos": ganhos,
        "eventos": eventos,
    }


def renovados_x_perdidos_por_mes(inicio=None, fim=None):
    eventos = listar_historico()
    if inicio:
        eventos = [e for e in eventos if e["data"] and e["data"] >= inicio]
    if fim:
        eventos = [e for e in eventos if e["data"] and e["data"] <= fim]

    meses_pt = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"]
    contagem = {}
    for ev in eventos:
        if not ev["data"]:
            continue
        chave = (ev["data"].year, ev["data"].month)
        if chave not in contagem:
            contagem[chave] = {"renovados": 0, "perdidos": 0}
        if ev["resultado"] == "Renovado":
            contagem[chave]["renovados"] += 1
        elif ev["resultado"] == "Perdido":
            contagem[chave]["perdidos"] += 1

    resultado = []
    for (ano, mes) in sorted(contagem.keys()):
        rotulo = f"{meses_pt[mes - 1]}/{str(ano)[-2:]}"
        resultado.append({
            "mes": rotulo,
            "renovados": contagem[(ano, mes)]["renovados"],
            "perdidos": contagem[(ano, mes)]["perdidos"],
        })
    return resultado


def carteira_por_ramo():
    conn = database.get_db()
    rows = conn.execute("""
        SELECT ramo, COUNT(*) as qtd
        FROM seguros
        WHERE status = 'Ativo'
        GROUP BY ramo
        ORDER BY qtd DESC;
    """).fetchall()
    conn.close()
    return {r["ramo"]: r["qtd"] for r in rows}


def gerar_relatorio(inicio=None, fim=None, filtros=None):
    filtros = filtros or {}
    conn = database.get_db()

    sql = """
        SELECT s.*, c.nome as cad_nome, c.tipo as cad_tipo, c.comercial as cad_comercial,
               c.origem as cad_origem, c.status as cad_status
        FROM seguros s
        JOIN cadastros c ON s.cadastro_id = c.id
        WHERE 1=1
    """
    params = []

    if inicio:
        sql += " AND s.vigencia_fim >= ?"
        params.append(inicio.isoformat() if isinstance(inicio, (date, datetime)) else str(inicio))
    if fim:
        sql += " AND s.vigencia_fim <= ?"
        params.append(fim.isoformat() if isinstance(fim, (date, datetime)) else str(fim))

    if filtros.get("tipo"):
        sql += " AND c.tipo = ?"
        params.append(filtros["tipo"])
    if filtros.get("status"):
        sql += " AND c.status = ?"
        params.append(filtros["status"])
    if filtros.get("origem"):
        sql += " AND c.origem = ?"
        params.append(filtros["origem"])
    if filtros.get("comercial"):
        sql += " AND c.comercial = ?"
        params.append(filtros["comercial"])
    if filtros.get("ramo"):
        sql += " AND s.ramo = ?"
        params.append(filtros["ramo"])
    if filtros.get("seguradora"):
        sql += " AND s.seguradora = ?"
        params.append(filtros["seguradora"])

    sql += " ORDER BY s.vigencia_fim ASC;"
    rows = conn.execute(sql, params).fetchall()
    conn.close()

    linhas = []
    for r in rows:
        seg = _seguro_from_row(r)
        cad = {
            "id": r["cadastro_id"],
            "nome": r["cad_nome"],
            "tipo": r["cad_tipo"],
            "comercial": r["cad_comercial"],
            "origem": r["cad_origem"],
            "status": r["cad_status"],
        }
        linhas.append({"cadastro": cad, "seguro": seg})
    return linhas


def valores_distintos(campo, origem="cadastro"):
    conn = database.get_db()
    tabela = "cadastros" if origem == "cadastro" else "seguros"
    rows = conn.execute(f"SELECT DISTINCT {campo} FROM {tabela} WHERE {campo} IS NOT NULL AND {campo} != '' ORDER BY {campo} ASC;").fetchall()
    conn.close()
    return [r[0] for r in rows]


# -----------------------------------------------------------------------------
# COMPATIBILIDADE DINÂMICA VIA __getattr__
# -----------------------------------------------------------------------------
def __getattr__(name):
    """Permite acesso a data.CARDS, data.USUARIOS, etc. consultando o banco SQLite dinamicamente."""
    if name == "COMERCIAIS":
        return listar_comerciais()
    if name == "CARDS":
        return listar_cards()
    if name == "USUARIOS":
        return listar_usuarios()
    if name == "CADASTROS":
        return listar_cadastros()
    if name == "SEGUROS":
        return listar_seguros()
    if name == "DOCUMENTOS":
        return listar_documentos()
    if name == "HISTORICO":
        return listar_historico()
    if name == "TAREFAS":
        return listar_tarefas()
    raise AttributeError(f"Módulo '{__name__}' não possui o atributo '{name}'.")
