"""Locate source regions before paragraph ordering; never infer missing content."""
import re
import statistics
import layout_rules


def contains(box, item):
    x, y = (item[0] + item[2]) / 2, (item[1] + item[3]) / 2
    return box[0] <= x <= box[2] and box[1] <= y <= box[3]


def table_regions(page, lines):
    """Caption plus aligned horizontal rules bounds a ruled table, not a column."""
    result = []
    captions = [l for l in lines if layout_rules.TABLE_START.match(l['text'])]
    edges = sorted(page.horizontal_edges, key=lambda e: e['top'])
    for caption in captions:
        cb = caption['bbox']
        tops = [e for e in edges if 0 <= e['top'] - cb[3] < 24
                and abs(e['x0'] - cb[0]) < 12 and e['x1'] - e['x0'] > page.width * .25]
        if not tops:
            continue
        top = tops[0]
        stop = min([c['bbox'][1] for c in captions if c['bbox'][1] > cb[3]] + [page.height - 20])
        rules = []
        for edge in edges:
            if (top['top'] <= edge['top'] < stop and abs(edge['x0'] - top['x0']) < 3
                    and abs(edge['x1'] - top['x1']) < 3):
                if not rules or edge['top'] - rules[-1] > 2:
                    rules.append(edge['top'])
        if len(rules) < 3 or rules[-1] - rules[0] > page.height * .55:
            continue
        box = [top['x0'] - 1, cb[1] - 1, top['x1'] + 1, rules[-1] + 2]
        # Include a nearby small-font table footnote, but never the next prose paragraph.
        foot = [l for l in lines if 0 <= l['bbox'][1] - rules[-1] < 14
                and l['size'] < caption['size'] * .95 and l['bbox'][0] >= box[0]
                and l['bbox'][2] <= box[2]]
        if foot:
            box[3] = max(box[3], max(l['bbox'][3] for l in foot) + 1)
        members = [l for l in lines if contains(box, l['bbox'])]
        grid = source_grid(page, top['x0'], top['x1'], rules[0], rules[-1], edges)
        result.append({'text': caption['text'], 'kind': 'caption', 'bbox': box,
                       '_lines': members, 'is_formula': False, 'is_table': True,
                       'table_note': ' '.join(l['text'] for l in sorted(foot, key=lambda l: (l['bbox'][1], l['bbox'][0]))),
                       'table_cells': grid, 'table_status': 'source-grid' if grid else 'needs-review'})
    return result


def source_grid(page, left, right, top, bottom, edges):
    """Keep physical rows and columns, including empty cells. Reject ambiguous grids."""
    crop = page.crop((left, top, right, bottom))
    # Prefer actual vertical borders; otherwise use the gaps between header underlines.
    vertical = sorted({round(e['x0'], 1) for e in page.vertical_edges
                       if top <= e['top'] < bottom and e['bottom'] - e['top'] > (bottom - top) * .95
                       and left <= e['x0'] <= right})
    if len(vertical) >= 3:
        boundaries = sorted(set([left, right] + vertical))
        boundaries = [x for i, x in enumerate(boundaries) if i == 0 or x - boundaries[i - 1] > 2]
    else:
        short = [e for e in edges if top + 3 < e['top'] < min(top + 40, bottom)
                 and left - 1 <= e['x0'] and e['x1'] <= right + 1
                 and 15 < e['x1'] - e['x0'] < (right - left) * .8]
        if not short:
            return []
        y = min(e['top'] for e in short)
        spans = sorted({(round(e['x0'], 1), round(e['x1'], 1)) for e in short if abs(e['top'] - y) < 2})
        if len(spans) < 2 or any(a[1] > b[0] for a, b in zip(spans, spans[1:])):
            return []
        boundaries = [left] + [(a[1] + b[0]) / 2 for a, b in zip(spans, spans[1:])] + [right]
    chars = [c for c in crop.chars if c['text'].strip()]
    if not chars:
        return []
    # A spanning header (e.g. force/error) can cover multiple leaf columns.
    # Accept only whitespace corridors shared by every data row, never split
    # through a value. Keep the parent header in each leaf heading below.
    size = statistics.median(c['size'] for c in chars)
    rows = []
    for char in sorted(chars, key=lambda c: ((c['top'] + c['bottom']) / 2, c['x0'])):
        cy = (char['top'] + char['bottom']) / 2
        if not rows or abs(cy - rows[-1][0]) > size * .55:
            rows.append([cy, []])
        rows[-1][1].append(char)
    from pdf_tools import _line_text
    # Only fully ruled rows are safely reconstructable without semantic inference.
    full = sorted({e['top'] for e in edges if top <= e['top'] <= bottom + 1
                   and abs(e['x0'] - left) < 2 and abs(e['x1'] - right) < 2})
    full = [y for i, y in enumerate(full) if i == 0 or y - full[i - 1] > 2]
    if len(full) < 3:
        return []
    if any(c['x0'] < x - .5 and c['x1'] > x + .5 for c in chars for x in boundaries[1:-1]):
        return []
    grid = []
    if len(vertical) < 3 and len(full) < len(rows) + 1:
        # Three-rule tables: header is multiline, each body row must have a
        # populated first column. Never turn arbitrary physical lines into rows.
        header_end = full[1]
        header = [row for y, row in rows if y < header_end]
        body = [row for y, row in rows if y >= header_end]
        if not body:
            return []
        data_rows = []
        for row in body:
            first = [c for c in row if boundaries[0] <= (c['x0'] + c['x1']) / 2 < boundaries[1]]
            # Flush-left row labels start records; indented continuation lines
            # remain in that record (author citations and wrapped cell text).
            starts = first and min(c['x0'] for c in first) <= left + 4
            if starts:
                data_rows.append([row])
            elif data_rows:
                data_rows[-1].append(row)
            else:
                return []
        if len(data_rows) < 2:
            return []
        extra = []
        for a, b in zip(boundaries, boundaries[1:]):
            intervals = sorted((c['x0'], c['x1']) for row in body for c in row
                               if a <= (c['x0'] + c['x1']) / 2 < b)
            merged = []
            for x0, x1 in intervals:
                if merged and x0 <= merged[-1][1] + 1:
                    merged[-1][1] = max(merged[-1][1], x1)
                else:
                    merged.append([x0, x1])
            for first, second in zip(merged, merged[1:]):
                if second[0] - first[1] >= 10:
                    x = (first[1] + second[0]) / 2
                    if all(any(a <= c['x0'] < x for row in group for c in row)
                           and any(x < c['x1'] <= b for row in group for c in row) for group in data_rows):
                        extra.append(x)
        if extra:
            original_boundaries = boundaries
            boundaries = sorted(boundaries + extra)
            # Split header physical lines by large gaps first so a parent title
            # spanning the new boundary is repeated intact, not cut mid-word.
            header_cells = []
            for a, b in zip(boundaries, boundaries[1:]):
                parent_a = max(x for x in original_boundaries if x <= a)
                parent_b = min(x for x in original_boundaries if x >= b)
                pieces = []
                for row in header:
                    cs = sorted([c for c in row if parent_a <= (c['x0'] + c['x1']) / 2 < parent_b], key=lambda c: c['x0'])
                    segments = []
                    for c in cs:
                        if not segments or c['x0'] - segments[-1][-1]['x1'] > 8:
                            segments.append([])
                        segments[-1].append(c)
                    pieces.extend(_line_text(s) for s in segments if min(c['x0'] for c in s) < b
                                  and max(c['x1'] for c in s) > a)
                header_cells.append('\n'.join(pieces))
            return [header_cells] + [['\n'.join(text for row in group if (text := _line_text(
                [c for c in row if a <= (c['x0'] + c['x1']) / 2 < b])))
                    for a, b in zip(boundaries, boundaries[1:])] for group in data_rows]
        grouped_rows = [header] + data_rows
        return [['\n'.join(text for row in group if (text := _line_text(
            [c for c in row if a <= (c['x0'] + c['x1']) / 2 < b])))
                 for a, b in zip(boundaries, boundaries[1:])] for group in grouped_rows]
    for y0, y1 in zip(full, full[1:]):
        cell_rows = [row for y, row in rows if y0 <= y < y1]
        grid.append(['\n'.join(text for row in cell_rows if (text := _line_text(
            [c for c in row if a <= (c['x0'] + c['x1']) / 2 < b]))) for a, b in zip(boundaries, boundaries[1:])])
    return grid


def reference_blocks(blocks, active=False):
    """Use explicit numbering or author-year starts, retaining hanging continuations."""
    from pdf_tools import _join_lines, _union
    output = []
    for block in blocks:
        if layout_rules.REFERENCE_HEADING.fullmatch(block['text']):
            active = True
            output.append(block)
            continue
        if layout_rules.REFERENCE_END.match(block['text']):
            active = False
        if not active or block.get('is_formula') or block.get('is_table'):
            output.append(block)
            continue
        for line in block.get('_lines', []):
            if line['bbox'][1] < 60 and (re.fullmatch(r'\d{3,5}', line['text'])
                                         or 'Journal' in line['text'] or re.search(r'et al\.$', line['text'])):
                output.append({'text': line['text'], 'kind': 'heading', 'bbox': line['bbox'],
                               '_lines': [line], 'is_formula': False})
                continue
            start = layout_rules.reference_start(line['text'])
            previous = output[-1] if output else {}
            if (not start and previous.get('kind') == 'reference'
                    and previous['_lines'][-1].get('_column') == line.get('_column')):
                previous['_lines'].append(line)
                previous['text'] = _join_lines(previous['_lines'])
                previous['bbox'] = _union([l['bbox'] for l in previous['_lines']])
            else:
                output.append({'text': line['text'], 'kind': 'reference', 'bbox': line['bbox'],
                               '_lines': [line], 'is_formula': False})
    return output


def numbered_regions(lines, width):
    """Use numbered display rows as seeds, then include adjacent script fragments."""
    from pdf_tools import _EQUATION_NUMBER, _numbered_equation, _union
    result, used = [], set()
    for number in lines:
        if not _EQUATION_NUMBER.fullmatch(number['text'].strip()):
            continue
        nb = number['bbox']
        candidates = [l for l in lines if l is not number and id(l) not in used
                      and l.get('_column') == number.get('_column')
                      and 0 <= nb[0] - l['bbox'][2] < width * .55
                      and l['bbox'][1] - 4 <= (nb[1] + nb[3]) / 2 <= l['bbox'][3] + 4
                      and _numbered_equation(l['text'] + ' ' + number['text'])]
        if not candidates:
            continue
        body = max(candidates, key=lambda l: l['bbox'][2] - l['bbox'][0])
        group = [body, number]
        bottom = min([l['bbox'][1] - 5 for l in lines if l.get('_column') == number.get('_column')
                      and l['bbox'][1] > nb[3] and _EQUATION_NUMBER.search(l['text'])]
                     + [body['bbox'][3] + 26])
        for line in lines:
            if line in group or id(line) in used or line.get('_column') != body.get('_column'):
                continue
            b = line['bbox']
            if (body['bbox'][0] - 8 <= b[0] and b[2] <= max(body['bbox'][2] + 8, nb[0] - 12)
                    and b[1] >= body['bbox'][1] - 15 and b[3] <= bottom
                    and len(line['text']) < 90
                    and not re.search(r'[A-Za-z]{4,}', line['text'])
                    and not _EQUATION_NUMBER.fullmatch(line['text'].strip())):
                group.append(line)
        used.update(id(l) for l in group)
        result.append({'text': ' '.join(l['text'] for l in group), 'kind': 'paragraph',
                       'bbox': _union([l['bbox'] for l in group]), '_lines': group, 'is_formula': True})
    return result


def algorithm_regions(page, lines):
    """An explicit algorithm caption and matching rules bound a pseudocode panel."""
    result = []
    for caption in lines:
        if not re.match(r'^Algorithm\s+\d+\b', caption['text'], re.I):
            continue
        cb = caption['bbox']
        edges = [e for e in page.horizontal_edges if e['x0'] <= cb[0] + 5
                 and e['x1'] >= cb[2] - 5 and e['x1'] - e['x0'] > page.width * .2]
        tops = [e for e in edges if cb[1] - 12 <= e['top'] <= cb[1] + 2]
        if not tops:
            continue
        top = max(tops, key=lambda e: e['top'])
        bottoms = sorted([e for e in edges if cb[3] + 20 < e['top'] < cb[3] + page.height * .65
                          and abs(e['x0'] - top['x0']) < 3 and abs(e['x1'] - top['x1']) < 3], key=lambda e: e['top'])
        if not bottoms:
            continue
        box = [top['x0'] - 1, top['top'] - 1, top['x1'] + 1, bottoms[0]['top'] + 1]
        members = sorted([l for l in lines if contains(box, l['bbox'])], key=lambda l: (l['bbox'][1], l['bbox'][0]))
        result.append({'text': '\n'.join(l['text'] for l in members), 'kind': 'paragraph',
                       'bbox': box, '_lines': members, 'is_formula': False, 'source_region_kind': 'algorithm'})
    return result
