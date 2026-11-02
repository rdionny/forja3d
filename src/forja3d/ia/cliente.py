"""Cliente do endpoint de IA, com erros em português e chamada cancelável."""
from __future__ import annotations

import base64
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import openai
from openai import OpenAI

from forja3d.configuracao import ConfigIA

log = logging.getLogger(__name__)


class ErroIA(Exception):
    """Falha ao falar com a IA, com uma mensagem pronta para o usuário."""


class ModeloIndisponivel(ErroIA):
    """O servidor não consegue atender a este modelo. Repetir o pedido não adianta."""


class Cancelado(Exception):
    """O usuário cancelou a operação."""


@dataclass(frozen=True)
class ImagemGerada:
    dados: bytes
    tipo: str


def data_uri_png(dados: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(dados).decode()


def eh_modelo_de_imagem(modelo: str) -> bool:
    nome = modelo.lower()
    return any(trecho in nome for trecho in ("image", "dall-e", "flux", "imagen", "sdxl"))


def usa_rota_de_imagens(modelo: str) -> bool:
    """Os modelos gpt-image respondem em /images/edits. Os demais devolvem a imagem pelo chat."""
    return modelo.startswith("gpt-image")


@dataclass(frozen=True)
class ChamadaFerramenta:
    id: str
    nome: str
    argumentos: str


@dataclass(frozen=True)
class RespostaIA:
    conteudo: str
    chamadas: list[ChamadaFerramenta]
    mensagem: dict[str, Any] = field(repr=False)
    tokens: dict[str, Any] = field(default_factory=dict)


def traduzir_erro(erro: Exception) -> ErroIA:
    if isinstance(erro, openai.AuthenticationError):
        return ErroIA("A IA recusou a chave da API. Confira a chave em Configurações.")
    if isinstance(erro, openai.RateLimitError):
        return ErroIA("O endpoint de IA atingiu o limite de uso. Aguarde um pouco e tente de novo.")
    if isinstance(erro, openai.APITimeoutError):
        return ErroIA("A IA demorou demais para responder. Tente de novo.")
    if isinstance(erro, openai.APIConnectionError):
        return ErroIA("Não consegui conectar ao endpoint de IA. Confira a rede e o endereço em Configurações.")
    if isinstance(erro, openai.NotFoundError):
        return ErroIA("O endpoint não reconheceu o endereço ou o modelo. Confira em Configurações.")
    if isinstance(erro, openai.APIStatusError) and ("auth_unavailable" in str(erro) or erro.code == "model_not_found"):
        return ModeloIndisponivel("O modelo escolhido não está disponível no servidor agora. Escolha outro em Configurações.")
    if isinstance(erro, openai.APIStatusError):
        return ErroIA(f"O endpoint de IA respondeu com erro {erro.status_code}. Tente de novo em instantes.")
    return ErroIA(f"Erro inesperado ao falar com a IA: {type(erro).__name__}.")


class ClienteIA:
    def __init__(self, config: ConfigIA) -> None:
        self._config = config
        self._cliente = OpenAI(
            base_url=config.endpoint,
            api_key=config.api_key or "sem-chave",
            timeout=config.timeout_s,
            max_retries=config.max_tentativas_rede,
        )
        self._cliente_imagem = self._cliente
        if config.endpoint_imagem and (config.endpoint_imagem, config.api_key_imagem) != (config.endpoint, config.api_key):
            self._cliente_imagem = OpenAI(
                base_url=config.endpoint_imagem,
                api_key=config.api_key_imagem or "sem-chave",
                timeout=config.timeout_s,
                max_retries=config.max_tentativas_rede,
            )

    @property
    def modelo(self) -> str:
        return self._config.modelo

    def listar_modelos(self) -> list[str]:
        try:
            return sorted(m.id for m in self._cliente.models.list().data)
        except Exception as erro:
            raise traduzir_erro(erro) from erro

    def verificar(self) -> None:
        """Confirma que o endpoint responde e que o modelo configurado existe."""
        modelos = self.listar_modelos()
        if self._config.modelo not in modelos:
            raise ErroIA(f"O modelo {self._config.modelo} não está disponível no endpoint. Escolha outro em Configurações.")

    def conversar(
        self,
        mensagens: list[dict[str, Any]],
        ferramentas: list[dict[str, Any]] | None,
        etapa: str,
        cancelar: threading.Event | None = None,
        temperatura: float | None = None,
    ) -> RespostaIA:
        argumentos: dict[str, Any] = {
            "model": self._config.modelo,
            "messages": mensagens,
            "temperature": self._config.temperatura if temperatura is None else temperatura,
        }
        esforco = self._config.esforco.get(etapa)
        if esforco:
            argumentos["reasoning_effort"] = esforco
        if ferramentas:
            argumentos["tools"] = ferramentas

        inicio = time.monotonic()
        resposta = self._criar(self._cliente, argumentos, cancelar)
        mensagem = resposta.choices[0].message
        chamadas = [
            ChamadaFerramenta(c.id, c.function.name, c.function.arguments or "{}") for c in (mensagem.tool_calls or [])
        ]
        tokens = resposta.usage.model_dump(exclude_none=True) if resposta.usage else {}
        log.info(
            "etapa=%s duracao=%.1fs chamadas=%s modelo=%s tokens=%s",
            etapa,
            time.monotonic() - inicio,
            [c.nome for c in chamadas],
            resposta.model,
            tokens,
        )
        return RespostaIA(mensagem.content or "", chamadas, mensagem.model_dump(exclude_none=True), tokens)

    def gerar_imagem(self, prompt: str, referencia: bytes, cancelar: threading.Event | None = None) -> ImagemGerada:
        """Gera uma imagem por chamada: o modelo recusa `n` maior que 1."""
        inicio = time.monotonic()
        # Sem as novas tentativas do SDK: quem repete é o fluxo de conceitos, que sabe quando não adianta.
        cliente = self._cliente_imagem.with_options(max_retries=0)
        if usa_rota_de_imagens(self._config.modelo_imagem):
            imagem = self._gerar_pela_rota_de_imagens(cliente, prompt, referencia, cancelar)
        else:
            imagem = self._gerar_pelo_chat(cliente, prompt, referencia, cancelar)
        log.info("imagem modelo=%s duracao=%.1fs", self._config.modelo_imagem, time.monotonic() - inicio)
        return imagem

    def _gerar_pelo_chat(self, cliente: OpenAI, prompt: str, referencia: bytes, cancelar: threading.Event | None) -> ImagemGerada:
        conteudo = [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": data_uri_png(referencia)}},
        ]
        argumentos = {"model": self._config.modelo_imagem, "messages": [{"role": "user", "content": conteudo}], "max_tokens": 8000}
        resposta = self._criar(cliente, argumentos, cancelar)
        imagens = (resposta.choices[0].message.model_extra or {}).get("images") or []
        if not imagens:
            raise ErroIA("O modelo de imagem não devolveu nenhuma imagem. Tente de novo.")
        cabecalho, _, dados = imagens[0]["image_url"]["url"].partition(";base64,")
        if not dados or not cabecalho.startswith("data:image/"):
            raise ErroIA("O modelo de imagem devolveu um formato que não reconheço. Tente de novo.")
        return ImagemGerada(base64.b64decode(dados), cabecalho.removeprefix("data:image/"))

    def _gerar_pela_rota_de_imagens(self, cliente: OpenAI, prompt: str, referencia: bytes, cancelar: threading.Event | None) -> ImagemGerada:
        resposta = self._executar(
            lambda: cliente.images.edit(
                model=self._config.modelo_imagem,
                image=("referencia.png", referencia, "image/png"),
                prompt=prompt,
                size="1536x1024",
            ),
            cancelar,
        )
        dados = resposta.data[0].b64_json if resposta.data else None
        if not dados:
            raise ErroIA("O modelo de imagem não devolveu nenhuma imagem. Tente de novo.")
        return ImagemGerada(base64.b64decode(dados), "png")

    def _criar(self, cliente: OpenAI, argumentos: dict[str, Any], cancelar: threading.Event | None) -> Any:
        resposta = self._executar(lambda: cliente.chat.completions.create(**argumentos), cancelar)
        if not resposta.choices:
            raise ErroIA("A IA devolveu uma resposta vazia. Tente de novo.")
        return resposta

    @staticmethod
    def _executar(chamada: Callable[[], Any], cancelar: threading.Event | None) -> Any:
        # O SDK síncrono não é interrompível: a chamada corre em uma thread descartável.
        caixa: dict[str, Any] = {}

        def chamar() -> None:
            try:
                caixa["resposta"] = chamada()
            except Exception as erro:  # noqa: BLE001
                caixa["erro"] = erro

        linha = threading.Thread(target=chamar, daemon=True)
        linha.start()
        while linha.is_alive():
            linha.join(0.1)
            if cancelar is not None and cancelar.is_set():
                raise Cancelado
        if "erro" in caixa:
            raise traduzir_erro(caixa["erro"]) from caixa["erro"]
        return caixa["resposta"]
