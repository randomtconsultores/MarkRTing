# Roadmap por etapas

Mapa de evolução acordado em 15 de outubro de 2026. Não é uma lista de PRs nem um compromisso de calendário. As etapas indicam capacidades e dependências; cada implementação pode ser dividida em alterações pequenas e testáveis.

## Objetivo e limite

Uma aplicação de email marketing com contactos, segmentos, consentimento, campanhas, subscrição e envio controlado. Prioridade a integridade, segurança, manutenção e operação verificável, sem transformar o projeto antecipadamente numa plataforma genérica.

Fora do âmbito por omissão: rastreio de aberturas e cliques, testes A/B, vários clientes isolados na mesma instalação e construtor visual de emails. Qualquer entrada destes temas exige uma decisão nova, requisitos e análise de impacto.

## Visão geral

| Etapa | Capacidade | Estado |
|---|---|---|
| 1 | Contactos e empresas | Integrada |
| 2 | Segmentos e consentimento | Integrada |
| 3 | Campanhas, envio e subscrição pública | Em fecho; subscrição na branch, ainda por integrar |
| 4 | Acesso e dados pessoais | Planeada, sem implementação confirmada |
| 5 | Conteúdo e preferências | Planeada, sem implementação confirmada |
| 6 | Entrega e operação | Planeada; ações críticas antecipadas antes de produção |
| 7 | Integrações externas | Condicional a casos concretos |

## Etapa 1 — Contactos e empresas

Existente: contactos, empresas, emails múltiplos com principal, unicidade por endereço, SQLite e PostgreSQL, migrações e ligações por pedido.

Decisões e histórico: [decisoes.md](decisoes.md). Política de remoção de empresas e transferência SQLite -> PostgreSQL continuam a precisar de avaliação nos trabalhos seguintes.

## Etapa 2 — Segmentos e consentimento

Existente: critérios de segmentação, consentimento independente por finalidade e histórico, evolução dos segmentos e paginação de membros.

Decisões: [decisoes-etapa2.md](decisoes-etapa2.md). Consentimento concedido, sem registo, retirado e endereço suprimido são conceitos distintos.

## Etapa 3 — Campanhas, envio e subscrição

Integrado: campanhas, caixa de saída, processo SMTP, cancelamento por ligação e supressão por endereço. O PR GitHub #14 integrou a supressão.

Na branch `feat/etapa3-subscricao`: pedido público, confirmação por email, ambas as finalidades opcionais e independentes, textos separados por idioma. Execução reportada em `83f913e`: 974 aprovados e 1 ignorado em 72,39 s.

Para encerrar: documentação, revisão das limitações, pull request e integração da subscrição. Validação em SMTP/proxy/browser reais é separada da aprovação dos testes.

Decisões: [decisoes-etapa3.md](decisoes-etapa3.md) e [decisoes-subscricao.md](decisoes-subscricao.md).

## Etapa 4 — Acesso e dados pessoais

Objetivo: controlar quem administra o sistema e como entram, saem e são conservados os dados.

- Autenticação e autorização por papéis, proteção de sessões e operações administrativas; escolher mecanismo conforme o deploy, sem assumir já um fornecedor de identidade.
- Importação/exportação com validação, duplicados e relatório de erros. Não conceder consentimento por existir um contacto importado.
- Remoção ou anonimização de contactos considerando referências de campanhas, histórico de consentimento e supressões.
- Política de retenção para pedidos de subscrição e informação de auditoria; expirar um token não elimina dados.
- Decisão explícita sobre remoção de empresas.

Condição de saída: acessos negados testados, fluxos de dados verificáveis e política de conservação definida. Até lá, administração protegida por rede privada ou proxy autenticado e sem assumir adequação a dados reais apenas por os testes passarem.

## Etapa 5 — Conteúdo e preferências

Objetivo: permitir campanhas mais úteis sem duplicar textos nem comprometer consentimento.

- Blocos reutilizáveis definidos pelo utilizador, modelos e traduções. O catálogo JSON atual é uma base para textos de sistema, não um editor de blocos já entregue.
- Definir se uma campanha guarda uma cópia dos blocos ou referências versionadas, para uma edição posterior não alterar conteúdo já enfileirado.
- Edição de rascunhos e agendamento, com regra clara para mensagens já enfileiradas.
- Preferências por finalidade e opção de cancelar todas.
- Mensagens de boas-vindas e, só quando necessário, automatismos simples.

Dependências: etapa 3 e autorização da etapa 4 para edição. Condição de saída: conteúdo determinístico, escape e marcadores testados, cancelamento sem enfraquecer consentimento ou supressão.

## Etapa 6 — Entrega e operação

Objetivo: transformar um envio testado numa operação recuperável e observável.

- Escolher SMTP/relay e criar adaptador de bounces e queixas assíncronos para `/events/email`; verificar autenticidade e repetições dos eventos antes de os aceitar.
- Se Postfix próprio, definir a ponte DSN; se fornecedor comercial, implementar só a integração escolhida.
- Rever limites globais, concorrência, throttling entre confirmações e campanhas e recuperação após falha parcial.
- Métricas de entrega, diagnóstico e pausa com critérios definidos; não inventar limiares sem a política do fornecedor.
- HTTPS, proxy com limites de pedidos/corpo/tempo, segredos e exposição mínima de rotas.
- Cópias de segurança e restauro exercitado; migrações sobre cópia da base real e transferência SQLite -> PostgreSQL quando necessária.
- Paginação, índices e desempenho a partir de volume medido.

As proteções necessárias ao primeiro envio real não esperam pelo fim das etapas 4 e 5. Esta etapa pode ser antecipada por partes; a ordem numérica não autoriza deploy sem estas condições.

Condição de saída: teste controlado de entrega, receção autenticada de eventos, falhas e recuperação demonstradas, restauro verificado e limites operacionais definidos.

## Etapa 7 — Integrações externas

Objetivo: ligar sistemas concretos sem transformar a aplicação num CRM genérico.

Possibilidades, não compromissos: API para sistemas autorizados, webhooks de saída, sincronização com CRM e formulários incorporáveis.

Antes de cada integração, definir: fonte de verdade, identidade e permissões, dados mínimos, idempotência, tratamento de falhas/repetições, regras de consentimento e conflito de atualizações. Depende da etapa 4 e de um caso de uso real.

## Como decidir o próximo trabalho

1. Fechar a etapa 3 sem confundir código existente com integração concluída.
2. Rever as limitações documentadas da confirmação e do envio antes da exposição pública.
3. Priorizar etapa 4 para acesso e dados pessoais; antecipar proteções da etapa 6 para qualquer utilização real.
4. Introduzir conteúdo e integrações conforme necessidades, não por popularidade de tecnologias.
5. Atualizar este mapa quando uma decisão mudar; manter requisitos, implementação, validação e ideias futuras separados.
