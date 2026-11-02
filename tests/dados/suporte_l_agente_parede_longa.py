# Gerado por gemini-3.8-flash-high com PROMPT_AGENTE_3D.md (teste de 02/11/2026).
# Parede no lado de 40 mm: leitura válida do pedido, diferente de suporte_l_agente_valido.py.
import cadquery as cq
import math

# === PARAMETROS (mm) ===
LARGURA_X = 40.0
PROFUNDIDADE_Y = 30.0
ESPESSURA_BASE = 4.0
ESPESSURA_PAREDE = 4.0
ALTURA_PAREDE = 30.0
ALTURA_TOTAL = ESPESSURA_BASE + ALTURA_PAREDE  # 34.0 mm
RAIO_FILETE = 3.0
CHANFRO_BASE = 0.4
DIAMETRO_FURO_M4 = 4.5  # Diametro modelado com folga para M4
DISTANCIA_FUROS_X = 20.0  # Centros em X = -10.0 e X = +10.0

# Posicoes calculadas
Y_JUNTA = PROFUNDIDADE_Y / 2.0 - ESPESSURA_PAREDE  # 11.0 mm
POS_Y_FUROS = (-PROFUNDIDADE_Y / 2.0 + Y_JUNTA) / 2.0  # -2.0 mm (centro util da base)

# === GEOMETRIA ===
# 1. Bloco envolvente total apoiado em Z = 0
bloco = cq.Workplane("XY").box(
    LARGURA_X, PROFUNDIDADE_Y, ALTURA_TOTAL, centered=(True, True, False)
)

# 2. Caixa de corte para formar o perfil em L
y_corte_min = -PROFUNDIDADE_Y / 2.0 - 1.0  # -16.0 mm (passa da face frontal)
y_corte_max = Y_JUNTA  # 11.0 mm (face interna da parede vertical)
comp_y_corte = y_corte_max - y_corte_min  # 27.0 mm
centro_y_corte = (y_corte_min + y_corte_max) / 2.0  # -2.5 mm
alt_corte = ALTURA_PAREDE + 2.0  # 32.0 mm (passa do topo)

corte_l = (
    cq.Workplane("XY")
    .box(LARGURA_X + 2.0, comp_y_corte, alt_corte, centered=(True, True, False))
    .translate((0.0, centro_y_corte, ESPESSURA_BASE))
)

corpo = bloco.cut(corte_l)

# 3. Filete na junta interna (aresta em Y = 11.0, Z = 4.0)
seletor_junta = cq.selectors.BoxSelector(
    (-1.0, Y_JUNTA - 0.5, ESPESSURA_BASE - 0.5),
    (1.0, Y_JUNTA + 0.5, ESPESSURA_BASE + 0.5),
)
corpo = corpo.edges(seletor_junta).fillet(RAIO_FILETE)

# 4. Chanfro de compensacao de pe de elefante nas arestas inferiores externas
seletor_base = cq.selectors.BoxSelector(
    (-LARGURA_X / 2.0 - 1.0, -PROFUNDIDADE_Y / 2.0 - 1.0, -0.1),
    (LARGURA_X / 2.0 + 1.0, PROFUNDIDADE_Y / 2.0 + 1.0, 0.1),
)
corpo = corpo.edges(seletor_base).chamfer(CHANFRO_BASE)

# 5. Dois furos passantes para parafuso M4 na base
furo_esq = (
    cq.Workplane("XY")
    .cylinder(ESPESSURA_BASE + 2.0, DIAMETRO_FURO_M4 / 2.0)
    .translate((-DISTANCIA_FUROS_X / 2.0, POS_Y_FUROS, ESPESSURA_BASE / 2.0))
)

furo_dir = (
    cq.Workplane("XY")
    .cylinder(ESPESSURA_BASE + 2.0, DIAMETRO_FURO_M4 / 2.0)
    .translate((DISTANCIA_FUROS_X / 2.0, POS_Y_FUROS, ESPESSURA_BASE / 2.0))
)

corpo = corpo.cut(furo_esq).cut(furo_dir)

# === RESULTADO ===
result = corpo
