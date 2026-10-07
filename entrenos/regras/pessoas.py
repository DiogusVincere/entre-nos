"""Pessoas citadas no texto: parentescos, amigos, gente do trabalho e nomes próprios.

A namorada é implícita ("ela") e nunca entra na lista. "Minha mãe" é do usuário e sai
com a relação "mãe (sua)".
"""
from __future__ import annotations

import re
import unicodedata

from ..modelos import PessoaEncontrada
from ..texto import frases, normalizar, palavras, semelhanca
from .categorias import LEXICO, RAIZ_MINIMA


def plano(s: str) -> str:
    """Minúsculas e sem acento com o MESMO tamanho do original (as posições valem nos dois)."""
    return "".join(unicodedata.normalize("NFD", c)[:1].lower()[:1] or c for c in s)


# exibição -> (gênero "f" | "m" | "c", grupo). "c" = comum de dois ("a chefe", "o chefe").
_TABELA: dict[str, tuple[str, str]] = {}


def _registrar(grupo: str, lista: str) -> None:
    for par in lista.split():
        nome, genero = par.split(":")
        _TABELA[nome.replace("_", " ")] = (genero, grupo)


_registrar("familia", "mãe:f pai:m pais:m irmã:f irmão:m avó:f avô:m tia:f tio:m prima:f primo:m "
           "sobrinha:f sobrinho:m madrinha:f padrinho:m cunhada:f cunhado:m sogra:f sogro:m "
           "filha:f filho:m madrasta:f padrasto:m enteada:f enteado:m afilhada:f afilhado:m "
           "neta:f neto:m")
_registrar("importante", "melhor_amiga:f melhor_amigo:m")
_registrar("amigos", "amiga:f amigo:m vizinha:f vizinho:m")
_registrar("trabalho", "chefe:c gerente:c supervisora:f supervisor:m coordenadora:f coordenador:m "
           "diretora:f diretor:m sócia:f sócio:m colega_de_trabalho:c colega:c")
_registrar("estudos", "professora:f professor:m orientadora:f orientador:m")
_registrar("saude", "terapeuta:c psicóloga:f psicólogo:m psiquiatra:c médica:f médico:m "
           "dentista:c nutricionista:c")
_registrar("ex", "ex:c ex-namorado:m ex-namorada:f ex-marido:m")
_registrar("pet", "pet:c cachorro:m cachorra:f cadela:f gato:m gata:f")

_VARIANTES = {
    "mamãe": "mãe", "papai": "pai", "vó": "avó", "vovó": "avó", "vô": "avô", "vovô": "avô",
    "cachorrinho": "cachorro", "cachorrinha": "cachorra", "gatinho": "gato", "gatinha": "gata",
    "ex namorado": "ex-namorado", "ex namorada": "ex-namorada", "ex marido": "ex-marido",
}
# "avó" e "avô" viram "avo" sem acento; o acento do texto original decide.
_CIRCUNFLEXO = {"avô", "vô", "vovô"}

RELACOES: dict[str, str] = {}
for _exib in [*_TABELA, *_VARIANTES]:
    RELACOES.setdefault(normalizar(_exib), _VARIANTES.get(_exib, _exib))

# Relações que contam como pessoa dela mesmo sem "dela" ("a mãe comentou").
_FORTES = {k for k, (_, g) in _TABELA.items() if g in ("familia", "importante", "ex")} | {
    "chefe", "gerente", "supervisora", "supervisor", "coordenadora", "coordenador",
    "diretora", "diretor", "sócia", "sócio",
}

_ARTIGOS = {"a", "o", "as", "os", "um", "uma"}
_CONTRACOES = {"da", "do", "das", "dos", "na", "no", "nas", "nos", "pela", "pelo", "ao", "aos",
               "com", "pra", "pro", "para"}
_POSSE_USUARIO = {"minha", "minhas", "meu", "meus"}
_POSSE_CASAL = {"nossa", "nossas", "nosso", "nossos"}
_POSSE_DELA = {"sua", "suas", "seu", "seus"}

_RE_RELACAO = re.compile(
    r"(?<![\w-])(?:"
    + "|".join(r"\s+".join(map(re.escape, k.split())) for k in sorted(RELACOES, key=len, reverse=True))
    + r")(?![\w-])"
)
_RE_DONO_NOME = re.compile(r"\s+d[aoe]\s+([A-ZÀ-Þ][a-zß-ÿ]+(?:\s+[A-ZÀ-Þ][a-zß-ÿ]+)*)")
_RE_TOKEN_NOME = re.compile(r"(?<![\wÀ-ÿ'-])[A-ZÀ-Þ][a-zß-ÿ]+(?:['-][A-Za-zÀ-ÿ]+)*")
# Entre o nome e a relação que vem depois: "Mariana, melhor amiga dela" / "Mariana (amiga)" /
# "Mariana é a melhor amiga dela".
_RE_NOME_ANTES_REL = re.compile(r"\s*,\s*(?:que\s+[eé]\s+)?|\s*\(\s*|\s+[eé]\s+")
# Entre a relação e o nome que vem depois: "a mãe dela, Sandra" / "o pai dela, seu Roberto" /
# "o chefe dela, o Marcos" / "a orientadora, a Dra. Helena". "seu"/"dona" aqui é tratamento.
_RE_REL_ANTES_NOME = re.compile(
    r"\s*,?\s*(?:(?:o|a|seu|sua|dona|dom|sr|sra|srta|dr|dra|prof|profa)\.?\s+){0,2}", re.IGNORECASE)

# Palavras com maiúscula que não são nome de gente.
_NAO_NOMES = frozenset(normalizar(w) for w in """
janeiro fevereiro março abril maio junho julho agosto setembro outubro novembro dezembro
segunda terça quarta quinta sexta sábado domingo segunda-feira terça-feira quarta-feira
quinta-feira sexta-feira feriado
eu ela ele elas eles nós você vocês a o as os um uma e é mas porque pois quando se então
hoje ontem amanhã anteontem agora depois antes talvez acho ah oi olá nossa meu minha
tipo gente sim não ok obrigado obrigada tchau bom boa enfim aí lá cá
deus jesus senhor senhora dona seu dr dra sr sra srta prof profa
natal páscoa carnaval réveillon ano novo dia mês semana
brasil são santo santa rio europa eua portugal paris londres itália japão china frança
espanha alemanha argentina chile méxico disney orlando bahia minas santos campinas curitiba
floripa florianópolis recife salvador fortaleza manaus belém goiânia brasília sp rj bh
ubatuba guarujá gramado búzios paraty jundiaí sorocaba valinhos vinhedo
netflix spotify instagram insta whatsapp whats zap google iphone android uber ifood amazon
youtube tiktok facebook face twitter shopee starbucks outback mcdonalds mcdonald's coca nike
adidas zara renner shein apple samsung globo bbb enem unicamp usp puc unesp ufrj pucc
linkedin gmail excel word teams zoom meet pix nubank itaú bradesco santander caixa
correios sus unimed prime hbo
português inglês espanhol francês matemática história biologia química física direito
medicina engenharia psicologia administração jornalismo arquitetura odontologia enfermagem
grécia irlanda inglaterra escócia canadá austrália suíça holanda bélgica áustria rússia índia egito
marrocos turquia peru colômbia uruguai paraguai bolívia venezuela cuba noruega suécia dinamarca
finlândia islândia croácia tailândia coreia vietnã israel dubai maldivas bali havaí cancún miami
roma veneza florença milão barcelona madri lisboa amsterdã berlim praga viena budapeste atenas
santorini tóquio pequim seul sydney toronto montreal vancouver chicago boston califórnia flórida
maranhão piauí ceará paraíba pernambuco alagoas sergipe tocantins goiás pará amazonas acre rondônia
roraima amapá paraná piracicaba ribeirão indaiatuba limeira hortolândia sumaré paulínia atibaia
itatiba niterói petrópolis ilhabela maresias jericoacoara noronha maceió aracaju teresina macapá
cuiabá londrina maringá blumenau joinville canela bonito pantanal
natura ambev boticário magalu petrobras embraer americanas riachuelo havaianas rappi microsoft
accenture deloitte ibm oracle totvs picpay carrefour assaí atacadão walmart ikea decathlon centauro
netshoes dafiti hering arezzo havan fnac saraiva vivo
coldplay beyoncé anitta neymar messi friends mindhunter spotify
""".split())
# Lugares e nomes compostos que parecem nome de gente.
_RE_COMPOSTOS = re.compile(r"\b(?:" + "|".join(map(re.escape, """
sao paulo|rio de janeiro|belo horizonte|porto alegre|santa catarina|rio grande do sul|mato grosso
|estados unidos|nova york|nova iorque|mercado livre|nossa senhora|ano novo|dia dos namorados
|dia das maes|dia dos pais|black friday|copa do mundo|sao joao|espirito santo|minas gerais
|rio grande|sao jose|santo andre|sao bernardo|ribeirao preto|joao pessoa|campo grande
|foz do iguacu|porto seguro|buenos aires|las vegas|los angeles|reino unido|costa rica
|burger king|rock in rio|prime video|disney plus|nova zelandia|coco bambu|pao de acucar|casas bahia
|ponto frio|mercado pago|taylor swift|harry potter|chico buarque|colleen hoover|marilia mendonca
|ivete sangalo|caetano veloso|gilberto gil|stranger things|game of thrones|la casa de papel|star wars
|the office|grey s anatomy|lady gaga|bruno mars|ed sheeran|billie eilish|dua lipa|ariana grande
|justin bieber|selena gomez|luan santana|jorge e mateus|henrique e juliano|ze neto|gusttavo lima
|o boticario|nova york|reino unido|belo monte|porto de galinhas|foz do iguacu|cabo frio
|angra dos reis|campos do jordao|sao luis|boa vista|joao pessoa""".replace("\n", "").split("|")))
                           + r")\b")
# Primeira palavra que faz de um nome composto um lugar ou uma coisa ("Shopping Iguatemi",
# "Parque Ibirapuera", "Projeto Mandragorix"), e não uma pessoa.
_PALAVRAS_LUGAR = frozenset(normalizar(w) for w in """
shopping parque praia hospital clínica restaurante bar rua avenida av escola faculdade universidade
colégio igreja hotel museu teatro cinema academia mercado supermercado padaria loja projeto empresa
banco instituto clube estádio arena aeroporto lago serra ilha bairro condomínio edifício prédio praça
pousada sítio fazenda chácara café cafeteria pizzaria lanchonete hamburgueria festival programa curso
time rodoviária estação metrô livraria biblioteca capela catedral santuário zoológico aquário boate
balada feira hospitalar laboratório farmácia posto cidade estado rio lagoa morro pico monte vale
""".split())
# Antes de um nome, o que faz dele obra ou artista ("fã da Anitta", "show do Coldplay",
# "assistiu Friends", "ama Coldplay"). Com artigo ("ama a Mariana") continua sendo gente.
_RE_ANTES_DE_OBRA = re.compile(
    r"(?:\bfa\s+(?:de|d[oa]s?)|\bshows?\s+(?:de|d[oa]s?)|\bmusicas?\s+(?:de|d[oa]s?)|\blivros?\s+(?:de|d[oa]s?)"
    r"|\bfilmes?\s+(?:de|d[oa]s?)|\bseries?|\bbanda|\bcantora?|\bautora?|\balbum\s+(?:de|d[oa]s?)"
    r"|\bdisco\s+(?:de|d[oa]s?)|\bassisti(?:u|ndo|r|mos)?|\bleu|\blendo|\bler|\bouvi(?:ndo|u|r)|\bescuta(?:ndo|r|va)?"
    r"|\bmaratona(?:ndo|r|u)?|\bcurte|\bama|\badora|\bodeia|\bdetesta|\bgosta\s+(?:muito\s+)?de|\bcurtiu|\bamou"
    r"|\badorou)\s+$")
# Antes de um nome, o que faz dele lugar ("mora no Maranhão", "voltou de Ribeirão", "é de Piracicaba").
# "no/na" só depois de verbo de lugar: "pensou na Mariana" continua sendo gente.
_RE_ANTES_DE_LUGAR = re.compile(
    r"(?:^|\W)(?:em|para|pra|ate|rumo\s+a"
    r"|(?:mor\w*|trabalh\w*|estud\w*|estagi\w*|entrevista|emprego|vaga|nasceu|cresceu|passe\w*|viaj\w*"
    r"|esteve|ficou|fica|foi|vai|vou|fomos|chegou|chega)\s+(?:no|na|nos|nas|num|numa)"
    r"|(?:voltou|veio|vem|volta|chegou|chega|saiu|sai|mudou|voltando|vindo|chegando)\s+d[eoa]s?)\s+$")
# Palavras comuns que, no começo da frase e seguidas de verbo, não são nome ("Tempo tá feio").
_COMUNS = frozenset(normalizar(w) for w in """
tempo chuva noite tarde manhã vida coisa mundo clima sol frio calor fim início resto parte primeira
última outra outro nada tudo todo toda muito pouco cada qualquer algum alguma ninguém alguém isso isto
aquilo esse essa este aquele aquela quem qual como onde por pra para com sem sobre entre desde durante
contra ao aos no na nos nas do da dos das pelo pela seu sua nosso teu tua ainda sempre nunca também
já só mesmo quase bem mal melhor pior logo cedo pessoal galera dinheiro cabeça corpo sono fome sede
vento azul verde vermelho amarelo rosa roxo preto branco cinza laranja marrom paciência amor saudade
festa casa carro moto celular telefone computador internet ônibus trânsito roupa sapato cabelo unha
briga conversa mensagem ligação resposta notícia novidade resultado pedido conta contas aluguel quarto
sala cozinha banheiro janela porta cama sofá mesa bolsa chave dinheiro tudo situação coisa história
""".split())
# Verbo logo depois do nome no começo da frase: "Camila vai casar", "Mariana ligou".
_VERBOS_DEPOIS_DO_NOME = frozenset("""
vai vao foi foram esta ta tava estava tem tinha disse falou contou ligou mandou veio vem quer queria
gosta ama adora odeia detesta mora morava trabalha estuda casou faz fez fica ficou chegou saiu passou
pediu comprou deu perguntou chorou riu brigou terminou voltou viajou conheceu nasceu morreu acha achou
sabe soube pode precisa anda andou continua comecou arrumou conseguiu perdeu ganhou vive mandou
""".split())
_ADVERBIOS_ANTES_DO_VERBO = frozenset("nao nunca ja tambem so ainda sempre me te lhe nos a o".split())
_TERMOS_LEXICO = frozenset(t for termos in LEXICO.values() for t in termos if " " not in t)
_RAIZES_LEXICO = tuple(t for t in _TERMOS_LEXICO if len(t) >= RAIZ_MINIMA)


# ------------------------------------------------------------------ utilidades públicas

def relacao_base(relacao: str | None) -> str:
    """'mãe (sua)' -> 'mãe'; 'irmã de Mariana' -> 'irmã'; 'chefe dele' -> 'chefe'."""
    if not relacao:
        return ""
    r = re.sub(r"\s*\(.*\)$", "", relacao.strip())
    if normalizar(r) in RELACOES:
        return "avô" if r.lower() in _CIRCUNFLEXO else RELACOES[normalizar(r)]
    return re.sub(r"\s+(?:dele|deles|delas|de\s+.+)$", "", r)


def grupo_relacao(relacao: str | None) -> str | None:
    """familia | importante | amigos | trabalho | estudos | saude | ex | pet | None."""
    exib = RELACOES.get(normalizar(relacao_base(relacao)))
    return _TABELA[exib][1] if exib in _TABELA else None


def genero_relacao(relacao: str | None) -> str | None:
    """'f', 'm', 'c' (comum de dois) ou None quando não é uma relação conhecida."""
    base = relacao_base(relacao)
    if base in _TABELA:
        return _TABELA[base][0]
    exib = RELACOES.get(normalizar(base))
    return _TABELA[exib][0] if exib in _TABELA else None


def e_dela(relacao: str | None) -> bool:
    """True quando a relação é da namorada (sem "(sua)", "dele", "de Fulana"...)."""
    if not relacao:
        return True
    return normalizar(relacao) in RELACOES


# ------------------------------------------------------------------ casamento com o cadastro

def _apelido_de(palavra: str, nome: str) -> bool:
    """'ju', 'jú' e 'julinha' são apelidos de 'julia'; 'mari' de 'mariana'."""
    if palavra == nome:
        return True
    # Só apelido curto: "Maria" não é "Mariana", "Gabriel" não é "Gabriela".
    if 2 <= len(palavra) <= 4 and len(nome) >= len(palavra) + 2 and nome.startswith(palavra):
        return True
    raiz = re.sub(r"(?:z?inh[ao]|ito|ita|ucha|uxa)$", "", palavra)
    return raiz != palavra and len(raiz) >= 2 and nome.startswith(raiz)


def _mesmo_nome(texto_nome: str, nome_dela: str) -> bool:
    """O nome no texto é ela? Nome inteiro, parte do nome, apelido ("Ju") ou diminutivo ("Julinha")."""
    a, b = normalizar(texto_nome).split(), normalizar(nome_dela).split()
    return bool(a) and bool(b) and all(any(_apelido_de(x, y) for y in b) for x in a)


def e_nome_dela(texto: str | None, nome_dela: str | None) -> bool:
    """Para a busca e o banco: o texto ("a Ju", "Julinha") se refere a ela?"""
    if not texto or not nome_dela:
        return False
    t = re.sub(r"^(?:a|o)\s+", "", normalizar(texto))
    return bool(t) and _mesmo_nome(t, nome_dela)


def _nome_exato(nome: str, conhecidas: list[dict]) -> int | None:
    alvo = normalizar(nome)
    if not alvo:
        return None
    for c in conhecidas:
        nomes = [c.get("nome") or ""] + list(c.get("apelidos") or [])
        if any(alvo == normalizar(n) for n in nomes if n):
            return c.get("id")
    return None


def _nome_parecido(nome: str, conhecidas: list[dict]) -> int | None:
    melhor, ids = 0.0, set()
    for c in conhecidas:
        for n in [c.get("nome")] + list(c.get("apelidos") or []):
            if not n:
                continue
            s = semelhanca(nome, n)
            if s > melhor:
                melhor, ids = s, {c.get("id")}
            elif s == melhor:
                ids.add(c.get("id"))
    return next(iter(ids)) if melhor >= 90 and len(ids) == 1 else None


def mesma_relacao(a: str | None, b: str | None) -> bool:
    """Compara relações sem acento, mas 'avó' e 'avô' continuam duas (sem acento, vale qualquer uma)."""
    na, nb = normalizar(a or ""), normalizar(b or "")
    if not na or na != nb:
        return False
    acento = [("ô" in x.lower(), "ó" in x.lower()) for x in (a or "", b or "")]
    if na in ("avo", "vo", "vovo") and all(any(x) for x in acento):
        return acento[0] == acento[1]
    return True


def _por_relacao(relacao: str, conhecidas: list[dict]) -> int | None:
    candidatas = [c for c in conhecidas if relacao and mesma_relacao(relacao, c.get("relacao"))]
    return candidatas[0].get("id") if len(candidatas) == 1 else None


def casar_pessoa(nome_ou_relacao: str, conhecidas: list[dict]) -> int | None:
    """Id da pessoa cadastrada por nome, apelido, relação única ou nome parecido (>= 90)."""
    if not nome_ou_relacao or not conhecidas:
        return None
    t = re.sub(r"^(?:a|o|as|os)\s+", "", nome_ou_relacao.strip(), flags=re.IGNORECASE)
    t = re.sub(r"\s+dela$", "", t, flags=re.IGNORECASE)
    for tentativa in (_nome_exato, _por_relacao, _nome_parecido):
        pid = tentativa(t, conhecidas)
        if pid is not None:
            return pid
    return None


# ------------------------------------------------------------------ extração

def _relacoes(texto: str, p: str, nome_dela: str | None) -> list[dict]:
    achadas = []
    for m in _RE_RELACAO.finditer(p):
        ini, fim = m.start(), m.end()
        if re.search(r"\bdia\s+d[oa]s?\s+$", p[max(0, ini - 12):ini]):
            continue  # "dia das mães", "dia dos pais"
        original = texto[ini:fim].lower()
        exib = "avô" if original in _CIRCUNFLEXO else RELACOES[re.sub(r"\s+", " ", m.group())]
        mdet = re.search(r"(\w+)\s+$", p[max(0, ini - 20):ini])
        det = mdet.group(1) if mdet else ""
        inclui_det = det in _ARTIGOS | _POSSE_USUARIO | _POSSE_CASAL | _POSSE_DELA
        span_ini = ini - len(mdet.group(0)) if inclui_det else ini

        dono, span_fim, dono_ini = None, fim, None
        mo = re.match(r"\s+(dela|dele|delas|deles)(?!\w)", p[fim:])
        if mo:
            dono, span_fim = mo.group(1), fim + mo.end()
        else:
            mn = _RE_DONO_NOME.match(texto, fim)
            if mn and normalizar(mn.group(1).split()[0]) not in _NAO_NOMES:
                dono, span_fim, dono_ini = mn.group(1), mn.end(), mn.start(1)

        precisa_nome = False
        if dono in ("dela", "delas") or (dono and nome_dela and _mesmo_nome(dono, nome_dela)):
            relacao = exib
        elif dono in ("dele", "deles"):
            relacao = f"{exib} {dono}"
        elif dono:
            relacao = f"{exib} de {dono}"
        elif det in _POSSE_USUARIO:
            relacao = f"{exib} ({'sua' if det.startswith('minha') else 'seu'})"
        elif det in _POSSE_CASAL:
            relacao = f"{exib} (de vocês)"
        elif det in _POSSE_DELA or (exib in _FORTES and det in _ARTIGOS | _CONTRACOES):
            relacao = exib
        else:
            relacao, precisa_nome = exib, True  # "uma amiga", "ao médico": só vale ligada a um nome
        achadas.append({"ini": span_ini, "fim": span_fim, "relacao": relacao,
                        "precisa_nome": precisa_nome, "dono_ini": dono_ini})
    return achadas


def _palavra_comum(n: str) -> bool:
    """Palavra do dia a dia (léxico das categorias ou lista curta): no começo da frase não é nome."""
    if n in _COMUNS or n in _TERMOS_LEXICO:
        return True
    return any(n.startswith(r) for r in _RAIZES_LEXICO)


def _nome_no_inicio(texto: str, p: str, ini: int, fim: int) -> bool:
    """Palavra com maiúscula no começo da frase que ainda assim é nome: vem seguida de verbo
    ("Camila vai casar", "Mariana ligou") e não é palavra comum ("Tempo tá feio")."""
    primeira = normalizar(texto[ini:fim].split()[0])
    if _palavra_comum(primeira):
        return False
    seguintes = re.findall(r"[a-z]+", p[fim:fim + 60])
    for w in seguintes[:3]:
        if w in _ADVERBIOS_ANTES_DO_VERBO:
            continue
        return w in _VERBOS_DEPOIS_DO_NOME or (len(w) >= 4 and w.endswith(("ou", "iu")))
    return False


def _nomes(texto: str, p: str, conhecidas: list[dict], nome_dela: str | None) -> list[dict]:
    inicios = {i for i, _, _ in frases(texto)}
    bloqueios = [(m.start(), m.end()) for m in _RE_COMPOSTOS.finditer(p)]
    todas = palavras(texto)
    titulo = len(todas) >= 4 and sum(1 for w in todas if w[:1].isupper()) / len(todas) > 0.6

    tokens: list[list] = []
    for m in _RE_TOKEN_NOME.finditer(texto):
        n = normalizar(m.group())
        if n in _NAO_NOMES or n in RELACOES or any(a <= m.start() < b for a, b in bloqueios):
            continue
        antes = texto[:m.start()].rstrip()
        inicio = m.start() in inicios or not antes or antes[-1] in "\"“”'‘:—-"
        janela = p[max(0, m.start() - 24):m.start()]
        lugar = bool(_RE_ANTES_DE_LUGAR.search(janela)) or bool(
            re.search(r"(?<![\wÀ-ÿ])[éÉ]\s+d[eoa]s?\s+$", texto[max(0, m.start() - 12):m.start()]))
        obra = bool(_RE_ANTES_DE_OBRA.search(janela))
        tokens.append([m.start(), m.end(), inicio, lugar or obra])

    grupos: list[list] = []
    for t in tokens:  # "Ana Paula" vira um nome só
        if grupos and re.fullmatch(r"[ \t]+", texto[grupos[-1][1]:t[0]]) and not t[2]:
            grupos[-1][1] = t[1]
        else:
            grupos.append(t)

    saida = []
    for ini, fim, inicio, lugar in grupos:
        nome = texto[ini:fim]
        if nome_dela and _mesmo_nome(nome, nome_dela) and (
                set(normalizar(nome).split()) <= set(normalizar(nome_dela).split())
                or _nome_exato(nome, conhecidas) is None):
            continue  # é ela (pelo nome, parte dele ou apelido que não é de outra pessoa cadastrada)
        if normalizar(nome.split()[0]) in _PALAVRAS_LUGAR:
            continue  # "Shopping Iguatemi", "Parque Ibirapuera": lugar
        conhecido = _nome_exato(nome, conhecidas) is not None or _nome_parecido(nome, conhecidas) is not None
        comeco_valido = inicio and not lugar and not titulo and _nome_no_inicio(texto, p, ini, fim)
        saida.append({"ini": ini, "fim": fim, "nome": nome,
                      "livre": conhecido or comeco_valido or not (inicio or lugar or titulo)})
    return saida


def extrair_pessoas(texto: str, conhecidas: list[dict],
                    nome_dela: str | None = None) -> list[PessoaEncontrada]:
    """Pessoas citadas (menos ela). `nome_dela` (opcional) é o nome da namorada, que nunca entra."""
    if not texto:
        return []
    conhecidas = conhecidas or []
    p = plano(texto)
    rels = _relacoes(texto, p, nome_dela)
    nomes = _nomes(texto, p, conhecidas, nome_dela)

    brutas: list[tuple[int, int, str | None, str | None]] = []
    usadas: set[int] = set()
    for n in nomes:
        par = None
        e_dono = any(r["dono_ini"] is not None and r["dono_ini"] <= n["ini"] < r["fim"] for r in rels)
        if not e_dono:
            for i, r in enumerate(rels):
                if i not in usadas and r["ini"] >= n["fim"] and _RE_NOME_ANTES_REL.fullmatch(texto[n["fim"]:r["ini"]]):
                    par = i
                    break
            if par is None:
                for i, r in enumerate(rels):
                    if i not in usadas and r["fim"] < n["ini"] and _RE_REL_ANTES_NOME.fullmatch(texto[r["fim"]:n["ini"]]):
                        par = i
                        break
        if par is not None:
            usadas.add(par)
            r = rels[par]
            ini, fim = min(n["ini"], r["ini"]), max(n["fim"], r["fim"])
            if r["ini"] >= n["fim"] and "(" in texto[n["fim"]:r["ini"]]:
                fecha = re.match(r"\s*\)", texto[fim:])
                if fecha:
                    fim += fecha.end()
            brutas.append((ini, fim, n["nome"], r["relacao"]))
        elif n["livre"]:
            brutas.append((n["ini"], n["fim"], n["nome"], None))
    for i, r in enumerate(rels):
        if i not in usadas and not r["precisa_nome"]:
            brutas.append((r["ini"], r["fim"], None, r["relacao"]))
    brutas.sort(key=lambda b: (b[0], b[1]))

    resultado: list[PessoaEncontrada] = []
    vistos: dict[tuple, PessoaEncontrada] = {}
    for ini, fim, nome, relacao in brutas:
        if nome:
            pid = _nome_exato(nome, conhecidas)
            if pid is None:
                pid = _nome_parecido(nome, conhecidas)
            if pid is None and relacao and e_dela(relacao):
                cand = _por_relacao(relacao, conhecidas)
                if cand is not None and not next(c for c in conhecidas if c.get("id") == cand).get("nome"):
                    pid = cand
        else:
            pid = _por_relacao(relacao or "", conhecidas)
        pe = PessoaEncontrada(trecho=texto[ini:fim], inicio=ini, fim=fim, nome=nome,
                              relacao=relacao, pessoa_id=pid, nova=pid is None)
        if pid is not None:
            cadastro = next(c for c in conhecidas if c.get("id") == pid)
            pe.nome = cadastro.get("nome") or nome
            pe.relacao = relacao or cadastro.get("relacao")
        if pid is not None:
            chave = ("id", pid)
        elif pe.nome:
            chave = ("n", normalizar(pe.nome))
        else:
            chave = ("r", (pe.relacao or "").lower())  # sem normalizar: avó e avô são duas
        if chave in vistos:
            if not vistos[chave].relacao and pe.relacao:
                vistos[chave].relacao = pe.relacao
            continue
        vistos[chave] = pe
        resultado.append(pe)

    # "Sandra, mãe dela, ... a mãe dela ..." é uma pessoa só.
    com_nome = {x.relacao.lower() for x in resultado if x.nome and x.relacao}
    return [x for x in resultado if x.nome or (x.relacao or "").lower() not in com_nome]


__all__ = ["RELACOES", "casar_pessoa", "e_dela", "e_nome_dela", "extrair_pessoas", "genero_relacao",
           "grupo_relacao", "mesma_relacao", "plano", "relacao_base"]
