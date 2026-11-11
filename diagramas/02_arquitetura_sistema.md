# Diagrama de Arquitetura do Sistema

Este documento descreve a arquitetura em camadas do Sistema de Gestão de Corretora de Seguros, cobrindo da interface do usuário até a persistência de dados e segurança.

---

## 🏛️ Visão Geral da Arquitetura

```mermaid
flowchart TD
    subgraph CLIENT_LAYER["Camada de Apresentação (Cliente)"]
        UI_DESKTOP["Navegador Desktop (Chrome, Edge, Firefox)"]
        UI_MOBILE["Navegador Mobile (Smartphones / Tablets)"]
        UI_THEME["Design System (Modo Claro / Modo Escuro via localStorage)"]
        UI_KANBAN["Interface Kanban Trello (HTML5 Drag & Drop)"]
    end

    subgraph NETWORK_LAYER["Camada de Rede & Segurança de Transporte"]
        LAN["Rede Local / LAN (0.0.0.0:5000)"]
        FIREWALL["Regra de Firewall Windows (Porta 5000 TCP)"]
    end

    subgraph SERVER_LAYER["Camada de Servidor WSGI"]
        WAITRESS["Servidor de Produção Multithreaded (Waitress — 8 Threads)"]
    end

    subgraph APP_LAYER["Camada de Aplicação (Flask 3.1)"]
        SESSION_MGR["Gerenciador de Sessão (HTTPOnly, SameSite=Lax, .secret_key)"]
        RATE_LIMITER["Proteção de Força Bruta (Rate Limiting de Tentativas de Login)"]
        AUTH_GUARD["Guarda de Autenticação & Troca Obrigatória de Senha"]
        RBAC["Controle de Acesso por Perfil (@perfil_obrigatorio)"]
        UPLOAD_MGR["Módulo de Upload Seguro (UUID + Whitelist de Extensões)"]
        ROUTES["Controladores de Rotas (Dashboard, Clientes, Quadros, Métricas, etc.)"]
        JINJA["Motor de Templates Jinja2 (Filtros Moeda/Data, Injeção Global)"]
    end

    subgraph DATA_LAYER["Camada de Lógica de Negócios & Dados (Python)"]
        DATA_PY["data.py (Regras de Negócio, Automação de Renovações e Cálculos)"]
        DB_PY["database.py (Conexão SQLite, PRAGMA Foreign Keys, DDL)"]
        LOGGER["Sistema de Logs e Auditoria (RotatingFileHandler)"]
    end

    subgraph STORAGE_LAYER["Camada de Persistência Local"]
        SQLITE_DB[("corretora.db (Banco de Dados Relacional SQLite)")]
        UPLOADS_DIR["uploads/ (Armazenamento de Anexos e Documentos)"]
        LOGS_FILE["app.log (Arquivo Rotativo de Logs de Sistema)"]
        BACKUPS_DIR["backups/ (Cópias de Segurança com Carimbo de Data/Hora)"]
    end

    UI_DESKTOP --> LAN
    UI_MOBILE --> LAN
    LAN --> FIREWALL
    FIREWALL --> WAITRESS

    WAITRESS --> SESSION_MGR
    SESSION_MGR --> RATE_LIMITER
    RATE_LIMITER --> AUTH_GUARD
    AUTH_GUARD --> RBAC
    RBAC --> ROUTES
    ROUTES --> JINJA
    ROUTES --> UPLOAD_MGR

    ROUTES --> DATA_PY
    UPLOAD_MGR --> UPLOADS_DIR
    DATA_PY --> DB_PY
    DATA_PY --> LOGGER

    DB_PY --> SQLITE_DB
    LOGGER --> LOGS_FILE
    SQLITE_DB -.-> BACKUPS_DIR
    UPLOADS_DIR -.-> BACKUPS_DIR
```

---

## 🛡️ Camadas e Responsabilidades

1. **Cliente:**
   * Aplicação web responsiva compatível com dispositivos móveis e desktops.
   * Renderização imediata anti-flicker de tema claro/escuro.
   * Interações ricas no Kanban (arrastar e soltar cartões sem recarregar tela).

2. **Servidor e Rede:**
   * **Waitress WSGI:** processa múltiplas conexões em paralelo com 8 threads simultâneas, ideal para trabalho em equipe na rede da corretora.
   * Firewall configurado automaticamente na primeira inicialização para permitir tráfego local na porta 5000.

3. **Aplicação (Flask):**
   * Verificação de primeiro acesso forçando redefinição da senha padrão.
   * Cookies assinados com proteção contra sequestro via JavaScript (`HTTPOnly`).
   * Sanitização rigorosa de uploads impedindo a execução de códigos maliciosos.

4. **Persistência:**
   * **`corretora.db`:** Banco de dados ACID relacional local, com chaves estrangeiras ativas (`PRAGMA foreign_keys = ON`).
   * Script de backup automatizado (`fazer_backup.bat`) para preservação histórica.
