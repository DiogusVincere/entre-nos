"""Integração dos nove recursos com o centro (DESIGN.md, seção 20.10).

O que os recursos pediram aos arquivos centrais e a integração fez:
- o resumo da importação só traz um recurso quando o arquivo trouxe dados dele (ou ele contou algo);
- `recursos.discretos` (o ciclo): o Hoje não mostra o começo do texto, Pessoas não cita a frase e as
  ligações "mesma pessoa" não usam esses registros; na dúvida (gancho que falha), os 🔒 também;
- `conhecimento.consultar`: o `extrair` lê o diário aberto (só leitura), sem referência guardada;
- regras: "conheci" é passado; nome de lugar depois de "restaurante", "café"... não é pessoa; "não
  curtiu muito" não vira gosto por "muito"; `texto.termos` com as stopwords calculadas uma vez.

Pasta de dados temporária (ENTRENOS_DADOS), data fixa 30/09/2026, scrypt pequeno.
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import tempfile
import unittest
from datetime import date
from unittest import mock

from entrenos import alertas, cofre, contexto, motor, perfil, recursos
from entrenos import nucleo as nucleo_mod
from entrenos import texto as texto_mod
from entrenos.banco import Banco, OperacaoProibida
from entrenos.modelos import TipoItem, para_json
from entrenos.motor import Conhecimento, MotorRegras, conhecimento_do_banco
from entrenos.recursos import ciclo
from entrenos.regras.datas import extrair_datas
from entrenos.regras.pessoas import extrair_pessoas
from testes.test_fundacao_recursos import explodir, recurso_falso
from testes.test_servidor import REF
from testes.test_servidor_ia import BaseIA

_remendos: list = []
_env_antigo: str | None = None
_pasta_env: str | None = None


def setUpModule() -> None:
    global _env_antigo, _pasta_env
    _env_antigo = os.environ.get("ENTRENOS_DADOS")
    _pasta_env = tempfile.mkdtemp(prefix="entrenos-teste-integracao-env-")
    os.environ["ENTRENOS_DADOS"] = _pasta_env      # garantia: nada cai em %LOCALAPPDATA%
    for nome, valor in (("SCRYPT_N", 2 ** 12), ("VERIFICADOR_N", 2 ** 10)):
        remendo = mock.patch.object(cofre, nome, valor)
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


def gravar(b: Banco, texto: str, data: date = REF, privado: bool = False) -> int:
    """Grava como a revisão grava: o que as regras entenderam, com as pessoas do texto."""
    p = MotorRegras().analisar(texto, data, conhecimento_do_banco(b))
    return b.criar_registro(texto, data, p.itens, p.pessoas, [], privado=privado)


# ---------------------------------------------------------------- importar: o resumo dos recursos

class TestResumoDaImportacao(unittest.TestCase):
    def test_recurso_sem_dados_no_arquivo_fica_fora_do_resumo(self):
        with recursos.usando(recurso_falso()):
            a = Banco.novo()
            self.addCleanup(a.fechar)
            a.criar_registro("Ela adora tulipas.", REF, [], [], [])
            exportado = json.dumps(a.exportar(), ensure_ascii=False)
            self.assertEqual(json.loads(exportado)["recursos"], {"falso": {"coisas": []}})
            b = Banco.novo()
            self.addCleanup(b.fechar)
            r = b.importar_json(exportado)
            self.assertEqual(r["registros_novos"], 1)
            self.assertNotIn("recursos", r)          # "0 coisas novas" de cada recurso só atrapalharia

    def test_com_dados_no_arquivo_entra_mesmo_sem_nada_novo(self):
        falso = recurso_falso()
        with recursos.usando(falso):
            a = Banco.novo()
            self.addCleanup(a.fechar)
            rid = a.criar_registro("Prometi levar ela ao cinema.", REF, [], [], [])
            a.inserir(falso.TABELA, {"tipo": "promessa", "resumo": "Cinema", "trecho": "Prometi levar ela ao cinema.",
                                     "registro_id": rid})
            exportado = json.dumps(a.exportar(), ensure_ascii=False)
            b = Banco.novo()
            self.addCleanup(b.fechar)
            self.assertEqual(b.importar_json(exportado)["recursos"], {"falso": {"novos": 1, "texto": "1 coisas de teste"}})
            # De novo: nada novo, mas o arquivo trouxe dados do recurso ("0 novos" informa).
            self.assertEqual(b.importar_json(exportado)["recursos"]["falso"]["novos"], 0)

    def test_tem_dados_e_conta_algo(self):
        self.assertFalse(recursos._tem_dados({"versao": 1, "lugares": [], "mencoes": []}))
        self.assertFalse(recursos._tem_dados({"ajustes": {}, "ciclos": []}))
        # Ajustes não são linhas (e o ciclo não os importa): não contam como "trouxe dados".
        self.assertFalse(recursos._tem_dados({"ajustes": {"lembrete_hoje": False}, "ciclos": [], "eventos": []}))
        self.assertTrue(recursos._tem_dados({"ciclos": [{"inicio": "2026-09-01"}]}))
        self.assertTrue(recursos._tem_dados([1]))
        self.assertFalse(recursos._conta_algo({"novos": 0, "texto": "nada", "ok": True}))
        self.assertTrue(recursos._conta_algo({"novos": 0, "ignorados": 2}))


# ---------------------------------------------------------------- discretos (o ciclo, seção 20.8)

class TestDiscretos(unittest.TestCase):
    def setUp(self):
        self.enterContext(recursos.usando(ciclo))
        self.b = Banco.novo()
        self.addCleanup(self.b.fechar)
        self.normal = gravar(self.b, "A Mariana passou aqui ontem e trouxe bolo.", date(2026, 9, 28))
        self.do_ciclo = gravar(self.b, "A Mariana contou que ficou menstruada pela primeira vez no ônibus.",
                               date(2026, 9, 29))

    def test_ciclo_diz_quais_sao(self):
        self.assertEqual(recursos.discretos(self.b), {self.do_ciclo})

    def test_hoje_nao_mostra_o_comeco_do_texto(self):
        ultimos = {u["id"]: u for u in alertas.hoje(self.b, REF)["ultimos"]}
        u = ultimos[self.do_ciclo]
        self.assertEqual((u["resumo"], u["pessoas"], u["categorias"], u["discreto"]),
                         (alertas.RESUMO_DISCRETO, [], [], True))
        self.assertNotIn("menstru", json.dumps(ultimos, ensure_ascii=False, default=str).lower())
        self.assertIn("Mariana", ultimos[self.normal]["resumo"])

    def test_pessoas_nao_cita_a_frase(self):
        [mariana] = [p for p in perfil.conhecendo_ela(self.b, REF)["pessoas"] if p["nome"] == "Mariana"]
        self.assertEqual([m["registro_id"] for m in mariana["ultimas_mencoes"]], [self.normal])
        self.assertNotIn("menstru", json.dumps(mariana, ensure_ascii=False, default=str).lower())
        self.assertEqual([m["registro_id"] for m in self.b.mencoes(mariana["id"])], [self.do_ciclo, self.normal])

    def test_ligacao_mesma_pessoa_nao_usa_o_registro_do_ciclo(self):
        p = MotorRegras().analisar("A Mariana vai casar em maio.", REF, conhecimento_do_banco(self.b))
        rels = [r for r in contexto.relacionar(p, self.b, REF) if r.tipo == "mesma_pessoa"]
        self.assertEqual([r.registro_antigo_id for r in rels], [self.normal])
        self.assertNotIn("menstru", " ".join(r.motivo for r in rels).lower())

    def test_na_duvida_os_privados_tambem(self):
        privado = gravar(self.b, "Segredo: a Camila vai pedir demissão.", REF, privado=True)
        with recursos.usando(recurso_falso("falso", discretos=explodir)):
            self.assertEqual(recursos.discretos(self.b), {privado})
            ultimos = {u["id"]: u for u in alertas.hoje(self.b, REF)["ultimos"]}
            self.assertEqual(ultimos[privado]["resumo"], alertas.RESUMO_DISCRETO)
        with recursos.usando():                     # sem recurso nenhum: nada é discreto
            self.assertEqual(recursos.discretos(self.b), set())


class TestPlanejamentoNaRevisao(unittest.TestCase):
    """Seção 20.8: evento que cai nos dias previstos + algo que ela disse -> aviso no cartão 🌙 E na
    revisão daquele evento (o `extrair` do ciclo lê o diário pelo `conhecimento.consultar`)."""

    def setUp(self):
        self.enterContext(recursos.usando(ciclo))
        self.b = Banco.novo()
        self.addCleanup(self.b.fechar)
        for d in ("2026-06-06", "2026-07-04", "2026-08-01", "2026-08-29", "2026-09-26"):
            dia = date.fromisoformat(d)
            texto = "Ela ficou menstruada hoje. Ela disse que chá de camomila ajuda quando tá com cólica."
            p = MotorRegras().analisar(texto, dia, conhecimento_do_banco(self.b))
            with self.b.transacao():
                rid = self.b.criar_registro(texto, dia, p.itens, p.pessoas, [], privado=True)
                recursos.gravar_extras(self.b, rid, {"ciclo": p.extras["ciclo"]}, dia)

    def test_aviso_na_revisao_do_evento_nos_dias_previstos(self):
        p = MotorRegras().analisar("A gente vai viajar pra praia dia 24.", REF, conhecimento_do_banco(self.b))
        [aviso] = p.extras.get("ciclo", [])
        self.assertEqual((aviso["tipo"], aviso["trecho"]), ("ciclo_planejamento", "A gente vai viajar pra praia dia 24."))
        self.assertNotIn("_privado", aviso)                              # não deixa o registro 🔒
        self.assertNotIn(recursos.AVISO_PRIVADO, p.avisos)
        self.assertTrue(any("camomila" in t for t in aviso["lembrar"]))
        # Confirmado ou não, não grava nada (e não vira dado do ciclo nem fica fora da IA).
        rid = self.b.criar_registro(p.texto, REF, p.itens, p.pessoas, [])
        antes = self.b.consultar("SELECT count(*) AS n FROM ciclo_eventos")[0]["n"]
        self.assertEqual(recursos.recursos_que_marcam_privado({"ciclo": [aviso]}), [])
        recursos.gravar_extras(self.b, rid, {"ciclo": [aviso]}, REF)
        self.assertEqual(self.b.consultar("SELECT count(*) AS n FROM ciclo_eventos")[0]["n"], antes)
        self.assertNotIn(rid, recursos.fora_da_ia(self.b))

    def test_fora_dos_dias_ou_sem_diario_nada(self):
        self.assertEqual(MotorRegras().analisar("A gente vai viajar pra praia dia 10.", REF,
                                                conhecimento_do_banco(self.b)).extras.get("ciclo", []), [])
        self.assertEqual(MotorRegras().analisar("A gente vai viajar pra praia dia 24.", REF,
                                                Conhecimento()).extras.get("ciclo", []), [])


class TestLembreteDaConversa(unittest.TestCase):
    def test_lembrete_confirmado_afasta_o_acontecimento_da_mesma_frase(self):
        from entrenos.recursos import semana
        with recursos.usando(semana):
            b = Banco.novo()
            self.addCleanup(b.fechar)
            texto, ref = "Lembrar: perguntar dos exames da mãe dela na sexta.", date(2026, 9, 23)
            p = MotorRegras().analisar(texto, ref, conhecimento_do_banco(b))
            self.assertTrue([i for i in p.itens if i.tipo == TipoItem.EVENTO])
            with b.transacao():
                rid = b.criar_registro(texto, ref, p.itens, p.pessoas, [])
                recursos.gravar_extras(b, rid, {"semana": p.extras["semana"]}, ref)
            self.assertFalse([i for i in b.itens() if i.registro_id == rid and i.tipo == TipoItem.EVENTO])
            self.assertTrue([i for i in b.itens(status=None) if i.registro_id == rid and i.status == "descartado"])
            # No Hoje de quinta, um aviso só: o lembrete seu (com "Feito"), não um "acontecimento" dela.
            alertas_ = [a for a in alertas.hoje(b, date(2026, 9, 24))["alertas"]]
            self.assertEqual([getattr(a, "tipo", "núcleo") for a in alertas_], ["semana_lembrete"])


# ---------------------------------------------------------------- extrair lê o diário (só leitura)

class TestConhecimentoConsultar(unittest.TestCase):
    def test_extrair_recebe_a_leitura_do_diario(self):
        vistos = []

        def extrair(texto, referencia, pessoas, datas, itens, conhecimento):
            vistos.append(conhecimento.consultar("SELECT count(*) AS n FROM registros")[0]["n"])
            with self.assertRaises(OperacaoProibida):
                conhecimento.consultar("DELETE FROM registros")
            return []

        with recursos.usando(recurso_falso(extrair=extrair)):
            b = Banco.novo()
            self.addCleanup(b.fechar)
            b.criar_registro("Ela adora tulipas.", REF, [], [], [])
            c = conhecimento_do_banco(b)
            self.assertIs(copy.deepcopy(c).consultar, c.consultar)       # a cópia não copia o Banco
            MotorRegras().analisar("Ela adora girassóis.", REF, c)
            self.assertEqual(vistos, [1])
            self.assertEqual(b.registros()[0]["texto"], "Ela adora tulipas.")
        self.assertIsNone(Conhecimento().consultar)                     # o motor sozinho: sem diário
        self.assertEqual(Conhecimento(), Conhecimento(consultar=print))  # não entra na comparação


# ---------------------------------------------------------------- Hoje: rodízio das sugestões e cartões mostrados

def fornecedor(nome: str, relevancia: int, vistos: list) -> object:
    def cartoes_hoje(banco, referencia):
        return [recursos.cartao(f"s:{referencia.isoformat()}", "sugestao", f"Talvez algo de {nome}", relevancia=relevancia)]

    def cartoes_mostrados(banco, referencia, cartoes):
        vistos.append((nome, referencia, [c["chave"] for c in cartoes]))

    return recurso_falso(nome, ESQUEMA=None, ESQUEMA_VERSAO=None, cartoes_hoje=cartoes_hoje,
                         cartoes_mostrados=cartoes_mostrados, rotas=lambda: [])


class TestHojeSugestoes(unittest.TestCase):
    def sugestoes(self, b, dia):
        return [(getattr(a, "recurso", None), a.relevancia) for a in alertas.hoje(b, dia)["alertas"]
                if a.nivel == "sugestao"]

    def test_rodizio_entre_os_recursos_e_so_os_mostrados_ficam_sabendo(self):
        vistos: list = []
        with recursos.usando(fornecedor("aa", 50, vistos), fornecedor("bb", 55, vistos), fornecedor("cc", 60, vistos)):
            b = Banco.novo()
            self.addCleanup(b.fechar)
            vezes = {"aa": 0, "bb": 0, "cc": 0}
            for n in range(6):
                dia = date(2026, 10, 1 + n)
                s = self.sugestoes(b, dia)
                self.assertEqual(len(s), 2)
                self.assertEqual(s, self.sugestoes(b, dia))                    # o mesmo dia, a mesma escolha
                for nome, _ in s:
                    vezes[nome] += 1
            self.assertTrue(all(v >= 2 for v in vezes.values()), vezes)      # todo mundo tem a sua vez
            # Cada recurso só soube dos cartões dele que apareceram (o Hoje abriu 2 vezes por dia, acima).
            self.assertEqual(len(vistos), 6 * 2 * 2)
            self.assertEqual(len({(nome, ref) for nome, ref, _ in vistos}), 6 * 2)
            self.assertTrue(all(ch == [f"s:{ref.isoformat()}"] for _, ref, ch in vistos))

    def test_urgente_passa_na_frente_e_o_nucleo_vem_primeiro(self):
        vistos: list = []
        with recursos.usando(fornecedor("aa", 50, vistos), fornecedor("bb", 90, vistos), fornecedor("cc", 60, vistos)):
            b = Banco.novo()
            self.addCleanup(b.fechar)
            for n in range(4):
                self.assertIn(("bb", 90), self.sugestoes(b, date(2026, 10, 1 + n)))
            # Um estado difícil recente: a sugestão do núcleo (sobre ela) sempre entra.
            gravar(b, "Ela está muito triste com a demissão.", date(2026, 10, 1))
            for n in range(3):
                s = self.sugestoes(b, date(2026, 10, 2 + n))
                self.assertIn(None, [r or None for r, _ in s], s)
                self.assertIn(("bb", 90), s)


# ---------------------------------------------------------------- Banco: transação sem mudança não regrava

class TestTransacaoSemMudanca(unittest.TestCase):
    def test_so_grava_o_arquivo_quando_algo_mudou(self):
        gravacoes = []
        b = Banco.novo(ao_salvar=gravacoes.append)
        self.addCleanup(b.fechar)
        with b.transacao():
            b.consultar("SELECT count(*) FROM registros")
        self.assertEqual(gravacoes, [])
        b.criar_registro("Ela adora tulipas.", REF, [], [], [])
        self.assertEqual(len(gravacoes), 1)
        with b.transacao():
            pass
        self.assertEqual(len(gravacoes), 1)


# ---------------------------------------------------------------- regras

class TestRegras(unittest.TestCase):
    def datas(self, texto):
        return [(d.expressao, d.data.isoformat()) for d in extrair_datas(texto, REF)]

    def test_primeira_pessoa_do_preterito_em_i_e_passado(self):
        self.assertEqual(self.datas("conheci os pais dela em 12/03"), [("12/03", "2026-03-12")])
        self.assertEqual(self.datas("Comi lá dia 10"), [("dia 10", "2026-09-10")])

    def test_pedi_continua_sendo_fala(self):
        # "Pedi pra ela me avisar quando sair o resultado dia 20": o dia 20 é o que vem.
        self.assertEqual(self.datas("Pedi pra ela me avisar quando sair o resultado dia 20"),
                         [("dia 20", "2026-10-20")])

    def nomes(self, texto, conhecidas=()):
        return [p.nome for p in extrair_pessoas(texto, list(conhecidas)) if p.nome]

    def test_nome_de_lugar_nao_vira_pessoa(self):
        self.assertEqual(self.nomes("Jantamos no restaurante Mangiare e ela amou."), [])
        self.assertEqual(self.nomes("Fomos conhecer o Museu do Ipiranga."), [])
        self.assertEqual(self.nomes("Quero levar ela no Beco do Batman."), [])
        self.assertEqual(self.nomes("O festival Coala foi incrível."), [])
        self.assertEqual(self.nomes("Tomamos um café no café Santo Grão com o Pedro."), ["Pedro"])
        self.assertEqual(self.nomes("Ela foi na sorveteria Bacio di Latte com a Mariana."), ["Mariana"])

    def test_gente_continua_gente(self):
        self.assertEqual(self.nomes("Fui buscar ela na escola da Ana."), ["Ana"])          # pode ser a Ana
        self.assertEqual(self.nomes("A Mariana ligou ontem."), ["Mariana"])
        self.assertEqual(self.nomes("Encontramos no bar Mariana e o Pedro.",
                                    [{"id": 1, "nome": "Mariana", "relacao": "amiga", "apelidos": []}]),
                         ["Mariana", "Pedro"])

    def test_palavra_de_intensidade_nao_e_alvo_de_gosto(self):
        p = MotorRegras().analisar("Dei uma blusa pra ela e ela não curtiu muito.", REF, Conhecimento())
        self.assertFalse([i for i in p.itens if i.tipo == TipoItem.PREFERENCIA and (i.alvo or "").strip() == "muito"])
        p = MotorRegras().analisar("Ela não gosta muito de praia.", REF, Conhecimento())
        self.assertTrue([i for i in p.itens if i.tipo == TipoItem.PREFERENCIA and "praia" in (i.alvo or "")])

    def test_prazo_nao_e_aniversario(self):
        # "comprar o presente de aniversário dela até dia 25": o dia 25 é prazo, não o aniversário
        # dela (antes virava a data de aniversário, que se repetia todo ano, no Hoje e nos presentes).
        p = MotorRegras().analisar("Lembrar: comprar o presente de aniversário dela até dia 25.", date(2026, 10, 5),
                                   Conhecimento())
        self.assertFalse([i for i in p.itens if i.tipo == TipoItem.DATA_IMPORTANTE])
        self.assertFalse([i for i in p.itens if i.recorrente])
        self.assertFalse([d for d in p.datas if d.recorrente])
        p = MotorRegras().analisar("O aniversário dela é dia 25.", date(2026, 10, 5), Conhecimento())
        self.assertEqual([(i.tipo, i.data_evento, i.recorrente) for i in p.itens],
                         [(TipoItem.DATA_IMPORTANTE, date(2026, 10, 25), True)])

    def test_data_de_perguntar_nao_e_a_do_evento(self):
        # "perguntar dos exames da mãe dela na sexta": a sexta é de perguntar. Antes virava "Amanhã é o
        # exame da mãe dela" (Importante) no Hoje de quinta, uma data que ninguém disse.
        ref = date(2026, 9, 23)
        p = MotorRegras().analisar("Lembrar: perguntar dos exames da mãe dela na sexta.", ref, Conhecimento())
        self.assertFalse([i for i in p.itens if i.tipo == TipoItem.EVENTO and i.resumo.startswith("Mãe: exame")])
        self.assertTrue([i for i in p.itens if "perguntar dos exames" in i.resumo and i.data_evento == date(2026, 9, 25)])
        p = MotorRegras().analisar("A mãe dela vai fazer exames na sexta.", ref, Conhecimento())
        self.assertEqual([(i.resumo, i.data_evento) for i in p.itens], [("Mãe: exame — 25/09/2026", date(2026, 9, 25))])

    def test_estado_de_quem_e_os_dois_estados(self):
        # Revisão de 07/10: o estado da avó virava dela; "mas hoje tá ótima" e "cansada e triste" perdiam um.
        def estados(texto):
            p = MotorRegras().analisar(texto, REF, Conhecimento())
            return [(i.resumo, i.pessoa, i.data_evento) for i in p.itens if i.tipo == TipoItem.ESTADO]
        self.assertEqual(estados("Ela volta de Recife, foi visitar a avó que tá doente."),
                         [("Avó: está doente", "avó", REF)])
        self.assertEqual(estados("Ela tava triste ontem mas hoje tá ótima."),
                         [("Estava triste", None, date(2026, 9, 29)), ("Está ótima", None, REF)])
        self.assertEqual(estados("Ela tá cansada e triste."), [("Está cansada", None, REF), ("Está triste", None, REF)])
        p = MotorRegras().analisar("ela n curtiu mt", REF, Conhecimento())
        self.assertFalse([i for i in p.itens if i.tipo == TipoItem.PREFERENCIA])

    def test_termos_igual_ao_de_antes(self):

        frase = "Ela disse que a apresentação no trabalho é muito importante, e que o chefe dela é chato."
        antes = [w for w in (texto_mod.normalizar(p) for p in texto_mod.palavras(frase))
                 if len(w) > 1 and w not in {texto_mod.normalizar(s) for s in texto_mod.STOPWORDS}]
        self.assertEqual(texto_mod.termos(frase), antes)


# ---------------------------------------------------------------- correção final: o texto também decide

# Frases que o próprio app reconhece (o ✨ Analisar recusa com 403) e que, salvas sem confirmar o extra
# (ou com "Não guardar"), iam para a IA e apareciam nas telas centrais (revisão de privacidade, 07/10).
FRASES_FORA_DA_IA = {
    "ciclo": "O ciclo dela atrasou essa semana e ela ficou preocupada com isso.",
    "desceu": "Desceu hoje pra ela, tá com cólica e bem cansada.",
    "periodo": "Ela tá no período dela e anda bem sensível.",
    "regra": "A regra dela veio forte esse mês e ela ficou chateadinha.",
    "briga": "A gente brigou feio ontem por causa da viagem e eu gritei com ela.",
    "nos_dois": "Eu perguntei: “Tem algo que eu possa fazer melhor por você?”\nEla respondeu: me ouvir mais sem dar conselho.",
}
PALAVRAS_QUE_NAO_SAEM = ("ciclo dela", "Desceu", "cólica", "período dela", "regra dela", "brigou", "conselho")


class TestTextoQueFicaForaDaIA(BaseIA):
    """Seção 20.10: o que um recurso deixa fora da IA vale pelo TEXTO, confirmado ou não."""

    def setUp(self):
        super().setUp()
        self.criar_pin_ia()
        self.falsa = self.ia_falsa()
        self.configurar_ia("principal", self.falsa.url)
        self.normal = self.registrar("Ela tá cansada com o trabalho e ansiosa com a prova de sexta, ficou preocupada.")
        self.ids = {}
        for chave, texto in FRASES_FORA_DA_IA.items():
            self.assertEqual(self.ia(self.s, "POST", "analisar", {"texto": texto}).status, 403, chave)
            p = self.s.pedir("POST", "/api/analisar", {"texto": texto, "data_registro": "2026-09-29"})
            self.assertTrue(any(e.get("_privado") for lista in p.dados["extras"].values() for e in lista), chave)
            corpo = {"texto": texto, "data_registro": "2026-09-29", "itens": p.dados["itens"],
                     "pessoas": p.dados["pessoas"], "relacoes": [], "extras": {}}       # nada confirmado
            r = self.s.pedir("POST", "/api/registros", corpo)
            self.assertEqual(r.status, 200, r.dados)
            self.assertTrue(r.dados.get("privado"), chave)                               # ficou 🔒, com o aviso
            self.assertEqual(r.dados["avisos"][0], recursos.AVISO_PRIVADO_GRAVADO)
            self.ids[chave] = r.dados["id"]
        # Mesmo desmarcando a caixinha depois, continuam fora (o texto é o mesmo).
        for rid in self.ids.values():
            self.assertEqual(self.s.pedir("PATCH", f"/api/registros/{rid}", {"privado": False}).status, 200)

    def test_detalhe_trava_a_caixinha(self):
        for chave, rid in self.ids.items():
            r = self.s.pedir("GET", f"/api/registros/{rid}")
            self.assertEqual((r.dados["privado"], r.dados["fora_da_ia"]), (False, True), chave)
        self.assertFalse(self.s.pedir("GET", f"/api/registros/{self.normal}").dados["fora_da_ia"])

    def test_sugestao_e_busca_nao_mandam_o_texto(self):
        self.falsa.resposta = {"sugestoes": [{"texto": "Talvez perguntar como foi a prova.", "porque": "Ela estava ansiosa.",
                                              "registro_ids": [self.normal]}],
                               "texto": "Ela estava cansada.", "citacoes": [self.normal]}
        self.assertEqual(self.ia(self.s, "POST", "sugestao", {}).status, 200)
        for q in ("preocupada", "cansada", "sensível", "viagem", "conselho", "forte"):
            self.assertEqual(self.ia(self.s, "POST", "busca", {"q": q}).status, 200, q)
        recebido = self.falsa.recebido()
        self.assertIn("prova de sexta", recebido)                     # o registro comum foi
        for palavra in PALAVRAS_QUE_NAO_SAEM:
            self.assertNotIn(palavra, recebido)
        # A busca comum marca os achados como 🔒 (o ✨ só aparece se sobrar algo para mandar).
        r = self.s.pedir("GET", "/api/busca?q=sens%C3%ADvel")
        self.assertTrue(all(e["privado"] for e in r.dados["encontrados"] if e["registro_id"] != self.normal))

    def test_telas_centrais_sem_as_palavras_do_ciclo(self):
        hoje = json.dumps(self.s.pedir("GET", "/api/hoje?data=2026-09-30").dados, ensure_ascii=False)
        perfil_ = json.dumps(self.s.pedir("GET", "/api/perfil").dados, ensure_ascii=False)
        for texto in (hoje, perfil_):
            for palavra in ("ciclo dela", "Desceu", "cólica", "período dela", "regra dela"):
                self.assertNotIn(palavra, texto)


class TestItensDaFraseDoCiclo(unittest.TestCase):
    """Seção 20.10 (`alertas.sem_o_reservado`): sem confirmar o dado do ciclo, o humor tirado da frase não
    entra em Como ela está, no Hoje nem no "momento dela" de Presentes, Promessas e Lugares; um
    acontecimento com as palavras do ciclo também não; um de resumo neutro fica, sem citar a frase."""

    def setUp(self):
        from entrenos.recursos import lugares, presentes, promessas
        self.enterContext(recursos.usando(ciclo, presentes, promessas, lugares))
        self.b = Banco.novo()
        self.addCleanup(self.b.fechar)
        self.mods = (presentes, promessas, lugares)
        gravar(self.b, "Ela disse que prefere que eu só fique perto quando ela tá mal.", date(2026, 9, 20))
        gravar(self.b, "Prometi levar ela no cinema.", date(2026, 9, 21))
        gravar(self.b, "Ela quer conhecer o restaurante Mani.", date(2026, 9, 22))
        self.irritada = gravar(self.b, "Ela menstruou ontem e tá muito irritada.", date(2026, 9, 29))
        self.descer = gravar(self.b, "A menstruação dela deve descer amanhã.", REF)
        self.consulta = gravar(self.b, "Ela tem consulta na ginecologista amanhã por causa da menstruação atrasada.", REF)

    def test_discretos_e_fora_da_ia_mesmo_sem_confirmar(self):
        self.assertTrue({self.irritada, self.descer, self.consulta} <= recursos.discretos(self.b))

    def test_como_ela_esta_e_hoje(self):
        p = perfil.conhecendo_ela(self.b, REF)
        self.assertEqual(p["como_ela_esta"]["situacao_atual"], [])
        h = alertas.hoje(self.b, REF)
        texto = json.dumps(para_json(h), ensure_ascii=False).lower()
        for palavra in ("menstru", "irritada", "ginecolog"):
            self.assertNotIn(palavra, texto)
        # A consulta de amanhã continua lembrada (resumo neutro), sem citar a frase.
        self.assertTrue([a for a in h["alertas"] if a.nivel == "importante" and "onsulta" in a.texto])

    def test_momento_dela_nos_recursos(self):
        presentes, promessas, lugares = self.mods
        self.assertIsNone(promessas.momento(self.b, REF))
        for modulo in (presentes, lugares):
            vista = modulo.painel(self.b, REF)
            self.assertNotIn("menstru", json.dumps(para_json(vista), ensure_ascii=False).lower())


class TestApagarItemTiraAsChavesDosRecursos(unittest.TestCase):
    def test_chave_de_recurso_com_a_data_do_item_apagado_sai(self):
        # Revisão de 07/10: apagar só o item deixava "presentes:ocasiao:data:<id>:<data>" (a data do
        # aniversário apagado) nos dispensados e nas cópias.
        from entrenos.recursos import presentes
        with recursos.usando(presentes):
            b = Banco.novo()
            self.addCleanup(b.fechar)
            rid = gravar(b, "O aniversário da Julia é dia 20 de outubro.", date(2026, 9, 10))
            [item] = [i for i in b.itens() if i.registro_id == rid]
            b.dispensar_alerta(f"presentes:ocasiao:data:{item.id}:2026-10-20")
            b.dispensar_alerta(f"data:{item.id}:2026-10-20:d7")
            b.dispensar_alerta("presentes:ocasiao:data:999:2026-10-20")
            b.apagar_item(item.id)
            self.assertEqual(b.alertas_dispensados(), {"presentes:ocasiao:data:999:2026-10-20"})


class NucleoComTudo(unittest.TestCase):

    """O núcleo sem transporte, com os nove recursos da lista fixa (como o app roda)."""

    def setUp(self):
        from testes.test_servidor import PIN, Relogio
        self.pasta = tempfile.mkdtemp(prefix="entrenos-teste-integracao-")
        self.addCleanup(shutil.rmtree, self.pasta, True)
        self.hoje = REF
        self.app = nucleo_mod.Nucleo(self.pasta, hoje=lambda: self.hoje, relogio=Relogio(), atraso_pin=0)
        self.addCleanup(self.app.bloquear)
        self.token = None
        r = self.pedir("POST", "/api/configurar", {"pin": PIN, "nome_dela": "Julia"})
        self.assertEqual(r.status, 200, r.dados)
        self.token = r.dados["token"]

    def pedir(self, metodo, caminho, corpo=None, consulta=None):
        bruto = None if corpo is None else json.dumps(corpo).encode("utf-8")
        return self.app.tratar(metodo, caminho, corpo=bruto, token=self.token, consulta=consulta)

    def registrar(self, texto: str, data: date = REF, confirmar: bool = True) -> int:
        p = self.pedir("POST", "/api/analisar", {"texto": texto, "data_registro": data.isoformat()}).dados
        extras = {nome: [e for e in lista if not e.get("informativo")] for nome, lista in p["extras"].items()} \
            if confirmar else {}
        r = self.pedir("POST", "/api/registros", {"texto": texto, "data_registro": data.isoformat(), "itens": p["itens"],
                                                  "pessoas": p["pessoas"], "relacoes": [],
                                                  "extras": {k: v for k, v in extras.items() if v}})
        self.assertEqual(r.status, 200, r.dados)
        return r.dados["id"]

    def get(self, caminho, **consulta):
        r = self.pedir("GET", caminho, consulta=consulta or None)
        self.assertEqual(r.status, 200, r.dados)
        return r.dados


class TestFrasesDoCicloNosRecursos(NucleoComTudo):
    """Revisão de 07/10 (telas): Presentes, Promessas e Lugares citavam frases do ciclo fora do 🌙."""

    def test_presentes_e_lugares_nao_citam_a_frase(self):
        self.registrar("Ela menstruou hoje e eu comprei chocolate pra ela. Ela amou!", date(2026, 9, 20))
        self.registrar("Ela estava de TPM e eu dei uma pantufa pra ela.", date(2026, 9, 22))
        self.registrar("Ela está com cólica, então levei ela no café Santo Grão e ela adorou.", date(2026, 9, 24))
        presentes = self.get("/api/presentes")
        lugares = self.get("/api/lugares")
        self.assertTrue(presentes["recebidos"]["de_voce"])                 # o presente continua lá
        for dados in (presentes, lugares):
            texto = json.dumps(dados, ensure_ascii=False).lower()
            for palavra in ("menstru", "tpm", "cólica"):
                self.assertNotIn(palavra, texto)
        self.assertTrue(any(p.get("reservado") for p in presentes["recebidos"]["de_voce"]))


class TestPresentesTalvezJaRealizados(NucleoComTudo):
    def test_cartao_da_ocasiao_nao_cita_o_que_ela_ja_ganhou(self):
        # Revisão de 07/10: o cartão citava o kindle que ela ganhou da mãe e o fone que você já deu.
        self.pedir("PATCH", "/api/config", {"aniversario_dela": "2000-10-20"})
        self.registrar("Ela queria um fone novo.", date(2026, 6, 1))
        self.registrar("Ela tá doida por um kindle.", date(2026, 6, 2))
        self.registrar("Ela comentou que adoraria ganhar uma câmera instantânea.", date(2026, 6, 3))
        self.registrar("Dei um fone novo pra ela e ela amou.", date(2026, 8, 1))
        self.registrar("Ela ganhou um kindle da mãe dela.", date(2026, 8, 10))
        hoje = self.get("/api/hoje", data="2026-09-30")
        [c] = [a for a in hoje["alertas"] if a.get("tipo") == "presentes_ocasiao"] or [None]
        if c is not None:                       # a vaga de sugestão é rodízio: confere direto no recurso
            texto = json.dumps(c, ensure_ascii=False).lower()
            self.assertNotIn("kindle", texto)
            self.assertNotIn("fone", texto)
        from entrenos.recursos import presentes
        [cartao] = presentes.cartoes_hoje(self.app._banco, REF)
        texto = json.dumps(para_json(cartao), ensure_ascii=False).lower()
        self.assertNotIn("kindle", texto)
        self.assertNotIn("fone", texto)
        self.assertIn("1 coisa que ela mencionou", cartao["texto"])


if __name__ == "__main__":
    unittest.main()


