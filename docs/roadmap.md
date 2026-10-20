# Roadmap por etapas

Mapa de evolução acordado em 15 de outubro de 2026; estado revisto em 20 de outubro de 2026 após a integração HTTP administrativa. Não é uma lista de PRs nem um compromisso de calendário. As etapas indicam capacidades e dependências; cada implementação pode ser dividida em alterações pequenas e testáveis.

## Objetivo e limite

Uma aplicação de email marketing com contactos, segmentos, consentimento, campanhas, subscrição e envio controlado. Prioridade a integridade, segurança, manutenção e operação verificável, sem transformar o projeto antecipadamente numa plataforma genérica.

Fora do âmbito por omissão: rastreio de aberturas e cliques, testes A/B, vários clientes isolados na mesma instalação e construtor visual de emails. Qualquer entrada destes temas exige uma decisão nova, requisitos e análise de impacto.

## Visão geral

| Etapa | Capacidade | Estado do código e pendências |
|---|---|---|
| 1 | Contactos e empresas | Integrada; remoção de empresas por decidir na etapa 4 |
| 2 | Segmentos e consentimento | Integrada |
| 3 | Campanhas, envio e subscrição pública | Código integrado, incluindo subscrição pelo PR #15; validação operacional separada |
| 4 | Acesso e dados pessoais | Acesso administrativo integrado pelo PR #27; gestão do ciclo de vida dos dados pendente |
| 5 | Conteúdo e preferências | Planeada, sem implementação confirmada |
| 6 | Entrega e operação | Em preparação; há mecanismos técnicos integrados, mas operação real não demonstrada |
| 7 | Integrações externas | Condicional a casos concretos |

Integração, testes focados, validação operacional e disponibilidade em produção são estados diferentes. Não marcar uma etapa concluída apenas por existir código ou por uma seleção de testes passar.

## Etapa 1 — Contactos e empresas

Existente: contactos, empresas, emails múltiplos com principal, unicidade por endereço, SQLite e PostgreSQL, migrações e ligações por pedido.

Decisões e histórico: [decisoes.md](decisoes.md). Política de remoção de empresas continua por decidir na etapa 4. Transferência SQLite -> PostgreSQL exige avaliação quando necessária, no âmbito operacional da etapa 6.

## Etapa 2 — Segmentos e consentimento

Existente: critérios de segmentação, consentimento independente por finalidade e histórico, evolução dos segmentos e paginação de membros.

Decisões: [decisoes-etapa2.md](decisoes-etapa2.md). Consentimento concedido, sem registo, retirado e endereço suprimido são conceitos distintos.

## Etapa 3 — Campanhas, envio e subscrição

Integrado: campanhas, caixa de saída, processo SMTP, cancelamento por ligação e supressão por endereço (PR #14). A subscrição pública com confirmação por email foi integrada pelo PR #15: ambas as finalidades são opcionais e independentes; textos de sistema separados por idioma.

Histórico: o mapa inicial registava 974 aprovados e 1 ignorado em 72,39 s no commit `83f913e`, ainda na branch de subscrição. O PR #15 registou posteriormente 998 aprovados e 1 ignorado em 79,92 s no commit `a307b2d`, seguido de commits documentais. Estes resultados pertencem às versões e âmbitos indicados; não validam automaticamente alterações posteriores.

A integração do código da subscrição deixou de ser pendência. Continuam separadas as verificações em SMTP/proxy/browser reais, migrações sobre cópia da base real e recuperação de falhas. Consultar as limitações de confirmação e envio antes de exposição pública, incluindo repetição possível de entrega quando SMTP e gravação do estado falham em momentos diferentes.

Decisões: [decisoes-etapa3.md](decisoes-etapa3.md) e [decisoes-subscricao.md](decisoes-subscricao.md).

## Etapa 4 — Acesso e dados pessoais

Objetivo: controlar quem administra o sistema e como entram, saem e são conservados os dados.

### Acesso administrativo integrado

Contas administrativas próprias, hashing de palavras-passe, sessões opacas revogáveis, papéis admin/viewer, gestão local de contas, limitação persistente de tentativas e proteção HTTP foram implementados. O PR #27 integrou a montagem conjunta de login/logout e guard, CSRF nos formulários e contexto validado por pedido, mantendo os mecanismos próprios das rotas públicas e a documentação da API protegida.

Referência de validação reportada pelo utilizador: `e2012d850260c7f5ea74b41adb1d5ef1ea7704ad`, antes do merge, em worktree isolado com Python da `.venv` e imports desse checkout. Seleção focada com PostgreSQL: 999 aprovados em 69,36 s. Inclui os dois testes de regressão do guard, também executados isoladamente em 0,06 s; não são 1001 testes distintos. `git diff --check` desde a base do 3c passou. Não foi a suite completa, nem validação posterior ao merge ou de produção; os testes não foram executados pelo assistente.

Contratos e operação: [decisoes-etapa4.md](decisoes-etapa4.md), [admin-http.md](admin-http.md) e [admin-accounts.md](admin-accounts.md).

### Dados pessoais ainda pendentes

- Importação/exportação com validação, duplicados, permissões e relatório de erros. Não conceder consentimento por existir um contacto importado.
- Remoção ou anonimização de contactos considerando referências de campanhas, histórico de consentimento e supressões.
- Política de retenção para pedidos de subscrição e informação de auditoria; expirar um token não elimina dados.
- Decisão explícita sobre remoção de empresas.

Condição de saída da etapa: acessos negados testados, fluxos de dados verificáveis e política de conservação definida e implementada conforme o âmbito aprovado. A autenticação não encerra esta etapa nem comprova adequação a dados reais.

## Etapa 5 — Conteúdo e preferências

Objetivo: permitir campanhas mais úteis sem duplicar textos nem comprometer consentimento.

- Blocos reutilizáveis definidos pelo utilizador, modelos e traduções. O catálogo JSON atual é uma base para textos de sistema, não um editor de blocos já entregue.
- Definir se uma campanha guarda uma cópia dos blocos ou referências versionadas, para uma edição posterior não alterar conteúdo já enfileirado.
- Edição de rascunhos e agendamento, com regra clara para mensagens já enfileiradas.
- Preferências por finalidade e opção de cancelar todas.
- Mensagens de boas-vindas e, só quando necessário, automatismos simples.

Dependências: etapa 3 e autorização administrativa da etapa 4 para edição. Essa componente de acesso já está integrada, mas cada funcionalidade continua a exigir análise das regras de dados e consentimento. Condição de saída: conteúdo determinístico, escape e marcadores testados, cancelamento sem enfraquecer consentimento ou supressão.

## Etapa 6 — Entrega e operação

Objetivo: transformar um envio testado numa operação recuperável e observável.

Já existem mecanismos como o processo SMTP, endpoint de eventos com validação própria, limites de corpos administrativos, respostas privadas e comando de manutenção limitada das sessões/tentativas. A existência destes mecanismos não demonstra a operação do ambiente real.

Pendências:

- Escolher SMTP/relay e criar adaptador de bounces e queixas assíncronos para `/events/email`; verificar autenticidade e repetições antes de aceitar eventos.
- Se Postfix próprio, definir a ponte DSN; se fornecedor comercial, implementar só a integração escolhida.
- Rever limites globais, concorrência, throttling entre confirmações e campanhas e recuperação após falha parcial.
- Métricas de entrega, diagnóstico e pausa com critérios definidos; não inventar limiares sem a política do fornecedor.
- HTTPS, proxies confiáveis com limites de pedidos/corpo/tempo, segredos e exposição mínima de rotas. Configuração administrativa inválida impede o arranque de toda a aplicação, incluindo rotas públicas.
- Cópias de segurança e restauro exercitado; migrações sobre cópia da base real e transferência SQLite -> PostgreSQL quando necessária.
- Agendamento operacional da limpeza limitada, monitorização de falhas e repetição por lotes. A limpeza de sessões/tentativas não é a política de retenção geral da etapa 4.
- Paginação, índices e desempenho a partir de volume medido.

As proteções necessárias ao primeiro envio real não esperam pelo fim das etapas 4 e 5. Esta etapa pode ser antecipada por partes; a ordem numérica não autoriza deploy sem estas condições.

Condição de saída: teste controlado de entrega, receção autenticada de eventos, falhas e recuperação demonstradas, restauro verificado e limites operacionais definidos.

## Etapa 7 — Integrações externas

Objetivo: ligar sistemas concretos sem transformar a aplicação num CRM genérico.

Possibilidades, não compromissos: API para sistemas autorizados, webhooks de saída, sincronização com CRM e formulários incorporáveis.

Antes de cada integração, definir: fonte de verdade, identidade e permissões, dados mínimos, idempotência, tratamento de falhas/repetições, regras de consentimento e conflito de atualizações. Depende das capacidades relevantes da etapa 4 e de um caso de uso real.

## Como decidir o próximo trabalho

1. Não reabrir como pendentes a subscrição integrada pelo PR #15 ou a integração de acesso do PR #27.
2. Como próximo incremento proposto, definir o ciclo de vida dos dados: inventário, retenção, remoção/anonimização, referências e política de remoção de empresas. Não inventar prazos nem executar eliminações antes de aprovar essas decisões.
3. Implementar depois os casos de uso de dados, em alterações pequenas; não juntar importação/exportação e eliminação num único incremento.
4. Se houver intenção de utilização real, antecipar as condições críticas da etapa 6 antes de exposição ou envio.
5. Introduzir conteúdo e integrações conforme necessidades, não por popularidade de tecnologias. Atualizar o mapa quando uma decisão mudar, separando requisitos, implementação, validação e ideias futuras.
