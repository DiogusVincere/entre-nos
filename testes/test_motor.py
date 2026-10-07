"""Testes do motor de regras, do MotorIA e da cadeia de IAs (servidores HTTP FALSOS em 127.0.0.1).

Nenhum teste fala com serviço de IA de verdade: cada servidor falso roda numa thread e imita
o Ollama (/api/chat, /api/tags) ou o formato comum (/v1/chat/completions, /v1/models).
"""
from __future__ import annotations

import json
import socket
import threading
import time
import unittest
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from entrenos.modelos import CATEGORIAS, Certeza, Item, Precisao, Proposta, Resposta, TipoItem, para_json
from entrenos.motor import (AVISO_IA, FRASE_TESTE, NAO_ENCONTREI, CadeiaIA, Conhecimento, ErroIA, Motor, MotorIA,
                            MotorRegras, _json_do_modelo, _manipuladora, comparar_propostas, data_extenso,
                            envia_para_fora, esquecer_formatos, ia_envia_para_fora, juntar, listar_modelos,
                            obter_cadeia_ia, obter_motor, testar_ia)

REF = date(2026, 9, 30)   # quarta-feira


# ---------------------------------------------------------------- servidor falso

class _Manipulador(BaseHTTPRequestHandler):
    def log_message(self, *args):   # silêncio nos testes
        pass

    def _responder(self, codigo: int, corpo: dict | str, cabecalhos: dict | None = None) -> None:
        dados = (corpo if isinstance(corpo, str) else json.dumps(corpo, ensure_ascii=False)).encode("utf-8")
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(dados)))
        for k, v in (cabecalhos or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(dados)

    def do_GET(self):
        srv = self.server
        srv.pedidos.append({"metodo": "GET", "caminho": self.path, "cabecalhos": dict(self.headers)})
        if srv.modo in ("erro401", "erro403"):
            self._responder(int(srv.modo[-3:]), {"error": {"message": "Incorrect API key provided: sk-...zzzz"}})
        elif self.path.endswith("/api/tags"):
            self._responder(200, {"models": [{"name": n, "model": n, "size": 1} for n in srv.modelos]})
        else:
            self._responder(200, {"object": "list", "data": [{"id": n, "object": "model"} for n in srv.modelos]})

    @staticmethod
    def _recusa(modo: str, corpo: dict) -> tuple[int, dict] | None:
        formato = (corpo.get("response_format") or {}).get("type")
        if modo == "erro500":
            return 500, {"error": "falhou"}
        if modo in ("erro401", "erro403"):
            return int(modo[-3:]), {"error": {"message": "Incorrect API key provided: sk-...zzzz"}}
        if modo == "erro429":
            return 429, {"error": {"message": "Rate limit reached for model"}}
        if modo == "erro404":
            return 404, {"error": "model 'modelo-teste' not found"}
        if modo == "recusa_tudo":
            return 400, {"error": {"message": "bad request"}}
        if modo == "recusa_schema" and formato == "json_schema":
            return 400, {"error": {"message": "response_format json_schema is not supported by this model"}}
        if modo == "so_texto" and formato:
            return 400, {"error": {"message": "response_format is not supported"}}
        if modo == "recusa_esquema_ollama" and isinstance(corpo.get("format"), dict):
            return 400, {"error": "invalid format: expected \"json\" or a valid JSON schema"}
        if modo == "recusa_temperatura" and "temperature" in corpo:
            return 400, {"error": {"message": "Unsupported value: 'temperature' does not support 0 with this model."}}
        if modo == "contexto_grande":
            return 400, {"error": {"message": "This model's maximum context length is 8192 tokens. Please reduce "
                                              "the length of the messages.", "type": "invalid_request_error",
                                   "code": "context_length_exceeded"}}
        if modo == "modelo_invalido":
            return 400, {"error": {"message": "The model `modelo-teste` does not exist or you do not have access to it."}}
        return None

    def do_POST(self):
        tamanho = int(self.headers.get("Content-Length") or 0)
        bruto = self.rfile.read(tamanho) if tamanho else b""
        corpo = json.loads(bruto.decode("utf-8")) if bruto else {}
        srv = self.server
        srv.pedidos.append({"metodo": "POST", "caminho": self.path, "cabecalhos": dict(self.headers), "corpo": corpo,
                            "bruto": bruto})
        recusa = self._recusa(srv.modo, corpo)
        if recusa:
            self._responder(*recusa)
            return
        if srv.modo == "redireciona":
            self._responder(307, {}, {"Location": f"http://127.0.0.1:{srv.server_address[1]}/capturado"})
            return
        if srv.modo == "demora":
            time.sleep(1.0)
        conteudo = srv.conteudo if isinstance(srv.conteudo, str) else json.dumps(srv.conteudo, ensure_ascii=False)
        if srv.modo in ("corpo_lento", "corpo_pingado"):
            # Cabeçalhos na hora; o corpo chega um byte de cada vez (espaços antes do JSON).
            dados = json.dumps({"choices": [{"message": {"role": "assistant", "content": conteudo}}]},
                               ensure_ascii=False).encode("utf-8")
            pingos, pausa = (20, 0.25) if srv.modo == "corpo_lento" else (4, 0.15)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(pingos + len(dados)))
            self.end_headers()
            try:
                for _ in range(pingos):
                    self.wfile.write(b" ")
                    self.wfile.flush()
                    time.sleep(pausa)
                self.wfile.write(dados)
            except OSError:
                pass            # o cliente desistiu (tempo esgotado)
            return
        if srv.modo in ("texto_com_json", "so_texto"):
            conteudo = ("<think>Rascunho {com chaves} que não é a resposta.</think>\n"
                        "Claro! Veja {isto}: aqui está o que entendi.\n" + conteudo + "\nQualquer coisa, é só pedir.")
        if self.path.endswith("/chat/completions"):
            self._responder(200, {"choices": [{"message": {"role": "assistant", "content": conteudo}}]})
        else:
            self._responder(200, {"message": {"role": "assistant", "content": conteudo}, "done": True})


class _ServidorFalso(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), _Manipulador)
        self.pedidos: list[dict] = []
        self.modo = "bom"
        self.conteudo: dict | str = {"itens": []}
        self.modelos: list[str] = []

    def handle_error(self, request, client_address):   # cliente que desistiu (tempo esgotado)
        pass

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"

    def posts(self) -> list[dict]:
        return [p for p in self.pedidos if p["metodo"] == "POST"]


def _ligar(servidor: _ServidorFalso) -> threading.Thread:
    linha = threading.Thread(target=servidor.serve_forever, daemon=True)
    linha.start()
    return linha


def _desligar(servidor: _ServidorFalso, linha: threading.Thread) -> None:
    servidor.shutdown()
    servidor.server_close()
    linha.join(5)


def _porta_fechada() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _item_ia(tipo, resumo, trecho, certeza="observado", categorias=(), alvo=None, polaridade=None,
             pessoa=None, data_expressao=None, **extra):
    d = {"tipo": tipo, "resumo": resumo, "trecho": trecho, "certeza": certeza, "categorias": list(categorias),
         "alvo": alvo, "polaridade": polaridade, "pessoa": pessoa, "data_expressao": data_expressao}
    d.update(extra)
    return d


TEXTO = ("Ela me contou que ama sushi. Acho que ela gosta de tulipas. "
         "Ela vai fazer uma apresentação no trabalho dia 20/10.")
SUSHI = _item_ia("preferencia", "Gosta de sushi", "Ela me contou que ama sushi.", "confirmado",
                 ["Comidas", "Astrologia"], "sushi", 1)
TULIPAS = _item_ia("preferencia", "Gosta de tulipas", "Acho que ela gosta de tulipas.", "confirmado",
                   ["Presentes"], "tulipas", 1)
APRESENTACAO = _item_ia("evento", "Apresentação no trabalho",
                        "Ela vai fazer uma apresentação no trabalho dia 20/10.", "observado", ["Trabalho"],
                        data_expressao="dia 20/10", data_evento="2031-01-01")


class _ComServidor(unittest.TestCase):
    """Um servidor falso por classe; formatos lembrados e pedidos zerados a cada teste."""

    @classmethod
    def setUpClass(cls):
        cls.srv = _ServidorFalso()
        cls.linha = _ligar(cls.srv)

    @classmethod
    def tearDownClass(cls):
        _desligar(cls.srv, cls.linha)

    def setUp(self):
        esquecer_formatos()
        self.srv.pedidos.clear()
        self.srv.modo = "bom"
        self.srv.conteudo = {"itens": [SUSHI, TULIPAS, APRESENTACAO]}
        self.srv.modelos = []

    def motor(self, **kw) -> MotorIA:
        kw.setdefault("tempo_limite", 5)
        tipo = kw.pop("tipo", "ollama")
        provedor = kw.pop("provedor", "ollama" if tipo == "ollama" else ("openai" if kw.get("chave") else "outro"))
        return MotorIA(provedor, kw.pop("url", self.srv.url), kw.pop("modelo", "modelo-teste"), tipo, **kw)


class TesteMotorIA(_ComServidor):
    def assert_voltou_para_regras(self, proposta: Proposta, texto: str = TEXTO):
        regras = MotorRegras().analisar(texto, REF, Conhecimento())
        self.assertEqual(proposta.motor, "regras")
        self.assertIn(AVISO_IA, proposta.avisos)
        self.assertEqual([i.resumo for i in proposta.itens], [i.resumo for i in regras.itens])

    # ------------------------------------------------------------ JSON bom

    def test_json_bom_ollama(self):
        motor = self.motor()
        p = motor.analisar(TEXTO, REF, Conhecimento())
        self.assertEqual(p.motor, "ia:ollama/modelo-teste")
        self.assertNotIn(AVISO_IA, p.avisos)
        self.assertEqual(len(p.itens), 3)
        sushi, tulipas, evento = p.itens
        self.assertEqual(sushi.categorias, ["Comidas"])            # "Astrologia" saiu
        self.assertEqual(sushi.certeza, Certeza.CONFIRMADO)
        self.assertEqual(tulipas.certeza, Certeza.INFERENCIA)       # "acho que" não vira confirmado
        self.assertEqual(evento.data_evento, date(2026, 10, 20))    # o Python calculou, não a IA
        self.assertEqual(evento.precisao, Precisao.DIA)
        self.assertEqual(evento.data_expressao, "dia 20/10")
        self.assertIn("Eventos futuros", evento.categorias)
        for item in p.itens:
            self.assertIn(item.trecho, TEXTO)
            self.assertTrue(set(item.categorias) <= set(CATEGORIAS))
        self.assertIsNone(motor.ultimo_erro)
        self.assertEqual(motor.formato, "esquema")
        json.dumps(para_json(p))

    def test_pedido_ollama(self):
        self.motor().analisar(TEXTO, REF, Conhecimento())
        pedido = self.srv.pedidos[-1]
        self.assertEqual(pedido["caminho"], "/api/chat")
        corpo = pedido["corpo"]
        self.assertEqual(corpo["model"], "modelo-teste")
        self.assertIs(corpo["stream"], False)
        self.assertEqual(corpo["options"], {"temperature": 0})
        self.assertIsInstance(corpo["format"], dict)
        self.assertIn("itens", corpo["format"]["properties"])
        self.assertTrue(pedido["cabecalhos"]["Content-Type"].startswith("application/json"))
        self.assertNotIn("Authorization", pedido["cabecalhos"])
        enviado = json.loads(corpo["messages"][1]["content"])
        self.assertEqual(enviado["texto"], TEXTO)
        self.assertTrue(enviado["proposta_das_regras"])             # as regras rodam antes

    def test_openai_compativel(self):
        motor = self.motor(tipo="openai", chave="segredo-de-teste")
        p = motor.analisar(TEXTO, REF, Conhecimento())
        self.assertEqual(p.motor, "ia:openai/modelo-teste")
        self.assertEqual(len(p.itens), 3)
        pedido = self.srv.pedidos[-1]
        self.assertEqual(pedido["caminho"], "/v1/chat/completions")
        self.assertEqual(pedido["cabecalhos"]["Authorization"], "Bearer segredo-de-teste")
        formato = pedido["corpo"]["response_format"]
        self.assertEqual(formato["type"], "json_schema")
        self.assertTrue(formato["json_schema"]["strict"])
        self.assertIn("schema", formato["json_schema"])
        self.assertEqual(pedido["corpo"]["temperature"], 0)
        self.assertEqual(motor.formato, "json_schema")

    def test_openai_url_com_v1_nao_duplica(self):
        self.motor(tipo="openai", url=self.srv.url + "/v1/").analisar(TEXTO, REF, Conhecimento())
        self.assertEqual(self.srv.pedidos[-1]["caminho"], "/v1/chat/completions")

    def test_url_com_versao_propria(self):
        # Serviço com a versão no meio do caminho (como Groq e Google): nada é acrescentado.
        self.motor(tipo="openai", url=self.srv.url + "/openai/v1beta").analisar(TEXTO, REF, Conhecimento())
        self.assertEqual(self.srv.pedidos[-1]["caminho"], "/openai/v1beta/chat/completions")

    def test_json_em_cerca_de_codigo(self):
        self.srv.conteudo = "```json\n" + json.dumps({"itens": [SUSHI]}) + "\n```"
        p = self.motor().analisar(TEXTO, REF, Conhecimento())
        self.assertEqual(p.motor, "ia:ollama/modelo-teste")

    def test_texto_com_json_no_meio(self):
        self.srv.modo = "texto_com_json"
        motor = self.motor(tipo="openai")
        p = motor.analisar(TEXTO, REF, Conhecimento())
        self.assertEqual(p.motor, "ia:outro/modelo-teste")
        self.assertEqual(len(p.itens), 3)
        self.assertEqual(len(self.srv.posts()), 1)                  # o formato foi aceito; só o texto veio solto

    def test_nao_manda_cadastro_nem_nome_dela(self):
        # O cadastro e o nome dela ficam no computador: só vai o que está no próprio texto.
        texto = "A mãe dela vai fazer uma cirurgia em novembro."
        conhecimento = Conhecimento(pessoas=[{"id": 7, "nome": "Sandra", "relacao": "mãe", "apelidos": ["Sandrinha"]}],
                                    nome_dela="Valentina")
        self.srv.conteudo = {"itens": [_item_ia("evento", "Mãe: cirurgia", texto, pessoa="mãe", categorias=["Saúde"],
                                                data_expressao="em novembro")]}
        p = self.motor().analisar(texto, REF, conhecimento)
        self.assertEqual(p.itens[0].pessoa_id, 7)                    # o casamento com o cadastro é local
        bruto = self.srv.pedidos[-1]["bruto"].decode("utf-8")
        for segredo in ("Sandra", "Valentina", "Sandrinha"):
            self.assertNotIn(segredo, bruto)
        enviado = json.loads(self.srv.pedidos[-1]["corpo"]["messages"][1]["content"])
        regra = enviado["proposta_das_regras"][0]
        self.assertTrue(regra["resumo"].startswith("Mãe: cirurgia"), regra)   # o nome virou a relação do texto
        self.assertEqual(regra["pessoa"], "mãe")
        self.assertEqual(enviado["pessoas_citadas"], [{"trecho": "A mãe dela", "nome": None, "relacao": "mãe"}])

    # ------------------------------------------------------------ validações

    def test_trecho_inventado_e_descartado(self):
        inventado = _item_ia("preferencia", "Odeia rosas", "Ela odeia rosas.", "confirmado", ["Presentes"],
                             "rosas", -1)
        self.srv.conteudo = {"itens": [SUSHI, TULIPAS, APRESENTACAO, inventado]}
        p = self.motor().analisar(TEXTO, REF, Conhecimento())
        self.assertEqual(p.motor, "ia:ollama/modelo-teste")
        self.assertNotIn("rosas", " ".join(i.resumo for i in p.itens))
        self.assertTrue(any("Descartei 1 item" in a for a in p.avisos))

    def test_so_trechos_inventados_volta_para_regras(self):
        self.srv.conteudo = {"itens": [_item_ia("nota", "Nada a ver", "Frase que não existe.")]}
        self.assert_voltou_para_regras(self.motor().analisar(TEXTO, REF, Conhecimento()))

    def test_certeza_inflada_e_rebaixada(self):
        texto = "Ela pediu sushi no jantar. Talvez ela goste de tulipas."
        self.srv.conteudo = {"itens": [
            _item_ia("preferencia", "Gosta de sushi", "Ela pediu sushi no jantar.", "confirmado", ["Comidas"],
                     "sushi", 1),
            _item_ia("preferencia", "Gosta de tulipas", "Talvez ela goste de tulipas.", "incerto", ["Presentes"],
                     "tulipas", 1),
        ]}
        p = self.motor().analisar(texto, REF, Conhecimento())
        self.assertEqual(p.motor, "ia:ollama/modelo-teste")
        self.assertEqual(p.itens[0].certeza, Certeza.OBSERVADO)
        self.assertEqual(p.itens[1].certeza, Certeza.INFERENCIA)

    def test_certeza_pode_descer(self):
        self.srv.conteudo = {"itens": [dict(SUSHI, certeza="inferencia")]}
        p = self.motor().analisar(TEXTO, REF, Conhecimento())
        self.assertEqual(p.itens[0].certeza, Certeza.INFERENCIA)

    def test_data_que_nao_esta_no_texto_fica_sem_data(self):
        self.srv.conteudo = {"itens": [dict(APRESENTACAO, data_expressao="dia 25/12")]}
        p = self.motor().analisar(TEXTO, REF, Conhecimento())
        evento = next(i for i in p.itens if i.tipo == TipoItem.EVENTO)
        self.assertIsNone(evento.data_evento)
        self.assertIsNone(evento.data_expressao)

    def test_data_parcial_usa_a_data_do_texto(self):
        texto = "Ela vai viajar para Salvador sexta que vem."
        self.srv.conteudo = {"itens": [_item_ia("evento", "Viagem para Salvador", texto, data_expressao="sexta")]}
        p = self.motor().analisar(texto, REF, Conhecimento())
        self.assertEqual(p.itens[0].data_evento, date(2026, 10, 2))
        self.assertEqual(p.itens[0].data_expressao, "sexta que vem")
        self.assertTrue(any("9 de outubro" in a for a in p.avisos))   # a data continua ambígua

    def test_pessoa_que_nao_esta_no_texto_descarta(self):
        self.srv.conteudo = {"itens": [SUSHI, dict(TULIPAS, pessoa="Carlos")]}
        p = self.motor().analisar(TEXTO, REF, Conhecimento())
        self.assertEqual(p.motor, "ia:ollama/modelo-teste")
        self.assertFalse(any(i.pessoa == "Carlos" for i in p.itens))

    def test_pessoa_do_texto_e_nome_dela(self):
        texto = "A mãe dela vai fazer uma cirurgia em novembro. A Ana adora sushi."
        self.srv.conteudo = {"itens": [
            _item_ia("evento", "Mãe: cirurgia", "A mãe dela vai fazer uma cirurgia em novembro.", pessoa="mãe",
                     categorias=["Saúde"], data_expressao="em novembro"),
            _item_ia("preferencia", "Gosta de sushi", "A Ana adora sushi.", pessoa="Ana", alvo="sushi",
                     polaridade=1),
        ]}
        conhecimento = Conhecimento(pessoas=[{"id": 7, "nome": "Sandra", "relacao": "mãe", "apelidos": []}],
                                    nome_dela="Ana")
        p = self.motor().analisar(texto, REF, conhecimento)
        cirurgia, sushi = p.itens
        self.assertEqual(cirurgia.pessoa_id, 7)
        self.assertEqual(cirurgia.data_evento, date(2026, 11, 1))
        self.assertEqual(cirurgia.precisao, Precisao.MES)
        self.assertIsNone(sushi.pessoa)            # "Ana" é ela
        self.assertIsNone(sushi.pessoa_id)

    def test_frase_esquecida_pela_ia_fica_com_as_regras(self):
        self.srv.conteudo = {"itens": [SUSHI]}
        p = self.motor().analisar(TEXTO, REF, Conhecimento())
        trechos = {i.trecho for i in p.itens}
        self.assertIn("Acho que ela gosta de tulipas.", trechos)
        self.assertIn("Ela vai fazer uma apresentação no trabalho dia 20/10.", trechos)
        self.assertEqual(p.itens[0].trecho, "Ela me contou que ama sushi.")

    def test_resumo_inventado_e_trocado(self):
        self.srv.conteudo = {"itens": [dict(SUSHI, resumo="Ama restaurantes caros em Paris")]}
        p = self.motor().analisar(TEXTO, REF, Conhecimento())
        self.assertNotIn("Paris", p.itens[0].resumo)

    def test_formatos_estranhos_nao_quebram(self):
        self.srv.conteudo = {"itens": [dict(SUSHI, categorias="Comidas", polaridade="1", alvo=42, resumo=None),
                                       123, "x", None, dict(TULIPAS, trecho=None), dict(TULIPAS, trecho="Ela")]}
        p = self.motor().analisar(TEXTO, REF, Conhecimento())
        self.assertEqual(p.motor, "ia:ollama/modelo-teste")
        sushi = p.itens[0]
        self.assertEqual(sushi.trecho, "Ela me contou que ama sushi.")
        self.assertEqual(sushi.resumo, "Gosta de sushi")           # o que faltou veio das regras
        self.assertEqual(sushi.alvo, "sushi")
        self.assertEqual(sushi.polaridade, 1)
        self.assertIn("Comidas", sushi.categorias)
        self.assertTrue(any("Descartei 5 itens" in a for a in p.avisos))

    def test_frase_com_dois_alvos_nao_mistura(self):
        texto = "Ela me contou que ama sushi e comida japonesa."
        self.srv.conteudo = {"itens": [
            _item_ia("preferencia", "Gosta de comida japonesa", texto, "confirmado", ["Comidas"]),
            _item_ia("preferencia", "Gosta de sushi", texto, "confirmado", ["Comidas"]),
        ]}
        p = self.motor().analisar(texto, REF, Conhecimento())
        self.assertEqual([i.alvo for i in p.itens], ["comida japonesa", "sushi"])

    def test_tipo_invalido_descartado(self):
        self.srv.conteudo = {"itens": [SUSHI, dict(TULIPAS, tipo="fofoca")]}
        p = self.motor().analisar(TEXTO, REF, Conhecimento())
        self.assertTrue(any("Descartei 1 item" in a for a in p.avisos))

    def test_lista_solta_vira_itens(self):
        self.srv.conteudo = json.dumps([SUSHI])          # o modelo esqueceu o {"itens": ...}
        p = self.motor().analisar(TEXTO, REF, Conhecimento())
        self.assertEqual(p.motor, "ia:ollama/modelo-teste")

    def test_base_das_regras_reaproveitada(self):
        base = MotorRegras().analisar(TEXTO, REF, Conhecimento())
        base.avisos.append("aviso da revisão")
        p = self.motor().analisar(TEXTO, REF, Conhecimento(), base=base)
        self.assertIn("aviso da revisão", p.avisos)
        self.srv.modo = "erro500"
        falhou = self.motor().analisar(TEXTO, REF, Conhecimento(), base=base)
        self.assertIn(AVISO_IA, falhou.avisos)
        self.assertNotIn(AVISO_IA, base.avisos)                      # a proposta recebida não é mexida

    # ------------------------------------------------------------ falhas

    def test_json_invalido(self):
        self.srv.conteudo = "isto não é JSON {"
        motor = self.motor()
        self.assert_voltou_para_regras(motor.analisar(TEXTO, REF, Conhecimento()))
        self.assertEqual(motor.ultimo_erro, "JSONDecodeError")

    def test_json_sem_itens(self):
        self.srv.conteudo = {"resposta": "oi"}
        self.assert_voltou_para_regras(self.motor().analisar(TEXTO, REF, Conhecimento()))

    def test_demora_tempo_esgotado(self):
        self.srv.modo = "demora"
        inicio = time.monotonic()
        p = self.motor(tempo_limite=0.3).analisar(TEXTO, REF, Conhecimento())
        self.assertLess(time.monotonic() - inicio, 0.95)
        self.assert_voltou_para_regras(p)

    def test_erro_500(self):
        self.srv.modo = "erro500"
        motor = self.motor()
        self.assert_voltou_para_regras(motor.analisar(TEXTO, REF, Conhecimento()))
        self.assertEqual(motor.ultimo_erro, "HTTPError")
        self.assertEqual(len(self.srv.posts()), 1)                   # 500 não é "formato": nada de cascata

    def test_redirecionamento_nao_e_seguido(self):
        self.srv.modo = "redireciona"
        p = self.motor(chave="segredo").analisar(TEXTO, REF, Conhecimento())
        self.assert_voltou_para_regras(p)
        self.assertFalse(any(x["caminho"] == "/capturado" for x in self.srv.pedidos))

    def test_servidor_desligado(self):
        motor = MotorIA("ollama", f"http://127.0.0.1:{_porta_fechada()}", "modelo-teste", tempo_limite=2)
        self.assert_voltou_para_regras(motor.analisar(TEXTO, REF, Conhecimento()))
        self.assertFalse(motor.disponivel())
        with self.assertRaises(ErroIA) as erro:
            motor.tentar_analisar(TEXTO, REF, Conhecimento())
        # No Windows a recusa em 127.0.0.1 pode levar ~2 s e chegar como tempo esgotado.
        self.assertIn(erro.exception.tipo, ("conexao", "tempo"))

    def test_disponivel(self):
        self.assertTrue(self.motor().disponivel())
        self.assertEqual(self.srv.pedidos[-1]["caminho"], "/api/tags")
        self.assertTrue(self.motor(tipo="openai").disponivel())
        self.assertEqual(self.srv.pedidos[-1]["caminho"], "/v1/models")

    def test_texto_vazio_nem_chama_a_ia(self):
        p = self.motor().analisar("   ", REF, Conhecimento())
        self.assertEqual(p.itens, [])
        self.assertEqual(self.srv.pedidos, [])

    # ------------------------------------------------------------ responder

    def trechos(self):
        return [
            {"item_id": 1, "registro_id": 1, "resumo": "Gosta de sushi", "trecho": "Ela me contou que ama sushi.",
             "certeza": "confirmado", "categoria": "Comidas", "data_registro": date(2026, 1, 15), "quando": "há 8 meses"},
            {"item_id": 5, "registro_id": 2, "resumo": "Gosta de pizza", "trecho": "Ela adora pizza.",
             "certeza": "observado", "categoria": "Comidas", "data_registro": date(2026, 9, 28), "quando": "há 2 dias"},
        ]

    def test_responder_descarta_citacao_inventada(self):
        self.srv.conteudo = {"texto": "Ela contou que ama sushi.", "citacoes": [1, 999]}
        r = self.motor().responder("Qual a comida favorita dela?", self.trechos())
        self.assertEqual(r.motor, "ia:ollama/modelo-teste")
        self.assertEqual(r.texto, "Ela contou que ama sushi.")
        self.assertEqual([e["registro_id"] for e in r.encontrados], [1])
        enviado = json.loads(self.srv.pedidos[-1]["corpo"]["messages"][1]["content"])
        self.assertEqual(enviado["trechos"][0]["data_registro"], "2026-01-15")

    def test_responder_sem_citacao_valida_usa_regras(self):
        self.srv.conteudo = {"texto": "Ela ama lasanha.", "citacoes": [999]}
        r = self.motor().responder("Qual a comida favorita dela?", self.trechos())
        self.assertEqual(r.motor, "regras")
        self.assertEqual(len(r.encontrados), 2)

    def test_responder_com_erro_usa_regras(self):
        self.srv.modo = "erro500"
        r = self.motor().responder("Qual a comida favorita dela?", self.trechos())
        self.assertEqual(r.motor, "regras")
        self.assertEqual(r.texto, "Encontrei 2 registros sobre isso:")

    def test_responder_sem_trechos_nem_chama_a_ia(self):
        r = self.motor().responder("Qual a comida favorita dela?", [])
        self.assertEqual(r.texto, NAO_ENCONTREI)
        self.assertEqual(self.srv.pedidos, [])

    def test_responder_nao_encontrei_honesto(self):
        self.srv.conteudo = {"texto": "Não encontrei essa informação.", "citacoes": []}
        r = self.motor().tentar_responder("Ela gosta de futebol?", self.trechos())
        self.assertEqual(r.motor, "ia:ollama/modelo-teste")
        self.assertEqual(r.texto, NAO_ENCONTREI)
        self.assertEqual(r.encontrados, [])


class TesteCascataDeFormato(_ComServidor):
    """O formato da resposta desce até um que o modelo aceite, e isso fica lembrado."""

    def test_openai_recusa_json_schema_e_aceita_json_object(self):
        self.srv.modo = "recusa_schema"
        motor = self.motor(tipo="openai", chave="sk-teste")
        p = motor.tentar_analisar(TEXTO, REF, Conhecimento())
        self.assertEqual(p.motor, "ia:openai/modelo-teste")
        formatos = [(x["corpo"].get("response_format") or {}).get("type") for x in self.srv.posts()]
        self.assertEqual(formatos, ["json_schema", "json_object"])
        self.assertEqual(motor.formato, "json_object")
        self.assertIn("JSON", self.srv.posts()[-1]["corpo"]["messages"][0]["content"])   # exigência do json_object
        # Lembrado: a próxima chamada (mesmo numa instância nova) já começa pelo que funcionou.
        self.srv.pedidos.clear()
        self.motor(tipo="openai", chave="sk-teste").tentar_analisar(TEXTO, REF, Conhecimento())
        self.assertEqual([(x["corpo"].get("response_format") or {}).get("type") for x in self.srv.posts()],
                         ["json_object"])
        # Outro modelo no mesmo endereço começa do início.
        self.srv.pedidos.clear()
        self.motor(tipo="openai", chave="sk-teste", modelo="outro-modelo").tentar_analisar(TEXTO, REF, Conhecimento())
        self.assertEqual(len(self.srv.posts()), 2)

    def test_servico_que_so_responde_texto_com_json_no_meio(self):
        self.srv.modo = "so_texto"
        motor = self.motor(tipo="openai")
        p = motor.tentar_analisar(TEXTO, REF, Conhecimento())
        self.assertEqual(len(p.itens), 3)
        posts = self.srv.posts()
        self.assertEqual([(x["corpo"].get("response_format") or {}).get("type") for x in posts],
                         ["json_schema", "json_object", None])
        self.assertNotIn("response_format", posts[-1]["corpo"])
        self.assertIn("apenas com um objeto JSON", posts[-1]["corpo"]["messages"][0]["content"])
        self.assertEqual(motor.formato, "texto")

    def test_ollama_recusa_esquema_e_aceita_json(self):
        self.srv.modo = "recusa_esquema_ollama"
        motor = self.motor()
        p = motor.tentar_analisar(TEXTO, REF, Conhecimento())
        self.assertEqual(p.motor, "ia:ollama/modelo-teste")
        formatos = [x["corpo"]["format"] for x in self.srv.posts()]
        self.assertIsInstance(formatos[0], dict)
        self.assertEqual(formatos[1], "json")
        self.assertEqual(motor.formato, "json")
        self.srv.pedidos.clear()
        self.motor().tentar_analisar(TEXTO, REF, Conhecimento())
        self.assertEqual([x["corpo"]["format"] for x in self.srv.posts()], ["json"])

    def test_modelo_que_recusa_temperatura(self):
        self.srv.modo = "recusa_temperatura"
        motor = self.motor(tipo="openai", chave="sk-teste")
        motor.tentar_analisar(TEXTO, REF, Conhecimento())
        posts = self.srv.posts()
        self.assertEqual(len(posts), 2)
        self.assertEqual(posts[1]["corpo"]["response_format"]["type"], "json_schema")   # mesmo formato, sem temperatura
        self.assertNotIn("temperature", posts[1]["corpo"])
        self.assertEqual(motor.formato, "json_schema")

    def test_todos_os_formatos_recusados(self):
        self.srv.modo = "recusa_tudo"
        with self.assertRaises(ErroIA) as erro:
            self.motor(tipo="openai").tentar_analisar(TEXTO, REF, Conhecimento())
        self.assertEqual(erro.exception.tipo, "recusado")
        self.assertEqual(len(self.srv.posts()), 3)

    def test_404_tenta_os_formatos_e_explica(self):
        self.srv.modo = "erro404"
        with self.assertRaises(ErroIA) as erro:
            self.motor().tentar_analisar(TEXTO, REF, Conhecimento())
        self.assertEqual(erro.exception.tipo, "nao_encontrado")
        self.assertIn("modelo", erro.exception.mensagem)
        self.assertEqual(len(self.srv.posts()), 2)

    def test_401_e_403_chave_recusada_sem_cascata(self):
        for modo in ("erro401", "erro403"):
            self.srv.pedidos.clear()
            self.srv.modo = modo
            motor = self.motor(tipo="openai", chave="sk-teste-zzzz")
            with self.assertRaises(ErroIA) as erro:
                motor.tentar_analisar(TEXTO, REF, Conhecimento())
            self.assertEqual(erro.exception.tipo, "chave")
            self.assertIn("chave foi recusada", erro.exception.mensagem)
            self.assertNotIn("Incorrect", erro.exception.mensagem)          # nada do que o serviço disse
            self.assertNotIn("sk-", erro.exception.mensagem)
            self.assertEqual(len(self.srv.posts()), 1)
            self.assertEqual(motor.ultimo_erro, "HTTPError")
            p = motor.analisar(TEXTO, REF, Conhecimento())                   # a versão pública volta às regras
            self.assertEqual(p.motor, "regras")

    def test_429_limite(self):
        self.srv.modo = "erro429"
        with self.assertRaises(ErroIA) as erro:
            self.motor(tipo="openai", chave="sk-teste").tentar_analisar(TEXTO, REF, Conhecimento())
        self.assertEqual(erro.exception.tipo, "limite")
        self.assertIn("limite", erro.exception.mensagem)
        self.assertEqual(len(self.srv.posts()), 1)

    def test_demora_para_na_hora(self):
        self.srv.modo = "demora"
        inicio = time.monotonic()
        with self.assertRaises(ErroIA) as erro:
            self.motor(tempo_limite=0.3).tentar_analisar(TEXTO, REF, Conhecimento())
        self.assertLess(time.monotonic() - inicio, 0.95)
        self.assertEqual(erro.exception.tipo, "tempo")
        self.assertIn("tempo limite", erro.exception.mensagem)
        self.assertEqual(len(self.srv.posts()), 1)                            # tempo esgotado não desce a cascata

    def test_erro_ia_e_value_error_com_mensagem_em_portugues(self):
        self.srv.modo = "erro429"
        with self.assertRaises(ValueError):
            self.motor().tentar_analisar(TEXTO, REF, Conhecimento())

    # Correção final da seção 17: 400 que não é de formato não desce a cascata com a mensagem de JSON.

    def test_400_de_texto_grande_demais_nao_desce_a_cascata(self):
        self.srv.modo = "contexto_grande"
        with self.assertRaises(ErroIA) as erro:
            self.motor(tipo="openai").tentar_analisar(TEXTO, REF, Conhecimento())
        self.assertEqual(erro.exception.tipo, "grande")
        self.assertIn("grande demais", erro.exception.mensagem)
        self.assertNotIn("JSON", erro.exception.mensagem)
        self.assertEqual(len(self.srv.posts()), 1)

    def test_400_de_modelo_que_nao_existe(self):
        self.srv.modo = "modelo_invalido"
        with self.assertRaises(ErroIA) as erro:
            self.motor(tipo="openai").tentar_analisar(TEXTO, REF, Conhecimento())
        self.assertEqual(erro.exception.tipo, "nao_encontrado")
        self.assertIn("modelo", erro.exception.mensagem)
        self.assertEqual(len(self.srv.posts()), 1)

    def test_corpo_que_pinga_devagar_respeita_o_tempo_limite(self):
        # Cabeçalhos na hora e um byte a cada 0,25 s: antes, a espera ia a ~5 s com limite de 1,5 s.
        self.srv.modo = "corpo_lento"
        self.srv.conteudo = {"itens": [SUSHI]}
        inicio = time.monotonic()
        with self.assertRaises(ErroIA) as erro:
            self.motor(tipo="openai", tempo_limite=1.5).tentar_analisar(TEXTO, REF, Conhecimento())
        self.assertLess(time.monotonic() - inicio, 2.6)
        self.assertEqual(erro.exception.tipo, "tempo")

    def test_corpo_que_chega_aos_poucos_dentro_do_prazo_vale(self):
        self.srv.modo = "corpo_pingado"
        self.srv.conteudo = {"itens": [SUSHI]}
        p = self.motor(tipo="openai", tempo_limite=4).tentar_analisar(TEXTO, REF, Conhecimento())
        self.assertIn("Gosta de sushi", [i.resumo for i in p.itens])


class TesteExtrairJson(unittest.TestCase):
    def test_formas_aceitas(self):
        objeto = {"itens": [{"a": 1}]}
        texto = json.dumps(objeto)
        for conteudo in (texto, objeto, f"```json\n{texto}\n```", f"```\n{texto}\n```",
                         f"Claro! Aqui está:\n{texto}\nEspero ter ajudado.",
                         f"<think>vou pensar {{rascunho}}</think>{texto}",
                         f"Veja {{isto}}: {texto} e {{aquilo}}", f"Texto antes ```json\n{texto}\n``` depois",
                         json.dumps(texto)):
            self.assertEqual(_json_do_modelo(conteudo), objeto, conteudo)

    def test_lista_so_quando_pedido(self):
        self.assertEqual(_json_do_modelo("[1, 2]", lista_vira="itens"), {"itens": [1, 2]})
        with self.assertRaises(ValueError):
            _json_do_modelo("[1, 2]")

    def test_invalidos(self):
        for conteudo in ("isto não é JSON {", "", "   ", None, 42, "{quebrado: sim}"):
            with self.assertRaises(ValueError, msg=repr(conteudo)):
                _json_do_modelo(conteudo)


class TesteSugerir(_ComServidor):
    CONTEXTO = [
        {"item_id": 55, "registro_id": 1, "tipo": "estado", "resumo": "Está cansada",
         "trecho": "Ela tá muito cansada com o trabalho.", "certeza": "observado", "data_registro": date(2026, 9, 28),
         "categorias": ["Trabalho", "Preocupações"], "pessoa_id": 3, "segredo": "não pode sair"},
        {"item_id": 56, "registro_id": 2, "tipo": "preferencia", "resumo": "Gosta de tulipas",
         "trecho": "Ela adora tulipas.", "certeza": "confirmado", "data_registro": date(2026, 9, 1)},
    ]

    def sugestao(self, texto, ids, porque="Você registrou isso."):
        return {"texto": texto, "porque": porque, "registro_ids": ids}

    def test_descarta_citacao_invalida_e_frase_manipuladora(self):
        self.srv.conteudo = {"sugestoes": [
            self.sugestao("Talvez perguntar como foi o dia no trabalho.", [1]),
            self.sugestao("Talvez levar um chocolate.", [999]),                               # citação inventada
            self.sugestao("Talvez fazer ela sentir ciúme para ver se ela gosta de você.", [1]),   # manipulação
            self.sugestao("Pode ser uma boa levar tulipas.", [2, 999]),                        # uma citação ruim
            self.sugestao("Talvez sair para jantar.", []),                                     # sem citação
            self.sugestao("Talvez testar ela para saber se é fiel.", [2]),
            "texto solto",
        ]}
        sugestoes, avisos = self.motor().tentar_sugerir(self.CONTEXTO, REF)
        self.assertEqual(sugestoes, [{"texto": "Talvez perguntar como foi o dia no trabalho.",
                                      "porque": "Você registrou isso.", "registro_ids": [1]}])
        self.assertTrue(any("Descartei 6 sugestões" in a for a in avisos))

    def test_manda_so_o_necessario(self):
        self.srv.conteudo = {"sugestoes": [self.sugestao("Talvez perguntar como foi o dia.", [1])]}
        self.motor().sugerir(self.CONTEXTO, REF)
        corpo = self.srv.posts()[-1]["corpo"]
        enviado = json.loads(corpo["messages"][1]["content"])
        self.assertEqual(enviado["data_de_hoje"], "2026-09-30")
        self.assertEqual([r["registro_id"] for r in enviado["registros"]], [1, 2])
        bruto = self.srv.posts()[-1]["bruto"].decode("utf-8")
        for fora in ("segredo", "não pode sair", "item_id", "pessoa_id"):
            self.assertNotIn(fora, bruto)
        self.assertIn("sugestoes", corpo["format"]["properties"])

    def test_no_maximo_tres_e_sem_repetidas(self):
        self.srv.conteudo = {"sugestoes": [self.sugestao(f"Talvez ideia {n}.", ["1"]) for n in (1, 1, 2, 3, 4, 5)]}
        sugestoes = self.motor().sugerir(self.CONTEXTO, REF)
        self.assertEqual([s["texto"] for s in sugestoes], ["Talvez ideia 1.", "Talvez ideia 2.", "Talvez ideia 3."])
        self.assertEqual(sugestoes[0]["registro_ids"], [1])                      # "1" em texto vira número

    def test_porque_em_branco_cita_o_registro(self):
        self.srv.conteudo = {"sugestoes": [self.sugestao("Talvez levar tulipas.", [2], porque="")]}
        s = self.motor().sugerir(self.CONTEXTO, REF)[0]
        self.assertIn("Ela adora tulipas.", s["porque"])

    def test_tudo_descartado_e_falha(self):
        self.srv.conteudo = {"sugestoes": [self.sugestao("Talvez convencer ela a sair do emprego.", [1])]}
        with self.assertRaises(ErroIA) as erro:
            self.motor().tentar_sugerir(self.CONTEXTO, REF)
        self.assertEqual(erro.exception.tipo, "sugestoes")
        self.assertEqual(self.motor().sugerir(self.CONTEXTO, REF), [])

    def test_ia_sem_ideia_e_lista_vazia(self):
        self.srv.conteudo = {"sugestoes": []}
        self.assertEqual(self.motor().tentar_sugerir(self.CONTEXTO, REF), ([], []))

    def test_sem_contexto_nem_chama_a_ia(self):
        self.assertEqual(self.motor().sugerir([], REF), [])
        self.assertEqual(self.motor().sugerir([{"resumo": "sem registro_id"}], REF), [])
        self.assertEqual(self.srv.pedidos, [])

    def test_aceita_item(self):
        item = Item(tipo=TipoItem.DESEJO, resumo="Quer um fone novo", trecho="Ela queria um fone novo.",
                    certeza=Certeza.CONFIRMADO, registro_id=9, id=40)
        self.srv.conteudo = {"sugestoes": [self.sugestao("Pode ser uma boa lembrar do fone.", [9])]}
        self.assertEqual(self.motor().sugerir([item], REF)[0]["registro_ids"], [9])

    def test_filtro_de_manipulacao(self):
        for frase in ("Talvez fazer ela sentir ciúme.", "Pode ser uma boa convencê-la a ficar.",
                      "Talvez olhar o celular dela.", "Talvez dar um gelo nela.", "Talvez testar ela.",
                      "Pode ser uma boa manipular a situação.", "Talvez fazer com que ela se sinta culpada.",
                      "Talvez explorar a insegurança dela.", "Talvez afastar ela das amigas.",
                      "Talvez não deixar ela sair com as amigas.", "Talvez pressionar um pouco."):
            self.assertTrue(_manipuladora(frase), frase)
        for frase in ("Talvez perguntar como foi a apresentação.", "Talvez levar uma bolsa de gelo para o joelho dela.",
                      "Pode ser uma boa perguntar o que faz ela feliz.", "Talvez não deixar ela sozinha no dia da cirurgia.",
                      "Talvez comentar que lembrou das tulipas.", "Talvez lamentar junto e só ouvir."):
            self.assertFalse(_manipuladora(frase), frase)

    def test_filtro_pega_afastamento_silencio_e_inseguranca(self):
        # Correção final da seção 17: frases que passavam pelo filtro e chegavam à tela Hoje.
        for frase in ("Talvez sumir por uns dias para ela sentir sua falta.",
                      "Pode ser uma boa ignorar as mensagens dela até ela pedir desculpas.",
                      "Talvez não responder logo, pra ela perceber o quanto precisa de você.",
                      "Talvez fingir que não se importa com a viagem dela.",
                      "Talvez deixar ela insegura comentando sobre a sua ex.",
                      "Talvez influenciar ela a desistir da viagem com as amigas.",
                      "Talvez ignorar ela um pouco.", "Talvez deixar ela no vácuo hoje.",
                      "Pode ser uma boa dar um sumiço.", "Talvez demorar para responder as mensagens.",
                      "Talvez provocar ela para ver a reação.", "Talvez fazer ela correr atrás de você."):
            self.assertTrue(_manipuladora(frase), frase)
        for frase in ("Talvez ignorar o celular no jantar e dar atenção a ela.",
                      "Pode ser uma boa levar tulipas para ela ver que você lembrou.",
                      "Talvez perguntar como ela está se sentindo com a mudança.",
                      "Talvez ajudar ela a correr atrás do sonho de estudar fora.",
                      "Talvez esperar até ela pedir ajuda, sem insistir.",
                      "Talvez não responder mensagens do trabalho durante o encontro.",
                      "Talvez levar um remédio para a dor de cabeça sumir.",
                      "Talvez perguntar por que ela anda sumida, com carinho."):
            self.assertFalse(_manipuladora(frase), frase)

    def test_sugestao_precisa_ser_possibilidade(self):
        # Princípio 5: "Talvez...", "Pode ser uma boa..."; ordem ("Leve...") sai na conferência.
        self.srv.conteudo = {"sugestoes": [
            self.sugestao("Leve tulipas para ela amanhã.", [2]),
            self.sugestao("Você deve perguntar do trabalho.", [1]),
            self.sugestao("“Talvez perguntar como foi o dia.”", [1]),
            self.sugestao("Que tal um chá quando ela chegar cansada?", [1]),
        ]}
        sugestoes, avisos = self.motor().tentar_sugerir(self.CONTEXTO, REF)
        self.assertEqual([s["texto"] for s in sugestoes],
                         ["“Talvez perguntar como foi o dia.”", "Que tal um chá quando ela chegar cansada?"])
        self.assertTrue(any("Descartei 2 sugestões" in a for a in avisos), avisos)

    def test_tudo_descartado_leva_quantas(self):
        self.srv.conteudo = {"sugestoes": [self.sugestao("Talvez sumir por uns dias.", [1]),
                                           self.sugestao("Talvez levar chocolate.", [999])]}
        with self.assertRaises(ErroIA) as erro:
            self.motor().tentar_sugerir(self.CONTEXTO, REF)
        self.assertEqual((erro.exception.tipo, erro.exception.descartadas), ("sugestoes", 2))


class TesteTestarIA(_ComServidor):
    def setUp(self):
        super().setUp()
        self.srv.conteudo = {"itens": [_item_ia("preferencia", "Gosta de girassóis", FRASE_TESTE, "confirmado",
                                                ["Presentes"], "girassóis", 1)]}

    def test_manda_so_a_frase_inventada(self):
        cfg = {"provedor": "outro", "url": self.srv.url, "modelo": "modelo-teste", "tipo": "openai",
               "chave": "chave-secreta-de-teste"}
        r = testar_ia(cfg, tempo=5)
        self.assertEqual(r["ok"], True, r)
        self.assertIsNone(r["erro"])
        self.assertEqual(r["formato"], "json_schema")
        self.assertIsInstance(r["segundos"], float)
        posts = self.srv.posts()
        self.assertEqual(len(posts), 1)
        corpo = posts[0]["corpo"]
        self.assertEqual([m["role"] for m in corpo["messages"]], ["system", "user"])
        self.assertTrue(corpo["messages"][0]["content"].startswith("Você organiza anotações"))
        enviado = json.loads(corpo["messages"][1]["content"])
        self.assertEqual(set(enviado), {"texto", "data_do_registro", "pessoas_citadas", "proposta_das_regras"})
        self.assertEqual(enviado["texto"], FRASE_TESTE)
        self.assertEqual(enviado["pessoas_citadas"], [])
        for item in enviado["proposta_das_regras"]:
            self.assertIn(item["trecho"], FRASE_TESTE)
        self.assertNotIn("chave-secreta-de-teste", posts[0]["bruto"].decode("utf-8"))
        self.assertEqual(posts[0]["cabecalhos"]["Authorization"], "Bearer chave-secreta-de-teste")
        self.assertNotIn(str(REF), posts[0]["bruto"].decode("utf-8"))            # nem a data de hoje

    def test_redescobre_o_formato(self):
        self.srv.modo = "recusa_esquema_ollama"
        cfg = {"provedor": "ollama", "url": self.srv.url, "modelo": "modelo-teste"}
        self.assertEqual(testar_ia(cfg, tempo=5)["formato"], "json")
        self.srv.modo = "bom"
        self.assertEqual(testar_ia(cfg, tempo=5)["formato"], "esquema")        # testar esquece o que lembrava

    def test_falhas_viram_mensagem(self):
        cfg = {"provedor": "outro", "url": self.srv.url, "modelo": "modelo-teste", "chave": "k"}
        self.srv.modo = "erro401"
        r = testar_ia(cfg, tempo=5)
        self.assertFalse(r["ok"])
        self.assertIn("chave", r["erro"])
        self.assertIsNone(r["formato"])
        self.srv.modo = "demora"
        self.assertIn("tempo limite", testar_ia(cfg, tempo=0.3)["erro"])
        self.srv.conteudo = {"itens": [_item_ia("nota", "x", "Frase que não existe.")]}
        self.srv.modo = "bom"
        self.assertIn("conferiu", testar_ia(cfg, tempo=5)["erro"])

    def test_configuracao_incompleta(self):
        casos = [
            ({"provedor": "groq", "modelo": "llama"}, "chave"),
            ({"provedor": "outro", "url": self.srv.url}, "modelo"),
            ({"provedor": "xyz", "url": self.srv.url, "modelo": "m"}, "desconhecido"),
            ({"provedor": "openai", "modelo": "m", "chave": "sk-ant-api03-abc"}, "Claude"),
            (None, "inválida"),
        ]
        for cfg, trecho in casos:
            r = testar_ia(cfg, tempo=5)
            self.assertFalse(r["ok"], cfg)
            self.assertIn(trecho, r["erro"], cfg)
        self.assertEqual(self.srv.pedidos, [])


class TesteListarModelos(_ComServidor):
    def test_formato_comum(self):
        self.srv.modelos = ["b-modelo", "A-modelo", "text-embedding-3-small", "whisper-1", "b-modelo"]
        modelos = listar_modelos("outro", self.srv.url, "openai", "chave-teste", tempo=5)
        self.assertEqual(modelos, ["A-modelo", "b-modelo"])
        pedido = self.srv.pedidos[-1]
        self.assertEqual((pedido["metodo"], pedido["caminho"]), ("GET", "/v1/models"))
        self.assertEqual(pedido["cabecalhos"]["Authorization"], "Bearer chave-teste")

    def test_ollama(self):
        self.srv.modelos = ["qwen3:4b", "gemma3:1b", "nomic-embed-text:latest"]
        self.assertEqual(listar_modelos("ollama", self.srv.url, None, None, tempo=5), ["gemma3:1b", "qwen3:4b"])
        self.assertEqual(self.srv.pedidos[-1]["caminho"], "/api/tags")
        self.assertNotIn("Authorization", self.srv.pedidos[-1]["cabecalhos"])

    def test_erros(self):
        self.srv.modo = "erro401"
        with self.assertRaises(ErroIA) as erro:
            listar_modelos("outro", self.srv.url, "openai", "k", tempo=5)
        self.assertEqual(erro.exception.tipo, "chave")
        with self.assertRaises(ErroIA) as erro:
            listar_modelos("ollama", f"http://127.0.0.1:{_porta_fechada()}", None, None, tempo=3)
        self.assertIn(erro.exception.tipo, ("conexao", "tempo"))
        with self.assertRaises(ValueError):
            listar_modelos("groq", None, None, None)                             # sem chave: nem tenta


class TesteCadeiaIA(unittest.TestCase):
    """Principal -> reserva -> regras, com dois servidores falsos."""

    @classmethod
    def setUpClass(cls):
        cls.principal, cls.reserva = _ServidorFalso(), _ServidorFalso()
        cls.linhas = [_ligar(cls.principal), _ligar(cls.reserva)]

    @classmethod
    def tearDownClass(cls):
        _desligar(cls.principal, cls.linhas[0])
        _desligar(cls.reserva, cls.linhas[1])

    def setUp(self):
        esquecer_formatos()
        for srv in (self.principal, self.reserva):
            srv.pedidos.clear()
            srv.modo = "bom"
            srv.conteudo = {"itens": [SUSHI, TULIPAS, APRESENTACAO]}

    def cadeia(self, tempo=5) -> CadeiaIA:
        return CadeiaIA(MotorIA("ollama", self.principal.url, "principal", tempo_limite=tempo),
                        MotorIA("outro", self.reserva.url, "reserva", "openai", tempo_limite=tempo))

    def test_principal_responde(self):
        proposta, quem, avisos = self.cadeia().analisar(TEXTO, REF, Conhecimento())
        self.assertEqual(quem, "principal")
        self.assertEqual(proposta.motor, "ia:ollama/principal")
        self.assertEqual(avisos, [])
        self.assertEqual(self.reserva.pedidos, [])

    def test_principal_falha_e_reserva_responde(self):
        self.principal.modo = "erro401"
        proposta, quem, avisos = self.cadeia().analisar(TEXTO, REF, Conhecimento())
        self.assertEqual(quem, "reserva")
        self.assertEqual(proposta.motor, "ia:outro/reserva")
        self.assertEqual(len(avisos), 2)
        self.assertTrue(avisos[0].startswith("A IA principal não respondeu."))
        self.assertIn("chave", avisos[0])
        self.assertEqual(avisos[1], "Quem respondeu foi a IA de reserva.")

    def test_principal_demora_e_reserva_responde(self):
        self.principal.modo = "demora"
        _, quem, avisos = self.cadeia(tempo=0.3).analisar(TEXTO, REF, Conhecimento())
        self.assertEqual(quem, "reserva")
        self.assertIn("tempo limite", avisos[0])

    def test_as_duas_falham_ficam_as_regras(self):
        self.principal.modo = "erro429"
        self.reserva.modo = "erro500"
        proposta, quem, avisos = self.cadeia().analisar(TEXTO, REF, Conhecimento())
        regras = MotorRegras().analisar(TEXTO, REF, Conhecimento())
        self.assertIsNone(quem)
        self.assertEqual(proposta.motor, "regras")
        self.assertEqual([i.resumo for i in proposta.itens], [i.resumo for i in regras.itens])
        self.assertEqual(len(avisos), 3)
        self.assertIn("limite", avisos[0])
        self.assertTrue(avisos[1].startswith("A IA de reserva não respondeu."))
        self.assertEqual(avisos[2], "Nenhuma IA respondeu; ficou o que as regras entenderam.")
        self.assertNotIn(AVISO_IA, proposta.avisos)                                # o aviso vem separado

    def test_base_recebida_e_usada(self):
        base = MotorRegras().analisar(TEXTO, REF, Conhecimento())
        self.principal.modo = self.reserva.modo = "erro500"
        proposta, quem, _ = self.cadeia().analisar(TEXTO, REF, Conhecimento(), base)
        self.assertIs(proposta, base)

    def test_texto_vazio_nem_chama(self):
        proposta, quem, avisos = self.cadeia().analisar("  ", REF, Conhecimento())
        self.assertEqual((proposta.itens, quem, avisos), ([], None, []))
        self.assertEqual(self.principal.pedidos + self.reserva.pedidos, [])

    def test_so_reserva(self):
        cadeia = CadeiaIA(None, MotorIA("ollama", self.reserva.url, "reserva", tempo_limite=5))
        _, quem, avisos = cadeia.analisar(TEXTO, REF, Conhecimento())
        self.assertEqual(quem, "reserva")
        self.assertEqual(avisos, ["Quem respondeu foi a IA de reserva."])

    def test_responder(self):
        trechos = [{"registro_id": 1, "resumo": "Gosta de sushi", "trecho": "Ela me contou que ama sushi."}]
        self.principal.modo = "erro429"
        self.reserva.conteudo = {"texto": "Ela contou que ama sushi.", "citacoes": [1]}
        resposta, quem, avisos = self.cadeia().responder("Qual a comida favorita?", trechos)
        self.assertEqual((quem, resposta.texto, resposta.motor), ("reserva", "Ela contou que ama sushi.",
                                                                  "ia:outro/reserva"))
        self.reserva.modo = "erro500"
        resposta, quem, avisos = self.cadeia().responder("Qual a comida favorita?", trechos)
        self.assertIsNone(quem)
        self.assertEqual(resposta.motor, "regras")
        self.assertEqual(avisos[-1], "Nenhuma IA respondeu; ficou a resposta das regras.")
        resposta, quem, avisos = self.cadeia().responder("x?", [])
        self.assertEqual((resposta.texto, quem, avisos), (NAO_ENCONTREI, None, []))

    def test_sugerir(self):
        contexto = [{"registro_id": 4, "resumo": "Está cansada", "trecho": "Ela tá cansada."}]
        self.principal.conteudo = {"sugestoes": [{"texto": "Talvez fazer ela sentir ciúme.", "porque": "x",
                                                  "registro_ids": [4]}]}
        self.reserva.conteudo = {"sugestoes": [{"texto": "Talvez perguntar como ela está.", "porque": "Ela tá cansada.",
                                                "registro_ids": [4]}]}
        sugestoes, quem, avisos = self.cadeia().sugerir(contexto, REF)
        self.assertEqual(quem, "reserva")
        self.assertEqual([s["texto"] for s in sugestoes], ["Talvez perguntar como ela está."])
        self.assertIn("conferência", avisos[0])
        self.principal.modo = self.reserva.modo = "erro500"
        self.assertEqual(self.cadeia().sugerir(contexto, REF)[:2], ([], None))
        self.assertEqual(self.cadeia().sugerir([], REF), ([], None, []))

    def test_sugestoes_descartadas_nao_e_falta_de_resposta(self):
        # A IA respondeu, mas nada passou na conferência: lista vazia COM quem respondeu (a tela
        # diz "Não tenho informação suficiente..."), e não "Nenhuma IA respondeu".
        contexto = [{"registro_id": 4, "resumo": "Está cansada", "trecho": "Ela tá cansada."}]
        ruim = {"sugestoes": [{"texto": "Talvez levar chocolate.", "porque": "x", "registro_ids": [99999]}]}
        self.principal.conteudo = ruim
        self.reserva.modo = "erro500"
        sugestoes, quem, avisos = self.cadeia().sugerir(contexto, REF)
        self.assertEqual((sugestoes, quem), ([], "principal"))
        self.assertTrue(any("respondeu, mas descartei 1 sugestão" in a for a in avisos), avisos)
        self.assertFalse(any("Nenhuma IA respondeu" in a for a in avisos), avisos)
        # As duas respondem e as duas caem: fica a última que respondeu.
        self.reserva.modo = "bom"
        self.reserva.conteudo = ruim
        sugestoes, quem, avisos = self.cadeia().sugerir(contexto, REF)
        self.assertEqual((sugestoes, quem, len(avisos)), ([], "reserva", 2))
        # A principal cai na conferência e a reserva responde bem: vale a reserva.
        self.reserva.conteudo = {"sugestoes": [{"texto": "Talvez perguntar como ela está.", "porque": "x",
                                                "registro_ids": [4]}]}
        sugestoes, quem, _ = self.cadeia().sugerir(contexto, REF)
        self.assertEqual((len(sugestoes), quem), (1, "reserva"))


class TesteSoOEscrito(unittest.TestCase):
    """O que vai para a IA na busca e na sugestão: só o que está escrito no próprio registro."""

    PESSOAS = [{"id": 1, "nome": "Lúcia", "relacao": "tia", "apelidos": []},
               {"id": 2, "nome": "Renata", "relacao": "terapeuta", "apelidos": []},
               {"id": 3, "nome": "Sandra Regina", "relacao": "mãe", "apelidos": ["Sandrinha"]},
               {"id": 4, "nome": "Paulo", "relacao": "psiquiatra", "apelidos": []}]

    def limpo(self, entrada, texto):
        from entrenos.motor import so_o_escrito
        return so_o_escrito(entrada, texto, self.PESSOAS)

    def test_nome_do_cadastro_vira_a_relacao_escrita(self):
        e = self.limpo({"registro_id": 1, "resumo": "Lúcia: gosta de pudim", "pessoa": "Lúcia",
                        "trecho": "A tia dela vem jantar sábado e adora pudim."},
                       "A tia dela vem jantar sábado e adora pudim.")
        self.assertEqual((e["resumo"], e["pessoa"]), ("Tia: gosta de pudim", "tia"))
        e = self.limpo({"registro_id": 2, "resumo": "Sandra Regina: cirurgia em novembro/2026", "pessoa": "Sandra Regina",
                        "trecho": "A mãe dela vai fazer uma cirurgia em novembro."},
                       "A mãe dela vai fazer uma cirurgia em novembro e a Julia está preocupada.")
        self.assertEqual((e["resumo"], e["pessoa"]), ("Mãe: cirurgia em novembro/2026", "mãe"))

    def test_relacao_que_nao_esta_no_texto_sai_do_rotulo(self):
        texto = "Ela voltou da consulta com a Renata mais calma."
        e = self.limpo({"registro_id": 3, "resumo": "Renata (terapeuta) — aparece em 1 registro", "trecho": texto}, texto)
        self.assertEqual(e["resumo"], "Renata — aparece em 1 registro")
        e = self.limpo({"registro_id": 4, "resumo": "Lúcia (tia) — aparece em 1 registro", "trecho": "x"},
                       "A tia dela vem jantar sábado.")
        self.assertEqual(e["resumo"], "Tia — aparece em 1 registro")
        e = self.limpo({"registro_id": 5, "resumo": "Paulo (psiquiatra) — aparece em 1 registro"},
                       "O Dr. Paulo pediu para ela voltar semana que vem.")
        self.assertEqual(e["resumo"], "Paulo — aparece em 1 registro")

    def test_sem_troca_possivel_vai_o_trecho(self):
        texto = "Ela ligou para alguém da família ontem."
        e = self.limpo({"registro_id": 6, "resumo": "Sandra Regina: ligou", "pessoa": "Sandra Regina", "alvo": None,
                        "trecho": texto}, texto)
        self.assertEqual((e["resumo"], e["pessoa"], e["alvo"]), (texto, None, None))

    def test_o_que_esta_escrito_fica_igual(self):
        texto = "A mãe dela, Sandra Regina, adora pudim."
        entrada = {"registro_id": 7, "resumo": "Sandra Regina: gosta de pudim", "pessoa": "Sandra Regina",
                   "trecho": texto, "certeza": "observado", "data_registro": "2026-09-29"}
        self.assertEqual(self.limpo(entrada, texto), entrada)
        e = self.limpo({"registro_id": 8, "resumo": "Sandra (mãe): gosta de pudim", "pessoa": "Sandra"},
                       "A Sandra adora pudim.")
        self.assertEqual((e["resumo"], e["pessoa"]), ("Sandra: gosta de pudim", "Sandra"))

    def test_nada_do_cadastro_escapa(self):
        texto = "A tia dela vem jantar."
        for resumo in ("LÚCIA: vem jantar", "Lucia: vem jantar", "Sandrinha e Lúcia vêm jantar", "Terapeuta disse algo"):
            e = self.limpo({"registro_id": 9, "resumo": resumo, "trecho": texto}, texto)
            for segredo in ("lucia", "sandr", "terapeuta"):
                self.assertNotIn(segredo, (e["resumo"] or "").lower().replace("ú", "u"), resumo)


class TesteProblemasDaConfig(unittest.TestCase):
    def test_diz_por_que_nao_da_para_usar(self):
        from entrenos.motor import problemas_da_config_ia
        self.assertEqual(problemas_da_config_ia(None), {})
        self.assertEqual(problemas_da_config_ia({"ia_principal": {"provedor": "ollama", "modelo": "m"}}), {})
        problemas = problemas_da_config_ia({
            "ia_principal": {"provedor": "groq", "url": "https://api.groq.com/openai/v1", "modelo": "llama",
                             "tipo": "openai", "chave": None},
            "ia_reserva": {"provedor": "groq", "url": "http://api.groq.com/openai/v1", "modelo": "llama",
                           "tipo": "openai", "chave": "gsk_SEGREDO123"}})
        self.assertIn("precisa de uma chave", problemas["principal"])
        self.assertIn("https://", problemas["reserva"])
        self.assertNotIn("SEGREDO", json.dumps(problemas, ensure_ascii=False))


class TesteEscolhaDoMotor(unittest.TestCase):
    def test_padrao_e_regras(self):
        for config in (None, {}, {"motor": "regras"}, {"motor": "xyz"}, {"motor": None}):
            self.assertIsInstance(obter_motor(config), MotorRegras)
            self.assertFalse(envia_para_fora(config))

    def test_configuracao_quebrada_vira_regras(self):
        for config in ({"motor": "openai"}, {"motor": "ollama"},
                       {"motor": "ollama", "motor_url": "ftp://x", "motor_modelo": "m"},
                       {"motor": "openai", "motor_url": "nada", "motor_modelo": "m"},
                       {"motor": "ollama", "motor_modelo": "m", "motor_tempo_limite": "abc"},
                       {"motor": "ollama", "motor_modelo": "m", "motor_tempo_limite": -1},
                       ["não é dict"]):
            self.assertIsInstance(obter_motor(config), MotorRegras, config)
            self.assertFalse(envia_para_fora(config))

    def test_regras_sempre_mesmo_com_ia_configurada(self):
        # Seção 17: a IA só roda por botão; o motor do app é sempre o das regras.
        for config in ({"motor": "ollama", "motor_modelo": "qwen3:4b-instruct-2507-q4_K_M"},
                       {"ia_principal": {"provedor": "ollama", "modelo": "qwen3:4b"}}):
            motor = obter_motor(config)
            self.assertIsInstance(motor, MotorRegras)
            self.assertEqual(motor.nome, "regras")
            self.assertFalse(envia_para_fora(config))

    def test_ia_envia_para_fora(self):
        casos = {
            "http://localhost:11434": False,
            "http://127.0.0.1:8080/": False,
            "http://127.0.0.2:8080": False,
            "http://[::1]:8080": False,
            "https://api.exemplo.com": True,
            "http://192.168.0.10:11434": True,
            "http://localhost.exemplo.com": True,
            "http://127.0.0.1.exemplo.com": True,
        }
        for url, esperado in casos.items():
            cfg = {"provedor": "outro", "url": url, "modelo": "m", "tipo": "openai", "chave": "k"}
            self.assertEqual(ia_envia_para_fora(cfg), esperado, url)

    def test_modelo_cloud_do_ollama_local_envia_para_fora(self):
        # O Ollama local repassa modelos "-cloud" para ollama.com: o texto sai da máquina.
        for modelo, esperado in (("gpt-oss:120b-cloud", True), ("qwen3-coder:480b-cloud", True),
                                 ("deepseek-v3.1:671b-cloud", True), ("kimi-k2:cloud", True),
                                 ("qwen3:4b-instruct-2507-q4_K_M", False), ("cloudy-llama:7b", False)):
            cfg = {"provedor": "ollama", "url": "http://127.0.0.1:11434", "modelo": modelo, "tipo": "ollama"}
            self.assertEqual(ia_envia_para_fora(cfg), esperado, modelo)

    def test_ia_envia_para_fora_sem_url_e_configuracao_inteira(self):
        self.assertFalse(ia_envia_para_fora(None))
        self.assertFalse(ia_envia_para_fora({}))
        self.assertTrue(ia_envia_para_fora({"provedor": "groq", "modelo": "llama", "chave": "gsk_x"}))
        self.assertFalse(ia_envia_para_fora({"provedor": "ollama", "modelo": "qwen3:4b"}))
        self.assertFalse(ia_envia_para_fora({"provedor": "lmstudio", "modelo": "m"}))
        self.assertTrue(ia_envia_para_fora({"provedor": "outro", "modelo": "m"}))       # sem endereço: avisa
        local = {"provedor": "ollama", "modelo": "qwen3:4b"}
        fora = {"provedor": "google", "modelo": "gemini", "chave": "AIza"}
        self.assertFalse(ia_envia_para_fora({"ia_principal": local, "ia_reserva": None}))
        self.assertTrue(ia_envia_para_fora({"ia_principal": local, "ia_reserva": fora}))

    def test_motor_ia_valida_parametros(self):
        casos = [
            (("xyz", "http://127.0.0.1:1", "m"), {}, "desconhecido"),
            (("outro", "http://127.0.0.1:1", "m", "anthropic"), {}, "Tipo"),
            (("outro", "javascript:alert(1)", "m"), {}, "inválido"),
            (("outro", "ftp://127.0.0.1/", "m"), {}, "inválido"),
            (("outro", "http://127.0.0.1:99999", "m"), {}, "inválido"),
            (("outro", "", "m"), {}, "inválido"),                                     # "outro" não tem URL padrão
            (("ollama", "http://127.0.0.1:1", "  "), {}, "modelo"),
            (("ollama", "http://127.0.0.1:1", "m"), {"tempo_limite": 0}, "positivo"),
            (("ollama", "http://127.0.0.1:1", "m"), {"tempo_limite": float("inf")}, "positivo"),
            (("ollama", "http://127.0.0.1:1", "m"), {"tempo_limite": "abc"}, "segundos"),
            (("groq", None, "llama"), {}, "precisa de uma chave"),
            (("openai", None, "gpt"), {"chave": "sk-ant-api03-x"}, "Claude"),
            (("outro", "http://127.0.0.1:1", "m"), {"chave": "tem espaço"}, "caracteres"),
            (("outro", "http://127.0.0.1:1", "m"), {"chave": "a\r\nX-Injetado: 1"}, "caracteres"),
            (("outro", "http://api.exemplo.com/v1", "m"), {"chave": "k"}, "https://"),  # chave sem criptografia
        ]
        for args, kw, trecho in casos:
            with self.assertRaises(ValueError, msg=args) as erro:
                MotorIA(*args, **kw)
            self.assertIn(trecho, str(erro.exception), args)

    def test_motor_ia_monta_certo(self):
        groq = MotorIA("groq", None, "llama-3.3-70b-versatile", chave=" gsk_abc123 ")
        self.assertEqual((groq.url, groq.tipo, groq.chave), ("https://api.groq.com/openai/v1", "openai", "gsk_abc123"))
        self.assertEqual(groq.nome, "ia:groq/llama-3.3-70b-versatile")
        self.assertFalse(groq.local)
        self.assertNotIn("gsk_abc123", repr(groq))
        ollama = MotorIA("ollama", None, "qwen3:4b")
        self.assertEqual((ollama.url, ollama.tipo, ollama.tempo_limite), ("http://127.0.0.1:11434", "ollama", 120.0))
        self.assertTrue(ollama.local)
        self.assertEqual(MotorIA("outro", "http://192.168.0.10:8080", "m", chave="k").url, "http://192.168.0.10:8080/v1")
        self.assertIsInstance(ollama, Motor)


class TesteObterCadeiaIA(unittest.TestCase):
    def test_sem_ia(self):
        for config in (None, {}, ["x"], {"ia_principal": None, "ia_reserva": None}, {"motor": "ollama"}):
            self.assertIsNone(obter_cadeia_ia(config), config)

    def test_principal_e_reserva(self):
        cadeia = obter_cadeia_ia({
            "ia_principal": {"provedor": "ollama", "url": None, "modelo": "qwen3:4b", "tipo": "ollama", "chave": None},
            "ia_reserva": {"provedor": "groq", "url": "https://api.groq.com/openai/v1", "modelo": "llama",
                           "tipo": "openai", "chave": "gsk_teste"},
            "ia_tempo_limite": 45,
        })
        self.assertEqual(cadeia.principal.nome, "ia:ollama/qwen3:4b")
        self.assertEqual(cadeia.principal.url, "http://127.0.0.1:11434")
        self.assertEqual(cadeia.reserva.chave, "gsk_teste")
        self.assertEqual((cadeia.principal.tempo_limite, cadeia.reserva.tempo_limite), (45.0, 45.0))
        self.assertTrue(cadeia.envia_para_fora)
        self.assertEqual(cadeia.avisos_config, [])

    def test_tempo_estranho_vira_padrao(self):
        for tempo in ("abc", -1, 0, None, True):
            cadeia = obter_cadeia_ia({"ia_principal": {"provedor": "ollama", "modelo": "m"}, "ia_tempo_limite": tempo})
            self.assertEqual(cadeia.principal.tempo_limite, 120.0, tempo)

    def test_configuracao_quebrada(self):
        cadeia = obter_cadeia_ia({"ia_principal": {"provedor": "groq", "modelo": "llama"},     # sem chave
                                  "ia_reserva": {"provedor": "ollama", "modelo": "qwen3:4b"}})
        self.assertIsNone(cadeia.principal)
        self.assertEqual(cadeia.reserva.nome, "ia:ollama/qwen3:4b")
        self.assertEqual(cadeia.avisos_config, ["A IA principal está com a configuração incompleta; "
                                                "confira em Ajustes → IA."])
        self.assertFalse(cadeia.envia_para_fora)
        for quebrada in ({"provedor": "xyz", "modelo": "m"}, {"provedor": "openai", "modelo": "m", "chave": "sk-ant-x"},
                         {"provedor": "ollama"}, "texto", 42):
            self.assertIsNone(obter_cadeia_ia({"ia_principal": quebrada}), quebrada)

    def test_sem_provedor_deduz_pela_url(self):
        cadeia = obter_cadeia_ia({"ia_principal": {"url": "http://127.0.0.1:11434", "modelo": "m", "tipo": "ollama"}})
        self.assertEqual(cadeia.principal.provedor, "ollama")
        cadeia = obter_cadeia_ia({"ia_principal": {"url": "http://127.0.0.1:8080", "modelo": "m"}})
        self.assertEqual((cadeia.principal.provedor, cadeia.principal.url), ("outro", "http://127.0.0.1:8080/v1"))


def _item(tipo, resumo, trecho, **kw) -> Item:
    return Item(tipo=TipoItem(tipo), resumo=resumo, trecho=trecho, certeza=Certeza(kw.pop("certeza", "observado")),
                **kw)


class TesteCompararPropostas(unittest.TestCase):
    def test_igual_mudou_novo_removido(self):
        texto = "Ela adora tulipas. Ela odeia filas. Ela quer um fone. Ela viaja dia 20. Ela é de Recife."
        regras = Proposta(texto=texto, referencia=REF, itens=[
            _item("preferencia", "Gosta de tulipas", "Ela adora tulipas.", alvo="tulipas", polaridade=1,
                  categorias=["Presentes"]),
            _item("preferencia", "Não gosta de filas", "Ela odeia filas.", alvo="filas", polaridade=-1),
            _item("nota", "Ela quer um fone.", "Ela quer um fone.", importancia=1),
            _item("fato", "É de Recife", "Ela é de Recife."),
        ])
        ia = Proposta(texto=texto, referencia=REF, itens=[
            _item("preferencia", "Gosta de tulipas", "Ela adora tulipas.", alvo="tulipas", polaridade=1,
                  categorias=["Presentes"]),
            _item("preferencia", "Odeia filas", "Ela odeia filas.", alvo="filas", polaridade=-1, certeza="confirmado"),
            _item("desejo", "Quer um fone", "Ela quer um fone.", alvo="fone", categorias=["Presentes"]),
            _item("evento", "Viagem", "Ela viaja dia 20.", data_evento=date(2026, 10, 20)),
        ], motor="ia:ollama/m")
        mudancas = comparar_propostas(regras, ia)
        self.assertEqual(mudancas[0], {"indice": 0, "indice_regras": 0, "situacao": "igual", "campos": []})
        self.assertEqual(mudancas[1], {"indice": 1, "indice_regras": 1, "situacao": "mudou",
                                       "campos": ["resumo", "certeza"]})
        self.assertEqual(mudancas[2]["situacao"], "mudou")
        self.assertEqual(mudancas[2]["indice_regras"], 2)
        self.assertEqual(mudancas[2]["campos"], ["tipo", "resumo", "categorias", "alvo", "importancia"])
        self.assertEqual(mudancas[3], {"indice": 3, "indice_regras": None, "situacao": "novo", "campos": []})
        self.assertEqual(mudancas[4], {"indice": None, "indice_regras": 3, "situacao": "removido", "campos": [],
                                       "resumo": "É de Recife", "tipo": "fato"})
        self.assertEqual(len(mudancas), 5)
        json.dumps(mudancas)

    def test_mesma_frase_dois_alvos_casa_pelo_alvo(self):
        trecho = "Ela ama sushi e comida japonesa."
        regras = Proposta(texto=trecho, referencia=REF, itens=[
            _item("preferencia", "Gosta de sushi", trecho, alvo="sushi", polaridade=1),
            _item("preferencia", "Gosta de comida japonesa", trecho, alvo="comida japonesa", polaridade=1),
        ])
        ia = Proposta(texto=trecho, referencia=REF, itens=[
            _item("preferencia", "Gosta de comida japonesa", trecho, alvo="comida japonesa", polaridade=1),
            _item("preferencia", "Gosta de sushi", trecho, alvo="sushi", polaridade=1),
        ])
        self.assertEqual([(m["indice_regras"], m["situacao"]) for m in comparar_propostas(regras, ia)],
                         [(1, "igual"), (0, "igual")])

    def test_de_verdade_com_a_ia_falsa(self):
        srv = _ServidorFalso()
        linha = _ligar(srv)
        try:
            esquecer_formatos()
            srv.conteudo = {"itens": [SUSHI]}
            regras = MotorRegras().analisar(TEXTO, REF, Conhecimento())
            ia = MotorIA("ollama", srv.url, "m", tempo_limite=5).tentar_analisar(TEXTO, REF, Conhecimento(), regras)
            mudancas = comparar_propostas(regras, ia)
            self.assertEqual(len([m for m in mudancas if m["indice"] is not None]), len(ia.itens))
            self.assertFalse(any(m["situacao"] == "removido" for m in mudancas))   # o que a IA esqueceu voltou
        finally:
            _desligar(srv, linha)

    def test_propostas_vazias(self):
        vazia = Proposta(texto="", referencia=REF)
        self.assertEqual(comparar_propostas(vazia, vazia), [])
        self.assertEqual(comparar_propostas(None, None), [])


class TesteMotorRegras(unittest.TestCase):
    def setUp(self):
        self.motor = MotorRegras()

    def test_e_um_motor(self):
        self.assertIsInstance(self.motor, Motor)
        self.assertEqual(self.motor.nome, "regras")
        self.assertTrue(self.motor.disponivel())

    def test_analisar_junta_tudo(self):
        texto = "A mãe dela vai fazer uma cirurgia em novembro. Ela está preocupada."
        p = self.motor.analisar(texto, REF, Conhecimento())
        self.assertIsInstance(p, Proposta)
        self.assertEqual(p.motor, "regras")
        self.assertEqual(p.referencia, REF)
        evento = next(i for i in p.itens if i.tipo == TipoItem.EVENTO)
        self.assertEqual(evento.pessoa, "mãe")
        self.assertEqual(evento.data_evento, date(2026, 11, 1))
        self.assertTrue(any(i.tipo == TipoItem.ESTADO for i in p.itens))
        self.assertEqual([d.expressao for d in p.datas], ["em novembro"])
        self.assertEqual([x.relacao for x in p.pessoas], ["mãe"])
        for c in ("Família", "Saúde", "Eventos futuros", "Preocupações"):
            self.assertIn(c, p.categorias)
        self.assertTrue(any(a.startswith("Pessoa nova: mãe") for a in p.avisos))
        for item in p.itens:
            self.assertIn(item.trecho, texto)

    def test_data_ambigua_por_extenso(self):
        p = self.motor.analisar("Ela vai viajar para Salvador sexta que vem.", REF, Conhecimento())
        aviso = next(a for a in p.avisos if "sexta que vem" in a)
        self.assertIn("sexta-feira, 2 de outubro", aviso)
        self.assertIn("sexta-feira, 9 de outubro", aviso)
        self.assertIn(" ou ", aviso)

    def test_pessoa_conhecida_nao_gera_aviso(self):
        conhecimento = Conhecimento(pessoas=[{"id": 3, "nome": "Mariana", "relacao": "melhor amiga",
                                              "apelidos": ["Mari"]}])
        p = self.motor.analisar("Ela saiu com a Mari ontem.", REF, conhecimento)
        self.assertEqual([x.pessoa_id for x in p.pessoas], [3])
        self.assertFalse(any(a.startswith("Pessoa nova") for a in p.avisos))

    def test_pessoa_nova_com_nome_e_relacao(self):
        p = self.motor.analisar("Mariana é a melhor amiga dela.", REF, Conhecimento())
        self.assertIn("Pessoa nova: Mariana (melhor amiga). Confira o nome e a relação antes de salvar.", p.avisos)

    def test_nome_dela_nao_vira_pessoa(self):
        p = self.motor.analisar("Hoje a Ana me disse que ama sushi.", REF, Conhecimento(nome_dela="Ana"))
        self.assertEqual(p.pessoas, [])
        self.assertIsNone(p.itens[0].pessoa)

    def test_texto_vazio(self):
        for texto in ("", "   ", None):
            p = self.motor.analisar(texto, REF, Conhecimento())
            self.assertEqual(p.itens, [])
            self.assertEqual(p.avisos, [])

    def test_sem_conhecimento(self):
        p = self.motor.analisar("Ela adora tulipas.", REF, None)
        self.assertEqual(p.itens[0].alvo, "tulipas")

    def test_responder_modelo_de_frase(self):
        self.assertEqual(self.motor.responder("x?", []).texto, NAO_ENCONTREI)
        r = self.motor.responder("x?", [{"registro_id": 1}, {"registro_id": 1}, {"registro_id": 4}])
        self.assertIsInstance(r, Resposta)
        self.assertEqual(r.texto, "Encontrei 2 registros sobre isso:")
        self.assertEqual(r.motor, "regras")


class TesteUtilidades(unittest.TestCase):
    def test_data_extenso(self):
        self.assertEqual(data_extenso(date(2026, 10, 1), REF), "quinta-feira, 1º de outubro")
        self.assertEqual(data_extenso(date(2026, 10, 9), REF), "sexta-feira, 9 de outubro")
        self.assertEqual(data_extenso(date(2027, 1, 2), REF), "sábado, 2 de janeiro de 2027")

    def test_juntar(self):
        self.assertEqual(juntar([]), "")
        self.assertEqual(juntar(["a"]), "a")
        self.assertEqual(juntar(["a", "b"], "ou"), "a ou b")
        self.assertEqual(juntar(["a", "b", "c"]), "a, b e c")


if __name__ == "__main__":
    unittest.main()
