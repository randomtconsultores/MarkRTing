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
- Edição por substituição completa (nome, descrição, modo e critérios de uma vez), e não por alterações parciais. É o que um formulário envia, e evita estados intermediários sem critérios. Alternativa rejeitada: editar um critério de cada vez, que exigiria identificadores por critério.
- Um segmento pode mudar só a capitalização do seu próprio nome: o índice único não conflita com a linha que está a ser atualizada. Trocar para o nome de outro segmento é recusado.
- O formulário de criação e o de edição partilham um template parcial (`_segment_form.html`); só mudam o destino e o texto do botão.
- Correção ao formulário: as linhas de critérios eram cortadas às 10. Com a edição, um segmento com mais critérios perderia os restantes ao ser aberto para editar. As linhas existentes nunca são cortadas; completam-se apenas as linhas vazias até 10.
- Concorrência: sem controlo otimista. Se duas pessoas editarem o mesmo segmento, vale a última gravação. Aceitável nesta fase.

**Limitações conhecidas e pendentes**

- Sem paginação na lista de membros.
- A comparação de texto não distingue maiúsculas, mas distingue acentos.
- Operadores mostrados todos juntos no formulário; a combinação inválida é recusada com mensagem, em vez de filtrada na interface.
- Consentimento continua por tratar: um segmento não decide quem pode ser contactado.
