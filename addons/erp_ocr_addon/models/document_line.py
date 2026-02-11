# -*- coding: utf-8 -*-
from odoo import models, fields, api


class OCRDocumentLine(models.Model):
    _name = "ocr.document.line"
    _description = "OCR Document Line Item"
    _order = "id asc"

    document_id = fields.Many2one(
        "ocr.document",
        string="Document",
        required=True,
        ondelete="cascade",
        index=True,
    )

    # Azure prebuilt-invoice item fields (normalized)
    description = fields.Text(string="Description")
    product_code = fields.Char(string="Product Code")
    quantity = fields.Float(string="Quantity", default=1.0)
    unit_price = fields.Monetary(string="Unit Price", currency_field="currency_id")
    discount_amount = fields.Monetary(string="Discount", currency_field="currency_id")
    tax_rate = fields.Float(string="Tax Rate (%)")  # if available / derived
    tax_amount = fields.Monetary(string="Tax Amount", currency_field="currency_id")

    subtotal_amount = fields.Monetary(
        string="Subtotal (Excl. Tax)",
        currency_field="currency_id",
        compute="_compute_amounts",
        store=True,
    )
    total_amount = fields.Monetary(
        string="Total (Incl. Tax)",
        currency_field="currency_id",
        compute="_compute_amounts",
        store=True,
    )

    currency_id = fields.Many2one(
        "res.currency",
        related="document_id.currency_id",
        store=True,
        readonly=True,
    )

    @api.depends("quantity", "unit_price", "discount_amount", "tax_rate", "tax_amount")
    def _compute_amounts(self):
        for line in self:
            qty = float(line.quantity or 0.0)
            price = float(line.unit_price or 0.0)
            disc = float(line.discount_amount or 0.0)

            subtotal = (qty * price) - disc
            if subtotal < 0 and price > 0:
                subtotal = 0.0

            # Prefer explicit tax_amount if set, otherwise compute from rate
            if line.tax_amount:
                tax_amt = float(line.tax_amount)
            else:
                rate = float(line.tax_rate or 0.0)
                tax_amt = subtotal * (rate / 100.0) if rate else 0.0

            line.subtotal_amount = subtotal
            line.total_amount = subtotal + tax_amt
