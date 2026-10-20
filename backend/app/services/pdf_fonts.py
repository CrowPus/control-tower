"""The fonts every PDF we make uses (Nia's meeting decks): Vera, which ships with reportlab."""

from __future__ import annotations

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

pdfmetrics.registerFont(TTFont("Tower", "Vera.ttf"))
pdfmetrics.registerFont(TTFont("Tower-Bold", "VeraBd.ttf"))
REGULAR, BOLD = "Tower", "Tower-Bold"
