"""Ligar e desligar o MCP: a chave nas Configurações, o config.toml do Codex e a versão na barra de baixo."""
import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FORJA3D_SEM_3D", "1")

from PySide6.QtWidgets import QApplication, QMessageBox

from forja3d import configuracao
from forja3d.configuracao import ConfigIA, Configuracao
from forja3d.interface.dialogo_config import DialogoConfig
from forja3d.mcp import clientes
from forja3d.mcp.servidor import DESLIGADO, ForjaMCP
from forja3d.versao import VERSAO, texto_da_versao

RAIZ = Path(__file__).resolve().parents[1]
DO_CODEX = """model = "gpt"
notify = [
  "a",
  "b",
]

[windows]
sandbox = "elevated"

[mcp_servers.linear]
url = "https://exemplo/mcp"

[mcp_servers.node_repl]
command = 'C:\\x\\node.exe'
args = []

[mcp_servers.node_repl.env]
A = "1"

[features]
goals = true
"""


@pytest.fixture
def codex(tmp_path, monkeypatch):
    pasta = tmp_path / "codex"
    pasta.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(pasta))
    return pasta / "config.toml"


def servidores(caminho):
    return tomllib.loads(caminho.read_text(encoding="utf-8")).get("mcp_servers", {})


# ---------- O config.toml do Codex ----------


def test_ligar_acrescenta_o_bloco_e_nao_muda_o_resto(codex):
    codex.write_text(DO_CODEX, encoding="utf-8", newline="\n")
    assert "Abra uma conversa nova" in clientes.ligar_no_codex(True)
    texto = codex.read_text(encoding="utf-8")
    assert texto.startswith(DO_CODEX)
    forja = servidores(codex)["forja3d"]
    assert (forja["command"], forja["args"]) == clientes.comando_do_servidor()
    assert forja["tool_timeout_sec"] == 900
    assert "APPDATA" in forja["env_vars"]
    assert set(servidores(codex)) == {"linear", "node_repl", "forja3d"}
    assert clientes.estado_no_codex() == "ligado"
    assert (codex.parent / "config.toml.antes-do-forja3d").read_text(encoding="utf-8") == DO_CODEX


def test_ligar_de_novo_nao_muda_nada(codex):
    codex.write_text(DO_CODEX, encoding="utf-8")
    clientes.ligar_no_codex(True)
    antes = codex.read_bytes()
    assert "já estava ligado" in clientes.ligar_no_codex(True)
    assert codex.read_bytes() == antes


def test_desligar_tira_so_o_forja3d(codex):
    codex.write_text(DO_CODEX, encoding="utf-8", newline="\n")
    clientes.ligar_no_codex(True)
    clientes.ligar_no_codex(False)
    assert tomllib.loads(codex.read_text(encoding="utf-8")) == tomllib.loads(DO_CODEX)
    assert clientes.estado_no_codex() == "desligado"


def test_bloco_antigo_de_outro_caminho_e_trocado_com_as_subtabelas(codex):
    codex.write_text(DO_CODEX + "\n[mcp_servers.forja3d]\ncommand = 'C:\\\\velho\\\\python.exe'\nargs = []\n\n[mcp_servers.forja3d.env]\nX = \"1\"\n\n[depois]\nfica = true\n", encoding="utf-8")
    assert clientes.estado_no_codex() == "outro"
    clientes.ligar_no_codex(True)
    dados = tomllib.loads(codex.read_text(encoding="utf-8"))
    assert dados["depois"] == {"fica": True}
    assert "env" not in dados["mcp_servers"]["forja3d"]
    assert clientes.estado_no_codex() == "ligado"


def test_quebra_de_linha_do_windows_e_mantida(codex):
    codex.write_bytes(DO_CODEX.replace("\n", "\r\n").encode("utf-8"))
    clientes.ligar_no_codex(True)
    dados = codex.read_bytes()
    assert b"\r\n" in dados and b"\n" not in dados.replace(b"\r\n", b"")


def test_arquivo_vazio_ou_ausente(codex):
    clientes.ligar_no_codex(True)
    assert set(servidores(codex)) == {"forja3d"}


def test_codex_nao_instalado(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "nao_existe"))
    assert clientes.estado_no_codex() == "ausente"
    assert "não foi encontrado" in clientes.ligar_no_codex(True)
    assert not (tmp_path / "nao_existe").exists()


def test_toml_quebrado_nao_e_tocado(codex):
    codex.write_text("isto [ nao e toml", encoding="utf-8")
    with pytest.raises(clientes.ErroNoCodex, match="Nada foi mudado"):
        clientes.ligar_no_codex(True)
    assert codex.read_text(encoding="utf-8") == "isto [ nao e toml"


def test_formato_que_nao_sei_editar_nao_e_tocado(codex):
    """Um servidor declarado em linha, e não em tabela: tirar só o bloco deixaria o forja3d de antes no arquivo."""
    original = "mcp_servers = { forja3d = { command = 'x', args = [] } }\n"
    codex.write_text(original, encoding="utf-8")
    with pytest.raises(clientes.ErroNoCodex, match="não mexi"):
        clientes.ligar_no_codex(False)
    assert codex.read_text(encoding="utf-8") == original


def test_comando_do_claude_code():
    comando, argumentos = clientes.comando_do_servidor()
    texto = clientes.comando_do_claude_code()
    assert texto.startswith("claude mcp add forja3d --scope user -- ")
    assert f'"{comando}"' in texto and all(f'"{a}"' in texto for a in argumentos)


# ---------- A configuração do Forja3D ----------


@pytest.fixture
def preferencias(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path / "dados"))
    monkeypatch.setattr(configuracao, "PASTA_CONFIG", tmp_path)
    return tmp_path


def test_ligado_por_padrao_e_desligado_quando_salvo(preferencias):
    assert configuracao.carregar().mcp_ligado
    configuracao.salvar_preferencias("http://u/v1", "k", "m", "", "", mcp_ligado=False)
    assert not configuracao.carregar().mcp_ligado
    configuracao.salvar_preferencias("http://u/v1", "k", "m", "", "")
    assert configuracao.carregar().mcp_ligado


@pytest.mark.skipif(os.name != "nt", reason="Pasta do Windows")
def test_pasta_do_usuario_sem_appdata_no_ambiente(monkeypatch):
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.delenv("USERPROFILE", raising=False)
    monkeypatch.delenv("HOME", raising=False)
    pasta = configuracao.pasta_usuario()
    assert pasta.name == "Forja3D" and pasta.parent.name.lower() == "roaming"


def test_servidor_desligado_recusa_tudo(tmp_path, perfil):
    config = Configuracao(ia=ConfigIA(endpoint="", api_key="", modelo=""), pasta_projetos=tmp_path)
    conteudo, erro = ForjaMCP(config, perfil, lambda _p: None, lambda: False).chamar("listar_pecas", {})
    assert erro
    assert json.loads(conteudo[0]["text"])["erro"] == DESLIGADO
    assert not list(tmp_path.iterdir())


def test_servidor_sobe_sem_as_variaveis_do_usuario(tmp_path):
    """Como o Codex pode abrir: só PATH e SYSTEMROOT. Antes, o servidor caía sem achar a pasta do usuário."""
    ambiente = {k: os.environ[k] for k in ("PATH", "SYSTEMROOT") if k in os.environ}
    mensagens = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "ler_regras", "arguments": {}}},
    ]
    entrada = "".join(json.dumps(m) + "\n" for m in mensagens).encode("utf-8")
    saida = subprocess.run([sys.executable, str(RAIZ / "scripts" / "servidor_mcp.py")], input=entrada, capture_output=True, env=ambiente, cwd=tmp_path, timeout=120, check=False)
    assert saida.returncode == 0, saida.stderr.decode("utf-8", "replace")[-2000:]
    respostas = [json.loads(linha) for linha in saida.stdout.decode("utf-8").splitlines()]
    assert [r["id"] for r in respostas] == [1, 2]
    assert "result" in respostas[1]


# ---------- Na tela ----------


def abrir(qtbot, ligado=True):
    dialogo = DialogoConfig(Configuracao(ia=ConfigIA(endpoint="http://u/v1", api_key="k", modelo="m"), mcp_ligado=ligado))
    qtbot.addWidget(dialogo)
    dialogo.seletor_chat.combo_modelo.setEditText("m")
    dialogo.seletor_imagem.combo_modelo.setEditText("gemini-imagem")
    return dialogo


def test_dialogo_mostra_a_chave_e_o_estado(qtbot, preferencias, codex):
    codex.write_text(DO_CODEX, encoding="utf-8")
    dialogo = abrir(qtbot)
    assert dialogo.caixa_mcp.isChecked()
    assert "ao salvar, o Forja3D entra" in dialogo.rotulo_mcp.text()
    dialogo.caixa_mcp.setChecked(False)
    assert "recusa tudo" in dialogo.rotulo_mcp.text()


def test_salvar_ligado_poe_no_codex_e_desligado_tira(qtbot, preferencias, codex):
    codex.write_text(DO_CODEX, encoding="utf-8")
    dialogo = abrir(qtbot)
    dialogo._salvar()
    assert "forja3d" in servidores(codex)
    assert configuracao.carregar().mcp_ligado
    dialogo = abrir(qtbot)
    dialogo.caixa_mcp.setChecked(False)
    dialogo._salvar()
    assert "forja3d" not in servidores(codex)
    assert not configuracao.carregar().mcp_ligado


def test_sem_codex_salvar_so_guarda_a_preferencia(qtbot, preferencias):
    dialogo = abrir(qtbot)
    assert "não foi encontrado" in dialogo.rotulo_mcp.text()
    dialogo.caixa_mcp.setChecked(False)
    dialogo._salvar()
    assert not configuracao.carregar().mcp_ligado


def test_problema_no_codex_avisa_mas_salva(qtbot, preferencias, codex, monkeypatch):
    codex.write_text("isto [ nao e toml", encoding="utf-8")
    avisos = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: avisos.append(a[2]))
    dialogo = abrir(qtbot)
    monkeypatch.setattr(clientes, "estado_no_codex", lambda: "desligado")
    dialogo._salvar()
    assert "Nada foi mudado" in avisos[0]
    assert configuracao.carregar().mcp_ligado


def test_copiar_o_comando_do_claude_code(qtbot, preferencias):
    dialogo = abrir(qtbot)
    dialogo.botao_claude.click()
    assert QApplication.clipboard().text() == clientes.comando_do_claude_code()


# ---------- Versão ----------


def test_versao_igual_a_do_pyproject():
    assert VERSAO == tomllib.loads((RAIZ / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]


def test_texto_da_versao():
    texto = texto_da_versao()
    assert texto.startswith(f"Forja3D {VERSAO}, atualização de ")
