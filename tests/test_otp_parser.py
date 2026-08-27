import unittest
import email
from seezar_operator.utils.otp_fetcher import GmailOTPClient


class TestOTPParser(unittest.TestCase):

    def test_contextual_otp_extraction(self):
        sample_texts = [
            ("Your Seezar verification code is 849201. Please do not share this code.", "849201"),
            ("Seezar Login Security Code: 491028. Valid for 10 minutes.", "491028"),
            ("Please enter 593821 to log in to your Seezar dashboard.", "593821"),
            ("Here is your one-time passcode: 739102", "739102"),
            ("OTP: 192837", "192837"),
            ("Email Confirmation - OTP: EB5F80", "EB5F80"),
            ("Welcome. Your one-time code is EB5F80", "EB5F80"),
            ("Your confirmation PIN is 482910.", "482910"),
            ("Use 384910 as your verification code.", "384910"),
            ("Code is 1234", "1234"),
            ("Your 6-digit OTP code is 987654.", "987654"),
        ]

        for text, expected in sample_texts:
            with self.subTest(text=text):
                extracted = GmailOTPClient._extract_otp_code(text)
                self.assertEqual(extracted, expected, f"Failed to extract '{expected}' from '{text}'")

    def test_html_email_extraction(self):
        html_payload = """
        <!DOCTYPE html>
        <html>
        <body>
            <div style="font-family: Arial;">
                <h2>Welcome to Seezar Dashboard</h2>
                <p>You requested a login verification code.</p>
                <div style="font-size: 24px; font-weight: bold; color: #06b6d4;">
                    629401
                </div>
                <p>This code will expire in 2 minutes.</p>
            </div>
        </body>
        </html>
        """
        clean_text = GmailOTPClient._extract_body_text(
            email.message_from_string(
                f"Content-Type: text/html; charset=utf-8\n\n{html_payload}"
            )
        )
        extracted = GmailOTPClient._extract_otp_code(clean_text)
        self.assertEqual(extracted, "629401")

    def test_excludes_years_and_screen_dimensions(self):
        text = "Seezar Operator Copyright 2026. Browser resolution 1920x1080. Your code is 748291."
        extracted = GmailOTPClient._extract_otp_code(text)
        self.assertEqual(extracted, "748291")


if __name__ == "__main__":
    unittest.main()
