# import sys
# from pathlib import Path
# from paddleocr import PaddleOCR
# from paddleocr import PPStructureV3
# ocr = PaddleOCR(use_angle_cls=True, lang='ru',show_log=False)
# ocr = PaddleOCR()
# structure = PPStructureV3(use_doc_orientation_classify=True)
# res = ocr.ocr('test.png', cls=True)
# print(res)
from paddleocr import PaddleOCR
ocr = PaddleOCR(
    lang='ru',
    use_doc_orientation_classify=False,
    use_doc_unwarping=False,
    use_textline_orientation=False)

# Run OCR inference on a sample image 
result = ocr.predict(
    input="./photo_1754050117342.jpg")

# Visualize the results and save the JSON results
for res in result:
    res.print()
    res.save_to_img("output")
    res.save_to_json("output")