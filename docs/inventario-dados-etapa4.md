# Etapa 4 — Inventário de dados, referências e fluxos

## Estado, base e limites

Inventário técnico estático preparado em 20 de outubro de 2026 sobre `b07c3bac36d1126768cd29f29ae22479c7758b61`, base do ramo `docs/etapa4-ciclo-vida-dados` após o merge do PR #28. Este é o primeiro commit documental do PR de ciclo de vida dos dados.

Regista o que existe no esquema e nos serviços consultados; não aprova políticas de importação/exportação, eliminação, anonimização ou retenção. Não altera código, migrações, restrições ou dados. Nenhuma base real foi consultada e nenhum teste foi executado nesta preparação. Não é um inventário de dados efetivamente presentes numa instalação, nem uma conclusão de conformidade jurídica ou de anonimização.

Os esquemas consultados chegam à versão 10 e declaram 13 tabelas funcionais, incluindo as três de acesso administrativo. PostgreSQL acrescenta metadados em `schema_version`; SQLite usa `PRAGMA user_version`. Isto não é uma contagem de tabelas de uma base em execução. Migrações antigas permanecem históricas; o estado pretendido resulta da sequência completa.

## Fontes conferidas

- [database.py](../src/mini_marketo/database.py): esquema e migrações SQLite, ativação de foreign keys e inicialização transacional.
- [postgres_database.py](../src/mini_marketo/postgres_database.py): esquema PostgreSQL, versões e migrações transacionais.
- [access_schema.py](../src/mini_marketo/access_schema.py): tabelas administrativas v9/v10 e restrições.
- [contact_service.py](../src/mini_marketo/services/contact_service.py), [consent_service.py](../src/mini_marketo/services/consent_service.py) e [segment_service.py](../src/mini_marketo/services/segment_service.py).
- [campaign_service.py](../src/mini_marketo/services/campaign_service.py) e [delivery.py](../src/mini_marketo/delivery.py).
- [subscription_service.py](../src/mini_marketo/services/subscription_service.py), [confirmation_service.py](../src/mini_marketo/services/confirmation_service.py) e [confirmation_repository.py](../src/mini_marketo/repositories/confirmation_repository.py).
- [suppression_service.py](../src/mini_marketo/services/suppression_service.py), [unsubscribe_service.py](../src/mini_marketo/services/unsubscribe_service.py) e [subscription_delivery.py](../src/mini_marketo/subscription_delivery.py).

A revisão dos repositórios não foi exaustiva: foi lida a escrita composta de confirmação, além dos esquemas e serviços enumerados. As referências abaixo são as declaradas no DDL; não foi executado SQL para testar integridade, concorrência ou efeitos de DELETE. O controlo de acesso já integrado é descrito em [admin-http.md](admin-http.md); as pendências da etapa constam do [roadmap](roadmap.md).

## 1. Inventário das tabelas de marketing

| Tabela | Dados e finalidade observados | Identificadores, cópias e observações |
| --- | --- | --- |
| `companies` | `id`, nome e website; associação organizacional dos contactos | Nome e website são texto e podem conter dados identificativos; a aplicação não determina por si só se cada empresa representa uma pessoa |
| `contacts` | `id`, nome, apelido, `company_id` opcional e criação | O email atual não está nesta tabela; é obtido de `contact_emails` |
| `contact_emails` | `id`, `contact_id`, email e indicador de principal | Email único globalmente e normalizado segundo o contrato existente; índice único parcial permite no máximo um principal por contacto, não garante sozinho que todo o contacto tenha um |
| `segments` | Nome, descrição, modo all/any, critérios serializados e criação | Critérios, nomes e descrições podem conter emails ou outros valores pessoais; membros são calculados com contactos/consentimentos atuais, não guardados numa tabela de pertença no esquema consultado |
| `consent_events` | Contacto, finalidade newsletter/promotional, decisão granted/withdrawn, origem form/admin/unsubscribe, email e momento | O email é uma cópia do endereço usado no evento. O histórico e o estado atual não se reduzem à lista atual de emails do contacto |
| `campaigns` | Nome, assunto, corpos texto/HTML, finalidade, segmento opcional, cópia do nome do segmento, estado e datas | Conteúdo livre pode conter dados pessoais. Guarda conteúdo/modelo da campanha; não existe coluna de corpo renderizado por destinatário no DDL de `email_messages` |
| `email_messages` | Campanha, contacto, cópia do destinatário, estado, tentativas, agendamento, reclamação, erro/motivo e datas de criação/envio | Unicidade por campanha/contacto. `email` conserva o destinatário escolhido ao enfileirar; mensagens históricas não passam a referir automaticamente o email principal atual |
| `suppressions` | Email único, motivo hard_bounce/complaint/manual, origem smtp/event/admin, detalhe e criação | Lista independente por endereço, sem FK para contacto; detalhe é texto livre e pode conter informação recebida do operador ou do servidor |
| `subscription_requests` | Email, nome, apelido, idioma, criação, estado/tentativas/agendamento/reclamação/erro de entrega, envio e confirmação | Cópia dos dados fornecidos no pedido público, mesmo antes de existir contacto. Não tem coluna `contact_id` nem FK para `contacts` no esquema consultado |
| `subscription_request_purposes` | Pedido e finalidades escolhidas, com chave composta | Pedir uma finalidade não é, por si só, conceder consentimento; a confirmação trata a criação dos eventos |

Textos livres e diagnósticos também entram na análise do ciclo de vida: não basta procurar apenas colunas chamadas `email`, `first_name` ou `last_name`. Este documento assinala a possibilidade de conterem dados pessoais, sem afirmar que foram inspecionados valores reais.

## 2. Acesso administrativo: dados separados dos contactos

| Tabela | Dados observados | Limites e relação com marketing |
| --- | --- | --- |
| `admin_users` | Username, hash da palavra-passe, papel, estado ativo, versão de credenciais e criação | Contas de administração separadas dos contactos; não há relação automática username-contacto |
| `admin_sessions` | Digest do token, utilizador, versão de credenciais, criação, última atividade e expiração | O DDL recebe `token_hash`, não o token bruto. Digest não deve ser tratado como prova de anonimização nem incluído numa exportação operacional de contactos |
| `admin_login_attempts` | Hash do orçamento, início/fim de janela e contador | Esquema guarda `bucket_hash`, não username/IP em claro. A política do limitador e o segredo HMAC são distintos dos dados dos contactos |

Chaves de CSRF/HMAC e credenciais SMTP/DSNs pertencem à configuração operacional; não são campos das tabelas de marketing enumeradas. O inventário não autoriza exportar segredos, hashes de credenciais ou dados de sessão. Não se identificou neste DDL uma tabela geral de auditoria de todas as operações; não presumir que esse histórico já existe.

## 3. Referências e efeitos declarados no esquema

As ações seguintes são comportamento de integridade referencial declarado, não operações de remoção aprovadas nem indicação para executar DELETE manualmente.

| Referência | Ação declarada ao eliminar o registo referenciado | Consequência para o planeamento |
| --- | --- | --- |
| `contacts.company_id -> companies.id` | `ON DELETE SET NULL` | A base já prevê desassociação se uma empresa for eliminada; ainda é preciso decidir a operação de negócio, autorização e confirmação |
| `contact_emails.contact_id -> contacts.id` | `ON DELETE CASCADE` | Eliminar um contacto pode eliminar os seus emails atuais, mas não apaga as cópias existentes noutras tabelas |
| `consent_events.contact_id -> contacts.id` | `ON DELETE RESTRICT` | Histórico de consentimento pode impedir eliminação física do contacto |
| `email_messages.contact_id -> contacts.id` | `ON DELETE RESTRICT` | Mensagens podem impedir eliminação física do contacto |
| `email_messages.campaign_id -> campaigns.id` | `ON DELETE RESTRICT` | Campanhas com mensagens têm referências a preservar ou tratar explicitamente |
| `campaigns.segment_id -> segments.id` | `ON DELETE SET NULL` | A campanha conserva `segment_name`; remover segmento não elimina campanhas/mensagens |
| `subscription_request_purposes.request_id -> subscription_requests.id` | `ON DELETE CASCADE` | Eliminar um pedido elimina as suas finalidades, mas não revoga eventos de consentimento já criados |
| `admin_sessions.user_id -> admin_users.id` | `ON DELETE CASCADE` | Relação apenas administrativa; não é remoção de contacto |

`suppressions` e `subscription_requests` relacionam-se funcionalmente com emails, mas não são dependências FK de `contacts`. Não existe ação em cascata que trate essas cópias por eliminar o contacto. Igualdade textual de email não prova, por si só, identidade histórica: um endereço pode mudar de associação ao longo do tempo.

SQLite armazena os instantes de marketing como texto e usa inteiros nos indicadores de email principal; PostgreSQL usa TIMESTAMPTZ e BOOLEAN nesse indicador. Timestamps administrativos são segundos Unix validados pelo contrato. Seleção por prazo e testes de migração terão de respeitar essas representações e os dois motores.

## 4. Fluxos existentes

### 4.1 Entrada administrativa de contactos e empresas

`ContactService.create_contact` valida nomes, endereço e empresa existente e delega a criação no repositório. A criação não chama o serviço de consentimento. Há adição de emails, troca de principal e remoção de email secundário; o serviço recusa remover o principal sem escolher primeiro outro. Não existe método de remoção de contacto ou empresa nesse serviço consultado.

Adicionar ou remover um email atual não elimina os snapshots de endereço em consentimentos, mensagens ou pedidos públicos. A ligação entre esses elementos tem de entrar no contrato futuro de remoção/anonimização.

### 4.2 Pedido público e confirmação

`SubscriptionService` valida e regista pedidos limitados, consultando supressões antes de inserir. Não cria contactos, não concede consentimento e não envia email nessa chamada. Pedidos válidos suprimidos/limitados recebem resposta pública genérica segundo o contrato existente; não presumir que todos geram uma linha persistida.

`ConfirmationRepository.confirm` coordena numa transação a procura/criação do contacto/email, eventos de consentimento aplicáveis e `confirmed_at`. Se o email já existe, reutiliza o contacto, sem promover automaticamente esse endereço a principal. Para cada finalidade escolhida, insere granted quando o último evento ainda não é granted. Pedido confirmado é devolvido sem voltar a conceder consentimento; não é apagado pela confirmação.

A confirmação usa o endereço do pedido como associação de trabalho, mas não grava um vínculo pedido-contacto no esquema. Uma futura procura de todos os pedidos de uma pessoa exige um contrato explícito; não se pode garantir abrangência ou identidade apenas seguindo FKs.

### 4.3 Consentimento e cancelamento

`ConsentService.record` usa o email principal observado ao registar a decisão administrativa. Confirmação usa o endereço do pedido; cancelamento por ligação usa o endereço copiado na mensagem. Assim, eventos do mesmo contacto podem conter endereços diferentes do principal atual.

`ConsentService.history` ordena por id decrescente e o estado é calculado por finalidade. Não há, nesse serviço, edição ou apagamento de eventos. Isso descreve a implementação, não uma decisão jurídica de conservação ilimitada.

`UnsubscribeService` resolve a mensagem e a campanha pela ligação, consulta a finalidade e acrescenta withdrawn se necessário. O GET só mostra confirmação; o POST retira consentimento. O serviço não elimina contacto, mensagens ou pedido de subscrição e não equivale a uma operação de apagamento de dados.

### 4.4 Segmentação, enfileiramento e envio

Segmentos são dinâmicos: os membros são calculados a partir de contactos e, quando aplicável, consentimentos atuais. Eliminar um segmento não elimina mensagens já criadas; uma campanha draft cujo segmento deixou de existir não pode ser enfileirada por `CampaignService`.

Ao enfileirar, `CampaignService` copia o email principal do membro para `email_messages` e cria pending ou skipped conforme consentimento/supressão. O envio não acontece no pedido web.

`DeliveryWorker` volta a carregar contacto, pertença do endereço à lista atual de emails, supressão e consentimento antes de chamar SMTP. Renderiza assunto/corpos com dados do contacto consultado nesse momento, enviando para a cópia de endereço da mensagem e incluindo ligação de cancelamento. Não presumir que todos os valores renderizados são snapshots imutáveis do enfileiramento.

`ConfirmationSender` envia nome/finalidades e ligação a partir do pedido, revalida supressão e expiração antes do SMTP e pode registar erros de entrega. Ambos os fluxos podem promover um endereço rejeitado para hard_bounce. SMTP e a gravação posterior do estado são operações distintas; este inventário não demonstra atomicidade entre aplicação e servidor externo.

### 4.5 Supressão

`SuppressionService` trabalha por endereço, independentemente de contacto existente. Adição é idempotente segundo o contrato do repositório. Apenas supressões manuais podem ser removidas pelo serviço; a escrita revalida o motivo para proteger contra promoção concorrente. A interface não permite remover hard_bounce/complaint.

Apagar um contacto e apagar uma supressão são operações diferentes. Uma futura política de dados deve explicar como preserva o impedimento de envio sem prometer anonimização apenas por converter ou conservar um identificador.

## 5. Conservação e superfícies fora da base

A expiração de um pedido de confirmação impede a utilização de um pedido não confirmado segundo os validadores, mas não elimina automaticamente a linha. A janela de limitação de subscrição também depende do histórico de pedidos. Remover linhas pode alterar os orçamentos e a reutilização das ligações; não tratar a limpeza como operação sem efeito funcional.

Já existe manutenção administrativa limitada de sessões/tentativas descrita em `admin-http.md`; isso não é retenção geral de marketing. Não foi aprovada neste inventário qualquer limpeza automática de contactos, pedidos, consentimentos, campanhas ou supressões.

Dados já entregues por SMTP, mailboxes de destinatários, logs de aplicação/servidor/proxy, relatórios operacionais e backups podem formar cópias adicionais. A existência e conservação efetivas dessas cópias dependem do deploy e não foram inspecionadas. A limpeza da base não é prova de eliminação dessas superfícies. Exportações futuras e relatórios de importação também terão de entrar no inventário quando existirem.

Não foram verificadas contas de fornecedor, instalações, ficheiros de base, backups, mensagens reais, acessos do operador ou conteúdo de logs. Não se assume rastreio de aberturas/cliques ou uma plataforma genérica de auditoria.

## 6. Questões para o segundo commit e PRs seguintes

1. Qual o âmbito de cada exportação e que identidade/associações a delimitam? Que dados e papéis ficam excluídos?
2. Que contrato de importação suporta emails múltiplos, empresas e duplicados sem consentimento implícito ou alteração indevida de supressões?
3. Remover empresa deve ser recusado enquanto houver contactos ou desassociar explicitamente? O SET NULL do esquema não decide a experiência de utilização.
4. Para cada categoria, qual a decisão aprovada: conservar, remover ou anonimizar? Que prazos, eventos iniciais e exceções se justificam?
5. Como tratar os snapshots de email, texto livre, contactos sem emails atuais e associação histórica de pedidos sem FK?
6. Que acontece a mensagens pending/sending e a confirmação/cancelamento durante remoção ou limpeza? É necessário definir concorrência com o worker antes de prometer ausência de novos envios.
7. Como preservar o histórico e a supressão necessários sem transformar esse requisito técnico numa afirmação de conservação ilimitada ou de anonimização?
8. Que dados operacionais/backup existem no ambiente e como se documenta o tratamento de restauros e de cópias externas?

Este commit não resolve essas escolhas nem introduz permissões, rotas ou comandos. O segundo commit deverá separar contratos propostos, alternativas e decisões aprovadas; não executar eliminações ou fixar prazos por inferência.
