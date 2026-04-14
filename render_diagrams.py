import json
from pathlib import Path

root = Path(__file__).resolve().parent


def esc(s: str) -> str:
    return s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def svg_header(width, height):
    return [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{int(width)}" height="{int(height)}" viewBox="0 0 {int(width)} {int(height)}">',
        '<defs>',
        '<marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse">',
        '<path d="M 0 0 L 10 5 L 0 10 z" fill="#1e1e1e"/>',
        '</marker>',
        '<style>text { font-family: Inter, PingFang SC, Microsoft YaHei, sans-serif; fill: #1e1e1e; }</style>',
        '</defs>',
        '<rect x="0" y="0" width="100%" height="100%" fill="#ffffff"/>',
    ]


def render_text(el):
    x = el.get('x', 0)
    y = el.get('y', 0)
    font_size = el.get('fontSize', 20)
    color = el.get('strokeColor', '#1e1e1e')
    text = el.get('text', '')
    lines = text.split('\n') or ['']
    out = [f'<text x="{x}" y="{y + font_size}" font-size="{font_size}" fill="{color}">']
    for i, line in enumerate(lines):
        dy = '0' if i == 0 else str(font_size * 1.25)
        out.append(f'<tspan x="{x}" dy="{dy}">{esc(line)}</tspan>')
    out.append('</text>')
    return '\n'.join(out)


def render_rect(el):
    x, y = el.get('x', 0), el.get('y', 0)
    w, h = el.get('width', 0), el.get('height', 0)
    fill = el.get('backgroundColor', 'transparent')
    stroke = el.get('strokeColor', '#1e1e1e')
    rx = 18 if el.get('roundness') else 4
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" ry="{rx}" fill="{fill}" stroke="{stroke}" stroke-width="2"/>'


def render_ellipse(el):
    x, y = el.get('x', 0), el.get('y', 0)
    w, h = el.get('width', 0), el.get('height', 0)
    fill = el.get('backgroundColor', 'transparent')
    stroke = el.get('strokeColor', '#1e1e1e')
    return f'<ellipse cx="{x + w/2}" cy="{y + h/2}" rx="{w/2}" ry="{h/2}" fill="{fill}" stroke="{stroke}" stroke-width="2"/>'


def render_line(el, arrow=False):
    x, y = el.get('x', 0), el.get('y', 0)
    points = el.get('points', [[0, 0], [el.get('width', 0), el.get('height', 0)]])
    pts = [f'{x + px},{y + py}' for px, py in points]
    stroke = el.get('strokeColor', '#1e1e1e')
    marker = ' marker-end="url(#arrow)"' if arrow else ''
    return f'<polyline points="{" ".join(pts)}" fill="none" stroke="{stroke}" stroke-width="2"{marker}/>'


def render_file(path: Path):
    data = json.loads(path.read_text(encoding='utf-8'))
    elements = data.get('elements', [])
    max_x = max((el.get('x', 0) + el.get('width', 0) for el in elements), default=1200) + 80
    max_y = max((el.get('y', 0) + el.get('height', 0) for el in elements), default=800) + 80
    out = svg_header(max_x, max_y)
    for el in elements:
        t = el.get('type')
        if t == 'rectangle':
            out.append(render_rect(el))
        elif t == 'ellipse':
            out.append(render_ellipse(el))
        elif t == 'text':
            out.append(render_text(el))
        elif t == 'arrow':
            out.append(render_line(el, arrow=True))
        elif t == 'line':
            out.append(render_line(el, arrow=False))
    out.append('</svg>')
    path.with_suffix('.svg').write_text('\n'.join(out), encoding='utf-8')

for p in root.glob('*.excalidraw'):
    render_file(p)

print('Rendered SVG companions for simple_hermes_codex diagrams.')
