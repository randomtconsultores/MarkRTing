# Protecao administrativa integrada — plano de tres commits

Ramo: feat/etapa4-protecao-administrativa. Nao abrir PR de conclusao da protecao administrativa apenas com o primeiro ou segundo commit.

## 1/3 — Matriz de autorizacao e inventario

access_policy.py define classificacao explicita por metodo/caminho e permissoes. Caminhos desconhecidos, metodos nao previstos e caminhos ambiguos sao negados por omissao. As decisoes de papel so podem receber o papel da conta validada no servidor, nunca de formulario, query ou header enviado pelo utilizador.

- GET/HEAD das paginas de consulta e documentacao (/docs, /redoc, /openapi.json e retorno OAuth de docs): admin ou viewer.
- GET/HEAD do formulario de edicao de segmento: apenas admin.
- POST de contactos, empresas, consentimento, segmentos, campanhas e supressoes: apenas admin e finalidade CSRF admin.
- Login: GET/HEAD e POST classificados separadamente; POST exige finalidade CSRF login na integracao.
- Logout: apenas POST, conta autenticada admin/viewer e finalidade CSRF logout.
- Subscricao, confirmacao, cancelamento, assets e POST /events/email mantem mecanismos proprios. Classificacao public nao autentica eventos nem valida tokens de ligacao: os handlers existentes continuam responsaveis por isso. Nao ha permissao publica por prefixo generico /subscribe.

HEAD segue a politica de GET, mas isto nao cria um handler HEAD nem garante que o framework o suporta numa determinada rota. A classificacao recebe o path descodificado sem query; nao normaliza nem descodifica novamente. Valores com controlos, percent-encoding residual, backslashes, query/fragmento ou segmentos . e .. falham fechados.

O teste estatico le main.py e verifica que cada rota declarada tem politica conhecida; novas rotas nao classificadas devem fazer o teste falhar. Roteamento dinamico e uso de app.api_route requerem revisao explicita. A matriz inclui ainda documentacao e assets, que nao aparecem nos 30 decorators atuais. O teste de inventario nao substitui verificacao HTTP do terceiro commit.

Verificacao: 63 casos aprovados em pacote temporario com Python 3.12.8, sem iniciar FastAPI, na execucao registada durante a preparacao. O main.py anexado foi conferido pelo hash Git 0f071400d028871b1c156010d9665f7a49b4d698, igual ao ficheiro remoto anteriormente lido. Foram cobertos os 30 decorators existentes, papeis, metodos, excecoes publicas, documentacao, CSRF e negacao por omissao. A suite completa nao foi executada aqui.

Importante: este commit nao instala a matriz na aplicacao. Nao ha ainda middleware/guard que aplique estas decisoes aos pedidos; nenhuma rota fica protegida por importar este modulo.

## 2/3 — Tentativas persistentes

A migracao v10 adiciona `admin_login_attempts`, sem alterar dados das tabelas existentes. Os repositorios SQLite e PostgreSQL reservam, numa unica transacao, os limites por conta, endereco IP e instalacao. Se algum limite recusar, as reservas parciais da tentativa sao revertidas. A reserva usa UPSERT condicional, nao uma leitura seguida de incremento; contadores saturados nao aumentam e uma janela expirada pode comecar de novo. `cleanup_expired_login_attempts(now)` remove linhas expiradas.

`LoginAttemptLimiter` exige `LoginAttemptConfig` explicita. `LoginAttemptConfig.from_env()` exige todas estas variaveis, sem limites por omissao:

| Variavel | Significado |
| --- | --- |
| `MINI_MARKETO_LOGIN_ATTEMPT_HMAC_KEY` | Chave secreta independente, com pelo menos 32 bytes UTF-8 |
| `MINI_MARKETO_LOGIN_ATTEMPT_WINDOW_SECONDS` | Duracao positiva da janela |
| `MINI_MARKETO_LOGIN_ATTEMPT_ACCOUNT_LIMIT` | Tentativas maximas por conta normalizada |
| `MINI_MARKETO_LOGIN_ATTEMPT_IP_LIMIT` | Tentativas maximas por endereco IP |
| `MINI_MARKETO_LOGIN_ATTEMPT_INSTALLATION_LIMIT` | Tentativas maximas para a instalacao |

Todos os processos da mesma instalacao devem usar a mesma base de dados, chave HMAC e valores de configuracao. Gere e distribua a chave como segredo operacional; nao reutilize as chaves de CSRF, cancelamento ou subscricao. Os identificadores persistidos sao HMAC-SHA256 com contexto e ambito separados; username e IP nao sao guardados em claro. A normalizacao do username so e aplicada ao identificador do orcamento: a autenticacao deve receber o valor original. A limpeza pode ser agendada pelo operador, chamando o metodo do repositorio/servico com o tempo Unix atual.

Este commit nao liga o limitador ao `AuthenticationService` nem expoe login. A reserva deve ser chamada antes do hashing quando ocorrer a integracao web, no terceiro commit.

## 3/3 — Integracao web

Aplicar a matriz antes de regras de negocio, ligar login/logout, validacao/revogacao das sessoes, cookies e CSRF a handlers e formularios. Integrar limitacao de tentativas, proteger documentacao de API, preservar fluxos publicos e testar negacao sem efeitos secundarios. A verificacao HTTP deve demonstrar que novas rotas nao ficam abertas por esquecimento. Evitar bypass de producao para conservar testes antigos: atualizar testes de interface conforme necessario.

So depois dos tres commits e da validacao integrada propor PR. O fecho global da etapa 4 continua a exigir tambem fluxos e politicas de dados pessoais previstos no roadmap. Manter administracao em rede privada ou proxy autenticado ate protecao integrada e verificada.
