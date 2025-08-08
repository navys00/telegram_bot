from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder
from typing import List, Dict, Any
from pathlib import Path
import io, os, re, time

import numpy as np
import cv2
from PIL import Image
from paddleocr import PaddleOCR

app = FastAPI(title='check_api')

# Сохраняем именно в ./downloads рядом с файлом
DOWNLOAD_DIR = Path(__file__).parent / "downloads"
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

OCR = PaddleOCR(
    use_angle_cls=True,
    lang='ru',
    
    det_limit_side_len=1200
)

def _safe_stem(name: str) -> str:
    base = os.path.basename(name) if name else "upload"
    stem = Path(base).stem
    return re.sub(r'[^A-Za-z0-9._-]+', '_', stem) or "upload"

def read_image_to_bgr(file_bytes: bytes):
    arr = np.frombuffer(file_bytes, np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("Не удалось декодировать изображение")
    return img

def mask_highlight(img_bgr: np.ndarray) -> np.ndarray:
    hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)
    red1 = cv2.inRange(hsv, (0, 120, 70), (10, 255, 255))
    red2 = cv2.inRange(hsv, (170, 120, 70), (180, 255, 255))
    blue = cv2.inRange(hsv, (90, 80, 50), (130, 255, 255))
    green = cv2.inRange(hsv, (36, 80, 50), (86, 255, 255))
    mask_color = cv2.bitwise_or(cv2.bitwise_or(red1, red2), cv2.bitwise_or(blue, green))
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    grad = cv2.morphologyEx(gray, cv2.MORPH_GRADIENT, np.ones((3,3), np.uint8))
    _, th = cv2.threshold(grad, 0, 255, cv2.THRESH_OTSU)
    thick = cv2.dilate(th, np.ones((3,3), np.uint8), iterations=1)
    mask = cv2.bitwise_or(mask_color, thick)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((7,7), np.uint8), iterations=2)
    mask = cv2.dilate(mask, np.ones((5,5), np.uint8), iterations=1)
    return mask

def iou_with_mask(box: List[List[float]], mask: np.ndarray) -> float:
    poly = np.array(box, dtype=np.int32)
    x_min, y_min = np.min(poly[:, 0]), np.min(poly[:, 1])
    x_max, y_max = np.max(poly[:, 0]), np.max(poly[:, 1])
    roi = np.zeros_like(mask)
    cv2.fillPoly(roi, [poly], 255)
    inter = cv2.bitwise_and(roi, mask)
    inter_area = int(np.sum(inter > 0))
    box_area = max((x_max - x_min + 1) * (y_max - y_min + 1), 1)
    return inter_area / box_area

def _to_py(obj):
    # Рекурсивно переводит numpy-типы в чистые Python-типы
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, (np.integer, np.floating)):
        return obj.item()
    if isinstance(obj, list):
        return [_to_py(x) for x in obj]
    if isinstance(obj, tuple):
        return [_to_py(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _to_py(v) for k, v in obj.items()}
    return obj

def parse_predict_result(pred, score_thresh: float = 0.5):
    """
    Извлекает текст/боксы/скор из результата predict, предпочитая поля rec_*.
    Собирает full_text из rec_texts с фильтрацией пустых строк и низких скорингов.
    """
    lines: List[Dict[str, Any]] = []
    full_text_parts: List[str] = []
    if not pred:
        return lines, ""

    res = pred[0] if isinstance(pred, list) else pred

    # 1) Пытаемся взять новые поля rec_*
    rec_texts = getattr(res, "rec_texts", None)
    rec_scores = getattr(res, "rec_scores", None)
    rec_polys = getattr(res, "rec_polys", None)

    # Если это OCRResult с to_dict()
    if (rec_texts is None or rec_scores is None or rec_polys is None) and hasattr(res, "to_dict"):
        d = res.to_dict()
        rec_texts = rec_texts or d.get("rec_texts")
        rec_scores = rec_scores or d.get("rec_scores")
        rec_polys = rec_polys or d.get("rec_polys")

    # Если это dict
    if rec_texts is None and isinstance(res, dict):
        rec_texts = res.get("rec_texts")
        rec_scores = rec_scores or res.get("rec_scores")
        rec_polys = rec_polys or res.get("rec_polys")

    # 2) Фоллбек на старые имена
    texts = rec_texts or getattr(res, "texts", None) or (res.get("texts") if isinstance(res, dict) else None)
    scores = rec_scores or getattr(res, "scores", None) \
             or (res.get("rec_scores") if isinstance(res, dict) else None) \
             or (res.get("scores") if isinstance(res, dict) else None)
    boxes = rec_polys or getattr(res, "dt_polys", None) or getattr(res, "boxes", None) \
            or (res.get("rec_polys") if isinstance(res, dict) else None) \
            or (res.get("dt_polys") if isinstance(res, dict) else None) \
            or (res.get("boxes") if isinstance(res, dict) else None)

    if not texts:
        return lines, ""

    # Приводим боксы к чистым Python-типам (без numpy)
    out_boxes = None
    if boxes is not None:
        out_boxes = _to_py(boxes)
        # Дополнительная нормализация формы [8] -> [[x,y]x4]
        norm_boxes = []
        for b in out_boxes:
            if isinstance(b, list) and len(b) == 8 and all(isinstance(x, (int, float)) for x in b):
                norm_boxes.append([[b[0], b[1]], [b[2], b[3]], [b[4], b[5]], [b[6], b[7]]])
            else:
                norm_boxes.append(b)
        out_boxes = norm_boxes

    # Собираем строки с фильтрацией
    n = len(texts)
    for i in range(n):
        txt = texts[i] if texts[i] is not None else ""
        if isinstance(txt, bytes):
            try:
                txt = txt.decode("utf-8", "ignore")
            except Exception:
                txt = str(txt)
        t = str(txt).strip()
        sc = float(scores[i]) if (scores is not None and i < len(scores) and scores[i] is not None) else None

        if t and (sc is None or sc >= score_thresh):
            box_i = out_boxes[i] if (out_boxes is not None and i < len(out_boxes)) else None
            lines.append({"box": box_i, "text": t, "conf": sc if sc is not None else 1.0})
            full_text_parts.append(t)

    return lines, " ".join(full_text_parts).strip()

@app.get('/check')
def check():
    return {'status': 'ok'}

@app.post('/ocr')
async def ocr(image: UploadFile = File(...), focus: str = Form('full')):
    if not image.content_type or not image.content_type.startswith('image/'):
        raise HTTPException(status_code=400, detail='Field "image" должен быть картинкой (image/*)')

    content = await image.read()
    if not content:
        raise HTTPException(status_code=400, detail="Файл пустой")

    # Имя и путь для сохранения (PNG в ./downloads)
    safe_stem = _safe_stem(image.filename or "upload")
    ts = int(time.time() * 1000)
    filename = f"{safe_stem}_{ts}.png"
    save_path = DOWNLOAD_DIR / filename

    # Декод и размеры
    try:
        img_bgr = read_image_to_bgr(content)
        h, w = img_bgr.shape[:2]
        width, height = w, h
    except Exception as e:
        try:
            im = Image.open(io.BytesIO(content)).convert("RGB")
            width, height = im.size
            img_bgr = cv2.cvtColor(np.array(im), cv2.COLOR_RGB2BGR)
        except Exception:
            raise HTTPException(status_code=400, detail=f"Не удалось декодировать изображение: {e}")

    # Сохраняем PNG в ./downloads
    ok, buf = cv2.imencode('.png', img_bgr)
    if not ok:
        raise HTTPException(status_code=500, detail="Не удалось перекодировать в PNG")
    try:
        with open(save_path, "wb") as f:
            f.write(buf.tobytes())
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Не удалось сохранить PNG: {e}")

    # Путь к файлу для PaddleOCR.predict
    image_path_for_predict = save_path.as_posix()
    try:
        pred = OCR.predict(input=image_path_for_predict)
    except Exception:
        pred = OCR.predict(img_bgr)

    lines, full_text = parse_predict_result(pred)

    highlighted_text = ""
    mask_present = False
    if focus == "highlight" and lines:
        mask = mask_highlight(img_bgr)
        mask_present = bool(np.any(mask > 0))
        filtered = []
        for ln in lines:
            if ln["box"] is None:
                continue
            ov = iou_with_mask(ln["box"], mask)
            if ov >= 0.28:
                ln["overlap"] = ov
                filtered.append(ln)
        if not filtered and mask_present:
            ys, xs = np.where(mask > 0)
            if len(xs):
                m_center = np.array([np.mean(xs), np.mean(ys)])
                def center(bx):
                    p = np.array(bx); return np.mean(p, axis=0)
                filtered = sorted(
                    [ln for ln in lines if ln["box"] is not None],
                    key=lambda ln: np.linalg.norm(center(ln["box"]) - m_center)
                )[:1]
        highlighted_text = " ".join([ln["text"] for ln in filtered]).strip()

    payload = {
        'status': 'ok',
        'filename': image.filename,
        'saved_filename': filename,
        'saved_relpath': str(Path("downloads") / filename),
        'content_type': "image/png",
        'size_bytes': (save_path.stat().st_size if save_path.exists() else None),
        'width': width,
        'height': height,
        'focus': focus,
        'ocr': {
            'full_text': full_text,
            'highlighted_text': highlighted_text,
            'mask_present': mask_present,
            'boxes': lines
        }
    }
    return JSONResponse(content=jsonable_encoder(payload))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("test_ocr:app", host="0.0.0.0", port=8000, reload=True)