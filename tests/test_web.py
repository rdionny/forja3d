"""Pesquisa na web do agente: só fontes que aceitam acesso automático, só endereços públicos, conteúdo como dado."""
import json
import socket

import pytest

from forja3d import web
from forja3d.agente.orquestrador import AVISO_DA_WEB, SO_DO_APP
from forja3d.mcp.servidor import definicoes
from test_orquestrador import agente, chamada, config, resultados_das_ferramentas, texto  # noqa: F401

PAGINA = """<html><head><title>18650 battery - Wikipedia</title><style>.x{}</style><script>alert(1)</script></head>
<body><nav>menu</nav><p>An 18650 battery measures <b>18&nbsp;mm</b> in diameter.</p>
<table><tr><th>Size</th><th>Diameter</th><th>Length</th></tr><tr><td>18650</td><td>18 mm</td><td>65 mm</td></tr></table>
<footer>rodapé</footer></body></html>"""


def enderecos(*ips):
    return lambda _host, _porta, *_a, **_k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0)) for ip in ips]


# ---------- Só endereços públicos ----------


@pytest.mark.parametrize("ip", ["192.168.10.1", "127.0.0.1", "10.0.0.5", "169.254.1.1", "::1"])
def test_recusa_a_rede_local(monkeypatch, ip):
    monkeypatch.setattr(socket, "getaddrinfo", enderecos(ip))
    with pytest.raises(web.ErroDaWeb, match="rede local"):
        web.ler_pagina("http://roteador.casa/admin")


def test_recusa_site_que_mistura_endereco_publico_e_local(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", enderecos("93.184.216.34", "192.168.0.1"))
    with pytest.raises(web.ErroDaWeb, match="rede local"):
        web.ler_pagina("http://misturado.example/")


@pytest.mark.parametrize("url", ["file:///C:/Windows/win.ini", "ftp://site/x", "javascript:alert(1)"])
def test_so_http_e_https(url):
    with pytest.raises(web.ErroDaWeb, match="http"):
        web.ler_pagina(url)


def test_redirecionamento_para_a_rede_local_e_recusado(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", enderecos("192.168.10.1"))
    with pytest.raises(web.ErroDaWeb, match="rede local"):
        web._SoParaPublico().redirect_request(None, None, 302, "Found", {}, "http://192.168.10.1/")


# ---------- Leitura ----------


def test_pagina_vira_texto_com_tabela_e_sem_codigo(monkeypatch):
    monkeypatch.setattr(web, "_baixar", lambda url, cabecalhos=None: (PAGINA.encode(), "text/html", "utf-8", url))
    pagina = web.ler_pagina("https://en.wikipedia.org/wiki/18650_battery")
    assert pagina["titulo"] == "18650 battery - Wikipedia"
    assert "An 18650 battery measures 18 mm in diameter." in pagina["texto"]
    assert "18650 | 18 mm | 65 mm" in pagina["texto"]
    assert not any(sobra in pagina["texto"] for sobra in ("alert", "menu", "rodapé", ".x{}"))


def test_pdf_e_imagem_pedem_outra_fonte(monkeypatch):
    monkeypatch.setattr(web, "_baixar", lambda url, cabecalhos=None: (b"%PDF", "application/pdf", "utf-8", url))
    with pytest.raises(web.ErroDaWeb, match="PDF"):
        web.ler_pagina("https://site.example/ficha.pdf")


def test_texto_longo_e_cortado(monkeypatch):
    longo = "<p>" + "medida 18 mm " * 5000 + "</p>"
    monkeypatch.setattr(web, "_baixar", lambda url, cabecalhos=None: (longo.encode(), "text/html", "utf-8", url))
    pagina = web.ler_pagina("https://site.example/longa")
    assert pagina["cortado"] and len(pagina["texto"]) == web.LIMITE_DO_TEXTO


# ---------- Pesquisa ----------


def test_sem_chave_pesquisa_na_wikipedia_em_ingles_primeiro(monkeypatch):
    pedidos = []

    def responder(url, cabecalhos=None):
        pedidos.append(url)
        idioma = url.split("//")[1][:2]
        return {"query": {"search": [{"title": f"Bateria {idioma} {i}", "snippet": "<span>18 mm</span>"} for i in range(5)]}}

    monkeypatch.setattr(web, "_json", responder)
    resultado = web.pesquisar("18650 battery")
    assert resultado["fonte"] == "Wikipédia"
    assert [r["titulo"] for r in resultado["resultados"]][:2] == ["Bateria en 0", "Bateria en 1"]
    assert len(resultado["resultados"]) == web.RESULTADOS and resultado["resultados"][-1]["titulo"].startswith("Bateria pt")
    assert resultado["resultados"][0]["url"] == "https://en.wikipedia.org/wiki/Bateria_en_0"
    assert resultado["resultados"][0]["trecho"] == "18 mm"


def test_com_chave_pesquisa_no_brave_com_a_chave_no_cabecalho(monkeypatch):
    recebidos = []

    def responder(url, cabecalhos=None):
        recebidos.append((url, cabecalhos))
        return {"web": {"results": [{"title": "18650 <b>specs</b>", "url": "https://loja.example/18650", "description": "18 x 65 mm"}]}}

    monkeypatch.setattr(web, "_json", responder)
    resultado = web.pesquisar("bateria 18650", chave_brave="chave-brave")
    assert resultado == {"fonte": "Brave Search", "resultados": [{"titulo": "18650 specs", "url": "https://loja.example/18650", "trecho": "18 x 65 mm"}]}
    url, cabecalhos = recebidos[0]
    assert url.startswith("https://api.search.brave.com/") and "chave-brave" not in url
    assert cabecalhos["X-Subscription-Token"] == "chave-brave"


def test_app_se_identifica_e_nao_finge_ser_navegador():
    assert web.AGENTE.startswith("Forja3D/") and "Mozilla" not in web.AGENTE


def test_pesquisa_vazia_e_recusada():
    with pytest.raises(web.ErroDaWeb):
        web.pesquisar("   ")


# ---------- No agente ----------


def test_agente_pesquisa_e_recebe_o_aviso_de_conteudo_de_terceiros(config, perfil, monkeypatch):  # noqa: F811
    monkeypatch.setattr(web, "pesquisar", lambda consulta, chave="": {"fonte": "Wikipédia", "resultados": [{"titulo": consulta, "url": "https://x", "trecho": ""}]})
    a, cliente = agente(config, perfil, [chamada("pesquisar_na_web", {"consulta": "18650 battery"}), texto("Achei: 18 × 65 mm.")])
    assert a.processar("Case para 4 baterias 18650") == "Achei: 18 × 65 mm."
    resultado = resultados_das_ferramentas(cliente.chamadas[-1]["mensagens"])[0]
    assert resultado["ok"] and resultado["aviso"] == AVISO_DA_WEB and resultado["resultados"][0]["titulo"] == "18650 battery"


def test_erro_da_web_volta_para_o_agente_sem_derrubar(config, perfil, monkeypatch):  # noqa: F811
    def falhar(_url):
        raise web.ErroDaWeb("Só leio páginas públicas da internet, nunca endereços da rede local.")

    monkeypatch.setattr(web, "ler_pagina", falhar)
    a, cliente = agente(config, perfil, [chamada("ler_pagina_web", {"url": "http://192.168.10.1"}), texto("Não deu.")])
    assert a.processar("Leia o roteador") == "Não deu."
    assert resultados_das_ferramentas(cliente.chamadas[-1]["mensagens"])[0] == {"ok": False, "erro": "Só leio páginas públicas da internet, nunca endereços da rede local."}


def test_mcp_nao_oferece_a_pesquisa_do_app():
    assert not SO_DO_APP & {d["name"] for d in definicoes()}


def test_chave_do_brave_e_salva_e_escondida_dos_logs(monkeypatch, tmp_path):
    from forja3d import configuracao

    monkeypatch.setenv("APPDATA", str(tmp_path))
    configuracao.salvar_preferencias("http://u/v1", "k", "m", "", "", chave_brave="chave-do-brave-123")
    carregada = configuracao.carregar()
    assert carregada.chave_brave == "chave-do-brave-123" and "chave-do-brave-123" in carregada.chaves
    assert "chave-do-brave-123" not in repr(carregada)
    assert json.dumps("chave-do-brave-123") in (tmp_path / "Forja3D" / "config.toml").read_text(encoding="utf-8")
