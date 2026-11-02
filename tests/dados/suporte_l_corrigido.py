import cadquery as cq

# === PARAMETROS (mm) ===
COMPRIMENTO_BASE = 40.0
LARGURA = 30.0
ESPESSURA = 4.0
ALTURA_PAREDE = 30.0
RAIO_FILETE = 3.0
DIAMETRO_FURO = 4.0
FURO_X = 22.0
FURO_Y = (-7.5, 7.5)

# === GEOMETRIA ===
perfil = [
    (0, 0),
    (COMPRIMENTO_BASE, 0),
    (COMPRIMENTO_BASE, ESPESSURA),
    (ESPESSURA, ESPESSURA),
    (ESPESSURA, ESPESSURA + ALTURA_PAREDE),
    (0, ESPESSURA + ALTURA_PAREDE),
]

# A normal de "XZ" aponta para -Y: com both=True o sólido fica centralizado em Y.
corpo = cq.Workplane("XZ").polyline(perfil).close().extrude(LARGURA / 2, both=True)

aresta_interna = cq.selectors.BoxSelector(
    (ESPESSURA - 0.1, -LARGURA, ESPESSURA - 0.1),
    (ESPESSURA + 0.1, LARGURA, ESPESSURA + 0.1),
)
corpo = corpo.edges(aresta_interna).fillet(RAIO_FILETE)

for y in FURO_Y:
    furo = (
        cq.Workplane("XY")
        .workplane(offset=-1.0)
        .center(FURO_X, y)
        .circle(DIAMETRO_FURO / 2)
        .extrude(ESPESSURA + 2.0)
    )
    corpo = corpo.cut(furo)

# === RESULTADO ===
result = corpo
