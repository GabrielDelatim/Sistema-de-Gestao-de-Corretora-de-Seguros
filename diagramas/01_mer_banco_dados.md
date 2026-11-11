# Diagrama Entidade-Relacionamento (MER / DER)

Este documento descreve a modelagem de dados relacional do sistema da corretora de seguros implementada sobre o banco de dados SQLite (`corretora.db`).

---

## 📊 Diagrama de Entidades e Relacionamentos

```mermaid
erDiagram
    usuarios {
        INTEGER id PK
        TEXT nome
        TEXT login UK
        TEXT senha_hash
        TEXT perfil
        TEXT status
        INTEGER precisa_trocar_senha
    }

    configuracoes {
        TEXT chave PK
        TEXT valor
    }

    cadastros {
        INTEGER id PK
        TEXT nome
        TEXT documento
        TEXT tipo
        TEXT vinculos
        TEXT descricao
        TEXT comercial
        TEXT status
        TEXT origem
        TEXT endereco
        TEXT telefone
        TEXT email
        DATE data_cadastro
    }

    quadros {
        INTEGER id PK
        TEXT nome
        TEXT comercial
        TEXT tipo
        TEXT colunas_json
    }

    seguros {
        INTEGER id PK
        INTEGER cadastro_id FK
        INTEGER origem_id FK
        TEXT ramo
        TEXT item
        DATE vigencia_inicio
        DATE vigencia_fim
        TEXT seguradora
        REAL premio
        REAL comissao
        TEXT forma_pagamento
        TEXT numero_apolice
        TEXT status
        TEXT anexos_json
    }

    sinistros {
        INTEGER id PK
        INTEGER seguro_id FK
        TEXT numero
        DATE data_ocorrencia
        TEXT terceiros
        TEXT status
        TEXT anexos_json
    }

    cards {
        INTEGER id PK
        TEXT tipo
        TEXT referencia_tipo
        INTEGER referencia_id
        INTEGER quadro_id FK
        TEXT coluna
        DATE data_criacao
        TEXT criado_por
        TEXT motivo_perda
        TEXT comentarios_json
        TEXT anexos_json
    }

    tarefas {
        INTEGER id PK
        INTEGER card_id FK
        TEXT tipo
        TEXT urgencia
        DATE prazo
        TEXT descricao
        TEXT comercial
        TEXT assistente
        TEXT status
        DATE data_pedido
        DATE data_entrega
    }

    documentos_negociacao {
        INTEGER id PK
        INTEGER cadastro_id FK
        INTEGER card_id FK
        TEXT descricao
        TEXT status
        TEXT anexos_json
        DATE data_atualizacao
    }

    historico_eventos {
        INTEGER id PK
        TEXT resultado
        DATE data
        TEXT comercial
        TEXT ramo
        TEXT seguradora
        REAL premio
        REAL comissao
        TEXT motivo_perda
    }

    logs_auditoria {
        INTEGER id PK
        TIMESTAMP data
        TEXT usuario
        TEXT perfil
        TEXT acao
        TEXT detalhes
        TEXT ip
    }

    cadastros ||--o{ seguros : "possui (1:N)"
    seguros ||--o{ sinistros : "gera (1:N)"
    seguros |o--o{ seguros : "renovacao de (1:N)"
    quadros ||--o{ cards : "contem (1:N)"
    cards ||--o{ tarefas : "possui (1:N)"
    cadastros ||--o{ documentos_negociacao : "registra (1:N)"
    cards ||--o{ documentos_negociacao : "anexa (1:N)"
```

---

## 🔑 Descrição das Tabelas e Integridade

| Tabela | Função no Sistema | Regras de Integridade |
|---|---|---|
| **`usuarios`** | Colaboradores da corretora e seus perfis de acesso. | `login` único; senhas com hash criptográfico; `precisa_trocar_senha` força redefinição no primeiro uso. |
| **`configuracoes`** | Parâmetros globais personalizáveis (ex: Nome da Corretora, CNPJ). | Chave primária textual única; busca centralizada para todo o sistema. |
| **`cadastros`** | Segurados (Pessoa Física ou Pessoa Jurídica) e seus dados de contato. | Vínculo obrigatório com o comercial responsável da carteira. |
| **`quadros`** | Pipelines Kanban de **Seguros** e de **Sinistros** vinculados a cada comercial. | Criados automaticamente no cadastro de cada comercial; deletados em cascata ao remover o comercial. |
| **`seguros`** | Apólices contratadas ou propostas em negociação. | `cadastro_id` (FK ON DELETE CASCADE); `origem_id` aponta para a apólice anterior em caso de renovação. |
| **`sinistros`** | Ocorrências de sinistro registradas sob uma apólice coberta. | `seguro_id` (FK ON DELETE CASCADE); acompanhamento de terceiros e regulação. |
| **`cards`** | Cartões operacionais do Kanban (Renovação, Novo, Endosso, Sinistro). | `quadro_id` (FK ON DELETE CASCADE); guarda coluna atual, histórico de comentários e anexos. |
| **`tarefas`** | Atividades delegadas entre Comercial e Assistente (cotação, vistoria, etc.). | `card_id` (FK ON DELETE CASCADE); prazos, status e urgência. |
| **`documentos_negociacao`**| Registro documental e histórico de anexos da negociação. | Relaciona cliente (`cadastro_id`) e negociação (`card_id`). |
| **`historico_eventos`** | Memória estática de eventos (fechados, renovados, perdidos e comissões). | Usado na geração de métricas de retenção e gráficos financeiros. |
| **`logs_auditoria`** | Trilha de auditoria e segurança. | Registra data, hora, IP, usuário e ação executada para conformidade e segurança. |
