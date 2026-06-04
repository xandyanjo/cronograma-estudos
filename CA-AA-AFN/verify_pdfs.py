import os
import re
import pdfplumber

BASE_DIR = r"C:\Users\alexa\Documents\Estudos\CA-AA-AFN"
report_path = os.path.join(BASE_DIR, "pdf_report.txt")

with open(report_path, "w", encoding="utf-8") as out:
    out.write("PDF Headers Verification Report\n")
    out.write("================================\n")
    
    for ano in range(2019, 2025):
        pdf_path = os.path.join(BASE_DIR, str(ano), f"{ano}.pdf")
        if not os.path.exists(pdf_path):
            out.write(f"Year {ano}: PDF NOT FOUND at {pdf_path}\n")
            continue
            
        print(f"Processing year {ano}...")
        try:
            with pdfplumber.open(pdf_path) as pdf:
                text_lines = []
                # Process only page index 1 to 3 to verify the first few questions
                for idx, page in enumerate(pdf.pages[1:4]):
                    width = page.width
                    height = page.height
                    left = page.crop((0, 0, width / 2, height))
                    right = page.crop((width / 2, 0, width, height))
                    left_text = left.extract_text() or ""
                    right_text = right.extract_text() or ""
                    text_lines.append(left_text)
                    text_lines.append(right_text)
                    
                full_text = "\n".join(text_lines)
                matches = re.findall(r'(QUEST[ÃA]O\s+\d+)', full_text, re.IGNORECASE)
                out.write(f"Year {ano}: found {len(matches)} question headers in first few pages. Sample: {matches[:10]}\n")
        except Exception as e:
            out.write(f"Year {ano}: ERROR: {e}\n")
            
print(f"Report written to {report_path}")
