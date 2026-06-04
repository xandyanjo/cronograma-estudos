import pdfplumber
import sys

sys.stdout.reconfigure(encoding='utf-8')

pdf_path = r"C:\Users\alexa\Documents\Estudos\CA-AA-AFN\2019\2019.pdf"
out_path = r"C:\Users\alexa\Documents\Estudos\pages_text_2019.txt"

with pdfplumber.open(pdf_path) as pdf:
    with open(out_path, "w", encoding="utf-8") as f:
        for idx, page in enumerate(pdf.pages):
            width = page.width
            height = page.height
            left = page.crop((0, 0, width / 2, height))
            right = page.crop((width / 2, 0, width, height))
            left_text = left.extract_text() or ""
            right_text = right.extract_text() or ""
            
            f.write(f"\n\n================ PAGE {idx+1} ================\n")
            f.write("--- LEFT COLUMN ---\n")
            f.write(left_text)
            f.write("\n--- RIGHT COLUMN ---\n")
            f.write(right_text)
            
print(f"Saved all pages of 2019.pdf to {out_path}")
