"""Versioned, publication-independent evidence rules for PDF reading."""
import re

VERSION = 1
EXTRACTION_VERSION = 5
IDENTIFIER = r'(?:[A-Z]?\d+(?:\.\d+)*|[IVXLCDM]+)'
FIGURE_START = re.compile(r'^(?:Fig(?:ure)?\.?|图)\s*' + IDENTIFIER + r'(?=[\s.:：、—–]|$)', re.I)
TABLE_START = re.compile(r'^(?:Table|Tab\.?|表)\s*' + IDENTIFIER + r'(?=[\s.:：、—–]|$)', re.I)
REFERENCE_HEADING = re.compile(r'^(?:References|Bibliography|Literature cited|参考文献)\s*[:：]?$', re.I)
REFERENCE_END = re.compile(r'^(?:Appendix|Appendices|Acknowledgements|Acknowledgments|Author contributions|Competing interests|Additional information|Publisher.s note|附录|致谢)\b', re.I)


def reference_start(text):
    """Only use within bibliography; an in-text author/year is not a new entry."""
    return bool(re.match(r'^(?:\[\d+\]|\d+[.)])\s*\S', text) or
                re.match(r'^[A-ZÀ-Ž][^!?]{2,120}\(\d{4}[a-z]?\)', text))


def audit(doc):
    """Report concrete ambiguity, without discarding content or inventing OCR."""
    blocks = [b for b in doc.get('blocks', []) if not b.get('formula_continuation')]
    issues = []
    def add(code, block=None):
        issues.append({'code': code, 'block_id': block.get('id') if block else None,
                       'page': block.get('page') if block else None})
    if doc.get('extraction', {}).get('version', 0) < EXTRACTION_VERSION:
        add('legacy-extraction')
    if not blocks:
        add('no-selectable-text')
    for b in blocks:
        if b.get('is_table') and not b.get('table_cells'):
            add('table-grid-unconfirmed', b)
        if b.get('is_formula') and not (b.get('image') or b.get('image_url')):
            add('equation-source-missing', b)
        if b.get('has_unparsed_math'):
            add('inline-symbol-unconfirmed', b)
        if TABLE_START.match(b.get('text', '')) and not b.get('is_table'):
            add('table-caption-without-region', b)
        if not b.get('is_formula') or not b.get('bbox'):
            continue
        for figure in doc.get('figures', []):
            if figure.get('page') != b.get('page') or not figure.get('bbox'):
                continue
            a, q = b['bbox'], figure['bbox']
            overlap = max(0, min(a[2], q[2]) - max(a[0], q[0])) * max(0, min(a[3], q[3]) - max(a[1], q[1]))
            area = max(1e-9, (a[2] - a[0]) * (a[3] - a[1]))
            if overlap / area > .25:
                add('figure-equation-overlap', b)
                break
    return {'rules_version': VERSION, 'extraction_version': doc.get('extraction', {}).get('version'),
            'status': 'needs-review' if issues else 'checked', 'issues': issues}
