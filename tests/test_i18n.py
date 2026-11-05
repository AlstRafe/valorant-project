"""Every text the pages show has its Korean, English and Chinese (the owner's own pages excepted)."""

import ast
import re
import unittest
from pathlib import Path

from valtracker import i18n
from valtracker.locale import OWNER_ONLY, TEXTS
from valtracker.views import FAILURES, QUEUES

PACKAGE = Path(__file__).resolve().parent.parent / "valtracker"
#: The owner's pages: Japanese only.
OWNER_PAGES = {"settings.html", "collection.html"}


def used_texts() -> set[str]:
    found = set()
    for template in (PACKAGE / "templates").glob("*.html"):
        if template.name in OWNER_PAGES:
            continue
        found.update(re.findall(r"\bt\('((?:[^'\\]|\\.)*)'", template.read_text(encoding="utf-8")))
    for module in PACKAGE.glob("*.py"):
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", "")) in (
                "_t",
                "text",
            ):
                arguments = [arg for arg in node.args if isinstance(arg, ast.Constant) and isinstance(arg.value, str)]
                found.update(arg.value for arg in arguments if re.search(r"[ぁ-んァ-ヶ一-龯]", arg.value))
    found.update(QUEUES.values())
    found.update(text for _, visitor, owner in FAILURES.values() for text in (visitor, owner) if text)
    found.update(("自動", "オフ", "オン"))
    return found - OWNER_ONLY


class TranslationsTest(unittest.TestCase):
    def test_every_text_is_translated(self):
        missing = {
            text: [lang for lang in i18n.LANGS[1:] if not TEXTS.get(text, {}).get(lang)]
            for text in sorted(used_texts())
        }
        missing = {text: langs for text, langs in missing.items() if langs}
        self.assertEqual(missing, {}, "訳のない文言")

    def test_placeholders_match(self):
        for ja, translations in TEXTS.items():
            for lang, said in translations.items():
                with self.subTest(ja=ja, lang=lang):
                    self.assertEqual(set(re.findall(r"{\w+}", said)), set(re.findall(r"{\w+}", ja)))

    def test_no_unused_translations(self):
        self.assertEqual(sorted(set(TEXTS) - used_texts()), [], "使われていない訳")


class ChoiceTest(unittest.TestCase):
    def test_cookie_wins(self):
        self.assertEqual(i18n.chosen({"HTTP_COOKIE": "lang=ko", "HTTP_ACCEPT_LANGUAGE": "en"}), "ko")

    def test_browser(self):
        self.assertEqual(i18n.from_browser("zh-CN,zh;q=0.9"), "zh-TW")
        self.assertEqual(i18n.from_browser("en-US"), "en-GB")
        self.assertEqual(i18n.from_browser("fr-FR"), "en-GB")
        self.assertEqual(i18n.from_browser(""), "ja")
        self.assertEqual(i18n.from_browser("fr;q=1, ko;q=0.5"), "ko")

    def test_a_bad_cookie_is_no_choice(self):
        self.assertEqual(i18n.chosen({"HTTP_COOKIE": "lang=xx"}), "ja")

    def test_missing_text_is_japanese(self):
        self.assertEqual(i18n.text("ko", "どこにもない文"), "どこにもない文")


if __name__ == "__main__":
    unittest.main()
