# Etapa 3 — Subscrição pública com confirmação por email (PR 3)

Complementa [as decisões da etapa 3](decisoes-etapa3.md), sem repetir as decisões dos outros PRs. O [roadmap por etapas](roadmap.md) distingue trabalho existente e evolução futura.

## Estado

Implementação na branch `feat/etapa3-subscricao`, validada por testes reportados no commit `a307b2d`, ainda por rever e integrar. O PR 4 (supressão) foi integrado no PR GitHub #14, merge `5b7b6d5`. A etapa 3 não se considera encerrada só porque esta implementação passou os testes.

## Requisitos e fluxo

1. A pessoa abre `/subscribe`, indica nome, apelido e email e escolhe newsletter, comunicações promocionais ou ambas. As caixas começam desmarcadas.
2. O POST grava um pedido e as finalidades escolhidas. Não cria contacto nem concede consentimento. Redireciona para `/subscribe/received`.
3. O processo `python -m mini_marketo.send_worker` envia o email de confirmação, pelo mesmo SMTP e remetente das campanhas. Não se envia email no pedido web.
4. A ligação abre `/subscribe/confirm/{token}`. GET apenas mostra a confirmação; POST confirma. Um verificador que só visita ligações não concede consentimento.
5. Ao confirmar, reutiliza-se o contacto que possui esse email (principal ou secundário), sem lhe alterar o nome; se não existir, cria-se. Regista-se consentimento concedido com origem `form` só nas finalidades escolhidas que não estejam já concedidas.
6. Uma nova confirmação pode conceder uma finalidade anteriormente retirada. Reutilizar uma ligação já confirmada não volta a conceder consentimento.

O consentimento continua a ser do contacto, por finalidade, como na etapa 2. Confirmar um email secundário não muda o email principal nem transforma o consentimento existente em consentimento por endereço.

## Persistência e decisões

- Migração v8 nos dois motores: `subscription_requests` e `subscription_request_purposes`, com chave estrangeira e remoção em cascata. Acrescenta tabelas e índices, sem transformar dados anteriores.
- Pedido e finalidades são gravados na mesma transação. O limite de três pedidos por endereço numa janela de 24 horas é verificado nessa transação: SQLite usa `BEGIN IMMEDIATE`; PostgreSQL usa bloqueio consultivo por endereço. Não se faz uma contagem desligada da escrita.
- A confirmação também é transacional: criação ou reutilização do contacto, concessão das finalidades e `confirmed_at` são tratados por `ConfirmationRepository`, sem chamar operações que façam commit intermédio. SQLite usa `BEGIN IMMEDIATE`; PostgreSQL usa transação e bloqueios do pedido, do endereço e do contacto. O serviço exige a mesma ligação para os repositórios envolvidos. Uma falha nas escritas faz rollback integral.
- O estado de envio (`pending`, `sending`, `sent`, `failed`, `skipped`) é independente de `confirmed_at`. A expiração é calculada, não há estado `expired` persistido. Pedidos expirados podem continuar `pending`, mas deixam de ser selecionados para envio.
- O token contém id do pedido e assinatura HMAC-SHA256 truncada a 128 bits, sem guardar o token. Reutiliza a configuração do cancelamento, com prefixo de assinatura `subscribe:`: tokens de confirmação e cancelamento não são intercambiáveis. A validação usa `fullmatch`.
- Um pedido não confirmado expira exatamente às 48 horas após a criação, não após o envio. Uma ligação já confirmada continua a mostrar o resultado após esse prazo, sem novas escritas.
- A expiração é revalidada imediatamente antes de chamar o SMTP, depois de preparar o texto. Um pedido que expire enquanto aguarda no lote ou durante a preparação fica `skipped`, com motivo `expirado`, sem chamar o SMTP. Isto não garante que a ligação ainda seja válida quando a mensagem chegar à caixa de correio.
- Supressão é verificada ao pedir, antes de enviar e antes de confirmar pedidos ainda não confirmados. Não levanta supressões nem ignora queixas. A consulta de uma ligação já confirmada não reavalia a supressão, mas não altera consentimento nem autoriza envios.
- Pedidos válidos gravados, limitados, suprimidos ou filtrados pelo honeypot recebem o mesmo redirecionamento. Um endereço já conhecido não recebe resposta diferente. Formulário inválido recebe 422 com texto genérico traduzível e os valores escapados.
- Um endereço que já tem consentimento concedido também pode receber um novo email de confirmação, sujeito aos limites. Não existe otimização para omitir esse envio.
- As páginas públicas são autónomas, sem menu administrativo, com `noindex`, `Cache-Control: no-store` e `Referrer-Policy: no-referrer`.

## Textos e idiomas

- `src/mini_marketo/texts/pt.json` contém os textos das páginas de subscrição e do email de confirmação. Não traduz atualmente as páginas administrativas nem a página de cancelamento.
- `texts.py` carrega os ficheiros com `importlib.resources`; `texts/*.json` está incluído nos dados do pacote em `pyproject.toml`.
- O idioma do pedido é guardado. Idioma desconhecido ou chave ausente numa tradução recorre a `pt`. Neste momento só há catálogo português; suporte estrutural não significa traduções já disponíveis.
- Para acrescentar idioma, criar outro JSON com as mesmas chaves e marcadores. Os testes verificam a coerência. Textos usam marcadores de `str.format`; as páginas escapam o resultado, sem tratar o catálogo como HTML arbitrário.
- O catálogo é carregado com cache; alterações exigem reiniciar os processos. Editar traduções não é uma funcionalidade da interface administrativa.
- Blocos reutilizáveis definidos pelo utilizador para campanhas são trabalho futuro da etapa 5, não uma funcionalidade entregue por este catálogo.

## Configuração e operação

| Variável | Uso | Omissão |
|---|---|---|
| `MINI_MARKETO_PUBLIC_URL` | Base das ligações de confirmação e cancelamento | Sem configuração, não se aceitam confirmações |
| `MINI_MARKETO_SECRET_KEY` | Assinatura das ligações; mínimo 32 caracteres | Obrigatória para enviar |
| `MINI_MARKETO_SECRET_KEY_PREVIOUS` | Chaves antigas, separadas por vírgulas, só para validar | Vazia |
| `MINI_MARKETO_CONFIRMATION_DAILY_CAP` | Teto diário UTC só das confirmações; zero impede o envio | 200 |
| `MINI_MARKETO_PRIVACY_URL` | Ligação opcional para política de privacidade no formulário e email | Vazia |

Mantêm-se as variáveis SMTP e da base de dados descritas nas decisões da etapa 3. `MINI_MARKETO_PRIVACY_URL` aceita URL absoluto HTTP ou HTTPS sem espaços; não obriga HTTPS neste momento. Configuração inválida da política ou do teto é recusada no arranque relevante.

A aplicação web e o processo de envio precisam de partilhar base de dados, URL público e chaves. Sem URL e chave válidos, o formulário ainda aceita pedidos, mas não é possível confirmar e o processo de envio recusa arrancar. Não expor o formulário nessas condições operacionais.

O processo envia primeiro confirmações e depois campanhas. O teto de confirmações é independente de `MINI_MARKETO_SMTP_DAILY_CAP`, que continua a contar apenas campanhas. Não há quota global atómica comum aos dois tipos ou a vários processos.

Throttling, falhas consecutivas ou uma paragem desconhecida nas confirmações impedem a fase de campanhas nessa ronda. Erro fatal termina com código 2. Em `--loop`, uma paragem não fatal das confirmações aguarda pelo menos o recuo inicial antes da ronda seguinte (60 segundos por omissão). Atingir apenas o teto diário das confirmações permite a fase de campanhas, porque os tetos são independentes. `--poll-seconds` tem de ser positivo.

### Exposição pública

A aplicação ainda não autentica as páginas administrativas. No proxy, permitir apenas os caminhos necessários:

- `/subscribe` e `/subscribe/*`;
- `/unsubscribe/*`;
- `/events/email`, com a sua autenticação Bearer;
- `/static/*`.

Contactos, empresas, segmentos, campanhas, consentimento e `/suppressions` devem ficar numa rede privada ou atrás de autenticação do proxy. A existência destas rotas não configura o proxy automaticamente. Aplicar limites de pedidos, tamanho de corpo e tempos de espera no proxy; o honeypot não substitui essas proteções.

## Limitações e revisão antes de produção

- A transação de confirmação não constitui uma garantia geral de serialização com todos os outros fluxos. Uma corrida com retirada de consentimento, alteração de emails ou supressão merece testes específicos; nem todos esses fluxos usam os mesmos bloqueios.
- O instante de expiração usado ao confirmar é capturado antes de esperar pelos bloqueios. Reavaliar se uma espera prolongada tiver de invalidar pedidos que chegaram ainda dentro do prazo.
- Sem limpeza automática ou prazo de retenção dos pedidos, confirmados ou não. A expiração da ligação não apaga dados pessoais.
- Limite por email, honeypot e teto diário não substituem limitação por IP nem proteção contra ataques distribuídos.
- SMTP e gravação do estado não são atómicos: falhar depois da entrega e antes de `mark_sent` pode causar repetição após recuperação.
- O email de confirmação é texto simples e não leva os cabeçalhos de cancelamento de campanhas. Não há mensagem de boas-vindas automática.
- Ainda sem validação contra SMTP, proxy ou browser reais, nem migração v8 sobre cópia da base real.

## Validação reportada

Execuções feitas pelo utilizador no ambiente Copilot, com PostgreSQL ativo; não são execuções efetuadas pelo assistente:

| Commit / fase | Resultado reportado |
|---|---|
| `246d51b` — parte 1b | 857 aprovados em 61,52 s |
| Após parte 2a | 882 aprovados e uma falha no teste de token |
| `e20d663` — parte 2b | 932 aprovados e 1 ignorado em 69,88 s |
| `83f913e` — páginas públicas | 974 aprovados e 1 ignorado em 72,39 s |
| `a307b2d` — após as três correções | 998 aprovados e 1 ignorado em 79,92 s |

A falha do teste de token foi corrigida em `7fdca4c`: trocar a última letra por `A` não adulterava uma assinatura que já terminava em `A`. Foi também acrescentada a rejeição de quebra de linha final no token de confirmação.

Correções incluídas na última execução:

- `7535ba8`: paragem entre confirmações e campanhas; testes de throttling, falhas consecutivas, erros fatais, teto diário e espera em loop.
- `9fe5131`: expiração por mensagem; testes do limite exato, de espera no lote e de preparação demorada.
- `a307b2d`: confirmação transacional; testes de rollback no segundo consentimento e na gravação de `confirmed_at`, com contacto novo ou existente, e reutilização de token sem anular retirada posterior.

O teste ignorado é a variante PostgreSQL de um teste do comando que só prepara um ficheiro SQLite. Os restantes testes parametrizados continuam a exercitar os dois motores. Não significa cobertura de ponta a ponta do comando com PostgreSQL.

Não há nesta documentação resultado confirmado de CI ou revisão automática do PR. Testes aprovados não demonstram ausência das limitações acima.
