"""High-fidelity PDF document builder with running headers, footers, and two-pass page numbering."""

from pathlib import Path
from typing import List, Optional

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    HRFlowable,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.synthetic.models import DocumentDefinition, SectionContent


class NumberedCanvas(canvas.Canvas):
    """Two-pass canvas to dynamically compute and print 'Page X of Y' in the running footer."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_decorations(num_pages)
            super().showPage()
        super().save()

    def draw_page_decorations(self, page_count: int):
        self.saveState()
        self.setFont("Helvetica", 8)
        self.setFillColor(colors.HexColor("#64748B"))

        # Header (Pages 2+)
        if self._pageNumber > 1:
            header_text = getattr(self, "doc_header_title", "GrantLoom Document")
            self.drawString(54, 750, header_text[:85])
            self.setStrokeColor(colors.HexColor("#CBD5E1"))
            self.setLineWidth(0.5)
            self.line(54, 744, 558, 744)

        # Footer (All pages)
        self.setStrokeColor(colors.HexColor("#CBD5E1"))
        self.setLineWidth(0.5)
        self.line(54, 45, 558, 45)

        footer_left = getattr(self, "doc_footer_left", "Confidential / Official Grant Record")
        self.drawString(54, 32, footer_left)
        page_str = f"Page {self._pageNumber} of {page_count}"
        self.drawRightString(558, 32, page_str)

        self.restoreState()


class SyntheticPDFBuilder:
    """Renders structured document definitions into professional multi-page PDFs."""

    def __init__(self):
        self.styles = getSampleStyleSheet()
        self._setup_custom_styles()

    def _setup_custom_styles(self):
        # Base colors
        self.primary_color = colors.HexColor("#0F172A")  # Slate 900
        self.secondary_color = colors.HexColor("#0369A1")  # Sky 700
        self.text_color = colors.HexColor("#1E293B")  # Slate 800
        self.callout_bg = colors.HexColor("#F8FAFC")  # Slate 50
        self.border_color = colors.HexColor("#E2E8F0")

        self.styles.add(
            ParagraphStyle(
                name="DocTitle",
                fontName="Helvetica-Bold",
                fontSize=20,
                leading=24,
                textColor=self.primary_color,
                spaceAfter=8,
            )
        )
        self.styles.add(
            ParagraphStyle(
                name="DocSubtitle",
                fontName="Helvetica",
                fontSize=11,
                leading=15,
                textColor=self.secondary_color,
                spaceAfter=14,
            )
        )
        self.styles.add(
            ParagraphStyle(
                name="SectionH1",
                fontName="Helvetica-Bold",
                fontSize=14,
                leading=18,
                textColor=self.primary_color,
                spaceBefore=12,
                spaceAfter=6,
                keepWithNext=True,
            )
        )
        self.styles.add(
            ParagraphStyle(
                name="SectionH2",
                fontName="Helvetica-Bold",
                fontSize=11,
                leading=15,
                textColor=self.secondary_color,
                spaceBefore=8,
                spaceAfter=4,
                keepWithNext=True,
            )
        )
        self.styles.add(
            ParagraphStyle(
                name="BodyDark",
                fontName="Helvetica",
                fontSize=9.5,
                leading=13.5,
                textColor=self.text_color,
                spaceAfter=6,
            )
        )
        self.styles.add(
            ParagraphStyle(
                name="BulletItem",
                fontName="Helvetica",
                fontSize=9.5,
                leading=13.5,
                textColor=self.text_color,
                leftIndent=15,
                firstLineIndent=-10,
                spaceAfter=3,
            )
        )
        self.styles.add(
            ParagraphStyle(
                name="CalloutText",
                fontName="Helvetica-Oblique",
                fontSize=9,
                leading=13,
                textColor=colors.HexColor("#0F172A"),
            )
        )
        self.styles.add(
            ParagraphStyle(
                name="TableHeader",
                fontName="Helvetica-Bold",
                fontSize=9,
                leading=11,
                textColor=colors.white,
                alignment=0,
            )
        )
        self.styles.add(
            ParagraphStyle(
                name="TableCell",
                fontName="Helvetica",
                fontSize=8.5,
                leading=11,
                textColor=self.text_color,
            )
        )

    def build_pdf(self, doc_def: DocumentDefinition, output_path: Path) -> Path:
        """Compiles DocumentDefinition into a PDF file at output_path."""
        output_path.parent.mkdir(parents=True, exist_ok=True)

        doc = SimpleDocTemplate(
            str(output_path),
            pagesize=letter,
            leftMargin=54,
            rightMargin=54,
            topMargin=54,
            bottomMargin=54,
        )

        flowables = []

        # Document Header Block
        flowables.append(Paragraph(doc_def.title, self.styles["DocTitle"]))
        if doc_def.subtitle:
            flowables.append(Paragraph(doc_def.subtitle, self.styles["DocSubtitle"]))

        meta_line = (
            f"<b>Author/Agency:</b> {doc_def.author_or_agency} | "
            f"<b>Date:</b> {doc_def.date_str} | "
            f"<b>Document ID:</b> {doc_def.doc_id}"
        )
        flowables.append(Paragraph(meta_line, self.styles["BodyDark"]))
        flowables.append(HRFlowable(width="100%", thickness=1, color=self.border_color, spaceAfter=12))

        # Render sections
        for idx, sec in enumerate(doc_def.sections):
            flowables.extend(self._render_section(sec))
            if sec.page_break_after and idx < len(doc_def.sections) - 1:
                flowables.append(PageBreak())

        # Pass metadata to NumberedCanvas
        def canvas_maker(*args, **kwargs):
            canv = NumberedCanvas(*args, **kwargs)
            canv.doc_header_title = f"{doc_def.doc_id} — {doc_def.title}"
            canv.doc_footer_left = f"{doc_def.author_or_agency} | Official Document"
            return canv

        doc.build(flowables, canvasmaker=canvas_maker)
        return output_path

    def _render_section(self, sec: SectionContent) -> List:
        elements = []
        heading_style = self.styles["SectionH1"] if sec.level == 1 else self.styles["SectionH2"]
        elements.append(Paragraph(sec.title, heading_style))

        for p in sec.paragraphs:
            elements.append(Paragraph(p, self.styles["BodyDark"]))

        for b in sec.bullet_points:
            bullet_html = f"&bull; {b}"
            elements.append(Paragraph(bullet_html, self.styles["BulletItem"]))

        if sec.bullet_points:
            elements.append(Spacer(1, 4))

        if sec.callout_box:
            callout_p = Paragraph(f"<b>NOTE / REQUIREMENT:</b> {sec.callout_box}", self.styles["CalloutText"])
            callout_table = Table([[callout_p]], colWidths=[504])
            callout_table.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, -1), self.callout_bg),
                        ("BOX", (0, 0), (-1, -1), 0.75, self.secondary_color),
                        ("PADDING", (0, 0), (-1, -1), 8),
                        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ]
                )
            )
            elements.append(Spacer(1, 4))
            elements.append(callout_table)
            elements.append(Spacer(1, 6))

        if sec.table_data:
            table_flowable = self._build_table(sec.table_data)
            elements.append(Spacer(1, 4))
            elements.append(table_flowable)
            elements.append(Spacer(1, 8))

        return elements

    def _build_table(self, raw_data: List[List[str]]) -> Table:
        formatted_data = []
        for row_idx, row in enumerate(raw_data):
            row_items = []
            for col in row:
                style = self.styles["TableHeader"] if row_idx == 0 else self.styles["TableCell"]
                row_items.append(Paragraph(str(col), style))
            formatted_data.append(row_items)

        # Distribute width evenly across available 504 pt width
        col_count = len(raw_data[0]) if raw_data else 1
        col_width = 504.0 / col_count

        t = Table(formatted_data, colWidths=[col_width] * col_count, repeatRows=1)
        t_style = [
            ("BACKGROUND", (0, 0), (-1, 0), self.primary_color),
            ("ALIGN", (0, 0), (-1, -1), "LEFT"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("INNERGRID", (0, 0), (-1, -1), 0.5, self.border_color),
            ("BOX", (0, 0), (-1, -1), 0.75, self.border_color),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]
        # Alternating row colors
        for r in range(1, len(formatted_data)):
            if r % 2 == 0:
                t_style.append(("BACKGROUND", (0, r), (-1, r), colors.HexColor("#F8FAFC")))
        t.setStyle(TableStyle(t_style))
        return t
