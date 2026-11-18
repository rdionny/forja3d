"""O servidor MCP: o protocolo, as ferramentas com as mesmas travas do app e o aviso para a janela aberta."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FORJA3D_SEM_3D", "1")

from forja3d.configuracao import ConfigIA, Configuracao
from forja3d.fatiamento.orca import localizar_orca
from forja3d.interface.instancia import InstanciaUnica, avisar_que_mudou
from forja3d.interface.janela_principal import JanelaPrincipal
from forja3d.layout_mesa import LayoutMesa
from forja3d.mcp.protocolo import VERSOES, Servidor
from forja3d.mcp.servidor import PREFACIO, ForjaMCP, definicoes
from forja3d.projetos.armazenamento import Projeto
from test_orquestrador import ESPEC

RAIZ = Path(__file__).resolve().parents[1]
DADOS = Path(__file__).parent / "dados"
FERRAMENTAS = {d["name"]: d for d in definicoes()}


def pedido(identificador, metodo, parametros=None):
    return {"jsonrpc": "2.0", "id": identificador, "method": metodo, **({"params": parametros} if parametros is not None else {})}


# ---------- Protocolo ----------


def servidor_de_teste(chamadas=None):
    def chamar(nome, argumentos):
        if chamadas is not None:
            chamadas.append((nome, argumentos))
        return [{"type": "text", "text": "ok"}], False

    return Servidor("teste", "1.0", "instruções", [{"name": "eco", "inputSchema": {"type": "object"}}], chamar)


@pytest.mark.parametrize(("pedida", "respondida"), [(VERSOES[0], VERSOES[0]), ("2024-11-05", "2024-11-05"), ("1999-01-01", VERSOES[0])])
def test_initialize_combina_a_versao(pedida, respondida):
    resposta = servidor_de_teste().responder(pedido(1, "initialize", {"protocolVersion": pedida, "capabilities": {}, "clientInfo": {"name": "x", "version": "1"}}))
    assert resposta["result"]["protocolVersion"] == respondida
    assert resposta["result"]["capabilities"] == {"tools": {"listChanged": False}}
    assert resposta["result"]["instructions"] == "instruções"


def test_notificacao_nao_tem_resposta():
    assert servidor_de_teste().responder({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_metodo_desconhecido_e_pedido_invalido():
    servidor = servidor_de_teste()
    assert servidor.responder(pedido(2, "resources/list"))["error"]["code"] == -32601
    assert servidor.responder({"id": 3, "method": "ping"})["error"]["code"] == -32600
    assert servidor.responder(pedido(4, "ping")) == {"jsonrpc": "2.0", "id": 4, "result": {}}


def test_chamada_de_ferramenta():
    chamadas = []
    resposta = servidor_de_teste(chamadas).responder(pedido(5, "tools/call", {"name": "eco", "arguments": {"a": 1}}))
    assert resposta["result"] == {"content": [{"type": "text", "text": "ok"}], "isError": False}
    assert chamadas == [("eco", {"a": 1})]


def test_rodar_le_linha_por_linha_e_responde_json_quebrado():
    saida = _Saida()
    servidor_de_teste().rodar([b"", json.dumps(pedido(1, "ping")).encode() + b"\n", b"{quebrado\n", json.dumps([pedido(2, "ping"), pedido(3, "ping")]).encode()], saida)
    respostas = [json.loads(linha) for linha in saida.dados.decode().splitlines()]
    assert [r.get("id") for r in respostas[:2]] == [1, None]
    assert respostas[1]["error"]["code"] == -32700
    assert [r["id"] for r in respostas[2]] == [2, 3]


class _Saida:
    def __init__(self):
        self.dados = b""

    def write(self, dados):
        self.dados += dados

    def flush(self):
        pass


# ---------- O que o agente de fora pode fazer ----------


def test_ferramentas_do_app_mais_as_de_achar_e_criar_pecas():
    assert set(FERRAMENTAS) == {
        "ler_regras", "listar_pecas", "criar_peca", "ver_peca", "voltar_para_versao",
        "definir_especificacao", "executar_cad", "editar_cad", "verificar_peca", "renderizar_vistas", "definir_cores", "fatiar_peca",
    }


def test_toda_ferramenta_da_peca_pede_a_peca():
    for nome, definicao in FERRAMENTAS.items():
        if nome not in ("ler_regras", "listar_pecas", "criar_peca"):
            assert "peca" in definicao["inputSchema"]["required"], nome
        assert definicao["inputSchema"]["type"] == "object"


def test_nenhuma_ferramenta_aprova_exporta_ou_mexe_no_gcode():
    texto = json.dumps({nome: [nome, *sorted(d["inputSchema"]["properties"])] for nome, d in FERRAMENTAS.items()}).lower()
    for proibido in ("aprov", "export", "gcode", "g-code", "temperatura", "bico"):
        assert proibido not in texto


def test_leitura_marcada_como_somente_leitura():
    assert FERRAMENTAS["ver_peca"]["annotations"] == {"readOnlyHint": True}
    assert "annotations" not in FERRAMENTAS["executar_cad"]


@pytest.fixture
def forja(tmp_path, perfil):
    avisos = []
    config = Configuracao(ia=ConfigIA(endpoint="", api_key="", modelo=""), pasta_projetos=tmp_path / "projetos")
    return ForjaMCP(config, perfil, avisos.append, lambda: True), avisos


def chamar(forja, ferramenta, /, **argumentos):
    conteudo, erro = forja.chamar(ferramenta, argumentos)
    return json.loads(conteudo[0]["text"]), erro, conteudo


def test_regras_juntam_o_prefacio_e_o_prompt_do_app(forja):
    resultado, erro, _ = chamar(forja[0], "ler_regras")
    assert not erro
    assert resultado["regras"].startswith(PREFACIO)
    assert "Ender-3 V3 SE" in resultado["regras"]
    assert "{{CALIBRACAO_USUARIO}}" not in resultado["regras"]


def test_criar_e_listar(forja):
    servidor, avisos = forja
    criada, _, _ = chamar(servidor, "criar_peca", titulo="Suporte de teste")
    lista, _, _ = chamar(servidor, "listar_pecas")
    assert lista["pecas"] == [{"peca": criada["peca"], "titulo": "Suporte de teste", "versao_atual": None, "aprovada_no_app": False}]
    assert [a.name for a in avisos] == [criada["peca"]]


@pytest.mark.parametrize("peca", ["../fora", "..", "C:\\Windows", "a/b", "", None, 3, "nao_existe"])
def test_peca_que_nao_e_da_pasta_de_projetos_e_recusada(forja, peca):
    resultado, erro, _ = chamar(forja[0], "ver_peca", peca=peca)
    assert erro
    assert not resultado["ok"]


def test_ferramenta_desconhecida_e_erro_do_protocolo(forja):
    from forja3d.mcp.protocolo import FerramentaDesconhecida

    with pytest.raises(FerramentaDesconhecida):
        forja[0].chamar("exportar_gcode", {})


def test_travas_do_app_continuam_valendo(forja):
    servidor, _ = forja
    peca = chamar(servidor, "criar_peca")[0]["peca"]
    resultado, erro, _ = chamar(servidor, "executar_cad", peca=peca, codigo="x", resumo_alteracao="")
    assert erro
    assert "definir_especificacao" in resultado["erro"]
    resultado, erro, _ = chamar(servidor, "renderizar_vistas", peca=peca)
    assert erro


def test_peca_vazia_diz_por_onde_comecar(forja):
    servidor, _ = forja
    peca = chamar(servidor, "criar_peca")[0]["peca"]
    resultado, erro, _ = chamar(servidor, "ver_peca", peca=peca)
    assert not erro
    assert "definir_especificacao" in resultado["proximo_passo"]


@pytest.mark.cad
def test_fluxo_completo_de_um_agente_de_fora(forja):
    servidor, avisos = forja
    peca = chamar(servidor, "criar_peca", titulo="Suporte L")[0]["peca"]
    assert not chamar(servidor, "definir_especificacao", peca=peca, **ESPEC)[1]
    codigo = (DADOS / "suporte_l_corrigido.py").read_text(encoding="utf-8")
    resultado, erro, _ = chamar(servidor, "executar_cad", peca=peca, codigo=codigo, resumo_alteracao="primeira")
    assert not erro and resultado["versao"] == "v001"
    verificacao, erro, _ = chamar(servidor, "verificar_peca", peca=peca)
    assert verificacao["aprovado"], verificacao["bloqueios"]

    _, erro, conteudo = chamar(servidor, "renderizar_vistas", peca=peca)
    assert not erro
    imagens = [c for c in conteudo if c["type"] == "image"]
    assert len(imagens) == 6
    assert all(c["mimeType"] == "image/png" and c["data"].startswith("iVBOR") for c in imagens)

    estado, _, _ = chamar(servidor, "ver_peca", peca=peca)
    assert estado["versao_atual"] == "v001"
    assert estado["script"] == codigo
    assert estado["verificacao"]["aprovado"]
    assert estado["especificacao"]["nome"] == "suporte_l"

    projeto = Projeto(servidor.config.pasta_projetos / peca)
    assert any("Agente externo, pelo MCP: gerou a versão v001: primeira" in r["texto"] for r in projeto.ler_conversa())
    assert "Um agente externo, pelo MCP do Forja3D, gerou a versão v001" in projeto.ler_historico()[-1]["content"]

    fatiar = {"material": "PLA", "qualidade": "rascunho", "paredes": 2, "preenchimento_pct": 10, "suporte": False, "brim": False}
    resultado, erro, _ = chamar(servidor, "fatiar_peca", peca=peca, **fatiar)
    assert erro and "Aprovar e preparar" in resultado["erro"]

    # Uma segunda versão, e a volta para a primeira.
    assert not chamar(servidor, "executar_cad", peca=peca, codigo=codigo, resumo_alteracao="de novo")[1]
    resultado, erro, _ = chamar(servidor, "voltar_para_versao", peca=peca, versao="v001")
    assert not erro and Projeto(projeto.pasta).estado["versao_atual"] == "v001"
    assert chamar(servidor, "voltar_para_versao", peca=peca, versao="v009")[1]
    # Ler e renderizar não mudam nada: a janela aberta não precisa reler.
    assert [a.name for a in avisos].count(peca) == 7


@pytest.mark.cad
@pytest.mark.orca
@pytest.mark.skipif(localizar_orca() is None, reason="OrcaSlicer não encontrado")
def test_fatiar_depois_que_o_usuario_aprova_no_app(forja):
    from forja3d.agente.orquestrador import Orquestrador
    from forja3d.mcp.servidor import SemIA

    servidor, _ = forja
    peca = chamar(servidor, "criar_peca")[0]["peca"]
    chamar(servidor, "definir_especificacao", peca=peca, **ESPEC)
    chamar(servidor, "executar_cad", peca=peca, codigo=(DADOS / "suporte_l_corrigido.py").read_text(encoding="utf-8"), resumo_alteracao="")
    chamar(servidor, "verificar_peca", peca=peca)
    projeto = Projeto(servidor.config.pasta_projetos / peca)
    # O que o botão Aprovar e preparar faz no app.
    Orquestrador(servidor.config, servidor.perfil, SemIA(), projeto).aprovar()
    resultado, erro, _ = chamar(servidor, "fatiar_peca", peca=peca, material="PLA", qualidade="rascunho", paredes=2, preenchimento_pct=10, suporte=False, brim=False)
    assert not erro and resultado["aprovado"], resultado
    assert any("preparou o G-code da versão v001" in r["texto"] for r in Projeto(projeto.pasta).ler_conversa())


# ---------- Pelo stdin e stdout, como o Codex e o Claude ----------


def test_servidor_de_verdade_pelo_stdio(tmp_path):
    dados = tmp_path / "dados"
    (dados / "Forja3D").mkdir(parents=True)
    projetos = tmp_path / "projetos"
    (dados / "Forja3D" / "config.toml").write_text(f'[app]\npasta_projetos = "{projetos.as_posix()}"\n', encoding="utf-8")
    ambiente = {k: v for k, v in os.environ.items() if not k.startswith(("FORJA3D_", "PYTHON"))} | {"APPDATA": str(dados), "PYTHONIOENCODING": "utf-8"}
    mensagens = [
        pedido(1, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "teste", "version": "1"}}),
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        pedido(2, "tools/list"),
        pedido(3, "tools/call", {"name": "criar_peca", "arguments": {"titulo": "Peça ção"}}),
        pedido(4, "tools/call", {"name": "listar_pecas", "arguments": {}}),
    ]
    entrada = "".join(json.dumps(m, ensure_ascii=False) + "\n" for m in mensagens).encode("utf-8")
    saida = subprocess.run([sys.executable, str(RAIZ / "scripts" / "servidor_mcp.py")], input=entrada, capture_output=True, env=ambiente, cwd=tmp_path, timeout=120, check=False)
    assert saida.returncode == 0, saida.stderr.decode("utf-8", "replace")
    # Nada além do protocolo no stdout: uma linha a mais quebraria o cliente.
    respostas = [json.loads(linha) for linha in saida.stdout.decode("utf-8").splitlines()]
    assert [r["id"] for r in respostas] == [1, 2, 3, 4]
    assert respostas[0]["result"]["serverInfo"]["name"] == "forja3d"
    assert len(respostas[1]["result"]["tools"]) == len(FERRAMENTAS)
    criada = json.loads(respostas[2]["result"]["content"][0]["text"])
    assert (projetos / criada["peca"] / "projeto.json").is_file()
    listadas = json.loads(respostas[3]["result"]["content"][0]["text"])["pecas"]
    assert listadas[0]["titulo"] == "Peça ção"


# ---------- O aviso para a janela aberta ----------


def test_aviso_chega_na_janela_aberta(qtbot, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "dados"))
    instancia = InstanciaUnica()
    assert instancia.assumir()
    recebidos = []
    instancia.peca_mudou.connect(recebidos.append)
    # Em outro processo, como o servidor MCP de verdade: o pipe só anda quando a janela lê.
    codigo = f"""
import sys
from pathlib import Path
sys.path.insert(0, {str(RAIZ / 'src')!r})
from forja3d.interface.instancia import avisar_que_mudou
sys.exit(0 if avisar_que_mudou(Path({str(tmp_path / 'peça_x')!r})) else 4)
"""
    processo = subprocess.Popen([sys.executable, "-c", codigo], env=os.environ.copy())
    qtbot.waitUntil(lambda: bool(recebidos), timeout=20000)
    assert processo.wait(20) == 0
    assert recebidos == [tmp_path / "peça_x"]


def test_sem_janela_aberta_o_aviso_nao_trava(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "ninguem"))
    assert avisar_que_mudou(tmp_path) is False


class ClienteFalso:
    def __init__(self, _config=None):
        pass

    def verificar(self):
        return None


@pytest.fixture
def janela_com_peca(qtbot, perfil, tmp_path, monkeypatch, forja):
    monkeypatch.setenv("APPDATA", str(tmp_path / "dados"))
    servidor, _ = forja
    janela = JanelaPrincipal(servidor.config, perfil, ClienteFalso, verificar_endpoint=False)
    qtbot.addWidget(janela)
    projeto = Projeto.criar(servidor.config.pasta_projetos)
    pasta = projeto.pasta_temporaria()
    (pasta / "script.py").write_text("# nada\n", encoding="utf-8")
    import trimesh

    trimesh.creation.box(extents=(30, 30, 10)).apply_translation((0, 0, 5)).export(pasta / "placa.stl")
    medida = {"nome": "placa", "importada": True, "valido": True, "solidos": 1, "volume_mm3": 9000.0, "tamanho": [30.0, 30.0, 10.0], "stl": "placa.stl", "furos": [], "caixa": {"x": [-15.0, 15.0], "y": [-15.0, 15.0], "z": [0.0, 10.0]}}
    projeto.estado["versao_atual"] = projeto.registrar_versao(pasta, None, [medida], "teste")
    projeto.salvar_estado()
    janela.abrir_projeto(projeto)
    qtbot.waitUntil(lambda: not janela.ocupado, timeout=60000)
    yield janela, servidor, projeto
    janela.close()


def test_janela_rele_a_peca_aberta_quando_ela_muda_por_fora(janela_com_peca):
    janela, servidor, projeto = janela_com_peca
    chamar(servidor, "definir_cores", peca=projeto.pasta.name, parte="placa", cor="azul")
    assert janela.painel_3d.mesa.itens[0].cores == {}
    janela.peca_mudou_por_fora(projeto.pasta)
    assert janela.painel_3d.mesa.itens[0].cores == {"": "#1976D2"}
    # A janela agora grava por cima do que leu, e não do que tinha antes.
    janela._ao_mudar_mesa()
    assert LayoutMesa.de_dados(json.loads((projeto.pasta / "v001" / "layout.json").read_text(encoding="utf-8")), {"placa": (30.0, 30.0)}, servidor.perfil).itens[0].cores == {"": "#1976D2"}


def test_janela_ocupada_rele_so_quando_termina(janela_com_peca):
    janela, servidor, projeto = janela_com_peca
    chamar(servidor, "definir_cores", peca=projeto.pasta.name, parte="placa", cor="verde")
    janela._definir_ocupado(True)
    janela.peca_mudou_por_fora(projeto.pasta)
    assert janela.painel_3d.mesa.itens[0].cores == {}
    janela._ao_concluir()
    assert janela.painel_3d.mesa.itens[0].cores == {"": "#388E3C"}


def test_outra_peca_mudar_nao_rele_a_aberta(janela_com_peca, tmp_path):
    janela, _, _ = janela_com_peca
    aberta = janela.projeto
    janela.peca_mudou_por_fora(tmp_path / "outra")
    assert janela.projeto is aberta
