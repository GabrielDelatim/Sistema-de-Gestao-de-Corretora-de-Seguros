# 📐 Modelagem e Diagramas do Sistema

Esta pasta reúne a documentação técnica, arquitetural e funcional do **Sistema de Gestão de Corretora de Seguros**. Todos os diagramas utilizam o padrão **Mermaid**, renderizado nativamente no GitHub e em visualizadores Markdown.

---

## 📑 Índice de Diagramas

| Documento | Assunto | Conteúdo Principal |
|---|---|---|
| [**01. Modelo Entidade-Relacionamento (MER)**](01_mer_banco_dados.md) | Banco de Dados Relacional | Modelagem completa das 11 tabelas do SQLite, chaves primárias, estrangeiras e relacionamentos |
| [**02. Arquitetura do Sistema**](02_arquitetura_sistema.md) | Engenharia de Software | Camadas de Apresentação, Rede, WSGI (Waitress), Aplicação (Flask) e Persistência Local |
| [**03. Diagrama de Casos de Uso**](03_casos_de_uso.md) | Papéis e Permissões | Matriz RBAC detalhada para Administrador, Comercial, Assistente e Área de Sinistros |
| [**04. Fluxo Comercial & Kanban**](04_fluxo_comercial_kanban.md) | Processos Operacionais | Fluxo em raias (Swimlane) da negociação e máquinas de estado dos cards de Seguro e Sinistro |
| [**05. Mapa de Navegação**](05_mapa_navegacao.md) | Sitemap e Telas | Estrutura de rotas, transições de tela e hierarquia dos módulos do sistema |

---

## 💡 Como Visualizar os Diagramas

* **No GitHub:** Ao abrir qualquer um dos arquivos `.md` desta pasta no GitHub, os diagramas são renderizados automaticamente em formato visual interativo.
* **No VS Code:** Instale a extensão *Markdown Preview Mermaid Support* ou abra a pré-visualização padrão do Markdown (`Ctrl + Shift + V`).
