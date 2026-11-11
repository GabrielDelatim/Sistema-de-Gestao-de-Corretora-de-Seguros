# Corretora de Seguros · Sistema de Gestão Integrada

Sistema web completo para gestão operacional e comercial de corretoras de seguros, desenvolvido em **Python (Flask + SQLite)** com interface moderna, minimalista, quadro **Kanban estilo Trello** e suporte nativo a **Modo Escuro / Claro**.

---

## 🚀 Como Iniciar o Sistema

### No Windows (Recomendado)
Basta dar um duplo-clique no arquivo:
```cmd
iniciar.bat
```
O script verifica o Python, instala as dependências necessárias, inicializa o banco de dados caso não exista e inicia o servidor de produção multithreaded (**Waitress**) abrindo o navegador automaticamente em `http://127.0.0.1:5000`.

### Em Rede Local (LAN)
Qualquer computador ou dispositivo conectado à mesma rede Wi-Fi/cabo pode acessar o sistema digitando o endereço IP exibido na janela do terminal (exemplo: `http://192.168.15.100:5000`).

### Inicialização Manual (Linux, macOS ou Terminal)
```bash
python3 -m venv venv
source venv/bin/activate       # No Windows: venv\Scripts\activate
pip install -r requirements.txt
python init_db.py              # Cria o banco de dados limpo
python -m waitress --host=0.0.0.0 --port=5000 --threads=8 app:app
```

---

## 🔐 Primeiro Acesso e Configuração Inicial

1. **Acesso Inicial:**
   * **Usuário:** `admin`
   * **Senha padrão:** `admin123`

2. **Troca Obrigatória de Senha:**
   * No primeiro login, o sistema exige a definição imediata de uma nova senha segura.
   * O aviso de acesso padrão desaparece permanentemente da tela de login assim que a senha for alterada.

3. **Nome da Corretora:**
   * Acesse **Administração → Configurações** para cadastrar o nome da sua corretora, CNPJ e canais de contato. O nome configurado é refletido automaticamente em todo o sistema.

---

## 📋 Regras de Negócio e Funcionalidades

* **Kanban por Comercial:** Não existem quadros genéricos. Ao cadastrar um colaborador com perfil **Comercial**, o sistema gera automaticamente seus dois quadros de negociação:
  * **Seguros:** fluxo completo de prospecção, cotação, cálculo, proposta enviada, negociação e emissão.
  * **Sinistros:** aviso do sinistro, documentação/vistoria, análise da seguradora e indenização.
  *(Ao excluir um comercial, os quadros e cards vinculados a ele são removidos automaticamente).*
* **Visibilidade por Perfil:**
  * **Administrador:** Acesso completo a todas as áreas, usuários, configurações e auditoria.
  * **Comercial:** Acessa seus próprios quadros, carteira de clientes e tarefas delegadas.
  * **Assistente:** Enxerga os quadros de Seguros de todos os comerciais para apoiar cálculos e transmissões.
  * **Área Sinistros:** Enxerga e opera todos os quadros de sinistros.
* **Alertas e Renovações Automáticas:** Apólices a vencer nos próximos 30 dias geram cards de renovação automáticos no Kanban.
* **Métricas e Relatórios:** Gráficos com paleta suave e sóbria de renovações x perdas, distribuição de carteira por ramo e exportação para Excel/CSV.

---

## 🛡️ Segurança e Auditoria

* **Criptografia de Senhas:** Hashes seguros gerados com salt via `werkzeug.security`.
* **Chave Secreta Persistente:** Geração automática de chave de 256 bits em `.secret_key`.
* **Proteção contra Força Bruta:** Bloqueio temporário por rate-limiting após 5 tentativas incorretas de login.
* **Upload Seguro:** Validação estrita de extensões permitidas (`.pdf`, `.png`, `.jpg`, `.docx`, `.xlsx`, etc.) com nomes únicos via UUID para evitar sobrescritas.
* **Servidor WSGI de Produção:** Executado via **Waitress** com suporte nativo a requisições concorrentes simultâneas em rede local.
* **Trilha de Auditoria:** Registro de acessos e ações críticas no banco de dados (`logs_auditoria`) e em arquivo rotativo (`app.log`).
* **Backup Automático:** Script [`fazer_backup.bat`](fazer_backup.bat) para cópia instantânea com data e hora para a pasta `backups/`.

---

## 📁 O que Enviar e o que NÃO Enviar ao GitHub

O arquivo `.gitignore` já está configurado. A tabela abaixo resume o que deve ser commitado:

| Tipo de Arquivo | Arquivo / Pasta | Enviar ao GitHub? | Motivo |
|---|---|:---:|---|
| **Código-fonte** | `app.py`, `data.py`, `database.py`, `init_db.py` | ✅ **SIM** | Estrutura e lógica do sistema |
| **Scripts de execução** | `iniciar.bat`, `fazer_backup.bat` | ✅ **SIM** | Facilidade de inicialização e backup |
| **Interface / Telas** | `templates/` e `static/` | ✅ **SIM** | Layouts HTML e estilos CSS |
| **Dependências** | `requirements.txt` | ✅ **SIM** | Lista de bibliotecas para instalação |
| **Documentação** | `README.md`, `.gitignore` | ✅ **SIM** | Instruções do repositório |
| **Chave Secreta** | `.secret_key` | ❌ **NÃO** | **Segredo criptográfico da instância** |
| **Banco de Dados** | `corretora.db`, `*.db` | ❌ **NÃO** | **Contém dados reais de clientes e apólices** |
| **Arquivos dos Clientes** | `uploads/*` | ❌ **NÃO** | **Documentos, vistorias e PDFs confidenciais** |
| **Cópias de Segurança** | `backups/*` | ❌ **NÃO** | Backups locais gerados |
| **Logs do Servidor** | `app.log`, `*.log` | ❌ **NÃO** | Contém IPs e histórico de acessos |
| **Cache do Python** | `__pycache__/`, `venv/` | ❌ **NÃO** | Arquivos temporários de compilação |

---

## 📐 Modelagem e Diagramas

A documentação técnica e visual completa do sistema está organizada na pasta [`diagramas/`](diagramas/):
* [**01. Modelo Entidade-Relacionamento (MER)**](diagramas/01_mer_banco_dados.md) — Esquema das 11 tabelas, chaves e integridade relacional.
* [**02. Arquitetura do Sistema**](diagramas/02_arquitetura_sistema.md) — Camadas de apresentação, servidor WSGI Waitress, aplicação e dados.
* [**03. Casos de Uso & Matriz de Permissões (RBAC)**](diagramas/03_casos_de_uso.md) — Atribuições de Administrador, Comercial, Assistente e Sinistros.
* [**04. Fluxo Comercial & Estados do Kanban**](diagramas/04_fluxo_comercial_kanban.md) — Diagrama de raias (swimlane) e ciclo de vida dos cards.
* [**05. Mapa de Navegação**](diagramas/05_mapa_navegacao.md) — Sitemap interativo de rotas e transições de tela.

---

## 📄 Licença

Este projeto é disponibilizado para uso e gestão de corretoras de seguros.
