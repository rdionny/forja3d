"""Loop do agente. As travas de segurança ficam aqui, no código, e não só no prompt."""
from __future__ import annotations

import base64
import json
import logging
import re
import shutil
import threading
from collections import Counter
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from forja3d.agente.especificacao import Especificacao, problemas_de_tamanho
from forja3d.cad.executor import executar_script
from forja3d.conceitos.fluxo import mensagem_do_conceito
from forja3d.configuracao import Configuracao
from forja3d.fatiamento.orca import ErroFatiamento, ParametrosFatiamento, fatiar_layout
from forja3d.ia.cliente import Cancelado, ChamadaFerramenta, ClienteIA, ErroIA
from forja3d.projetos.armazenamento import Projeto
from forja3d.render.vistas import renderizar
from forja3d.verificacao.verificador import verificar_peca

log = logging.getLogger(__name__)

PASTA = Path(__file__).parent
DEFINICOES: list[dict[str, Any]] = json.loads((PASTA / "ferramentas.json").read_text(encoding="utf-8"))
PROMPT_SISTEMA = (PASTA.parent / "ia" / "prompts" / "agente_3d.md").read_text(encoding="utf-8")
SEM_CALIBRACAO = "Sem dados de calibração ainda; use as tabelas."
ROTULOS = {
    "especificacao": "Entendendo o pedido",
    "modelagem": "Modelando a peça",
    "reparo": "Corrigindo o modelo",
    "critica_visual": "Conferindo as vistas da peça",
    "conversa": "Revisando a peça",
}


class ArgsExecutarCad(BaseModel):
    codigo: str
    resumo_alteracao: str = ""


class ArgsFatiar(BaseModel):
    material: str
    qualidade: str = "padrao"
    paredes: int = Field(default=3, ge=2, le=8)
    preenchimento_pct: int = Field(default=15, ge=0, le=100)
    suporte: bool = False
    brim: bool = False


class Ouvinte:
    """Eventos do agente. A interface recebe isso em forma de sinais do Qt."""

    def etapa(self, texto: str) -> None: ...
    def especificacao(self, especificacao: dict[str, Any]) -> None: ...
    def versao(self, versao: str) -> None: ...
    def verificacao(self, versao: str, resultado: dict[str, Any]) -> None: ...
    def fatiamento(self, versao: str, resumo: dict[str, Any]) -> None: ...


def _sem_imagens(mensagens: list[dict[str, Any]]) -> list[dict[str, Any]]:
    limpas = []
    for mensagem in mensagens:
        if isinstance(mensagem.get("content"), list):
            mensagem = {**mensagem, "content": "[Sistema] Imagens anteriores, removidas do histórico."}
        limpas.append(mensagem)
    return limpas


def _e_a_mensagem_do_pedido(mensagem: dict[str, Any], texto: str) -> bool:
    """A conversa guarda o que o usuário viu. O conceito e a parte vão para o modelo em outro formato."""
    conteudo = mensagem.get("content")
    if mensagem["role"] != "user" or not isinstance(conteudo, str):
        return False
    if conteudo == texto:
        return True
    if texto.startswith("Escolhi o conceito: "):
        return conteudo.startswith("[CONCEITO ESCOLHIDO]") and f"Opção: {texto.removeprefix('Escolhi o conceito: ')[:-1]}:" in conteudo
    if texto.startswith("Sobre a parte "):
        pedido = texto.partition(": ")[2]
        return conteudo == pedido or (conteudo.startswith("[PARTE SELECIONADA]") and conteudo.endswith(f"Pedido: {pedido}"))
    return False


def _parte_imagem(caminho: Path) -> dict[str, Any]:
    dados = base64.b64encode(caminho.read_bytes()).decode()
    return {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{dados}"}}


def _normalizar_erro(texto: str) -> str:
    return re.sub(r"[\d.]+", "#", texto).strip().lower()


def _titulo(nome: str) -> str:
    """suporte_l_parede vira Suporte L parede."""
    palavras = [p.upper() if len(p) == 1 else p for p in nome.replace("_", " ").split()]
    texto = " ".join(palavras)
    return texto[:1].upper() + texto[1:] if texto else "Nova peça"


class Orquestrador:
    def __init__(
        self,
        config: Configuracao,
        perfil: dict[str, Any],
        cliente: ClienteIA,
        projeto: Projeto,
        ouvinte: Ouvinte | None = None,
        calibracao: str = SEM_CALIBRACAO,
    ) -> None:
        self.config = config
        self.perfil = perfil
        self.cliente = cliente
        self.projeto = projeto
        self.ouvinte = ouvinte or Ouvinte()
        self._cancelar = threading.Event()
        self._falhas: Counter[str] = Counter()
        self._forcar_texto: str | None = None
        self._proxima = "especificacao"
        self._versao_sem_verificar = False

        sistema = {"role": "system", "content": PROMPT_SISTEMA.replace("{{CALIBRACAO_USUARIO}}", calibracao)}
        self.mensagens: list[dict[str, Any]] = [sistema, *projeto.ler_historico()[1:]]

        self.versao_atual: str | None = projeto.estado.get("versao_atual")
        self.versao_aprovada: str | None = projeto.estado.get("versao_aprovada")
        self.especificacao: Especificacao | None = None
        self.resultado_verificacao: dict[str, Any] | None = None
        dados_espec = projeto.estado.get("especificacao")
        if dados_espec:
            self.especificacao = Especificacao.model_validate(dados_espec)
        if self.versao_atual:
            self.resultado_verificacao = projeto.verificacao(self.versao_atual)

    def cancelar(self) -> None:
        self._cancelar.set()

    def pode_aprovar(self) -> bool:
        return bool(self.versao_atual and self.resultado_verificacao and self.resultado_verificacao["aprovado"])

    def aprovar(self) -> None:
        """Só a interface chama isto: a aprovação nunca vem do modelo."""
        if not self.pode_aprovar():
            raise ValueError("Esta versão ainda não foi aprovada na verificação.")
        self.versao_aprovada = self.versao_atual
        self._salvar_estado()

    def restaurar_versao(self, versao: str) -> None:
        if versao not in self.projeto.versoes():
            raise ValueError(f"Versão desconhecida: {versao}")
        self._ir_para_versao(versao)
        codigo = (self.projeto.pasta_versao(versao) / "modelo.py").read_text(encoding="utf-8")
        self.mensagens.append(
            {
                "role": "user",
                "content": f"[Sistema] O usuário voltou para a versão {versao}. Daqui em diante, parta deste script:\n```python\n{codigo}\n```",
            }
        )
        self._salvar_estado()

    def _ir_para_versao(self, versao: str | None) -> None:
        self.versao_atual = versao
        self.versao_aprovada = None
        self.resultado_verificacao = self.projeto.verificacao(versao) if versao else None
        dados = self.projeto.especificacao(versao) if versao else None
        self.especificacao = Especificacao.model_validate(dados) if dados else None
        self._versao_sem_verificar = False

    def verificar_versao_atual(self) -> None:
        """Uma versão sem verificação, como a que vem de um arquivo, passa pelo mesmo crivo de uma nova."""
        self._verificar({})

    def voltar_ao_pedido(self, numero: int) -> dict[str, Any]:
        """Tira da conversa esse pedido e tudo o que veio depois, e devolve o registro dele.

        A peça volta para a versão daquele momento. As versões mais novas continuam salvas.
        """
        conversa = self.projeto.ler_conversa()
        posicoes = [i for i, r in enumerate(conversa) if r["papel"] == "usuario"]
        pedidos = [conversa[i] for i in posicoes]
        pedido = pedidos[numero]
        self.projeto.arquivar_conversa()
        inicio = pedido["indice"] if "indice" in pedido else self._indice_sem_marco(pedidos, numero)
        self.mensagens = self.mensagens[:inicio]
        versao = pedido["versao"] if "versao" in pedido else self.projeto.versao_em(pedido["em"])
        aprovada = self.versao_aprovada if versao == self.versao_atual else None
        self._ir_para_versao(versao)
        self.versao_aprovada = aprovada
        self.projeto.reescrever_conversa(conversa[: posicoes[numero]])
        self._salvar_historico()
        self._salvar_estado()
        return pedido

    def _indice_sem_marco(self, pedidos: list[dict[str, Any]], numero: int) -> int:
        """Conversas antigas não guardam onde cada pedido começa: procura pelo texto, na ordem."""
        cursor = 1
        for posicao, pedido in enumerate(pedidos):
            achado = next((i for i in range(cursor, len(self.mensagens)) if _e_a_mensagem_do_pedido(self.mensagens[i], pedido["texto"])), None)
            if achado is None:
                continue
            cursor = achado + 1
            if posicao >= numero:
                return achado
        return len(self.mensagens)

    def _registrar_pedido(self, texto: str, imagem: str | None = None) -> None:
        # O marco diz onde este pedido começa, para a conversa poder voltar até ele.
        self.projeto.registrar_conversa("usuario", texto, imagem, indice=len(self.mensagens), versao=self.versao_atual)

    def processar(self, texto: str) -> str:
        """Atende a um pedido do usuário e devolve a resposta final.

        Não limpa o cancelamento aqui: um clique em Cancelar logo depois de enviar chega antes
        de esta thread começar. Quem inicia a operação chama `preparar_operacao` antes.
        """
        self._registrar_pedido(texto)
        return self._atender(texto)

    def processar_conceito(self, conceito: dict[str, Any]) -> str:
        """Segue a partir do conceito que o usuário escolheu no diálogo de imagem de referência."""
        self._registrar_pedido(f"Escolhi o conceito: {conceito['opcao']['titulo']}.", conceito["imagem"])
        self.mensagens = _sem_imagens(self.mensagens)
        return self._atender(mensagem_do_conceito(conceito))

    def processar_parte(self, texto: str, parte: str) -> str:
        """Atende a um pedido sobre uma parte que o usuário selecionou na mesa."""
        self._registrar_pedido(f"Sobre a parte {parte}: {texto}")
        medidas = self.projeto.medidas(self.versao_atual) if self.versao_atual else []
        dados = next((p for p in medidas if p["nome"] == parte), None)
        if dados is None:
            return self._atender(texto)
        x, y, z = dados["tamanho"]
        outras = ", ".join(p["nome"] for p in medidas if p["nome"] != parte) or "nenhuma"
        mensagem = (
            "[PARTE SELECIONADA]\n"
            f"Parte: {parte}\n"
            f"Tamanho: {x:g} x {y:g} x {z:g} mm\n"
            f"Furos: {len(dados['furos'])}\n"
            f"Outras partes, que devem continuar iguais: {outras}\n"
            f"Versão atual: {self.versao_atual}\n"
            f"Pedido: {texto}"
        )
        self.mensagens = _sem_imagens(self.mensagens)
        return self._atender(mensagem, self._vistas_da_parte(dados))

    def _vistas_da_parte(self, dados: dict[str, Any]) -> list[Path]:
        self.ouvinte.etapa("Gerando as vistas da parte selecionada")
        pasta_versao = self.projeto.pasta_versao(self.versao_atual)
        try:
            return renderizar([pasta_versao / dados["stl"]], pasta_versao / "renders_partes" / dados["nome"], cancelar=self._cancelar)
        except RuntimeError as erro:
            log.warning("Falha ao renderizar a parte: %s", erro)
            return []

    def _atender(self, texto: str, vistas: list[Path] | None = None) -> str:
        self._falhas.clear()
        self._forcar_texto = None
        tamanho_antes = len(self.mensagens)
        self.mensagens.append({"role": "user", "content": texto})
        if vistas:
            legenda = {"type": "text", "text": "[Sistema] Vistas da parte selecionada: " + ", ".join(v.stem for v in vistas) + "."}
            self.mensagens.append({"role": "user", "content": [legenda, *(_parte_imagem(v) for v in vistas)]})
        self._anexar_conceito_se_faltar()
        self._proxima = "modelagem" if self.especificacao else "especificacao"
        try:
            resposta = self._loop()
        except Cancelado:
            self.mensagens.append({"role": "user", "content": "[Sistema] O usuário cancelou a operação anterior."})
            resposta = "Operação cancelada. Nada foi alterado além das versões já salvas."
        except ErroIA as erro:
            # Se a IA não chegou a responder, reenviar não pode deixar o mesmo pedido repetido no histórico.
            if not any(m.get("role") == "assistant" for m in self.mensagens[tamanho_antes:]):
                del self.mensagens[tamanho_antes:]
            self.projeto.registrar_conversa("erro", str(erro))
            raise
        finally:
            self.projeto.limpar_execucoes()
            self._salvar_historico()
        self.projeto.registrar_conversa("agente", resposta)
        return resposta

    def _loop(self) -> str:
        pedido_de_texto = False
        for _ in range(self.config.max_chamadas_por_pedido):
            self.ouvinte.etapa(ROTULOS.get(self._proxima, "Trabalhando"))
            ferramentas = None if self._forcar_texto else DEFINICOES
            if self._forcar_texto:
                self.mensagens.append({"role": "user", "content": self._forcar_texto})
            resposta = self.cliente.conversar(self.mensagens, ferramentas, self._proxima, self._cancelar)

            if not resposta.chamadas:
                if resposta.conteudo.strip():
                    self.mensagens.append(resposta.mensagem)
                    # O modelo pode apresentar a peça sem verificar. Quem garante a verificação é o código.
                    if self._versao_sem_verificar and self.versao_atual and self.especificacao:
                        verificacao, _ = self._verificar({})
                        if verificacao["bloqueios"]:
                            motivos = "; ".join(i["mensagem"] for i in verificacao["bloqueios"])
                            self.mensagens.append(
                                {
                                    "role": "user",
                                    "content": f"[Sistema] A verificação da versão {self.versao_atual} reprovou a peça: {motivos}. Corrija antes de apresentar ao usuário.",
                                }
                            )
                            continue
                    return resposta.conteudo
                if pedido_de_texto:
                    return "A IA não devolveu nenhum texto. Tente reformular o pedido."
                pedido_de_texto = True
                self._forcar_texto = "[Sistema] Responda ao usuário em texto, agora."
                continue

            self.mensagens.append(resposta.mensagem)
            imagens: list[Path] = []
            for indice, chamada in enumerate(resposta.chamadas):
                try:
                    resultado, novas = self._executar(chamada)
                except Cancelado:
                    # Toda chamada precisa de uma resposta, ou o histórico fica inválido para a API.
                    for pendente in resposta.chamadas[indice:]:
                        self._responder(pendente, {"ok": False, "erro": "Cancelado pelo usuário."})
                    raise
                imagens += novas
                self._responder(chamada, resultado)
            if imagens:
                self._anexar_imagens(imagens)
            self._salvar_historico()

        self.mensagens.append(
            {"role": "user", "content": "[Sistema] Limite de chamadas atingido. Pare e explique ao usuário em que pé está."}
        )
        final = self.cliente.conversar(self.mensagens, None, "conversa", self._cancelar)
        self.mensagens.append(final.mensagem)
        return final.conteudo or "Atingi o limite de etapas deste pedido. Tente dividir o pedido em partes menores."

    def _responder(self, chamada: ChamadaFerramenta, resultado: dict[str, Any]) -> None:
        self.mensagens.append({"role": "tool", "tool_call_id": chamada.id, "content": json.dumps(resultado, ensure_ascii=False)})

    def _anexar_imagens(self, imagens: list[Path]) -> None:
        # role=tool só leva texto. Só as vistas da versão mais recente ficam no histórico.
        self.mensagens = _sem_imagens(self.mensagens)
        conteudo: list[dict[str, Any]] = [
            {"type": "text", "text": f"[Sistema] Vistas da versão {self.versao_atual}: " + ", ".join(i.stem for i in imagens) + "."}
        ]
        conteudo += [_parte_imagem(imagem) for imagem in imagens]
        conceito = self.projeto.conceito_escolhido()
        if conceito:
            conteudo += [
                {"type": "text", "text": "[Sistema] Imagem do conceito escolhido, para comparar. Ela é ilustração, não medida."},
                _parte_imagem(self.projeto.pasta / conceito["imagem"]),
            ]
        self.mensagens.append({"role": "user", "content": conteudo})

    def _anexar_conceito_se_faltar(self) -> None:
        # As imagens não vão para o histórico em disco: depois de reabrir a peça, precisam voltar.
        conceito = self.projeto.conceito_escolhido()
        if not conceito or any(isinstance(m.get("content"), list) for m in self.mensagens):
            return
        conteudo = [
            {"type": "text", "text": "[Sistema] Imagem de referência do usuário e imagem do conceito escolhido, nesta ordem."},
            _parte_imagem(self.projeto.pasta / conceito["referencia"]),
            _parte_imagem(self.projeto.pasta / conceito["imagem"]),
        ]
        self.mensagens.append({"role": "user", "content": conteudo})

    def _registrar_falha(self, chave: str) -> None:
        self._falhas[_normalizar_erro(chave)] += 1
        if self._falhas[_normalizar_erro(chave)] > self.config.max_reparos_mesmo_erro:
            self._forcar_texto = (
                "[Sistema] Limite de correções atingido para este erro. Não tente de novo. "
                "Explique o problema ao usuário em linguagem simples e proponha alternativas."
            )

    def _executar(self, chamada: ChamadaFerramenta) -> tuple[dict[str, Any], list[Path]]:
        try:
            argumentos = json.loads(chamada.argumentos or "{}")
        except json.JSONDecodeError as erro:
            return {"ok": False, "erro": f"Argumentos em JSON inválido: {erro}"}, []
        funcoes = {
            "definir_especificacao": self._definir_especificacao,
            "executar_cad": self._executar_cad,
            "verificar_peca": self._verificar,
            "renderizar_vistas": self._renderizar,
            "fatiar_peca": self._fatiar_ferramenta,
        }
        funcao = funcoes.get(chamada.nome)
        if funcao is None:
            return {"ok": False, "erro": f"Ferramenta desconhecida: {chamada.nome}"}, []
        try:
            return funcao(argumentos)
        except ValidationError as erro:
            return {"ok": False, "erro": f"Argumentos inválidos: {erro.errors(include_url=False, include_input=False)}"}, []

    def _definir_especificacao(self, argumentos: dict[str, Any]) -> tuple[dict[str, Any], list[Path]]:
        self.ouvinte.etapa("Registrando a especificação")
        especificacao = Especificacao.model_validate(argumentos)
        problemas = problemas_de_tamanho(especificacao, self.perfil)
        if problemas:
            return {"ok": False, "problemas": problemas}, []
        self.especificacao = especificacao
        self.resultado_verificacao = None
        self.projeto.titulo_automatico(_titulo(especificacao.nome))
        self._salvar_estado()
        self.ouvinte.especificacao(especificacao.model_dump())
        self._proxima = "modelagem"
        return {"ok": True, "problemas": []}, []

    def _executar_cad(self, argumentos: dict[str, Any]) -> tuple[dict[str, Any], list[Path]]:
        dados = ArgsExecutarCad.model_validate(argumentos)
        if self.especificacao is None:
            return {"ok": False, "erro": "Chame definir_especificacao antes de executar_cad."}, []
        self.ouvinte.etapa("Gerando o sólido")
        pasta = self.projeto.pasta_temporaria()
        resultado = executar_script(dados.codigo, pasta, cancelar=self._cancelar)
        if not resultado.ok:
            shutil.rmtree(pasta, ignore_errors=True)
            self._proxima = "reparo"
            self._registrar_falha(f"{resultado.tipo_erro}: {resultado.erro}")
            return {"ok": False, "tipo": resultado.tipo_erro, "erro": resultado.erro, "linha": resultado.linha, "trecho": resultado.trecho}, []
        versao = self.projeto.registrar_versao(pasta, self.especificacao.model_dump(), resultado.partes, dados.resumo_alteracao)
        self.versao_atual = versao
        self.versao_aprovada = None
        self.resultado_verificacao = None
        self._versao_sem_verificar = True
        self._salvar_estado()
        self.ouvinte.versao(versao)
        self._proxima = "conversa"
        return {
            "ok": True,
            "versao": versao,
            "partes": [
                {k: p[k] for k in ("nome", "tamanho", "solidos", "volume_mm3", "valido")} | {"furos": len(p["furos"])}
                for p in resultado.partes
            ],
        }, []

    def _verificar(self, _argumentos: dict[str, Any]) -> tuple[dict[str, Any], list[Path]]:
        if not self.versao_atual or self.especificacao is None:
            return {"ok": False, "erro": "Ainda não há versão para verificar. Chame executar_cad."}, []
        self.ouvinte.etapa("Verificando medidas e imprimibilidade")
        pasta = self.projeto.pasta_versao(self.versao_atual)
        resultado = verificar_peca(self.projeto.medidas(self.versao_atual), self.especificacao, self.perfil, pasta)
        self.resultado_verificacao = resultado
        self._versao_sem_verificar = False
        self.projeto.salvar_verificacao(self.versao_atual, resultado)
        self.ouvinte.verificacao(self.versao_atual, resultado)
        if resultado["bloqueios"]:
            self._proxima = "reparo"
            self._registrar_falha("verificacao " + " ".join(sorted(i["codigo"] for i in resultado["bloqueios"])))
        else:
            self._proxima = "conversa"
        return resultado, []

    def _renderizar(self, _argumentos: dict[str, Any]) -> tuple[dict[str, Any], list[Path]]:
        if not self.versao_atual or self.resultado_verificacao is None:
            return {"ok": False, "erro": "Chame verificar_peca antes de renderizar_vistas."}, []
        if self.resultado_verificacao["bloqueios"]:
            return {"ok": False, "erro": "A versão atual tem bloqueios. Corrija antes de renderizar."}, []
        self.ouvinte.etapa("Gerando as vistas da peça")
        pasta = self.projeto.pasta_versao(self.versao_atual) / "renders"
        try:
            imagens = renderizar(self.projeto.stls(self.versao_atual), pasta, cancelar=self._cancelar)
        except RuntimeError as erro:
            log.warning("Falha ao renderizar: %s", erro)
            return {"ok": False, "erro": "Não consegui gerar as vistas. Siga sem a crítica visual e avise o usuário."}, []
        self._proxima = "critica_visual"
        return {"ok": True, "vistas": [i.stem for i in imagens]}, imagens

    def _fatiar_ferramenta(self, argumentos: dict[str, Any]) -> tuple[dict[str, Any], list[Path]]:
        dados = ArgsFatiar.model_validate(argumentos)
        if not self.versao_atual or self.versao_aprovada != self.versao_atual:
            return {
                "ok": False,
                "erro": "O usuário ainda não aprovou esta versão. Peça para ele clicar em Aprovar e preparar.",
            }, []
        try:
            resumo = self.fatiar(ParametrosFatiamento(**dados.model_dump()))
        except ErroFatiamento as erro:
            return {"ok": False, "erro": str(erro)}, []
        return {"ok": True, **resumo["validacao"]}, []

    def fatiar(self, parametros: ParametrosFatiamento) -> dict[str, Any]:
        """Determinístico. Exige uma versão aprovada pelo usuário e sem bloqueios."""
        versao = self.versao_atual
        if not versao or self.versao_aprovada != versao or not self.pode_aprovar():
            raise ErroFatiamento("Esta versão precisa estar aprovada, sem bloqueios, antes de fatiar.")
        self.ouvinte.etapa("Fatiando")
        resumo = fatiar_layout(
            self.projeto.layout(versao, self.perfil),
            self.projeto.stls_por_parte(versao),
            parametros,
            self.perfil,
            self.projeto.pasta_impressao(versao, parametros.material, parametros.qualidade),
            self.config.caminho_orca,
            self._cancelar,
        )
        self.ouvinte.fatiamento(versao, resumo)
        validacao = resumo["validacao"]
        self.mensagens.append(
            {
                "role": "user",
                "content": (
                    f"[Sistema] Versão {versao} fatiada em {parametros.material}, qualidade {parametros.qualidade}: "
                    f"{validacao.get('tempo', '?')}, {validacao.get('filamento_g', '?')} g, "
                    f"validação {'aprovada' if validacao['aprovado'] else 'reprovada'}."
                ),
            }
        )
        self._salvar_historico()
        return resumo

    def preparar_operacao(self) -> None:
        """Limpa um cancelamento antigo. Chame na thread que inicia a operação, antes de iniciá-la."""
        self._cancelar.clear()

    def _salvar_estado(self) -> None:
        self.projeto.estado.update(
            {
                "versao_atual": self.versao_atual,
                "versao_aprovada": self.versao_aprovada,
                "especificacao": self.especificacao.model_dump() if self.especificacao else None,
            }
        )
        self.projeto.salvar_estado()

    def _salvar_historico(self) -> None:
        self.projeto.salvar_historico(_sem_imagens(self.mensagens))
