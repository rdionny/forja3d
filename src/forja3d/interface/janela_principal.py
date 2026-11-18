"""Janela principal do Forja3D."""
from __future__ import annotations

import logging
import re
import shutil
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import QEvent, QFile, QObject, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QIcon, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMenu,
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
from forja3d.fatiamento.cores import (
    COMO_TROCAR,
    LIMITE_DE_TROCAS_SEM_AVISO,
    ErroDeCor,
    estimar_trocas,
    nome_da_cor,
    roteiro_da_preparacao,
    roteiro_das_trocas,
)
from forja3d.fatiamento.exportacao import (
    ExportacaoBloqueada,
    destinos_dos_arquivos,
    exportar_arquivos,
    exportar_gcode,
    gcode_aprovado,
    nome_sugerido,
    unidades_removiveis,
)
from forja3d.fatiamento.orca import ParametrosFatiamento, localizar_orca
from forja3d.fatiamento.simulacao import Percurso
from forja3d.fatiamento.tempo import AVISO_DA_ESTIMATIVA, segundos_do_tempo, texto_da_previsao
from forja3d.fatiamento.usb import ErroUsb, achar_impressora
from forja3d.ia.cliente import ClienteIA, ErroIA
from forja3d.importacao.leitura import FORMATOS_ACEITOS, FORMATOS_RECUSADOS
from forja3d.importacao.preparo import ModeloLido
from forja3d.interface import tema
from forja3d.interface.dialogo_conceitos import DialogoConceitos
from forja3d.interface.dialogo_config import DialogoConfig
from forja3d.interface.dialogo_importar import DialogoImportar
from forja3d.interface.dialogo_usb import DialogoUsb, aviso_de_energia
from forja3d.interface.painel_3d import Painel3D
from forja3d.interface.painel_chat import PainelChat
from forja3d.interface.painel_checks import PainelChecks
from forja3d.interface.painel_versoes import PainelVersoes
from forja3d.interface.trabalhador import Trabalhador
from forja3d.projetos.armazenamento import Projeto
from forja3d.projetos.compartilhar import EXTENSAO, com_extensao, nome_do_arquivo
from forja3d.registro import proteger_chaves
from forja3d.versao import texto_da_versao

log = logging.getLogger(__name__)

ICONE_CONFIGURACOES = Path(__file__).parent / "icones" / "engrenagem.svg"
QUALIDADES = (("Padrão, 0,20 mm", "padrao"), ("Ótima, 0,16 mm", "otima"), ("Fina, 0,12 mm", "fina"), ("Rascunho, 0,24 mm", "rascunho"))
AJUSTES_POR_CARGA = {"nenhuma": (2, 15), "leve": (3, 15), "media": (4, 25), "alta": (5, 40)}
FILTRO_DE_PROJETO = f"Projeto do Forja3D (*{EXTENSAO})"
# Os recusados também aparecem: sem eles, a pessoa não veria o arquivo que baixou nem saberia o que fazer com ele.
FILTRO_DE_MODELO = "Modelos 3D (" + " ".join(f"*{f}" for f in (*FORMATOS_ACEITOS, *FORMATOS_RECUSADOS)) + ")"
PEDIDO_DE_PARTE = re.compile(r"^Sobre a parte (\S+): (.*)$", re.DOTALL)
ESPERA_DO_DIALOGO_MS = 1000


def mandar_para_a_lixeira(pasta: Path) -> bool:
    resultado = QFile.moveToTrash(str(pasta))
    return resultado[0] if isinstance(resultado, tuple) else bool(resultado)


class SemRodaSemFoco(QObject):
    """Passar a roda do mouse por cima não pode trocar uma opção de impressão sem ninguém notar."""

    def eventFilter(self, alvo: QObject, evento: QEvent) -> bool:
        if evento.type() == QEvent.Type.Wheel and not alvo.hasFocus():
            evento.ignore()
            return True
        return False


class JanelaPrincipal(QMainWindow):
    _pedir_processar = Signal(str)
    _pedir_parte = Signal(str, str)
    _pedir_conceito = Signal(object)
    _pedir_fatiar = Signal(object)
    _pedir_verificacao = Signal()
    _pedir_salvar_arquivo = Signal(object, object)
    _pedir_importar_arquivo = Signal(object, object)
    _pedir_ler_modelo = Signal(object)
    _pedir_adicionar_modelo = Signal(object, object)
    _pedir_simulacao = Signal(object, object, object)
    _pedir_regioes = Signal(str, object)
    _pedir_separar = Signal(str, object, object, bool)
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
        self._arquivos_recebidos: list[Path] = []
        self._dialogo_usb: DialogoUsb | None = None
        # Um agente de fora, pelo MCP, mudou a peça aberta: ela é relida quando a janela estiver livre.
        self._peca_mudou_por_fora = False

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
        self.painel_chat.endereco_transcricao = self.config.transcricao
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
        # A data diz se a janela aberta já tem a última atualização: a versão só muda de tempos em tempos.
        self.rotulo_versao = QLabel(texto_da_versao(), objectName="suave")
        self.statusBar().addPermanentWidget(self.rotulo_versao)
        self.statusBar().setSizeGripEnabled(False)

    def _criar_topo(self) -> QWidget:
        topo = QWidget(objectName="painel")
        linha = QHBoxLayout(topo)
        linha.setContentsMargins(14, 8, 10, 8)
        linha.addWidget(QLabel("Forja3D", objectName="marca"))
        linha.addSpacing(6)
        linha.addWidget(self._criar_botao_arquivo())
        linha.addSpacing(6)
        self.rotulo_projeto = QLabel("Nova peça", objectName="suave")
        self.rotulo_projeto.setTextFormat(Qt.TextFormat.PlainText)
        linha.addWidget(self.rotulo_projeto)
        linha.addStretch(1)
        self.botao_desfazer = self._criar_botao_da_mesa("desfazer", "Desfazer a última alteração na mesa (Ctrl+Z)", "Desfazer", self.painel_3d.desfazer)
        self.botao_refazer = self._criar_botao_da_mesa("refazer", "Refazer o que foi desfeito na mesa (Ctrl+Y)", "Refazer", self.painel_3d.refazer)
        linha.addWidget(self.botao_desfazer)
        linha.addWidget(self.botao_refazer)
        linha.addSpacing(10)
        linha.addWidget(QLabel("Creality Ender-3 V3 SE, bico de 0,4 mm", objectName="suave"))
        linha.addSpacing(10)
        self.botao_config = QPushButton(objectName="icone", toolTip="Configurações: modelos e provedores de IA", accessibleName="Configurações")
        self.botao_config.setIcon(QIcon(str(ICONE_CONFIGURACOES)))
        self.botao_config.setIconSize(QSize(20, 20))
        self.botao_config.setFixedSize(32, 32)
        self.botao_config.clicked.connect(self._abrir_configuracoes)
        linha.addWidget(self.botao_config)
        return topo

    def _criar_botao_arquivo(self) -> QPushButton:
        self.botao_arquivo = QPushButton("Arquivo", objectName="plano", toolTip="Salva o projeto em um arquivo, para guardar ou passar para outra pessoa")
        menu = QMenu(self)
        self.acao_salvar = menu.addAction("Salvar", self._salvar)
        self.acao_salvar.setShortcut(QKeySequence.StandardKey.Save)
        self.acao_salvar_como = menu.addAction("Salvar como…", self._salvar_como)
        self.acao_salvar_como.setShortcut(QKeySequence("Ctrl+Shift+S"))
        menu.addSeparator()
        self.acao_abrir = menu.addAction("Abrir arquivo…", self._abrir_arquivo)
        self.acao_abrir.setShortcut(QKeySequence.StandardKey.Open)
        self.acao_importar = menu.addAction("Importar modelo 3D…", self._importar_modelo)
        self.acao_importar.setShortcut(QKeySequence("Ctrl+I"))
        self.botao_arquivo.setMenu(menu)
        # Um atalho só vale em uma ação da janela: o menu fica escondido.
        self.addActions([self.acao_salvar, self.acao_salvar_como, self.acao_abrir, self.acao_importar])
        return self.botao_arquivo

    def _criar_botao_da_mesa(self, icone: str, dica: str, nome: str, acao: Callable[[], None]) -> QPushButton:
        botao = QPushButton(objectName="icone", toolTip=dica, accessibleName=nome, enabled=False)
        botao.setIcon(tema.icone(icone))
        botao.setIconSize(QSize(20, 20))
        botao.setFixedSize(32, 32)
        # O foco fica na mesa, para os atalhos continuarem valendo depois do clique.
        botao.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        botao.clicked.connect(acao)
        return botao

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

        self.rotulo_tempo = QLabel("", objectName="tempo", wordWrap=True, toolTip=AVISO_DA_ESTIMATIVA)
        self.rotulo_tempo.hide()
        coluna.addWidget(self.rotulo_tempo)
        # O horário em que a impressão fica pronta muda conforme o relógio anda.
        self._relogio_tempo = QTimer(self, interval=30000)
        self._relogio_tempo.timeout.connect(self._atualizar_tempo)
        self._relogio_tempo.start()

        acoes = QHBoxLayout()
        acoes.setSpacing(10)
        self.botao_aprovar = QPushButton("Aprovar e preparar", objectName="primario", toolTip="Aprova esta versão e gera o G-code")
        self.botao_aprovar.clicked.connect(self._aprovar_e_fatiar)
        self.rotulo_resumo = QLabel("Gere uma peça para preparar a impressão.", objectName="suave", wordWrap=True)
        self.botao_exportar = QPushButton("Exportar G-code", toolTip="Copia o arquivo validado para o cartão SD")
        self.botao_exportar.clicked.connect(self._exportar)
        self.botao_simular = QPushButton("Simular", toolTip="Mostra a impressão camada por camada, a partir do G-code preparado")
        self.botao_simular.clicked.connect(self._simular)
        self.botao_usb = QPushButton("Imprimir pela USB", toolTip="Manda o G-code validado direto para a impressora, pelo cabo USB")
        self.botao_usb.clicked.connect(self._imprimir_pela_usb)
        acoes.addWidget(self.botao_aprovar)
        acoes.addWidget(self.rotulo_resumo, 1)
        acoes.addWidget(self.botao_simular)
        acoes.addWidget(self.botao_usb)
        acoes.addWidget(self.botao_exportar)
        coluna.addLayout(acoes)
        return barra

    def _conectar(self) -> None:
        self.painel_chat.enviado.connect(self._enviar)
        self.painel_chat.cancelar.connect(self._cancelar)
        self.painel_chat.imagem_pedida.connect(self._escolher_imagem)
        self.painel_chat.imagem_recebida.connect(self._abrir_conceitos)
        self.painel_chat.refazer_pedido.connect(self._refazer_pedido)
        self.painel_3d.mesa_mudou.connect(self._ao_mudar_mesa)
        self.painel_3d.referencia_pedida.connect(self.painel_chat.definir_referencia)
        self.painel_3d.desfazer_mudou.connect(self.botao_desfazer.setEnabled)
        self.painel_3d.desfazer_mudou.connect(lambda _desfazer, refazer: self.botao_refazer.setEnabled(refazer))
        self.painel_versoes.nova_peca.connect(self._nova_peca)
        self.painel_versoes.projeto_escolhido.connect(self.abrir_projeto)
        self.painel_versoes.versao_escolhida.connect(self._ver_versao)
        self.painel_versoes.restaurar_pedido.connect(self._restaurar_versao)
        self.painel_versoes.renomear_pedido.connect(self._renomear_projeto)
        self.painel_versoes.fixar_pedido.connect(self._fixar_projeto)
        self.painel_versoes.apagar_pedido.connect(self._apagar_projeto)
        self.painel_versoes.salvar_como_pedido.connect(self._salvar_como)
        self.painel_versoes.abrir_arquivo_pedido.connect(self._abrir_arquivo)
        self.painel_3d.importar_pedido.connect(self._importar_modelo)
        self.painel_3d.modelo_solto.connect(self._importar_modelo)
        self.painel_3d.separacao_pedida.connect(self._ler_regioes)
        self.painel_3d.separar_pedido.connect(self._separar_cores)

        self._pedir_processar.connect(self.trabalhador.processar)
        self._pedir_parte.connect(self.trabalhador.processar_parte)
        self._pedir_conceito.connect(self.trabalhador.processar_conceito)
        self._pedir_fatiar.connect(self.trabalhador.aprovar_e_fatiar)
        self._pedir_verificacao.connect(self.trabalhador.verificar)
        self._pedir_salvar_arquivo.connect(self.trabalhador.salvar_arquivo)
        self._pedir_importar_arquivo.connect(self.trabalhador.importar_arquivo)
        self._pedir_ler_modelo.connect(self.trabalhador.ler_modelo)
        self._pedir_adicionar_modelo.connect(self.trabalhador.adicionar_modelo)
        self._pedir_simulacao.connect(self.trabalhador.ler_simulacao)
        self.trabalhador.simulacao_lida.connect(self._ao_ler_simulacao)
        self._pedir_regioes.connect(self.trabalhador.ler_regioes)
        self._pedir_separar.connect(self.trabalhador.separar_cores)
        self.trabalhador.regioes_lidas.connect(self.painel_3d.iniciar_separacao)
        self.trabalhador.cores_separadas.connect(self._ao_separar_cores)
        self.trabalhador.modelo_lido.connect(self._ao_ler_modelo)
        self.trabalhador.modelo_adicionado.connect(self._ao_adicionar_modelo)
        self.trabalhador.arquivo_salvo.connect(self._ao_salvar_arquivo)
        self.trabalhador.projeto_importado.connect(self._ao_importar_arquivo)
        self.trabalhador.arquivo_falhou.connect(self._ao_falhar_arquivo)
        self.trabalhador.resposta.connect(self._ao_responder)
        self.trabalhador.erro.connect(self._ao_errar)
        self.trabalhador.concluido.connect(self._ao_concluir)
        self.trabalhador.fatiado.connect(self._ao_fatiar)

        ouvinte = self.trabalhador.ouvinte
        ouvinte.sinal_etapa.connect(self.painel_chat.definir_etapa)
        ouvinte.sinal_especificacao.connect(self._ao_especificar)
        ouvinte.sinal_versao.connect(self._ao_criar_versao)
        ouvinte.sinal_verificacao.connect(self._ao_verificar)
        ouvinte.sinal_cores.connect(self._ao_agente_colorir)
        self._estado_ia.connect(self._mostrar_estado_ia)

        for campo in (self.combo_material, self.combo_qualidade):
            campo.currentIndexChanged.connect(self._ao_mudar_opcoes)
        for campo in (self.spin_paredes, self.spin_preenchimento):
            campo.valueChanged.connect(self._ao_mudar_opcoes)
        for campo in (self.caixa_suporte, self.caixa_brim):
            campo.toggled.connect(self._ao_mudar_opcoes)
        self.caixa_brim.toggled.connect(lambda ligado: self.painel_3d.definir_brim(self._brim_mm() if ligado else 0.0))

    # ---------- Estado ----------

    @property
    def orquestrador(self):
        return self.trabalhador.orquestrador

    def _definir_ocupado(self, ocupado: bool) -> None:
        # O que vai acontecer pode mudar a peça: a simulação do G-code anterior não vale mais.
        if ocupado:
            self.painel_3d.encerrar_simulacao()
        self.ocupado = ocupado
        self.painel_chat.definir_ocupado(ocupado)
        self.painel_versoes.botao_nova.setEnabled(not ocupado)
        self.painel_versoes.botao_abrir.setEnabled(not ocupado)
        self.painel_versoes.lista_projetos.setEnabled(not ocupado)
        self.painel_versoes.lista_versoes.setEnabled(not ocupado)
        self.painel_versoes.botao_restaurar.setEnabled(not ocupado)
        self.botao_config.setEnabled(not ocupado)
        # O agente pode criar uma versão nova: a mesa espera ele terminar.
        self.painel_3d.definir_editavel(not ocupado and self._vendo_a_versao_atual())
        self._atualizar_botoes()

    def _vendo_a_versao_atual(self) -> bool:
        return self.orquestrador is not None and self.versao_vista == self.orquestrador.versao_atual

    def _atualizar_botoes(self) -> None:
        mesa = self.painel_3d.mesa
        tem_itens = mesa is not None and bool(mesa.itens)
        pode_aprovar = tem_itens and not self.ocupado and self.orquestrador is not None and self._vendo_a_versao_atual() and self.orquestrador.pode_aprovar()
        liberado = not self.ocupado and self._arquivo_liberado()
        self.botao_aprovar.setEnabled(pode_aprovar)
        self.botao_aprovar.setText("Preparar de novo" if liberado else "Aprovar e preparar")
        self.botao_exportar.setEnabled(liberado)
        self.botao_simular.setEnabled(liberado)
        self.botao_usb.setEnabled(liberado and not self._usb_ativa())
        self.acao_salvar.setEnabled(not self.ocupado and self.projeto is not None)
        self.acao_salvar_como.setEnabled(not self.ocupado and self.projeto is not None)
        self.acao_abrir.setEnabled(not self.ocupado)
        self.acao_importar.setEnabled(not self.ocupado)
        self.painel_3d.botao_importar.setEnabled(not self.ocupado)
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
        if ultimo is None or ultimo[0] != self.pasta_impressao or not self._fatiamento_vale(ultimo[1]):
            return None
        return ultimo[1] if ultimo[1]["validacao"]["aprovado"] else None

    def _fatiamento_vale(self, resumo: dict[str, Any]) -> bool:
        """O G-code só vale para a mesa com que foi gerado."""
        assinatura = resumo.get("layout")
        if assinatura is None:
            return not self.projeto.tem_layout(self.versao_vista)
        return self.painel_3d.mesa is not None and assinatura == self.painel_3d.mesa.assinatura()

    def _atualizar_tempo(self) -> None:
        resumo = self._resumo_liberado() if self.projeto is not None and self.versao_vista else None
        texto = texto_da_previsao(resumo) if resumo else ""
        self.rotulo_tempo.setText(texto)
        self.rotulo_tempo.setVisible(bool(texto))

    def _recarregar_projetos(self) -> None:
        self.painel_versoes.atualizar_projetos(Projeto.listar(self.config.pasta_projetos), self.projeto)

    def _atualizar_orca(self) -> None:
        orca = localizar_orca(self.config.caminho_orca)
        self.rotulo_orca.setText(f"OrcaSlicer: {orca.parent.name}" if orca else "OrcaSlicer: não encontrado")
        self.rotulo_orca.setToolTip(str(orca) if orca else "Indique o caminho em Configurações")

    def _verificar_endpoint(self) -> None:
        if not self.config.ia_configurada:
            self.rotulo_ia.setToolTip("Informe o endpoint, a chave e o modelo em Configurações")
            self._mostrar_estado_ia(False, "IA: não configurada")
            return
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
        self.painel_chat.limpar_referencia()
        self.painel_chat.campo.setFocus()
        self._atualizar_tempo()
        self._atualizar_botoes()

    def peca_mudou_por_fora(self, pasta: Path) -> None:
        """Sem reler, a próxima gravação da janela, como mover um item, desfaria o trabalho do agente de fora."""
        self._recarregar_projetos()
        if self.projeto is not None and self.projeto.pasta.resolve() == Path(pasta).resolve():
            self._peca_mudou_por_fora = True
            self._reler_se_mudou()

    def _reler_se_mudou(self) -> None:
        if not self._peca_mudou_por_fora or self.projeto is None or self.ocupado:
            return
        if QApplication.activeModalWidget() is not None:
            QTimer.singleShot(ESPERA_DO_DIALOGO_MS, self._reler_se_mudou)
            return
        pasta = self.projeto.pasta
        self.projeto = None
        self.abrir_projeto(Projeto(pasta))
        self._recarregar_projetos()
        self.painel_chat.adicionar_evento("A peça mudou por fora, pelo MCP, e foi relida.")

    def abrir_projeto(self, projeto: Projeto) -> None:
        if self.projeto is not None and projeto.pasta == self.projeto.pasta:
            return
        self._peca_mudou_por_fora = False
        self.projeto = projeto
        agente = self.trabalhador.abrir(projeto)
        self.pasta_impressao = None
        self.rotulo_projeto.setText(projeto.titulo)
        self.painel_chat.limpar()
        self.painel_chat.limpar_referencia()
        for registro in projeto.ler_conversa():
            imagem = projeto.pasta / registro["imagem"] if registro.get("imagem") else None
            papel = registro["papel"] if registro["papel"] in ("usuario", "erro") else "agente"
            self.painel_chat.adicionar(papel, registro["texto"], imagem)
        self.painel_versoes.atualizar_versoes(projeto, agente.versao_atual)
        if agente.especificacao:
            self._ajustar_por_especificacao(agente.especificacao.model_dump())
        self._ver_versao(agente.versao_atual)
        self._verificar_se_faltar()

    def _verificar_se_faltar(self) -> None:
        agente = self.orquestrador
        if agente is None or self.ocupado or not agente.versao_atual or agente.resultado_verificacao is not None:
            return
        if not agente.especificacao and not agente.so_importadas():
            return
        agente.preparar_operacao()
        self._definir_ocupado(True)
        self.painel_chat.definir_etapa("Verificando a peça")
        self._pedir_verificacao.emit()

    # ---------- Arquivo .forja3d ----------

    def _salvar(self) -> None:
        """No arquivo já escolhido. Sem arquivo ainda, é o mesmo que Salvar como."""
        if self.projeto is None or self.ocupado:
            return
        destino = self.projeto.estado.get("arquivo")
        if destino:
            self._gravar_arquivo(self.projeto, Path(destino))
        else:
            self._salvar_como()

    def _salvar_como(self, projeto: Projeto | None = None) -> None:
        alvo = self._o_mesmo_projeto(projeto) if projeto is not None else self.projeto
        if alvo is None or self.ocupado:
            return
        inicial = alvo.estado.get("arquivo") or str(Path.home() / "Documents" / nome_do_arquivo(alvo.titulo))
        destino, _ = QFileDialog.getSaveFileName(self, "Salvar como", inicial, FILTRO_DE_PROJETO)
        if destino:
            self._gravar_arquivo(alvo, com_extensao(Path(destino)))

    def _gravar_arquivo(self, projeto: Projeto, destino: Path) -> None:
        self._iniciar_tarefa_de_arquivo("Salvando o arquivo")
        self._pedir_salvar_arquivo.emit(projeto, destino)

    def _abrir_arquivo(self) -> None:
        if self.ocupado:
            return
        caminho, _ = QFileDialog.getOpenFileName(self, "Abrir arquivo", str(Path.home() / "Documents"), FILTRO_DE_PROJETO)
        if caminho:
            self._importar_arquivo(Path(caminho))

    def _importar_arquivo(self, arquivo: Path) -> None:
        self._iniciar_tarefa_de_arquivo("Conferindo o arquivo")
        self._pedir_importar_arquivo.emit(arquivo, self.config.pasta_projetos)

    def receber_pedido(self, arquivo: Path | None) -> None:
        """Alguém abriu o app ou deu dois cliques em um arquivo."""
        self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
        self.raise_()
        self.activateWindow()
        if arquivo is None:
            return
        self._arquivos_recebidos.append(arquivo)
        if self.ocupado:
            self.statusBar().showMessage(f"{arquivo.name} abre quando a tarefa atual terminar.", 8000)
        self._abrir_proximo_recebido()

    def _abrir_proximo_recebido(self) -> None:
        if not self._arquivos_recebidos or self.ocupado:
            return
        # Com uma janela de diálogo aberta, trocar a peça por trás dela deixaria o diálogo falando de outra peça.
        if QApplication.activeModalWidget() is not None:
            QTimer.singleShot(ESPERA_DO_DIALOGO_MS, self._abrir_proximo_recebido)
            return
        self._importar_arquivo(self._arquivos_recebidos.pop(0))

    def _iniciar_tarefa_de_arquivo(self, etapa: str) -> None:
        self._definir_ocupado(True)
        self.painel_chat.definir_etapa(etapa)
        # Salvar e abrir terminam em segundos e não têm como parar no meio.
        self.painel_chat.botao_cancelar.setEnabled(False)

    def _importar_modelo(self, arquivos: Path | list[Path] | None = None) -> None:
        """Um modelo baixado vai para a mesa da peça aberta. Sem peça aberta, vira uma peça nova.

        Vários arquivos juntos são as cores de um modelo só, como black.stl e white.stl.
        """
        if self.ocupado:
            return
        if arquivos is None:
            caminhos, _ = QFileDialog.getOpenFileNames(self, "Importar modelo 3D", str(Path.home() / "Downloads"), FILTRO_DE_MODELO)
            if not caminhos:
                return
            arquivos = [Path(c) for c in caminhos]
        self._iniciar_tarefa_de_arquivo("Lendo o modelo 3D")
        self._pedir_ler_modelo.emit(arquivos)

    def _ao_ler_modelo(self, modelo: ModeloLido) -> None:
        versao = self.orquestrador.versao_atual if self.orquestrador else None
        ocupados = set(self.projeto.stls_por_parte(versao)) if self.projeto and versao else set()
        dialogo = DialogoImportar(modelo, self.perfil, ocupados, self)
        aceito = dialogo.exec()
        opcoes = dialogo.opcoes()
        dialogo.deleteLater()
        if not aceito:
            shutil.rmtree(modelo.pasta, ignore_errors=True)
            return
        self._garantir_projeto((modelo.nome_comum or modelo.arquivo.stem)[:60])
        self._iniciar_tarefa_de_arquivo("Colocando o modelo na mesa")
        self._pedir_adicionar_modelo.emit(modelo, opcoes)

    def _ao_adicionar_modelo(self, parte: dict[str, Any]) -> None:
        x, y, z = parte["tamanho"]
        self.painel_chat.adicionar_evento(f"Modelo importado: {parte['nome']}, {tema.numero(x)} × {tema.numero(y)} × {tema.numero(z)} mm.")

    def _ler_regioes(self, parte: str) -> None:
        """Achar as regiões de um modelo grande leva alguns segundos: fica fora da interface."""
        if self.ocupado or self.projeto is None or not self._vendo_a_versao_atual():
            return
        self._iniciar_tarefa_de_arquivo("Procurando as regiões da peça")
        self._pedir_regioes.emit(parte, self.projeto.stls_por_parte(self.versao_vista)[parte])

    def _separar_cores(self, parte: str, regioes: Any, pintura: dict[int, str], pino: bool) -> None:
        if self.ocupado or self.orquestrador is None:
            return
        self._iniciar_tarefa_de_arquivo("Separando as cores")
        self._pedir_separar.emit(parte, regioes, pintura, pino)

    def _ao_separar_cores(self, resultado: dict[str, Any]) -> None:
        pecas = resultado["pecas"]
        cores = ", ".join(dict.fromkeys(nome_da_cor(p["cor"]) for p in pecas))
        texto = f"Cores separadas em {resultado['parte']}: {len(pecas)} {'peça nova' if len(pecas) == 1 else 'peças novas'} ({cores}), cada cor em um arquivo."
        com_suporte = [p["nome"] for p in pecas if p["suporte"]]
        if com_suporte:
            texto += f" Marque Suporte ao preparar: {', '.join(com_suporte)} imprime com o pino para baixo."
        self.painel_chat.adicionar_evento(texto)

    def _ao_salvar_arquivo(self, destino: Path) -> None:
        self.statusBar().showMessage(f"Salvo em {destino}", 8000)

    def _ao_importar_arquivo(self, projeto: Projeto) -> None:
        self.abrir_projeto(projeto)
        self._recarregar_projetos()

    def _ao_falhar_arquivo(self, motivo: str) -> None:
        QMessageBox.warning(self, "Arquivo", motivo)

    def _o_mesmo_projeto(self, projeto: Projeto) -> Projeto:
        """A peça aberta tem seu objeto próprio: gravar por outra cópia seria desfeito no próximo salvamento."""
        return self.projeto if self.projeto is not None and self.projeto.pasta == projeto.pasta else projeto

    def _renomear_projeto(self, projeto: Projeto) -> None:
        alvo = self._o_mesmo_projeto(projeto)
        titulo, confirmou = QInputDialog.getText(self, "Renomear", "Nome da conversa:", text=alvo.titulo)
        if not confirmou or not titulo.strip():
            return
        alvo.renomear(titulo.strip())
        if alvo is self.projeto:
            self.rotulo_projeto.setText(alvo.titulo)
        self._recarregar_projetos()

    def _fixar_projeto(self, projeto: Projeto) -> None:
        alvo = self._o_mesmo_projeto(projeto)
        alvo.fixar(not alvo.fixado)
        self._recarregar_projetos()

    def _apagar_projeto(self, projeto: Projeto) -> None:
        if self.ocupado:
            return
        resposta = QMessageBox.question(
            self,
            "Apagar projeto",
            f"Apagar “{projeto.titulo}”?\n\nA conversa, as versões e os G-codes vão para a Lixeira do Windows.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if resposta != QMessageBox.StandardButton.Yes:
            return
        if self.projeto is not None and self.projeto.pasta == projeto.pasta:
            self._nova_peca()
        if not mandar_para_a_lixeira(projeto.pasta):
            QMessageBox.warning(self, "Apagar projeto", f"Não consegui mover para a Lixeira:\n{projeto.pasta}\n\nO projeto continua na lista.")
        self._recarregar_projetos()

    def _ver_versao(self, versao: str | None) -> None:
        self.versao_vista = versao
        self.pasta_impressao = None
        if self.projeto is None or versao is None:
            self.painel_3d.limpar()
            self.painel_checks.limpar()
            self.rotulo_resumo.setText("Gere uma peça para preparar a impressão.")
            self._atualizar_tempo()
            self._atualizar_botoes()
            return
        self._mostrar_mesa(versao)
        self.painel_checks.mostrar_verificacao(self.projeto.verificacao(versao))
        ultimo = self.projeto.ultimo_fatiamento(versao)
        if ultimo and self._fatiamento_vale(ultimo[1]):
            self.pasta_impressao = ultimo[0]
            self.rotulo_resumo.setText(self._texto_resumo(ultimo[1]))
            self.painel_checks.mostrar_gcode(ultimo[1]["validacao"])
            self.painel_checks.mostrar_roteiro(roteiro_da_preparacao(ultimo[1]))
        else:
            self.rotulo_resumo.setText(self._texto_sem_fatiamento())
        self._atualizar_tempo()
        self._atualizar_botoes()

    def _mostrar_mesa(self, versao: str) -> None:
        self.painel_3d.mostrar_mesa(
            self.projeto.layout(versao, self.perfil),
            self.projeto.stls_por_parte(versao),
            not self.ocupado and self._vendo_a_versao_atual(),
            self.projeto.medidas_por_parte(versao),
        )

    def _ao_mudar_mesa(self) -> None:
        if self.projeto is None or self.versao_vista is None or self.painel_3d.mesa is None:
            return
        self.projeto.salvar_layout(self.versao_vista, self.painel_3d.mesa)
        if self.pasta_impressao is not None:
            self.pasta_impressao = None
            self.rotulo_resumo.setText("A mesa mudou. Clique em Aprovar e preparar para gerar o G-code de novo.")
            self._atualizar_tempo()
        self._atualizar_botoes()

    def _brim_mm(self) -> float:
        return float(self.perfil["processo_padrao"]["brim_mm"])
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
        self._verificar_se_faltar()

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
        parte = self.painel_chat.referencia
        self.painel_chat.limpar_referencia()
        self._enviar_pedido(texto, parte)

    def _enviar_pedido(self, texto: str, parte: str | None) -> None:
        self.painel_chat.adicionar("usuario", f"Sobre a parte {parte}: {texto}" if parte else texto)
        self.orquestrador.preparar_operacao()
        self._definir_ocupado(True)
        if parte:
            self._pedir_parte.emit(texto, parte)
        else:
            self._pedir_processar.emit(texto)

    def _refazer_pedido(self, numero: int, texto_novo: str | None) -> None:
        """Editar ou enviar de novo: a conversa volta a esse pedido, e o que veio depois sai dela."""
        if self.ocupado or self.orquestrador is None:
            return
        pedidos = self.projeto.pedidos()
        if numero >= len(pedidos):
            self.painel_chat.adicionar_evento("Esta mensagem não está salva no projeto, então não dá para refazê-la.")
            return
        conceito = None
        if texto_novo is None and pedidos[numero].get("imagem"):
            conceito = self.projeto.conceito_escolhido()
            if not conceito or conceito["imagem"] != pedidos[numero]["imagem"]:
                QMessageBox.information(self, "Enviar de novo", "Este não é mais o conceito escolhido. Para refazer, envie a imagem de referência de novo.")
                return
        seguintes = len(pedidos) - numero - 1
        if seguintes and not self._confirmar_volta(seguintes):
            return
        antes = self.orquestrador.versao_atual
        pedido = self.orquestrador.voltar_ao_pedido(numero)
        depois = self.orquestrador.versao_atual
        self.painel_chat.remover_desde(numero)
        self.painel_versoes.atualizar_versoes(self.projeto, depois)
        if depois != antes:
            self.painel_chat.adicionar_evento(f"A peça voltou para a versão {depois}." if depois else "A peça voltou para antes da primeira versão.")
        if depois != self.versao_vista:
            self._ver_versao(depois)
        if numero == 0 and texto_novo is not None and self.orquestrador.especificacao is None:
            # O título ainda é o começo do primeiro pedido: acompanha a edição.
            self.projeto.titulo_automatico(texto_novo[:60])
            self.projeto.salvar_estado()
            self.rotulo_projeto.setText(self.projeto.titulo)
            self._recarregar_projetos()
        if conceito:
            self._seguir_com_conceito(conceito)
            return
        texto = texto_novo if texto_novo is not None else pedido["texto"]
        de_parte = PEDIDO_DE_PARTE.match(texto)
        if de_parte:
            self._enviar_pedido(de_parte.group(2), de_parte.group(1))
        else:
            self._enviar_pedido(texto, None)

    def _confirmar_volta(self, seguintes: int) -> bool:
        plural = "o pedido seguinte sai" if seguintes == 1 else f"os {seguintes} pedidos seguintes saem"
        resposta = QMessageBox.question(
            self,
            "Voltar a conversa",
            f"A conversa volta a esta mensagem: {plural} da conversa, com as respostas. As versões da peça continuam salvas.\n\nContinuar?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return resposta == QMessageBox.StandardButton.Yes

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
        self._abrir_proximo_recebido()
        self._reler_se_mudou()

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
        self._mostrar_mesa(versao)
        self.painel_checks.limpar()
        self.rotulo_resumo.setText("Verificando a peça…")
        self._atualizar_tempo()
        self._atualizar_botoes()

    def _ao_verificar(self, versao: str, resultado: dict[str, Any]) -> None:
        if versao == self.versao_vista:
            self.painel_checks.mostrar_verificacao(resultado)
        self.painel_versoes.atualizar_versoes(self.projeto, versao)
        self._atualizar_botoes()

    def _ao_agente_colorir(self, versao: str, arquivo: int) -> None:
        self.painel_chat.adicionar_evento("Cores aplicadas na mesa.")
        # O agente grava a mesa no disco enquanto a tela está travada, então não há edição do usuário para perder.
        if versao == self.versao_vista:
            self._ver_versao(versao)
            self.painel_3d.mostrar_arquivo(arquivo)

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
            self.painel_3d.encerrar_simulacao()
            self.rotulo_resumo.setText("As opções mudaram. Clique em Aprovar e preparar para gerar o G-code de novo.")
            self._atualizar_tempo()
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

    def _confirmar_mesa(self) -> bool:
        """Itens encostados viram uma peça só na impressão: seguir assim exige uma decisão explícita."""
        mesa = self.painel_3d.mesa
        if mesa is None or not mesa.conflitos(self._brim_mm() if self.caixa_brim.isChecked() else 0.0):
            return True
        resposta = QMessageBox.question(
            self,
            "Itens muito perto",
            "Há itens perto demais ou sobrepostos na mesa, marcados em vermelho.\n\nPreparar mesmo assim?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return resposta == QMessageBox.StandardButton.Yes

    def _confirmar_posicao(self) -> bool:
        """Deitada sem apoio, a peça pode se soltar da mesa no meio da impressão: seguir sem suporte exige uma decisão explícita."""
        if self.caixa_suporte.isChecked():
            return True
        # Balanço e base pequena só deixam a peça mais feia, e ficam no aviso da mesa, como na posição original.
        avisos = [a["mensagem"] for a in self.painel_3d.avisos_de_posicao() if a["codigo"] == "SEM_APOIO"] if self.painel_3d.mesa else []
        if not avisos:
            return True
        resposta = QMessageBox.question(
            self,
            "Peça deitada sem suporte",
            "\n\n".join(avisos) + "\n\nPreparar sem suporte mesmo assim?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return resposta == QMessageBox.StandardButton.Yes

    def _confirmar_trocas(self) -> bool:
        """Uma peça alta com várias cores pede uma pausa por camada: a pessoa precisa saber antes de deixar a impressora rodando."""
        mesa = self.painel_3d.mesa
        if mesa is None or self.versao_vista is None:
            return True
        medidas = self.projeto.medidas_por_parte(self.versao_vista)
        camada = float(self.perfil["qualidades"][self.combo_qualidade.currentData()]["camada_mm"])
        try:
            total = sum(estimar_trocas(mesa, numero, medidas, camada) for numero in mesa.arquivos())
        except ErroDeCor:
            # A mensagem completa aparece ao fatiar.
            return True
        if total <= LIMITE_DE_TROCAS_SEM_AVISO:
            return True
        caixa = QMessageBox(self)
        caixa.setIcon(QMessageBox.Icon.Warning)
        caixa.setWindowTitle("Muitas trocas de filamento")
        caixa.setText(
            f"Esta mesa pede cerca de {total} trocas manuais de filamento, com alguém ao lado da impressora em cada uma.\n\n"
            "Peças separadas saem melhor em um arquivo por cor, sem nenhuma pausa no meio."
        )
        separar = caixa.addButton("Um arquivo por cor", QMessageBox.ButtonRole.AcceptRole)
        assim = caixa.addButton("Preparar assim mesmo", QMessageBox.ButtonRole.DestructiveRole)
        caixa.addButton("Cancelar", QMessageBox.ButtonRole.RejectRole)
        caixa.setDefaultButton(separar)
        caixa.exec()
        if caixa.clickedButton() is separar:
            self.painel_3d.separar_por_cor()
            return True
        return caixa.clickedButton() is assim

    def _aprovar_e_fatiar(self) -> None:
        if self.ocupado or self.orquestrador is None or not self._confirmar_material() or not self._confirmar_mesa() or not self._confirmar_posicao() or not self._confirmar_trocas():
            return
        self.orquestrador.preparar_operacao()
        self._definir_ocupado(True)
        self.painel_chat.definir_etapa("Fatiando")
        self._pedir_fatiar.emit(self._parametros())

    def _texto_resumo(self, resumo: dict[str, Any]) -> str:
        v = resumo["validacao"]
        if not v["aprovado"]:
            return "G-code reprovado na validação. Veja os motivos na verificação."
        arquivos = len(resumo.get("arquivos") or [])
        partes = [
            f"{arquivos} arquivos" if arquivos > 1 else "",
            resumo["parametros"]["material"],
            v.get("tempo", "tempo não informado"),
            f"{tema.numero(v['filamento_g'], 1)} g" if "filamento_g" in v else "",
            f"{v['camadas']} camadas",
            f"bico {v['bico_c']:.0f} °C, mesa {v['mesa_c']:.0f} °C",
            f"{v['trocas_de_filamento']} {'troca' if v['trocas_de_filamento'] == 1 else 'trocas'} de filamento" if v.get("trocas_de_filamento") else "",
        ]
        return "Pronto para exportar: " + ", ".join(p for p in partes if p) + "."

    def _ao_fatiar(self, resumo: dict[str, Any]) -> None:
        parametros = resumo["parametros"]
        self.pasta_impressao = self.projeto.pasta_impressao(self.orquestrador.versao_atual, parametros["material"], parametros["qualidade"])
        self.rotulo_resumo.setText(self._texto_resumo(resumo))
        self.painel_checks.mostrar_verificacao(self.orquestrador.resultado_verificacao)
        self.painel_checks.mostrar_gcode(resumo["validacao"])
        self.painel_checks.mostrar_roteiro(roteiro_da_preparacao(resumo))
        self._atualizar_tempo()
        self._atualizar_botoes()

    def _simular(self) -> None:
        """Lê o G-code do arquivo que está na tela. A leitura fica fora da interface: um G-code pode ter milhões de linhas."""
        resumo = self._resumo_liberado()
        if resumo is None or self.ocupado:
            return
        arquivo = self._arquivo_na_tela(resumo)
        cor_inicial = (arquivo.get("cores") or {}).get("cor_inicial")
        self._iniciar_tarefa_de_arquivo("Lendo o G-code")
        self._pedir_simulacao.emit(self.pasta_impressao / arquivo["gcode"], cor_inicial, segundos_do_tempo(arquivo["validacao"].get("tempo")))

    def _arquivo_na_tela(self, resumo: dict[str, Any]) -> dict[str, Any]:
        """O arquivo da mesa que está na tela. Um resumo antigo, de um arquivo só, não tem a lista."""
        arquivo = next((a for a in resumo.get("arquivos") or [] if a["numero"] == self.painel_3d.arquivo_ativo), None)
        return arquivo or {"numero": 1, "gcode": resumo["gcode"], "validacao": resumo["validacao"]}

    def _ao_ler_simulacao(self, percurso: Percurso) -> None:
        self.painel_3d.iniciar_simulacao(percurso)

    @staticmethod
    def _texto_das_cores(resumo: dict[str, Any]) -> str:
        """Para a confirmação antes de exportar: a pessoa precisa carregar a primeira cor certa."""
        roteiro = roteiro_da_preparacao(resumo)
        if roteiro:
            return f"\n\n{roteiro}"
        cores = [a["cores"]["cor_inicial"] for a in resumo.get("arquivos") or [] if (a.get("cores") or {}).get("cor_inicial")]
        return f"\nCor: {', '.join(nome_da_cor(c) for c in cores)}" if cores else ""

    def _gravar_roteiro(self, resumo: dict[str, Any], destino: Path) -> str:
        """O roteiro vai junto com o G-code. Devolve o que acrescentar ao aviso de conclusão."""
        roteiro = roteiro_da_preparacao(resumo)
        if not roteiro:
            return ""
        try:
            destino.write_text(f"{roteiro}\n\n{COMO_TROCAR}\n", encoding="utf-8")
        except OSError:
            log.exception("Falha ao gravar o roteiro de trocas")
            return f"\n\nAs trocas de filamento:\n{roteiro}\n\n{COMO_TROCAR}"
        return f"\n\nO passo a passo das trocas ficou em {destino.name}.\n\n{COMO_TROCAR}"

    def _exportar(self) -> None:
        resumo = self._resumo_liberado()
        if resumo is None:
            return
        v = resumo["validacao"]
        p = resumo["parametros"]
        quantidade = len(resumo.get("arquivos") or [])
        confirmacao = QMessageBox.question(
            self,
            "Exportar G-code",
            (f"Arquivos: {quantidade}\n" if quantidade > 1 else "")
            + f"Material: {p['material']}\nBico: {v['bico_c']:.0f} °C, mesa: {v['mesa_c']:.0f} °C\n"
            f"Tempo: {v.get('tempo', 'não informado')}\nFilamento: {tema.numero(v.get('filamento_g', 0), 1)} g"
            f"{self._texto_das_cores(resumo)}\n\n"
            "Confirma que esse é o filamento carregado na impressora?",
        )
        if confirmacao != QMessageBox.StandardButton.Yes:
            return
        unidades = unidades_removiveis()
        inicial = unidades[0] if unidades else Path.home() / "Documents"
        especificacao = self.orquestrador.especificacao if self.orquestrador else None
        nome_peca = especificacao.nome if especificacao else self.projeto.titulo
        if quantidade > 1:
            destino = self._exportar_varios(inicial, nome_peca, p["material"])
            if destino is None:
                return
        else:
            nome = nome_sugerido(nome_peca, p["material"])
            destino, _ = QFileDialog.getSaveFileName(self, "Salvar no cartão SD", str(inicial / nome), "G-code (*.gcode)")
            if not destino:
                return
            try:
                exportar_gcode(self.pasta_impressao, Path(destino))
            except (ExportacaoBloqueada, OSError) as erro:
                QMessageBox.warning(self, "Não foi possível exportar", str(erro))
                return
        primeiro = Path(destino.splitlines()[0])
        roteiro = primeiro.with_suffix(".txt") if quantidade <= 1 else primeiro.with_name(f"{Path(nome_sugerido(nome_peca, p['material'])).stem}_trocas.txt")
        QMessageBox.information(
            self,
            "G-code exportado",
            f"{'Arquivos salvos em' if quantidade > 1 else 'Arquivo salvo em'}:\n{destino}\n\nAntes de imprimir:\n"
            f"• A impressora não tem sensor de fim de filamento: confira se há {tema.numero(v.get('filamento_g', 0), 0)} g no carretel.\n"
            "• Limpe a mesa e faça o nivelamento, se não fez recentemente.\n"
            f"• Acompanhe a primeira camada.{self._gravar_roteiro(resumo, roteiro)}",
        )

    def _exportar_varios(self, inicial: Path, nome_peca: str, material: str) -> str | None:
        """Vários arquivos vão para uma pasta. Devolve a pasta, ou nada se o usuário desistiu."""
        pasta = QFileDialog.getExistingDirectory(self, "Salvar no cartão SD", str(inicial))
        if not pasta:
            return None
        destinos = destinos_dos_arquivos(self.pasta_impressao, Path(pasta), nome_peca, material)
        existentes = [d.name for d in destinos.values() if d.exists()]
        if existentes:
            resposta = QMessageBox.question(self, "Arquivos já existem", "Estes arquivos já existem e serão substituídos:\n" + "\n".join(existentes))
            if resposta != QMessageBox.StandardButton.Yes:
                return None
        try:
            salvos = exportar_arquivos(self.pasta_impressao, Path(pasta), nome_peca, material)
        except (ExportacaoBloqueada, OSError) as erro:
            QMessageBox.warning(self, "Não foi possível exportar", str(erro))
            return None
        return "\n".join(str(s) for s in salvos)

    def _usb_ativa(self) -> bool:
        return self._dialogo_usb is not None and self._dialogo_usb.ativa

    def _imprimir_pela_usb(self) -> None:
        """Imprime o arquivo da tela, com a mesma regra do Exportar: só um G-code aprovado sai do app."""
        resumo = self._resumo_liberado()
        if resumo is None or self._usb_ativa():
            return
        arquivo = self._arquivo_na_tela(resumo)
        v = arquivo["validacao"]
        try:
            gcode = gcode_aprovado(self.pasta_impressao, arquivo["gcode"])
            dispositivo = achar_impressora()
        except (ExportacaoBloqueada, ErroUsb) as erro:
            QMessageBox.warning(self, "Não foi possível imprimir pela USB", str(erro))
            return
        if dispositivo is None:
            QMessageBox.warning(
                self,
                "Impressora não encontrada",
                "Não achei a impressora na USB. Ligue a impressora, encaixe o cabo nos dois lados e tente de novo. "
                "No Windows ela aparece como USB-SERIAL CH340. No Linux, como /dev/ttyUSB0.",
            )
            return
        quantidade = len(resumo.get("arquivos") or [])
        cores = arquivo.get("cores") or {}
        roteiro = roteiro_das_trocas(cores)
        if roteiro:
            texto_cores = f"\n\n{roteiro}\nEm cada troca, a impressora espera você clicar em Retomar no app."
        else:
            texto_cores = f"\nCor: {nome_da_cor(cores['cor_inicial'])}" if cores.get("cor_inicial") else ""
        confirmacao = QMessageBox.question(
            self,
            "Imprimir pela USB",
            (f"Arquivo {arquivo['numero']} de {quantidade}, o que está na tela\n" if quantidade > 1 else "")
            + f"Impressora: {dispositivo}\nMaterial: {resumo['parametros']['material']}\nBico: {v['bico_c']:.0f} °C, mesa: {v['mesa_c']:.0f} °C\n"
            f"Tempo: {v.get('tempo', 'não informado')}\nFilamento: {tema.numero(v.get('filamento_g', 0), 1)} g{texto_cores}\n\n"
            "• Ligar pela USB pode reiniciar a impressora. Se ela estiver imprimindo pelo cartão, essa impressão para.\n"
            f"• {aviso_de_energia()}\n"
            "• Confira o filamento carregado, limpe a mesa e acompanhe a primeira camada.\n\n"
            "Começar a impressão?",
        )
        if confirmacao != QMessageBox.StandardButton.Yes:
            return
        tempo = v.get("tempo_s") or segundos_do_tempo(v.get("tempo"))
        self._dialogo_usb = DialogoUsb(gcode, tempo, self.perfil["validacao_gcode"]["z_max"], dispositivo, self)
        self._dialogo_usb.terminou.connect(self._atualizar_botoes)
        self._dialogo_usb.iniciar()
        self._atualizar_botoes()

    # ---------- Geral ----------

    def _abrir_configuracoes(self) -> None:
        dialogo = DialogoConfig(self.config, self, self._fabrica_cliente)
        if dialogo.exec():
            self.config = configuracao.carregar()
            proteger_chaves(*self.config.chaves)
            self.painel_chat.endereco_transcricao = self.config.transcricao
            self.trabalhador.config = self.config
            if self.projeto is not None:
                self.trabalhador.abrir(self.projeto)
            self._recarregar_projetos()
            self._atualizar_orca()
            self._verificar_endpoint()

    def closeEvent(self, evento) -> None:
        if self._usb_ativa():
            resposta = QMessageBox.question(
                self,
                "Impressão pela USB em andamento",
                "Fechar o Forja3D cancela a impressão pela USB: o bico e a mesa desligam, e a peça fica pela metade. Fechar mesmo assim?",
            )
            if resposta != QMessageBox.StandardButton.Yes:
                evento.ignore()
                return
            self._dialogo_usb.encerrar()
        self.painel_chat.parar_leitura()
        # No Linux o arecord é outro processo: sem isto, continuaria gravando com o app fechado.
        self.painel_chat.cancelar_gravacao()
        if self.orquestrador is not None:
            self.orquestrador.cancelar()
        self._linha.quit()
        self._linha.wait(5000)
        self.painel_3d.fechar()
        super().closeEvent(evento)
