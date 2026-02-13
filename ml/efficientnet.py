import os
from pathlib import Path

import torch
import torch.nn.functional as F
from torchvision import transforms
from PIL import Image

DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")

# IMPORTANT: Keep this list aligned with the class order used to train the model.
# Default keys match the existing DB (vegetables.class_key).
DEFAULT_CLASS_KEYS = [
    "makwaen",
    "neem",
    "paracress",
    "rattailed_radish",
    "tupistra",
    "salae",
]

_env_keys = [k.strip() for k in os.environ.get("MODEL_CLASS_KEYS", "").split(",") if k.strip()]
CLASS_KEYS = _env_keys if len(_env_keys) == len(DEFAULT_CLASS_KEYS) else DEFAULT_CLASS_KEYS

# Expect: <project_root>/model/EfficientNet-B2.pt
MODEL_PATH = Path(__file__).resolve().parents[1] / "model" / "EfficientNet-B2.pt"

if not MODEL_PATH.exists():
    raise FileNotFoundError(
        f"Model file not found: {MODEL_PATH}. Put EfficientNet-B2.pt in the 'model' folder." 
    )

# EfficientNet-B2 exported as TorchScript (.pt)
_model = torch.jit.load(str(MODEL_PATH), map_location=DEVICE)
_model.eval()

# Preprocess: typical EfficientNet-B2 pipeline.
_transform = transforms.Compose([
    transforms.Resize(260),
    transforms.CenterCrop(260),
    transforms.ToTensor(),
    transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
])


def predict_image(file):
    """Accepts Flask FileStorage (file.stream). Returns {classKey, confidence}."""
    img = Image.open(file.stream).convert("RGB")
    x = _transform(img).unsqueeze(0).to(DEVICE)

    with torch.no_grad():
        out = _model(x)
        if isinstance(out, (tuple, list)):
            out = out[0]
        prob = F.softmax(out, dim=1)[0]

    conf, idx = torch.max(prob, 0)
    label = CLASS_KEYS[int(idx)]
    return {"classKey": label, "confidence": float(conf)}
