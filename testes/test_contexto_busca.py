"""Testes do detector de contexto (contexto.py) e da busca em linguagem natural (busca.py)."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from datetime import date

from entrenos.banco import Banco
from entrenos.busca import MAX_ENCONTRADOS, buscar
from entrenos.contexto import LIMITE_RELACOES, relacionar
from entrenos.modelos import CATEGORIAS, Certeza, Relacao, Resposta, TipoItem, para_json
from entrenos.motor import NAO_ENCONTREI, MotorRegras, conhecimento_do_banco

REF = date(2026, 9, 30)   # quarta-feira
MOTOR = MotorRegras()


class _ComPastaTemporaria(unittest.TestCase):
    """Nada destes testes vai para o disco; a pasta existe só para garantir o isolamento."""

    @classmethod
    def setUpClass(cls):
        cls._pasta = tempfile.mkdtemp(prefix="entrenos-teste-")
        cls._antes = os.environ.get("ENTRENOS_DADOS")
        os.environ["ENTRENOS_DADOS"] = cls._pasta

    @classmethod
    def tearDownClass(cls):
        if cls._antes is None:
            os.environ.pop("ENTRENOS_DADOS", None)
        else:
            os.environ["ENTRENOS_DADOS"] = cls._antes
        shutil.rmtree(cls._pasta, ignore_errors=True)


def _gravar(banco: Banco, texto: str, quando: date) -> int:
    p = MOTOR.analisar(texto, quando, conhecimento_do_banco(banco))
    return banco.criar_registro(texto, quando, p.itens, p.pessoas, [])


def _proposta(banco: Banco, texto: str, quando: date = REF):
    return MOTOR.analisar(texto, quando, conhecimento_do_banco(banco))


# ---------------------------------------------------------------- contexto

class TesteContexto(_ComPastaTemporaria):
    def setUp(self):
        self.banco = Banco.novo()
        self.banco.salvar_config(nome_dela="Ana")
        self.sushi = _gravar(self.banco, "Ela não gosta de sushi.", date(2026, 3, 12))
        self.nervosa = _gravar(self.banco, "Ela fica muito nervosa antes de apresentações.", date(2026, 9, 12))
        self.exames = _gravar(self.banco, "A mãe dela vai fazer exames.", date(2026, 9, 12))
        self.salvos = 0
        self.banco.ao_salvar = lambda dados: setattr(self, "salvos", self.salvos + 1)

    def tearDown(self):
        self.banco.fechar()

    def relacoes(self, texto: str) -> list[Relacao]:
        return relacionar(_proposta(self.banco, texto), self.banco, REF)

    def item_sushi(self):
        return self.banco.registro(self.sushi)["itens"][0]

    def test_contradicao_sushi(self):
        rels = self.relacoes("Ela disse que ama sushi.")
        self.assertEqual([r.tipo for r in rels], ["contradicao"])
        r = rels[0]
        self.assertEqual(r.motivo, "Em 12/03 você registrou que ela não gosta de sushi. Agora: “Ela disse que ama "
                                   "sushi.” Existe uma possível mudança ou contradição. Deseja atualizar a preferência?")
        self.assertEqual(r.item_novo, 0)
        self.assertEqual(r.item_antigo_id, self.item_sushi().id)
        self.assertEqual(r.registro_antigo_id, self.sushi)
        self.assertEqual(r.status, "pendente")

    def test_contradicao_pela_acao(self):
        rels = self.relacoes("Ela pediu sushi no jantar.")
        self.assertEqual([r.tipo for r in rels], ["contradicao"])
        self.assertIn("Em 12/03 você registrou que ela não gosta de sushi.", rels[0].motivo)
        self.assertIn("Agora: “Ela pediu sushi no jantar.”", rels[0].motivo)

    def test_antiga_nao_muda_sozinha(self):
        self.relacoes("Ela disse que ama sushi.")
        self.assertEqual(self.item_sushi().status, "ativo")
        self.assertEqual(self.salvos, 0)          # relacionar só lê

    def test_sem_contradicao(self):
        for texto in ("Ela não pediu sushi.", "Ela odeia sushi.", "A mãe dela adora sushi.",
                      "Ela adora sorvete."):
            self.assertFalse([r for r in self.relacoes(texto) if r.tipo == "contradicao"], texto)

    def test_preferencia_que_ja_mudou_nao_contradiz(self):
        self.banco.atualizar_item(self.item_sushi().id, status="mudou")
        self.assertEqual(self.relacoes("Ela disse que ama sushi."), [])

    def test_contexto_apresentacao_cita_a_data(self):
        rels = self.relacoes("Ela vai fazer uma apresentação no trabalho dia 20/10.")
        self.assertEqual([r.tipo for r in rels], ["contexto"])
        r = rels[0]
        self.assertIn("Em 12/09 você registrou que ela fica nervosa antes de apresentações.", r.motivo)
        self.assertIn("20/10", r.sugestao)
        self.assertTrue(r.sugestao.startswith("Talvez"))
        antigo = self.banco.registro(self.nervosa)["itens"][0]
        self.assertEqual(antigo.tipo, TipoItem.PADRAO)
        self.assertEqual(r.item_antigo_id, antigo.id)

    def test_contexto_ao_contrario(self):
        _gravar(self.banco, "Ela vai fazer uma entrevista de emprego dia 15/10.", date(2026, 9, 20))
        rels = self.relacoes("Ela está muito nervosa com a entrevista.")
        contexto = [r for r in rels if r.tipo == "contexto"]
        self.assertEqual(len(contexto), 1)
        self.assertIn("Entrevista de emprego — 15/10/2026", contexto[0].motivo)
        self.assertIn("15/10", contexto[0].sugestao)

    def test_mesma_pessoa(self):
        rels = self.relacoes("A mãe dela vai fazer uma cirurgia em novembro.")
        self.assertEqual([r.tipo for r in rels], ["mesma_pessoa"])
        r = rels[0]
        self.assertEqual(r.motivo, "A mãe dela também apareceu em 12/09: “A mãe dela vai fazer exames.”")
        self.assertEqual(r.registro_antigo_id, self.exames)
        self.assertEqual(r.item_novo, 0)

    def test_mesma_pessoa_fora_dos_90_dias(self):
        banco = Banco.novo()
        _gravar(banco, "A mãe dela vai fazer exames.", date(2026, 5, 1))
        self.assertEqual(relacionar(_proposta(banco, "A mãe dela vai viajar."), banco, REF), [])
        banco.fechar()

    def test_sem_relacao_lista_vazia(self):
        self.assertEqual(self.relacoes("Ela adora tulipas."), [])
        self.assertEqual(relacionar(_proposta(Banco.novo(), "Ela disse que ama sushi."), Banco.novo(), REF), [])
        self.assertEqual(relacionar(_proposta(self.banco, ""), self.banco, REF), [])

    def test_no_maximo_cinco(self):
        comidas = ["pizza", "lasanha", "coxinha", "pastel", "tapioca", "risoto", "nhoque"]
        for c in comidas:
            _gravar(self.banco, f"Ela não gosta de {c}.", date(2026, 6, 1))
        texto = " ".join(f"Ela disse que ama {c}." for c in comidas)
        rels = self.relacoes(texto)
        self.assertEqual(len(rels), LIMITE_RELACOES)
        self.assertTrue(all(r.tipo == "contradicao" for r in rels))

    def test_mesma_informacao_repetida_gera_uma_relacao(self):
        recente = _gravar(self.banco, "Ela não gosta de sushi.", date(2026, 8, 1))
        _gravar(self.banco, "Ela não gosta de sushi.", date(2026, 5, 1))
        rels = self.relacoes("Ela disse que ama sushi. Ela vai fazer uma apresentação no trabalho dia 20/10.")
        self.assertEqual(sorted(r.tipo for r in rels), ["contexto", "contradicao"])
        contradicao = next(r for r in rels if r.tipo == "contradicao")
        self.assertEqual(contradicao.registro_antigo_id, recente)       # a mais recente
        self.assertIn("Em 01/08", contradicao.motivo)

    def test_relacoes_gravam_com_o_registro(self):
        texto = "Ela disse que ama sushi."
        p = _proposta(self.banco, texto)
        p.relacoes = relacionar(p, self.banco, REF)
        rid = self.banco.criar_registro(texto, REF, p.itens, p.pessoas, p.relacoes)
        gravada = self.banco.registro(rid)["relacoes"][0]
        self.assertEqual(gravada.tipo, "contradicao")
        self.assertEqual(gravada.item_novo, self.banco.registro(rid)["itens"][0].id)
        self.assertEqual(gravada.item_antigo_id, self.item_sushi().id)


# ---------------------------------------------------------------- busca

REGISTROS = [
    (date(2026, 1, 15), "Ela me contou que ama sushi e comida japonesa."),
    (date(2026, 2, 10), "A mãe dela, Sandra, faz aniversário dia 12/05. Ela disse que a mãe adora orquídeas."),
    (date(2026, 3, 12), "Ela não gosta de coentro."),
    (date(2026, 4, 5), "Mariana é a melhor amiga dela, se conheceram na faculdade."),
    (date(2026, 5, 20), "Ela disse que o trabalho anda puxado e que a chefe dela cobra demais."),
    (date(2026, 6, 12), "Ela ficou muito feliz quando ganhou tulipas de presente."),
    (date(2026, 6, 30), "Quando está estressada ela gosta de tomar banho demorado e ver série."),
    (date(2026, 7, 10), "Ela me disse que prefere que eu apenas fique perto quando está ansiosa."),
    (date(2026, 7, 15), "Quando está estressada ela não gosta que eu tente resolver tudo."),
    (date(2026, 7, 25), "Ela queria um fone novo de presente."),
    (date(2026, 8, 2), "Ela vai viajar para Salvador com a Mariana em dezembro."),
    (date(2026, 8, 10), "Ela adora sushi de salmão."),
    (date(2026, 8, 20), "Ela falou que a viagem para Salvador vai ser de ônibus porque tem medo de avião."),
    (date(2026, 9, 1), "Ela está preocupada com a entrevista de emprego dia 15/10."),
    (date(2026, 9, 12), "A mãe dela vai fazer exames no hospital."),
    (date(2026, 9, 18), "Ela comentou que no trabalho vai ter uma apresentação importante e que fica muito "
                        "nervosa antes de apresentações."),
    (date(2026, 9, 25), "O pai dela ligou para combinar a festa da família no Natal."),
    (date(2026, 9, 28), "Ela adora pizza de calabresa."),
]


class _MotorFalso:
    """Faz o papel do MotorIA na busca, sem rede."""

    def __init__(self, nome="ia:teste", erro=False, motor_da_resposta=None):
        self.nome = nome
        self.erro = erro
        self.motor_da_resposta = motor_da_resposta or nome
        self.chamadas = 0

    def responder(self, pergunta, trechos):
        self.chamadas += 1
        if self.erro:
            raise RuntimeError("falhou")
        inventado = {"registro_id": 999, "resumo": "Inventado", "trecho": "Nada disso foi escrito."}
        return Resposta(pergunta=pergunta, texto="Resposta escrita pela IA.",
                        encontrados=[trechos[0], inventado], motor=self.motor_da_resposta)


class TesteBusca(_ComPastaTemporaria):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.banco = Banco.novo()
        cls.banco.salvar_config(nome_dela="Ana")
        cls.ids = {}
        for quando, texto in REGISTROS:
            cls.ids[texto[:25]] = _gravar(cls.banco, texto, quando)

    @classmethod
    def tearDownClass(cls):
        cls.banco.fechar()
        super().tearDownClass()

    def rid(self, comeco: str) -> int:
        return next(v for k, v in self.ids.items() if k.startswith(comeco[:25]))

    def buscar(self, pergunta: str, motor=MOTOR) -> Resposta:
        r = buscar(pergunta, self.banco, motor, REF)
        self.conferir_formato(r)
        return r

    def conferir_formato(self, r: Resposta):
        self.assertIsInstance(r, Resposta)
        self.assertLessEqual(len(r.encontrados), MAX_ENCONTRADOS)
        for e in r.encontrados:
            self.assertTrue({"item_id", "registro_id", "resumo", "trecho", "certeza", "categoria", "data_registro",
                             "quando"} <= set(e))
            registro = self.banco.registro(e["registro_id"])
            self.assertIsNotNone(registro)
            self.assertIn(e["trecho"], registro["texto"])            # sempre literal, nunca inventado
            self.assertEqual(e["data_registro"], registro["data_registro"])
            self.assertIn(e["certeza"], [c.value for c in Certeza])
            self.assertTrue(e["categoria"] is None or e["categoria"] in CATEGORIAS)
            self.assertIsInstance(e["quando"], str)
        json.dumps(para_json(r))

    def registros(self, r: Resposta) -> set[int]:
        return {e["registro_id"] for e in r.encontrados}

    # ------------------------------------------------------------ as 7 perguntas da tela

    def test_familia_dela(self):
        r = self.buscar("O que eu sei sobre a família dela?")
        self.assertEqual(r.texto, "Encontrei 3 registros sobre a família dela:")
        esperados = {self.rid("A mãe dela, Sandra"), self.rid("A mãe dela vai fazer exa"), self.rid("O pai dela ligou")}
        self.assertEqual(self.registros(r), esperados)

    def test_comida_que_ela_mais_gosta(self):
        r = self.buscar("Qual é a comida que ela mais gosta?")
        self.assertEqual(r.texto, "Encontrei 3 registros sobre comidas de que ela gosta. "
                                  "O que mais aparece: sushi (2 registros).")
        self.assertIn("sushi", r.encontrados[0]["resumo"])
        resumos = " ".join(e["resumo"] for e in r.encontrados)
        self.assertIn("pizza de calabresa", resumos)
        self.assertNotIn("coentro", resumos)                            # não gosta: fica de fora

    def test_o_que_ajuda_quando_estressada(self):
        r = self.buscar("O que ela gosta de fazer quando está estressada?")
        self.assertTrue(r.texto.startswith("Encontrei 2 registros sobre o que ajuda quando ela está estressada"))
        resumos = {e["resumo"] for e in r.encontrados}
        self.assertIn("Gosta de tomar banho demorado", resumos)
        self.assertIn("Gosta de ver série", resumos)
        self.assertIn("Prefere que eu fique perto quando está ansiosa", resumos)
        self.assertNotIn("Está estressada", resumos)                   # estado não é o que ajuda

    def test_pessoas_mais_importantes(self):
        r = self.buscar("Quais são as pessoas mais importantes para ela?")
        self.assertTrue(r.texto.startswith("As pessoas que mais aparecem nos seus registros são: "
                                           "Mariana (melhor amiga), Sandra (mãe)"))
        self.assertTrue(r.encontrados[0]["resumo"].startswith("Mariana (melhor amiga) — aparece em 2 registros"))
        self.assertTrue(all(e["categoria"] == "Pessoas importantes" for e in r.encontrados))

    def test_ja_falou_de_presentes(self):
        r = self.buscar("Ela já falou alguma coisa sobre presentes?")
        self.assertTrue(r.texto.startswith("Sim. Encontrei"))
        self.assertIn(self.rid("Ela queria um fone novo"), self.registros(r))
        self.assertIn(self.rid("Ela ficou muito feliz quando"), self.registros(r))

    def test_o_que_falou_da_viagem(self):
        r = self.buscar("O que ela falou sobre a viagem?")
        self.assertEqual(r.texto, "Encontrei 2 registros sobre a viagem:")
        self.assertEqual(self.registros(r), {self.rid("Ela vai viajar para Salva"), self.rid("Ela falou que a viagem")})

    def test_quando_comentou_do_trabalho(self):
        r = self.buscar("Quando ela comentou sobre o trabalho?")
        self.assertEqual(r.texto, "Encontrei 3 registros sobre o trabalho, em 18/09/2026, 01/09/2026 e 20/05/2026:")
        datas = [e["data_registro"] for e in r.encontrados]
        self.assertEqual(datas, sorted(datas, reverse=True))

    # ------------------------------------------------------------ outras perguntas

    def test_sobre_uma_pessoa(self):
        r = self.buscar("O que eu sei sobre a Mariana?")
        self.assertEqual(self.registros(r), {self.rid("Mariana é a melhor amiga"), self.rid("Ela vai viajar para Salva")})

    def test_sobre_a_mae_pela_relacao(self):
        r = self.buscar("o que eu sei sobre a mãe dela")
        self.assertEqual(self.registros(r), {self.rid("A mãe dela, Sandra"), self.rid("A mãe dela vai fazer exa")})

    def test_o_que_nao_ajuda(self):
        r = self.buscar("O que ela não gosta quando está estressada?")
        self.assertEqual(r.texto, "Encontrei 1 registro sobre o que não ajuda quando ela está estressada:")
        self.assertEqual(self.registros(r), {self.rid("Quando está estressada ela não")})

    def test_sobre_ela(self):
        r = self.buscar("O que eu sei sobre ela?")
        self.assertTrue(r.texto.startswith("Encontrei"))
        self.assertTrue(r.encontrados)
        for e in r.encontrados:
            item = self.banco.item(e["item_id"])
            self.assertIsNone(item.pessoa)
            self.assertNotEqual(item.tipo, TipoItem.NOTA)
        pelo_nome = buscar("O que eu sei sobre a Ana?", self.banco, MOTOR, REF)
        self.assertEqual(pelo_nome.encontrados, r.encontrados)
        self.assertTrue(pelo_nome.texto.startswith("Encontrei 13 registros sobre a Ana."))

    def test_assunto_composto_nao_vira_categoria_inteira(self):
        r = self.buscar("O que ela falou sobre comida japonesa?")
        self.assertEqual(self.registros(r), {self.rid("Ela me contou que ama sus")})

    def test_busca_livre_ignora_nome_dela_e_verbo_comum(self):
        r = self.buscar("Ana gosta de sushi?")
        self.assertEqual(self.registros(r), {self.rid("Ela me contou que ama sus"), self.rid("Ela adora sushi de sal")})

    def test_busca_livre(self):
        r = self.buscar("sushi")
        self.assertEqual(self.registros(r), {self.rid("Ela me contou que ama sus"), self.rid("Ela adora sushi de sal")})
        self.assertEqual(r.encontrados[0]["quando"], "há 2 meses")

    def test_nao_encontrei(self):
        for pergunta in ("O que ela acha de futebol?", "Qual o filme favorito dela?", "Quando ela comentou sobre "
                         "o cachorro?", "", "   ", "O que?", None):
            r = buscar(pergunta, self.banco, MOTOR, REF)
            self.assertEqual(r.texto, NAO_ENCONTREI, pergunta)
            self.assertEqual(r.encontrados, [])

    def test_ia_escreve_o_texto_e_so_cita_o_que_existe(self):
        motor = _MotorFalso()
        r = self.buscar("O que ela falou sobre a viagem?", motor)
        self.assertEqual(r.texto, "Resposta escrita pela IA.")
        self.assertEqual(r.motor, "ia:teste")
        self.assertEqual(len(r.encontrados), 1)                         # o 999 inventado saiu

    def test_ia_com_problema_fica_o_modelo_de_frase(self):
        for motor in (_MotorFalso(erro=True), _MotorFalso(motor_da_resposta="regras")):
            r = self.buscar("O que ela falou sobre a viagem?", motor)
            self.assertEqual(r.texto, "Encontrei 2 registros sobre a viagem:")
            self.assertEqual(r.motor, "regras")

    def test_ia_nao_e_chamada_sem_resultado(self):
        motor = _MotorFalso()
        r = buscar("O que ela acha de futebol?", self.banco, motor, REF)
        self.assertEqual(r.texto, NAO_ENCONTREI)
        self.assertEqual(motor.chamadas, 0)


class TesteBuscaRevisao(_ComPastaTemporaria):
    """Perguntas que o revisor de português mostrou dando errado."""

    REGISTROS = [
        (date(2026, 1, 10), "A mãe dela gosta de sushi."),
        (date(2026, 2, 10), "Ela gosta de sushi e de comida japonesa."),
        (date(2026, 3, 1), "O aniversário dela é 14/03."),
        (date(2026, 3, 5), "A Mariana, melhor amiga dela, faz aniversário dia 03/10."),
        (date(2026, 3, 6), "O aniversário da mãe dela é dia 22/10."),
        (date(2026, 4, 1), "Ela é alérgica a camarão."),
        (date(2026, 4, 2), "Ela não come carne de porco."),
        (date(2026, 4, 3), "Ela gosta de sair pra jantar."),
        (date(2026, 4, 4), "Ela gosta de vinho tinto."),
        (date(2026, 4, 5), "Ela gosta de praia."),
        (date(2026, 5, 1), "Ela ficou muito feliz quando ganhou um buquê de girassol."),
        (date(2026, 5, 2), "Ela ama tulipas amarelas."),
        (date(2026, 5, 3), "A gata dela, Mel, ta doente."),
        (date(2026, 5, 4), "Ela quer um cachorro."),
        (date(2026, 5, 5), "A Camila odeia o chefe dela."),
        (date(2026, 5, 6), "Ela está gripada."),
        (date(2026, 5, 9), "Ela odeia coentro."),
        (date(2026, 5, 10), "Ela lembra muito do avô, que morreu quando ela tinha 10 anos."),
        (date(2026, 5, 11), "A avó dela faz aniversário dia 20/11."),
    ] + [(date(2026, 6, i), f"Ela disse que ama a música número {i}.") for i in range(1, 13)]

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.banco = Banco.novo()
        cls.banco.salvar_config(nome_dela="Julia")
        cls.ids = [_gravar(cls.banco, texto, quando) for quando, texto in cls.REGISTROS]

    @classmethod
    def tearDownClass(cls):
        cls.banco.fechar()
        super().tearDownClass()

    def buscar(self, pergunta: str) -> Resposta:
        return buscar(pergunta, self.banco, MOTOR, REF)

    def resumos(self, pergunta: str) -> list[str]:
        return [e["resumo"] for e in self.buscar(pergunta).encontrados]

    def test_pergunta_sobre_ela_nao_traz_outra_pessoa(self):
        self.assertEqual(sorted(self.resumos("ela gosta de sushi?")), ["Gosta de comida japonesa", "Gosta de sushi"])
        self.assertTrue(all("Camila" not in r for r in self.resumos("o que ela odeia?")))

    def test_aniversario_de_quem(self):
        r = self.buscar("quando é o aniversário dela?")
        self.assertEqual(r.texto, "O aniversário dela é em 14/03:")
        self.assertEqual([e["resumo"] for e in r.encontrados], ["Aniversário dela — 14/03"])
        r = self.buscar("quando é o aniversário da mãe dela?")
        self.assertEqual(r.texto, "O aniversário da mãe dela é em 22/10:")
        self.assertEqual([e["resumo"] for e in r.encontrados], ["Aniversário da mãe — 22/10"])
        self.assertEqual(self.buscar("quando a Mariana faz aniversário?").texto, "O aniversário da Mariana é em 03/10:")

    def test_o_que_ela_nao_gosta_de_comer(self):
        resumos = self.resumos("o que ela não gosta de comer?")
        self.assertEqual(sorted(resumos), ["Alérgica a camarão", "Não come carne de porco", "Não gosta de coentro"])

    def test_preferida_sem_mais_gosta(self):
        self.assertEqual(self.resumos("qual bebida ela prefere?"), ["Gosta de vinho tinto"])
        self.assertNotIn("Gosta de sair pra jantar", self.resumos("qual a comida que ela mais gosta?"))

    def test_topicos_flores_e_pet(self):
        flores = self.resumos("ela gosta de flores?")
        self.assertIn("Gosta de tulipas amarelas", flores)
        self.assertTrue(any("girassol" in r for r in flores))
        pet = self.resumos("ela tem algum pet?")
        self.assertIn("Quer um cachorro", pet)
        self.assertTrue(any(r.startswith("Mel") for r in pet))

    def test_palavra_de_conversa_nao_vira_termo(self):
        self.assertEqual(self.buscar("ela ta gravida?").texto, NAO_ENCONTREI)

    def test_restricao_sempre_entra_no_resumo_sobre_ela(self):
        # 12 falas confirmadas de importância 2 não empurram a alergia para fora dos 10.
        self.assertIn("Alérgica a camarão", self.resumos("o que eu sei sobre ela?"))

    def test_apelido_dela_e_ela(self):
        self.assertEqual(self.resumos("o que eu sei sobre a Ju?"), self.resumos("o que eu sei sobre ela?"))

    def test_avo_nao_e_avo(self):
        self.assertEqual(self.resumos("o que eu sei sobre a avó dela?"), ["Aniversário da avó — 20/11"])


class TesteBuscaDepoisDeApagar(_ComPastaTemporaria):
    def test_registro_apagado_some_da_busca(self):
        banco = Banco.novo()
        rid = _gravar(banco, "Ela adora sushi de salmão.", date(2026, 8, 10))
        self.assertEqual(len(buscar("sushi", banco, MOTOR, REF).encontrados), 1)
        banco.apagar_registro(rid)
        self.assertEqual(buscar("sushi", banco, MOTOR, REF).texto, NAO_ENCONTREI)
        banco.fechar()

    def test_banco_vazio(self):
        banco = Banco.novo()
        for pergunta in ("O que eu sei sobre a família dela?", "Quais são as pessoas mais importantes para ela?",
                         "Qual é a comida que ela mais gosta?", "sushi"):
            self.assertEqual(buscar(pergunta, banco, MOTOR, REF).texto, NAO_ENCONTREI)
        banco.fechar()


if __name__ == "__main__":
    unittest.main()
