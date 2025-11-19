from odoo import models, fields

class OCRInvoice(models.Model):
    _name = "ocr.invoice"
    _description = "OCR Invoice"

    name = fields.Char(string="Document Name", required=True)
    file = fields.Binary(string="Invoice File", attachment=True, required=True)

    vendor_name = fields.Char(string="Vendor")
    invoice_date = fields.Date(string="Invoice Date")
    total_amount = fields.Float(string="Total Amount")
    vat_amount = fields.Float(string="VAT Amount")

    confidence_score = fields.Float(string="OCR Confidence")
    status = fields.Selection([
        ('uploaded', "Uploaded"),
        ('extracting', "Extracting"),
        ('extracted', "Extracted"),
        ('review', "In Review"),
        ('posted', "Posted")
    ], string="Status", default="uploaded")

    extraction_log = fields.Text(string="OCR Log")
