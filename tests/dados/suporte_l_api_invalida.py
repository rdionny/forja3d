# Gerado por gemini-3.8-flash-high com PROMPT_AGENTE_3D.md (rodada 2 do teste de 02/11/2026).
# Mantido como exemplo de chamadas que o loop de reparo precisa conseguir corrigir.
import cadquery as cq

# === PARAMETROS (mm) ===
LARGURA_X = 30.0
COMPRIMENTO_BASE_Y = 40.0
ESPESSURA_BASE_Z = 4.0
ALTURA_VERTICAL = 30.0
ESPESSURA_PAREDE_Y = 4.0
ALTURA_TOTAL_Z = ALTURA_VERTICAL + ESPESSURA_BASE_Z
DIAM_FURO_M4 = 4.5
RAIO_FILETE = 3.0
CHANFRO_BASE = 0.4

# === GEOMETRIA ===
perfil_pontos = [
    (0.0, 0.0),
    (COMPRIMENTO_BASE_Y, 0.0),
    (COMPRIMENTO_BASE_Y, ALTURA_TOTAL_Z),
    (COMPRIMENTO_BASE_Y - ESPESSURA_PAREDE_Y, ALTURA_TOTAL_Z),
    (COMPRIMENTO_BASE_Y - ESPESSURA_PAREDE_Y, ESPESSURA_BASE_Z),
    (0.0, ESPESSURA_BASE_Z),
]
perfil = (
    cq.Workplane("YZ")
    .polyline(perfil_pontos)
    .close()
    .extrude(LARGURA_X / 2.0, both=True)
)
corpo = perfil.translate((0, -COMPRIMENTO_BASE_Y / 2.0, 0))

aresta_interna = corpo.edges(
    cq.selectors.NearestToPointSelector((0.0, (COMPRIMENTO_BASE_Y / 2.0) - ESPESSURA_PAREDE_Y, ESPESSURA_BASE_Z))
)
corpo = corpo.fillet(RAIO_FILETE, aresta_interna)

furo_y1 = -12.0
furo_y2 = 4.0
corpo = (
    corpo.faces("<Z")
    .workplane()
    .pushPoints([(0.0, furo_y1), (0.0, furo_y2)])
    .hole(DIAM_FURO_M4, depth=ESPESSURA_BASE_Z + 1.0)
)

arestas_base = corpo.edges(cq.selectors.BoxSelector(
    -LARGURA_X / 2.0 - 0.1, -COMPRIMENTO_BASE_Y / 2.0 - 0.1, -0.1,
    LARGURA_X / 2.0 + 0.1, COMPRIMENTO_BASE_Y / 2.0 + 0.1, 0.1
))
corpo = corpo.chamfer(CHANFRO_BASE, arestas_base)

# === RESULTADO ===
result = corpo
