import unittest

from mamentor import content_to_html


class MamentorFormatTest(unittest.TestCase):
    def test_plain_text_newlines_become_html_breaks(self):
        self.assertEqual(content_to_html("line 1\nline 2"), "line 1<br>line 2")

    def test_windows_newlines_become_single_html_breaks(self):
        self.assertEqual(
            content_to_html("line 1\r\nline 2\r\nline 3"),
            "line 1<br>line 2<br>line 3",
        )

    def test_existing_html_is_preserved(self):
        self.assertEqual(content_to_html("line 1<br>line 2"), "line 1<br>line 2")


if __name__ == "__main__":
    unittest.main()
