# -*- coding: utf-8 -*-
{
    "name": "ERP OCR Addon",
    "summary": "Upload invoices/receipts → OCR extraction → auto-fill Accounting & Expenses.",
    "version": "1.0.4",
    "category": "Tools",
    "author": "Your Name",
    "website": "https://www.example.com",
    "license": "LGPL-3",

    "depends": [
        "base",
        "account",
        "hr_expense"
    ],

    "data": [
    "security/ir.model.access.csv",
    "views/ocr_invoice_views.xml",
    "views/menus.xml",
    ],

    "installable": True,
    "application": True,
}
