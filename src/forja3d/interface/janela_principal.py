"""Janela principal do Forja3D."""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import QEvent, QObject, QSize, Qt, QThread, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from forja3d import configuracao
from forja3d.conceitos.referencia import ReferenciaInvalida, preparar_referencia
from forja3d.configuracao import Configuracao
from forja3d.fatiamento.exportacao import ExportacaoBloqueada, exportar_gcode, nome_sugerido, unidades_removiveis
from forja3d.fatiamento.orca import ParametrosFatiamento, localizar_orca
from forja3d.ia.cliente import ClienteIA, ErroIA
from forja3d.interface import tema
from forja3d.interface.dialogo_conceitos import DialogoConceitos
from forja3d.interface.dialogo_config import DialogoConfig
from forja3d.interface.painel_3d import Painel3D
from forja3d.interface.painel_chat import PainelChat
from forja3d.interface.painel_checks import PainelChecks
from forja3d.interface.painel_versoes import PainelVersoes
from forja3d.interface.trabalhador import Trabalhador
from forja3d.projetos.armazenamento import Projeto
from forja3d.registro import proteger_chaves

log = logging.getLogger(__name__)

ICONE_CONFIGURACOES = Path(__file__).parent / "icones" / "engrenagem.svg"
QUALIDADES = (("Padrão, 0,20 mm", "padrao"), ("Ótima, 0,16 mm", "otima"), ("Fina, 0,12 mm", "fina"), ("Rascunho, 0,24 mm", "rascunho"))
AJUSTES_POR_CARGA = {"nenhuma": (2, 15), "leve": (3, 15), "media": (4, 25), "alta": (5, 40)}


class SemRodaSemFoco(QObject):
    """Passar a roda do mouse por cima não pode trocar uma opção de impressão sem ninguém notar."""

    def eventFilter(self, alvo: QObject, evento: QEvent) -> bool:  # noqa: N802 - nome definido pelo Qt.
        if evento.type() == QEvent.Type.Wheel and not alvo.hasFocus():
            evento.ignore()
            return True
        return False


class JanelaPrincipal(QMainWindow):
    _pedir_processar = Signal(str)
    _pedir_conceito = Signal(object)
    _pedir_fatiar = Signal(object)
    _estado_ia = Signal(bool, str)

    def __init__(self, config: Configuracao, perfil: dict[str, Any], fabrica_cliente: Callable[..., Any] = ClienteIA, verificar_endpoint: bool = True) -> None:
        super().__init__()
        self.config = config
        self.perfil = perfil
        self.projeto: Projeto | None = None
        self.ocupado = False
        self.versao_vista: str | None = None
        self.pasta_impressao: Path | None = None
        self._fabrica_cliente = fabrica_cliente

        self.setWindowTitle("Forja3D")
        self.resize(1440, 880)
        self.setMinimumSize(1100, 680)

        self._linha = QThread(self)
        self.trabalhador = Trabalhador(config, perfil, fabrica_cliente)
        self.trabalhador.moveToThread(self._linha)
        self.trabalhador.ouvinte.moveToThread(self._linha)
        self._linha.start()

        self._montar()
        self._conectar()
        self._recarregar_projetos()
        self._atualizar_botoes()
        self._atualizar_orca()
        if verificar_endpoint:
            self._verificar_endpoint()

    # ---------- Montagem ----------

    def _montar(self) -> None:
        self.painel_versoes = PainelVersoes()
        self.painel_3d = Painel3D(self.perfil)
        self.painel_chat = PainelChat()
        self.painel_checks = PainelChecks()

        centro = QWidget()
        coluna = QVBoxLayout(centro)
        coluna.setContentsMargins(0, 0, 0, 0)
        coluna.setSpacing(0)
        coluna.addWidget(self._criar_topo())
        coluna.addWidget(self.painel_3d, 1)
        coluna.addWidget(self._criar_barra_impressao())

        direita = QSplitter(Qt.Orientation.Vertical)
        direita.addWidget(self.painel_chat)
        direita.addWidget(self.painel_checks)
        direita.setStretchFactor(0, 4)
        direita.setStretchFactor(1, 1)
        direita.setSizes([640, 220])

        principal = QSplitter(Qt.Orientation.Horizontal)
        principal.addWidget(self.painel_versoes)
        principal.addWidget(centro)
        principal.addWidget(direita)
        principal.setStretchFactor(1, 1)
        principal.setChildrenCollapsible(False)
        self.painel_versoes.setMinimumWidth(190)
        direita.setMinimumWidth(400)
        principal.setSizes([215, 775, 450])
        self.setCentralWidget(principal)

        self.rotulo_ia = QLabel("IA: verificando…")
        self.rotulo_orca = QLabel("")
        self.statusBar().addWidget(self.rotulo_ia)
        self.statusBar().addWidget(self.rotulo_orca)
        self.statusBar().setSizeGripEnabled(False)

    def _criar_topo(self) -> QWidget:
        topo = QWidget(objectName="painel")
        linha = QHBoxLayout(topo)
        linha.setContentsMargins(14, 8, 10, 8)
        linha.addWidget(QLabel("Forja3D", objectName="marca"))
        linha.addSpacing(10)
        self.rotulo_projeto = QLabel("Nova peça", objectName="suave")
        linha.addWidget(self.rotulo_projeto)
        linha.addStretch(1)
        linha.addWidget(QLabel("Creality Ender-3 V3 SE, bico de 0,4 mm", objectName="suave"))
        linha.addSpacing(10)
        self.botao_config = QPushButton(objectName="icone", toolTip="Configurações: modelos e provedores de IA", accessibleName="Configurações")
        self.botao_config.setIcon(QIcon(str(ICONE_CONFIGURACOES)))
        self.botao_config.setIconSize(QSize(20, 20))
        self.botao_config.setFixedSize(32, 32)
        self.botao_config.clicked.connect(self._abrir_configuracoes)
        linha.addWidget(self.botao_config)
        return topo

    def _criar_barra_impressao(self) -> QWidget:
        barra = QFrame(objectName="barraImpressao")
        coluna = QVBoxLayout(barra)
        coluna.setContentsMargins(14, 10, 14, 12)
        coluna.setSpacing(10)

        opcoes = QHBoxLayout()
        opcoes.setSpacing(6)
        self.combo_material = QComboBox()
        self.combo_material.addItems(list(self.perfil["materiais"]))
        self.combo_qualidade = QComboBox()
        for texto, valor in QUALIDADES:
            self.combo_qualidade.addItem(texto, valor)
        self.combo_qualidade.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.spin_paredes = QSpinBox(minimum=2, maximum=8, value=3)
        self.spin_paredes.setFixedWidth(62)
        self.spin_preenchimento = QSpinBox(minimum=0, maximum=100, value=15, suffix=" %", singleStep=5)
        self.spin_preenchimento.setFixedWidth(76)
        self.caixa_suporte = QCheckBox("Suporte")
        self.caixa_brim = QCheckBox("Brim", toolTip="Aba na primeira camada, que ajuda a peça a aderir à mesa")
        self._filtro_roda = SemRodaSemFoco(self)
        for campo in (self.combo_material, self.combo_qualidade, self.spin_paredes, self.spin_preenchimento):
            campo.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            campo.installEventFilter(self._filtro_roda)
        for rotulo, campo in (("Material", self.combo_material), ("Qualidade", self.combo_qualidade), ("Paredes", self.spin_paredes), ("Preenchimento", self.spin_preenchimento)):
            opcoes.addWidget(QLabel(rotulo, objectName="suave"))
            opcoes.addWidget(campo)
            opcoes.addSpacing(8)
        opcoes.addWidget(self.caixa_suporte)
        opcoes.addSpacing(6)
        opcoes.addWidget(self.caixa_brim)
        opcoes.addStretch(1)
        coluna.addLayout(opcoes)

        acoes = QHBoxLayout()
        acoes.setSpacing(10)
        self.botao_aprovar = QPushButton("Aprovar e preparar", objectName="primario", toolTip="Aprova esta versão e gera o G-code")
        self.botao_aprovar.clicked.connect(self._aprovar_e_fatiar)
        self.rotulo_resumo = QLabel("Gere uma peça para preparar a impressão.", objectName="suave", wordWrap=True)
        self.botao_exportar = QPushButton("Exportar G-code", toolTip="Copia o arquivo validado para o cartão SD")
        self.botao_exportar.clicked.connect(self._exportar)
        acoes.addWidget(self.botao_aprovar)
        acoes.addWidget(self.rotulo_resumo, 1)
        acoes.addWidget(self.botao_exportar)
        coluna.addLayout(acoes)
        return barra

    def _conectar(self) -> None:
        self.painel_chat.enviado.connect(self._enviar)
        self.painel_chat.cancelar.connect(self._cancelar)
        self.painel_chat.imagem_pedida.connect(self._escolher_imagem)
        self.painel_chat.imagem_recebida.connect(self._abrir_conceitos)
        self.painel_versoes.nova_peca.connect(self._nova_peca)
        self.painel_versoes.projeto_escolhido.connect(self.abrir_projeto)
        self.painel_versoes.versao_escolhida.connect(self._ver_versao)
        self.painel_versoes.restaurar_pedido.connect(self._restaurar_versao)

        self._pedir_processar.connect(self.trabalhador.processar)
        self._pedir_conceito.connect(self.trabalhador.processar_conceito)
        self._pedir_fatiar.connect(self.trabalhador.aprovar_e_fatiar)
        self.trabalhador.resposta.connect(self._ao_responder)
        self.trabalhador.erro.connect(self._ao_errar)
        self.trabalhador.concluido.connect(self._ao_concluir)
        self.trabalhador.fatiado.connect(self._ao_fatiar)

        ouvinte = self.trabalhador.ouvinte
        ouvinte.sinal_etapa.connect(self.painel_chat.definir_etapa)
        ouvinte.sinal_especificacao.connect(self._ao_especificar)
        ouvinte.sinal_versao.connect(self._ao_criar_versao)
        ouvinte.sinal_verificacao.connect(self._ao_verificar)
        self._estado_ia.connect(self._mostrar_estado_ia)

        for campo in (self.combo_material, self.combo_qualidade):
            campo.currentIndexChanged.connect(self._ao_mudar_opcoes)
        for campo in (self.spin_paredes, self.spin_preenchimento):
            campo.valueChanged.connect(self._ao_mudar_opcoes)
        for campo in (self.caixa_suporte, self.caixa_brim):
            campo.toggled.connect(self._ao_mudar_opcoes)

    # ---------- Estado ----------

    @property
    def orquestrador(self):  # noqa: ANN201
        return self.trabalhador.orquestrador

    def _definir_ocupado(self, ocupado: bool) -> None:
        self.ocupado = ocupado
        self.painel_chat.definir_ocupado(ocupado)
        self.painel_versoes.botao_nova.setEnabled(not ocupado)
        self.painel_versoes.lista_projetos.setEnabled(not ocupado)
        self.painel_versoes.lista_versoes.setEnabled(not ocupado)
        self.painel_versoes.botao_restaurar.setEnabled(not ocupado)
        self.botao_config.setEnabled(not ocupado)
        self._atualizar_botoes()

    def _vendo_a_versao_atual(self) -> bool:
        return self.orquestrador is not None and self.versao_vista == self.orquestrador.versao_atual

    def _atualizar_botoes(self) -> None:
        pode_aprovar = not self.ocupado and self.orquestrador is not None and self._vendo_a_versao_atual() and self.orquestrador.pode_aprovar()
        liberado = not self.ocupado and self._arquivo_liberado()
        self.botao_aprovar.setEnabled(pode_aprovar)
        self.botao_aprovar.setText("Preparar de novo" if liberado else "Aprovar e preparar")
        self.botao_exportar.setEnabled(liberado)
        # Só a próxima ação fica em destaque.
        for botao, destaque in ((self.botao_aprovar, not liberado), (self.botao_exportar, liberado)):
            botao.setObjectName("primario" if destaque else "")
            botao.style().unpolish(botao)
            botao.style().polish(botao)

    def _arquivo_liberado(self) -> bool:
        return self._resumo_liberado() is not None

    def _resumo_liberado(self) -> dict[str, Any] | None:
        if self.pasta_impressao is None or self.projeto is None:
            return None
        ultimo = self.projeto.ultimo_fatiamento(self.versao_vista) if self.versao_vista else None
        if ultimo is None or ultimo[0] != self.pasta_impressao:
            return None
        return ultimo[1] if ultimo[1]["validacao"]["aprovado"] else None

    def _recarregar_projetos(self) -> None:
        self.painel_versoes.atualizar_projetos(Projeto.listar(self.config.pasta_projetos), self.projeto)

    def _atualizar_orca(self) -> None:
        orca = localizar_orca(self.config.caminho_orca)
        self.rotulo_orca.setText(f"OrcaSlicer: {orca.parent.name}" if orca else "OrcaSlicer: não encontrado")
        self.rotulo_orca.setToolTip(str(orca) if orca else "Indique o caminho em Configurações")

    def _verificar_endpoint(self) -> None:
        self.rotulo_ia.setText("IA: verificando…")
        ia = self.config.ia
        self.rotulo_ia.setToolTip(f"Chat: {ia.provedor}, {ia.modelo}\nImagem: {ia.provedor_imagem}, {ia.modelo_imagem}")
        fabrica = self._fabrica_cliente

        def executar() -> None:
            try:
                fabrica(ia).verificar()
                self._estado_ia.emit(True, f"IA: conectada, {ia.modelo}")
            except ErroIA as erro:
                self._estado_ia.emit(False, f"IA: {erro}")
            except Exception:
                log.exception("Falha ao verificar o endpoint")
                self._estado_ia.emit(False, "IA: não consegui verificar o endpoint")

        threading.Thread(target=executar, daemon=True).start()

    def _mostrar_estado_ia(self, ok: bool, texto: str) -> None:
        cor = tema.OK if ok else tema.ERRO
        self.rotulo_ia.setText(f'<span style="color:{cor}">●</span> {texto}')

    # ---------- Projetos e versões ----------

    def _nova_peca(self) -> None:
        self.projeto = None
        self.trabalhador.orquestrador = None
        self.versao_vista = None
        self.pasta_impressao = None
        self.rotulo_projeto.setText("Nova peça")
        self.painel_chat.limpar()
        self.painel_checks.limpar()
        self.painel_3d.limpar()
        self.painel_3d.ver_mesa()
        self.painel_versoes.atualizar_versoes(None, None)
        self.painel_versoes.lista_projetos.clearSelection()
        self.rotulo_resumo.setText("Gere uma peça para preparar a impressão.")
        self.painel_chat.campo.setFocus()
        self._atualizar_botoes()

    def abrir_projeto(self, projeto: Projeto) -> None:
        if self.projeto is not None and projeto.pasta == self.projeto.pasta:
            return
        self.projeto = projeto
        agente = self.trabalhador.abrir(projeto)
        self.pasta_impressao = None
        self.rotulo_projeto.setText(projeto.titulo)
        self.painel_chat.limpar()
        for registro in projeto.ler_conversa():
            imagem = projeto.pasta / registro["imagem"] if registro.get("imagem") else None
            self.painel_chat.adicionar("usuario" if registro["papel"] == "usuario" else "agente", registro["texto"], imagem)
        self.painel_versoes.atualizar_versoes(projeto, agente.versao_atual)
        if agente.especificacao:
            self._ajustar_por_especificacao(agente.especificacao.model_dump())
        self._ver_versao(agente.versao_atual)

    def _ver_versao(self, versao: str | None) -> None:
        self.versao_vista = versao
        self.pasta_impressao = None
        if self.projeto is None or versao is None:
            self.painel_3d.limpar()
            self.painel_checks.limpar()
            self.rotulo_resumo.setText("Gere uma peça para preparar a impressão.")
            self._atualizar_botoes()
            return
        self.painel_3d.mostrar(self.projeto.stls(versao))
        self.painel_checks.mostrar_verificacao(self.projeto.verificacao(versao))
        ultimo = self.projeto.ultimo_fatiamento(versao)
        if ultimo:
            self.pasta_impressao = ultimo[0]
            self.rotulo_resumo.setText(self._texto_resumo(ultimo[1]))
            self.painel_checks.mostrar_gcode(ultimo[1]["validacao"])
        else:
            self.rotulo_resumo.setText(self._texto_sem_fatiamento())
        self._atualizar_botoes()

    def _texto_sem_fatiamento(self) -> str:
        if self.orquestrador is not None and self._vendo_a_versao_atual() and self.orquestrador.pode_aprovar():
            return "Peça verificada. Revise e clique em Aprovar e preparar."
        return "Esta versão ainda não pode ser preparada."

    def _restaurar_versao(self, versao: str) -> None:
        if self.orquestrador is None:
            return
        self.orquestrador.restaurar_versao(versao)
        self.painel_versoes.atualizar_versoes(self.projeto, versao)
        self.painel_chat.adicionar_evento(f"Voltou para a versão {versao}.")
        self._ver_versao(versao)

    # ---------- Agente ----------

    def _garantir_projeto(self, titulo: str) -> Projeto:
        if self.projeto is None:
            self.projeto = Projeto.criar(self.config.pasta_projetos, titulo)
            self.trabalhador.abrir(self.projeto)
            self.rotulo_projeto.setText(self.projeto.titulo)
            self._recarregar_projetos()
        return self.projeto

    def _enviar(self, texto: str) -> None:
        if self.ocupado:
            return
        self._garantir_projeto(texto[:60])
        self.painel_chat.adicionar("usuario", texto)
        self.orquestrador.preparar_operacao()
        self._definir_ocupado(True)
        self._pedir_processar.emit(texto)

    def _escolher_imagem(self) -> None:
        caminho, _ = QFileDialog.getOpenFileName(self, "Imagem de referência", str(Path.home() / "Pictures"), "Imagens (*.png *.jpg *.jpeg *.webp)")
        if caminho:
            self._abrir_conceitos(Path(caminho))

    def _abrir_conceitos(self, origem: bytes | Path) -> None:
        if self.ocupado:
            return
        try:
            referencia = preparar_referencia(origem)
        except ReferenciaInvalida as erro:
            QMessageBox.warning(self, "Imagem de referência", str(erro))
            return
        dialogo = DialogoConceitos(referencia, self._fabrica_cliente(self.config.ia), lambda: self._garantir_projeto("Peça a partir de imagem"), self)
        aceito = dialogo.exec()
        conceito, quer_configuracoes = dialogo.conceito, dialogo.quer_configuracoes
        dialogo.deleteLater()
        if aceito and conceito:
            self._seguir_com_conceito(conceito)
        elif quer_configuracoes:
            self._abrir_configuracoes()

    def _seguir_com_conceito(self, conceito: dict[str, Any]) -> None:
        self.painel_chat.adicionar("usuario", f"Escolhi o conceito: {conceito['opcao']['titulo']}.", self.projeto.pasta / conceito["imagem"])
        self.rotulo_projeto.setText(self.projeto.titulo)
        self._recarregar_projetos()
        self.orquestrador.preparar_operacao()
        self._definir_ocupado(True)
        self._pedir_conceito.emit(conceito)

    def _cancelar(self) -> None:
        if self.orquestrador is not None:
            self.painel_chat.indicar_cancelando()
            self.orquestrador.cancelar()

    def _ao_responder(self, texto: str) -> None:
        self.painel_chat.adicionar("agente", texto)

    def _ao_errar(self, texto: str) -> None:
        self.painel_chat.adicionar("erro", texto)

    def _ao_concluir(self) -> None:
        self._definir_ocupado(False)
        if self.projeto is not None:
            self.rotulo_projeto.setText(self.projeto.titulo)
            self._recarregar_projetos()
            self.painel_versoes.atualizar_versoes(self.projeto, self.orquestrador.versao_atual if self.orquestrador else None)
        if self.versao_vista and not self.pasta_impressao:
            self.rotulo_resumo.setText(self._texto_sem_fatiamento())
        self._atualizar_botoes()

    def _ajustar_por_especificacao(self, especificacao: dict[str, Any]) -> None:
        self.combo_material.setCurrentText(especificacao["material"])
        paredes, preenchimento = AJUSTES_POR_CARGA.get(especificacao["carga"], (3, 15))
        self.spin_paredes.setValue(paredes)
        self.spin_preenchimento.setValue(preenchimento)

    def _ao_especificar(self, especificacao: dict[str, Any]) -> None:
        self._ajustar_por_especificacao(especificacao)
        if self.projeto is not None:
            self.rotulo_projeto.setText(self.projeto.titulo)

    def _ao_criar_versao(self, versao: str) -> None:
        self.painel_chat.adicionar_evento(f"Versão {versao} gerada.")
        self.painel_versoes.atualizar_versoes(self.projeto, versao)
        self.versao_vista = versao
        self.pasta_impressao = None
        self.painel_3d.mostrar(self.projeto.stls(versao))
        self.painel_checks.limpar()
        self.rotulo_resumo.setText("Verificando a peça…")
        self._atualizar_botoes()

    def _ao_verificar(self, versao: str, resultado: dict[str, Any]) -> None:
        if versao == self.versao_vista:
            self.painel_checks.mostrar_verificacao(resultado)
        self.painel_versoes.atualizar_versoes(self.projeto, versao)
        self._atualizar_botoes()

    # ---------- Impressão ----------

    def _parametros(self) -> ParametrosFatiamento:
        return ParametrosFatiamento(
            material=self.combo_material.currentText(),
            qualidade=self.combo_qualidade.currentData(),
            paredes=self.spin_paredes.value(),
            preenchimento_pct=self.spin_preenchimento.value(),
            suporte=self.caixa_suporte.isChecked(),
            brim=self.caixa_brim.isChecked(),
        )

    def _ao_mudar_opcoes(self) -> None:
        # O G-code pronto vale para as opções com que foi gerado: mudou, precisa preparar de novo.
        if self.pasta_impressao is not None and not self.ocupado:
            self.pasta_impressao = None
            self.rotulo_resumo.setText("As opções mudaram. Clique em Aprovar e preparar para gerar o G-code de novo.")
            self._atualizar_botoes()

    def _confirmar_material(self) -> bool:
        """O G-code é feito para um material: preparar em outro exige uma decisão explícita."""
        especificacao = self.orquestrador.especificacao
        escolhido = self.combo_material.currentText()
        if especificacao is None or especificacao.material == escolhido:
            return True
        resposta = QMessageBox.question(
            self,
            "Material diferente",
            f"A peça foi especificada em {especificacao.material}, mas o seletor está em {escolhido}.\n\n"
            f"Preparar em {escolhido}? Se escolher Não, o seletor volta para {especificacao.material}.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if resposta == QMessageBox.StandardButton.Yes:
            return True
        self.combo_material.setCurrentText(especificacao.material)
        return False

    def _aprovar_e_fatiar(self) -> None:
        if self.ocupado or self.orquestrador is None or not self._confirmar_material():
            return
        self.orquestrador.preparar_operacao()
        self._definir_ocupado(True)
        self.painel_chat.definir_etapa("Fatiando")
        self._pedir_fatiar.emit(self._parametros())

    def _texto_resumo(self, resumo: dict[str, Any]) -> str:
        v = resumo["validacao"]
        if not v["aprovado"]:
            return "G-code reprovado na validação. Veja os motivos na verificação."
        partes = [
            resumo["parametros"]["material"],
            v.get("tempo", "tempo não informado"),
            f"{tema.numero(v['filamento_g'], 1)} g" if "filamento_g" in v else "",
            f"{v['camadas']} camadas",
            f"bico {v['bico_c']:.0f} °C, mesa {v['mesa_c']:.0f} °C",
        ]
        return "Pronto para exportar: " + ", ".join(p for p in partes if p) + "."

    def _ao_fatiar(self, resumo: dict[str, Any]) -> None:
        parametros = resumo["parametros"]
        self.pasta_impressao = self.projeto.pasta_impressao(self.orquestrador.versao_atual, parametros["material"], parametros["qualidade"])
        self.rotulo_resumo.setText(self._texto_resumo(resumo))
        self.painel_checks.mostrar_verificacao(self.orquestrador.resultado_verificacao)
        self.painel_checks.mostrar_gcode(resumo["validacao"])
        self._atualizar_botoes()

    def _exportar(self) -> None:
        resumo = self._resumo_liberado()
        if resumo is None:
            return
        v = resumo["validacao"]
        p = resumo["parametros"]
        confirmacao = QMessageBox.question(
            self,
            "Exportar G-code",
            f"Material: {p['material']}\nBico: {v['bico_c']:.0f} °C, mesa: {v['mesa_c']:.0f} °C\n"
            f"Tempo: {v.get('tempo', 'não informado')}\nFilamento: {tema.numero(v.get('filamento_g', 0), 1)} g\n\n"
            "Confirma que esse é o filamento carregado na impressora?",
        )
        if confirmacao != QMessageBox.StandardButton.Yes:
            return
        unidades = unidades_removiveis()
        inicial = unidades[0] if unidades else Path.home() / "Documents"
        especificacao = self.orquestrador.especificacao if self.orquestrador else None
        nome = nome_sugerido(especificacao.nome if especificacao else "peca", p["material"])
        destino, _ = QFileDialog.getSaveFileName(self, "Salvar no cartão SD", str(inicial / nome), "G-code (*.gcode)")
        if not destino:
            return
        try:
            exportar_gcode(self.pasta_impressao, Path(destino))
        except (ExportacaoBloqueada, OSError) as erro:
            QMessageBox.warning(self, "Não foi possível exportar", str(erro))
            return
        QMessageBox.information(
            self,
            "G-code exportado",
            f"Arquivo salvo em:\n{destino}\n\nAntes de imprimir:\n"
            f"• A impressora não tem sensor de fim de filamento: confira se há {tema.numero(v.get('filamento_g', 0), 0)} g no carretel.\n"
            "• Limpe a mesa e faça o nivelamento, se não fez recentemente.\n"
            "• Acompanhe a primeira camada.",
        )

    # ---------- Geral ----------

    def _abrir_configuracoes(self) -> None:
        dialogo = DialogoConfig(self.config, self, self._fabrica_cliente)
        if dialogo.exec():
            self.config = configuracao.carregar()
            proteger_chaves(*self.config.chaves)
            self.trabalhador.config = self.config
            if self.projeto is not None:
                self.trabalhador.abrir(self.projeto)
            self._recarregar_projetos()
            self._atualizar_orca()
            self._verificar_endpoint()

    def closeEvent(self, evento) -> None:  # noqa: ANN001, N802 - nome definido pelo Qt.
        if self.orquestrador is not None:
            self.orquestrador.cancelar()
        self._linha.quit()
        self._linha.wait(5000)
        self.painel_3d.fechar()
        super().closeEvent(evento)
