"""Léxico das 32 categorias e classificação de um texto por palavras-chave.

Os termos estão normalizados (minúsculas, sem acento). Cada termo casa no começo de uma
palavra; se a última palavra do termo tem 6 letras ou mais, ele vale como raiz
("apresent" pega apresentação/apresentar); se tem menos, só a palavra inteira, com plural
opcional ("mae" pega mãe/mães, mas não "maestro").
"""
from __future__ import annotations

import re

from ..modelos import CATEGORIAS
from ..texto import normalizar

RAIZ_MINIMA = 6


def _t(lista: str) -> list[str]:
    termos: list[str] = []
    for bruto in lista.split(","):
        termo = normalizar(bruto)
        if termo and termo not in termos:
            termos.append(termo)
    return termos


LEXICO: dict[str, list[str]] = {
    "Família": _t("""família, familiar, parente, parentes, mãe, mamãe, pai, papai, pais, irmã, irmão,
        irmãs, irmãos, avó, avô, vó, vovó, bisavó, tia, tio, prima, primo, sobrinha, sobrinho,
        madrinha, padrinho, cunhada, cunhado, sogra, sogro, filha, filho, madrasta, padrasto,
        enteada, enteado, afilhada, afilhado, neta, neto, caçula, gêmea, gêmeo, irmandade,
        casa dos pais, casa da mãe, casa da avó, almoço de família, almoço de domingo,
        reunião de família, criação, primogênita"""),
    "Amigos": _t("""amiga, amigo, amizade, melhor amiga, melhor amigo, colega, colegas, turma, galera,
        parceira, parceiro, confidente, vizinha, vizinho, amiga de infância, grupo de amigas,
        as meninas, as amigas, os amigos, sair com as amigas, comadre, compadre, bff, migas,
        amigona, rolê com as amigas"""),
    "Trabalho": _t("""trabalh, emprego, desempregada, empresa, escritório, firma, chefe, gerente, patrão,
        patroa, colega de trabalho, reunião, reuniões, projeto, apresent, cliente, salário,
        promoção, promovida, aumento, demissão, demiti, contrato, estágio, entrevista de emprego,
        vaga, currículo, carreira, cargo, expediente, hora extra, home office, plantão, plantões,
        meta, prazo, relatório, planilha, equipe, liderança, líder, supervisora, coordenadora,
        diretora, recursos humanos, rh, freela, freelancer, negócio, deadline, turno,
        processo seletivo, feedback, avaliação de desempenho, burnout, clt, carteira assinada,
        profissão, profissional, sindicato, loja, atendimento, escala"""),
    "Estudos": _t("""estuda, estudo, faculdade, facul, universidade, escola, colégio, curso, cursinho,
        aula, prova, provão, tcc, monografia, mestrado, doutorado, pós-graduação, pós graduação,
        vestibular, enem, concurso, matéria, professor, orientador, nota, boletim, semestre,
        diploma, matrícula, bolsa de estudos, intercâmbio, pesquisa, artigo, defesa, banca,
        apostila, simulado, trabalho da faculdade, idioma, lição, dever de casa, reprovou,
        aprovada, formatura"""),
    "Saúde": _t("""saúde, médico, médica, medicina, doutor, doutora, hospital, hospitais, clínica,
        consulta, exame, cirurgia, cirurgião, operação, operar, remédio, tratamento, terapia,
        terapeuta, psicolog, psiquiatra, dentista, dente, dor, dor de cabeça, enxaqueca, febre,
        gripe, gripada, resfriad, covid, doente, doença, internada, internação, pressão alta,
        diabetes, diabética, alergia, alérgica, alérgico, intolerância, intolerante, celíaca,
        depressão, insônia, gravid, fisioterapia, fisioterapeuta, dieta, nutricionista, vacina,
        ressonância, ultrassom, raio-x, biópsia, câncer, tumor, quimioterapia, fratura, machuc,
        pronto-socorro, pronto socorro, plano de saúde, check-up, menstruação, cólica,
        endometriose, exame de sangue, pós-operatório, ginecologista, dermatologista,
        cardiologista, pediatra, ortopedista, upa, sus, sintoma, infecção, virose, labirintite,
        anemia"""),
    "Viagens": _t("""viagem, viagens, viaja, viajar, viajou, viajando, viajamos, férias, passagem,
        passagens, avião, aviões, voo, aeroporto, hotel, hotéis, pousada, airbnb, mala,
        passaporte, turismo, turista, roteiro, mochilão, cruzeiro, excursão, trilha, acampamento,
        exterior, lua de mel, bate e volta, road trip, fim de semana fora, praia, intercâmbio,
        destino"""),
    "Datas importantes": _t("""aniversário, niver, natal, véspera de natal, ano novo, réveillon,
        dia dos namorados, dia das mães, dia dos pais, páscoa, carnaval, mesversário, bodas,
        aniversário de namoro, formatura, casamento, batizado, feriado, comemor, data especial,
        data importante, todo ano, chá de bebê, chá de panela, chá revelação, noivado,
        dia das crianças, dia da mulher"""),
    "Preferências": _t("""gosta, gostar, gostou, gostava, adora, adorou, adorava, ama, amou, curte, curtiu,
        prefer, favorit, predilet, odeia, detesta, não gosta, não curte, não suporta,
        apaixonada por, fã de, louca por, tipo preferido, estilo"""),
    "Comidas": _t("""comida, comer, comeu, come, comendo, sushi, japones, comida japonesa, temaki,
        sashimi, pizza, hambúrguer, lanche, chocolate, doce, sorvete, bolo, brigadeiro, açaí,
        macarrão, lasanha, churrasco, carne, frango, peixe, camarão, camarões, frutos do mar,
        salada, fruta, morango, abacaxi, pão, queijo, feijoada, estrogonofe, strogonoff, batata,
        batata frita, coxinha, pastel, esfiha, tapioca, cuscuz, pipoca, restaurante, jantar,
        almoço, café da manhã, receita, cozinhar, culinária, glúten, lactose, amendoim, nutella,
        torta, pudim, mousse, ramen, yakisoba, comida mexicana, taco, burrito, cachorro-quente,
        picanha, arroz, feijão, ovo, salgado, pizzaria, hamburgueria, padaria, confeitaria,
        vegetarian, vegana, vegano, marmita, delivery, ifood, sobremesa, petisco, fondue, risoto,
        nhoque, empada, quibe, acarajé, moqueca, costela, costelinha, cupim, maminha, fraldinha,
        alcatra, bife, filé, contrafilé, parmegiana, milanesa, bacalhau, salmão, atum, tilápia, lagosta,
        polvo, ostra, panqueca, omelete, sopa, caldo, purê, polenta, mandioca, aipim, farofa,
        linguiça, torresmo, vatapá, baião de dois, galinhada, escondidinho, sanduíche, cheesecake,
        brownie, cookie, biscoito, bolacha, pão de queijo, churros, crepe, waffle, pad thai, curry,
        comida tailandesa, comida árabe, comida italiana, espaguete, talharim, ravioli, azeitona,
        uva passa, pepino, coentro, cebola, pimenta, tomate, brócolis, cenoura, berinjela, cogumelo,
        abacate, manga, banana, maçã, melancia, calabresa, frutos do mar, comida mineira"""),
    "Bebidas": _t("""bebida, beber, bebeu, bebe, café, chá, suco, refrigerante, refri, coca, cerveja,
        vinho, drink, drinque, caipirinha, gin, vodka, whisky, uísque, cappuccino, capuccino,
        latte, água de coco, energético, milkshake, smoothie, chopp, chope, espumante,
        champanhe, tequila, licor, matcha, chimarrão, achocolatado, cafeteria, bar, barzinho,
        happy hour"""),
    "Filmes": _t("""filme, cinema, assisti, ator, atriz, netflix, streaming, pipoca, comédia, romance,
        terror, drama, animação, disney, pixar, marvel, oscar, trailer, estreia, documentário,
        sessão, comédia romântica, prime video, hbo, suspense, ficção científica, dublado,
        legendado"""),
    "Séries": _t("""série, seriado, temporada, episódio, maratona, netflix, novela, dorama, anime,
        sitcom, reality, reality show, bbb, spoiler, capítulo, streaming, hbo, prime video,
        disney plus, próximo episódio, última temporada"""),
    "Música": _t("""música, cantor, cantar, cantando, cantou, banda, show, playlist, spotify, álbum,
        disco, festival, rock, pop, sertanejo, funk, mpb, samba, pagode, forró, rap, kpop, k-pop,
        jazz, violão, piano, guitarra, karaokê, clipe, lollapalooza, rock in rio, ouvir música,
        fone de ouvido, dançar, coral"""),
    "Livros": _t("""livro, ler, leitura, lendo, leu, autor, autora, romance, poesia, poema, biblioteca,
        livraria, kindle, ebook, e-book, saga, trilogia, capítulo, best-seller, literatura,
        escritor, clube do livro, quadrinhos, gibi, hq, página"""),
    "Hobbies": _t("""hobby, hobbies, passatempo, desenh, pintar, pintura, dançar, dança, balé, ballet,
        academia, treino, treinar, correr, corrida, yoga, ioga, pilates, crossfit, natação,
        nadar, bicicleta, bike, pedalar, fotograf, jardinagem, planta, crochê, tricô, bordado,
        costura, artesanato, videogame, game, games, jogar, cozinhar, confeitaria, caminhada,
        patinar, surf, skate, muay thai, jiu-jitsu, xadrez, quebra-cabeça, tocar violão,
        maquiagem, skincare, fazer as unhas"""),
    "Lugares": _t("""lugar, praia, parque, shopping, restaurante, bar, barzinho, cafeteria, cidade,
        bairro, apartamento, museu, teatro, cinema, igreja, praça, fazenda, sítio, chácara,
        montanha, serra, lago, cachoeira, mirante, feira, mercado, padaria, balada, clube, rua,
        casa de praia, interior, cidade natal, lugar favorito"""),
    "Presentes": _t("""presente, ganhou, ganhar, ganha, recebeu, receber, surpresa, surpreend, flor,
        buquê, tulipa, rosa, girassol, girassóis, orquídea, cartinha, carta, bilhete, joia, colar,
        brinco, anel, pulseira, perfume, pelúcia, urso de pelúcia, lembrancinha, mimo, caneca,
        vale-presente, cesta, cesta de café, kit, chocolate, fone novo, embrulho,
        lista de presentes"""),
    "Sonhos": _t("""sonho, sonha, sonhar, sonhava, sonhando, sonha em, sempre quis, um dia quer, um dia,
        gostaria muito, vontade de, tem vontade, morre de vontade, realiz, conhecer o mundo,
        morar fora, morar no exterior, ter filhos, casar, ter uma casa, abrir o próprio,
        viajar o mundo, lista de desejos, bucket list, desejo"""),
    "Objetivos": _t("""objetivo, meta, plano, planeja, pretend, conquist, foco, focada, juntar dinheiro,
        economiz, passar em, passar no, passar na, aprender, aprovação, se formar, tirar carta,
        carteira de motorista, mudar de emprego, emagrec, crescer na carreira,
        terminar a faculdade, abrir um negócio, empreend, conseguir"""),
    "Medos": _t("""medo, pavor, fobia, tem medo, com medo, assust, susto, pânico, crise de pânico,
        receio, morre de medo, barata, altura, escuro, agulha, falar em público, insegurança,
        insegura, solidão, abandono, rejeição, trauma, apavorad, aterrorizad, terror"""),
    "Preocupações": _t("""preocup, ansios, ansiedade, nervos, tensa, tenso, apreensiv, estress,
        angústia, angustiad, aflita, aflição, inquiet, insegura, triste, chatead, cansad,
        cansaço, exaust, esgotad, sobrecarreg, desanimad, frustrad, magoad, não dorme, insônia,
        pressão, problema, dificuldade, crise, abalad, deprimid, chorou, chorando, pra baixo,
        com medo"""),
    "Coisas que deixam feliz": _t("""feliz, felicidade, alegre, alegria, animad, empolgad, contente,
        radiante, sorriu, sorriso, sorrindo, riu, rindo, gargalhad, emocion, encantad, grata,
        gratidão, aliviad, orgulhos, realizada, ficou feliz, deixa feliz, fez o dia, diverti,
        diversão, amou, adorou, curtiu muito, derreteu, vibrou"""),
    "Coisas que incomodam": _t("""incomod, irrita, chateia, chatead, odeia, detesta, não suporta,
        não aguenta, não gosta, raiva, brava, tira do sério, perde a paciência, impaciente,
        atraso, atrasad, atrasar, barulho, grosseri, mentira, falta de respeito, deixa chateada,
        reclama, encheu o saco, saco cheio, nojo, tem nojo, injustiç, desrespeit, bagunça,
        ciúme"""),
    "Experiências importantes": _t("""primeira vez, formou, se formou, formatura, passou no, passou na,
        passou em, aprovada, aprovação, promovida, promoção, intercâmbio, mudou de cidade,
        mudança, conquista, conquistou, faleceu, falecimento, morreu, morte, luto, perdeu, perda,
        acidente, nasceu, nascimento, tirou a carta, primeiro emprego, marcou muito,
        nunca esqueceu, mudou a vida, experiênc, superou, superação, marcante, inesquecível"""),
    "Infância": _t("""infância, criança, quando era criança, quando era pequena, quando pequena,
        de pequena, desde pequena, pequenininha, brincava, brincadeira, brinquedo, boneca,
        desenho animado, escolinha, primário, ensino fundamental, colégio, adolescência,
        adolescente, quando tinha, cresceu, cidade natal, casa da avó, na infância,
        quando menina, época da escola, anos 90, memória de infância"""),
    "Relacionamento": _t("""namoro, namorar, namorad, nosso namoro, nossa relação, relação,
        relacionamento, casal, a gente, nós dois, eu e ela, ela e eu, juntos, ciúme, ciumenta,
        briga, brigamos, brigou comigo, discuti, discussão, fizemos as pazes, as pazes, carinho,
        beijo, abraço, saudade, sente falta, me ama, te amo, eu te amo, pedido de namoro,
        noivado, morar juntos, futuro juntos, primeiro encontro, confiança, entre nós"""),
    "Conversas importantes": _t("""conversa séria, conversa, conversamos, desabaf, abriu o coração,
        se abriu, confess, confissão, segredo, contou um segredo, falamos sobre, papo sério,
        discutimos a relação, decidimos, combinamos, pediu desculpas, desculpa, perdão,
        contou pela primeira vez, nunca tinha contado, conversa difícil, conversa importante,
        me confidenciou"""),
    "Situações pendentes": _t("""espera, aguarda, ainda não sabe, vai saber, resultado, resposta, retorno,
        vai decidir, decidindo, ficou de, pendente, pendência, em aberto, sem resposta,
        ainda não, na dúvida, indecis, vai ver se, vai descobrir, não sabe se, depende de,
        falta resolver, precisa resolver, tem que resolver, a confirmar"""),
    "Eventos futuros": _t("""vai ter, vai fazer, vai ser, marcad, marcou, agendad, agendou,
        semana que vem, próxima semana, mês que vem, próximo mês, ano que vem, amanhã,
        depois de amanhã, daqui a, em breve, logo mais, planejando, programad, compromisso,
        vai acontecer, está chegando, tá chegando, falta pouco, contagem regressiva,
        vai viajar, vai apresentar"""),
    "Pessoas importantes": _t("""melhor amiga, melhor amigo, pessoa importante, muito importante para ela,
        muito próxima, super próxima, braço direito, admira, admiração, inspiração, referência,
        ídolo, ídola, mentor, confia, confidente, madrinha, padrinho, como uma irmã,
        como uma mãe, é tudo para ela, a pessoa que ela mais, pessoa especial, grande amiga"""),
    "Aniversários": _t("""aniversário, niver, faz aniversário, faz anos, fazer anos, completa anos,
        completar anos, parabéns, festa de aniversário, bolo de aniversário, aniversariante,
        velinhas, anos de idade, aniversário de namoro, mesversário, bodas, idade"""),
    "Memórias compartilhadas": _t("""primeiro encontro, primeiro beijo, nossa viagem, nosso primeiro,
        nossa primeira, fomos, a gente foi, nós fomos, fizemos, a gente fez, passamos,
        assistimos, jantamos, saímos, viajamos, conheci a família, conheci os pais,
        conheci a mãe, conheci a amiga, conheci as amigas, lembramos, rimos, nosso lugar,
        nossa música, nosso date, nosso encontro, lembranç, recordaç, a primeira vez que"""),
}


def _padrao(termo: str) -> re.Pattern[str]:
    partes = termo.split()
    corpo = r"\s+".join(re.escape(x) for x in partes)
    fim = r"[a-z0-9]*" if len(partes[-1]) >= RAIZ_MINIMA else r"(?:s|es)?"
    return re.compile(r"(?<![a-z0-9])" + corpo + fim + r"(?![a-z0-9])")


_COMPILADOS: dict[str, list[tuple[str, re.Pattern[str]]]] = {
    cat: [(t, _padrao(t)) for t in LEXICO.get(cat, [])] for cat in CATEGORIAS
}


def classificar(texto: str) -> list[tuple[str, float]]:
    """[(categoria, pontuação 0-1)], maior primeiro, só as que pontuaram.

    Cada termo encontrado soma um ponto (expressões de várias palavras, dois);
    pontuação = 1 - 0,6^pontos. Empate: a que aparece primeiro no texto.
    """
    n = normalizar(texto or "")
    if not n:
        return []
    achadas = []
    for cat, termos in _COMPILADOS.items():
        pontos, primeira = 0, len(n)
        for termo, rx in termos:
            m = rx.search(n)
            if m:
                pontos += 2 if " " in termo else 1
                primeira = min(primeira, m.start())
        if pontos:
            achadas.append((cat, round(1 - 0.6 ** pontos, 3), primeira))
    achadas.sort(key=lambda a: (-a[1], a[2]))
    return [(cat, pontuacao) for cat, pontuacao, _ in achadas]


def categorias_de(texto: str, limite: int = 3) -> list[str]:
    """Só os nomes das categorias mais fortes."""
    return [c for c, _ in classificar(texto)[:limite]]


__all__ = ["LEXICO", "RAIZ_MINIMA", "categorias_de", "classificar"]
