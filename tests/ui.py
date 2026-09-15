"""The shared shell, as tests see it.

Every page draws its heading from one partial, so the markup that used to be
copied into each template — and asserted in each test — lives in exactly one
place now. A test that cares that a page *has* a heading asserts that it composes
the partial; the partial's own contents are asserted once, here.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "templates"

HEADER = (TEMPLATES / "partials" / "page_header.html").read_text()
BREADCRUMB = (TEMPLATES / "partials" / "breadcrumb.html").read_text()

HEADER_INCLUDE = "{% include 'partials/page_header.html' %}"
BREADCRUMB_INCLUDE = "{% include 'partials/breadcrumb.html' %}"

# Where the shared markup lives, for tests that want to read a page's own file.
PAGE_HEADING = "page-heading product-page-heading"
PAGE_FORM_HEADING = "page-heading product-form-heading"


def composes_header(*pages: str) -> bool:
    """Whether each page draws the one shared header."""
    return all(HEADER_INCLUDE in page for page in pages)
