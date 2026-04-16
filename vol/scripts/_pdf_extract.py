
import sys
import pdfplumber

pdf_path = sys.argv[1]
pages_text = []
with pdfplumber.open(pdf_path) as pdf:
    for page in pdf.pages:
        text = page.extract_text() or ""
        pages_text.append(text)

print("\n\n".join(pages_text))
