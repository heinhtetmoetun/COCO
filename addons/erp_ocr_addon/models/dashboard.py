from odoo import models, fields, api


class OcrDashboard(models.TransientModel):
    _name = "ocr.dashboard"
    _description = "OCR Dashboard"

    name = fields.Char(string="Name", default="OCR Dashboard")

    invoice_count = fields.Integer(compute="_compute_kpis")
    receipt_count = fields.Integer(compute="_compute_kpis")
    failed_count = fields.Integer(compute="_compute_kpis")

    total_processed_value = fields.Monetary(
        string="Total Value",
        currency_field="currency_id",
        compute="_compute_kpis",
    )
    currency_id = fields.Many2one("res.currency", default=lambda self: self.env.company.currency_id)

    recent_document_ids = fields.Many2many("ocr.document", compute="_compute_recent")

    def _compute_kpis(self):
        Document = self.env["ocr.document"]
        for record in self:
            record.invoice_count = Document.search_count([("document_type", "=", "invoice")])
            record.receipt_count = Document.search_count([("document_type", "=", "receipt")])
            record.failed_count = Document.search_count([("status", "=", "failed")])

            docs_processed = Document.search([("status", "in", ["done", "reviewed"])])
            record.total_processed_value = sum(d.total_amount or 0.0 for d in docs_processed)

    def _compute_recent(self):
        for record in self:
            record.recent_document_ids = self.env["ocr.document"].search([], order="create_date desc", limit=10)

    def action_open_invoices(self):
        return self._open_action("Invoices", [("document_type", "=", "invoice")])

    def action_open_receipts(self):
        return self._open_action("Receipts", [("document_type", "=", "receipt")])

    def action_open_failed(self):
        return self._open_action("Failed Documents", [("status", "=", "failed")])

    def _open_action(self, name, domain):
        return {
            "type": "ir.actions.act_window",
            "name": name,
            "res_model": "ocr.document",
            "view_mode": "tree,form",
            "domain": domain,
            "context": {"create": False},
        }

    @api.model
    def action_get_dashboard(self):
        res_id = self.create({}).id
        return {
            "name": "Dashboard",
            "res_model": "ocr.dashboard",
            "view_mode": "form",
            "res_id": res_id,
            "type": "ir.actions.act_window",
            "target": "current",
        }
