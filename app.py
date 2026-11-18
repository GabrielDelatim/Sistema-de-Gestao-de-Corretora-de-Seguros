# =============================================================================
# app.py — ROTAS DO SISTEMA
# -----------------------------------------------------------------------------
# Cada função decorada com @app.route(...) é uma URL do sistema. A organização
# segue o mapa de navegação (seção 15.1 do documento de visão):
#
#   Login → Dashboard → { Cliente, Quadros, Métricas, Relatórios, Usuários }
#   Quadros → { Tarefas Pendentes, Pipelines Kanban → Cartão → Tarefa }
#   Pesquisa de Clientes → Dados do Cliente → Seguros → Sinistro → Cartão
#
# Para rodar:
#   pip install -r requirements.txt
#   python app.py
# Acesse http://localhost:5000
# =============================================================================

import csv
import io
import logging
from logging.handlers import RotatingFileHandler
import os
import secrets
import time
import uuid
from datetime import date, datetime
from functools import wraps

from flask import (Flask, Response, flash, jsonify, redirect, render_template, request,
                   send_from_directory, session, url_for)
from werkzeug.security import check_password_hash
from werkzeug.utils import secure_filename

import data
import database

# -----------------------------------------------------------------------------
# LOGS DO SISTEMA (Arquivo rotativo app.log + Console)
# -----------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        RotatingFileHandler("app.log", maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("corretora")


# -----------------------------------------------------------------------------
# CHAVE SECRETA PERSISTENTE E CONFIGURAÇÕES DE SEGURANÇA
# -----------------------------------------------------------------------------
def _obter_chave_secreta():
    chave_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".secret_key")
    if os.path.exists(chave_path):
        try:
            with open(chave_path, "r", encoding="utf-8") as f:
                chave = f.read().strip()
                if chave:
                    return chave
        except Exception:
            pass
    nova_chave = secrets.token_hex(32)
    try:
        with open(chave_path, "w", encoding="utf-8") as f:
            f.write(nova_chave)
    except Exception:
        pass
    return nova_chave


app = Flask(__name__)
app.secret_key = _obter_chave_secreta()

# Cookies seguros e limites contra DoS
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["PERMANENT_SESSION_LIFETIME"] = 28800  # 8 horas em segundos
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # Máximo de 16 MB por upload
app.config["PASTA_ANEXOS"] = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads")
os.makedirs(app.config["PASTA_ANEXOS"], exist_ok=True)

# Extensões permitidas para upload
EXTENSOES_PERMITIDAS = {".pdf", ".png", ".jpg", ".jpeg", ".docx", ".xlsx", ".csv", ".txt"}

# Controle de tentativas de login (Prevenção de Ataques de Força Bruta)
TENTATIVAS_FALHAS = {}  # {chave: [timestamp, ...]}


# -----------------------------------------------------------------------------
# MENU LATERAL — os itens seguem exatamente o menu do wireframe 15.3:
# Cliente | Quadros | Métricas | Relatórios | Usuários
# -----------------------------------------------------------------------------
ITENS_MENU = [
    {"chave": "dashboard",  "rota": "dashboard",  "rotulo": "Dashboard",  "icone": "dashboard"},
    {"chave": "clientes",   "rota": "clientes",   "rotulo": "Clientes",   "icone": "clientes"},
    {"chave": "quadros",    "rota": "quadros",    "rotulo": "Quadros",    "icone": "kanban"},
    {"chave": "metricas",   "rota": "metricas",   "rotulo": "Métricas",   "icone": "metricas"},
    {"chave": "relatorios", "rota": "relatorios", "rotulo": "Relatórios", "icone": "relatorios"},
]
ITENS_MENU_ADMIN = [
    {"chave": "usuarios", "rota": "usuarios", "rotulo": "Usuários", "icone": "usuarios"},
    {"chave": "configuracoes", "rota": "configuracoes", "rotulo": "Configurações", "icone": "settings"},
]


# -----------------------------------------------------------------------------
# FILTROS DE TEMPLATE — formatação reaproveitável dentro do HTML
# -----------------------------------------------------------------------------
@app.template_filter("moeda")
def filtro_moeda(valor):
    texto = f"{(valor or 0):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {texto}"


@app.template_filter("data_br")
def filtro_data_br(valor_data):
    """Aceita tanto objetos date/datetime quanto strings ISO (YYYY-MM-DD)."""
    if not valor_data:
        return "—"
    if isinstance(valor_data, str):
        try:
            from datetime import date as _date
            valor_data = _date.fromisoformat(valor_data[:10])
        except (ValueError, TypeError):
            return valor_data  # devolve como texto se não conseguir parsear
    return valor_data.strftime("%d/%m/%Y")


# -----------------------------------------------------------------------------
# VARIÁVEIS DISPONÍVEIS EM TODOS OS TEMPLATES
# -----------------------------------------------------------------------------
@app.context_processor
def injetar_globais():
    # Não consulta o banco para requisições de arquivos estáticos
    if request.endpoint == "static":
        return {}
    usuario = usuario_atual()
    total_pendentes = len(data.tarefas_pendentes(usuario["nome"], usuario["perfil"])) if usuario else 0
    return {
        "itens_menu": ITENS_MENU,
        "itens_menu_admin": ITENS_MENU_ADMIN if usuario and usuario["perfil"] == "Administrador" else [],
        "usuario_logado": usuario,
        "total_pendentes": total_pendentes,
        "hoje": date.today(),
        "nome_corretora": data.nome_corretora(),
    }


def usuario_atual():
    if "usuario_id" not in session:
        return None
    return data.buscar_usuario(session["usuario_id"])


def _usuario_ativo():
    """Retorna o usuário da sessão somente se ele ainda estiver Ativo no banco."""
    u = usuario_atual()
    if u and u.get("status") == "Ativo":
        return u
    return None


# -----------------------------------------------------------------------------
# CONTROLE DE ACESSO
# -----------------------------------------------------------------------------
def login_obrigatorio(funcao_rota):
    """Porteiro: sem sessão ativa ou usuário inativo, manda para o login."""
    @wraps(funcao_rota)
    def rota_protegida(*args, **kwargs):
        if _usuario_ativo() is None:
            session.clear()  # garante que sessão de usuário inativado seja limpa
            return redirect(url_for("login"))
        return funcao_rota(*args, **kwargs)
    return rota_protegida


def perfil_obrigatorio(*perfis_permitidos):
    """Restringe uma rota a determinados perfis (RF26). Ex.: manter usuários
    é exclusivo do Administrador."""
    def decorador(funcao_rota):
        @wraps(funcao_rota)
        def rota_protegida(*args, **kwargs):
            usuario = _usuario_ativo()
            if usuario is None:
                session.clear()
                return redirect(url_for("login"))
            if usuario["perfil"] not in perfis_permitidos:
                flash("Seu perfil não tem permissão para acessar essa área.", "erro")
                return redirect(url_for("dashboard"))
            return funcao_rota(*args, **kwargs)
        return rota_protegida
    return decorador


# -----------------------------------------------------------------------------
# HELPERS DE AUTORIZAÇÃO — impedem IDOR (acesso por troca de ID na URL)
# -----------------------------------------------------------------------------
RESULTADOS_VALIDOS = {"Renovado", "Perdido", "Cancelado", "Endossado", "Encerrado"}


def _verificar_acesso_card(id_card):
    """Retorna o card se o usuário atual tiver acesso, ou None + flash de erro.
    Admin e Assistente enxergam cards para operação e suporte; Comercial enxerga os do seu quadro."""
    usuario = _usuario_ativo()
    card = data.buscar_card(id_card)
    if card is None:
        flash("Cartão não encontrado.", "erro")
        return None
    if usuario["perfil"] in ("Administrador", "Assistente"):
        return card
    ids_quadros = {q["id"] for q in data.quadros_do_usuario(usuario)}
    if card["quadro_id"] in ids_quadros:
        return card
    flash("Você não tem acesso a este cartão.", "erro")
    return None


def _verificar_acesso_seguro(id_seguro):
    """Retorna o seguro se o usuário tiver acesso via cliente, ou None."""
    usuario = _usuario_ativo()
    seguro = data.buscar_seguro(id_seguro)
    if seguro is None:
        flash("Seguro não encontrado.", "erro")
        return None
    if usuario["perfil"] in ("Administrador", "Assistente"):
        return seguro
    cliente = data.buscar_cadastro(seguro["cadastro_id"])
    if cliente and (not cliente.get("comercial") or cliente["comercial"] == usuario["nome"]):
        return seguro
    flash("Você não tem acesso a este seguro.", "erro")
    return None


def _verificar_acesso_cliente(id_cliente):
    """Retorna o cliente se o usuário tiver acesso, ou None."""
    usuario = _usuario_ativo()
    cliente = data.buscar_cadastro(id_cliente)
    if cliente is None:
        flash("Cliente não encontrado.", "erro")
        return None
    if usuario["perfil"] in ("Administrador", "Assistente"):
        return cliente
    if not cliente.get("comercial") or cliente["comercial"] == usuario["nome"]:
        return cliente
    flash("Você não tem acesso a este cliente.", "erro")
    return None


def _url_segura(url):
    """Valida que o redirect 'voltar' é uma URL relativa interna (evita open redirect)."""
    if url and url.startswith("/") and not url.startswith("//"):
        return url
    return None


# -----------------------------------------------------------------------------
# HANDLER DE ERROS
# -----------------------------------------------------------------------------
@app.errorhandler(500)
def erro_interno(e):
    logger.exception("Erro interno não tratado: %s", e)
    return render_template("erro_500.html"), 500


@app.errorhandler(404)
def nao_encontrado(e):
    return render_template("erro_404.html"), 404


def _data_do_form(campo):
    """Lê uma data enviada pelo formulário (ou None, se veio em branco)."""
    valor = request.args.get(campo) or request.form.get(campo)
    if not valor:
        return None
    try:
        return datetime.strptime(valor, "%Y-%m-%d").date()
    except ValueError:
        return None


def _ip_cliente():
    """Usa o IP real da conexão TCP. Não confia em X-Forwarded-For sem
    proxy configurado — cabeçalho forjável por qualquer cliente."""
    return request.remote_addr or "127.0.0.1"


def _verificar_bloqueio_login(chave):
    """Retorna True se houver 5 ou mais tentativas incorretas nos últimos 10 minutos."""
    agora = time.time()
    tentativas = [t for t in TENTATIVAS_FALHAS.get(chave, []) if agora - t < 600]
    TENTATIVAS_FALHAS[chave] = tentativas
    return len(tentativas) >= 5


def _registrar_falha_login(chave):
    """Registra uma tentativa de login falha."""
    agora = time.time()
    tentativas = TENTATIVAS_FALHAS.get(chave, [])
    tentativas.append(agora)
    TENTATIVAS_FALHAS[chave] = [t for t in tentativas if agora - t < 600]


def _limpar_falhas_login(chave):
    """Limpa as tentativas falhas após login bem-sucedido."""
    TENTATIVAS_FALHAS.pop(chave, None)


def _salvar_anexo(arquivo):
    """
    Grava o arquivo enviado com validação estrita de extensão e nome seguro único.
    Evita sobreposição de arquivos com mesmo nome e bloqueia arquivos maliciosos.
    """
    if not arquivo or not arquivo.filename:
        return None

    nome_original = secure_filename(arquivo.filename)
    if not nome_original:
        return None

    _, ext = os.path.splitext(nome_original)
    ext = ext.lower()
    if ext not in EXTENSOES_PERMITIDAS:
        raise ValueError(f"Extensão '{ext}' não permitida por segurança. Envie apenas documentos (PDF, imagens, Word, Excel).")

    # Prefixo aleatório único de 8 caracteres para garantir integridade
    prefixo = uuid.uuid4().hex[:8]
    nome_final = f"{prefixo}_{nome_original}"
    caminho = os.path.join(app.config["PASTA_ANEXOS"], nome_final)
    arquivo.save(caminho)
    return nome_final


# =============================================================================
# LOGIN / LOGOUT (RF01) COM AUDITORIA E PROTEÇÃO DE FORÇA BRUTA
# =============================================================================

@app.before_request
def verificar_troca_obrigatoria():
    """Força o usuário a trocar a senha inicial antes de navegar pelo sistema."""
    if session.get("precisa_trocar_senha"):
        rotas_permitidas = {"primeiro_acesso", "logout", "static"}
        if request.endpoint and request.endpoint not in rotas_permitidas:
            return redirect(url_for("primeiro_acesso"))


@app.route("/")
def raiz():
    return redirect(url_for("dashboard") if usuario_atual() else url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    exibir_aviso = data.admin_usa_senha_padrao()
    if request.method == "GET":
        return render_template("login.html", exibir_aviso_admin=exibir_aviso)

    ip = _ip_cliente()
    login_informado = request.form.get("username", "").strip()

    # Checa bloqueio por excesso de tentativas
    if _verificar_bloqueio_login(ip) or _verificar_bloqueio_login(login_informado):
        logger.warning(f"Tentativa de login bloqueada por rate-limit: usuário '{login_informado}' do IP {ip}")
        data.registrar_auditoria(login_informado, "", "LOGIN_BLOQUEADO", "Bloqueio temporário por tentativas excessivas", ip)
        flash("Muitas tentativas incorretas. Por segurança, aguarde alguns minutos antes de tentar novamente.", "erro")
        return render_template("login.html", exibir_aviso_admin=exibir_aviso), 429

    usuario = data.buscar_usuario_por_login(login_informado)
    senha_ok = usuario and check_password_hash(usuario["senha_hash"], request.form.get("password", ""))

    if not senha_ok:
        _registrar_falha_login(ip)
        _registrar_falha_login(login_informado)
        data.registrar_auditoria(login_informado, "", "LOGIN_FALHA", "Tentativa de login com credenciais incorretas", ip)
        logger.warning(f"Falha de autenticação para '{login_informado}' do IP {ip}")
        flash("Usuário ou senha incorretos.", "erro")
        return render_template("login.html", exibir_aviso_admin=exibir_aviso), 401

    if usuario.get("status") != "Ativo":
        data.registrar_auditoria(usuario["nome"], usuario["perfil"], "LOGIN_RECUSADO", "Usuário inativo tentou acessar", ip)
        flash("Este usuário está inativo. Contate o administrador.", "erro")
        return render_template("login.html", exibir_aviso_admin=exibir_aviso), 403

    _limpar_falhas_login(ip)
    _limpar_falhas_login(login_informado)
    session["usuario_id"] = usuario["id"]

    data.registrar_auditoria(usuario["nome"], usuario["perfil"], "LOGIN_SUCESSO", "Login realizado com sucesso", ip)
    logger.info(f"Usuário '{usuario['nome']}' ({usuario['perfil']}) autenticado com sucesso [IP: {ip}]")

    # Se for primeiro acesso do admin ou flag ativada, exige troca imediata de senha
    if usuario.get("precisa_trocar_senha") == 1:
        session["precisa_trocar_senha"] = True
        flash("Por segurança, defina uma nova senha para o Administrador antes de continuar.", "aviso")
        return redirect(url_for("primeiro_acesso"))

    return redirect(url_for("dashboard"))


@app.route("/primeiro-acesso", methods=["GET", "POST"])
def primeiro_acesso():
    """Tela de definição obrigatória de nova senha no primeiro acesso."""
    usuario = usuario_atual()
    if not usuario:
        return redirect(url_for("login"))

    if not session.get("precisa_trocar_senha") and usuario.get("precisa_trocar_senha") != 1:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        nova_senha = request.form.get("nova_senha", "").strip()
        confirmacao = request.form.get("confirmacao", "").strip()

        if len(nova_senha) < 6:
            flash("A nova senha deve ter no mínimo 6 caracteres.", "erro")
        elif nova_senha == "admin123":
            flash("A nova senha não pode ser igual à senha padrão ('admin123'). Escolha uma senha segura.", "erro")
        elif nova_senha != confirmacao:
            flash("A confirmação de senha não confere com a nova senha digitada.", "erro")
        else:
            data.alterar_senha_usuario(usuario["id"], nova_senha)
            session.pop("precisa_trocar_senha", None)

            ip = _ip_cliente()
            data.registrar_auditoria(usuario["nome"], usuario["perfil"], "SENHA_ALTERADA", "Senha inicial alterada com sucesso no primeiro acesso", ip)
            logger.info(f"Usuário '{usuario['nome']}' alterou a senha padrão com sucesso [IP: {ip}]")
            flash("Senha alterada com sucesso! Bem-vindo ao sistema da sua corretora.", "sucesso")
            return redirect(url_for("dashboard"))

    return render_template("primeiro_acesso.html", usuario=usuario)


@app.route("/logout")
def logout():
    usuario = usuario_atual()
    if usuario:
        ip = _ip_cliente()
        data.registrar_auditoria(usuario["nome"], usuario["perfil"], "LOGOUT", "Logout do sistema", ip)
        logger.info(f"Usuário '{usuario['nome']}' encerrou sessão [IP: {ip}]")
    session.clear()
    return redirect(url_for("login"))


# =============================================================================
# DASHBOARD (RF02)
# =============================================================================

@app.route("/dashboard")
@login_obrigatorio
def dashboard():
    usuario = usuario_atual()

    # A rotina de renovação roda aqui, a cada visita ao dashboard: é o
    # equivalente simples do "processo interno periódico" da seção 8.3.
    # Num ambiente real isso viraria uma tarefa agendada (cron/APScheduler).
    novos_cards = data.verificar_renovacoes()
    if novos_cards:
        flash(f"{len(novos_cards)} cartão(ões) de renovação criado(s) automaticamente.", "sucesso")

    vencendo = data.seguros_a_vencer(data.DIAS_PARA_RENOVACAO)
    pendentes = data.tarefas_pendentes(usuario["nome"], usuario["perfil"])

    cartoes = [
        {"rotulo": "Clientes", "valor": len(data.listar_cadastros())},
        # só apólices vigentes: registros "em negociação" não contam como carteira
        {"rotulo": "Seguros ativos", "valor": len(data.apolices_ativas())},
        {"rotulo": "Vencendo em breve", "valor": len(vencendo)},
        {"rotulo": "Tarefas pendentes", "valor": len(pendentes)},
    ]

    alertas = [
        f"{len(vencendo)} seguro(s) vencem nos próximos {data.DIAS_PARA_RENOVACAO} dias",
        f"{len(pendentes)} tarefa(s) pendente(s) na sua fila",
        f"{len([c for c in data.CARDS if c['coluna'] not in data.COLUNAS_FINAIS])} cartão(ões) em andamento nos quadros",
    ]

    return render_template(
        "dashboard.html",
        pagina_ativa="dashboard",
        cartoes=cartoes,
        alertas=alertas,
        meses=data.renovados_x_perdidos_por_mes(),
        carteira=data.carteira_por_ramo(),
        vencendo=sorted(vencendo, key=lambda s: s["vigencia_fim"])[:6],
        dias_restantes=data.dias_restantes,
    )


# =============================================================================
# PESQUISA DE CLIENTES E CADASTRO (RF03, RF04)
# =============================================================================

@app.route("/clientes")
@login_obrigatorio
def clientes():
    termo = request.args.get("q", "")
    modo = request.args.get("modo", "comeca_com")
    filtros = {
        "tipo": request.args.get("tipo", ""),
        "status": request.args.get("status", ""),
        "origem": request.args.get("origem", ""),
        "comercial": request.args.get("comercial", ""),
        "vinculos": request.args.get("vinculos", ""),
    }

    return render_template(
        "clientes.html",
        pagina_ativa="clientes",
        clientes=data.pesquisar_cadastros(termo, modo, filtros),
        termo=termo,
        modo=modo,
        filtros=filtros,
        opcoes_origem=data.valores_distintos("origem"),
        opcoes_comercial=data.valores_distintos("comercial"),
    )


@app.route("/clientes/novo", methods=["GET", "POST"])
@login_obrigatorio
def cliente_novo():
    if request.method == "GET":
        return render_template("cliente_form.html", pagina_ativa="clientes", cliente=None, comerciais=data.COMERCIAIS)

    erros = _validar_cadastro(request.form)
    if erros:
        return render_template("cliente_form.html", pagina_ativa="clientes", cliente=None, erros=erros, valores=request.form, comerciais=data.COMERCIAIS)

    novo = data.criar_cadastro(request.form)
    flash("Cliente cadastrado com sucesso.", "sucesso")
    return redirect(url_for("cliente_detalhe", id_cliente=novo["id"]))


@app.route("/clientes/<int:id_cliente>")
@login_obrigatorio
def cliente_detalhe(id_cliente):
    """Tela 'Dados do Cliente' (wireframe 15.9): dados cadastrais, documentos
    de negociação e navegação para os seguros do cliente."""
    cliente = _verificar_acesso_cliente(id_cliente)
    if cliente is None:
        return redirect(url_for("clientes"))

    return render_template(
        "cliente_detalhe.html",
        pagina_ativa="clientes",
        cliente=cliente,
        seguros=data.seguros_do_cadastro(id_cliente),
        documentos=data.documentos_do_cadastro(id_cliente),
        dias_restantes=data.dias_restantes,
    )


@app.route("/clientes/<int:id_cliente>/editar", methods=["GET", "POST"])
@login_obrigatorio
def cliente_editar(id_cliente):
    cliente = data.buscar_cadastro(id_cliente)
    if cliente is None:
        flash("Cliente não encontrado.", "erro")
        return redirect(url_for("clientes"))

    if request.method == "GET":
        return render_template("cliente_form.html", pagina_ativa="clientes", cliente=cliente, comerciais=data.COMERCIAIS)

    erros = _validar_cadastro(request.form)
    if erros:
        return render_template("cliente_form.html", pagina_ativa="clientes", cliente=cliente, erros=erros, valores=request.form, comerciais=data.COMERCIAIS)

    data.atualizar_cadastro(id_cliente, request.form)
    flash("Cliente atualizado com sucesso.", "sucesso")
    return redirect(url_for("cliente_detalhe", id_cliente=id_cliente))


@app.route("/clientes/<int:id_cliente>/deletar", methods=["POST"])
@login_obrigatorio
@perfil_obrigatorio("Administrador")
def cliente_deletar(id_cliente):
    data.deletar_cadastro(id_cliente)
    flash("Cliente removido.", "sucesso")
    return redirect(url_for("clientes"))


def _validar_cadastro(formulario):
    """Validação no servidor: vale mesmo se o JS do navegador for burlado."""
    erros = {}
    if not formulario.get("nome", "").strip():
        erros["nome"] = "Informe o nome / razão social."
    if not formulario.get("documento", "").strip():
        erros["documento"] = "Informe o CPF ou CNPJ."
    if not formulario.get("comercial", "").strip():
        erros["comercial"] = "Informe o comercial responsável."
    return erros


# =============================================================================
# SEGUROS (RF05, RF07) — sempre dentro de um cadastro de cliente
# =============================================================================

@app.route("/clientes/<int:id_cliente>/seguros/novo", methods=["GET", "POST"])
@login_obrigatorio
def seguro_novo(id_cliente):
    cliente = data.buscar_cadastro(id_cliente)
    if cliente is None:
        flash("Cliente não encontrado.", "erro")
        return redirect(url_for("clientes"))

    if request.method == "GET":
        return render_template("seguro_form.html", pagina_ativa="clientes", cliente=cliente, seguro=None)

    erros = _validar_seguro(request.form)
    if erros:
        return render_template("seguro_form.html", pagina_ativa="clientes", cliente=cliente, seguro=None, erros=erros, valores=request.form)

    novo = data.criar_seguro(id_cliente, request.form)
    flash("Seguro cadastrado. O sistema já está monitorando a renovação.", "sucesso")
    return redirect(url_for("seguro_detalhe", id_seguro=novo["id"]))


@app.route("/seguros/<int:id_seguro>")
@login_obrigatorio
def seguro_detalhe(id_seguro):
    """Tela de Seguros (wireframe 15.10): dados da apólice, sinistros
    vinculados, anexos e navegação para o card."""
    seguro = _verificar_acesso_seguro(id_seguro)
    if seguro is None:
        return redirect(url_for("clientes"))

    return render_template(
        "seguro_detalhe.html",
        pagina_ativa="clientes",
        seguro=seguro,
        cliente=data.buscar_cadastro(seguro["cadastro_id"]),
        sinistros=data.sinistros_do_seguro(id_seguro),
        card=data.card_da_referencia("seguro", id_seguro),
        dias=data.dias_restantes(seguro["vigencia_fim"]),
    )


@app.route("/seguros/<int:id_seguro>/editar", methods=["GET", "POST"])
@login_obrigatorio
def seguro_editar(id_seguro):
    seguro = data.buscar_seguro(id_seguro)
    if seguro is None:
        flash("Seguro não encontrado.", "erro")
        return redirect(url_for("clientes"))

    cliente = data.buscar_cadastro(seguro["cadastro_id"])

    if request.method == "GET":
        return render_template("seguro_form.html", pagina_ativa="clientes", cliente=cliente, seguro=seguro)

    erros = _validar_seguro(request.form)
    if erros:
        return render_template("seguro_form.html", pagina_ativa="clientes", cliente=cliente, seguro=seguro, erros=erros, valores=request.form)

    data.atualizar_seguro(id_seguro, request.form)
    flash("Seguro atualizado.", "sucesso")
    return redirect(url_for("seguro_detalhe", id_seguro=id_seguro))


@app.route("/seguros/<int:id_seguro>/deletar", methods=["POST"])
@login_obrigatorio
def seguro_deletar(id_seguro):
    seguro = _verificar_acesso_seguro(id_seguro)
    if seguro is None:
        return redirect(url_for("clientes"))
    id_cliente = seguro["cadastro_id"]
    data.deletar_seguro(id_seguro)
    flash("Seguro excluído.", "sucesso")
    return redirect(url_for("cliente_detalhe", id_cliente=id_cliente))


@app.route("/seguros/<int:id_seguro>/anexos", methods=["POST"])
@login_obrigatorio
def seguro_anexar(id_seguro):
    nome = _salvar_anexo(request.files.get("arquivo"))
    if nome:
        data.anexar_no_seguro(id_seguro, nome, importante=bool(request.form.get("importante")))
        flash("Anexo adicionado.", "sucesso")
    return redirect(url_for("seguro_detalhe", id_seguro=id_seguro))


def _validar_seguro(formulario):
    erros = {}
    if not formulario.get("ramo", "").strip():
        erros["ramo"] = "Informe o ramo."
    if not formulario.get("seguradora", "").strip():
        erros["seguradora"] = "Informe a seguradora."
    if not formulario.get("numero_apolice", "").strip():
        erros["numero_apolice"] = "Informe o número da apólice."
    if not formulario.get("vigencia_inicio") or not formulario.get("vigencia_fim"):
        erros["vigencia"] = "Informe o início e o fim da vigência."
    elif formulario["vigencia_fim"] <= formulario["vigencia_inicio"]:
        erros["vigencia"] = "O fim da vigência deve ser posterior ao início."
    return erros


# =============================================================================
# SINISTROS (RF06, RF07) — sempre dentro de um seguro
# =============================================================================

@app.route("/seguros/<int:id_seguro>/sinistros/novo", methods=["GET", "POST"])
@login_obrigatorio
@perfil_obrigatorio("Administrador", "Área Sinistros")
def sinistro_novo(id_seguro):
    seguro = data.buscar_seguro(id_seguro)
    if seguro is None:
        flash("Seguro não encontrado.", "erro")
        return redirect(url_for("clientes"))

    if request.method == "GET":
        return render_template("sinistro_form.html", pagina_ativa="clientes", seguro=seguro, sinistro=None,
                               cliente=data.buscar_cadastro(seguro["cadastro_id"]))

    erros = _validar_sinistro(request.form)
    if erros:
        return render_template("sinistro_form.html", pagina_ativa="clientes", seguro=seguro, sinistro=None,
                               cliente=data.buscar_cadastro(seguro["cadastro_id"]), erros=erros, valores=request.form)

    # O registro de sinistro nasce junto do card (mesma regra dos seguros:
    # quem cria o registro é o card). criar_card devolve o card; o sinistro
    # criado é a referência dele.
    seguro = data.buscar_seguro(id_seguro)
    card = data.criar_card(
        tipo="Sinistro",
        id_cadastro=seguro["cadastro_id"],
        seguro_base_id=id_seguro,
        criado_por=usuario_atual()["nome"],
        dados=request.form,
    )
    flash("Sinistro registrado e card criado no quadro de Sinistros.", "sucesso")
    return redirect(url_for("sinistro_detalhe", id_sinistro=card["referencia_id"]))


@app.route("/sinistros/<int:id_sinistro>")
@login_obrigatorio
def sinistro_detalhe(id_sinistro):
    sinistro = data.buscar_sinistro(id_sinistro)
    if sinistro is None:
        flash("Sinistro não encontrado.", "erro")
        return redirect(url_for("clientes"))

    seguro = data.buscar_seguro(sinistro["seguro_id"])
    return render_template(
        "sinistro_detalhe.html",
        pagina_ativa="clientes",
        sinistro=sinistro,
        seguro=seguro,
        cliente=data.buscar_cadastro(seguro["cadastro_id"]) if seguro else None,
        card=data.card_da_referencia("sinistro", id_sinistro),
    )


@app.route("/sinistros/<int:id_sinistro>/deletar", methods=["POST"])
@login_obrigatorio
@perfil_obrigatorio("Administrador", "Área Sinistros")
def sinistro_deletar(id_sinistro):
    sinistro = data.buscar_sinistro(id_sinistro)
    id_seguro = sinistro["seguro_id"] if sinistro else None
    data.deletar_sinistro(id_sinistro)
    flash("Sinistro excluído.", "sucesso")
    return redirect(url_for("seguro_detalhe", id_seguro=id_seguro))


@app.route("/sinistros/<int:id_sinistro>/anexos", methods=["POST"])
@login_obrigatorio
def sinistro_anexar(id_sinistro):
    nome = _salvar_anexo(request.files.get("arquivo"))
    if nome:
        data.anexar_no_sinistro(id_sinistro, nome, importante=bool(request.form.get("importante")))
        flash("Anexo adicionado.", "sucesso")
    return redirect(url_for("sinistro_detalhe", id_sinistro=id_sinistro))


def _validar_sinistro(formulario):
    erros = {}
    if not formulario.get("numero", "").strip():
        erros["numero"] = "Informe o número do sinistro."
    if not formulario.get("data_ocorrencia"):
        erros["data_ocorrencia"] = "Informe a data de ocorrência."
    return erros


# =============================================================================
# QUADROS E PIPELINE KANBAN (RF11, RF12)
# =============================================================================

@app.route("/quadros")
@login_obrigatorio
def quadros():
    """Tela de Quadros (wireframe 15.4): lista os quadros que o usuário
    enxerga e dá acesso às tarefas pendentes."""
    usuario = usuario_atual()
    lista = data.quadros_do_usuario(usuario)

    return render_template(
        "quadros.html",
        pagina_ativa="quadros",
        quadros=[{**q, "total_cards": len(data.cards_do_quadro(q["id"]))} for q in lista],
        pendentes=data.tarefas_pendentes(usuario["nome"], usuario["perfil"]),
    )


@app.route("/quadros/<int:id_quadro>")
@login_obrigatorio
def pipeline(id_quadro):
    """Pipeline Kanban (wireframe 15.5) — as colunas vêm do próprio quadro."""
    usuario = _usuario_ativo()
    quadro = data.buscar_quadro(id_quadro)

    if quadro is None:
        flash("Quadro não encontrado.", "erro")
        return redirect(url_for("quadros"))

    # Regra de negócio: o usuário só enxerga os quadros aos quais pertence.
    ids_permitidos = {q["id"] for q in data.quadros_do_usuario(usuario)}
    if quadro["id"] not in ids_permitidos:
        flash("Você não está vinculado a esse quadro.", "erro")
        return redirect(url_for("quadros"))

    # monta {coluna: [cards]} já com a referência (seguro/sinistro) resolvida e etiquetas
    colunas = {}
    for coluna in quadro["colunas"]:
        colunas[coluna] = []
    for card in data.cards_do_quadro(id_quadro):
        referencia, cliente = data.referencia_do_card(card)
        tarefas = data.tarefas_do_card(card["id"])
        etiquetas = data.listar_etiquetas_por_card(card["id"])
        colunas.setdefault(card["coluna"], []).append({
            **card,
            "referencia": referencia,
            "cliente": cliente,
            "etiquetas": etiquetas,
            "total_tarefas": len(tarefas),
            "tarefas_pendentes": len([t for t in tarefas if t.get("status") == "Pendente"]),
            "total_comentarios": len(card.get("comentarios", [])),
        })

    # Assistentes e Administradores podem mover cards entre quadros de comerciais diferentes
    quadros_transferencia = []
    if usuario["perfil"] in ("Administrador", "Assistente"):
        quadros_transferencia = [q for q in data.listar_quadros() if q["id"] != quadro["id"] and q["tipo"] == quadro["tipo"]]

    return render_template(
        "pipeline.html",
        pagina_ativa="quadros",
        quadro=quadro,
        colunas=colunas,
        etiquetas_quadro=data.listar_etiquetas_por_quadro(id_quadro),
        quadros_transferencia=quadros_transferencia,
        clientes=data.clientes_do_comercial(quadro["comercial"]),
        apolices=data.apolices_ativas(comercial=quadro["comercial"], com_cliente=True),
        tipos_card=[t for t, ref in data.TIPOS_CARD.items() if ref == ("sinistro" if quadro["tipo"] == "Sinistro" else "seguro")],
    )


@app.route("/quadros/<int:id_quadro>/cards/novo", methods=["POST"])
@login_obrigatorio
def card_novo(id_quadro):
    """
    Criação MANUAL de card. O card NÃO é pendurado num seguro existente:
    ele CRIA um registro novo (seguro ou sinistro) já com status
    "<tipo> em negociação" — ex.: "Endosso em negociação".

    Para Renovação / Endosso / Cancelamento e para Sinistro, escolhe-se a
    apólice base, de onde os dados são copiados e à qual o novo registro
    fica ligado (origem_id). Para "Novo", não há base: o registro nasce em
    branco, sem nenhum dado "anterior".

    "Novo" é também o único tipo que aceita um cliente que ainda não existe
    no sistema — nesse caso o cadastro é criado na hora, já vinculado ao
    comercial dono deste quadro.
    """
    usuario = usuario_atual()
    quadro = data.buscar_quadro(id_quadro)
    if quadro is None:
        flash("Quadro não encontrado.", "erro")
        return redirect(url_for("quadros"))

    # o tipo do card é sempre coerente com o tipo do quadro onde ele nasce
    tipo = "Sinistro" if quadro["tipo"] == "Sinistro" else request.form.get("tipo", "Novo")

    if tipo == "Sinistro":
        # o cliente do sinistro é sempre o dono da apólice escolhida —
        # não existe seleção de cliente separada nesse fluxo.
        base_id = request.form.get("seguro_base_id")
        base = data.buscar_seguro(int(base_id)) if base_id else None
        if base is None:
            flash("Selecione a apólice do sinistro.", "erro")
            return redirect(url_for("pipeline", id_quadro=id_quadro))
        id_cadastro = base["cadastro_id"]

    elif request.form.get("modo_cliente") == "novo":
        # Cliente ainda não existe no sistema — vale para qualquer tipo de seguro
        nome_novo = request.form.get("novo_nome", "").strip()
        documento_novo = request.form.get("novo_documento", "").strip()
        if not nome_novo or not documento_novo:
            flash("Para cadastrar cliente novo, informe nome e CPF/CNPJ.", "erro")
            return redirect(url_for("pipeline", id_quadro=id_quadro))

        cadastro = data.criar_cadastro({
            "nome": nome_novo,
            "documento": documento_novo,
            "tipo": request.form.get("novo_tipo", "PF"),
            "comercial": quadro["comercial"],
            "origem": f"Card de {tipo}",
            "status": "Ativo",
        })
        id_cadastro = cadastro["id"]
    else:
        cadastro_id_form = request.form.get("cadastro_id")
        if not cadastro_id_form:
            flash("Selecione um cliente.", "erro")
            return redirect(url_for("pipeline", id_quadro=id_quadro))
        id_cadastro = int(cadastro_id_form)

    base = request.form.get("seguro_base_id")
    card = data.criar_card(
        tipo=tipo,
        id_cadastro=id_cadastro,
        seguro_base_id=int(base) if base else None,
        criado_por=usuario["nome"],
        dados=request.form,
        quadro_id=id_quadro,
    )

    if card is None:
        flash("Não foi possível criar o card.", "erro")
        return redirect(url_for("pipeline", id_quadro=id_quadro))

    flash("Card criado. Um registro em negociação foi aberto no cadastro do cliente.", "sucesso")
    return redirect(url_for("card_detalhe", id_card=card["id"]))


@app.route("/cards/<int:id_card>/deletar", methods=["POST"])
@login_obrigatorio
def card_deletar(id_card):
    """
    Exclui SÓ o card (e o documento de negociação que vive dele). O
    seguro/sinistro que ele criou e o cadastro do cliente nunca são
    apagados por essa rota — ver data.deletar_card().
    """
    card = _verificar_acesso_card(id_card)
    if card is None:
        return redirect(url_for("quadros"))

    id_quadro = card["quadro_id"]
    data.deletar_card(id_card)
    flash("Card excluído. O seguro/sinistro e o cliente continuam no cadastro.", "sucesso")
    return redirect(url_for("pipeline", id_quadro=id_quadro))


@app.route("/cards/mover", methods=["POST"])
@login_obrigatorio
def card_mover():
    """Chamado via fetch() quando um cartão é arrastado para outra coluna."""
    corpo = request.get_json(silent=True)
    if not corpo or "id_card" not in corpo or "coluna_destino" not in corpo:
        return jsonify({"sucesso": False, "erro": "Payload inválido"}), 400
    try:
        sucesso = data.mover_card(int(corpo["id_card"]), corpo["coluna_destino"])
    except (ValueError, TypeError):
        return jsonify({"sucesso": False, "erro": "ID inválido"}), 400
    return jsonify({"sucesso": sucesso})


# =============================================================================
# CARTÃO (RF13, RF19, RF20, RF21, RF22)
# =============================================================================

@app.route("/cards/<int:id_card>")
@login_obrigatorio
def card_detalhe(id_card):
    """Tela do Cartão (wireframe 15.6): ficha, anexos, tarefas, comentários e
    navegação para a referência (seguro ou sinistro)."""
    card = _verificar_acesso_card(id_card)
    if card is None:
        return redirect(url_for("quadros"))

    referencia, cliente = data.referencia_do_card(card)

    # "Ficha preenchida com os dados do ano anterior" (RF17): só existe
    # "dado anterior" quando o registro deste card teve uma apólice de base
    # (origem_id). Um card "Novo" sem base nunca teve seguro nenhum antes —
    # mostrar prêmio/comissão "anterior" nesse caso seria inventar dado.
    ficha = None
    if card["referencia_tipo"] == "seguro" and referencia and referencia.get("origem_id"):
        ficha = {
            "Ramo": referencia["ramo"],
            "Item": referencia["item"],
            "Seguradora": referencia["seguradora"],
            "Nº Apólice": referencia["numero_apolice"],
            "Prêmio anterior": referencia["premio"],
            "Comissão": f"{referencia['comissao']}%",
            "Forma de pagamento": referencia["forma_pagamento"],
        }

    quadro = data.buscar_quadro(card["quadro_id"])
    usuario = _usuario_ativo()
    quadros_transferencia = []
    if usuario["perfil"] in ("Administrador", "Assistente"):
        quadros_transferencia = [q for q in data.listar_quadros() if q["id"] != card["quadro_id"] and q["tipo"] == quadro["tipo"]]

    return render_template(
        "card_detalhe.html",
        pagina_ativa="quadros",
        card=card,
        quadro=quadro,
        referencia=referencia,
        cliente=cliente,
        ficha=ficha,
        documento=data.documento_do_card(id_card),
        tarefas=data.tarefas_do_card(id_card),
        assistentes=[u["nome"] for u in data.USUARIOS if u["perfil"] in ("Assistente", "Administrador")],
        tipos_tarefa=data.TIPOS_TAREFA,
        urgencias=data.URGENCIAS,
        etiquetas_quadro=data.listar_etiquetas_por_quadro(card["quadro_id"]),
        etiquetas_card=data.listar_etiquetas_por_card(id_card),
        quadros_transferencia=quadros_transferencia,
    )


@app.route("/cards/<int:id_card>/transferir_quadro", methods=["POST"])
@login_obrigatorio
def card_transferir_quadro(id_card):
    """Permite que Assistentes e Administradores movam cards entre quadros de comerciais diferentes."""
    usuario = _usuario_ativo()
    if usuario["perfil"] not in ("Administrador", "Assistente"):
        flash("Apenas Assistentes e Administradores podem transferir cartões entre quadros.", "erro")
        return redirect(url_for("card_detalhe", id_card=id_card))

    corpo = request.get_json(silent=True) or {}
    novo_quadro_id = request.form.get("novo_quadro_id") or corpo.get("novo_quadro_id")
    if not novo_quadro_id:
        if request.is_json:
            return jsonify({"sucesso": False, "erro": "Quadro não informado"}), 400
        flash("Selecione o quadro de destino.", "erro")
        return redirect(url_for("card_detalhe", id_card=id_card))

    try:
        novo_quadro_id = int(novo_quadro_id)
    except (ValueError, TypeError):
        if request.is_json:
            return jsonify({"sucesso": False, "erro": "ID inválido"}), 400
        flash("Quadro inválido.", "erro")
        return redirect(url_for("card_detalhe", id_card=id_card))

    sucesso, msg = data.transferir_card_quadro(id_card, novo_quadro_id, usuario["nome"])
    if sucesso:
        flash(msg, "sucesso")
        if request.is_json:
            return jsonify({"sucesso": True, "mensagem": msg})
        return redirect(url_for("pipeline", id_quadro=novo_quadro_id))
    else:
        flash(msg, "erro")
        if request.is_json:
            return jsonify({"sucesso": False, "erro": msg}), 400
        return redirect(url_for("card_detalhe", id_card=id_card))


@app.route("/cards/<int:id_card>/comentar", methods=["POST"])
@login_obrigatorio
def card_comentar(id_card):
    card = _verificar_acesso_card(id_card)
    if card is None:
        return redirect(url_for("quadros"))
    data.comentar_card(id_card, _usuario_ativo()["nome"], request.form.get("texto", ""))
    return redirect(url_for("card_detalhe", id_card=id_card))


@app.route("/cards/<int:id_card>/comentar_imagem", methods=["POST"])
@login_obrigatorio
def card_comentar_imagem(id_card):
    """Recebe uma imagem colada (Ctrl+V) via fetch/FormData e salva como
    comentário especial '[IMAGEM:nome_arquivo]' no histórico do card."""
    card = _verificar_acesso_card(id_card)
    if card is None:
        return jsonify({"erro": "Acesso negado"}), 403
    try:
        nome = _salvar_anexo(request.files.get("imagem"))
        if not nome:
            return jsonify({"erro": "Nenhum arquivo recebido"}), 400
        autor = _usuario_ativo()["nome"]
        data.comentar_card(id_card, autor, f"[IMAGEM:{nome}]")
        # Retorna URL para o JS exibir a imagem imediatamente, sem reload
        url = url_for("anexo", nome_arquivo=nome)
        return jsonify({"ok": True, "url": url, "nome": nome})
    except ValueError as e:
        return jsonify({"erro": str(e)}), 400
    except Exception:
        logger.exception("Erro ao comentar imagem no card %s", id_card)
        return jsonify({"erro": "Erro interno ao salvar imagem"}), 500


# ---------------------------------------------------------------------------
# BUSCA GLOBAL DE CARDS
# ---------------------------------------------------------------------------
@app.route("/cards/busca")
@login_obrigatorio
def cards_busca():
    """Pesquisa cards nos quadros visíveis para o usuário logado."""
    termo = request.args.get("q", "").strip()
    usuario = _usuario_ativo()
    quadros = data.quadros_do_usuario(usuario)
    ids_quadros = [q["id"] for q in quadros]

    resultados = []
    if termo and ids_quadros:
        resultados = data.buscar_cards_por_texto(termo, ids_quadros)

    return render_template(
        "cards_busca.html",
        resultados=resultados,
        termo=termo,
        total=len(resultados),
    )


@app.route("/cards/<int:id_card>/anexos", methods=["POST"])
@login_obrigatorio
def card_anexar(id_card):
    card = _verificar_acesso_card(id_card)
    if card is None:
        return redirect(url_for("quadros"))
    try:
        nome = _salvar_anexo(request.files.get("arquivo"))
        if nome:
            data.anexar_no_card(id_card, nome, importante=bool(request.form.get("importante")))
            flash("Anexo adicionado ao cartão.", "sucesso")
        else:
            flash("Nenhum arquivo selecionado.", "erro")
    except ValueError as e:
        flash(str(e), "erro")
    except Exception as e:
        logger.exception("Erro ao salvar anexo do card %s", id_card)
        flash("Erro ao salvar o arquivo. Tente novamente.", "erro")
    return redirect(url_for("card_detalhe", id_card=id_card))


@app.route("/cards/<int:id_card>/anexos/<int:indice>/importante", methods=["POST"])
@login_obrigatorio
def card_marcar_anexo(id_card, indice):
    """Marca/desmarca um anexo como importante — só os importantes vão para o
    documento de negociação do cadastro ao finalizar (RF18/RF21)."""
    data.alternar_importancia_anexo(id_card, indice)
    return redirect(url_for("card_detalhe", id_card=id_card))


@app.route("/cards/<int:id_card>/finalizar", methods=["POST"])
@login_obrigatorio
def card_finalizar(id_card):
    """Finalização (seção 8.7): atualiza status no documento do cadastro,
    anexa os documentos importantes, atualiza o seguro e alimenta as métricas.
    Se o resultado for perda, o motivo é obrigatório (RF22)."""
    card = data.buscar_card(id_card)
    if card is None:
        flash("Cartão não encontrado.", "erro")
        return redirect(url_for("quadros"))

    # Idempotência: não finaliza duas vezes
    if card.get("coluna") in data.COLUNAS_FINAIS:
        flash("Este cartão já foi finalizado.", "aviso")
        return redirect(url_for("card_detalhe", id_card=id_card))

    resultado = request.form.get("resultado", "").strip()
    if resultado not in RESULTADOS_VALIDOS:
        flash(f"Resultado inválido. Escolha: {', '.join(sorted(RESULTADOS_VALIDOS))}.", "erro")
        return redirect(url_for("card_detalhe", id_card=id_card))

    motivo = request.form.get("motivo_perda", "").strip()

    if resultado == "Perdido" and not motivo:
        flash("Informe o motivo da perda para finalizar o cartão.", "erro")
        return redirect(url_for("card_detalhe", id_card=id_card))

    data.finalizar_card(id_card, resultado, motivo)
    flash(f"Cartão finalizado como '{resultado}'. Cadastro e métricas atualizados.", "sucesso")
    return redirect(url_for("card_detalhe", id_card=id_card))


# =============================================================================
# TAREFAS (RF14, RF15, RF16)
# =============================================================================

@app.route("/cards/<int:id_card>/tarefas/nova", methods=["POST"])
@login_obrigatorio
@perfil_obrigatorio("Administrador", "Comercial")
def tarefa_nova(id_card):
    """Só o Comercial (e o Administrador, que acumula os papéis) pede tarefa."""
    erros = []
    if not request.form.get("prazo"):
        erros.append("prazo")
    if not request.form.get("descricao", "").strip():
        erros.append("descrição")

    if erros:
        flash(f"Preencha: {', '.join(erros)}.", "erro")
        return redirect(url_for("card_detalhe", id_card=id_card))

    data.criar_tarefa(id_card, request.form, usuario_atual()["nome"])
    flash("Tarefa criada e atribuída.", "sucesso")
    return redirect(url_for("card_detalhe", id_card=id_card))


@app.route("/tarefas/<int:id_tarefa>/entregar", methods=["POST"])
@login_obrigatorio
@perfil_obrigatorio("Administrador", "Assistente")
def tarefa_entregar(id_tarefa):
    """Quem entrega a tarefa é o Assistente (hierarquia da seção 13)."""
    tarefa = data.buscar_tarefa(id_tarefa)
    if tarefa is None:
        flash("Tarefa não encontrada.", "erro")
        return redirect(url_for("quadros"))
    data.entregar_tarefa(id_tarefa)
    flash("Tarefa entregue.", "sucesso")
    # Validação anti open-redirect: só aceita URLs relativas internas
    voltar = _url_segura(request.form.get("voltar"))
    return redirect(voltar or url_for("card_detalhe", id_card=tarefa["card_id"]))


# =============================================================================
# MÉTRICAS (RF23) E RELATÓRIOS (RF24)
# =============================================================================

@app.route("/metricas")
@login_obrigatorio
def metricas():
    inicio, fim = _data_do_form("inicio"), _data_do_form("fim")
    return render_template(
        "metricas.html",
        pagina_ativa="metricas",
        metricas=data.calcular_metricas(inicio, fim),
        meses=data.renovados_x_perdidos_por_mes(inicio, fim),
        inicio=request.args.get("inicio", ""),
        fim=request.args.get("fim", ""),
    )


@app.route("/relatorios")
@login_obrigatorio
def relatorios():
    inicio, fim = _data_do_form("inicio"), _data_do_form("fim")
    filtros = {campo: request.args.get(campo, "") for campo in ("tipo", "origem", "comercial", "status", "ramo", "seguradora")}

    return render_template(
        "relatorios.html",
        pagina_ativa="relatorios",
        linhas=data.gerar_relatorio(inicio, fim, filtros),
        filtros=filtros,
        inicio=request.args.get("inicio", ""),
        fim=request.args.get("fim", ""),
        opcoes_origem=data.valores_distintos("origem"),
        opcoes_comercial=data.valores_distintos("comercial"),
        opcoes_ramo=data.valores_distintos("ramo", origem="seguro"),
        opcoes_seguradora=data.valores_distintos("seguradora", origem="seguro"),
    )


@app.route("/relatorios/exportar")
@login_obrigatorio
@perfil_obrigatorio("Administrador", "Comercial")
def relatorio_exportar():
    """A 'lista exportável' do wireframe 15.13: devolve o resultado da busca
    como um arquivo CSV, que abre direto no Excel."""
    inicio, fim = _data_do_form("inicio"), _data_do_form("fim")
    filtros = {campo: request.args.get(campo, "") for campo in ("tipo", "origem", "comercial", "status", "ramo", "seguradora")}

    buffer = io.StringIO()
    escritor = csv.writer(buffer, delimiter=";")
    escritor.writerow(["Cliente", "CPF/CNPJ", "Tipo", "Comercial", "Origem", "Ramo", "Seguradora", "Nº Apólice", "Vigência", "Prêmio", "Comissão %"])

    for linha in data.gerar_relatorio(inicio, fim, filtros):
        cadastro, seguro = linha["cadastro"], linha["seguro"]
        escritor.writerow([
            cadastro["nome"], cadastro["documento"], cadastro["tipo"], cadastro["comercial"], cadastro["origem"],
            seguro["ramo"], seguro["seguradora"], seguro["numero_apolice"],
            f"{seguro['vigencia_inicio']:%d/%m/%Y} a {seguro['vigencia_fim']:%d/%m/%Y}",
            f"{seguro['premio']:.2f}".replace(".", ","), f"{seguro['comissao']:.1f}".replace(".", ","),
        ])

    return Response(
        buffer.getvalue().encode("utf-8-sig"),  # BOM para o Excel abrir com acentos corretos
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=relatorio_corretora.csv"},
    )


# =============================================================================
# USUÁRIOS (RF25, RF26) — exclusivo do Administrador
# =============================================================================

@app.route("/usuarios")
@login_obrigatorio
@perfil_obrigatorio("Administrador")
def usuarios():
    # quadros_visiveis vai como função: o template pergunta, para cada
    # usuário, quais quadros aquele perfil enxerga (regra única em data.py).
    return render_template("usuarios.html", pagina_ativa="usuarios",
                           usuarios=data.listar_usuarios(), quadros=data.QUADROS,
                           quadros_visiveis=data.quadros_do_usuario)


@app.route("/usuarios/novo", methods=["GET", "POST"])
@login_obrigatorio
@perfil_obrigatorio("Administrador")
def usuario_novo():
    if request.method == "GET":
        return render_template("usuario_form.html", pagina_ativa="usuarios", usuario=None, perfis=data.PERFIS)

    if not request.form.get("nome", "").strip() or not request.form.get("login", "").strip():
        flash("Nome e login são obrigatórios.", "erro")
        return render_template("usuario_form.html", pagina_ativa="usuarios", usuario=None, perfis=data.PERFIS, valores=request.form)

    data.criar_usuario(request.form)
    flash("Usuário cadastrado.", "sucesso")
    return redirect(url_for("usuarios"))


@app.route("/usuarios/<int:id_usuario>/editar", methods=["GET", "POST"])
@login_obrigatorio
@perfil_obrigatorio("Administrador")
def usuario_editar(id_usuario):
    usuario = data.buscar_usuario(id_usuario)
    if usuario is None:
        flash("Usuário não encontrado.", "erro")
        return redirect(url_for("usuarios"))

    if request.method == "GET":
        return render_template("usuario_form.html", pagina_ativa="usuarios", usuario=usuario, perfis=data.PERFIS)

    data.atualizar_usuario(id_usuario, request.form)
    flash("Usuário atualizado.", "sucesso")
    return redirect(url_for("usuarios"))


@app.route("/usuarios/<int:id_usuario>/deletar", methods=["POST"])
@login_obrigatorio
@perfil_obrigatorio("Administrador")
def usuario_deletar(id_usuario):
    if id_usuario == session.get("usuario_id"):
        flash("Você não pode excluir o próprio usuário.", "erro")
    else:
        data.deletar_usuario(id_usuario)
        flash("Usuário removido.", "sucesso")
    return redirect(url_for("usuarios"))


# =============================================================================
# ANEXOS — serve os arquivos enviados
# =============================================================================

@app.route("/anexos/<nome_arquivo>")
@login_obrigatorio
def anexo(nome_arquivo):
    # Por padrão serve inline (para abrir imagens e PDFs no card sem baixar).
    # Se o parâmetro ?download=1 for passado, força o download.
    baixar = request.args.get("download") == "1"
    response = send_from_directory(
        app.config["PASTA_ANEXOS"],
        nome_arquivo,
        as_attachment=baixar,
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


# =============================================================================
# CONFIGURAÇÕES DA CORRETORA (Exclusivo do Administrador)
# =============================================================================

@app.route("/configuracoes", methods=["GET", "POST"])
@login_obrigatorio
@perfil_obrigatorio("Administrador")
def configuracoes():
    if request.method == "POST":
        nome = request.form.get("nome_corretora", "").strip()
        if not nome:
            flash("O nome da corretora não pode ficar em branco.", "erro")
        else:
            data.salvar_configuracao("nome_corretora", nome)
            data.salvar_configuracao("cnpj_corretora", request.form.get("cnpj_corretora", "").strip())
            data.salvar_configuracao("telefone_corretora", request.form.get("telefone_corretora", "").strip())
            data.salvar_configuracao("email_corretora", request.form.get("email_corretora", "").strip())

            usuario = usuario_atual()
            data.registrar_auditoria(
                usuario["nome"],
                usuario["perfil"],
                "CONFIGURACAO_ATUALIZADA",
                f"Nome da corretora alterado para '{nome}'",
                _ip_cliente()
            )
            flash("Configurações da corretora atualizadas com sucesso!", "sucesso")
        return redirect(url_for("configuracoes"))

    dados = {
        "nome_corretora": data.obter_configuracao("nome_corretora", "Minha Corretora de Seguros"),
        "cnpj_corretora": data.obter_configuracao("cnpj_corretora", ""),
        "telefone_corretora": data.obter_configuracao("telefone_corretora", ""),
        "email_corretora": data.obter_configuracao("email_corretora", ""),
    }
    return render_template("configuracoes.html", pagina_ativa="configuracoes", config=dados)


# =============================================================================
# ETIQUETAS (Labels estilo Trello)
# =============================================================================

@app.route("/api/quadros/<int:quadro_id>/etiquetas", methods=["GET"])
@login_obrigatorio
def api_etiquetas_quadro(quadro_id):
    """Retorna as etiquetas do quadro em JSON."""
    etiquetas = data.listar_etiquetas_por_quadro(quadro_id)
    return jsonify(etiquetas)


@app.route("/api/quadros/<int:quadro_id>/etiquetas", methods=["POST"])
@login_obrigatorio
def api_criar_etiqueta(quadro_id):
    """Cria uma etiqueta e a vincula ao quadro."""
    payload = request.get_json(force=True, silent=True) or {}
    nome = payload.get("nome", "").strip()
    cor = payload.get("cor", "#3498db").strip()
    if not nome:
        return jsonify({"erro": "Nome obrigatório"}), 400
    etiqueta_id = data.criar_etiqueta(nome, cor, quadro_id)
    return jsonify({"id": etiqueta_id, "nome": nome, "cor": cor}), 201


@app.route("/api/etiquetas/<int:etiqueta_id>", methods=["PUT"])
@login_obrigatorio
def api_atualizar_etiqueta(etiqueta_id):
    """Atualiza nome e/ou cor de uma etiqueta."""
    payload = request.get_json(force=True, silent=True) or {}
    nome = payload.get("nome", "").strip()
    cor = payload.get("cor", "#3498db").strip()
    if not nome:
        return jsonify({"erro": "Nome obrigatório"}), 400
    data.atualizar_etiqueta(etiqueta_id, nome, cor)
    return jsonify({"ok": True})


@app.route("/api/etiquetas/<int:etiqueta_id>", methods=["DELETE"])
@login_obrigatorio
def api_deletar_etiqueta(etiqueta_id):
    """Remove uma etiqueta e todos os seus vínculos."""
    data.deletar_etiqueta(etiqueta_id)
    return jsonify({"ok": True})


@app.route("/api/cards/<int:card_id>/etiquetas", methods=["GET"])
@login_obrigatorio
def api_etiquetas_card(card_id):
    """Retorna as etiquetas vinculadas a um card."""
    etiquetas = data.listar_etiquetas_por_card(card_id)
    return jsonify(etiquetas)


@app.route("/api/cards/<int:card_id>/etiquetas/<int:etiqueta_id>/toggle", methods=["POST"])
@login_obrigatorio
def api_toggle_etiqueta_card(card_id, etiqueta_id):
    """Adiciona ou remove uma etiqueta do card (toggle)."""
    adicionou = data.toggle_etiqueta_card(card_id, etiqueta_id)
    return jsonify({"adicionou": adicionou})


# =============================================================================
# BUSCA E APÓLICES DE CLIENTES VIA API (para modais e buscas dinâmicas)
# =============================================================================

@app.route("/api/clientes/busca", methods=["GET"])
@login_obrigatorio
def api_clientes_busca():
    """Busca rápida de clientes por nome ou CPF/CNPJ."""
    termo = request.args.get("q", "").strip()
    comercial = request.args.get("comercial", "").strip()
    usuario = _usuario_ativo()

    conn = database.get_db()
    sql = "SELECT id, nome, documento, tipo, comercial FROM cadastros WHERE status = 'Ativo'"
    params = []

    if termo:
        sql += " AND (nome LIKE ? COLLATE NOCASE OR documento LIKE ? COLLATE NOCASE)"
        params.extend([f"%{termo}%", f"%{termo}%"])

    if usuario["perfil"] == "Comercial":
        sql += " AND comercial = ?"
        params.append(usuario["nome"])
    elif comercial:
        sql += " AND comercial = ?"
        params.append(comercial)

    sql += " ORDER BY nome ASC LIMIT 30;"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/clientes/<int:id_cliente>/apolices", methods=["GET"])
@login_obrigatorio
def api_cliente_apolices(id_cliente):
    """Retorna apólices ativas de um cliente para seleção de apólice base."""
    conn = database.get_db()
    rows = conn.execute("""
        SELECT id, ramo, seguradora, numero_apolice, vigencia_fim
        FROM seguros
        WHERE cadastro_id = ? AND status = 'Ativo'
        ORDER BY id DESC;
    """, (id_cliente,)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


if __name__ == "__main__":
    import socket
    def _obter_ip_local():
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        except Exception:
            return "127.0.0.1"
        finally:
            s.close()

    host = os.environ.get("FLASK_HOST", "0.0.0.0")
    port = int(os.environ.get("FLASK_PORT", 5000))
    ip_rede = _obter_ip_local()

    print("\n" + "=" * 58)
    print("  NCM SISTEMA DE GESTAO - SERVIDOR INICIADO")
    print(f"  > Acesso neste computador:  http://127.0.0.1:{port}")
    if ip_rede != "127.0.0.1":
        print(f"  > Acesso na rede local:     http://{ip_rede}:{port}")
    print("  > Modo Debug: Desativado (Seguro)")
    print("=" * 58 + "\n")

    app.run(host=host, port=port, debug=False)
