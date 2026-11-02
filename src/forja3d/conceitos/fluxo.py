"""Da imagem de referência às opções de conceito, cada uma com a peça montada e a vista explodida.

A imagem gerada é só ilustração: nunca vira medida, STL nem G-code.
"""
from __future__ import annotations

import concurrent.futures
import io
import json
import logging
import re
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field

from forja3d.ia.cliente import ClienteIA, ErroIA, ImagemGerada, ModeloIndisponivel, data_uri_png
from forja3d.projetos.armazenamento import Projeto, gravar_json

log = logging.getLogger(__name__)

PROMPTS = Path(__file__).parent.parent / "ia" / "prompts"
PROMPT_ANALISE = (PROMPTS / "conceito_analise.md").read_text(encoding="utf-8")
PROMPT_IMAGEM = (PROMPTS / "conceito_imagem.md").read_text(encoding="utf-8")
PROMPT_CONFERENCIA = (PROMPTS / "conceito_conferencia.md").read_text(encoding="utf-8")

OPCOES_POR_RODADA = 2
MAX_TENTATIVAS = 3
TENTATIVAS_DE_ANALISE = 2
SEM_PEDIDO = "nenhum, só a imagem"


class Componente(BaseModel):
    nome: str
    quantidade: int = Field(default=1, ge=1)
    impresso: bool = True
    detalhes: str = ""


class OpcaoConceito(BaseModel):
    titulo: str
    descricao: str = ""
    componentes: list[Componente] = Field(min_length=1)


class Analise(BaseModel):
    objeto: str = ""
    recusa: str | None = None
    medidas_criticas: list[str] = Field(default_factory=list)
    opcoes: list[OpcaoConceito] = Field(default_factory=list)


@dataclass
class Rodada:
    pasta: Path
    pedido: str
    analise: Analise
    conferencias: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ResultadoOpcao:
    rodada: Rodada
    numero: int
    opcao: OpcaoConceito
    imagem: Path | None = None
    aprovada: bool = False
    observacao: str = ""
    erro: str = ""


@dataclass
class Conferencia:
    aprovada: bool
    observacao: str = ""


Progresso = Callable[[int, str], None]


def ler_json_da_ia(texto: str) -> dict[str, Any]:
    """O modelo costuma cercar o JSON com crases, mesmo quando se pede só JSON."""
    limpo = re.sub(r"^```(?:json)?\s*|\s*```$", "", texto.strip())
    dados = json.loads(limpo)
    if not isinstance(dados, dict):
        raise ValueError("A IA não devolveu um objeto JSON.")  # noqa: TRY004 - quem chama trata tudo como resposta ilegível.
    return dados


def lista_de_componentes(opcao: OpcaoConceito) -> str:
    linhas = []
    for c in opcao.componentes:
        tipo = "impresso" if c.impresso else "não impresso"
        linhas.append(f"- {c.quantidade} x {c.nome} ({tipo}): {c.detalhes}")
    return "\n".join(linhas)


def prompt_da_imagem(opcao: OpcaoConceito) -> str:
    return (
        PROMPT_IMAGEM.replace("{titulo}", opcao.titulo)
        .replace("{descricao}", opcao.descricao)
        .replace("{componentes}", lista_de_componentes(opcao))
    )


def mensagem_do_conceito(conceito: dict[str, Any]) -> str:
    """O que o agente recebe quando o usuário escolhe um conceito."""
    opcao = OpcaoConceito.model_validate(conceito["opcao"])
    medidas = "\n".join(f"- {m}" for m in conceito["medidas_criticas"]) or "- Nenhuma"
    linhas = [
        "[CONCEITO ESCOLHIDO]",
        f"Pedido: {conceito['pedido'] or SEM_PEDIDO}",
        f"Objeto: {conceito['objeto']}",
        f"Opção: {opcao.titulo}: {opcao.descricao}",
        "Componentes:",
        lista_de_componentes(opcao),
        "Medidas críticas pendentes:",
        medidas,
    ]
    if not conceito["aprovada_na_conferencia"]:
        linhas.append(f"Atenção: os dois painéis da imagem podem divergir. {conceito['observacao']}")
    return "\n".join(linhas)


def _normalizar_imagem(imagem: ImagemGerada) -> bytes:
    """A imagem veio pela rede: só passa adiante o que o Pillow consegue decodificar por inteiro."""
    try:
        with Image.open(io.BytesIO(imagem.dados)) as aberta:
            limpa = aberta.convert("RGB")
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as erro:
        raise ErroIA("O modelo de imagem devolveu um arquivo que não consegui abrir.") from erro
    saida = io.BytesIO()
    limpa.save(saida, format="PNG")
    return saida.getvalue()


class FluxoConceitos:
    def __init__(self, cliente: ClienteIA, projeto: Projeto, referencia: bytes, cancelar: threading.Event | None = None) -> None:
        self.cliente = cliente
        self.projeto = projeto
        self.referencia = referencia
        self.cancelar = cancelar or threading.Event()
        self.caminho_referencia = projeto.guardar_referencia(referencia)
        self._trava = threading.Lock()

    def iniciar_rodada(self, pedido: str, ja_mostrados: Iterable[str] = ()) -> Rodada:
        """Analisa a referência. Com `recusa` preenchida, a rodada não tem opções e nenhuma imagem é gerada."""
        complemento = pedido.strip() or SEM_PEDIDO
        titulos = list(ja_mostrados)
        if titulos:
            complemento += "\nConceitos já mostrados, proponha outros bem diferentes: " + "; ".join(titulos) + "."
        conteudo = [
            {"type": "text", "text": PROMPT_ANALISE.replace("{pedido}", complemento)},
            {"type": "image_url", "image_url": {"url": data_uri_png(self.referencia)}},
        ]
        analise = self._analisar(conteudo)
        rodada = Rodada(self.projeto.nova_rodada_de_conceitos(), pedido.strip(), analise)
        gravar_json(rodada.pasta / "analise.json", analise.model_dump())
        return rodada

    def _analisar(self, conteudo: list[dict[str, Any]]) -> Analise:
        for _ in range(TENTATIVAS_DE_ANALISE):
            resposta = self.cliente.conversar([{"role": "user", "content": conteudo}], None, "conceito_analise", self.cancelar, temperatura=0.4)
            try:
                analise = Analise.model_validate(ler_json_da_ia(resposta.conteudo))
            except ValueError as erro:
                log.warning("Análise da referência ilegível: %s", erro)
                continue
            if analise.recusa:
                analise.opcoes = []
                return analise
            if len(analise.opcoes) >= OPCOES_POR_RODADA:
                analise.opcoes = analise.opcoes[:OPCOES_POR_RODADA]
                return analise
        raise ErroIA("A IA não conseguiu propor 2 conceitos para esta imagem. Tente de novo ou ajuste o pedido.")

    def produzir_rodada(
        self,
        rodada: Rodada,
        ao_pronta: Callable[[ResultadoOpcao], None],
        ao_progresso: Progresso | None = None,
    ) -> None:
        """Uma chamada por opção, em paralelo: o modelo só gera uma imagem por vez."""
        numeros = range(1, len(rodada.analise.opcoes) + 1)
        if not numeros:
            return
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(numeros)) as grupo:
            pendentes = [grupo.submit(self.produzir_opcao, rodada, n, ao_progresso) for n in numeros]
            for pronto in concurrent.futures.as_completed(pendentes):
                ao_pronta(pronto.result())

    def produzir_opcao(self, rodada: Rodada, numero: int, ao_progresso: Progresso | None = None) -> ResultadoOpcao:
        """Gera e confere até dar certo. Depois de 3 reprovações, entrega a última imagem com o aviso."""
        opcao = rodada.analise.opcoes[numero - 1]
        avisar = ao_progresso or (lambda _numero, _texto: None)
        prompt = prompt_da_imagem(opcao)
        resultado = ResultadoOpcao(rodada, numero, opcao)
        for tentativa in range(1, MAX_TENTATIVAS + 1):
            avisar(numero, "Gerando" if tentativa == 1 else f"Gerando de novo ({tentativa} de {MAX_TENTATIVAS})")
            try:
                imagem = _normalizar_imagem(self.cliente.gerar_imagem(prompt, self.referencia, self.cancelar))
            except ModeloIndisponivel:
                raise
            except ErroIA as erro:
                resultado.erro = str(erro)
                self._registrar_conferencia(rodada, numero, tentativa, None, False, str(erro))
                continue
            caminho = self._gravar_imagem(rodada, numero, imagem)
            avisar(numero, "Conferindo")
            conferencia = self._conferir(imagem, opcao)
            self._registrar_conferencia(rodada, numero, tentativa, caminho, conferencia.aprovada, conferencia.observacao)
            resultado.imagem = caminho
            resultado.erro = ""
            resultado.aprovada = conferencia.aprovada
            resultado.observacao = conferencia.observacao
            if conferencia.aprovada:
                break
        if resultado.imagem is not None:
            resultado.erro = ""
        return resultado

    def _gravar_imagem(self, rodada: Rodada, numero: int, imagem: bytes) -> Path:
        # Toda tentativa fica guardada, inclusive as reprovadas.
        with self._trava:
            indice = len(list(rodada.pasta.glob(f"opcao_{numero}_*.png"))) + 1
            caminho = rodada.pasta / f"opcao_{numero}_{indice:02d}.png"
            caminho.write_bytes(imagem)
        return caminho

    def _conferir(self, imagem: bytes, opcao: OpcaoConceito) -> Conferencia:
        conteudo = [
            {"type": "text", "text": PROMPT_CONFERENCIA.replace("{componentes}", lista_de_componentes(opcao))},
            {"type": "image_url", "image_url": {"url": data_uri_png(imagem)}},
        ]
        try:
            resposta = self.cliente.conversar([{"role": "user", "content": conteudo}], None, "conceito_conferencia", self.cancelar, temperatura=0)
            dados = ler_json_da_ia(resposta.conteudo)
        except ModeloIndisponivel:
            raise
        except (ErroIA, ValueError) as erro:
            log.warning("Falha ao conferir a imagem: %s", erro)
            return Conferencia(False, "Não consegui conferir esta imagem.")
        campos = ("dois_paineis", "esquerda_montada", "direita_explodida", "mesmos_componentes")
        aprovada = all(dados.get(c) is True for c in campos) and dados.get("tem_texto") is False
        return Conferencia(aprovada, str(dados.get("observacao") or ""))

    def _registrar_conferencia(self, rodada: Rodada, numero: int, tentativa: int, caminho: Path | None, aprovada: bool, observacao: str) -> None:
        with self._trava:
            rodada.conferencias.append(
                {
                    "opcao": numero,
                    "tentativa": tentativa,
                    "imagem": caminho.name if caminho else None,
                    "aprovada": aprovada,
                    "observacao": observacao,
                }
            )
            gravar_json(rodada.pasta / "conferencia.json", rodada.conferencias)

    def escolher(self, resultado: ResultadoOpcao) -> dict[str, Any]:
        """Grava a escolha do usuário na peça e devolve os dados que o agente vai receber."""
        if resultado.imagem is None:
            raise ValueError("Esta opção não tem imagem.")
        rodada = resultado.rodada
        conceito = {
            "rodada": rodada.pasta.name,
            "imagem": resultado.imagem.relative_to(self.projeto.pasta).as_posix(),
            "referencia": self.caminho_referencia.relative_to(self.projeto.pasta).as_posix(),
            "pedido": rodada.pedido,
            "objeto": rodada.analise.objeto,
            "opcao": resultado.opcao.model_dump(),
            "medidas_criticas": rodada.analise.medidas_criticas,
            "aprovada_na_conferencia": resultado.aprovada,
            "observacao": resultado.observacao,
        }
        self.projeto.salvar_conceito_escolhido(conceito)
        self.projeto.estado["titulo"] = resultado.opcao.titulo
        self.projeto.salvar_estado()
        return conceito
