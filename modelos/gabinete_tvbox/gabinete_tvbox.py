# Mini gabinete para placa de TV Box, com cooler de fonte ATX.
#
# As medidas da placa são PROVISÓRIAS: confira com um paquímetro e ajuste o bloco abaixo.
# Todo o resto do gabinete se recalcula a partir delas. Todas as medidas em mm.
import cadquery as cq

# === MEDIDAS PARA CONFERIR NA SUA PLACA (PROVISÓRIAS) ===
PLACA_X = 100.0  # Largura, ao longo do lado dos conectores
PLACA_Y = 100.0  # Profundidade
PLACA_ESPESSURA = 1.6
# Furos de fixação, medidos a partir do canto da placa do lado dos conectores
PLACA_FUROS = [(3.5, 3.5), (96.5, 3.5), (3.5, 96.5), (96.5, 96.5)]
PLACA_PILOTO = 2.7  # Parafuso autoatarraxante M3. Para M2,5 use 2.2
ALTURA_COMPONENTES = 22.0  # Do topo da placa até o componente mais alto, com dissipador
JANELA_LARGURA = 96.0  # Abertura para os conectores (HDMI, USB, energia, cartão...)
JANELA_ALTURA = 22.0

# === COOLER DE FONTE ATX ===
# Padrão de mercado: 120 x 120 x 25 mm, furos a 105 mm. Para 80 mm: 80.0, 25.0 e 71.5.
COOLER_LADO = 120.0
COOLER_ESPESSURA = 25.0
COOLER_ENTRE_FUROS = 105.0
COOLER_FURO = 4.5  # Folga para parafuso M4
POSICAO_COOLER = "topo"  # "topo" ou "traseira"

# === PARÂMETROS DO GABINETE ===
PISO = 4.0
TAMPA = 4.0
PAINEL = 3.0
ALTURA_ESPACADOR = 6.0
DIAMETRO_ESPACADOR = 8.0
FOLGA_SOBRE_COMPONENTES = 6.0
FOLGA_PLACA_FRENTE = 1.0
FOLGA_PLACA_FUNDO = 5.0
FOLGA_LATERAL_PLACA = 4.0

COLUNA = 10.0  # Lado da coluna de cada canto
ENCAIXE = 3.5  # Profundidade do canal na coluna
LINGUETA = 3.0  # Quanto o painel entra no canal
RECUO = 2.2  # Do lado de fora do gabinete até a face externa do painel
FOLGA = 0.2  # Folga por lado nos canais: encaixe deslizante
SULCO = 1.5  # Profundidade do canal no piso e na tampa
RAIO_CANTO = 3.0
CHANFRO_BASE = 0.4
PILOTO_TAMPA = 2.7  # Autoatarraxante M3 nas colunas
FURO_TAMPA = 3.4  # Folga M3 na tampa
FURO_COLUNA_XY = 3.25
FURO_CABO = 8.0

LARGURA_FENDA = 3.0
PASSO_FENDA = 7.0
MARGEM_FENDA = 12.0
LARGURA_ANEL = 2.0
PASSO_ANEL = 9.0
LARGURA_RAIO = 3.0

# === MEDIDAS CALCULADAS ===
COOLER_NO_TOPO = POSICAO_COOLER == "topo"
ALTURA_MIN_PLACA = ALTURA_ESPACADOR + PLACA_ESPESSURA + ALTURA_COMPONENTES + FOLGA_SOBRE_COMPONENTES
ALTURA_INTERNA = ALTURA_MIN_PLACA if COOLER_NO_TOPO else max(ALTURA_MIN_PLACA, COOLER_LADO + 8.0)

MINIMO_COOLER = COOLER_LADO + 14.0  # Sobra para a porca do cooler não bater na coluna
LARGURA = max(PLACA_X + 2.0 * (COLUNA + FOLGA_LATERAL_PLACA), MINIMO_COOLER)
FACE_INTERNA_PAINEL = RECUO + PAINEL
PROFUNDIDADE = FACE_INTERNA_PAINEL + FOLGA_PLACA_FRENTE + PLACA_Y + FOLGA_PLACA_FUNDO + FACE_INTERNA_PAINEL
if COOLER_NO_TOPO:
    PROFUNDIDADE = max(PROFUNDIDADE, MINIMO_COOLER)

ALTURA_BASE = PISO + ALTURA_INTERNA
ALTURA_PAINEL = ALTURA_INTERNA + 2.0 * SULCO - 2.0 * FOLGA
COMPRIMENTO_FRENTE = LARGURA - 2.0 * (COLUNA - LINGUETA)
COMPRIMENTO_LATERAL = PROFUNDIDADE - 2.0 * (COLUNA - LINGUETA)
Z_INICIO_PAINEL = PISO - SULCO + FOLGA
Z_TOPO_PAINEL = Z_INICIO_PAINEL + ALTURA_PAINEL
PLACA_ORIGEM_X = (LARGURA - PLACA_X) / 2.0
PLACA_ORIGEM_Y = FACE_INTERNA_PAINEL + FOLGA_PLACA_FRENTE
Z_PLACA = PISO + ALTURA_ESPACADOR


# === GEOMETRIA ===
def caixa(x0, x1, y0, y1, z0, z1):
    """Bloco por coordenadas absolutas: evita erros de sentido de plano e de centralização."""
    return cq.Workplane("XY").box(x1 - x0, y1 - y0, z1 - z0, centered=False).translate((x0, y0, z0))


def cilindro(x, y, z0, z1, diametro):
    return cq.Workplane("XY").circle(diametro / 2.0).extrude(z1 - z0).translate((x, y, z0))


def canais(z0, z1):
    """Os quatro canais onde os painéis encaixam, cortados de uma vez nas colunas e no piso."""
    interno = COLUNA - ENCAIXE
    a0, a1 = RECUO - FOLGA, FACE_INTERNA_PAINEL + FOLGA
    frente = caixa(interno, LARGURA - interno, a0, a1, z0, z1)
    fundo = caixa(interno, LARGURA - interno, PROFUNDIDADE - a1, PROFUNDIDADE - a0, z0, z1)
    esquerda = caixa(a0, a1, interno, PROFUNDIDADE - interno, z0, z1)
    direita = caixa(LARGURA - a1, LARGURA - a0, interno, PROFUNDIDADE - interno, z0, z1)
    return frente.union(fundo).union(esquerda).union(direita)


def furos_dos_cantos():
    a = FURO_COLUNA_XY
    return [(a, a), (LARGURA - a, a), (a, PROFUNDIDADE - a), (LARGURA - a, PROFUNDIDADE - a)]


def criar_base():
    corpo = caixa(0, LARGURA, 0, PROFUNDIDADE, 0, ALTURA_BASE).edges("|Z").fillet(RAIO_CANTO)
    corpo = corpo.faces("<Z").edges().chamfer(CHANFRO_BASE)
    # Dois cortes em cruz deixam só o piso e as quatro colunas.
    corpo = corpo.cut(caixa(COLUNA, LARGURA - COLUNA, -1, PROFUNDIDADE + 1, PISO, ALTURA_BASE + 1))
    corpo = corpo.cut(caixa(-1, LARGURA + 1, COLUNA, PROFUNDIDADE - COLUNA, PISO, ALTURA_BASE + 1))
    corpo = corpo.cut(canais(PISO - SULCO, ALTURA_BASE + 1))
    for x, y in furos_dos_cantos():
        corpo = corpo.cut(cilindro(x, y, ALTURA_BASE - 12.0, ALTURA_BASE + 1, PILOTO_TAMPA))
    for fx, fy in PLACA_FUROS:
        x, y = PLACA_ORIGEM_X + fx, PLACA_ORIGEM_Y + fy
        corpo = corpo.union(cilindro(x, y, PISO - 0.5, Z_PLACA, DIAMETRO_ESPACADOR))
        corpo = corpo.cut(cilindro(x, y, PISO - 1.0, Z_PLACA + 1, PLACA_PILOTO))
    return corpo


def recortar_cooler(corpo, cx, cy, espessura):
    """Abertura com grade de proteção e os quatro furos do cooler, em peça impressa deitada."""
    raio = COOLER_LADO / 2.0 - 4.0
    corpo = corpo.cut(cilindro(cx, cy, -1, espessura + 1, 2.0 * raio))
    r = raio - PASSO_ANEL + LARGURA_ANEL
    while r > PASSO_ANEL:
        anel = cilindro(cx, cy, 0, espessura, 2.0 * r).cut(cilindro(cx, cy, -1, espessura + 1, 2.0 * (r - LARGURA_ANEL)))
        corpo = corpo.union(anel)
        r -= PASSO_ANEL
    meio = LARGURA_RAIO / 2.0
    alcance = raio + 1.0
    corpo = corpo.union(caixa(cx - alcance, cx + alcance, cy - meio, cy + meio, 0, espessura))
    corpo = corpo.union(caixa(cx - meio, cx + meio, cy - alcance, cy + alcance, 0, espessura))
    corpo = corpo.union(cilindro(cx, cy, 0, espessura, 2.0 * r + 4.0))
    d = COOLER_ENTRE_FUROS / 2.0
    for sx, sy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
        corpo = corpo.cut(cilindro(cx + sx * d, cy + sy * d, -1, espessura + 1, COOLER_FURO))
    return corpo


def criar_tampa():
    # Impressa com a face externa na mesa: o canal fica para cima e não precisa de suporte.
    corpo = caixa(0, LARGURA, 0, PROFUNDIDADE, 0, TAMPA).edges("|Z").fillet(RAIO_CANTO)
    corpo = corpo.faces("<Z").edges().chamfer(CHANFRO_BASE)
    corpo = corpo.cut(canais(TAMPA - SULCO, TAMPA + 1))
    for x, y in furos_dos_cantos():
        corpo = corpo.cut(cilindro(x, y, -1, TAMPA + 1, FURO_TAMPA))
    if COOLER_NO_TOPO:
        corpo = recortar_cooler(corpo, LARGURA / 2.0, PROFUNDIDADE / 2.0, TAMPA)
    return corpo


def painel_liso(comprimento):
    corpo = caixa(0, comprimento, 0, ALTURA_PAINEL, 0, PAINEL)
    return corpo.faces("<Z").edges().chamfer(CHANFRO_BASE)


def recortar_fendas(corpo, x0, x1):
    y0, y1 = MARGEM_FENDA / 2.0, ALTURA_PAINEL - MARGEM_FENDA / 2.0
    x = x0
    while x + LARGURA_FENDA <= x1:
        corpo = corpo.cut(caixa(x, x + LARGURA_FENDA, y0, y1, -1, PAINEL + 1))
        x += PASSO_FENDA
    return corpo


def criar_lateral():
    return recortar_fendas(painel_liso(COMPRIMENTO_LATERAL), MARGEM_FENDA, COMPRIMENTO_LATERAL - MARGEM_FENDA)


def criar_frente():
    # Na montagem, a frente fica de cabeça para baixo: o lado liso da mesa aponta para fora.
    z0 = Z_PLACA - 1.0
    y0, y1 = Z_TOPO_PAINEL - (z0 + JANELA_ALTURA), Z_TOPO_PAINEL - z0
    x0 = (COMPRIMENTO_FRENTE - JANELA_LARGURA) / 2.0
    return painel_liso(COMPRIMENTO_FRENTE).cut(caixa(x0, x0 + JANELA_LARGURA, y0, y1, -1, PAINEL + 1))


def criar_traseira():
    corpo = painel_liso(COMPRIMENTO_FRENTE)
    if COOLER_NO_TOPO:
        corpo = recortar_fendas(corpo, 24.0, COMPRIMENTO_FRENTE - MARGEM_FENDA)
        y_cabo = ALTURA_PAINEL / 2.0
    else:
        corpo = recortar_cooler(corpo, COMPRIMENTO_FRENTE / 2.0, ALTURA_PAINEL / 2.0, PAINEL)
        y_cabo = ALTURA_PAINEL - 8.0
    return corpo.cut(cilindro(MARGEM_FENDA, y_cabo, -1, PAINEL + 1, FURO_CABO))


# === RESULTADO ===
result = {
    "base": criar_base(),
    "tampa": criar_tampa(),
    "frente": criar_frente(),
    "traseira": criar_traseira(),
    "lateral_esquerda": criar_lateral(),
    "lateral_direita": criar_lateral(),
}


def montar():
    """Posiciona as peças como no gabinete montado. Usado nos testes de encaixe e nas imagens."""
    x0 = COLUNA - LINGUETA
    z0, z1 = Z_INICIO_PAINEL, Z_TOPO_PAINEL
    p = result
    return {
        "base": p["base"],
        "tampa": p["tampa"].rotate((0, 0, 0), (1, 0, 0), 180).translate((0, PROFUNDIDADE, ALTURA_BASE + TAMPA)),
        "frente": p["frente"].rotate((0, 0, 0), (1, 0, 0), -90).translate((x0, RECUO, z1)),
        "traseira": p["traseira"].rotate((0, 0, 0), (1, 0, 0), 90).translate((x0, PROFUNDIDADE - RECUO, z0)),
        "lateral_esquerda": p["lateral_esquerda"].rotate((0, 0, 0), (1, 0, 0), 90).rotate((0, 0, 0), (0, 0, 1), 90).translate((RECUO, x0, z0)),
        "lateral_direita": p["lateral_direita"].rotate((0, 0, 0), (1, 0, 0), -90).rotate((0, 0, 0), (0, 0, 1), 90).translate((LARGURA - RECUO, x0, z1)),
    }


def itens_de_referencia():
    """A placa e o cooler, para conferir o encaixe. Não são impressos."""
    placa = caixa(PLACA_ORIGEM_X, PLACA_ORIGEM_X + PLACA_X, PLACA_ORIGEM_Y, PLACA_ORIGEM_Y + PLACA_Y, Z_PLACA, Z_PLACA + PLACA_ESPESSURA)
    if COOLER_NO_TOPO:
        c = COOLER_LADO / 2.0
        cx, cy = LARGURA / 2.0, PROFUNDIDADE / 2.0
        z = ALTURA_BASE + TAMPA
        cooler = caixa(cx - c, cx + c, cy - c, cy + c, z, z + COOLER_ESPESSURA)
    else:
        c = COOLER_LADO / 2.0
        cx = LARGURA / 2.0
        cz = Z_INICIO_PAINEL + ALTURA_PAINEL / 2.0
        y = PROFUNDIDADE - RECUO
        cooler = caixa(cx - c, cx + c, y, y + COOLER_ESPESSURA, cz - c, cz + c)
    return {"placa": placa, "cooler": cooler}
