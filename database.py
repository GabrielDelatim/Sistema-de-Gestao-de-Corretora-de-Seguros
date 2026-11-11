# =============================================================================
# database.py — CAMADA DE BANCO DE DADOS (SQLite3)
# -----------------------------------------------------------------------------
# Implementação relacional do MER (Modelo Entidade-Relacionamento) para
# Sistema de Gestão Integrada de Corretora de Seguros:
#
#   cadastros (1)  ── possui ──> (0..N) seguros
#   seguros   (1)  ── possui ──> (0..N) sinistros
#   seguros   (1)  ── possui ──> (1)    cards
#   cards     (N)  ── está   ──> (1)    quadros
#   tarefas   (N)  ── ligada ──> (1)    cards
#   cadastros (1)  ── possui ──> (N)    documentos_negociacao
#   logs_auditoria ── auditoria de segurança e acessos
# =============================================================================

import json
import os
import sqlite3
from datetime import datetime
from werkzeug.security import generate_password_hash

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "corretora.db")


def get_db():
    """Retorna uma conexão com o SQLite configurada com suporte a Row e Foreign Keys."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db(forcar_recriar=False):
    """
    Inicializa o banco de dados conforme o MER.
    Se 'forcar_recriar' for True ou se o banco estiver vazio, semeia os dados iniciais de produção.
    """
    conn = get_db()
    cursor = conn.cursor()

    if forcar_recriar:
        cursor.executescript("""
            DROP TABLE IF EXISTS configuracoes;
            DROP TABLE IF EXISTS logs_auditoria;
            DROP TABLE IF EXISTS historico_eventos;
            DROP TABLE IF EXISTS documentos_negociacao;
            DROP TABLE IF EXISTS tarefas;
            DROP TABLE IF EXISTS cards;
            DROP TABLE IF EXISTS quadros;
            DROP TABLE IF EXISTS sinistros;
            DROP TABLE IF EXISTS seguros;
            DROP TABLE IF EXISTS cadastros;
            DROP TABLE IF EXISTS usuarios;
        """)

    # 1. USUARIOS
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            login TEXT UNIQUE NOT NULL,
            senha_hash TEXT NOT NULL,
            perfil TEXT NOT NULL CHECK(perfil IN ('Administrador', 'Comercial', 'Assistente', 'Área Sinistros')),
            status TEXT NOT NULL DEFAULT 'Ativo',
            precisa_trocar_senha INTEGER NOT NULL DEFAULT 0
        );
    """)

    # 2. CADASTROS (Clientes / Segurados)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS cadastros (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            documento TEXT NOT NULL,
            tipo TEXT NOT NULL CHECK(tipo IN ('PF', 'PJ')),
            vinculos TEXT DEFAULT '',
            descricao TEXT DEFAULT '',
            comercial TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'Ativo',
            origem TEXT DEFAULT '',
            endereco TEXT DEFAULT '',
            telefone TEXT DEFAULT '',
            email TEXT DEFAULT '',
            data_cadastro DATE DEFAULT CURRENT_DATE
        );
    """)

    # 3. QUADROS KANBAN
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS quadros (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            comercial TEXT NOT NULL,
            tipo TEXT NOT NULL CHECK(tipo IN ('Seguros', 'Sinistro')),
            colunas_json TEXT NOT NULL
        );
    """)

    # 4. SEGUROS (Apólices)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS seguros (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cadastro_id INTEGER NOT NULL REFERENCES cadastros(id) ON DELETE CASCADE,
            origem_id INTEGER REFERENCES seguros(id) ON DELETE SET NULL,
            ramo TEXT NOT NULL,
            item TEXT DEFAULT '',
            vigencia_inicio DATE NOT NULL,
            vigencia_fim DATE NOT NULL,
            seguradora TEXT NOT NULL,
            premio REAL DEFAULT 0.0,
            comissao REAL DEFAULT 0.0,
            forma_pagamento TEXT DEFAULT '',
            numero_apolice TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'Ativo',
            anexos_json TEXT DEFAULT '[]'
        );
    """)

    # 5. SINISTROS
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sinistros (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            seguro_id INTEGER NOT NULL REFERENCES seguros(id) ON DELETE CASCADE,
            numero TEXT DEFAULT '',
            data_ocorrencia DATE NOT NULL,
            terceiros TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'Sinistro aberto',
            anexos_json TEXT DEFAULT '[]'
        );
    """)

    # 6. CARDS KANBAN
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS cards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tipo TEXT NOT NULL,
            referencia_tipo TEXT NOT NULL CHECK(referencia_tipo IN ('seguro', 'sinistro')),
            referencia_id INTEGER NOT NULL,
            quadro_id INTEGER NOT NULL REFERENCES quadros(id) ON DELETE CASCADE,
            coluna TEXT NOT NULL,
            data_criacao DATE NOT NULL,
            criado_por TEXT NOT NULL,
            motivo_perda TEXT DEFAULT '',
            comentarios_json TEXT DEFAULT '[]',
            anexos_json TEXT DEFAULT '[]'
        );
    """)

    # 7. TAREFAS
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tarefas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            card_id INTEGER NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
            tipo TEXT NOT NULL,
            urgencia TEXT NOT NULL DEFAULT 'Média',
            prazo DATE NOT NULL,
            descricao TEXT NOT NULL,
            comercial TEXT NOT NULL,
            assistente TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'Pendente',
            data_pedido DATE NOT NULL,
            data_entrega DATE
        );
    """)

    # 8. DOCUMENTOS DE NEGOCIAÇÃO
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS documentos_negociacao (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cadastro_id INTEGER NOT NULL REFERENCES cadastros(id) ON DELETE CASCADE,
            card_id INTEGER NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
            descricao TEXT NOT NULL,
            status TEXT NOT NULL,
            anexos_json TEXT DEFAULT '[]',
            data_atualizacao DATE NOT NULL
        );
    """)

    # 9. HISTÓRICO DE EVENTOS (Métricas e Relatórios)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS historico_eventos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            resultado TEXT NOT NULL CHECK(resultado IN ('Renovado', 'Perdido', 'Cancelado', 'Novo')),
            data DATE NOT NULL,
            comercial TEXT NOT NULL,
            ramo TEXT NOT NULL,
            seguradora TEXT NOT NULL,
            premio REAL DEFAULT 0.0,
            comissao REAL DEFAULT 0.0,
            motivo_perda TEXT DEFAULT ''
        );
    """)

    # 10. LOGS DE AUDITORIA E SEGURANÇA
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS logs_auditoria (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            data TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            usuario TEXT NOT NULL,
            perfil TEXT DEFAULT '',
            acao TEXT NOT NULL,
            detalhes TEXT DEFAULT '',
            ip TEXT DEFAULT ''
        );
    """)

    # 11. CONFIGURAÇÕES DA CORRETORA (Nome da Corretora, etc.)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS configuracoes (
            chave TEXT PRIMARY KEY,
            valor TEXT NOT NULL
        );
    """)

    # Índices para alta performance e segurança
    cursor.executescript("""
        CREATE INDEX IF NOT EXISTS idx_cadastros_comercial ON cadastros(comercial);
        CREATE INDEX IF NOT EXISTS idx_cadastros_documento ON cadastros(documento);
        CREATE INDEX IF NOT EXISTS idx_seguros_cadastro ON seguros(cadastro_id);
        CREATE INDEX IF NOT EXISTS idx_seguros_vigencia ON seguros(vigencia_fim);
        CREATE INDEX IF NOT EXISTS idx_sinistros_seguro ON sinistros(seguro_id);
        CREATE INDEX IF NOT EXISTS idx_cards_quadro ON cards(quadro_id);
        CREATE INDEX IF NOT EXISTS idx_cards_coluna ON cards(coluna);
        CREATE INDEX IF NOT EXISTS idx_tarefas_card ON tarefas(card_id);
        CREATE INDEX IF NOT EXISTS idx_tarefas_status ON tarefas(status);
        CREATE INDEX IF NOT EXISTS idx_documentos_cadastro ON documentos_negociacao(cadastro_id);
        CREATE INDEX IF NOT EXISTS idx_historico_data ON historico_eventos(data);
        CREATE INDEX IF NOT EXISTS idx_logs_data ON logs_auditoria(data);
        CREATE INDEX IF NOT EXISTS idx_logs_usuario ON logs_auditoria(usuario);
    """)

    conn.commit()

    # Verifica se já existem dados; se não, inicializa com o estado limpo de produção
    cursor.execute("SELECT COUNT(*) FROM usuarios;")
    if cursor.fetchone()[0] == 0:
        _semear_dados_producao(conn)

    conn.close()


def _semear_dados_producao(conn):
    """
    Inicializa o sistema limpo para uso diário em qualquer corretora de seguros.
    Cria apenas o usuário Administrador padrão e a configuração inicial.
    Os quadros de Seguros e Sinistros são criados automaticamente ao cadastrar
    cada usuário comercial (sem quadros gerais).
    """
    cursor = conn.cursor()

    # 1. Usuário Administrador Inicial
    # (Altere a senha após o primeiro acesso em Administração -> Usuários)
    senha_hash = generate_password_hash("admin123")
    cursor.execute("""
        INSERT INTO usuarios (id, nome, login, senha_hash, perfil, status, precisa_trocar_senha)
        VALUES (1, 'Administrador', 'admin', ?, 'Administrador', 'Ativo', 1);
    """, (senha_hash,))

    # 2. Configurações Iniciais da Corretora
    cursor.execute("""
        INSERT OR IGNORE INTO configuracoes (chave, valor)
        VALUES ('nome_corretora', 'Minha Corretora de Seguros');
    """)

    # 3. Registro do Log Inicial de Auditoria
    cursor.execute("""
        INSERT INTO logs_auditoria (usuario, perfil, acao, detalhes, ip)
        VALUES ('SISTEMA', 'Administrador', 'SISTEMA_INICIALIZADO', 'Banco de dados da corretora inicializado para uso em produção.', '127.0.0.1');
    """)

    conn.commit()
