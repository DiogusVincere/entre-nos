"""Perguntas profundas e "Nós dois" (DESIGN.md, seções 20.1 e 20.9, item 5).

O recurso é testado sozinho (`recursos.usando(perguntas)`): os outros estão sendo escritos ao mesmo
tempo e não podem mudar o resultado daqui. Pasta de dados temporária (ENTRENOS_DADOS), data fixa
30/09/2026 (quarta-feira, último dia do mês), scrypt pequeno. Nada aqui espera de verdade.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import tempfile
import types
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from entrenos import alertas, cofre, motor, nucleo, recursos
from entrenos.banco import Banco
from entrenos.modelos import CATEGORIAS as CATEGORIAS_APP
from entrenos.modelos import Certeza, Item, TipoItem
from entrenos.motor import Conhecimento
from entrenos.recursos import perguntas as P
from testes.test_servidor import PIN, REF, Relogio

D = P                                       # o banco de perguntas mora no próprio módulo
RAIZ = Path(os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))
ANTIGO = REF - timedelta(days=40)          # longe dos 14 dias de gancho
_PADRAO = object()

_remendos: list = []
_env_antigo: str | None = None
_pasta_env: str | None = None


def setUpModule() -> None:
    global _env_antigo, _pasta_env
    _env_antigo = os.environ.get("ENTRENOS_DADOS")
    _pasta_env = tempfile.mkdtemp(prefix="entrenos-teste-perguntas-env-")
    os.environ["ENTRENOS_DADOS"] = _pasta_env      # garantia: nada cai em %LOCALAPPDATA%
    remendo = mock.patch.object(cofre, "SCRYPT_N", 2 ** 12)
    remendo.start()
    _remendos.append(remendo)


def tearDownModule() -> None:
    for remendo in _remendos:
        remendo.stop()
    _remendos.clear()
    if _env_antigo is None:
        os.environ.pop("ENTRENOS_DADOS", None)
    else:
        os.environ["ENTRENOS_DADOS"] = _env_antigo
    shutil.rmtree(_pasta_env, ignore_errors=True)


def item(tipo: TipoItem, trecho: str, categorias=(), certeza=Certeza.OBSERVADO, **campos) -> Item:
    return Item(tipo=tipo, resumo=campos.pop("resumo", trecho.rstrip(".")), trecho=trecho, certeza=certeza,
                categorias=list(categorias), **campos)


class Coletor(logging.Handler):
    def __init__(self):
        super().__init__()
        self.mensagens: list[str] = []

    def emit(self, record):
        self.mensagens.append(record.getMessage())


# ====================================================================== base (banco, sem HTTP)

class Base(unittest.TestCase):
    """Banco em memória só com este recurso. "Nós dois" desligado por padrão (30/09 é fim de mês e
    a pergunta sobre vocês dois ganharia sempre); os testes de "Nós dois" ligam."""

    nos_dois = False

    def setUp(self):
        self.enterContext(recursos.usando(P))
        self.banco = Banco.novo()
        self.addCleanup(self.banco.fechar)
        self.banco.salvar_config(perguntas_nos_dois=self.nos_dois)
        self.motor = motor.MotorRegras()

    # ------------------------------------------------------------ montar dados

    def encher(self, n: int = P.MIN_REGISTROS, quando: date = ANTIGO) -> list[int]:
        """Registros neutros e antigos (Comidas): passam do mínimo sem puxar nenhuma categoria."""
        return [self.banco.criar_registro(f"Ela pediu o prato {i} no jantar.", quando, [
            item(TipoItem.NOTA, f"Ela pediu o prato {i} no jantar.", ["Comidas"])], [], []) for i in range(n)]

    def gravar(self, texto: str, quando: date = REF, itens=()) -> int:
        return self.banco.criar_registro(texto, quando, list(itens), [], [])

    def estado(self, texto: str, quando: date, polaridade: int = -1, **campos) -> int:
        return self.gravar(texto, quando, [item(TipoItem.ESTADO, texto, ["Preocupações"], data_evento=quando,
                                                polaridade=polaridade, **campos)])

    def analisar(self, texto: str, quando: date = REF, nome_dela: str | None = None):
        return self.motor.analisar(texto, quando, motor.Conhecimento(pessoas=[], nome_dela=nome_dela))

    def extras(self, texto: str, nome_dela: str | None = None) -> list[dict]:
        return self.analisar(texto, nome_dela=nome_dela).extras.get("perguntas", [])

    def registrar(self, texto: str, quando: date = REF, confirmar: bool = True) -> int:
        """Como a tela faria: as regras analisam, o usuário confirma os extras, grava tudo junto."""
        proposta = self.analisar(texto, quando)
        extras = recursos.extras_confirmados({"perguntas": proposta.extras.get("perguntas", [])} if confirmar else None,
                                             texto)
        with self.banco.transacao():
            rid = self.banco.criar_registro(texto, quando, proposta.itens, proposta.pessoas, [])
            recursos.gravar_extras(self.banco, rid, extras, quando)
        return rid

    def linhas(self) -> list[dict]:
        return self.banco.consultar(f"SELECT * FROM {P.TABELA} ORDER BY id")

    def cartoes(self, quando: date = REF) -> list[dict]:
        """Como o Hoje faz: os cartões e, como eles aparecem (sem outros recursos disputando), o
        `cartoes_mostrados` que grava a sugestão nova (integração, 20.10)."""
        cartoes = P.cartoes_hoje(self.banco, quando)
        if cartoes:
            P.cartoes_mostrados(self.banco, quando, [{"chave": c["chave"], "nivel": c["nivel"], "tipo": c["tipo"],
                                                      "dados": c["dados"]} for c in cartoes])
        return cartoes

    def cartao(self, quando: date = REF) -> dict | None:
        c = self.cartoes(quando)
        self.assertLessEqual(len(c), 1)
        return c[0] if c else None


# ====================================================================== o banco de perguntas

class TestBancoDePerguntas(unittest.TestCase):
    def test_pelo_menos_70_profundas_nas_7_categorias(self):
        profundas = [p for p in D.PERGUNTAS if D.CATEGORIAS[p.categoria].trilha == "profundas"]
        self.assertGreaterEqual(len(profundas), 70)
        por_categoria = {c.id: [p for p in profundas if p.categoria == c.id] for c in D.CATEGORIAS.values()
                         if c.trilha == "profundas"}
        self.assertEqual(set(por_categoria), {"infancia", "sonhos", "medos", "relacionamentos", "valores",
                                              "autoconhecimento", "futuro"})
        self.assertTrue(all(len(v) >= 10 for v in por_categoria.values()), {k: len(v) for k, v in por_categoria.items()})

    def test_nos_dois_e_cuidado(self):
        nos_dois = [p for p in D.PERGUNTAS if p.categoria == "nos_dois"]
        self.assertGreaterEqual(len(nos_dois), 20)
        self.assertGreaterEqual(len([p for p in D.PERGUNTAS if p.categoria == "apoio"]), 6)
        # "Nós dois" são perguntas para ELA sobre a relação: falam de "eu"/"a gente"/"nós".
        self.assertTrue(all(re.search(r"\b(eu|me|meu|mim|comigo|a gente|nosso|nossa|nós|casal)\b", p.texto.lower())
                            for p in nos_dois), [p.texto for p in nos_dois])

    def test_ids_unicos_e_campos_validos(self):
        ids = [p.id for p in D.PERGUNTAS]
        self.assertEqual(len(ids), len(set(ids)))
        for p in D.PERGUNTAS:
            with self.subTest(id=p.id):
                self.assertIn(p.categoria, D.CATEGORIAS)
                self.assertIn(p.nivel, (1, 2, 3))
                self.assertTrue(p.texto.endswith("?") and p.texto.count("?") == 1, p.texto)
                self.assertFalse(re.search(r"[.!](\s|$)", p.texto), "uma frase só")
                if p.repete_dias is not None:
                    self.assertIn(D.CATEGORIAS[p.categoria].trilha, ("nos_dois", "apoio"))

    def test_sem_perguntas_superficiais(self):
        superficiais = ("cor favorita", "comida favorita", "signo", "time do coração", "qual seu nome", "quantos anos")
        for p in D.PERGUNTAS:
            self.assertFalse(any(s in p.texto.lower() for s in superficiais), p.texto)
        self.assertTrue(all(len(p.texto.split()) >= 5 for p in D.PERGUNTAS))

    def test_ganchos_e_categorias_usam_as_categorias_do_app(self):
        for nome, (categorias, frase) in D.GANCHOS.items():
            self.assertIn(nome, CATEGORIAS_APP)
            self.assertTrue(set(categorias) <= set(D.CATEGORIAS), nome)
            self.assertTrue(frase.startswith("d"), frase)          # "ela falou do/da/de ..."
        for c in D.CATEGORIAS.values():
            self.assertTrue(set(c.categorias_app) <= set(CATEGORIAS_APP), c.id)

    def test_a_linha_que_vai_para_registrar_nao_vira_item_pessoa_nem_data(self):
        # Nunca inventar: uma pergunta não é algo que ela disse. Nenhuma das perguntas do banco, na
        # linha que a tela põe em Registrar, pode virar gosto, medo, desejo, pessoa ou data.
        m = motor.MotorRegras()
        with recursos.usando():
            for p in D.PERGUNTAS:
                texto = P.texto_para_registrar(p)
                linha = texto.split("\n")[0]
                proposta = m.analisar(texto, REF)
                with self.subTest(id=p.id):
                    self.assertEqual([i.tipo for i in proposta.itens if i.trecho == linha], [TipoItem.NOTA])
                    self.assertEqual([i.certeza for i in proposta.itens if i.trecho == linha], [Certeza.OBSERVADO])
                    self.assertEqual(proposta.pessoas, [])
                    self.assertEqual(proposta.datas, [])

    def test_cada_pergunta_com_duas_palavras_de_conteudo_se_reconhece_escrita_livre(self):
        for p in D.PERGUNTAS:
            if len(P._RAIZES[p.id]) < 2:
                continue
            with self.subTest(id=p.id):
                achado = P._casar_livre(p.texto)
                self.assertIsNotNone(achado)
                self.assertEqual(achado[0].id, p.id)

    def test_a_propria_pergunta_entre_aspas_e_literal(self):
        for p in D.PERGUNTAS:
            self.assertEqual(P._casar_literal(p.texto)[:2], (p, "literal"))


# ====================================================================== extrair (regras da revisão)

class TestExtrair(Base):
    def test_linha_da_tela_com_resposta(self):
        e, = self.extras("Eu perguntei: “Qual é o seu maior sonho neste momento da vida?”\n"
                         "Ela respondeu: morar perto do mar, com um cachorro.")
        self.assertEqual((e["tipo"], e["pergunta_id"], e["forma"], e["categoria"]), ("pergunta_feita", 31, "literal", "sonhos"))
        self.assertEqual(e["resposta"], "morar perto do mar, com um cachorro.")
        self.assertEqual(e["certeza"], "confirmado")
        self.assertEqual(e["trecho"], "Eu perguntei: “Qual é o seu maior sonho neste momento da vida?”")
        self.assertFalse(e["recusou"])
        self.assertNotIn("_privado", e)

    def test_resposta_em_branco_nao_vira_a_linha_de_baixo(self):
        e, = self.extras("Eu perguntei: “Qual é o seu maior sonho neste momento da vida?”\nEla respondeu: \n"
                         "Depois fomos jantar.")
        self.assertIsNone(e["resposta"])
        e, = self.extras("Eu perguntei: “Qual é o seu maior sonho neste momento da vida?”\n\nDepois fomos jantar.")
        self.assertIsNone(e["resposta"])

    def test_escrita_livre_com_resposta_na_mesma_frase(self):
        texto = "Perguntei pra ela qual era o maior medo dela e ela disse que é perder a avó. Achei fofo."
        e, = self.extras(texto)
        self.assertEqual((e["pergunta_id"], e["forma"]), (51, "livre"))
        self.assertEqual(e["resposta"], "ela disse que é perder a avó.")
        self.assertEqual(e["trecho"], "Perguntei pra ela qual era o maior medo dela e ela disse que é perder a avó.")
        self.assertEqual(e["certeza"], "confirmado")

    def test_escrita_livre_sem_acento_e_com_virgula(self):
        e, = self.extras("ontem perguntei qual o maior sonho dela, ela respondeu q quer morar na praia")
        self.assertEqual(e["pergunta_id"], 31)
        self.assertEqual(e["resposta"], "ela respondeu q quer morar na praia")

    def test_negacao_nao_e_pergunta_feita(self):
        for texto in ("Ainda não perguntei qual o maior sonho dela.", "Esqueci de perguntar qual o maior medo dela.",
                      "Não vou perguntar qual o maior medo dela, não é hora.", "Nunca perguntei qual o maior sonho dela.",
                      "n perguntei qual o maior sonho dela kkk"):
            with self.subTest(texto=texto):
                self.assertEqual(self.extras(texto), [])

    def test_vou_perguntar_vira_guardada(self):
        e, = self.extras("Vou perguntar pra ela amanhã qual o maior medo dela.")
        self.assertEqual((e["tipo"], e["pergunta_id"]), ("pergunta_guardada", 51))
        self.assertNotIn("resposta", e)

    def test_pergunta_para_outra_pessoa_nao_conta(self):
        for texto in ("Perguntei pra mãe dela qual o maior medo dela.", "Perguntei pro chefe dela qual era o maior sonho dela.",
                      "Perguntei pra Mariana qual era o maior sonho da Ju."):
            with self.subTest(texto=texto):
                self.assertEqual(self.extras(texto), [])

    def test_namorada_e_carinhos_contam_como_ela_e_meu_pai_nao(self):
        self.assertEqual([e["pergunta_id"] for e in self.extras("Perguntei pra minha namorada qual o maior sonho dela.")], [31])
        self.assertEqual([e["pergunta_id"] for e in self.extras("Perguntei pro meu amor qual o maior medo dela.")], [51])
        self.assertEqual([e["pergunta_id"] for e in self.extras("perguntei à Julinha qual o maior sonho dela",
                                                                 nome_dela="Julia")], [31])
        self.assertEqual(self.extras("Perguntei pro meu pai qual o maior sonho dele."), [])
        # Mesmo sem as pessoas que as regras acharam (como na gravação, que confere só o trecho).
        self.assertEqual(P._achar("Perguntei pro meu pai qual o maior sonho dele."), [])
        self.assertEqual(P._achar("Perguntei pra mãe dela qual o maior medo dela."), [])

    def test_ela_me_perguntou_nao_e_nossa(self):
        self.assertEqual(self.extras("Ela me perguntou qual era o meu maior sonho e eu disse que é viajar."), [])

    def test_apelido_dela_conta_como_ela(self):
        texto = "perguntei pra Ju qual o maior sonho dela kkk e ela falou q é ter uma livraria"
        e, = self.extras(texto, nome_dela="Julia")
        self.assertEqual(e["pergunta_id"], 31)
        self.assertEqual(e["resposta"], "ela falou q é ter uma livraria")
        self.assertEqual(self.extras(texto), [])               # sem saber o nome dela, "Ju" é outra pessoa

    def test_ela_preferiu_nao_responder(self):
        e, = self.extras("Perguntei qual era o maior medo dela mas ela não quis responder, mudou de assunto.")
        self.assertTrue(e["recusou"])
        self.assertIsNone(e["certeza"])
        self.assertIn("preferiu não responder", e["resumo"])
        e, = self.extras("Eu perguntei: “Qual é o seu maior medo neste momento da vida?”\nEla desconversou.")
        self.assertTrue(e["recusou"])

    def test_palpite_continua_palpite(self):
        e, = self.extras("Eu perguntei: “Qual é o seu maior sonho neste momento da vida?”\n"
                         "Ela respondeu: acho que viajar o mundo, mas não tem certeza.")
        self.assertEqual(e["certeza"], "inferencia")

    def test_sem_a_marca_de_resposta_a_certeza_nao_sobe(self):
        e, = self.extras("Eu perguntei: “Qual é o seu maior sonho neste momento da vida?”\nEla ficou pensativa.")
        self.assertEqual(e["resposta"], "Ela ficou pensativa.")
        self.assertEqual(e["certeza"], "observado")

    def test_duas_perguntas_no_mesmo_registro(self):
        texto = ("Eu perguntei: “Qual é o seu maior sonho neste momento da vida?” Ela disse que é ter uma casa no campo.\n"
                 "Eu perguntei: “Qual é o seu maior medo neste momento da vida?”\nEla respondeu: ficar sozinha.")
        a, b = self.extras(texto)
        self.assertEqual((a["pergunta_id"], a["resposta"]), (31, "Ela disse que é ter uma casa no campo."))
        self.assertEqual((b["pergunta_id"], b["resposta"]), (51, "ficar sozinha."))

    def test_pergunta_quase_igual_entre_aspas(self):
        e, = self.extras("Eu perguntei: “Qual é o seu maior sonho neste momento?”\nEla respondeu: escrever um livro.")
        self.assertEqual((e["pergunta_id"], e["forma"]), (31, "parecida"))

    def test_pergunta_fora_do_banco_nao_e_ligada(self):
        self.assertEqual(self.extras("Eu perguntei: “Você quer ir à praia no sábado?”\nEla respondeu: sim!"), [])
        self.assertEqual(self.extras("Perguntei como foi o dia dela."), [])
        self.assertEqual(self.extras("Perguntei o que ela achou do filme."), [])

    def test_ambigua_nao_liga_nenhuma(self):
        # "maior sonho e maior medo": empata entre duas perguntas; melhor não ligar do que ligar errado.
        self.assertEqual(self.extras("Perguntei qual o maior sonho e o maior medo dela."), [])

    def test_nos_dois_deixa_o_registro_fora_da_ia(self):
        proposta = self.analisar("Eu perguntei: “Tem algo que eu possa fazer melhor por você?”\n"
                                 "Ela respondeu: mandar mais mensagem durante o dia.")
        e, = proposta.extras["perguntas"]
        self.assertEqual((e["trilha"], e["_privado"]), ("nos_dois", True))
        self.assertIn(recursos.AVISO_PRIVADO, proposta.avisos)

    def test_pergunta_profunda_nao_vira_cadeado(self):
        proposta = self.analisar("Eu perguntei: “Qual é o seu maior sonho neste momento da vida?”\nEla respondeu: viajar.")
        self.assertNotIn(recursos.AVISO_PRIVADO, proposta.avisos)

    def test_tudo_o_que_sai_e_literal_do_texto(self):
        textos = [
            "Perguntei pra ela qual era o maior medo dela e ela disse que é perder a avó. Achei fofo.",
            "Eu perguntei: “Tem algo que eu possa fazer melhor por você?”\nEla respondeu: mais carinho de manhã 🥰",
            "Eu perguntei: “Qual é o seu maior sonho neste momento da vida?” Ela disse que é ter uma casa no campo.",
            "fiz a pergunta sobre a lembrança mais feliz da infância dela e ela contou da casa da avó em Minas",
        ]
        for texto in textos:
            for e in self.extras(texto):
                with self.subTest(texto=texto):
                    self.assertIn(e["trecho"], texto)
                    if e.get("resposta"):
                        self.assertIn(e["resposta"], texto)
                    self.assertEqual(e["pergunta"], P.pergunta(e["pergunta_id"]).texto)

    def test_marca_privado_na_duvida_e_sim(self):
        self.assertTrue(P.marca_privado([{"tipo": "pergunta_feita", "pergunta_id": "x"}]))
        self.assertTrue(P.marca_privado([{"tipo": "pergunta_feita", "pergunta_id": 200}]))
        self.assertFalse(P.marca_privado([{"tipo": "pergunta_feita", "pergunta_id": 31}]))
        self.assertFalse(P.marca_privado([{"tipo": "pergunta_guardada", "pergunta_id": 200}]))


# ====================================================================== gravar (pelo núcleo)

class BaseNucleo(unittest.TestCase):
    nos_dois = False

    def setUp(self):
        self.enterContext(recursos.usando(P))
        self.pasta = tempfile.mkdtemp(prefix="entrenos-teste-perguntas-")
        self.addCleanup(shutil.rmtree, self.pasta, True)
        self.dia = REF
        self.app = nucleo.Nucleo(self.pasta, hoje=lambda: self.dia, relogio=Relogio(), atraso_pin=0)
        self.addCleanup(self.app.bloquear)
        self.token = None
        r = self.pedir("POST", "/api/configurar", {"pin": PIN})
        self.assertEqual(r.status, 200, r.dados)
        self.token = r.dados["token"]
        self.banco.salvar_config(perguntas_nos_dois=self.nos_dois)

    @property
    def banco(self) -> Banco:
        return self.app._banco

    def pedir(self, metodo, caminho, corpo=None, token=_PADRAO, **kw) -> nucleo.RespostaApi:
        bruto = None if corpo is None else json.dumps(corpo).encode("utf-8")
        resposta = self.app.tratar(metodo, caminho, corpo=bruto, token=self.token if token is _PADRAO else token, **kw)
        self.assertEqual(json.loads(resposta.corpo.decode("utf-8")), resposta.dados)
        return resposta

    def analisar(self, texto: str) -> dict:
        r = self.pedir("POST", "/api/analisar", {"texto": texto, "data_registro": self.dia.isoformat()})
        self.assertEqual(r.status, 200, r.dados)
        return r.dados

    def corpo(self, proposta: dict, extras=_PADRAO, **mais) -> dict:
        extras = proposta["extras"] if extras is _PADRAO else extras
        return {"texto": proposta["texto"], "data_registro": self.dia.isoformat(), "itens": proposta["itens"],
                "pessoas": proposta["pessoas"], "relacoes": proposta["relacoes"], "extras": extras, **mais}

    def registrar(self, texto: str, extras=_PADRAO, **mais) -> nucleo.RespostaApi:
        return self.pedir("POST", "/api/registros", self.corpo(self.analisar(texto), extras, **mais))

    def encher(self, n: int = P.MIN_REGISTROS) -> None:
        for i in range(n):
            texto = f"Ela pediu o prato {i} no jantar."
            r = self.pedir("POST", "/api/registros", {"texto": texto, "data_registro": ANTIGO.isoformat(), "itens": [
                {"tipo": "nota", "resumo": texto, "trecho": texto, "certeza": "observado", "categorias": ["Comidas"]}]})
            self.assertEqual(r.status, 200, r.dados)

    def linhas(self) -> list[dict]:
        return self.banco.consultar(f"SELECT * FROM {P.TABELA} ORDER BY id")

    def cartao_do_hoje(self) -> dict | None:
        r = self.pedir("GET", "/api/hoje")
        self.assertEqual(r.status, 200, r.dados)
        meus = [a for a in r.dados["alertas"] if a.get("recurso") == "perguntas"]
        self.assertLessEqual(len(meus), 1)
        return meus[0] if meus else None


SONHO = "Eu perguntei: “Qual é o seu maior sonho neste momento da vida?”\nEla respondeu: morar perto do mar."
NOS_DOIS = "Eu perguntei: “Tem algo que eu possa fazer melhor por você?”\nEla respondeu: me ouvir até o fim, sem pressa."


class TestGravar(BaseNucleo):
    def test_extra_confirmado_liga_o_registro_a_pergunta(self):
        r = self.registrar(SONHO)
        self.assertEqual(r.status, 200, r.dados)
        self.assertNotIn("privado", r.dados)
        l, = self.linhas()
        self.assertEqual((l["pergunta_id"], l["status"], l["registro_id"], l["feita_em"], l["origem"]),
                         (31, "feita", r.dados["id"], REF.isoformat(), "registro"))
        self.assertEqual((l["resposta"], l["certeza"], l["recusou"]), ("morar perto do mar.", "confirmado", 0))

    def test_sem_confirmar_nada_e_guardado(self):
        r = self.registrar(SONHO, extras={})
        self.assertEqual(r.status, 200, r.dados)
        self.assertEqual(self.linhas(), [])

    def test_pergunta_inventada_400_e_nada_gravado(self):
        proposta = self.analisar(SONHO)
        extra = proposta["extras"]["perguntas"][0]
        for nome, troca, mensagem in (("id que não existe", {"pergunta_id": 9999}, P.PERGUNTA_DESCONHECIDA),
                                      ("outra pergunta", {"pergunta_id": 51}, P.PERGUNTA_NAO_CITADA),
                                      ("id que não é número", {"pergunta_id": "abc"}, P.PERGUNTA_DESCONHECIDA)):
            with self.subTest(caso=nome):
                r = self.pedir("POST", "/api/registros", self.corpo(proposta, {"perguntas": [{**extra, **troca}]}))
                self.assertEqual((r.status, r.dados["erro"]), (400, mensagem))
        self.assertEqual(self.pedir("GET", "/api/registros").dados, [])
        self.assertEqual(self.linhas(), [])

    def test_vou_perguntar_nao_vira_perguntei_pela_tela(self):
        proposta = self.analisar("Vou perguntar pra ela qual o maior medo dela.")
        extra = {**proposta["extras"]["perguntas"][0], "tipo": "pergunta_feita", "resposta": None}
        r = self.pedir("POST", "/api/registros", self.corpo(proposta, {"perguntas": [extra]}))
        self.assertEqual((r.status, r.dados["erro"]), (400, P.PERGUNTA_NAO_CITADA))
        self.assertEqual(self.linhas(), [])

    def test_resposta_inventada_pela_tela_nao_entra(self):
        proposta = self.analisar(SONHO)
        extra = {**proposta["extras"]["perguntas"][0], "resposta": "ela quer casar amanhã", "certeza": "confirmado",
                 "recusou": True}
        r = self.pedir("POST", "/api/registros", self.corpo(proposta, {"perguntas": [extra]}))
        self.assertEqual(r.status, 200, r.dados)
        l, = self.linhas()
        self.assertIsNone(l["resposta"])                      # não está no texto: não entra
        self.assertEqual((l["certeza"], l["recusou"]), (None, 0))   # recalculados pelo texto, não pela tela

    def test_certeza_recalculada_pelo_texto(self):
        proposta = self.analisar("Eu perguntei: “Qual é o seu maior sonho neste momento da vida?”\n"
                                 "Ela respondeu: acho que viajar o mundo.")
        extra = {**proposta["extras"]["perguntas"][0], "certeza": "confirmado"}
        self.assertEqual(self.pedir("POST", "/api/registros", self.corpo(proposta, {"perguntas": [extra]})).status, 200)
        self.assertEqual(self.linhas()[0]["certeza"], "inferencia")

    def test_perguntei_no_cartao_e_depois_o_registro_e_a_mesma_linha(self):
        r = self.pedir("POST", "/api/perguntas/31/perguntei", {"origem": "sugestao"})
        self.assertEqual(r.status, 200, r.dados)
        self.assertEqual(r.dados["texto_registrar"], P.texto_para_registrar(P.pergunta(31)))
        self.assertEqual(self.pedir("POST", "/api/perguntas/31/perguntei", {}).dados["linha_id"], r.dados["linha_id"])
        rid = self.registrar(SONHO).dados["id"]
        l, = self.linhas()
        self.assertEqual((l["id"], l["registro_id"], l["feita_por"], l["resposta"]),
                         (r.dados["linha_id"], rid, "botao", "morar perto do mar."))

    def test_vou_perguntar_no_texto_e_depois_a_resposta(self):
        r1 = self.registrar("Vou perguntar pra ela qual o maior medo dela, quando ela estiver tranquila.")
        l, = self.linhas()
        self.assertEqual((l["status"], l["registro_id"], l["origem"]), ("guardada", r1.dados["id"], "registro"))
        self.dia = REF + timedelta(days=2)
        r2 = self.registrar("Eu perguntei: “Qual é o seu maior medo neste momento da vida?”\nEla respondeu: ficar sozinha.")
        l, = self.linhas()
        self.assertEqual((l["status"], l["registro_id"], l["feita_em"]), ("feita", r2.dados["id"], self.dia.isoformat()))

    def test_resposta_de_nos_dois_fica_fora_da_ia(self):
        r = self.registrar(NOS_DOIS, privado=False)
        self.assertEqual(r.status, 200, r.dados)
        self.assertTrue(r.dados["privado"])
        self.assertEqual(r.dados["avisos"], [recursos.AVISO_PRIVADO_GRAVADO])
        rid = r.dados["id"]
        self.assertIn(rid, nucleo.fora_da_ia(self.banco))
        self.assertTrue(self.pedir("GET", f"/api/registros/{rid}").dados["fora_da_ia"])
        # Desmarcar o 🔒 depois não manda para a IA: o recurso continua dizendo que fica fora.
        self.assertEqual(self.pedir("PATCH", f"/api/registros/{rid}", {"privado": False}).status, 200)
        self.assertIn(rid, nucleo.fora_da_ia(self.banco))

    def test_ia_nao_analisa_o_texto_com_resposta_de_nos_dois(self):
        self.banco.salvar_config(ia_principal={"provedor": "ollama", "url": "http://127.0.0.1:11434",
                                               "modelo": "modelo-de-teste", "tipo": "ollama", "chave": None})
        # Recusado antes de qualquer conexão (nenhuma IA é chamada neste teste).
        with mock.patch.object(motor.MotorIA, "tentar_analisar", side_effect=AssertionError("chamou a IA")):
            r = self.pedir("POST", "/api/ia/analisar", {"texto": NOS_DOIS, "data_registro": REF.isoformat()})
        self.assertEqual((r.status, r.dados["erro"]), (403, nucleo.TEXTO_FICA_FORA_DA_IA))


# ====================================================================== a escolha e o Hoje

class TestEscolha(Base):
    def test_diario_vazio_ou_pequeno_nao_inventa_sugestao(self):
        self.assertEqual(self.cartoes(), [])
        self.encher(P.MIN_REGISTROS - 1)
        self.assertEqual(self.cartoes(), [])
        self.assertEqual(self.linhas(), [])                   # nada foi gravado
        self.assertEqual(alertas.hoje(self.banco, REF)["alertas"], [])

    def test_com_registros_um_cartao_com_motivo(self):
        self.encher()
        c = self.cartao()
        self.assertEqual((c["nivel"], c["tipo"]), ("sugestao", "pergunta"))
        self.assertTrue(c["texto"].startswith("Talvez perguntar a ela: “"))
        self.assertTrue(c["porque"])
        d = c["dados"]
        self.assertEqual(d["trilha"], "profundas")
        self.assertEqual(c["texto"], f"Talvez perguntar a ela: “{d['pergunta']}”")
        self.assertIn("ainda não registrou nada sobre", c["porque"])
        # Desde a integração (20.10) o cartão sai "nova" e a sugestão é gravada quando ele aparece
        # (cartoes_mostrados); a partir daí ela vem "sugerida", com a mesma chave.
        self.assertEqual(d["estado"], "nova")
        self.assertTrue(all(m["texto"] for m in d["motivos"]))
        l, = self.linhas()
        self.assertEqual((l["status"], l["origem"], l["sugerida_em"], l["pergunta_id"]),
                         ("sugerida", "sugestao", REF.isoformat(), d["pergunta_id"]))
        de_novo = self.cartao()
        self.assertEqual((de_novo["dados"]["estado"], de_novo["chave"]), ("sugerida", c["chave"]))

    def test_cartao_que_nao_aparece_nao_grava_nada(self):
        # O anti-excesso central pode deixar o cartão de fora num dia cheio: aí nada é gravado.
        self.encher()
        [c] = P.cartoes_hoje(self.banco, REF)
        self.assertEqual(c["dados"]["estado"], "nova")
        self.assertEqual(self.linhas(), [])
        self.assertEqual(P.cartoes_hoje(self.banco, REF)[0]["chave"], c["chave"])

    def test_comeca_leve(self):
        self.encher()
        self.assertEqual(P.pergunta(self.cartao()["dados"]["pergunta_id"]).nivel, 1)

    def test_prefere_o_que_se_sabe_menos(self):
        self.encher()
        # Tudo bem conhecido, menos Valores.
        for categoria in ("Infância", "Sonhos", "Medos", "Família", "Coisas que deixam feliz", "Objetivos"):
            for i in range(8):
                texto = f"{categoria}: lembrança {i}."
                self.gravar(texto, ANTIGO, [item(TipoItem.NOTA, texto, [categoria])])
        c = self.cartao()
        self.assertEqual(c["dados"]["categoria"], "valores")
        self.assertIn("ainda não registrou nada sobre o que é importante para ela", c["porque"])

    def test_gancho_no_que_ela_contou(self):
        self.encher()
        texto = "Ela contou que o trabalho anda puxado."
        rid = self.gravar(texto, REF - timedelta(days=2), [
            item(TipoItem.NOTA, texto, ["Trabalho"], certeza=Certeza.CONFIRMADO)])
        c = self.cartao()
        self.assertIn(c["dados"]["categoria"], ("futuro", "valores"))
        # O tempo vem antes (revisão de 07/10) e, com o gancho, a lacuna não se contradiz.
        self.assertIn("Anteontem ela falou do trabalho.", c["porque"])
        self.assertNotIn("ainda não registrou nada", c["porque"])
        self.assertIn(rid, c["registro_ids"])
        self.assertIn("trabal", P.pergunta(c["dados"]["pergunta_id"]).termos)   # a pergunta fala de trabalho

    def test_gancho_com_dia_da_semana(self):
        self.encher()
        texto = "Ela falou da família no almoço de domingo."
        self.gravar(texto, date(2026, 9, 27), [item(TipoItem.NOTA, texto, ["Família"], certeza=Certeza.CONFIRMADO)])
        c = self.cartao()
        self.assertIn(c["dados"]["categoria"], ("relacionamentos", "infancia"))
        self.assertIn("No domingo (27/09) ela falou da família.", c["porque"])

    def test_resposta_que_nao_foi_dita_e_recusa_com_outras_palavras(self):
        # Revisão de 07/10: "não lembro o que ela respondeu" virava a resposta 🟢 "ela respondeu."; e
        # "ela desconversou" não descansava a categoria.
        with recursos.usando(P):
            def extras(texto):
                return motor.MotorRegras().analisar(texto, REF, Conhecimento()).extras.get("perguntas", [])
            [e] = extras("Acho que perguntei qual o maior medo dela, mas não lembro o que ela respondeu.")
            self.assertEqual((e["resposta"], e["certeza"], e["recusou"]), (None, None, False))
            [e] = extras("Perguntei qual o maior medo dela e ela respondeu.")
            self.assertIsNone(e["resposta"])
            for fim in ("ela desconversou.", "ela ficou quieta.", "ela fugiu do assunto."):
                with self.subTest(fim=fim):
                    [e] = extras(f"Perguntei o que te recarrega depois de um dia difícil e {fim}")
                    self.assertTrue(e["recusou"])
                    self.assertIsNone(e["certeza"])
            [e] = extras("Acho que perguntei qual o maior medo dela e ela disse que é perder a mãe.")
            self.assertEqual(e["certeza"], "inferencia")                 # "acho que perguntei": no máximo 🔵


    def test_gancho_velho_nao_conta(self):
        self.encher()
        texto = "Ela contou que o trabalho anda puxado."
        self.gravar(texto, REF - timedelta(days=20), [item(TipoItem.NOTA, texto, ["Trabalho"], certeza=Certeza.CONFIRMADO)])
        self.assertNotIn("trabalho", self.cartao()["porque"])

    def test_estavel_enquanto_vale(self):
        self.encher()
        primeira = self.cartao()["dados"]["pergunta_id"]
        texto = "Ela contou que o trabalho anda puxado."
        self.gravar(texto, REF, [item(TipoItem.NOTA, texto, ["Trabalho"], certeza=Certeza.CONFIRMADO)])
        for dias in range(P.VALIDADE_SUGESTAO):
            self.assertEqual(self.cartao(REF + timedelta(days=dias))["dados"]["pergunta_id"], primeira)
        self.assertEqual(len(self.linhas()), 1)
        # Ignorada por 7 dias, abre lugar para outra (e a de antes perde pontos).
        self.assertNotEqual(self.cartao(REF + timedelta(days=P.VALIDADE_SUGESTAO))["dados"]["pergunta_id"], primeira)

    def test_espaco_entre_sugestoes(self):
        self.encher()
        p = P.pergunta(self.cartao()["dados"]["pergunta_id"])
        P._marcar_feita(self.banco, p, REF, origem="sugestao", feita_por="botao")
        for dias in range(P.DIAS_ENTRE_PADRAO):
            self.assertIsNone(self.cartao(REF + timedelta(days=dias)), dias)
        nova = self.cartao(REF + timedelta(days=P.DIAS_ENTRE_PADRAO))
        self.assertNotEqual(nova["dados"]["pergunta_id"], p.id)

    def test_espaco_configuravel_e_ajuste_estranho_vira_o_padrao(self):
        self.encher()
        p = P.pergunta(self.cartao()["dados"]["pergunta_id"])
        P._marcar_feita(self.banco, p, REF, origem="sugestao", feita_por="botao")
        self.banco.salvar_config(dias_entre_perguntas=1)
        self.assertIsNotNone(self.cartao(REF + timedelta(days=1)))
        for estranho in (0, 31, "3", True, 2.5e9):
            self.banco.salvar_config(dias_entre_perguntas=estranho)
            self.assertEqual(P._ajustes(self.banco)[P.CHAVE_DIAS], P.DIAS_ENTRE_PADRAO, estranho)

    def test_nunca_repete(self):
        self.encher()
        vistas = []
        dia = REF
        for _ in range(25):
            c = self.cartao(dia)
            if c is None:
                dia += timedelta(days=1)
                continue
            pid = c["dados"]["pergunta_id"]
            self.assertNotIn(pid, vistas)
            vistas.append(pid)
            P._marcar_feita(self.banco, P.pergunta(pid), dia, origem="sugestao", feita_por="botao")
            dia += timedelta(days=P.DIAS_ENTRE_PADRAO)
        self.assertGreaterEqual(len(vistas), 8)
        categorias = [P.pergunta(i).categoria for i in vistas]
        self.assertTrue(all(a != b for a, b in zip(categorias, categorias[1:])), categorias)   # varia

    def test_sensivel_sem_pergunta_funda_e_com_cuidado(self):
        self.encher()
        rid = self.estado("Ela tá muito cansada e triste com o trabalho.", REF - timedelta(days=1))
        c = self.cartao()
        self.assertEqual((c["dados"]["trilha"], c["dados"]["estado"]), ("apoio", "apoio"))
        self.assertTrue(c["texto"].startswith("Talvez, com carinho: “"))
        self.assertIn("Ontem você registrou: “Ela tá muito cansada e triste com o trabalho”.", c["porque"])
        self.assertIn(rid, c["registro_ids"])
        self.assertEqual(self.linhas(), [])                 # a de cuidado não ocupa o lugar das outras
        visao = P.visao_geral(self.banco, REF)
        self.assertTrue(visao["momento"]["sensivel"])

    def test_sensivel_nunca_traz_medos_nem_infancia(self):
        self.encher()
        # Medos e Infância são as que mais pontuam (nada registrado + gancho), e mesmo assim não vêm.
        for categoria in ("Sonhos", "Família", "Coisas que deixam feliz", "Objetivos", "Experiências importantes"):
            for i in range(8):
                self.gravar(f"{categoria} {i}.", ANTIGO, [item(TipoItem.NOTA, f"{categoria} {i}.", [categoria])])
        self.estado("Ela está triste.", REF)
        for dias in range(P.DIAS_SENSIVEL + 1):
            c = self.cartao(REF + timedelta(days=dias))
            if c:
                self.assertNotIn(c["dados"]["categoria"], ("medos", "infancia"))
                self.assertEqual(c["dados"]["trilha"], "apoio")

    def test_sensivel_sabendo_o_que_ajuda_fica_quieto(self):
        self.encher()
        self.gravar("Ela prefere que eu só fique perto quando está mal.", ANTIGO, [
            item(TipoItem.APOIO, "Ela prefere que eu só fique perto quando está mal.", ["Preocupações"], polaridade=1)])
        self.estado("Ela está triste.", REF)
        self.assertEqual(self.cartoes(), [])

    def test_sensivel_com_algo_marcado_para_amanha_fica_quieto(self):
        # O Hoje já mostra o compromisso de amanhã como Importante (e a sugestão dele): sem cartão a mais.
        self.encher()
        self.estado("Ela estava cansada hoje.", REF)
        self.assertEqual(self.cartao()["dados"]["trilha"], "apoio")
        texto = "Ela tem consulta amanhã."
        self.gravar(texto, ANTIGO, [item(TipoItem.EVENTO, texto, ["Saúde"], data_evento=REF + timedelta(days=1),
                                         precisao=None)])
        self.assertEqual(self.cartoes(), [])

    def test_dia_importante_fica_quieto(self):
        self.encher()
        texto = "A apresentação dela é amanhã."
        self.gravar(texto, ANTIGO, [item(TipoItem.EVENTO, texto, ["Eventos futuros"], data_evento=REF + timedelta(days=1),
                                         importancia=3)])
        self.assertEqual(self.cartoes(), [])
        self.assertIsNotNone(self.cartao(REF + timedelta(days=2)))      # passou o dia: volta

    def test_estado_bom_mais_recente_nao_e_sensivel_e_puxa_sonhos(self):
        self.encher()
        self.estado("Ela estava triste.", REF - timedelta(days=2))
        rid = self.estado("Ela está animada com o curso novo.", REF, polaridade=1)
        c = self.cartao()
        self.assertEqual(c["dados"]["trilha"], "profundas")
        self.assertIn(c["dados"]["categoria"], ("sonhos", "futuro"))
        self.assertIn(rid, c["registro_ids"])

    def test_estado_negativo_de_4_dias_ja_nao_e_sensivel(self):
        self.encher()
        self.estado("Ela estava triste.", REF - timedelta(days=P.DIAS_SENSIVEL + 1))
        self.assertEqual(self.cartao()["dados"]["trilha"], "profundas")

    def test_estado_de_outra_pessoa_nao_e_dela(self):
        self.encher()
        self.estado("A mãe dela está triste.", REF, pessoa="mãe")
        self.assertEqual(self.cartao()["dados"]["trilha"], "profundas")

    def test_categoria_que_ela_nao_quis_responder_descansa(self):
        self.encher()
        P._marcar_feita(self.banco, P.pergunta(51), REF - timedelta(days=10), origem="registro",
                        feita_por="registro", registro_id=self.gravar("x", ANTIGO), recusou=True)
        ctx = P._contexto(self.banco, REF)
        self.assertFalse(any(c.pergunta.categoria == "medos" for c in P._candidatas(ctx, ("profundas",))))
        ctx = P._contexto(self.banco, REF + timedelta(days=P.DIAS_RECUSA))
        self.assertTrue(any(c.pergunta.categoria == "medos" for c in P._candidatas(ctx, ("profundas",))))

    def test_registro_que_outro_recurso_deixa_fora_nao_vira_gancho(self):
        # Um recurso falso no lugar do ciclo: o registro dele nunca é citado no Hoje.
        self.encher()
        texto = "Ela contou que o trabalho anda puxado."
        rid = self.gravar(texto, REF, [item(TipoItem.NOTA, texto, ["Trabalho"], certeza=Certeza.CONFIRMADO)])
        ciclo = types.ModuleType("testes.recurso_ciclo_falso")
        ciclo.NOME = "ciclofalso"
        ciclo.fora_da_ia = lambda banco: {rid}
        with recursos.usando(P, ciclo):
            c = self.cartao()
        self.assertNotIn(rid, c["registro_ids"])
        self.assertNotIn("trabalho", c["porque"])

    def test_outro_recurso_que_falha_tira_os_ganchos_mas_nao_o_cartao(self):
        self.encher()
        texto = "Ela contou que o trabalho anda puxado."
        rid = self.gravar(texto, REF, [item(TipoItem.NOTA, texto, ["Trabalho"], certeza=Certeza.CONFIRMADO)])
        ruim = types.ModuleType("testes.recurso_ruim")
        ruim.NOME = "ruim"

        def explodir(banco):
            raise RuntimeError("Mariana adora tulipas")

        ruim.fora_da_ia = explodir
        coletor = Coletor()
        logging.getLogger("entrenos").addHandler(coletor)
        self.addCleanup(logging.getLogger("entrenos").removeHandler, coletor)
        with recursos.usando(P, ruim):
            c = self.cartao()
        self.assertIsNotNone(c)
        self.assertNotIn(rid, c["registro_ids"])
        self.assertFalse(any("Mariana" in m for m in coletor.mensagens))

    def test_sugestao_atual_nao_grava(self):
        self.encher()
        dados = P.sugestao_atual(self.banco, REF)
        self.assertEqual(dados["estado"], "nova")
        self.assertEqual(self.linhas(), [])
        self.assertEqual(P.visao_geral(self.banco, REF)["cartao"]["pergunta_id"], dados["pergunta_id"])
        self.assertEqual(self.linhas(), [])

    def test_anti_excesso_com_a_sugestao_do_hoje(self):
        self.encher()
        self.gravar("Ela está esperando o resultado da entrevista.", REF - timedelta(days=5), [
            item(TipoItem.PENDENCIA, "Ela está esperando o resultado da entrevista.", ["Trabalho"],
                 lembrar_em=REF - timedelta(days=1))])
        sugestoes = [a for a in alertas.hoje(self.banco, REF)["alertas"] if a.nivel == "sugestao"]
        self.assertEqual(len(sugestoes), 2)
        self.assertEqual({getattr(a, "recurso", "") for a in sugestoes}, {"", "perguntas"})
        self.assertGreater(sugestoes[0].relevancia, sugestoes[1].relevancia)     # a do Hoje vem antes


class TestNosDois(Base):
    nos_dois = True

    def test_fim_do_mes_traz_uma_pergunta_sobre_voces(self):
        self.encher()
        c = self.cartao()                                 # 30/09: último dia do mês
        self.assertEqual(c["dados"]["trilha"], "nos_dois")
        self.assertIn(P.FIM_DO_MES, c["porque"])
        self.assertEqual(c["relevancia"], P.RELEVANCIA_FIM_DO_MES)
        self.assertEqual(c["dados"]["titulo"], "Nós dois")

    def test_meio_do_mes_sem_o_motivo_do_fim_do_mes(self):
        self.encher()
        self.assertNotIn(P.FIM_DO_MES, self.cartao(date(2026, 10, 14))["porque"])

    def test_desligado_nunca_traz_nos_dois(self):
        self.encher()
        self.banco.salvar_config(perguntas_nos_dois=False)
        for dias in range(0, 60, 3):
            c = self.cartao(REF + timedelta(days=dias))
            self.assertEqual(c["dados"]["trilha"], "profundas")
            P._marcar_feita(self.banco, P.pergunta(c["dados"]["pergunta_id"]), REF + timedelta(days=dias),
                            origem="sugestao", feita_por="botao")

    def test_uma_de_nos_dois_a_cada_14_dias_e_respostas_para_a_area_eu(self):
        self.encher()
        rid = self.registrar(NOS_DOIS)
        self.assertEqual(P.respostas_nos_dois(self.banco), [P._vista(self.linhas()[0])])
        resposta, = P.respostas_nos_dois(self.banco, desde=date(2026, 9, 1), ate=REF)
        self.assertEqual((resposta["pergunta_id"], resposta["registro_id"], resposta["resposta"], resposta["certeza"]),
                         (200, rid, "me ouvir até o fim, sem pressa.", "confirmado"))
        self.assertEqual(P.respostas_nos_dois(self.banco, desde=REF + timedelta(days=1)), [])
        ctx = P._contexto(self.banco, REF + timedelta(days=5))
        self.assertFalse(any(c.pergunta.categoria == "nos_dois" for c in P._candidatas(ctx, ("profundas", "nos_dois"))))
        ctx = P._contexto(self.banco, REF + timedelta(days=P.DIAS_NOS_DOIS + 1))
        self.assertTrue(any(c.pergunta.categoria == "nos_dois" for c in P._candidatas(ctx, ("profundas", "nos_dois"))))

    def test_de_acompanhamento_volta_depois_de_um_tempo(self):
        self.encher()
        self.registrar(NOS_DOIS)
        p = P.pergunta(200)
        self.assertFalse(P._disponivel(p, P._contexto(self.banco, REF + timedelta(days=p.repete_dias - 1))))
        self.assertTrue(P._disponivel(p, P._contexto(self.banco, REF + timedelta(days=p.repete_dias))))
        # A que não é de acompanhamento nunca volta.
        P._marcar_feita(self.banco, P.pergunta(204), REF, origem="tela", feita_por="botao")
        self.assertFalse(P._disponivel(P.pergunta(204), P._contexto(self.banco, REF + timedelta(days=400))))


# ====================================================================== rotas e ações

class TestRotas(BaseNucleo):
    def test_visao_geral(self):
        r = self.pedir("GET", "/api/perguntas")
        self.assertEqual(r.status, 200, r.dados)
        d = r.dados
        self.assertEqual(len(d["categorias"]), len(D.CATEGORIAS))
        self.assertEqual(sum(c["total"] for c in d["categorias"]), len(D.PERGUNTAS))
        self.assertIsNone(d["cartao"])
        self.assertEqual((d["registros"], d["minimo_registros"]), (0, P.MIN_REGISTROS))
        self.assertEqual(d["ajustes"], {P.CHAVE_DIAS: 3, P.CHAVE_NOS_DOIS: False})
        self.encher()
        d = self.pedir("GET", "/api/perguntas").dados
        self.assertEqual(d["cartao"]["estado"], "nova")
        self.assertEqual(self.linhas(), [])                    # a tela só olha

    def test_rotas_pedem_token(self):
        for metodo, caminho in (("GET", "/api/perguntas"), ("POST", "/api/perguntas/31/guardar"),
                                ("GET", "/api/perguntas/respostas"), ("DELETE", "/api/perguntas/linhas/1")):
            with self.subTest(caminho=caminho):
                self.assertEqual(self.pedir(metodo, caminho, {} if metodo != "GET" else None, token=None).status, 401)

    def test_pergunta_que_nao_existe(self):
        self.assertEqual(self.pedir("POST", "/api/perguntas/9999/guardar", {}).status, 404)
        r = self.pedir("POST", "/api/perguntas/outra", {"pergunta_id": 9999})
        self.assertEqual((r.status, r.dados["erro"]), (400, P.PERGUNTA_DESCONHECIDA))

    def test_vou_perguntar_vira_lembrete_no_hoje(self):
        self.encher()
        pid = self.cartao_do_hoje()["dados"]["pergunta_id"]
        r = self.pedir("POST", f"/api/perguntas/{pid}/guardar", {"origem": "sugestao"})
        self.assertEqual(r.dados["cartao"]["dados"]["estado"], "guardada")
        c = self.cartao_do_hoje()
        self.assertEqual((c["dados"]["pergunta_id"], c["dados"]["estado"]), (pid, "guardada"))
        self.assertTrue(c["texto"].startswith("Você guardou esta pergunta"))
        # 15 dias depois: continua na lista, mas o Hoje volta a sugerir outras.
        self.dia = REF + timedelta(days=P.VALIDADE_GUARDADA + 1)
        c = self.cartao_do_hoje()
        self.assertNotEqual(c["dados"]["pergunta_id"], pid)
        self.assertEqual([g["pergunta_id"] for g in self.pedir("GET", "/api/perguntas").dados["guardadas"]], [pid])

    def test_outra_troca_na_hora_e_a_pulada_nao_volta(self):
        self.encher()
        primeira = self.cartao_do_hoje()["dados"]["pergunta_id"]
        r = self.pedir("POST", "/api/perguntas/outra", {"pergunta_id": primeira})
        self.assertEqual(r.status, 200, r.dados)
        segunda = r.dados["cartao"]["dados"]["pergunta_id"]
        self.assertNotEqual(segunda, primeira)
        self.assertEqual(self.cartao_do_hoje()["dados"]["pergunta_id"], segunda)
        status = {l["pergunta_id"]: l["status"] for l in self.linhas()}
        self.assertEqual((status[primeira], status[segunda]), ("pulada", "sugerida"))
        for _ in range(5):
            atual = self.pedir("POST", "/api/perguntas/outra", {"pergunta_id": segunda}).dados["cartao"]["dados"]
            self.assertNotIn(atual["pergunta_id"], (primeira, segunda))
            segunda = atual["pergunta_id"]

    def test_agora_nao_some_e_volta_depois(self):
        self.encher()
        pid = self.cartao_do_hoje()["dados"]["pergunta_id"]
        r = self.pedir("POST", f"/api/perguntas/{pid}/agora-nao", {})
        self.assertEqual(r.dados, {"cartao": None, "volta_em": (REF + timedelta(days=3)).isoformat()})
        for dias in range(3):
            self.dia = REF + timedelta(days=dias)
            self.assertIsNone(self.cartao_do_hoje())
        self.dia = REF + timedelta(days=3)
        self.assertEqual(self.cartao_do_hoje()["dados"]["pergunta_id"], pid)

    def test_perguntei_marca_feita_e_some_do_hoje(self):
        self.encher()
        pid = self.cartao_do_hoje()["dados"]["pergunta_id"]
        r = self.pedir("POST", f"/api/perguntas/{pid}/perguntei", {"origem": "sugestao"})
        self.assertIsNone(r.dados["cartao"])
        self.assertTrue(r.dados["texto_registrar"].startswith("Eu perguntei: “"))
        self.assertIsNone(self.cartao_do_hoje())
        l, = [l for l in self.linhas() if l["pergunta_id"] == pid]
        self.assertEqual((l["status"], l["feita_em"], l["feita_por"]), ("feita", REF.isoformat(), "botao"))

    def test_voltar_para_a_lista(self):
        self.pedir("POST", "/api/perguntas/31/pular", {})
        self.pedir("POST", "/api/perguntas/51/guardar", {})
        self.pedir("POST", "/api/perguntas/1/perguntei", {})
        for pid in (31, 51, 1):
            self.assertEqual(self.pedir("POST", f"/api/perguntas/{pid}/voltar", {}).status, 200)
        self.assertEqual([(l["pergunta_id"], l["status"]) for l in self.linhas()], [(1, "feita")])

    def test_esquecer_uma_linha_tambem_nas_copias(self):
        self.registrar(SONHO)
        lid = self.linhas()[0]["id"]
        copia = Banco.de_bytes(self.banco.para_bytes())
        self.addCleanup(copia.fechar)
        repetir = []
        with mock.patch.object(self.app, "_limpar_backups", side_effect=repetir.append):
            self.assertEqual(self.pedir("DELETE", f"/api/perguntas/linhas/{lid}").status, 200)
        self.assertEqual(self.linhas(), [])
        repetir[0](copia)
        self.assertEqual(copia.consultar(f"SELECT * FROM {P.TABELA}"), [])
        self.assertEqual(self.pedir("DELETE", f"/api/perguntas/linhas/{lid}").status, 404)

    def test_respostas_pela_rota(self):
        self.registrar(SONHO)
        self.registrar(NOS_DOIS)
        r = self.pedir("GET", "/api/perguntas/respostas", consulta={"trilha": "nos_dois"})
        self.assertEqual([x["pergunta_id"] for x in r.dados], [200])
        r = self.pedir("GET", "/api/perguntas/respostas", consulta={"trilha": "sonhos"})
        self.assertEqual([x["pergunta_id"] for x in r.dados], [31])
        self.assertEqual(len(self.pedir("GET", "/api/perguntas/respostas").dados), 2)
        self.assertEqual(self.pedir("GET", "/api/perguntas/respostas", consulta={"trilha": "x"}).status, 400)
        self.assertEqual(self.pedir("GET", "/api/perguntas/respostas", consulta={"desde": "30/09"}).status, 400)

    def test_cuidado_outra_traz_outra_de_cuidado(self):
        self.encher()
        texto = "Ela está triste hoje."
        self.pedir("POST", "/api/registros", {"texto": texto, "data_registro": REF.isoformat(), "itens": [
            {"tipo": "estado", "resumo": "Está triste", "trecho": texto, "certeza": "observado",
             "categorias": ["Preocupações"], "data_evento": REF.isoformat(), "polaridade": -1}]})
        c = self.cartao_do_hoje()
        self.assertEqual(c["dados"]["trilha"], "apoio")
        r = self.pedir("POST", "/api/perguntas/outra", {"pergunta_id": c["dados"]["pergunta_id"]})
        nova = r.dados["cartao"]["dados"]
        self.assertEqual(nova["trilha"], "apoio")
        self.assertNotEqual(nova["pergunta_id"], c["dados"]["pergunta_id"])
        # "Agora não" na de cuidado: quieto pelo resto do momento.
        self.pedir("POST", f"/api/perguntas/{nova['pergunta_id']}/agora-nao", {})
        self.assertIsNone(self.cartao_do_hoje())


# ====================================================================== exportar, importar, apagar

class TestDadosDoDiario(BaseNucleo):
    def test_exportar_e_juntar_em_outro_diario_sem_duplicar(self):
        self.registrar(SONHO)
        self.pedir("POST", "/api/perguntas/51/guardar", {})
        self.pedir("POST", "/api/perguntas/1/pular", {})
        self.pedir("POST", "/api/perguntas/outra", {})          # uma sugerida: não vai para o outro aparelho
        exportado = self.pedir("GET", "/api/exportar").dados
        self.assertIn("perguntas", exportado["recursos"])
        outro = tempfile.mkdtemp(prefix="entrenos-teste-perguntas-outro-")
        self.addCleanup(shutil.rmtree, outro, True)
        app = nucleo.Nucleo(outro, hoje=lambda: REF, relogio=Relogio(), atraso_pin=0)
        self.addCleanup(app.bloquear)
        token = json.loads(app.tratar("POST", "/api/configurar", corpo=json.dumps({"pin": PIN}).encode()).corpo)["token"]
        app._banco.criar_registro("Um registro que já estava lá.", REF, [], [], [])    # os ids mudam
        corpo = json.dumps({"json": json.dumps(exportado), "confirmar": True}).encode()
        r = app.tratar("POST", "/api/importar", corpo=corpo, token=token)
        self.assertEqual(r.status, 200, r.dados)
        self.assertEqual(r.dados["recursos"]["perguntas"]["novos"], 3)
        linhas = app._banco.consultar(f"SELECT * FROM {P.TABELA} ORDER BY id")
        self.assertEqual(sorted((l["pergunta_id"], l["status"]) for l in linhas), [(1, "pulada"), (31, "feita"), (51, "guardada")])
        feita, = [l for l in linhas if l["status"] == "feita"]
        texto = app._banco.registro(feita["registro_id"])["texto"]
        self.assertEqual(texto, SONHO)
        self.assertIn(feita["resposta"], texto)
        r = app.tratar("POST", "/api/importar", corpo=corpo, token=token)
        self.assertEqual(r.dados["recursos"]["perguntas"]["novos"], 0)
        self.assertEqual(len(app._banco.consultar(f"SELECT * FROM {P.TABELA}")), 3)

    def test_importar_recusa_o_que_nao_confere(self):
        rid = self.banco.criar_registro("Ela adora tulipas.", REF, [], [], [])
        dados = {"versao": 1, "perguntas_feitas": [
            {"pergunta_id": 9999, "status": "feita", "feita_em": "2026-09-01"},                  # pergunta desconhecida
            {"pergunta_id": 31, "status": "sugerida", "sugerida_em": "2026-09-01"},              # do momento de lá
            {"pergunta_id": 31, "status": "feita", "feita_em": "2026-09-01", "registro_id": 7,
             "resposta": "ela quer casar amanhã", "certeza": "confirmado"},                      # resposta inventada
            "nem é um objeto",
            {"pergunta_id": True, "status": "feita"},
        ]}
        with self.banco.transacao():
            resumo = P.importar(self.banco, dados, {7: rid})
        self.assertEqual(resumo["novos"], 1)
        l, = self.linhas()
        self.assertEqual((l["pergunta_id"], l["registro_id"], l["resposta"], l["certeza"]), (31, rid, None, None))
        with self.assertRaises(ValueError):
            P.importar(self.banco, ["lista"], {})

    def test_apagar_o_registro_apaga_a_resposta_e_a_data(self):
        rid = self.registrar(SONHO).dados["id"]
        guardada = self.registrar("Vou perguntar pra ela qual o maior medo dela.").dados["id"]
        self.assertEqual(self.pedir("DELETE", f"/api/registros/{rid}").status, 200)
        self.assertEqual(self.pedir("DELETE", f"/api/registros/{guardada}").status, 200)
        l, = self.linhas()
        self.assertEqual((l["pergunta_id"], l["status"], l["registro_id"], l["resposta"], l["feita_em"], l["certeza"]),
                         (31, "feita", None, None, None, None))
        # continua "já perguntada": não volta como sugestão
        self.assertFalse(P._disponivel(P.pergunta(31), P._contexto(self.banco, REF)))
        self.assertEqual(P.respostas(self.banco)[0]["resposta"], None)

    def test_apagar_resposta_de_nos_dois_tira_de_fora_da_ia(self):
        rid = self.registrar(NOS_DOIS).dados["id"]
        self.assertIn(rid, P.fora_da_ia(self.banco))
        self.pedir("DELETE", f"/api/registros/{rid}")
        self.assertEqual(P.fora_da_ia(self.banco), set())

    def test_apagar_nas_copias_automaticas(self):
        rid = self.registrar(SONHO).dados["id"]
        copia = Banco.de_bytes(self.banco.para_bytes())
        self.addCleanup(copia.fechar)
        copia.apagar_registro(rid)                         # o que o núcleo repete em cada cópia
        l, = copia.consultar(f"SELECT * FROM {P.TABELA}")
        self.assertEqual((l["registro_id"], l["resposta"], l["feita_em"]), (None, None, None))

    def test_resposta_esquecida_nao_fica_no_arquivo(self):
        self.registrar("Eu perguntei: “Qual é o seu maior sonho neste momento da vida?”\nEla respondeu: palavra-única-zebra.")
        lid = self.linhas()[0]["id"]
        rid = self.linhas()[0]["registro_id"]
        self.pedir("DELETE", f"/api/registros/{rid}")
        self.pedir("DELETE", f"/api/perguntas/linhas/{lid}")
        self.assertNotIn("palavra-única-zebra".encode("utf-8"), self.banco.para_bytes())


# ====================================================================== a tela (conferência estática)

class TestTela(unittest.TestCase):
    def setUp(self):
        self.js = (RAIZ / "web" / "telas" / "perguntas.js").read_text(encoding="utf-8")

    def test_sem_html_cru_nem_codigo_dinamico(self):
        for proibido in ("innerHTML", "outerHTML", "insertAdjacentHTML", "eval(", "new Function", "document.write",
                         "style=", ".style.", "localStorage", "fetch("):
            self.assertFalse(proibido in self.js, proibido)

    def test_encaixa_pelo_contrato(self):
        for chamada in ("registrarMenu('perguntas'", "registrarTela('perguntas'", "registrarCartaoHoje('pergunta'",
                        "registrarRevisaoExtra('pergunta_feita'", "registrarRevisaoExtra('pergunta_guardada'",
                        "registrarSecaoAjustes('perguntas'"):
            self.assertIn(chamada, self.js)
        # As rotas que a tela chama existem.
        caminhos = {r.caminho for r in P.rotas()}
        for usado in ("{id}/guardar", "{id}/perguntei", "{id}/pular", "{id}/agora-nao", "{id}/voltar", "outra",
                      "linhas/{id}", ""):
            self.assertIn(usado, caminhos)

    def test_campos_com_rotulo(self):
        for campo in ("aj-perguntas-dias", "aj-perguntas-nos-dois"):
            self.assertIn(f"'{campo}'", self.js)
        self.assertIn("for: 'aj-perguntas-nos-dois'", self.js)
        self.assertIn("u.campo('Dias entre uma sugestão e outra', dias", self.js)


if __name__ == "__main__":
    unittest.main()
