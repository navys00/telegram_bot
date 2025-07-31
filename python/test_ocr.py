import sys
from pathlib import Path
from paddleocr import PaddleOCR
ocr = PaddleOCR(use_angle_cls=True, lang='ru',show_log=False)
res = ocr.ocr('test.png', cls=True)
print(res)