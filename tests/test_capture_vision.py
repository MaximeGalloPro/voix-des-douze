"""OCR réel sur recadrages des captures utilisateur, jamais un client Dofus live.

Les attentes ci-dessous sont transcrites manuellement depuis les images ; seules
les variantes typographiques apostrophes/espaces devant ponctuation sont ignorées.
Les fixtures retirent le chat, noms de compte et l’essentiel de l’interface.
"""
from pathlib import Path
import platform
import re

from PIL import Image
import pytest

from voix_des_douze.macos import CapturePermissionError, WindowClosedError, capture_window, list_windows, permission_status
from voix_des_douze.vision import Dialogue, OCRError, Rect, detect_regions, recognize

FIXTURES = Path(__file__).parent / "fixtures" / "dialogues"
CASES = [
    ("kerubim", (1290, 370), "Kerubim Crépin",
     "La boutique est fermée pour cause d’inventaire. Mais vous pouvez flâner un peu et admirer toutes les merveilles que j’ai accumulées au fil des ans. Eh, c’est que j’ai pas mal bourlingué. Le Monde des Douze n’a plus de secrets pour Kerubim Crépin ! Si vous faites des trouvailles intéressantes, n’hésitez pas à me les montrer, je pourrai peut-être vous en dire plus à leur sujet.",
     ("En savoir plus sur Ecaflipus.", "Dire que vous feriez bien un petit donjon.", "Montrer un objet magique.", "S’en aller.")),
    ("nacf", (750, 480), "Nacf Virgil",
     "Deeeemandez mes parchemins et mes livres anciens ! Ils sont garantis sans taches d’encre et sans pliures intempestives. Si vous ne savez pas lire, ils décoreront votre intérieur. N’hésitez plus, la culture est à votre portée !",
     ("S’en aller.",)),
    ("al", (1290, 330), "Al Etsop",
     "Bonjour, nous sommes les banquiers. Vous pouvez ouvrir un compte dans notre banque et vous aurez un coffre à disposition pour déposer vos biens. Mais attention, chaque consultation de votre compte vous coûtera 0 Kamas.",
     ("Consulter son coffre personnel.", "Se renseigner sur les modalités d’ouverture d’un compte en banque.")),
]


def canonical(text):
    return re.sub(r"\s+([!?:;])", r"\1", " ".join(text.replace("’", "'").split()))


@pytest.mark.skipif(platform.system() != "Darwin", reason="Apple Vision exige macOS")
@pytest.mark.parametrize("name,origin,npc,text,responses", CASES)
def test_real_vision_dialogue_order_and_separation(name, origin, npc, text, responses):
    canvas = Image.new("RGB", (2048, 1280))
    with Image.open(FIXTURES / f"{name}.png") as crop:
        canvas.paste(crop, origin)
        canvas.info["icc_profile"] = crop.info["icc_profile"]
    result = recognize(canvas)
    assert result.npc == npc
    assert canonical(result.text) == canonical(text)
    assert tuple(map(canonical, result.responses)) == tuple(map(canonical, responses))
    assert set(result.regions) == {"npc", "text", "responses"}


def test_manual_regions_do_not_recognize_outside_selection():
    # The image contains a real dialogue, but deliberately selected blank pixels
    # must not trigger auto detection or leak another region into the result.
    with Image.open(FIXTURES / "al.png") as crop:
        image = Image.new("RGB", (1000, 1000), "black")
        image.paste(crop, (400, 400))
    assert recognize(image, {}) == Dialogue()
    if platform.system() == "Darwin":
        assert recognize(image, {"text": Rect(0, 0, .1, .1)}).text == ""


def test_closed_conversation_and_unrelated_ui():
    assert detect_regions(Image.new("RGB", (2048, 1280), (25, 25, 45))) == {}
    assert recognize(Image.new("RGB", (400, 300), "white")) == Dialogue()
    with pytest.raises(OCRError):
        recognize(Image.new("RGB", (100, 100)), {"unknown": Rect(0, 0, 1, 1)})


@pytest.mark.parametrize("values", [(-.1, 0, .5, .5), (0, 0, 0, 1), (.8, 0, .3, 1), (0, float('nan'), 1, 1)])
def test_invalid_regions_rejected(values):
    with pytest.raises(ValueError):
        Rect(*values)


def test_invalid_window_identifier_is_closed():
    for identifier in (0, -1, 2**32, True):
        with pytest.raises(WindowClosedError):
            capture_window(identifier)


@pytest.mark.skipif(platform.system() != "Darwin", reason="ScreenCaptureKit exige macOS")
def test_real_capture_permission_or_missing_window():
    if permission_status():
        with pytest.raises(WindowClosedError):
            capture_window(0xFFFFFFFF)
    else:
        # No permission prompt and no privacy bypass: native checks precede lookup.
        with pytest.raises(CapturePermissionError):
            list_windows()
        with pytest.raises(CapturePermissionError):
            capture_window(0xFFFFFFFF)
