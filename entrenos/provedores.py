"""Serviços de IA que o Entre Nós sabe usar (DESIGN.md, seção 17.2).

Aqui só há dados e contas simples: a tabela de serviços, a detecção pela chave ou pela URL,
a limpeza da URL e a pergunta "isto roda neste computador?". Nada daqui faz rede; quem
conversa com a IA é `motor.py`.

Dois formatos de conversa:
- "ollama": API nativa do Ollama, `{url}/api/chat` e `{url}/api/tags`;
- "openai": o formato comum que quase todo serviço aceita, `{url}/chat/completions` e
  `{url}/models` (a `url` já traz a versão, como "https://api.groq.com/openai/v1").
"""
from __future__ import annotations

import ipaddress
import re
import urllib.parse
from dataclasses import dataclass

TIPOS = ("ollama", "openai")


@dataclass(frozen=True)
class Provedor:
    id: str                    # "ollama" | "lmstudio" | "groq" | "openrouter" | "google" | "openai" | "mistral" | "outro"
    nome: str                  # para a tela
    url: str                   # base padrão já com a versão; "" em "outro" (o usuário digita)
    tipo: str                  # "ollama" (API nativa /api/chat) | "openai" (formato comum /chat/completions)
    precisa_chave: bool
    prefixos: tuple[str, ...]  # começo da chave que identifica o serviço
    local: bool                # roda neste computador
    aviso: str | None          # alerta de privacidade para a tela (None = sem alerta especial)
    pagina_chave: str | None   # onde o usuário cria a chave (só texto/link na tela)


PROVEDORES: list[Provedor] = [
    Provedor("ollama", "Ollama (neste computador)", "http://127.0.0.1:11434", "ollama", False, (), True,
             None, None),
    Provedor("lmstudio", "LM Studio (neste computador)", "http://127.0.0.1:1234/v1", "openai", False, (), True,
             None, None),
    Provedor("groq", "Groq", "https://api.groq.com/openai/v1", "openai", True, ("gsk_",), False,
             "O texto sai do país. No painel da Groq dá para ligar a retenção zero de dados.",
             "https://console.groq.com/keys"),
    Provedor("openrouter", "OpenRouter", "https://openrouter.ai/api/v1", "openai", True, ("sk-or-",), False,
             "Modelos terminados em \":free\" podem guardar ou usar os textos para treino.",
             "https://openrouter.ai/keys"),
    Provedor("google", "Google Gemini", "https://generativelanguage.googleapis.com/v1beta/openai", "openai", True,
             ("AIza",), False,
             "No plano grátis o Google usa os textos para melhorar produtos e permite revisão humana — "
             "não recomendado para este diário.",
             "https://aistudio.google.com/apikey"),
    # Só os prefixos que são da OpenAI: "sk-" sozinho também é de DeepSeek, Moonshot, Qwen... (AMBIGUOS).
    Provedor("openai", "OpenAI", "https://api.openai.com/v1", "openai", True, ("sk-proj-", "sk-svcacct-"), False,
             None, "https://platform.openai.com/api-keys"),
    Provedor("mistral", "Mistral", "https://api.mistral.ai/v1", "openai", True, (), False,
             "No plano grátis a Mistral usa os dados para treino por padrão.",
             "https://console.mistral.ai/api-keys"),
    Provedor("outro", "Outro (compatível com OpenAI)", "", "openai", False, (), False,
             "Depende do serviço: confira se ele guarda ou usa os textos.", None),
]

# Chaves reconhecidas só para dizer que o serviço ainda não é suportado (decisão 17.1.6).
NAO_SUPORTADOS: dict[str, str] = {
    "sk-ant-": "O Claude (Anthropic) ainda não está disponível no Entre Nós. "
               "Por enquanto, escolha um dos outros serviços da lista.",
}

# Começos de chave que vários serviços usam: a tela só sugere, não escolhe o serviço sozinha.
AMBIGUOS: dict[str, str] = {
    "sk-": "Chaves que começam com “sk-” são de vários serviços (OpenAI, DeepSeek, Moonshot, Qwen...). "
           "Escolha o serviço na lista; se não estiver lá, use “Outro” com o endereço do serviço.",
}

_POR_ID = {p.id: p for p in PROVEDORES}
_RE_VERSAO = re.compile(r"^v\d+[a-z]*\d*$", re.IGNORECASE)          # v1, v1beta, v2alpha1
_RE_CHAVE_VALIDA = re.compile(r"^[\x21-\x7e]+$")                    # ASCII visível, sem espaço
_SUFIXOS = {
    # Quem cola o endereço completo de uma rota ganha a base de volta.
    "openai": ("/chat/completions", "/completions", "/models"),
    "ollama": ("/api/chat", "/api/tags", "/api/generate", "/api", "/v1"),
}


def provedor(id: str) -> Provedor | None:  # noqa: A002 - nome do contrato
    """O serviço com esse id, ou None."""
    return _POR_ID.get(id) if isinstance(id, str) else None


def limpar_chave(chave) -> str:
    """Tira espaços, aspas e um "Bearer " colado junto. Não valida."""
    if not isinstance(chave, str):
        return ""
    chave = chave.strip().strip("\"'").strip()
    if chave.lower().startswith("bearer "):
        chave = chave[7:].strip()
    return chave


def chave_valida(chave: str) -> bool:
    """A chave pode ir num cabeçalho HTTP? (só ASCII visível, sem espaço nem quebra de linha)"""
    return bool(chave) and len(chave) <= 4096 and bool(_RE_CHAVE_VALIDA.match(chave))


def _prefixo_mais_longo(chave: str) -> tuple[int, Provedor | None, str | None, str | None]:
    """(tamanho, serviço, mensagem de não suportado, dica de ambíguo) do prefixo mais longo que casa."""
    melhor: tuple[int, Provedor | None, str | None, str | None] = (0, None, None, None)
    for p in PROVEDORES:
        for prefixo in p.prefixos:
            if chave.startswith(prefixo) and len(prefixo) > melhor[0]:
                melhor = (len(prefixo), p, None, None)
    for prefixo, mensagem in NAO_SUPORTADOS.items():
        if chave.startswith(prefixo) and len(prefixo) > melhor[0]:
            melhor = (len(prefixo), None, mensagem, None)
    for prefixo, dica in AMBIGUOS.items():
        if chave.startswith(prefixo) and len(prefixo) > melhor[0]:
            melhor = (len(prefixo), None, None, dica)
    return melhor


def detectar_por_chave(chave: str) -> Provedor | None:
    """O serviço pelo começo da chave; o prefixo mais longo vence ("sk-or-" antes de "sk-").

    Chave de serviço não suportado ("sk-ant-") dá None; `chave_nao_suportada` diz o porquê.
    Começo que vários serviços usam ("sk-" sozinho) também dá None; `chave_ambigua` explica.
    """
    return _prefixo_mais_longo(limpar_chave(chave))[1]


def chave_nao_suportada(chave: str) -> str | None:
    """Mensagem para a tela quando a chave é de um serviço que ainda não é suportado."""
    return _prefixo_mais_longo(limpar_chave(chave))[2]


def chave_ambigua(chave: str) -> str | None:
    """Dica para a tela quando o começo da chave é de vários serviços ("sk-")."""
    return _prefixo_mais_longo(limpar_chave(chave))[3]


def _com_esquema(url: str) -> str:
    """'localhost:1234' -> 'http://localhost:1234'; 'api.x.com/v1' -> 'https://api.x.com/v1'."""
    if "://" in url:
        return url
    host = url.split("/", 1)[0]
    host = host[1:host.find("]")] if host.startswith("[") and "]" in host else host.rsplit(":", 1)[0]
    return ("http://" if _host_local(host) else "https://") + url


def _host_local(host: str | None) -> bool:
    host = (host or "").strip().lower()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def normalizar_url(url: str, tipo: str, versao: bool = True) -> str:
    """Endereço base limpo: sem barra final, sem usuário/senha, sem consulta.

    No formato comum ("openai"), endereço sem versão ganha "/v1" (é o caso de "Outro"), a não
    ser com `versao=False`: serviço cuja base não tem versão ("https://models.github.ai/inference")
    fica como foi digitado. No Ollama, sobra só a raiz do servidor. Quem colou a rota inteira
    ganha a base.
    """
    if not isinstance(url, str) or not url.strip():
        return ""
    url = _com_esquema(url.strip())
    try:
        partes = urllib.parse.urlsplit(url)
    except ValueError:
        return url.rstrip("/")
    netloc = partes.netloc.rsplit("@", 1)[-1].lower()
    caminho = re.sub(r"/{2,}", "/", partes.path).rstrip("/")
    sufixos = _SUFIXOS.get(tipo, _SUFIXOS["openai"])
    mudou = True
    while mudou and caminho:
        mudou = False
        for sufixo in sufixos:
            if caminho.lower().endswith(sufixo):
                caminho = caminho[:-len(sufixo)].rstrip("/")
                mudou = True
                break
    if versao and tipo != "ollama" and not any(_RE_VERSAO.match(s) for s in caminho.split("/")):
        caminho += "/v1"
    return urllib.parse.urlunsplit((partes.scheme.lower(), netloc, caminho, "", ""))


def detectar_por_url(url: str) -> Provedor | None:
    """O serviço pelo endereço: host conhecido ou porta padrão local (11434 Ollama, 1234 LM Studio)."""
    if not isinstance(url, str) or not url.strip():
        return None
    try:
        partes = urllib.parse.urlsplit(_com_esquema(url.strip()))
        host, porta = (partes.hostname or "").lower(), partes.port
    except ValueError:
        return None
    if not host:
        return None
    if _host_local(host):
        for p in PROVEDORES:
            if p.local and urllib.parse.urlsplit(p.url).port == porta:
                return p
        return None
    for p in PROVEDORES:
        if p.url and not p.local and urllib.parse.urlsplit(p.url).hostname == host:
            return p
    return None


def endereco_local(url: str | None) -> bool:
    """O endereço é deste computador (localhost, 127.x.x.x, ::1)? Rede de casa não conta."""
    if not isinstance(url, str) or not url.strip():
        return False
    try:
        return _host_local(urllib.parse.urlsplit(_com_esquema(url.strip())).hostname)
    except ValueError:
        return False


def modelo_na_nuvem(modelo: str | None) -> bool:
    """Modelos "-cloud"/":cloud" do Ollama rodam em ollama.com, mesmo chamados pelo Ollama local."""
    nome = (modelo or "").strip().lower() if isinstance(modelo, str) else ""
    return bool(re.search(r"[-:]cloud(?:$|[-:])", nome))


def e_local(url: str, modelo: str | None) -> bool:
    """True só quando o texto não sai deste computador: endereço local e modelo que não é "-cloud"."""
    return endereco_local(url) and not modelo_na_nuvem(modelo)


def para_tela() -> list[dict]:
    """A tabela para a tela (Ajustes → IA). Sem nada secreto: só o que está no código."""
    return [{"id": p.id, "nome": p.nome, "url": p.url, "tipo": p.tipo, "precisa_chave": p.precisa_chave,
             "prefixos": list(p.prefixos), "local": p.local, "aviso": p.aviso, "pagina_chave": p.pagina_chave}
            for p in PROVEDORES]


__all__ = ["AMBIGUOS", "NAO_SUPORTADOS", "PROVEDORES", "TIPOS", "Provedor", "chave_ambigua", "chave_nao_suportada",
           "chave_valida",
           "detectar_por_chave", "detectar_por_url", "e_local", "endereco_local", "limpar_chave",
           "modelo_na_nuvem", "normalizar_url", "para_tela", "provedor"]
