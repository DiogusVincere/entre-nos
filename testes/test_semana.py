"""Diário de conversa e resumo da semana (DESIGN.md, seção 20.7).

Só este recurso entra na lista (`recursos.usando(semana)`): os outros estão sendo escritos ao mesmo
tempo. Quando o resumo lê outro recurso (Promessas, Perguntas, Ciclo), o teste usa um recurso FALSO
com as colunas do contrato da seção 20, nunca o de verdade.

Cobre: o formulário "Depois de uma conversa" (o texto montado, o humor, os assuntos, o que lembrar);
a extração por regras (lembretes e assuntos, frases de conversa, gírias, sem acento, datas
relativas, negação, pergunta, ironia, fato que não é tarefa); a gravação conferida (nada inventado:
trecho literal, assunto escrito no trecho, expressão de data escrita); a semana (dia configurável,
nome AAAA-Www, janela do cartão); o resumo (por categoria, destaques com pontos explicáveis, como ela
estava, momento sensível, conversas e assuntos que voltaram, em aberto, próximos 7 dias, "Talvez",
pergunta sugerida, semana antiga, semana vazia); o Hoje (cartão do resumo, lembretes, atrasado,
dispensar); as rotas; exportar/importar/apagar; e os limites de privacidade (ciclo fora do resumo e do
Hoje, nada vai para a IA, log e histórico sem texto).

Pasta de dados temporária (ENTRENOS_DADOS), data fixa 30/09/2026 (quarta), scrypt pequeno.
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

from entrenos import cofre, motor, nucleo, recursos
from entrenos.banco import Banco
from entrenos.modelos import Resposta
from entrenos.motor import Conhecimento
from entrenos.recursos import semana
from testes.test_servidor import PIN, REF, Relogio

DATA = REF.isoformat()
RAIZ = Path(os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))
IA = {"provedor": "ollama", "url": "http://127.0.0.1:11434", "modelo": "modelo-de-teste", "tipo": "ollama",
      "chave": None}
_PADRAO = object()

_remendos: list = []
_env_antigo: str | None = None
_pasta_env: str | None = None


def setUpModule() -> None:
    global _env_antigo, _pasta_env
    _env_antigo = os.environ.get("ENTRENOS_DADOS")
    _pasta_env = tempfile.mkdtemp(prefix="entrenos-teste-semana-env-")
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


def dia(n: int) -> date:
    return REF + timedelta(days=n)


def extras(texto: str, referencia: date = REF) -> list[dict]:
    """O que o recurso propõe para o texto, como a revisão recebe (passando pelo motor e pela fundação)."""
    with recursos.usando(semana):
        proposta = motor.MotorRegras().analisar(texto, referencia, Conhecimento(pessoas=[], nome_dela="Julia"))
    return proposta.extras.get("semana", [])


def lembretes_de(texto: str, **kw) -> list[dict]:
    return [e for e in extras(texto, **kw) if e["tipo"] == "conversa_lembrete"]


def assuntos_de(texto: str) -> list[str]:
    achados = [e for e in extras(texto) if e["tipo"] == "conversa_assuntos"]
    return achados[0]["assuntos"] if achados else []


# ---------------------------------------------------------------- recursos falsos (contrato da seção 20)

def falso_promessas() -> types.ModuleType:
    m = types.ModuleType("testes.falso_promessas")
    m.NOME = "promessas"
    m.ESQUEMA = ["CREATE TABLE promessas (id INTEGER PRIMARY KEY AUTOINCREMENT, texto TEXT NOT NULL, trecho TEXT,"
                 " prazo TEXT, precisao TEXT, status TEXT NOT NULL DEFAULT 'aberta', registro_id INTEGER,"
                 " criado_em TEXT NOT NULL, concluida_em TEXT)"]
    m.ESQUEMA_VERSAO = 1
    return m


def falso_perguntas(resposta=None, erro: bool = False) -> types.ModuleType:
    m = types.ModuleType("testes.falso_perguntas")
    m.NOME = "perguntas"
    m.chamadas = []

    def sugestao_atual(banco, referencia):
        m.chamadas.append(referencia)
        if erro:
            raise RuntimeError("Mariana adora tulipas")
        return resposta

    m.sugestao_atual = sugestao_atual
    return m


def falso_ciclo() -> types.ModuleType:
    m = types.ModuleType("testes.falso_ciclo")
    m.NOME = "ciclo"
    m.ESQUEMA = ["CREATE TABLE ciclo_eventos (id INTEGER PRIMARY KEY AUTOINCREMENT, data TEXT, tipo TEXT, valor TEXT,"
                 " trecho TEXT, registro_id INTEGER, criado_em TEXT)"]
    m.ESQUEMA_VERSAO = 1
    m.fora_da_ia = lambda banco: {l["registro_id"] for l in banco.consultar(
        "SELECT registro_id FROM ciclo_eventos WHERE registro_id IS NOT NULL")}
    return m


class Coletor(logging.Handler):
    def __init__(self):
        super().__init__()
        self.mensagens: list[str] = []

    def emit(self, record):
        self.mensagens.append(record.getMessage())


# ======================================================================== depois de uma conversa

class TestConversa(unittest.TestCase):
    def test_formulario_inteiro_vira_um_texto_com_contexto_e_humor(self):
        r = semana.montar_conversa({
            "onde": "no jantar", "contou": "A mae dela vai operar dia 20.\nEla disse que o trabalho ta puxado.",
            "humor": "cansada, mas animada com a viagem", "assuntos": "trabalho\na viagem pra praia\na irmã dela",
            "lembrar": "- perguntar do exame na sexta\n- comprar o presente da mãe dela"})
        self.assertEqual(r["texto"],
                         "A mae dela vai operar dia 20.\nEla disse que o trabalho ta puxado.\n\n"
                         "Ela estava cansada, mas animada com a viagem.\n"
                         "Assuntos importantes: trabalho, a viagem pra praia e a irmã dela.\n"
                         "Lembrar: perguntar do exame na sexta.\n"
                         "Lembrar: comprar o presente da mãe dela.")
        self.assertEqual((r["contexto"], r["humor"]), ("no jantar", "cansada, mas animada com a viagem"))

    def test_so_o_que_ela_contou_fica_como_foi_escrito(self):
        r = semana.montar_conversa({"contou": "  ela tava de boa hj, falou mt da prima  ", "humor": "", "onde": " "})
        self.assertEqual(r, {"texto": "ela tava de boa hj, falou mt da prima", "contexto": None, "humor": None})

    def test_como_ela_estava_vira_frase_sem_inventar_palavra(self):
        casos = {
            "cansada": "Ela estava cansada.",
            "meio pra baixo...": "Ela estava meio pra baixo.",
            "brava kkkk": "Ela estava brava.",
            "tava feliz da vida": "Ela tava feliz da vida.",
            "tá estressada com o trampo": "Ela tá estressada com o trampo.",
            "ela chorou um pouco": "Ela chorou um pouco.",
            "chorou no carro": "Ela chorou no carro.",
            "parecia preocupada": "Ela parecia preocupada.",
            "com dor de cabeça": "Ela estava com dor de cabeça.",
            "   ": "",
        }
        for humor, esperado in casos.items():
            with self.subTest(humor=humor):
                self.assertEqual(semana.frase_do_humor(humor), esperado)

    def test_assuntos_com_virgula_ficam_como_escritos_e_por_linha_viram_lista(self):
        self.assertEqual(semana.lista_de_assuntos("trabalho, viagem e a mãe dela"), "trabalho, viagem e a mãe dela")
        self.assertEqual(semana.lista_de_assuntos("trabalho; viagem;\n\n- a mãe dela."), "trabalho, viagem e a mãe dela")
        self.assertEqual(semana.lista_de_assuntos("só o casamento"), "só o casamento")

    def test_o_que_lembrar_vira_uma_frase_por_linha(self):
        self.assertEqual(semana.frases_de_lembrar("1) ligar pro pai dela\n* reservar o restaurante!\n\npreciso comprar flores"),
                         ["Lembrar: ligar pro pai dela.", "Lembrar: reservar o restaurante.", "Preciso comprar flores."])

    def test_vazio_ou_formato_errado_e_400(self):
        for campos in ({}, {"contou": "  ", "humor": "", "lembrar": "\n- \n"}, {"onde": "no carro"}):
            with self.subTest(campos=campos), self.assertRaises(ValueError) as erro:
                semana.montar_conversa(campos)
            self.assertEqual(str(erro.exception), semana.CONVERSA_VAZIA)
        for campos in ([], {"contou": 12}, {"humor": ["cansada"]}):
            with self.subTest(campos=campos), self.assertRaises(ValueError):
                semana.montar_conversa(campos)
        with self.assertRaises(ValueError) as erro:
            semana.montar_conversa({"contou": "x", "onde": "a" * 201})
        self.assertIn("Onde ou como foi", str(erro.exception))

    def test_humor_comprido_vai_inteiro_no_texto_e_curto_no_campo(self):
        humor = "cansada porque dormiu mal, preocupada com a mãe dela e meio irritada com a chefe que pegou no pé"
        r = semana.montar_conversa({"humor": humor})
        self.assertIn(humor, r["texto"])
        self.assertLessEqual(len(r["humor"]), semana.MAX_HUMOR)
        self.assertTrue(r["humor"].endswith("…"))
        self.assertTrue(humor.startswith(r["humor"][:-1]))

    def test_texto_montado_e_lido_pelas_regras_como_sempre(self):
        r = semana.montar_conversa({"contou": "Ela disse que adora tulipas amarelas.", "humor": "cansada",
                                    "assuntos": "trabalho, a viagem", "lembrar": "perguntar do exame na sexta"})
        with recursos.usando(semana):
            p = motor.MotorRegras().analisar(r["texto"], REF, Conhecimento())
        tipos = {i.tipo.value for i in p.itens}
        self.assertIn("estado", tipos)                      # "Ela estava cansada."
        self.assertIn("preferencia", tipos)                 # o que ela contou, como sempre
        self.assertEqual([e["tipo"] for e in p.extras["semana"]], ["conversa_assuntos", "conversa_lembrete"])


# ======================================================================== extração: lembretes

class TestExtrairLembretes(unittest.TestCase):
    def um(self, texto: str, **kw) -> dict:
        achados = lembretes_de(texto, **kw)
        self.assertEqual(len(achados), 1, achados)
        e = achados[0]
        self.assertIn(e["trecho"], texto)                           # nunca inventar: trecho literal
        return e

    def nenhum(self, texto: str, **kw) -> None:
        self.assertEqual(lembretes_de(texto, **kw), [], texto)

    def test_marcador_lembrar_com_data_relativa(self):
        e = self.um("Lembrar: perguntar do exame na sexta.")
        self.assertEqual((e["texto"], e["data"], e["precisao"], e["data_expressao"]),
                         ("Perguntar do exame", "2026-10-02", "dia", "sexta"))
        self.assertEqual(e["resumo"], "Perguntar do exame — sexta (02/10)")
        self.assertFalse(e["pedido_dela"])

    def test_preciso_lembrar_sem_acento_e_amanha(self):
        e = self.um("preciso lembrar de comprar racao pro gato dela amanha")
        self.assertEqual((e["texto"], e["data"], e["data_expressao"]), ("Comprar racao pro gato dela", "2026-10-01", "amanha"))

    def test_nao_posso_esquecer_e_lembrete_e_nao_e_negacao(self):
        e = self.um("Não posso esquecer de levar o carregador dela no sábado!")
        self.assertEqual((e["texto"], e["data"]), ("Levar o carregador dela", "2026-10-03"))
        e = self.um("obs: n esquecer de mandar msg pro irmão dela")
        self.assertEqual((e["texto"], e["data"]), ("Mandar msg pro irmão dela", None))

    def test_girias_e_abreviacoes(self):
        casos = {
            "tenho q lembrar de pagar o ingresso do show dela": "Pagar o ingresso do show dela",
            "mano, preciso me lembrar de ligar pro pai dela kkk": "Ligar pro pai dela",
            "Lembrete: devolver o livro que ela me emprestou": "Devolver o livro que ela me emprestou",
        }
        for texto, esperado in casos.items():
            with self.subTest(texto=texto):
                self.assertEqual(self.um(texto)["texto"], esperado)

    def test_como_foi_depois_nao_joga_o_dia_para_tras(self):
        # Revisão de 07/10: o "foi" de "como foi" lia a sexta no passado e o lembrete ficava sem data.
        for texto in ("Lembrar: perguntar na sexta como foi a conversa com o RH.",
                      "Lembrar: perguntar sexta como foi a conversa com o RH."):
            with self.subTest(texto=texto):
                [l] = lembretes_de(texto)
                self.assertEqual((l["data"], l["texto"]), ("2026-10-02", "Perguntar como foi a conversa com o RH"))

    def test_data_que_ja_passou_nao_vira_prazo(self):
        e = self.um("Ontem eu pensei: preciso lembrar de pedir a receita do bolo pra mãe dela.")
        self.assertEqual((e["texto"], e["data"], e["data_expressao"]), ("Pedir a receita do bolo pra mãe dela", None, None))

    def test_data_antes_do_gatilho_vale_e_fica_no_trecho(self):
        e = self.um("Amanhã preciso lembrar de levar o casaco dela.")
        self.assertEqual((e["texto"], e["data"], e["data_expressao"]), ("Levar o casaco dela", "2026-10-01", "Amanhã"))

    def test_mes_inteiro_guarda_a_precisao(self):
        e = self.um("preciso lembrar de comprar o presente dela em dezembro")
        self.assertEqual((e["texto"], e["data"], e["precisao"]), ("Comprar o presente dela", "2026-12-01", "mes"))
        self.assertEqual(e["resumo"], "Comprar o presente dela — em dezembro")

    def test_data_ambigua_leva_as_leituras_para_a_revisao(self):
        e = self.um("Lembrar que tenho que reservar o restaurante sexta que vem")
        self.assertEqual((e["texto"], e["data"]), ("Reservar o restaurante", "2026-10-02"))
        self.assertEqual(e["data_alternativas"], ["2026-10-02", "2026-10-09"])

    def test_pedido_dela(self):
        e = self.um("Ela pediu pra eu lembrar ela de tomar o remédio amanhã")
        self.assertEqual((e["texto"], e["data"], e["pedido_dela"]), ("Lembrar ela de tomar o remédio", "2026-10-01", True))

    def test_fato_sobre_ela_nao_e_tarefa(self):
        for texto in ("Lembrar: ela não gosta de cebola.", "tenho que lembrar que ela odeia cebola",
                      "preciso lembrar do que ela falou ontem", "Lembrar: dela."):
            with self.subTest(texto=texto):
                self.nenhum(texto)

    def test_negacao_pergunta_ironia_e_outra_pessoa_ficam_de_fora(self):
        for texto in ("não preciso lembrar de nada kkk", "nem preciso lembrar de levar guarda-chuva",
                      "preciso lembrar de quê?", "preciso lembrar de comprar flores sqn",
                      "ela nao esquece de nada", "a mãe dela precisa lembrar de tomar o remédio",
                      "ela não pediu pra eu lembrar de nada", "lembrei de comprar o pão"):
            with self.subTest(texto=texto):
                self.nenhum(texto)

    def test_dois_lembretes_no_mesmo_texto(self):
        achados = lembretes_de("Lembrar: ligar pro pai dela sexta.\nLembrar: reservar o restaurante.")
        self.assertEqual([(e["texto"], e["data"]) for e in achados],
                         [("Ligar pro pai dela", "2026-10-02"), ("Reservar o restaurante", None)])

    def test_cada_palavra_do_lembrete_esta_no_trecho(self):
        textos = ["Lembrar: perguntar do exame na sexta.", "preciso lembrar de comprar racao pro gato dela amanha",
                  "Não posso esquecer de levar o carregador dela no sábado!", "Lembrar: o aniversário da mãe dela dia 12."]
        for texto in textos:
            for e in lembretes_de(texto):
                with self.subTest(texto=texto):
                    palavras_trecho = set(re.findall(r"\w+", e["trecho"].lower()))
                    self.assertLessEqual(set(re.findall(r"\w+", e["texto"].lower())), palavras_trecho)
                    if e["data_expressao"]:
                        self.assertIn(e["data_expressao"], e["trecho"])


# ======================================================================== extração: assuntos

class TestExtrairAssuntos(unittest.TestCase):
    def test_marcador_do_formulario(self):
        self.assertEqual(assuntos_de("Assuntos importantes: trabalho, a viagem pra praia e a irmã dela."),
                         ["trabalho", "a viagem pra praia", "a irmã dela"])

    def test_conversamos_falamos_e_o_assunto_foi(self):
        casos = {
            "conversamos muito sobre a viagem e sobre a mãe dela": ["a viagem", "a mãe dela"],
            "Ela falou do trabalho dela, que tá puxado": ["trabalho dela"],
            "a gente conversou bastante sobre o casamento da prima": ["o casamento da prima"],
            "o assunto foi a mudança de emprego": ["a mudança de emprego"],
            "batemos um papo sobre series e filmes de terror": ["series", "filmes de terror"],
            "conversamos sobre a viagem e ela ficou animada": ["a viagem"],
        }
        for texto, esperado in casos.items():
            with self.subTest(texto=texto):
                self.assertEqual(assuntos_de(texto), esperado)

    def test_abreviacao_e_fim_da_lista(self):
        # Revisão de 07/10: "mt" escondia os assuntos; "e foi tenso" e "; ela disse" viravam assuntos.
        self.assertEqual(assuntos_de("a gente falou mt sobre a mãe dela e sobre o estágio"), ["a mãe dela", "o estágio"])
        self.assertEqual(assuntos_de("Discutimos sobre dinheiro e foi tenso."), ["dinheiro"])
        self.assertEqual(assuntos_de("Conversamos sobre casamento e filhos; ela disse que quer esperar."),
                         ["casamento", "filhos"])

    def test_o_que_nao_e_assunto(self):
        for texto in ("a gente falou de tudo", "nao conversamos sobre o casamento", "nem falamos do aniversário",
                      "a mae dela falou sobre a reforma", "conversamos sobre isso?", "falamos sobre coisas, sqn",
                      "Ela disse que o trabalho ta puxado."):
            with self.subTest(texto=texto):
                self.assertEqual(assuntos_de(texto), [])

    def test_assuntos_sao_pedacos_do_trecho(self):
        for texto in ("Assuntos importantes: trabalho; viagem e a irmã dela.",
                      "conversamos sobre o show, a faculdade e o cachorro dela"):
            [e] = [x for x in extras(texto) if x["tipo"] == "conversa_assuntos"]
            with self.subTest(texto=texto):
                for a in e["assuntos"]:
                    self.assertIn(a, e["trecho"])

    def test_chave_junta_artigo_e_dela(self):
        self.assertEqual(semana.chave_assunto("o trabalho dela"), "trabalho")
        self.assertEqual(semana.chave_assunto("Trabalho"), "trabalho")
        self.assertEqual(semana.chave_assunto("a viagem pra Praia"), "viagem pra praia")
        self.assertEqual(semana.chave_assunto("do casamento"), "casamento")


# ======================================================================== a semana

class TestPeriodo(unittest.TestCase):
    def test_semana_padrao_termina_no_domingo_e_tem_o_nome_iso(self):
        p = semana.periodo_de(REF)
        self.assertEqual((p.inicio, p.fim, p.id), (date(2026, 9, 28), date(2026, 10, 4), "2026-W40"))
        self.assertEqual(semana.periodo_de(date(2026, 10, 4)), p)            # o próprio domingo
        self.assertEqual(semana.periodo_de(date(2026, 10, 5)).id, "2026-W41")

    def test_dia_configuravel(self):
        sexta = semana.dia_do_resumo({"semana_dia": "sexta-feira"})
        self.assertEqual(sexta, 4)
        p = semana.periodo_de(REF, sexta)
        self.assertEqual((p.inicio, p.fim, p.id), (date(2026, 9, 26), date(2026, 10, 2), "2026-W40"))
        self.assertEqual(semana.periodo_de(date(2026, 10, 3), sexta).id, "2026-W41")
        for estranho in ("feriado", 9, True, None, ["domingo"]):
            self.assertEqual(semana.dia_do_resumo({"semana_dia": estranho}), 6)
        self.assertEqual(semana.dia_do_resumo({"semana_dia": "Sábado"}), 5)

    def test_nome_ida_e_volta_e_nome_invalido(self):
        for dia_resumo in range(7):
            for d in (REF, date(2026, 12, 31), date(2027, 1, 1), date(2020, 12, 30)):
                p = semana.periodo_de(d, dia_resumo)
                with self.subTest(dia=dia_resumo, d=d):
                    self.assertEqual(semana.periodo_por_id(p.id, dia_resumo), p)
                    self.assertEqual(semana.periodo_por_id(p.id.lower(), dia_resumo), p)
        for ruim in ("2026-40", "2026-W54", "W40", "", None, 2026, "2026-W00"):
            with self.subTest(ruim=ruim), self.assertRaises(ValueError):
                semana.periodo_por_id(ruim)

    def test_janela_do_cartao_e_tela_padrao(self):
        self.assertIsNone(semana.periodo_do_cartao(REF))                      # quarta: o domingo foi há 3 dias
        for d in (date(2026, 10, 4), date(2026, 10, 5), date(2026, 10, 6)):
            self.assertEqual(semana.periodo_do_cartao(d).id, "2026-W40")
        self.assertIsNone(semana.periodo_do_cartao(date(2026, 10, 7)))
        self.assertEqual(semana.periodo_padrao(date(2026, 10, 5)).id, "2026-W40")    # o que acabou de fechar
        self.assertEqual(semana.periodo_padrao(date(2026, 10, 7)).id, "2026-W41")    # a semana de agora

    def test_quando_em_palavras(self):
        casos = {dia(0): "hoje", dia(-1): "ontem", dia(-2): "anteontem", dia(-3): "no domingo (27/09)",
                 dia(-4): "no sábado (26/09)", dia(-5): "na sexta (25/09)", dia(1): "amanhã", dia(2): "sexta (02/10)",
                 dia(12): "em 12/10", date(2027, 1, 3): "em 03/01/2027"}
        for d, esperado in casos.items():
            with self.subTest(d=d):
                self.assertEqual(semana.quando(d, REF), esperado)
        self.assertEqual(semana.quando_data(date(2026, 10, 5), "semana", REF), "na semana de 05/10")
        self.assertEqual(semana.quando_data(date(2027, 2, 1), "mes", REF), "em fevereiro de 2027")


# ======================================================================== núcleo (gravação, resumo, Hoje, rotas)

class BaseNucleo(unittest.TestCase):
    modulos: tuple = ()

    def setUp(self):
        self.enterContext(recursos.usando(semana, *self.modulos))
        self.pasta = tempfile.mkdtemp(prefix="entrenos-teste-semana-")
        self.addCleanup(shutil.rmtree, self.pasta, True)
        self.app = nucleo.Nucleo(self.pasta, hoje=lambda: REF, relogio=Relogio(), atraso_pin=0)
        self.addCleanup(self.app.bloquear)
        self.token = None
        r = self.pedir("POST", "/api/configurar", {"pin": PIN, "nome_dela": "Julia"})
        self.assertEqual(r.status, 200, r.dados)
        self.token = r.dados["token"]

    @property
    def banco(self) -> Banco:
        return self.app._banco

    def pedir(self, metodo, caminho, corpo=None, token=_PADRAO, **kw) -> nucleo.RespostaApi:
        bruto = None if corpo is None else json.dumps(corpo).encode("utf-8")
        resposta = self.app.tratar(metodo, caminho, corpo=bruto, token=self.token if token is _PADRAO else token, **kw)
        self.assertEqual(json.loads(resposta.corpo.decode("utf-8")), resposta.dados)
        return resposta

    def analisar(self, texto: str, data: str = DATA) -> dict:
        r = self.pedir("POST", "/api/analisar", {"texto": texto, "data_registro": data})
        self.assertEqual(r.status, 200, r.dados)
        return r.dados

    def registrar(self, texto: str, data: str = DATA, confirmar=semana.TIPOS_EXTRAS, mudar=None, **mais) -> int:
        p = self.analisar(texto, data)
        escolhidos = [dict(e) for e in p["extras"].get("semana", []) if e["tipo"] in confirmar]
        for e in escolhidos:
            e.update(mudar or {})
        corpo = {"texto": p["texto"], "data_registro": data, "itens": p["itens"], "pessoas": p["pessoas"],
                 "relacoes": p["relacoes"], "extras": {"semana": escolhidos} if escolhidos else {}, **mais}
        r = self.pedir("POST", "/api/registros", corpo)
        self.assertEqual(r.status, 200, r.dados)
        return r.dados["id"]

    def conversa(self, data: str = DATA, **campos) -> int:
        r = self.pedir("POST", "/api/semana/conversa", campos)
        self.assertEqual(r.status, 200, r.dados)
        return self.registrar(r.dados["texto"], data, contexto=r.dados["contexto"], humor=r.dados["humor"])

    def resumo(self, data: date = REF, semana_id: str | None = None) -> dict:
        consulta = {"data": data.isoformat()}
        if semana_id:
            consulta["semana"] = semana_id
        r = self.pedir("GET", "/api/semana", consulta=consulta)
        self.assertEqual(r.status, 200, r.dados)
        return r.dados

    def hoje(self, data: date = REF) -> list[dict]:
        r = self.pedir("GET", "/api/hoje", consulta={"data": data.isoformat()})
        self.assertEqual(r.status, 200, r.dados)
        return [a for a in r.dados["alertas"] if a.get("recurso") == "semana"]

    def lembretes(self) -> list[dict]:
        return self.banco.consultar(f"SELECT * FROM {semana.T_LEMBRETES} ORDER BY id")

    def assuntos(self) -> list[dict]:
        return self.banco.consultar(f"SELECT * FROM {semana.T_ASSUNTOS} ORDER BY id")

    def semana_cheia(self) -> dict[str, int]:
        """Uma semana real (28/09 a 04/10): o que ela contou, uma pendência, um evento que preocupa,
        uma alegria e uma conversa com humor, assuntos e lembretes."""
        return {
            "seg": self.registrar("Ela disse que adora tulipas amarelas. A chefe dela pegou no pé de novo no trabalho.",
                                  "2026-09-28"),
            "ter": self.registrar("Ela tá esperando o resultado da entrevista.", "2026-09-29"),
            "qua": self.registrar("A apresentação dela é dia 6 e ela tá nervosa.", "2026-09-30"),
            "sex": self.registrar("Ela ficou feliz com a promoção no trabalho.", "2026-10-02"),
            "sab": self.conversa("2026-10-03", onde="no jantar", contou="Ela disse que a irmã dela vai casar em maio.",
                                 humor="cansada", assuntos="trabalho, o casamento da irmã",
                                 lembrar="perguntar da entrevista na segunda\nreservar o restaurante"),
        }


class TestGravar(BaseNucleo):
    def test_confirmar_grava_lembrete_e_assuntos_com_o_registro(self):
        rid = self.conversa(contou="Ela tá esperando o resultado da entrevista.", assuntos="trabalho, a viagem",
                            lembrar="perguntar da entrevista na sexta", onde="no carro")
        [l] = self.lembretes()
        self.assertEqual((l["texto"], l["data"], l["precisao"], l["data_expressao"], l["status"], l["registro_id"]),
                         ("Perguntar da entrevista", "2026-10-02", "dia", "sexta", "aberto", rid))
        self.assertEqual(l["trecho"], "Lembrar: perguntar da entrevista na sexta.")
        self.assertEqual([(a["assunto"], a["chave"], a["registro_id"]) for a in self.assuntos()],
                         [("trabalho", "trabalho", rid), ("a viagem", "viagem", rid)])
        registro = self.pedir("GET", f"/api/registros/{rid}").dados
        self.assertEqual((registro["contexto"], registro["privado"]), ("no carro", False))   # não vira 🔒
        for linha in self.lembretes() + self.assuntos():
            self.assertIn(linha["trecho"], registro["texto"])

    def test_sem_confirmar_nada_e_gravado(self):
        r = self.pedir("POST", "/api/semana/conversa", {"assuntos": "trabalho", "lembrar": "ligar pro pai dela"})
        self.registrar(r.dados["texto"], confirmar=())
        self.assertEqual((self.lembretes(), self.assuntos()), ([], []))

    def test_texto_e_data_ajustados_na_revisao(self):
        self.registrar("Lembrar: perguntar do exame na sexta.", confirmar=("conversa_lembrete",),
                       mudar={"texto": "perguntar como foi o exame dela", "data": "2026-10-03"})
        [l] = self.lembretes()
        self.assertEqual((l["texto"], l["data"], l["data_expressao"]), ("Perguntar como foi o exame dela", "2026-10-03", None))

    def test_data_invalida_e_400_e_nada_gravado(self):
        p = self.analisar("Lembrar: perguntar do exame na sexta.")
        [e] = p["extras"]["semana"]
        e["data"] = "sexta-feira"
        r = self.pedir("POST", "/api/registros", {"texto": p["texto"], "data_registro": DATA, "itens": p["itens"],
                                                  "pessoas": p["pessoas"], "relacoes": [], "extras": {"semana": [e]}})
        self.assertEqual((r.status, r.dados["erro"]), (400, semana.DATA_INVALIDA))
        self.assertEqual((self.pedir("GET", "/api/registros").dados, self.lembretes()), ([], []))

    def test_assunto_e_expressao_que_nao_estao_no_trecho_nao_entram(self):
        self.registrar("Assuntos importantes: trabalho e a viagem.\nLembrar: ligar pro pai dela sexta.",
                       mudar={"assuntos": ["trabalho", "a viagem", "a ex dela"], "data_expressao": "no Natal"})
        self.assertEqual([a["assunto"] for a in self.assuntos()], ["trabalho", "a viagem"])
        [l] = self.lembretes()
        self.assertEqual((l["data"], l["data_expressao"]), ("2026-10-02", None))

    def test_mesmo_lembrete_contado_de_novo_nao_duplica(self):
        self.registrar("Lembrar: ligar pro pai dela.")
        self.registrar("De novo, preciso lembrar de ligar pro pai dela.", "2026-10-01")
        self.assertEqual(len(self.lembretes()), 1)
        self.registrar("Lembrar: ligar pro pai dela sábado.", "2026-10-01")             # com data: outra coisa
        self.assertEqual(len(self.lembretes()), 2)


class TestResumo(BaseNucleo):
    modulos = ()

    def test_o_que_ela_contou_por_categoria_citando(self):
        ids = self.semana_cheia()
        r = self.resumo(date(2026, 10, 4))
        self.assertEqual((r["semana"], r["inicio"], r["fim"], r["registros"], r["atual"]),
                         ("2026-W40", "2026-09-28", "2026-10-04", 5, True))
        categorias = [g["categoria"] for g in r["contou"]]
        self.assertEqual(categorias[0], "Trabalho")
        trabalho = r["contou"][0]
        self.assertEqual(trabalho["registros"], 3)
        textos = {e["trecho"] for g in r["contou"] for e in g["itens"]}
        self.assertIn("Ela disse que adora tulipas amarelas.", textos)
        registros = {rid: self.pedir("GET", f"/api/registros/{rid}").dados["texto"] for rid in ids.values()}
        for g in r["contou"]:
            for e in g["itens"]:
                self.assertIn(e["trecho"], registros[e["registro_id"]])           # cada citação é literal
        # As frases montadas ("Assuntos importantes:", "Lembrar:") não são algo que ela contou.
        self.assertFalse(any(t.startswith(("Assuntos importantes", "Lembrar:")) for t in textos))

    def test_o_que_ela_contou_nao_tem_frase_sua(self):
        # Revisão de 07/10: a sua promessa e o seu desentendimento apareciam em "O que ela contou".
        self.registrar("Ela disse que adora tulipas amarelas.", "2026-09-28")
        self.registrar("Prometi levar ela no cinema sábado.", "2026-09-29")
        self.registrar("A gente discutiu por causa do celular, eu fui grosso.", "2026-09-30")
        r = self.resumo(date(2026, 10, 4))
        textos = [e["trecho"] for g in r["contou"] for e in g["itens"]] + [e["trecho"] for e in r["destaques"]]
        self.assertIn("Ela disse que adora tulipas amarelas.", textos)
        self.assertFalse([t for t in textos if "Prometi" in t or "discutiu" in t], textos)

    def test_destaques_com_pontos_explicaveis(self):

        self.semana_cheia()
        r = self.resumo(date(2026, 10, 4))
        primeiro = r["destaques"][0]
        self.assertEqual(primeiro["resumo"], "Apresentação — 06/10/2026")
        self.assertEqual(primeiro["motivos"], ["tem data marcada (06/10)", "é importante", "o assunto voltou em 3 registros"])
        self.assertEqual(primeiro["pontos"], 8)
        tulipas = next(e for e in r["destaques"] if "tulipas" in e["trecho"])
        self.assertEqual((tulipas["pontos"], tulipas["motivos"]), (4, ["ela mesma disse"]))
        self.assertLessEqual(len(r["destaques"]), semana.MAX_DESTAQUES)
        self.assertEqual([e["pontos"] for e in r["destaques"]], sorted((e["pontos"] for e in r["destaques"]), reverse=True))

    def test_data_do_destaque_com_a_precisao_que_foi_dita(self):
        self.registrar("Ela disse que a irmã dela vai casar em maio.", "2026-10-03")
        r = self.resumo(date(2026, 10, 4))
        [e] = [e for e in r["destaques"] if "casamento" in e["resumo"]]
        self.assertEqual(e["motivos"], ["ela mesma disse", "tem data marcada (maio de 2027)"])     # nunca "01/05"
        self.assertEqual(semana.data_dita(date(2026, 10, 5), "semana"), "semana de 05/10")
        self.assertEqual(semana.data_dita(date(2026, 10, 5), None), "05/10")

    def test_citacoes_sem_pontuacao_dobrada(self):
        self.semana_cheia()
        r = self.resumo(date(2026, 10, 4))
        textos = [r["momento"]["texto"]] + [s["porque"] for s in r["sugestoes"]] + [s["texto"] for s in r["sugestoes"]]
        for t in textos:
            with self.subTest(t=t):
                self.assertNotRegex(t, r"[.!?…]”\.")
        self.assertEqual(semana.frase("Você registrou: “Ela tá bem.”"), "Você registrou: “Ela tá bem.”")
        self.assertEqual(semana.frase("Você registrou: “Ela tá bem”"), "Você registrou: “Ela tá bem”.")

    def test_pontuar_e_puro_e_explica_cada_ponto(self):
        item = {"tipo": "restricao", "certeza": "confirmado", "importancia": 3}
        self.assertEqual(semana.pontuar(item, REF, 1), (7, ["ela mesma disse", "é um cuidado (restrição)", "é importante"]))
        nota = {"tipo": "nota", "certeza": "inferencia", "importancia": 1}
        self.assertEqual(semana.pontuar(nota, REF, 4), (1, ["o assunto voltou em 4 registros"]))

    def test_como_ela_estava_em_ordem_e_sem_diagnostico(self):
        self.semana_cheia()
        r = self.resumo(date(2026, 10, 4))
        self.assertEqual([(e["data"], e["texto"]) for e in r["estados"]],
                         [("2026-09-30", "nervosa"), ("2026-10-03", "cansada")])
        self.assertTrue(r["momento"]["sensivel"])
        self.assertEqual(r["momento"]["texto"], "Ontem você registrou: “Ela estava cansada.”")

    def test_momento_sensivel_vira_o_primeiro_talvez_com_o_que_ajuda(self):
        self.registrar("Quando ela tá cansada, ela prefere que eu só fique perto.", "2026-09-10")
        self.registrar("Ela tá muito cansada com o trabalho.", "2026-10-03")
        r = self.resumo(date(2026, 10, 4))
        primeiro = r["sugestoes"][0]
        self.assertTrue(primeiro["texto"].startswith("Talvez"))
        self.assertIn("do jeito que ela disse que ajuda", primeiro["texto"])
        self.assertIn("só fique perto", primeiro["porque"])
        self.assertEqual(len(primeiro["registro_ids"]), 2)

    def test_semana_boa_nao_e_sensivel(self):
        self.registrar("Ela tá muito cansada com o trabalho.", "2026-09-30")
        self.registrar("Ela tá super feliz com a viagem.", "2026-10-03")
        r = self.resumo(date(2026, 10, 4))
        self.assertFalse(r["momento"]["sensivel"])
        self.assertFalse(any("como ela está do que" in s["texto"] for s in r["sugestoes"]))

    def test_talvez_sempre_como_possibilidade_e_citando(self):
        self.semana_cheia()
        r = self.resumo(date(2026, 10, 4))
        self.assertTrue(r["sugestoes"])
        for s in r["sugestoes"]:
            with self.subTest(s=s["texto"]):
                self.assertIn("Talvez", s["texto"])
                self.assertTrue(s["registro_ids"])
                self.assertTrue(s["porque"])
                for proibido in ("faça", "precisa", "deve", "convencer", "fazer ela"):
                    self.assertNotIn(proibido, s["texto"].lower().replace("faça diferença", ""))

    def test_em_aberto_pendencias_e_lembretes(self):
        ids = self.semana_cheia()
        r = self.resumo(date(2026, 10, 4))
        abertos = r["abertos"]
        self.assertEqual([p["texto"] for p in abertos["pendencias"]], ["Esperando o resultado da entrevista"])
        self.assertTrue(abertos["pendencias"][0]["da_semana"])
        self.assertEqual([(l["texto"], l["data"]) for l in abertos["lembretes"]],
                         [("Perguntar da entrevista", "2026-10-05"), ("Reservar o restaurante", None)])
        self.assertEqual(abertos["lembretes"][0]["registro_id"], ids["sab"])
        self.assertEqual(abertos["total"], 3)

    def test_proximos_7_dias(self):
        self.semana_cheia()
        r = self.resumo(date(2026, 10, 4))
        self.assertEqual([(p["data"], p["texto"], p["origem"]) for p in r["proximos"]],
                         [("2026-10-05", "Lembrar: Perguntar da entrevista", "lembrete"),
                          ("2026-10-06", "Apresentação", "registro")])
        self.assertEqual([p["dias"] for p in r["proximos"]], [1, 2])
        self.assertTrue(any("Apresentação" in s["texto"] and "Talvez" in s["texto"] for s in r["sugestoes"]))

    def test_conversas_e_assuntos(self):
        ids = self.semana_cheia()
        r = self.resumo(date(2026, 10, 4))
        self.assertEqual([(c["registro_id"], c["contexto"], c["assuntos"]) for c in r["conversas"]],
                         [(ids["sab"], "no jantar", ["trabalho", "o casamento da irmã"])])
        self.assertEqual([(a["assunto"], a["vezes"], a["semanas_antes"]) for a in r["assuntos"]],
                         [("o casamento da irmã", 1, 0), ("trabalho", 1, 0)])
        self.assertEqual(r["voltaram"][0], {"rotulo": "Trabalho", "tipo": "categoria", "registros": 3,
                                            "registro_ids": [ids["seg"], ids["qua"], ids["sex"]]})

    def test_assunto_que_voltou_de_outras_semanas(self):
        self.conversa("2026-09-10", assuntos="o trabalho dela")
        self.conversa("2026-09-17", assuntos="Trabalho e a mudança")
        self.conversa("2026-09-30", assuntos="trabalho")
        r = self.resumo()
        self.assertEqual([(a["assunto"], a["semanas_antes"]) for a in r["assuntos"]], [("trabalho", 2)])

    def test_pergunta_sugerida_vem_do_recurso_perguntas_sem_gravar(self):
        dados = {"pergunta": "Qual lugar do mundo você ainda quer conhecer comigo?", "titulo": "Conheça melhor",
                 "categoria_nome": "Sonhos", "estado": "nova", "motivos": [{"texto": "Você ainda sabe pouco", "registro_ids": [1]}]}
        perguntas = falso_perguntas(dados)
        with recursos.usando(perguntas, com_os_fixos=True):
            self.registrar("Ela adora praia.")
            r = self.resumo()
        self.assertEqual(r["pergunta"]["pergunta"], dados["pergunta"])
        self.assertEqual(r["pergunta"]["motivos"], [{"texto": "Você ainda sabe pouco", "registro_ids": [1]}])
        self.assertEqual(perguntas.chamadas, [REF])
        # Semana antiga: sem pergunta (é do agora).
        with recursos.usando(perguntas, com_os_fixos=True):
            self.assertIsNone(self.resumo(semana_id="2026-W38")["pergunta"])

    def test_pergunta_que_falha_ou_vem_estranha_nao_derruba(self):
        coletor = Coletor()
        logging.getLogger("entrenos").addHandler(coletor)
        self.addCleanup(logging.getLogger("entrenos").removeHandler, coletor)
        self.registrar("Ela adora praia.")
        for falso in (falso_perguntas(erro=True), falso_perguntas({"pergunta": 3}), falso_perguntas("texto")):
            with recursos.usando(falso, com_os_fixos=True):
                self.assertIsNone(self.resumo()["pergunta"])
        self.assertFalse(any("Mariana" in m for m in coletor.mensagens))

    def test_semana_antiga_mostra_o_que_veio_dela_e_continua_em_aberto(self):
        self.registrar("Ela tá esperando o resultado da entrevista. Lembrar: ligar pro pai dela.", "2026-09-15")
        self.registrar("Ela tá esperando o resultado do exame.", "2026-09-29")
        r = self.resumo(semana_id="2026-W38")
        self.assertEqual((r["atual"], r["inicio"], r["fim"]), (False, "2026-09-14", "2026-09-20"))
        self.assertEqual([p["texto"] for p in r["abertos"]["pendencias"]], ["Esperando o resultado da entrevista"])
        self.assertEqual([l["texto"] for l in r["abertos"]["lembretes"]], ["Ligar pro pai dela"])
        self.assertEqual((r["proximos"], r["sugestoes"], r["pergunta"]), ([], [], None))
        self.assertEqual((r["anterior"], r["seguinte"]), ("2026-W37", "2026-W39"))

    def test_semana_vazia_e_futura(self):
        r = self.resumo()
        self.assertEqual((r["vazia"], r["registros"], r["contou"], r["sugestoes"]), (True, 0, [], []))
        self.assertIsNone(r["seguinte"])
        futura = self.resumo(semana_id="2026-W45")
        self.assertTrue(futura["futura"])
        self.assertEqual(self.pedir("GET", "/api/semana", consulta={"semana": "2026-W99"}).status, 400)

    def test_sem_o_recurso_promessas_o_resumo_abre_sem_elas(self):
        # A tabela do recurso Promessas (seção 20.3) só existe quando ele está aqui.
        self.registrar("Prometi levar ela ao cinema.")
        r = self.resumo()
        self.assertEqual((r["abertos"]["promessas"], r["registros"]), ([], 1))


class TestResumoComPromessas(BaseNucleo):
    modulos = (falso_promessas(),)

    def test_promessas_abertas_com_prazo_entram_no_aberto_e_nos_proximos(self):
        rid = self.registrar("Prometi levar ela ao cinema na sexta.")
        self.banco.inserir("promessas", {"texto": "Levar ela ao cinema", "trecho": "Prometi levar ela ao cinema na sexta.",
                                         "prazo": "2026-10-02", "precisao": "dia", "status": "aberta",
                                         "registro_id": rid, "criado_em": "2026-09-30T10:00:00"})
        self.banco.inserir("promessas", {"texto": "Aprender a fazer pão", "status": "aberta", "criado_em": "2026-08-01T10:00:00"})
        self.banco.inserir("promessas", {"texto": "Já foi", "status": "cumprida", "criado_em": "2026-08-01T10:00:00"})
        r = self.resumo()
        self.assertEqual([(p["texto"], p["prazo"]) for p in r["abertos"]["promessas"]],
                         [("Levar ela ao cinema", "2026-10-02"), ("Aprender a fazer pão", None)])
        self.assertIn(("2026-10-02", "Promessa: Levar ela ao cinema"), [(p["data"], p["texto"]) for p in r["proximos"]])
        antiga = self.resumo(semana_id="2026-W31")
        self.assertEqual([p["texto"] for p in antiga["abertos"]["promessas"]], ["Aprender a fazer pão"])


class TestHoje(BaseNucleo):
    def test_cartao_do_resumo_no_domingo_e_nos_dois_dias_seguintes(self):
        self.semana_cheia()
        self.assertEqual([c for c in self.hoje(date(2026, 10, 3)) if c["tipo"] == "semana_resumo"], [])
        relevancias = []
        for d in (date(2026, 10, 4), date(2026, 10, 5), date(2026, 10, 6)):
            [c] = [c for c in self.hoje(d) if c["tipo"] == "semana_resumo"]
            self.assertEqual((c["chave"], c["nivel"], c["dados"]["semana"]), ("semana:resumo:2026-W40", "lembrete", "2026-W40"))
            relevancias.append(c["relevancia"])
        self.assertEqual(relevancias, [72, 66, 60])
        self.assertEqual([c for c in self.hoje(date(2026, 10, 7)) if c["tipo"] == "semana_resumo"], [])

    def test_dados_do_cartao_resumem_sem_inventar(self):
        self.semana_cheia()
        [c] = [c for c in self.hoje(date(2026, 10, 4)) if c["tipo"] == "semana_resumo"]
        d = c["dados"]
        self.assertEqual(d["categorias"][0], {"nome": "Trabalho", "total": 3})
        self.assertEqual(d["destaques"][0]["resumo"], "Apresentação — 06/10/2026")
        self.assertEqual(d["abertos"]["total"], 3)
        self.assertEqual(d["proximos"][0]["texto"], "Lembrar: Perguntar da entrevista")
        self.assertEqual(d["momento"], "Ontem você registrou: “Ela estava cansada.”")
        self.assertTrue(d["sugestao"].startswith("Talvez"))
        self.assertIn("domingo", c["porque"])

    def test_semana_sem_registro_ou_com_um_so_nao_tem_cartao_e_ajuste_desliga(self):
        self.assertEqual(self.hoje(date(2026, 10, 4)), [])
        self.registrar("Ela adora praia.", "2026-10-01")
        self.assertEqual(self.hoje(date(2026, 10, 4)), [])             # um registro só já está em "Últimos"
        self.registrar("Ela adora sorvete.", "2026-10-02")
        self.assertEqual(len(self.hoje(date(2026, 10, 4))), 1)
        self.assertEqual(self.pedir("PATCH", "/api/config", {"semana_no_hoje": False}).status, 200)
        self.assertEqual(self.hoje(date(2026, 10, 4)), [])

    def test_dia_do_resumo_configuravel(self):
        self.registrar("Ela adora praia.", "2026-09-29")
        self.registrar("Ela adora sorvete.", "2026-09-30")
        self.assertEqual(self.pedir("PATCH", "/api/config", {"semana_dia": "sexta"}).status, 200)
        [c] = self.hoje(date(2026, 10, 2))
        self.assertEqual(c["dados"]["semana"], "2026-W40")
        self.assertEqual((c["dados"]["inicio"], c["dados"]["fim"]), ("2026-09-26", "2026-10-02"))
        self.assertEqual(len(self.hoje(date(2026, 10, 4))), 1)       # ainda dentro dos 2 dias depois
        self.assertEqual(self.hoje(date(2026, 10, 5)), [])           # domingo deixou de ser o dia

    def test_dispensar_vale_so_para_aquela_semana(self):
        self.registrar("Ela adora praia.", "2026-10-01")
        self.registrar("Ela adora sorvete.", "2026-10-02")
        [c] = self.hoje(date(2026, 10, 4))
        self.assertEqual(self.pedir("POST", "/api/alertas/dispensar", {"chave": c["chave"]}).status, 200)
        self.assertEqual(self.hoje(date(2026, 10, 5)), [])
        self.registrar("Ela adora sorvete.", "2026-10-08")
        self.registrar("Ela adora tulipas.", "2026-10-09")
        self.assertEqual([x["chave"] for x in self.hoje(date(2026, 10, 11))], ["semana:resumo:2026-W41"])

    def test_lembretes_de_hoje_e_amanha(self):
        self.registrar("Lembrar: ligar pro pai dela sexta.\nLembrar: reservar o restaurante amanhã.")
        cartoes = {c["texto"]: c for c in self.hoje()}
        self.assertEqual(set(cartoes), {"Amanhã: Reservar o restaurante."})
        c = cartoes["Amanhã: Reservar o restaurante."]
        self.assertEqual((c["nivel"], c["tipo"], c["relevancia"]), ("lembrete", "semana_lembrete", 76))
        self.assertIn("“Lembrar: reservar o restaurante amanhã.”", c["porque"])
        [hoje] = [c for c in self.hoje(dia(2)) if c["tipo"] == "semana_lembrete" and c["nivel"] == "lembrete"]
        self.assertEqual((hoje["texto"], hoje["relevancia"]), ("Hoje: Ligar pro pai dela.", 82))

    def test_lembrete_que_ficou_para_tras_e_feito_some(self):
        self.registrar("Lembrar: reservar o restaurante amanhã.")
        [c] = [c for c in self.hoje(dia(3)) if c["tipo"] == "semana_lembrete"]
        self.assertEqual(c["nivel"], "sugestao")
        self.assertIn("Talvez", c["texto"])
        self.assertTrue(c["dados"]["atrasado"])
        self.assertEqual(self.hoje(dia(10)), [])                      # passou de 7 dias: fica quieto
        [l] = self.lembretes()
        self.assertEqual(self.pedir("PATCH", f"/api/semana/lembretes/{l['id']}", {"status": "feito"}).status, 200)
        self.assertEqual(self.hoje(dia(3)), [])


class TestRotas(BaseNucleo):
    def test_resumo_padrao_e_por_semana(self):
        self.registrar("Ela adora praia.", "2026-09-22")
        self.assertEqual(self.resumo()["semana"], "2026-W40")
        self.assertEqual(self.resumo(date(2026, 10, 5))["semana"], "2026-W40")      # acabou de fechar
        antiga = self.resumo(semana_id="2026-w39")
        self.assertEqual((antiga["semana"], antiga["registros"]), ("2026-W39", 1))
        r = self.pedir("GET", "/api/semana", consulta={"semana": "semana 40"})
        self.assertEqual((r.status, r.dados["erro"]), (400, semana.SEMANA_INVALIDA))
        r = self.pedir("GET", "/api/semana", consulta={"data": "30/09/2026"})
        self.assertEqual((r.status, r.dados["erro"]), (400, semana.DATA_INVALIDA))
        self.assertEqual(self.pedir("GET", "/api/semana", token="errado").status, 401)

    def test_historico_das_semanas(self):
        self.conversa("2026-09-10", contou="Ela adora praia.", onde="no parque")
        self.registrar("Ela adora sorvete.", "2026-09-11")
        self.registrar("Ela adora tulipas.", "2026-09-29")
        r = self.pedir("GET", "/api/semana/historico", consulta={"data": DATA}).dados
        self.assertEqual(r["atual"], "2026-W40")
        self.assertEqual([(s["semana"], s["registros"], s["conversas"]) for s in r["semanas"]],
                         [("2026-W40", 1, 0), ("2026-W37", 2, 1)])
        self.assertEqual(len(self.pedir("GET", "/api/semana/historico", consulta={"limite": "1"}).dados["semanas"]), 1)
        self.assertEqual(self.pedir("GET", "/api/semana/historico", consulta={"limite": "um"}).status, 400)

    def test_conversa_pela_api(self):
        r = self.pedir("POST", "/api/semana/conversa", {"contou": "Ela adora praia.", "humor": "feliz", "onde": "no carro"})
        self.assertEqual(r.dados, {"texto": "Ela adora praia.\n\nEla estava feliz.", "contexto": "no carro", "humor": "feliz"})
        r = self.pedir("POST", "/api/semana/conversa", {"contou": ""})
        self.assertEqual((r.status, r.dados["erro"]), (400, semana.CONVERSA_VAZIA))
        self.assertEqual(self.pedir("GET", "/api/registros").dados, [])       # montar não grava nada

    def test_mudar_lembrete(self):
        self.registrar("Lembrar: ligar pro pai dela sexta.")
        [l] = self.lembretes()
        url = f"/api/semana/lembretes/{l['id']}"
        r = self.pedir("PATCH", url, {"texto": "  ligar pro seu Roberto ", "data": "2026-10-09"})
        self.assertEqual(r.status, 200, r.dados)
        self.assertEqual((r.dados["texto"], r.dados["data"], r.dados["precisao"], r.dados["data_expressao"]),
                         ("Ligar pro seu Roberto", "2026-10-09", "dia", None))
        r = self.pedir("PATCH", url, {"status": "feito"})
        self.assertEqual((r.dados["status"], r.dados["concluido_em"]), ("feito", DATA))
        lista = self.pedir("GET", "/api/semana/lembretes").dados
        self.assertEqual(([x["id"] for x in lista["abertos"]], [x["id"] for x in lista["feitos"]]), ([], [l["id"]]))
        r = self.pedir("PATCH", url, {"status": "aberto", "data": None})
        self.assertEqual((r.dados["status"], r.dados["concluido_em"], r.dados["data"]), ("aberto", None, None))
        for corpo, erro in (({"status": "talvez"}, semana.STATUS_INVALIDO), ({"data": "amanhã"}, semana.DATA_INVALIDA),
                            ({"texto": ""}, semana.TEXTO_INVALIDO), ({"texto": "x" * 301}, semana.TEXTO_INVALIDO),
                            ({"cor": "azul"}, semana.CAMPO_DESCONHECIDO.format(campo="cor")),
                            ({"precisao": "mes"}, semana.PRECISAO_INVALIDA), ({}, semana.NADA_PARA_MUDAR)):
            with self.subTest(corpo=corpo):
                r = self.pedir("PATCH", url, corpo)
                self.assertEqual((r.status, r.dados["erro"]), (400, erro))
        self.assertEqual(self.pedir("PATCH", "/api/semana/lembretes/999", {"status": "feito"}).status, 404)

    def test_apagar_lembrete_e_assunto_tambem_nas_copias_e_nas_chaves(self):
        self.registrar("Lembrar: ligar pro pai dela amanhã. Assuntos importantes: trabalho.")
        [l] = self.lembretes()
        [a] = self.assuntos()
        [c] = self.hoje()
        self.pedir("POST", "/api/alertas/dispensar", {"chave": c["chave"]})
        copia = Banco.de_bytes(self.banco.para_bytes())
        self.addCleanup(copia.fechar)
        self.app.limpar_copias = lambda apagar: apagar(copia)           # as cópias automáticas, aqui uma só
        self.assertEqual(self.pedir("DELETE", f"/api/semana/lembretes/{l['id']}").status, 200)
        self.assertEqual(self.pedir("DELETE", f"/api/semana/assuntos/{a['id']}").status, 200)
        self.assertEqual((self.lembretes(), self.assuntos()), ([], []))
        self.assertEqual(copia.consultar(f"SELECT * FROM {semana.T_LEMBRETES}"), [])
        self.assertEqual(copia.consultar(f"SELECT * FROM {semana.T_ASSUNTOS}"), [])
        self.assertFalse(any(k.startswith("semana:") for k in self.banco.alertas_dispensados()))
        self.assertFalse(any(k.startswith("semana:") for k in copia.alertas_dispensados()))
        self.assertEqual(self.pedir("DELETE", f"/api/semana/lembretes/{l['id']}").status, 404)


class TestExportarImportarApagar(BaseNucleo):
    def test_exportar_e_juntar_noutro_diario_sem_duplicar(self):
        rid = self.conversa(assuntos="trabalho, a viagem", lembrar="ligar pro pai dela sexta")
        exportado = self.pedir("GET", "/api/exportar").dados
        dados = exportado["recursos"]["semana"]
        self.assertEqual([(l["texto"], l["registro_id"], l["data"]) for l in dados["lembretes"]],
                         [("Ligar pro pai dela", rid, "2026-10-02")])
        self.assertEqual([a["assunto"] for a in dados["assuntos"]], ["trabalho", "a viagem"])
        texto = json.dumps(exportado, ensure_ascii=False)
        with recursos.usando(semana):
            destino = Banco.novo()
            self.addCleanup(destino.fechar)
            destino.criar_registro("Um registro de antes.", REF, [], [], [])
            previa = destino.importar_json(texto, previa=True)
            self.assertEqual(previa["recursos"]["semana"]["novos"], 3)
            self.assertEqual(destino.consultar(f"SELECT * FROM {semana.T_LEMBRETES}"), [])
            r = destino.importar_json(texto)
            self.assertEqual(r["recursos"]["semana"], {"novos": 3, "texto": "3 lembretes e assuntos de conversa."})
            novo = [x["id"] for x in destino.registros() if "Lembrar" in x["texto"]][0]
            self.assertEqual({l["registro_id"] for l in destino.consultar(f"SELECT * FROM {semana.T_LEMBRETES}")}, {novo})
            self.assertEqual(destino.importar_json(texto)["recursos"]["semana"]["novos"], 0)
            self.assertEqual(len(destino.consultar(f"SELECT * FROM {semana.T_ASSUNTOS}")), 2)

    def test_importar_tolerante_e_nunca_inventa(self):
        rid = self.registrar("Lembrar: ligar pro pai dela. Assuntos importantes: trabalho.")
        exportado = self.pedir("GET", "/api/exportar").dados
        dados = exportado["recursos"]["semana"]
        dados["lembretes"] += [{"texto": "Viajar pra Paris", "trecho": "Lembrar: viajar pra Paris.", "registro_id": rid},
                               {"texto": "Sem registro", "trecho": "x", "registro_id": 777}, "nem é objeto",
                               {"texto": "", "trecho": "Lembrar: ligar pro pai dela.", "registro_id": rid}]
        dados["assuntos"] += [{"assunto": "a ex dela", "trecho": "Assuntos importantes: trabalho.", "registro_id": rid}]
        with recursos.usando(semana):
            destino = Banco.novo()
            self.addCleanup(destino.fechar)
            r = destino.importar_json(json.dumps(exportado, ensure_ascii=False))
            resumo = r["recursos"]["semana"]
            self.assertEqual(resumo["novos"], 2)
            self.assertIn("5 ficaram de fora", resumo["texto"])
            self.assertEqual([l["texto"] for l in destino.consultar(f"SELECT texto FROM {semana.T_LEMBRETES}")],
                             ["Ligar pro pai dela"])
            outro = Banco.novo()
            self.addCleanup(outro.fechar)
            ruim = {**exportado, "recursos": {"semana": {"lembretes": "não é lista"}}}
            r = outro.importar_json(json.dumps(ruim, ensure_ascii=False))
            self.assertIn(recursos.AVISO_IMPORTAR.format(rotulo="Resumo da semana"), r["avisos"])

    def test_apagar_o_registro_tira_tudo_dele_do_resumo_das_tabelas_e_das_chaves(self):
        rid = self.conversa(contou="Ela disse que adora tulipas amarelas.", assuntos="trabalho",
                            lembrar="ligar pro pai dela amanhã")
        outro = self.registrar("Ela adora praia.", "2026-10-01")
        [c] = self.hoje()
        self.pedir("POST", "/api/alertas/dispensar", {"chave": c["chave"]})
        self.assertEqual(self.pedir("DELETE", f"/api/registros/{rid}").status, 200)
        self.assertEqual((self.lembretes(), self.assuntos()), ([], []))
        self.assertFalse(any(k.startswith("semana:") for k in self.banco.alertas_dispensados()))
        r = self.resumo(date(2026, 10, 4))
        self.assertEqual(r["registro_ids"], [outro])
        tudo = json.dumps(r, ensure_ascii=False) + json.dumps(self.pedir("GET", "/api/exportar").dados, ensure_ascii=False)
        for apagado in ("tulipas", "pai dela", "trabalho"):
            self.assertNotIn(apagado, tudo)                   # apagar é apagar: nem no resumo, nem no export


class TestInteligencia(BaseNucleo):
    """O que o resumo faz "de esperto", sempre explicável e só com o que está registrado."""

    def test_fato_no_que_lembrar_vira_preferencia_e_nao_lembrete(self):
        rid = self.conversa(lembrar="ela não gosta de cebola\ncomprar a ração do gato dela")
        self.assertEqual([l["texto"] for l in self.lembretes()], ["Comprar a ração do gato dela"])
        itens = self.pedir("GET", f"/api/registros/{rid}").dados["itens"]
        self.assertTrue(any(i["tipo"] == "preferencia" and i["alvo"] == "cebola" and i["polaridade"] == -1 for i in itens))

    def test_a_mesma_frase_nao_aparece_duas_vezes_nos_proximos(self):
        self.registrar("Lembrar: o aniversário da mãe dela dia 12.", "2026-10-06")
        r = self.resumo(date(2026, 10, 6))
        self.assertEqual([(p["data"], p["texto"], p["origem"]) for p in r["proximos"]],
                         [("2026-10-12", "Lembrar: O aniversário da mãe dela", "lembrete")])
        self.assertEqual(r["contou"], [])                  # lembrete seu não é "o que ela contou"

    def test_so_o_estado_dela_conta_como_ela_estava(self):
        self.registrar("A mãe dela tá preocupada com a cirurgia.", "2026-10-03")
        r = self.resumo(date(2026, 10, 4))
        self.assertEqual(r["estados"], [])
        self.assertFalse(r["momento"]["sensivel"])
        self.registrar("Ela tá triste com a demissão da amiga dela.", "2026-10-03")
        r = self.resumo(date(2026, 10, 4))
        self.assertEqual([e["texto"] for e in r["estados"]], ["triste"])
        self.assertTrue(r["momento"]["sensivel"])

    def test_estado_dificil_antigo_nao_deixa_o_momento_sensivel(self):
        self.registrar("Ela tá muito cansada com o trabalho.", "2026-09-28")
        r = self.resumo(date(2026, 10, 4))
        self.assertEqual([e["texto"] for e in r["estados"]], ["cansada"])
        self.assertFalse(r["momento"]["sensivel"])                 # 6 dias atrás: já não é "agora"

    def test_assunto_que_voltou_na_semana_vira_um_talvez_citando(self):
        for d in ("2026-09-28", "2026-09-30", "2026-10-02"):
            self.registrar(f"A chefe dela pegou no pé de novo no trabalho ({d[-2:]}).", d)
        r = self.resumo(date(2026, 10, 4))
        [s] = [s for s in r["sugestoes"] if "Trabalho" in s["texto"]]
        self.assertEqual(s["texto"], "Talvez valha perguntar como ela está com o assunto “Trabalho”.")
        self.assertEqual((s["porque"], len(s["registro_ids"])), ("Ele apareceu em 3 registros nesta semana.", 3))
        pessoa = [v for v in r["voltaram"] if v["tipo"] == "pessoa"]
        self.assertEqual([(v["rotulo"], v["registros"]) for v in pessoa], [("chefe", 3)])

    def test_pendencia_da_semana_vira_talvez_perguntar(self):
        self.registrar("Ela tá esperando o resultado da entrevista.", "2026-10-02")
        r = self.resumo(date(2026, 10, 4))
        [s] = [s for s in r["sugestoes"] if "como ficou" in s["texto"]]
        self.assertEqual(s["texto"], "Talvez perguntar como ficou: “Esperando o resultado da entrevista”.")
        self.assertIn("“Ela tá esperando o resultado da entrevista.”", s["porque"])

    def test_pergunta_vai_no_cartao_do_hoje(self):
        perguntas = falso_perguntas({"pergunta": "O que te faz sentir em casa?", "titulo": "Conheça melhor"})
        with recursos.usando(perguntas, com_os_fixos=True):
            self.registrar("Ela adora praia.", "2026-10-01")
            self.registrar("Ela adora sorvete.", "2026-10-02")
            [c] = [c for c in self.hoje(date(2026, 10, 4)) if c["tipo"] == "semana_resumo"]
        self.assertEqual(c["dados"]["pergunta"], {"pergunta": "O que te faz sentir em casa?", "titulo": "Conheça melhor"})

    def test_lembrete_de_mes_ou_semana_nao_vai_para_o_hoje(self):
        self.registrar("Preciso lembrar de comprar o presente dela em outubro.\nLembrar: ver o show semana que vem.")
        self.assertEqual({(l["precisao"]) for l in self.lembretes()}, {"mes", "semana"})
        for d in (dia(1), date(2026, 10, 5), date(2026, 10, 31)):
            self.assertEqual([c for c in self.hoje(d) if c["tipo"] == "semana_lembrete"], [])
        lista = self.pedir("GET", "/api/semana/lembretes").dados["abertos"]
        self.assertEqual([l["quando"] for l in lista], ["em outubro", "na semana de 05/10"])

    def test_historico_nao_conta_semana_que_ainda_vai_vir(self):
        self.registrar("Ela adora praia.", "2026-10-20")              # data escolhida no futuro
        self.registrar("Ela adora sorvete.", "2026-09-29")
        semanas = self.pedir("GET", "/api/semana/historico", consulta={"data": DATA}).dados["semanas"]
        self.assertEqual([s["semana"] for s in semanas], ["2026-W40"])


# ======================================================================== privacidade

class TestPrivacidade(BaseNucleo):
    modulos = (falso_ciclo(),)

    class CadeiaFalsa:
        """No lugar da IA: anota o que receberia (nada sai do computador)."""

        def __init__(self):
            self.recebido: list = []

        def responder(self, pergunta, trechos):
            self.recebido.append(json.dumps(trechos, ensure_ascii=False, default=str))
            return Resposta(pergunta=pergunta, texto="Achei.", encontrados=list(trechos)), "principal", []

        def sugerir(self, contexto, referencia):
            self.recebido.append(json.dumps(contexto, ensure_ascii=False, default=str))
            return [], "principal", []

    def test_ciclo_fica_fora_do_resumo_e_do_hoje(self):
        rid = self.registrar("Ela ficou menstruada ontem e tá com cólica. Ela adora tulipas.\n"
                             "Lembrar: comprar bolsa de água quente pra cólica dela amanhã.", "2026-10-01")
        self.banco.inserir("ciclo_eventos", {"data": "2026-09-30", "tipo": "inicio",
                                             "trecho": "Ela ficou menstruada ontem e tá com cólica.", "registro_id": rid})
        r = self.resumo(date(2026, 10, 4))
        texto = json.dumps(r, ensure_ascii=False).lower()
        for palavra in ("menstru", "cólica", "colica"):
            self.assertNotIn(palavra, texto)
        self.assertIn("tulipas", texto)                                    # o resto do registro continua
        for d in (dia(1), date(2026, 10, 4)):
            for c in self.hoje(d):
                tudo = json.dumps(c, ensure_ascii=False).lower()
                with self.subTest(d=d, tipo=c["tipo"]):
                    for palavra in ("menstru", "cólica", "colica", "ciclo", "tpm"):
                        self.assertNotIn(palavra, tudo)
        [lembrete] = [c for c in self.hoje(dia(1)) if c["tipo"] == "semana_lembrete"]
        self.assertEqual(lembrete["texto"], "Amanhã: um lembrete que você anotou.")

    def test_trecho_guardado_pelo_ciclo_tambem_fica_de_fora(self):
        rid = self.registrar("Ela tá chateada desde ontem. Ela adora praia.", "2026-10-01")
        self.banco.inserir("ciclo_eventos", {"data": "2026-10-01", "tipo": "sintoma", "trecho": "Ela tá chateada desde ontem.",
                                             "registro_id": rid})
        r = self.resumo(date(2026, 10, 4))
        self.assertNotIn("chateada", json.dumps(r, ensure_ascii=False))
        self.assertIn("praia", json.dumps(r, ensure_ascii=False))

    def test_lembretes_e_assuntos_nunca_vao_para_a_ia(self):
        self.banco.salvar_config(ia_principal=IA)
        cadeia = self.CadeiaFalsa()
        self.enterContext(mock.patch.object(motor, "obter_cadeia_ia", lambda ajustes: cadeia))
        self.registrar("Ela adora tulipas amarelas. Lembrar: ligar pro pai dela.")
        [l] = self.lembretes()
        self.pedir("PATCH", f"/api/semana/lembretes/{l['id']}", {"texto": "Levar ela pra ver a aurora boreal"})
        self.assertEqual(self.pedir("POST", "/api/ia/sugestao", {}).status, 200)
        self.assertEqual(self.pedir("POST", "/api/ia/busca", {"q": "aurora boreal tulipas"}).status, 200)
        self.assertTrue(cadeia.recebido)
        self.assertFalse(any("aurora" in r for r in cadeia.recebido))
        self.assertEqual(recursos.fora_da_ia(self.banco), set())        # e não tira registro nenhum da IA

    def test_conversa_nao_deixa_o_registro_fora_da_ia(self):
        p = self.analisar("Assuntos importantes: trabalho.\nLembrar: ligar pro pai dela.")
        self.assertTrue(all("_privado" not in e for e in p["extras"]["semana"]))
        self.assertNotIn(recursos.AVISO_PRIVADO, p["avisos"])

    def test_log_e_historico_sem_conteudo(self):
        self.registrar("Lembrar: levar ela pra ver a aurora boreal amanhã.")
        coletor = Coletor()
        logging.getLogger("entrenos").addHandler(coletor)
        self.addCleanup(logging.getLogger("entrenos").removeHandler, coletor)
        with mock.patch.object(semana, "_vista_lembrete", side_effect=RuntimeError("aurora boreal")):
            self.assertEqual(self.pedir("GET", "/api/hoje").status, 200)        # o Hoje abre sem os cartões daqui
            self.assertEqual(self.pedir("GET", "/api/semana/lembretes").status, 500)
        texto_log = "\n".join(coletor.mensagens)
        self.assertIn("semana", texto_log)
        self.assertIn("RuntimeError", texto_log)
        self.assertNotIn("aurora", texto_log)
        historico = json.dumps(self.pedir("GET", "/api/historico").dados, ensure_ascii=False)
        self.assertIn("semana_lembretes", historico)
        self.assertNotIn("aurora", historico)


# ======================================================================== a tela

class TestTela(unittest.TestCase):
    def setUp(self):
        self.js = (RAIZ / "web" / "telas" / "semana.js").read_text(encoding="utf-8")

    def test_tela_sem_html_montado_nem_estilo_inline(self):
        for proibido in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", ".style.",
                         "style=", "localStorage", "sessionStorage", "fetch("):
            with self.subTest(proibido=proibido):
                self.assertNotIn(proibido, self.js)
        self.assertIn("window.EntreNos", self.js)

    def test_encaixes_registrados(self):
        for registro in ("registrarMenu('semana'", "registrarMenu('conversa'", "registrarTela('semana'",
                         "registrarTela('conversa'", "registrarSecaoAjustes('semana'"):
            with self.subTest(registro=registro):
                self.assertIn(registro, self.js)

    def test_tipos_dos_cartoes_e_dos_extras_batem_com_a_tela(self):
        for tipo in semana.TIPOS_EXTRAS:
            self.assertIn(f"registrarRevisaoExtra('{tipo}'", self.js)
        fonte = (RAIZ / "entrenos" / "recursos" / "semana.py").read_text(encoding="utf-8")
        tipos = set(re.findall(r'tipo="(semana_[a-z_]+)"', fonte))
        self.assertEqual(tipos, {"semana_resumo", "semana_lembrete"})
        for tipo in tipos:
            self.assertIn(f"registrarCartaoHoje('{tipo}'", self.js)

    def test_hoje_nunca_usa_as_palavras_do_ciclo(self):
        fonte = (RAIZ / "entrenos" / "recursos" / "semana.py").read_text(encoding="utf-8")
        trecho = fonte[fonte.index("# ================================================================ Hoje"):
                       fonte.index("# ================================================================ rotas")]
        for palavra in ("menstrua", "ciclo", "TPM"):
            self.assertNotIn(palavra, trecho)


if __name__ == "__main__":
    unittest.main()
