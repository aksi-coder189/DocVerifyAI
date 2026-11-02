"""Generate a synthetic marksheet PDF for local demonstrations only."""
from pathlib import Path
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

OUT = Path(__file__).resolve().parents[1] / "examples" / "synthetic-marksheet.pdf"


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    pdf = canvas.Canvas(str(OUT), pagesize=A4)
    pdf.setTitle("Synthetic Demo Marksheet")
    pdf.setFont("Helvetica-Bold", 18)
    pdf.drawCentredString(297, 790, "SYNTHETIC DEMONSTRATION")
    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawCentredString(297, 755, "Academic Marksheet")
    pdf.setFont("Helvetica", 12)
    lines = [
        ("Issuing Authority", "Example Demo Board"),
        ("Document Type", "Marksheet"),
        ("Student Name", "Aarav Sharma"),
        ("Roll Number", "DEMO-2025"),
        ("Exam Year", "2025"),
        ("Result", "Pass"),
    ]
    y = 700
    for label, value in lines:
        pdf.drawString(90, y, f"{label}: {value}")
        y -= 35
    # Fictional table data gives the bundled parser fixture real coordinate-based
    # subject rows while keeping the document visibly synthetic.
    pdf.setFont("Helvetica-Bold", 9)
    columns = [(55, "SUBJECT CODE"), (110, "SUBJECT NAME"), (300, "THEORY"),
               (355, "PRACTICAL"), (425, "TOTAL"), (500, "GRADE")]
    for x, label in columns:
        pdf.drawString(x, 455, label)
    pdf.setFont("Helvetica", 9)
    for row_y, values in zip((435, 417, 399), [
        ("041", "MATHEMATICS", "078", "020", "098", "A1"),
        ("184", "ENGLISH LANGUAGE", "075", "020", "095", "A1"),
        ("086", "APPLIED SCIENCE", "072", "020", "092", "A2"),
    ]):
        for (x, _), value in zip(columns, values):
            pdf.drawString(x, row_y, value)
    pdf.setFont("Helvetica-Oblique", 9)
    pdf.drawCentredString(297, 85, "Synthetic sample only — not an official academic record")
    pdf.save()
    print(OUT)


if __name__ == "__main__":
    main()
