import pytest

from forja3d.cad.seguranca_ast import CodigoRecusado, validar_codigo

MALICIOSOS = {
    "importar os": "import os\nos.system('calc')",
    "importar subprocess": "import subprocess",
    "from os": "from os import system",
    "import pontilhado": "import cadquery.occ_impl.exporters",
    "from cadquery exporters": "from cadquery import exporters",
    "from *": "from cadquery import *",
    "abrir arquivo": "f = open('x.txt', 'w')",
    "__import__": "m = __import__('os')",
    "exec": "exec('1')",
    "eval": "eval('1')",
    "getattr": "getattr(object, 'x')",
    "__class__": "x = ().__class__",
    "__subclasses__": "x = [].__class__.__base__.__subclasses__()",
    "__builtins__": "x = __builtins__",
    "exporters": "import cadquery as cq\ncq.exporters.export(1, 'x')",
    "importers": "import cadquery as cq\ncq.importers.importStep('x')",
    "exportStl": "w.exportStl('x')",
    "assembly save": "w.save('x')",
    "occ_impl": "import cadquery as cq\nx = cq.occ_impl",
    "classe": "class A:\n    pass",
    "decorador": "@print\ndef f():\n    pass",
    "global": "def f():\n    global x",
    "async": "async def f():\n    pass",
    "script grande": "x = 1\n" * 10000,
    "sintaxe": "def (",
}

VALIDOS = {
    "caixa": "import cadquery as cq\nresult = cq.Workplane('XY').box(1, 1, 1)",
    "math": "import cadquery as cq\nimport math\nr = math.pi\nresult = cq.Workplane('XY').circle(r).extrude(1)",
    "funcao": "import cadquery as cq\ndef furo(d):\n    return cq.Workplane('XY').circle(d).extrude(1)\nresult = furo(2)",
    "seletor": "import cadquery as cq\ns = cq.selectors.BoxSelector((0, 0, 0), (1, 1, 1))\nresult = cq.Workplane('XY').box(1, 1, 1)",
}


@pytest.mark.parametrize("codigo", MALICIOSOS.values(), ids=MALICIOSOS.keys())
def test_recusa_codigo_malicioso(codigo):
    with pytest.raises(CodigoRecusado):
        validar_codigo(codigo)


@pytest.mark.parametrize("codigo", VALIDOS.values(), ids=VALIDOS.keys())
def test_aceita_codigo_valido(codigo):
    validar_codigo(codigo)


def test_informa_a_linha():
    with pytest.raises(CodigoRecusado) as erro:
        validar_codigo("x = 1\ny = 2\nimport os")
    assert erro.value.linha == 3
