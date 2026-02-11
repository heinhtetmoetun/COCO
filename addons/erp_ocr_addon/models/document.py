# -*- coding: utf-8 -*-
import os
import base64
import json
import logging
import hashlib
import re

from odoo import models, fields, api, _
from odoo.exceptions import UserError

from .azure import AzureInvoiceService

_logger = logging.getLogger(__name__)


class OCRDocument(models.Model):
    _name = "ocr.document"
    _description = "OCR Document"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "create_date desc"

    # ======================================================
    # 1) SYSTEM & STATUS
    # ======================================================
    name = fields.Char(
        string="Ref",
        required=True,
        copy=False,
        readonly=True,
        default=lambda self: _("New"),
    )

    # Canonical (Azure-aligned) status field
    status = fields.Selection(
        [
            ("draft", "Draft"),
            ("processing", "Processing"),
            ("done", "Done"),
            ("reviewed", "Reviewed"),
            ("failed", "Failed"),
        ],
        string="Status",
        default="draft",
        tracking=True,
    )

    # Backward-compat alias (some views/actions may still reference `state`)
    state = fields.Selection(related="status", string="State", readonly=True)

    progress = fields.Float(string="Progress", default=0.0, readonly=True)

    # ======================================================
    # 2) FILE HANDLING
    # ======================================================
    file_filename = fields.Char(string="File Name")
    file = fields.Binary(string="File", attachment=True, required=True)
    file_sha256 = fields.Char(string="File SHA256", readonly=True, copy=False)

    # ======================================================
    # 3) DOCUMENT TYPE & HEADER (Azure-aligned)
    # ======================================================
    document_type = fields.Selection(
        [("invoice", "Invoice"), ("receipt", "Receipt")],
        string="Type",
        default="invoice",
        tracking=True,
    )

    # Canonical Azure-like fields
    invoice_id = fields.Char(string="Invoice / Receipt ID", tracking=True)
    invoice_date = fields.Date(string="Invoice / Receipt Date", tracking=True)
    due_date = fields.Date(string="Due Date")
    payment_terms = fields.Char(string="Payment Terms")

    # References: keep simple but able to store multiple labels
    reference_number = fields.Char(string="Primary Reference")
    reference_candidates = fields.Text(
        string="Reference Candidates (JSON)",
        help="Optional list of reference strings detected from the document, stored as JSON.",
        readonly=True,
    )

    confidence_score = fields.Float(string="Confidence", default=0.0)

    # ======================================================
    # 4) PARTIES (Azure-aligned naming)
    # ======================================================
    vendor_name = fields.Char(string="Vendor Name", tracking=True)
    vendor_branch_name = fields.Char(string="Branch / Head Office", tracking=True)
    vendor_tax_id = fields.Char(string="Vendor Tax ID")
    vendor_address = fields.Text(string="Vendor Address")
    vendor_phone = fields.Char(string="Vendor Phone")
    vendor_website = fields.Char(string="Vendor Website")

    customer_name = fields.Char(string="Customer Name", tracking=True)
    customer_tax_id = fields.Char(string="Customer Tax ID")
    customer_address = fields.Text(string="Customer Address")
    customer_phone = fields.Char(string="Customer Phone")

    # ======================================================
    # 5) TOTALS (Azure-aligned naming)
    # ======================================================
    currency_id = fields.Many2one(
        "res.currency",
        string="Currency",
        default=lambda self: self.env.company.currency_id,
    )
    currency_code = fields.Char(string="Currency Code", readonly=True)

    subtotal_amount = fields.Monetary(
        string="Subtotal (Excl. Tax)",
        currency_field="currency_id",
        tracking=True,
    )
    discount_amount = fields.Monetary(
        string="Discount",
        currency_field="currency_id",
        tracking=True,
    )
    vat_amount = fields.Monetary(
        string="Tax (VAT) Amount",
        currency_field="currency_id",
        tracking=True,
    )
    total_amount = fields.Monetary(
        string="Total (Incl. Tax)",
        currency_field="currency_id",
        tracking=True,
    )
    vat_base_amount = fields.Monetary(
        string="VAT Base Amount (Net)",
        currency_field="currency_id",
        help="If Azure provides TotalNet, or we can derive Net = Total - Tax.",
    )

    # ======================================================
    # 6) LOGS & META
    # ======================================================
    ocr_provider = fields.Char(
        string="OCR Provider",
        default="Azure Document Intelligence",
        readonly=True,
    )
    ocr_run_at = fields.Datetime(string="OCR Run Time", readonly=True)
    upload_date = fields.Datetime(
        string="Upload Date",
        default=fields.Datetime.now,
        readonly=True,
    )
    user_id = fields.Many2one(
        "res.users",
        string="Uploaded By",
        default=lambda self: self.env.user,
        readonly=True,
    )

    extraction_log = fields.Text(string="Extraction Log")
    extracted_text = fields.Text(string="Raw OCR Text")
    ocr_error_message = fields.Text(string="Error Message")

    # ======================================================
    # 7) RELATIONS
    # ======================================================
    line_ids = fields.One2many(
        "ocr.document.line",
        "document_id",
        string="Line Items",
    )

    # ======================================================
    # 8) ACCOUNTING LINK (kept)
    # ======================================================
    vendor_bill_id = fields.Many2one(
        "account.move",
        string="Vendor Bill",
        readonly=True,
        copy=False,
    )

    # ======================================================
    # 9) BACKWARD-COMPAT FIELD ALIASES (KEEP OLD VIEWS STABLE)
    # ======================================================
    doc_type = fields.Selection(related="document_type", string="Doc Type", readonly=True)
    document_number = fields.Char(related="invoice_id", string="Doc No", readonly=True)
    document_date = fields.Date(related="invoice_date", string="Doc Date", readonly=True)

    seller_name = fields.Char(related="vendor_name", string="Seller Name", readonly=True)
    seller_branch_name = fields.Char(related="vendor_branch_name", string="Branch / Head Office", readonly=True)
    seller_tax_id = fields.Char(related="vendor_tax_id", string="Seller Tax ID", readonly=True)
    seller_address = fields.Text(related="vendor_address", string="Seller Address", readonly=True)
    seller_phone = fields.Char(related="vendor_phone", string="Seller Phone", readonly=True)
    seller_website = fields.Char(related="vendor_website", string="Seller Website", readonly=True)

    subtotal_excl_tax = fields.Monetary(
        related="subtotal_amount",
        string="Subtotal (Excl. Tax)",
        readonly=True,
        currency_field="currency_id",
    )
    total_discount = fields.Monetary(
        related="discount_amount",
        string="Total Discount",
        readonly=True,
        currency_field="currency_id",
    )
    total_incl_tax = fields.Monetary(
        related="total_amount",
        string="Total (Incl. Tax)",
        readonly=True,
        currency_field="currency_id",
    )

    currency = fields.Many2one(related="currency_id", string="Currency", readonly=True)

    # ======================================================
    # CREATE / WRITE
    # ======================================================
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("New")) == _("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code("ocr.document") or _("New")
            if vals.get("file"):
                try:
                    raw = base64.b64decode(vals["file"])
                    vals["file_sha256"] = hashlib.sha256(raw).hexdigest()
                except Exception:
                    pass
        return super().create(vals_list)

    def write(self, vals):
        if "file" in vals:
            try:
                raw = base64.b64decode(vals["file"])
                vals["file_sha256"] = hashlib.sha256(raw).hexdigest()
            except Exception:
                pass
        return super().write(vals)

    # ======================================================
    # ACTIONS
    # ======================================================
    def action_retry(self):
        self.write(
            {
                "status": "draft",
                "ocr_error_message": False,
                "progress": 0.0,
                "extraction_log": False,
                "extracted_text": False,
                "reference_candidates": False,
            }
        )

    def action_mark_reviewed(self):
        self.write({"status": "reviewed"})

    def action_open_vendor_bill(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": "account.move",
            "res_id": self.vendor_bill_id.id,
            "view_mode": "form",
        }

    def action_create_vendor_bill(self):
        self.ensure_one()

        if self.vendor_bill_id:
            raise UserError(_("Vendor Bill already created."))

        if not self.vendor_name:
            raise UserError(_("Vendor information is missing."))

        partner = self.env["res.partner"].search(
            [("name", "=", self.vendor_name), ("supplier_rank", ">", 0)],
            limit=1,
        )

        if not partner:
            partner = self.env["res.partner"].create(
                {
                    "name": self.vendor_name,
                    "vat": self.vendor_tax_id,
                    "phone": self.vendor_phone,
                    "website": self.vendor_website,
                    "supplier_rank": 1,
                }
            )

        invoice_lines = []
        for line in self.line_ids:
            invoice_lines.append(
                (
                    0,
                    0,
                    {
                        "name": line.description or "",
                        "quantity": line.quantity or 1.0,
                        "price_unit": line.unit_price or 0.0,
                    },
                )
            )

        if not invoice_lines:
            raise UserError(_("No line items found."))

        bill = self.env["account.move"].create(
            {
                "move_type": "in_invoice",
                "partner_id": partner.id,
                "invoice_date": self.invoice_date,
                "invoice_origin": self.name,
                "invoice_line_ids": invoice_lines,
            }
        )

        self.vendor_bill_id = bill.id

        return {
            "type": "ir.actions.act_window",
            "res_model": "account.move",
            "res_id": bill.id,
            "view_mode": "form",
        }

    # ======================================================
    # OCR LOGIC (Azure)
    # ======================================================
    def _detect_currency(self, code_from_azure):
        if code_from_azure:
            cur = self.env["res.currency"].search([("name", "=", code_from_azure)], limit=1)
            if cur:
                return cur, code_from_azure

        thb = self.env["res.currency"].with_context(active_test=False).search([("name", "=", "THB")], limit=1)
        if thb:
            return thb, "THB"

        return self.env.company.currency_id, self.env.company.currency_id.name

    def _extract_reference_candidates(self, raw_text):
        if not raw_text:
            return []

        patterns = [
            r"\b(?:PO|P\.O\.|Purchase\s*Order)\s*[:#-]?\s*([A-Z0-9][A-Z0-9\-/]{3,})",
            r"\b(?:Order\s*No\.?|Order\s*#)\s*[:#-]?\s*([A-Z0-9][A-Z0-9\-/]{3,})",
            r"\b(?:Ref\.?|Reference)\s*(?:No\.?|#)?\s*[:#-]?\s*([A-Z0-9][A-Z0-9\-/]{3,})",
            r"\b(?:Invoice\s*No\.?|Inv\.?\s*No\.?)\s*[:#-]?\s*([A-Z0-9][A-Z0-9\-/]{3,})",
        ]
        found = []
        for pat in patterns:
            for m in re.finditer(pat, raw_text, flags=re.IGNORECASE):
                v = (m.group(0) or "").strip()
                if v and v not in found:
                    found.append(v)

        return found[:15]

    def action_run_ocr(self):
        self.ensure_one()

        endpoint = os.environ.get("AZURE_FORM_ENDPOINT")
        key = os.environ.get("AZURE_FORM_KEY")
        if not endpoint or not key:
            raise UserError(_("Azure Settings Missing (AZURE_FORM_ENDPOINT / AZURE_FORM_KEY)."))

        self.write({"status": "processing", "progress": 10.0})
        self.env.cr.commit()

        try:
            service = AzureInvoiceService(endpoint, key)
            data = service.analyze(base64.b64decode(self.file))
            if not data:
                raise UserError(_("No data returned from Azure."))

            currency, currency_code = self._detect_currency(data.get("currency_code"))
            subtotal = data.get("vat_base_amount") or data.get("subtotal_amount")

            raw_text = data.get("raw_text") or ""
            ref_candidates = self._extract_reference_candidates(raw_text)
            primary_ref = data.get("reference_number") or (ref_candidates[0] if ref_candidates else False)

            self.write(
                {
                    "status": "done",
                    "progress": 100.0,
                    "ocr_run_at": fields.Datetime.now(),
                    "extracted_text": json.dumps(data, indent=4, ensure_ascii=False, default=str),
                    "ocr_error_message": False,

                    "currency_id": currency.id,
                    "currency_code": currency_code,

                    "confidence_score": float(data.get("confidence_score") or 0.0),

                    "vendor_name": data.get("vendor_name"),
                    "vendor_branch_name": data.get("vendor_branch_name"),
                    "vendor_tax_id": data.get("vendor_tax_id"),
                    "vendor_address": data.get("vendor_address"),
                    "vendor_phone": data.get("vendor_phone"),
                    "vendor_website": data.get("vendor_website"),

                    "customer_name": data.get("customer_name"),
                    "customer_tax_id": data.get("customer_tax_id"),
                    "customer_address": data.get("customer_address"),
                    "customer_phone": data.get("customer_phone"),

                    "invoice_id": data.get("invoice_id"),
                    "invoice_date": data.get("invoice_date"),
                    "due_date": data.get("due_date"),
                    "payment_terms": data.get("payment_terms"),

                    "reference_number": primary_ref,
                    "reference_candidates": json.dumps(ref_candidates, ensure_ascii=False),

                    "vat_base_amount": data.get("vat_base_amount"),
                    "subtotal_amount": subtotal,
                    "discount_amount": data.get("discount_amount"),
                    "vat_amount": data.get("vat_amount"),
                    "total_amount": data.get("total_amount"),
                }
            )

            self.line_ids.unlink()
            lines = []
            for item in data.get("items", []) or []:
                lines.append(
                    (
                        0,
                        0,
                        {
                            "description": item.get("description"),
                            "product_code": item.get("product_code"),
                            "quantity": item.get("quantity", 1.0),
                            "unit_price": item.get("unit_price", 0.0),
                        },
                    )
                )
            if lines:
                self.write({"line_ids": lines})

        except Exception as e:
            _logger.exception("OCR Error")
            self.write(
                {
                    "status": "failed",
                    "progress": 0.0,
                    "ocr_error_message": str(e),
                }
            )
