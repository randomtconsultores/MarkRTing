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

Implementar migracao, repositorios SQLite/PostgreSQL, reserva atomica de tentativas antes do hashing e limpeza. Definir configuracao explicita e testar concorrencia e falhas; nao depender de contador em memoria. Identificadores usados na persistencia devem minimizar exposicao de dados pessoais. O segundo commit nao pode expor login antes do terceiro.

## 3/3 — Integracao web

Aplicar a matriz antes de regras de negocio, ligar login/logout, validacao/revogacao das sessoes, cookies e CSRF a handlers e formularios. Integrar limitacao de tentativas, proteger documentacao de API, preservar fluxos publicos e testar negacao sem efeitos secundarios. A verificacao HTTP deve demonstrar que novas rotas nao ficam abertas por esquecimento. Evitar bypass de producao para conservar testes antigos: atualizar testes de interface conforme necessario.

So depois dos tres commits e da validacao integrada propor PR. O fecho global da etapa 4 continua a exigir tambem fluxos e politicas de dados pessoais previstos no roadmap. Manter administracao em rede privada ou proxy autenticado ate protecao integrada e verificada.
