# Fluxo Comercial e Ciclo de Vida do Kanban

Este documento detalha o fluxo de trabalho entre os colaboradores da corretora e os estados pelos quais um card de negociação ou sinistro transita no Kanban.

---

## 🌊 1. Fluxo Comercial em Raias (Swimlane)

```mermaid
sequenceDiagram
    autonumber
    actor Cliente as Cliente Segurado
    actor Comercial as Comercial Responsável
    actor Assistente as Assistente Operacional
    participant Sistema as Sistema da Corretora
    actor Seguradora as Companhia Seguradora

    %% Início da Negociação
    Cliente->>Comercial: Solicita cotação ou aviso de renovação
    Comercial->>Sistema: Cria Card no Kanban ("Em cotação")
    Comercial->>Sistema: Delega tarefa de cálculo com prazo
    Sistema-->>Assistente: Notifica tarefa na fila de pendências

    %% Operação e Cálculo
    Assistente->>Seguradora: Realiza cálculo nas companhias
    Assistente->>Sistema: Anexa estudos/cálculos e clica em "Entregar Tarefa"
    Sistema-->>Comercial: Notifica entrega do cálculo concluído

    %% Apresentação da Proposta
    Comercial->>Cliente: Apresenta opções e proposta formal
    Comercial->>Sistema: Move card para "Proposta Enviada"

    %% Decisão do Cliente
    alt Cliente Aprova Proposta
        Cliente->>Comercial: Aprova proposta e forma de pagamento
        Comercial->>Sistema: Move card para "Em Emissão"
        Comercial->>Seguradora: Transmite proposta para emissão da apólice
        Seguradora->>Comercial: Emite apólice e número de contrato
        Comercial->>Sistema: Cadastra número da apólice e finaliza card
        Sistema->>Sistema: Registra evento de sucesso em "historico_eventos"
    else Cliente Recusa Proposta
        Cliente->>Comercial: Informa recusa (preço, venda do bem, etc.)
        Comercial->>Sistema: Finaliza card como "Perdido" com motivo obrigatório
        Sistema->>Sistema: Registra perda e motivo para relatório de métricas
    end

    %% Monitoramento Automático de Renovação
    loop Monitoramento Diário
        Sistema->>Sistema: Verifica apólices com menos de 30 dias de vigência
        Sistema->>Sistema: Gera automaticamente card de "Renovação" no quadro do comercial
    end
```

---

## 🔄 2. Máquina de Estados: Card de Seguros

```mermaid
stateDiagram-v2
    [*] --> A_Renovar : Apólice a vencer (30 dias)
    [*] --> Prospeccao : Novo Cliente ou Indicação

    Prospeccao --> Em_Cotacao : Início da análise
    A_Renovar --> Em_Cotacao : Revisão de bônus e vigência

    Em_Cotacao --> Calculados : Assistente conclui cálculos
    Calculados --> Proposta_Enviada : Comercial envia opções

    Proposta_Enviada --> Em_Negociacao : Cliente solicita ajustes
    Em_Negociacao --> Calculados : Recálculo necessário

    Em_Negociacao --> Em_Emissao : Proposta aceita pelo cliente
    Proposta_Enviada --> Em_Emissao : Aceite direto

    Em_Emissao --> Fechado_Emitido : Apólice transmitida e emitida
    
    Em_Cotacao --> Perdido : Recusa / Desistência
    Em_Negociacao --> Perdido : Optou por concorrente
    Proposta_Enviada --> Perdido : Sem retorno do cliente

    Fechado_Emitido --> [*]
    Perdido --> [*]
```

---

## 🛡️ 3. Máquina de Estados: Card de Sinistro

```mermaid
stateDiagram-v2
    [*] --> Aviso_Sinistro : Cliente comunica ocorrência

    Aviso_Sinistro --> Documentacao_Vistoria : Abertura na seguradora e vistoria
    Documentacao_Vistoria --> Em_Analise_Seguradora : Documentos e laudo enviados

    Em_Analise_Seguradora --> Concluido_Indenizado : Indenização ou conserto liberado
    Em_Analise_Seguradora --> Negado_Finalizado : Sinistro recusado pela seguradora

    Concluido_Indenizado --> [*]
    Negado_Finalizado --> [*]
```
