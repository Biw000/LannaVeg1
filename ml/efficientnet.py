from __future__ import annotations
from pathlib import Path
import io

import torch
from PIL import Image
from torchvision import transforms

BASE_DIR = Path(__file__).resolve().parents[1]
MODEL_PATH = BASE_DIR / "model" / "EfficientNet-B2.pt"

DEVICE = "cpu"

# ✅ ป้องกันพัง: ถ้าไม่มีโมเดล จะคืน error ชัดๆ
_model = None

def _load_model():
    global _model
    if _model is not None:
        return _model

    if not MODEL_PATH.exists():
        _model = None
        return None

    # หมายเหตุ: ไฟล์ .pt ของคุณต้องเป็นแบบ torchscript หรือ state_dict ที่โหลดได้
    # ที่นี่ทำแบบ “torch.jit.load” ก่อน ถ้าไม่ใช่ค่อยปรับเป็น load_state_dict
    try:
        _model = torch.jit.load(str(MODEL_PATH), map_location=DEVICE)
        _model.eval()
        return _model
    except Exception:
        # fallback: assume it is a regular PyTorch model saved with torch.save(model)
        _model = torch.load(str(MODEL_PATH), map_location=DEVICE)
        _model.eval()
        return _model

# ปรับ label ให้ตรงคลาสของคุณ (class_key)
CLASS_KEYS = [
    "Broussonetia kurzil",
    "Azadirachta indica",
    "Acmella oleracea",
    "Raphanus sativus",
    "Tupistra albiflora",
    "Zanthoxylum limonella",
]

_preprocess = transforms.Compose([
    transforms.Resize((260, 260)),
    transforms.ToTensor(),
])

def predict_image(file_storage) -> dict:
    model = _load_model()
    if model is None:
        return {
            "ok": False,
            "error": "model_missing",
            "detail": f"Missing model file at {MODEL_PATH}",
            "classKey": None,
            "label": "Model not found",
            "confidence": 0.0,
        }

    img_bytes = file_storage.read()
    image = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    x = _preprocess(image).unsqueeze(0).to(DEVICE)

    with torch.no_grad():
        y = model(x)
        if isinstance(y, (list, tuple)):
            y = y[0]
        probs = torch.softmax(y, dim=1)
        conf, idx = torch.max(probs, dim=1)

    idx_i = int(idx.item())
    conf_f = float(conf.item())
    class_key = CLASS_KEYS[idx_i] if 0 <= idx_i < len(CLASS_KEYS) else None

    return {
        "ok": True,
        "classIndex": idx_i,
        "classKey": class_key,
        "label": class_key or "unknown",
        "confidence": conf_f,
    }
