import cadquery as cq

# === PARAMETROS (mm) ===
LADO = 20.0
TAMANHO_LETRA = 9.0
PROFUNDIDADE_LETRA = 0.6

# === GEOMETRIA ===
corpo = cq.Workplane("XY").box(LADO, LADO, LADO, centered=(True, True, False))

# Letras gravadas para identificar cada eixo ao medir com o paquímetro.
corpo = (
    corpo.faces(">X").workplane(centerOption="CenterOfBoundBox")
    .text("X", TAMANHO_LETRA, -PROFUNDIDADE_LETRA, combine="cut")
)
corpo = (
    corpo.faces(">Y").workplane(centerOption="CenterOfBoundBox")
    .text("Y", TAMANHO_LETRA, -PROFUNDIDADE_LETRA, combine="cut")
)
corpo = (
    corpo.faces(">Z").workplane(centerOption="CenterOfBoundBox")
    .text("Z", TAMANHO_LETRA, -PROFUNDIDADE_LETRA, combine="cut")
)

# === RESULTADO ===
result = corpo
