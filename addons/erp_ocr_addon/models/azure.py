# -*- coding: utf-8 -*-
from azure.core.credentials import AzureKeyCredential
from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.ai.documentintelligence.models import AnalyzeDocumentRequest
import logging
import re

_logger = logging.getLogger(__name__)


class AzureInvoiceService:
    """
    Wrapper for Azure Document Intelligence (prebuilt-invoice)

    Design principles:
    - Azure-first: prefer structured fields returned by Azure.
    - Safe fallbacks: ONLY derive missing fields from raw_text with strict rules.
    - Thai-friendly: supports common Thai receipt/invoice patterns (phone, branch code, website).
    """

    def __init__(self, endpoint: str, key: str):
        self.client = DocumentIntelligenceClient(
            endpoint=endpoint,
            credential=AzureKeyCredential(key)
        )

    # ======================================================
    # NORMALIZATION HELPERS
    # ======================================================
    def _normalize_phone(self, s: str) -> str:
        if not s:
            return ""
        s = s.strip()
        s = re.sub(r"[ \t\r\n\-\(\)\.]", "", s)
        return s

    def _normalize_website(self, s: str) -> str:
        if not s:
            return ""
        s = s.strip()
        # remove trailing punctuation that OCR often includes
        s = s.rstrip(".,;:)")
        return s

    # ======================================================
    # SAFE FALLBACK EXTRACTORS (raw_text)
    # ======================================================
    def _extract_phone_from_text(self, text: str) -> str | None:
        """
        Extract phone numbers ONLY near phone labels.
        Prevents invoice numbers / dates from being mistaken as phones.
        """
        if not text:
            return None

        # Label-based extraction (BEST & SAFEST)
        label_patterns = [
            r"(?:เบอร์โทร|เบอรโทร|โทรศัพท์|โทร|Tel\.?|Telephone|Phone)"
            r"\s*[:\|]?\s*([+0-9][0-9 \-\(\)\.]{7,})"
        ]

        for pat in label_patterns:
            m = re.search(pat, text, flags=re.IGNORECASE)
            if m:
                cand = self._normalize_phone(m.group(1))

                # Thai phone rules
                if cand.startswith("+66") and 9 <= len(cand) <= 11:
                    return cand
                if cand.startswith("0") and 9 <= len(cand) <= 10:
                    return cand

        # Secondary scan (still strict)
        # Accept ONLY Thai-looking numbers, not generic digits
        loose = re.findall(r"(\+66[0-9]{8,9}|0[0-9]{8,9})", text)
        for cand in loose:
            cand = self._normalize_phone(cand)
            if cand.startswith("+66") or cand.startswith("0"):
                return cand

        return None

    def _extract_vendor_website_from_text(self, text: str) -> str | None:
        """
        Website is not guaranteed to be returned by Azure.
        Fallback: extract ONLY when preceded by Website/เว็บไซต์ label.
        """
        if not text:
            return None
        m = re.search(
            r"(Website|เว็บไซต์)\s*[:\-]?\s*(https?://\S+|www\.[^\s]+)",
            text,
            flags=re.IGNORECASE,
        )
        if not m:
            return None
        return self._normalize_website(m.group(2))

    def _extract_branch_from_text(self, text: str) -> str | None:
        """
        Azure prebuilt-invoice does NOT provide branch code as a first-class field.
        Thai receipts often contain 'รหัสสาขา 00014' or similar.
        We return a compact, UI-friendly string like 'Branch 00014'.
        """
        if not text:
            return None

        # Thai: รหัสสาขา 00014
        m = re.search(r"(รหัสสาขา)\s*[:\-]?\s*(\d{4,6})", text)
        if m:
            return f"Branch {m.group(2)}"

        # English: Branch 00014
        m = re.search(r"(?:Branch)\s*[:\-]?\s*(\d{4,6})", text, flags=re.IGNORECASE)
        if m:
            return f"Branch {m.group(1)}"

        return None

    # ======================================================
    # MAIN ANALYSIS
    # ======================================================
    def analyze(self, file_bytes: bytes) -> dict:
        poller = self.client.begin_analyze_document(
            "prebuilt-invoice",
            AnalyzeDocumentRequest(bytes_source=file_bytes),
        )
        result = poller.result()

        raw_text = getattr(result, "content", None) or ""

        if not result.documents:
            return {"raw_text": raw_text}

        doc = result.documents[0]
        fields = doc.fields or {}

        # ==================================================
        # HELPER FUNCTIONS
        # ==================================================
        def fget(name):
            return fields.get(name)

        def get_str(name):
            f = fget(name)
            return f.value_string if f else None

        def get_date(name):
            f = fget(name)
            return f.value_date if f else None

        def get_currency(name):
            f = fget(name)
            if not f or not f.value_currency:
                return (None, None)
            cur = f.value_currency
            return (cur.amount, getattr(cur, "currency_code", None))

        def get_first_str(candidates):
            for key in candidates:
                v = get_str(key)
                if v:
                    return v
            return None

        def format_address(field_name):
            f = fget(field_name)
            if not f:
                return None

            if f.value_address:
                addr = f.value_address
                parts = []
                if getattr(addr, "house_number", None):
                    parts.append(f"เลขที่ {addr.house_number}")
                if getattr(addr, "road", None):
                    parts.append(addr.road)
                if getattr(addr, "city_district", None):
                    parts.append(f"เขต{addr.city_district}")
                if getattr(addr, "city", None):
                    parts.append(addr.city)
                if getattr(addr, "postal_code", None):
                    parts.append(addr.postal_code)
                if getattr(addr, "country_region", None):
                    parts.append(addr.country_region)

                joined = " ".join(p for p in parts if p).strip()
                if joined:
                    return joined

            return f.content if f.content else None

        # ==================================================
        # EXTRACTION (Azure-first, safe fallbacks)
        # ==================================================

        # --- Parties ---
        vendor_name = get_str("VendorName")
        customer_name = get_str("CustomerName")

        vendor_tax_id = get_first_str(["VendorTaxId", "TaxId", "VendorVATNumber"])
        customer_tax_id = get_first_str(["CustomerTaxId", "CustomerVATNumber"])

        # --- Phones ---
        vendor_phone = (
            get_first_str(["VendorPhoneNumber", "VendorPhone"])
            or self._extract_phone_from_text(raw_text)
        )
        customer_phone = get_first_str(["CustomerPhoneNumber", "CustomerPhone"])

        # --- Website (Azure first, then strict label-based regex) ---
        vendor_website = get_first_str(["VendorWebsite", "Website"])
        if not vendor_website:
            vendor_website = self._extract_vendor_website_from_text(raw_text)
        if vendor_website:
            vendor_website = self._normalize_website(vendor_website)

        # --- Branch (derived; not an Azure native field) ---
        vendor_branch_name = self._extract_branch_from_text(raw_text)

        # --- Addresses ---
        vendor_address = format_address("VendorAddress")
        customer_address = format_address("CustomerAddress")

        # --- Document Info ---
        # IMPORTANT: invoice_id should NOT fallback to ReferenceNumber (that causes confusion)
        invoice_id = get_first_str(["InvoiceId", "InvoiceNumber", "ReceiptNumber"])
        reference_number = get_first_str(["ReferenceNumber", "PurchaseOrder", "Reference"])

        invoice_date = get_date("InvoiceDate") or get_date("Date")
        due_date = get_date("DueDate")
        payment_terms = get_str("PaymentTerm") or get_str("PaymentTerms")

        # --- Financials & Currency ---
        subtotal, c1 = get_currency("SubTotal")
        discount, c2 = get_currency("TotalDiscount")
        vat, c3 = get_currency("TotalTax")
        total, c4 = get_currency("InvoiceTotal")
        currency_code = c4 or c3 or c2 or c1

        # --- VAT Base Calculation ---
        vat_base_amount = None
        net_amount, _net_ccy = get_currency("TotalNet")
        if net_amount is not None:
            vat_base_amount = net_amount
        elif total is not None and vat is not None:
            try:
                vat_base_amount = round(float(total) - float(vat), 2)
            except Exception:
                vat_base_amount = None

        # --- Line Items ---
        items = []
        items_field = fget("Items")
        if items_field and items_field.value_array:
            for item in items_field.value_array:
                obj = item.value_object
                if not obj:
                    continue

                def l_str(k):
                    return obj.get(k).value_string if obj.get(k) else ""

                def l_num(k):
                    return obj.get(k).value_number if obj.get(k) else 1.0

                def l_money(k):
                    return (
                        obj.get(k).value_currency.amount
                        if obj.get(k) and obj.get(k).value_currency
                        else 0.0
                    )

                amount = l_money("Amount")
                if not amount:
                    amount = l_money("LineTotal") or l_money("TotalPrice")

                items.append({
                    "description": l_str("Description"),
                    "product_code": l_str("ProductCode"),
                    "quantity": l_num("Quantity"),
                    "unit_price": l_money("UnitPrice"),
                    "amount": amount,
                })

        confidence = doc.confidence if hasattr(doc, "confidence") else 0.9

        return {
            "raw_text": raw_text,
            "confidence_score": confidence,
            "currency_code": currency_code,

            # Azure-aligned canonical fields
            "invoice_id": invoice_id,
            "reference_number": reference_number,
            "invoice_date": invoice_date,
            "due_date": due_date,
            "payment_terms": payment_terms,

            "vendor_name": vendor_name,
            "vendor_branch_name": vendor_branch_name,
            "vendor_tax_id": vendor_tax_id,
            "vendor_address": vendor_address,
            "vendor_phone": vendor_phone,
            "vendor_website": vendor_website,

            "customer_name": customer_name,
            "customer_tax_id": customer_tax_id,
            "customer_address": customer_address,
            "customer_phone": customer_phone,

            "subtotal_amount": subtotal,
            "discount_amount": discount,
            "vat_amount": vat,
            "total_amount": total,
            "vat_base_amount": vat_base_amount,

            "items": items,
        }
