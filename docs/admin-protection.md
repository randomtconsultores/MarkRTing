# Proteção administrativa — matriz, limitador e integração

## Estado atual — 20 de outubro de 2026

A matriz e o limitador persistente estão ligados à aplicação pelo router e guard integrados no PR #27. Login/logout, validação/revogação de sessões, cookies e CSRF nos formulários já não são trabalho futuro. O plano original em três commits pertence ao histórico de preparação; a integração HTTP foi detalhada em 3a/3b/3c.

Contratos atuais: [admin-http.md](admin-http.md), [admin-accounts.md](admin-accounts.md) e [web-security.md](web-security.md). A etapa 4 não termina com acesso; dados pessoais e verificações operacionais permanecem no [roadmap](roadmap.md).

## Matriz de autorização e inventário

`access_policy.py` classifica explicitamente método/caminho e permissões. Caminhos desconhecidos, métodos não previstos ou caminhos ambíguos são recusados por omissão. Papel provém da conta validada no servidor, nunca de formulário, query ou header do cliente.

- GET/HEAD de consultas e documentação (`/docs`, `/redoc`, `/openapi.json` e retorno OAuth de docs): admin ou viewer.
- GET/HEAD de edição de segmento: apenas admin.
- POST de contactos, empresas, consentimento, segmentos, campanhas e supressões: apenas admin e finalidade CSRF admin.
- Login: GET/HEAD e POST classificados separadamente; POST exige CSRF login e verificações do router.
- Logout: só POST, conta autenticada admin/viewer e CSRF logout.
- Subscrição, confirmação, cancelamento, assets e POST `/events/email` conservam validadores próprios. `public` não autentica eventos ou valida ligações; não há permissão por prefixo genérico `/subscribe`.

HEAD segue a política de GET, mas não cria um handler ou garante suporte pelo framework. O path chega descodificado, sem query; não se normaliza ou descodifica outra vez. Controlos, percent-encoding residual, backslashes, query/fragmento e segmentos `.`/`..` falham fechados.

O teste estático de inventário verifica as rotas declaradas em `main.py`. Roteamento dinâmico e `app.api_route` exigem revisão explícita. Documentação e assets também são abrangidos. Esse teste não substitui verificações HTTP. `AdminAccessGuard`, agora montado, é quem aplica a política aos pedidos de negócio; importar a matriz isoladamente continua a não proteger rotas.

## Tentativas persistentes

A migração v10 acrescenta `admin_login_attempts`, sem transformar dados anteriores. SQLite e PostgreSQL reservam atomicamente os orçamentos por conta, IP e instalação. Se algum recusar, reservas parciais são revertidas. UPSERT condicional evita leitura seguida de incremento; saturados não aumentam e janela expirada pode recomeçar.

`LoginAttemptLimiter` exige `LoginAttemptConfig` explícita. `from_env` não fornece valores por omissão:

| Variável | Significado |
| --- | --- |
| `MINI_MARKETO_LOGIN_ATTEMPT_HMAC_KEY` | Segredo independente, pelo menos 32 bytes UTF-8 |
| `MINI_MARKETO_LOGIN_ATTEMPT_WINDOW_SECONDS` | Janela positiva em segundos |
| `MINI_MARKETO_LOGIN_ATTEMPT_ACCOUNT_LIMIT` | Máximo por conta normalizada |
| `MINI_MARKETO_LOGIN_ATTEMPT_IP_LIMIT` | Máximo por IP |
| `MINI_MARKETO_LOGIN_ATTEMPT_INSTALLATION_LIMIT` | Máximo por instalação |

Os limites aceitam 1 a 2147483647, devido ao INTEGER da v10 PostgreSQL; não é um orçamento recomendado de produção. Mesma instalação exige base, chave HMAC e configuração coerentes entre processos. Não reutilizar chaves de CSRF, subscrição ou cancelamento. Identificadores persistidos são HMAC-SHA256 com contextos/âmbitos separados; username/IP não são guardados em claro pelo limitador. Normalização do identificador do orçamento não altera o username entregue à autenticação.

No fluxo integrado, reserva precede hashing. Recusa responde 429 com Retry-After; sucesso de login não reinicia contadores. `AuthenticationService` isolado não aplica o limitador nem autoriza operações de negócio: a coordenação cabe ao router.

## Limpeza e operação

`cleanup_expired_login_attempts(now, batch_size=1000)` e o serviço correspondente processam um lote por chamada; a seleção ordena por expiração e hash e o DELETE revalida a expiração. Tamanho permitido: 1 a 10000; o teto limita linhas, não tempo de execução. A integração acrescentou limpeza limitada de sessões e CLI:

```sh
.venv/bin/python -m mini_marketo.admin_maintenance --batch-size 1000
```

O destino deve ser explícito e revisto; agendamento externo e repetições cabem ao operador. Não há scheduler ou limpeza ilimitada por pedido. Isto não implementa retenção geral de contactos/consentimentos/subscrições.

Configuração administrativa inválida impede o arranque de toda a aplicação. Validar TLS, proxy, browser, segredos, limites, cópias de segurança e recuperação no ambiente de destino antes de exposição. Não retirar restrições de rede apenas porque testes passaram.

## Histórico de verificação

Inventário inicial: 63 aprovados em pacote temporário com Python 3.12.8, sem iniciar FastAPI. `main.py` conferido pelo hash `0f071400d028871b1c156010d9665f7a49b4d698`; cobriu os 30 decorators então existentes, papéis, métodos, exceções públicas, documentação e CSRF. Não era suite completa nem prova de proteção instalada naquele momento.

Ajuste posterior do limitador: 33 aprovados com SQLite em pacote temporário; condições e limitações em [decisoes-etapa4.md](decisoes-etapa4.md).

Integração final reportada pelo utilizador no Copilot para `e2012d850260c7f5ea74b41adb1d5ef1ea7704ad`, antes do merge: 999 aprovados com PostgreSQL em 69,36 s, incluindo dois testes de regressão do guard também aprovados isoladamente em 0,06 s. Diff-check desde a base do 3c passou. Não é suite completa, validação pós-merge ou ensaio de produção. O assistente não executou esses testes nem novos testes nesta revisão documental.

O fecho global da etapa 4 continua a depender dos fluxos e políticas de dados pessoais e das verificações relevantes previstas no roadmap.
