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
# Acesse http://localhost:5000 — login de teste: adm / 123456
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

from flask import (Flask, Response, flash, redirect, render_template, request,
                   send_from_directory, session, url_for)
from werkzeug.security import check_password_hash
from werkzeug.utils import secure_filename

import data

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
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # Máximo de 16 MB por upload
app.config["PASTA_ANEXOS"] = "uploads"

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
    return valor_data.strftime("%d/%m/%Y") if valor_data else "—"


# -----------------------------------------------------------------------------
# VARIÁVEIS DISPONÍVEIS EM TODOS OS TEMPLATES
# -----------------------------------------------------------------------------
@app.context_processor
def injetar_globais():
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


# -----------------------------------------------------------------------------
# CONTROLE DE ACESSO
# -----------------------------------------------------------------------------
def login_obrigatorio(funcao_rota):
    """Porteiro: sem sessão ativa, manda para o login."""
    @wraps(funcao_rota)
    def rota_protegida(*args, **kwargs):
        if usuario_atual() is None:
            return redirect(url_for("login"))
        return funcao_rota(*args, **kwargs)
    return rota_protegida


def perfil_obrigatorio(*perfis_permitidos):
    """Restringe uma rota a determinados perfis (RF26). Ex.: manter usuários
    é exclusivo do Administrador."""
    def decorador(funcao_rota):
        @wraps(funcao_rota)
        def rota_protegida(*args, **kwargs):
            usuario = usuario_atual()
            if usuario is None:
                return redirect(url_for("login"))
            if usuario["perfil"] not in perfis_permitidos:
                flash("Seu perfil não tem permissão para acessar essa área.", "erro")
                return redirect(url_for("dashboard"))
            return funcao_rota(*args, **kwargs)
        return rota_protegida
    return decorador


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
    """Obtém o IP real do cliente mesmo através de proxy reverso."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
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
    cliente = data.buscar_cadastro(id_cliente)
    if cliente is None:
        flash("Cliente não encontrado.", "erro")
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
    seguro = data.buscar_seguro(id_seguro)
    if seguro is None:
        flash("Seguro não encontrado.", "erro")
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
    seguro = data.buscar_seguro(id_seguro)
    id_cliente = seguro["cadastro_id"] if seguro else None
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
    usuario = usuario_atual()
    quadro = data.buscar_quadro(id_quadro)

    if quadro is None:
        flash("Quadro não encontrado.", "erro")
        return redirect(url_for("quadros"))

    # Regra de negócio: o usuário só enxerga os quadros aos quais pertence.
    if quadro not in data.quadros_do_usuario(usuario):
        flash("Você não está vinculado a esse quadro.", "erro")
        return redirect(url_for("quadros"))

    # monta {coluna: [cards]} já com a referência (seguro/sinistro) resolvida
    colunas = {}
    for coluna in quadro["colunas"]:
        colunas[coluna] = []
    for card in data.cards_do_quadro(id_quadro):
        referencia, cliente = data.referencia_do_card(card)
        tarefas = data.tarefas_do_card(card["id"])
        colunas.setdefault(card["coluna"], []).append({
            **card,
            "referencia": referencia,
            "cliente": cliente,
            "total_tarefas": len(tarefas),
            "tarefas_pendentes": len([t for t in tarefas if t.get("status") == "Pendente"]),
            "total_comentarios": len(card.get("comentarios", [])),
        })

    # Tanto o seletor de cliente quanto o de apólice-base só mostram quem é
    # do comercial dono deste quadro — não faz sentido oferecer um cliente
    # de outro comercial na hora de abrir um card aqui.
    return render_template(
        "pipeline.html",
        pagina_ativa="quadros",
        quadro=quadro,
        colunas=colunas,
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

    elif tipo == "Novo" and request.form.get("modo_cliente") == "novo":
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
            "origem": "Card de novo seguro",
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
    card = data.buscar_card(id_card)
    if card is None:
        flash("Card não encontrado.", "erro")
        return redirect(url_for("quadros"))

    id_quadro = card["quadro_id"]
    data.deletar_card(id_card)
    flash("Card excluído. O seguro/sinistro e o cliente continuam no cadastro.", "sucesso")
    return redirect(url_for("pipeline", id_quadro=id_quadro))


@app.route("/cards/mover", methods=["POST"])
@login_obrigatorio
def card_mover():
    """Chamado via fetch() quando um cartão é arrastado para outra coluna."""
    corpo = request.get_json()
    sucesso = data.mover_card(int(corpo["id_card"]), corpo["coluna_destino"])
    return {"sucesso": sucesso}


# =============================================================================
# CARTÃO (RF13, RF19, RF20, RF21, RF22)
# =============================================================================

@app.route("/cards/<int:id_card>")
@login_obrigatorio
def card_detalhe(id_card):
    """Tela do Cartão (wireframe 15.6): ficha, anexos, tarefas, comentários e
    navegação para a referência (seguro ou sinistro)."""
    card = data.buscar_card(id_card)
    if card is None:
        flash("Cartão não encontrado.", "erro")
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

    return render_template(
        "card_detalhe.html",
        pagina_ativa="quadros",
        card=card,
        quadro=data.buscar_quadro(card["quadro_id"]),
        referencia=referencia,
        cliente=cliente,
        ficha=ficha,
        documento=data.documento_do_card(id_card),
        tarefas=data.tarefas_do_card(id_card),
        assistentes=[u["nome"] for u in data.USUARIOS if u["perfil"] in ("Assistente", "Administrador")],
        tipos_tarefa=data.TIPOS_TAREFA,
        urgencias=data.URGENCIAS,
    )


@app.route("/cards/<int:id_card>/comentar", methods=["POST"])
@login_obrigatorio
def card_comentar(id_card):
    data.comentar_card(id_card, usuario_atual()["nome"], request.form.get("texto", ""))
    return redirect(url_for("card_detalhe", id_card=id_card))


@app.route("/cards/<int:id_card>/anexos", methods=["POST"])
@login_obrigatorio
def card_anexar(id_card):
    nome = _salvar_anexo(request.files.get("arquivo"))
    if nome:
        data.anexar_no_card(id_card, nome, importante=bool(request.form.get("importante")))
        flash("Anexo adicionado ao cartão.", "sucesso")
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
    resultado = request.form.get("resultado", "Renovado")
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
    data.entregar_tarefa(id_tarefa)
    flash("Tarefa entregue.", "sucesso")
    return redirect(request.form.get("voltar") or url_for("card_detalhe", id_card=tarefa["card_id"]))


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
    return send_from_directory(app.config["PASTA_ANEXOS"], nome_arquivo)


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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
