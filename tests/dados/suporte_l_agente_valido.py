# Gerado por gemini-3.8-flash-high com PROMPT_AGENTE_3D.md (rodada 1 do teste de 02/11/2026).
import cadquery as cq

# === PARAMETROS (mm) ===
LARGURA = 30.0  # Dimensão em X
COMPRIMENTO_BASE = 40.0  # Dimensão total em Y
ESPESSURA_BASE = 4.0  # Espessura da base horizontal em Z
ALTURA_PAREDE = 30.0  # Altura da parede vertical acima da base
ESPESSURA_PAREDE = 4.0  # Espessura da parede vertical em Y
ALTURA_TOTAL = ESPESSURA_BASE + ALTURA_PAREDE  # 34.0 mm

RAIO_FILETE = 3.0  # Filete de reforço na junta interna
CHANFRO_BASE = 0.4  # Chanfro anti-pé-de-elefante na base

DIAMETRO_FURO_M4 = 4.5  # Diâmetro modelado para parafuso M4 (passante com folga)
FURO_1_Y = -8.0  # Posição Y do furo 1
FURO_2_Y = 5.0  # Posição Y do furo 2

# === GEOMETRIA ===
# Perfil em L desenhado no plano YZ
y_min = -COMPRIMENTO_BASE / 2.0  # -20.0
y_max = COMPRIMENTO_BASE / 2.0  # 20.0
y_parede_interna = y_max - ESPESSURA_PAREDE  # 16.0

pontos_perfil = [
    (y_min, 0.0),
    (y_max, 0.0),
    (y_max, ALTURA_TOTAL),
    (y_parede_interna, ALTURA_TOTAL),
    (y_parede_interna, ESPESSURA_BASE),
    (y_min, ESPESSURA_BASE),
]

# Extrusão ao longo de X, centralizada
corpo = (
    cq.Workplane("YZ")
    .polyline(pontos_perfil)
    .close()
    .extrude(LARGURA)
    .translate((-LARGURA / 2.0, 0, 0))
)

# Chanfro anti-pé-de-elefante nas arestas inferiores da base (em Z = 0)
corpo = corpo.faces("<Z").edges().chamfer(CHANFRO_BASE)

# Filete de reforço na junta interna (aresta em Y = y_parede_interna, Z = ESPESSURA_BASE)
seletor_junta = cq.selectors.BoxSelector(
    (-LARGURA / 2.0 - 1.0, y_parede_interna - 0.2, ESPESSURA_BASE - 0.2),
    (LARGURA / 2.0 + 1.0, y_parede_interna + 0.2, ESPESSURA_BASE + 0.2),
)
corpo = corpo.edges(seletor_junta).fillet(RAIO_FILETE)

# Furos passantes M4 na base
furo1 = (
    cq.Workplane("XY")
    .workplane(offset=-1.0)
    .circle(DIAMETRO_FURO_M4 / 2.0)
    .extrude(ESPESSURA_BASE + 2.0)
    .translate((0.0, FURO_1_Y, 0.0))
)

furo2 = (
    cq.Workplane("XY")
    .workplane(offset=-1.0)
    .circle(DIAMETRO_FURO_M4 / 2.0)
    .extrude(ESPESSURA_BASE + 2.0)
    .translate((0.0, FURO_2_Y, 0.0))
)

corpo = corpo.cut(furo1).cut(furo2)

# === RESULTADO ===
result = corpo
