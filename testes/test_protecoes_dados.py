"""Testes da seção 18.6: o que protege o diário quando arquivos somem (antivírus, desinstalador,
exclusão) e as correções da revisão de 01/10/2026 em restaurar e importar.

- A cópia "antes da restauração" sai da memória, não só do disco (salvar que falhou não some).
- Rodízio dos backups só apaga cópias da chave de agora; no primeiro uso as cópias de um diário
  que sumiu ficam de lado (entrenos-anterior-…), e a tela fica sabendo que elas existem.
- Diário danificado (cortado): desbloquear não gasta PIN, e dá para restaurar guardando o arquivo.
- Pasta de dados apagada com o diário aberto: nada é recriado.
- Backup com gatilho, visão ou coluna calculada é recusado.
- Importar: o 🔒 passa para o registro repetido, pessoas completadas e nomes na prévia, pontuação
  não duplica, repetido dentro do arquivo, conferência fora da trava e custo limitado.
- Corpo grande: um por vez e parâmetros de scrypt só os do app.
- Página sem arquivos: aviso em vez de JSON cru ou página em branco.
- "Abrir Entre Nos.bat", README e construir_instalador.ps1 falam do antivírus.

Pasta de dados temporária (ENTRENOS_DADOS), data fixa 30/09/2026 e scrypt pequeno.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import struct
import subprocess
import tempfile
import threading
import time
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock

from entrenos import banco as banco_mod
from entrenos import cofre, config, nucleo, servidor
from entrenos.banco import Banco
from testes.test_restaurar_importar import PIN_BACKUP, Base, cifrar, decifrar, diario_de_origem
from testes.test_servidor import PIN, REF, ServidorDeTeste, _esperar

RAIZ = Path(os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

_remendos: list = []
_env_antigo: str | None = None
_pasta_env: str | None = None


def setUpModule() -> None:
    global _env_antigo, _pasta_env
    _env_antigo = os.environ.get("ENTRENOS_DADOS")
    _pasta_env = tempfile.mkdtemp(prefix="entrenos-teste-protecoes-env-")
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


def _ler(caminho: Path) -> str:
    return caminho.read_text(encoding="utf-8-sig")


def textos_do_diario(dados: bytes) -> list[str]:
    b = Banco.de_bytes(dados)
    try:
        return sorted(r["texto"] for r in b.registros(1000))
    finally:
        b.fechar()


def exportar(b: Banco) -> dict:
    return json.loads(json.dumps(b.exportar(), ensure_ascii=False))


# ---------------------------------------------------------------- restaurar com o app aberto

class TestCopiaDeAntesVemDaMemoria(Base):
    """Achado: a cópia "antes da restauração" era feita do disco. Um salvar que falhou (antivírus
    segurando o arquivo) deixava o último registro só na memória, e ele sumia na troca."""

    def test_registro_que_nao_chegou_ao_disco_vai_para_a_copia(self):
        backup = cifrar(Banco.novo().para_bytes())
        s = self.servidor()
        s.configurar(PIN)
        s.app._banco.criar_registro("R1 gravado no disco.", REF, [], [], [])
        with mock.patch("entrenos.cofre._gravar_atomico", side_effect=PermissionError("arquivo preso")):
            r = s.pedir("POST", "/api/registros", {"texto": "R2 só na memória.", "data_registro": "2026-09-30"})
        self.assertEqual(r.status, 500)
        self.assertEqual(self.textos(s), ["R1 gravado no disco.", "R2 só na memória."])
        r = self.restaurar(s, backup, token=s.token, pin_atual=PIN)
        self.assertEqual(r.status, 200, r.dados)
        copia = Path(r.dados["copia"])
        self.assertEqual(textos_do_diario(decifrar(copia.read_bytes(), PIN)),
                         ["R1 gravado no disco.", "R2 só na memória."])

    def test_sem_conseguir_salvar_antes_nada_e_trocado(self):
        backup = cifrar(Banco.novo().para_bytes())
        s = self.servidor()
        s.configurar(PIN)
        s.app._banco.criar_registro("Fica como estava.", REF, [], [], [])
        antes, token = self.arquivos(s), s.token
        with mock.patch("entrenos.cofre._gravar_atomico", side_effect=PermissionError("arquivo preso")):
            r = self.restaurar(s, backup, token=token, pin_atual=PIN)
        self.assertEqual(r.status, 500)
        self.assertEqual(r.dados["erro"], servidor.NAO_SALVOU_ANTES)
        self.assertEqual(self.arquivos(s), antes)
        self.assertEqual(s.token, token)
        self.assertEqual(self.textos(s), ["Fica como estava."])           # a sessão continua


# ---------------------------------------------------------------- cópias de um diário que sumiu

class TestCopiasDeOutroDiario(Base):
    """Achado: o rodízio apagava cópias de qualquer chave. Se o entrenos.cofre sumia (quarentena,
    exclusão), um diário novo (ou um backup antigo restaurado) apagava em cinco dias as cópias
    mais recentes do diário que sumiu."""

    def setUp(self):
        self.pasta = self.pasta_temporaria()

    def novo_cofre(self, dia: date = REF) -> cofre.Cofre:
        c = cofre.Cofre(self.pasta)
        c.hoje = lambda: dia
        c.agora = lambda: datetime(2026, 9, 30, 21, 5, 9)
        return c

    def usar_por_dias(self, c: cofre.Cofre, inicio: date, dias: int, dados: bytes) -> None:
        for i in range(dias):
            dia = inicio + timedelta(days=i)
            c.hoje = lambda dia=dia: dia
            c.salvar(dados + dia.isoformat().encode())

    def diario_a_com_copias(self) -> list[str]:
        a = self.novo_cofre()
        a.criar(PIN, b"diario A")
        self.usar_por_dias(a, date(2026, 9, 20), 6, b"diario A ")
        nomes = [p.name for p in a.backups()]
        self.assertEqual(len(nomes), config.BACKUPS_AUTOMATICOS)
        a.fechar()
        return nomes

    def test_primeiro_uso_poe_as_copias_de_lado_e_o_diario_novo_nao_as_apaga(self):
        nomes_a = self.diario_a_com_copias()
        (self.pasta / config.ARQUIVO_COFRE).unlink()                      # foi para a quarentena
        b = self.novo_cofre()
        self.assertEqual(b.resumo_das_copias()["quantas"], config.BACKUPS_AUTOMATICOS)
        self.assertEqual(b.resumo_das_copias()["mais_recente"], "2026-09-25")
        self.assertEqual(b.separar_copias_anteriores(), config.BACKUPS_AUTOMATICOS)
        self.assertEqual(b.backups(), [])
        b.criar(PIN_BACKUP, b"diario B")
        self.usar_por_dias(b, date(2026, 9, 26), 8, b"diario B ")
        anteriores = sorted(p for p in (self.pasta / config.PASTA_BACKUPS).iterdir()
                            if p.name.startswith(cofre.PREFIXO_DIARIO_ANTERIOR))
        self.assertEqual([p.name.replace(cofre.PREFIXO_DIARIO_ANTERIOR, "entrenos-") for p in anteriores],
                         sorted(nomes_a))
        for p in anteriores:                                              # abrem com o PIN do diário A
            self.assertTrue(decifrar(p.read_bytes(), PIN).startswith(b"diario A"))
        self.assertEqual(len(b.backups()), config.BACKUPS_AUTOMATICOS)    # o rodízio do B continua

    def test_rodizio_nao_apaga_copia_de_outra_chave(self):
        # Mesmo sem o primeiro uso (uma cópia de outro PIN que ficou em backups/), o rodízio só
        # apaga as da chave de agora; a de fora ganha o nome entrenos-anterior-… e fica.
        estranha = self.pasta / config.PASTA_BACKUPS / "entrenos-2026-09-01.cofre"
        estranha.parent.mkdir(parents=True)
        estranha.write_bytes(cifrar(b"outro diario", PIN_BACKUP))
        c = self.novo_cofre()
        c.criar(PIN, b"diario")
        self.usar_por_dias(c, date(2026, 9, 20), config.BACKUPS_AUTOMATICOS + 3, b"diario ")
        self.assertFalse(estranha.exists())
        guardada = estranha.with_name("entrenos-anterior-2026-09-01.cofre")
        self.assertEqual(decifrar(guardada.read_bytes(), PIN_BACKUP), b"outro diario")
        self.assertEqual(len(c.backups()), config.BACKUPS_AUTOMATICOS)
        self.assertNotIn(guardada, c.backups())

    def test_servidor_avisa_das_copias_e_configurar_as_poe_de_lado(self):
        s = self.servidor(hoje=lambda: date(2026, 9, 20))
        s.configurar(PIN)
        for dia in range(20, 26):
            s.app.cofre.hoje = lambda dia=dia: date(2026, 9, dia)
            s.app._banco.criar_registro(f"Registro do dia {dia}.", date(2026, 9, dia), [], [], [])
        s.pedir("POST", "/api/bloquear")
        Path(s.pasta, config.ARQUIVO_COFRE).unlink()
        estado = s.pedir("GET", "/api/estado", token=None).dados
        self.assertFalse(estado["configurado"])
        self.assertEqual(estado["copias_anteriores"]["quantas"], 5)
        self.assertEqual(estado["copias_anteriores"]["mais_recente"], "2026-09-25")
        self.assertEqual(Path(estado["copias_anteriores"]["pasta"]), Path(s.pasta) / config.PASTA_BACKUPS)
        # Desbloquear diz que o arquivo sumiu e onde estão as cópias (409, sem testar PIN).
        r = s.pedir("POST", "/api/desbloquear", {"pin": PIN}, token=None)
        self.assertEqual(r.status, 409)
        self.assertTrue(r.dados["sem_diario"])
        self.assertIn("quarentena", r.dados["erro"])
        self.assertIn("5 cópias", r.dados["erro"])
        self.assertEqual(s.app._falhas_pin, 0)
        s.configurar(PIN_BACKUP)
        nomes = sorted(p.name for p in (Path(s.pasta) / config.PASTA_BACKUPS).iterdir())
        self.assertEqual(len(nomes), 5)
        self.assertTrue(all(n.startswith(cofre.PREFIXO_DIARIO_ANTERIOR) for n in nomes), nomes)
        self.assertIsNone(s.pedir("GET", "/api/estado", token=None).dados["copias_anteriores"])

    def test_restaurar_no_primeiro_uso_tambem_poe_de_lado(self):
        s = self.servidor()
        s.configurar(PIN)
        s.app._banco.criar_registro("Do diário que sumiu.", REF, [], [], [])
        s.app.cofre.hoje = lambda: REF + timedelta(days=1)
        s.app._banco.criar_registro("Mais um.", REF, [], [], [])
        s.pedir("POST", "/api/bloquear")
        Path(s.pasta, config.ARQUIVO_COFRE).unlink()
        r = self.restaurar(s, cifrar(Banco.novo().para_bytes()))
        self.assertEqual(r.status, 200, r.dados)
        nomes = sorted(p.name for p in (Path(s.pasta) / config.PASTA_BACKUPS).iterdir())
        self.assertEqual(nomes, ["entrenos-anterior-2026-09-30.cofre", "entrenos-anterior-2026-10-01.cofre"])
        self.assertEqual(s.app.cofre.backups(), [])


# ---------------------------------------------------------------- diário danificado

class TestDiarioDanificado(Base):
    """Achado: com o arquivo do diário cortado, desbloquear dizia "PIN incorreto" (e fazia
    esperar), restaurar pedia token e configurar dizia que já existe: não havia saída pela tela."""

    def danificado(self) -> ServidorDeTeste:
        s = self.servidor(atraso_pin=0.3)
        s.configurar(PIN)
        s.app._banco.criar_registro("Antes de cortar.", REF, [], [], [])
        s.pedir("POST", "/api/bloquear")
        arquivo = Path(s.pasta, config.ARQUIVO_COFRE)
        conteudo = arquivo.read_bytes()
        arquivo.write_bytes(conteudo[:len(conteudo) // 2])             # cortado no meio
        self.cortado = arquivo.read_bytes()
        return s

    def test_desbloquear_nao_gasta_pin(self):
        s = self.danificado()
        self.assertTrue(s.pedir("GET", "/api/estado", token=None).dados["danificado"])
        inicio = time.monotonic()
        r = s.pedir("POST", "/api/desbloquear", {"pin": PIN}, token=None)
        self.assertLess(time.monotonic() - inicio, 0.25)
        self.assertEqual(r.status, 409)
        self.assertTrue(r.dados["danificado"])
        self.assertEqual(r.dados["erro"], servidor.DIARIO_DANIFICADO)
        self.assertEqual(s.app._falhas_pin, 0)

    def test_restaurar_guarda_o_arquivo_danificado(self):
        s = self.danificado()
        backup = cifrar(Banco.novo().para_bytes())
        r = self.restaurar(s, backup)                                     # sem confirmar: nada muda
        self.assertEqual(r.status, 409)
        self.assertTrue(r.dados["danificado"])
        self.assertEqual(Path(s.pasta, config.ARQUIVO_COFRE).read_bytes(), self.cortado)
        r = self.restaurar(s, backup, substituir_danificado=True)
        self.assertEqual(r.status, 200, r.dados)
        self.assertTrue(r.dados["danificado"])
        guardado = Path(r.dados["copia"])
        self.assertTrue(guardado.name.startswith(cofre.PREFIXO_DANIFICADO), guardado.name)
        self.assertEqual(guardado.read_bytes(), self.cortado)
        s.token = r.dados["token"]
        self.assertEqual(self.textos(s), [])
        s.pedir("POST", "/api/bloquear")
        self.assertEqual(s.desbloquear(PIN_BACKUP).status, 200)
        # Com o diário bom de novo, restaurar volta a pedir o token.
        self.assertEqual(self.restaurar(s, backup, token=None, substituir_danificado=True).status, 401)

    def test_arquivo_inteiro_nao_e_danificado(self):
        s = self.servidor()
        s.configurar(PIN)
        s.pedir("POST", "/api/bloquear")
        self.assertFalse(s.pedir("GET", "/api/estado", token=None).dados["danificado"])
        self.assertEqual(self.restaurar(s, cifrar(Banco.novo().para_bytes()), substituir_danificado=True).status, 423)


# ---------------------------------------------------------------- pasta de dados apagada

class TestPastaApagadaComODiarioAberto(Base):
    """Achado: o desinstalador ("Sim: apaga tudo para sempre") apagava a pasta, mas um Entre Nós
    aberto pelo pythonw recriava o diário no próximo salvar ou no bloqueio automático."""

    def test_bloqueio_automatico_nao_recria(self):
        s = self.servidor(minutos_bloqueio=1 / 60, intervalo_verificacao=0.1)
        s.configurar(PIN)
        s.app._banco.criar_registro("Registro que a pessoa mandou apagar para sempre.", REF, [], [], [])
        shutil.rmtree(s.pasta)
        self.assertTrue(_esperar(lambda: not s.app.desbloqueado, 5))
        time.sleep(0.3)
        self.assertFalse(Path(s.pasta).exists())

    def test_gravar_nao_recria_e_bloqueia(self):
        s = self.servidor()
        s.configurar(PIN)
        shutil.rmtree(s.pasta)
        r = s.pedir("POST", "/api/registros", {"texto": "Depois de apagar.", "data_registro": "2026-09-30"})
        self.assertEqual(r.status, 409)
        self.assertEqual(r.dados["erro"], servidor.PASTA_SUMIU)
        self.assertFalse(Path(s.pasta).exists())
        self.assertFalse(s.app.desbloqueado)
        self.assertEqual(s.pedir("GET", "/api/registros").status, 423)
        s.pedir("POST", "/api/bloquear")
        s.app.encerrar()
        self.assertFalse(Path(s.pasta).exists())


# ---------------------------------------------------------------- backup com gatilho

class TestBackupComObjetosEstranhos(Base):
    """Achado: a conferência do backup aceitava gatilhos e visões a mais no esquema."""

    @staticmethod
    def banco_com(sql: str) -> bytes:
        b = Banco.novo()
        try:
            b._con.executescript(sql)
            return b._con.serialize()
        finally:
            b.fechar()

    def test_recusa_gatilho_visao_tabela_e_coluna_calculada(self):
        casos = {
            "gatilho": "CREATE TRIGGER tira_cadeado AFTER INSERT ON registros BEGIN "
                       "UPDATE registros SET privado = 0 WHERE id = new.id; END;",
            "nao_apaga": "CREATE TRIGGER nao_apaga BEFORE DELETE ON registros BEGIN SELECT RAISE(IGNORE); END;",
            "visao": "CREATE VIEW espia AS SELECT texto FROM registros;",
            "tabela": "CREATE TABLE extra (x);",
            "indice": "CREATE INDEX extra_idx ON registros(texto);",
            "calculada": "ALTER TABLE historico ADD COLUMN sempre_zero INTEGER GENERATED ALWAYS AS (0) VIRTUAL;",
        }
        for nome, sql in casos.items():
            with self.subTest(nome):
                b = Banco.de_bytes(self.banco_com(sql))
                try:
                    with self.assertRaises(ValueError):
                        b.conferir_integridade()
                finally:
                    b.fechar()
        limpo = Banco.de_bytes(self.banco_com("ANALYZE;"))                  # estatísticas do SQLite: ok
        try:
            limpo.conferir_integridade()
        finally:
            limpo.fechar()

    def test_restaurar_backup_com_gatilho_400(self):
        s = self.servidor()
        conteudo = cifrar(self.banco_com(
            "CREATE TRIGGER nao_apaga BEFORE DELETE ON registros BEGIN SELECT RAISE(IGNORE); END;"))
        r = self.restaurar(s, conteudo)
        self.assertEqual(r.status, 400)
        self.assertEqual(r.dados["erro"], servidor.BACKUP_DANIFICADO)
        self.assertFalse(Path(s.pasta, config.ARQUIVO_COFRE).exists())


# ---------------------------------------------------------------- importar

class TestImportarCorrecoes(unittest.TestCase):
    """Achados da importação: 🔒 que não passava, pessoas completadas sem aparecer, prévia só com
    números, pontuação que duplicava e repetidos dentro do próprio arquivo."""

    def setUp(self):
        self.aqui = Banco.novo()
        self.addCleanup(self.aqui.fechar)

    def test_cadeado_passa_para_o_registro_repetido(self):
        origem = Banco.novo()
        origem.criar_registro("Ela ficou triste com o trabalho.", date(2026, 9, 20), [], [], [], privado=True)
        dados = exportar(origem)
        origem.fechar()
        rid = self.aqui.criar_registro("Ela ficou triste com o trabalho.", date(2026, 9, 20), [], [], [])
        previa = self.aqui.importar_json(dados, previa=True)
        self.assertEqual((previa["registros_novos"], previa["registros_repetidos"],
                          previa["registros_marcados_privados"]), (0, 1, 1))
        self.assertTrue(any("🔒" in a for a in previa["avisos"]), previa["avisos"])
        self.assertEqual(self.aqui.registros_privados(), set())                # a prévia não grava
        r = self.aqui.importar_json(dados)
        self.assertEqual(r["registros_marcados_privados"], 1)
        self.assertEqual(self.aqui.registros_privados(), {rid})
        self.assertEqual(self.aqui.historico(1)[0]["resumo"],
                         "importação: 0 registros, 1 registro marcado para não ir para a IA")
        # Nunca desliga: importar a versão sem 🔒 deixa como está.
        dados["registros"][0]["privado"] = False
        self.assertEqual(self.aqui.importar_json(dados)["registros_marcados_privados"], 0)
        self.assertEqual(self.aqui.registros_privados(), {rid})

    def test_pessoa_completada_aparece_na_previa_e_no_historico(self):
        origem = Banco.novo()
        mariana = origem.criar_pessoa("Mariana", "melhor amiga", apelidos=["Mari"])
        origem.atualizar_pessoa(mariana, aniversario=date(1998, 3, 12))
        origem.criar_registro("A Mari vai viajar.", date(2026, 9, 20), [], [], [])
        dados = exportar(origem)
        origem.fechar()
        aqui = self.aqui.criar_pessoa("Mariana", "melhor amiga")
        self.aqui.criar_registro("A Mari vai viajar.", date(2026, 9, 20), [], [], [])
        previa = self.aqui.importar_json(dados, previa=True)
        self.assertEqual((previa["registros_novos"], previa["pessoas_novas"], previa["pessoas_existentes"],
                          previa["pessoas_completadas"]), (0, 0, 1, 1))
        self.assertEqual(previa["detalhes_pessoas"]["completadas"],
                         [{"rotulo": "Mariana (melhor amiga)", "campos": ["aniversario", "apelidos"], "apelidos": ["Mari"]}])
        self.assertIsNone(self.aqui.pessoa(aqui)["aniversario"])                # prévia não grava
        self.aqui.importar_json(dados)
        pessoa = self.aqui.pessoa(aqui)
        self.assertEqual((pessoa["apelidos"], pessoa["aniversario"]), (["Mari"], date(1998, 3, 12)))
        self.assertEqual(self.aqui.historico(1)[0]["resumo"], "importação: 0 registros, 1 pessoa completada")

    def test_previa_mostra_nomes_e_avisa_de_homonimo(self):
        origem = Banco.novo()
        origem.criar_pessoa("Mariana", "amiga")
        origem.criar_pessoa("Sandra", "mãe")
        origem.criar_registro("A Mariana ligou.", date(2026, 9, 20), [], [], [])
        dados = exportar(origem)
        origem.fechar()
        self.aqui.criar_pessoa("Mariana", "melhor amiga")
        self.aqui.criar_pessoa("Sandra", "mãe")
        previa = self.aqui.importar_json(dados, previa=True)
        self.assertEqual(previa["detalhes_pessoas"]["novas"],
                         [{"rotulo": "Mariana (amiga)", "parecida_com": "Mariana (melhor amiga)"}])
        self.assertEqual(previa["detalhes_pessoas"]["existentes"],
                         [{"arquivo": "Sandra (mãe)", "daqui": "Sandra (mãe)"}])
        self.aqui.importar_json(dados)
        historico = json.dumps(self.aqui.historico(10), ensure_ascii=False, default=str)
        self.assertIn("importação: 1 registro", historico)
        self.assertNotIn("Mariana", historico)                           # só contagens no histórico

    def test_pontuacao_nao_duplica_e_repetido_no_arquivo_conta_a_parte(self):
        origem = Banco.novo()
        origem.criar_registro("Ela adora café.", date(2026, 9, 20), [], [], [])
        origem.criar_registro("Ela comentou da viagem!", date(2026, 9, 21), [], [], [])
        dados = exportar(origem)
        origem.fechar()
        dados["registros"].append({**dados["registros"][1], "id": 999})     # o mesmo registro duas vezes
        self.aqui.criar_registro("Ela adora café", date(2026, 9, 20), [], [], [])
        r = self.aqui.importar_json(dados)
        self.assertEqual((r["registros_novos"], r["registros_repetidos"], r["registros_repetidos_no_arquivo"]),
                         (1, 1, 1))
        self.assertTrue(any("dentro dele mesmo" in a for a in r["avisos"]), r["avisos"])
        self.assertEqual(sorted(x["texto"] for x in self.aqui.registros()),
                         ["Ela adora café", "Ela comentou da viagem!"])

    def test_custo_de_conferir_trechos_tem_limite(self):
        texto = "a" * 2_000_000
        dados = {"app": "entre-nos", "versao": "0.2.0", "registros": [{
            "id": 1, "texto": texto, "data_registro": "2026-09-20",
            "itens": [{"tipo": "fato", "trecho": "a" * 40 + "b"} for _ in range(2000)]}]}
        inicio = time.monotonic()
        with self.assertRaises(ValueError) as erro:
            banco_mod.validar_exportacao(dados)
        self.assertEqual(str(erro.exception), banco_mod.GRANDE_DEMAIS)
        self.assertLess(time.monotonic() - inicio, 2)

    def test_muitos_registros_da_mesma_pessoa(self):
        registros = [{"id": i, "texto": f"Registro {i} com a Ana.", "data_registro": "2026-09-20",
                      "pessoas": [{"nome": "Ana"}]} for i in range(1, 20_001)]
        dados = {"app": "entre-nos", "versao": "0.2.0", "registros": registros}
        inicio = time.monotonic()
        r = self.aqui.importar_json(dados, previa=True)
        self.assertEqual((r["registros_novos"], r["pessoas_novas"]), (20_000, 1))
        self.assertLess(time.monotonic() - inicio, 30)


class TestImportarForaDaTrava(Base):
    """Achado: a conferência do JSON (que pode levar segundos) prendia a trava do app: estado,
    bloquear e Sair esperavam, e o bloqueio automático não acontecia."""

    def test_conferencia_roda_sem_a_trava_do_app(self):
        s = self.servidor()
        s.configurar(PIN)
        origem = diario_de_origem()
        texto = json.dumps(origem.exportar(), ensure_ascii=False, default=str)
        origem.fechar()
        livre = []
        original = nucleo.validar_exportacao

        def conferir(dados):
            def tentar():
                if s.app._trava.acquire(timeout=1):
                    livre.append(True)
                    s.app._trava.release()
                else:
                    livre.append(False)
            outra = threading.Thread(target=tentar)
            outra.start()
            outra.join(3)
            return original(dados)

        with mock.patch.object(nucleo, "validar_exportacao", side_effect=conferir):
            r = s.pedir("POST", "/api/importar/previa", {"json": texto})
        self.assertEqual(r.status, 200, r.dados)
        self.assertEqual(livre, [True])


# ---------------------------------------------------------------- corpo grande e scrypt

class TestCorpoGrande(Base):
    def test_um_arquivo_grande_por_vez(self):
        s = self.servidor()
        self.assertTrue(s.app.vez_do_arquivo.acquire(blocking=False))       # outro pedido em andamento
        try:
            r = self.restaurar(s, cifrar(Banco.novo().para_bytes()))
            self.assertEqual(r.status, 503)
            self.assertEqual(r.dados["erro"], servidor.ARQUIVO_OCUPADO)
        finally:
            s.app.vez_do_arquivo.release()
        self.assertEqual(self.restaurar(s, cifrar(Banco.novo().para_bytes())).status, 200)
        self.assertTrue(s.app.vez_do_arquivo.acquire(blocking=False))       # devolvido no fim
        s.app.vez_do_arquivo.release()

    def test_scrypt_so_com_os_parametros_do_app(self):
        bom = bytearray(cifrar(Banco.novo().para_bytes()))
        for n, r, p in ((2 ** 20, 2, 16), (2 ** 18, 8, 1), (2 ** 16, 8, 2), (2 ** 16, 16, 1)):
            with self.subTest(n=n, r=r, p=p):
                mexido = bytearray(bom)
                struct.pack_into(">III", mexido, len(cofre.MAGICO), n, r, p)
                with self.assertRaises(cofre.CofreCorrompido):
                    cofre.tamanho_decifrado(bytes(mexido))
        s = self.servidor()
        mexido = bytearray(bom)
        struct.pack_into(">III", mexido, len(cofre.MAGICO), 2 ** 20, 2, 16)
        inicio = time.monotonic()
        r = self.restaurar(s, bytes(mexido))
        self.assertEqual(r.status, 400)
        self.assertEqual(r.dados["erro"], servidor.NAO_E_BACKUP)
        self.assertLess(time.monotonic() - inicio, 2)
        self.assertTrue(cofre._parametros_validos(2 ** 16, 8, 1))
        self.assertTrue(cofre._parametros_validos(cofre.VERIFICADOR_N, cofre.VERIFICADOR_R, cofre.VERIFICADOR_P))

    def test_texto_invalido_fora_do_arquivo_continua_recusado(self):
        s = self.servidor()
        corpo = '{"arquivo_b64": "AAAA", "pin": "\\ud800"}'.encode()
        r = s.pedir("POST", "/api/restaurar", bruto=corpo, token=None)
        self.assertEqual(r.status, 400)
        corpo = '{"arquivo_b64": "AA\\ud800AA", "pin": "x"}'.encode()
        r = s.pedir("POST", "/api/restaurar", bruto=corpo, token=None)
        self.assertEqual(r.status, 400)


# ---------------------------------------------------------------- página sem arquivos

class TestPaginaSemArquivos(unittest.TestCase):
    """Achado: sem index.html o navegador mostrava JSON cru; sem app.js, página em branco."""

    def test_sem_index_mostra_o_que_fazer(self):
        vazia = tempfile.mkdtemp(prefix="entrenos-teste-web-vazia-")
        self.addCleanup(shutil.rmtree, vazia, True)
        s = ServidorDeTeste(pasta_web=vazia)
        self.addCleanup(s.fechar)
        for caminho in ("/", "/index.html"):
            r = s.pedir("GET", caminho, token=None)
            self.assertEqual(r.status, 404)
            self.assertTrue(r.cabecalhos["Content-Type"].startswith("text/html"))
            texto = r.bruto.decode("utf-8")
            self.assertIn("antivírus", texto)
            self.assertIn("Reinstale", texto)
        self.assertEqual(s.pedir("GET", "/app.js", token=None).status, 404)

    def test_main_avisa_e_nao_abre_sem_os_arquivos(self):
        vazia = Path(tempfile.mkdtemp(prefix="entrenos-teste-web-vazia-"))
        self.addCleanup(shutil.rmtree, vazia, True)
        (vazia / "index.html").write_text("<p>só a página</p>", encoding="utf-8")
        self.assertEqual(servidor.faltam_arquivos_da_pagina(vazia), ["app.js", "estilo.css"])
        self.assertEqual(servidor.faltam_arquivos_da_pagina(), [])
        with mock.patch.object(config, "PASTA_WEB", vazia), mock.patch.object(servidor, "_avisar") as avisar, \
                mock.patch.object(servidor, "criar_servidor") as criar:
            self.assertEqual(servidor.main(["--sem-navegador", "--porta", "8797"]), 1)
        criar.assert_not_called()
        avisar.assert_called_once()
        self.assertIn("app.js", avisar.call_args.args[0])
        self.assertIn("quarentena", avisar.call_args.args[0])

    def test_index_tem_aviso_que_o_app_esconde(self):
        html = _ler(RAIZ / "web" / "index.html")
        self.assertRegex(html, r'<p id="carregando"[^>]*>[^<]*Se esta mensagem não sumir')
        self.assertNotIn("<script>", html)                                  # sem script inline (CSP)
        self.assertIn("$('#carregando')", _ler(RAIZ / "web" / "app.js"))


# ---------------------------------------------------------------- .bat, README e script de construção

class TestArquivosParaAPessoa(unittest.TestCase):
    """Achados: o .bat abria nada quando faltava o iniciar.py (antivírus), o README não falava de
    antivírus e o construir_instalador.ps1 dizia "não gerou" quando o antivírus apagou o .exe."""

    BAT = RAIZ / "Abrir Entre Nos.bat"

    def test_bat_confere_os_arquivos_e_o_python(self):
        bruto = self.BAT.read_bytes()
        bruto.decode("ascii")                                              # cmd.exe: sem acentos
        self.assertNotIn(b"\n", bruto.replace(b"\r\n", b""))                # CRLF
        bat = bruto.decode("ascii")
        for arquivo in ("iniciar.py", "entrenos\\servidor.py", "entrenos\\nucleo.py", "entrenos\\cofre.py",
                        "web\\index.html", "web\\app.js"):
            self.assertIn(f'"{arquivo}"', bat)
        self.assertIn("import cryptography, entrenos.servidor", bat)
        self.assertIn("%LOCALAPPDATA%\\Python\\pythoncore-3.14-64\\pythonw.exe", bat)
        self.assertNotIn("C:\\Users\\Usuario", bat)
        self.assertNotIn("-m entrenos", bat)                                 # nada de contornar o antivírus
        for trecho in ("quarentena", "%LOCALAPPDATA%\\EntreNos", "Nao desligue o antivirus", "pause", "EntreNos-Setup"):
            self.assertIn(trecho, bat)

    @unittest.skipUnless(os.name == "nt", "cmd.exe")
    def test_bat_sem_iniciar_explica_e_nao_abre_nada(self):
        pasta = Path(tempfile.mkdtemp(prefix="entrenos-teste-bat-"))
        self.addCleanup(shutil.rmtree, pasta, True)
        copia = pasta / "Abrir Entre Nos.bat"
        shutil.copyfile(self.BAT, copia)
        r = subprocess.run(["cmd.exe", "/c", str(copia)], stdin=subprocess.DEVNULL, capture_output=True,
                           timeout=30, cwd=pasta)
        saida = r.stdout.decode("cp850", "replace")
        self.assertEqual(r.returncode, 1, saida)
        self.assertIn("iniciar.py", saida)
        self.assertIn("quarentena", saida)
        self.assertEqual(sorted(p.name for p in pasta.iterdir()), ["Abrir Entre Nos.bat"])

    def test_readme_fala_do_antivirus_e_de_outro_computador(self):
        readme = _ler(RAIZ / "README.md")
        for trecho in ("## Se o antivírus bloquear", "quarentena", "Não desligue o antivírus",
                       "C:\\Users\\Usuario\\EntreNos", "Get-FileHash", "Já tenho um backup",
                       "Mais informações", "Executar assim mesmo", "Se nada acontecer"):
            self.assertIn(trecho, readme)
        self.assertNotIn("e-mail para você mesmo", readme)

    def test_script_de_construcao_confere_o_antivirus(self):
        script = _ler(RAIZ / "construir_instalador.ps1")
        for trecho in ("'iniciar.py'", "Start-Sleep", "quarentena", "não desligue o antivírus", "Get-FileHash"):
            self.assertIn(trecho, script)
        # Conferir de novo depois da espera, antes de dizer "Pronto".
        self.assertLess(script.index("Start-Sleep"), script.index("'Pronto.'"))

    def test_script_de_construcao_confere_o_que_o_bat_confere(self):
        # Achado de 07/10/2026: o .bat passou a conferir o entrenos\nucleo.py (metade do app mora
        # nele desde a seção 19) e o construir_instalador.ps1 não. Sem ele, a conferência passava
        # calada e o EntreNos.exe quebrava ao abrir, em vez de o build falar da quarentena.
        script = _ler(RAIZ / "construir_instalador.ps1")
        bloco = re.search(r"\$obrigatorios = @\((.*?)\n\)", script, re.S)
        self.assertIsNotNone(bloco)
        obrigatorios = set(re.findall(r"'([^']+)'", bloco.group(1)))
        bat = self.BAT.read_bytes().decode("ascii")
        do_bat = set(re.findall(r'"((?:entrenos|web)\\[^"]+|iniciar\.py)"', bat))
        self.assertIn("entrenos\\nucleo.py", do_bat)
        self.assertLessEqual(do_bat, obrigatorios, sorted(do_bat - obrigatorios))
        self.assertLessEqual({"entrenos\\servidor.py", "entrenos\\nucleo.py", "entrenos\\cofre.py",
                              "entrenos\\banco.py"}, obrigatorios)


if __name__ == "__main__":
    unittest.main()
