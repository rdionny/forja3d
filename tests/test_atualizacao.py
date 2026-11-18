"""Atualização pelo próprio app: procura na página, confere o SHA-256 e só então instala."""
import functools
import hashlib
import io
import json
import os
import sys
import tarfile
import threading
import tomllib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QWidget

from forja3d import atualizacao, configuracao
from forja3d.atualizacao import ErroDeAtualizacao, Novidade, baixar, comando_do_instalador, procurar, trocar_pasta
from forja3d.interface import dialogo_atualizacao
from forja3d.interface.dialogo_atualizacao import DialogoAtualizacao, atualizar, instalar_e_fechar

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "scripts"))
import publicar_na_pagina  # noqa: E402

PACOTE = b"instalador de mentira" * 1000


class Silencioso(SimpleHTTPRequestHandler):
    def log_message(self, *_argumentos):
        pass


@pytest.fixture
def pagina(tmp_path):
    """Uma página de downloads de verdade, numa porta livre deste computador."""
    pasta = tmp_path / "pagina"
    pasta.mkdir()
    servidor = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Silencioso, directory=str(pasta)))
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    yield pasta, f"http://127.0.0.1:{servidor.server_port}"
    servidor.shutdown()


def publicar(pasta, versao="9.0.0", arquivo="Forja3D-9.0.0-pacote", conteudo=PACOTE, sha=None):
    (pasta / arquivo).write_bytes(conteudo)
    pacote = {"arquivo": arquivo, "sha256": sha or hashlib.sha256(conteudo).hexdigest()}
    (pasta / "atualizacao.json").write_text(json.dumps({"versao": versao, "notas": "- **Novidade** boa", "windows": pacote, "linux": pacote}), encoding="utf-8")


# ---------- Procurar ----------


def test_acha_a_versao_nova(pagina):
    pasta, endereco = pagina
    publicar(pasta)
    novidade = procurar(endereco, "0.2.1")
    assert novidade == Novidade("9.0.0", "- **Novidade** boa", "Forja3D-9.0.0-pacote", hashlib.sha256(PACOTE).hexdigest())


@pytest.mark.parametrize("instalada", ["9.0.0", "9.0.1", "10.0.0"])
def test_mesma_versao_ou_mais_nova_nao_avisa(pagina, instalada):
    pasta, endereco = pagina
    publicar(pasta)
    assert procurar(endereco, instalada) is None


def test_compara_numeros_e_nao_texto(pagina):
    pasta, endereco = pagina
    publicar(pasta, versao="0.10.0")
    assert procurar(endereco, "0.9.0") is not None


def test_fora_de_casa_o_app_abre_normalmente():
    assert procurar("http://127.0.0.1:9", "0.2.1") is None


@pytest.mark.parametrize("arquivo", ["../fora.exe", "pasta/x.exe", "..\\fora.exe", ".."])
def test_nome_de_arquivo_com_caminho_e_recusado(pagina, arquivo):
    pasta, endereco = pagina
    (pasta / "atualizacao.json").write_text(json.dumps({"versao": "9.0.0", "windows": {"arquivo": arquivo, "sha256": "0"}, "linux": {"arquivo": arquivo, "sha256": "0"}}), encoding="utf-8")
    assert procurar(endereco, "0.2.1") is None


def test_manifesto_quebrado_nao_derruba(pagina):
    pasta, endereco = pagina
    (pasta / "atualizacao.json").write_text("{nao é json", encoding="utf-8")
    assert procurar(endereco, "0.2.1") is None


# ---------- Baixar ----------


def test_baixa_e_confere_o_sha256(pagina, tmp_path):
    pasta, endereco = pagina
    publicar(pasta)
    progresso = []
    destino = baixar(endereco, procurar(endereco, "0.2.1"), tmp_path / "baixados", lambda feito, total: progresso.append((feito, total)))
    assert destino.read_bytes() == PACOTE
    assert progresso[-1] == (len(PACOTE), len(PACOTE))


def test_sha256_diferente_nao_deixa_nada_para_instalar(pagina, tmp_path):
    pasta, endereco = pagina
    publicar(pasta, sha="0" * 64)
    with pytest.raises(ErroDeAtualizacao, match="SHA-256"):
        baixar(endereco, procurar(endereco, "0.2.1"), tmp_path / "baixados")
    assert not list((tmp_path / "baixados").iterdir())


def test_cancelar_apaga_o_que_ja_baixou(pagina, tmp_path):
    pasta, endereco = pagina
    publicar(pasta)
    cancelar = threading.Event()
    cancelar.set()
    with pytest.raises(ErroDeAtualizacao, match="cancelada"):
        baixar(endereco, procurar(endereco, "0.2.1"), tmp_path / "baixados", cancelar=cancelar)
    assert not list((tmp_path / "baixados").iterdir())


# ---------- Instalar ----------


def test_instalador_do_windows_roda_em_silencio_e_abre_o_app_no_fim():
    comando = comando_do_instalador(Path("Forja3D-9.0.0-instalador.exe"))
    assert "/SILENT" in comando and "/abrir=1" in comando
    iss = (RAIZ / "instalador" / "forja3d.iss").read_text(encoding="utf-8-sig")
    assert "Check: AbrirDepois" in iss and "{param:abrir|0}" in iss


def tar_com(tmp_path, arquivos):
    caminho = tmp_path / "pacote.tar.gz"
    with tarfile.open(caminho, "w:gz") as tar:
        for nome, conteudo in arquivos.items():
            info = tarfile.TarInfo(nome)
            info.size = len(conteudo)
            info.mode = 0o755
            tar.addfile(info, io.BytesIO(conteudo))
    return caminho


def test_linux_troca_a_pasta_do_app_pela_nova(tmp_path):
    app = tmp_path / "Forja3D"
    app.mkdir()
    (app / "Forja3D").write_bytes(b"velho")
    (app / "so_no_velho.txt").write_bytes(b"x")
    trocar_pasta(tar_com(tmp_path, {"Forja3D/Forja3D": b"novo", "Forja3D/_internal/a.so": b"lib"}), app)
    assert (app / "Forja3D").read_bytes() == b"novo" and (app / "_internal" / "a.so").is_file()
    assert not (app / "so_no_velho.txt").exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["Forja3D", "pacote.tar.gz"], "nada de .nova nem .antiga"


def test_pacote_sem_o_app_deixa_a_versao_atual(tmp_path):
    app = tmp_path / "Forja3D"
    app.mkdir()
    (app / "Forja3D").write_bytes(b"velho")
    with pytest.raises(ErroDeAtualizacao):
        trocar_pasta(tar_com(tmp_path, {"outra_coisa/x": b"?"}), app)
    assert (app / "Forja3D").read_bytes() == b"velho"


# ---------- Na tela ----------


def test_dialogo_mostra_a_versao_e_as_novidades(qtbot):
    dialogo = DialogoAtualizacao(Novidade("9.0.0", "- **Novidade** boa", "x", "0"))
    qtbot.addWidget(dialogo)
    textos = " ".join(r.text() for r in dialogo.findChildren(QLabel))
    assert "9.0.0" in textos and "Novidade" in textos
    assert dialogo.botao_atualizar.text() == "Atualizar agora"


class JanelaQueRecusa(QWidget):
    def closeEvent(self, evento):  # noqa: N802 - nome definido pelo Qt.
        evento.ignore()


def test_janela_que_nao_fecha_nao_e_atualizada(qtbot, monkeypatch, tmp_path):
    instalados = []
    monkeypatch.setattr(atualizacao, "instalar", instalados.append)
    monkeypatch.setattr(dialogo_atualizacao.QMessageBox, "information", lambda *_a, **_k: None)
    janela = JanelaQueRecusa()
    qtbot.addWidget(janela)
    instalar_e_fechar(janela, tmp_path / "pacote")
    assert instalados == []


def test_atualizar_baixa_instala_e_fecha(qtbot, monkeypatch, pagina):
    pasta, endereco = pagina
    publicar(pasta)
    instalados, fechou = [], []
    monkeypatch.setattr(atualizacao, "instalar", lambda pacote: instalados.append(pacote.read_bytes()))
    monkeypatch.setattr(atualizacao, "pasta_de_download", lambda: pasta.parent / "baixados")
    monkeypatch.setattr(QApplication, "quit", lambda: fechou.append(True))
    janela = QWidget()
    qtbot.addWidget(janela)
    atualizar(janela, procurar(endereco, "0.2.1"), endereco)
    qtbot.waitUntil(lambda: bool(fechou), timeout=10000)
    assert instalados == [PACOTE]


# ---------- Configuração e publicação ----------


def test_endereco_das_atualizacoes_e_salvo(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    configuracao.salvar_preferencias("http://u/v1", "k", "m", "", "", atualizacao="http://192.168.0.5:8000/")
    assert configuracao.carregar().atualizacao == "http://192.168.0.5:8000/".rstrip("/")


def test_app_instalado_ja_vem_procurando_na_pagina_de_casa():
    pacote = tomllib.loads((RAIZ / "config" / "ia_padrao.pacote.toml").read_text(encoding="utf-8"))
    exemplo = tomllib.loads((RAIZ / "config" / "ia_padrao.example.toml").read_text(encoding="utf-8"))
    assert pacote["atualizacao"] == exemplo["atualizacao"] and pacote["atualizacao"]["endereco"].startswith("http")


def test_publicar_gera_o_manifesto_que_o_app_le(tmp_path, pagina):
    pasta, endereco = pagina
    exe, tar = pasta / "Forja3D-9.0.0-instalador.exe", pasta / "Forja3D-9.0.0-linux-x64.tar.gz"
    exe.write_bytes(b"exe")
    tar.write_bytes(b"tar")
    notas = publicar_na_pagina.novidades("## Novidades\n\n- Coisa nova\n\n## Downloads\n\n- links")
    dados = publicar_na_pagina.manifesto("9.0.0", notas, exe, tar)
    (pasta / "atualizacao.json").write_text(json.dumps(dados), encoding="utf-8")
    novidade = procurar(endereco, "0.2.1")
    assert novidade.notas == "- Coisa nova" and novidade.sha256 == hashlib.sha256(b"exe" if os.name == "nt" else b"tar").hexdigest()
    html = publicar_na_pagina.pagina(dados, exe, tar)
    assert "$" not in html and exe.name in html and tar.name in html
