# Decisões de arquitetura - Etapa 3

Estado: em curso. A etapa 3 divide-se em quatro PRs, pela ordem **1, 2, 4, 3**: (1) campanhas e envio, (2) cancelar subscrição, (4) lista de supressão e bounces, (3) subscrição pública com confirmação por email (double opt-in). Este documento regista, para já, só o primeiro.

**Não se envia nenhuma campanha real antes de os PRs 2 e 4 estarem integrados.** Sem ligação para cancelar a subscrição nem tratamento de bounces, envios em volume prejudicam a reputação e, acima de 5000 mensagens por dia, ficam fora das regras dos grandes fornecedores de caixas de correio.

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

**Configuração (variáveis de ambiente)**

| Variável | Significado | Omissão |
|---|---|---|
| `MINI_MARKETO_SMTP_HOST` | servidor SMTP (obrigatória) | - |
| `MINI_MARKETO_SMTP_SENDER` | remetente, por exemplo `Nome <no-reply@dominio.pt>` (obrigatória) | - |
| `MINI_MARKETO_SMTP_SECURITY` | `starttls`, `ssl` ou `none` (só local) | `starttls` |
| `MINI_MARKETO_SMTP_PORT` | porta | 587, 465 ou 25 conforme o modo |
| `MINI_MARKETO_SMTP_USERNAME` e `MINI_MARKETO_SMTP_PASSWORD` | credenciais, definidas em conjunto | vazias |
| `MINI_MARKETO_SMTP_TIMEOUT` | segundos | 30 |
| `MINI_MARKETO_SMTP_RATE` | mensagens por segundo | 1 |
| `MINI_MARKETO_SMTP_DAILY_CAP` | teto de envios por dia UTC | 200 |
| `MINI_MARKETO_SMTP_MAX_ATTEMPTS` | tentativas por mensagem | 5 |
| `MINI_MARKETO_SMTP_BATCH_SIZE` | mensagens reclamadas por lote | 50 |

A base de dados é a mesma da aplicação (`MINI_MARKETO_DB` ou `MINI_MARKETO_DATABASE_URL`).

**Operação**

- Uma ronda: `python -m mini_marketo.send_worker`. Em contínuo: `python -m mini_marketo.send_worker --loop --poll-seconds 30`.
- O teto diário serve também para aquecer a reputação de um domínio ou IP novo: começa-se baixo e sobe-se manualmente, de semana a semana, enquanto os bounces e as queixas se mantêm baixos. Os valores de referência vistos em guias de entrega (cerca de 20 a 30% de aumento por semana; parar o aumento com bounces acima de 2% ou queixas acima de 0,1%) são indicativos e devem ser confirmados com o fornecedor escolhido.
- Relay comercial: uma conta nova costuma começar limitada (na sandbox do Amazon SES, por exemplo, só se envia para destinatários verificados e com um teto diário baixo); é preciso pedir acesso de produção. Postfix próprio: SPF, DKIM, DMARC e DNS inverso do IP têm de estar configurados antes do primeiro envio.
- Com SQLite, os escritores serializam-se; um só processo de envio é o recomendado. Com PostgreSQL podem correr vários.

**Limitações conhecidas**

- Ainda sem ligação para cancelar a subscrição nem cabeçalhos `List-Unsubscribe` (PR 2).
- Ainda sem lista de supressão nem tratamento de bounces e queixas assíncronas (PR 4). Um 5xx recusado no momento do envio já é permanente, mas não impede campanhas futuras para o mesmo endereço.
- Sem edição, remoção nem agendamento de campanhas; enfileirar é definitivo.
- Enfileirar lê todos os membros do segmento e todo o histórico de consentimento em memória, como as listagens de segmentos. Reavaliar com volume medido.
- A paragem «limite_diario» também aparece quando o teto é atingido exatamente ao esgotar a fila.
- Não se guarda a resposta completa do servidor, só o último erro (500 caracteres).
- Modelos só com dois marcadores.

**Validação**

Pendente: os testes estão escritos (domínio, persistência nos dois backends, emissor com um servidor SMTP falso, entrega e web) mas ainda não foram executados. Nenhum teste envia email a sério. Não foi enviado nenhum email real a partir desta aplicação.
