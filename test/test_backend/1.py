import pytesseract
from PIL import Image
pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
img = Image.open("../test_image/1.png")
text = pytesseract.image_to_string(img, lang='chi_sim+eng')
print(repr(text))