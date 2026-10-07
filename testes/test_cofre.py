"""Testes de entrenos.cofre (DESIGN.md, seção 13).

Tudo em pasta temporária apontada por ENTRENOS_DADOS; nunca em %LOCALAPPDATA%.
Data de referência fixa: quarta-feira, 30/09/2026 (os backups levam a data no nome).
"""
import os
import shutil
import struct
import tempfile
import unicodedata
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from entrenos import config
from entrenos.cofre import (MAGICO, TAMANHO_CABECALHO, Cofre, CofreCorrompido, CofreFechado,
                            PinErrado, problema_do_pin)

REF = date(2026, 9, 30)  # quarta-feira
PIN = "tulipas amarelas"
DADOS = "Ela me contou que a apresentação do dia 20 a deixa nervosa.".encode("utf-8")


class BaseCofre(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="entrenos-teste-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        antigo = os.environ.get("ENTRENOS_DADOS")
        os.environ["ENTRENOS_DADOS"] = self.tmp
        self.addCleanup(self._restaurar_ambiente, antigo)
        self.pasta = config.pasta_dados()
        self.assertEqual(Path(self.tmp), self.pasta)

    @staticmethod
    def _restaurar_ambiente(antigo):
        if antigo is None:
            os.environ.pop("ENTRENOS_DADOS", None)
        else:
            os.environ["ENTRENOS_DADOS"] = antigo

    def novo_cofre(self, pasta: Path | None = None, hoje: date = REF) -> Cofre:
        cofre = Cofre(pasta or self.pasta)
        cofre.hoje = lambda: hoje
        return cofre

    def criado(self, dados: bytes = DADOS, pin: str = PIN) -> Cofre:
        cofre = self.novo_cofre()
        cofre.criar(pin, dados)
        return cofre

    def abrir_arquivo(self, caminho: Path, pin: str) -> bytes:
        """Abre um arquivo cifrado qualquer (backup, cópia) copiando-o para uma pasta própria."""
        pasta = Path(tempfile.mkdtemp(prefix="entrenos-copia-", dir=self.tmp))
        shutil.copyfile(caminho, pasta / config.ARQUIVO_COFRE)
        return Cofre(pasta).abrir(pin)

    def alterar_byte(self, posicao: int) -> None:
        conteudo = bytearray(self.pasta.joinpath(config.ARQUIVO_COFRE).read_bytes())
        conteudo[posicao] ^= 0x01
        self.pasta.joinpath(config.ARQUIVO_COFRE).write_bytes(bytes(conteudo))


class TestCriarEAbrir(BaseCofre):
    def test_nao_existe_antes_de_criar(self):
        cofre = self.novo_cofre()
        self.assertFalse(cofre.existe())
        self.assertFalse(cofre.aberto)

    def test_criar_e_abrir_devolve_os_mesmos_dados(self):
        cofre = self.criado()
        self.assertTrue(cofre.existe())
        self.assertTrue(cofre.aberto)
        self.assertEqual(self.novo_cofre().abrir(PIN), DADOS)

    def test_abrir_deixa_o_cofre_aberto(self):
        self.criado()
        cofre = self.novo_cofre()
        self.assertFalse(cofre.aberto)
        cofre.abrir(PIN)
        self.assertTrue(cofre.aberto)

    def test_cabecalho_tem_magico_e_parametros_scrypt(self):
        self.criado()
        conteudo = (self.pasta / config.ARQUIVO_COFRE).read_bytes()
        self.assertTrue(conteudo.startswith(MAGICO))
        n, r, p = struct.unpack_from(">III", conteudo, len(MAGICO))
        self.assertEqual((n, r, p), (2 ** 16, 8, 1))
        # cabeçalho + dados + tag de 16 bytes do GCM
        self.assertEqual(len(conteudo), TAMANHO_CABECALHO + len(DADOS) + 16)

    def test_arquivo_nao_contem_texto_em_claro(self):
        self.criado()
        conteudo = (self.pasta / config.ARQUIVO_COFRE).read_bytes()
        for trecho in ("apresentação", "nervosa", "Ela me contou"):
            self.assertNotIn(trecho.encode("utf-8"), conteudo)
            self.assertNotIn(trecho.encode("utf-16-le"), conteudo)

    def test_criar_cria_a_pasta(self):
        pasta = self.pasta / "sub" / "pasta"
        cofre = self.novo_cofre(pasta)
        cofre.criar(PIN, DADOS)
        self.assertTrue((pasta / config.ARQUIVO_COFRE).is_file())

    def test_criar_duas_vezes_recusa(self):
        self.criado()
        with self.assertRaises(FileExistsError):
            self.novo_cofre().criar("outro pin qualquer", b"outros dados")
        self.assertEqual(self.novo_cofre().abrir(PIN), DADOS)

    def test_dados_vazios_e_grandes(self):
        cofre = self.criado(b"")
        self.assertEqual(self.novo_cofre().abrir(PIN), b"")
        grande = os.urandom(2 * 1024 * 1024)
        cofre.salvar(grande)
        self.assertEqual(self.novo_cofre().abrir(PIN), grande)

    def test_abrir_sem_arquivo(self):
        with self.assertRaises(FileNotFoundError):
            self.novo_cofre().abrir(PIN)


class TestPin(BaseCofre):
    def test_pin_errado(self):
        self.criado()
        cofre = self.novo_cofre()
        with self.assertRaises(PinErrado):
            cofre.abrir("tulipas vermelhas")
        self.assertFalse(cofre.aberto)

    def test_pin_curto_recusado(self):
        cofre = self.novo_cofre()
        for pin in ("", "12345", "a" * (config.PIN_MINIMO - 1)):
            with self.subTest(pin=pin), self.assertRaises(ValueError):
                cofre.criar(pin, DADOS)
        self.assertFalse(cofre.existe())
        self.assertFalse(cofre.aberto)

    def test_pin_no_limite_aceito(self):
        pin = "tulip!"[:config.PIN_MINIMO]
        self.criado(pin=pin)
        self.assertEqual(self.novo_cofre().abrir(pin), DADOS)

    def test_pin_facil_de_adivinhar_recusado(self):
        # Quem copia o arquivo testa PINs fora do app, sem atraso: os óbvios caem em segundos.
        cofre = self.novo_cofre()
        for pin in ("000000", "111111", "123456", "654321", "abcdef", "qwerty", "      ", "121212",
                    "senha123", "Senha 123", "amor123", "12345678", "987654321"):
            with self.subTest(pin=pin):
                self.assertIsNotNone(problema_do_pin(pin))
                with self.assertRaises(ValueError):
                    cofre.criar(pin, DADOS)
        self.assertFalse(cofre.existe())

    def test_pin_forte_aceito(self):
        for pin in ("tulipas amarelas", "pizza de sexta", "4815162342", "gir4ssol", "coração de pão"):
            with self.subTest(pin=pin):
                self.assertIsNone(problema_do_pin(pin))

    def test_trocar_para_pin_fraco_recusado(self):
        cofre = self.criado()
        with self.assertRaises(ValueError):
            cofre.trocar_pin(PIN, "123456")
        self.assertEqual(self.novo_cofre().abrir(PIN), DADOS)

    def test_pin_que_nao_e_texto(self):
        with self.assertRaises(ValueError):
            self.novo_cofre().criar(12345678, DADOS)
        self.criado()
        with self.assertRaises(PinErrado):
            self.novo_cofre().abrir(None)

    def test_pin_com_acento_em_nfc_ou_nfd(self):
        pin = unicodedata.normalize("NFC", "coração de pão")
        self.criado(pin=pin)
        self.assertEqual(self.novo_cofre().abrir(unicodedata.normalize("NFD", pin)), DADOS)

    def test_pin_diferencia_maiusculas(self):
        self.criado()
        with self.assertRaises(PinErrado):
            self.novo_cofre().abrir(PIN.upper())


class TestAdulteracao(BaseCofre):
    def test_byte_alterado_nos_dados_faz_abrir_falhar(self):
        self.criado()
        tamanho = (self.pasta / config.ARQUIVO_COFRE).stat().st_size
        for posicao in (TAMANHO_CABECALHO, TAMANHO_CABECALHO + 10, tamanho - 1):
            with self.subTest(posicao=posicao):
                self.alterar_byte(posicao)
                with self.assertRaises(PinErrado):
                    self.novo_cofre().abrir(PIN)
                self.alterar_byte(posicao)       # desfaz
        self.assertEqual(self.novo_cofre().abrir(PIN), DADOS)

    def test_byte_alterado_no_cabecalho_faz_abrir_falhar(self):
        self.criado()
        inicio_sal = len(MAGICO) + 12
        posicoes = {
            "mágico": 0,
            "parâmetro r": len(MAGICO) + 7,
            "sal": inicio_sal + 3,
            "nonce": inicio_sal + 16 + 5,
        }
        for nome, posicao in posicoes.items():
            with self.subTest(parte=nome):
                self.alterar_byte(posicao)
                with self.assertRaises((PinErrado, CofreCorrompido)):
                    self.novo_cofre().abrir(PIN)
                self.alterar_byte(posicao)

    def test_magico_errado_e_arquivo_corrompido(self):
        self.criado()
        self.alterar_byte(0)
        with self.assertRaises(CofreCorrompido):
            self.novo_cofre().abrir(PIN)

    def test_arquivo_cortado(self):
        self.criado()
        arquivo = self.pasta / config.ARQUIVO_COFRE
        arquivo.write_bytes(arquivo.read_bytes()[:TAMANHO_CABECALHO + 5])
        with self.assertRaises(CofreCorrompido):
            self.novo_cofre().abrir(PIN)

    def test_arquivo_estranho(self):
        (self.pasta / config.ARQUIVO_COFRE).write_bytes(b"SQLite format 3\x00" + os.urandom(200))
        with self.assertRaises(CofreCorrompido):
            self.novo_cofre().abrir(PIN)

    def test_parametros_absurdos_no_cabecalho(self):
        self.criado()
        arquivo = self.pasta / config.ARQUIVO_COFRE
        conteudo = bytearray(arquivo.read_bytes())
        struct.pack_into(">III", conteudo, len(MAGICO), 2 ** 30, 8, 1)
        arquivo.write_bytes(bytes(conteudo))
        with self.assertRaises(CofreCorrompido):
            self.novo_cofre().abrir(PIN)


class TestSalvarEFechar(BaseCofre):
    def test_salvar_e_reabrir(self):
        cofre = self.criado()
        cofre.salvar(b"versao 2")
        self.assertEqual(self.novo_cofre().abrir(PIN), b"versao 2")

    def test_salvar_usa_nonce_novo_e_mesmo_sal(self):
        cofre = self.criado()
        arquivo = self.pasta / config.ARQUIVO_COFRE
        antes = arquivo.read_bytes()
        cofre.salvar(DADOS)
        depois = arquivo.read_bytes()
        inicio_sal = len(MAGICO) + 12
        self.assertEqual(antes[inicio_sal:inicio_sal + 16], depois[inicio_sal:inicio_sal + 16])
        self.assertNotEqual(antes[inicio_sal + 16:TAMANHO_CABECALHO], depois[inicio_sal + 16:TAMANHO_CABECALHO])
        self.assertNotEqual(antes[TAMANHO_CABECALHO:], depois[TAMANHO_CABECALHO:])

    def test_fechar_esquece_a_chave(self):
        cofre = self.criado()
        cofre.fechar()
        self.assertFalse(cofre.aberto)
        self.assertIsNone(cofre._chave)
        with self.assertRaises(CofreFechado):
            cofre.salvar(b"depois de fechar")
        self.assertEqual(self.novo_cofre().abrir(PIN), DADOS)

    def test_fechar_zera_a_chave_na_memoria(self):
        cofre = self.criado()
        chave = cofre._chave
        self.assertTrue(any(chave))
        cofre.fechar()
        self.assertFalse(any(chave))

    def test_salvar_sem_abrir_da_erro(self):
        self.criado()
        with self.assertRaises(CofreFechado):
            self.novo_cofre().salvar(b"x")

    def test_abrir_de_novo_depois_de_fechar(self):
        cofre = self.criado()
        cofre.fechar()
        self.assertEqual(cofre.abrir(PIN), DADOS)
        cofre.salvar(b"de novo")
        self.assertEqual(self.novo_cofre().abrir(PIN), b"de novo")

    def test_fechar_duas_vezes_nao_da_erro(self):
        cofre = self.criado()
        cofre.fechar()
        cofre.fechar()
        self.assertFalse(cofre.aberto)


class TestGravacaoAtomica(BaseCofre):
    def test_nao_deixa_temporarios(self):
        cofre = self.criado()
        for i in range(3):
            cofre.salvar(f"versao {i}".encode())
        restos = [p.name for p in self.pasta.rglob("*") if ".tmp" in p.name]
        self.assertEqual(restos, [])

    def test_falha_ao_substituir_preserva_o_original(self):
        cofre = self.criado()
        with mock.patch("entrenos.cofre.os.replace", side_effect=OSError("disco cheio")), \
                self.assertLogs("entrenos.cofre", level="WARNING"):
            with self.assertRaises(OSError):
                cofre.salvar(b"nunca gravado")
        self.assertEqual(self.novo_cofre().abrir(PIN), DADOS)
        restos = [p.name for p in self.pasta.iterdir() if ".tmp" in p.name]
        self.assertEqual(restos, [])

    def test_windows_ocupado_tenta_de_novo(self):
        cofre = self.criado()
        replace_real = os.replace
        tentativas = []

        def replace_ocupado(origem, alvo):
            tentativas.append(alvo)
            if len(tentativas) == 1:
                raise PermissionError("arquivo em uso")
            return replace_real(origem, alvo)

        with mock.patch("entrenos.cofre.os.replace", side_effect=replace_ocupado), \
                mock.patch("entrenos.cofre.time.sleep"):
            cofre.salvar(b"segunda tentativa")
        self.assertEqual(self.novo_cofre().abrir(PIN), b"segunda tentativa")


class TestBackups(BaseCofre):
    def test_backup_guarda_a_versao_anterior(self):
        cofre = self.criado()
        self.assertEqual(cofre.backups(), [])
        cofre.salvar(b"versao 2")
        backups = cofre.backups()
        self.assertEqual([p.name for p in backups], ["entrenos-2026-09-30.cofre"])
        self.assertEqual(backups[0].parent, self.pasta / config.PASTA_BACKUPS)
        self.assertEqual(self.abrir_arquivo(backups[0], PIN), DADOS)

    def test_um_backup_por_dia(self):
        cofre = self.criado()
        for i in range(4):
            cofre.salvar(f"versao {i}".encode())
        backups = cofre.backups()
        self.assertEqual(len(backups), 1)
        # fica a primeira versão do dia (o estado antes das mudanças de hoje)
        self.assertEqual(self.abrir_arquivo(backups[0], PIN), DADOS)

    def test_backups_rotativos_mantem_os_mais_novos(self):
        cofre = self.criado()
        dias = [REF + timedelta(days=i) for i in range(config.BACKUPS_AUTOMATICOS + 3)]
        for dia in dias:
            cofre.hoje = lambda dia=dia: dia
            cofre.salvar(dia.isoformat().encode())
        nomes = [p.name for p in cofre.backups()]
        self.assertEqual(len(nomes), config.BACKUPS_AUTOMATICOS)
        esperados = [f"entrenos-{d.isoformat()}.cofre" for d in reversed(dias)][:config.BACKUPS_AUTOMATICOS]
        self.assertEqual(nomes, esperados)
        # o backup de um dia guarda o que estava gravado antes do primeiro salvar daquele dia
        self.assertEqual(self.abrir_arquivo(cofre.backups()[0], PIN), dias[-2].isoformat().encode())

    def test_backups_tambem_sao_cifrados(self):
        cofre = self.criado()
        cofre.salvar(b"outra coisa")
        conteudo = cofre.backups()[0].read_bytes()
        self.assertTrue(conteudo.startswith(MAGICO))
        self.assertNotIn(b"nervosa", conteudo)

    def test_falha_no_backup_nao_impede_salvar(self):
        cofre = self.criado()
        (self.pasta / config.PASTA_BACKUPS).write_bytes(b"um arquivo no lugar da pasta")
        with self.assertLogs("entrenos.cofre", level="WARNING") as registro:
            cofre.salvar(b"salvo mesmo assim")
        self.assertEqual(self.novo_cofre().abrir(PIN), b"salvo mesmo assim")
        self.assertNotIn("salvo mesmo assim", "\n".join(registro.output))

    def test_copiar_backup(self):
        self.criado()
        destino = Path(tempfile.mkdtemp(prefix="entrenos-destino-", dir=self.tmp))
        cofre = self.novo_cofre()
        copia = cofre.copiar_backup(destino)
        self.assertEqual(copia.name, "entrenos-backup-2026-09-30.cofre")
        self.assertEqual(copia.read_bytes(), (self.pasta / config.ARQUIVO_COFRE).read_bytes())
        segunda = cofre.copiar_backup(destino)
        self.assertEqual(segunda.name, "entrenos-backup-2026-09-30-2.cofre")
        self.assertEqual(copia.read_bytes(), segunda.read_bytes())
        self.assertEqual(self.abrir_arquivo(copia, PIN), DADOS)

    def test_copiar_backup_destino_inexistente(self):
        self.criado()
        with self.assertRaises(NotADirectoryError):
            self.novo_cofre().copiar_backup(self.pasta / "nao-existe")

    def test_copiar_backup_sem_cofre(self):
        with self.assertRaises(FileNotFoundError):
            self.novo_cofre().copiar_backup(self.pasta)

    def test_reescrever_backups(self):
        cofre = self.criado()
        cofre.salvar(b"versao 2")
        backup = cofre.backups()[0]
        vistos = []

        def transformar(dados: bytes) -> bytes | None:
            vistos.append(dados)
            return dados.replace(b"nervosa", b"*******")

        self.assertEqual(cofre.reescrever_backups(transformar), 1)
        self.assertEqual(vistos, [DADOS])
        self.assertEqual(self.abrir_arquivo(backup, PIN), DADOS.replace(b"nervosa", b"*******"))
        self.assertEqual(cofre.reescrever_backups(lambda d: None), 0)     # None = não mexe
        self.assertEqual(self.novo_cofre().abrir(PIN), b"versao 2")         # o principal fica

    def test_reescrever_backups_com_cofre_fechado(self):
        cofre = self.criado()
        cofre.salvar(b"versao 2")
        cofre.fechar()
        with self.assertRaises(CofreFechado):
            cofre.reescrever_backups(lambda d: b"x")

    def test_reescrever_backups_ignora_copia_de_outro_pin(self):
        cofre = self.criado()
        cofre.salvar(b"versao 2")
        backup = cofre.backups()[0]
        outra = Path(tempfile.mkdtemp(prefix="entrenos-outro-", dir=self.tmp))
        estranho = self.novo_cofre(outra)
        estranho.criar("outro pin qualquer", b"de outro cofre")
        shutil.copyfile(outra / config.ARQUIVO_COFRE, backup)
        self.assertEqual(cofre.reescrever_backups(lambda d: b"x"), 0)
        self.assertEqual(self.abrir_arquivo(backup, "outro pin qualquer"), b"de outro cofre")


class TestTrocarPin(BaseCofre):
    NOVO = "girassois no domingo"

    def test_trocar_pin(self):
        cofre = self.criado()
        cofre.trocar_pin(PIN, self.NOVO)
        self.assertTrue(cofre.aberto)
        with self.assertRaises(PinErrado):
            self.novo_cofre().abrir(PIN)
        self.assertEqual(self.novo_cofre().abrir(self.NOVO), DADOS)

    def test_trocar_pin_recifra_com_sal_novo(self):
        cofre = self.criado()
        arquivo = self.pasta / config.ARQUIVO_COFRE
        antes = arquivo.read_bytes()
        cofre.trocar_pin(PIN, self.NOVO)
        depois = arquivo.read_bytes()
        inicio_sal = len(MAGICO) + 12
        self.assertNotEqual(antes[inicio_sal:inicio_sal + 16], depois[inicio_sal:inicio_sal + 16])

    def test_salvar_depois_de_trocar_usa_o_pin_novo(self):
        cofre = self.criado()
        cofre.trocar_pin(PIN, self.NOVO)
        cofre.salvar(b"depois da troca")
        self.assertEqual(self.novo_cofre().abrir(self.NOVO), b"depois da troca")

    def test_trocar_com_pin_atual_errado(self):
        cofre = self.criado()
        antes = (self.pasta / config.ARQUIVO_COFRE).read_bytes()
        with self.assertRaises(PinErrado):
            cofre.trocar_pin("pin que nao e", self.NOVO)
        self.assertEqual((self.pasta / config.ARQUIVO_COFRE).read_bytes(), antes)

    def test_trocar_para_pin_curto(self):
        cofre = self.criado()
        with self.assertRaises(ValueError):
            cofre.trocar_pin(PIN, "123")
        self.assertEqual(self.novo_cofre().abrir(PIN), DADOS)

    def test_trocar_pin_recifra_os_backups(self):
        cofre = self.criado()
        cofre.salvar(b"versao 2")
        backup = cofre.backups()[0]
        self.assertEqual(self.abrir_arquivo(backup, PIN), DADOS)
        cofre.trocar_pin(PIN, self.NOVO)
        self.assertEqual(self.abrir_arquivo(backup, self.NOVO), DADOS)
        with self.assertRaises(PinErrado):
            self.abrir_arquivo(backup, PIN)
        # trocar o PIN não cria backup novo com o PIN antigo
        self.assertEqual(len(cofre.backups()), 1)

    def test_trocar_pin_com_cofre_fechado_mantem_fechado(self):
        cofre = self.criado()
        cofre.fechar()
        cofre.trocar_pin(PIN, self.NOVO)
        self.assertFalse(cofre.aberto)
        self.assertEqual(cofre.abrir(self.NOVO), DADOS)


class TestVerificadorPinIA(BaseCofre):
    """Seção 17.5: o PIN da IA vira só um verificador scrypt; o PIN principal é conferido na chave."""

    PIN_IA = "cafe com canela"

    def test_criar_e_conferir(self):
        from entrenos.cofre import VERIFICADOR_N, conferir_verificador, criar_verificador
        self.assertEqual(VERIFICADOR_N, 2 ** 15)
        verificador = criar_verificador(self.PIN_IA)
        self.assertEqual(set(verificador), {"sal", "n", "r", "p", "hash"})
        self.assertEqual((verificador["n"], verificador["r"], verificador["p"]), (2 ** 15, 8, 1))
        self.assertNotIn("canela", repr(verificador))
        self.assertTrue(conferir_verificador(self.PIN_IA, verificador))
        self.assertFalse(conferir_verificador("cafe com canelas", verificador))
        self.assertFalse(conferir_verificador(PIN, verificador))
        outro = criar_verificador(self.PIN_IA)
        self.assertNotEqual(outro["sal"], verificador["sal"])          # sal novo a cada vez
        self.assertNotEqual(outro["hash"], verificador["hash"])

    def test_acento_em_qualquer_forma(self):
        from entrenos.cofre import conferir_verificador, criar_verificador
        with mock.patch("entrenos.cofre.VERIFICADOR_N", 2 ** 10):
            verificador = criar_verificador(unicodedata.normalize("NFC", "pão de açúcar"))
        self.assertTrue(conferir_verificador(unicodedata.normalize("NFD", "pão de açúcar"), verificador))

    def test_formato_estranho_nunca_confere(self):
        from entrenos.cofre import conferir_verificador, criar_verificador, verificador_valido
        with mock.patch("entrenos.cofre.VERIFICADOR_N", 2 ** 10):
            bom = criar_verificador(self.PIN_IA)
        self.assertTrue(verificador_valido(bom))
        for ruim in (None, "texto", {}, bom | {"n": 1000}, bom | {"n": 2 ** 25}, bom | {"r": True},
                     bom | {"hash": "!!"}, bom | {"hash": bom["sal"]}, bom | {"extra": 1},
                     {k: v for k, v in bom.items() if k != "sal"}):
            with self.subTest(ruim=ruim):
                self.assertFalse(verificador_valido(ruim))
                self.assertFalse(conferir_verificador(self.PIN_IA, ruim))
        for pin in (None, "", 123456789012):
            self.assertFalse(conferir_verificador(pin, bom))

    def test_pin_facil_recusado(self):
        from entrenos.cofre import criar_verificador
        for pin in ("123456", "aaaaaa", "curto"):
            with self.subTest(pin=pin), self.assertRaises(ValueError):
                criar_verificador(pin)

    def test_confere_pin_principal(self):
        cofre = self.criado()
        self.assertTrue(cofre.confere_pin(PIN))
        self.assertFalse(cofre.confere_pin(self.PIN_IA))
        self.assertFalse(cofre.confere_pin(None))
        self.assertFalse(cofre.confere_pin(""))
        self.assertTrue(cofre.aberto)                 # conferir não fecha nem troca nada
        cofre.salvar(b"depois")
        self.assertEqual(self.novo_cofre().abrir(PIN), b"depois")
        cofre.fechar()
        with self.assertRaises(CofreFechado):
            cofre.confere_pin(PIN)

    def test_confere_pin_depois_de_trocar(self):
        cofre = self.criado()
        cofre.trocar_pin(PIN, "frase nova e secreta")
        self.assertFalse(cofre.confere_pin(PIN))
        self.assertTrue(cofre.confere_pin("frase nova e secreta"))


if __name__ == "__main__":
    unittest.main()
