"""As ferramentas do Forja3D para um agente de fora, como o Claude ou o Codex, pelo MCP.

São as mesmas do agente do app, com as mesmas travas no código: especificar antes de modelar, verificar
antes de renderizar e fatiar só o que o usuário aprovou no app. Aprovar e exportar ficam só com o usuário.
Veja "Usar com o Claude ou o Codex" no README.
"""
from __future__ import annotations

import base64
import json
import logging
import re
import sys
import threading
from pathlib import Path
from typing import Any

from forja3d import configuracao
from forja3d.agente.orquestrador import (
    DEFINICOES,
    PROMPT_SISTEMA,
    SCRIPT_SEM_PARTES,
    SEM_CALIBRACAO,
    SO_DO_APP,
    Orquestrador,
)
from forja3d.configuracao import Configuracao
from forja3d.fatiamento.cores import cores_do_item, nome_da_cor
from forja3d.ia.cliente import ErroIA
from forja3d.impressora import carregar_perfil
from forja3d.interface.instancia import avisar_que_mudou
from forja3d.mcp.protocolo import FerramentaDesconhecida, Servidor
from forja3d.projetos.armazenamento import Projeto
from forja3d.versao import VERSAO

log = logging.getLogger(__name__)

_ID_DA_PECA = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}")
_VERSAO = re.compile(r"v\d{3,}")
ESPERA_DO_AVISO_S = 3.0
INSTRUCOES = (
    "Ferramentas do Forja3D, para projetar peças para a impressora Creality Ender-3 V3 SE. "
    "Antes de modelar, chame ler_regras e siga as regras. Comece por listar_pecas ou criar_peca. "
    "Aprovar e exportar o G-code é só do usuário, no app."
)
PREFACIO = """# FORJA3D PELO MCP

Você está usando as ferramentas do Forja3D pelo MCP, de fora do app. As regras abaixo foram escritas para o agente que roda dentro do app e valem igual para você, com estas diferenças:

- **Toda ferramenta da peça pede `peca`:** o identificador que `criar_peca` ou `listar_pecas` devolve.
- **Antes de mudar uma peça que já existe, chame `ver_peca`.** Ela devolve a especificação, as medidas, a verificação e o script da versão atual. `executar_cad` recebe sempre o script completo.
- **`renderizar_vistas` devolve as imagens no próprio resultado.** Examine todas.
- **Você não aprova, não prepara e não exporta.** Com a peça verificada, diga ao usuário para abrir a peça no Forja3D e clicar em **Aprovar e preparar**. Depois disso, `fatiar_peca` funciona.
- **O que você faz aparece no app:** cada versão nova entra na lista de versões, e o chat da peça registra o que mudou.
- **As seções sobre `[PARTE SELECIONADA]` e `[CONCEITO ESCOLHIDO]` não se aplicam:** essas mensagens só existem dentro do app.

"""
# O que não lê só a peça: depois disso, a janela aberta relê a peça.
MUDAM_A_PECA = {"criar_peca", "definir_especificacao", "executar_cad", "verificar_peca", "definir_cores", "fatiar_peca", "voltar_para_versao"}
DESLIGADO = "O servidor MCP do Forja3D está desligado. Para usar, o usuário precisa ligar em Configurações, no app, na parte de agentes de fora."
SO_LEITURA = {"ler_regras", "listar_pecas", "ver_peca", "renderizar_vistas"}
PARAMETRO_PECA = {"type": "string", "description": "Identificador da peça, devolvido por criar_peca ou listar_pecas"}


class ErroDaFerramenta(Exception):
    """Vai para o modelo como resultado com erro: ele lê, corrige e tenta de novo."""


class SemIA:
    """Quem pensa é o agente de fora: o Forja3D só executa as ferramentas."""

    def conversar(self, *_args: Any, **_kwargs: Any) -> Any:
        raise ErroIA("O servidor MCP não conversa com uma IA.")


def _ferramenta(nome: str, descricao: str, propriedades: dict[str, Any], obrigatorios: list[str]) -> dict[str, Any]:
    definicao = {"name": nome, "description": descricao, "inputSchema": {"type": "object", "properties": propriedades, "required": obrigatorios}}
    if nome in SO_LEITURA:
        definicao["annotations"] = {"readOnlyHint": True}
    return definicao


def definicoes() -> list[dict[str, Any]]:
    """As ferramentas do agente do app, cada uma com `peca`, mais as de achar, criar e ler uma peça."""
    proprias = [
        _ferramenta("ler_regras", "As regras de modelagem do Forja3D: a impressora, os materiais, o CadQuery e o fluxo. Leia antes de modelar.", {}, []),
        _ferramenta("listar_pecas", "As peças do Forja3D, da mais recente para a mais antiga, com a versão atual de cada uma.", {}, []),
        _ferramenta("criar_peca", "Cria uma peça vazia no Forja3D e devolve o identificador dela.", {"titulo": {"type": "string", "description": "Nome da peça na lista do app"}}, []),
        _ferramenta(
            "ver_peca",
            "O estado da peça: versões, especificação, medidas, verificação, cores na mesa e o script da versão atual.",
            {"peca": PARAMETRO_PECA, "versao": {"type": "string", "description": "Outra versão, como v003, para ler o script dela"}},
            ["peca"],
        ),
        _ferramenta(
            "voltar_para_versao",
            "Faz uma versão antiga virar a atual. As mais novas continuam salvas.",
            {"peca": PARAMETRO_PECA, "versao": {"type": "string", "description": "Como v003"}},
            ["peca", "versao"],
        ),
    ]
    do_agente = []
    for item in DEFINICOES:
        funcao = item["function"]
        if funcao["name"] in SO_DO_APP:
            continue
        parametros = funcao["parameters"]
        do_agente.append(
            _ferramenta(
                funcao["name"],
                funcao["description"],
                {"peca": PARAMETRO_PECA, **parametros.get("properties", {})},
                ["peca", *parametros.get("required", [])],
            )
        )
    return proprias + do_agente


class ForjaMCP:
    def __init__(self, config: Configuracao, perfil: dict[str, Any], avisar: Any = avisar_que_mudou, ligado: Any = None) -> None:
        self.config = config
        self.perfil = perfil
        self.avisar = avisar
        # Lido a cada chamada: desligar nas Configurações do app vale na hora, sem reiniciar o Codex.
        self.ligado = ligado or (lambda: configuracao.carregar().mcp_ligado)

    # ---------- Chamada ----------

    def chamar(self, nome: str, argumentos: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]:
        if nome not in {d["name"] for d in definicoes()}:
            raise FerramentaDesconhecida(f"Ferramenta desconhecida: {nome}")
        if not self.ligado():
            return [_texto({"ok": False, "erro": DESLIGADO})], True
        argumentos = dict(argumentos)
        try:
            resultado, imagens, projeto = self._executar(nome, argumentos)
        except ErroDaFerramenta as falha:
            return [_texto({"ok": False, "erro": str(falha)})], True
        if nome in MUDAM_A_PECA and projeto is not None:
            self._avisar_o_app(projeto.pasta)
        conteudo = [_texto(resultado)]
        if imagens:
            conteudo.append({"type": "text", "text": "Vistas, nesta ordem: " + ", ".join(i.stem for i in imagens) + "."})
            conteudo += [{"type": "image", "data": base64.b64encode(i.read_bytes()).decode("ascii"), "mimeType": "image/png"} for i in imagens]
        return conteudo, resultado.get("ok") is False

    def _executar(self, nome: str, argumentos: dict[str, Any]) -> tuple[dict[str, Any], list[Path], Projeto | None]:
        if nome == "ler_regras":
            return {"ok": True, "regras": PREFACIO + PROMPT_SISTEMA.replace("{{CALIBRACAO_USUARIO}}", SEM_CALIBRACAO)}, [], None
        if nome == "listar_pecas":
            return {"ok": True, "pecas": [_resumo_da_peca(p) for p in Projeto.listar(self.config.pasta_projetos)]}, [], None
        if nome == "criar_peca":
            titulo = str(argumentos.get("titulo") or "Nova peça").strip()[:120] or "Nova peça"
            projeto = Projeto.criar(self.config.pasta_projetos, titulo)
            return {"ok": True, "peca": projeto.pasta.name, "titulo": projeto.titulo, "proximo_passo": "Chame ler_regras, se ainda não leu, e depois definir_especificacao."}, [], projeto

        projeto = self._projeto(argumentos.pop("peca", None))
        agente = Orquestrador(self.config, self.perfil, SemIA(), projeto)
        if nome == "ver_peca":
            return self._ver(projeto, agente, argumentos.get("versao")), [], projeto
        if nome == "voltar_para_versao":
            versao = str(argumentos.get("versao", ""))
            if not _VERSAO.fullmatch(versao) or versao not in projeto.versoes():
                raise ErroDaFerramenta(f"Versão desconhecida: {versao}. As versões são: {', '.join(projeto.versoes()) or 'nenhuma'}.")
            agente.restaurar_versao(versao)
            agente.registrar_trabalho_externo(f"voltou para a versão {versao}")
            return {"ok": True, "versao_atual": versao, "proximo_passo": "Chame verificar_peca."}, [], projeto

        antes = agente.versao_atual
        resultado, imagens = agente.executar_ferramenta(nome, argumentos)
        if resultado.get("ok"):
            self._registrar(agente, nome, argumentos, resultado, antes)
        return resultado, imagens, projeto

    def _registrar(self, agente: Orquestrador, nome: str, argumentos: dict[str, Any], resultado: dict[str, Any], antes: str | None) -> None:
        """O que muda o modelo, as cores ou o G-code fica no chat da peça e no histórico do agente do app."""
        if nome == "executar_cad" and agente.versao_atual != antes:
            resumo = str(argumentos.get("resumo_alteracao") or "").strip()
            agente.registrar_trabalho_externo(f"gerou a versão {agente.versao_atual}" + (f": {resumo}" if resumo else ""))
        elif nome == "definir_cores" and "mudou_de_arquivo" in resultado:
            agente.registrar_trabalho_externo(f"aplicou cores em {resultado['parte']}")
        elif nome == "fatiar_peca":
            agente.registrar_trabalho_externo(f"preparou o G-code da versão {agente.versao_atual}: {resultado.get('tempo', '?')}, {resultado.get('filamento_g', '?')} g")

    def _projeto(self, peca: Any) -> Projeto:
        """Só uma pasta de peça, direto na pasta de projetos: o identificador vem do modelo e não pode apontar para outro lugar."""
        if not isinstance(peca, str) or not _ID_DA_PECA.fullmatch(peca) or ".." in peca:
            raise ErroDaFerramenta("Informe `peca` com o identificador devolvido por listar_pecas ou criar_peca.")
        pasta = self.config.pasta_projetos / peca
        if not (pasta / "projeto.json").is_file():
            raise ErroDaFerramenta(f"Não existe a peça {peca}. Chame listar_pecas para ver as que existem.")
        return Projeto(pasta)

    def _ver(self, projeto: Projeto, agente: Orquestrador, versao: Any) -> dict[str, Any]:
        atual = agente.versao_atual
        escolhida = str(versao) if versao else atual
        if escolhida and escolhida not in projeto.versoes():
            raise ErroDaFerramenta(f"Versão desconhecida: {escolhida}. As versões são: {', '.join(projeto.versoes()) or 'nenhuma'}.")
        estado: dict[str, Any] = {
            **_resumo_da_peca(projeto),
            "versoes": [{"versao": v, **projeto.info_versao(v)} for v in projeto.versoes()],
            "especificacao": agente.especificacao.model_dump() if agente.especificacao else None,
        }
        if not escolhida:
            return {"ok": True, **estado, "proximo_passo": "A peça ainda não tem modelo. Chame definir_especificacao e depois executar_cad."}
        medidas = projeto.medidas(escolhida)
        verificacao = projeto.verificacao(escolhida)
        script = projeto.pasta_versao(escolhida) / "modelo.py"
        codigo = script.read_text(encoding="utf-8") if script.is_file() else ""
        estado |= {
            "versao_mostrada": escolhida,
            "partes": [
                {"nome": p["nome"], "tamanho": p["tamanho"], "furos": len(p.get("furos", [])), **({"importada": True} if p.get("importada") else {}), **({"corpos": [c["nome"] for c in p["corpos"]]} if p.get("corpos") else {})}
                for p in medidas
            ],
            "verificacao": None
            if verificacao is None
            else {"aprovado": verificacao["aprovado"], "bloqueios": [i["mensagem"] for i in verificacao["bloqueios"]], "alertas": [i["mensagem"] for i in verificacao["alertas"]]},
            "mesa": _mesa(projeto, escolhida, self.perfil),
            "script": codigo if codigo and codigo != SCRIPT_SEM_PARTES else None,
        }
        return {"ok": True, **estado}

    def _avisar_o_app(self, pasta: Path) -> None:
        """A janela só lê o aviso quando está livre: o servidor não fica esperando por ela."""
        aviso = threading.Thread(target=self.avisar, args=(pasta,), daemon=True)
        aviso.start()
        aviso.join(ESPERA_DO_AVISO_S)


def _resumo_da_peca(projeto: Projeto) -> dict[str, Any]:
    return {
        "peca": projeto.pasta.name,
        "titulo": projeto.titulo,
        "versao_atual": projeto.estado.get("versao_atual"),
        "aprovada_no_app": bool(projeto.estado.get("versao_aprovada")) and projeto.estado.get("versao_aprovada") == projeto.estado.get("versao_atual"),
    }


def _mesa(projeto: Projeto, versao: str, perfil: dict[str, Any]) -> list[dict[str, Any]]:
    layout = projeto.layout(versao, perfil)
    medidas = projeto.medidas_por_parte(versao)
    itens = []
    for item in layout.itens:
        cores = {corpo or "peça": nome_da_cor(cor) for corpo, cor in cores_do_item(item, medidas.get(item.parte)).items() if cor}
        trocas = [{"z_mm": t["z"], "cor": nome_da_cor(t["cor"])} for t in layout.alturas.get(item.arquivo, [])]
        itens.append({"parte": item.parte, "arquivo": item.arquivo, **({"cores": cores} if cores else {}), **({"trocas": trocas} if trocas else {}), **({"deitada": True} if item.tombo else {})})
    return itens


def _texto(dados: dict[str, Any]) -> dict[str, Any]:
    return {"type": "text", "text": json.dumps(dados, ensure_ascii=False, indent=1)}


def principal() -> int:
    """Fala MCP pelo stdin e stdout. Tudo o mais que for escrito vai para o stderr, para não quebrar o protocolo."""
    from forja3d.registro import configurar_logs

    saida = sys.stdout.buffer
    sys.stdout = sys.stderr
    config = configuracao.carregar()
    configurar_logs(*config.chaves)
    forja = ForjaMCP(config, carregar_perfil())
    log.info("Servidor MCP iniciado, peças em %s", config.pasta_projetos)
    Servidor("forja3d", VERSAO, INSTRUCOES, definicoes(), forja.chamar).rodar(sys.stdin.buffer, saida)
    return 0
