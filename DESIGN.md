# Entre Nós: projeto técnico (Fase 1)

Diário inteligente do relacionamento. Roda **só neste computador**, abre no navegador
(`http://127.0.0.1:8787`), guarda tudo **criptografado** e funciona **sem IA** (motor de
regras). Uma IA pode ser ligada depois sem mudar o resto (motor plugável).

Este documento é o contrato entre os módulos. `entrenos/modelos.py`, `entrenos/texto.py` e
`entrenos/config.py` já estão prontos e **não devem ser alterados** sem atualizar este arquivo
(`texto.semelhanca` mudou em 07/10/2026: seção 19.7; `Proposta.extras` entrou em 07/10/2026:
seção 20.0; `texto.termos` passou a usar as stopwords normalizadas uma vez só, mesmo resultado:
seção 20.10).

## 1. Princípios (valem para todo o código)

1. **Nada sai da máquina.** Sem CDN, sem Google Fonts, sem telemetria, sem requisição externa.
   A única exceção futura é o `MotorIA` apontado para uma URL que o usuário configurar, e a
   tela precisa dizer isso em destaque.
2. **Nunca inventar.** Todo item guarda o `trecho` literal de onde saiu. Busca, alertas, perfil e
   sugestões só usam o que está gravado e citam a origem ("você registrou em 12/09 que...").
   Sem informação: "Não encontrei essa informação." / "Não tenho informação suficiente para
   sugerir algo específico."
3. **Inferência nunca vira fato.** Só o usuário promove uma inferência. O motor pode rebaixar a
   certeza, nunca subir sem pista explícita ("ela disse que...").
4. **Revisar antes de gravar.** `analisar` devolve uma `Proposta`; só `POST /api/registros` grava,
   com o que o usuário confirmou.
5. **Sugestão é possibilidade, nunca ordem.** Frases com "Talvez...", "Pode ser uma boa...".
   Nada de manipular, influenciar decisões dela ou explorar inseguranças (seção 9 da especificação).
6. **O Python calcula datas.** Nem regras nem IA "chutam" datas: tudo passa por `regras/datas.py`.
7. **Apagar é apagar.** Registro apagado some do banco, do índice de busca e do arquivo
   (VACUUM antes de salvar). O histórico registra só "registro #12 apagado", sem conteúdo.
8. **Não logar conteúdo.** `erros.log` (na pasta de dados) guarda só tipo de erro e traceback de
   código, nunca textos do usuário, PIN ou corpo de requisição.

Estilo: Python 3.14, nomes e comentários em **português**, só biblioteca padrão +
`cryptography` (já instalada). Nada de pip novo. O `rapidfuzz` saiu do app em 07/10/2026:
`texto.semelhanca` é o mesmo `token_set_ratio` em Python puro, igual no computador e no celular
(seção 19.7). Todo `entrenos/*.py` precisa abrir também no Python **3.12** (margem: o Pyodide 314
roda o 3.14, seção 19): sintaxe 3.12 e `from __future__ import annotations` onde houver anotação;
o núcleo não inicia thread nem dorme.
Testes com `unittest` em `testes/` e rodam com:

```
python -m unittest discover -s testes -t .
```

(executado na raiz `C:\Users\Usuario\EntreNos`). Testes usam pasta temporária
(`tempfile.mkdtemp()` + `ENTRENOS_DADOS`), **nunca** `%LOCALAPPDATA%\EntreNos`.

## 2. Pastas e donos

```
EntreNos/
  iniciar.py              ponto de entrada (põe a raiz no sys.path e chama servidor.main)
  Abrir Entre Nos.bat     pythonw iniciar.py
  README.md  DESIGN.md  .gitignore
  entrenos/
    __init__.py __main__.py
    modelos.py texto.py config.py     PRONTOS (contrato)
    regras/datas.py                   datas em português
    regras/pessoas.py                 pessoas e parentescos
    regras/categorias.py              léxico das 32 categorias
    regras/certeza.py                 fato / observado / inferência
    regras/itens.py                   extração de itens (usa os quatro acima)
    motor.py                          Motor, MotorRegras, MotorIA, obter_motor
    contexto.py                       relações e contradições
    busca.py                          busca em linguagem natural
    alertas.py                        página "Hoje" e lembretes
    perfil.py                         "Conhecendo Ela", mapa de preferências, "Como ela está"
    cofre.py                          PIN, chave, criptografia do arquivo
    banco.py                          SQLite em memória, esquema, CRUD, histórico, FTS5
    nucleo.py                         o app sem transporte: sessão, PIN, rotas da API (seção 19)
    servidor.py                       transporte do computador: HTTP local + segurança do pedido + página
    ponte.py                          transporte do navegador: o Pyodide chama (seção 19)
    sincronizacao.py                  senha, cifra e mescla da sincronização entre aparelhos (seção 22)
    recursos/__init__.py              registro e pontos de encaixe dos recursos (seção 20.0)
    recursos/<nome>.py                um por recurso (perguntas, presentes... eu; seção 20)
                                      (testes/test_integracao.py: o que a integração mudou, seção 20.10)
  web/index.html app.js estilo.css    interface (SPA sem framework)
  web/telas/<nome>.js <nome>.css      a tela de cada recurso, por window.EntreNos (seção 20.0)
  web/sw.js trabalhador.mjs entrenos_pyodide.mjs agenda_avisos.js manifest.webmanifest icones/
  web/pyodide/314.0.7/                o app no celular e no navegador (PWA, seção 21)
  web/sincronizar.js                  Google Drive, login (janela do Google) e Ajustes → ☁️ Sincronização (seção 22)
  web/oauth.html oauth.js             a volta do login do Google, sem o app (seção 22.2)
  web/404.html                        endereço que não existe no site: 404, e não o app (seção 21.7)
  construir_web.py                    monta site/ para o Cloudflare Pages (seção 21.6)
  testes/test_*.py  testes/navegador/teste_pyodide.mjs (o Pyodide de verdade, no Node)
  testes/navegador/teste_sincronizar.mjs  (Drive de mentira + três aparelhos no Pyodide, seção 22)
  construir_instalador.ps1            testes + PyInstaller + Inno Setup → dist\ (seção 18.3/18.4)
  instalador/                         entrenos.spec, EntreNos.iss, entrenos.ico, gerar_icone.py
```

## 3. `regras/datas.py`

```python
def extrair_datas(texto: str, referencia: date) -> list[DataEncontrada]
def proxima_ocorrencia(dia: int, mes: int, referencia: date) -> date   # para aniversários
```

`referencia` é a data do registro (quase sempre hoje). As posições `inicio/fim` são no texto
original e `texto[inicio:fim] == expressao`. Não sobrepor expressões (a mais longa ganha).
Exemplos com referência **quarta-feira, 30/09/2026**:

| Texto | data | precisão | observação |
|---|---|---|---|
| hoje / amanhã / depois de amanhã | 30/09, 01/10, 02/10 | dia | |
| ontem / anteontem | 29/09, 28/09 | dia | passado |
| dia 20 | 20/10/2026 | dia | dia < hoje → mês seguinte; dia ≥ hoje → este mês (dia 30 = hoje) |
| 20/10, 20/10/26, 20/10/2026, 20-10, 20.10 | 20/10/2026 | dia | dd/mm sem ano: próxima ocorrência se contexto futuro; "ontem"-like não |
| 20 de outubro / 20 de outubro de 2027 / 1º de maio | | dia | |
| em novembro / no mês de novembro | 01/11/2026 | mes | mês já passado → ano seguinte |
| novembro de 2025 | 01/11/2025 | mes | passado |
| sexta / sexta-feira / na sexta | 02/10 | dia | próxima sexta ≥ amanhã |
| próxima sexta / sexta que vem | 02/10 | dia | **ambígua**: alternativas [02/10, 09/10] |
| sexta passada / na última sexta | 25/09 | dia | passado |
| semana que vem / próxima semana | 05/10 (segunda) | semana | |
| semana passada | 21/09 (segunda) | semana | passado |
| mês que vem / próximo mês | 01/10 | mes | |
| mês passado | 01/08 | mes | passado |
| ano que vem / próximo ano | 01/01/2027 | ano | |
| daqui a 3 dias / em 3 dias / daqui três dias | 03/10 | dia | números por extenso: um..trinta |
| daqui a 2 semanas / em duas semanas | 14/10 | dia | |
| daqui a um mês / daqui 2 meses | 30/10, 30/11 | dia | |
| há 3 dias / 3 dias atrás | 27/09 | dia | passado |
| fim de semana / final de semana | 03/10 (sábado) | dia | |
| natal / véspera de natal / ano novo / réveillon | 25/12, 24/12, 01/01/2027, 31/12 | dia | recorrente |
| dia dos namorados | 12/06/2027 | dia | recorrente (Brasil) |
| dia das mães / dia dos pais | 2º domingo de maio / agosto | dia | recorrente |
| páscoa / carnaval | calcular (algoritmo de Gauss/Meeus) | dia | |
| em 2019 | 01/01/2019 | ano | passado |
| aniversário ... 12/05 / faz aniversário dia 3 de março | próxima ocorrência | dia | `recorrente=True` quando a frase fala de aniversário/"todo ano" |
| hj | 30/09 | dia | abreviação de "hoje" |
| semana q vem / mês q vem / sexta q vem | como "que vem" | | "q" vale como "que" |
| fds | 03/10 (sábado) | dia | como "fim de semana" |
| no fim do mês / final do mês (que vem) / fim do ano | 28/09 (segunda da última semana) / 26/10 / 01/12 | semana / semana / mes | |
| terça passada (dita na quarta) | 29/09 | dia | **ambígua** quando cai ontem: alternativas [29/09, 22/09] |

Números que não são datas ("tenho 20 anos", "R$ 20,50", "2 filhos", "10h") **não** viram data.
Horas ("às 15h", "15:30") podem ser ignoradas nesta fase.

## 4. `regras/pessoas.py`

```python
RELACOES: dict[str, str]   # forma normalizada -> forma de exibição ("mae": "mãe", "melhor amiga": "melhor amiga")
def extrair_pessoas(texto: str, conhecidas: list[dict]) -> list[PessoaEncontrada]
# conhecidas: [{"id": 3, "nome": "Mariana", "relacao": "melhor amiga", "apelidos": ["Mari"]}, ...]
def casar_pessoa(nome_ou_relacao: str, conhecidas: list[dict]) -> int | None
```

- Parentescos e relações: mãe, pai, irmã, irmão, avó, avô, tia, tio, prima, primo, sobrinha,
  sobrinho, madrinha, padrinho, cunhada, cunhado, sogra (dela), filha, filho, madrasta, padrasto,
  melhor amiga, melhor amigo, amiga, amigo, chefe, gerente, colega (de trabalho), professora,
  professor, orientadora, orientador, terapeuta, psicóloga, médica, médico, ex, pet/cachorro/cachorra/gato/gata.
  Só conta como pessoa **dela** quando vem com "dela", "da [nome dela]" ou sem dono ("a mãe
  comentou" no contexto). "Minha mãe" é do usuário: relação `"mãe (sua)"`.
- Nomes: palavras capitalizadas que não são meses, dias da semana, lugares comuns óbvios nem
  palavras de uma lista de exclusão (Deus, Natal, Brasil, Netflix, cidades, países, empresas e
  artistas comuns...). Nome composto ("Ana Paula") junta.
  - No **início da frase** só vale como nome quando vem seguido de verbo ("Camila vai casar",
    "Mariana ligou") e não é palavra do dia a dia (léxico das categorias ou lista curta:
    "Tempo tá feio", "Almoço foi ótimo" não são pessoas).
  - Não é pessoa: nome composto que começa com palavra de lugar ("Shopping Iguatemi", "Parque
    Ibirapuera", "Projeto X"); nome depois de "em/no/na/para/pra", "é de", "voltou/veio de"
    (lugar, empresa); nome sem artigo depois de "fã de", "show do", "assistiu", "ama", "curte"...
    (obra ou artista). Com artigo ("ama a Mariana") continua sendo gente.
- Ligar nome e relação: "a mãe dela, Sandra," / "Sandra, mãe dela," / "a amiga dela Mariana" /
  "Mariana (melhor amiga)" / "o pai dela, seu Roberto" / "o chefe dela, o Marcos" → uma pessoa
  só com `nome` e `relacao` ("seu"/"dona" antes do nome é tratamento).
- Casar com conhecidas por nome, apelido ou relação única (`semelhanca` ≥ 90 para nomes).
  "a mãe dela" casa com a pessoa cadastrada com relação "mãe" se houver só uma. "avó" e "avô"
  são relações diferentes (`mesma_relacao`); sem acento, vale qualquer uma.
- A namorada é implícita ("ela") e **não** aparece na lista. Se o nome dela (config
  `nome_dela`) aparecer, também é ela, e também um apelido curto (2 a 4 letras do começo do
  nome: "Ju" de Julia) ou diminutivo ("Julinha"), a não ser que seja o nome de alguém
  cadastrado. "Maria" não é "Mariana". `e_nome_dela(texto, nome_dela)` faz essa conta.

## 5. `regras/categorias.py`

```python
LEXICO: dict[str, list[str]]  # categoria (nome de CATEGORIAS) -> termos/raízes normalizados
def classificar(texto: str) -> list[tuple[str, float]]   # [(categoria, pontuação 0-1)], maior primeiro, só > 0
```

- Léxico rico para as 32 categorias (pelo menos 15 termos por categoria, 40+ para Comidas,
  Saúde, Trabalho, Família). Termos normalizados (`texto.normalizar`), aceitando raiz
  ("apresent" pega apresentação/apresentar) e expressões de várias palavras.
- "Família" inclui os parentescos; "Aniversários" pega "aniversário/faz anos"; "Eventos futuros"
  é somado quando `datas` acharem data futura (quem faz isso é `itens.py`).
- Deve dar resultado útil para frases reais: "a mãe dela vai fazer uma cirurgia em novembro" →
  Família, Saúde, Eventos futuros, Preocupações (se "preocupada").

## 6. `regras/certeza.py`

```python
def classificar_certeza(frase: str) -> Certeza
```

- **CONFIRMADO**: fala dela relatada: "ela disse/contou/falou/comentou/mencionou/explicou/
  confirmou/avisou/me disse/me contou/respondeu", "segundo ela", "nas palavras dela", "ela
  garantiu", citação entre aspas atribuída a ela.
- **INFERENCIA**: "talvez", "acho que", "acredito que", "provavelmente", "pode ser que",
  "deve ser", "deve gostar", "imagino", "suponho", "parece que", "será que", "tenho a impressão",
  "achei que", e dúvida explícita: "não sei se", "sei lá", "não faço ideia", "não lembro se".
  Inferência vence confirmado ("acho que ela disse" → inferência).
- **OBSERVADO**: todo o resto (ações e reações dela que o usuário viu: "ela ficou feliz quando
  ganhou tulipas", "ela pediu sushi", "percebi que").
- Não confirmam: fala negada ("ela não disse que...", "ela nunca disse que...") e fala de outra
  pessoa ("a Mariana me contou que...", "a mãe dela me disse que..."). "Me disse" sem sujeito
  ("Hoje me contou que...") continua sendo ela.
- A fala dela confirma só a oração que ela disse. `trechos_confirmados(frase) -> [(ini, fim)]`
  vai do verbo de fala até "mas", "e eu", ", eu", "eu percebi/sei/vi..."; itens fora desses
  trechos ficam OBSERVADO ("Ela disse que tá cansada 🟢 e eu percebi que ela odeia o chefe 🟡").
- `trechos_nao_afirmados(frase) -> [(ini, fim)]`: depois de fala negada ou de "não sei se" nada
  foi afirmado; `itens.py` não tira item desses trechos (a frase vira NOTA).
- INCERTO nunca vem do texto; é calculado por idade/contradição em `perfil.py`.

## 7. `regras/itens.py`

```python
def extrair_itens(texto: str, referencia: date, pessoas: list[PessoaEncontrada],
                  datas: list[DataEncontrada]) -> list[Item]
```

Percorre as frases (`texto.frases`) e aplica padrões. Uma frase pode gerar vários itens; toda
frase que não gerar item estruturado vira `NOTA` (nada se perde). `trecho` = a frase literal.
`resumo` por modelo de frase curto, em terceira pessoa, sem inventar ("Gosta de tulipas",
"Não gosta de falar em público", "Apresentação no trabalho — 20/10/2026", "Mãe: cirurgia em
novembro/2026"). `pessoa` = relação/nome quando o item é de outra pessoa ("a mãe dela vai
fazer cirurgia" → pessoa "mãe").

| Padrão (exemplos) | Tipo | Detalhes |
|---|---|---|
| gosta de / adora / ama / curte / é apaixonada por / é fã de / prefere X | PREFERENCIA +1 | `alvo` = X (corta em vírgula, "mas", "porque"; "X e Y" gera dois itens) |
| não gosta de / odeia / detesta / não suporta / não curte / tem nojo de / não aguenta X | PREFERENCIA -1 | |
| é alérgica a / tem alergia a / não pode comer / não come / é vegetariana / intolerante a lactose | RESTRICAO | importância 3, categorias Saúde + Comidas quando for comida |
| ficou (muito) feliz / amou / se emocionou quando (ganhou/recebeu/fomos) X | PREFERENCIA +1, OBSERVADO | nunca CONFIRMADO; categoria "Coisas que deixam feliz" |
| ficou chateada / irritada / incomodada quando / com X | PREFERENCIA -1, OBSERVADO | "Coisas que incomodam" |
| evento (apresentação, prova, entrevista, cirurgia, consulta, exame, viagem, show, casamento, formatura, reunião, festa, mudança, defesa, TCC, vestibular, concurso, voo, audiência, curso, aula, jogo) + data futura | EVENTO | `data_evento`, `precisao`, `data_expressao`, categoria "Eventos futuros" + a do assunto; importância 3 se a frase ou vizinha tem preocupação |
| o mesmo com data passada | EVENTO (passado) | vira memória/experiência |
| está/tá/anda/ficou/estava preocupada, nervosa, ansiosa, triste, chateada, cansada, estressada, insegura, com medo, animada, feliz, empolgada (com/por causa de X) | ESTADO | `data_evento` = referência; categoria Preocupações (negativo) ou "Coisas que deixam feliz" |
| fica/costuma ficar/sempre fica nervosa antes de X / odeia falar em público / tem medo de X | PADRAO | Medos quando for medo |
| prefere que eu / gosta quando eu (alguém) / o que ajuda é / precisa de espaço / quer ficar sozinha quando / não gosta que eu tente resolver | APOIO ±1 | "prefere que eu apenas fique perto" → +1 alvo "que eu fique perto" |
| esperando (o) resultado / aguardando / vai saber / ainda não sabe / vai decidir / ficou de | PENDENCIA | `lembrar_em` = data do evento + 1 dia (nunca antes de amanhã), ou referência + 3 dias; Situações pendentes |
| aniversário (dela / da mãe dela / da Mariana) é/será/cai em/dia X | DATA_IMPORTANTE | `recorrente=True`, `pessoa`, categorias Aniversários + Datas importantes |
| quer / queria / gostaria de / sonha em / tem vontade de / um dia quer X | DESEJO | Sonhos, Objetivos ou Presentes (se for objeto: "queria um fone novo") |
| X é a melhor amiga dela / conheceram na faculdade / trabalha com ela | PESSOA_INFO | `pessoa` = nome |
| primeiro encontro, nossa viagem, fomos, conheci (a família/amiga dela) + data | EVENTO / Memórias compartilhadas | |

Importância 3: saúde, restrição, aniversário, evento com preocupação, "muito importante",
"nunca", "odeia" (não negado; "nunca esquecer" não conta), evento ou pendência "importante".
Importância 1: comentários soltos.

Cuidados que valem para todos os padrões:
- Negação também pelo "n" abreviado ("ela n gosta") e antes de verbo negativo ("não odeia" não é
  desgosto). Ironia ("..., só que não", "sqn") vira NOTA.
- Listas ("A, B e C", "X nem Y") param no que é outra oração: pedaço com verbo ("é" com acento
  é verbo, não "e"), trecho já usado por outra regra, "nem tanto". "Gosta de tudo menos de X" →
  -1 X. Fim de alvo sem "kkk", "rs", "de jeito nenhum", "desde...". Alvo que é só palavra
  funcional ("dela", "o") não vale.
- Dono: vírgula ou "e" seguidos de artigo começam outra oração ("Ela gosta de rosas, a mãe dela
  odeia rosas"; "ela e a Mariana" é um sujeito só). "ela" depois de verbo ("pedir ela",
  "levou ela") é objeto. Evento de saúde "pedido" por médica/dentista é dela.
- ESTADO só do que está acontecendo: "quando/se/sempre que ela tá estressada" não vira ESTADO.
  Formas de conversa contam: "tava", "tá", "mt/mto", "gostou", "amando", "começou a gostar".
  Reação a algo vago ("ficou triste com a notícia") é ESTADO, não gosto. "Odeia quando o Uber
  cancela" é PREFERENCIA -1 (a situação inteira); com "eu"/"a gente" continua APOIO.
- EVENTO não pega a data de outra ação: "o resultado do exame (sai semana que vem)" não vira
  exame (a pendência leva a data); "brigou semana passada por causa do casamento" não põe o
  casamento na semana passada. Evento que cai em data comemorativa só é `recorrente` se a frase
  fala em aniversário ou "todo ano".
- "Adorava/gostava ... quando era criança" vira FATO de Infância com o verbo no passado.
- Item de outra pessoa não leva "Sonhos"/"Objetivos" (são dela). "Quer ganhar um X" é Presentes.

## 8. `motor.py`

> **Atualizado pela seção 17 (IA sob demanda, 30/09/2026).** O que está abaixo sobre `obter_motor`,
> `config["motor"]` e `motor_url/motor_modelo/motor_chave` foi substituído: as regras são sempre o
> motor padrão e a IA só roda por botão. Quando esta seção e a 17 discordarem, vale a 17.

```python
@dataclass
class Conhecimento:                       # o que o motor pode consultar do banco
    pessoas: list[dict]                   # como em extrair_pessoas
    nome_dela: str | None

class Motor(Protocol):
    nome: str                             # "regras", "ia:ollama/qwen3:4b-instruct-2507-q4_K_M"
    def disponivel(self) -> bool
    def analisar(self, texto: str, referencia: date, conhecimento: Conhecimento) -> Proposta
    def responder(self, pergunta: str, trechos: list[dict]) -> Resposta   # trechos = encontrados da busca

class MotorRegras: ...                    # datas + pessoas + itens + categorias; sem rede
class MotorIA:                            # DESLIGADO por padrão
    def __init__(self, url: str, modelo: str, tipo: str = "ollama"|"openai", chave: str|None = None, tempo_limite: float = 180)
def obter_motor(config: dict) -> Motor    # config["motor"] in {"regras", "ollama", "openai"}; qualquer erro -> MotorRegras
def envia_para_fora(config: dict) -> bool # True se a URL do motor não for 127.0.0.1/localhost
                                          # ou se o modelo for "-cloud"/":cloud" (o Ollama local repassa para ollama.com)
```

- `MotorIA.analisar` **sempre** roda `MotorRegras` primeiro e manda à IA o texto + a proposta
  das regras, pedindo JSON num esquema fixo (lista de itens com tipo, resumo, trecho, certeza,
  categorias, alvo, polaridade, pessoa, `data_expressao`). Validação no Python: `trecho` tem de
  ser substring do texto (senão descarta o item), categorias fora de `CATEGORIAS` saem, datas
  são recalculadas com `extrair_datas` a partir de `data_expressao`, e a certeza nunca sobe
  acima do que `classificar_certeza(trecho)` permite. Qualquer erro, tempo esgotado ou JSON
  inválido → devolve a proposta das regras com aviso "A IA não respondeu; usei as regras."
- Ollama: `POST {url}/api/chat` com `format` = esquema JSON, `stream: false`,
  `options: {temperature: 0}`. OpenAI-compatível: `POST {url}/v1/chat/completions` com
  `response_format: {type: "json_schema", ...}`. Só `urllib.request` (sem pip).
- `responder` com IA: manda a pergunta + os trechos encontrados, exige citar `registro_id` dos
  trechos usados; o Python descarta citações que não estavam nos trechos. Sem IA,
  `MotorRegras.responder` monta a resposta por modelo de frase.

## 9. `contexto.py`

```python
def relacionar(proposta: Proposta, banco: Banco, referencia: date) -> list[Relacao]
```

- **contradicao**: item novo PREFERENCIA/RESTRICAO com mesmo `alvo` (normalizado, `semelhanca`
  ≥ 88) e polaridade oposta a um item ativo. Motivo: "Em 12/03 você registrou que ela não gosta
  de sushi. Agora: 'Ela pediu sushi no jantar.' Existe uma possível mudança ou contradição.
  Deseja atualizar a preferência?" A antiga **não** muda sozinha.
- **contexto**: evento novo + PADRAO/ESTADO/RESTRICAO antigo do mesmo assunto (mapa de
  assuntos: apresentação↔falar em público/apresentações; entrevista↔emprego/trabalho;
  cirurgia/exame/consulta↔saúde da mesma pessoa; prova↔estudos; viagem↔cidade/lugar citado).
  Sugestão, ex.: "Ela já comentou que apresentações costumam deixá-la nervosa. Talvez seja
  interessante perguntar como ela está alguns dias antes."
- **mesma_pessoa**: pessoa já citada nos últimos 90 dias → "A mãe dela também apareceu em
  12/09: 'vai fazer exames'".
- Semelhança de texto (FTS5 via `banco.buscar_texto` com os `termos` do texto novo) para achar
  candidatos; no máximo 5 relações, as mais fortes. Sem relação: lista vazia (não forçar).

## 10. `busca.py`

```python
def buscar(pergunta: str, banco: Banco, motor: Motor, referencia: date) -> Resposta
```

- Entende intenções comuns: "o que eu sei sobre (a família dela|a Mariana|o trabalho)",
  "qual (é) a comida que ela mais gosta", "o que ela falou sobre a viagem", "quando ela comentou
  sobre X" (ordena por data), "quais (são) as pessoas mais importantes", "o que ela gosta de
  fazer quando está estressada", "ela já falou alguma coisa sobre presentes".
- Expande sinônimos (família → parentescos; estressada → estresse/nervosa/ansiosa;
  presentes → presente/ganhou/gostaria/queria; flores → tulipa/rosa/girassol...; pet →
  gato/cachorro + pessoas do grupo "pet") e filtra por categoria/tipo/pessoa quando a
  intenção for clara; depois FTS5 + `semelhanca`.
- Também entende "quando é o aniversário (dela | da mãe dela | da Mariana)" / "quando X faz
  aniversário" (só DATA_IMPORTANTE da pessoa; sem registro, vale a data de Ajustes ou do
  cadastro) e "o que ela não gosta (de comer/beber...)" / "o que ela odeia" (PREFERENCIA e
  RESTRICAO -1 dela, na categoria do verbo). "Qual X ela prefere" é pergunta de favorito.
- Filtro de dono na busca livre: pergunta que cita outra pessoa só traz itens dela; pergunta
  com "ela"/"dela" (sem outra pessoa) só traz itens dela. Palavras de conversa ("tá", "pra",
  "q", "hj"...) não viram termo de busca. No "o que eu sei sobre ela", restrição e saúde têm
  peso extra (informação de segurança sempre aparece).
- `encontrados`: `[{item_id, registro_id, resumo, trecho, certeza, categoria, data_registro,
  quando ("há 8 meses")}]`, no máximo 10. `texto` resume: "Encontrei 3 registros sobre a
  família dela:". Nada encontrado → "Não encontrei essa informação."
- Com `MotorIA` ativo, `motor.responder(pergunta, encontrados)` escreve o texto; sem, modelo
  de frase.

## 11. `alertas.py`

```python
def hoje(banco: Banco, referencia: date) -> dict
# {"alertas": [Alerta...] (já filtrados e ordenados),
#  "proximos": [{data, texto, dias, tipo, item_id}] (próximos 30 dias),
#  "lembrar": [{texto, item_id, registro_id}] (pendências e estados recentes),
#  "ultimos": [registros recentes resumidos]}
def proximos_eventos(banco: Banco, referencia: date, dias: int = 30) -> list[dict]
```

- **importante** (🔴): evento hoje ou amanhã; relevância 90-100: 90, +5 se é hoje, +5 se ligado
  a preocupação (hoje com preocupação = 100; hoje vem antes de amanhã).
- `lembrar` também leva `certeza` e `trecho` de cada item (a tela mostra selo, citação e, na
  pendência, "Já resolvido" → `PATCH /api/itens/{id} {status: "resolvido"}`).
- **lembrete** (🟡): evento ou data recorrente (aniversário) cuja distância em dias está em
  `config["antecedencias"]` (padrão 30, 15, 7, 3, 1) — "Daqui a 15 dias é o aniversário da mãe
  dela."; mesversário/aniversário de namoro a partir de `config["inicio_namoro"]` (7 dias antes e
  no dia): "Vocês completam 7 meses juntos daqui a 7 dias."
- **sugestao** (💙): pendência cujo `lembrar_em` ≤ hoje e ainda ativa ("Há alguns dias ela
  comentou que estava esperando o resultado de uma entrevista. Talvez seja um bom momento para
  perguntar como foi."); evento que passou há 1-2 dias e tinha preocupação ("Talvez perguntar
  como foi a apresentação"); ESTADO negativo nos últimos 7 dias ("Ela comentou esta semana que
  estava cansada. Talvez perguntar como foi o dia seja uma boa ideia."); APOIO conhecido entra
  no `porque` quando existir ("você registrou que ela prefere que você só fique perto").
- Anti-excesso: todos os importantes, até 3 lembretes e **1** sugestão (a de maior relevância);
  alertas dispensados (`banco.alertas_dispensados()`) não voltam. Chave estável por
  item + data + antecedência.
- Sem dados suficientes, nenhuma sugestão (nada genérico inventado).

## 12. `perfil.py`

```python
def conhecendo_ela(banco: Banco, referencia: date) -> dict
# {"ela": {"nome", "aniversario", "inicio_namoro", "tempo_juntos"},
#  "pessoas": [{id, nome, relacao, infos: [item...], ultimas_mencoes: [{registro_id, data, trecho}],
#               datas: [...], observacoes, mencoes: int, importancia: "muito importante"|...}],
#  "preferencias": {categoria: {"gosta": [...], "nao_gosta": [...], "restricoes": [...],
#                   "comentou_uma_vez": [...], "incertos": [...]}},
#  "como_ela_esta": {"situacao_atual": [...], "o_que_ajuda": [...], "o_que_nao_gosta": [...],
#                    "ultimos_acontecimentos": [...]}}
def forca_preferencia(itens_mesmo_alvo: list[Item], referencia: date) -> str
# "gosta" | "nao_gosta" | "comentou_uma_vez" | "incerto"
```

- "gosta": ao menos 1 CONFIRMADO positivo, ou 2+ OBSERVADOS positivos, sem contradição ativa.
- "comentou_uma_vez": um único OBSERVADO (ou inferência) positivo. Nunca vira "gosta" sozinho.
- "incerto" (🔴): contradição (positivo e negativo ativos) ou mais de 12 meses sem nova menção.
- Cada entrada leva `certeza`, `quando` ("há 8 meses"), `registro_id` para abrir o contexto.
- "Como ela está" **não diagnostica** nada: só lista ESTADOs dos últimos 14 dias, APOIOs e
  eventos recentes, com as palavras registradas.

## 13. `cofre.py`

```python
class PinErrado(Exception): ...
class Cofre:
    def __init__(self, pasta: Path)
    def existe(self) -> bool
    def criar(self, pin: str, dados: bytes) -> None        # PIN com pelo menos config.PIN_MINIMO caracteres
    def abrir(self, pin: str) -> bytes                     # PinErrado se não decifrar
    def salvar(self, dados: bytes) -> None                 # usa a chave guardada em criar/abrir
    def trocar_pin(self, atual: str, novo: str) -> None
    def fechar(self) -> None                               # esquece a chave (bloqueio)
    @property
    def aberto(self) -> bool
    def copiar_backup(self, destino: Path) -> Path         # copia o arquivo cifrado com data no nome
    def reescrever_backups(self, transformar: Callable[[bytes], bytes | None]) -> int
        # passa cada backup automático (que abre com a chave atual) por `transformar` e grava de
        # novo, cifrado; None = não mexe. Usado para apagar dos backups o que foi apagado.
def problema_do_pin(pin) -> str | None                     # mensagem para a tela, ou None se o PIN serve
```

- PIN aceito: pelo menos `config.PIN_MINIMO` caracteres e **não** fácil de adivinhar (quem copia
  o arquivo testa PINs fora do app, sem atraso): nada de só espaços, um ou dois caracteres
  repetidos ("111111", "121212"), sequência ("123456", "abcdef", "qwerty"), lista de PINs comuns;
  só números, a partir de `DIGITOS_MINIMOS` (10) dígitos. A tela repete a regra.

- Formato: `b"ENTRENOS1"` + parâmetros scrypt (n=2**16, r=8, p=1; gravados no cabeçalho) +
  sal 16 bytes + nonce 12 bytes + AES-256-GCM(dados), com o cabeçalho como dado associado.
  Nonce novo a cada `salvar`.
- Gravação atômica: arquivo temporário na mesma pasta + `os.replace`. Antes de substituir,
  guarda a versão anterior em `backups/` (máximo `BACKUPS_AUTOMATICOS`, uma por dia).
  `PermissionError` no `os.replace` (antivírus segurando o arquivo) tenta de novo até 5 vezes; a
  espera entre as tentativas é `cofre.esperar_entre_tentativas`, que o `servidor.py` liga em
  `time.sleep` (no navegador fica `None`: o cofre não dorme sozinho, seção 19.2).
- A chave fica só em memória; `fechar` apaga a referência.
- Desde 07/10/2026 (seção 19.7): `CofreMudou` (o arquivo não é mais o que este cofre leu ou gravou:
  outra aba gravou; `salvar`, `trocar_pin` com o cofre aberto e `conteudo` recusam), `conteudo()`
  (os bytes cifrados, para baixar), `backups_automaticos` (padrão `config.BACKUPS_AUTOMATICOS`; 2
  no navegador) e `copias_extras_maximas` (None = as cópias fora do rodízio nunca saem; no
  navegador, 2 de cada tipo).

## 14. `banco.py`

SQLite **em memória** (`sqlite3.connect(":memory:")`, `check_same_thread=False`, com um
`threading.RLock` em volta de tudo). Carregado de/para bytes com `Connection.deserialize` /
`serialize`. Após cada escrita chama `ao_salvar(self.para_bytes())` (o servidor liga isso ao
`Cofre.salvar`). `para_bytes` faz `VACUUM` antes (apagado some de verdade). A conexão usa
`PRAGMA temp_store = MEMORY`: sem isso o VACUUM de um banco maior que o cache monta a cópia em
`%TEMP%\etilqs_*`, sem criptografia. `Banco.sujo` diz se há mudança que ainda não chegou a um
`ao_salvar` que deu certo (ou a migração de um diário antigo); `marcar_salvo()` é para quem gravou
`para_bytes()` por fora (criar, restaurar). O núcleo só regrava ao bloquear se estiver sujo (19.7).

Tabelas: `config(chave PK, valor JSON)`, `registros(id, criado_em, data_registro, texto,
contexto, humor)`, `itens(id, registro_id, tipo, resumo, trecho, certeza, categorias JSON,
importancia, data_evento, data_expressao, precisao, recorrente, pessoa, pessoa_id, alvo,
polaridade, lembrar_em, status, criado_em, atualizado_em)`, `pessoas(id, nome, relacao,
apelidos JSON, observacoes, aniversario, criado_em)`, `registro_pessoas(registro_id, pessoa_id)`,
`relacoes(id, registro_id, tipo, motivo, item_novo_id, item_antigo_id, registro_antigo_id,
sugestao, status, criado_em)`, `alertas_dispensados(chave PK, quando)`, `historico(id, quando,
acao, tabela, linha_id, resumo)`, FTS5 `busca_fts(conteudo, tipo, ref_id, tokenize="unicode61
remove_diacritics 2")` mantida pelo próprio `Banco`. `PRAGMA user_version` = versão do esquema
(migrações simples em `_migrar`). Esquema 2: `pessoas.nome_registro_id` e
`pessoas.relacao_registro_id` (de qual registro o banco tirou o nome/relação que preencheu
sozinho; editar a pessoa zera). Esquema 3: `registros.privado` (seção 17.4). Esquema 4:
`recursos_esquema(nome, versao)`, a versão das tabelas de cada recurso (seção 20.0). Esquema 5:
`sync_ids`, `sync_linhas`, `sync_lapides`, `sync_aparelhos`, `sync_conflitos` (a sincronização,
seção 22.1; tabelas centrais: nenhum recurso grava nelas).

Apagar é apagar também em volta:
- `apagar_registro` tira da pessoa o nome/relação que vieram só do texto apagado (se outro
  registro dela ainda cita o nome, ele fica);
- apagar registro, item ou pessoa tira de `alertas_dispensados` as chaves que citam o id
  (`data:{id}:…`, `evento:{id}:…`, `pendencia:{id}:…`, `depois:{id}:…`, `estado:{id}`,
  `pessoa:{id}:…`), que revelariam a data do que foi apagado.

```python
class Banco:
    @classmethod
    def novo(cls, ao_salvar=None) -> "Banco"
    @classmethod
    def de_bytes(cls, dados: bytes, ao_salvar=None) -> "Banco"
    def para_bytes(self) -> bytes
    # config (chaves: nome_dela, aniversario_dela, inicio_namoro, antecedencias, minutos_bloqueio,
    #          motor, motor_url, motor_modelo, pergunta_ambigua)
    def config(self) -> dict
    def salvar_config(self, **valores) -> None
    # registros
    def criar_registro(self, texto: str, data_registro: date, itens: list[Item],
                       pessoas: list[PessoaEncontrada], relacoes: list[Relacao],
                       contexto: str | None = None, humor: str | None = None) -> int
        # cria pessoas novas (nova=True e com nome ou relação), resolve pessoa_id dos itens
        # (pelo texto de item.pessoa), grava itens, liga relações (item_novo = índice -> id).
        # Pessoa em dict com "nova_escolhida": true (o usuário marcou "é uma pessoa nova" na
        # revisão) não é procurada no cadastro: vira pessoa nova mesmo com nome repetido.
    def registro(self, id: int) -> dict | None            # com "itens", "pessoas", "relacoes"
    def registros(self, limite: int = 50, antes_de: int | None = None) -> list[dict]
    def apagar_registro(self, id: int) -> None
    # itens
    def item(self, id: int) -> Item | None
    def itens(self, tipo: TipoItem | None = None, categoria: str | None = None,
              pessoa_id: int | None | type(...) = ..., status: str | None = "ativo",
              desde: date | None = None) -> list[Item]    # pessoa_id=... (Ellipsis) = qualquer; None = dela
    def atualizar_item(self, id: int, **campos) -> None
    def acrescentar_item(self, registro_id: int, item: Item) -> int   # item novo num registro existente
    def apagar_item(self, id: int) -> None
    # pessoas
    def pessoas(self) -> list[dict]                        # {id, nome, relacao, apelidos, observacoes, aniversario}
    def pessoa(self, id: int) -> dict | None
    def criar_pessoa(self, nome: str | None, relacao: str | None, apelidos=(), observacoes: str = "") -> int
    def atualizar_pessoa(self, id: int, **campos) -> None
    def apagar_pessoa(self, id: int) -> None               # itens da pessoa ficam, com pessoa_id NULL e pessoa = nome
    def mencoes(self, pessoa_id: int, limite: int = 5) -> list[dict]   # {registro_id, data_registro, trecho}
    # busca
    def buscar_texto(self, consulta: str, limite: int = 20) -> list[dict]
        # consulta = termos soltos; monta a expressão FTS5 com OR e prefixo; devolve
        # [{tipo: "item"|"registro", ref_id, score}] (score maior = melhor). Nunca lança por sintaxe.
    # relações
    def relacoes(self, status: str | None = "pendente", registro_id: int | None = None) -> list[Relacao]
    def resolver_relacao(self, id: int, status: str) -> None
    # alertas
    def dispensar_alerta(self, chave: str) -> None
    def alertas_dispensados(self) -> set[str]
    # histórico e exportação
    def historico(self, limite: int = 100) -> list[dict]
    def exportar(self) -> dict                             # tudo em JSON legível (para o usuário)
```

## 15. `servidor.py`

> **Atualizado pela seção 19 (núcleo e transporte, 07/10/2026).** A API, a sessão, o PIN e o
> bloqueio por inatividade agora moram em `nucleo.py`; aqui ficou o transporte HTTP. O "atraso
> crescente" do PIN errado virou bloqueio temporário com 429 (19.3), e a inatividade é conferida
> a cada pedido, com a thread vigia só no servidor (19.4). Quando esta seção e a 19 discordarem,
> vale a 19.

`ThreadingHTTPServer` em **127.0.0.1** (nunca 0.0.0.0), porta 8787 (se ocupada por outro
programa, tenta 8788-8799; se ocupada pelo próprio Entre Nós — `GET /api/ping` responde
`{"app": "entre-nos"}` — só abre o navegador). `iniciar.py [--sem-navegador] [--porta N]
[--dados PASTA]`.

Segurança:
- Recusa `Host` que não seja `127.0.0.1:porta` ou `localhost:porta` (DNS rebinding).
- Recusa `Origin` presente e diferente da própria origem.
- Toda rota `/api/*` exceto `ping`, `estado`, `configurar`, `desbloquear` exige o cabeçalho
  `X-EntreNos-Token` (gerado com `secrets.token_urlsafe(32)` no desbloqueio; o navegador guarda
  em `sessionStorage`).
- `POST/PATCH/DELETE` só com `Content-Type: application/json`, corpo até 1 MB.
- Cabeçalhos: `Content-Security-Policy: default-src 'self'; img-src 'self' data:; style-src
  'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'`, `X-Frame-Options:
  DENY`, `Referrer-Policy: no-referrer`, `Cache-Control: no-store` na API.
- PIN errado: bloqueio temporário crescente (1 s, 2 s, 4 s... até 30 s): dentro dele, 429 com
  `espere_segundos` e `Retry-After`, sem testar o PIN (seção 19.3). PIN fácil de adivinhar é
  recusado em `/configurar` e `/pin` (`cofre.problema_do_pin`).
- Apagar registro, item ou pessoa repete a remoção nas cópias automáticas de `backups/`
  (`Cofre.reescrever_backups`). Cópias feitas pelo usuário em outra pasta não mudam.
- Bloqueio automático após `minutos_bloqueio` sem requisição: salva, `cofre.fechar()`, descarta
  o `Banco` e o token. O núcleo confere no começo de cada pedido; no computador, uma thread
  verifica também a cada 30 s (seção 19.4).
- Arquivos estáticos só de `web/` (sem `..`), com tipo MIME correto.

API (JSON; datas ISO `AAAA-MM-DD`; erros `{"erro": "mensagem em português"}` com 400/401/403/404/409/423,
e 429 `{"erro", "espere_segundos"}` no bloqueio do PIN, seção 19.3):

| Método e rota | Corpo / parâmetros | Resposta |
|---|---|---|
| GET /api/ping | | `{"app": "entre-nos", "versao"}` |
| GET /api/estado | | `{configurado, desbloqueado, versao, motor: {nome, envia_para_fora, url}}` (19.7: e `plataforma`, `backup_em_pasta`, `limite_arquivo`) |
| POST /api/configurar | `{pin, nome_dela?, inicio_namoro?, aniversario_dela?}` | `{token}` (só se ainda não existe cofre) |
| POST /api/desbloquear | `{pin}` | `{token}` / 401 |
| POST /api/bloquear | | `{}` |
| POST /api/analisar | `{texto, data_registro?}` | `Proposta` (não grava) |
| POST /api/registros | `{texto, data_registro, contexto?, humor?, itens, pessoas, relacoes}` | `{id}` |
| GET /api/registros | `?limite&antes_de` | `[registro]` |
| GET /api/registros/{id} | | registro completo |
| DELETE /api/registros/{id} | | `{}` |
| PATCH /api/itens/{id} | campos | `{}` |
| DELETE /api/itens/{id} | | `{}` |
| GET /api/hoje | `?data` (teste) | ver alertas.hoje |
| POST /api/alertas/dispensar | `{chave}` | `{}` |
| GET /api/perfil | | ver perfil.conhecendo_ela |
| GET /api/pessoas · POST · PATCH /{id} · DELETE /{id} | | |
| GET /api/datas | `?dias=365` | próximos eventos e aniversários |
| GET /api/busca | `?q=` | `Resposta` |
| POST /api/relacoes/{id} | `{acao: "confirmar"|"descartar"|"atualizar_preferencia"}` | `{}` (atualizar: item antigo → status "mudou"; se o novo não é preferência — "ela pediu sushi" — vira/ganha a PREFERENCIA oposta, observado) |
| GET /api/config · PATCH /api/config | | |
| POST /api/pin | `{atual, novo}` | `{}` |
| POST /api/backup | `{pasta}` | `{arquivo}` (no navegador: 409 `{baixar: true}`, 19.7) |
| POST /api/backup/baixar | | `{nome, tamanho, arquivo_b64}`: o `.cofre` de agora, para baixar (19.7) |
| GET /api/exportar | | JSON completo (a tela avisa que sai sem criptografia) |
| GET /api/historico | | |
| POST /api/sair | | encerra o servidor depois de salvar |

## 16. Interface (`web/`)

SPA em HTML/CSS/JS puro (sem framework, sem CDN, sem fonte externa; fonte do sistema
"Segoe UI", system-ui). Rotas por `#hash`. Tudo em português. Funciona em tela de celular
(largura 360 px) e desktop. Modo claro e escuro (`prefers-color-scheme`).

Paleta (tokens em `:root`): fundo creme `#FBF7F2`, superfície `#FFFFFF`, texto tinta
`#2B2D42`, secundário `#6C6F82`, primária azul-noite `#3D5A80`, destaque coral suave
`#E07A5F`, confirmado verde `#2A9D8F`, observado âmbar `#E9C46A`, inferência azul `#5B8DEF`,
incerto vermelho `#D1495B`. Escuro: fundo `#1B1D2A`, superfície `#252838`, texto `#ECEAF2`.
Cantos arredondados, sombras leves, muito espaço, ícones em emoji ou SVG inline.

Telas:
1. **Primeiro uso**: boas-vindas, o que fica onde ("Tudo fica neste computador, criptografado.
   Sem IA ligada, nada sai daqui."), criar PIN (mín. 6, recomenda frase), nome dela, início do
   namoro, aniversário dela.
2. **Bloqueio**: PIN. Bloqueia sozinho (o servidor responde 423 → volta para esta tela).
3. **💙 Hoje** (`#hoje`, início): alertas por nível (🔴 Importante, 🟡 Lembrete, 💙 Sugestão, cada
   um com "por quê?" expansível e "dispensar"), Próximos acontecimentos, Coisas para lembrar,
   Últimos registros, botão grande "Registrar algo".
4. **✍️ Registrar** (`#registrar`): uma caixa de texto grande ("O que ela te contou?"), data (hoje
   por padrão), campos opcionais recolhidos (contexto, como ela estava). "Organizar" → tela de
   revisão: cartões por item (tipo, resumo, trecho citado, selo de certeza clicável para trocar,
   categorias editáveis, data editável, remover), pessoas detectadas (confirmar nome/relação),
   datas ambíguas com botões para escolher, relações ("Isso se relaciona com...") e
   contradições com as opções. "Salvar" grava. Atalho Ctrl+Enter.
5. **🌷 Conhecendo Ela** (`#ela`): abas Pessoas, Preferências, Como ela está.
6. **📅 Datas** (`#datas`): próximos 12 meses agrupados por mês; antecedências configuráveis.
7. **🔎 Buscar** (`#buscar`): pergunta em linguagem natural, exemplos clicáveis, resposta com os
   registros citados (clicar abre o registro).
8. **📖 Registros** (`#registros`): lista em ordem de data (linha do tempo simples); abrir mostra
   texto, itens, pessoas, relações; editar item, apagar registro (confirmação).
9. **⚙️ Ajustes** (`#ajustes`): dados dela, antecedências, minutos de bloqueio, trocar PIN,
   backup (pasta), exportar (aviso), histórico, "Privacidade" (onde fica cada coisa), "IA"
   (desligada; explica que pode ser ligada depois e o que isso significa), "Sair".

Selos de certeza em todo lugar: 🟢 Confirmado, 🟡 Observado, 🔵 Inferência, 🔴 Incerto, com
legenda. Textos acolhedores, nunca de "CRM" (a importância das pessoas só ordena a lista).

Privacidade na tela:
- Campos de PIN com `autocomplete="one-time-code"` (e marcas para gerenciadores de senha): o
  navegador não oferece "Salvar senha?". A tela avisa para não salvar o PIN.
- Ao bloquear, a página esquece a revisão (cita registros antigos), os ajustes e o motor; só o
  rascunho que a pessoa está escrevendo fica. Diálogo aberto e avisos fecham.
- Datas com a precisão que foi dita: mês aparece como "novembro de 2026", semana como "semana de
  05/10"; período em andamento não ganha "há N dias".
- Antecedências: só de 2 dias em diante (no dia e na véspera o aviso é sempre Importante);
  salvar sem nenhuma pede confirmação.

## 17. IA sob demanda, vários provedores e PIN da IA (decidido pelo usuário em 30/09/2026)

Esta seção **substitui** tudo o que as seções 8, 14, 15 e 16 dizem sobre `config["motor"]`,
`motor_url`, `motor_modelo` e `motor_chave`.

### 17.1 Decisões do usuário

1. **As regras são sempre o motor.** `POST /api/analisar` e `GET /api/busca` usam só `MotorRegras`,
   com ou sem IA configurada. Não existe modo automático.
2. **A IA só roda por botão ✨**, em três lugares: revisão do registro ("Analisar a fundo com IA"),
   busca ("Perguntar à IA") e Hoje ("Pensar numa sugestão").
3. **Principal + reserva.** Ordem: principal → reserva → regras (com aviso de quem respondeu).
4. **PIN da IA** (segundo PIN) protege **só a área Ajustes → IA** (ver qual IA está escolhida, URL,
   modelo, chave; trocar; testar). Os botões ✨ funcionam para quem abriu o app com o PIN principal.
   **O PIN da IA é uma trava na tela, não criptografia:** a chave fica no banco cifrado pelo PIN
   principal (os ✨ precisam dela), então quem sabe o PIN principal e tem o arquivo `.cofre` lê a
   chave e pode testar palpites do PIN da IA fora do app. Ele protege contra quem encontra o app
   aberto, e a tela diz isso. Cifrar a chave com o PIN da IA faria os ✨ pedirem o PIN da IA
   (decisão do usuário, se quiser).
5. **🔒 Não enviar para a IA**, por registro. Registro privado nunca sai do computador: não pode ser
   analisado pela IA e fica fora do que a busca e as sugestões mandam para a IA — inclusive como
   contagem ("aparece em 2 registros") e como nome ou relação que o cadastro aprendeu nele (17.9).
6. **Claude (Anthropic) fica de fora por enquanto.** Chave `sk-ant-` é reconhecida só para dizer
   que ainda não é suportada.
7. Nada de pacote novo: tudo com `urllib`.

### 17.2 `entrenos/provedores.py` (novo)

```python
@dataclass(frozen=True)
class Provedor:
    id: str                    # "ollama" | "lmstudio" | "groq" | "openrouter" | "google" | "openai" | "mistral" | "outro"
    nome: str                  # para a tela
    url: str                   # base padrão já com a versão ("https://api.groq.com/openai/v1"); "" em "outro"
    tipo: str                  # "ollama" (API nativa /api/chat) | "openai" (formato comum /chat/completions)
    precisa_chave: bool
    prefixos: tuple[str, ...]  # começo da chave que identifica o serviço
    local: bool                # roda neste computador
    aviso: str | None          # alerta de privacidade para a tela (None = sem alerta especial)
    pagina_chave: str | None   # onde o usuário cria a chave (só texto/link na tela)

PROVEDORES: list[Provedor]
NAO_SUPORTADOS: dict[str, str]           # prefixo -> mensagem ("sk-ant-": "Claude ... ainda não está disponível")
AMBIGUOS: dict[str, str]                 # prefixo de vários serviços -> dica ("sk-": OpenAI, DeepSeek, Moonshot...)
def provedor(id: str) -> Provedor | None
def detectar_por_chave(chave: str) -> Provedor | None     # prefixo mais longo vence (sk-or- antes de sk-proj-);
                                                           # "sk-" sozinho (ambíguo) dá None
def chave_ambigua(chave: str) -> str | None                # a dica de AMBIGUOS, se o começo é de vários serviços
def detectar_por_url(url: str) -> Provedor | None
def normalizar_url(url: str, tipo: str, versao: bool = True) -> str
    # tira barra final; "outro" sem versão ganha /v1, a não ser com versao=False (url_exata, 17.9)
def e_local(url: str, modelo: str | None) -> bool          # loopback e modelo que não é "-cloud"/":cloud"
```

| id | nome | url | tipo | chave | prefixos | aviso |
|---|---|---|---|---|---|---|
| ollama | Ollama (neste computador) | http://127.0.0.1:11434 | ollama | não | | — |
| lmstudio | LM Studio (neste computador) | http://127.0.0.1:1234/v1 | openai | não | | — |
| groq | Groq | https://api.groq.com/openai/v1 | openai | sim | gsk_ | o texto sai do país; no painel da Groq dá para ligar retenção zero |
| openrouter | OpenRouter | https://openrouter.ai/api/v1 | openai | sim | sk-or- | modelos terminados em ":free" podem guardar ou usar os textos para treino |
| google | Google Gemini | https://generativelanguage.googleapis.com/v1beta/openai | openai | sim | AIza | **no plano grátis o Google usa os textos para melhorar produtos e permite revisão humana — não recomendado para este diário** |
| openai | OpenAI | https://api.openai.com/v1 | openai | sim | sk-proj-, sk-svcacct- (`sk-` sozinho é ambíguo) | — |
| mistral | Mistral | https://api.mistral.ai/v1 | openai | sim | | no plano grátis a Mistral usa os dados para treino por padrão |
| outro | Outro (compatível com OpenAI) | (digitar) | openai | opcional | | depende do serviço |

Endereços no formato comum: `{url}/chat/completions` e `{url}/models`. Ollama: `{url}/api/chat` e
`{url}/api/tags`.

### 17.3 `motor.py`

```python
def obter_motor(config) -> MotorRegras          # sempre as regras (a IA não é mais "o motor")
class ErroIA(ValueError):                        # .mensagem (para a tela), .tipo, .causa (só o nome do erro)
class MotorIA:
    def __init__(self, provedor: str, url: str | None, modelo: str, tipo: str | None = None,
                 chave: str | None = None, tempo_limite: float = 120,
                 url_exata: bool = False)   # url/tipo None = os do provedor; url_exata: 17.9
    # tentar_analisar / tentar_responder / tentar_sugerir levantam ErroIA (a cadeia passa adiante);
    # analisar / responder / sugerir nunca levantam (sem IA, ficam as regras).
    def analisar(self, texto, referencia, conhecimento, base: Proposta | None = None) -> Proposta
    def responder(self, pergunta, trechos) -> Resposta
    def sugerir(self, contexto: list[dict], referencia: date) -> list[dict]   # [{texto, porque, registro_ids}]
class CadeiaIA:                                  # principal -> reserva -> regras
    def __init__(self, principal: MotorIA | None, reserva: MotorIA | None)
    def analisar(self, texto, referencia, conhecimento, base) -> tuple[Proposta, str | None, list[str]]
    def responder(self, pergunta, trechos) -> tuple[Resposta, str | None, list[str]]
    def sugerir(self, contexto, referencia) -> tuple[list[dict], str | None, list[str]]
    # segundo valor: "principal" | "reserva" | None (ninguém respondeu); terceiro: avisos para a tela
def obter_cadeia_ia(config: dict) -> CadeiaIA | None       # None se não há IA configurada
def problemas_da_config_ia(config: dict) -> dict[str, str] # {qual: por que a IA salva não dá para usar} (17.9)
def so_o_escrito(entrada: dict, texto: str, pessoas: list[dict]) -> dict   # só o escrito no registro (17.9)
def listar_modelos(provedor, url, tipo, chave, tempo=15, url_exata=False) -> list[str]   # ordenados
def testar_ia(cfg_ia: dict, tempo=None) -> dict   # {ok, segundos, formato, erro}; usa frase INVENTADA
def comparar_propostas(regras: Proposta, ia: Proposta) -> list[dict]
    # UMA lista: uma entrada por item da IA, na ordem de ia.itens,
    #   {indice, indice_regras, situacao: "igual"|"mudou"|"novo", campos: [nomes]},
    # seguida dos itens das regras que a IA tirou:
    #   {indice: None, indice_regras, situacao: "removido", campos: [], resumo, tipo}
def ia_envia_para_fora(cfg_ia: dict | None) -> bool
```

- **Formato de resposta em cascata**, por modelo (lembra o que funcionou enquanto o app está
  aberto): formato comum `response_format` `json_schema` estrito → `json_object` → sem
  `response_format`, com o sistema pedindo "responda apenas com JSON"; Ollama `format` = esquema →
  `format: "json"`. O Python extrai o JSON (aceita cercas ``` e texto antes/depois do primeiro
  objeto) e **valida tudo como na seção 8** (trecho literal, categoria válida, data pelo Python,
  certeza nunca sobe). Erro HTTP 400/404/422 ligado ao formato → tenta o próximo; 401/403 →
  "chave recusada"; 429 → "limite do serviço"; tempo esgotado → próximo da cadeia.
- `sugerir`: recebe itens recentes e relevantes (já sem registros privados e só com o que está
  escrito em cada registro, 17.9), pede no máximo 3 gestos pequenos e genuínos com `porque` e
  `registro_ids`. Descarta sugestão sem citação válida, que não começa como possibilidade
  ("Talvez", "Pode ser", "Que tal", "Quem sabe"...; princípio 5) ou com linguagem de
  controle/manipulação (lista de termos: "fazer ela", "convencer", "para que ela", "ciúme",
  "testar ela", e também afastamento e silêncio de propósito — "sumir uns dias", "ignorar as
  mensagens dela", "não responder logo", "sentir sua falta", "o quanto precisa de você" —,
  "fingir", "deixar ela insegura", "sua ex", "influenciar", "induzir", "provocar ela"...). Sem nada
  válido: lista vazia (a tela diz "Não tenho informação suficiente para sugerir algo específico.").
- Nada de nome de provedor, URL ou chave em mensagem de erro para a tela além do necessário;
  nunca logar texto.

### 17.4 Banco (esquema 3)

- `registros.privado INTEGER NOT NULL DEFAULT 0`; `registro()`/`registros()` devolvem `privado`;
  `Banco.registros_privados() -> set[int]`; `Banco.marcar_privado(id, privado: bool)`.
- Config nova: `ia_principal` e `ia_reserva` (`{provedor, url, modelo, tipo, chave}` ou `None`),
  `ia_pin` (verificador scrypt `{sal, n, r, p, hash}` em base64, ou `None`), `ia_tempo_limite`
  (120). Chaves antigas `motor`, `motor_url`, `motor_modelo`, `motor_chave`, `motor_tempo_limite`:
  a migração v2 → v3 converte um motor antigo configurado em `ia_principal` e apaga as antigas.
- **Segredos:** `ia_principal.chave`, `ia_reserva.chave` e `ia_pin` nunca saem em `GET /api/config`,
  `exportar()`, histórico nem erro. `PATCH /api/config` recusa chaves `ia_*` e `motor*` (400).

### 17.5 `cofre.py`

```python
def criar_verificador(pin: str) -> dict                 # scrypt n=2**15, sal novo; guarda só o hash
def conferir_verificador(pin: str, verificador: dict) -> bool   # hmac.compare_digest
class Cofre:
    def confere_pin(self, pin: str) -> bool             # o PIN principal bate com a chave aberta?
```

### 17.6 API

Rotas novas (todas com o token normal). **(PIN IA)** = exige a área liberada pelo PIN da IA
(`POST /api/ia/entrar`), senão 403 `{"erro": "...", "pin_ia": true}`. A área se fecha sozinha
após 10 min, no bloqueio do app e em `POST /api/ia/sair`.

| Método e rota | Corpo | Resposta |
|---|---|---|
| GET /api/ia/estado | | `{pin_definido, liberada, configurada, utilizavel, reserva_configurada, sai_do_computador, espera_maxima, segundos_restantes}` (sem nome, URL nem modelo; 17.9) |
| POST /api/ia/pin | `{novo}` | cria o PIN da IA (só se não existe) e libera; mesmas regras de força do PIN principal e **diferente do PIN principal** |
| POST /api/ia/entrar | `{pin_ia}` | libera; PIN errado: 401 e bloqueio temporário crescente (contador próprio; 429 dentro dele, seção 19.3) |
| POST /api/ia/sair | | fecha a área |
| POST /api/ia/pin/trocar **(PIN IA)** | `{atual, novo}` | |
| POST /api/ia/redefinir | `{pin}` (PIN **principal**) | "esqueci o PIN da IA": apaga `ia_pin`, `ia_principal` e `ia_reserva`; o diário fica |
| GET /api/ia/config **(PIN IA)** | | `{principal, reserva, tempo_limite, provedores: [...], nao_suportados, ambiguos, problemas}`; cada IA: `{provedor, nome_provedor, url, modelo, tipo, tem_chave, chave_final (4 últimos), local, aviso, url_exata?}`; `problemas`: `{qual: por que a IA salva não dá para usar}` |
| PUT /api/ia/config/{principal\|reserva} **(PIN IA)** | `{provedor, url?, modelo, chave?, url_exata?}` | `chave` ausente = mantém; `""`/`null` = remove; `url_exata` (só "Outro"): endereço sem o /v1 automático |
| DELETE /api/ia/config/{principal\|reserva} **(PIN IA)** | | |
| PATCH /api/ia/config **(PIN IA)** | `{tempo_limite}` | |
| POST /api/ia/modelos **(PIN IA)** | `{provedor, url?, chave?, qual?, url_exata?}` | `{modelos: [...]}` (sem `chave` e com `qual`, usa a chave salva); em "Outro", se só o endereço como digitado respondeu: `{modelos, url, url_exata: true}` |
| POST /api/ia/testar **(PIN IA)** | `{qual}` | `{ok, segundos, formato, erro}` |
| POST /api/ia/analisar | `{texto, data_registro, privado?}` | `{proposta, mudancas, ia_usada, avisos}`; privado → 403; sem IA → 409 |
| POST /api/ia/busca | `{q}` | `Resposta` + `ia_usada`, `avisos` (POST para a pergunta não ir na URL) |
| POST /api/ia/sugestao | `{data?}` | `{sugestoes: [{texto, porque, registro_ids}], ia_usada, avisos}` |
| PATCH /api/registros/{id} | `{privado}` | marca/desmarca "não enviar para a IA" |

`POST /api/registros` aceita `privado`. `GET /api/estado` passa a trazer `ia: {configurada,
utilizavel, sai_do_computador, espera_maxima}` no lugar de `motor`. `GET /api/busca` traz `privado`
em cada achado e `para_ia` (há trecho que possa ir para a IA?).

- **A chave nunca segue para outro endereço sem ser digitada de novo:** `PUT` (e `modelos` com
  `qual`) que troque o host da URL em relação ao salvo, sem `chave` nova, é recusado (400
  "Informe a chave de novo para o novo endereço"). Trocar de provedor também apaga a chave antiga.
- Pedidos à IA não seguem redirecionamento nem proxy do sistema (como já é hoje).

### 17.7 Interface

- **Ajustes → 🤖 IA**: sem PIN da IA → explica e oferece "Criar PIN da IA". Com PIN e fechada →
  pede o PIN da IA ("Esqueci o PIN da IA" pede o PIN principal e apaga só a configuração da IA).
  Liberada → cartões **IA principal** e **IA de reserva**: serviço (lista), chave (campo de senha,
  `autocomplete="off"`; colar a chave escolhe o serviço sozinho; salva mostra `••••7f3a`), URL
  (preenchida; editável em "Outro"), "Buscar modelos" → lista, "Testar conexão" (frase inventada),
  Salvar, Remover. Selo "neste computador" / "na internet" e o `aviso` do serviço em destaque.
  Tempo limite, trocar PIN da IA, "Fechar esta área".
- **Registrar**: caixinha "🔒 Não enviar este registro para a IA" (também no detalhe do registro).
  Na revisão, com IA configurada e registro não privado: botão **✨ Analisar a fundo com IA** e,
  ao lado, "vai para uma IA na internet" ou "IA neste computador". Enquanto espera: "a IA pode
  levar até N min" (`espera_maxima`: tempo limite × IAs utilizáveis). O botão das regras é
  "🗂️ Organizar" (o ✨ é só dos botões de IA). Resultado: os cartões trocam pelos da IA com marcas "mudou"/"novo pela IA",
  lista do que a IA tirou, e **"↩ Voltar ao que as regras entenderam"**. Avisos de quem respondeu.
- **Buscar**: depois da resposta das regras, **✨ Perguntar à IA** (não aparece quando `para_ia` é
  falso: "Os registros achados estão marcados para não ir para a IA").
- **Hoje**: cartão "💡 Sugestão" com **✨ Pensar numa sugestão**; cada sugestão mostra "por quê",
  links para os registros e "é só uma possibilidade".
- Sem IA configurada **e utilizável**, os botões ✨ não aparecem (em Ajustes → IA há o convite para
  configurar, ou o aviso "IA configurada, mas incompleta" com o motivo em cada cartão).
- A faixa "IA externa ligada" em todas as telas sai: a IA não roda sozinha.

### 17.8 Como ficou na implementação (integração de 30/09/2026)

Detalhes decididos ao construir; valem junto com 17.1 a 17.7.

**Motor e provedores**
- Cascata de formato: 400/404/422 descem para o próximo formato; 400/422 que citam
  `temperature` repetem o mesmo formato sem `temperature`. 5xx, tempo esgotado, 401/403, 429 e
  JSON ilegível **não** descem a cascata: vão direto para a próxima IA da cadeia. 400/422 cujo
  corpo não fala de formato (`response_format`, `json_schema`, `schema`, `format`) mas de texto
  grande demais (`context length`, `too many tokens`...) viram "grande", e de modelo que não
  existe viram "nao_encontrado", sem descer (17.9).
- Memória do formato que funcionou: no módulo, por `(tipo, url, modelo)` (a cadeia é montada a
  cada pedido). `testar_ia` esquece e redescobre; `esquecer_formatos()` limpa tudo.
- `FRASE_TESTE` é a frase inventada do "Testar conexão"; o corpo enviado não leva chave, data
  de hoje nem nomes do cadastro.
- Chave só vai por `https://`, a não ser para loopback ou IP de rede privada. Chave com
  caractere que não cabe em cabeçalho HTTP e chave `sk-ant-` são recusadas.
- Pedido de análise não manda nomes que vieram só do cadastro (o nome vira a relação ou o
  trecho escrito no texto). `pessoas_citadas` leva `trecho`.
- `responder`: resposta exatamente "Não encontrei essa informação." sem citação é aceita.
  `sugerir`: sugestão com qualquer citação inválida cai inteira; se todas caem, é falha (vai
  para a reserva); lista vazia da IA é resposta válida. Se nenhuma IA deu sugestão válida mas
  alguma respondeu, `CadeiaIA.sugerir` devolve `[]` com quem respondeu (a última) e o aviso
  "respondeu, mas descartei N sugestões..." — não "Nenhuma IA respondeu" (17.9).
- `listar_modelos` tira modelos que não são de conversa (embed, whisper, tts, dall-e,
  moderation, transcri, image, rerank) e o prefixo `models/` do Google.
- Pedidos à IA levam `User-Agent: EntreNos/<versão>`. `envia_para_fora(config)` ficou só por
  compatibilidade e sempre dá False; `ia_envia_para_fora` aceita uma IA ou a configuração inteira.

**Servidor e banco**
- `GET /api/config` e `GET /api/exportar` tiram **todas** as chaves `ia_*` (qual IA está escolhida
  é da área do PIN da IA). `Banco.exportar()` sozinho tira só a chave (põe `tem_chave`) e `ia_pin`.
  O histórico registra só o nome do ajuste ("ajustes alterados: ia_reserva"), nunca o valor.
- Recusa de endereço novo sem chave: compara (esquema, host, porta) com o salvo, e só quando há
  chave salva do mesmo serviço. `PUT` também recusa serviço que precisa de chave sem chave, chave
  por http para host público, `sk-ant-` e campos desconhecidos.
- "PIN da IA igual ao principal?" ganha o atraso do PIN principal depois de
  `COMPARACOES_SEM_ESPERA` (3) comparações (desde a seção 19.3, o bloqueio temporário do PIN
  principal); `/api/pin` não compara o PIN principal novo com o PIN da IA (seria um oráculo sem
  atraso).
- A área da IA fica aberta por `minutos_area_ia` (10) contados de quando abriu; não renova com o uso.
  `GET /api/ia/estado` diz quanto falta (`segundos_restantes`) e a tela conta a partir dali.
- `configurada` = principal **ou** reserva configurada.
- `/api/ia/busca`: sem trecho que possa ir para a IA (nada achado, ou só registros 🔒), a IA nem é
  chamada e a resposta vem com `ia_usada: null` e `encontrados: []` (a tela usa isso para dizer
  "nada saiu daqui"); com `ia_usada: null` e `encontrados` preenchido, a IA foi chamada e falhou.
- `/api/ia/sugestao`: contexto de `servidor.contexto_sugestao` (itens ativos dos últimos 30 dias;
  eventos, datas e pendências de 3 dias atrás a 30 dias à frente; APOIO e RESTRICAO; no máximo 40;
  nunca de registro 🔒). Contexto vazio: a IA não é chamada.
- Erro ao falar com a IA em `/api/ia/modelos` → 502 (504 no tempo esgotado), com a `mensagem` do
  `ErroIA`. `ErroHttp` ganhou `extras` (é assim que sai `{"pin_ia": true}`); o servidor ganhou `PUT`.
- Migração v2 → v3: URL do formato comum ganha `/v1`; provedor por `detectar_por_url` quando o
  tipo é "openai", senão "outro"; Ollama vira "ollama"; URL com usuário e senha não é convertida;
  `motor_tempo_limite` vira `ia_tempo_limite` (5–600 s). Toda chave `motor*` some e
  `salvar_config` passa a recusá-las.

**Interface**
- Colar a chave troca o serviço sozinho, **menos** quando o escolhido é "Outro": aí só sugere.
  Chave `sk-` sem outro prefixo conhecido é ambígua: não escolhe nada, só mostra a dica.
- "Apagar a chave salva" (caixinha) manda `chave: ""` no `PUT`.
- Para serviços que não são "Outro" a URL fica só para leitura, mas a tela manda a URL mostrada
  (a salva do mesmo serviço, senão a padrão), para não trocar em silêncio um endereço migrado.
- Na revisão, ao trocar os cartões entre regras e IA, as pessoas e as datas ambíguas escolhidas
  ficam; itens, relações e avisos vêm da proposta nova.

### 17.9 Correção final (revisão de segurança e funcionamento, 30/09/2026)

Valem junto com 17.1 a 17.8; onde discordarem, vale esta.

**Só o que está escrito no registro vai para a IA (busca e sugestão, como já era na análise)**
- `motor.so_o_escrito(entrada, texto, pessoas) -> dict`: cópia de um achado da busca ou de um item
  do contexto da sugestão em que nome, apelido (e partes de nome composto) ou relação do cadastro
  que **não estão escritos no texto do próprio registro** saem de `resumo`, `pessoa` e `alvo`:
  nome vira a relação escrita (ou outro nome escrito); "Nome (relação)" perde a parte que não
  está escrita; sem troca possível, o resumo vira o próprio `trecho` e `pessoa`/`alvo` viram None.
  Conferência final pelo texto normalizado (acento de outro jeito não escapa).
  `servidor.so_o_escrito(banco, entradas)` aplica isso com o texto de cada registro.
- `busca.buscar(..., privados=None)`: com `privados`, registro 🔒 não entra nos achados, não conta
  menção de pessoa ("aparece em N registros"), não vira favorito nem aniversário. `/api/ia/busca`
  busca com `privados` e limpa com `so_o_escrito`; o aviso "ficaram de fora" sai quando a busca
  normal achou algum 🔒. Na resposta, os achados citados voltam como a busca mostra (sem limpar).
- `servidor.contexto_sugestao` já devolve os itens limpos.

**Nada do serviço escolhido fora da área do PIN da IA**
- `proposta.motor` e `motor` da busca ✨ saem como `"ia:principal"`/`"ia:reserva"` (`"regras"` sem
  IA): nunca o `nome` do `MotorIA` (serviço/modelo), que fica só dentro do motor.

**A configuração da IA some também dos backups automáticos**
- `DELETE /api/ia/config/{qual}` tira aquela IA das cópias; `PUT` que apaga ou troca a chave tira a
  chave antiga das cópias; trocar o PIN da IA põe o verificador novo nas cópias que tinham um;
  "Esqueci o PIN da IA" tira `ia_pin`, `ia_principal` e `ia_reserva` das cópias
  (`_limpar_backups`, como já era com registros, itens e pessoas apagados).

**PIN da IA que é variação do principal**
- "Igual ao principal?" confere também as variações óbvias (sem espaço nas pontas, espaços
  repetidos, minúsculas, MAIÚSCULAS, Primeira maiúscula, Cada Palavra): até 7 scrypts num pedido,
  que conta como uma comparação. Tirar os espaços não dá para detectar: a tela pede que o PIN
  da IA não seja uma variação do principal.

**IA salva que não dá para usar**
- `motor.problemas_da_config_ia(config) -> {qual: motivo}` (motor v2 migrado sem chave, chave por
  http:// para a internet...). `GET /api/estado` e `/api/ia/estado` trazem `utilizavel`; a tela só
  mostra os ✨ com `utilizavel`. Os ✨ respondem 409 com o motivo ("A IA configurada não pode ser
  usada assim. IA principal: Este serviço precisa de uma chave. Confira em Ajustes → IA."), não
  "Nenhuma IA configurada". `GET /api/ia/config` traz `problemas` para o cartão de cada IA.

**Rede e cascata**
- O corpo da resposta é lido com `read1` e o tempo limite do socket encolhe junto com o prazo: corpo
  que pinga devagar não passa do tempo limite; corpo que chegou inteiro em cima do prazo vale.
- 400/422 de texto grande demais ou de modelo que não existe não descem a cascata (17.8).
- `/api/ia/modelos` com tempo esgotado (`ErroIA` tipo "tempo") responde 504.

**Sugestões**
- Precisam começar como possibilidade e a lista de manipulação cobre afastamento, silêncio,
  fingimento, insegurança, "sua ex", influenciar/induzir e provocar (17.3).
- Resposta da IA sem nenhuma sugestão válida: `[]` com `ia_usada` preenchido e o aviso do
  descarte; a tela mostra "Não tenho informação suficiente para sugerir algo específico."

**Chaves e endereços**
- `sk-` sozinho é ambíguo (DeepSeek, Moonshot, Qwen, SiliconFlow também usam): OpenAI só por
  `sk-proj-`/`sk-svcacct-`; `GET /api/ia/config` traz `ambiguos` e a tela só sugere.
- "Outro" com base sem versão (GitHub Models, `.../inference`): "Buscar modelos" tenta a URL com
  /v1 e, se ali der 404 ou resposta que não é de IA, a URL como foi digitada (mesmo host); dando
  certo, a tela passa a mandar `url_exata: true` e a IA é salva com `"url_exata": true`
  (`banco.CAMPOS_IA`, `MotorIA(..., url_exata=)`, `listar_modelos(..., url_exata=)`), sem ganhar /v1.
  A consulta (`?api-version=...`) continua sendo tirada: Azure usa outro cabeçalho de chave e não é
  suportado.

**Interface**
- Espera dos ✨ e do "Testar conexão": "até N min" pelo tempo limite (× principal e reserva).
- "🗂️ Organizar" no lugar de "✨ Organizar".
- Texto do PIN da IA: fecha a área para quem encontrar o app aberto; quem sabe o PIN principal
  consegue ler a chave.
- A área da IA conta o tempo pelo `segundos_restantes` do servidor.
- Busca: achado de registro 🔒 vem com `privado: true` e, se todos são 🔒 (e a busca sem eles
  não acha nada), `para_ia: false` e o ✨ Perguntar à IA não aparece.

## 18. Restaurar backup, importar JSON e instalador (decidido pelo usuário em 30/09/2026)

### 18.1 Decisões do usuário

1. **Restaurar backup** (`.cofre`): na tela de primeiro uso **e** em Ajustes. Digita o PIN daquele
   backup; o app confere que abre (e que é um diário do Entre Nós válido) **antes** de trocar
   qualquer coisa; guarda uma cópia do diário atual; depois substitui. Depois de restaurar, o PIN
   passa a ser o do backup.
2. **Importar JSON — juntar** (o arquivo de `GET /api/exportar`): em Ajustes. Mostra um resumo antes
   e junta sem duplicar. **Não existe** "importar JSON substituindo".
3. **Instalador `EntreNos-Setup-<versão>.exe`** (PyInstaller em pasta + Inno Setup), sem pedir
   administrador, atalhos no menu Iniciar e (opcional) na área de trabalho, desinstalador.
   **Abre no navegador padrão** (nada de janela do Edge).
4. Tudo roda na máquina onde foi instalado; cada máquina tem seu diário
   (`%LOCALAPPDATA%\EntreNos`); os dados passam de uma para outra por backup/restauração.

### 18.2 API

Corpo em JSON com o arquivo em base64. Estas rotas aceitam corpo de até **64 MB** (as outras
continuam com 1 MB).

| Método e rota | Quando | Corpo | Resposta |
|---|---|---|---|
| POST /api/restaurar | primeiro uso (sem cofre; pública como `configurar`) | `{arquivo_b64, pin}` | `{token, resumo}`; PIN errado 401 com o bloqueio do PIN (19.3); arquivo que não é cofre do Entre Nós 400 |
| POST /api/restaurar | com o app desbloqueado (token) | `{arquivo_b64, pin, pin_atual}` | confere `pin_atual` (o PIN principal de agora; errado → 401 com bloqueio, 19.3), guarda o diário atual em `backups/entrenos-antes-da-restauracao-AAAA-MM-DD-HHMMSS.cofre`, troca, abre sessão nova e devolve `{token, resumo}` |
| POST /api/importar/previa | desbloqueado | `{json}` (texto do arquivo) | `{registros_novos, registros_repetidos, pessoas_novas, pessoas_existentes, itens, avisos}`; nada é gravado |
| POST /api/importar | desbloqueado | `{json, confirmar: true}` | mesmo resumo + `{importados}` |

- `resumo` da restauração: `{registros, pessoas, primeiro_registro, ultimo_registro}`.
- A restauração valida o arquivo inteiro antes: cabeçalho `ENTRENOS1`, decifra com o PIN,
  `Banco.de_bytes` funciona (com migração de esquema), `PRAGMA integrity_check` = ok. Só então
  grava (atômico). Arquivo com parâmetros de scrypt absurdos é recusado (como o `Cofre` já faz).
- A área da IA (PIN da IA) e a configuração de IA passam a ser as do backup.
- **Juntar JSON:** aceita só o formato de `exportar()` (versão/app conferidos). Registro repetido =
  mesma `data_registro` + mesmo texto (normalizado) → ignorado. Pessoa: casa por nome/apelido/
  relação (como `casar_pessoa`); senão cria. Itens e relações recebem os ids novos; relação que
  aponta para registro/item que não veio é descartada. `privado` é preservado. Config, PIN da IA,
  chaves e alertas dispensados do JSON **não** são importados. Histórico: "importação: N
  registros" (sem conteúdo). JSON malformado/grande demais/campos de tipo errado → 400 sem gravar
  nada (tudo numa transação).

### 18.3 Empacotamento (`instalador/`)

- `instalador/entrenos.spec` (PyInstaller, pasta única, sem console, ícone), `instalador/EntreNos.iss`
  (Inno Setup, em português: `Languages\BrazilianPortuguese.isl`), `instalador/entrenos.ico` (gerado
  com Pillow: coração), `construir_instalador.ps1` na raiz (roda os testes, o PyInstaller e o ISCC
  em `%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe`; saída em `dist\`). `build/` e `dist/` no
  `.gitignore`.
- O programa instalado aceita os mesmos argumentos do `iniciar.py` (`--sem-navegador`, `--porta`,
  `--dados`). Em modo empacotado (`sys.frozen`), `web/` vem de dentro do pacote e os erros vão para
  `erros.log` na pasta de dados (sem conteúdo).
- Instala em `{localappdata}\Programs\Entre Nos` (`PrivilegesRequired=lowest`), `AppId` fixo, versão
  = `config.VERSAO` (sobe para 0.2.0). Reinstalar por cima atualiza e mantém os dados.
- **O desinstalador nunca apaga dados em modo silencioso.** No modo normal pergunta, com "Não" como
  padrão, se apaga `%LOCALAPPDATA%\EntreNos` (explicando que é o diário e os backups).
- Se o Entre Nós já estiver aberto, abrir de novo só abre o navegador (como hoje, pelo `/api/ping`).

### 18.4 Como ficou o empacotamento (30/09/2026)

Detalhes decididos ao construir; valem junto com 18.3.

**Código (`config.py` e `servidor.py`)**
- `config.empacotado()` = `sys.frozen`. `config.RAIZ` vem de `config._raiz()`: empacotado, é
  `sys._MEIPASS` (a pasta `_internal` ao lado do `EntreNos.exe`; sem ele, a pasta do executável);
  pelo código, a pasta acima de `entrenos/`. `PASTA_WEB = RAIZ / "web"` nos dois casos.
  `config.VERSAO = "0.2.0"`.
- `servidor.MUTEX_ABERTO` ("EntreNos-Aberto-A5F79238-…"): só o programa instalado cria esse mutex
  (`_marcar_aberto`, depois de abrir a porta) e o guarda enquanto vive. (Desde 18.6 o aberto pelo
  `iniciar.py` também cria.) É o `AppMutex` do
  instalador: com o Entre Nós aberto, instalar por cima e desinstalar pedem "feche o Entre Nós"
  (OK/Cancelar). Não dá para fechar pelo `POST /api/sair` sem o token da sessão; em modo silencioso
  com o app aberto, o instalador desiste (mensagem suprimida = Cancelar) em vez de trocar arquivos
  em uso. `CloseApplications=yes` (filtro `*.exe,*.dll,*.pyd`, sem reiniciar) é a segunda proteção.
- Sem console (`EntreNos.exe`, `pythonw`): `--help` e erro de argumento aparecem numa janela
  (`_LeitorArgumentos`); erro que derrubaria o `main` vai para `erros.log` da pasta de dados (só o
  tipo, como `registrar_erro`) e aparece numa janela genérica. Com console, nada muda.
- `print` com `sys.stdout` None não faz nada, e `log_message` do servidor já é mudo: o modo sem
  console não precisa de mais nada.

**PyInstaller (`instalador/entrenos.spec`)**
- Entrada `iniciar.py`; onedir (`EntreNos.exe` + `_internal\`), `console=False`, sem UPX, ícone e
  recurso de versão (lido de `config.py`). `datas`: `web/` → `web`.
- `hiddenimports`: todos os módulos de `entrenos/` (o servidor importa motor, busca, alertas etc. por
  `importlib` na hora do uso) + `rapidfuzz`. Excluídos: tkinter, numpy, pandas, PIL, matplotlib,
  scipy, Jupyter/IPython, pacotes do Fiscal (openpyxl, xlrd, lxml, pdfplumber...), PyInstaller,
  setuptools/pip, unittest, pydoc, doctest, curses, pywin32. O pacote leva o Python 3.14, sqlite3,
  cryptography (com `_cffi_backend`, que o hook dela pede) e rapidfuzz com as versões AVX2.
- Resultado em 0.2.0: `_internal` com ~36 MB; instalador de ~13 MB (lzma2/max, sólido).

**Inno Setup (`instalador/EntreNos.iss`, UTF-8 sem BOM, Inno Setup ≥ 6.3)**
- `AppId={57800451-942D-4B6C-ADDC-86A52EA33434}` (nunca mudar). Versão por `/DAppVersao`; sem ela,
  o ISPP lê `VERSAO = "x.y.z"` de `entrenos\config.py` (no `#sub`, atribuição com `#expr`: `#define`
  ali cria variável local).
- `PrivilegesRequired=lowest`, sem `PrivilegesRequiredOverridesAllowed` (`/ALLUSERS` e
  `/CURRENTUSER` não mudam nada). x64 e Windows 10 ou mais novo.
- Atalho direto em `{autoprograms}` (sem pasta "Entre Nós" com um item só), com
  `Check: not WizardNoIcons` e `AllowNoIcons=yes` para o `/NOICONS` valer; área de trabalho só pela
  tarefa `desktopicon` (desmarcada). `[Run]` "Abrir o Entre Nós agora" com
  `nowait postinstall skipifsilent`.
- `[InstallDelete]` apaga `{app}\_internal` antes de copiar: atualização sem sobra da versão anterior.
- Desinstalar: em `usPostUninstall`, `UninstallSilent` → sai sem tocar nos dados; no modo normal,
  `SuppressibleMsgBox(..., MB_YESNO or MB_DEFBUTTON2, IDNO)` e só com "Sim" `DelTree` em
  `{localappdata}\EntreNos`.

**`construir_instalador.ps1`** (UTF-8 com BOM, que o PowerShell 5.1 exige para acentos)
- Testes → ícone (só se faltar) → PyInstaller (`--distpath build\pyinstaller`,
  `--workpath build\trabalho`, `--clean`) → ISCC (`/DAppVersao`, `/DPastaPrograma`, `/O dist`) →
  caminho, tamanho e SHA-256 de `dist\EntreNos-Setup-<versão>.exe`. Para no primeiro erro. Opções
  `-Python`, `-Iscc` e `-SemTestes`.
- Cuidado: `ISCC /O-` (só conferir) **apaga** o instalador da mesma versão que estiver em `dist\`.

**Testado (30/09/2026):** instalação silenciosa numa pasta temporária (`/DIR=… /VERYSILENT
/SUPPRESSMSGBOXES /NOICONS /TASKS="" /CURRENTUSER`, sem atalho criado), `EntreNos.exe --sem-navegador
--porta 8797 --dados <temp>`: ping, página e `app.js` com CSP, estado, configurar com PIN de teste,
analisar, gravar, busca, hoje, perfil, datas, backup, mutex presente e `/api/sair` encerrando o
processo; desinstalação silenciosa removeu programa e registro e deixou a pasta de dados de teste e
`%LOCALAPPDATA%\EntreNos` intactas. `testes/test_empacotado.py` guarda essas decisões.

### 18.5 Como ficaram restaurar e importar (integração de 30/09/2026)

Detalhes decididos ao construir; valem junto com 18.1 e 18.2. Testes em
`testes/test_restaurar_importar.py`.

**`cofre.py`**
- `abrir_conteudo(conteudo, pin) -> CofreRecebido` decifra um `.cofre` recebido sem tocar no disco
  (`CofreCorrompido` se não é cofre ou está cortado; `PinErrado`); `tamanho_decifrado(conteudo)` lê
  só o cabeçalho. `CofreRecebido.descartar()` esquece a chave e os dados.
- `Cofre.restaurar(recebido, dados) -> Path | None`: com um diário no disco, grava antes a cópia
  `backups/entrenos-antes-da-restauracao-AAAA-MM-DD-HHMMSS.cofre` (bytes iguais ao arquivo de antes,
  abre com o PIN de antes; data por `Cofre.hoje()`, hora por `Cofre.agora()`; no mesmo segundo ganha
  `-2`, `-3`); cópia que falha não troca nada. Depois grava `dados` cifrado com a chave do backup
  (atômico) e o cofre fica aberto com ela. Primeiro uso: sem cópia (`None`).
- A cópia de antes da restauração não entra no rodízio de `BACKUPS_AUTOMATICOS`, mas `trocar_pin` e
  `reescrever_backups` também passam por ela quando abre com a chave atual
  (`Cofre.copias_de_restauracao()`), como já era com os backups automáticos.

**`banco.py`**
- `Banco.resumo()` = `{registros, pessoas, primeiro_registro, ultimo_registro}` (datas do registro).
- `Banco.conferir_integridade()`: `PRAGMA integrity_check`, integridade do FTS5, tabelas e colunas do
  esquema atual, ajustes e datas legíveis; `ValueError` se algo falha.
- `Banco.maiores_ids()` / `garantir_ids_acima(maiores)`: ao restaurar com o app aberto, os próximos
  ids do diário restaurado passam dos já usados no diário de agora (restaurar uma cópia antiga do
  mesmo diário não pode repetir id, senão apagar um registro apagaria outro nos backups automáticos).
- `ler_exportacao(texto)` e `Banco.importar_json(dados, previa=False)`: `previa=True` faz tudo numa
  transação e desfaz (o resumo é exatamente o da importação). Limites de "grande demais" (400):
  `MAX_JSON_IMPORTAR` (60 × 1024 × 1024 caracteres), 100 mil registros, 20 mil pessoas, 1 milhão
  de itens e de relações. O
  JSON precisa de `versao` (texto) e `esquema` acima do atual é recusado.
- Casar pessoas: nome ou apelido exato (normalizado) respeitando a relação (mesmo nome com outra
  relação é outra pessoa); depois "relação única sem nome". **Não** usa o "nome parecido" (≥ 90) de
  `casar_pessoa` ("Ana" casaria com "Ana Paula"). Cada pessoa do diário casa com uma só do arquivo
  por importação (homônimos continuam dois; reimportar não duplica). Pessoa casada ganha só o que
  falta (nunca sobrescreve) e os apelidos novos. Pessoa nova sem apelido/observação/aniversário
  citada por registros importados vira `automatica`, com `nome_registro_id`/`relacao_registro_id`
  do primeiro registro que a escreve (apagar os registros apaga a pessoa, como no resto do app).
- Item cujo trecho não está no texto do registro fica de fora com aviso (não derruba a importação);
  relação cujo `item_novo` não é do próprio registro também. Relação de registro novo que aponta
  para registro repetido é ligada aos itens que já existem (tipo + trecho, de preferência mesmo
  resumo). `criado_em` de registros e itens vem do arquivo.
- Histórico: só "importação: N registros" ("registro" quando é 1), e só quando algo mudou.

**`servidor.py`**
- `POST /api/restaurar` devolve `{token, resumo, copia}` (`copia`: caminho da cópia de antes da
  restauração; `null` no primeiro uso). Acesso novo `PRIMEIRO_USO`: sem token só enquanto não existe
  diário; existindo, pede token (401 sem ele, 423 bloqueado). O conjunto `PUBLICA` continua com as 4
  rotas de antes.
- Nas três rotas de 64 MB o token (ou o primeiro uso) é conferido **antes** de ler o corpo; a
  mensagem do 413 diz o limite da rota (1 MB ou 64 MB).
- Arquivo que não é cofre ou está cortado: 400 sem gastar tentativa de PIN. Além do cabeçalho, o
  tamanho decifrado precisa ser múltiplo de 512 (página do SQLite); sem isso o GCM responderia como
  PIN errado.
- Mensagens (a tela usa para escolher o campo do foco): `"Esse PIN não abre este backup."` (401,
  PIN do backup), `"O PIN atual não confere."` (401, PIN de agora), `"Digite o PIN atual do Entre
  Nós."` (400).
- `arquivo_b64` aceita o prefixo `data:...;base64,` e espaços/quebras de linha.

**Interface (`web/app.js`)**
- `api(..., {limiteBytes})`: nas rotas com arquivo o corpo vai como `Blob` e passar de 64 MB dá 413
  sem ir para a rede. Antes de ler, a página recusa `.cofre` acima de 47 MB (o base64 cresce 4/3),
  JSON acima de 64 MB, arquivo vazio e extensão trocada (`.json` onde se espera `.cofre` e vice-versa).
- Primeiro uso: botões "🌱 Criar meu diário" e "📦 Já tenho um backup" (`aria-pressed`) trocam o
  painel. 409, ou 401/423 com `GET /api/estado` dizendo `configurado` (outra aba criou ou restaurou
  um diário no meio), vão para o bloqueio com "Este computador já tem um Entre Nós configurado".
- Depois de restaurar (primeiro uso ou Ajustes), a tela `telaRestaurado` ("Backup restaurado":
  registros, período, pessoas; em Ajustes, o nome da cópia de antes) passa por `mostrarPortao` e o
  botão "Abrir meu diário" entra em `#hoje`.
- Ajustes: o cartão "📦 Levar ou trazer dados" reúne Fazer backup, Restaurar um backup (arquivo, PIN
  do backup, PIN de agora e diálogo de perigo com nome, tamanho e data do arquivo), Exportar (JSON)
  e Importar JSON (juntar): prévia com contagens e avisos, "Juntar ao meu diário" (`{json,
  confirmar: true}`) e o lembrete de apagar o JSON. O texto do JSON só fica na página entre a prévia
  e o "Juntar" (`esquecedoresArquivo`, esquecido ao trocar de tela ou bloquear); o base64 do `.cofre`
  só existe durante o pedido. "Juntar" sem resposta (status 0) pede para refazer a prévia.

**Testado pela API (30/09/2026, 864 testes OK):** `iniciar.py` em pastas temporárias — diário A
com 3 registros (um 🔒), backup e exportação; B vazio: PIN errado 401 sem criar nada, restaurar no
primeiro uso (resumo 3 registros, 1 pessoa, período certo, 🔒 preservado), depois sem token 401 e
`configurar` 409; com o app aberto: PIN atual errado e PIN do backup errado 401 sem mexer em nada,
troca com token novo (o antigo 401) e cópia `entrenos-antes-da-restauracao-…` com os bytes de antes;
C com 1 registro próprio: prévia sem gravar nada, juntar 3 (pessoa casada, sem duplicar), juntar de
novo 0 e sem linha nova no histórico. O `EntreNos.exe` instalado em silêncio (`--porta 8800
--dados <temp>`) repetiu a restauração (primeiro uso e com o app aberto); a desinstalação
silenciosa deixou a pasta de dados de teste e `%LOCALAPPDATA%\EntreNos` como estavam.

### 18.6 Quando arquivos somem: antivírus, desinstalador e a revisão de 01/10/2026

Valem junto com 18.1 a 18.5; onde discordarem, vale esta. Testes em
`testes/test_protecoes_dados.py` (e o que é do instalador em `testes/test_empacotado.py`).

**O incidente.** Em 30/09/2026, por volta das 20h23, durante os testes do instalador, o antivírus
da empresa (Bitdefender, gerenciado pela TI) pôs em quarentena `iniciar.py` e
`dist\EntreNos-Setup-0.2.0.exe` e passou a bloquear a criação do `iniciar.py` (Permission
denied). O `.bat` fazia `start pythonw iniciar.py`: sem o arquivo, o pythonw morria sem janela e
nada aparecia ("não consigo abrir o .bat"). Os dados (`%LOCALAPPDATA%\EntreNos`) não foram
afetados. Em 01/10/2026 os dois arquivos estavam de volta na pasta (modificados às 08:03), o
`iniciar.py` igual ao do índice do git. Regra daqui em diante: **nunca contornar o antivírus**
(nada de renomear, copiar ou rodar o programa por outro caminho; nada de desligá-lo): o programa
e os scripts explicam e mandam pedir a liberação à TI.

**`Abrir Entre Nos.bat`** (ASCII, CRLF)
- Antes do `start`, confere `iniciar.py`, `entrenos\servidor.py`, `cofre.py`, `banco.py`,
  `web\index.html`, `app.js` e `estilo.css`. Faltando um: diz qual, que o antivírus pode tê-lo
  posto em quarentena (o caso de 30/09), que o diário está a salvo em `%LOCALAPPDATA%\EntreNos`, o
  que fazer (TI / Segurança do Windows), "Nao desligue o antivirus", `pause` e `exit /b 1`.
- O Python só serve se `pythonw -c "import cryptography, entrenos.servidor"` der certo (o do PATH,
  depois `%LOCALAPPDATA%\Python\pythoncore-3.14-64\pythonw.exe`, sem nome de usuário fixo). Sem
  Python: aponta o instalador para outro computador e o `pip install cryptography rapidfuzz`;
  Python que não carrega: manda rodar `python iniciar.py` num terminal para ver o motivo.
- Sem fallback para `-m entrenos` (isso esconderia a decisão do antivírus).
- **Não feito:** o guarda de `ImportError` dentro do `iniciar.py` (mostraria um MessageBox também no
  `EntreNos.exe`). Mexer no `iniciar.py` pode disparar o antivírus de novo: fica para depois de a
  TI liberar a pasta.

**Página sem arquivos**
- `servidor.main()` confere `ARQUIVOS_DA_PAGINA` (`index.html`, `app.js`, `estilo.css`) em
  `config.PASTA_WEB` antes de abrir a porta (`faltam_arquivos_da_pagina`); faltando, `_avisar`
  (janela no pythonw/EntreNos.exe) com quais faltam, a quarentena, "reinstale por cima" e "seus
  dados não foram afetados", e devolve 1.
- `GET /` e `/index.html` sem o arquivo: 404 com uma página HTML curta (`PAGINA_SEM_ARQUIVOS`), não o
  JSON cru. Outros arquivos que faltam continuam 404 JSON.
- `index.html` tem `<p id="carregando" class="sem-script">Abrindo o Entre Nós… Se esta mensagem não
  sumir…</p>`, visível por padrão; o `app.js` o remove ao iniciar (sem script inline: a CSP vale).

**A cópia de antes da restauração sai da memória**
- Restaurar com o app aberto salva antes o diário vivo (`_salvar(antigo.para_bytes())`) e só então
  `Cofre.restaurar` copia o arquivo do disco. Um salvar que tinha falhado (antivírus segurando o
  arquivo) deixava o último registro só na memória, e ele sumia na troca. Se esse salvar falha:
  500 `NAO_SALVOU_ANTES` e nada muda (sessão, arquivo e cópias como estavam).

**Cópias de um diário que sumiu (rodízio e primeiro uso)**
- O rodízio de `_guardar_backup` só apaga cópias automáticas da **chave de agora** (sal e
  parâmetros do cabeçalho). Cópia automática de outra chave (ou ilegível) nunca é apagada: ganha o
  nome `backups/entrenos-anterior-<resto do nome>.cofre` (`PREFIXO_DIARIO_ANTERIOR`; nunca
  sobrescreve, `-2`, `-3`).
- Primeiro uso com cópias em `backups/`: `configurar` e `restaurar` (sem diário ou com ele
  danificado) chamam `Cofre.separar_copias_anteriores()`, que põe de lado (mesmo prefixo) as
  automáticas e as de antes da restauração. Sem isso, restaurar um backup antigo do mesmo diário
  (mesma chave) e usar por cinco dias apagaria as cópias mais recentes do diário que sumiu.
- As `entrenos-anterior-…` não entram no rodízio, em `reescrever_backups` (apagar) nem em
  `trocar_pin`: como uma cópia num pendrive, abrem com o PIN que o diário tinha até a pessoa
  apagá-las. (Ids podem repetir entre elas e o diário novo; por isso apagar pelo id não as alcança.)
- `Cofre.copias_guardadas()` (todas as `entrenos-*.cofre` de `backups/`) e
  `resumo_das_copias() -> {quantas, mais_recente, pasta} | None` (data do nome mais novo).
- `GET /api/estado` ganha `copias_anteriores` (só sem diário ou com ele danificado; senão `null`) e
  `danificado`. A rota é pública: diz só quantas cópias, a data e a pasta, e só para 127.0.0.1.
- Tela de primeiro uso com cópias: aviso "Encontrei N cópias de um diário que já esteve neste
  computador (a mais recente é de DD/MM)", a pasta com botão "Copiar caminho", o painel "Já tenho
  um backup" já escolhido, e "Criar meu diário" pede confirmação (`confirmar`).
- `POST /api/desbloquear` sem o arquivo: 409 `{sem_diario: true}` com a pasta, a quarentena e
  quantas cópias há; a tela mostra o botão "Ver as opções" (volta a `carregarEstado`).

**Diário danificado**
- `Cofre.danificado()`: o arquivo existe, mas o cabeçalho não é do Entre Nós ou o tamanho decifrado
  não é múltiplo de 512 (cortado). Lê só o cabeçalho e o `stat`; arquivo que não dá para ler agora
  (antivírus segurando) não conta. Byte trocado no meio não aparece aqui (o GCM não distingue de
  PIN errado).
- `desbloquear` com o diário danificado: 409 `{danificado: true}` (`DIARIO_DANIFICADO`) sem testar o
  PIN, sem atraso e sem contar falha.
- `POST /api/restaurar` com o diário danificado e nada aberto: sem token (como no primeiro uso; quem
  pode cortar o arquivo pode apagá-lo), mas só com `substituir_danificado: true` (senão 409
  `{danificado: true}`). O arquivo danificado vai para
  `backups/entrenos-danificado-AAAA-MM-DD-HHMMSS.cofre` (`guardar_danificado`, nunca apagado); a
  resposta traz `danificado: true` e `copia` com esse caminho. Falhando a gravação, ele volta para o
  lugar. `conferir_antes_do_corpo` aceita esse caso como o primeiro uso.
- Tela `telaDanificado`: explica, mostra as cópias e o formulário de restauração (o mesmo do
  primeiro uso, com `substituir_danificado`) e "Conferir de novo".

**Pasta de dados apagada com o diário aberto**
- `Cofre.salvar` recusa (`PastaSumiu`, um `FileNotFoundError`) quando a pasta de dados não existe:
  `_gravar_atomico` faria `mkdir` e traria de volta o que o desinstalador apagou ("Sim: apaga tudo").
  `EntreNos._salvar` esquece diário, chave e token sem gravar (`_descartar_sem_salvar`) e responde
  409 `PASTA_SUMIU`; `bloquear` (inclusive o automático e Sair) ignora `PastaSumiu`. Só o arquivo
  `entrenos.cofre` sumir (pasta existindo) continua sendo regravado da memória.
- O mutex `MUTEX_ABERTO` passa a ser criado também pelo `iniciar.py`/pythonw (`_marcar_aberto` em
  todo Windows): ele usa a mesma pasta de dados, e o atalho do instalado, achando a porta ocupada
  por ele, só abre o navegador. Substitui o "só o programa instalado cria esse mutex" de 18.4.
- `EntreNos.iss`: depois do "Sim", `CheckForMutexes('{#AppMutexNome}')`; com o Entre Nós aberto,
  não apaga e avisa ("o diário NÃO foi apagado").

**Instalador**
- `[InstallDelete]` do `{app}\_internal` só com `Check: PastaJaEraDoEntreNos` (`{app}\EntreNos.exe`
  existe): a pasta é escolhida na instalação, e na de outro programa do PyInstaller o `_internal` é
  dele.

**`construir_instalador.ps1`** (continua UTF-8 com BOM; conferido com o parser do PowerShell, sem
rodar)
- Começa conferindo os arquivos obrigatórios (programa, página e `instalador\`); faltando, para com
  "veja a quarentena; não desligue o antivírus, peça à TI" (`Texto-Antivirus`).
- "O PyInstaller/Inno Setup terminou sem erro, mas … não está lá: o antivírus pode tê-lo removido".
- Depois do ISCC espera 5 s e confere de novo o instalador e o SHA-256; sumiu ou mudou: aviso,
  SHA-256 para a TI e `exit 1`. Só então "Pronto", e os próximos passos (pendrive ou nuvem, e-mail
  bloqueia `.exe`; `Get-FileHash` lá; SmartScreen; "Já tenho um backup").

**Restaurar: memória e scrypt**
- `_parametros_validos`: só o que o app grava, `r = 8`, `p = 1` e `n` potência de 2 entre 2^10 e
  2^17 (no máximo 128 MB e menos de meio segundo). Antes passava n=2^20, r=2, p=16 (13,7 s e 256 MB
  por tentativa, antes de qualquer PIN).
- Um pedido de corpo grande por vez (`EntreNos.vez_do_arquivo`, semáforo): o outro recebe 503
  `ARQUIVO_OCUPADO`, com o corpo lido e jogado fora em pedaços para a resposta chegar.
- Menos cópias: `Rota.campos_grandes` (`arquivo_b64`, `json`) fica de fora da conferência de UTF-8
  do `_json_do_corpo` (a rota confere: base64 só ASCII, `ler_exportacao` refaz a dele); `Pedido`
  solta os bytes depois de ler o JSON; o base64 sai do dict depois de decodificado; `_separar`
  devolve a parte cifrada como `memoryview`. Medido com um `.cofre` falso de 47 MB (PIN errado):
  4 pedidos em paralelo levavam o processo a 1.252 MB; agora 327 MB (um responde 401, três 503).

**Backup com objetos estranhos**
- `conferir_integridade` recusa (`BACKUP_DANIFICADO`) qualquer item de `sqlite_master` fora do que
  `Banco.novo()` tem (`_objetos_do_esquema`: tabelas, índices, `sqlite_sequence`, sombras do FTS5);
  aceita só `sqlite_stat1`/`sqlite_stat4` a mais. Nada de gatilho (tirar o 🔒 de todo registro novo,
  fingir que apagou), visão, tabela ou índice extra, nem coluna calculada (`table_xinfo` hidden 2/3).

**Importar JSON**
- `banco.validar_exportacao(dados)` é pública e não usa o banco: o servidor lê e confere o JSON
  **fora da trava do app** e passa o `_Exportacao` para `importar_json`, que aceita os dois. Antes, um
  JSON pequeno prendia estado, bloquear e Sair por 16 s (e o bloqueio automático não acontecia).
- `MAX_CUSTO_TRECHOS` = 500 milhões (soma de tamanho do texto × itens de cada registro): acima, 400
  "grande demais". `citadas` virou dict (era lista: quadrático com milhares de registros da mesma
  pessoa).
- Registro repetido: chave `_chave_registro` = data + texto normalizado **sem pontuação**
  (`[.!?…,;:]` e espaços contam como um espaço): "Ela adora café" = "Ela adora café.". Acento
  diferente continua fundindo ("é" e "e"): é raro mudar o sentido, e duplicar seria pior.
- O 🔒 do arquivo passa para o registro repetido daqui (só liga, nunca desliga):
  `registros_marcados_privados` conta os que já estavam no diário; aviso "N registros que já estavam
  aqui passam a ficar 🔒 fora da IA".
- Repetido dentro do próprio arquivo conta à parte (`registros_repetidos_no_arquivo`, com aviso),
  e `registros_repetidos` passa a ser só "já estava no diário".
- `pessoas_completadas`: pessoas daqui que ganharam o que faltava (nome, relação, aniversário,
  observação, apelidos).
- `detalhes_pessoas = {novas: [{rotulo, parecida_com}], existentes: [{arquivo, daqui}],
  completadas: [{rotulo, campos, apelidos}]}` (até `MAX_NOMES_PREVIA` = 100 por lista; as contagens
  continuam exatas). `parecida_com`: alguém que já estava no diário com o mesmo nome ou apelido
  ("Mariana (amiga)" entra como nova e você já tem "Mariana (melhor amiga)"). São dados do próprio
  usuário, só na sessão dele. **Não** casa por relação contida ("amiga" ⊂ "melhor amiga"): a prévia
  avisa e a pessoa ajusta a relação antes de juntar.
- Histórico, só contagens: "importação: N registros[, M pessoas completadas][, K registros marcados
  para não ir para a IA]", e só quando algo mudou.
- Tela: a prévia mostra os nomes, os pares "arquivo → daqui", as completadas com os campos, os 🔒 e
  os repetidos ("vale a versão deste computador, inclusive os itens editados aqui"), e avisa dos
  homônimos; o botão "Juntar" aparece também quando só há pessoas completadas ou 🔒 a passar.

**Telas**
- `telaRestaurado` mostra o caminho completo da cópia de antes (ou do arquivo danificado) com
  "Copiar caminho" e como desfazer.
- `telaSemConexao` e `telaSaiu` citam o atalho "Entre Nós" do menu Iniciar e o "Abrir Entre Nos.bat".

**README**: "Se nada acontecer" em "Como abrir"; "Instalar em outra máquina" em passos (gerar,
levar por pendrive ou nuvem e conferir o SHA-256, SmartScreen, primeiro uso com "Já tenho um
backup", outros caminhos, atualizar e desinstalar); seção nova "Se o antivírus bloquear" (o que
aconteceu, sinais, dados a salvo, como ver a quarentena, texto para a TI, não desligar o
antivírus); tabela de dados com `entrenos-anterior-…` e `entrenos-danificado-…`.

**Testado (01/10/2026, 897 testes OK):** os testes novos de cada achado falham no código de antes
(exportado do índice do git para uma pasta temporária, sem o `iniciar.py`) e passam no de agora;
só os que guardam o que já valia (texto inválido recusado, importar 20 mil registros) passam nos
dois. Nada rodou o `.exe`, o PyInstaller, o ISCC nem o `construir_instalador.ps1`; o `.bat` foi
rodado só numa cópia em pasta temporária sem o `iniciar.py` (mostra o aviso e sai com 1, sem abrir
Python).

## 19. Núcleo e transporte: o mesmo app no computador e no navegador (07/10/2026)

Decisão do usuário: levar o Entre Nós para iPhone e Android como **PWA**, rodando o **mesmo código
Python** no navegador pelo Pyodide (Python em WebAssembly), dentro de um Web Worker. O diário fica
no armazenamento do navegador (sistema de arquivos IDBFS do Emscripten montado numa pasta), no
**mesmo formato `.cofre`**; a sincronização virá depois, pelo Google Drive. O computador continua
com o servidor HTTP em 127.0.0.1. No navegador não existem threads (`Thread.start` falha),
`time.sleep` confiável, servidor HTTP, sockets nem ctypes/Windows: por isso o app foi separado em
um **núcleo** sem transporte e dois transportes.

Esta seção vale junto com as anteriores; onde discordarem (o atraso do PIN nas seções 15, 17 e
18, a thread vigia na 15), vale esta.

### 19.1 Quem faz o quê

```
entrenos/
  nucleo.py     o app: sessão e token, PIN e bloqueio temporário, inatividade, área da IA,
                rotas da API, restaurar/importar, IA. Não sabe nada de HTTP.
  servidor.py   transporte do computador: HTTP em 127.0.0.1, Host/Origin/Sec-Fetch, Content-Type,
                leitura do corpo com o limite da rota, CSP e cabeçalhos, página (web/), um corpo
                grande por vez, thread vigia, mutex do Windows, main()/argumentos, navegador
  ponte.py      transporte do navegador: o que o Pyodide chama (texto entra, texto sai)
```

- `servidor.EntreNos` é uma subclasse de `nucleo.Nucleo` com o que só o computador tem: `porta`,
  `servidor`, `pasta_web`, `vez_do_arquivo` (o semáforo de 18.6), a vigia (`iniciar_vigia`,
  `intervalo_verificacao`), `host_permitido` e `origem_permitida`; `encerrar` também para a vigia.
- Foram para `nucleo.py` (o que as seções 15 a 18 chamam de `servidor.X` ou `EntreNos.X`):
  `ErroHttp`, `traduzir_erro`, `registrar_erro`, `configurar_log`, `Rota`, `ROTAS`, `achar_rota`,
  `Pedido`, os acessos (`PUBLICA`, `SESSAO`, `LIVRE`, `PRIMEIRO_USO`), `contexto_sugestao`,
  `so_o_escrito`, `_erro_da_ia`, `_importar`, `LIMITE_CORPO`, `LIMITE_CORPO_ARQUIVO`,
  `atraso_do_pin` e as mensagens. `servidor.py` reexporta os nomes que os testes e o `iniciar.py`
  usam. Ficaram no servidor: `ENDERECO`, `CSP`, `CABECALHOS_SEGURANCA`, `TIPOS_MIME`,
  `arquivo_estatico`, `ARQUIVO_OCUPADO`, `PAGINA_SEM_ARQUIVOS`, `faltam_arquivos_da_pagina`,
  `MUTEX_ABERTO`, `criar_servidor`, `e_entre_nos`, `main`.
- O log de erros sai como `entrenos.nucleo` (era `entrenos.servidor`); o conteúdo continua só tipo
  e ponto do código.
- O `Abrir Entre Nos.bat` também confere `entrenos\nucleo.py` (seção 18.6).

### 19.2 O contrato do núcleo

```python
class Nucleo:
    def __init__(self, pasta, *, atraso_pin=1.0, atraso_pin_maximo=30.0, minutos_bloqueio=None,
                 hoje=None, relogio=None, relogio_parede=None, minutos_area_ia=10,
                 plataforma="computador")
        # hoje: () -> date (padrão date.today). relogio: () -> float, segundos monotônicos
        # (padrão time.monotonic): o bloqueio do PIN conta nele. relogio_parede: () -> float
        # (padrão time.time): inatividade e área da IA contam pelo que andar mais dos dois (19.4).
        # plataforma: "computador" ou "navegador" (19.7); dela saem os atributos backup_em_pasta,
        # ia_disponivel, limite_corpo_arquivo e as cópias do cofre.
    def tratar(self, metodo: str, caminho: str, *, corpo: bytes | None = None,
               token: str | None = None, consulta: Mapping[str, str | list[str]] | None = None) -> RespostaApi
    def restaurar_arquivo(self, arquivo: bytes, *, corpo: bytes | None = None,
                          token: str | None = None) -> RespostaApi   # /api/restaurar sem base64 (19.7)
    def limite_do_corpo(self, rota) -> int         # 1 MB, ou limite_corpo_arquivo (64 MB; 16 MB no navegador)
    def copia_do_diario(self, banco) -> tuple[str, bytes]   # (nome, .cofre de agora), salvo antes
    def verificar_inatividade(self) -> bool        # True = bloqueou agora
    def conferir_antes_do_corpo(self, rota, token) -> None   # rotas de 64 MB (18.5)
    def bloquear(self) -> None
    def encerrar(self) -> None      # salva e bloqueia de vez: configurar/desbloquear/restaurar → 503
    def espera_do_pin(self) -> float      # segundos até o próximo PIN principal ser conferido
    def espera_do_pin_ia(self) -> float
    desbloqueado: bool

@dataclass
class RespostaApi:
    status: int
    dados: Any                    # JSON simples (para_json já aplicado)
    cabecalhos: dict[str, str]    # os que importam a quem chamou: Allow (405), Retry-After (429)
    corpo: bytes                  # `dados` em JSON UTF-8, compacto, acentos sem escape
    encerrar: bool = False        # /api/sair: o transporte desliga (ou para) depois de responder

def achar_rota(metodo, caminho) -> tuple[Rota, tuple[str, ...]]   # 404; 405 com Allow
def resposta_de_erro(erro) -> RespostaApi                           # {"erro", ...extras}
```

`tratar` **nunca levanta**; na ordem:
1. confere a inatividade (preguiçoso: passou do tempo, salva e bloqueia antes de tudo);
2. acha a rota: 404, ou 405 com `Allow`;
3. corpo maior que `limite_do_corpo(rota)` (1 MB; 64 MB em restaurar e importar, 16 MB no
   navegador): 413, com o limite na mensagem;
4. consulta `{nome: texto}`; valor em lista vale o último (como o `parse_qs`); valor que não é
   texto, ou mais de 50 parâmetros: 400;
5. acesso da rota como nas seções 15 e 18 (pública, sessão com a trava do app, livre, primeiro
   uso), com o token conferido antes de ler o JSON;
6. JSON: objeto, sem NaN, UTF-8 válido (campos grandes como em 18.6);
7. erro → `traduzir_erro` → `{"erro": "...", ...extras}`; sucesso → `para_json`, 200.

O corpo vai direto na chamada (`corpo=self._ler_corpo(...)`), sem outra referência no transporte:
o pedido solta os bytes assim que lê o JSON, e um arquivo de 64 MB não fica duas vezes na memória
(18.6).

**O que o núcleo não pode usar.** `nucleo.py`, `ponte.py` e tudo o que eles importam (inclusive na
hora do uso, pelo `_importar`: regras, motor, banco, cofre, busca, alertas, perfil, contexto,
provedores, modelos, texto, config) não iniciam thread (só `threading.RLock`/`Lock`), não usam
`time.sleep`, `http.server`, `socketserver`, `socket`, `ctypes`, `webbrowser`, `subprocess` nem
`multiprocessing`. Para isso:
- `cofre._substituir` (troca do arquivo com novas tentativas quando o antivírus o segura) espera
  por `cofre.esperar_entre_tentativas`, que o `servidor.py` liga em `time.sleep` ao ser importado;
  no navegador fica `None` e o cofre tenta de novo direto;
- `motor` deixou de importar `socket`: a falha de DNS é reconhecida pelo `socket.gaierror` que
  estiver em `sys.modules` (se houve um, o urllib já carregou o módulo);
- `_erro_da_ia` olha só `TimeoutError` (o `socket.timeout` é o mesmo desde o Python 3.10).

`testes/test_compat_navegador.py` guarda isso: abre todo `entrenos/*.py` com a gramática do Python
3.12 (`ast.parse(feature_version=(3, 12))`; o Pyodide 314 roda o 3.14.2, o 3.12 é margem para
versões anteriores), exige `from __future__ import annotations` onde há anotação (no 3.12 e no
3.13 elas rodam ao importar), varre com `ast` os imports e os usos proibidos (inclusive
com apelido e o `_importar("motor")`, ignorando `if TYPE_CHECKING:`), testa o próprio varredor, roda
o núcleo sem transporte e a ponte, e roda um diário inteiro pela ponte num **Python separado** em
que `Thread.start` e `time.sleep` dão erro e `ctypes`, `webbrowser`, `http.server`, `socketserver`
e `entrenos.servidor` não existem (configurar, analisar, gravar, busca, telas, PIN errado, 429,
inatividade, sair; no fim, uma thread só).

### 19.3 PIN errado: bloqueio temporário com 429

Substitui o "atraso crescente" das seções 15, 17.6, 17.8 e 18.2, em que o pedido **dormia** antes
de responder (inviável no navegador).

- Depois da n-ésima tentativa errada seguida, nenhum PIN é conferido por `atraso_do_pin(n)`: a
  mesma progressão de antes, 1, 2, 4, 8, 16, 30, 30... s (`atraso_pin` e `atraso_pin_maximo` mudam
  a base e o teto). O 401 sai **na hora** e já diz quanto esperar: `{"erro", "espere_segundos"}`.
- Tentativa antes da hora: **429** `{"erro": "Espere N s antes de tentar o PIN de novo.",
  "espere_segundos": N}` e cabeçalho `Retry-After: N` (N inteiro, para cima, no mínimo 1), **sem
  testar o PIN** (nem scrypt), sem contar falha, e mesmo que o PIN esteja certo.
- A conferência do bloqueio e o teste do PIN acontecem juntos, sob a trava do PIN (`RLock`):
  tentativas em paralelo testam **um PIN só**; as outras recebem 429.
- Contadores como antes: o do PIN principal é um só para desbloquear, restaurar (o PIN do backup e
  o PIN de agora), trocar o PIN e "Esqueci o PIN da IA"; o PIN da IA (entrar, trocar) tem trava,
  contador e bloqueio próprios. Acertar zera o contador.
- "PIN da IA igual ao principal?" (17.8, 17.9): as `COMPARACOES_SEM_ESPERA` (3) primeiras
  comparações não bloqueiam; depois cada uma bloqueia como um PIN errado (1×, 2×, 4×... a base),
  **no bloqueio do PIN principal**: é um oráculo dele. (Antes cada comparação dormia segurando a
  trava do PIN, o que também segurava o desbloqueio.) Acertar o PIN principal zera a conta.
- Configurar um diário novo zera o contador e o bloqueio do PIN principal; criar o PIN da IA,
  "Esqueci o PIN da IA" e restaurar um backup zeram os do PIN da IA.
- Tela (`web/app.js`): `segundosDeEspera(erro)` lê `espere_segundos` do 401 e do 429. A mensagem
  fica parada no campo de erro (um alerta só, por causa do leitor de tela) e o botão vira
  **"Espere N s"**, contando de segundo em segundo; no fim ele volta, e **tocar de novo reenvia**
  (antes disso o envio é ignorado). Vale no desbloqueio, nas duas restaurações (primeiro uso e
  Ajustes), em trocar o PIN, criar, abrir e trocar o PIN da IA e "Esqueci o PIN da IA".
  `MENSAGENS_HTTP[429]` para quando o servidor não manda mensagem.
- Os testes usam um relógio de mentira (`testes.test_servidor.Relogio`) e nada de esperar de
  verdade; o `ServidorDeTeste` não bloqueia por padrão (`atraso_pin=0`).
- **O bloqueio sobrevive a fechar e abrir (19.7).** Cada PIN errado (e cada comparação que bloqueia)
  grava `pin_espera.json` na pasta de dados **antes** de responder: `{"principal": {falhas,
  livre_em}, "ia": {...}, "visto"}`, com os horários no relógio de parede; só contadores, nada do
  PIN. Acertar, configurar e zerar o PIN da IA regravam (sem nada a guardar, o arquivo sai). Ao
  criar o `Nucleo`: o `livre_em` gravado continua valendo; mesmo já passado, quem abre espera de novo
  `atraso_do_pin(falhas)` (adiantar a hora não pula a espera, e cada reabertura custa um degrau
  inteiro); relógio de parede antes do `visto` (hora atrasada à mão) = o teto. Arquivo estranho é
  ignorado (quem o apaga também pode copiar o `.cofre` e testar PINs fora do app). Nunca recria a
  pasta de dados apagada (18.6); falhar ao gravar não atrapalha o PIN (fica no `erros.log`).

### 19.4 Bloqueio por inatividade

- **Dois relógios (19.7).** O `time.monotonic` do Pyodide é o `performance.now()`, que não anda
  enquanto o celular dorme (só no Windows conta o sono): com a tela travada por horas, o diário
  continuava aberto. A inatividade e a área da IA contam pelo que andar mais: `relogio`
  (monotônico) ou `relogio_parede` (time.time). Hora adiantada só bloqueia antes; hora atrasada à
  mão faz a parede andar para trás, e aí vale o monotônico. O bloqueio do PIN, ao contrário, conta
  só no monotônico enquanto o núcleo vive (ninguém o encurta mudando a hora; o sono só o alonga).
- A página também (`web/app.js`): `visibilitychange`, `pageshow` e `focus` conferem na hora (o
  `setInterval` quase não roda com a aba escondida), e um toque depois do tempo sem uso bloqueia
  em vez de contar como uso (`passouDoTempoSemUso`).
- O núcleo confere no começo de cada `tratar` (e, como antes, em `_exigir_sessao` e em
  `/api/estado`) e expõe `verificar_inatividade()`.
- No computador, a thread vigia do `servidor.EntreNos` chama `verificar_inatividade()` a cada
  `intervalo_verificacao` (30 s): bloqueia mesmo sem pedido nenhum.
- No navegador, o Worker chama `ponte.verificar_inatividade()` de tempos em tempos (`setInterval`).

### 19.5 A ponte do navegador (`entrenos/ponte.py`)

```python
def iniciar_no_navegador(pasta_dados: str, **opcoes) -> Nucleo
    # cria a pasta, liga o erros.log nela e cria o Nucleo (sem thread) com plataforma="navegador".
    # Chamar de novo bloqueia (e salva) o anterior e troca; o erros.log não duplica.
def tratar_json(metodo, caminho, corpo_texto=None, token=None, consulta_json=None) -> str
    # {"status", "cabecalhos" (só Allow e Retry-After), "gravou", "dados"[, "encerrado": true]}
def restaurar_arquivo(arquivo, corpo_texto=None, token=None) -> str
    # /api/restaurar com o .cofre em bytes (o JsProxy do Uint8Array, com to_bytes()), sem base64
def verificar_inatividade() -> str     # {"bloqueou", "gravou"}
def autoteste() -> str                 # {"ok", "falhas", "python", "sqlite"}: antes de abrir o diário
```

- Texto entra, texto sai: nenhum objeto Python atravessa para o JavaScript. Os `dados` entram na
  saída já serializados (`RespostaApi.corpo`), sem converter uma exportação grande duas vezes.
- `caminho` pode trazer a consulta (`"/api/busca?q=tulipas"`, como no `fetch`); `consulta_json`
  (objeto de textos) vale por cima. Método em minúsculas vale.
- `corpo_texto` vira UTF-8 com `surrogatepass`: metade de um par de surrogates (o JavaScript
  deixa) chega ao núcleo e é recusada com 400, como no computador.
- `gravou`: algum arquivo da pasta de dados mudou durante a chamada (nome, inode, tamanho, data);
  aí o Worker chama `FS.syncfs(false, ...)` para o IDBFS ir para o armazenamento do navegador.
  Pode acontecer até num GET (o bloqueio por inatividade salva).
- Antes de `iniciar_no_navegador`: 503 com `NAO_INICIADO`.
- **Nunca levanta** (19.7): consulta ou corpo muito aninhados dão 400, e qualquer erro inesperado
  vira `{status: 500, erro}` (no `erros.log`, só o tipo), em vez de um PythonError no JavaScript.
- Não importa o `servidor.py`. No navegador, `/api/backup` (pasta) responde 409 `{baixar: true}`
  e o backup é `POST /api/backup/baixar`; não há arquivos da página (a PWA serve os seus) nem
  mutex. `/api/sair` encerra o núcleo (`"encerrado": true`); para voltar, a página chama
  `iniciar_no_navegador` de novo.
- Ordem no Worker: Pyodide + `cryptography`, `autoteste()` (com `ok` falso, a página explica que
  este navegador não roda o Entre Nós), montar o IDBFS, `FS.syncfs(true)`, `iniciar_no_navegador`.

### 19.6 O que fica para a fase do PWA

- **Pacotes (corrigido em 19.7).** No Pyodide 314 (Python 3.14.2) o `sqlite3` vem na biblioteca
  padrão e o `ssl` é uma casca; nenhum dos dois está no `pyodide-lock.json`, e
  `loadPackage(["sqlite3", ...])` falha inteiro ("No known package with name 'sqlite3'"), sem
  carregar o `cryptography`. O certo é `loadPyodide({..., packages: ["cryptography"]})` (ou
  `loadPackage(["cryptography"])`). O `motor` importa `ssl` dentro de `try` (sem ele, a IA não
  funciona, e no navegador ela já está desligada).
- O `ponte.autoteste()` confere o resto no aparelho, antes de abrir o diário: FTS5 com
  `remove_diacritics 2`, `bm25`, `optimize`, `json_each`, `serialize`/`deserialize`, o esquema do
  diário indo e voltando (com `conferir_integridade` e a busca), scrypt (RFC 7914) e AES-256-GCM
  com valores conhecidos, o formato `.cofre` e os módulos de uso na hora. O SQLite 3.39 do Pyodide
  atende o esquema de hoje; recurso do SQLite mais novo que a 3.39 fica proibido no esquema.
- `rapidfuzz`: resolvido (19.7), a semelhança é Python puro.
- IA no navegador: o `urllib` não alcança a rede no Pyodide (o TLS do `ssl` de casca levanta
  NotImplementedError). Desligada por enquanto (`ia_disponivel`, 19.7); depois, um transporte por
  `fetch` do Worker injetado no motor, sem mexer no núcleo.
- Cópia para o Google Drive: o `.cofre` já é o formato de troca (restaurar, 18.2).

**Testado (07/10/2026, 915 testes OK):** a suíte inteira no CPython 3.14 deste computador, com os testes de
bloqueio do PIN reescritos para a semântica nova (um PIN só testado em paralelo, 429 com
`Retry-After`, teto, volta ao primeiro degrau, comparação com o PIN principal no mesmo bloqueio) e
`test_compat_navegador.py`. O núcleo **ainda não rodou num Pyodide de verdade**; nada abriu
navegador, `.exe`, PyInstaller ou ISCC.

### 19.7 Revisão do núcleo para o celular (07/10/2026)

Achados de duas revisões (paridade de segurança e navegador) sobre a separação núcleo/transporte.
Vale junto com 19.1 a 19.6; onde discordarem, vale esta. Testes novos em
`testes/test_compat_navegador.py` (classes da seção "revisão de 07/10/2026"); cada um falha com a
correção desfeita (conferido desfazendo uma por vez).

**Plataforma.** `Nucleo(..., plataforma="computador"|"navegador")`; a ponte cria com "navegador".
Dela saem atributos que os testes podem trocar depois:

| | computador | navegador |
|---|---|---|
| `backup_em_pasta` | sim | não: `POST /api/backup` → 409 `{erro: BACKUP_SO_BAIXANDO, baixar: true}` |
| `ia_disponivel` | sim | não: ✨, "Buscar modelos" e "Testar" → 503 `{erro: IA_SO_NO_COMPUTADOR, ia_indisponivel: true}`; `ia.utilizavel` falso e `ia.motivo` em `/api/estado` e `/api/ia/estado` (os ajustes da IA continuam guardados) |
| `limite_corpo_arquivo` | 64 MB | 16 MB (restaurar e importar; o pico de memória da leitura passa de 4x o arquivo, e o wasm não devolve memória) |
| `cofre.backups_automaticos` | 5 | 2 |
| `cofre.copias_extras_maximas` | None (nunca saem) | 2 de cada tipo (antes da restauração, anterior, danificado) |
| textos | antivírus, pasta, `E:\` | "o navegador pode ter apagado os dados", sem caminho; `copias_anteriores.pasta` e `copia` da restauração só com o nome |

`/api/estado` ganha `plataforma`, `backup_em_pasta` e `limite_arquivo` (para a página do celular se
ajustar; a do computador não muda). O `ia.motivo` só aparece no navegador, com IA configurada.

**Backup no celular.** `POST /api/backup/baixar` (sessão) devolve `{nome, tamanho, arquivo_b64}`: o
`.cofre` de agora, cifrado, depois de salvar o que faltava (`copia_do_diario`). Vale nos dois
lugares; é o arquivo que `/api/restaurar` aceita. A página baixa ou compartilha (`navigator.share`).

**Restaurar sem base64.** `Nucleo.restaurar_arquivo(arquivo, corpo=..., token=...)` e
`ponte.restaurar_arquivo(arquivo, corpo_texto, token)`: o arquivo vem em bytes (o JsProxy do
Uint8Array, convertido com `to_bytes()`; também `bytes`, `bytearray`, `memoryview`), e o corpo só
traz `{pin[, pin_atual, substituir_danificado]}`. Mesmas regras e respostas da rota.

**PIN errado entre aberturas** (19.3): `pin_espera.json`. Antes, recarregar a página zerava o
bloqueio: 20 reaberturas no mesmo instante testavam 20 PINs.

**Dois relógios** (19.4): o diário bloqueia depois do celular dormir com o app aberto.

**Duas abas no mesmo diário.** Cada aba (ou a aba e o app instalado) é um núcleo com a sua cópia do
IDBFS; o `syncfs` de uma sobrescreve a outra. Defesa do núcleo: o cofre guarda (inode, tamanho,
data) do arquivo que leu ou gravou e, antes de salvar, confere; mudou: `CofreMudou`, o núcleo
bloqueia **sem salvar** e responde 409 `DIARIO_MUDOU` ("feche as outras abas, recarregue"). Também
em trocar o PIN e baixar a cópia, e `bloquear` não grava por cima. Arquivo que sumiu (pasta
existindo) continua sendo regravado (18.6). A defesa principal fica no Worker (pendente): Web
Locks (`navigator.locks.request("entrenos-cofre", {ifAvailable: true})`), uma aba dona só, e
`FS.syncfs(true)` antes de cada pedido se houver mais de uma.

**Bloquear sem regravar.** `bloquear` só salva se o banco está sujo (19.7 em 14): antes, cada
bloqueio recifrava e regravava o arquivo inteiro (no celular, mais um `syncfs`; com o Drive, uma
versão nova por bloqueio).

**Semelhança igual em todo lugar.** `texto.semelhanca` = `token_set_ratio` (o do rapidfuzz 3) em
Python puro, com a subsequência comum pelo método de bits de Hyyrö: o mesmo número, bit a bit, que
o rapidfuzz (conferido em 50 mil pares sorteados), ~20 µs por par. Antes o celular caía no
`difflib`: "Ana" x "Ana Paula" dava 50 (não casava a pessoa), "chocolate" x "chocolate amargo" 72
(não achava a contradição). O `instalador/entrenos.spec`, o workflow e o `.bat` ainda citam o
rapidfuzz: inofensivo (o app não o importa mais); sai quando o instalador for refeito.

**A ponte nunca levanta**, **autoteste** e o `loadPackage` certo: 19.5 e 19.6.

**`construir_instalador.ps1`** confere também `entrenos\nucleo.py` (como o `.bat`, 19.1); o teste
confere que tudo o que o `.bat` confere está na lista do script.

**Teste do "Python de navegador"** (`ROTEIRO`): além do que já bloqueava, `rapidfuzz`, `_hashlib`
(o OpenSSL do hashlib), `ssl`, `_ssl`, `zoneinfo`, `tzdata`, `subprocess` e `multiprocessing` não
existem, e `_thread.start_new_thread`/`start_joinable_thread` (por onde o `threading` do 3.13+
inicia threads) dão erro. Ganhou o autoteste, a semelhança sem rapidfuzz, backup (409 e baixar),
IA (503) e reabrir com o PIN bloqueado (429).

**Não feito (fica para a fase do PWA):**
- scrypt no Worker de uma linha só: criar o PIN da IA roda até 4 scrypt de 2^16 (as variações do
  PIN principal) e mais 1 de 2^15; no PC são ~0,25 s cada, no wasm deve ser várias vezes mais. Medir
  no aparelho e mostrar progresso antes de mexer nas variações (menos variações = PIN da IA igual
  ao principal em maiúsculas passaria);
- listar, apagar e restaurar as cópias internas de `backups/` pela tela (no celular não há
  gerenciador de arquivos);
- Web Locks e `syncfs` no Worker; a página do celular usar `plataforma`, `limite_arquivo` e
  `backup_em_pasta` (o botão "Baixar uma cópia" e o aviso de tamanho antes de mandar);
- medir memória e tempo no aparelho com um diário grande (5 a 10 anos).

**Testado (07/10/2026):** a suíte inteira no CPython 3.14 deste computador. O núcleo continua sem
ter rodado num Pyodide de verdade; nada abriu navegador, `.exe`, PyInstaller ou ISCC, e o `app.js`
(eventos de visibilidade) foi conferido só por leitura e por uma conferência de parênteses,
chaves, textos e templates.
## 20. Melhorias, ciclo e área 🪞 Eu (decidido pelo usuário em 06-07/10/2026)

Tudo aqui roda **nas regras** (sem IA), no **núcleo** (seção 19), e tem de funcionar igual no PC
e no celular (PWA com Pyodide): nada de threads, `sleep`, rede ou recurso só de Windows.
Princípios da seção 1 continuam: nunca inventar (tudo cita registros), sugestão é possibilidade
("Talvez…"), revisar antes de gravar, apagar é apagar.

### 20.0 Fundação (feita ANTES dos recursos, para eles serem construídos em paralelo)

Cada recurso mora em `entrenos/recursos/<nome>.py` + `web/telas/<nome>.js` + `testes/test_<nome>.py`
e **não edita** os arquivos centrais (`banco.py`, `nucleo.py`, `motor.py`, `alertas.py`, `app.js`,
`index.html`, `modelos.py`). A fundação cria os pontos de encaixe:

- **Registro de recursos** (`entrenos/recursos/__init__.py`): lista fixa dos módulos
  (`perguntas, presentes, promessas, rapida, lugares, marcos, semana, ciclo, eu`) importados de
  forma tolerante (um recurso quebrado não derruba o app; o erro vai para o log sem conteúdo).
  Cada módulo pode definir (tudo opcional):
  - `NOME`, `ESQUEMA_VERSAO: int`, `ESQUEMA: list[str]` (DDL próprio; o banco guarda
    `recursos_esquema(nome, versao)` e aplica migrações por recurso, dentro do mesmo arquivo cifrado);
  - `extrair(texto, referencia, pessoas, datas, itens, conhecimento) -> list[dict]`: propostas
    extras para a revisão (cada uma `{tipo, resumo, trecho, ...}`; `trecho` literal);
  - `gravar_extras(banco, registro_id, extras_confirmados, referencia)`;
  - `marca_privado(extras) -> bool` (o ciclo devolve True: o registro vira 🔒);
  - `rotas() -> list[Rota]` (handlers `fn(nucleo, pedido, banco) -> Any`, acesso como no núcleo);
  - `cartoes_hoje(banco, referencia) -> list[Alerta]` (o anti-excesso continua central:
    todos os importantes, até 3 lembretes, 1 sugestão por fornecedor no máximo e 2 no total);
  - `exportar(banco) -> dict`, `importar(banco, dados, mapa_registros) -> dict` (juntar sem duplicar),
    `ao_apagar_registro(banco, registro_id)` (o que depende do registro some junto);
  - `fora_da_ia(banco) -> set[int]` (registros que nunca vão para a IA; o ciclo e o eu usam);
  - (correção final, 20.10.1) `discretos(banco)`, `reservado(texto) -> bool` e `TIPOS_FORA_PELO_TEXTO`.
- `Proposta.extras: dict[str, list[dict]]` (campo novo em `modelos.py`, padrão vazio; entra em
  `para_json`). `MotorRegras.analisar` chama os `extrair` registrados; `POST /api/registros`
  aceita `extras` confirmados e chama `gravar_extras`. A IA (✨) nunca recebe `extras` nem nada
  de `fora_da_ia`.
- `Banco` ganha ajudantes genéricos para os recursos (`executar`, `consultar`, `inserir`,
  `atualizar`, `apagar` com histórico sem conteúdo) e chama os ganchos em exportar/importar/apagar.
- `nucleo` junta às `ROTAS` as rotas dos recursos (prefixo `/api/<recurso>/...`).
- `alertas.hoje()` junta os `cartoes_hoje` de cada recurso.
- **Web:** `index.html` carrega `web/telas/<nome>.js` (um por recurso, já criados vazios pela
  fundação, sem editar o HTML depois). `app.js` expõe `window.EntreNos = { api, h, selo, navegar,
  registrarTela(rota, render), registrarCartaoHoje(tipo, render), registrarAbaEla(id, titulo,
  render), registrarSecaoAjustes(id, titulo, render), registrarRevisaoExtra(tipo, render),
  registrarMenu(rota, rotulo, icone, grupo) }`. Menu: no celular a barra de baixo tem Hoje,
  Registrar (+), Ela, Buscar e Mais; o resto vai para "Mais".
- O teste de compatibilidade com o navegador (seção 19) passa a cobrir `entrenos/recursos/`.
- Esquema do banco: `ESQUEMA_VERSAO` central sobe para 4 (só `recursos_esquema` + o que a
  fundação precisar); as tabelas de cada recurso são do próprio recurso, **com as colunas desta
  seção como contrato** (outros recursos leem: o `eu` lê promessas, perguntas, atitudes etc.).

#### 20.0.1 Como ficou a fundação (07/10/2026) — o guia de quem escreve um recurso

Detalhes decididos ao construir; valem junto com a lista acima (onde discordarem, vale esta). Testes
em `testes/test_fundacao_recursos.py` (um recurso FALSO que usa todos os ganchos) e
`testes/test_compat_navegador.py`.

**Registro (`entrenos/recursos/__init__.py`).**
- `NOMES` é a lista fixa (a ordem é a dos extras na revisão e a das chamadas). Os nove módulos
  existem como esqueletos (só `NOME`). O registro carrega na primeira chamada a `carregados()` (o
  banco chama ao abrir), confere o contrato e deixa de fora quem falhar: `falhas()` = `{nome: tipo do
  erro}`, e o log guarda `recurso <nome> (<onde>)` + tipo + pontos do código, nunca a mensagem.
- Contrato conferido: `NOME` igual ao nome do arquivo (`[a-z][a-z0-9_]{0,30}`); `ESQUEMA_VERSAO`
  igual a `len(ESQUEMA)` (sem `ESQUEMA_VERSAO`, vale o tamanho); ganchos que existem são funções.
  Opcionais novos: `ROTULO` (nome para a tela nos avisos; sem ele, o `NOME`) e `TIPOS_EXTRAS` (os
  `tipo` aceitos nos extras; sem ele, só `tipo == NOME`).
- Para os testes: `recursos.usando(*modulos, com_os_fixos=False)` troca a lista (e devolve no fim) e
  `recarregar()` lê a lista fixa de novo. `geracao()` muda a cada troca: o núcleo (rotas) e o banco
  (objetos esperados do esquema) refazem os caches.
- Ajudantes para os recursos: `rota(metodo, caminho, funcao, acesso="sessao")`, `cartao(chave,
  nivel, texto, porque="", relevancia=50, data=None, registro_ids=(), tipo=None, dados=None)`,
  `registrar_erro(erro, recurso, onde)`.

**Esquema dos recursos.**
- Central v4 = `recursos_esquema(nome TEXT PRIMARY KEY, versao INTEGER NOT NULL)`. Depois das
  migrações centrais, `Banco._migrar_recursos` leva cada recurso da versão gravada até a do código:
  `ESQUEMA[v]` (v -> v + 1) é texto (comandos separados por ";", divididos com
  `sqlite3.complete_statement`) ou função `(conexão) -> None`; cada passo numa transação.
- Enquanto o passo roda, um **autorizador do SQLite** só deixa criar, mudar e apagar tabelas e índices
  do próprio recurso (e mexer nos dados delas). Recusado: tabela central (mais `sqlite_*` e
  `busca_fts*`), gatilho (mesmo na tabela do recurso), visão, tabela virtual ou temporária, ATTACH,
  PRAGMA que muda algo e BEGIN/COMMIT/SAVEPOINT; e os contadores de `sqlite_sequence` das tabelas
  centrais não podem mudar. Passo que falha: desfeito, no log, o recurso fica na versão em que estava
  e o diário abre normalmente (tenta de novo na próxima abertura). O `ponte.autoteste()` ganhou o
  passo `sqlite_autorizador` (o Pyodide precisa do `set_authorizer`, e do `None` para desligar).
- Versão gravada maior que a do código: `ValueError(DADOS_DE_VERSAO_NOVA)`, como no esquema central.
- `conferir_integridade` (restaurar): as tabelas dos recursos carregados fazem parte do esperado.
  Com algum recurso em `falhas()`, tabelas e índices não centrais a mais são aceitos (não dá para
  conferir a forma deles); gatilho e visão nunca.
- Use `id INTEGER PRIMARY KEY AUTOINCREMENT`: `maiores_ids`/`garantir_ids_acima` passam a cobrir as
  tabelas dos recursos (ao restaurar uma cópia antiga, ids não se repetem e apagar pelo id nas cópias
  continua seguro).

**Ajudantes do Banco** (todos passam pelo autorizador; violar levanta `banco.OperacaoProibida`, que
o núcleo responde como 500 e o log guarda só o tipo):
- `consultar(sql, parametros=()) -> list[dict]`: só leitura, em qualquer tabela (o `eu` lê itens,
  promessas...). JSON e datas voltam como o SQLite guarda (texto).
- `executar(sql, parametros=()) -> int`: um INSERT/UPDATE/DELETE nas tabelas dos recursos (linhas
  mudadas). Nada de DDL, PRAGMA, ATTACH ou transação.
- `inserir(tabela, valores) -> id`, `atualizar(tabela, id, valores)`, `apagar(tabela, id)`
  (`NaoEncontrado` se não existe): só tabela de recurso; `id` é do banco; dict/lista viram JSON, data
  vira ISO, verdadeiro/falso vira 1/0. Histórico só "tabela #id criado / atualizado: colunas / apagado".
- `transacao()`: várias escritas como uma só (tudo ou nada) e o arquivo gravado uma vez no fim.
  Escrita dentro de escrita vira **ponto de salvamento** (`SAVEPOINT`): um erro desfaz só aquela parte.
- `esquecer_alertas(prefixo)`: tira dos dispensados as chaves do próprio recurso (`"<NOME>:..."`;
  `%` e `_` são letras). A chave de um cartão pode revelar a data do que foi apagado.

**Extras (revisão e gravação).**
- `extrair` recebe cópias (pessoas, datas, itens, conhecimento): mexer nelas não muda a proposta.
  Cada extra precisa de `tipo` em `TIPOS_EXTRAS`, `trecho` literal (não vazio) do texto e `resumo`
  em texto (vazio = o trecho); o que não cumpre é descartado (log com a contagem, sem conteúdo). Até
  50 por recurso. Chaves que começam com "_" são da fundação: saem do que o recurso devolve.
- A fundação chama `marca_privado([extra])` em cada extra e marca `"_privado": true` (erro conta como
  sim: na dúvida, 🔒); com algum, a proposta ganha o aviso `recursos.AVISO_PRIVADO`. Erro em `extrair`
  vira o aviso `AVISO_EXTRAIR` com o `ROTULO` e a análise continua.
- `POST /api/registros {..., extras: {NOME: [extras confirmados]}}`: recurso sem `gravar_extras`,
  tipo desconhecido, trecho que não está no texto, formato errado ou mais de 200 extras → 400
  (`EXTRA_DESCONHECIDO`, `EXTRA_SEM_TRECHO`, `EXTRAS_FORMATO`, `EXTRAS_DEMAIS`) e nada é gravado. As
  chaves "_" que a tela mandar são ignoradas (o servidor confere tudo de novo).
- Registro e extras numa transação só (um arquivo gravado). `gravar_extras` de cada recurso num ponto
  de salvamento: `ValueError` → 400 com a mensagem e nada gravado; outro erro → a parte do recurso é
  desfeita, o registro fica e a resposta traz o aviso `AVISO_GRAVAR`.
- `marca_privado(extras confirmados)` verdadeiro (ou com erro) → registro gravado 🔒 mesmo com
  `privado: false`. Resposta: `{id}` e, quando for o caso, `privado: true` e `avisos`
  (`AVISO_PRIVADO_GRAVADO` primeiro).

**IA (✨).**
- `nucleo.fora_da_ia(banco)` = registros 🔒 ∪ `fora_da_ia` de cada recurso. Sem saber com segurança
  (um recurso da lista não abriu, o gancho falhou ou devolveu algo que não é id): 503
  `FORA_DA_IA_INDISPONIVEL` e nada vai para a IA.
- `/api/ia/busca` e `/api/ia/sugestao` (`contexto_sugestao`) usam `fora_da_ia`. `/api/busca` marca
  `privado` em cada achado pelo mesmo conjunto; sem saber, `para_ia` é falso (o ✨ não aparece).
  `GET /api/registros/{id}` traz `fora_da_ia` (um recurso o deixa fora da IA; `null` = não deu para
  saber): o detalhe trava a caixinha 🔒 marcada, porque desmarcar não o mandaria para a IA.
- `/api/ia/analisar`: com recurso em `falhas()`, 503; texto com algum extra `_privado` → 403
  `TEXTO_FICA_FORA_DA_IA` (mesmo com a caixinha desmarcada); a proposta das regras vai para a IA com
  `extras` vazio, e a resposta volta com os extras das regras (a IA nunca os vê).

**Rotas.** `rotas()` devolve `recursos.rota(...)`: `caminho` relativo (segmentos `[a-z0-9_-]` ou
`{id}`), servido em `/api/<NOME>/<caminho>` (vazio = `/api/<NOME>`); acesso `"sessao"` (padrão, com a
trava do app) ou `"livre"` (sem a trava: pegue `with nucleo.trava:` antes de mexer no banco); nunca
pública. As do núcleo vêm antes (`nucleo.todas_as_rotas()`); rota repetida: vale a primeira; recurso
com uma rota fora do contrato fica sem nenhuma (log). Handler `fn(nucleo, pedido, banco)`: `pedido.corpo`,
`pedido.id`, `pedido.parametro(nome)`, `pedido.inteiro(...)`; `ValueError` → 400 com a mensagem,
`NaoEncontrado` → 404, outro erro → 500 sem conteúdo no log. Rota que apaga algo repete nas cópias
automáticas com `nucleo.limpar_copias(lambda copia: copia.apagar(tabela, id))`.

**Hoje.** `cartoes_hoje` devolve `Alerta` ou dict (`recursos.cartao`). O centro normaliza em
`alertas.CartaoRecurso` (Alerta + `recurso`, `tipo` — padrão o NOME — e `dados`, JSON), põe o prefixo
`"<NOME>:"` na chave, descarta o que não cumpre (log), aceita até 20 por recurso e tira os dispensados.
Anti-excesso central: todos os importantes; até 3 lembretes somando todos; no máximo 1 sugestão por
fornecedor (o Hoje e cada recurso) e 2 no total (`MAX_SUGESTOES_TOTAL`). Sem cartões de recurso, a
página sai igual a antes.

**Exportar, importar, apagar.**
- `exportar()` ganha `"recursos": {NOME: dados}` (o `exportar` de cada um); quem falha fica de fora e
  o arquivo leva `"avisos"`.
- Importar: `"recursos"` do arquivo (precisa ser objeto, senão 400) vai para o `importar` de cada
  recurso dentro da transação da importação (um ponto de salvamento cada; a prévia desfaz tudo), com
  `mapa_registros` = {id no arquivo: id aqui} dos registros novos e dos repetidos. O resumo ganha
  `"recursos": {NOME: o que o importar devolveu}` (só quando há) e os avisos (recurso que falhou;
  dados de recurso que este aparelho não tem). A tela mostra o `texto` de cada um e conta `novos`.
- Apagar registro: primeiro o `ao_apagar_registro` de cada recurso (o registro ainda existe; erro vai
  para o log), depois **toda tabela não central com coluna `registro_id`** perde as linhas daquele
  registro (vale também para recurso que não abriu ou cujo gancho falhou), depois o resto. As cópias
  automáticas repetem tudo isso (o núcleo chama `apagar_registro` nelas).

**Página.**
- `index.html` carrega `telas/<nome>.css` (depois de `estilo.css`) e `telas/<nome>.js` (com `defer`,
  depois do `app.js`), na ordem de `NOMES`. Os 18 arquivos nasceram só com um comentário. O CSS por
  recurso é decisão da fundação: cada recurso tem o seu sem disputar o `estilo.css`.
- O `app.js` só começa (`carregarEstado`) no `DOMContentLoaded` (`quandoPronto`), depois de todas as
  telas: o primeiro desenho já tem cartões, abas e menus dos recursos.
- `window.EntreNos` (congelado): `api(metodo, '/rota', corpo)` (o `/api` vem sozinho), `h`, `selo`,
  `navegar(hash)`, `registrarTela(rota, render(main, partes, atual))` (rota do núcleo é recusada);
  `registrarCartaoHoje(tipo, render(cartao, {dispensar, remover, recarregar, botaoDispensar}) -> Node)`
  (procura pelo `tipo` do cartão, depois pelo NOME; sem desenho, ou com erro, o cartão genérico);
  `registrarAbaEla(id, "🌙 Título", render(painel, {perfil, pessoas, atual}))`;
  `registrarSecaoAjustes(id, "🌙 Título", render(corpo, {config, salvarConfig, atual}))` (antes do
  Histórico); `registrarRevisaoExtra(tipo, render(extra, {recurso, revisao, redesenhar}) -> Node)`
  (o miolo; confirmar/não guardar/desfazer são da fundação); `registrarMenu(rota, rotulo, icone,
  grupo)`. Os `render` de área (aba, seção) podem ser assíncronos e devolver um nó; erro vira uma
  mensagem só ali. `EntreNos.util` traz os ajudantes das telas daqui (`cabecalho`, `secao`, `campo`,
  `carregando`, `estadoVazio`, `vazioPequeno`, `caixaErro`, `citacao`, `linkRegistro`, `avisar`,
  `avisarErro`, `falar`, `confirmar`, `dataBr`, `dataLonga`, `diasAte`, `textoDias`, `hojeIso`,
  `salvarConfig`...). Identificadores: `[a-z][a-z0-9_-]{0,39}`.
- Menus: no computador, no menu lateral antes de Ajustes; no celular, dentro de "Mais" (a barra de
  baixo fica Hoje, Ela, o botão redondo **+** Registrar no centro, Buscar e Mais), agrupados por
  `grupo`. O menu "Mais" e o lateral rolam por dentro quando crescem. Rotas só as próprias
  (`#toString` não vira tela).
- Revisão: seção "🧩 Mais coisas que eu notei" com os extras; cada um começa **sem decisão** e só
  vai se for confirmado (✔️ Confirmar / 🗑️ Não guardar / ↩️ Desfazer); salvar com algum sem
  decisão pede confirmação. Extra `_privado` confirmado trava a caixinha 🔒 marcada (com o porquê);
  qualquer extra `_privado` esconde o ✨ Analisar a fundo com IA. Os extras ficam ao trocar regras ↔ IA.
  Depois de salvar: "Ele ficou 🔒 fora da IA" e os avisos da resposta.
- Conhecendo Ela com mais de 3 abas: as abas quebram linha (360 px sem rolagem para o lado).

**Testado (07/10/2026):** a suíte inteira; os testes novos de proteção (autorizadores, contadores,
apagar sem gancho, `fora_da_ia`) falham com a proteção desligada. O `app.js` foi conferido com
`Code.exe --check` e por fumaças num DOM de mentira no Node, fora da suíte: o arquivo roda inteiro, o
contrato existe e é congelado, os registros recusam o que devem, os menus entram antes de Ajustes,
nada é pedido antes do `DOMContentLoaded`; na revisão, extra pendente não vai, confirmar/não guardar
mudam o corpo enviado e o 🔒 forçado; no Hoje, desenho do recurso, genérico quando falta ou falha, e
cartão do núcleo nunca usa desenho de recurso; aba de recurso em Ela (com erro mostrado só nela) e
seção em Ajustes. Nenhum navegador foi aberto.

### 20.1 Perguntas profundas (`recursos/perguntas.py`)

- Banco de perguntas (≥ 70) nas categorias da seção 10 da especificação: Infância, Sonhos, Medos,
  Relacionamentos, Valores, Autoconhecimento, Futuro (as da especificação + novas no mesmo tom).
  Sem perguntas superficiais.
- Tabela `perguntas_feitas(id, pergunta_id, sugerida_em, status: sugerida|guardada|feita|pulada,
  feita_em, registro_id)`.
- **Escolha inteligente** (pontuação explicável): categoria sobre a qual se sabe menos (itens
  por categoria) + gancho no que ela contou nos últimos 14 dias (ex.: falou do trabalho →
  Futuro/Valores) + nunca repetir + espaçamento (no máximo uma sugestão nova a cada
  `dias_entre_perguntas`, padrão 3). **Sensibilidade:** com ESTADO negativo recente (últimos 3
  dias), não sugere Medos/Infância; prefere apoio ou nada.
- Cartão "💬 Conheça melhor" no Hoje: a pergunta + por quê ("você ainda sabe pouco sobre os
  sonhos dela"; "ela falou do trabalho na terça") + Vou perguntar / Perguntei → registrar
  resposta (abre Registrar com `pergunta_id` e o contexto) / Outra / Agora não.
- Tela `#perguntas`: por categoria, o que já foi perguntado e as respostas (links).

#### 20.1.1 Como ficou (07/10/2026)

- **Banco de perguntas** no próprio `perguntas.py` (o teste de compatibilidade exige um arquivo por
  recurso): 112 perguntas de id fixo — 78 profundas (11-12 em cada uma das 7 categorias), 24 de
  "Nós dois" (para fazer A ELA; as de acompanhamento repetem depois de 30-90 dias) e 8 "Com cuidado"
  (leves, só para momentos sensíveis). Cada uma com nível 1-3 e raízes de assunto.
- **Tabela `perguntas_feitas`**: o contrato mais `trilha` (profundas|nos_dois|apoio), `categoria`,
  `pergunta` (texto), `origem` (sugestao|tela|registro), `guardada_em`, `feita_por`, `pulada_em`,
  `adiada_ate`, `resposta` (trecho LITERAL do registro: o campo público que o `eu` lê), `certeza`,
  `recusou`, `criado_em`.
- **Escolha com pontos explicáveis**: lacuna da categoria (até 30), gancho dos últimos 14 dias pela
  categoria do item (20/16/10; +8 se o texto tem os termos), começar leve, ela animada puxa Sonhos e
  Futuro, variedade (−10 mesma categoria, −12 categoria pulada 2×, −8 sugerida e ignorada); Nós dois
  (+20 sem resposta em 60 dias, +25 nos últimos 7 dias do mês, +15 depois de um reparo), no máximo
  uma a cada 14 dias e desligável. Nunca repete; "ela não quis responder" descansa a categoria 90 dias.
  Espaço entre sugestões `dias_entre_perguntas` (3; 1-30); mínimo de 5 registros (nada genérico).
- **Sensibilidade**: estado negativo mais recente dos últimos 3 dias ou evento de importância 3 hoje
  ou amanhã → nada fundo; só uma de "Com cuidado" quando o momento veio de um estado, sem APOIO
  conhecido e sem nada marcado para hoje/amanhã. Registro que outro recurso deixa fora da IA nunca
  vira gancho.
- **Registrar a resposta**: "Perguntei" abre o Registrar (EntreNos.abrirRegistrar, 20.10) com
  `Eu perguntei: “…”` e `Ela respondeu: ` embaixo do que já estava escrito. A revisão reconhece a
  pergunta (literal, parecida ≥ 90 ou livre), ignora negação, "ela me perguntou" e pergunta a outra
  pessoa; "vou perguntar" vira `pergunta_guardada`. Resposta sempre literal; certeza 🟢 com a marca de
  resposta. `TIPOS_EXTRAS = pergunta_feita, pergunta_guardada`; a gravação confere tudo de novo (400).
- **Nós dois é da área Eu**: a resposta deixa o registro 🔒 (`marca_privado`, `fora_da_ia`).
- **Hoje**: cartão `pergunta` (sugestão, relevância 44-50, sempre "Talvez…"). Desde a integração o
  cartão sai "nova" e a sugestão só é gravada como "sugerida" quando o cartão **aparece de verdade**
  (`cartoes_mostrados`, 20.10); a chave é `sugestao:p<id>:<dia>`, a mesma antes e depois de gravar.
- **Apagar o registro**: a pergunta continua "já perguntada", sem data, resposta nem link; a
  "Vou perguntar" que só existia por causa dele sai. Exportar/importar juntam sem duplicar (não levam
  as só sugeridas; a resposta só entra se for literal do registro daqui).
- **Rotas**: `GET /api/perguntas`, `GET /api/perguntas/respostas`, `POST /api/perguntas/outra`,
  `POST /api/perguntas/{id}/guardar|perguntei|pular|agora-nao|voltar`, `DELETE /api/perguntas/linhas/{id}`.
  API para outros recursos: `respostas_nos_dois`, `respostas`, `sugestao_atual` (não grava), `pergunta`.
- **Tela**: menu "💬 Perguntas" (`#perguntas`), cartão do Hoje com Vou perguntar / Perguntei / Outra /
  Agora não, miolo dos extras e Ajustes → 💬 Perguntas.

### 20.2 Ideias de presentes (`recursos/presentes.py`)

- Tabela `presentes(id, descricao, data, ocasiao, de_quem: eu|outro, reacao:
  gostou|nao_gostou|neutra|nao_sei, registro_id, criado_em)`.
- Regras: "dei X para ela", "ela ganhou X", "ela amou o X que eu dei", "não curtiu o X" →
  presente + reação; DESEJO de objeto ("queria um fone novo") → desejo.
- Tela `#presentes` com as listas: **Ela comentou que gostaria** (com "há 8 meses"), **Ideias
  possíveis** (🔵, só derivadas de preferências fortes e hobbies registrados, citando a origem),
  **Já recebeu** (de você / de outros), **Gostou**, **Não gostou**, **Não recomendados** (não
  gosta, restrições, já dado — evita repetir). Marcar desejo como "dei" move para Já recebeu.
- Ocasião próxima (aniversário dela, do namoro, Natal, Dia dos Namorados em ≤ 45 dias) → cartão
  no Hoje: "Ideias para o aniversário dela (em 30 dias): 3 coisas que ela mencionou".

#### 20.2.1 Como ficou (07/10/2026)

- **Tabelas**: `presentes` (o contrato + `trecho`, `quem`, `certeza`, `planejado`, `desejo_item_id`,
  `desejo_id`, `atualizado_em`), `presentes_desejos` (desejos que as regras não pegam) e
  `presentes_ocultos` (o que você escondeu e desejos já realizados). Tudo com `registro_id`.
- **Extras** `presente` e `presente_desejo` (ROTULO "Ideias de presentes"), sempre com a frase
  literal (ou duas, quando a reação vem logo depois: "Dei um colar. Ela amou!"). Pega "dei/presenteei",
  "comprei/trouxe/fiz" para ela, "vou dar / comprei pra dar" (separado), "ela ganhou de X", listas;
  recusa negação, "se", pergunta, ironia, "dei carona/um beijo", "ganhou o jogo", presente para outra
  pessoa. Desejos: "tá doida por", "de olho num", "precisa de um X novo", "adoraria ganhar", "lista de
  desejos"... O desejo que o núcleo já guarda ("queria um fone novo") não duplica.
- **Gesto não é presente (08/10/2026).** "Levei café para ela no trabalho", "fiz um bolo pra ela",
  "comprei pão pra ela": atitude do dia a dia, não presente. Com os verbos que também descrevem gestos
  (comprei, trouxe, levei, mandei, fiz, preparei...), só vira presente com marca de presente ("de
  presente", mimo, surpresa, ocasião, "pra dar"), guardado para depois (separei, guardei, escondi,
  encomendei) ou um objeto claramente de presente (`_OBJETOS_DE_PRESENTE`: flores, joias, perfume,
  chocolate, livro, roupa, carta, ingresso...). Verbos de dar/presentear/ganhar continuam valendo com
  qualquer objeto ("dei café pra ela").
- **Ideias possíveis 🔵** só de preferências fortes (dela ou 2+ registros), hobbies e nomes que ela ama,
  em categoria que dá presente; caem se contraditas, velhas (12 meses), não desejadas, não gostou ou já
  dadas. Restrição alimentar entra citada nas ideias de comida. **Não recomendados** citam a origem.
- **Hoje**: cartão `presentes_ocasiao` (aniversário dela — Ajustes ou registro —, de namoro, Natal e
  Dia dos Namorados em até 45 dias, só com algo registrado), relevância 55-75; chave
  `ocasiao:<origem>:<data>`. Desde a integração ele entra no rodízio das sugestões (20.10).
- **Rotas** em `/api/presentes` (GET; PATCH/DELETE `{id}`; `itens|desejos/{id}/dei|ocultar`;
  `DELETE desejos/{id}`; `ideias/{id}/ocultar`; `DELETE ocultos/{id}`), apagando também nas cópias.
  Exportar leva o vínculo com desejos do núcleo como registro + trecho; importar junta sem duplicar.
- Sem `marca_privado`/`fora_da_ia` (presente não é sensível; a IA nunca vê extras).
- **Tela** `#presentes` (menu 🎁): ocasiões, o momento dela (com o que ajuda, citado), desejos,
  ideias, separados, recebidos (de você / de outros) com a reação, gostou/não gostou, não recomendados,
  ocultos.

### 20.3 Promessas (`recursos/promessas.py`)

- Tabela `promessas(id, texto, prazo, status: aberta|cumprida|desisti, registro_id, criado_em,
  concluida_em)`.
- Regras (primeira pessoa): "prometi…", "fiquei de…", "combinei de…", "vou levar ela…",
  "tenho que levar ela…" → promessa (prazo pelas datas do texto).
- Hoje: prazo chegando (lembrete); aberta há mais de 14 dias sem prazo (sugestão gentil).
- Alimenta o espelho da área Eu.

#### 20.3.1 Como ficou (07/10/2026)

- **Tabela `promessas`**: o contrato + `trecho` (frase literal; à mão = o texto), `prazo_expressao`,
  `precisao` (dia|semana|mes|ano), `pessoa`, `forma` (prometi|fiquei_de|combinei|vou|disse_que|manual),
  `feita_em` (o dia da promessa; o `eu` conta por ela, não por `criado_em`), `concluida_registro_id`.
- **Extras** `promessa` (texto e prazo editáveis; datas ambíguas viram botões; força fraca com "acho
  que") e `promessa_cumprida` (só cumprimento dito: "como prometi", "cumpri a promessa"…; a revisão
  busca `POST /api/promessas/candidatas` e a pessoa escolhe; sem escolha, nada muda). Resumo só com
  palavras do trecho; prazo = a primeira data futura depois do gatilho; não duplica (semelhança ≥ 88 e
  mesmo prazo). Nenhum dos dois marca 🔒.
- **Hoje**: `promessa_prazo` (lembrete; hoje 86, amanhã 80, 2-3 dias 70; semana/mês em volta do
  período; conflito com evento dela no mesmo dia), `promessa_gentil` (sugestão: prazo passado até 30
  dias, ou aberta há mais de 14 dias sem prazo) e `promessa_cumprida_talvez`. Momento difícil dela: a
  sugestão desce 12, ou sobe citando o APOIO que combina. O lembrete não aparece quando o próprio
  registro já virou EVENTO do núcleo na mesma data.
- **Rotas**: `GET|POST /api/promessas`, `PATCH|DELETE /api/promessas/{id}`, `POST .../parecidas`,
  `POST .../candidatas`. Apagar o registro da promessa apaga a promessa; a prova de cumprimento
  apagada deixa a promessa cumprida sem a data e sem o vínculo.
- **Tela** `#promessas` (menu 🤝), sem placar nem porcentagem.

### 20.4 Anotação rápida (`recursos/rapida.py`)

- Tabela `rascunhos(id, texto, criado_em, referencia)`. Salvar sem revisar (um campo e um
  botão; no celular, o botão "+" grande; no PC, atalho de teclado). **A data de referência é a
  do momento da anotação** (para "amanhã" continuar certo quando organizar depois).
- Hoje: "📝 3 anotações para organizar" → abre a revisão normal com a referência guardada.
- Rascunho é criptografado como o resto e some ao ser organizado ou apagado.

#### 20.4.1 Como ficou (07/10/2026)

- **Tabela `rascunhos`**: o contrato + `privado`, `contexto`, `humor`; sem `registro_id`. A referência é
  a do formulário ou hoje, nunca mais de 1 dia no futuro. A lista mostra as datas da anotação
  calculadas pela referência, "urgente" (fala de hoje/amanhã) e "parece organizada".
- **Rotas**: `GET|POST /api/rapida/rascunhos`, `PATCH|DELETE /api/rapida/rascunhos/{id}`,
  `POST /api/rapida/rascunhos/{id}/organizado {registro_id}` (só apaga se o texto está no registro —
  ou semelhança ≥ 80 — e o registro é de depois; apaga nas cópias também).
- **Organizar** (desde a integração, pelo contrato do app, 20.10): `EntreNos.abrirRegistrar({texto,
  data: referência, privado, contexto, humor, organizar: true, aoSalvar})` — a revisão abre sozinha e,
  salvo o registro, `aoSalvar(id)` chama o `/organizado`. O que já estava em Registrar vira outra
  anotação antes (nada se perde).
- **Guardar sem revisar**: tela `#anotar` (menu 📝, grupo "registrar"; **Alt+N** fora de campo de texto;
  Ctrl+Enter guarda) e o botão "📝 Guardar para organizar depois" no formulário de Registrar
  (`registrarAcaoRegistrar`), que é para onde leva o **+** da barra de baixo do celular.
- **Hoje**: lembrete `rapida` "📝 N anotações para organizar" (40 + 3/dia da mais antiga + N; 82 se
  alguma fala de hoje, 76 de amanhã; chave `n<maior_id>:pendentes`).
- Nunca vai para a IA nem para a busca.

### 20.5 Lugares (`recursos/lugares.py`)

- Tabela `lugares(id, nome, tipo, status: fomos|queremos_ir, opiniao (+1/-1/None), cidade,
  registro_ids JSON, criado_em)`.
- Regras: "fomos no/ao <Lugar>", "jantamos no…", "ela quer conhecer…", "restaurante/bar/café/
  praia/parque <Nome>", "viagem para <Cidade>" com a opinião dela quando houver.
- Aba "Lugares" em Conhecendo Ela; sugestão no Hoje (no máximo uma por semana): "Talvez o
  <Lugar> que ela quer conhecer seja uma boa para o próximo encontro".

#### 20.5.1 Como ficou (07/10/2026)

- **Tabelas**: `lugares` (o contrato + `chave`, `generico`, `juntos`, `quem_quer`, `editado` JSON,
  `visitado_em`) e `lugares_mencoes` (uma linha por vez que o lugar aparece: `registro_id`, trecho
  literal, `mencao` visita|vontade|opiniao, `quem`, opinião, reação literal, ressalvas, voltar, data,
  nome, tipo, cidade). A situação do lugar sai das menções; a correção feita à mão vale mais.
- **Extração** (extra `lugar`; desenho registrado pelo NOME `lugares`): tipo + nome ("restaurante
  Mangiare"), tipo + cozinha (genérico), nomes com maiúscula depois do verbo, cidades e pontos
  conhecidos; fomos/jantamos/viajamos, ela quer/sonha/tá doida pra conhecer, a gente vai, ela
  adora/odeia (só com lugar reconhecível), reação na mesma frase ou na seguinte; "achou caro" é
  ressalva. Tipo sozinho ("a praia") não vira lugar. Datas pelo `regras/datas.py`, só dia exato.
- **Pontuação explicável** (ela quer +3, combinaram +2, ideia sua +1, palavras dela +2, vezes +n até 3,
  recência, gosto forte +2, gosto negativo −3, restrição citada, momento difícil, viagem registrada +3,
  cada "agora não" −1).
- **Hoje**: no máximo um cartão `lugares_sugestao` por semana ISO (chave
  `sugestao:<id>:<AAAA-Www>`), nunca com estado difícil dela nos últimos 3 dias nem se vocês já saíram
  na semana; modos conhecer/voltar/rever; sempre "Talvez…"; considera aniversário e viagem.
  **Só na quinta e na sexta-feira** (planejar o fim de semana, `DIAS_DA_SUGESTAO`) **ou com uma data
  especial em até 3 dias** (mesversário, aniversário dela ou de namoro, `DIAS_DATA_ESPECIAL`; decisão de
  08/10/2026). Nos outros dias o cartão não aparece; a aba Lugares mostra a sugestão em qualquer dia.
- **Rotas**: `GET /api/lugares`, `GET|PATCH|DELETE /api/lugares/{id}`, `POST /api/lugares/{id}/fomos`,
  `DELETE /api/lugares/mencoes/{id}` (apagam também nas cópias). Sem `marca_privado`/`fora_da_ia`.
- **Tela**: aba "📍 Lugares" em Conhecendo Ela (`#ela/lugares`), sem item de menu.
- Integração (20.10): nome com maiúscula logo depois de uma palavra de lugar em minúsculas
  ("restaurante Mangiare", "café Santo Grão", "festival Coala") e o resto de um nome de lugar ("Museu
  do Ipiranga", "Beco do Batman") deixaram de virar "Pessoa nova" em `regras/pessoas.py`.

### 20.6 Marcos, linha do tempo e "Neste dia" (`recursos/marcos.py`)

- Tabela `marcos(id, titulo, data, tipo: primeiro_encontro|namoro|viagem|familia|eu_te_amo|outro,
  descricao, registro_id, criado_em)`.
- Regras: "primeiro encontro", "pedi ela em namoro", "nossa primeira viagem", "conheci a mãe/
  família dela", "dissemos eu te amo" → marco (com a data do texto ou do registro).
- **Marcos de tempo juntos** (calculados de `inicio_namoro`): 100 dias, 6 meses, 1 ano, 500 dias,
  1.000 dias, anos seguintes; aviso 7 dias antes e no dia (além dos mesversários que já existem).
- Tela `#tempo`: linha do tempo (marcos + eventos passados + registros importantes), por ano,
  cada ponto clicável com o contexto (registro, pessoas, relações).
- "Neste dia" no Hoje: registro ou marco de anos anteriores no mesmo dia/mês (e "há 6 meses")
  — só o que foi registrado.

#### 20.6.1 Como ficou (07/10/2026)

- **Tabela `marcos`**: o contrato (tipo com CHECK nos 6 valores) + `precisao`, `pessoa` (literal),
  `trecho`, `data_expressao` (literal), `data_origem` (texto|registro|ajustada|manual). "Vocês se
  conheceram" fica como `primeiro_encontro`; noivado, casamento, morar juntos, pet, primeiro beijo como
  `outro` (tipos novos pediriam mudar este contrato).
- **Extra `marco`** (campo `marco_tipo`): primeiro encontro, pedido de namoro, primeira viagem (com o
  destino), conheci a família dela (com o nome escrito), primeiro "eu te amo", "nosso primeiro X juntos".
  Fora: negação, plano, futuro, pergunta, dúvida, ironia, ex, marco de outras pessoas, briga e o tempo
  juntos (é calculado). Datas: "hoje faz 1 ano do…" calculado; "há 2 anos" sem "hoje" vira precisão de
  ano/mês; **data futura em frase no passado** volta para a última vez que passou (`ajustada`, com nota
  na revisão); sem data, o dia do registro. Integração (20.10): "conheci/comi/perdi…" (1ª pessoa do
  pretérito em -i) passaram a contar como passado no `regras/datas.py`; "pedi" continua fala (como
  "pediu") e segue ajustado só no marco.
- **Tempo juntos**: base em `inicio_namoro` (ou no marco de namoro mais antigo); 100, 500 e cada 1.000
  dias como lembretes daqui (7 dias antes e no dia); 6 meses e anos o núcleo já avisa (aqui só o
  "relembrar" no dia, com marcos guardados).
- **Hoje**: aniversário de marco (7 dias antes e no dia), "Neste dia" (marco ou registro de anos
  anteriores ou de 6 meses atrás, com pontos explicáveis), aniversário de perda dela (cuidado, véspera e
  dia), momento difícil reduz e tira o convite; 29/02 em 28/02.
- **Privacidade**: o que os recursos deixam fora da IA nunca volta no Neste dia nem na linha do tempo
  (sem saber, ficam fora todos os 🔒); trecho com palavra do ciclo nunca é citado.
- **Rotas**: `GET|POST /api/marcos`, `PATCH|DELETE /api/marcos/{id}`, `GET /api/marcos/linha?tudo=1`,
  `GET /api/marcos/neste-dia`, `POST /api/marcos/parecidos`.
- **Tela** `#tempo` (menu 🕰️ "Linha do tempo"): "Vocês", "Neste dia", marco à mão, filtro essencial/tudo
  e a linha por ano com o contexto de cada ponto.

### 20.7 Diário de conversa e resumo da semana (`recursos/semana.py`)

- Registrar ganha o modo **"Depois de uma conversa"** (formulário curto: onde/contexto, o que
  ela contou, como ela estava, assuntos importantes, o que preciso lembrar) que vira um texto
  só + `contexto`/`humor`, analisado pelas regras como sempre.
- **Resumo da semana** (dia configurável, padrão domingo): cartão no Hoje com o que ela contou
  na semana (por categoria, citando), o que ficou em aberto (pendências e promessas), os
  próximos 7 dias e a pergunta sugerida. Tela `#semana/AAAA-Www` com o histórico.

#### 20.7.1 Como ficou (07/10/2026)

- **Tabelas** (contrato para o `eu` ler): `semana_lembretes(id, texto, trecho, data, precisao,
  data_expressao, status aberto|feito|descartado, registro_id, criado_em, concluido_em)` e
  `semana_assuntos(id, assunto, chave, trecho, registro_id, criado_em)`. O resumo não é guardado: é
  calculado na hora (apagar um registro tira tudo dele do resumo).
- **Semana**: os 7 dias que terminam no dia do resumo (`semana_dia`, padrão domingo); nome ISO do
  último dia. `semana_no_hoje` liga/desliga o cartão do resumo.
- **Depois de uma conversa**: `POST /api/semana/conversa {onde, contou, humor, assuntos, lembrar}`
  devolve `{texto, contexto, humor}` sem gravar ("Ela estava X.", "Assuntos importantes: a, b e c.",
  "Lembrar: X."). A tela `#conversa` traz o que já estava no Registrar e, desde a integração, entra no
  Registrar pelo contrato do app: o modo "🗣️ Depois de uma conversa" no alto do formulário
  (`registrarModoRegistrar`) e `abrirRegistrar({..., organizar: true})` (20.10).
- **Extras** `conversa_assuntos` e `conversa_lembrete` (frase literal; data pelo Python; ambígua com
  alternativas; "lembrar que ela não gosta de cebola" é fato, não lembrete). Integração (20.10): o
  lembrete confirmado deixa "descartado" o acontecimento que as regras tiraram da mesma frase com a
  mesma data (antes o Hoje avisava duas vezes, uma como se fosse um acontecimento dela).
- **Resumo**: o que ela contou por categoria, destaques com pontos explicáveis, como ela estava (só
  estados dela), momento sensível, assuntos e pessoas que voltaram, em aberto (pendências, promessas,
  lembretes), próximos 7 dias, até 3 "Talvez" e a pergunta sugerida (`perguntas.sugestao_atual`).
  O ciclo fica de fora (palavras e trechos de `ciclo_eventos`).
- **Hoje**: `semana_resumo` (lembrete, do dia do resumo até 2 dias depois, só com 2+ registros na
  semana), `semana_lembrete` (hoje 82 / amanhã 76; atrasado até 7 dias vira sugestão gentil).
- **Rotas**: `GET /api/semana`, `GET /api/semana/historico`, `POST /api/semana/conversa`,
  `GET /api/semana/lembretes`, `PATCH|DELETE /api/semana/lembretes/{id}`, `DELETE /api/semana/assuntos/{id}`.

### 20.8 Ciclo (`recursos/ciclo.py`) — ela sabe e concorda

- Tabelas `ciclos(id, inicio, fim, criado_em)` e `ciclo_eventos(id, data, tipo:
  inicio|durante|fim|fluxo|sintoma, valor, trecho, registro_id, criado_em)`.
- Regras: início ("menstruou", "ficou menstruada", "desceu", "veio a menstruação", com a data
  certa: "ontem", "desde segunda"); durante ("está menstruada" → pergunta "sabe quando
  começou?"); fim ("parou", "acabou a menstruação"); fluxo (leve/moderado/intenso, só se ela
  disser); sintomas com as palavras dela (cólica, dor de cabeça/enxaqueca, inchaço, cansaço,
  dor nas costas, seios sensíveis, náusea, espinhas, desejo de doce...); "o que ajuda" =
  APOIO ligado ao ciclo ("quando está com cólica, bolsa de água quente ajuda").
- **Registro com informação de ciclo vira 🔒 automaticamente** (com aviso na revisão).
- **Previsão:** intervalos entre inícios; registro esquecido (intervalo ≈ 2× a mediana) gera a
  pergunta "faltou registrar um mês?" e não entra no cálculo; mês muito fora do padrão é
  descartado; **só a partir de 3 ciclos**; **faixa** (mediana ± dispersão, mínimo ±2 dias),
  "irregular" se a variação dos últimos ciclos passa de 7 dias; duração média pelos fins.
- **Período fértil (incluído pelo usuário, com aviso):** só com ≥ 3 ciclos e regulares; faixa
  de trás para frente (ovulação ≈ próximo início − 14 ± 2; janela −5..+1), 🔵, com o aviso
  fixo: "Estimativa aproximada, feita a partir de anotações. Não serve como método para evitar
  ou planejar gravidez; para isso, o ideal é um ginecologista." **Só no cartão**, nunca no Hoje
  nem em notificação.
- **Padrões de sintomas:** "cólica em 3 dos últimos 4 ciclos, quase sempre no 1º dia" (fato dos
  registros, sem diagnóstico).
- **Hoje:** 1-2 dias antes da faixa prevista, um lembrete NEUTRO ("💙 Um lembrete de cuidado
  para os próximos dias") com sugestão só do que ela disse que ajuda; sem fala dela, sem
  sugestão. O texto do Hoje nunca usa as palavras menstruação/ciclo/TPM.
- **Planejamento:** evento futuro registrado que cai na faixa + algo que ela já disse sobre isso →
  aviso no cartão do ciclo e na revisão daquele evento.
- **Cartão "🌙 Ciclo" em Conhecendo Ela**: faixa prevista, duração média, histórico (calendário
  simples com duração e intervalo de cada ciclo), sintomas, o que ajuda, período fértil com
  aviso, editar/apagar um ciclo, **"apagar todos os dados do ciclo"**.
- **Limites no código (com testes):** nada do ciclo vai para a IA (fora de todo contexto/
  trecho/sugestão mandados à IA, mesmo com o registro desmarcado de 🔒); o ciclo nunca entra em
  "Como ela está" nem em frase que explique humor/comportamento; nada de diagnóstico.

#### 20.8.1 Como ficou (07/10/2026)

- **Tabelas**: `ciclos(id, inicio, fim, criado_em)` + `exatidao` (dia|aproximada|talvez), `origem`
  (registro|manual), `registro_id` (o do início), `fim_registro_id`, `intervalo_confirmado`;
  `ciclo_eventos(id, data, tipo, valor, trecho, registro_id, criado_em)` + `ciclo_id`, `palavras`,
  `polaridade`, `certeza`, `de_quem` (tipos também `ajuda` e `mencao`); `ciclo_ajustes(chave, valor)`
  (`lembrete_hoje`, `mostrar_fertil`).
- **Extras com prefixo** (`ciclo_inicio|durante|fim|fluxo|sintoma|ajuda|mencao`; o desenho da revisão é
  escolhido pelo tipo): um por acontecimento, com sintomas e fluxo da frase. Dono pela oração ("a irmã
  dela", "minha" → `ciclo_mencao` de outra pessoa: não entra no 🌙 dela, mas o registro fica 🔒).
  Negação, dúvida, hábito e futuro viram comentário; "acho que" vira `talvez` (fora da previsão).
  Humor nunca vira sintoma; "cólica" e "fluxo intenso" sozinhos são marcadores fracos.
- **Gravar**: todo dado do ciclo pede 🔒; os itens das regras da mesma frase (menos a nota) ficam
  "descartados" (`banco.atualizar_item`; continuam no registro e dá para reativar). Início a até 10
  dias de outro é o mesmo ciclo.
- **`fora_da_ia`** = registros com dado guardado + qualquer registro cujo texto fala do ciclo (mesmo
  sem nada confirmado). **`discretos`** (integração, 20.10) = os mesmos. Desde a correção final
  (20.10.1), "fala do ciclo" é **o mesmo critério da revisão** (`texto_fala_do_ciclo`: o `extrair` acha
  algo), com "a regra dela" no vocabulário; os itens da frase aparecem nas telas centrais pelo
  `reservado` (sem humor explicado pelo ciclo, sem as palavras dele), confirmado ou não.
- **Previsão**: só com 3 ciclos completos (dos últimos 6); faixa do menor ao maior intervalo, mínimo
  mediana ±2; variação > 7 = irregular; intervalo ≈ 2× pergunta "faltou registrar um mês?"; fora do
  padrão sai da conta. Período fértil só regular, base exata, com o aviso literal, só no cartão.
- **Hoje**: lembrete neutro `ciclo_cuidado` 1-2 dias antes da faixa ("💙 Um lembrete de cuidado para
  os próximos dias."), com no máximo 2 coisas que ELA disse que ajudam; sem data e sem registro.
- **Planejamento**: no cartão 🌙 e, desde a integração (20.10), **na revisão** do acontecimento que cai
  nos dias previstos: extra `ciclo_planejamento` (o `extrair` lê o diário por `conhecimento.consultar`),
  só informação — não é dado do ciclo, não deixa o registro 🔒, não grava nada, e salvar sem decidir
  não pede confirmação (`registrarRevisaoExtra(..., {opcional: true})`).
- **Cartão 🌙 Ciclo** em Conhecendo Ela (`#ela/ciclo`, sem item de menu): agora, faixa 🔵, perguntas,
  duração, planejamento, padrões de sintomas, o que ajuda, período fértil, histórico (editar/apagar),
  anotar um início, ajustes e "apagar todos os dados do ciclo" (`POST /api/ciclo/apagar-tudo`).
- **Rotas**: `GET /api/ciclo`, `POST /api/ciclo/ciclos`, `PATCH|DELETE /api/ciclo/ciclos/{id}`,
  `POST /api/ciclo/ciclos/{id}/intervalo`, `DELETE /api/ciclo/eventos/{id}`, `PATCH /api/ciclo/ajustes`,
  `POST /api/ciclo/apagar-tudo`. Tudo o que apaga repete nas cópias e esquece as chaves `ciclo:`.
- **Em aberto (decisão do usuário)**: "cólica" sozinha (sem outra palavra do ciclo) hoje vira um
  sintoma para confirmar e esconde o ✨ daquela revisão; "cólica renal/intestinal/de bebê" não.

### 20.9 Área 🪞 Eu (`recursos/eu.py`) — como estou sendo com ela

Sem nota, sem placar, sem ranking; comemora mais do que cobra; tudo cita a origem; **🔒: nada da
área Eu vai para a IA**; sem PIN extra. Telas `#eu` (visão geral), `#eu/checkin`, `#eu/mes`,
`#eu/aprendizados`, `#eu/reparos`, `#eu/cartas`.

1. **Check-in semanal curto** (≈ 1 min) e **reflexão mensal**. Tabela `checkins(id, tipo:
   semanal|mensal, periodo, respostas JSON, criado_em)`. As perguntas do semanal são
   **escolhidas pela semana real** (2-3): semana difícil dela registrada → "Como você a apoiou
   na semana difícil dela?"; promessa vencendo → "Conseguiu cumprir X?"; pergunta profunda feita
   → "Como foi a conversa sobre Y?"; sem gancho → "Uma coisa boa que você fez por ela" e "Algo
   que faria diferente". Escala de presença em 5 emojis (só sua, nunca vira nota dela).
2. **Espelho (observações dos registros)**, por semana e por mês: promessas feitas × cumpridas;
   acompanhamento das pendências ("ela contou da entrevista no dia 12; você marcou que perguntou
   no dia 15"); equilíbrio (preocupações × alegrias registradas); antecedência nas datas;
   perguntas profundas feitas; atitudes que combinam com o que ela disse que ajuda. Cada linha
   com link para o registro. Tendência ao longo dos meses em texto ("em agosto…, em outubro…").
3. **Minhas atitudes:** regras em primeira pessoa ("levei café para ela", "fiquei com ela no
   hospital", "escutei sem dar conselho") → ATITUDE (tabela `atitudes(id, texto, data,
   registro_id)`), **cruzadas com o que ela disse**: "combina com o que ela disse que ajuda
   (30/09)"; e, com cuidado, o descompasso como pergunta: "Ela disse que prefere que você não
   tente resolver; em 2 registros você anotou que deu conselho. Como foi?".
4. **Aprendizados que voltam na hora certa:** tabela `aprendizados(id, texto, gatilhos JSON,
   criado_em, mostrado_em)`. Gatilhos tirados do texto (termos, categorias, estados: "quando ela
   chega cansada" → ESTADO cansada). Quando um registro novo casa com o gatilho, a revisão e o
   Hoje mostram o seu aprendizado.
5. **O que ela diz (retorno dela):** perguntas para fazer A ELA sobre a relação ("Tem algo que
   eu possa fazer melhor por você?", "O que te fez se sentir cuidada este mês?"...), no mesmo
   esquema das perguntas profundas (categoria "Nós dois"); a resposta é 🟢 dela e, no mês,
   aparece lado a lado com o seu check-in ("o que eu achei" × "o que ela disse").
6. **Intenção do mês:** sugerida pelos dados (ex.: poucas alegrias registradas → "notar o que a
   deixa feliz"); você escolhe ou escreve; lembrete gentil no meio do mês; revisão no fim.
7. **Desentendimentos e reparos** (incluído pelo usuário): tabela `reparos(id, data, aconteceu,
   eu_senti, ela_pode_ter_sentido (🔵 até ela dizer), faria_diferente, conversamos (null/sim/
   não), reparo, tema, intensidade 1-3, criado_em, acompanhado_em)`. **Só a sua parte — não
   existe campo "o que ela errou".** Depois de 2-3 dias: "Vocês conversaram sobre aquilo?".
   Tema repetido (3 vezes) → "esse assunto voltou 3 vezes; talvez valha uma conversa calma".
   Se ficarem frequentes e intensos (≥ 3 de intensidade 3 em 30 dias) → sugere, com cuidado,
   conversar com ela ou com um profissional.
8. **Admiração e gratidão:** "o que admiro nela" em uma linha (tabela `admiracoes`), que
   alimenta cartas e a reflexão.
9. **Ajuda para cartas:** escolhe a ocasião; o app junta blocos dos registros (marcos, frases
   dela 🟢, coisas que ela gosta, momentos felizes, admirações) para marcar; monta um esqueleto
   de carta só com o que foi escolhido (frases-modelo "Lembro de quando…"); você escreve e
   guarda (tabela `cartas`, 🔒).
10. **Cuidar de você:** se o seu check-in de presença cair por 3 semanas seguidas → "As últimas
   semanas parecem mais pesadas para você. Como você está?" (sem diagnóstico).

#### 20.9.1 Como ficou (07/10/2026)

- **Tabelas** (esquema v1): `checkins(id, tipo semanal|mensal, periodo AAAA-Www|AAAA-MM, respostas JSON,
  presenca 1-5, criado_em, atualizado_em)` (única por tipo + período); `atitudes(id, texto, data,
  registro_id, trecho, jeito, criado_em)`; `aprendizados(id, texto, gatilhos JSON, criado_em,
  mostrado_em, data, registro_id, trecho)`; `reparos(id, data, aconteceu, eu_senti,
  ela_pode_ter_sentido, ela_certeza, faria_diferente, conversamos, reparo, tema, intensidade 1-3,
  criado_em, acompanhado_em, registro_id, trecho)`; `admiracoes(id, texto, data, registro_id, trecho,
  criado_em)`; `cartas(id, ocasiao, titulo, texto, blocos JSON, criado_em, atualizado_em)`;
  `intencoes(id, mes único, texto, origem sugerida|escrita, sugestao, criado_em, revisao, revisada_em)`.
- **Extras** `atitude`, `aprendizado`, `reparo`, `admiracao` (deixam o registro 🔒; `fora_da_ia`
  continua valendo mesmo com a caixinha desmarcada) e `aprendizado_lembrete` (não deixa 🔒; é só um
  lembrete — desde a integração é `{opcional: true}` na revisão e o `extrair` lê os aprendizados por
  `conhecimento.consultar`, 20.10; a referência fraca ao diário ficou só de reserva).
- **Regras**: atitudes em 1ª pessoa no passado com "jeito" (cuidado, presença, escuta, espaço, carinho,
  ajuda, interesse, desculpas, gratidão, conselho); aprendizado ("aprendi que", "da próxima vez",
  "nota mental:"); desentendimento só com sujeito "a gente/nós/eu e ela", intensidade por palavras,
  **só a sua parte** (campo estranho no reparo → 400 `SO_A_SUA_PARTE`); o que ela pode ter sentido com a
  certeza da frase (🔵 até ela dizer); admiração.
- **Check-in semanal** (sexta a domingo: a semana que acaba; segunda a quinta: a que passou), até 2
  ganchos da semana real + "Uma coisa boa que você fez por ela"; escala de presença em 5 emojis, nunca
  número. **Reflexão mensal** do dia 25 em diante. **Espelho** por semana e mês (cada linha com os
  registros), tendência em texto. **Atitudes × APOIO** dela (combina / descompasso como pergunta).
  **O que ela diz** lê `perguntas_feitas` (Nós dois). **Intenção do mês** sugerida com o porquê.
  **Reparos**: "Vocês conversaram sobre aquilo de <dia>?" de 2 a 7 dias depois; tema repetido 3× em
  180 dias; 3 de intensidade 3 em 30 dias → conversar com ela ou com um profissional (sem diagnóstico).
  **Cuidar de você** quando a presença pesa 3 semanas. **Cartas** com blocos só do registrado e
  esqueleto montado no servidor.
- **Ciclo nunca explica nada aqui**: registros que outros recursos deixam fora da IA (menos as
  respostas de Nós dois) não viram semana difícil, gatilho, momento dela nem bloco de carta.
- **Hoje**: `eu_checkin`, `eu_mes` (desligáveis em Ajustes → 🪞), `eu_intencao`, `eu_reparo`, `eu_tema`,
  `eu_cuidado_relacao`, `eu_aprendizado`, `eu_cuidar`, `eu_carta`; chaves sem conteúdo.
- **Rotas** (todas de sessão): `GET /api/eu`, `GET|POST /api/eu/checkin`, `GET /api/eu/checkins`,
  `DELETE /api/eu/checkins/{id}`, `GET /api/eu/mes`, `GET /api/eu/espelho`, atitudes, aprendizados
  (+ `/{id}/lembrei`), reparos, admirações, intenção, cartas (+ `blocos`, `esqueleto`).
- **Telas**: `#eu`, `#eu/checkin[/AAAA-Www]`, `#eu/mes[/AAAA-MM]`, `#eu/aprendizados`, `#eu/reparos`,
  `#eu/cartas`; Ajustes → 🪞 Área Eu.

### 20.10 Integração dos recursos (07/10/2026)

Os nove recursos foram escritos em paralelo sobre a fundação (20.0.1) e depois juntados. Vale junto com
20.0 a 20.9; onde discordarem, vale esta. Testes em `testes/test_integracao.py` (cada proteção nova
falha com ela desligada; conferido por mutação).

**O que os recursos pediram e entrou no centro**
- **Resumo da importação**: um recurso só aparece em `recursos` quando o arquivo trouxe linhas dele
  (listas com conteúdo; versão e ajustes não contam) ou quando o `importar` contou algo (> 0). Todo
  recurso exporta a sua parte mesmo vazia; antes, "0 marcos novos" de sete recursos enchia a prévia. Só
  dados de recurso novos (registros repetidos) já não dizem "nada novo para juntar".
- **`conhecimento.consultar`** (`motor.Conhecimento`, campo novo, fora da comparação e da IA): a leitura
  do diário aberto para o `extrair` (é o `Banco.consultar`, só leitura). É uma função, não o método:
  o `extrair` recebe cópias do conhecimento, e copiar o método copiaria o Banco. Usam: a área Eu (o
  aprendizado na revisão) e o ciclo (o aviso de planejamento na revisão).
- **Gancho `discretos(banco) -> set[int]`** (o ciclo): registros cujo texto fica só na tela do recurso.
  O Hoje mostra "🔒 Um registro reservado (abra para ver)." em "Últimos registros" (sem pessoas nem
  categorias, `discreto: true`), Pessoas não cita a frase (`Banco.mencoes(..., excluir=)`) nem mostra a
  anotação solta dele como informação da pessoa, e a ligação "mesma pessoa" não o usa. Na dúvida (um
  recurso não abriu ou o gancho falhou), todos os 🔒 também. Os itens tirados da frase do ciclo o
  próprio ciclo descarta ao gravar (o humor de outra frase do mesmo registro continua valendo).
- **Gancho `cartoes_mostrados(banco, referencia, cartoes)`**: depois do anti-excesso, cada recurso sabe
  quais cartões dele apareceram (`[{chave sem o prefixo, nivel, tipo, dados}]`, num ponto de salvamento).
  As perguntas passaram a gravar "sugerida" aqui (antes gravavam no `cartoes_hoje`, mesmo quando o cartão
  ficava de fora num dia cheio — e o espaço entre sugestões contava sem a pessoa ter visto nada).
- **Anti-excesso das sugestões**: a do núcleo (sobre ela, agora) vem primeiro; depois as dos recursos com
  relevância ≥ `alertas.SUGESTAO_URGENTE` (70), pela relevância; as outras num **rodízio pelo dia**
  (ordenadas pelo NOME, deslocadas pelo número do dia). Continua no máximo 1 por fornecedor e 2 no total.
  Medido num mês de teste: sem o rodízio, as ideias de presente (45 dias antes de cada data) ocupavam a
  vaga todo dia e a pergunta da semana nunca aparecia; com ele, cada recurso tem a sua vez.
- **`Banco.transacao()` sem mudança não regrava**: o arquivo só é recifrado e gravado quando
  `total_changes` mudou (o Hoje abre um ponto de salvamento para `cartoes_mostrados` a cada visita).
- **`texto.termos`** com as stopwords normalizadas uma vez só (mesmo resultado; era 90% do tempo do
  `GET /api/eu` num diário de 400 registros).
- **Regras**: "conheci/comi/perdi/recebi/assisti…" (1ª pessoa do pretérito em -i) e "trouxe, quis, pude,
  soube, pus" contam como passado em `regras/datas.py` ("conheci os pais dela em 12/03" era 12/03 do ano
  que vem); "pedi/falei/contei" continuam fala. **Data mais adiante no ano com verbo no passado continua
  indo para a próxima** ("reservei o restaurante dia 25 de outubro" é um plano; o marco ajusta o dele):
  perder um lembrete seria pior. Data logo depois de "até"/"antes do" é **prazo**: não é aniversário nem
  se repete ("comprar o presente de aniversário dela até dia 25" virava o aniversário dela em 25/10, todo
  ano). Acontecimento logo depois de "perguntar dos/sobre/como foi" não leva a data ("perguntar dos
  exames da mãe dela na sexta": a sexta é de perguntar). Nome com maiúscula logo depois de palavra de
  lugar em minúsculas, e o resto de um nome de lugar ("Museu do Ipiranga"), não é pessoa (20.5.1).
  "muito/tanto/mais/bem/demais…" sozinhos não são alvo de gosto ("não curtiu muito").
- **Lembrete da conversa** confirmado descarta o acontecimento das regras da mesma frase e data (20.7.1).
- **Planejamento do ciclo na revisão** (20.8.1).

**Página (`web/app.js`): o contrato `window.EntreNos` ganhou**
- `abrirRegistrar({texto, data, contexto, humor, privado, juntar, organizar, aoSalvar})` → `{ok}` ou
  `{ok: false, motivo: 'revisao'|'bloqueado'}`: preenche o rascunho (com `juntar`, embaixo do que já
  estava, sem repetir), abre o Registrar com o cursor no fim ou já organiza; `aoSalvar(id)` é chamado
  quando ESTE rascunho for salvo (vários podem esperar o mesmo; trocar o texto esquece os anteriores).
  Com uma revisão aberta, mostra a revisão e não mexe em nada.
- `rascunhoAtual()` → cópia de `{texto, data, contexto, humor, privado, emRevisao}`.
- `registrarAcaoRegistrar(id, rotulo, acao(rascunho, {limpar, focar}))`: botão ao lado do Organizar.
- `registrarModoRegistrar(id, rotulo, aoEscolher(rascunho), dica?())`: no alto do Registrar, ao lado de
  "✍️ Texto livre".
- `registrarRevisaoExtra(tipo, render, {opcional: true})`: extra só informativo; salvar sem decidir não
  pede confirmação (a revisão diz "É só um lembrete").
- Eventos em `window`: `entrenos:registro-salvo` `{id, privado}` e `entrenos:bloqueado` (ao ir para o
  portão).
- As telas das perguntas, da anotação rápida e da conversa deixaram de mexer nos campos do Registrar
  pelos ids (`#reg-*`) e de observar a tela (`MutationObserver`): usam só o contrato.

**Decisões de integração**
- `web/telas/<nome>.css` pode ter estilo do recurso (o `index.html` carrega); hoje todos ficaram só com o
  comentário e usam as classes do `estilo.css`.
- `PATCH /api/registros/{id} {privado: false}` num registro que um recurso deixa fora da IA continua
  aceito (a caixinha só muda o 🔒 do usuário; ele continua fora pelo `fora_da_ia`, e a tela trava a
  caixinha com o `fora_da_ia` do `GET`).
- Texto com algum extra `_privado` continua sem ✨ e com 403 em `/api/ia/analisar` **mesmo que a pessoa
  escolha "Não guardar"** o extra: o texto é o mesmo; na dúvida, fica fora da IA.
- Itens das regras que um recurso tira das telas centrais: o recurso os deixa "descartados" pelo
  `banco.atualizar_item` dentro do `gravar_extras` (ciclo, lembrete da conversa) — continuam no registro
  e dá para reativar. É o jeito oficial (não há gancho separado).

**Testado (07/10/2026):** a suíte inteira. Um mês fictício pela API, com o servidor em processo (pasta
temporária, PIN de teste) e o "hoje" do núcleo andando dia a dia de 06/06 a 19/10/2026: 35 registros
cobrindo os nove recursos (5 ciclos com sintomas e o que ajuda; perguntas sugeridas e respondidas,
inclusive uma de Nós dois; presentes e desejos com o aniversário de namoro chegando; promessas com
cumprimento; anotação rápida organizada depois com a data de quando foi anotada; lugares; marcos;
conversa guiada; atitudes, aprendizado que volta na revisão, desentendimento com acompanhamento,
check-ins semanais e mensal, intenção, admiração e carta). Conferido: anti-excesso e textos do Hoje todo
dia (nunca uma palavra do ciclo visível), perfil (Pessoas e Como ela está sem o ciclo), as telas de cada
recurso pelas rotas, exportar → importar num diário vazio (nada perdido; de novo, nada duplicado),
apagar registro levando junto o que depende dele (e sumindo da exportação), uma IA falsa local que não
recebeu nada do ciclo, da área Eu, das respostas de Nós dois nem do 🔒, e bloquear/desbloquear. A página
(`app.js` e as nove telas) rodou num DOM de mentira no Node (Electron como Node), falando com esse
servidor: todas as telas e o Hoje de cada dia do mês sem "undefined/null/NaN", sem caixa de erro, sem
campo sem rótulo, sem HTML montado nem estilo inline, e os fluxos novos (abrirRegistrar, ação e modo do
Registrar, extras opcionais, eventos); cinco mutações na página foram todas pegas. Nada abriu navegador,
`.exe`, PyInstaller ou ISCC, e o núcleo continua sem ter rodado num Pyodide de verdade.

#### 20.10.1 Correção final das etapas 2-4 (07/10/2026)

Três revisões independentes (privacidade, português, telas) acharam 43 problemas; o que mudou está
abaixo, cada mudança com teste de regressão (as frases do revisor viraram casos nos testes do recurso).

**O texto também decide o que fica fora da IA** (privacidade; contrato da fundação)
- `recursos.fica_fora_pelo_texto(extras)` + o atributo opcional **`TIPOS_FORA_PELO_TEXTO`** de cada
  recurso: os tipos de extra (dos que pedem 🔒) que deixam o TEXTO fora da IA mesmo sem confirmar ("Não
  guardar" ou sem decidir). Sem o atributo, nenhum (só o confirmado conta, como antes). O ciclo lista
  todos os dele; a área Eu, só `reparo` (um desentendimento); as perguntas, só `pergunta_feita` (o
  `marca_privado` delas só pede 🔒 para Nós dois): uma atitude sua não confirmada ("levei café pra ela")
  continua podendo ir para a IA.

- `nucleo.texto_fica_fora_da_ia(texto, referencia, banco)` roda as regras no texto (com pessoas e o nome
  dela, sem a leitura do diário) e guarda só sim/não por um resumo (hash) do texto (esquecido ao
  bloquear). Na dúvida (recurso que não abriu ou não leu o texto), sim, menos ao gravar.
- `POST /api/registros`: texto assim é gravado 🔒 com `privado: true` e o aviso
  `AVISO_PRIVADO_GRAVADO` (o aviso da revisão já prometia). Desmarcar a caixinha depois não o manda
  para a IA: `nucleo.fora_da_ia_pelo_texto(banco, ids)` confere o texto do que está para ir — achados do
  ✨ busca (com nova busca sem eles, até 3 voltas, e conferência final), itens do contexto da ✨ sugestão
  (até completar o máximo), o `fora_da_ia` do detalhe (`GET /api/registros/{id}`, a tela trava a caixinha)
  e o `privado` de cada achado da busca comum. Ler o diário inteiro a cada pedido custaria ~9 ms por
  registro; por isso só o que iria para a IA é lido.
- O ✨ Analisar continua recusando (403) qualquer texto com extra `_privado` (20.10).

**Ciclo: um vocabulário só**
- `ciclo.texto_fala_do_ciclo(texto)` = o `extrair` acha algum dado (o mesmo critério da revisão e do
  403); o `fora_da_ia`/`discretos` do ciclo usa isso, com um pré-filtro LIKE que cobre tudo o que o
  `extrair` reconhece ("ciclo", "período", "desceu", "regra", "cólica", "fluxo"; com e sem acento composto)
  e uma memória sim/não por texto. "A regra dela" entrou no vocabulário. "Cólica" sozinha também deixa o
  registro discreto e fora da IA (já escondia o ✨; a decisão em aberto de 20.8.1 continua valendo: se
  ela mudar, o `extrair` muda e tudo acompanha).
- **Gancho `reservado(texto) -> bool`** (o ciclo: tem palavra do ciclo) e `recursos.texto_reservado`
  (na dúvida, sim). `alertas.sem_o_reservado` (no `carregar`, que Hoje, perfil, Presentes e Lugares usam):
  de registro discreto, item com palavra reservada no resumo/alvo sai, e humor/padrão/o que ajuda tirado
  de uma frase com essas palavras sai — **confirmado ou não**, e também para registros antigos. O resto
  fica, sem citar a frase (`Dados.trecho` = ""; a tela cita o resumo): "Consulta — 01/10" continua no Hoje.
  O `_afastar_itens` do ciclo (confirmado) agora também mantém o acontecimento de resumo neutro.
- `recursos.sem_frases_reservadas(valor, discretos)`: Presentes e Lugares (painel e cartões) e as
  promessas (indício, momento, o que ajuda, acontecimentos no mesmo dia) não citam a frase do ciclo de um
  registro discreto (`trecho`/`reacao` = "" e `reservado: true`; a tela diz "🔒 Frase reservada").
- **Garantia final do Hoje** (`alertas._sem_palavras_reservadas`): cartão de recurso ligado a registro
  discreto com palavra reservada no texto, porquê ou dados vira "🔒 Um aviso reservado (abra o registro
  para ver)", com a mesma chave e os mesmos registros. A anotação rápida mostra "🔒 Uma anotação reservada
  (abra para ver)." no cartão do Hoje (`discreto: true`); o texto continua na tela dela. O lembrete
  sensível da semana leva ao registro (o resumo da semana não o mostra).
- O "Últimos registros" já era discreto; Semana também passa a filtrar pelo `reservado` dos discretos.
- **Período fértil**: regular ou não pelos últimos 6 intervalos de verdade, **incluindo** os fora do
  padrão e o longo que você confirmou (só o "faltou registrar um mês?" sem resposta fica de fora). A faixa
  continua saindo dos ciclos parecidos; com um ciclo bem diferente, aviso de irregular e nada de fértil.
- Hábito e condição não são sintoma do dia ("sempre fica com enxaqueca quando menstrua", "quando está com
  cólica, chá ajuda"); "terminou/acabou de menstruar" é fim, com a data.

**IA**: a lista de linguagem de controle (17.3) cobre as paráfrases da revisão ("para ela perceber o que
perdeu", "sentir a sua ausência", "até ela procurar você", "sem avisar", "sem resposta", "dar um tempo nas
mensagens", "exigir", "outra mulher na frente dela", "para ver a reação", "o celular dela", "enquanto ela
dorme"...), e as instruções da sugestão dizem que todo gesto é em direção a ela.

**Apagar**: apagar um item tira também as chaves dispensadas de recurso com a origem dele
(`%:data:<id>:%`, como "presentes:ocasiao:data:12:2026-10-20"), no diário e nas cópias.

**Português (regras e textos)**
- Área Eu: o aprendizado volta só quando o estado dela casa (sem negação, sem outra pessoa no meio; termo e
  categoria sozinhos não bastam), com sinônimos ("exausta" faz voltar "cansada"); risada e verbo não são
  gatilho; "da próxima vez que ela estiver nervosa, vou..." e "aprendi com ela que" viram aprendizado;
  "aprendi a" só sobre ela; a revisão mostra `gatilhos_rotulos` ("ela cansada"). Admiração só por ela
  ("admiro meu pai", "admiro a mãe dela" não). Check-in: promessa com prazo à frente pergunta "Já deu para
  cumprir…?" com "O prazo é daqui a 2 dias"; evento sem a data repetida. Espelho sem a palavra "período".
- Perguntas: "não lembro o que ela respondeu" e "ela respondeu." sozinho não são resposta; "desconversou",
  "ficou quieta", "fugiu do assunto" são recusa; "acho que perguntei" deixa a resposta no máximo 🔵; o
  porquê põe o tempo antes ("Anteontem ela falou de...") e não se contradiz com a lacuna.
- Semana: lembrete com "perguntar sexta como foi..." é a próxima sexta; "mt", "e foi tenso" e ";" na lista
  de assuntos; "O que ela contou" sem promessa nem desentendimento seu.
- Marcos: "faz 2 anos hoje que", "amanhã faz 1 ano que" (âncora no dia dito); "como se fosse ontem" não é
  data.
- Presentes: ideia que contradiz o que ela disse sai (grupos: flores ⊃ girassóis, álcool ⊃ vinho; e
  restrição); "já apareceu ligado a presentes" só com registro de presente (senão "costuma ser um bom
  presente"); o cartão da ocasião deixa de fora o que talvez já foi realizado (a tela os mostra em
  "✔️ Talvez já realizado"); "presenteei ela com", "lista de desejos dela tem", "de presente da X",
  "flores minhas", "ingresso pro show de X".
- Promessas: "combinei com o Pedro de..." sem ela não é promessa; "prometi... que vou" fica "prometi"; o
  indício não casa preposição com assunto ("para" ≠ "paraty").
- Lugares: "achou caro mas gostou" é opinião; a viagem com a precisão dita ("novembro").
- Regras (`regras/itens.py`): "a avó que tá doente" é da avó; "triste ontem mas hoje tá ótima" e
  "cansada e triste" dão os dois estados; "parou de beber vinho" é restrição; "mt" não é alvo de gosto.

**Telas**: Presentes e Lugares salvam a reação/opinião com um botão "Salvar" (as setas do teclado num
select fechado não gravam mais a cada tecla) e o foco volta ao mesmo select; o filtro de opinião devolve o
foco ao botão; sem `aria-expanded` fixo; Presentes vazio ainda mostra o que foi escondido; 🌙 "O que ajuda"
apaga todas as anotações do grupo; intenção do mês com o campo "Como está indo?" e um formulário só por
cartão; "Nós dois" recusada sem "a resposta está no registro"; prazo das promessas no resumo da semana com
a precisão dita; CSS: listas em grade `minmax(0, 1fr)` e citação com `overflow-wrap: anywhere`.

**Testado (07/10/2026, correção final):** a suíte inteira (1.860 testes); três proteções desligadas uma a
uma (o texto na IA, o `reservado`, o vocabulário do ciclo) fazem os testes delas falharem; o mês fictício
pela API refeito (06/06 a 19/10, 26 registros, metade dos extras sem confirmar): Hoje de cada dia sem
palavra do ciclo e com o anti-excesso, telas dos recursos sem o ciclo, IA falsa sem nada do ciclo, da área
Eu, de Nós dois nem do 🔒, exportar → importar sem duplicar, apagar e bloquear/desbloquear. A página rodou
no DOM de mentira (Electron como Node) contra o servidor em processo: todas as telas sem
"undefined/null/NaN", sem campo sem rótulo, sem HTML montado; e os fluxos novos (Salvar da reação e da
opinião com o foco de volta, "Talvez já realizado", "Como está indo?", um formulário só, apagar o grupo do
"O que ajuda", Hoje sem o ciclo). Nenhum navegador, `.exe`, PyInstaller ou ISCC.

**Fica para depois**
- APOIO "X ajuda (quando ela…)" nas regras (o presentes ligaria "Para este momento" mais vezes): não
  entrou porque, em frase do ciclo, criaria um "o que ajuda" com as palavras do ciclo quando o extra não
  é confirmado.
- Nome de lugar sem palavra de lugar antes ("o Ibirapuera") ainda pode virar "Pessoa nova" na revisão.
- Data mais adiante no ano com verbo no passado (acima): fica como está, por decisão de segurança.
- "Cólica" sozinha no ciclo (20.8.1): decisão do usuário.
- (20.10.1) A sugestão de lugar ainda aparece todos os dias da semana ISO (o revisor pediu 1 ou 2 dias;
  mudaria o contrato de 20.5.1 e pede uma decisão). "Um livro da Mariana" sem a Mariana reconhecida como
  pessoa continua com "da Mariana" na descrição (com um nome com maiúscula pode ser a marca: "perfume da
  Natura"). A pergunta "Tem algo que eu possa fazer melhor por você?" não liga a "o que ela acha que eu
  poderia fazer melhor" (só uma raiz em comum). Algumas frases de lugar ("detestou o hotel em Santos",
  "não quer voltar naquele bar da Augusta", "o café é o melhor da cidade") ainda não viram opinião.

## 21. App no celular e no navegador (PWA) — decidido pelo usuário em 07/10/2026

O mesmo `web/` serve o PC (servidor HTTP local, como hoje) e a PWA (site estático, sem servidor).
Na PWA, o **núcleo Python roda num Web Worker via Pyodide 314.0.7** (Python 3.14.2), guardado no
próprio site (sem CDN). O diário fica no armazenamento do navegador (IDBFS em `/dados`), **no
mesmo formato `.cofre`**: backup do PC abre no celular e vice-versa.

### 21.1 Arquivos

- `web/pyodide/314.0.7/`: `pyodide.mjs`, `pyodide.asm.mjs`, `pyodide.asm.wasm`,
  `python_stdlib.zip`, `pyodide-lock.json` (do pacote npm `pyodide@314.0.7`, integridade npm
  conferida) e as rodas `cryptography-47.0.0`, `cffi-2.0.0`, `pycparser-3.0`, `six-1.17.0`
  (SHA-256 do lockfile conferido) + licenças (`LICENSE-pyodide.txt` MPL-2.0 e as demais).
  Atualizar o Pyodide = pasta nova inteira, nunca misturar versões.
- `web/manifest.webmanifest`: `name` "Entre Nós", `short_name` "Entre Nós", `lang` pt-BR,
  `display` standalone, `start_url`/`scope` "./", cores da paleta, ícones PNG 192/512 (+ maskable)
  e `apple-touch-icon` 180 (gerados a partir do ícone do instalador, script em `instalador/`).
- `web/sw.js`: service worker. Pré-cache do app (HTML/JS/CSS/telas/ícones/`entrenos-py.zip`) e do
  Pyodide em caches com versão; Pyodide **cache-first** (imutável); app stale-while-revalidate
  com aviso "nova versão disponível — recarregar" (skipWaiting só com o toque do usuário);
  `cache: 'reload'` no install; limpeza dos caches antigos no activate; nada de resposta opaca
  nem redirect no cache.
- `web/trabalhador.mjs` (Worker `type: "module"`): `loadPyodide({indexURL: './pyodide/314.0.7/',
  packages: ['cryptography']})`; monta IDBFS em `/dados` (`FS.mkdirTree`, `FS.mount`,
  `FS.syncfs(true)` antes de qualquer pedido); baixa `entrenos-py.zip` e faz `unpackArchive` em
  `/app`; `sys.path`; **autoteste** (FTS5 com `remove_diacritics 2`, `serialize/deserialize`,
  `AESGCM`+`Scrypt` com vetor conhecido) — falhou, avisa a tela e não abre o diário;
  `ponte.iniciar_no_navegador('/dados')`; atende mensagens `{id, metodo, caminho, corpo, token,
  consulta}` com `ponte.tratar_json` e, quando `gravou`, **`await FS.syncfs(false)` serializado**
  antes de responder; temporizador do JS chama a conferência de inatividade; **Web Locks**
  (`navigator.locks.request('entrenos-cofre', ...)`): só uma aba/janela é dona do diário; a outra
  mostra "o Entre Nós já está aberto em outra janela".
- `web/entrenos-py.zip`: gerado pelo build com `entrenos/` (sem `servidor.py`, sem `__pycache__`).
- `construir_web.py` (raiz): monta `site/` (ignorado no git) = `web/` + `entrenos-py.zip` +
  `_headers` (Cloudflare Pages) com CSP e tipos; confere SHA-256 do Pyodide; **sem .exe**.

### 21.2 Uma tela para os dois modos

- `app.js` descobre o modo uma vez: `GET api/ping` com resposta `{"app": "entre-nos"}` → **servidor**
  (PC, como hoje); senão → **navegador** (Worker). A função `api()` é a mesma para o resto da tela.
- Abertura: começa a carregar o Pyodide enquanto mostra a tela do PIN, com progresso
  ("preparando o Entre Nós… 40%"). Primeira vez no celular: 5-15 s (medir).
- Backup no navegador: "Baixar uma cópia" (rota `/api/backup/baixar`) com `navigator.share({files})`
  quando houver (salvar no Arquivos/iCloud/Drive) e link de download como alternativa.
  Restaurar e importar pelo seletor de arquivos (já existe).
- `navigator.storage.persist()` ao criar/restaurar o diário; Ajustes mostra
  `navigator.storage.estimate()` e o estado ("protegido contra limpeza: sim/não").
- Instalação: Android/PC (Chromium) — botão "Instalar o app" pelo `beforeinstallprompt`;
  iPhone fora do modo app — instruções "Compartilhar → Adicionar à Tela de Início" (e o aviso:
  instale ANTES de criar o diário, porque o app instalado tem armazenamento separado do Safari).
- IA no navegador: a rota responde 503 com explicação (rede da IA no navegador fica para depois);
  os botões ✨ não aparecem no modo navegador.

### 21.3 CSP e cabeçalhos

- `script-src 'self' 'wasm-unsafe-eval'` (necessário para WebAssembly; confirmar no teste se o
  Pyodide exige `'unsafe-eval'` e, se exigir, só no trabalhador), `worker-src 'self'`,
  `connect-src 'self'` (+ Google na seção 22). O servidor do PC passa a servir `.mjs`, `.wasm`
  (`application/wasm`), `.webmanifest`, `.zip`, `.whl` com tipos certos e o mesmo CSP.
- `_headers` do site com o mesmo CSP, `X-Frame-Options: DENY`, `Referrer-Policy`,
  `Cross-Origin-Opener-Policy: same-origin-allow-popups` (login do Google).

### 21.4 Hospedagem

Cloudflare Pages (escolha do usuário em 07/10/2026; grátis, repositório privado do GitHub, HTTPS). O usuário cria a conta e liga o
repositório (ele mesmo faz os logins); comando de build `python construir_web.py`, pasta `site`.
O site guarda só o código.

### 21.5 Avisos (Android e PC pelo navegador)

Periodic Background Sync (só Chromium, app instalado; intervalo ≥ ~12 h, decidido pelo
navegador) + `showNotification` sem servidor. O service worker não abre o diário (está cifrado),
então o aviso usa uma **agenda de avisos fora do cofre só com DATAS e contagens** (decisão do
usuário em 07/10/2026): ao salvar, o núcleo atualiza no IndexedDB a lista de dias com lembretes
(sem texto, sem tipo, sem nome); o service worker conta os de hoje e mostra "Você tem 2 lembretes
hoje no Entre Nós". Apagar o diário ou desligar os avisos apaga a agenda. iPhone: depois.

### 21.6 Como ficou na implementação (07/10/2026)

Vale junto com 21.1 a 21.5; onde discordarem, vale esta. Resolve, da lista "não feito" de 19.7, o
Web Locks e o `syncfs` no Worker e a página usar `plataforma`, `limite_arquivo` e `backup_em_pasta`.

**Arquivos novos.**

```
construir_web.py              monta site/ (21.1); --licencas extrai as licenças das rodas;
                              --sw-dev refaz as listas do web/sw.js guardado no projeto
instalador/gerar_icones_web.py  PNGs da PWA a partir de gerar_icone.desenhar (Pillow)
web/manifest.webmanifest      id/start_url/scope "./", standalone, fundo e tema #FBF7F2
web/icones/                   icone-192/512 (any), icone-maskable-192/512 (fundo creme, coração
                              na zona segura de 80%), apple-touch-icon.png (180, sem transparência)
web/sw.js                     service worker (classic; importScripts da agenda)
web/trabalhador.mjs           o Worker: só transporte (mensagens, fila, Web Locks, inatividade, agenda)
web/entrenos_pyodide.mjs      a lógica do Worker sem Worker: Pyodide, FS/syncfs, ponte (o teste do
                              Node usa este mesmo arquivo)
web/agenda_avisos.js          a agenda de avisos (IndexedDB), script clássico usado pelo sw.js e
                              pelo trabalhador.mjs (globalThis.EntreNosAgenda)
web/pyodide/314.0.7/licencas/ as licenças tiradas das rodas + LEIA-ME.txt (a lista dos componentes)
testes/test_pwa.py            agenda, servidor, construir_web e os arquivos da PWA
testes/navegador/teste_pyodide.mjs   o Pyodide de verdade no Node (abaixo)
```

**Python.** `alertas.agenda_de_avisos(banco, referencia, dias=60) -> {"AAAA-MM-DD": n}`: para cada
um dos próximos 60 dias, quantos avisos de DATA a página Hoje daquele dia teria: todos os
importantes (hoje e amanhã) + até `MAX_LEMBRETES` lembretes (antecedências de Ajustes, mesversário,
namoro), sem os dispensados (a chave depende do dia, como no Hoje). Sugestões e cartões dos
recursos ficam de fora (dependem do dia em que a página abre). Só lê; o nível de cada data saiu
para `_nivel_do_aviso`, usado pelo Hoje e pela agenda (o teste confere a agenda contra a página Hoje
recalculada dia a dia). `Nucleo.agenda_de_avisos()` (None bloqueado; confere a inatividade antes e
não conta como uso) e `ponte.agenda_de_avisos() -> {"aberto", "dias", "gravou"}` (nunca levanta).
Não há rota HTTP: só o Worker chama.

**Worker** (`trabalhador.mjs` + `entrenos_pyodide.mjs`). Mensagens da página: `{id, tipo: 'api',
metodo, caminho, corpo, token, consulta}`, `{id, tipo: 'restaurar', arquivo (ArrayBuffer
transferido), corpo (só os PINs), token}`, `{id, tipo: 'avisos', ligar}`, `{id, tipo:
'avisos-estado'}`; resposta `{id, ok, resposta | erro}`. Para a página: `progresso {fracao, fase}`,
`ocupado`, `pronto {medidas}`, `falhou {motivo: carga|autoteste|dados|fatal|sem-suporte, falhas}`,
`bloqueou`. Ordem (o teste confere): Pyodide (`loadPyodide` sem `packages` + `loadPackage(['cryptography'])`,
só para dar progresso e calar as mensagens; o lock confere o SHA-256 das rodas) e o
`entrenos-py.zip` em paralelo → `unpackArchive` em `/app` → `autoteste` → **trava** → IDBFS em
`/dados` + `syncfs(true)` → `iniciar_no_navegador('/dados')`. A trava é pedida logo no começo
(`navigator.locks.request('entrenos-cofre', {ifAvailable: true})`): sem ela, avisa `ocupado` e
espera a outra aba soltar (o Python carrega enquanto isso; o diário só é tocado depois). Tudo o
que mexe no Python vai numa fila (um pedido por vez); `syncfs(false)` em outra fila, aguardado antes
da resposta sair. Inatividade: `setInterval` de 30 s. Sem diário ao abrir, apaga a agenda. stdout e
stderr do Python não vão para o console.

**Página** (`app.js`). O modo sai uma vez, em `carregarEstado` (o `quandoPronto(carregarEstado)`
continua): service worker no controle → navegador (no PC ele nunca é registrado); senão
`fetch('api/ping')` com 4 s de limite → `{"app": "entre-nos"}` é o servidor. Em
127.0.0.1/localhost nas portas 8787-8799 é sempre servidor (sem resposta: "Não encontrei o Entre
Nós"), para nunca criar um segundo diário no armazenamento do navegador do PC; no modo servidor
a página também desregistra service workers que tiverem sobrado. `api()` é a mesma para tudo (e
para os recursos): no navegador manda ao Worker e trata a resposta igual (`resultadoDaApi`).
Restaurar no navegador lê o `.cofre` como ArrayBuffer e vai em bytes; limites pelo `limite_arquivo`
(16 MB). Abertura: com `localStorage['entrenos.temDiario'] === '1'` (só sim/não; o próprio
armazenamento do site já mostra que há diário) a tela do PIN aparece na hora, com "Preparando o
Entre Nós… N%", e o PIN digitado espera o Python; senão, "Preparando". Falhas têm tela própria,
com "Tentar de novo" e os nomes técnicos do autoteste (sem conteúdo). Ajustes ganha 📱 **Este
aparelho** (instalar: `beforeinstallprompt` guardado ou os passos do iPhone; espaço pelo
`estimate()` e "Proteger contra limpeza" pelo `persist()`, também pedido ao criar e ao restaurar;
avisos). No navegador somem a área da IA (e os ✨), "Fechar o Entre Nós" (fica "Bloquear") e o
backup em pasta, que vira **Baixar uma cópia** em dois toques (preparar → compartilhar ou baixar:
o `navigator.share` exige um toque recente). Textos com "computador", pasta e `%LOCALAPPDATA%`
viram "aparelho"/"navegador" nas telas de começo, restauração, cópias, dados e privacidade. No
iPhone fora do app instalado, o primeiro uso avisa para instalar antes de criar o diário.

**Service worker.** Dois caches: `entrenos-app-<VERSAO>` e `entrenos-pyodide-314.0.7`. Em vez de
stale-while-revalidate arquivo a arquivo (que misturaria versões no mesmo cache), a revalidação é
da versão: `VERSAO` = 12 hex do SHA-256 de todos os arquivos do site, gravada no `sw.js` pelo build;
qualquer mudança muda o `sw.js`, o navegador instala a versão nova em segundo plano (registro com
`updateViaCache: 'none'` e `update()` ao voltar para a aba) e a página mostra "Nova versão do Entre
Nós disponível" (Recarregar | Depois). Recarregar bloqueia o diário, manda `pular-espera` (o único
`skipWaiting`) e recarrega no `controllerchange`. Na instalação: app com `cache: 'reload'`; Pyodide
com `cache: 'default'` (imutável, e assim a primeira abertura não baixa duas vezes o que o Worker
está baixando ao mesmo tempo). Só entra no cache resposta 200, `basic`, sem `redirected`, pedida com
`redirect: 'error'`; a página entra como `./` (o Cloudflare Pages redireciona `/index.html` para
`/`). Nunca responde `api/`, outro site, POST nem arquivo fora das listas (as licenças vão pela rede).

**CSP** (substituído em 21.7 e 22.2: a CSP vai uma vez em "/*", e o PC não tem 'wasm-unsafe-eval').
`script-src 'self' 'wasm-unsafe-eval'; worker-src 'self'; manifest-src 'self'` a mais, no
PC e no site. `'unsafe-eval'` não é preciso: no Pyodide 314.0.7 o `eval` só roda para módulos
dinâmicos com EM_ASM/EM_JS, e as rodas daqui não têm (conferido nos `.so`); confirmar num navegador
de verdade. No `_headers` a CSP vai só em `/`, `/index.html`, `/sw.js` e `/trabalhador.mjs` (a do
documento vale para a página; a do script, para o Worker e o service worker): o Cloudflare junta
com vírgula o mesmo cabeçalho de duas regras, o que viraria duas políticas. Tipos explícitos para
`.mjs`, `.wasm`, `.webmanifest`, `.zip` e `.whl`, uma regra por arquivo; Pyodide com
`Cache-Control: immutable`; `sw.js` e a página com `no-cache`. COOP `same-origin-allow-popups` só
no site (o PC continua `same-origin` até a seção 22).

**Avisos.** IndexedDB `entrenos-avisos`, loja `agenda`: `ligado` (true), `agenda` ({dias, gerada_em})
e `avisado` (o último dia avisado). O Worker grava a agenda (com os avisos ligados) depois de cada
pedido que gravou algo e ao desbloquear, configurar e restaurar. Ligar (Ajustes → Este aparelho):
permissão de notificação, `periodicSync.register('entrenos-avisos', {minInterval: 12 h})` (com a
permissão `periodic-background-sync` concedida) e o Worker liga e calcula; desligar tira o registro e
apaga tudo. O `periodicsync` só avisa com os avisos ligados, permissão concedida e uma vez por dia;
tocar no aviso abre `#hoje`. "Apagar o diário" no navegador é limpar os dados do site (vai junto,
mesma origem); além disso o Worker apaga a agenda quando abre e não há diário.

**Licenças.** As das rodas (cryptography: LICENSE, LICENSE.APACHE, LICENSE.BSD; cffi: LICENSE,
AUTHORS; pycparser; six) em `licencas/`, conferidas a cada build, e o `LICENSE-pyodide.txt`. As do
CPython, do Emscripten e das bibliotecas compiladas junto (SQLite, OpenSSL, zlib, libffi...) não
vêm nos arquivos do Pyodide guardados aqui: listadas como pendentes no `LEIA-ME.txt`.

**Medido (Node 24 do VS Code, neste PC; no celular deve ser algumas vezes mais lento):** carga do
Pyodide 2,5 s + cryptography 0,35 s + zip = 3,2 s; autoteste 1,6 s; scrypt do PIN (2^16, r=8) 0,30 s
no Pyodide contra 0,27 s no CPython; configurar 0,3 a 0,5 s; desbloquear 0,25 a 0,27 s; `construir_web.py` 0,9 s.
Site: 51 arquivos, 16,4 MB (o `pyodide.asm.wasm` tem 9,6 MB).

**Testado.** `testes/test_pwa.py` (27 testes) e a suíte inteira no CPython (1887 OK).
`testes/navegador/teste_pyodide.mjs` (20 passos, ~20 s)
no Pyodide de verdade (Node, sem navegador): monta o site com o `construir_web.py`, carrega pelo
`entrenos_pyodide.mjs`, autoteste, configurar → analisar → registros com extras (promessa) → hoje →
busca → agenda (só datas e números) → bloquear → PIN errado 401 → 429 com `Retry-After` →
desbloquear → exportar → backup 409/baixar → IA 503; `.cofre` criado pelo CPython restaurado e
desbloqueado no Pyodide com o mesmo PIN, e o criado no Pyodide aberto no CPython (e recusado com
outro PIN); o `trabalhador.mjs` com um `self` de mentira (trava ocupada → espera, ids, restaurar em
bytes, agenda ao desbloquear, desligar apaga, inatividade → `bloqueou` e 423); a agenda com um
IndexedDB de mentira; e o `sw.js` do site num `vm` (pré-cache, recusa de redirect/opaco/404, rotas,
limpeza dos caches velhos, aviso só com permissão e uma vez por dia). No Windows o Pyodide do Node
chama `process.getuid()`, que não existe ali, e o Rust do cryptography aborta ao importar ("panic in
a function that cannot unwind"); no navegador a chamada devolve 0, e o teste põe o mesmo 0. Nada
abriu navegador; o `app.js` foi conferido com `--check`, pelos testes de texto e por leitura.

**Não feito / pendente:**
- conferir num navegador de verdade (Chrome Android, Safari iPhone, Chrome/Edge no PC): CSP sem
  `'unsafe-eval'`, IDBFS e `syncfs`, Web Locks entre abas, instalação, `share` de arquivo,
  `persist()`, Periodic Background Sync e o aviso, a faixa de versão nova, tempos e memória no
  celular (5-15 s na primeira vez era a estimativa);
- licenças do CPython, do Emscripten e das bibliotecas compiladas junto;
- o `instalador/entrenos.spec` leva `web/` inteira: o instalador do PC passaria a levar o Pyodide
  (16 MB) que ele não usa; tirar `web/pyodide` e o `entrenos-py.zip` quando o instalador for refeito;
- listar e abrir pela tela as cópias internas de `backups/` no celular (19.7) e "apagar o diário
  deste aparelho" pela tela;
- outros textos com "computador" nas telas dos recursos e nas mensagens do núcleo;
- avisos no iPhone; IA no navegador (transporte por `fetch` do Worker, 19.6).

### 21.7 Correção final (08/10/2026)

Achados de três revisões (segurança, o Pyodide de verdade e as telas), cada um reproduzido antes de
corrigir. Vale junto com 21.1 a 21.6; onde discordarem, vale esta. A parte da sincronização está em 22.2.

**json_each no Pyodide (alto).** No SQLite 3.39 do Pyodide, o primeiro `json_each` de uma conexão monta a
tabela virtual e pede ao autorizador UPDATE em `sqlite_master`; o `_autorizar_leitura` do
`Banco.consultar` nega, e a consulta de um recurso falhava ("vtable constructor failed: json_each") até
alguma rota do núcleo usar `json_each` sem autorizador. Toda conexão nova (configurar, desbloquear,
restaurar) começava assim: logo depois de abrir, `GET /api/perguntas` dava 500, o Hoje perdia o cartão
"Conheça melhor" e a cada Hoje ia um erro para o `erros.log` (com um `syncfs` a mais). No SQLite do PC
isso não acontece. Agora `banco._montar_tabelas_json` usa `json_each` uma vez, sem autorizador, ao criar
a conexão (`Banco.novo`) e logo depois do `deserialize` (`Banco.de_bytes`). **Nunca antes de um
`deserialize`**: ele troca o esquema da conexão e a tabela montada antes fica apontando para o esquema
que saiu (no Pyodide, "memory access out of bounds" e o Python inteiro cai; reproduzido). O autorizador
não foi afrouxado. O `ponte.autoteste` ganhou o passo `json_dos_recursos` (Banco.novo e de_bytes, pelo
`consultar`), e o `teste_pyodide.mjs` confere perguntas, semana e Hoje logo depois de desbloquear.

**O Hoje mais leve (médio).** `Banco.lembrar(chave, calcular)` guarda o resultado de uma leitura cara até
a próxima escrita na conexão (`total_changes`; qualquer INSERT, UPDATE ou DELETE invalida tudo) e não
guarda nada dentro de uma transação (um ROLLBACK desfaria dados sem mudar o contador). Usam:
`alertas.carregar` (as leituras; cada chamada recebe cópias dos contêineres, dicts e itens),
`recursos.fora_da_ia` e `recursos.discretos` (a chave inclui quais ganchos responderam e os recursos
que falharam) e `ciclo._registros_que_falam_do_ciclo`. Medido (CPython, este PC, 600 registros): o
Hoje passou de 0,36/0,30/0,28 s para 0,21/0,15/0,12 s (antes `discretos` rodava 50 vezes e
`alertas.carregar` 3 por pedido). Com textos curtos: 1.500 registros, 0,35 s na primeira vez e 0,25
nas seguintes; 3.650 (10 anos, um por dia), 0,89 e 0,62 s, quase tudo nos `cartoes_hoje` de presentes
(0,59 s) e da área Eu (0,21 s), espalhado (no perfil de 1.500 registros, 10 mil chamadas de
`texto.normalizar`, sem um laço quadrático): o custo continua
crescendo com o diário (no celular, algumas vezes mais), e é o próximo a atacar.

**A cópia do celular em bytes (baixo).** `Nucleo.baixar_copia(token)` devolve (resposta {nome, tamanho},
bytes), `ponte.baixar_copia(token)` guarda os bytes e `ponte.copia_em_bytes()` os entrega uma vez; o
`AppPython.baixarCopia` converte (`toJs`, um Uint8Array com buffer próprio) e o Worker atende
`{tipo: 'baixar', token}` transferindo o ArrayBuffer para a página (`copiaEmBytes` no `app.js`). Antes
o .cofre ia em base64 dentro do JSON (texto 33% maior no Python, na string, no `JSON.parse` e no
`postMessage`; o wasm não devolve memória). No PC continua a rota com base64.

**CSP e o _headers (médio e baixo).** A CSP do site vai **uma vez, em "/*"** (antes só em "/",
"/index.html", "/sw.js" e "/trabalhador.mjs": um endereço qualquer que o Cloudflare respondesse com o
app abriria a página sem CSP), e o `404.html` faz endereço desconhecido responder 404. Uma regra só,
então nada é juntado com vírgula. A do PC (`servidor.CSP`) não tem mais `'wasm-unsafe-eval'` (a página
do PC não roda WebAssembly) nem nada do Google além das conexões (22.2), e ganhou `object-src 'none'`
e `form-action 'self'` (não `'none'`: os diálogos usam `<form method="dialog">`). A do site é a mesma
com `'wasm-unsafe-eval'` (`construir_web.csp_do_site`): no Cloudflare a CSP do script do Worker vem da
mesma regra "/*", então a página também a recebe. Páginas `.html` (oauth, 404) ficam fora do
pré-cache: o Cloudflare redireciona `/x.html` para `/x`, e redirect não entra no cache.

**Telas do celular (médio e baixo).**
- `telaSemNucleo`: falha no `GET /config` depois do PIN nunca mostra "Não encontrei o Entre Nós... menu
  Iniciar" no celular: tela de falha do navegador, ou o PIN de novo com a mensagem num 409 (outra aba).
- Worker que morre depois de pronto: tela de falha (com "Tentar de novo") em vez de avisos soltos.
  Pedido sem resposta: depois de 90 s (300 s em restaurar, importar, baixar a cópia e sincronizar,
  contando só depois de o Python ficar pronto) vira a tela "O Entre Nós parou de responder". As
  mensagens não pedem mais "recarregue a página" (o app instalado não tem botão de recarregar).
- Primeira abertura: a barra fica "andando" (sem número) até o Python chegar, em vez de parada em
  3%; o texto diz que são cerca de 16 MB (prefira o Wi-Fi) e até um minuto; depois de 60 s, "Ainda
  baixando… confira a conexão". (O progresso real do download fica para depois.)
- iPhone no Safari, sem diário: os passos de instalar aparecem ANTES de baixar o app (o app
  instalado tem armazenamento separado e baixaria tudo de novo), com "Continuar no Safari mesmo
  assim" (guarda só um sim em `localStorage['entrenos.continuarNoSafari']`). Passos com o iOS 26
  (Compartilhar dentro do menu ⋯, "Abrir como App Web").
- Avisos: a permissão de notificação só é pedida depois de conferir o despertar periódico
  (`periodicSync` no `ServiceWorkerRegistration`, app instalado, permissão `periodic-background-sync`);
  sem isso a caixinha fica desabilitada com a explicação. A caixinha mostra o estado de verdade
  (ligado no IndexedDB, notificação permitida e o registro em `periodicSync.getTags()`); ligado sem
  permissão ou sem registro: a agenda é apagada e a tela diz por quê.
- O seletor do backup aceita `.cofre,application/octet-stream` (no iPhone, só `.cofre` pode deixar o
  arquivo cinza); o `problemaDoArquivo` confere a extensão depois.
- Restaurar: com a sincronização ligada, a confirmação diz que o que está nos outros aparelhos volta e
  o apagado neles continua apagado (testado em `test_sincronizacao.py`); no celular, que a cópia
  interna ainda não abre pela tela, com o botão "Baixar uma cópia de agora" ali mesmo. Textos do
  danificado e da restauração sem "computador"/`entrenos.cofre`/pasta no celular.
- O 🔒 não promete mais "nunca sai deste computador": "nunca vai para a IA" (e, com a sincronização,
  vai embaralhado para os seus outros aparelhos). Também na conversa da semana.
- `index.html`: o texto de "Abrindo…" serve aos dois (celular: a internet; computador: o antivírus).
- Faixa de versão nova: diz que o PIN é pedido de novo e, com rascunho em Registrar, confirma antes.
- PIN com outra janela dona do diário: o botão diz "Esperando a outra janela fechar…" e o texto lembra
  da aba do Chrome/Safari esquecida no fundo depois de instalar.
- Tocar no aviso com o app aberto em outra tela: o service worker manda `{tipo: 'abrir-hoje'}` e a página
  vai para o Hoje (sem recarregar).
- Falhas: "se você já tinha um diário aqui, ele continua guardado"; no iPhone, "atualize o iOS" no
  lugar de "use outro navegador".
- Espaço: "O app e o diário usam X (a conta inclui o próprio app, uns 16 MB)"; depois de baixar a
  cópia, onde ela costuma ficar (Downloads; no iPhone, Arquivos → Transferências).
- Primeiro uso: "Usava a sincronização em outro aparelho? Crie um diário aqui e ligue a sincronização";
  no navegador de um computador, "este é um diário só deste navegador".
- `DIARIO_MUDOU` e "sem o arquivo do diário" sem "recarregue a página".

**Testado (08/10/2026).** `teste_pyodide.mjs` (21 passos): perguntas, semana e Hoje logo depois de
desbloquear (o json_each), e o Worker entregando a cópia como ArrayBuffer transferido (e 401 sem
arquivo). `test_sincronizacao.TestMemoriaDoBanco` (`lembrar` e json_each), `test_pwa` (a CSP em "/*",
404.html e oauth.html no site, a CSP mínima). O `app.js` foi conferido com `--check` e por leitura.

**A conferir num aparelho de verdade:** o seletor de `.cofre` no iPhone; os avisos com e sem a
permissão; a barra indeterminada e o texto de 60 s numa rede lenta; os passos do iOS 26; a tela de
falha quando o celular encerra o Worker; o 404 e a CSP em "/x" no Cloudflare (`curl -I`).

## 22. Sincronização (Google Drive) — decidido pelo usuário em 07/10/2026

Tela **Ajustes → ☁️ Sincronização**, no padrão da tela da IA: serviço (Google Drive agora;
Dropbox/OneDrive aparecem como "em breve"), **ID do app** (Client ID do Google, não é segredo),
"Conectar ao Google" (o próprio usuário entra na tela do Google), situação ("conectado como…,
última sincronização há 3 min"), "Sincronizar agora", "Desconectar", lista de aparelhos, e o
passo a passo para criar o ID no Google Cloud (projeto, Drive API, cliente OAuth Web, origens
autorizadas: o endereço do site e `http://localhost:8787`, usuário de teste).

- **Escopo só `drive.appdata`** (pasta oculta do app; não enxerga mais nada do Drive).
- **Um arquivo por aparelho** na pasta do app (`entrenos-<aparelho>.sync`) + `entrenos-sync.json`
  (sal e parâmetros da chave de sincronização, verificador). Cada aparelho só escreve o seu
  arquivo (o Drive não tem gravação condicional confiável); a mescla acontece na leitura.
- **Chave de sincronização** derivada de uma **senha de sincronização própria** (decisão do usuário
  em 07/10/2026; pode ser igual ao PIN, mas trocar o PIN não quebra a sincronização) (scrypt, sal comum
  guardado no `entrenos-sync.json`), digitada uma vez em cada aparelho e guardada dentro do
  diário cifrado de cada um. O conteúdo de cada `.sync` = exportação do diário + lápides, cifrado
  com AES-GCM nessa chave. O Google só vê arquivos embaralhados.
- **Mescla (fase 1):** registros novos dos outros aparelhos entram pela lógica de importar
  (juntar, sem duplicar, 🔒 preservado, extras dos recursos inclusive ciclo/eu); **lápides**
  (impressão digital do registro apagado, sem conteúdo) fazem o apagado sumir em todos os
  aparelhos e não voltar; mudanças de status/edição de item: vale a mais recente (carimbo de
  data/hora). Conflito que não dá para decidir sozinho → a tela pergunta.
- Quando: ao abrir (se o acesso ao Google ainda vale), depois de salvar (agrupado, ~30 s) e no
  "Sincronizar agora". Sem internet: fica na fila. Token do Google dura ~1 h e só se renova com
  um toque (limite do Google para app sem servidor) — a tela mostra "toque para sincronizar".
- PC (servidor) e navegador (Worker) usam o mesmo código: o JS fala com o Google e entrega os
  arquivos ao núcleo (`/api/sync/...`), que decifra, mescla e devolve o seu arquivo cifrado.
- iPhone instalado: o login do Google por janela pode falhar (risco conhecido); se falhar, a tela
  explica e oferece o caminho manual (baixar/restaurar/importar).

### 22.1 Como ficou na implementação (07/10/2026)

Vale junto com a lista acima; onde discordarem, vale esta. Testes em `testes/test_sincronizacao.py`
(três aparelhos com núcleos de verdade e um Drive de mentira; 41 testes) e
`testes/navegador/teste_sincronizar.mjs` (a página contra um Drive de mentira, três aparelhos com o
núcleo no Pyodide de verdade e a tela num DOM de mentira).

```
entrenos/sincronizacao.py     senha, chaves, cifra dos arquivos, estado local (uids, carimbos, lápides),
                              mescla, perguntas; só biblioteca padrão + cryptography, roda no Pyodide
web/sincronizar.js            Drive REST v3, login (GIS), o motor (fila, agrupamento, erros) e a seção
                              Ajustes → ☁️ Sincronização (registrarSecaoAjustes)
entrenos/banco.py             esquema 5 (tabelas sync_*), juntar_exportacao(), "sync" fora da exportação
entrenos/nucleo.py            as rotas /api/sync/..., o "sync" fora de /api/config, restaurar = aparelho novo
entrenos/servidor.py          CSP com os endereços do Google e COOP same-origin-allow-popups (também no PC)
web/app.js                    eventos 'entrenos:gravou' e 'entrenos:desbloqueado'; Privacidade
```

**Senha e chaves.** `scrypt(senha NFC, sal de 16 bytes, n=2^16, r=8, p=1)` = chave mestra (32 bytes).
Dela saem por HMAC-SHA256 com rótulos diferentes: a chave da cifra, a das impressões (uids, lápides,
assinaturas) e o **verificador** (o que vai para o Drive; nunca a chave). Regras da senha = as do PIN
(`problema_da_senha`, o texto fala em "senha de sincronização"); pode ser igual ao PIN.
`entrenos-sync.json` = `{app, tipo: "sincronizacao", formato: 1, kdf: {algoritmo: "scrypt", sal, n, r,
p}, verificador}`: sem data, nome nem aparelho. Parâmetros fora do que o app grava (os mesmos limites
do cofre: n até 2^17, r=8, p=1) são recusados antes do scrypt.
No diário, o ajuste `"sync"` guarda `{versao, chave, sal, n, r, p, verificador, aparelho (16 hex),
nome, id_app, conta, automatica, ultima, enviado, ligada_em, versao_estado}`. `banco.chave_da_sincronizacao`:
sai de `/api/config`, da exportação (`config_sem_segredos`) e o `PATCH /api/config` recusa (400); o
histórico registra só "ajustes alterados: sync". Senha errada: 401 com `espere_segundos` e o mesmo
bloqueio crescente do PIN (contador próprio, só em memória: quem tem o `entrenos-sync.json` testa
senhas fora do app de qualquer jeito); antes da hora, 429 sem scrypt.

**O arquivo de cada aparelho.** `b"ENTRENOS-SYNC" | formato (1 byte) | nonce (12) | AES-256-GCM(zlib(JSON))`
com o cabeçalho como dado associado. O JSON: `{app, tipo, formato, versao_app, aparelho, nome, gerado_em,
exportacao (Banco.exportar() sem config, histórico, avisos dispensados e avisos), recursos_falhos, ids:
{registros|itens|pessoas: {id daqui: [uid, carimbo]}}, lapides: [[uid, tipo, apagado_em]], dados_dela}`.
Ao abrir: cabeçalho, tag do GCM (mexido ou de outra senha = "não abre", 400 `arquivo_invalido`), zlib
com limite (`MAX_DESCOMPRIMIDO` = 64 MB: bomba de zip para), formato (mais novo no byte, no JSON ou no
esquema da exportação = 409 `versao_nova`, "atualize este aparelho") e o nome: arquivo com o nome de
outro aparelho é recusado. O arquivo do próprio aparelho é ignorado (`{proprio: true}`).

**Identidade e carimbos (esquema 5).**
- `sync_ids(uid, tipo, local_id, assinatura, carimbo)`: cada registro, item e pessoa daqui. O uid sai
  do conteúdo, para dois aparelhos com o mesmo diário (um backup restaurado) acharem o mesmo:
  registro = HMAC(data, criado_em, texto normalizado como no importar); item = HMAC(uid do registro,
  tipo, trecho, criado_em); pessoa = HMAC(nome e relação normalizados); repetidos ganham `-1`, `-2`
  na ordem dos ids; o que já tem lápide (ou colide) ganha um uid aleatório. Quem chega de outro
  aparelho fica com o uid de lá. O mesmo registro, item ou pessoa com dois uids (casado pelo conteúdo)
  fica com o menor nos dois lados.
- `assinatura` = HMAC dos campos sincronizados (registro: `privado`; item: os 15 campos de
  `_COLUNAS_ITEM` menos `pessoa_id`; pessoa: nome, relação, apelidos em ordem, observações,
  aniversário). Mudou desde a última conferência: carimbo novo = quando mudou (o `atualizado_em` do
  item, nunca no futuro; pessoa e 🔒 = a hora da conferência), sempre depois do carimbo anterior. Quem
  é visto pela primeira vez ganha o carimbo de época (1970): "não se sabe quando". Carimbos em UTC com
  **microssegundos** (o banco guarda segundos; as frações vêm do relógio de agora): dois aparelhos
  nunca carimbam igual por acaso, e o carimbo de lá é copiado quando a versão de lá é aplicada. Por
  isso **o mesmo carimbo (fora a época) é a mesma versão** (`_decidir_edicao`): se o banco daqui
  guardou algum campo de outro jeito, não vira pergunta a cada sincronização. Datas para mostrar
  (`ultima`, `gerado_em`, `mesclado_em`) ficam em segundos (o Safari não lê as frações).
- Sumiu daqui = lápide (`sync_lapides(uid, tipo, apagado_em)`), só a impressão. As lápides nunca saem
  e vão em todos os arquivos (a união): o apagado não volta nem por um aparelho que ficou dias sem
  sincronizar. Registro escrito de novo depois de apagado é outro (o `criado_em` entra no uid).
- `atualizar_estado(absorver=True)`, no fim de cada mescla: o que a própria mescla mudou (importação
  que completou uma pessoa, apagado por lápide) só atualiza a assinatura, sem carimbo novo nem lápide.

**A mescla** (`mesclar`, um arquivo de outro aparelho por vez, numa transação, um arquivo gravado):
1. `atualizar_estado` (o que mudou ou sumiu aqui vira carimbo ou lápide antes de qualquer coisa);
2. as lápides de lá entram e apagam aqui o registro, item, pessoa ou linha de recurso (pelo
   `apagar_registro`/`apagar_item`/`apagar_pessoa` de sempre; o núcleo repete nas cópias automáticas);
3. a exportação de lá, sem nada que tenha lápide, entra por `Banco.juntar_exportacao` (o `_juntar` do
   importar, dentro da transação, com os mapas de ids de volta e as pessoas que já têm uid aqui
   amarradas, para não casar de novo pelo nome: renomear não duplica); o histórico diz "sincronização:";
4. os uids do que entrou ficam iguais aos de lá; item de registro que os dois têm e que só lá existe
   entra (`acrescentar_item`), a não ser que seja o mesmo item com outra identidade;
5. edições: item e pessoa que existem dos dois lados com valores diferentes — vale o carimbo mais
   recente; os dois mudaram a menos de `JANELA_CONFLITO` (120 s; e os dois "de época") = **pergunta**
   (`sync_conflitos`, com a versão de lá; até a pessoa responder, fica a daqui). Pessoa: antes de
   perguntar, o que um lado tem e o outro deixou em branco se completa (apelidos somam); só dois
   valores diferentes no mesmo campo perguntam. O 🔒 nunca pergunta: vale o mais recente e, na
   dúvida, fica 🔒 (o "só liga" da importação é desfeito quando o desmarcar é o mais recente);
6. dados dela (nome, aniversário, início do namoro): só completam o que está vazio aqui;
7. `sync_aparelhos` (nome, `gerado_em`, a etiqueta do Drive) e as linhas vistas daquele aparelho.

**Linhas dos recursos.** Cada linha das listas que um recurso exporta tem uma impressão: HMAC do
recurso, da lista e do conteúdo **sem** `id`, campos `*_id`/`*_ids`, `criado_em` e `atualizado_em`
(o que muda ao importar). Conferido: depois de exportar → importar, todas as linhas dos nove
recursos têm a mesma impressão nos dois diários (teste). `sync_linhas('', ...)` é a foto daqui na
última conferência: linha que sumiu vira lápide; editada vira lápide da versão de antes + linha nova
(nos outros, a de antes sai e a nova entra). Sem lápide quando o recurso não exportou agora
(`recursos_falhos`, um recurso que não abriu) ou quando a versão do app mudou (`versao_estado`: o
formato pode ter mudado; a foto é refeita sem apagar nada). Lápide de lá apaga a linha daqui (a
tabela é achada pelo id e pelos valores: a de mais colunas em comum, e só se for uma) **menos** se
outra linha daqui ainda aponta para ela (`X_id` → lista `Xs`/`Xes`/`X` do mesmo recurso: o lugar de
uma menção, o ciclo de uma anotação), para nada ficar solto; vão as filhas primeiro, depois as mães.
`sync_linhas(<aparelho>, ...)` = o que já veio daquele aparelho: só entra o que é novo de lá (o que
veio e foi apagado aqui não volta dele, mesmo que a impressão não ache a cópia); uma linha que já
veio entra de novo só para ligar uma linha nova que aponta para ela (o recurso junta sem duplicar).
"Apagar todos os dados do ciclo" num aparelho apaga nos outros.

**Rotas** (todas de sessão; `senha` é livre, com o scrypt fora da trava do app):

| Método e rota | Corpo | Resposta |
|---|---|---|
| GET /api/sync/estado | | `{configurada, servico, id_app, nome, automatica, conta, aparelho, arquivo, arquivo_config, ultima, enviado, sync_json, aparelhos: [{aparelho, nome, gerado_em, etiqueta, mesclado_em}], conflitos, plataforma}` (`sync_json`: o entrenos-sync.json desta chave, sem segredo, para recriar se sumir do Drive) |
| PATCH /api/sync/ajustes | `{id_app?, nome?, automatica?, conta?}` | o estado; outro campo 400 |
| POST /api/sync/senha | `{senha, sync_json?, substituir?}` | `{aparelho, arquivo, sync_json (só ao criar), criado}`; 401/429 senha errada; 409 já ligada (sem `substituir`) |
| POST /api/sync/iniciar | `{sync_json}` | `{aparelho, arquivo, etiquetas: {aparelho: etiqueta}}`; 409 `senha_mudou` se o do Drive não é o desta chave; 409 `sem_senha` |
| POST /api/sync/receber | `{nome, etiqueta?, conteudo_b64}` (até 64 MB; 16 MB no celular) | `{aparelho, nome, registros_novos, pessoas_novas, itens_novos, linhas_novas, atualizados, apagados, conflitos, lapides_novas, avisos}`; 400 `arquivo_invalido`; 409 `versao_nova` |
| POST /api/sync/preparar | | `{nome, tamanho, conteudo_b64[, aviso]}` (aviso acima de 11 MB: um celular pode não abrir) |
| POST /api/sync/enviado | `{etiqueta}` | `{ultima}` |
| GET /api/sync/conflitos | | `[{id, tipo: item\|pessoa, aparelho, nome_aparelho, resumo, contexto, campos: [{campo, aqui, la}]}]` |
| POST /api/sync/conflitos/{id} | `{fica: "este"\|"outro"}` | `{}`; a escolha ganha carimbo depois das duas versões e da janela (vai para todos) |
| POST /api/sync/desligar | | o estado; a chave e as tabelas sync_* saem daqui e o "sync" sai das cópias automáticas |

Restaurar um backup com sincronização ligada: o aparelho ganha identidade nova (`depois_de_restaurar`;
senão dois aparelhos escreveriam o mesmo arquivo), nome padrão, sem conta. Copiar a pasta de dados à
mão para outro aparelho não é detectado (use backup e restaurar).

**A página** (`web/sincronizar.js`, script clássico depois do `app.js`; o motor fica em
`globalThis.EntreNosSincronizar` para o teste do Node):
- `criarDrive({buscar, token, base, baseEnvio})`: `GET files?spaces=appDataFolder` (páginas, `orderBy
  createdTime`), `GET files/{id}?alt=media`, `POST upload/files?uploadType=multipart` (metadados com
  `parents: ["appDataFolder"]`), `PATCH upload/files/{id}?uploadType=media` (só o próprio arquivo),
  `DELETE` (só a configuração que o próprio aparelho acabou de criar numa corrida) e `about` (o e-mail,
  para "conectado como"). `credentials: 'omit'`, sem referrer.
- Erros em frases (`MENSAGENS`): sem rede (fila: tenta de novo a cada minuto e no evento `online`),
  401 (esquece o token: "toque para sincronizar"; nunca abre a janela sozinho), Drive API desligada,
  permissão negada, sem espaço, limite e falha passageira (nova tentativa em 2, 5, 15 min), arquivo
  que não abre ou de versão nova (fica de fora com aviso; o resto sincroniza).
- `criarConta()` (substituído em 22.2: sem biblioteca do Google): Google Identity Services, **modelo de token**, escopo só `drive.appdata`;
  `accounts.google.com/gsi/client` só é posto na página depois que a pessoa salva o ID do app (ligou),
  e de novo na seção quando já há ID (para a janela abrir no toque). `entrar()` chama
  `requestAccessToken` direto no toque; `hasGrantedAllScopes` (sem a permissão do Drive = erro claro);
  janela bloqueada ou fechada viram frases. O token fica só numa variável (nunca em armazenamento do
  navegador, nunca no console); vale até 1 min antes de vencer; "Desconectar" revoga.
- `criarSincronizador`: estado → `iniciar` (com o entrenos-sync.json mais antigo; sumiu: recria do
  `sync_json`) → baixa e `receber` só o arquivo de quem mudou (etiqueta = md5 do Drive, guardada no
  núcleo: depois de restaurar um backup, baixa de novo) → `preparar` → cria ou atualiza o próprio →
  `enviado`. Um por vez; salvar no meio agenda mais uma. `depoisDeSalvar` agrupa ~30 s
  (`AGRUPAR_MS`). `ligarComSenha`: sem configuração no Drive cria; dois aparelhos criando juntos =
  vale a mais antiga (`substituir: true`) e a nossa é apagada.
- Ganchos: `entrenos:gravou` (o `app.js` dispara depois de um POST/PATCH/PUT/DELETE que muda o
  diário; leitura, IA, PIN e a própria sincronização ficam de fora) → agrupa, se a sincronização
  automática está ligada; `entrenos:desbloqueado` (o `mostrarApp`) → sincroniza se o token ainda vale;
  `entrenos:bloqueado` cancela o que estava marcado; `online` e a aba voltar a aparecer → a fila.
- A seção: introdução e "Como funciona", serviço (Google Drive; Dropbox e OneDrive "em breve",
  desabilitados), ID do app (com o passo a passo e as origens desta página e `http://localhost:8787`,
  com "Copiar"), situação ("Conectado como…", "Última sincronização há N min", o erro em frase, o que
  chegou), Conectar / Sincronizar agora (que já abre o login quando o token venceu) / Desconectar,
  criar ou digitar a senha (campos de senha como os do PIN: o navegador não oferece salvar), as
  perguntas (os dois lados com nomes de campo e valores legíveis; "Fica a deste aparelho" / "Fica a do
  <aparelho>"), sincronizar sozinho, aparelhos, nome deste aparelho, trocar o ID, "Parar de sincronizar
  neste aparelho" (confirmação). Em 127.0.0.1 avisa que o Google só aceita `localhost` (com o link);
  no iPhone, o aviso do login por janela e o caminho manual.

**CSP** (a mesma no PC e no `_headers`): `script-src 'self' 'wasm-unsafe-eval'
https://accounts.google.com/gsi/client; style-src 'self' https://accounts.google.com/gsi/style;
frame-src https://accounts.google.com/gsi/; connect-src 'self' https://accounts.google.com/gsi/
https://www.googleapis.com/drive/v3/ https://www.googleapis.com/upload/drive/v3/` (o resto como em
21.6). `Cross-Origin-Opener-Policy: same-origin-allow-popups` também no PC (a janela do login avisa a
página que a abriu).

**Testado (07/10/2026):** a suíte inteira no CPython (com `test_sincronizacao.py`: três aparelhos —
registros novos de cada lado sem duplicar, só o que mudou é baixado, apagar num e sumir nos outros
sem voltar mesmo com um aparelho atrasado, escrito de novo depois de apagado, item e pessoa apagados,
edição concorrente (vale a mais recente), quase junto vira pergunta (respondida "outro" e "este"),
apagar tira a pergunta, pessoa renomeada sem duplicar, pessoa completada sem pergunta, item
acrescentado, 🔒 que vai e volta e na dúvida fica, ciclo e área Eu preservados, a chave fora de
config/exportação/histórico/arquivo, desligar tira a chave das cópias, apagado pela sincronização sai
das cópias, senha errada e fraca, bloqueio da senha, senha trocada em outro lugar, arquivo adulterado
(byte trocado, cortado, cabeçalho, lixo, outra senha, nome trocado), versão de formato (cabeçalho,
JSON, esquema e configuração), arquivo grande demais, restaurar = aparelho novo, o mesmo diário nos
dois antes de ligar, impressão igual depois de importar, anotação organizada que não volta, apagar
todos os dados do ciclo, promessa apagada, lugar corrigido sem soltar menções, anotação nova ligada
ao ciclo, o que já veio e foi apagado aqui não volta, dados dela, a mesma versão guardada de outro
jeito sem pergunta); 18 proteções desligadas uma a uma fazem algum desses testes falhar. `test_compat_navegador.py`: a sincronização no "Python de
navegador" (sem threads, `_hashlib` nem `ssl`). `teste_sincronizar.mjs` (19 passos, ~9 s): o motor
contra o Drive de mentira (só o próprio arquivo, só o que mudou, 401, sem rede e a volta, 30 s,
erros do Google, corrida da configuração, a configuração que sumiu, GIS de mentira), três aparelhos
com o núcleo no Pyodide 314 (senha errada 401; registros, 🔒 e ciclo em todos; apagar sem voltar; no
Drive só o `entrenos-sync.json` e três arquivos embaralhados; cada um atualizou só o seu) e a tela num
DOM de mentira (do ID do app até ligada e uma pergunta respondida; todo campo com rótulo, nada de
"undefined", o token nunca na tela; sem `innerHTML`, estilo inline, `eval` nem armazenamento do
token). Medido (CPython, este PC, 600 registros e 1.350 itens): preparar 0,2-0,3 s (arquivo de 70 kB),
receber tudo de outro aparelho 0,5 s, receber sem nada novo 0,6 s; no Pyodide, uma sincronização de
um diário pequeno leva 45-90 ms. Nada falou com o Google de verdade; nada abriu navegador.

**Fica para depois (fase 2):**
- apagar o que foi apagado ANTES de ligar a sincronização (não há lápide: volta de outro aparelho que
  ainda tinha);
- edição de linhas dos recursos (status de uma promessa, opinião de um lugar...): hoje a versão nova
  entra só onde a de antes não ficou (a de antes sai por lápide quando a impressão acha a cópia);
  relações (confirmar/descartar), avisos dispensados e os outros ajustes não sincronizam;
- carimbo de pessoa e do 🔒 é a hora da conferência (não a da edição): com um aparelho dias sem
  sincronizar, a mudança dele pode valer como mais recente que uma de outro feita depois;
- trocar a senha de sincronização e "esqueci a senha" (hoje: parar em todos e ligar de novo, com o
  `entrenos-sync.json` apagado pelo Drive → Gerenciar apps); tirar da lista um aparelho que não
  sincroniza mais;
- receber de cada aparelho passa pela importação de todos os registros dele (0,6 s com 600 no PC): com
  diários de anos, mandar só o que mudou;
- conferir num navegador de verdade (ver a lista do 21.6): o login do Google no Chrome/Edge (PC por
  `localhost`, Android) e no Safari do iPhone, dentro e fora do app instalado; a CSP com o GIS (o
  console sem erro); `Referrer-Policy: no-referrer` com o GIS; o Drive de verdade (multipart, md5,
  `about` com o escopo `drive.appdata`).

### 22.2 Correção final (08/10/2026)

Achados das revisões de segurança, do Pyodide de verdade e das telas, cada um reproduzido antes de
corrigir (testes em `test_sincronizacao.py`, classes do fim do arquivo, e em `teste_sincronizar.mjs`).
Vale junto com 22 e 22.1; onde discordarem, vale esta. As decisões do usuário continuam: senha de
sincronização própria (pode ser igual ao PIN), Google Drive, Cloudflare Pages, avisos só com datas.

**Senha de sincronização mais forte (alto).** Antes valiam as regras do PIN (6 caracteres): "flores",
"camila", "beijos" passavam, e quem lê a pasta do app no Drive (o Google, ou quem tomar a conta) testa
senhas fora do app, sem limite, só com o `entrenos-sync.json` (0,21 s por palpite num núcleo; "flores"
saiu em 1 s numa lista de 6). Agora `problema_da_senha` tem regra própria: pelo menos 14 caracteres e
3 palavras diferentes de 2+ letras (ou, sem frase, 20 caracteres de 3 tipos e 12 diferentes); recusa
palavra ou nome único (mesmo com números e símbolos), repetições, sequências, as senhas comuns do PIN e
frases feitas (`_FRASES_COMUNS`: "eu te amo muito", "te amo para sempre"...). A tela sugere uma frase
gerada (`gerarFrase`: 4 palavras diferentes de uma lista de 568, sem acento, + 2 algarismos, umas
9×10^12 combinações; "Usar esta frase", "Outra", "Copiar") e confere a mesma regra antes de enviar.
**Igual ao PIN**: ao criar, o núcleo compara com o PIN do diário (1 ou 2 scrypt, contando nas mesmas
comparações do "PIN da IA igual ao principal"; com o PIN em bloqueio, não compara) e responde 409
`{igual_ao_pin}` (`SENHA_IGUAL_AO_PIN`); a tela pergunta "Usar mesmo assim?" e reenvia com
`mesmo_que_o_pin: true`. O scrypt continua em 2^16: 2^17 dobraria a memória no wasm do celular (128 MB por
tentativa) e o Argon2id do cryptography não foi testado no Pyodide (fica para depois); a regra da senha é a defesa.

**Nenhum código do Google na página (médio).** A biblioteca GIS rodava no próprio documento, com o
diário aberto, alcançando o DOM, o token da sessão e `window.EntreNos.api` (que exporta tudo
decifrado). Agora o login é o fluxo de token para apps web, sem biblioteca: `criarConta().entrar()` abre
(no toque) `accounts.google.com/o/oauth2/v2/auth?response_type=token&scope=…drive.appdata&
include_granted_scopes=false&state=<32 hex aleatórios>&redirect_uri=<origem>/oauth.html[&login_hint]`.
O `oauth.html` (estático, sem o app, só o `oauth.js`) lê o fragmento, tira-o do histórico, entrega
`{tipo: 'entrenos-oauth', state, access_token, expires_in, scope, error}` por `postMessage` só para a
mesma origem e por `BroadcastChannel('entrenos-oauth')` (a janela que passou pelo Google costuma perder
a ligação com a página, por causa do COOP) e fecha. A página só aceita mensagem da mesma origem e com o
`state` deste pedido; escopo além de `drive.appdata` (o Drive inteiro, já concedido antes ao mesmo ID)
é recusado e o token revogado (`permissao_demais`). Sem resposta em 5 min, "a janela foi fechada"; há
"Cancelar". O token fica só na memória, é **esquecido quando o diário se bloqueia** (`entrenos:bloqueado`)
e **revogado** em "Desconectar" e "Parar" (`POST https://oauth2.googleapis.com/revoke`, `no-cors`, sem
credenciais). CSP: sem `accounts.google.com` em script/style/frame; `connect-src` só com a Drive API e
a revogação. O passo a passo pede os **URIs de redirecionamento** (`<origem>/oauth.html`). No Cloudflare,
`/oauth.html` é redirecionado para `/oauth`, e o navegador mantém o fragmento.

**Apagar é apagar no Drive (médio).**
- "Parar" não apaga mais a memória da sincronização (uids, lápides, o que veio de cada aparelho: só
  impressões): fica com `anterior: {aparelho, verificador, sequencia}` no ajuste "sync", sem a chave.
  Religar com a **mesma senha** (o mesmo verificador) volta a mesma identidade (o mesmo arquivo no Drive,
  reescrito) e o que foi apagado com ela parada vira lápide na primeira conferência (antes voltava dos
  outros aparelhos e o arquivo antigo ficava no Drive para sempre). Com **outra senha**: identidade e
  memória novas, e o arquivo antigo deste aparelho entra em `apagar` (o estado mostra; a página apaga do
  Drive e avisa em `POST /api/sync/enviado {apagados}`). Restaurar uma cópia "parada" não reaproveita a
  identidade de onde ela veio. As perguntas pendentes saem ao parar.
- "Parar" com o Google conectado oferece (marcado) "Apagar também o arquivo deste aparelho no Google
  Drive" (`apagarProprio`, só o próprio arquivo).
- **Versões antigas**: cada arquivo leva `sequencia` (cresce a cada `preparar`, guardada no "sync") e
  `vistos` ({aparelho: maior número visto}); quem já juntou um arquivo mais novo de um aparelho (ou viu,
  pelo arquivo de outro, que ele estava mais adiante) recusa uma versão mais velha posta no lugar: 400
  `arquivo_invalido` com `ARQUIVO_ANTIGO`. Limite: um aparelho novo, que nunca viu nada, não percebe se
  TODOS os arquivos forem trocados pelas versões antigas ao mesmo tempo.
- **Revisões**: depois de atualizar o próprio arquivo, a página apaga as revisões anteriores dele
  (`drive.limparRevisoes`: `GET files/{id}/revisions` e `DELETE …/revisions/{id}`, menos a última, que o
  Drive não deixa). É melhor esforço (um erro não atrapalha a sincronização) e falta conferir no Drive
  de verdade se o escopo `drive.appdata` permite e se o Drive guarda revisões desses arquivos.

**Respostas das perguntas (alto).** A resposta ganhava `base + janela` truncado ao segundo, o mesmo
carimbo em todos os aparelhos que respondiam dentro da janela; "mesmo carimbo = mesma versão", e cada um
ficava com a sua para sempre, sem pergunta. Agora `_carimbo_da_resposta` é único: o instante de agora,
se já passou da janela; senão, o fim da janela + 1 s com os microssegundos de agora. Respostas que
concordam ("este" num e "outro" no outro) convergem; respostas contrárias dadas quase juntas voltam
como pergunta (nunca divergem caladas). Testes com dois e três aparelhos. Não foi feito o desempate
determinístico para carimbos iguais com conteúdo diferente: ele refaria a cada sincronização a versão
de um aparelho que guarda algum campo de outro jeito (o caso que o "mesmo carimbo" protege); com o
carimbo único, carimbo igual só vem de cópia da mesma versão.

**O arquivo (baixo).** `abrir` confere a tag do GCM antes de olhar o byte do formato (que é dado
associado): só trocar esse byte dá "não abre" (400), nunca "atualize este aparelho"; um formato novo de
verdade (cifrado certo) continua 409 `versao_nova`. Num aparelho já ligado, um `entrenos-sync.json`
diferente (até com `"formato": 2`) em `/sync/iniciar` é 409 `senha_mudou`. O conteúdo é preenchido com
zeros depois do fim do fluxo zlib até um degrau (`tamanho_preenchido`: múltiplos de 64 kB até 1 MB, de
256 kB até 8 MB, de 1 MB acima): 0, 1 e 2 registros dão o mesmo tamanho. Arquivos antigos sem os zeros
continuam abrindo.

**Pergunta de registro do ciclo (baixo).** `conflitos()` marca `discreto` quando o item é de um registro
discreto (`recursos.discretos`): `resumo` vazio e os campos de texto (resumo, trecho, alvo, pessoa, como
a data foi dita, categorias) com `reservado: true` e sem valor; a tela mostra "🔒 Uma informação
reservada… abra o registro para ver" com o link.

**Rotas automáticas não contam como uso (baixo).** `Rota.conta_uso`: estado, iniciar, receber, preparar,
enviado e conflitos (GET) não adiam o bloqueio por inatividade (como a agenda de avisos); as novas
tentativas sem rede deixavam o diário aberto por horas.

**A cópia que sai do aparelho não leva a chave (baixo).** `_conteudo_para_fora`: "Baixar uma cópia" e o
backup numa pasta (PC) levam o ajuste "sync" de `para_fora` (sem a chave, sem identidade e conta; com o
verificador em `anterior`), num banco copiado e cifrado com a mesma chave do diário (abre com o mesmo
PIN; `cofre.cifrar_dados`). Restaurada, a cópia pede a senha de sincronização de novo (`religavel`).
As cópias automáticas (backups/) seguem como em 22.1.

**Tamanho antes de baixar (baixo).** `/sync/estado` traz `limite_arquivo` (o maior .sync que o núcleo
aceita em base64: 3/4 do limite do corpo, menos uma folga); a página nem baixa um maior. Arquivo
recusado (grande, que não abre, antigo) fica lembrado pela etiqueta e não é baixado de novo enquanto
não mudar; o aviso é a frase do núcleo.

**A tela.**
- **O selo ☁️ fora de Ajustes (alto):** fixo no alto da tela, com o diário aberto e a sincronização
  ligada, quando não há acesso ao Google ("Não sincronizado desde 14:20 · Sincronizar", "Salvo só neste
  aparelho · Sincronizar": um toque abre a janela do Google e sincroniza), quando há perguntas ("1
  pergunta da sincronização: qual versão fica?", leva a Ajustes → ☁️), erro ou fila. Depois de salvar sem
  acesso, um aviso uma vez: "Salvo neste aparelho. Para mandar aos outros, toque em ☁️ Sincronizar". O
  texto de "sincronizar sozinho" diz "enquanto o acesso ao Google valer (cerca de 1 hora)".
- Conectar: "Conectando ao Google…" com "Cancelar", sem `await` antes da janela; o mesmo nome no
  status e no botão ("Sincronizar agora"); o status e os botões se redesenham a cada minuto.
- Criar a senha: "Não encontrei a sincronização no Google Drive de <conta>… confira se entrou com a
  mesma conta", a caixinha "Este é o primeiro aparelho", a frase sugerida, "Mostrar o que estou
  digitando", o nome deste aparelho já preenchido (iPhone, Celular Android...), a regra na dica e a
  espera do 401/429 contada no botão. Digitar: "Esqueci a senha de sincronização" com o caminho de hoje.
- Perguntas: "Fica a versão deste aparelho" / "Fica a versão de: <nome>", com `aria-describedby` no
  título da pergunta. Depois de conectar, ligar, responder, desconectar ou parar, o foco vai para o
  status (`#sync-status`).
- Passo a passo: aberto quando não há ID; origens = o site (só se a página for https) e
  `http://localhost:<porta>` (nunca 127.0.0.1); os URIs de redirecionamento; o vencimento de 7 dias do
  app "em teste" (e publicar); "Copiar o ID" para levar ao celular.
- Depois de "Parar", o formulário da senha fica recolhido ("Quer ligar de novo?").

**Rotas** (mudanças sobre a tabela de 22.1): `GET /sync/estado` ganha `limite_arquivo`, `apagar` e
`religavel`; `POST /sync/senha` aceita `mesmo_que_o_pin` e pode responder 409 `{igual_ao_pin}`; `POST
/sync/iniciar` responde `senha_mudou` também para um entrenos-sync.json de "formato novo"; `POST
/sync/enviado` aceita `apagados`; `GET /sync/conflitos` traz `discreto` e, nos campos, `reservado`;
`POST /sync/desligar` guarda `anterior` e mantém as tabelas sync_* (menos as perguntas).

**Testado (08/10/2026).** `test_sincronizacao.py` (classes `TestRespostasDasPerguntas`, `TestSenhaForte`,
`TestApagarEApagarNoDrive`, `TestArquivoNoDrive`, `TestMemoriaDoBanco`, e os testes de 22.1 ajustados:
desligar guarda a memória, a cópia baixada pede a senha, o byte do formato sem a chave dá 400, a
senha igual ao PIN pede confirmação); `teste_sincronizar.mjs` (22 passos: o login pela janela de
mentira com o `state`, escopo a mais, bloqueada, cancelada e sem resposta, esquecer e revogar; o
`oauth.js` rodando num contexto isolado; revisões apagadas; arquivo antigo deste aparelho apagado;
arquivo grande nem baixado; recusado não baixado de novo; a frase sugerida; a tela até ligar, perguntar
e parar apagando o arquivo e revogando; três aparelhos no Pyodide de verdade). Nada falou com o Google
de verdade; nada abriu navegador.

**Fica para depois / a conferir num navegador de verdade:**
- o login pela janela no Chrome/Edge (PC por `localhost`, Android) e no Safari do iPhone, dentro e fora
  do app instalado: se a volta chega pelo `postMessage` ou pelo `BroadcastChannel`, se a janela fecha
  sozinha, e o redirecionamento `/oauth.html` → `/oauth` do Cloudflare com o fragmento;
- a revogação (`oauth2.googleapis.com/revoke` em `no-cors`), as revisões (`revisions.list/delete` com o
  escopo `drive.appdata`) e o `about` no Drive de verdade;
- o ID do app fixo no build do site (para não colar em cada aparelho); o progresso real do download
  do Python; o `.bat` do PC abrir direto por `http://localhost:8787`;
- os itens de 22.1 que continuam valendo (trocar a senha, edição de linhas dos recursos, mandar só o
  que mudou).
