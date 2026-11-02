"""Executa o processo auxiliar e o kernel CAD reais."""
import threading
import time
from pathlib import Path

import pytest

from forja3d.agente.especificacao import Especificacao
from forja3d.cad.executor import ambiente_limpo, executar_script
from forja3d.ia.cliente import Cancelado
from forja3d.render.vistas import VISTAS, renderizar
from forja3d.verificacao.verificador import verificar_peca

pytestmark = pytest.mark.cad
DADOS = Path(__file__).parent / "dados"


def _executar(nome, pasta):
    return executar_script((DADOS / nome).read_text(encoding="utf-8"), pasta)


def _codigos(itens):
    return {i["codigo"] for i in itens}


def test_regressao_codigo_errado_roda_mas_e_reprovado(tmp_path, perfil, espec_suporte):
    r = _executar("suporte_l_modelo_errado.py", tmp_path)
    assert r.ok
    parte = r.partes[0]
    assert parte["valido"]
    assert parte["tamanho"] == [40, 30, 34]
    assert parte["furos"] == []
    assert parte["volume_mm3"] == pytest.approx(8334.33, abs=0.01)
    v = verificar_peca(r.partes, espec_suporte, perfil, tmp_path)
    assert not v["aprovado"]
    assert "FURO_AUSENTE" in _codigos(v["bloqueios"])


def test_regressao_versao_corrigida_passa(tmp_path, perfil, espec_suporte):
    r = _executar("suporte_l_corrigido.py", tmp_path)
    assert r.ok
    parte = r.partes[0]
    assert parte["volume_mm3"] == pytest.approx(8357.41, abs=0.01)
    assert len(parte["furos"]) == 2
    assert len(parte["arcos_concavos"]) == 1, "o filete não pode contar como furo"
    v = verificar_peca(r.partes, espec_suporte, perfil, tmp_path)
    assert v["aprovado"], v["bloqueios"]
    assert not v["alertas"], v["alertas"]


def test_regressao_chamada_invalida_devolve_erro_e_linha(tmp_path):
    r = _executar("suporte_l_api_invalida.py", tmp_path)
    assert not r.ok
    assert r.tipo_erro == "TypeError"
    assert "fillet" in r.erro
    assert r.linha is not None
    assert "fillet" in r.trecho


def test_diametro_errado_e_reprovado(tmp_path, perfil, espec_suporte):
    r = _executar("suporte_l_corrigido.py", tmp_path)
    espec = espec_suporte.model_copy(deep=True)
    espec.furos[0].diametro_modelado_mm = 4.5
    v = verificar_peca(r.partes, espec, perfil, tmp_path)
    assert "DIAMETRO_DIVERGENTE" in _codigos(v["bloqueios"])


def test_dimensao_errada_e_reprovada(tmp_path, perfil, espec_suporte):
    r = _executar("suporte_l_corrigido.py", tmp_path)
    espec = espec_suporte.model_copy(deep=True)
    espec.dimensoes.x_mm = 50
    v = verificar_peca(r.partes, espec, perfil, tmp_path)
    assert "DIMENSAO_DIVERGENTE" in _codigos(v["bloqueios"])


def _espec(x, y, z, pecas=1, furos=()):
    return Especificacao(
        nome="t", finalidade="t", material="PLA", carga="leve", dimensoes={"x_mm": x, "y_mm": y, "z_mm": z},
        pecas=pecas, furos=list(furos), orientacao_impressao="t",
    )


def _peca(tmp_path, corpo):
    r = executar_script(f"import cadquery as cq\n{corpo}", tmp_path)
    assert r.ok, r.erro
    return r.partes


def test_peca_grande_e_alta_sao_bloqueadas(tmp_path, perfil):
    partes = _peca(tmp_path, "result = cq.Workplane('XY').box(218, 20, 245, centered=(True, True, False))")
    v = verificar_peca(partes, _espec(218, 20, 245), perfil, tmp_path)
    assert {"FORA_DO_VOLUME_FISICO", "ALTURA_ACIMA_DO_LIMITE"} <= _codigos(v["bloqueios"])


def test_peca_entre_seguro_e_maximo_gera_alerta(tmp_path, perfil):
    partes = _peca(tmp_path, "result = cq.Workplane('XY').box(215, 100, 5, centered=(True, True, False))")
    v = verificar_peca(partes, _espec(215, 100, 5), perfil, tmp_path)
    assert v["aprovado"]
    assert "FORA_DO_VOLUME_SEGURO" in _codigos(v["alertas"])


def test_peca_flutuando_e_bloqueada(tmp_path, perfil):
    partes = _peca(tmp_path, "result = cq.Workplane('XY').box(20, 20, 20)")
    v = verificar_peca(partes, _espec(20, 20, 20), perfil, tmp_path)
    assert "NAO_APOIADA" in _codigos(v["bloqueios"])


def test_parede_fina_e_bloqueada(tmp_path, perfil):
    corpo = (
        "result = cq.Workplane('XY').box(30, 30, 30, centered=(True, True, False))"
        ".faces('>Z').shell(-0.5)"
    )
    v = verificar_peca(_peca(tmp_path, corpo), _espec(30, 30, 30), perfil, tmp_path)
    assert "PAREDE_FINA" in _codigos(v["bloqueios"])


def test_balanco_gera_alerta(tmp_path, perfil):
    # Coluna com um pedestal: a face inferior do topo fica a 30° da horizontal.
    corpo = (
        "base = cq.Workplane('XY').box(20, 20, 10, centered=(True, True, False))\n"
        "topo = cq.Workplane('XZ').polyline([(-10, 10), (10, 10), (40, 27), (-10, 27)]).close().extrude(10, both=True)\n"
        "result = base.union(topo)"
    )
    partes = _peca(tmp_path, corpo)
    x, y, z = partes[0]["tamanho"]
    v = verificar_peca(partes, _espec(x, y, z), perfil, tmp_path)
    assert "BALANCO" in _codigos(v["alertas"])


def test_ponte_longa_gera_alerta(tmp_path, perfil):
    corpo = (
        "pe1 = cq.Workplane('XY').box(10, 20, 10, centered=(True, True, False)).translate((-30, 0, 0))\n"
        "pe2 = cq.Workplane('XY').box(10, 20, 10, centered=(True, True, False)).translate((30, 0, 0))\n"
        "viga = cq.Workplane('XY').box(70, 20, 5, centered=(True, True, False)).translate((0, 0, 10))\n"
        "result = pe1.union(pe2).union(viga)"
    )
    partes = _peca(tmp_path, corpo)
    x, y, z = partes[0]["tamanho"]
    v = verificar_peca(partes, _espec(x, y, z), perfil, tmp_path)
    assert "PONTE_LONGA" in _codigos(v["alertas"])


def test_varias_pecas_em_dicionario(tmp_path, perfil):
    corpo = (
        "a = cq.Workplane('XY').box(40, 40, 10, centered=(True, True, False))\n"
        "b = cq.Workplane('XY').box(20, 20, 5, centered=(True, True, False))\n"
        "result = {'base': a, 'tampa': b}"
    )
    partes = _peca(tmp_path, corpo)
    assert [p["nome"] for p in partes] == ["base", "tampa"]
    assert (tmp_path / "base.stl").exists()
    assert (tmp_path / "tampa.stl").exists()
    v = verificar_peca(partes, _espec(40, 40, 10, pecas=2), perfil, tmp_path)
    assert v["aprovado"], v["bloqueios"]


def test_quantidade_de_solidos_divergente(tmp_path, perfil):
    corpo = (
        "a = cq.Workplane('XY').box(10, 10, 10, centered=(True, True, False))\n"
        "b = cq.Workplane('XY').box(10, 10, 10, centered=(True, True, False)).translate((30, 0, 0))\n"
        "result = a.add(b).combine(clean=False)"
    )
    r = executar_script(f"import cadquery as cq\n{corpo}", tmp_path)
    if r.ok and r.partes[0]["solidos"] == 2:
        v = verificar_peca(r.partes, _espec(*r.partes[0]["tamanho"]), perfil, tmp_path)
        assert "QTD_SOLIDOS" in _codigos(v["bloqueios"])


def test_script_sem_result(tmp_path):
    r = executar_script("import cadquery as cq\nx = 1", tmp_path)
    assert not r.ok
    assert "result" in r.erro


def test_result_sem_solido(tmp_path):
    r = executar_script("import cadquery as cq\nresult = cq.Workplane('XY').box(1, 1, 1).edges('|Z')", tmp_path)
    assert not r.ok
    assert "sólido" in r.erro


def test_codigo_malicioso_nao_executa(tmp_path):
    alvo = tmp_path / "invadido.txt"
    r = executar_script(f"open(r'{alvo}', 'w').write('x')", tmp_path)
    assert not r.ok
    assert r.tipo_erro == "CodigoRecusado"
    assert not alvo.exists()


def test_builtins_restritos_em_tempo_de_execucao(tmp_path):
    # Mesmo que a análise deixasse passar, open não existe no ambiente do script.
    from forja3d.cad import seguranca_ast

    assert "open" not in seguranca_ast.BUILTINS_PERMITIDOS
    assert "exec" not in seguranca_ast.BUILTINS_PERMITIDOS
    assert "getattr" not in seguranca_ast.BUILTINS_PERMITIDOS


def test_laco_infinito_e_encerrado_por_timeout(tmp_path):
    inicio = time.monotonic()
    r = executar_script("while True:\n    pass", tmp_path, timeout=6)
    assert not r.ok
    assert r.tipo_erro == "Timeout"
    assert time.monotonic() - inicio < 15


def test_consumo_excessivo_de_memoria_nao_derruba_o_app(tmp_path):
    r = executar_script("x = [0] * (10 ** 10)", tmp_path, timeout=60)
    assert not r.ok
    assert r.tipo_erro in {"MemoryError", "FalhaKernel"}


def test_cancelamento_encerra_o_processo(tmp_path):
    cancelar = threading.Event()
    threading.Timer(1.0, cancelar.set).start()
    inicio = time.monotonic()
    with pytest.raises(Cancelado):
        executar_script("while True:\n    pass", tmp_path, cancelar=cancelar)
    assert time.monotonic() - inicio < 10


def test_ambiente_do_auxiliar_nao_tem_a_chave(monkeypatch):
    monkeypatch.setenv("FORJA3D_IA_API_KEY", "segredo")
    monkeypatch.setenv("OPENAI_API_KEY", "segredo")
    ambiente = ambiente_limpo()
    assert "segredo" not in ambiente.values()
    assert not [k for k in ambiente if "KEY" in k or "FORJA3D" in k]


def test_renderiza_todas_as_vistas(tmp_path):
    r = _executar("suporte_l_corrigido.py", tmp_path)
    imagens = renderizar([tmp_path / r.partes[0]["stl"]], tmp_path / "renders")
    assert {i.stem for i in imagens} == set(VISTAS)
    assert all(i.stat().st_size > 5000 for i in imagens)
