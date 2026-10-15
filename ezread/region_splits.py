"""Partition partially covered source blocks by PDF glyph positions, additively."""
from collections import Counter
import unicodedata
from .text_actions import digest


def contains(outer, inner):
    return all((inner[0] >= outer[0] - 1e-6, inner[1] >= outer[1] - 1e-6,
                inner[2] <= outer[2] + 1e-6, inner[3] <= outer[3] + 1e-6))


def canonical(text):
    return Counter(c for c in unicodedata.normalize('NFKC', text) if not c.isspace() and c not in '-\u00ad')


def partition(block, lines, regions, width, height, peers=()):
    """Every source glyph belongs to exactly one region or one outside fragment."""
    import pdf_tools
    bounds = block['bbox']
    groups, inside, all_chars = [], {r['id']: [] for r in regions}, []
    for line in lines:
        runs = []
        for c in sorted(line['chars'], key=lambda c: c['x0']):
            x, y = (c['x0'] + c['x1']) / (2*width), (c['top'] + c['bottom']) / (2*height)
            if not (bounds[0] - 1e-6 <= x <= bounds[2] + 1e-6 and bounds[1] - 1e-6 <= y <= bounds[3] + 1e-6):
                continue
            area = lambda b: (b[2]-b[0])*(b[3]-b[1])
            if any(other['id'] != block['id'] and area(other['bbox']) < area(bounds)
                   and other['bbox'][0] <= x <= other['bbox'][2] and other['bbox'][1] <= y <= other['bbox'][3]
                   for other in peers):
                continue
            all_chars.append(c)
            owner = next((r for r in regions if r['bbox'][0] <= x <= r['bbox'][2] and r['bbox'][1] <= y <= r['bbox'][3]), None)
            if owner:
                inside[owner['id']].append(c)
                continue
            # The side of every crop separates disconnected outside areas.
            side = tuple('top' if y < r['bbox'][1] else 'bottom' if y > r['bbox'][3]
                         else 'left' if x < r['bbox'][0] else 'right' for r in regions)
            if not runs or runs[-1][0] != side:
                runs.append((side, []))
            runs[-1][1].append(c)
        for side, chars in runs:
            box = pdf_tools._union([pdf_tools._box(c) for c in chars])
            last = next((g for g in reversed(groups) if g['side'] == side), None)
            if (last and box[1] > last['last'][1] + 1 and box[1] - last['last'][3] < max(c['size'] for c in chars)*1.8
                    and min(box[2], last['last'][2]) > max(box[0], last['last'][0])):
                last['lines'].append(chars); last['last'] = box
            else:
                groups.append({'side': side, 'lines': [chars], 'last': box})
    if not any(inside.values()):
        return [], {key: '' for key in inside}
    source = block.get('source_text') or block.get('text', '')
    if not source.startswith('[公式') and canonical(source) != canonical(''.join(c['text'] for c in all_chars)):
        raise ValueError('PDF 文字与已保存段落无法完整对应，未改动原文、译文和批注。')
    fragments = []
    for group in groups:
        chars = [c for line in group['lines'] for c in line]
        text = '\n'.join(pdf_tools._line_text(line) for line in group['lines'])
        box = pdf_tools._norm(pdf_tools._union([pdf_tools._box(c) for c in chars]), width, height)
        key = digest(str((block['id'], box, text)))[:24]
        fragments.append({'id': 'region-text-' + key, 'page': block['page'], 'bbox': box, 'text': text,
                          'kind': 'paragraph', 'region_split_parent': block['id'], 'source_parent_ids': [block['id']]})
    return fragments, {key: ''.join(c['text'] for c in chars) for key, chars in inside.items()}


def plan(doc, regions, pdf_path, changed_ids=None):
    """Repartition against all active crops, so multiple crops cannot discard tails."""
    from .formulas import intersection
    import pdfplumber
    import pdf_tools
    for region in regions:
        region.pop('split_sources', None)
    hidden = {bid for repair in doc.get('paragraph_repairs', []) for bid in repair['source_ids']}
    candidates = []
    previous = {s['parent_id'] for s in doc.get('region_text_splits', [])}
    for block in doc.get('blocks', []):
        if block.get('region_split_parent') or block['id'] in hidden or not block.get('bbox'):
            continue
        overlaps = [r for r in regions if r.get('region_kind', 'formula') in ('figure', 'table', 'formula', 'algorithm')
                    and r['page'] == block['page'] and intersection(r['bbox'], block['bbox']) > 0]
        affected = changed_ids is None or block['id'] in previous or any(r['id'] in changed_ids for r in overlaps)
        if affected and overlaps and not any(contains(r['bbox'], block['bbox']) for r in overlaps):
            candidates.append((block, overlaps))
    records, fragments = [], []
    if not candidates:
        return records, fragments
    with pdfplumber.open(pdf_path) as pdf:
        pages = {}
        for block, overlaps in candidates:
            number = block['page']
            if number not in pages:
                page = pdf.pages[number - 1]
                pages[number] = (pdf_tools._lines(page), page.width, page.height)
            lines, width, height = pages[number]
            parts, inside = partition(block, lines, overlaps, width, height, [b for b in doc['blocks'] if b.get('page') == number and b.get('bbox') and not b.get('region_split_parent') and b['id'] not in hidden])
            if not any(inside.values()):
                continue
            fragments.extend(parts)
            records.append({'parent_id': block['id'], 'fragment_ids': [p['id'] for p in parts],
                            'region_ids': [r['id'] for r in overlaps]})
            for region in overlaps:
                region['block_ids'] = [bid for bid in region['block_ids'] if bid != block['id']]
                region.setdefault('split_sources', {})[block['id']] = inside[region['id']]
    return records, fragments
