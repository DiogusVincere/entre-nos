"""Testes da IA sob demanda pela API (DESIGN.md, seção 17).

PIN da IA e a área de ajustes da IA, configuração (a chave nunca volta), registros marcados
"não enviar para a IA" e os botões ✨ (analisar, busca, sugestão) com principal e reserva.

Nada vai para a internet: as IAs daqui são servidores FALSOS em 127.0.0.1, no formato comum
(POST /v1/chat/completions e GET /v1/models). Pasta de dados temporária (ENTRENOS_DADOS) e data
fixa 30/09/2026. O scrypt usa n pequeno só para os testes andarem rápido; o real é testado em
test_cofre.py.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import time
import unittest
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from entrenos import cofre, config, servidor
from testes.test_servidor import PIN, TEXTO, ServidorDeTeste, _esperar

PIN_IA = "cafe com canela e mel"
PIN_IA_NOVO = "chuva fina na varanda"
# GET /api/ia/estado sem PIN da IA e sem IA (a correção final da seção 17 trouxe utilizavel,
# espera_maxima e segundos_restantes).
ESTADO_SEM_IA = {"pin_definido": False, "liberada": False, "configurada": False, "utilizavel": False,
                 "reserva_configurada": False, "sai_do_computador": False, "espera_maxima": None,
                 "segundos_restantes": None}
CHAVE = "sk-proj-SEGREDO0123456789wxyz"
CHAVE_FALSA = "chave-falsa-7f3a"


def _motor_novo() -> bool:
    """O motor com a API da seção 17.3 (escrito em paralelo) já está aí?"""
    try:
        from entrenos import motor
    except Exception:  # noqa: BLE001
        return False
    return all(hasattr(motor, nome) for nome in ("obter_cadeia_ia", "CadeiaIA", "listar_modelos", "testar_ia",
                                                  "comparar_propostas", "ia_envia_para_fora"))


def _provedores() -> bool:
    try:
        from entrenos import provedores  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return True


PRECISA_MOTOR = unittest.skipUnless(_motor_novo(), "motor.py ainda sem a API da seção 17.3")
PRECISA_PROVEDORES = unittest.skipUnless(_provedores(), "provedores.py (seção 17.2) ainda não existe")

_remendos: list = []
_env_antigo: str | None = None
_pasta_env: str | None = None


def setUpModule() -> None:
    global _env_antigo, _pasta_env
    _env_antigo = os.environ.get("ENTRENOS_DADOS")
    _pasta_env = tempfile.mkdtemp(prefix="entrenos-teste-ia-env-")
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


# ---------------------------------------------------------------- IA falsa

class IAFalsa:
    """Servidor de IA de mentira em 127.0.0.1, no formato comum. Anota cada pedido.

    `resposta` é o JSON que o "modelo" escreve: pode trazer itens (análise), texto/citacoes
    (busca) e sugestoes ao mesmo tempo, e o motor usa a parte que pediu.
    """

    def __init__(self, status: int = 200, prefixo: str = "/v1"):
        self.status = status
        self.prefixo = prefixo           # só responde debaixo deste caminho (o resto é 404)
        self.resposta: dict = {}
        self.modelos = ["modelo-b", "modelo-a", "text-embedding-3-small"]
        self.pedidos: list[dict] = []
        dona = self

        class Manipulador(BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:
                pass

            def _enviar(self, status: int, dados: dict) -> None:
                bruto = json.dumps(dados, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(bruto)))
                self.end_headers()
                self.wfile.write(bruto)

            def _anotar(self, corpo: str) -> None:
                dona.pedidos.append({"metodo": self.command, "caminho": self.path, "corpo": corpo,
                                     "autorizacao": self.headers.get("Authorization")})

            def do_GET(self) -> None:
                self._anotar("")
                if dona.status != 200:
                    self._enviar(dona.status, {"error": {"message": "fora do ar"}})
                elif self.path != dona.prefixo + "/models":
                    self._enviar(404, {"error": {"message": "não existe"}})
                elif self.path.endswith("/models"):
                    self._enviar(200, {"object": "list", "data": [{"id": m, "object": "model"} for m in dona.modelos]})
                else:
                    self._enviar(404, {"error": {"message": "não existe"}})

            def do_POST(self) -> None:
                corpo = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode("utf-8")
                self._anotar(corpo)
                if dona.status != 200:
                    self._enviar(dona.status, {"error": {"message": "fora do ar"}})
                elif self.path != dona.prefixo + "/chat/completions":
                    self._enviar(404, {"error": {"message": "não existe"}})
                else:
                    conteudo = json.dumps(dona.resposta, ensure_ascii=False)
                    self._enviar(200, {"id": "falsa", "object": "chat.completion", "choices": [
                        {"index": 0, "message": {"role": "assistant", "content": conteudo}, "finish_reason": "stop"}]})

        self._http = ThreadingHTTPServer(("127.0.0.1", 0), Manipulador)
        self._http.daemon_threads = True
        self.porta = self._http.server_address[1]
        self.url = f"http://127.0.0.1:{self.porta}{prefixo}"
        self._thread = threading.Thread(target=self._http.serve_forever, kwargs={"poll_interval": 0.05},
                                        daemon=True)
        self._thread.start()

    def fechar(self) -> None:
        self._http.shutdown()
        self._http.server_close()
        self._thread.join(5)

    def recebido(self) -> str:
        return "\n".join(p["corpo"] for p in self.pedidos)

    def conversas(self) -> list[dict]:
        return [p for p in self.pedidos if p["caminho"].endswith("/chat/completions")]


def resposta_da_ia(registro_id: int | None = None) -> dict:
    """O que a IA falsa "escreve": itens para TEXTO, resposta e sugestão citando `registro_id`."""
    citados = [registro_id] if registro_id is not None else []
    return {
        "itens": [
            {"tipo": "evento", "resumo": "Mãe: cirurgia em novembro",
             "trecho": "A mãe dela, Sandra, vai fazer uma cirurgia em novembro.", "certeza": "observado",
             "categorias": ["Família", "Saúde"], "alvo": None, "polaridade": None, "pessoa": "mãe",
             "data_expressao": "em novembro"},
            {"tipo": "preferencia", "resumo": "Adora tulipas", "trecho": "Ela disse que adora tulipas.",
             "certeza": "confirmado", "categorias": ["Preferências", "Coisas que deixam feliz"], "alvo": "tulipas",
             "polaridade": 1, "pessoa": None, "data_expressao": None},
        ],
        "texto": "Ela disse que adora tulipas.",
        "citacoes": citados,
        "sugestoes": [{"texto": "Talvez levar tulipas amarelas num dia comum, sem motivo especial.",
                       "porque": "Ela disse que adora tulipas.", "registro_ids": citados}],
    }


# ---------------------------------------------------------------- base

class BaseIA(unittest.TestCase):
    opcoes: dict = {}

    def setUp(self):
        self.s = self.novo_servidor(**self.opcoes)

    def novo_servidor(self, **opcoes) -> ServidorDeTeste:
        s = ServidorDeTeste(**opcoes)
        self.addCleanup(s.fechar)
        s.configurar(nome_dela="Ana")
        return s

    def ia_falsa(self, status: int = 200, prefixo: str = "/v1") -> IAFalsa:
        falsa = IAFalsa(status, prefixo)
        self.addCleanup(falsa.fechar)
        return falsa

    @staticmethod
    def ia(s: ServidorDeTeste, metodo: str, rota: str, corpo=None, **kw):
        return s.pedir(metodo, "/api/ia/" + rota, corpo, **kw)

    def criar_pin_ia(self, s: ServidorDeTeste | None = None, pin: str = PIN_IA) -> None:
        r = self.ia(s or self.s, "POST", "pin", {"novo": pin})
        self.assertEqual(r.status, 200, r.dados)
        self.assertEqual(r.dados, {"liberada": True})

    def estado_ia(self, s: ServidorDeTeste | None = None) -> dict:
        r = self.ia(s or self.s, "GET", "estado")
        self.assertEqual(r.status, 200, r.dados)
        return r.dados

    def configurar_ia(self, qual: str, url: str, modelo: str = "modelo-falso", chave: str | None = CHAVE_FALSA,
                      s: ServidorDeTeste | None = None) -> dict:
        corpo = {"provedor": "outro", "url": url, "modelo": modelo}
        if chave is not None:
            corpo["chave"] = chave
        r = self.ia(s or self.s, "PUT", f"config/{qual}", corpo)
        self.assertEqual(r.status, 200, r.dados)
        return r.dados

    def registrar(self, texto: str, data: str = "2026-09-29", privado: bool = False) -> int:
        """Analisa pelas regras e grava, como a tela faz (com a caixinha "não enviar para a IA")."""
        p = self.s.pedir("POST", "/api/analisar", {"texto": texto, "data_registro": data})
        self.assertEqual(p.status, 200, p.dados)
        corpo = {"texto": texto, "data_registro": data, "itens": p.dados["itens"], "pessoas": p.dados["pessoas"],
                 "relacoes": p.dados["relacoes"], "privado": privado}
        r = self.s.pedir("POST", "/api/registros", corpo)
        self.assertEqual(r.status, 200, r.dados)
        return r.dados["id"]


# ---------------------------------------------------------------- PIN da IA e a área

class TestPinDaIA(BaseIA):
    def test_ciclo_inteiro(self):
        s = self.s
        self.assertEqual(self.estado_ia(), ESTADO_SEM_IA)
        r = self.ia(s, "PATCH", "config", {"tempo_limite": 60})
        self.assertEqual((r.status, r.dados.get("pin_ia")), (403, True))
        self.assertEqual(self.ia(s, "POST", "entrar", {"pin_ia": PIN_IA}).status, 409)     # ainda não existe

        # Criar: forte e diferente do PIN principal.
        r = self.ia(s, "POST", "pin", {"novo": PIN})
        self.assertEqual(r.status, 400)
        self.assertIn("diferente do PIN principal", r.dados["erro"])
        for fraco in ("123456", "aaaaaa", "curto", None):
            with self.subTest(fraco=fraco):
                r = self.ia(s, "POST", "pin", {"novo": fraco})
                self.assertEqual(r.status, 400)
                self.assertTrue(r.dados["erro"].startswith(("O PIN da IA", "Um PIN", "Esse PIN")), r.dados)
        self.assertFalse(self.estado_ia()["pin_definido"])
        self.criar_pin_ia()
        estado = self.estado_ia()
        self.assertEqual((estado["pin_definido"], estado["liberada"]), (True, True))
        verificador = s.app._banco.config()["ia_pin"]
        self.assertEqual(set(verificador), {"sal", "n", "r", "p", "hash"})
        self.assertNotIn("canela", json.dumps(verificador))
        self.assertEqual(self.ia(s, "POST", "pin", {"novo": "outro pin da ia"}).status, 409)     # só uma vez

        # Área liberada.
        r = self.ia(s, "PATCH", "config", {"tempo_limite": 60})
        self.assertEqual((r.status, r.dados), (200, {"tempo_limite": 60}))
        self.assertEqual(self.ia(s, "PATCH", "config", {"tempo_limite": 2}).status, 400)
        self.assertEqual(self.ia(s, "PATCH", "config", {"tempo_limite": 60, "outra": 1}).status, 400)
        self.assertEqual(self.ia(s, "PATCH", "config", {"tempo_limite": None}).dados, {"tempo_limite": 120})

        # Fechar e abrir de novo.
        self.assertEqual(self.ia(s, "POST", "sair").status, 200)
        self.assertFalse(self.estado_ia()["liberada"])
        self.assertEqual(self.ia(s, "PATCH", "config", {"tempo_limite": 60}).status, 403)
        r = self.ia(s, "POST", "entrar", {"pin_ia": "nao e esse pin"})
        self.assertEqual((r.status, r.dados["erro"]), (401, "PIN da IA incorreto."))
        self.assertEqual(self.ia(s, "POST", "entrar", {"pin_ia": PIN}).status, 401)   # o principal não abre
        self.assertEqual((s.app._falhas_pin_ia, s.app._falhas_pin), (2, 0))           # contador próprio
        self.assertEqual(self.ia(s, "POST", "entrar", {"pin_ia": ""}).status, 400)
        self.assertEqual(self.ia(s, "POST", "entrar", {"pin_ia": PIN_IA}).status, 200)
        self.assertEqual(s.app._falhas_pin_ia, 0)
        self.assertTrue(self.estado_ia()["liberada"])

        # Trocar o PIN da IA.
        r = self.ia(s, "POST", "pin/trocar", {"atual": "nao e esse pin", "novo": PIN_IA_NOVO})
        self.assertEqual((r.status, r.dados["erro"]), (401, "O PIN da IA atual não confere."))
        r = self.ia(s, "POST", "pin/trocar", {"atual": PIN_IA, "novo": PIN})
        self.assertEqual(r.status, 400)
        self.assertIn("diferente do PIN principal", r.dados["erro"])
        self.assertEqual(self.ia(s, "POST", "pin/trocar", {"atual": PIN_IA, "novo": "12345"}).status, 400)
        self.assertEqual(self.ia(s, "POST", "pin/trocar", {"atual": PIN_IA, "novo": PIN_IA_NOVO}).status, 200)
        self.ia(s, "POST", "sair")
        self.assertEqual(self.ia(s, "POST", "pin/trocar", {"atual": PIN_IA_NOVO, "novo": PIN_IA}).status, 403)
        self.assertEqual(self.ia(s, "POST", "entrar", {"pin_ia": PIN_IA}).status, 401)
        self.assertEqual(self.ia(s, "POST", "entrar", {"pin_ia": PIN_IA_NOVO}).status, 200)

        # A área fecha no bloqueio do app e o PIN da IA continua valendo depois.
        self.assertEqual(s.pedir("POST", "/api/bloquear").status, 200)
        self.assertEqual(self.ia(s, "GET", "estado").status, 423)
        self.assertEqual(s.desbloquear().status, 200)
        estado = self.estado_ia()
        self.assertEqual((estado["pin_definido"], estado["liberada"]), (True, False))
        self.assertEqual(self.ia(s, "POST", "entrar", {"pin_ia": PIN_IA_NOVO}).status, 200)

    def test_pin_da_ia_errado_espera_cada_vez_mais(self):
        s = self.novo_servidor(atraso_pin=0.2, atraso_pin_maximo=0.4)
        self.criar_pin_ia(s)
        self.ia(s, "POST", "sair")
        tempos = []
        for _ in range(3):
            inicio = time.monotonic()
            self.assertEqual(self.ia(s, "POST", "entrar", {"pin_ia": "pin errado demais"}).status, 401)
            tempos.append(time.monotonic() - inicio)
        self.assertGreaterEqual(tempos[0], 0.19)
        self.assertGreaterEqual(tempos[1], 0.39)
        self.assertGreaterEqual(tempos[2], 0.39)
        self.assertLess(tempos[2], 0.75)                  # sem o teto seriam 0,8 s
        self.assertEqual((s.app._falhas_pin_ia, s.app._falhas_pin), (3, 0))
        # Errar o PIN principal não mexe no contador da IA (e vice-versa).
        s.pedir("POST", "/api/bloquear")
        self.assertEqual(s.desbloquear("pin errado demais").status, 401)
        self.assertEqual((s.app._falhas_pin_ia, s.app._falhas_pin), (3, 1))
        self.assertEqual(s.desbloquear().status, 200)
        self.assertEqual(self.ia(s, "POST", "entrar", {"pin_ia": PIN_IA}).status, 200)
        self.assertEqual(s.app._falhas_pin_ia, 0)
        inicio = time.monotonic()
        self.assertEqual(self.ia(s, "POST", "entrar", {"pin_ia": "pin errado demais"}).status, 401)
        self.assertLess(time.monotonic() - inicio, 0.36)  # voltou para o primeiro degrau

    def test_tentativas_em_paralelo_nao_encurtam_a_espera(self):
        s = self.novo_servidor(atraso_pin=0.3, atraso_pin_maximo=5)
        self.criar_pin_ia(s)
        self.ia(s, "POST", "sair")
        status: list[int] = []
        threads = [threading.Thread(target=lambda: status.append(
            self.ia(s, "POST", "entrar", {"pin_ia": "errado errado"}).status)) for _ in range(2)]
        inicio = time.monotonic()
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
        self.assertEqual(status, [401, 401])
        self.assertGreaterEqual(time.monotonic() - inicio, 0.85)     # 0,3 + 0,6, uma depois da outra

    def test_comparar_com_o_pin_principal_tambem_espera(self):
        # Quem achar o app aberto não descobre o PIN principal tentando "PIN da IA = palpite".
        s = self.novo_servidor(atraso_pin=0.25, atraso_pin_maximo=0.5)
        tempos = []
        for _ in range(servidor.COMPARACOES_SEM_ESPERA + 1):
            inicio = time.monotonic()
            self.assertEqual(self.ia(s, "POST", "pin", {"novo": PIN}).status, 400)
            tempos.append(time.monotonic() - inicio)
        self.assertTrue(all(t < 0.2 for t in tempos[:-1]), tempos)
        self.assertGreaterEqual(tempos[-1], 0.24)
        self.assertEqual(s.desbloquear().status, 200)          # acertar o PIN principal zera a conta
        inicio = time.monotonic()
        self.assertEqual(self.ia(s, "POST", "pin", {"novo": PIN}).status, 400)
        self.assertLess(time.monotonic() - inicio, 0.2)

    def test_area_fecha_sozinha(self):
        s = self.novo_servidor(minutos_area_ia=0.01)                  # 0,6 s
        self.criar_pin_ia(s)
        self.assertTrue(self.estado_ia(s)["liberada"])
        self.assertEqual(self.ia(s, "PATCH", "config", {"tempo_limite": 30}).status, 200)
        self.assertTrue(_esperar(lambda: not self.estado_ia(s)["liberada"], 5))
        r = self.ia(s, "PATCH", "config", {"tempo_limite": 30})
        self.assertEqual((r.status, r.dados["pin_ia"]), (403, True))
        self.assertTrue(s.app.desbloqueado)                        # só a área fechou, o app não
        self.assertEqual(self.ia(s, "POST", "entrar", {"pin_ia": PIN_IA}).status, 200)
        self.assertTrue(self.estado_ia(s)["liberada"])

    def test_area_fecha_no_bloqueio_automatico(self):
        s = self.novo_servidor(minutos_bloqueio=0.02, intervalo_verificacao=0.05)     # 1,2 s
        self.criar_pin_ia(s)
        self.assertTrue(_esperar(lambda: not s.app.desbloqueado, 5))
        self.assertFalse(s.app.area_ia_aberta())
        self.assertEqual(s.desbloquear().status, 200)
        self.assertFalse(self.estado_ia(s)["liberada"])

    def test_rotas_da_area_pedem_o_pin_da_ia(self):
        self.criar_pin_ia()
        self.ia(self.s, "POST", "sair")
        rotas = [("GET", "config", None), ("PATCH", "config", {"tempo_limite": 30}),
                 ("PUT", "config/principal", {"provedor": "ollama", "modelo": "m"}),
                 ("PUT", "config/reserva", {"provedor": "ollama", "modelo": "m"}),
                 ("DELETE", "config/principal", None), ("DELETE", "config/reserva", None),
                 ("POST", "modelos", {"provedor": "ollama"}), ("POST", "testar", {"qual": "principal"}),
                 ("POST", "pin/trocar", {"atual": PIN_IA, "novo": PIN_IA_NOVO})]
        for metodo, rota, corpo in rotas:
            with self.subTest(rota=f"{metodo} {rota}"):
                r = self.ia(self.s, metodo, rota, corpo)
                self.assertEqual(r.status, 403, r.dados)
                self.assertEqual(r.dados, {"erro": servidor.AREA_IA_FECHADA, "pin_ia": True})
        self.assertIsNone(self.s.app._banco.config()["ia_principal"])

    @PRECISA_PROVEDORES
    def test_esqueci_o_pin_da_ia(self):
        s = self.s
        self.criar_pin_ia()
        self.ia(s, "PUT", "config/principal", {"provedor": "ollama", "modelo": "qwen3:4b"})
        self.ia(s, "PATCH", "config", {"tempo_limite": 45})
        rid = self.registrar("Ela adora tulipas.")
        self.ia(s, "POST", "sair")
        self.assertEqual(self.ia(s, "POST", "redefinir", {"pin": ""}).status, 400)
        self.assertEqual(self.ia(s, "POST", "redefinir", {"pin": "nao e o principal"}).status, 401)
        self.assertEqual(self.ia(s, "POST", "redefinir", {"pin": PIN_IA}).status, 401)       # pede o principal
        self.assertEqual(s.app._falhas_pin, 2)
        self.assertTrue(self.estado_ia()["configurada"])
        self.assertEqual(self.ia(s, "POST", "redefinir", {"pin": PIN}).status, 200)
        self.assertEqual(self.estado_ia(), ESTADO_SEM_IA)
        ajustes = s.app._banco.config()
        self.assertEqual((ajustes["ia_pin"], ajustes["ia_principal"], ajustes["ia_tempo_limite"]), (None, None, 45))
        self.assertEqual(s.pedir("GET", f"/api/registros/{rid}").status, 200)                 # o diário fica
        self.criar_pin_ia(pin=PIN_IA_NOVO)                                                    # e dá para começar de novo


# ---------------------------------------------------------------- configuração

@PRECISA_PROVEDORES
class TestConfigDaIA(BaseIA):
    def setUp(self):
        super().setUp()
        self.criar_pin_ia()

    def test_salvar_ver_e_remover(self):
        s = self.s
        r = self.ia(s, "GET", "config")
        self.assertEqual(r.status, 200, r.dados)
        self.assertEqual((r.dados["principal"], r.dados["reserva"], r.dados["tempo_limite"]), (None, None, 120))
        self.assertIn("groq", [p["id"] for p in r.dados["provedores"]])
        self.assertIn("sk-ant-", r.dados["nao_suportados"])

        r = self.ia(s, "PUT", "config/principal", {"provedor": "openai", "modelo": " gpt-4o-mini ", "chave": CHAVE})
        self.assertEqual(r.status, 200, r.dados)
        self.assertEqual(r.dados, {"provedor": "openai", "nome_provedor": "OpenAI", "url": "https://api.openai.com/v1",
                                   "modelo": "gpt-4o-mini", "tipo": "openai", "tem_chave": True, "chave_final": "wxyz",
                                   "local": False, "aviso": None})
        self.assertNotIn(CHAVE.encode(), r.bruto)
        self.assertEqual(self.ia(s, "GET", "config").dados["principal"], r.dados)

        r = self.ia(s, "PUT", "config/reserva", {"provedor": "ollama", "modelo": "qwen3:4b"})
        self.assertEqual((r.dados["url"], r.dados["tipo"], r.dados["local"], r.dados["tem_chave"],
                          r.dados["chave_final"]), ("http://127.0.0.1:11434", "ollama", True, False, None))
        estado = self.estado_ia()
        self.assertEqual((estado["configurada"], estado["reserva_configurada"]), (True, True))
        self.assertTrue(s.pedir("GET", "/api/estado", token=None).dados["ia"]["configurada"])

        self.assertEqual(self.ia(s, "DELETE", "config/reserva").status, 200)
        self.assertFalse(self.estado_ia()["reserva_configurada"])
        self.assertEqual(self.ia(s, "DELETE", "config/principal").status, 200)
        self.assertFalse(self.estado_ia()["configurada"])
        self.assertEqual(s.pedir("GET", "/api/estado", token=None).dados["ia"],
                         {"configurada": False, "utilizavel": False, "sai_do_computador": False, "espera_maxima": None})

    def test_a_chave_nunca_volta(self):
        s = self.s
        self.assertEqual(self.ia(s, "PUT", "config/principal",
                                 {"provedor": "openai", "modelo": "gpt-4o-mini", "chave": CHAVE}).status, 200)
        s.registrar()
        segredos = [CHAVE.encode(), s.app._banco.config()["ia_pin"]["hash"].encode(), b"SEGREDO"]
        for rota in ("/api/config", "/api/exportar", "/api/historico", "/api/estado", "/api/ia/estado",
                     "/api/ia/config", "/api/registros", "/api/hoje"):
            with self.subTest(rota=rota):
                r = s.pedir("GET", rota)
                self.assertEqual(r.status, 200, r.dados)
                for segredo in segredos:
                    self.assertNotIn(segredo, r.bruto)
        # Nem qual IA: isso é da área do PIN da IA.
        self.assertFalse([k for k in s.pedir("GET", "/api/config").dados if k.startswith(("ia_", "motor"))])
        self.assertFalse([k for k in s.pedir("GET", "/api/exportar").dados["config"] if k.startswith(("ia_", "motor"))])
        # Erro com a chave errada não repete a chave.
        r = self.ia(s, "PUT", "config/principal", {"provedor": "openai", "modelo": "m", "chave": "sk-SEGREDO com espaço"})
        self.assertEqual(r.status, 400)
        self.assertNotIn(b"SEGREDO", r.bruto)
        # E no disco ela está cifrada.
        self.assertNotIn(CHAVE.encode(), (Path(s.pasta) / config.ARQUIVO_COFRE).read_bytes())

    def test_trocar_de_endereco_pede_a_chave_de_novo(self):
        s = self.s
        base = "http://127.0.0.1:5001"
        dados = self.configurar_ia("principal", base + "/v1", chave="chave-de-teste-1234")
        self.assertEqual((dados["url"], dados["chave_final"]), (base + "/v1", "1234"))
        for url in ("http://127.0.0.1:5002/v1", "http://localhost:5001/v1", "https://127.0.0.1:5001/v1",
                    "http://192.168.0.10:5001/v1"):
            with self.subTest(url=url):
                r = self.ia(s, "PUT", "config/principal", {"provedor": "outro", "url": url, "modelo": "m"})
                self.assertEqual((r.status, r.dados["erro"]), (400, servidor.CHAVE_DE_NOVO))
                r = self.ia(s, "POST", "modelos", {"provedor": "outro", "url": url, "qual": "principal"})
                self.assertEqual((r.status, r.dados["erro"]), (400, servidor.CHAVE_DE_NOVO))
        self.assertEqual(s.app._banco.config()["ia_principal"]["url"], base + "/v1")
        # Mesmo endereço (escrito de outro jeito): a chave fica.
        dados = self.configurar_ia("principal", base + "/", modelo="outro-modelo", chave=None)
        self.assertEqual((dados["url"], dados["modelo"], dados["chave_final"]), (base + "/v1", "outro-modelo", "1234"))
        # Endereço novo com a chave digitada de novo: pode.
        dados = self.configurar_ia("principal", "http://127.0.0.1:5002", chave="chave-nova-5678")
        self.assertEqual((dados["url"], dados["chave_final"]), ("http://127.0.0.1:5002/v1", "5678"))
        # "" remove a chave; aí trocar de endereço não carrega nada.
        self.assertFalse(self.configurar_ia("principal", "http://127.0.0.1:5002", chave="")["tem_chave"])
        self.assertFalse(self.configurar_ia("principal", "http://127.0.0.1:5003", chave=None)["tem_chave"])
        # Trocar de serviço apaga a chave antiga.
        self.configurar_ia("principal", "http://127.0.0.1:1234/v1", chave="chave-de-teste-9999")
        r = self.ia(s, "PUT", "config/principal", {"provedor": "lmstudio", "modelo": "gemma"})
        self.assertEqual((r.status, r.dados["tem_chave"], r.dados["url"]), (200, False, "http://127.0.0.1:1234/v1"))
        self.assertIsNone(s.app._banco.config()["ia_principal"]["chave"])

    def test_pedidos_recusados(self):
        s = self.s
        casos = [
            ({"provedor": "outro", "url": "https://ia.exemplo.com", "modelo": "m", "chave": "sk-ant-api03-abcdef"},
             "Claude"),
            ({"provedor": "outro", "url": "http://ia.exemplo.com", "modelo": "m", "chave": "chave-qualquer-123"},
             "https://"),
            ({"provedor": "groq", "modelo": "llama-3.3-70b"}, "precisa de uma chave"),
            ({"provedor": "inventado", "modelo": "m"}, "serviço"),
            ({"provedor": "outro", "modelo": "m"}, "endereço"),
            ({"provedor": "ollama"}, "modelo"),
            ({"provedor": "ollama", "modelo": "  "}, "modelo"),
            ({"provedor": "ollama", "modelo": "m", "tipo": "ollama"}, "tipo"),
            ({"provedor": "ollama", "modelo": "m", "chave": 123}, "chave"),
            ({"provedor": "outro", "url": "ftp://ia.exemplo.com", "modelo": "m"}, "http"),
        ]
        for corpo, trecho in casos:
            with self.subTest(corpo=corpo):
                r = self.ia(s, "PUT", "config/principal", corpo)
                self.assertEqual(r.status, 400, r.dados)
                self.assertIn(trecho, r.dados["erro"])
        self.assertIsNone(s.app._banco.config()["ia_principal"])
        # Usuário e senha colados na URL não ficam guardados.
        dados = self.configurar_ia("reserva", "https://eu:senha@ia.exemplo.com", chave=None)
        self.assertEqual(dados["url"], "https://ia.exemplo.com/v1")
        self.assertNotIn("senha", json.dumps(s.app._banco.config()["ia_reserva"]))
        # A rede de casa pode receber a chave sem https.
        self.assertTrue(self.configurar_ia("principal", "http://192.168.0.20:8080", chave="chave-local-123")["tem_chave"])

    @PRECISA_MOTOR
    def test_sai_do_computador(self):
        s = self.s
        self.ia(s, "PUT", "config/principal", {"provedor": "ollama", "modelo": "qwen3:4b"})
        self.assertFalse(self.estado_ia()["sai_do_computador"])
        self.assertEqual(s.pedir("GET", "/api/estado", token=None).dados["ia"],
                         {"configurada": True, "utilizavel": True, "sai_do_computador": False, "espera_maxima": 120})
        self.ia(s, "PUT", "config/reserva", {"provedor": "groq", "modelo": "llama-3.3-70b",
                                             "chave": "gsk_teste0123456789"})
        self.assertTrue(self.estado_ia()["sai_do_computador"])
        self.assertTrue(s.pedir("GET", "/api/estado", token=None).dados["ia"]["sai_do_computador"])
        self.ia(s, "DELETE", "config/reserva")
        self.ia(s, "PUT", "config/principal", {"provedor": "ollama", "modelo": "gpt-oss:120b-cloud"})
        self.assertTrue(self.estado_ia()["sai_do_computador"])          # o Ollama repassa para a nuvem

    @PRECISA_MOTOR
    def test_buscar_modelos_e_testar(self):
        s = self.s
        falsa = self.ia_falsa()
        falsa.resposta = resposta_da_ia()
        self.registrar("Ela me contou do Projeto Mandragorix.")
        self.configurar_ia("principal", falsa.url, modelo="modelo-a")
        r = self.ia(s, "POST", "modelos", {"provedor": "outro", "url": falsa.url, "qual": "principal"})
        self.assertEqual((r.status, r.dados), (200, {"modelos": ["modelo-a", "modelo-b"]}))
        self.assertEqual(falsa.pedidos[-1]["autorizacao"], f"Bearer {CHAVE_FALSA}")        # a chave salva
        r = self.ia(s, "POST", "modelos", {"provedor": "outro", "url": falsa.url})
        self.assertEqual(r.status, 200)
        self.assertIsNone(falsa.pedidos[-1]["autorizacao"])                              # sem qual: sem chave
        r = self.ia(s, "POST", "modelos", {"provedor": "outro", "url": falsa.url, "chave": "digitada-agora"})
        self.assertEqual(falsa.pedidos[-1]["autorizacao"], "Bearer digitada-agora")
        self.assertEqual(self.ia(s, "POST", "modelos", {"provedor": "outro", "url": falsa.url, "qual": "x"}).status,
                         400)

        r = self.ia(s, "POST", "testar", {"qual": "principal"})
        self.assertEqual(r.status, 200, r.dados)
        self.assertEqual(set(r.dados), {"ok", "segundos", "formato", "erro"})
        self.assertTrue(falsa.conversas())
        self.assertNotIn("Mandragorix", falsa.recebido())          # o teste usa frase inventada
        self.assertEqual(self.ia(s, "POST", "testar", {"qual": "reserva"}).status, 409)
        self.assertEqual(self.ia(s, "POST", "testar", {"qual": "tudo"}).status, 400)

        falsa.status = 401
        r = self.ia(s, "POST", "modelos", {"provedor": "outro", "url": falsa.url, "qual": "principal"})
        self.assertEqual(r.status, 502)
        self.assertIn("chave", r.dados["erro"])
        self.assertNotIn(CHAVE_FALSA.encode(), r.bruto)
        self.assertNotIn(str(falsa.porta).encode(), r.bruto)          # nem o endereço
        r = self.ia(s, "POST", "testar", {"qual": "principal"})
        self.assertEqual((r.status, r.dados["ok"]), (200, False))
        self.assertIn("chave", r.dados["erro"])


# ---------------------------------------------------------------- registros privados

class TestRegistroPrivado(BaseIA):
    def test_marcar_e_desmarcar(self):
        s = self.s
        rid = self.registrar("Ela me contou em segredo do Projeto Mandragorix.", privado=True)
        outro = self.registrar("Ela adora tulipas.")
        self.assertTrue(s.pedir("GET", f"/api/registros/{rid}").dados["privado"])
        self.assertEqual({r["id"]: r["privado"] for r in s.pedir("GET", "/api/registros").dados},
                         {rid: True, outro: False})
        self.assertEqual(s.pedir("PATCH", f"/api/registros/{rid}", {"privado": False}).status, 200)
        self.assertFalse(s.pedir("GET", f"/api/registros/{rid}").dados["privado"])
        self.assertEqual(s.pedir("PATCH", f"/api/registros/{outro}", {"privado": True}).status, 200)
        self.assertEqual(s.app._banco.registros_privados(), {outro})
        for corpo in ({"privado": "sim"}, {"privado": 1}, {}, {"texto": "outro texto"}, {"privado": True, "id": 3}):
            with self.subTest(corpo=corpo):
                self.assertEqual(s.pedir("PATCH", f"/api/registros/{rid}", corpo).status, 400)
        self.assertEqual(s.pedir("PATCH", "/api/registros/9999", {"privado": True}).status, 404)
        r = s.pedir("POST", "/api/registros", {"texto": "Outro.", "data_registro": "2026-09-30", "privado": "sim"})
        self.assertEqual(r.status, 400)
        # As regras continuam achando o registro privado: ele só não vai para a IA.
        busca = s.pedir("GET", "/api/busca?q=tulipas").dados
        self.assertIn(outro, [e["registro_id"] for e in busca["encontrados"]])
        # Some com o PATCH? Não: o histórico diz só o que aconteceu, sem o texto.
        historico = json.dumps(s.pedir("GET", "/api/historico").dados, ensure_ascii=False)
        self.assertIn(f"registro #{outro}: não enviar para a IA", historico)
        self.assertNotIn("tulipas", historico)

    def test_sem_ia_os_botoes_respondem_409(self):
        s = self.s
        for rota, corpo in (("analisar", {"texto": TEXTO}), ("busca", {"q": "tulipas"}), ("sugestao", {})):
            with self.subTest(rota=rota):
                r = self.ia(s, "POST", rota, corpo)
                self.assertEqual((r.status, r.dados["erro"]), (409, servidor.SEM_IA))
        r = self.ia(s, "POST", "analisar", {"texto": TEXTO, "privado": True})
        self.assertEqual((r.status, r.dados["erro"]), (403, servidor.REGISTRO_PRIVADO))
        self.assertEqual(self.ia(s, "POST", "analisar", {"texto": TEXTO, "privado": "sim"}).status, 400)
        self.assertEqual(self.ia(s, "POST", "busca", {"q": ""}).status, 400)
        self.assertEqual(self.ia(s, "POST", "busca", {"q": "a" * 600}).status, 400)

    @PRECISA_PROVEDORES
    def test_regras_nunca_chamam_a_ia(self):
        # /api/analisar e /api/busca usam só as regras, mesmo com IA configurada (seção 17.1).
        s = self.s
        falsa = self.ia_falsa()
        falsa.resposta = resposta_da_ia()
        self.criar_pin_ia()
        self.configurar_ia("principal", falsa.url)
        rid, proposta = s.registrar()
        self.assertEqual(proposta["motor"], "regras")
        busca = s.pedir("GET", "/api/busca?q=tulipas")
        self.assertEqual((busca.status, busca.dados["motor"]), (200, "regras"))
        self.assertIn(rid, [e["registro_id"] for e in busca.dados["encontrados"]])
        self.assertEqual(s.pedir("GET", "/api/hoje").status, 200)
        self.assertEqual(falsa.pedidos, [])

    @PRECISA_MOTOR
    def test_privado_nunca_vai_para_a_ia(self):
        s = self.s
        falsa = self.ia_falsa()
        self.criar_pin_ia()
        self.configurar_ia("principal", falsa.url)
        self.ia(s, "POST", "sair")                   # os botões ✨ não precisam da área da IA
        publico = self.registrar("Ela disse que adora tulipas amarelas.")
        segredo = "Ela me contou em segredo que adora as tulipas do Projeto Mandragorix."
        privado = self.registrar(segredo, privado=True)
        falsa.resposta = resposta_da_ia(publico)

        r = self.ia(s, "POST", "analisar", {"texto": segredo, "data_registro": "2026-09-30", "privado": True})
        self.assertEqual((r.status, r.dados["erro"]), (403, servidor.REGISTRO_PRIVADO))
        self.assertEqual(falsa.pedidos, [])

        r = self.ia(s, "POST", "busca", {"q": "tulipas"})
        self.assertEqual(r.status, 200, r.dados)
        self.assertEqual(r.dados["ia_usada"], "principal")
        self.assertIn(servidor.PRIVADOS_DE_FORA, r.dados["avisos"])
        self.assertIn("tulipas amarelas", falsa.recebido())
        self.assertNotIn("Mandragorix", falsa.recebido())
        self.assertEqual({e["registro_id"] for e in r.dados["encontrados"]}, {publico})

        r = self.ia(s, "POST", "sugestao", {})
        self.assertEqual(r.status, 200, r.dados)
        self.assertEqual(r.dados["ia_usada"], "principal")
        self.assertEqual([x["registro_ids"] for x in r.dados["sugestoes"]], [[publico]])
        self.assertNotIn("Mandragorix", falsa.recebido())

        # Só o privado responde à pergunta: nem a pergunta sai daqui.
        antes = len(falsa.pedidos)
        r = self.ia(s, "POST", "busca", {"q": "o que ela falou do projeto mandragorix"})
        self.assertEqual((r.status, r.dados["ia_usada"]), (200, None))
        self.assertEqual(len(falsa.pedidos), antes)
        self.assertNotIn("mandragorix", falsa.recebido().lower())

        # Desmarcado, ele pode ir.
        self.assertEqual(s.pedir("PATCH", f"/api/registros/{privado}", {"privado": False}).status, 200)
        self.ia(s, "POST", "sugestao", {})
        self.assertIn("Mandragorix", falsa.recebido())


# ---------------------------------------------------------------- os botões ✨

@PRECISA_MOTOR
class TestBotoesDaIA(BaseIA):
    def setUp(self):
        super().setUp()
        self.criar_pin_ia()

    def test_analisar_buscar_e_sugerir(self):
        s = self.s
        falsa = self.ia_falsa()
        falsa.resposta = resposta_da_ia()
        self.configurar_ia("principal", falsa.url)
        self.ia(s, "POST", "sair")

        r = self.ia(s, "POST", "analisar", {"texto": TEXTO, "data_registro": "2026-09-30"})
        self.assertEqual(r.status, 200, r.dados)
        self.assertEqual(set(r.dados), {"proposta", "mudancas", "ia_usada", "avisos"})
        self.assertEqual(r.dados["ia_usada"], "principal")
        proposta = r.dados["proposta"]
        self.assertTrue(proposta["motor"].startswith("ia:"))
        self.assertEqual(proposta["texto"], TEXTO)
        for item in proposta["itens"]:
            self.assertIn(item["trecho"], TEXTO)
        cirurgia = next(i for i in proposta["itens"] if i["tipo"] == "evento")
        self.assertEqual(cirurgia["data_evento"], "2026-11-01")          # a data é do Python
        self.assertIsInstance(proposta["relacoes"], list)
        self.assertTrue(r.dados["mudancas"])
        self.assertTrue(all(m["situacao"] in ("igual", "mudou", "novo", "removido") for m in r.dados["mudancas"]))
        conversa = falsa.conversas()[-1]
        self.assertEqual(conversa["autorizacao"], f"Bearer {CHAVE_FALSA}")
        self.assertIn("tulipas", conversa["corpo"])
        self.assertEqual(s.pedir("GET", "/api/registros").dados, [])       # analisar não grava

        corpo = {"texto": TEXTO, "data_registro": "2026-09-30", "itens": proposta["itens"],
                 "pessoas": proposta["pessoas"], "relacoes": proposta["relacoes"]}
        gravado = s.pedir("POST", "/api/registros", corpo)
        self.assertEqual(gravado.status, 200, gravado.dados)
        rid = gravado.dados["id"]

        falsa.resposta = resposta_da_ia(rid)
        r = self.ia(s, "POST", "busca", {"q": "o que ela disse sobre tulipas"})
        self.assertEqual(r.status, 200, r.dados)
        self.assertEqual((r.dados["ia_usada"], r.dados["texto"]), ("principal", "Ela disse que adora tulipas."))
        self.assertEqual({e["registro_id"] for e in r.dados["encontrados"]}, {rid})
        self.assertTrue(r.dados["motor"].startswith("ia:"))

        r = self.ia(s, "POST", "sugestao", {"data": "2026-09-30"})
        self.assertEqual(r.status, 200, r.dados)
        self.assertEqual(r.dados["ia_usada"], "principal")
        self.assertEqual(len(r.dados["sugestoes"]), 1)
        self.assertEqual(r.dados["sugestoes"][0]["registro_ids"], [rid])
        self.assertTrue(r.dados["sugestoes"][0]["porque"])
        self.assertEqual(self.ia(s, "POST", "sugestao", {"data": "30/09"}).status, 400)

    def test_sugestao_sem_nada_registrado_nao_chama_a_ia(self):
        falsa = self.ia_falsa()
        self.configurar_ia("principal", falsa.url)
        r = self.ia(self.s, "POST", "sugestao", {})
        self.assertEqual((r.status, r.dados), (200, {"sugestoes": [], "ia_usada": None, "avisos": []}))
        self.assertEqual(falsa.pedidos, [])

    def test_principal_cai_e_a_reserva_responde(self):
        s = self.s
        principal, reserva = self.ia_falsa(status=500), self.ia_falsa()
        self.configurar_ia("principal", principal.url)
        self.configurar_ia("reserva", reserva.url, chave=None)
        rid = self.registrar("Ela disse que adora tulipas.")
        reserva.resposta = resposta_da_ia(rid)

        r = self.ia(s, "POST", "analisar", {"texto": TEXTO, "data_registro": "2026-09-30"})
        self.assertEqual(r.status, 200, r.dados)
        self.assertEqual(r.dados["ia_usada"], "reserva")
        self.assertTrue(any("reserva" in a for a in r.dados["avisos"]), r.dados["avisos"])
        self.assertTrue(any("principal" in a for a in r.dados["avisos"]), r.dados["avisos"])
        self.assertTrue(principal.conversas())
        self.assertTrue(reserva.conversas())
        self.assertIsNone(reserva.conversas()[-1]["autorizacao"])       # a chave da principal não vai

        r = self.ia(s, "POST", "busca", {"q": "tulipas"})
        self.assertEqual((r.status, r.dados["ia_usada"]), (200, "reserva"))
        r = self.ia(s, "POST", "sugestao", {})
        self.assertEqual((r.status, r.dados["ia_usada"]), (200, "reserva"))

        # As duas fora: ficam as regras, com aviso.
        reserva.status = 503
        r = self.ia(s, "POST", "analisar", {"texto": TEXTO, "data_registro": "2026-09-30"})
        self.assertEqual(r.status, 200, r.dados)
        self.assertEqual((r.dados["ia_usada"], r.dados["mudancas"], r.dados["proposta"]["motor"]),
                         (None, [], "regras"))
        self.assertTrue(r.dados["avisos"])
        self.assertTrue(r.dados["proposta"]["itens"])
        r = self.ia(s, "POST", "busca", {"q": "tulipas"})
        self.assertEqual((r.status, r.dados["ia_usada"], r.dados["motor"]), (200, None, "regras"))
        self.assertIn(rid, [e["registro_id"] for e in r.dados["encontrados"]])
        r = self.ia(s, "POST", "sugestao", {})
        self.assertEqual((r.status, r.dados["sugestoes"], r.dados["ia_usada"]), (200, [], None))
        self.assertTrue(r.dados["avisos"])

    def test_bloqueio_durante_a_resposta_nao_entrega(self):
        s = self.s
        falsa = self.ia_falsa()
        rid = self.registrar("Ela disse que adora tulipas.")
        falsa.resposta = resposta_da_ia(rid)
        self.configurar_ia("principal", falsa.url)
        real = servidor.EntreNos._cadeia_ia

        def cadeia_que_bloqueia(app, token):
            cadeia, banco = real(app, token)
            original = cadeia.responder

            def responder(*args, **kwargs):
                resultado = original(*args, **kwargs)
                app.bloquear()                    # o app bloqueou enquanto a IA pensava
                return resultado
            cadeia.responder = responder
            return cadeia, banco

        with mock.patch.object(servidor.EntreNos, "_cadeia_ia", cadeia_que_bloqueia):
            r = self.ia(s, "POST", "busca", {"q": "tulipas"})
        self.assertEqual(r.status, 423)
        self.assertNotIn(b"tulipas", r.bruto)


@PRECISA_MOTOR
class TestCorrecaoFinal(BaseIA):
    """Regressões dos achados da revisão final da seção 17 (30/09/2026)."""

    def setUp(self):
        super().setUp()
        self.criar_pin_ia()

    def com_ia(self, modelo: str = "modelo-falso") -> IAFalsa:
        falsa = self.ia_falsa()
        self.configurar_ia("principal", falsa.url, modelo=modelo)
        self.ia(self.s, "POST", "sair")               # os botões ✨ não precisam da área da IA
        return falsa

    # ------------------------------------------------ 🔒 e cadastro não vão pela busca nem pela sugestão

    def test_privado_nao_vaza_pelas_pessoas_importantes_nem_pela_sugestao(self):
        s = self.s
        falsa = self.com_ia()
        self.registrar("A terapeuta dela, Renata, disse que ela tem crise de pânico.", "2026-09-20", privado=True)
        renata = self.registrar("Ela voltou da consulta com a Renata mais calma.", "2026-09-22")
        self.registrar("A tia dela, Lúcia, foi internada por alcoolismo.", "2026-09-24", privado=True)
        tia = self.registrar("A tia dela vem jantar sábado e adora pudim.", "2026-09-26")
        self.registrar("Ela começou a ir no psiquiatra, o Dr. Paulo, por causa da depressão.", "2026-09-25",
                       privado=True)
        paulo = self.registrar("O Dr. Paulo pediu para ela voltar semana que vem.", "2026-09-27")
        falsa.resposta = {"texto": "A tia dela e a Renata aparecem bastante.", "citacoes": [renata, tia, paulo],
                          "sugestoes": [{"texto": "Talvez fazer um pudim para o jantar de sábado.",
                                         "porque": "Ela adora pudim.", "registro_ids": [tia]}]}

        r = self.ia(s, "POST", "busca", {"q": "Quais são as pessoas mais importantes para ela?"})
        self.assertEqual((r.status, r.dados["ia_usada"]), (200, "principal"), r.dados)
        r = self.ia(s, "POST", "sugestao", {})
        self.assertEqual((r.status, r.dados["ia_usada"]), (200, "principal"), r.dados)
        self.assertEqual(len(falsa.conversas()), 2)

        recebido = falsa.recebido()
        self.assertIn("Renata", recebido)               # está escrito no registro comum: pode ir
        self.assertIn("pudim", recebido)
        for segredo in ("terapeuta", "pânico", "Lúcia", "Lucia", "alcoolismo", "internada", "psiquiatra",
                        "depressão", "2 registros"):
            self.assertNotIn(segredo, recebido, segredo)
        # Na tela, a busca normal (só as regras, nada sai daqui) continua mostrando o cadastro.
        busca = s.pedir("GET", "/api/busca?q=" + "pessoas%20mais%20importantes").dados
        self.assertIn("Lúcia", json.dumps(busca, ensure_ascii=False))

    def test_nome_so_do_cadastro_nao_vai_na_busca_nem_na_sugestao(self):
        s = self.s
        falsa = self.com_ia()
        self.assertEqual(s.pedir("POST", "/api/pessoas", {"nome": "Sandra Regina", "relacao": "mãe"}).status, 200)
        rid = self.registrar("A mãe dela vai fazer uma cirurgia em novembro e a Julia está preocupada.")
        registro = s.pedir("GET", f"/api/registros/{rid}").dados
        self.assertIn("Sandra Regina", json.dumps(registro["itens"], ensure_ascii=False))   # as regras usaram o cadastro
        falsa.resposta = resposta_da_ia(rid)
        self.assertEqual(self.ia(s, "POST", "sugestao", {}).dados["ia_usada"], "principal")
        r = self.ia(s, "POST", "busca", {"q": "cirurgia da mãe"})
        self.assertEqual(r.dados["ia_usada"], "principal", r.dados)
        self.assertIn("Sandra Regina", json.dumps(r.dados["encontrados"], ensure_ascii=False))   # a tela mostra
        recebido = falsa.recebido()
        self.assertIn("cirurgia", recebido)
        self.assertNotIn("Sandra", recebido)
        self.assertNotIn("Regina", recebido)
        # A análise ✨ já protegia; continua.
        self.ia(s, "POST", "analisar", {"texto": "A mãe dela vai fazer uma cirurgia em novembro.",
                                        "data_registro": "2026-09-30"})
        self.assertNotIn("Sandra", falsa.recebido())

    def test_busca_diz_o_que_e_privado_e_se_ha_o_que_mandar(self):
        s = self.s
        self.com_ia()
        self.registrar("Ela me contou da entrevista no banco.", privado=True)
        busca = s.pedir("GET", "/api/busca?q=entrevista").dados
        self.assertTrue(busca["encontrados"])
        self.assertTrue(all(e["privado"] for e in busca["encontrados"]))
        self.assertFalse(busca["para_ia"])
        publico = self.registrar("A entrevista dela foi remarcada para sexta.")
        busca = s.pedir("GET", "/api/busca?q=entrevista").dados
        self.assertTrue(busca["para_ia"])
        self.assertEqual({e["registro_id"] for e in busca["encontrados"] if not e["privado"]}, {publico})

    # ------------------------------------------------ o modelo escolhido não aparece nas respostas ✨

    def test_resposta_do_brilho_nao_diz_servico_nem_modelo(self):
        s = self.s
        falsa = self.com_ia(modelo="modelo-a")
        rid = self.registrar("Ela disse que adora tulipas.")
        falsa.resposta = resposta_da_ia(rid)
        r = self.ia(s, "POST", "analisar", {"texto": TEXTO, "data_registro": "2026-09-30"})
        self.assertEqual((r.status, r.dados["proposta"]["motor"]), (200, "ia:principal"))
        r2 = self.ia(s, "POST", "busca", {"q": "tulipas"})
        self.assertEqual((r2.dados["ia_usada"], r2.dados["motor"]), ("principal", "ia:principal"))
        for resposta in (r, r2, self.ia(s, "POST", "sugestao", {})):
            self.assertNotIn(b"modelo-a", resposta.bruto)
            self.assertNotIn(b"outro/", resposta.bruto)

    # ------------------------------------------------ a chave some também dos backups

    def test_chave_e_pin_da_ia_somem_dos_backups(self):
        dia = [date(2026, 9, 29)]
        s = self.novo_servidor(hoje=lambda: dia[0])
        self.criar_pin_ia(s)
        chave_reserva = "chave-da-reserva-SEGREDO2"
        self.configurar_ia("principal", "https://ia.exemplo.com/v1", chave=CHAVE, s=s)
        self.configurar_ia("reserva", "https://ia2.exemplo.com/v1", chave=chave_reserva, s=s)
        pin_ia_antigo = s.app._banco.config()["ia_pin"]["hash"]
        dia[0] = date(2026, 9, 30)

        def backups() -> list[tuple[dict, bytes]]:
            saida = []
            for caminho in s.app.cofre.backups():
                pasta = tempfile.mkdtemp(prefix="entrenos-teste-backup-")
                try:
                    shutil.copy(caminho, Path(pasta) / config.ARQUIVO_COFRE)
                    copia = cofre.Cofre(Path(pasta))
                    dados = copia.abrir(PIN)                    # só o PIN principal
                    copia.fechar()
                finally:
                    shutil.rmtree(pasta, ignore_errors=True)
                banco = servidor.Banco.de_bytes(dados)
                saida.append((banco.config(), dados))
                banco.fechar()
            return saida

        # Apagar a chave da principal: o backup de hoje (feito do disco, ainda com a chave) é limpo.
        self.configurar_ia("principal", "https://ia.exemplo.com/v1", chave="", s=s)
        copias = backups()
        self.assertTrue(copias)
        for ajustes, dados in copias:
            self.assertNotIn(CHAVE.encode(), dados)
            self.assertIsNone((ajustes.get("ia_principal") or {}).get("chave"))
        self.assertTrue(any(chave_reserva.encode() in dados for _, dados in copias))   # a reserva não mudou

        # Remover a reserva: some inteira das cópias.
        self.assertEqual(self.ia(s, "DELETE", "config/reserva").status, 200)
        for ajustes, dados in backups():
            self.assertNotIn(chave_reserva.encode(), dados)
            self.assertIsNone(ajustes.get("ia_reserva"))

        # Trocar o PIN da IA: o verificador antigo sai das cópias.
        self.assertEqual(self.ia(s, "POST", "pin/trocar", {"atual": PIN_IA, "novo": PIN_IA_NOVO}).status, 200)
        for ajustes, dados in backups():
            self.assertNotIn(pin_ia_antigo.encode(), dados)

        # "Esqueci o PIN da IA": nem PIN da IA nem IA nas cópias.
        self.ia(s, "POST", "sair")
        self.assertEqual(self.ia(s, "POST", "redefinir", {"pin": PIN}).status, 200)
        for ajustes, dados in backups():
            self.assertEqual((ajustes.get("ia_pin"), ajustes.get("ia_principal"), ajustes.get("ia_reserva")),
                             (None, None, None))

    # ------------------------------------------------ PIN da IA que é variação do principal

    def test_variacoes_do_pin_principal_nao_servem_de_pin_da_ia(self):
        s = self.novo_servidor()
        for variacao in (PIN.upper(), PIN + " ", "  " + PIN, PIN.replace(" ", "  "), PIN.title(), PIN.capitalize()):
            with self.subTest(variacao=variacao):
                r = self.ia(s, "POST", "pin", {"novo": variacao})
                self.assertEqual(r.status, 400, r.dados)
                self.assertIn("diferente do PIN principal", r.dados["erro"])
        self.criar_pin_ia(s)                                  # um PIN de verdade diferente passa

    # ------------------------------------------------ IA salva que não dá para usar

    def test_ia_salva_incompleta_diz_o_motivo(self):
        s = self.s
        # Como um motor da v2 migrado: Groq sem chave (o PUT de hoje nem deixaria salvar assim).
        s.app._banco.salvar_config(ia_principal={"provedor": "groq", "url": "https://api.groq.com/openai/v1",
                                                 "modelo": "llama", "tipo": "openai", "chave": None})
        ia = s.pedir("GET", "/api/estado", token=None).dados["ia"]
        self.assertEqual((ia["configurada"], ia["utilizavel"], ia["espera_maxima"]), (True, False, None))
        estado = self.estado_ia()
        self.assertEqual((estado["configurada"], estado["utilizavel"]), (True, False))
        for rota, corpo in (("analisar", {"texto": TEXTO}), ("busca", {"q": "tulipas"}), ("sugestao", {})):
            with self.subTest(rota=rota):
                r = self.ia(s, "POST", rota, corpo)
                self.assertEqual(r.status, 409, r.dados)
                self.assertNotEqual(r.dados["erro"], servidor.SEM_IA)
                self.assertIn("precisa de uma chave", r.dados["erro"])
        self.assertEqual(self.ia(s, "POST", "entrar", {"pin_ia": PIN_IA}).status, 200)
        problemas = self.ia(s, "GET", "config").dados["problemas"]
        self.assertIn("precisa de uma chave", problemas["principal"])
        # Chave indo por http:// para a internet: o motivo é outro.
        s.app._banco.salvar_config(ia_principal={"provedor": "groq", "url": "http://api.groq.com/openai/v1",
                                                 "modelo": "llama", "tipo": "openai", "chave": "gsk_SEGREDO0123"})
        r = self.ia(s, "POST", "analisar", {"texto": TEXTO})
        self.assertEqual(r.status, 409)
        self.assertIn("https://", r.dados["erro"])
        self.assertNotIn(b"SEGREDO", r.bruto)
        # Com uma reserva que funciona, o ✨ volta (e diz que a principal está incompleta).
        falsa = self.ia_falsa()
        rid = self.registrar("Ela disse que adora tulipas.")
        falsa.resposta = resposta_da_ia(rid)
        self.configurar_ia("reserva", falsa.url)
        self.assertTrue(self.estado_ia()["utilizavel"])
        r = self.ia(s, "POST", "busca", {"q": "tulipas"})
        self.assertEqual((r.status, r.dados["ia_usada"]), (200, "reserva"))
        self.assertTrue(any("incompleta" in a for a in r.dados["avisos"]), r.dados["avisos"])

    # ------------------------------------------------ sugestões que caem na conferência

    def test_sugestoes_descartadas_viram_sem_informacao(self):
        s = self.s
        falsa = self.com_ia()
        rid = self.registrar("Ela está cansada com o trabalho.")
        falsa.resposta = {"sugestoes": [{"texto": "Talvez levar um chocolate.", "porque": "x", "registro_ids": [99999]}]}
        r = self.ia(s, "POST", "sugestao", {})
        self.assertEqual((r.status, r.dados["sugestoes"], r.dados["ia_usada"]), (200, [], "principal"))
        self.assertTrue(any("descartei 1 sugestão" in a for a in r.dados["avisos"]), r.dados["avisos"])
        self.assertFalse(any("Nenhuma IA respondeu" in a for a in r.dados["avisos"]))
        # E frase de afastamento/silêncio não chega à tela.
        falsa.resposta = {"sugestoes": [
            {"texto": frase, "porque": "Ela está cansada.", "registro_ids": [rid]}
            for frase in ("Talvez sumir por uns dias para ela sentir sua falta.",
                          "Pode ser uma boa ignorar as mensagens dela até ela pedir desculpas.",
                          "Talvez não responder logo, pra ela perceber o quanto precisa de você.")]}
        r = self.ia(s, "POST", "sugestao", {})
        self.assertEqual((r.dados["sugestoes"], r.dados["ia_usada"]), ([], "principal"))

    # ------------------------------------------------ "Outro" cuja base não tem versão

    def test_outro_sem_versao_usa_o_endereco_como_digitado(self):
        s = self.s
        falsa = self.ia_falsa(prefixo="/inference")           # como https://models.github.ai/inference
        digitada = f"http://127.0.0.1:{falsa.porta}/inference"
        r = self.ia(s, "POST", "modelos", {"provedor": "outro", "url": digitada})
        self.assertEqual(r.status, 200, r.dados)
        self.assertEqual(r.dados, {"modelos": ["modelo-a", "modelo-b"], "url": digitada, "url_exata": True})
        self.assertEqual([p["caminho"] for p in falsa.pedidos], ["/inference/v1/models", "/inference/models"])
        # Salvar sem o aviso ganha /v1 (como antes); com url_exata fica como foi digitado.
        self.assertEqual(self.configurar_ia("principal", digitada)["url"], digitada + "/v1")
        r = self.ia(s, "PUT", "config/principal", {"provedor": "outro", "url": digitada, "modelo": "modelo-a",
                                                   "url_exata": True})
        self.assertEqual((r.status, r.dados["url"], r.dados.get("url_exata")), (200, digitada, True))
        self.assertEqual(s.app._banco.config()["ia_principal"]["url"], digitada)
        self.assertEqual(self.ia(s, "POST", "testar", {"qual": "principal"}).status, 200)
        self.assertEqual(falsa.pedidos[-1]["caminho"], "/inference/chat/completions")   # o teste usa o exato
        rid = self.registrar("Ela disse que adora tulipas.")
        falsa.resposta = resposta_da_ia(rid)
        self.ia(s, "POST", "sair")
        r = self.ia(s, "POST", "busca", {"q": "tulipas"})
        self.assertEqual(r.dados["ia_usada"], "principal", r.dados)
        self.assertEqual(falsa.pedidos[-1]["caminho"], "/inference/chat/completions")
        # url_exata só vale em "Outro", e precisa ser verdadeiro ou falso.
        self.assertEqual(self.ia(s, "POST", "entrar", {"pin_ia": PIN_IA}).status, 200)
        r = self.ia(s, "PUT", "config/reserva", {"provedor": "outro", "url": digitada, "modelo": "m", "url_exata": "sim"})
        self.assertEqual(r.status, 400)
        r = self.ia(s, "PUT", "config/reserva", {"provedor": "lmstudio", "modelo": "m", "url_exata": True})
        self.assertEqual((r.status, r.dados["url"], "url_exata" in r.dados), (200, "http://127.0.0.1:1234/v1", False))
        # Endereço que não responde nem com /v1 nem sem: fica o erro da primeira tentativa.
        r = self.ia(s, "POST", "modelos", {"provedor": "outro", "url": f"http://127.0.0.1:{falsa.porta}/nada"})
        self.assertEqual(r.status, 502)
        self.assertIn("não achou", r.dados["erro"])

    # ------------------------------------------------ estado da área e espera

    def test_estado_traz_o_relogio_da_area_e_a_espera(self):
        s = self.s
        estado = self.estado_ia()
        self.assertTrue(estado["liberada"])
        self.assertTrue(0 < estado["segundos_restantes"] <= servidor.MINUTOS_AREA_IA * 60, estado)
        principal, reserva = self.ia_falsa(), self.ia_falsa()
        self.configurar_ia("principal", principal.url)
        self.configurar_ia("reserva", reserva.url)
        self.assertEqual(self.ia(s, "PATCH", "config", {"tempo_limite": 45}).status, 200)
        self.assertEqual(self.estado_ia()["espera_maxima"], 90)          # principal + reserva
        self.assertEqual(s.pedir("GET", "/api/estado", token=None).dados["ia"]["espera_maxima"], 90)
        self.ia(s, "POST", "sair")
        self.assertIsNone(self.estado_ia()["segundos_restantes"])

    def test_tempo_esgotado_ao_buscar_modelos_e_504(self):
        from entrenos.motor import ErroIA
        erro = servidor._erro_da_ia(ErroIA("A IA demorou mais que o tempo limite (15 s).", "tempo", "TimeoutError"))
        self.assertEqual((erro.status, erro.mensagem), (504, "A IA demorou mais que o tempo limite (15 s)."))
        self.assertEqual(servidor._erro_da_ia(ErroIA("A chave foi recusada.", "chave")).status, 502)


class TestContextoDaSugestao(BaseIA):
    def test_recentes_proximos_e_sem_privados(self):
        s = self.s
        antigo = self.registrar("Ela adora girassóis.", "2026-05-02")
        recente = self.registrar("Ela está cansada com o trabalho.", "2026-09-28")
        evento = self.registrar("Ela vai fazer uma prova dia 5 de outubro.", "2026-06-10")
        privado = self.registrar("Ela está preocupada com o Projeto Mandragorix.", "2026-09-29", privado=True)
        contexto = servidor.contexto_sugestao(s.app._banco, date(2026, 9, 30))
        registros = {c["registro_id"] for c in contexto}
        self.assertIn(recente, registros)
        self.assertIn(evento, registros)                     # a prova é daqui a 5 dias
        self.assertNotIn(antigo, registros)                  # gosto antigo sozinho não entra
        self.assertNotIn(privado, registros)
        self.assertNotIn("Mandragorix", json.dumps(contexto, ensure_ascii=False))
        self.assertLessEqual(len(contexto), servidor.MAX_CONTEXTO_SUGESTAO)
        for c in contexto:
            self.assertTrue({"registro_id", "tipo", "resumo", "trecho", "certeza", "data_registro"} <= set(c))
            json.dumps(c)                                    # pronto para JSON (datas em texto)


if __name__ == "__main__":
    unittest.main()
