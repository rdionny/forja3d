"""A imagem de referência sai do computador limpa, pequena e sem metadados."""
import io

import pytest
from PIL import ExifTags, Image, PngImagePlugin

from forja3d.conceitos.referencia import LADO_MAX_PX, TAMANHO_MAX_BYTES, ReferenciaInvalida, preparar_referencia

SEGREDO = "MarcaSecreta123"


def _bytes(imagem: Image.Image, formato: str, **opcoes) -> bytes:
    saida = io.BytesIO()
    imagem.save(saida, format=formato, **opcoes)
    return saida.getvalue()


def _abrir(dados: bytes) -> Image.Image:
    imagem = Image.open(io.BytesIO(dados))
    imagem.load()
    return imagem


def _foto_com_gps() -> bytes:
    exif = Image.Exif()
    exif[ExifTags.Base.Make] = SEGREDO
    gps = exif.get_ifd(ExifTags.IFD.GPSInfo)
    gps[ExifTags.GPS.GPSLatitudeRef] = "S"
    gps[ExifTags.GPS.GPSLatitude] = (23.0, 33.0, 1.0)
    return _bytes(Image.new("RGB", (64, 48), "orange"), "JPEG", exif=exif)


def test_foto_com_gps_sai_sem_nenhum_metadado():
    foto = _foto_com_gps()
    assert _abrir(foto).getexif().get_ifd(ExifTags.IFD.GPSInfo), "a foto de teste precisa ter GPS"
    assert SEGREDO.encode() in foto

    limpa = preparar_referencia(foto)

    resultado = _abrir(limpa)
    assert resultado.format == "PNG"
    assert not resultado.getexif()
    assert "exif" not in resultado.info
    assert SEGREDO.encode() not in limpa
    assert b"eXIf" not in limpa


def test_comentario_e_perfil_de_cor_tambem_saem():
    texto = PngImagePlugin.PngInfo()
    texto.add_text("Comentario", SEGREDO)
    original = _bytes(Image.new("RGB", (32, 32), "blue"), "PNG", pnginfo=texto, icc_profile=b"perfil-falso")
    limpa = preparar_referencia(original)
    assert SEGREDO.encode() not in limpa
    assert "icc_profile" not in _abrir(limpa).info


def test_aplica_a_rotacao_do_exif_antes_de_descartar():
    exif = Image.Exif()
    exif[ExifTags.Base.Orientation] = 6
    foto = _bytes(Image.new("RGB", (80, 40), "red"), "JPEG", exif=exif)
    assert _abrir(preparar_referencia(foto)).size == (40, 80)


@pytest.mark.parametrize(
    ("entrada", "saida"),
    [((4000, 1000), (LADO_MAX_PX, 384)), ((1000, 4000), (384, LADO_MAX_PX)), ((100, 50), (100, 50))],
)
def test_reduz_so_o_que_passa_do_limite(entrada, saida):
    original = _bytes(Image.new("RGB", entrada, "green"), "PNG")
    assert _abrir(preparar_referencia(original)).size == saida


@pytest.mark.parametrize(("modo", "formato"), [("RGB", "JPEG"), ("RGB", "WEBP"), ("RGBA", "PNG"), ("P", "PNG"), ("L", "PNG"), ("CMYK", "JPEG")])
def test_aceita_os_formatos_e_modos_comuns(modo, formato):
    resultado = _abrir(preparar_referencia(_bytes(Image.new(modo, (16, 16)), formato)))
    assert resultado.mode in ("RGB", "RGBA")


def test_transparencia_e_preservada():
    original = _bytes(Image.new("RGBA", (8, 8), (255, 0, 0, 0)), "PNG")
    assert _abrir(preparar_referencia(original)).mode == "RGBA"


@pytest.mark.parametrize("dados", [b"", b"nao sou uma imagem", _bytes(Image.new("RGB", (8, 8)), "GIF")])
def test_recusa_o_que_nao_e_imagem_aceita(dados):
    with pytest.raises(ReferenciaInvalida, match="PNG, JPG ou WEBP"):
        preparar_referencia(dados)


def test_recusa_bytes_grandes_demais():
    with pytest.raises(ReferenciaInvalida, match="10 MB"):
        preparar_referencia(b"0" * (TAMANHO_MAX_BYTES + 1))


def test_recusa_arquivo_grande_demais_sem_ler(tmp_path):
    arquivo = tmp_path / "grande.png"
    with open(arquivo, "wb") as saida:
        saida.truncate(TAMANHO_MAX_BYTES + 1)
    with pytest.raises(ReferenciaInvalida, match="10 MB"):
        preparar_referencia(arquivo)


def test_arquivo_inexistente_vira_mensagem(tmp_path):
    with pytest.raises(ReferenciaInvalida, match="Não consegui ler"):
        preparar_referencia(tmp_path / "nao_existe.png")


def test_le_de_um_arquivo(tmp_path):
    arquivo = tmp_path / "foto.jpg"
    arquivo.write_bytes(_foto_com_gps())
    assert SEGREDO.encode() not in preparar_referencia(arquivo)
