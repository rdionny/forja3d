"""Instalador: o que muda com o app empacotado, a chave fora do pacote e os dois cliques em um arquivo."""
import json
import os
import subprocess
import sys
import time
import tomllib
import zipfile
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FORJA3D_SEM_3D", "1")

from PySide6.QtWidgets import QDialog, QFileDialog, QLabel, QMessageBox

from forja3d import __main__ as entrada
from forja3d import configuracao
from forja3d.configuracao import ConfigIA, Configuracao
from forja3d.ia.cliente import ClienteIA, ErroIA
from forja3d.interface import aplicativo
from forja3d.interface.dialogo_config import DialogoConfig
from forja3d.interface.instancia import InstanciaUnica
from forja3d.interface.janela_principal import JanelaPrincipal
from forja3d.projetos.armazenamento import Projeto
from test_compartilhar import ClienteSimples, aberta, janela, projeto, salvo  # noqa: F401

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "scripts"))
sys.path.insert(0, str(RAIZ / "instalador"))

import conferir_pacote  # noqa: E402
import gerar_instalador  # noqa: E402
from lista_do_pacote import dados_do_app  # noqa: E402

SEGREDO = "segredo-de-teste-1234567890"
ESPERA_DA_JANELA_OCUPADA_S = 4.0


# ---------- App empacotado ----------


def rodar_como_empacotado(tmp_path, codigo):
    """Roda em outro processo: os caminhos são decididos quando o módulo é importado."""
    instalado = tmp_path / "instalado"
    interna = instalado / "_internal"
    (interna / "config").mkdir(parents=True, exist_ok=True)
    preparo = f"""
import json, sys
sys.path.insert(0, {str(RAIZ / 'src')!r})
sys.frozen = True
sys._MEIPASS = {str(interna)!r}
sys.executable = {str(instalado / 'Forja3D.exe')!r}
"""
    ambiente = {**os.environ, "APPDATA": str(tmp_path / "appdata")}
    for variavel in ("FORJA3D_IA_ENDPOINT", "FORJA3D_IA_API_KEY", "FORJA3D_IA_MODELO"):
        ambiente.pop(variavel, None)
    saida = subprocess.run([sys.executable, "-c", preparo + codigo], capture_output=True, text=True, encoding="utf-8", env=ambiente, check=True)
    return json.loads(saida.stdout), instalado, interna


def test_empacotado_le_tudo_de_dentro_do_pacote_e_grava_fora_dele(tmp_path):
    dados, instalado, interna = rodar_como_empacotado(
        tmp_path,
        """
from forja3d import configuracao
from forja3d.fatiamento.orca import pasta_dados
print(json.dumps({
    "config": str(configuracao.PASTA_CONFIG),
    "orca": str(configuracao.ORCA_PADRAO),
    "projetos": str(configuracao.carregar().pasta_projetos),
    "dados_orca": str(pasta_dados()),
}))
""",
    )
    assert Path(dados["config"]) == interna / "config"
    assert Path(dados["orca"]).is_relative_to(instalado)
    assert not Path(dados["projetos"]).is_relative_to(instalado), "desinstalar não pode levar as peças"
    assert Path(dados["projetos"]).name == "Forja3D"
    assert not Path(dados["dados_orca"]).is_relative_to(instalado)


def test_empacotado_nao_le_a_chave_nem_com_o_arquivo_na_pasta(tmp_path):
    dados, _, interna = rodar_como_empacotado(
        tmp_path,
        f"""
from pathlib import Path
from forja3d import configuracao
config = configuracao.PASTA_CONFIG
config.mkdir(parents=True, exist_ok=True)
(config / "ia_padrao.toml").write_text('[ia]\\napi_key = "{SEGREDO}"\\nendpoint = "http://dono/v1"\\n', encoding="utf-8")
c = configuracao.carregar()
print(json.dumps({{"chave": c.ia.api_key, "endpoint": c.ia.endpoint, "configurada": c.ia_configurada}}))
""",
    )
    assert dados == {"chave": "", "endpoint": "", "configurada": False}
    assert (interna / "config" / "ia_padrao.toml").exists()


def test_empacotado_le_as_preferencias_de_quem_instalou(tmp_path):
    dados, _, _ = rodar_como_empacotado(
        tmp_path,
        """
from forja3d import configuracao
configuracao.salvar_preferencias("http://meu/v1", "minha-chave", "meu-modelo", "", "")
c = configuracao.carregar()
print(json.dumps({"endpoint": c.ia.endpoint, "configurada": c.ia_configurada}))
""",
    )
    assert dados == {"endpoint": "http://meu/v1", "configurada": True}


def test_auxiliares_reusam_o_executavel_sem_pythonpath(tmp_path):
    dados, instalado, _ = rodar_como_empacotado(
        tmp_path,
        """
from forja3d.cad.executor import ambiente_limpo, comando_auxiliar
print(json.dumps({"comando": comando_auxiliar("--worker-cad", "pasta"), "ambiente": sorted(ambiente_limpo())}))
""",
    )
    assert dados["comando"] == [str(instalado / "Forja3D.exe"), "--worker-cad", "pasta"]
    assert "PYTHONPATH" not in dados["ambiente"]


def test_padroes_do_pacote_so_diferem_no_que_cada_pessoa_informa():
    pacote = tomllib.loads((RAIZ / "config" / "ia_padrao.pacote.toml").read_text(encoding="utf-8"))
    exemplo = tomllib.loads((RAIZ / "config" / "ia_padrao.example.toml").read_text(encoding="utf-8"))
    assert (pacote["ia"]["endpoint"], pacote["ia"]["api_key"], pacote["ia"]["modelo"]) == ("", "", "")
    pessoais = {"endpoint", "api_key", "modelo", "modelo_imagem"}
    assert {c: v for c, v in pacote["ia"].items() if c not in pessoais} == {c: v for c, v in exemplo["ia"].items() if c not in pessoais}
    assert pacote["agente"] == exemplo["agente"]


# ---------- Linha de comando ----------


def test_um_arquivo_forja3d_vai_para_o_app(monkeypatch, tmp_path):
    abertos = []
    monkeypatch.setattr(aplicativo, "executar", lambda arquivo=None: abertos.append(arquivo) or 0)
    arquivo = tmp_path / "Minha Peça.FORJA3D"
    assert entrada.main([str(arquivo)]) == 0
    assert abertos == [arquivo.resolve()]


def test_sem_argumentos_abre_o_app_sem_arquivo(monkeypatch):
    abertos = []
    monkeypatch.setattr(aplicativo, "executar", lambda arquivo=None: abertos.append(arquivo) or 0)
    assert entrada.main([]) == 0
    assert abertos == [None]


def test_falha_ao_abrir_avisa_e_deixa_os_detalhes(monkeypatch, tmp_path):
    def quebrar(_arquivo=None):
        raise ImportError("DLL load failed while importing vtkCommonCore")

    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(aplicativo, "executar", quebrar)
    avisos = []
    monkeypatch.setattr(entrada.ctypes.windll.user32, "MessageBoxW", lambda _j, texto, _t, _i: avisos.append(texto))
    assert entrada.main([]) == 1
    assert "não conseguiu abrir" in avisos[0]
    assert "vtkCommonCore" in (tmp_path / "Forja3D" / "logs" / "falha_ao_abrir.txt").read_text(encoding="utf-8")


def test_saidas_ausentes_nao_derrubam_o_programa(monkeypatch):
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    entrada._garantir_saidas()
    sys.stderr.write("sem console")
    sys.stdout.write("sem console")


# ---------- Primeiro uso ----------


def sem_ia(tmp_path):
    return Configuracao(ia=ConfigIA(endpoint="", api_key="", modelo=""), pasta_projetos=tmp_path / "projetos")


def test_primeiro_uso_abre_as_configuracoes_e_recarrega(qtbot, monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(configuracao, "_padroes", dict)

    def preencher(dialogo):
        configuracao.salvar_preferencias("http://meu/v1", SEGREDO, "meu-modelo", "", "")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(DialogoConfig, "exec", preencher)
    novo = aplicativo.configurar_no_primeiro_uso(sem_ia(tmp_path))
    assert novo.ia_configurada and novo.ia.endpoint == "http://meu/v1"


def test_quem_ja_configurou_nao_ve_a_tela(monkeypatch, tmp_path):
    monkeypatch.setattr(DialogoConfig, "exec", lambda _d: pytest.fail("não devia abrir"))
    config = Configuracao(ia=ConfigIA(endpoint="http://x", api_key="k", modelo="m"), pasta_projetos=tmp_path)
    assert aplicativo.configurar_no_primeiro_uso(config) is config


def test_cancelar_o_primeiro_uso_abre_o_app_sem_ia(qtbot, monkeypatch, tmp_path, perfil):
    monkeypatch.setattr(DialogoConfig, "exec", lambda _d: QDialog.DialogCode.Rejected)
    config = aplicativo.configurar_no_primeiro_uso(sem_ia(tmp_path))
    assert not config.ia_configurada
    j = JanelaPrincipal(config, perfil, ClienteIA)
    qtbot.addWidget(j)
    assert "não configurada" in j.rotulo_ia.text()
    j.close()


def test_cliente_sem_endpoint_avisa_em_vez_de_derrubar(tmp_path):
    with pytest.raises(ErroIA):
        ClienteIA(ConfigIA(endpoint="", api_key="", modelo="", timeout_s=2, max_tentativas_rede=0)).listar_modelos()


def test_dialogo_explica_o_primeiro_uso(qtbot, tmp_path):
    dialogo = DialogoConfig(sem_ia(tmp_path))
    qtbot.addWidget(dialogo)
    assert any("Para começar" in r.text() for r in dialogo.findChildren(QLabel))


# ---------- Uma janela só ----------


@pytest.fixture
def primeira(qtbot, monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    instancia = InstanciaUnica()
    assert instancia.assumir()
    instancia.pedidos = []
    instancia.recebido.connect(instancia.pedidos.append)
    return instancia


def segunda_abertura(arquivo):
    """Em outro processo, como acontece de verdade: o pipe só anda quando a outra ponta lê."""
    codigo = f"""
import sys
from pathlib import Path
sys.path.insert(0, {str(RAIZ / 'src')!r})
from PySide6.QtCore import QCoreApplication
from forja3d.interface.instancia import InstanciaUnica
aplicativo = QCoreApplication([])
instancia = InstanciaUnica()
if instancia.assumir():
    sys.exit(3)
sys.exit(0 if instancia.entregar({None if arquivo is None else str(arquivo)!r} and Path({str(arquivo)!r})) else 4)
"""
    return subprocess.Popen([sys.executable, "-c", codigo], env=os.environ.copy())


def test_segunda_abertura_entrega_o_arquivo(qtbot, primeira, tmp_path):
    arquivo = tmp_path / "Peça em ação.forja3d"
    processo = segunda_abertura(arquivo)
    qtbot.waitUntil(lambda: bool(primeira.pedidos), timeout=20000)
    assert processo.wait(20) == 0
    assert primeira.pedidos == [arquivo]


def test_segunda_abertura_espera_a_janela_que_ainda_carrega(qtbot, primeira, tmp_path):
    arquivo = tmp_path / "peca.forja3d"
    processo = segunda_abertura(arquivo)
    # Sem atender eventos, como durante a importação do VTK: a entrega tem de esperar, não se perder.
    time.sleep(ESPERA_DA_JANELA_OCUPADA_S)
    assert processo.poll() is None
    qtbot.waitUntil(lambda: bool(primeira.pedidos), timeout=20000)
    assert processo.wait(20) == 0
    assert primeira.pedidos == [arquivo]


def test_segunda_abertura_sem_arquivo_so_chama_a_janela(qtbot, primeira):
    processo = segunda_abertura(None)
    qtbot.waitUntil(lambda: bool(primeira.pedidos), timeout=20000)
    assert processo.wait(20) == 0
    assert primeira.pedidos == [None]


def test_pedido_que_nao_e_um_projeto_e_ignorado(qtbot, primeira, tmp_path):
    processo = segunda_abertura(tmp_path / "programa.exe")
    qtbot.waitUntil(lambda: processo.poll() is not None, timeout=20000)
    processo = segunda_abertura(tmp_path / "certo.forja3d")
    qtbot.waitUntil(lambda: bool(primeira.pedidos), timeout=20000)
    processo.wait(20)
    assert primeira.pedidos == [tmp_path / "certo.forja3d"]


def test_janela_fechada_libera_a_trava(qtbot, monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    primeira = InstanciaUnica()
    assert primeira.assumir()
    primeira._trava.unlock()
    assert InstanciaUnica().assumir()


# ---------- Dois cliques com o app aberto ----------


def test_arquivo_recebido_passa_pela_mesma_conferencia(qtbot, janela, salvo, monkeypatch):  # noqa: F811
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_a, **_k: pytest.fail("não devia perguntar"))
    janela.receber_pedido(salvo)
    qtbot.waitUntil(lambda: janela.projeto is not None and not janela.ocupado, timeout=20000)
    assert janela.rotulo_projeto.text() == "Minha peça"
    assert janela.orquestrador.resultado_verificacao is not None, "a verificação foi refeita aqui"


def test_arquivo_recebido_invalido_avisa(qtbot, aberta, tmp_path, monkeypatch):  # noqa: F811
    falso = tmp_path / "falso.forja3d"
    falso.write_bytes(b"nao sou um zip")
    avisos = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **_k: avisos.append(a[2]))
    aberta.receber_pedido(falso)
    qtbot.waitUntil(lambda: not aberta.ocupado and bool(avisos), timeout=10000)
    assert "não é um projeto" in avisos[0]
    assert aberta.projeto.titulo == "Peça aberta"


def test_arquivo_recebido_com_o_agente_trabalhando_espera(qtbot, aberta, salvo):  # noqa: F811
    aberta._definir_ocupado(True)
    aberta.receber_pedido(salvo)
    assert aberta.projeto.titulo == "Peça aberta", "não troca a peça no meio da tarefa"
    assert "abre quando" in aberta.statusBar().currentMessage()
    aberta._definir_ocupado(False)
    aberta._ao_concluir()
    qtbot.waitUntil(lambda: aberta.projeto.titulo == "Minha peça" and not aberta.ocupado, timeout=20000)


def test_dois_arquivos_recebidos_abrem_em_sequencia(qtbot, aberta, salvo, tmp_path):  # noqa: F811
    segundo = tmp_path / "segundo.forja3d"
    segundo.write_bytes(salvo.read_bytes())
    aberta.receber_pedido(salvo)
    aberta.receber_pedido(segundo)
    qtbot.waitUntil(lambda: not aberta._arquivos_recebidos and not aberta.ocupado and aberta.projeto.titulo == "Minha peça", timeout=30000)
    assert len(Projeto.listar(aberta.config.pasta_projetos)) == 3


def test_pedido_sem_arquivo_nao_muda_nada(aberta):  # noqa: F811
    aberta.receber_pedido(None)
    assert aberta.projeto.titulo == "Peça aberta" and not aberta.ocupado


# ---------- Conferência do pacote ----------


def test_lista_branca_nao_leva_a_chave_nem_a_pasta_inteira():
    pares = dados_do_app(RAIZ)
    nomes = {Path(origem).name for origem, _ in pares}
    assert "ia_padrao.toml" not in nomes and "ia_padrao.example.toml" not in nomes
    assert all(Path(origem).is_file() for origem, _ in pares), "a lista só tem arquivos, nunca uma pasta"
    assert {"ia_padrao.pacote.toml", "impressora_ender3_v3_se.json", "ferramentas.json", "maquina.json", "agente_3d.md", "copiar.svg"} <= nomes
    icones = {a.name for a in (RAIZ / "src" / "forja3d" / "interface" / "icones").glob("*.svg")}
    assert icones <= nomes


def segredos_de_teste():
    return {SEGREDO: "chave de teste"}


def test_acha_a_chave_em_texto_aberto_e_em_utf16(tmp_path):
    (tmp_path / "a.txt").write_text(f"x{SEGREDO}x", encoding="utf-8")
    (tmp_path / "b.dll").write_bytes(SEGREDO.encode("utf-16-le"))
    (tmp_path / "limpo.txt").write_text("nada aqui", encoding="utf-8")
    problemas = conferir_pacote.procurar_segredos(tmp_path, segredos_de_teste())
    assert len(problemas) == 2
    assert not any(SEGREDO in p for p in problemas), "o relatório nunca mostra a chave"


def test_acha_a_chave_dentro_de_zip(tmp_path):
    with zipfile.ZipFile(tmp_path / "base_library.zip", "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("modulo.py", f"chave = '{SEGREDO}'")
    assert SEGREDO.encode() not in (tmp_path / "base_library.zip").read_bytes(), "a compressão esconde de uma busca comum"
    problemas = conferir_pacote.procurar_segredos(tmp_path, segredos_de_teste())
    assert len(problemas) == 1 and "modulo.py" in problemas[0]


@pytest.mark.skipif(os.name != "nt", reason="A conferência é do pacote do instalador do Windows, que é um .exe")
def test_acha_a_chave_dentro_do_executavel_do_pyinstaller(tmp_path):
    """Um pacote de verdade: o código vai compactado dentro do .exe."""
    script = tmp_path / "isca.py"
    script.write_text(f"CHAVE = '{SEGREDO}'\nprint(CHAVE)\n", encoding="utf-8")
    subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--noconfirm", "--log-level", "ERROR", "--distpath", str(tmp_path / "dist"), "--workpath", str(tmp_path / "w"), "--specpath", str(tmp_path), str(script)],
        check=True,
        capture_output=True,
    )
    pacote = tmp_path / "dist" / "isca"
    assert SEGREDO.encode() not in (pacote / "isca.exe").read_bytes(), "a compressão esconde de uma busca comum"
    problemas = conferir_pacote.procurar_segredos(pacote, segredos_de_teste())
    assert any("isca.exe >" in p for p in problemas), problemas


def test_recusa_arquivos_que_nao_podem_ir(tmp_path):
    for nome in ("ia_padrao.toml", "PLANEJAMENTO.md", "PROMPT_DESENVOLVIMENTO.md", "peca.forja3d", "00000.log"):
        (tmp_path / nome).write_bytes(b"x")
    (tmp_path / "pode.txt").write_bytes(b"x")
    problemas = conferir_pacote.procurar_arquivos_proibidos(tmp_path)
    assert len(problemas) == 5 and not any("pode.txt" in p for p in problemas)


def test_lista_branca_acusa_o_que_falta_e_o_que_sobra(tmp_path):
    interna = tmp_path / "_internal"
    for origem, destino in dados_do_app(RAIZ):
        alvo = interna / destino / Path(origem).name
        alvo.parent.mkdir(parents=True, exist_ok=True)
        alvo.write_bytes(b"x")
    for caminho in conferir_pacote.ARQUIVOS_NECESSARIOS:
        (tmp_path / caminho).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / caminho).write_bytes(b"x")
    assert conferir_pacote.conferir_lista_branca(tmp_path) == []
    (interna / "config" / "ia_padrao.toml").write_bytes(b"x")
    (interna / "config" / "impressora_ender3_v3_se.json").unlink()
    problemas = conferir_pacote.conferir_lista_branca(tmp_path)
    assert any("faltando" in p and "impressora_ender3" in p for p in problemas)
    assert any("fora da lista branca" in p and "ia_padrao.toml" in p for p in problemas)


def test_runtime_do_visual_cpp_nao_conta_como_windows():
    assert not conferir_pacote.vem_com_o_windows("msvcp140.dll")
    assert not conferir_pacote.vem_com_o_windows("vcruntime140_1.dll")
    assert not conferir_pacote.vem_com_o_windows("nao_existe_em_lugar_nenhum.dll")
    assert conferir_pacote.vem_com_o_windows("kernel32.dll")
    assert conferir_pacote.vem_com_o_windows("api-ms-win-crt-runtime-l1-1-0.dll")


def test_segredos_leem_todos_os_provedores(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    pasta = tmp_path / "Forja3D"
    pasta.mkdir()
    (pasta / "config.toml").write_text(
        f'[ia]\nendpoint = "http://padrao-longo/v1"\napi_key = "{SEGREDO}"\n[[provedores]]\nnome = "Outro"\nendpoint = "http://outro-longo/v1"\napi_key = "outra-chave-longa"\n',
        encoding="utf-8",
    )
    segredos = conferir_pacote.segredos_deste_computador()
    assert {SEGREDO, "outra-chave-longa", "http://padrao-longo/v1", "http://outro-longo/v1"} <= set(segredos)
    assert SEGREDO not in segredos.values()


# ---------- Geração ----------


def test_hash_do_orca_diferente_interrompe(tmp_path):
    falso = tmp_path / "orca.zip"
    falso.write_bytes(b"outro conteudo")
    with pytest.raises(SystemExit, match="não confere"):
        gerar_instalador.conferir_hash_do_orca(falso)


def test_hash_do_orca_correto_passa(tmp_path):
    bom = tmp_path / "orca.zip"
    bom.write_bytes(b"conteudo")
    gerar_instalador.conferir_hash_do_orca(bom, gerar_instalador.sha256(bom))


def test_icone_do_exe_tem_o_desenho_proprio_de_cada_tamanho(qapp, tmp_path, monkeypatch):
    from PIL import Image

    monkeypatch.setattr(gerar_instalador, "ICONE", tmp_path / "forja3d.ico")
    gerar_instalador.gerar_icone()
    with Image.open(tmp_path / "forja3d.ico") as icone:
        assert {16, 20, 24, 32, 40, 48, 64, 128, 256} == {lado for lado, _ in icone.ico.sizes()}
        grande = icone.ico.getimage((256, 256)).convert("RGBA")
        pequeno = icone.ico.getimage((16, 16)).convert("RGBA")
    assert pequeno.tobytes() != grande.resize((16, 16), Image.Resampling.LANCZOS).tobytes()


def test_desenhos_da_marca_vao_no_pacote():
    destinos = {Path(origem).name: destino for origem, destino in dados_do_app(RAIZ)}
    for nome in ("forja3d.svg", "forja3d_medio.svg", "forja3d_pequeno.svg", "logo.svg"):
        assert destinos[nome] == "forja3d/interface/icones"


def test_script_do_instalador_associa_a_extensao_sem_pedir_administrador():
    texto = (RAIZ / "instalador" / "forja3d.iss").read_text(encoding="utf-8-sig")
    assert "PrivilegesRequired=lowest" in texto
    assert "ChangesAssociations=yes" in texto
    assert 'Subkey: "Software\\Classes\\.forja3d"' in texto
    assert '"""{app}\\Forja3D.exe"" ""%1"""' in texto
    assert "ia_padrao" not in texto and "config\\" not in texto


def test_arquivos_do_instalador_vao_para_o_git():
    for caminho in ("instalador/forja3d.spec", "instalador/forja3d.iss", "instalador/lista_do_pacote.py", "config/ia_padrao.pacote.toml"):
        assert subprocess.run(["git", "check-ignore", "-q", caminho], cwd=RAIZ, check=False).returncode == 1, caminho
    for caminho in ("dist/", "build/", "outra_coisa.spec"):
        assert subprocess.run(["git", "check-ignore", "-q", caminho], cwd=RAIZ, check=False).returncode == 0, caminho
