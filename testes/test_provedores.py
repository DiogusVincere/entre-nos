"""Testes da tabela de serviços de IA (DESIGN.md, seção 17.2). Nada aqui faz rede."""
from __future__ import annotations

import unittest

from entrenos import provedores
from entrenos.provedores import (NAO_SUPORTADOS, PROVEDORES, chave_nao_suportada, chave_valida, detectar_por_chave,
                                 detectar_por_url, e_local, endereco_local, limpar_chave, modelo_na_nuvem,
                                 normalizar_url, para_tela, provedor)


def _id(p):
    return p.id if p is not None else None


class TesteTabela(unittest.TestCase):
    def test_ids_e_tabela(self):
        ids = [p.id for p in PROVEDORES]
        self.assertEqual(ids, ["ollama", "lmstudio", "groq", "openrouter", "google", "openai", "mistral", "outro"])
        self.assertEqual(len(set(ids)), len(ids))
        for p in PROVEDORES:
            self.assertIn(p.tipo, ("ollama", "openai"), p.id)
            self.assertIs(provedor(p.id), p)
            if p.precisa_chave:
                self.assertTrue(p.pagina_chave, p.id)
                self.assertFalse(p.local, p.id)
            if p.url:
                self.assertEqual(normalizar_url(p.url, p.tipo), p.url, p.id)   # já vem na forma limpa
                self.assertEqual(p.local, e_local(p.url, None), p.id)

    def test_valores_da_secao_17(self):
        self.assertEqual(provedor("ollama").url, "http://127.0.0.1:11434")
        self.assertEqual(provedor("ollama").tipo, "ollama")
        self.assertFalse(provedor("ollama").precisa_chave)
        self.assertEqual(provedor("lmstudio").url, "http://127.0.0.1:1234/v1")
        self.assertEqual(provedor("groq").url, "https://api.groq.com/openai/v1")
        self.assertEqual(provedor("openrouter").url, "https://openrouter.ai/api/v1")
        self.assertEqual(provedor("google").url, "https://generativelanguage.googleapis.com/v1beta/openai")
        self.assertEqual(provedor("openai").url, "https://api.openai.com/v1")
        self.assertEqual(provedor("mistral").url, "https://api.mistral.ai/v1")
        self.assertEqual(provedor("outro").url, "")
        self.assertFalse(provedor("outro").precisa_chave)          # opcional
        self.assertEqual(provedor("groq").prefixos, ("gsk_",))
        # Correção final da seção 17: "sk-" sozinho é de vários serviços (DeepSeek, Moonshot...).
        self.assertEqual(set(provedor("openai").prefixos), {"sk-proj-", "sk-svcacct-"})
        self.assertEqual(provedor("mistral").prefixos, ())
        self.assertIn("não recomendado", provedor("google").aviso)
        self.assertIn(":free", provedor("openrouter").aviso)
        self.assertIn("retenção zero", provedor("groq").aviso)
        self.assertIn("treino", provedor("mistral").aviso)
        self.assertIsNone(provedor("openai").aviso)
        self.assertTrue(provedor("outro").aviso)

    def test_provedor_desconhecido(self):
        for valor in ("xyz", "", None, 3, "OLLAMA"):
            self.assertIsNone(provedor(valor))

    def test_imutavel(self):
        with self.assertRaises(Exception):
            provedor("groq").url = "https://outro.com"   # type: ignore[misc]

    def test_para_tela(self):
        tela = para_tela()
        self.assertEqual([p["id"] for p in tela], [p.id for p in PROVEDORES])
        self.assertEqual(set(tela[0]), {"id", "nome", "url", "tipo", "precisa_chave", "prefixos", "local", "aviso",
                                        "pagina_chave"})
        self.assertIsInstance(tela[2]["prefixos"], list)


class TesteDeteccaoPorChave(unittest.TestCase):
    def test_prefixos_concorrentes(self):
        casos = {
            "sk-or-v1-0123456789abcdef": "openrouter",    # "sk-or-" vence "sk-"
            "sk-proj-AbCdEf0123456789": "openai",
            "sk-0123456789abcdef": None,                    # "sk-" sozinho: ambíguo (chave_ambigua)
            "sk-svcacct-0123456789": "openai",
            "AIzaSyA-0123456789abcdefghijklmnopq": "google",
            "gsk_0123456789abcdefghij": "groq",
        }
        for chave, esperado in casos.items():
            self.assertEqual(_id(detectar_por_chave(chave)), esperado, chave)
            self.assertIsNone(chave_nao_suportada(chave), chave)

    def test_anthropic_nao_suportada(self):
        chave = "sk-ant-api03-0123456789abcdef"
        self.assertIsNone(detectar_por_chave(chave))                 # não vira "OpenAI" por causa do "sk-"
        mensagem = chave_nao_suportada(chave)
        self.assertIn("Claude", mensagem)
        self.assertIn("ainda não", mensagem)
        self.assertIn("sk-ant-", NAO_SUPORTADOS)

    def test_sk_sozinho_e_ambiguo(self):
        # DeepSeek, Moonshot, Qwen, SiliconFlow... também usam "sk-": colar uma delas não pode
        # escolher a OpenAI sozinho (a chave iria para api.openai.com ao buscar modelos).
        for chave in ("sk-deepseek-like-0123", "sk-0123456789abcdef", " sk-abc "):
            self.assertIsNone(detectar_por_chave(chave), chave)
            self.assertIsNone(chave_nao_suportada(chave), chave)
            self.assertIn("vários serviços", provedores.chave_ambigua(chave), chave)
        for chave in ("sk-proj-abc", "sk-svcacct-abc", "sk-or-v1-abc", "sk-ant-api03-abc", "gsk_abc", "xyz"):
            self.assertIsNone(provedores.chave_ambigua(chave), chave)
        self.assertEqual(_id(detectar_por_chave("sk-proj-abc")), "openai")
        self.assertIn("sk-", provedores.AMBIGUOS)

    def test_sem_prefixo_conhecido(self):
        for chave in ("", None, "   ", "0123456789abcdef0123456789abcdef", "aizaSyMinusculo", "GSK_maiusculo",
                      "sk", "chave qualquer"):
            self.assertIsNone(detectar_por_chave(chave), chave)
            self.assertIsNone(chave_nao_suportada(chave), chave)

    def test_chave_colada_com_sujeira(self):
        self.assertEqual(_id(detectar_por_chave("  gsk_abc123  ")), "groq")
        self.assertEqual(_id(detectar_por_chave("Bearer sk-or-v1-abc")), "openrouter")
        self.assertEqual(_id(detectar_por_chave('"AIzaSyXYZ"')), "google")

    def test_limpar_e_validar_chave(self):
        self.assertEqual(limpar_chave("  'Bearer gsk_abc'  "), "gsk_abc")
        self.assertEqual(limpar_chave(None), "")
        self.assertTrue(chave_valida("gsk_abc-123.XYZ"))
        for ruim in ("", "com espaço", "linha\nquebrada", "cabecalho\r\nX-Injetado: 1", "acentuação"):
            self.assertFalse(chave_valida(ruim), repr(ruim))


class TesteDeteccaoPorUrl(unittest.TestCase):
    def test_hosts_conhecidos(self):
        casos = {
            "https://api.groq.com/openai/v1": "groq",
            "https://api.groq.com/openai/v1/chat/completions": "groq",
            "https://openrouter.ai/api/v1": "openrouter",
            "https://generativelanguage.googleapis.com/v1beta/openai/": "google",
            "https://api.openai.com/v1": "openai",
            "api.openai.com/v1": "openai",
            "https://API.MISTRAL.AI/v1": "mistral",
            "http://127.0.0.1:11434": "ollama",
            "http://localhost:11434/api/chat": "ollama",
            "localhost:11434": "ollama",
            "http://127.0.0.1:1234/v1": "lmstudio",
            "http://[::1]:1234/v1": "lmstudio",
        }
        for url, esperado in casos.items():
            self.assertEqual(_id(detectar_por_url(url)), esperado, url)

    def test_desconhecidos(self):
        for url in ("", None, "https://api.exemplo.com/v1", "http://127.0.0.1:8080/v1", "http://192.168.0.10:11434",
                    "https://api.groq.com.golpe.com/openai/v1", "https://golpe.com/api.groq.com",
                    "https://usuario@golpe.com", "http://[x"):
            self.assertIsNone(detectar_por_url(url), url)


class TesteNormalizarUrl(unittest.TestCase):
    def test_formato_comum(self):
        casos = {
            "http://127.0.0.1:1234": "http://127.0.0.1:1234/v1",               # "outro" sem versão ganha /v1
            "http://127.0.0.1:1234/": "http://127.0.0.1:1234/v1",
            "http://127.0.0.1:1234/v1/": "http://127.0.0.1:1234/v1",
            "https://api.groq.com/openai/v1/": "https://api.groq.com/openai/v1",
            "https://api.groq.com/openai": "https://api.groq.com/openai/v1",
            "https://api.groq.com/openai/v1/chat/completions": "https://api.groq.com/openai/v1",
            "https://openrouter.ai/api/v1/models": "https://openrouter.ai/api/v1",
            "https://generativelanguage.googleapis.com/v1beta/openai": "https://generativelanguage.googleapis.com/v1beta/openai",
            "HTTPS://API.Exemplo.com/V1?x=1#y": "https://api.exemplo.com/V1",
            "api.exemplo.com": "https://api.exemplo.com/v1",
            "localhost:8080": "http://localhost:8080/v1",
            "https://usuario:senha@api.exemplo.com/v2": "https://api.exemplo.com/v2",
            "https://api.exemplo.com//v1//": "https://api.exemplo.com/v1",
        }
        for url, esperado in casos.items():
            self.assertEqual(normalizar_url(url, "openai"), esperado, url)

    def test_ollama(self):
        casos = {
            "http://127.0.0.1:11434/": "http://127.0.0.1:11434",
            "http://127.0.0.1:11434/api": "http://127.0.0.1:11434",
            "http://127.0.0.1:11434/api/chat": "http://127.0.0.1:11434",
            "http://127.0.0.1:11434/v1": "http://127.0.0.1:11434",
            "localhost:11434": "http://localhost:11434",
            "http://192.168.0.10:11434": "http://192.168.0.10:11434",
        }
        for url, esperado in casos.items():
            self.assertEqual(normalizar_url(url, "ollama"), esperado, url)

    def test_vazio(self):
        for url in ("", "   ", None):
            self.assertEqual(normalizar_url(url, "openai"), "")

    def test_sem_versao_quando_pedido(self):
        # "Outro" com base sem versão (GitHub Models): versao=False não acrescenta /v1.
        casos = {
            "https://models.github.ai/inference": "https://models.github.ai/inference",
            "https://models.github.ai/inference/chat/completions": "https://models.github.ai/inference",
            "https://api.exemplo.com/v2/": "https://api.exemplo.com/v2",
            "api.exemplo.com": "https://api.exemplo.com",
        }
        for url, esperado in casos.items():
            self.assertEqual(normalizar_url(url, "openai", versao=False), esperado, url)
        self.assertEqual(normalizar_url("https://models.github.ai/inference", "openai"),
                         "https://models.github.ai/inference/v1")          # o padrão continua


class TesteLocal(unittest.TestCase):
    def test_enderecos(self):
        casos = {
            "http://localhost:11434": True,
            "http://127.0.0.1:8080/": True,
            "http://127.0.0.2:8080": True,
            "http://[::1]:8080": True,
            "localhost:11434": True,
            "https://api.exemplo.com": False,
            "http://192.168.0.10:11434": False,                   # rede de casa não é "neste computador"
            "http://localhost.exemplo.com": False,
            "http://127.0.0.1.exemplo.com": False,
            "http://0.0.0.0:11434": False,
            "": False,
            None: False,
        }
        for url, esperado in casos.items():
            self.assertEqual(endereco_local(url), esperado, url)
            self.assertEqual(e_local(url, "qwen3:4b"), esperado, url)

    def test_modelo_cloud(self):
        # O Ollama local repassa modelos "-cloud" para ollama.com: o texto sai da máquina.
        for modelo, nuvem in (("gpt-oss:120b-cloud", True), ("qwen3-coder:480b-cloud", True),
                              ("deepseek-v3.1:671b-cloud", True), ("kimi-k2:cloud", True), ("GLM-4.6:Cloud", True),
                              ("qwen3:4b-instruct-2507-q4_K_M", False), ("cloudy-llama:7b", False), (None, False)):
            self.assertEqual(modelo_na_nuvem(modelo), nuvem, modelo)
            self.assertEqual(e_local("http://127.0.0.1:11434", modelo), not nuvem, modelo)

    def test_modulo_exporta_o_contrato(self):
        for nome in ("Provedor", "PROVEDORES", "NAO_SUPORTADOS", "provedor", "detectar_por_chave", "detectar_por_url",
                     "normalizar_url", "e_local"):
            self.assertTrue(hasattr(provedores, nome), nome)


if __name__ == "__main__":
    unittest.main()
