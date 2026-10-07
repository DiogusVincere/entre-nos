"""Perguntas profundas (DESIGN.md, seção 20.1) e as perguntas "Nós dois" (seção 20.9, item 5).

O que faz:
- um banco de perguntas (aqui mesmo, `PERGUNTAS`): sete categorias para conhecer quem ela é, "Nós dois"
  (perguntas para fazer A ELA sobre a relação, que a área 🪞 Eu lê) e "Com cuidado" (leves, para dias
  difíceis);
- escolhe UMA pergunta por vez, com pontos explicáveis: o que você ainda sabe pouco, o que ela contou
  nos últimos 14 dias, variedade, começar leve; nunca repete; no máximo uma sugestão nova a cada
  `dias_entre_perguntas` (padrão 3). **Sensibilidade:** com estado negativo dela nos últimos 3 dias
  (ou um dia importante hoje/amanhã) não sugere pergunta profunda: só uma pergunta de cuidado, e só
  quando você ainda não sabe o que a ajuda; senão, nada;
- cartão "💬 Conheça melhor" no Hoje, com Vou perguntar / Perguntei → registrar resposta / Outra /
  Agora não; a tela #perguntas, por categoria, com o que já foi perguntado e as respostas;
- na revisão, acha a pergunta no texto ("Eu perguntei: “...”", ou "perguntei pra ela qual era o maior
  sonho dela e ela disse...") e propõe ligar o registro a ela; a resposta guardada é sempre um trecho
  literal do registro (nunca inventar);
- tabela `perguntas_feitas` (o contrato da seção 20.1, com o campo público `resposta`): a área Eu lê
  as respostas de "Nós dois" por `respostas_nos_dois()` ou direto pelo SQL. Registro com resposta de
  "Nós dois" fica 🔒 fora da IA (nada da área Eu vai para a IA).

Roda no navegador (Pyodide): sem thread, sem sleep, sem rede. As datas são do Python.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Iterable

from . import cartao, com_gancho, falhas, registrar_erro, rota
from ..modelos import Certeza
from ..regras.certeza import classificar_certeza
from ..regras.pessoas import RELACOES, plano
from ..texto import STOPWORDS, frases, normalizar, semelhanca

NOME = "perguntas"
ROTULO = "Perguntas"
TIPOS_EXTRAS = ("pergunta_feita", "pergunta_guardada")
# A resposta dela em "Nós dois" deixa o texto fora da IA mesmo sem confirmar (a pergunta profunda não:
# `marca_privado` só pede 🔒 para Nós dois). Seção 20.10.
TIPOS_FORA_PELO_TEXTO = ("pergunta_feita",)

TABELA = "perguntas_feitas"
ESQUEMA = [
    f"""
    CREATE TABLE {TABELA} (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        pergunta_id INTEGER NOT NULL,
        trilha TEXT NOT NULL,
        categoria TEXT NOT NULL,
        pergunta TEXT NOT NULL,
        status TEXT NOT NULL,
        origem TEXT NOT NULL,
        sugerida_em TEXT,
        guardada_em TEXT,
        feita_em TEXT,
        feita_por TEXT,
        pulada_em TEXT,
        adiada_ate TEXT,
        registro_id INTEGER,
        resposta TEXT,
        certeza TEXT,
        recusou INTEGER NOT NULL DEFAULT 0,
        criado_em TEXT NOT NULL
    );
    CREATE INDEX {TABELA}_pergunta ON {TABELA}(pergunta_id, status);
    CREATE INDEX {TABELA}_registro ON {TABELA}(registro_id)
    """,
]
ESQUEMA_VERSAO = len(ESQUEMA)

STATUS = ("sugerida", "guardada", "feita", "pulada")
ORIGENS = ("sugestao", "tela", "registro")

# Ajustes (Ajustes → 💬 Perguntas; ficam na config do diário).
CHAVE_DIAS = "dias_entre_perguntas"
CHAVE_NOS_DOIS = "perguntas_nos_dois"
DIAS_ENTRE_PADRAO = 3
DIAS_ENTRE_MIN, DIAS_ENTRE_MAX = 1, 30

MIN_REGISTROS = 5          # antes disso, o app ainda não sabe o bastante para escolher com motivo
DIAS_GANCHO = 14           # "ela falou do trabalho na terça": o que ela contou nos últimos 14 dias
DIAS_SENSIVEL = 3          # estado negativo nos últimos 3 dias: nada de pergunta funda
VALIDADE_SUGESTAO = 7      # sugestão ignorada por 7 dias abre lugar para outra
VALIDADE_GUARDADA = 14     # "Vou perguntar": o Hoje lembra por 14 dias; depois fica só na lista
DIAS_RECUSA = 90           # ela preferiu não responder: a categoria descansa
DIAS_NOS_DOIS = 14         # no máximo uma de "Nós dois" a cada 14 dias
JANELA_FIM_DO_MES = 7      # últimos 7 dias do mês: a voz dela entra na reflexão do mês
DIAS_PULADAS = 30
MAX_RESPOSTA = 2000

RELEVANCIA_SUGESTAO = 46
RELEVANCIA_FIM_DO_MES = 50
RELEVANCIA_GUARDADA = 44
RELEVANCIA_APOIO = 45

PERGUNTA_DESCONHECIDA = "Essa pergunta não existe no banco de perguntas."
PERGUNTA_NAO_CITADA = "O registro não cita essa pergunta; confira o texto."
SEM_MOTIVO = "Uma pergunta que você ainda não fez."
FIM_DO_MES = "O mês está acabando: a resposta dela pode entrar na sua reflexão do mês (área Eu)."

_DIAS_SEMANA = ("segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo")


# ================================================================ o banco de perguntas
#
# Regras do banco (os testes conferem):
# - `id` é para sempre: nunca reaproveitar nem renumerar (as respostas gravadas apontam para ele);
#   pergunta que sair fica comentada, com o id reservado;
# - nada de pergunta superficial ("qual sua cor favorita?");
# - cada pergunta, na linha que a tela põe em Registrar (`Eu perguntei: “...”`), não pode virar item,
#   pessoa nem data para as regras do app: uma pergunta não é algo que ela disse. Por isso o banco
#   evita "gosta/adora/quer/gostaria/prefere/tem medo de/está ..." e datas ("hoje", "este mês");
# - uma frase só, terminando em "?".
#
# Trilhas: "profundas" (conhecer quem ela é: Infância, Sonhos, Medos, Relacionamentos, Valores,
# Autoconhecimento, Futuro); "nos_dois" (para fazer A ELA sobre a relação; a resposta é dela, 🟢, e a
# área 🪞 Eu mostra ao lado do seu check-in; `repete_dias`: as de acompanhamento voltam depois disso);
# "apoio" (leves, só num momento sensível dela).


@dataclass(frozen=True)
class Categoria:
    id: str
    nome: str
    emoji: str
    trilha: str
    sobre: str                      # "os sonhos dela": completa "você ainda sabe pouco sobre ..."
    categorias_app: tuple[str, ...] = ()   # categorias dos itens (modelos.CATEGORIAS) que contam como saber
    sensivel: bool = False          # fica de fora quando ela anda num momento difícil


@dataclass(frozen=True)
class Pergunta:
    id: int
    categoria: str
    texto: str
    nivel: int = 2                  # 1 leve, 2 média, 3 funda
    termos: tuple[str, ...] = ()    # raízes (sem acento) de assuntos que puxam esta pergunta
    repete_dias: int | None = None  # só "nos_dois"/"apoio": pode voltar depois de N dias


TRILHAS: tuple[str, ...] = ("profundas", "nos_dois", "apoio")

CATEGORIAS: dict[str, Categoria] = {c.id: c for c in (
    Categoria("infancia", "Infância", "🧸", "profundas", "a infância dela", ("Infância",), sensivel=True),
    Categoria("sonhos", "Sonhos", "🌠", "profundas", "os sonhos dela", ("Sonhos",)),
    Categoria("medos", "Medos", "🌧️", "profundas", "os medos dela", ("Medos",), sensivel=True),
    Categoria("relacionamentos", "Relacionamentos", "🤝", "profundas",
              "as pessoas e as relações que marcaram a vida dela", ("Família", "Amigos", "Pessoas importantes")),
    Categoria("valores", "Valores", "🧭", "profundas", "o que é importante para ela",
              ("Experiências importantes", "Conversas importantes")),
    Categoria("autoconhecimento", "Autoconhecimento", "💭", "profundas", "como ela se vê",
              ("Coisas que deixam feliz", "Coisas que incomodam")),
    Categoria("futuro", "Futuro", "🔭", "profundas", "os planos dela para o futuro", ("Objetivos",)),
    Categoria("nos_dois", "Nós dois", "💞", "nos_dois", "o que ela pensa sobre vocês dois",
              ("Relacionamento", "Memórias compartilhadas")),
    Categoria("apoio", "Com cuidado", "🫶", "apoio", "o que ajuda ela nos dias difíceis"),
)}

# Assunto registrado nos últimos dias (categoria do item) -> (categorias de pergunta que ele puxa,
# como a frase do "por quê" fala dele: "ela falou do trabalho na terça").
GANCHOS: dict[str, tuple[tuple[str, ...], str]] = {
    "Trabalho": (("futuro", "valores"), "do trabalho"),
    "Estudos": (("futuro", "sonhos"), "dos estudos"),
    "Família": (("relacionamentos", "infancia"), "da família"),
    "Amigos": (("relacionamentos",), "dos amigos"),
    "Pessoas importantes": (("relacionamentos",), "de alguém importante para ela"),
    "Viagens": (("sonhos",), "de viagem"),
    "Lugares": (("sonhos",), "de um lugar"),
    "Objetivos": (("futuro", "sonhos"), "dos planos dela"),
    "Sonhos": (("sonhos", "futuro"), "de um sonho"),
    "Infância": (("infancia",), "da infância"),
    "Experiências importantes": (("valores", "autoconhecimento"), "de uma experiência importante"),
    "Conversas importantes": (("valores",), "de uma conversa importante"),
    "Coisas que deixam feliz": (("autoconhecimento",), "do que a deixa feliz"),
    "Coisas que incomodam": (("autoconhecimento", "valores"), "do que a incomoda"),
    "Hobbies": (("autoconhecimento", "sonhos"), "de um hobby"),
    "Música": (("autoconhecimento", "infancia"), "de música"),
    "Livros": (("valores", "autoconhecimento"), "de livros"),
    "Filmes": (("autoconhecimento",), "de filmes"),
    "Séries": (("autoconhecimento",), "de séries"),
    "Eventos futuros": (("futuro",), "de algo que está para acontecer"),
    "Relacionamento": (("nos_dois",), "de vocês dois"),
    "Memórias compartilhadas": (("nos_dois",), "de um momento de vocês"),
}

PERGUNTAS: tuple[Pergunta, ...] = (
    # ------------------------------------------------------------ Infância (1-19)
    Pergunta(1, "infancia", "Qual é a lembrança mais feliz que você tem da sua infância?", 1),
    Pergunta(2, "infancia", "Quem cuidava de você quando você era pequena, e o que essa pessoa te ensinou?", 2,
             ("famil", "avo", "mae", "pai", "tia")),
    Pergunta(3, "infancia", "Que brincadeira ou lugar fazia você perder a noção do tempo quando era criança?", 1),
    Pergunta(4, "infancia", "O que a menina que você foi diria se te visse agora?", 2),
    Pergunta(5, "infancia", "Teve algum momento da infância em que você sentiu muito orgulho de si mesma?", 2),
    Pergunta(6, "infancia", "Como eram as datas especiais na sua casa quando você era pequena?", 1, ("famil",)),
    Pergunta(7, "infancia", "Que cheiro, música ou comida te leva direto de volta para a infância?", 1,
             ("music", "comid", "receit")),
    Pergunta(8, "infancia", "Tem alguma parte da sua infância que, olhando com os olhos de agora, você mudaria?", 3),
    Pergunta(9, "infancia", "Que profissão você imaginava para você quando era criança, e o que sobrou disso?", 2,
             ("trabal", "profis", "carrei")),
    Pergunta(10, "infancia", "Quem foi a pessoa mais importante da sua infância, e por quê?", 2, ("famil", "avo")),
    Pergunta(11, "infancia", "Qual foi a primeira vez que você se lembra de ter se sentido realmente compreendida?", 3),
    Pergunta(12, "infancia", "Que história da sua família você ouviu tantas vezes que sabe de cor?", 1, ("famil",)),

    # ------------------------------------------------------------ Sonhos (20-39)
    Pergunta(20, "sonhos", "Qual é um sonho que você nunca contou para ninguém?", 3),
    Pergunta(21, "sonhos", "Se dinheiro e tempo não fossem problema, como seria um dia perfeito para você?", 1,
             ("dinhei", "folga", "ferias")),
    Pergunta(22, "sonhos", "Que sonho você deixou de lado e às vezes ainda volta a pensar nele?", 3),
    Pergunta(23, "sonhos", "Que lugar do mundo mexe com a sua imaginação, e por quê?", 1,
             ("viage", "viaja", "lugar", "pais")),
    Pergunta(24, "sonhos", "O que você faria se soubesse que não ia dar errado?", 2),
    Pergunta(25, "sonhos", "Que conquista faria você sentir que a vida valeu muito a pena?", 3),
    Pergunta(26, "sonhos", "Se pudesse aprender qualquer coisa de um dia para o outro, o que escolheria?", 1,
             ("curso", "aprend", "estud")),
    Pergunta(27, "sonhos", "Qual sonho seu ficou maior ou menor com o passar dos anos?", 2),
    Pergunta(28, "sonhos", "Como seria a casa dos seus sonhos, por dentro e por fora?", 1, ("casa", "apart", "mudan")),
    Pergunta(29, "sonhos", "Que experiência não pode faltar na sua vida, nem que seja uma vez só?", 1,
             ("viage", "show")),
    Pergunta(30, "sonhos", "Que sonho combina com a pessoa que você é agora?", 2),
    Pergunta(31, "sonhos", "Qual é o seu maior sonho neste momento da vida?", 1),

    # ------------------------------------------------------------ Medos (40-59)
    Pergunta(40, "medos", "O que mais te assusta quando você pensa no futuro?", 2),
    Pergunta(41, "medos", "Qual foi um medo que você já superou, e como conseguiu?", 2),
    Pergunta(42, "medos", "Em que situações do dia a dia você fica menos à vontade do que as pessoas imaginam?", 2),
    Pergunta(43, "medos", "Quando bate um medo, o que você precisa ouvir de alguém?", 2),
    Pergunta(44, "medos", "Que medo de criança ainda aparece de vez em quando?", 2),
    Pergunta(45, "medos", "O que você evita fazer por medo de dar errado?", 3),
    Pergunta(46, "medos", "De qual atitude corajosa sua você mais se orgulha?", 2),
    Pergunta(47, "medos", "Em relação às pessoas mais queridas, o que te dá mais medo?", 3),
    Pergunta(48, "medos", "Existe algum medo seu que, se eu conhecesse, me ajudaria a te apoiar melhor?", 3),
    Pergunta(49, "medos", "O que te faz sentir segura quando o mundo parece bagunçado?", 2),
    Pergunta(50, "medos", "Qual decisão você vem adiando por medo de errar?", 3, ("decis", "mudar")),
    Pergunta(51, "medos", "Qual é o seu maior medo neste momento da vida?", 2),

    # ------------------------------------------------------------ Relacionamentos (60-79)
    Pergunta(60, "relacionamentos", "Quem é a pessoa que mais te entende sem você precisar explicar?", 1,
             ("amig", "irma")),
    Pergunta(61, "relacionamentos", "Qual amizade marcou a sua vida, e o que ela te ensinou?", 2, ("amig",)),
    Pergunta(62, "relacionamentos", "O que você aprendeu sobre amor observando a sua família?", 3, ("famil",)),
    Pergunta(63, "relacionamentos", "Tem alguém com quem você perdeu o contato e de quem sente saudade?", 2,
             ("amig", "saudad")),
    Pergunta(64, "relacionamentos", "O que faz você confiar de verdade em alguém?", 2),
    Pergunta(65, "relacionamentos", "Qual foi a conversa que mudou a forma como você via alguém?", 2),
    Pergunta(66, "relacionamentos", "Quem você admira muito, e o que essa pessoa tem de especial?", 1),
    Pergunta(67, "relacionamentos", "Que tipo de amiga você é nos momentos difíceis?", 2, ("amig",)),
    Pergunta(68, "relacionamentos", "Que relação da sua vida merece mais cuidado do que tem recebido?", 3,
             ("famil", "amig")),
    Pergunta(69, "relacionamentos", "De que jeito você demonstra carinho pelas pessoas queridas?", 1),
    Pergunta(70, "relacionamentos", "O que as pessoas próximas ainda não entendem direito sobre você?", 2),

    # ------------------------------------------------------------ Valores (80-99)
    Pergunta(80, "valores", "O que é inegociável para você, aconteça o que acontecer?", 2),
    Pergunta(81, "valores", "Que atitude faz você perder o respeito por alguém?", 2),
    Pergunta(82, "valores", "Do que você mais se orgulha na forma como leva a vida?", 2),
    Pergunta(83, "valores", "Que valor da sua família você faz questão de levar adiante?", 2, ("famil",)),
    Pergunta(84, "valores", "Em que situação você mudou de opinião sobre algo importante?", 2),
    Pergunta(85, "valores", "O que significa ter uma vida boa para você?", 1),
    Pergunta(86, "valores", "Que injustiça mexe mais com você?", 2, ("notic", "injust")),
    Pergunta(87, "valores", "Qual foi uma escolha difícil que você fez por ser coerente com o que acredita?", 3,
             ("trabal", "chefe", "decis")),
    Pergunta(88, "valores", "Do que o mundo mais precisa hoje em dia, na sua opinião?", 1),
    Pergunta(89, "valores", "Que regra que te ensinaram você decidiu deixar para trás?", 3),
    Pergunta(90, "valores", "Como você define sucesso, do seu jeito?", 1, ("trabal", "promoc", "carrei")),

    # ------------------------------------------------------------ Autoconhecimento (100-119)
    Pergunta(100, "autoconhecimento", "Em que momentos você se reconhece mais como você mesma?", 1),
    Pergunta(101, "autoconhecimento", "O que te recarrega depois de um dia difícil?", 1),
    Pergunta(102, "autoconhecimento", "Que qualidade sua as pessoas não costumam enxergar?", 2),
    Pergunta(103, "autoconhecimento", "Qual parte de você mudou mais nos últimos anos?", 2),
    Pergunta(104, "autoconhecimento", "O que passa pela sua cabeça antes de dormir, na maioria das noites?", 2),
    Pergunta(105, "autoconhecimento", "Do que você precisa para ficar em paz?", 2),
    Pergunta(106, "autoconhecimento", "Em que você cobra mais de si mesma do que dos outros?", 3,
             ("trabal", "cobra")),
    Pergunta(107, "autoconhecimento", "Que pequena coisa, feita só para você, deixa o seu dia melhor?", 1),
    Pergunta(108, "autoconhecimento", "Quais sinais te mostram que chegou a hora de uma pausa?", 2,
             ("cansa", "pausa", "ferias")),
    Pergunta(109, "autoconhecimento", "Que elogio te marcou de verdade?", 1),
    Pergunta(110, "autoconhecimento", "Qual sentimento é mais difícil para você mostrar?", 3),

    # ------------------------------------------------------------ Futuro (120-139)
    Pergunta(120, "futuro", "Como você se imagina num futuro distante, olhando para trás?", 2),
    Pergunta(121, "futuro", "Se pudesse mudar de profissão sem nenhum medo, o que faria?", 2,
             ("trabal", "profis", "empreg", "carrei", "area")),
    Pergunta(122, "futuro", "Qual projeto novo faz seus olhos brilharem quando você pensa nele?", 1,
             ("projet", "curso")),
    Pergunta(123, "futuro", "Se pudesse escolher qualquer cidade para viver, qual seria?", 1,
             ("cidad", "mudan", "viage")),
    Pergunta(124, "futuro", "Como seria um trabalho que te deixasse realizada?", 2,
             ("trabal", "empreg", "chefe", "carrei")),
    Pergunta(125, "futuro", "Que tipo de pessoa você está tentando se tornar?", 3),
    Pergunta(126, "futuro", "O que precisa acontecer para você sentir que está no caminho certo?", 2),
    Pergunta(127, "futuro", "Que plano você tem guardado e ainda não começou?", 2, ("plano", "projet")),
    Pergunta(128, "futuro", "Como seria a rotina ideal da sua vida mais para frente?", 1, ("rotin",)),
    Pergunta(129, "futuro", "O que você deseja ter aprendido quando for mais velha?", 3),
    Pergunta(130, "futuro", "Por qual marca sua as pessoas vão lembrar de você?", 3),

    # ------------------------------------------------------------ Nós dois (200-249)
    Pergunta(200, "nos_dois", "Tem algo que eu possa fazer melhor por você?", 2, repete_dias=60),
    Pergunta(201, "nos_dois", "O que te fez se sentir cuidada por mim nas últimas semanas?", 1, repete_dias=45),
    Pergunta(202, "nos_dois", "Qual momento nosso recente você guardaria numa caixinha?", 1,
             ("passe", "jantar", "viage", "encontr", "cinema", "fomos"), repete_dias=45),
    Pergunta(203, "nos_dois", "Tem alguma coisa que eu faço que te incomoda e você ainda não falou?", 3,
             repete_dias=90),
    Pergunta(204, "nos_dois", "Em que momentos você se sente mais amada por mim?", 2),
    Pergunta(205, "nos_dois", "O que a gente devia fazer mais vezes?", 1, repete_dias=60),
    Pergunta(206, "nos_dois", "Tem algo que eu disse ou fiz que ficou na sua cabeça, para o bem ou para o mal?", 3,
             repete_dias=60),
    Pergunta(207, "nos_dois", "Nos dias mais corridos, que tipo de apoio meu faz mais diferença?", 2,
             ("corrid", "cansa", "trabal")),
    Pergunta(208, "nos_dois", "Qual parte da nossa rotina é a sua favorita?", 1, ("rotin",)),
    Pergunta(209, "nos_dois", "Qual sonho nosso merece começar a sair do papel?", 2, ("viage", "casa", "plano")),
    Pergunta(210, "nos_dois", "Quando você precisa, sente que eu te escuto de verdade?", 2, repete_dias=90),
    Pergunta(211, "nos_dois", "O que eu faço que mais te faz rir?", 1),
    Pergunta(212, "nos_dois", "Tem alguma coisa sobre a gente que anda tirando o seu sono?", 3, repete_dias=90),
    Pergunta(213, "nos_dois", "Que pequeno gesto meu fez diferença para você recentemente?", 1, repete_dias=45),
    Pergunta(214, "nos_dois", "Como têm sido as últimas semanas entre a gente, para você?", 2, repete_dias=30),
    Pergunta(215, "nos_dois", "Do que você sente falta entre nós dois?", 2, repete_dias=60),
    Pergunta(216, "nos_dois", "Em que momento você sentiu que a gente era um time de verdade?", 1),
    Pergunta(217, "nos_dois", "Tem algo que você precisa de mim e não tem pedido?", 3, repete_dias=60),
    Pergunta(218, "nos_dois", "Qual foi a nossa melhor conversa, na sua opinião?", 1),
    Pergunta(219, "nos_dois", "O que a gente faz bem como casal?", 1),
    Pergunta(220, "nos_dois", "Que tradição só nossa você criaria?", 1, ("natal", "anivers", "tradic")),
    Pergunta(221, "nos_dois", "Quando a gente discorda, o que eu poderia fazer diferente?", 3,
             ("brig", "discut", "discord"), repete_dias=90),
    Pergunta(222, "nos_dois", "O que te faz sentir que pode contar comigo?", 2),
    Pergunta(223, "nos_dois", "Qual história nossa você mais conta para os outros?", 1),

    # ------------------------------------------------------------ Com cuidado (300-319)
    Pergunta(300, "apoio", "Tem alguma coisa que eu possa fazer para o seu dia ficar mais leve?", 1, repete_dias=14),
    Pergunta(301, "apoio", "Agora ajuda mais conversar sobre o que aconteceu ou pensar em outra coisa?", 1,
             repete_dias=14),
    Pergunta(302, "apoio", "Agora faz mais sentido ter companhia ou um tempinho só seu?", 1, repete_dias=14),
    Pergunta(303, "apoio", "Nesses dias, qual coisa pequena faz bem para você?", 1, repete_dias=14),
    Pergunta(304, "apoio", "Você precisa que eu só escute, sem dar conselho?", 1, repete_dias=14),
    Pergunta(305, "apoio", "Posso preparar alguma coisa gostosa para você comer?", 1, repete_dias=14),
    Pergunta(306, "apoio", "Agora é melhor eu ficar por perto ou te deixar descansar?", 1, repete_dias=14),
    Pergunta(307, "apoio", "Como eu posso te apoiar sem atrapalhar?", 1, repete_dias=14),
)

POR_ID: dict[int, Pergunta] = {p.id: p for p in PERGUNTAS}


def pergunta(pid: Any) -> Pergunta | None:
    """A pergunta do banco com esse id (ou None)."""
    if isinstance(pid, bool):
        return None
    try:
        return POR_ID.get(int(pid))
    except (TypeError, ValueError):
        return None


def categoria(p: Pergunta) -> Categoria:
    return CATEGORIAS[p.categoria]


def trilha(p: Pergunta) -> str:
    return CATEGORIAS[p.categoria].trilha


def texto_para_registrar(p: Pergunta) -> str:
    """O que a tela põe em Registrar depois de "Perguntei": a linha da pergunta e o lugar da
    resposta. Nenhuma pergunta do banco vira item, pessoa ou data nessa linha (os testes conferem):
    uma pergunta não é algo que ela disse."""
    return f"Eu perguntei: “{p.texto}”\nEla respondeu: "


# ================================================================ achar a pergunta no texto

_RE_PONTUACAO = re.compile(r"[^\w\s]")
_RE_ASPAS = re.compile(r"[“\"«]([^”\"»\n]{6,400})[”\"»]")
_PALAVRAS_GENERICAS = frozenset(normalizar(w) for w in (
    "você vocês voce voces seu sua seus suas te ti teu tua qual quais que algum alguma alguns algumas "
    "alguém algo coisa coisas mais menos vez vezes dela dele ela ele pra pro para era foi são tem ter "
    "tinha faz fazer fez pode poderia porque por como quando onde quem já sobre entre seja fosse sempre "
    "nunca muito muita perguntei pergunta perguntar perguntou disse respondeu falou eu mim me minha meu "
    "nós gente isso isto esse essa este esta aquele aquela tão tanto agora hoje mesmo mesma qualquer "
    "acha acho seria será sido estar está estava respeito neste nesse nessa nesta momento vida dia dias ainda"
).split()) | frozenset(normalizar(w) for w in STOPWORDS)


def _chave_texto(texto: str) -> str:
    """Para comparar perguntas: sem acento, sem pontuação, espaços simples."""
    return " ".join(_RE_PONTUACAO.sub(" ", normalizar(texto)).split())


def _raizes(texto: str) -> frozenset[str]:
    """As palavras de conteúdo, pela raiz (4 letras), sem as genéricas de pergunta
    ("sonho"/"sonhava" -> "sonh"; "medo"/"medos" -> "medo")."""
    return frozenset(w[:4] for w in _chave_texto(texto).split() if len(w) > 2 and w not in _PALAVRAS_GENERICAS)



_POR_CHAVE: dict[str, Pergunta] = {_chave_texto(p.texto): p for p in PERGUNTAS}
_RAIZES: dict[int, frozenset[str]] = {p.id: _raizes(p.texto) for p in PERGUNTAS}

# Pergunta de primeira pessoa ("perguntei", "fiz a pergunta"); "ela me perguntou" não é nossa.
_RE_PERGUNTEI = re.compile(r"\b(?:eu\s+)?(?:perguntei|fiz\s+(?:a|aquela|essa|uma)\s+pergunta)\b")
_RE_VOU_PERGUNTAR = re.compile(
    r"\b(?:vou|quero|preciso|tenho\s+que|tenho\s+de|pretendo|queria|lembrar\s+de|nao\s+posso\s+esquecer\s+de)"
    r"\s+(?:\w+\s+){0,2}?perguntar\b")
_RE_NEGADO = re.compile(
    r"\b(?:nao|n|nunca|nem|ainda\s+nao)\s+(?:(?:te|lhe|me|cheguei\s+a|consegui|vou|ia|fui|quis)\s+)?"
    r"(?:perguntei|perguntar)\b|\b(?:esqueci|deixei)\s+de\s+perguntar\b|\bfaltou\s+perguntar\b")
_RE_OBJETO = re.compile(r"^\s*(?:pra|para|a|à|pro|ao)\s+(?:(?:a|o)\s+)?(?P<obj>[\w-]+(?:\s+[\w-]+)?)")
_RE_RESPOSTA_NA_FRASE = re.compile(
    r"(?:,\s*|\s+)(?:e\s+|mas\s+|ai\s+|entao\s+)?ela\s+(?:me\s+)?(?:disse|respondeu|falou|contou|explicou"
    r"|comentou|riu|ficou|nao\s+quis|preferiu|mudou|desconversou|fugiu\s+do\s+assunto|nao\s+respondeu)\b")
# Verbos de fala: a resposta precisa de conteúdo depois deles ("ela respondeu." sozinho não é resposta).
_RE_VERBO_DE_FALA = re.compile(r"ela\s+(?:me\s+)?(?:disse|respondeu|falou|contou|explicou|comentou)\s*$")
# "não lembro o que ela respondeu", "não sei se ela disse": não é a resposta, é a falta dela.
_RE_SEM_LEMBRAR_ANTES = re.compile(r"(?:\bo\s+que|\bse|\blembro|\bsei|\blembrar|\bsaber)\s*$")
_RE_ACHO_QUE_PERGUNTEI = re.compile(r"\b(?:acho\s+que|talvez|nao\s+lembro\s+se|nao\s+sei\s+se)\s+(?:eu\s+)?"
                                    r"(?:ja\s+)?(?:perguntei|fiz\s+(?:a|aquela|essa|uma)\s+pergunta)\b")
_RE_MARCA_RESPOSTA = re.compile(
    r"^[ \t]*(?:ela[ \t]+(?:me[ \t]+)?(?:respondeu|disse|falou|contou)|resposta(?:[ \t]+dela)?)"
    r"[ \t]*(?::|-|—|que\b)?[ \t]*")
_RE_MARCA_ANTES = re.compile(
    r"(?:ela\s+(?:me\s+)?(?:respondeu|disse|falou|contou)|resposta(?:\s+dela)?)\s*(?::|-|—|que)?\s*$")
_RE_RECUSA = re.compile(
    r"\b(?:nao|n)\s+quis\s+(?:responder|falar|comentar|entrar)|\bpreferiu\s+nao\s+(?:responder|falar|comentar)"
    r"|\bmudou\s+de\s+assunto|\bnao\s+respondeu|\bficou\s+sem\s+responder|\bdesconversou|\bfugiu\s+do\s+assunto"
    r"|\bficou\s+(?:quieta|calada|em\s+silencio)"
    r"|\bdisse\s+que\s+nao\s+queria\s+falar|\bnao\s+quer\s+falar\s+(?:disso|sobre)"
    r"|\bpediu\s+(?:pra|para)\s+(?:falar|conversar)\s+(?:disso\s+|sobre\s+isso\s+)?(?:outra\s+hora|depois)")
_PALAVRAS_DELA = frozenset({"ela", "namorada", "mozao", "amor"})
_CARINHOS = frozenset({"namorada", "amor", "mozao", "gata", "linda", "princesa", "vida", "pequena"})
_COMECO_DE_PERGUNTA = frozenset({"que", "qual", "quais", "se", "como", "quando", "onde", "quem", "por", "do", "da",
                                 "sobre", "de", "um", "uma", "respeito", "ela"})


def _nome_dela(palavra: str, nome_dela: str | None) -> bool:
    """"Julia", "Ju" e "Julinha" são ela (como `regras.pessoas.e_nome_dela`, de um jeito simples)."""
    n = normalizar(nome_dela or "").split()
    if not n:
        return False
    w, n = normalizar(palavra), n[0]
    apelido = 2 <= len(w) <= 4 and n.startswith(w)                         # "Ju" de Julia
    diminutivo = len(n) >= 3 and w.startswith(n[:3]) and w.endswith("inha")   # "Julinha"
    return w == n or apelido or diminutivo


def _objeto_e_ela(depois: str, nome_dela: str | None) -> bool:
    """Depois de "perguntei": "pra ela", "pra Julia" (o nome dela), "à minha namorada" ou sem
    objeto = ela; "pra mãe dela", "pro meu pai", "pra Mariana" = outra pessoa."""
    m = _RE_OBJETO.match(depois)
    if not m:
        return True
    palavras = m.group("obj").split()
    primeira = palavras[0]
    if primeira in ("minha", "meu"):
        return len(palavras) > 1 and palavras[1] in _CARINHOS
    if primeira in _PALAVRAS_DELA or _nome_dela(primeira, nome_dela):
        return True
    if normalizar(primeira) in RELACOES or normalizar(" ".join(palavras)) in RELACOES:
        return False
    return primeira in _COMECO_DE_PERGUNTA


def _casar_literal(citada: str) -> tuple[Pergunta, str, int] | None:
    """Texto entre aspas -> a pergunta do banco: igual ("literal") ou quase ("parecida": ≥ 90 e sem
    empate)."""
    chave = _chave_texto(citada)
    if chave in _POR_CHAVE:
        return _POR_CHAVE[chave], "literal", 100
    notas = sorted(((semelhanca(chave, _chave_texto(p.texto)), p.id) for p in PERGUNTAS), reverse=True)
    if notas and notas[0][0] >= 90 and (len(notas) == 1 or notas[0][0] - notas[1][0] >= 5):
        return POR_ID[notas[0][1]], "parecida", int(notas[0][0])
    return None


def _casar_livre(trecho: str) -> tuple[Pergunta, str, int] | None:
    """"qual era o maior sonho dela" -> a pergunta do banco com as mesmas palavras de conteúdo.
    Precisa de 2 raízes em comum e de metade das raízes da pergunta; empate = nenhuma (melhor não
    ligar do que ligar errado)."""
    alvo = _raizes(trecho)
    if len(alvo) < 2:
        return None
    notas = []
    for p in PERGUNTAS:
        raizes = _RAIZES[p.id]
        comuns = len(alvo & raizes)
        if comuns >= 2 and raizes and comuns / len(raizes) >= 0.5:
            notas.append((comuns / len(raizes), comuns, p.id))
    if not notas:
        return None
    notas.sort(reverse=True)
    if len(notas) > 1 and notas[0][:2] == notas[1][:2]:
        return None
    return POR_ID[notas[0][2]], "livre", int(round(notas[0][0] * 100))


@dataclass
class Achado:
    """Uma pergunta do banco achada no texto (posições no texto original)."""
    pergunta: Pergunta
    acao: str                        # "feita" | "guardada"
    forma: str                       # "literal" | "parecida" | "livre"
    semelhanca: int
    ini: int                         # a frase inteira (o `trecho` do extra)
    fim: int
    resposta_ini: int | None = None  # resposta dentro da própria frase ("...e ela disse que X"), relativa à frase


def _outra_pessoa_logo_depois(posicao: int, pessoas: Iterable[Any]) -> bool:
    """Uma pessoa (não ela) começa logo depois do verbo: "perguntei pra Mariana ..."."""
    for pessoa in pessoas or ():
        inicio = pessoa.get("inicio") if isinstance(pessoa, dict) else getattr(pessoa, "inicio", None)
        if isinstance(inicio, int) and 0 <= inicio - posicao <= 6:
            return True
    return False


def _resposta_na_frase(p: str, desde: int) -> re.Match[str] | None:
    """A marca de resposta na própria frase, se for mesmo uma resposta: nada de "não lembro o que ela
    respondeu" nem de "ela disse." sem nada depois (revisão de 07/10: virava a resposta 🟢 "ela respondeu.")."""
    for r in _RE_RESPOSTA_NA_FRASE.finditer(p, desde):
        if _RE_SEM_LEMBRAR_ANTES.search(p[max(0, r.start() - 25):r.start()]):
            continue
        marca = p[r.start():r.end()].strip(" ,")
        marca = re.sub(r"^(?:e|mas|ai|entao)\s+", "", marca)
        if _RE_VERBO_DE_FALA.match(marca) and not re.search(r"[a-z]{2,}", re.sub(r"^\s*(?:que|q)\b", "", p[r.end():])):
            continue
        return r
    return None


def _achar(texto: str, nome_dela: str | None = None, pessoas: Iterable[Any] = ()) -> list[Achado]:
    """As perguntas do banco que o texto diz que você fez (ou vai fazer) a ela."""
    achados: list[Achado] = []
    for ini, fim, frase in frases(texto):
        p = plano(frase)
        if _RE_NEGADO.search(p):
            continue
        passado, futuro = _RE_PERGUNTEI.search(p), _RE_VOU_PERGUNTAR.search(p)
        verbo = passado if passado and (not futuro or passado.start() <= futuro.start()) else futuro
        if verbo is None:
            continue
        acao = "feita" if verbo is passado else "guardada"
        if not _objeto_e_ela(p[verbo.end():], nome_dela) or _outra_pessoa_logo_depois(ini + verbo.end(), pessoas):
            continue
        casado, resposta_ini = None, None
        # 1) a pergunta entre aspas, como a tela escreve ("Eu perguntei: “...”")
        for m in _RE_ASPAS.finditer(frase):
            if m.start() < verbo.start():
                continue
            casado = _casar_literal(m.group(1))
            if casado:
                r = _resposta_na_frase(p, m.end())
                resposta_ini = r.start() if r else None
                break
        # 2) do jeito de cada um ("perguntei pra ela qual era o maior sonho dela e ela disse...")
        if casado is None:
            r = _resposta_na_frase(p, verbo.end())
            fim_pergunta = r.start() if r else len(frase)
            # "...mas não lembro o que ela respondeu": a pergunta acaba antes do "mas".
            corte = re.search(r"[,;]?\s+(?:mas|so\s+que|porem)\s", p[verbo.end():fim_pergunta])
            if corte:
                fim_pergunta = verbo.end() + corte.start()
            casado = _casar_livre(frase[verbo.end():fim_pergunta])
            resposta_ini = r.start() if r else None
        if casado is None or any(a.pergunta.id == casado[0].id for a in achados):
            continue
        if resposta_ini is not None:
            ela = re.search(r"\bela\b", p[resposta_ini:])      # começa no "ela disse" (sem a vírgula, o "e")
            resposta_ini += ela.start() if ela else 0
        achados.append(Achado(casado[0], acao, casado[1], casado[2], ini, fim, resposta_ini))
    return achados


def _resposta(texto: str, achado: Achado, limite: int | None = None) -> str | None:
    """O trecho literal da resposta: na própria frase ("...e ela disse que X", até o fim da frase) ou
    na linha seguinte, como a tela escreve ("Ela respondeu: X", até o fim da linha, sem a marca).
    Nunca passa da próxima pergunta."""
    limite = len(texto) if limite is None else limite
    if achado.resposta_ini is not None:
        ini, fim = achado.ini + achado.resposta_ini, achado.fim
    else:
        # A linha logo abaixo da pergunta (uma linha em branco no meio = outro assunto, sem resposta).
        pular = re.match(r"[ \t]*(?:\r?\n)?[ \t]*", texto[achado.fim:limite])
        ini = achado.fim + (pular.end() if pular else 0)
        if ini >= limite or texto[ini] in "\r\n":
            return None
        m = _RE_MARCA_RESPOSTA.match(plano(texto[ini:limite]))
        if m:
            ini += m.end()
        fim_linha = texto.find("\n", ini)
        fim = min(limite, fim_linha if fim_linha >= 0 else len(texto))
    resposta = texto[ini:min(fim, limite)].strip()[:MAX_RESPOSTA].strip()
    return resposta if re.search(r"\w", resposta) else None


def _recusou(*textos: str | None) -> bool:
    return any(t and _RE_RECUSA.search(plano(t)) for t in textos)


def _certeza(texto: str, resposta: str | None, recusou: bool) -> str | None:
    """A certeza da resposta, sem nunca subir sem pista: depois de "Ela respondeu:"/"ela disse"
    (a marca que a tela escreve) é fala dela (🟢), a não ser que o texto diga que é palpite (🔵).
    Sem a marca, vale o que as regras dizem da frase ("ela ficou pensativa" é 🟡)."""
    if not resposta or recusou:
        return None
    lida = classificar_certeza(resposta)
    if lida == Certeza.INFERENCIA:
        return Certeza.INFERENCIA.value
    pos = texto.find(resposta)
    antes = plano(texto[max(0, texto.rfind("\n", 0, pos) + 1):pos]) if pos >= 0 else ""
    if _RE_MARCA_ANTES.search(antes):
        return Certeza.CONFIRMADO.value
    return lida.value


# ================================================================ ganchos: extrair, marcar 🔒, gravar

def extrair(texto: str, referencia: date, pessoas: list, datas: list, itens: list, conhecimento: Any) -> list[dict]:
    """Perguntas do banco que o texto diz que você fez (ou vai fazer) a ela, para a revisão."""
    achados = _achar(texto, getattr(conhecimento, "nome_dela", None), pessoas)
    saida = []
    for i, a in enumerate(achados):
        cat = categoria(a.pergunta)
        extra = {"pergunta_id": a.pergunta.id, "categoria": cat.id, "categoria_nome": cat.nome, "emoji": cat.emoji,
                 "trilha": cat.trilha, "pergunta": a.pergunta.texto, "forma": a.forma, "semelhanca": a.semelhanca,
                 "trecho": texto[a.ini:a.fim]}
        if a.acao == "feita":
            resposta = _resposta(texto, a, achados[i + 1].ini if i + 1 < len(achados) else None)
            recusou = _recusou(resposta)
            certeza = _certeza(texto, resposta, recusou)
            if certeza and _RE_ACHO_QUE_PERGUNTEI.search(plano(texto[a.ini:a.fim])):
                certeza = Certeza.INFERENCIA.value          # "acho que perguntei": no máximo 🔵
            extra.update(tipo="pergunta_feita", resposta=resposta, recusou=recusou,
                         certeza=certeza,

                         resumo=f"Pergunta feita ({cat.nome}): “{a.pergunta.texto}”"
                                + (" — ela preferiu não responder" if recusou else ""))
        else:
            extra.update(tipo="pergunta_guardada", resumo=f"Para perguntar ({cat.nome}): “{a.pergunta.texto}”")
        saida.append(extra)
    return saida


def marca_privado(extras: list[dict]) -> bool:
    """Resposta de "Nós dois" é da área Eu: o registro fica 🔒 (nada da área Eu vai para a IA).
    Pergunta que não se reconhece conta como sim (na dúvida, fora da IA)."""
    for e in extras:
        if e.get("tipo") != "pergunta_feita":
            continue
        p = pergunta(e.get("pergunta_id"))
        if p is None or trilha(p) == "nos_dois":
            return True
    return False


def gravar_extras(banco: Any, registro_id: int, extras: list[dict], referencia: date) -> None:
    """Liga o registro às perguntas confirmadas na revisão. Tudo é conferido de novo aqui: a
    pergunta tem de estar no banco e citada no trecho; a resposta só fica se for literal do registro;
    certeza e "preferiu não responder" são recalculadas pelo texto (a tela não decide)."""
    linha = banco.consultar("SELECT texto FROM registros WHERE id = ?", (registro_id,))
    texto = linha[0]["texto"] if linha else ""
    nome_dela = banco.config().get("nome_dela")
    for e in extras:
        p = pergunta(e.get("pergunta_id"))
        if p is None:
            raise ValueError(PERGUNTA_DESCONHECIDA)
        # O trecho tem de citar ESTA pergunta, e do jeito do tipo: "vou perguntar" não vira "perguntei".
        acao = "guardada" if e.get("tipo") == "pergunta_guardada" else "feita"
        if not any(a.pergunta.id == p.id and a.acao == acao for a in _achar(e.get("trecho") or "", nome_dela)):
            raise ValueError(PERGUNTA_NAO_CITADA)
        if acao == "guardada":
            _marcar_guardada(banco, p, referencia, origem="registro", registro_id=registro_id)
            continue
        resposta = e.get("resposta")
        resposta = resposta.strip()[:MAX_RESPOSTA].strip() if isinstance(resposta, str) else ""
        if not resposta or resposta not in texto:
            resposta = None
        recusou = _recusou(resposta)
        _marcar_feita(banco, p, referencia, origem="registro", feita_por="registro", registro_id=registro_id,
                      resposta=resposta, certeza=_certeza(texto, resposta, recusou), recusou=recusou)


# ================================================================ a tabela

def _d(valor: Any) -> date | None:
    if not valor:
        return None
    try:
        return date.fromisoformat(str(valor)[:10])
    except ValueError:
        return None


def _agora() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _linhas(banco: Any, pergunta_id: int | None = None) -> list[dict]:
    if pergunta_id is None:
        return banco.consultar(f"SELECT * FROM {TABELA} ORDER BY id")
    return banco.consultar(f"SELECT * FROM {TABELA} WHERE pergunta_id = ? ORDER BY id", (pergunta_id,))


def _linha(banco: Any, linha_id: int) -> dict:
    return banco.consultar(f"SELECT * FROM {TABELA} WHERE id = ?", (linha_id,))[0]


def _valores(p: Pergunta, **campos: Any) -> dict:
    return {"pergunta_id": p.id, "trilha": trilha(p), "categoria": p.categoria, "pergunta": p.texto,
            "criado_em": _agora(), **campos}


def _aberta(linha: dict, ref: date) -> bool:
    """Sugerida ainda valendo (até 7 dias; a de cuidado, só no dia)."""
    if linha["status"] != "sugerida":
        return False
    quando = _d(linha["sugerida_em"])
    validade = 1 if linha["trilha"] == "apoio" else VALIDADE_SUGESTAO
    return quando is not None and 0 <= (ref - quando).days < validade


def _marcar_feita(banco: Any, p: Pergunta, ref: date, *, origem: str, feita_por: str, registro_id: int | None = None,
                  resposta: str | None = None, certeza: str | None = None, recusou: bool = False) -> int:
    linhas = _linhas(banco, p.id)
    campos: dict[str, Any] = {"status": "feita", "adiada_ate": None}
    if registro_id is not None:
        campos.update(registro_id=registro_id, resposta=resposta, certeza=certeza, recusou=1 if recusou else 0)
        for l in reversed(linhas):                       # o mesmo registro de novo: não duplica
            if l["registro_id"] == registro_id and l["status"] in ("feita", "sugerida", "guardada"):
                banco.atualizar(TABELA, l["id"], {**campos, "feita_em": l["feita_em"] or ref,
                                                  "feita_por": l["feita_por"] or feita_por})
                return l["id"]
        for l in reversed(linhas):                       # "Perguntei" no cartão e depois o registro da resposta
            feita = _d(l["feita_em"])
            if l["status"] == "feita" and l["registro_id"] is None and feita and -1 <= (ref - feita).days <= DIAS_GANCHO:
                banco.atualizar(TABELA, l["id"], campos)
                return l["id"]
    else:
        for l in reversed(linhas):
            if l["status"] == "feita" and l["registro_id"] is None and _d(l["feita_em"]) == ref:
                return l["id"]                           # tocou duas vezes em "Perguntei"
    for l in reversed(linhas):
        if l["status"] in ("sugerida", "guardada", "pulada"):
            banco.atualizar(TABELA, l["id"], {**campos, "feita_em": ref, "feita_por": feita_por})
            return l["id"]
    return banco.inserir(TABELA, _valores(p, origem=origem, feita_em=ref, feita_por=feita_por, **campos))


def _marcar_guardada(banco: Any, p: Pergunta, ref: date, *, origem: str, registro_id: int | None = None) -> int:
    campos = {"status": "guardada", "guardada_em": ref, "adiada_ate": None}
    for l in reversed(_linhas(banco, p.id)):
        if l["status"] == "guardada":
            return l["id"]
        if l["status"] in ("sugerida", "pulada"):
            banco.atualizar(TABELA, l["id"], campos)
            return l["id"]
    valores = _valores(p, origem=origem, **campos)
    if registro_id is not None:
        valores["registro_id"] = registro_id
    return banco.inserir(TABELA, valores)


def _marcar_pulada(banco: Any, p: Pergunta, ref: date, origem: str = "sugestao") -> int:
    for l in reversed(_linhas(banco, p.id)):
        if l["status"] in ("sugerida", "guardada"):
            banco.atualizar(TABELA, l["id"], {"status": "pulada", "pulada_em": ref, "adiada_ate": None})
            return l["id"]
        if l["status"] == "pulada":
            return l["id"]
    return banco.inserir(TABELA, _valores(p, status="pulada", origem=origem, pulada_em=ref))


def _adiar(banco: Any, p: Pergunta, ref: date, dias: int) -> int:
    ate = ref + timedelta(days=max(1, dias))
    for l in reversed(_linhas(banco, p.id)):
        if l["status"] in ("sugerida", "guardada"):
            banco.atualizar(TABELA, l["id"], {"adiada_ate": ate})
            return l["id"]
    return banco.inserir(TABELA, _valores(p, status="sugerida", origem="sugestao", sugerida_em=ref, adiada_ate=ate))


def _materializar(banco: Any, p: Pergunta, ref: date, linhas: list[dict]) -> int:
    """A sugestão nova fica guardada (vale igual pelos próximos dias). Sem histórico: é o app
    escolhendo, não uma ação sua."""
    for l in reversed(linhas):
        if l["pergunta_id"] == p.id and l["status"] == "sugerida":
            banco.atualizar(TABELA, l["id"], {"sugerida_em": ref, "adiada_ate": None}, historico=False)
            return l["id"]
    return banco.inserir(TABELA, _valores(p, status="sugerida", origem="sugestao", sugerida_em=ref), historico=False)


# ================================================================ o que se sabe agora

def _ajustes(banco: Any) -> dict:
    config = banco.config()
    dias = config.get(CHAVE_DIAS)
    if isinstance(dias, bool) or not isinstance(dias, (int, float)) or not DIAS_ENTRE_MIN <= dias <= DIAS_ENTRE_MAX:
        dias = DIAS_ENTRE_PADRAO
    nos_dois = config.get(CHAVE_NOS_DOIS)
    return {CHAVE_DIAS: int(dias), CHAVE_NOS_DOIS: nos_dois if isinstance(nos_dois, bool) else True}


def _dela(item: dict) -> bool:
    return item.get("pessoa_id") is None and not (item.get("pessoa") or "").strip()


def _quando(d: date, ref: date) -> str:
    dias = (ref - d).days
    if dias == 0:
        return "hoje"
    if dias == 1:
        return "ontem"
    if dias == 2:
        return "anteontem"
    if 3 <= dias <= 6:
        nome = _DIAS_SEMANA[d.weekday()]
        return f"{'no' if nome in ('sábado', 'domingo') else 'na'} {nome} ({d:%d/%m})"
    return f"em {d:%d/%m}"


def _capital(s: str) -> str:
    return s[:1].upper() + s[1:]


def _json_lista(valor: Any) -> list[str]:
    try:
        lista = json.loads(valor or "[]")
    except (TypeError, ValueError):
        return []
    return [c for c in lista if isinstance(c, str)] if isinstance(lista, list) else []


@dataclass
class Motivo:
    """Uma parte do "por quê": o texto (vazio = só conta ponto) e de onde veio."""
    texto: str
    pontos: int
    registro_ids: tuple[int, ...] = ()


@dataclass
class Momento:
    """Como ela anda: sensível (estado negativo nos últimos 3 dias, ou um dia importante hoje ou
    amanhã) e/ou animada (o estado mais recente, dos últimos 3 dias, é bom)."""
    sensivel: bool = False
    tipo: str | None = None              # "estado" | "evento"
    texto: str | None = None             # "Ontem você registrou: “Está cansada”."
    registro_ids: tuple[int, ...] = ()
    animada: Motivo | None = None


@dataclass
class Contexto:
    referencia: date
    ajustes: dict
    linhas: list[dict]
    registros: int
    contagem: dict[str, int]             # categoria dos itens -> registros que falam dela
    recentes: list[dict]                 # itens dos últimos 14 dias
    momento: Momento
    apoio_conhecido: bool                # você já registrou o que ajuda (ou não) nos dias difíceis
    fora: set[int] | None                # registros que outro recurso deixa fora (ciclo, Eu); None = não deu para saber
    reparo: date | None = None
    feitas_por_categoria: dict[str, int] = field(default_factory=dict)
    evento_proximo: bool = False         # algo marcado para hoje ou amanhã (o Hoje já mostra como Importante)


def _registros_de_outros_recursos(banco: Any) -> set[int] | None:
    """Os registros que outros recursos (o ciclo, a área Eu) deixam fora da IA: o cartão não os
    cita nem usa como gancho (o Hoje nunca explica nada pelo ciclo). None = não deu para saber."""
    if falhas():
        return None
    fora: set[int] = set()
    for nome, funcao in com_gancho("fora_da_ia"):
        if nome == NOME:
            continue
        try:
            fora |= {int(i) for i in (funcao(banco) or ()) if not isinstance(i, bool)}
        except Exception as erro:  # noqa: BLE001 - na dúvida, nada de gancho nem citação
            registrar_erro(erro, NOME, "fora_da_ia de outro recurso")
            return None
    return fora


def _contexto(banco: Any, ref: date) -> Contexto:
    linhas = _linhas(banco)
    contagem = {l["categoria"]: l["n"] for l in banco.consultar(
        "SELECT je.value AS categoria, COUNT(DISTINCT i.registro_id) AS n FROM itens i, json_each(i.categorias) je"
        " WHERE i.status = 'ativo' GROUP BY je.value")}
    recentes = banco.consultar(
        "SELECT i.id, i.registro_id, i.tipo, i.categorias, i.trecho, i.resumo, i.pessoa, i.pessoa_id, i.certeza,"
        " i.polaridade, i.data_evento, r.data_registro FROM itens i JOIN registros r ON r.id = i.registro_id"
        " WHERE i.status = 'ativo' AND r.data_registro >= ? AND r.data_registro <= ?"
        " ORDER BY r.data_registro DESC, i.id DESC LIMIT 500",
        ((ref - timedelta(days=DIAS_GANCHO)).isoformat(), ref.isoformat()))
    for item in recentes:
        item["_data"] = _d(item["data_registro"])
        item["_categorias"] = _json_lista(item["categorias"])
        item["_texto"] = normalizar(f"{item['trecho']} {item['resumo']}")
    fora = _registros_de_outros_recursos(banco)
    apoio = banco.consultar("SELECT COUNT(*) AS n FROM itens WHERE status = 'ativo' AND tipo = 'apoio'"
                            " AND pessoa_id IS NULL AND (pessoa IS NULL OR pessoa = '')")[0]["n"]
    feitas: dict[str, int] = {}
    for l in linhas:
        if l["status"] == "feita":
            feitas[l["categoria"]] = feitas.get(l["categoria"], 0) + 1
    evento_proximo = bool(banco.consultar(
        "SELECT 1 FROM itens WHERE status = 'ativo' AND tipo = 'evento' AND data_evento >= ? AND data_evento <= ?"
        " AND (precisao IS NULL OR precisao = 'dia') LIMIT 1", (ref.isoformat(), (ref + timedelta(days=1)).isoformat())))
    return Contexto(ref, _ajustes(banco), linhas, banco.consultar("SELECT COUNT(*) AS n FROM registros")[0]["n"],
                    contagem, recentes, _momento(banco, ref, recentes, fora), apoio > 0, fora,
                    _reparo_recente(banco, ref), feitas, evento_proximo)


def _momento(banco: Any, ref: date, recentes: list[dict], fora: set[int] | None) -> Momento:
    """Sensível pelo estado mais recente dela (como o Hoje: se o mais recente é bom, não é), ou por
    um evento importante (de qualquer pessoa) hoje ou amanhã."""
    momento = Momento()
    estados = []
    for item in recentes:
        if item["tipo"] != "estado" or not _dela(item):
            continue
        quando = _d(item["data_evento"]) or item["_data"]
        if quando is not None and quando <= ref and (ref - quando).days <= DIAS_SENSIVEL:
            estados.append((quando, item["id"], item))
    if estados:
        quando, _, item = max(estados, key=lambda x: (x[0], x[1]))
        citavel = fora is not None and item["registro_id"] not in fora
        texto = f"{_capital(_quando(quando, ref))} você registrou: “{item['resumo'].strip().rstrip('.')}”."
        if (item["polaridade"] or 0) < 0:
            momento = Momento(True, "estado", texto if citavel else "Ela anda num momento mais sensível.",
                              (item["registro_id"],) if citavel else ())
        elif (item["polaridade"] or 0) > 0 and citavel:
            momento.animada = Motivo(texto, 6, (item["registro_id"],))
    if not momento.sensivel:
        eventos = banco.consultar(
            "SELECT registro_id, data_evento FROM itens WHERE status = 'ativo' AND tipo = 'evento'"
            " AND importancia >= 3 AND data_evento >= ? AND data_evento <= ? ORDER BY data_evento, id",
            (ref.isoformat(), (ref + timedelta(days=1)).isoformat()))
        if eventos:
            e = eventos[0]
            citavel = fora is not None and e["registro_id"] not in fora
            dia = "Hoje" if _d(e["data_evento"]) == ref else "Amanhã"
            momento = Momento(True, "evento", f"{dia} é um dia importante." if citavel
                              else "Ela anda num momento mais sensível.",
                              (e["registro_id"],) if citavel else (), momento.animada)
    return momento


def _reparo_recente(banco: Any, ref: date) -> date | None:
    """Um desentendimento anotado na área Eu nos últimos 30 dias (tabela `reparos`, seção 20.9.7)
    puxa "Quando a gente discorda, o que eu poderia fazer diferente?". Sem a tabela, nada."""
    try:
        if not banco.consultar("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'reparos'"):
            return None
        linhas = banco.consultar("SELECT MAX(data) AS data FROM reparos WHERE data <= ?", (ref.isoformat(),))
    except Exception:  # noqa: BLE001 - a tabela é de outro recurso: forma diferente = sem gancho
        return None
    quando = _d(linhas[0]["data"]) if linhas else None
    return quando if quando and (ref - quando).days <= 30 else None


# ================================================================ a escolha (pontos explicáveis)

@dataclass
class Candidata:
    pergunta: Pergunta
    pontos: float
    motivos: list[Motivo]


def _ultima(ctx: Contexto) -> tuple[str | None, str | None]:
    """A categoria e a trilha da última pergunta perguntada, sugerida ou pulada (para variar)."""
    melhor: tuple[date, int, dict] | None = None
    for l in ctx.linhas:
        quando = max((d for d in (_d(l["feita_em"]), _d(l["sugerida_em"]), _d(l["pulada_em"])) if d), default=None)
        if quando and (melhor is None or (quando, l["id"]) > melhor[:2]):
            melhor = (quando, l["id"], l)
    return (melhor[2]["categoria"], melhor[2]["trilha"]) if melhor else (None, None)


def _disponivel(p: Pergunta, ctx: Contexto) -> bool:
    """Nunca repetir (as de acompanhamento só depois de `repete_dias`); a categoria que ela
    preferiu não responder descansa; a pergunta pulada ou guardada não volta como sugestão."""
    ref = ctx.referencia
    descansa_ate = ref - timedelta(days=DIAS_RECUSA)
    for l in ctx.linhas:
        if l["recusou"] and l["categoria"] == p.categoria and (_d(l["feita_em"]) or ref) > descansa_ate:
            return False
        if l["pergunta_id"] != p.id:
            continue
        if l["status"] in ("guardada", "pulada") or _aberta(l, ref):
            return False
        if l["status"] == "feita":
            feita = _d(l["feita_em"])
            if p.repete_dias is None or feita is None or (ref - feita).days < p.repete_dias:
                return False
    return True


def _gancho(p: Pergunta, ctx: Contexto) -> Motivo | None:
    """O que ela contou nos últimos 14 dias que puxa esta pergunta: a categoria do item (trabalho
    puxa Futuro e Valores) e, mais forte, as palavras da própria pergunta. Só registros que podem
    ser citados (nada do ciclo nem da área Eu)."""
    if ctx.fora is None:
        return None
    melhor: Motivo | None = None
    for item in ctx.recentes:
        if item["registro_id"] in ctx.fora or item["_data"] is None:
            continue
        dias = (ctx.referencia - item["_data"]).days
        recencia = 20 if dias <= 2 else 16 if dias <= 6 else 10
        frase, pontos = None, 0
        for cat in item["_categorias"]:
            puxa = GANCHOS.get(cat)
            if puxa and p.categoria in puxa[0]:
                frase, pontos = puxa[1], recencia
                break
        if p.termos and any(t in item["_texto"] for t in p.termos):
            pontos = (pontos or recencia // 2) + 8
            frase = frase or next((GANCHOS[c][1] for c in item["_categorias"] if c in GANCHOS),
                                  "de algo ligado a esta pergunta")
        if not pontos or (melhor and melhor.pontos >= pontos):
            continue
        # O tempo vem antes ("Anteontem ela falou de..."): depois da frase, "anteontem" parecia dizer
        # quando os planos chegavam (revisão de 07/10).
        quando = _capital(_quando(item["_data"], ctx.referencia))
        texto = (f"{quando} ela falou {frase}." if item["certeza"] == Certeza.CONFIRMADO.value
                 else f"{quando} você registrou algo {frase}.")
        melhor = Motivo(texto, pontos, (item["registro_id"],))
    return melhor


def _ultimo_dia_do_mes(ref: date) -> int:
    proximo = date(ref.year + 1, 1, 1) if ref.month == 12 else date(ref.year, ref.month + 1, 1)
    return (proximo - timedelta(days=1)).day


def _avaliar(p: Pergunta, ctx: Contexto, ultima: tuple[str | None, str | None]) -> Candidata:
    """Os pontos de uma pergunta, cada um com o seu porquê."""
    cat = categoria(p)
    ref = ctx.referencia
    motivos: list[Motivo] = []
    if cat.trilha == "nos_dois":
        feitas = [d for l in ctx.linhas if l["trilha"] == "nos_dois" and l["status"] == "feita"
                  and (d := _d(l["feita_em"])) and (ref - d).days <= 60]
        if not feitas:
            motivos.append(Motivo("Você ainda não registrou o que ela pensa sobre vocês dois nos últimos meses.", 20))
        if ref.day > _ultimo_dia_do_mes(ref) - JANELA_FIM_DO_MES and not any(
                (d.year, d.month) == (ref.year, ref.month) for d in feitas):
            motivos.append(Motivo(FIM_DO_MES, 25))
        if ctx.reparo and any(t in ("brig", "discut", "discord") for t in p.termos):
            motivos.append(Motivo(f"Houve um desentendimento anotado em {ctx.reparo:%d/%m}; talvez seja bom "
                                  "ouvir o lado dela, com calma.", 15))
        if any(l["pergunta_id"] == p.id and l["status"] == "feita" for l in ctx.linhas):
            motivos.append(Motivo("", -5))                       # prefere as que ainda não foram feitas
        if ultima[1] == "nos_dois":
            motivos.append(Motivo("", -15))
        if sum(ctx.feitas_por_categoria.values()) < 3:
            motivos.append(Motivo("", {1: 6, 2: 2, 3: -6}[p.nivel]))   # começar leve também aqui
    else:
        n = sum(ctx.contagem.get(c, 0) for c in cat.categorias_app) + 2 * ctx.feitas_por_categoria.get(p.categoria, 0)
        pontos = round(30 * (1 - min(n, 8) / 8))
        if n == 0:
            motivos.append(Motivo(f"Você ainda não registrou nada sobre {cat.sobre}.", pontos))
        elif n <= 3:
            motivos.append(Motivo(f"Você ainda sabe pouco sobre {cat.sobre}.", pontos))
        elif pontos:
            motivos.append(Motivo(f"Ainda dá para conhecer mais sobre {cat.sobre}.", pontos))
        if ctx.momento.animada and p.categoria in ("sonhos", "futuro"):
            motivos.append(ctx.momento.animada)
        feitas = sum(ctx.feitas_por_categoria.values())
        if feitas < 3:
            motivos.append(Motivo("Para começar, uma pergunta mais leve." if p.nivel == 1 else "",
                                  {1: 6, 2: 2, 3: -6}[p.nivel]))
        elif feitas >= 8 and p.nivel == 3:
            motivos.append(Motivo("", 4))
        if ultima[0] == p.categoria:
            motivos.append(Motivo("", -10))                      # para variar
        puladas = sum(1 for l in ctx.linhas if l["categoria"] == p.categoria and l["status"] == "pulada"
                      and _d(l["pulada_em"]) and (ref - _d(l["pulada_em"])).days <= DIAS_PULADAS)
        if puladas >= 2:
            motivos.append(Motivo("", -12))                      # você vem pulando esta categoria
    gancho = _gancho(p, ctx)
    if gancho:
        motivos.insert(0, gancho)
        # Com algo recente sobre o assunto, "você ainda não registrou nada sobre..." se contradiria.
        nada = f"Você ainda não registrou nada sobre {cat.sobre}."
        motivos = [Motivo(f"Ainda dá para conhecer mais sobre {cat.sobre}.", m.pontos, m.registro_ids)
                   if m.texto == nada else m for m in motivos]

    if any(l["pergunta_id"] == p.id and l["status"] == "sugerida" and not _aberta(l, ref) for l in ctx.linhas):
        motivos.append(Motivo("", -8))                           # já foi sugerida e ficou sem resposta
    # Desempate estável que muda a cada semana: perguntas empatadas não saem sempre na mesma ordem.
    desempate = ((p.id * 2654435761) ^ (ref.toordinal() // 7)) % 997 / 1000
    return Candidata(p, sum(m.pontos for m in motivos) + desempate, [m for m in motivos if m.texto])


def _trilhas(ctx: Contexto) -> tuple[str, ...]:
    return ("profundas", "nos_dois") if ctx.ajustes[CHAVE_NOS_DOIS] else ("profundas",)


def _candidatas(ctx: Contexto, trilhas: tuple[str, ...]) -> list[Candidata]:
    """As perguntas disponíveis, da melhor para a pior."""
    ref = ctx.referencia
    ultima = _ultima(ctx)
    nos_dois_recente = any(
        l["trilha"] == "nos_dois" and l["status"] in ("feita", "sugerida")
        and max((d for d in (_d(l["feita_em"]), _d(l["sugerida_em"])) if d), default=date.min)
        > ref - timedelta(days=DIAS_NOS_DOIS) for l in ctx.linhas)
    saida = []
    for p in PERGUNTAS:
        t = trilha(p)
        if t in trilhas and not (t == "nos_dois" and nos_dois_recente) and _disponivel(p, ctx):
            saida.append(_avaliar(p, ctx, ultima))
    saida.sort(key=lambda c: (-c.pontos, c.pergunta.id))
    return saida


def _escolher_apoio(ctx: Contexto, excluir: Iterable[int] = ()) -> Pergunta | None:
    """Uma pergunta de cuidado: a feita há mais tempo (ou nunca), sem as puladas nestes dias;
    entre as empatadas, uma por dia."""
    ref = ctx.referencia
    excluir = set(excluir)
    ultimas: dict[int, date] = {}
    for l in ctx.linhas:
        if l["trilha"] != "apoio":
            continue
        pulada, feita = _d(l["pulada_em"]), _d(l["feita_em"])
        if l["status"] == "pulada" and pulada and (ref - pulada).days <= DIAS_SENSIVEL:
            excluir.add(l["pergunta_id"])
        if l["status"] == "feita" and feita:
            ultimas[l["pergunta_id"]] = max(feita, ultimas.get(l["pergunta_id"], date.min))
    opcoes = [p for p in PERGUNTAS if trilha(p) == "apoio" and p.id not in excluir
              and (p.id not in ultimas or (ref - ultimas[p.id]).days >= (p.repete_dias or 14))]
    if not opcoes:
        return None
    rodar = ref.toordinal() % len(opcoes)
    posicao = {p.id: (i - rodar) % len(opcoes) for i, p in enumerate(opcoes)}
    return min(opcoes, key=lambda p: (ultimas.get(p.id, date.min), posicao[p.id]))


# ================================================================ o cartão de agora

@dataclass
class Atual:
    """O cartão de agora: a pergunta, como ele aparece e por quê."""
    pergunta: Pergunta
    estado: str                          # "sugerida" | "guardada" | "nova" | "apoio"
    motivos: list[Motivo]
    linha: dict | None = None
    relevancia: int = RELEVANCIA_SUGESTAO

    @property
    def registro_ids(self) -> list[int]:
        return list(dict.fromkeys(r for m in self.motivos for r in m.registro_ids))

    @property
    def dados(self) -> dict:
        p, cat = self.pergunta, categoria(self.pergunta)
        return {"pergunta_id": p.id, "trilha": cat.trilha, "categoria": cat.id, "categoria_nome": cat.nome,
                "emoji": cat.emoji, "titulo": {"nos_dois": "Nós dois", "apoio": "Com cuidado"}.get(cat.trilha,
                                                                                                    "Conheça melhor"),
                "pergunta": p.texto, "nivel": p.nivel, "estado": self.estado,
                "linha_id": self.linha["id"] if self.linha else None, "texto_registrar": texto_para_registrar(p),
                "motivos": [{"texto": m.texto, "registro_ids": list(m.registro_ids)} for m in self.motivos if m.texto]}


def _relevancia(c: Candidata) -> int:
    return RELEVANCIA_FIM_DO_MES if any(m.texto == FIM_DO_MES for m in c.motivos) else RELEVANCIA_SUGESTAO


def _proxima_em(ctx: Contexto) -> date | None:
    """Quando cabe a próxima sugestão nova: `dias_entre_perguntas` depois da última sugerida ou feita."""
    marcos = [d for l in ctx.linhas if l["trilha"] != "apoio" for d in (_d(l["sugerida_em"]), _d(l["feita_em"])) if d]
    if not marcos:
        return None
    proxima = max(marcos) + timedelta(days=ctx.ajustes[CHAVE_DIAS])
    return proxima if proxima > ctx.referencia else None


def _atual(banco: Any, ctx: Contexto, materializar: bool) -> Atual | None:
    """O que o Hoje mostra agora (no máximo um cartão). Com `materializar`, a sugestão nova fica
    gravada como "sugerida" e vale igual nos próximos dias."""
    ref = ctx.referencia
    if ctx.registros < MIN_REGISTROS:
        return None
    if ctx.momento.sensivel:
        return _atual_apoio(ctx)
    if any(l["trilha"] != "apoio" and l["status"] in ("sugerida", "guardada") and (_d(l["adiada_ate"]) or ref) > ref
           for l in ctx.linhas):
        return None                                              # "Agora não": nada por uns dias
    guardadas = [l for l in ctx.linhas if l["status"] == "guardada" and pergunta(l["pergunta_id"])
                 and (d := _d(l["guardada_em"])) and 0 <= (ref - d).days <= VALIDADE_GUARDADA]
    if guardadas:
        l = max(guardadas, key=lambda x: (x["guardada_em"], x["id"]))
        motivo = Motivo(f"Você escolheu “Vou perguntar” {_quando(_d(l['guardada_em']), ref)}. Quando perguntar, "
                        "registre o que ela respondeu.", 0)
        return Atual(pergunta(l["pergunta_id"]), "guardada", [motivo], l, RELEVANCIA_GUARDADA)
    abertas = [l for l in ctx.linhas if l["trilha"] in _trilhas(ctx) and _aberta(l, ref) and pergunta(l["pergunta_id"])]
    if abertas:
        l = max(abertas, key=lambda x: (x["sugerida_em"], x["id"]))
        c = _avaliar(pergunta(l["pergunta_id"]), ctx, (None, None))
        return Atual(c.pergunta, "sugerida", c.motivos or [Motivo(SEM_MOTIVO, 0)], l, _relevancia(c))
    if _proxima_em(ctx) is not None:
        return None
    candidatas = _candidatas(ctx, _trilhas(ctx))
    if not candidatas:
        return None
    melhor = candidatas[0]
    motivos = melhor.motivos or [Motivo(SEM_MOTIVO, 0)]
    if not materializar:
        return Atual(melhor.pergunta, "nova", motivos, None, _relevancia(melhor))
    linha = _linha(banco, _materializar(banco, melhor.pergunta, ref, ctx.linhas))
    return Atual(melhor.pergunta, "sugerida", motivos, linha, _relevancia(melhor))


def _motivos_apoio(ctx: Contexto) -> list[Motivo]:
    return [Motivo(ctx.momento.texto or "Ela anda num momento mais sensível.", 0, ctx.momento.registro_ids),
            Motivo("Talvez não seja dia de perguntas profundas; se fizer sentido, algo leve. Você ainda não "
                   "registrou o que costuma ajudá-la nesses dias.", 0)]


def _atual_apoio(ctx: Contexto) -> Atual | None:
    """Momento sensível: nada de pergunta funda ("prefere apoio ou nada"). Uma pergunta de cuidado
    só quando você ainda não sabe o que a ajuda (sabendo, o Hoje já lembra disso) e não com algo
    marcado para hoje ou amanhã (o aviso Importante já está lá: um cartão a mais seria excesso). Uma
    por momento: perguntou ou disse "Agora não", fica quieto."""
    ref = ctx.referencia
    if ctx.momento.tipo != "estado" or ctx.apoio_conhecido or ctx.evento_proximo:
        return None
    for l in ctx.linhas:
        if l["trilha"] != "apoio":
            continue
        feita = _d(l["feita_em"])
        if (feita and 0 <= (ref - feita).days <= DIAS_SENSIVEL) or (_d(l["adiada_ate"]) or ref) > ref:
            return None
    p = _escolher_apoio(ctx)
    return Atual(p, "apoio", _motivos_apoio(ctx), None, RELEVANCIA_APOIO) if p else None


def _cartao(atual: Atual, ref: date) -> dict:
    p = atual.pergunta
    if atual.estado == "guardada":
        texto, chave = f"Você guardou esta pergunta para fazer a ela: “{p.texto}”", f"guardada:{atual.linha['id']}"
    elif atual.estado == "apoio":
        texto, chave = f"Talvez, com carinho: “{p.texto}”", f"apoio:{p.id}:{ref.isoformat()}"
    else:
        texto = f"Talvez perguntar a ela: “{p.texto}”"
        # A mesma chave antes e depois de a sugestão ser gravada (no dia em que aparece pela primeira
        # vez, `sugerida_em` é esse dia): dispensar o cartão "novo" vale para o gravado também.
        desde = (_d(atual.linha.get("sugerida_em")) if atual.linha else None) or ref
        chave = f"sugestao:p{p.id}:{desde.isoformat()}"
    return cartao(chave, "sugestao", texto, " ".join(m.texto for m in atual.motivos if m.texto), atual.relevancia,
                  registro_ids=atual.registro_ids, tipo="pergunta", dados=atual.dados)


def cartoes_hoje(banco: Any, referencia: date) -> list[dict]:
    """No máximo um cartão: a pergunta guardada, a sugerida destes dias, uma nova (se já passou o
    espaço entre sugestões) ou, num momento sensível, uma de cuidado (ou nada). Não grava nada: a
    pergunta nova só vira "sugerida" quando o cartão aparece de verdade (`cartoes_mostrados`; o
    anti-excesso central pode deixá-lo de fora num dia cheio)."""
    atual = _atual(banco, _contexto(banco, referencia), materializar=False)
    return [_cartao(atual, referencia)] if atual else []


def cartoes_mostrados(banco: Any, referencia: date, cartoes: list[dict]) -> None:
    """O cartão apareceu no Hoje (integração, 20.10): a pergunta nova passa a valer como "sugerida"
    (o espaço entre sugestões conta daqui, e ela fica igual nos próximos dias)."""
    if any((c.get("dados") or {}).get("estado") == "nova" for c in cartoes):
        _atual(banco, _contexto(banco, referencia), materializar=True)


def sugestao_atual(banco: Any, referencia: date) -> dict | None:
    """Para outros recursos (o resumo da semana, a área Eu): o cartão de agora, sem gravar nada.
    {pergunta_id, trilha, categoria, pergunta, estado, motivos: [{texto, registro_ids}], ...} ou None."""
    atual = _atual(banco, _contexto(banco, referencia), materializar=False)
    return atual.dados if atual else None


# ================================================================ rotas (/api/perguntas/...)

def _hoje(nucleo: Any) -> date:
    hoje = getattr(nucleo, "hoje", None)
    try:
        valor = hoje() if callable(hoje) else None
    except Exception:  # noqa: BLE001
        valor = None
    return valor if isinstance(valor, date) else date.today()


def _da_rota(pedido: Any) -> Pergunta:
    p = pergunta(pedido.id)
    if p is None:
        from ..banco import NaoEncontrado
        raise NaoEncontrado(PERGUNTA_DESCONHECIDA)
    return p


def _origem(pedido: Any) -> str:
    valor = pedido.corpo.get("origem")
    return valor if valor in ("sugestao", "tela") else "tela"


def _depois(banco: Any, ref: date, **mais: Any) -> dict:
    """Resposta das ações: o cartão como fica agora (o Hoje troca o cartão sem recarregar)."""
    atual = _atual(banco, _contexto(banco, ref), materializar=True)
    return {"cartao": _cartao(atual, ref) if atual else None, **mais}


def _rota_visao(nucleo: Any, pedido: Any, banco: Any) -> dict:
    return visao_geral(banco, _hoje(nucleo))


def _rota_guardar(nucleo: Any, pedido: Any, banco: Any) -> dict:
    ref, p = _hoje(nucleo), _da_rota(pedido)
    with banco.transacao():
        linha = _marcar_guardada(banco, p, ref, origem=_origem(pedido))
        return _depois(banco, ref, linha_id=linha)


def _rota_perguntei(nucleo: Any, pedido: Any, banco: Any) -> dict:
    ref, p = _hoje(nucleo), _da_rota(pedido)
    with banco.transacao():
        linha = _marcar_feita(banco, p, ref, origem=_origem(pedido), feita_por="botao")
        return _depois(banco, ref, linha_id=linha, texto_registrar=texto_para_registrar(p))


def _rota_pular(nucleo: Any, pedido: Any, banco: Any) -> dict:
    ref, p = _hoje(nucleo), _da_rota(pedido)
    with banco.transacao():
        linha = _marcar_pulada(banco, p, ref, _origem(pedido))
        return _depois(banco, ref, linha_id=linha)


def _rota_agora_nao(nucleo: Any, pedido: Any, banco: Any) -> dict:
    ref, p = _hoje(nucleo), _da_rota(pedido)
    dias = DIAS_SENSIVEL if trilha(p) == "apoio" else _ajustes(banco)[CHAVE_DIAS]
    _adiar(banco, p, ref, dias)
    return {"cartao": None, "volta_em": ref + timedelta(days=max(1, dias))}


def _rota_voltar(nucleo: Any, pedido: Any, banco: Any) -> dict:
    """"Voltar para a lista": a pergunta deixa de estar sugerida, guardada ou pulada (as feitas
    ficam; para esquecer uma feita, apague a linha)."""
    p = _da_rota(pedido)
    banco.executar(f"DELETE FROM {TABELA} WHERE pergunta_id = ? AND status IN ('sugerida', 'guardada', 'pulada')",
                   (p.id,))
    return {}


def _rota_outra(nucleo: Any, pedido: Any, banco: Any) -> dict:
    """"Outra": a de agora vai para as puladas e vem outra já (sem esperar o espaço entre sugestões)."""
    ref = _hoje(nucleo)
    atual_id = pedido.corpo.get("pergunta_id")
    p = pergunta(atual_id) if atual_id is not None else None
    if atual_id is not None and p is None:
        raise ValueError(PERGUNTA_DESCONHECIDA)
    with banco.transacao():
        if p is not None:
            _marcar_pulada(banco, p, ref)
        ctx = _contexto(banco, ref)
        if ctx.momento.sensivel:
            nova = _escolher_apoio(ctx, {p.id} if p else ()) if ctx.momento.tipo == "estado" else None
            if nova is None:
                return {"cartao": None}
            return {"cartao": _cartao(Atual(nova, "apoio", _motivos_apoio(ctx), None, RELEVANCIA_APOIO), ref)}
        candidatas = _candidatas(ctx, _trilhas(ctx))
        if not candidatas:
            return {"cartao": None}
        melhor = candidatas[0]
        linha = _linha(banco, _materializar(banco, melhor.pergunta, ref, ctx.linhas))
    atual = Atual(melhor.pergunta, "sugerida", melhor.motivos or [Motivo(SEM_MOTIVO, 0)], linha, _relevancia(melhor))
    return {"cartao": _cartao(atual, ref)}


def _rota_esquecer(nucleo: Any, pedido: Any, banco: Any) -> dict:
    """Apaga uma linha ("esquecer que perguntei"): a resposta guardada sai também das cópias."""
    linha_id = pedido.id
    banco.apagar(TABELA, linha_id)
    nucleo.limpar_copias(lambda copia: copia.apagar(TABELA, linha_id))
    return {}


def _rota_respostas(nucleo: Any, pedido: Any, banco: Any) -> list[dict]:
    qual = pedido.parametro("trilha")
    if qual is not None and qual not in CATEGORIAS and qual not in TRILHAS:
        raise ValueError("Trilha desconhecida: use profundas, nos_dois, apoio ou uma categoria.")
    try:
        desde = date.fromisoformat(pedido.parametro("desde")) if pedido.parametro("desde") else None
        ate = date.fromisoformat(pedido.parametro("ate")) if pedido.parametro("ate") else None
    except ValueError:
        raise ValueError("Data inválida: use o formato AAAA-MM-DD.") from None
    return respostas(banco, trilha=qual, desde=desde, ate=ate)


def rotas() -> list:
    return [
        rota("GET", "", _rota_visao),
        rota("GET", "respostas", _rota_respostas),
        rota("POST", "outra", _rota_outra),
        rota("POST", "{id}/guardar", _rota_guardar),
        rota("POST", "{id}/perguntei", _rota_perguntei),
        rota("POST", "{id}/pular", _rota_pular),
        rota("POST", "{id}/agora-nao", _rota_agora_nao),
        rota("POST", "{id}/voltar", _rota_voltar),
        rota("DELETE", "linhas/{id}", _rota_esquecer),
    ]


# ================================================================ a tela #perguntas e a leitura dos outros recursos

def _vista(l: dict) -> dict:
    cat = CATEGORIAS.get(l["categoria"])
    return {"linha_id": l["id"], "pergunta_id": l["pergunta_id"], "pergunta": l["pergunta"], "trilha": l["trilha"],
            "categoria": l["categoria"], "categoria_nome": cat.nome if cat else l["categoria"],
            "emoji": cat.emoji if cat else "💬", "status": l["status"], "sugerida_em": l["sugerida_em"],
            "guardada_em": l["guardada_em"], "feita_em": l["feita_em"], "registro_id": l["registro_id"],
            "resposta": l["resposta"], "certeza": l["certeza"], "recusou": bool(l["recusou"])}


def _estado_da_pergunta(linhas: list[dict], ref: date) -> str | None:
    status = {l["status"] for l in linhas if l["status"] != "sugerida" or _aberta(l, ref)}
    return next((s for s in ("feita", "guardada", "sugerida", "pulada") if s in status), None)


def visao_geral(banco: Any, ref: date) -> dict:
    """GET /api/perguntas: tudo o que a tela #perguntas mostra (nada é gravado)."""
    ctx = _contexto(banco, ref)
    atual = _atual(banco, ctx, materializar=False)
    por_pergunta: dict[int, list[dict]] = {}
    for l in ctx.linhas:
        por_pergunta.setdefault(l["pergunta_id"], []).append(l)
    categorias = []
    for cat in CATEGORIAS.values():
        perguntas = [{"id": p.id, "texto": p.texto, "nivel": p.nivel,
                      "estado": _estado_da_pergunta(por_pergunta.get(p.id, []), ref),
                      "feitas": [_vista(l) for l in por_pergunta.get(p.id, []) if l["status"] == "feita"]}
                     for p in PERGUNTAS if p.categoria == cat.id]
        categorias.append({"id": cat.id, "nome": cat.nome, "emoji": cat.emoji, "trilha": cat.trilha, "sobre": cat.sobre,
                           "total": len(perguntas), "feitas": sum(1 for x in perguntas if x["feitas"]),
                           "perguntas": perguntas})
    feitas = sorted((l for l in ctx.linhas if l["status"] == "feita"), key=lambda l: (l["feita_em"] or "", l["id"]),
                    reverse=True)
    guardadas = sorted((l for l in ctx.linhas if l["status"] == "guardada"),
                       key=lambda l: (l["guardada_em"] or "", l["id"]), reverse=True)
    return {
        "referencia": ref,
        "cartao": atual.dados if atual else None,
        "momento": {"sensivel": ctx.momento.sensivel, "texto": ctx.momento.texto if ctx.momento.sensivel else None},
        "proxima_em": _proxima_em(ctx) if atual is None else None,
        "registros": ctx.registros,
        "minimo_registros": MIN_REGISTROS,
        "guardadas": [_vista(l) for l in guardadas],
        "feitas": [_vista(l) for l in feitas],
        "categorias": categorias,
        "ajustes": dict(ctx.ajustes),
    }


def respostas(banco: Any, *, trilha: str | None = None, desde: date | None = None, ate: date | None = None) -> list[dict]:
    """As perguntas feitas, mais novas primeiro: {linha_id, pergunta_id, pergunta, trilha, categoria,
    feita_em, registro_id, resposta (trecho literal do registro, ou None), certeza, recusou}.
    `trilha`: "profundas" | "nos_dois" | "apoio" ou o id de uma categoria. Datas pela `feita_em`."""
    condicoes, parametros = ["status = 'feita'"], []
    if trilha in TRILHAS:
        condicoes.append("trilha = ?")
        parametros.append(trilha)
    elif trilha is not None:
        condicoes.append("categoria = ?")
        parametros.append(trilha)
    if desde is not None:
        condicoes.append("feita_em >= ?")
        parametros.append(desde.isoformat())
    if ate is not None:
        condicoes.append("feita_em <= ?")
        parametros.append(ate.isoformat())
    linhas = banco.consultar(f"SELECT * FROM {TABELA} WHERE {' AND '.join(condicoes)} ORDER BY feita_em DESC, id DESC",
                             tuple(parametros))
    return [_vista(l) for l in linhas]


def respostas_nos_dois(banco: Any, desde: date | None = None, ate: date | None = None) -> list[dict]:
    """Para a área 🪞 Eu (seção 20.9, item 5): o que ela disse sobre vocês dois, para ficar ao lado
    do seu check-in. A `resposta` é um trecho literal do registro (🟢 dela quando veio de "Ela
    respondeu:"); `registro_id` abre o contexto."""
    return respostas(banco, trilha="nos_dois", desde=desde, ate=ate)


# ================================================================ exportar, importar, apagar, IA

_COLUNAS = ("pergunta_id", "trilha", "categoria", "pergunta", "status", "origem", "sugerida_em", "guardada_em",
            "feita_em", "feita_por", "pulada_em", "adiada_ate", "registro_id", "resposta", "certeza", "recusou",
            "criado_em")


def exportar(banco: Any) -> dict:
    return {"versao": 1, "perguntas_feitas": [{c: l[c] for c in ("id", *_COLUNAS)} for l in _linhas(banco)]}


def _data_texto(valor: Any) -> str | None:
    d = _d(valor) if isinstance(valor, str) else None
    return d.isoformat() if d else None


def _ja_tem(banco: Any, p: Pergunta, v: dict) -> bool:
    for l in _linhas(banco, p.id):
        if l["status"] != v["status"]:
            continue
        if v["status"] in ("guardada", "pulada"):
            return True
        if v["registro_id"] is not None and l["registro_id"] == v["registro_id"]:
            return True
        if v["registro_id"] is None and l["feita_em"] == v["feita_em"]:
            return True
    return False


def importar(banco: Any, dados: Any, mapa_registros: dict) -> dict:
    """Junta sem duplicar. Sugestões em aberto do outro aparelho não vêm (são do momento de lá);
    pergunta que este app não conhece fica de fora; a resposta só vem se for literal do registro
    que veio junto."""
    if not isinstance(dados, dict) or not isinstance(dados.get("perguntas_feitas"), list):
        raise ValueError("Dados de perguntas em formato desconhecido.")
    mapa: dict[int, int] = {}
    for k, v in (mapa_registros or {}).items():
        try:
            mapa[int(k)] = int(v)
        except (TypeError, ValueError):
            continue
    novos = 0
    for bruto in dados["perguntas_feitas"]:
        if not isinstance(bruto, dict):
            continue
        p = pergunta(bruto.get("pergunta_id"))
        status = bruto.get("status")
        if p is None or status not in ("guardada", "feita", "pulada"):
            continue
        rid = bruto.get("registro_id")
        rid = mapa.get(rid) if isinstance(rid, int) and not isinstance(rid, bool) else None
        resposta = bruto.get("resposta") if rid is not None else None
        resposta = resposta.strip()[:MAX_RESPOSTA].strip() if isinstance(resposta, str) else ""
        if resposta:
            texto = banco.consultar("SELECT texto FROM registros WHERE id = ?", (rid,))
            if not texto or resposta not in texto[0]["texto"]:
                resposta = ""
        valores = {
            "status": status, "origem": bruto.get("origem") if bruto.get("origem") in ORIGENS else "tela",
            "sugerida_em": _data_texto(bruto.get("sugerida_em")), "guardada_em": _data_texto(bruto.get("guardada_em")),
            "feita_em": _data_texto(bruto.get("feita_em")), "pulada_em": _data_texto(bruto.get("pulada_em")),
            "feita_por": bruto.get("feita_por") if bruto.get("feita_por") in ("botao", "registro") else None,
            "registro_id": rid, "resposta": resposta or None,
            "certeza": bruto.get("certeza") if resposta and bruto.get("certeza") in ("confirmado", "observado",
                                                                                    "inferencia") else None,
            "recusou": 1 if bruto.get("recusou") in (True, 1) and not isinstance(bruto.get("recusou"), float) else 0,
        }
        if _ja_tem(banco, p, valores):
            continue
        banco.inserir(TABELA, _valores(p, **valores), historico=False)
        novos += 1
    return {"novos": novos, "texto": f"{novos} {'pergunta' if novos == 1 else 'perguntas'} (feitas, guardadas ou puladas)"}


def ao_apagar_registro(banco: Any, registro_id: int) -> None:
    """Apagar é apagar: a resposta (trecho do registro) e a data que veio dele saem. A pergunta
    feita continua "já perguntada" (sem data, sem resposta), para não ser sugerida de novo; o
    "Vou perguntar" que só existia por causa do registro sai com ele (o banco apaga a linha)."""
    for l in banco.consultar(f"SELECT * FROM {TABELA} WHERE registro_id = ?", (registro_id,)):
        if l["status"] != "feita":
            continue                                    # sai com o registro (linhas com registro_id)
        campos: dict[str, Any] = {"registro_id": None, "resposta": None, "certeza": None, "recusou": 0}
        if l["feita_por"] == "registro":
            campos["feita_em"] = None
        banco.atualizar(TABELA, l["id"], campos, historico=False)


def fora_da_ia(banco: Any) -> set[int]:
    """Registros com resposta de "Nós dois": são da área Eu, e nada dela vai para a IA."""
    return {l["registro_id"] for l in banco.consultar(
        f"SELECT registro_id FROM {TABELA} WHERE trilha = 'nos_dois' AND status = 'feita' AND registro_id IS NOT NULL")}


__all__ = [
    "CATEGORIAS", "ESQUEMA", "ESQUEMA_VERSAO", "MIN_REGISTROS", "NOME", "PERGUNTAS", "ROTULO", "TABELA", "TIPOS_EXTRAS",
    "ao_apagar_registro", "cartoes_hoje", "exportar", "extrair", "fora_da_ia", "gravar_extras", "importar",
    "marca_privado", "pergunta", "respostas", "respostas_nos_dois", "rotas", "sugestao_atual", "texto_para_registrar",
    "visao_geral",
]
