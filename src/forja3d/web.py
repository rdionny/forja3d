"""Pesquisa na web para o agente achar medidas reais de objetos comerciais, como baterias, conectores e placas.

Busca pela API do Brave Search quando há chave nas configurações, e pela API da Wikipédia sem chave.
As duas aceitam acesso automático: o app se identifica como Forja3D e nunca se passa por navegador.
Só lê endereços públicos da internet, nada da rede de casa, nem por redirecionamento. O texto vem de
sites de terceiros, e o agente o recebe como dado, nunca como instrução.
"""
from __future__ import annotations

import html
import ipaddress
import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from typing import Any

from forja3d.versao import VERSAO

AGENTE = f"Forja3D/{VERSAO}"
TEMPO_S = 15
LIMITE_DA_PAGINA = 2_000_000
LIMITE_DO_TEXTO = 12_000
RESULTADOS = 6
BLOCOS = {"p", "div", "br", "li", "tr", "table", "ul", "ol", "dt", "dd", "section", "article", "h1", "h2", "h3", "h4", "h5", "h6"}
IGNORADOS = {"script", "style", "noscript", "svg", "template", "nav", "footer"}


class ErroDaWeb(Exception):
    """A mensagem é para o agente e para o usuário."""


def _exigir_publico(url: str) -> None:
    partes = urllib.parse.urlsplit(url)
    if partes.scheme not in ("http", "https") or not partes.hostname:
        raise ErroDaWeb("Só leio páginas http ou https.")
    try:
        enderecos = {info[4][0] for info in socket.getaddrinfo(partes.hostname, None)}
    except OSError as erro:
        raise ErroDaWeb(f"Não achei o site {partes.hostname}.") from erro
    if not enderecos or not all(ipaddress.ip_address(e.split("%")[0]).is_global for e in enderecos):
        raise ErroDaWeb("Só leio páginas públicas da internet, nunca endereços da rede local.")


class _SoParaPublico(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, ANN201 - assinatura do urllib.
        _exigir_publico(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_ABRIDOR = urllib.request.build_opener(_SoParaPublico)


def _baixar(url: str, cabecalhos: dict[str, str] | None = None) -> tuple[bytes, str, str, str]:
    """O conteúdo, o tipo, a codificação e o endereço final, depois dos redirecionamentos."""
    _exigir_publico(url)
    pedido = urllib.request.Request(url, headers={"User-Agent": AGENTE, "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8", **(cabecalhos or {})})
    try:
        with _ABRIDOR.open(pedido, timeout=TEMPO_S) as resposta:
            return resposta.read(LIMITE_DA_PAGINA), resposta.headers.get_content_type(), resposta.headers.get_content_charset() or "utf-8", resposta.geturl()
    except urllib.error.HTTPError as erro:
        raise ErroDaWeb(f"O site respondeu com o código {erro.code}.") from erro
    except OSError as erro:
        raise ErroDaWeb("Não consegui acessar o site. Confira a internet.") from erro


def _decodificar(dados: bytes, codificacao: str) -> str:
    try:
        return dados.decode(codificacao, errors="replace")
    except LookupError:
        return dados.decode("utf-8", errors="replace")


def _json(url: str, cabecalhos: dict[str, str] | None = None) -> Any:
    dados, _tipo, codificacao, _final = _baixar(url, cabecalhos)
    try:
        return json.loads(_decodificar(dados, codificacao))
    except ValueError as erro:
        raise ErroDaWeb("A pesquisa devolveu uma resposta que não entendi.") from erro


def _limpar(trecho: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", trecho))).strip()


def _resultados_do_brave(consulta: str, chave: str) -> list[dict[str, str]]:
    endereco = "https://api.search.brave.com/res/v1/web/search?" + urllib.parse.urlencode({"q": consulta, "count": RESULTADOS})
    dados = _json(endereco, {"Accept": "application/json", "X-Subscription-Token": chave})
    itens = (dados.get("web") or {}).get("results") or []
    return [{"titulo": _limpar(i.get("title", "")), "url": i.get("url", ""), "trecho": _limpar(i.get("description", ""))} for i in itens[:RESULTADOS]]


def _resultados_da_wikipedia(consulta: str, idioma: str) -> list[dict[str, str]]:
    endereco = f"https://{idioma}.wikipedia.org/w/api.php?" + urllib.parse.urlencode(
        {"action": "query", "list": "search", "srsearch": consulta, "format": "json", "srlimit": RESULTADOS}
    )
    itens = (_json(endereco).get("query") or {}).get("search") or []
    return [
        {
            "titulo": i["title"],
            "url": f"https://{idioma}.wikipedia.org/wiki/" + urllib.parse.quote(i["title"].replace(" ", "_")),
            "trecho": _limpar(i.get("snippet", "")),
        }
        for i in itens
    ]


def pesquisar(consulta: str, chave_brave: str = "") -> dict[str, Any]:
    """Título, endereço e trecho dos primeiros resultados, e de onde vieram."""
    consulta = consulta.strip()
    if not consulta:
        raise ErroDaWeb("A pesquisa está vazia.")
    if chave_brave:
        return {"fonte": "Brave Search", "resultados": _resultados_do_brave(consulta, chave_brave)}
    # Sem chave, só a Wikipédia. Primeiro em inglês, onde as fichas técnicas são mais completas.
    resultados = _resultados_da_wikipedia(consulta, "en")[:4]
    resultados += _resultados_da_wikipedia(consulta, "pt")[: RESULTADOS - len(resultados)]
    return {"fonte": "Wikipédia", "resultados": resultados}


class _Texto(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.partes: list[str] = []
        self.titulo = ""
        self._ignorando = 0
        self._no_titulo = False

    def handle_starttag(self, tag: str, _atributos: Any) -> None:
        if tag in IGNORADOS:
            self._ignorando += 1
        elif tag == "title":
            self._no_titulo = True
        elif tag in ("td", "th"):
            self.partes.append(" | ")
        elif tag in BLOCOS:
            self.partes.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in IGNORADOS:
            self._ignorando = max(0, self._ignorando - 1)
        elif tag == "title":
            self._no_titulo = False
        elif tag in BLOCOS:
            self.partes.append("\n")

    def handle_data(self, dados: str) -> None:
        if self._no_titulo:
            self.titulo += dados
        elif not self._ignorando:
            self.partes.append(dados)

    def texto(self) -> str:
        linhas = (re.sub(r"\s+", " ", linha).strip(" |") for linha in "".join(self.partes).split("\n"))
        return "\n".join(linha for linha in linhas if linha)


def ler_pagina(url: str) -> dict[str, Any]:
    """O texto de uma página pública, com as tabelas em linhas separadas por |, cortado em LIMITE_DO_TEXTO caracteres."""
    dados, tipo, codificacao, final = _baixar(url)
    if tipo == "text/plain":
        titulo, texto = "", _decodificar(dados, codificacao).strip()
    elif tipo in ("text/html", "application/xhtml+xml"):
        leitor = _Texto()
        leitor.feed(_decodificar(dados, codificacao))
        titulo, texto = re.sub(r"\s+", " ", leitor.titulo).strip(), leitor.texto()
    else:
        raise ErroDaWeb(f"Só leio páginas de texto, e este endereço é {tipo}, como um PDF ou uma imagem. Procure outra fonte.")
    return {"url": final, "titulo": titulo, "texto": texto[:LIMITE_DO_TEXTO], "cortado": len(texto) > LIMITE_DO_TEXTO}
