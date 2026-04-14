import unittest

from invoice_app import invoice_summary, total


class InvoiceDiscountTest(unittest.TestCase):
    def test_total_keeps_original_no_discount_call(self) -> None:
        self.assertEqual(total([100.0, 50.0]), 150.0)

    def test_total_applies_discount_percent(self) -> None:
        self.assertEqual(total([100.0, 50.0], discount_percent=10), 135.0)

    def test_invoice_summary_keeps_original_no_discount_call(self) -> None:
        self.assertEqual(invoice_summary([100.0, 50.0]), "Total: $150.00")

    def test_invoice_summary_reports_discounted_total(self) -> None:
        self.assertEqual(
            invoice_summary([100.0, 50.0], discount_percent=10),
            "Total after discount: $135.00",
        )


if __name__ == "__main__":
    unittest.main()
