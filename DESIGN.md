# Entre Nós: projeto técnico (Fase 1)

Diário inteligente do relacionamento. Roda **só neste computador**, abre no navegador
(`http://127.0.0.1:8787`), guarda tudo **criptografado** e funciona **sem IA** (motor de
regras). Uma IA pode ser ligada depois sem mudar o resto (motor plugável).

Este documento é o contrato entre os módulos. `entrenos/modelos.py`, `entrenos/texto.py` e
`entrenos/config.py` já estão prontos e **não devem ser alterados** sem atualizar este arquivo
(`texto.semelhanca` mudou em 07/10/2026: seção 19.7).

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
  web/index.html app.js estilo.css    interface (SPA sem framework)
  testes/test_*.py
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
sozinho; editar a pessoa zera).

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
