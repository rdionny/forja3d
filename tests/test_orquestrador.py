"""Fluxo e travas do agente, com respostas gravadas e sem rede."""
import json
from pathlib import Path

import pytest

from forja3d.agente.orquestrador import DEFINICOES, Orquestrador
from forja3d.configuracao import ConfigIA, Configuracao
from forja3d.fatiamento.orca import ErroFatiamento, ParametrosFatiamento
from forja3d.ia.cliente import Cancelado, ChamadaFerramenta, RespostaIA
from forja3d.projetos.armazenamento import Projeto

DADOS = Path(__file__).parent / "dados"
ESPEC = {
    "nome": "suporte_l",
    "finalidade": "teste",
    "material": "PLA",
    "carga": "leve",
    "dimensoes": {"x_mm": 40, "y_mm": 30, "z_mm": 34},
    "pecas": 1,
    "furos": [{"quantidade": 2, "diametro_nominal_mm": 4, "diametro_modelado_mm": 4, "eixo": "Z", "passante": True}],
    "orientacao_impressao": "base na mesa",
    "premissas": [],
}


def chamada(nome, argumentos=None, id_="c1"):
    mensagem = {
        "role": "assistant",
        "tool_calls": [{"id": id_, "type": "function", "function": {"name": nome, "arguments": json.dumps(argumentos or {})}}],
    }
    return RespostaIA("", [ChamadaFerramenta(id_, nome, json.dumps(argumentos or {}))], mensagem)


def texto(conteudo):
    return RespostaIA(conteudo, [], {"role": "assistant", "content": conteudo})


class ClienteGravado:
    """Devolve as respostas em ordem e guarda o que recebeu."""

    def __init__(self, respostas):
        self.respostas = list(respostas)
        self.chamadas = []

    def conversar(self, mensagens, ferramentas, etapa, cancelar=None):
        self.chamadas.append({"mensagens": list(mensagens), "ferramentas": ferramentas, "etapa": etapa})
        if cancelar is not None and cancelar.is_set():
            raise Cancelado
        return self.respostas.pop(0)


@pytest.fixture
def config(tmp_path):
    return Configuracao(ia=ConfigIA(endpoint="http://x", api_key="k", modelo="m"), pasta_projetos=tmp_path)


def agente(config, perfil, respostas):
    cliente = ClienteGravado(respostas)
    return Orquestrador(config, perfil, cliente, Projeto.criar(config.pasta_projetos)), cliente


def resultados_das_ferramentas(mensagens):
    return [json.loads(m["content"]) for m in mensagens if m.get("role") == "tool"]


def test_ferramentas_sao_exatamente_as_cinco_e_nenhuma_exporta():
    nomes = {d["function"]["name"] for d in DEFINICOES}
    assert nomes == {"definir_especificacao", "executar_cad", "verificar_peca", "renderizar_vistas", "fatiar_peca"}


def test_resposta_simples_sem_ferramentas(config, perfil):
    a, _ = agente(config, perfil, [texto("Qual o diâmetro?")])
    assert a.processar("Quero um suporte") == "Qual o diâmetro?"


def test_executar_cad_e_recusado_sem_especificacao(config, perfil):
    a, _ = agente(config, perfil, [chamada("executar_cad", {"codigo": "x", "resumo_alteracao": ""}), texto("ok")])
    a.processar("oi")
    assert "definir_especificacao" in resultados_das_ferramentas(a.mensagens)[0]["erro"]


def test_verificar_sem_versao_e_recusado(config, perfil):
    a, _ = agente(config, perfil, [chamada("verificar_peca"), texto("ok")])
    a.processar("oi")
    assert not resultados_das_ferramentas(a.mensagens)[0]["ok"]


def test_renderizar_sem_verificacao_e_recusado(config, perfil):
    a, _ = agente(config, perfil, [chamada("renderizar_vistas"), texto("ok")])
    a.processar("oi")
    assert "verificar_peca" in resultados_das_ferramentas(a.mensagens)[0]["erro"]


def test_especificacao_acima_do_limite_nao_e_registrada(config, perfil):
    grande = {**ESPEC, "dimensoes": {"x_mm": 300, "y_mm": 60, "z_mm": 40}}
    a, _ = agente(config, perfil, [chamada("definir_especificacao", grande), texto("ok")])
    a.processar("oi")
    r = resultados_das_ferramentas(a.mensagens)[0]
    assert not r["ok"]
    assert "dividir" in r["problemas"][0]
    assert a.especificacao is None


def test_argumentos_invalidos_voltam_para_o_modelo(config, perfil):
    a, _ = agente(config, perfil, [chamada("definir_especificacao", {"nome": "x"}), texto("ok")])
    a.processar("oi")
    assert "inválidos" in resultados_das_ferramentas(a.mensagens)[0]["erro"]


def test_json_quebrado_volta_para_o_modelo(config, perfil):
    quebrada = RespostaIA(
        "",
        [ChamadaFerramenta("c1", "executar_cad", "{quebrado")],
        {"role": "assistant", "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "executar_cad", "arguments": "{quebrado"}}]},
    )
    a, _ = agente(config, perfil, [quebrada, texto("ok")])
    a.processar("oi")
    assert "JSON" in resultados_das_ferramentas(a.mensagens)[0]["erro"]


def test_ferramenta_desconhecida(config, perfil):
    a, _ = agente(config, perfil, [chamada("exportar_gcode"), texto("ok")])
    a.processar("oi")
    assert "desconhecida" in resultados_das_ferramentas(a.mensagens)[0]["erro"]


def test_esforco_por_etapa(config, perfil):
    a, cliente = agente(config, perfil, [chamada("definir_especificacao", ESPEC), texto("ok")])
    a.processar("oi")
    assert [c["etapa"] for c in cliente.chamadas] == ["especificacao", "modelagem"]


def test_cancelamento_durante_a_ia_mantem_o_historico_valido(config, perfil):
    a, cliente = agente(config, perfil, [texto("nunca")])

    def cancela(*_args, **_kwargs):
        raise Cancelado

    cliente.conversar = cancela
    assert "cancelada" in a.processar("oi").lower()
    assert a.mensagens[-1]["role"] == "user"


@pytest.mark.cad
def test_fluxo_real_regressao_furos_voltam_para_o_modelo(config, perfil):
    """Com o kernel real: o script errado roda, é reprovado, e o corrigido passa."""
    errado = (DADOS / "suporte_l_modelo_errado.py").read_text(encoding="utf-8")
    certo = (DADOS / "suporte_l_corrigido.py").read_text(encoding="utf-8")
    a, cliente = agente(
        config,
        perfil,
        [
            chamada("definir_especificacao", ESPEC, "c1"),
            chamada("executar_cad", {"codigo": errado, "resumo_alteracao": "primeira"}, "c2"),
            chamada("verificar_peca", {}, "c3"),
            chamada("executar_cad", {"codigo": certo, "resumo_alteracao": "corrigido"}, "c4"),
            chamada("verificar_peca", {}, "c5"),
            chamada("renderizar_vistas", {}, "c6"),
            texto("Pronto para revisão."),
        ],
    )
    assert a.processar("Suporte em L") == "Pronto para revisão."

    resultados = resultados_das_ferramentas(a.mensagens)
    assert [r["codigo"] for r in resultados[2]["bloqueios"]] == ["FURO_AUSENTE"]
    assert resultados[4]["aprovado"]
    assert a.versao_atual == "v002"
    assert a.projeto.versoes() == ["v001", "v002"]
    assert a.pode_aprovar()

    # O reparo usa o esforço certo, e as vistas chegam em uma mensagem separada.
    assert [c["etapa"] for c in cliente.chamadas][:5] == ["especificacao", "modelagem", "conversa", "reparo", "conversa"]
    imagens = [m for m in cliente.chamadas[-1]["mensagens"] if isinstance(m.get("content"), list)]
    assert len(imagens) == 1
    assert len(imagens[0]["content"]) == 7

    # O histórico em disco não guarda imagens.
    assert not [m for m in a.projeto.ler_historico() if isinstance(m.get("content"), list)]


@pytest.mark.cad
def test_fatiar_exige_aprovacao_do_usuario(config, perfil):
    certo = (DADOS / "suporte_l_corrigido.py").read_text(encoding="utf-8")
    a, _ = agente(
        config,
        perfil,
        [
            chamada("definir_especificacao", ESPEC, "c1"),
            chamada("executar_cad", {"codigo": certo, "resumo_alteracao": ""}, "c2"),
            chamada("verificar_peca", {}, "c3"),
            chamada("fatiar_peca", {"material": "PLA", "qualidade": "padrao", "paredes": 3, "preenchimento_pct": 15, "suporte": False, "brim": False}, "c4"),
            texto("Aguardando."),
        ],
    )
    a.processar("Suporte")
    assert "aprovou" in resultados_das_ferramentas(a.mensagens)[3]["erro"]
    assert not list(a.projeto.pasta_versao("v001").glob("impressao/*/peca.gcode"))
    with pytest.raises(ErroFatiamento):
        a.fatiar(ParametrosFatiamento(material="PLA"))


@pytest.mark.cad
def test_nova_versao_invalida_a_aprovacao(config, perfil):
    certo = (DADOS / "suporte_l_corrigido.py").read_text(encoding="utf-8")
    a, _ = agente(
        config,
        perfil,
        [
            chamada("definir_especificacao", ESPEC, "c1"),
            chamada("executar_cad", {"codigo": certo, "resumo_alteracao": ""}, "c2"),
            chamada("verificar_peca", {}, "c3"),
            texto("ok"),
            chamada("executar_cad", {"codigo": certo, "resumo_alteracao": "ajuste"}, "c4"),
            texto("mudei"),
        ],
    )
    a.processar("Suporte")
    a.aprovar()
    assert a.versao_aprovada == "v001"
    a.processar("Ajusta")
    assert a.versao_atual == "v002"
    assert a.versao_aprovada is None
    # O modelo apresentou a v002 sem verificar: quem verificou foi o código.
    assert a.projeto.verificacao("v002")["aprovado"]


@pytest.mark.cad
def test_versao_reprovada_sem_verificar_volta_para_o_modelo(config, perfil):
    errado = (DADOS / "suporte_l_modelo_errado.py").read_text(encoding="utf-8")
    a, _ = agente(
        config,
        perfil,
        [
            chamada("definir_especificacao", ESPEC, "c1"),
            chamada("executar_cad", {"codigo": errado, "resumo_alteracao": ""}, "c2"),
            texto("Pronto, a peça ficou perfeita."),
            texto("A verificação reprovou esta versão."),
        ],
    )
    a.processar("Suporte")
    assert any("[Sistema] A verificação da versão v001 reprovou" in str(m.get("content")) for m in a.mensagens)
    assert not a.pode_aprovar()


@pytest.mark.cad
def test_aprovar_com_bloqueios_e_recusado(config, perfil):
    errado = (DADOS / "suporte_l_modelo_errado.py").read_text(encoding="utf-8")
    a, _ = agente(
        config,
        perfil,
        [
            chamada("definir_especificacao", ESPEC, "c1"),
            chamada("executar_cad", {"codigo": errado, "resumo_alteracao": ""}, "c2"),
            chamada("verificar_peca", {}, "c3"),
            texto("reprovada"),
        ],
    )
    a.processar("Suporte")
    assert not a.pode_aprovar()
    with pytest.raises(ValueError):
        a.aprovar()


@pytest.mark.cad
def test_limite_de_reparos_para_o_loop(config, perfil):
    ruim = "import cadquery as cq\nresult = cq.Workplane('XY').box(1, 1, 1).nao_existe()"
    respostas = [chamada("definir_especificacao", ESPEC, "c0")]
    respostas += [chamada("executar_cad", {"codigo": ruim, "resumo_alteracao": ""}, f"c{i}") for i in range(1, 5)]
    respostas += [texto("Não consegui.")]
    a, cliente = agente(config, perfil, respostas)
    assert a.processar("x") == "Não consegui."
    assert cliente.chamadas[-1]["ferramentas"] is None, "a última chamada não pode oferecer ferramentas"
    assert "Limite de correções" in cliente.chamadas[-1]["mensagens"][-1]["content"]


def test_projeto_reabre_com_o_estado(config, perfil):
    a, _ = agente(config, perfil, [chamada("definir_especificacao", ESPEC), texto("ok")])
    a.processar("oi")
    reaberto = Projeto(a.projeto.pasta)
    b = Orquestrador(config, perfil, ClienteGravado([]), reaberto)
    assert b.especificacao.nome == "suporte_l"
    assert len(b.mensagens) == len(a.mensagens)
    assert reaberto.titulo == "Suporte L"
    assert [c["papel"] for c in reaberto.ler_conversa()] == ["usuario", "agente"]
