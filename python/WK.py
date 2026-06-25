from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder
from typing import List, Dict, Any, Optional
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
    yellow = cv2.inRange(hsv, (18, 80, 120), (42, 255, 255))
    blue = cv2.inRange(hsv, (90, 80, 50), (130, 255, 255))
    green = cv2.inRange(hsv, (36, 80, 50), (86, 255, 255))
    mask_color = cv2.bitwise_or(cv2.bitwise_or(red1, red2), yellow)
    mask_color = cv2.bitwise_or(mask_color, cv2.bitwise_or(blue, green))
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    grad = cv2.morphologyEx(gray, cv2.MORPH_GRADIENT, np.ones((3,3), np.uint8))
    _, th = cv2.threshold(grad, 0, 255, cv2.THRESH_OTSU)
    thick = cv2.dilate(th, np.ones((3,3), np.uint8), iterations=1)
    mask = cv2.bitwise_or(mask_color, thick)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((7,7), np.uint8), iterations=2)
    mask = cv2.dilate(mask, np.ones((5,5), np.uint8), iterations=1)
    return mask

def mask_colored_highlight(img_bgr: np.ndarray) -> np.ndarray:
    hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)
    red1 = cv2.inRange(hsv, (0, 90, 90), (10, 255, 255))
    red2 = cv2.inRange(hsv, (170, 90, 90), (180, 255, 255))
    yellow = cv2.inRange(hsv, (18, 70, 115), (45, 255, 255))
    blue = cv2.inRange(hsv, (90, 70, 70), (135, 255, 255))
    green = cv2.inRange(hsv, (36, 70, 70), (86, 255, 255))
    mask = cv2.bitwise_or(cv2.bitwise_or(red1, red2), yellow)
    mask = cv2.bitwise_or(mask, cv2.bitwise_or(blue, green))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8), iterations=2)
    mask = cv2.dilate(mask, np.ones((7, 7), np.uint8), iterations=1)
    return mask

def mask_bounds(mask: np.ndarray, min_pixels: int = 80):
    ys, xs = np.where(mask > 0)
    if len(xs) < min_pixels:
        return None
    return {
        "x1": int(np.min(xs)),
        "y1": int(np.min(ys)),
        "x2": int(np.max(xs)),
        "y2": int(np.max(ys)),
        "cx": float(np.mean(xs)),
        "cy": float(np.mean(ys)),
        "pixels": int(len(xs)),
    }

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

def box_bounds(box):
    if not box:
        return None
    pts = np.array(box, dtype=np.float32)
    if pts.ndim != 2 or pts.shape[1] != 2:
        return None
    return {
        "x1": float(np.min(pts[:, 0])),
        "y1": float(np.min(pts[:, 1])),
        "x2": float(np.max(pts[:, 0])),
        "y2": float(np.max(pts[:, 1])),
        "cx": float(np.mean(pts[:, 0])),
        "cy": float(np.mean(pts[:, 1])),
    }

def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()

def _has_letters(text: str) -> bool:
    return bool(re.search(r"[A-Za-zА-Яа-яЁё]", text or ""))

def _looks_like_odds(text: str) -> bool:
    return bool(re.fullmatch(r"\d+[.,]\d{2}", _clean_text(text)))

def _looks_like_number(text: str) -> bool:
    return bool(re.fullmatch(r"[+-]?\d+(?:[.,]\d+)?", _clean_text(text)))

def _line_items(lines: List[Dict[str, Any]]):
    items = []
    for ln in lines:
        bounds = box_bounds(ln.get("box"))
        text = _clean_text(ln.get("text", ""))
        if bounds and text:
            items.append({**ln, **bounds, "text": text})
    return sorted(items, key=lambda item: (item["cy"], item["cx"]))

def extract_league(items: List[Dict[str, Any]], height: int) -> Optional[str]:
    top = [item for item in items if item["cy"] < height * 0.32]
    live_idx = None
    for idx, item in enumerate(top):
        text = item["text"]
        if "Live" in text or "Футбол" in text:
            live_idx = idx
            if "/" in text:
                tail = _clean_text(text.split("/", 1)[1])
                if tail and "Футбол" not in tail:
                    next_line = top[idx + 1]["text"] if idx + 1 < len(top) else ""
                    return _clean_text(f"{tail} / {next_line}") if next_line else tail
            break

    if live_idx is not None and live_idx + 1 < len(top):
        return top[live_idx + 1]["text"]

    for item in top:
        text = item["text"]
        if any(key in text.lower() for key in ("лига", "этап", "чм", "кубок")):
            return text
    return None

def extract_teams(items: List[Dict[str, Any]], height: int) -> List[str]:
    skip_words = (
        "fonbet", "live", "футбол", "матч", "обзор", "популярное", "основные",
        "исход", "тотал", "фора", "главная", "спорт", "казино", "мои ставки",
    )
    candidates = []
    for item in items:
        text = item["text"]
        low = text.lower()
        if not (height * 0.22 <= item["cy"] <= height * 0.52):
            continue
        if not _has_letters(text) or any(word in low for word in skip_words):
            continue
        if _looks_like_number(text) or _looks_like_odds(text):
            continue
        candidates.append(item)

    compact = []
    for item in candidates:
        if not compact or abs(compact[-1]["cy"] - item["cy"]) > 18:
            compact.append(item)
        elif len(item["text"]) > len(compact[-1]["text"]):
            compact[-1] = item
    return [item["text"] for item in compact[:2]]

def extract_prediction(
    items: List[Dict[str, Any]],
    highlighted: List[Dict[str, Any]],
    height: int,
    highlight_area=None,
):
    source = highlighted or [item for item in items if item["cy"] > height * 0.52]
    if not source:
        return None, None

    if highlight_area:
        y1 = highlight_area["y1"] - 58
        y2 = highlight_area["y2"] + 42
        x1 = highlight_area["x1"] - 45
        x2 = highlight_area["x2"] + 80
        source = [
            item for item in items
            if y1 <= item["cy"] <= y2 and x1 <= item["cx"] <= x2
        ]
        if not [item for item in source if _looks_like_odds(item["text"])]:
            source = [item for item in items if y1 <= item["cy"] <= y2]
    elif highlighted:
        y_min = min(item["cy"] for item in highlighted)
        y_max = max(item["cy"] for item in highlighted)
        source = [item for item in items if y_min - 34 <= item["cy"] <= y_max + 34]

    odds_items = [item for item in source if _looks_like_odds(item["text"])]
    highlighted_texts = {item["text"] for item in highlighted}
    highlighted_odds = [item for item in odds_items if item["text"] in highlighted_texts]
    odds_pool = highlighted_odds or odds_items
    odds_item = sorted(odds_pool, key=lambda item: item.get("overlap", 0), reverse=True)[0] if odds_pool else None
    odds = odds_item["text"].replace(",", ".") if odds_item else None
    if odds_item:
        source_for_prediction = [
            item for item in source
            if odds_item["x1"] - 330 <= item["cx"] <= odds_item["x2"] + 30
        ]
    else:
        source_for_prediction = source

    market_words = ("фора", "тотал", "исход", "больше", "меньше")
    prediction_parts = []
    for item in sorted(source_for_prediction, key=lambda item: item["cx"]):
        text = item["text"]
        if odds_item and item is odds_item:
            continue
        if _looks_like_odds(text):
            continue
        if _looks_like_number(text) or _has_letters(text) or any(word in text.lower() for word in market_words):
            prediction_parts.append(text)

    prediction = _clean_text(" ".join(prediction_parts)) or None
    if prediction and _looks_like_number(prediction) and odds_item:
        labels_above = [
            item for item in items
            if item["cy"] < odds_item["cy"]
            and odds_item["cy"] - item["cy"] <= 90
            and abs(item["cx"] - odds_item["cx"]) <= 190
            and _has_letters(item["text"])
        ]
        if labels_above:
            label = sorted(labels_above, key=lambda item: (odds_item["cy"] - item["cy"], abs(item["cx"] - odds_item["cx"])))[0]
            prediction = _clean_text(f"{label['text']} {prediction}")
    return prediction, odds

def extract_bet_info(
    lines: List[Dict[str, Any]],
    highlighted_lines: List[Dict[str, Any]],
    width: int,
    height: int,
    highlight_area=None,
    require_highlight: bool = False,
):
    items = _line_items(lines)
    highlighted = _line_items(highlighted_lines)
    if require_highlight and not highlight_area:
        prediction, odds = None, None
    else:
        prediction, odds = extract_prediction(items, highlighted, height, highlight_area)
    teams = extract_teams(items, height)
    return {
        "league": extract_league(items, height),
        "teams": teams,
        "team_1": teams[0] if len(teams) > 0 else None,
        "team_2": teams[1] if len(teams) > 1 else None,
        "prediction": prediction,
        "odds": odds,
        "source": "highlight" if highlight_area else ("no_highlight" if require_highlight else "ocr"),
        "highlight_area": highlight_area,
        "image_width": width,
        "image_height": height,
    }

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
    highlighted_lines = []
    highlight_area = None
    if focus == "highlight" and lines:
        color_mask = mask_colored_highlight(img_bgr)
        highlight_area = mask_bounds(color_mask)
        mask_present = highlight_area is not None
        mask = color_mask
        filtered = []
        if mask_present:
            for ln in lines:
                if ln["box"] is None:
                    continue
                ov = iou_with_mask(ln["box"], mask)
                bounds = box_bounds(ln["box"])
                near_highlight = False
                if bounds:
                    y_hit = highlight_area["y1"] - 58 <= bounds["cy"] <= highlight_area["y2"] + 42
                    x_hit = not (bounds["x2"] < highlight_area["x1"] - 45 or bounds["x1"] > highlight_area["x2"] + 80)
                    near_highlight = y_hit and x_hit
                if ov >= 0.03 or near_highlight:
                    ln["overlap"] = ov
                    filtered.append(ln)
        if not filtered and mask_present:
            m_center = np.array([highlight_area["cx"], highlight_area["cy"]])
            def center(bx):
                p = np.array(bx); return np.mean(p, axis=0)
            filtered = sorted(
                [ln for ln in lines if ln["box"] is not None],
                key=lambda ln: np.linalg.norm(center(ln["box"]) - m_center)
            )[:3]
        highlighted_lines = filtered
        highlighted_text = " ".join([ln["text"] for ln in filtered]).strip()

    bet_info = extract_bet_info(
        lines,
        highlighted_lines,
        width,
        height,
        highlight_area,
        require_highlight=(focus == "highlight"),
    )

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
        'bet': bet_info,
        'ocr': {
            'full_text': full_text,
            'highlighted_text': highlighted_text,
            'mask_present': mask_present,
            'highlight_area': highlight_area,
            'highlighted_boxes': highlighted_lines,
            'boxes': lines
        }
    }
    return JSONResponse(content=jsonable_encoder(payload))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("WK:app", host="0.0.0.0", port=8000, reload=True)
