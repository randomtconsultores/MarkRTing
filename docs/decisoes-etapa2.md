# Decisões de arquitetura - Etapa 2

## Segmentação dinâmica

Requisito: agrupar contactos por regras (por exemplo, «da empresa X» ou «criados depois de uma data») e ver quem cumpre cada regra em cada momento.

**O que é novo**

- `domain/segment.py`: `Criterion` (campo, operador, valor) e `Segment` (nome, descrição, modo, critérios). Ambos validam-se ao serem construídos e avaliam um `Contact` sem tocar na base de dados.
- Migração v3: a primeira migração real do mecanismo da Experiência 1, com a tabela `segments` nos dois backends.
- `repositories/segment_json.py`, `SegmentRepository` e `PostgresSegmentRepository`; `services/segment_service.py`.
- Web: `/segments` (listagem, contagem de membros e criação), `/segments/{id}` (critérios e membros), `/segments/{id}/edit` (edição) e `/segments/{id}/delete`.

**Decisões**

- Segmentos dinâmicos: guarda-se a regra, não a lista de membros. Os membros calculam-se a partir dos contactos atuais, por isso um contacto novo, ou um email acrescentado, entra no segmento sem mais passos. Alternativa rejeitada: tabela de associações, que ficaria desatualizada a cada alteração de contacto e exigiria recalcular.
- Avaliação em Python, no domínio, e não traduzida para SQL. Uma só implementação para os dois backends, com a mesma semantica de maiúsculas e ordenação, e testes sem base de dados. Custo: a contagem carrega todos os contactos (uma vez por listagem). Reavaliar quando houver milhares de contactos medidos.
- Campos e operadores fechados: cada campo aceita um conjunto fixo de operadores (texto: é, não é, contém, começa por; empresa acrescenta vazio e não vazio; data: antes de e depois de). Um critério inválido não chega a existir. Sem expressões livres: nada é interpretado como código ou SQL.
- Email com vários valores: os operadores positivos cumprem-se se qualquer email cumprir. «não é» só se cumpre se nenhum email for igual. Assim «não é x» é sempre o oposto de «é x».
- Datas: compara-se o dia em UTC, no formato AAAA-MM-DD, coerente com o resto da interface. Um contacto sem data de criação nunca cumpre um critério de data.
- Critérios guardados como texto JSON nos dois backends, com o mesmo código de codificação. Alternativa rejeitada: `JSONB` só no PostgreSQL, que traria comportamentos diferentes entre backends. Perde-se a validação do JSON na base de dados; a leitura valida sempre e rejeita dados mal formados.
- Nome único sem distinguir maiúsculas, garantido por índice sobre `lower(name)` e traduzido numa mensagem clara pelo repositório, tal como nas empresas.
- Formulário com 10 linhas fixas, sem JavaScript: as linhas sem campo são ignoradas e, se houver erro, a página volta a mostrar o que foi preenchido. Alternativa adiada: linhas dinâmicas com JavaScript.
- Uma ligação por pedido também nas rotas de segmentos (`build_segment_service_factory`), sem repetir a migração do esquema. `create_app` ganhou o parâmetro opcional `segment_service`, pelo que os testes anteriores não mudaram.

## Edição de segmentos

- `update` em cada repositório (nome, descrição, modo e critérios): o `id` e o `created_at` não mudam, e devolve se alguma linha foi alterada. `SegmentService.update_segment` valida primeiro e só depois grava, por isso um pedido inválido não altera nada.
- Edição por substituição completa (nome, descrição, modo e critérios de uma vez), e não por alterações parciais. É o que um formulário envia, e evita estados intermédios sem critérios. Alternativa rejeitada: editar um critério de cada vez, que exigiria identificadores por critério.
- Um segmento pode mudar só a capitalização do seu próprio nome: o índice único não conflita com a linha que está a ser atualizada. Trocar para o nome de outro segmento é recusado.
- O formulário de criação e o de edição partilham um template parcial (`_segment_form.html`); só mudam o destino e o texto do botão.
- Correção ao formulário: as linhas de critérios eram cortadas às 10. Com a edição, um segmento com mais critérios perderia os restantes ao ser aberto para editar. As linhas existentes nunca são cortadas; completam-se apenas as linhas vazias até 10.
- Concorrência: sem controlo otimista. Se duas pessoas editarem o mesmo segmento, vale a última gravação. Aceitável nesta fase.

## Paginação dos membros

- Paginação em memória (`pagination.py`, funções puras), depois de calcular os membros em Python. É a única opção coerente com a avaliação dos critérios em Python. Limite assumido: o cálculo continua a carregar todos os contactos; a paginação só evita enviar ao browser uma página gigante. Alternativa rejeitada: `LIMIT/OFFSET` em SQL, que obrigaria a traduzir os critérios para SQL.
- O utilizador escolhe o tamanho da página numa lista fechada (10, 25, 50 e 100; 25 por omissão). Alternativa rejeitada: campo numérico livre, que aceitaria zero, negativos ou valores enormes.
- Parâmetros `page` e `per_page` na URL (`/segments/{id}?per_page=50&page=2`), lidos como texto: um valor inválido volta ao valor por omissão e uma página fora do intervalo é ajustada à última, em vez de devolver erro. O seletor é um formulário GET sem JavaScript e mudar o tamanho volta à página 1; as ligações «anterior» e «seguinte» guardam o tamanho escolhido.
- A contagem de membros na listagem `/segments` não muda.
- Sem ordenação escolhida pelo utilizador: os membros aparecem pela ordem dos contactos (mais recentes primeiro).

**Limitações conhecidas e pendentes**

- A comparação de texto não distingue maiúsculas, mas distingue acentos.
- Operadores mostrados todos juntos no formulário; a combinação inválida é recusada com mensagem, em vez de filtrada na interface.
- Consentimento: tratado na secção seguinte. Um segmento continua a não decidir quem pode ser contactado.

## Consentimento

Requisito: determinar se um contacto pode ser considerado autorizado para uma finalidade de comunicação. Fora de âmbito: envio de emails. Ter consentimento registado não resolve, por si só, as obrigações legais de uma comunicação; esta etapa só trata do registo e da consulta.

**Três conceitos separados**

- Registo histórico: uma decisão concreta (`ConsentRecord`, tabela `consent_events`). Nunca é alterado nem apagado.
- Estado atual: derivado do histórico. Para cada contacto e finalidade vale o registo com o maior `id`: concedido, retirado ou sem registo.
- Elegibilidade operacional: `is_eligible`, verdadeira apenas se o estado atual é concedido. A ausência de registo não é consentimento. Nenhuma funcionalidade a usa ainda (não há envio); existe para que o futuro envio a consulte no domínio, em vez de a reimplementar.

**O que é novo**

- `domain/consent.py`: finalidades, decisões e origens (conjuntos fechados), `ConsentRecord`, `validate_decision` e as funções puras `current_status`, `latest_record` e `is_eligible`.
- Migração v4: tabela `consent_events` nos dois backends, mais um índice `(contact_id, purpose, id)`.
- `ConsentRepository` e `PostgresConsentRepository` (só `add` e `list_for_contact`); `services/consent_service.py`.
- Web: `GET e POST /contacts/{id}/consent`, `templates/consent.html` e um link «Consentimento» na lista de contactos.

**Decisões**

- Consentimento como conceito do domínio, numa tabela só de inserção. Alternativa rejeitada: um campo booleano em `contacts`, que não guarda finalidade, momento, origem nem histórico, e perde a concessão original quando há retirada.
- Política de histórico: o repositório só tem `INSERT` e leitura; não há `UPDATE` nem `DELETE`. A chave estrangeira para `contacts` usa `ON DELETE RESTRICT`, para que apagar um contacto não apague histórico em silêncio.
- Ordem pelo `id` e não pela data: `recorded_at` tem resolução de segundos no SQLite, e dois eventos no mesmo instante ficariam ambíguos. O `id` cresce em ambos os backends.
- Datas: `recorded_at` tem valor por omissão na base de dados, como `created_at`, e os dois repositórios devolvem `datetime` ingénuo em UTC.
- Finalidades: `newsletter` e `promotional`, conjunto fechado no domínio e em `CHECK` na base de dados. Acrescentar uma finalidade exige uma migração; aceite como custo da integridade. Consentimento ligado a uma campanha específica: adiado, sem requisito que o justifique.
- Origem: `form` e `admin`. A interface web grava sempre `admin`, fixado no servidor: o valor não vem do formulário, por isso não pode ser forjado. `form` existe no domínio para um futuro formulário público de subscrição, que ainda não existe.
- Validação em duas camadas: o domínio rejeita finalidade, decisão e origem inválidas antes de qualquer escrita; os `CHECK` da base de dados garantem o mesmo se alguém escrever por fora do serviço. Um contacto inexistente é rejeitado pelo serviço e, em caso de corrida, pela chave estrangeira.
- Repetições e idempotência: um registo repetido acrescenta sempre um evento novo. Alternativa rejeitada: não gravar se o estado já é o mesmo, que exigiria ler e depois escrever (uma corrida) e apagaria a prova de que a decisão foi repetida.
- Concorrência: sem bloqueios nem controlo otimista. Duas escritas simultâneas ficam ambas no histórico e o estado atual é o do evento com o maior `id`; não há estado contraditório nem histórico perdido.

**Âmbito e múltiplos emails**

O consentimento é do contacto e por finalidade. Cada evento guarda ainda uma cópia informativa do email principal no momento da decisão, preenchida pelo servidor a partir do contacto.

- Opção A, só contacto e finalidade: rejeitada em favor da C, porque não guarda qual era o endereço quando a pessoa decidiu.
- Opção B, por endereço de email: rejeitada. `remove_email` apaga o email, e a chave estrangeira apagaria ou bloquearia o histórico; a elegibilidade passaria a depender de qual email é o principal, que pode mudar.
- Opção C, por contacto com o email guardado como texto informativo: escolhida. Sobrevive a mudanças do email principal e à remoção de emails, e deixa o histórico auditável. O custo é duplicar um dado.
- Consequência aceite: a elegibilidade não distingue emails. Um email acrescentado depois herda o consentimento do contacto. Se isto vier a ser um problema, reavaliar a opção B.

**Migração v4**

- Só cria uma tabela e um índice, no mesmo mecanismo da v3: `PRAGMA user_version` no SQLite e `schema_version` com bloqueio consultivo no PostgreSQL. Não toca em contactos, emails, empresas nem segmentos.
- Se falhar, o rollback deixa a base na versão anterior.
- Elementos e razão: chave estrangeira com `RESTRICT` (integridade e histórico), `CHECK` nos campos fechados e no email (segunda camada de defesa), índice `(contact_id, purpose, id)` (consulta do estado atual).

**Segmentos**

A integração ficou adiada. Um critério de consentimento obrigaria a duplicar a regra de elegibilidade na segmentação, ou a carregar o estado em cada `Contact`. Regra a respeitar quando for feita: o segmento seleciona, e a autorização verifica-se sempre à parte, pelo serviço de consentimento, independentemente da pertena ao segmento.

**Validado**

- Testes novos (53): `tests/test_consent.py` (17 por backend e 1 de domínio puro) e `tests/test_web_consent.py` (9 por backend). Cobrem concessão, retirada, histórico preservado, finalidades independentes, ausência de registo, contacto inexistente, dados inválidos, decisões repetidas, associação ao contacto e ao email, `CHECK` da base de dados, migração e concorrência (12 threads).
- Suite completa com PostgreSQL ativo: 361 aprovados (antes: 308). Só com SQLite: 232 aprovados e 129 ignorados, que são os testes de PostgreSQL.
- Migração sobre uma cópia da base SQLite real: versão 2 para 4 (aplicou a v3 e a v4), com 2 contactos, 3 emails e 1 empresa preservados e `foreign_key_check` vazio. A base real foi migrada no arranque da app, com os mesmos totais.
- Interface no browser: sobre PostgreSQL e sobre SQLite, concessão e retirada na mesma finalidade mostram as duas linhas no histórico, e a outra finalidade não é afetada.

**Limitações conhecidas e decisões adiadas**

- A imutabilidade do histórico é garantida pela API do repositório, não pela base de dados: um `UPDATE` ou `DELETE` direto em `consent_events` não é impedido. Triggers ficam adiados até haver requisito.
- A coluna «Última decisão (UTC)» mostra a data da última decisão, não o momento em que o estado atual começou: uma retirada repetida atualiza essa data.
- Os três estados (concedido, retirado e sem registo) usam todos a classe `badge` já existente, sem estilo próprio por estado. Foi considerado suficiente nesta fase; distingui-los por cor fica adiado.
- Sem integração com segmentos e sem envio: a elegibilidade ainda não é consultada por nenhuma funcionalidade.
- Sem origem `form` em uso: não há formulário público de subscrição.
- Hipótese por validar: se a elegibilidade por contacto, sem distinguir emails, é suficiente quando existir envio.

**Lição operacional**

A verificação manual enganou-se duas vezes porque as apps de teste eram processos Uvicorn antigos (um arrancado na véspera) que serviam código anterior, enquanto os templates em disco já eram novos. Depois de um checkout, é preciso reiniciar a app e confirmar as rotas expostas (`/openapi.json`) antes de interpretar o que se vê.
