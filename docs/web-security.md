# Primitivas de seguranca web administrativa

Este incremento prepara cookies, CSRF, verificacao de origem e redirecionamentos. Nao modifica main.py, templates, rotas ou esquema. Importar o modulo nao protege nenhuma rota; nao ha middleware nem login exposto neste incremento. Mantem-se rede privada ou proxy autenticado.

## Configuracao explicita

WebSecurityConfig recebe origem administrativa, chave CSRF em bytes, duracao absoluta, inatividade e duracao CSRF. Nao ha chave gerada silenciosamente, valores de duracao por omissao ou leitura automatica de headers de proxy. A futura integracao deve carregar configuracao confiavel e partilhar a mesma chave CSRF entre processos. Usa segredo aleatorio independente, com pelo menos 32 bytes, nao credenciais de conta nem chave publicada; o campo nao aparece no repr. O comprimento e verificado, nao a entropia real do segredo. Rotacao invalida tokens CSRF antigos.

Origem HTTPS obrigatoria, sem credenciais, caminho, query ou fragmento. Host e porta por omissao sao normalizados; nomes internacionais devem ser fornecidos em representacao ASCII apropriada. Excecao HTTP apenas com allow_http_local=True e host literal localhost, 127.0.0.1 ou ::1. A excecao e recusada para hosts de rede e para configuracao HTTPS; nao inferir desenvolvimento de um pedido. Cookies HTTP locais tem nomes diferentes e nao usam o prefixo __Host-. A excecao nao substitui isolamento da maquina e nao e adequada a producao.

## Cookies e respostas privadas

Cookies de sessao e pre-login usam HttpOnly, SameSite=Lax explicito, Path=/ e nenhum Domain. Em HTTPS usam Secure e os nomes __Host-mm_session e __Host-mm_login. Lax e defesa adicional; nunca substitui CSRF. set_session_cookie usa apenas o tempo restante ate expires_at, nunca um prazo completo renovado a cada resposta. Validacao da sessao e expiracao continuam obrigatorias no servidor.

set_login_cookie recebe nonce aleatorio URL-safe de 43 caracteres (gerar com secrets.token_urlsafe(32)) e usa a duracao CSRF. clear_access_cookies expira ambos os cookies com o mesmo escopo e atributos; nao revoga por si so a sessao na base. No sucesso de login, eliminar a pre-sessao e emitir uma sessao nova, sem promover o nonce de login a sessao autenticada. Se usar o helper que elimina ambos, executa-lo antes de definir o novo cookie.

private_response aplica Cache-Control=no-store, Referrer-Policy=no-referrer, X-Content-Type-Options=nosniff e X-Frame-Options=DENY. Nao altera automaticamente respostas existentes. Estes headers nao demonstram protecao contra XSS, nem substituem escape dos templates, TLS ou configuracao do proxy.

## CSRF vinculado

CsrfTokens emite token com timestamp, nonce aleatorio e HMAC-SHA256. A mensagem assinada usa uma lista JSON canonica com contexto mm-csrf-v1, timestamp, nonce, vinculo e finalidade. O vinculo nunca aparece no token; deve ser o token da sessao administrativa validada ou o nonce aleatorio do cookie de pre-login. Nunca usar apenas user_id ou um valor fixo. Finalidades login, admin e logout nao sao intercambiaveis.

O token deve ser entregue explicitamente em campo de formulario ou header, nunca apenas lido do cookie e nunca em URL. A futura rota obtem o vinculo do cookie apropriado, valida a sessao quando autenticada e verifica finalidade, assinatura e tempo antes de qualquer mutacao. A comparacao da assinatura usa hmac.compare_digest. Entradas malformadas, assinaturas alteradas, outro vinculo/chave/finalidade, timestamps futuros e expiracao no limite sao rejeitados.

Tokens podem ser reutilizados dentro do prazo para a mesma sessao/finalidade; nao sao tokens de uso unico nem mecanismos de idempotencia. Nao garantem autenticacao, autorizacao ou protecao contra XSS. A validade CSRF nao prova que uma sessao continua ativa: o repositorio deve ser consultado separadamente. Nunca registar tokens, vinculos ou a chave.

## Origem e redirecionamento

same_origin compara a origem configurada com Origin, ou com a origem de Referer apenas se Origin estiver ausente. Origin invalido, vazio, null, porta/esquema diferentes ou ambos ausentes falham. Um Origin invalido nao pode ser compensado por Referer valido. A origem de destino nao e derivada de Host ou X-Forwarded-Host fornecidos pelo cliente. A integracao deve verificar metodos mutantes; nao aplicar cegamente esta regra a subscricao publica, cancelamento por ligacao ou eventos com autenticacao propria.

safe_redirect aceita apenas destinos ASCII locais iniciados por uma barra, com limite de 2048 caracteres. Rejeita URLs absolutos, barras iniciais duplas, backslashes, controlos, espacos, fragmentos e qualquer percent-encoding, devolvendo / em caso de duvida. E deliberadamente restritivo: alguns destinos locais legitimos com caracteres codificados tambem voltam a /. Nao descodificar novamente depois desta verificacao.

## Verificacao

87 testes de configuracao, CSRF, origem e redirecionamento aprovados em 0,07 s, num pacote temporario com Python 3.12.8. Sao testes reais do novo modulo, sem base de dados. A primeira tentativa de recolha do conjunto completo falhou porque Starlette nao esta instalado neste ambiente; depois separaram-se os testes independentes dos testes de respostas HTTP.

Os 9 casos de cookies/headers em test_web_security_cookies.py estao preparados para usar Response real do Starlette no ambiente do projeto, mas nao foram executados aqui. Nao se usou uma imitacao de Response para os contar como aprovados. A suite completa, SQLite/PostgreSQL e comportamento real no browser/proxy tambem nao foram executados neste incremento. Compilacao sintatica dos tres ficheiros Python verificada.

```sh
.venv/bin/python -m pytest -q -rs tests/test_web_security.py tests/test_web_security_cookies.py
.venv/bin/python -m pytest -q -rs
```

## Integracao ainda obrigatoria

Completar inventario de rotas e formularios a partir de main.py integral (a leitura pelo conector nesta analise foi truncada), implementar limitacao de tentativas persistente/coerente entre processos e ligar configuracao, login/logout, autorizacao por rota e tokens CSRF aos formularios. Preservar os fluxos publicos e testar acesso negado, sessao invalida e ausencia de efeitos secundarios. Nao declarar administracao protegida ou etapa 4 concluida com este modulo isolado. Permanecem tambem os fluxos e politicas de dados pessoais do roadmap.

Fontes tecnicas:
- https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html
- https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html
- https://starlette.dev/responses/
