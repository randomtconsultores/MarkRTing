# Decisões de arquitetura - Etapa 3

Estado: em curso. A etapa 3 divide-se em quatro PRs, pela ordem **1, 2, 4, 3**: (1) campanhas e envio (integrado), (2) cancelar subscrição, (4) lista de supressão e bounces, (3) subscrição pública com confirmação por email (double opt-in).

**Não se envia nenhuma campanha real antes de o PR 4 estar integrado.** Sem tratamento de bounces e queixas, envios em volume prejudicam a reputação e, acima de 5000 mensagens por dia, ficam fora das regras dos grandes fornecedores de caixas de correio.

## Campanhas e envio (PR 1)

Requisito: enviar uma campanha a um segmento, em volume (milhares por dia), respeitando o consentimento, com um servidor SMTP que tanto pode ser um relay comercial como um Postfix próprio.

**O que é novo**

- Migração v5 nos dois backends: `campaigns` e `email_messages` (a caixa de saída).
- `domain/campaign.py`: `Campaign`, `OutboxMessage`, `render`.
- `CampaignRepository` e `PostgresCampaignRepository`; `services/campaign_service.py`.
- `mailer.py` (SMTP e classificação de erros), `delivery.py` (lógica de envio) e `send_worker.py` (processo de envio).
- Web: `/campaigns` (lista e criação) e `/campaigns/{id}` (contagens, enfileirar, mensagens ignoradas e falhadas).

**Decisões**

- O envio nunca acontece dentro de um pedido web. Enfileirar cria as mensagens; um processo separado (`python -m mini_marketo.send_worker`) envia. Com milhares de mensagens por dia, enviar no pedido bloquearia a página e perderia mensagens em qualquer falha.
- Caixa de saída numa tabela, com `UNIQUE (campaign_id, contact_id)`: enfileirar duas vezes, ou o processo repetir uma ronda, nunca envia duas vezes ao mesmo contacto. Enfileirar é transacional: ou a campanha passa a enfileirada e todas as mensagens existem, ou nada muda.
- Consentimento verificado duas vezes: ao enfileirar (decide pendente ou ignorada) e outra vez a cada mensagem, na altura do envio, porque pode ser retirado entretanto. O email guardado na mensagem também tem de continuar a ser um dos emails do contacto; se foi removido, a mensagem é ignorada. O consentimento é do contacto e por finalidade (ver a etapa 2).
- Reclamação atómica de mensagens: cada reclamação conta como uma tentativa, para que um processo que morra a meio não repita para sempre. Em PostgreSQL usa `FOR UPDATE SKIP LOCKED`, seguro com vários processos; em SQLite, cada `UPDATE` só acerta se a mensagem ainda estiver pendente. Reclamações com mais de 15 minutos voltam a pendentes.
- Erros classificados: 4xx é temporário (repete com recuo exponencial: 60 s, 120 s, 240 s, com teto de 1 hora, até ao máximo de tentativas) e 5xx é permanente (não repete). 421 e 454 são limitação do servidor: o processo pára a ronda. Erros de autenticação, remetente ou certificado são fatais: o processo pára e termina com código 2, sem gastar tentativas das mensagens, porque o problema é da configuração e não dos destinatários.
- Paragens de segurança: o teto diário e as falhas consecutivas (10 por omissão) param a ronda e devolvem as mensagens do lote ainda não processadas, sem gastar tentativas.
- Um só emissor SMTP para os dois perfis. Relay e Postfix distinguem-se só por configuração e por trabalho fora da aplicação (SPF, DKIM, DMARC, DNS inverso, saída da sandbox do relay). A assinatura DKIM não se faz na aplicação: quem a faz é o servidor de saída.
- Sem TLS (`none`) só é aceite para um servidor local, para não enviar credenciais em claro por engano. A palavra-passe não aparece em `repr` nem em registos.
- Modelos: só `{first_name}` e `{last_name}` são substituídos, por uma expressão fixa, e no HTML os valores são escapados. Nada do que o utilizador escreve é interpretado como código. O assunto não aceita quebras de linha, e um destinatário com quebras de linha é recusado antes de ligar ao servidor, por isso não há injeção de cabeçalhos.
- A campanha guarda uma cópia do nome do segmento e a referência passa a nula se o segmento for apagado, em vez de o bloquear. As mensagens já enfileiradas não dependem do segmento; um rascunho sem segmento não pode ser enfileirado.
- Os contactos com mensagens na caixa de saída não podem ser apagados (`ON DELETE RESTRICT`), como já acontece com o consentimento.

## Cancelar subscrição (PR 2)

Requisito: cada email leva uma ligação para cancelar a subscrição, e os clientes de correio que a suportam mostram um botão de cancelamento com um clique.

**O que é novo**

- Migração v6: a origem `unsubscribe` passa a ser válida em `consent_events`.
- `unsubscribe.py` (`UnsubscribeLinks`: tokens e URLs), `services/unsubscribe_service.py`, `templates/unsubscribe.html`.
- Rotas públicas `GET` e `POST /unsubscribe/{token}`.
- O processo de envio acrescenta a cada mensagem os cabeçalhos `List-Unsubscribe` e `List-Unsubscribe-Post` e uma ligação no rodapé do texto e do HTML.

**Decisões**

- Token = id da mensagem + assinatura HMAC-SHA256 (128 bits) com uma chave secreta (`MINI_MARKETO_SECRET_KEY`, pelo menos 32 caracteres). Sem chave não se forja uma ligação. Alternativa rejeitada: tokens aleatórios guardados na base de dados, que obrigariam a guardar o token em claro (para o pôr no email) ou a gerar um novo a cada tentativa, deixando sem efeito a ligação de um email já entregue.
- Rotação da chave: `MINI_MARKETO_SECRET_KEY_PREVIOUS` aceita chaves antigas (separadas por vírgulas) só para verificar. Sem isto, rodar a chave inutilizaria as ligações de todos os emails já enviados, e as pessoas deixariam de conseguir cancelar.
- O `GET` só mostra uma página de confirmação. Quem faz `GET` (verificadores de ligações de segurança, pré-visualizações) não cancela ninguém por engano. Só o `POST` cancela.
- O `POST` responde 200 sem redirecionar e não exige campos: o cancelamento com um clique do RFC 8058 envia `List-Unsubscribe=One-Click` e não segue redirecionamentos; o formulário da página não envia nada.
- Efeito: retira o consentimento da finalidade da campanha (só dessa), com origem `unsubscribe` e o email da mensagem. É idempotente: se o estado já é «retirado», não acrescenta evento. Aqui não se aplica a regra da etapa 2 (um registo repetido acrescenta sempre um evento novo), porque o endereço é público e é chamado por robôs e repetições, que encheriam o histórico. Duas retiradas simultâneas podem gravar dois eventos iguais; é inofensivo.
- A elegibilidade verifica-se na altura do envio: quem cancela depois de enfileirar deixa de receber as mensagens ainda pendentes, e fica ignorado nas campanhas seguintes.
- Token inválido, mensagem inexistente e ligações por configurar dão o mesmo 404 genérico, para a resposta não dizer o que falhou. O email é mostrado mascarado (`a***@dominio`).
- As respostas levam `Referrer-Policy: no-referrer` e `Cache-Control: no-store`, porque o token está no URL. A página é autónoma, sem o menu de administração, e leva `noindex`. Sem proteção contra abuso no próprio endpoint: o token é a autorização e não há sessão nem cookies, por isso não há CSRF a tratar.
- O processo de envio recusa arrancar sem `MINI_MARKETO_PUBLIC_URL` (https; http só para servidor local) e sem chave, e `DeliveryWorker` não se constrói sem ligações: não se envia nenhuma mensagem sem cancelamento.
- Migração v6: no PostgreSQL, trocar a restrição `consent_events_source_check` (falha se o nome não existir, em vez de deixar a antiga ativa). No SQLite, que não altera restrições `CHECK`, a tabela é reconstruída: cria-se a nova, copiam-se os eventos por ordem de id (o estado atual depende do id), apaga-se a antiga, renomeia-se e recria-se o índice. Um teste confirma que todos os eventos e ids se mantêm, nos dois backends.

**Aviso de operação: a aplicação não tem autenticação.** Em produção, só `/unsubscribe/*` e `/static/*` devem estar acessíveis publicamente. Contactos, segmentos, campanhas e consentimento têm de ficar atrás de uma rede privada ou de um proxy com autenticação. Um proxy que só encaminhe esses dois prefixos resolve-o.

**Configuração (variáveis de ambiente)**

| Variável | Significado | Omissão |
|---|---|---|
| `MINI_MARKETO_SMTP_HOST` | servidor SMTP (obrigatória para enviar) | - |
| `MINI_MARKETO_SMTP_SENDER` | remetente, por exemplo `Nome <no-reply@dominio.pt>` (obrigatória para enviar) | - |
| `MINI_MARKETO_SMTP_SECURITY` | `starttls`, `ssl` ou `none` (só local) | `starttls` |
| `MINI_MARKETO_SMTP_PORT` | porta | 587, 465 ou 25 conforme o modo |
| `MINI_MARKETO_SMTP_USERNAME` e `MINI_MARKETO_SMTP_PASSWORD` | credenciais, definidas em conjunto | vazias |
| `MINI_MARKETO_SMTP_TIMEOUT` | segundos | 30 |
| `MINI_MARKETO_SMTP_RATE` | mensagens por segundo | 1 |
| `MINI_MARKETO_SMTP_DAILY_CAP` | teto de envios por dia UTC | 200 |
| `MINI_MARKETO_SMTP_MAX_ATTEMPTS` | tentativas por mensagem | 5 |
| `MINI_MARKETO_SMTP_BATCH_SIZE` | mensagens reclamadas por lote | 50 |
| `MINI_MARKETO_PUBLIC_URL` | URL público da aplicação, para as ligações de cancelamento (obrigatória para enviar e para a página de cancelamento) | - |
| `MINI_MARKETO_SECRET_KEY` | chave dos tokens, mínimo 32 caracteres (obrigatória, como a anterior) | - |
| `MINI_MARKETO_SECRET_KEY_PREVIOUS` | chaves antigas, separadas por vírgulas, só para verificar | vazia |

A base de dados é a mesma da aplicação (`MINI_MARKETO_DB` ou `MINI_MARKETO_DATABASE_URL`). A aplicação web e o processo de envio têm de partilhar o mesmo `MINI_MARKETO_PUBLIC_URL` e a mesma chave. Gerar uma chave: `python -c "import secrets; print(secrets.token_urlsafe(48))"`. Nunca pôr a chave em código, testes ou documentação.

**Operação**

- Uma ronda: `python -m mini_marketo.send_worker`. Em contínuo: `python -m mini_marketo.send_worker --loop --poll-seconds 30`.
- O teto diário serve também para aquecer a reputação de um domínio ou IP novo: começa-se baixo e sobe-se manualmente, de semana a semana, enquanto os bounces e as queixas se mantêm baixos. Os valores de referência vistos em guias de entrega (cerca de 20 a 30% de aumento por semana; parar o aumento com bounces acima de 2% ou queixas acima de 0,1%) são indicativos e devem ser confirmados com o fornecedor escolhido.
- Relay comercial: uma conta nova costuma começar limitada (na sandbox do Amazon SES, por exemplo, só se envia para destinatários verificados e com um teto diário baixo); é preciso pedir acesso de produção. Postfix próprio: SPF, DKIM, DMARC e DNS inverso do IP têm de estar configurados antes do primeiro envio. A assinatura DKIM tem de cobrir os cabeçalhos `List-Unsubscribe` e `List-Unsubscribe-Post` para o cancelamento com um clique ser aceite.
- Com SQLite, os escritores serializam-se; um só processo de envio é o recomendado. Com PostgreSQL podem correr vários.

**Limitações conhecidas**

- Ainda sem lista de supressão nem tratamento de bounces e queixas assíncronas (PR 4). Um 5xx recusado no momento do envio já é permanente, mas não impede campanhas futuras para o mesmo endereço.
- O cancelamento retira só a finalidade da campanha; não há «cancelar tudo» nem página de preferências.
- Sem edição, remoção nem agendamento de campanhas; enfileirar é definitivo.
- Enfileirar lê todos os membros do segmento e todo o histórico de consentimento em memória, como as listagens de segmentos. Reavaliar com volume medido.
- A paragem «limite_diario» também aparece quando o teto é atingido exatamente ao esgotar a fila.
- Não se guarda a resposta completa do servidor, só o último erro (500 caracteres).
- Modelos só com dois marcadores.
- O endpoint público não tem limite de pedidos; a proteção é o token. Um proxy pode acrescentar o limite.

**Validação**

- PR 1: suíte completa com SQLite e PostgreSQL ativos, 579 aprovados em 53,79 s (antes da etapa: 414), e integrado. Nenhum email real foi enviado.
- PR 2: pendente. Os testes estão escritos (migração v6 e preservação de dados nos dois backends, tokens, serviço, rotas públicas, cabeçalhos e rodapé no envio, arranque protegido) mas ainda não foram executados.
- Não verificado até agora: o envio contra um servidor SMTP real, a aceitação do cancelamento com um clique por um cliente de correio real, e as páginas no browser.
