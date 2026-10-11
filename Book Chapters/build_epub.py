"""Regenerate ch03-ch06 XHTML from current tex and splice into the existing EPUB zip.

Surgical approach: copy every zip entry verbatim, replacing only the four
chapter files. TOC/NCX/nav/metadata/images stay untouched.
"""
import re, html, zipfile, shutil, os

SRC = 'Building-Agentic-AI-for-BFSI.epub'
TEX = {'ch03': 'tex/ch03_findoc_data_extraction.tex',
       'ch04': 'tex/ch04_credit_risk.tex',
       'ch05': 'tex/ch05_hni_onboarding.tex',
       'ch06': 'tex/ch06_investment_research.tex'}
TITLES = {'ch03': 'Chapter 3: Financial Document Data Extraction Agent',
          'ch04': 'Chapter 4: Credit Risk Assessment Agent',
          'ch05': 'Chapter 5: HNI Customer Onboarding Agent',
          'ch06': 'Chapter 6: Investment Research Copilot'}
TARGETS = {'ch03': 'EPUB/ch03.xhtml', 'ch04': 'EPUB/ch04.xhtml',
           'ch05': 'EPUB/ch05.xhtml', 'ch06': 'EPUB/ch06.xhtml'}


def strip_comments(tex):
    out = []
    for line in tex.split('\n'):
        buf, i = '', 0
        while i < len(line):
            if line[i] == '%' and (i == 0 or line[i - 1] != '\\'):
                break
            buf += line[i]; i += 1
        out.append(buf)
    return '\n'.join(out)


def parse_braced(s, i):
    assert s[i] == '{', s[i:i + 20]
    i += 1; depth = 1; buf = ''
    while i < len(s) and depth:
        c = s[i]
        if c == '{': depth += 1; buf += c
        elif c == '}':
            depth -= 1
            if depth: buf += c
        else: buf += c
        i += 1
    return buf, i


def inline(t):
    """LaTeX fragment -> HTML. Only emits <strong>/<em>/<code>/<a>; all else escaped."""
    cmds = {'textbf': 'strong', 'textit': 'em', 'emph': 'em', 'texttt': 'code'}
    out, i = '', 0
    while i < len(t):
        if t.startswith('\\url{', i):
            inner, j = parse_braced(t, t.index('{', i))
            esc = html.escape(inner, quote=True)
            out += f'<a href="{esc}">{esc}</a>'; i = j; continue
        m = re.match(r'\\(textbf|textit|emph|texttt)\{', t[i:])
        if m:
            inner, j = parse_braced(t, i + len(m.group(0)) - 1)
            inner = re.sub(r'\\[a-zA-Z]+\{([^{}]*)\}', r'\1', inner)
            out += f'<{cmds[m.group(1)]}>{html.escape(inner, quote=False)}</{cmds[m.group(1)]}>'
            i = j; continue
        if t.startswith('``', i) or t.startswith("''", i):
            out += '"'; i += 2; continue
        if t.startswith('---', i):
            out += '\u2014'; i += 3; continue
        if t.startswith('--', i):
            out += '\u2013'; i += 2; continue
        c = t[i]
        if c == '\\':
            if i + 1 < len(t) and t[i + 1] in '&%$#_{}\'"':
                out += html.escape(t[i + 1], quote=False); i += 2; continue
            m2 = re.match(r'\\[a-zA-Z]+\s*', t[i:])
            if m2:
                i += len(m2.group(0)); continue
            out += html.escape(c, quote=False); i += 1; continue
        if c in '{}':
            i += 1; continue
        out += html.escape(c, quote=False); i += 1
    return out


PURPOSE = {
    'ch03': ('Chapter Purpose',
              'This chapter presents the Financial Document Data Extraction Agent as an interview-style '
              'discussion. The candidate clarifies the analyst workflow, confirms functional and non-functional '
              'requirements, and designs a single FastAPI service that extracts flattened financial line items '
              'with deterministic validation and retry. Object storage (S3 or ADLS) is authoritative for files, '
              'Postgres stores projects, documents, and line items, and every value carries page and table lineage.'),
    'ch04': ('Chapter Purpose',
              'This chapter presents the Credit Risk Assessment Agent as an interview-style discussion. The candidate '
              'clarifies the corporate lending workflow, confirms functional and non-functional requirements, and designs '
              'a decision-support system with deterministic ratios, benchmarks, and scorecard logic. Redis tracks live '
              'phases, Postgres with pgvector persists versions and lineage, and every result remains a draft until '
              'human review.'),
    'ch05': ('Chapter Purpose',
              'This chapter presents the HNI Customer Onboarding Agent as an interview-style discussion. The candidate '
              'clarifies document-led onboarding and name-based screening, confirms functional and non-functional '
              'requirements, and designs a decision-support system with deterministic UBO resolution, watchlist '
              'screening, risk scoring, and EDD rationale. Object storage (S3 or ADLS) is authoritative for files, '
              'and Postgres versions every assessment per client.'),
    'ch06': ('Chapter Purpose',
              'This chapter presents the Investment Research Copilot as an interview-style discussion. The candidate '
              'clarifies portfolio monitoring and construction, confirms functional and non-functional requirements, '
              'and designs a copilot in which deterministic Python owns every decision-bearing number. Redis tracks '
              'live phases with optional Celery workers, object storage (S3 or ADLS) holds uploads, Postgres persists '
              'runs, reviews, and audit history, and the system proposes trades but never executes them.'),
}


def nav_anchors(epub_path):
    zin = zipfile.ZipFile(epub_path, 'r')
    nav = zin.read('EPUB/nav.xhtml').decode('utf-8')
    zin.close()
    out = {}
    for key in TEX:
        out[key] = re.findall(key + r'\.xhtml#([^"]+)', nav)
    return out
def tex_to_html(tex_raw, img_names, sec_ids=(), purpose=None):
    ids = list(sec_ids)
    used = {'n': 0}

    def next_id():
        if used['n'] < len(ids):
            a = ids[used['n']]
            used['n'] += 1
            return a
        return None

    def h2(title):
        i = next_id()
        return f'<h2 id="{i}">{inline(title)}</h2>' if i else f'<h2>{inline(title)}</h2>'

    tex = strip_comments(tex_raw).replace('\r', '')
    tex = tex.replace('\\medskip\\hrule\\medskip', '\n\n<HR/>\n\n')
    tex = re.sub(r'^\\label\{[^{}]*\}\s*$', '', tex, flags=re.M)
    lines = tex.split('\n')
    blocks, cur = [], []
    for ln in lines:
        if ln.strip() == '':
            if cur: blocks.append('\n'.join(cur)); cur = []
        else:
            cur.append(ln)
    if cur: blocks.append('\n'.join(cur))
    out, in_list = [], None

    def close_list():
        nonlocal in_list
        if in_list: out.append('</ul>' if in_list == 'ul' else '</ol>'); in_list = None

    for b in blocks:
        bs = b.strip()
        # A block may bundle \begin{itemized}/items/\end on consecutive lines
        # (no blank lines between). Split those line-wise.
        blines = bs.split('\n')
        if len(blines) > 1 and any(re.match(r'\\(begin|end)\{(itemize|enumerate)\}|\\item', ln.strip()) for ln in blines):
            for ln in blines:
                bs = ln.strip()
                if not bs:
                    continue
                if re.match(r'\\begin\{(itemize|enumerate)\}', bs):
                    close_list()
                    in_list = 'ul' if 'itemize' in bs else 'ol'
                    out.append('<ul>' if in_list == 'ul' else '<ol>'); continue
                if re.match(r'\\end\{(itemize|enumerate)\}', bs):
                    close_list(); continue
                if bs.startswith('\\item'):
                    item = re.sub(r'^\\item\s*', '', bs)
                    if in_list is None:
                        out.append('<ul>'); in_list = 'ul'
                    out.append(f'<li>{inline(item)}</li>'); continue
                ms = re.match(r'\\speaker\{([^{}]+)\}\{', bs)
                mc = re.match(r'\\speakcont\{', bs)
                if ms:
                    close_list()
                    inner, _ = parse_braced(bs, ms.end() - 1)
                    out.append(f'<p><strong>{html.escape(ms.group(1).strip())}:</strong> {inline(inner)}</p>')
                    continue
                if mc:
                    close_list()
                    inner, _ = parse_braced(bs, mc.end() - 1)
                    out.append(f'<p>{inline(inner)}</p>')
                    continue
                if bs.startswith('\\'):
                    continue
                close_list()
                out.append(f'<p>{inline(bs)}</p>')
            continue
        if bs == '<HR/>':
            close_list(); out.append('<hr/>'); continue
        m = re.match(r'\\chapter\{(.*)\}\s*$', bs)
        if m:
            close_list(); out.append(f'<h1>{inline(m.group(1))}</h1>')
            if purpose:
                out.append(f'<h2 id="{ids[0]}">{purpose[0]}</h2>' if ids else f'<h2>{purpose[0]}</h2>')
                if ids:
                    used['n'] = 1
                out.append(f'<p>{purpose[1]}</p>')
                out.append('<hr/>')
            continue
        m = re.match(r'\\section\*?\{(.*)\}\s*$', bs)
        if m:
            close_list(); out.append(h2(m.group(1))); continue
        m = re.match(r'\\subsection\*?\{(.*)\}\s*$', bs)
        if m:
            close_list(); out.append(f'<h3>{inline(m.group(1))}</h3>'); continue
        m = re.match(r'\\subsubsection\*?\{(.*)\}\s*$', bs)
        if m:
            close_list(); out.append(f'<h4>{inline(m.group(1))}</h4>'); continue
        if re.match(r'\\begin\{(itemize|enumerate)\}', bs):
            close_list()
            in_list = 'ul' if 'itemize' in bs else 'ol'
            out.append('<ul>' if in_list == 'ul' else '<ol>'); continue
        if re.match(r'\\end\{(itemize|enumerate)\}', bs):
            close_list(); continue
        if bs.startswith('\\item'):
            item = re.sub(r'^\\item\s*', '', bs)
            if in_list is None:
                out.append('<ul>'); in_list = 'ul'
            out.append(f'<li>{inline(item)}</li>'); continue
        if bs.startswith('\\begin{figure}'):
            inc = re.search(r'\\includegraphics\[[^\]]*\]\{([^}]+)\}', bs)
            capm = re.search(r'\\caption(\[[^\]]*\])?\{', bs)
            captext = ''
            if capm:
                cj = bs.index('{', capm.start())
                captext, _ = parse_braced(bs, cj)
            if inc:
                base = inc.group(1).split('/')[-1]
                match = next((n for n in img_names if n.endswith(base)), None)
                if match:
                    rel = match[len('EPUB/'):] if match.startswith('EPUB/') else match
                    close_list()
                    alt = html.escape(re.sub(r'\\[a-zA-Z]+', '', captext), quote=True)
                    out.append(f'<figure><img src="{rel}" alt="{alt}"/>'
                               f'<figcaption>{inline(captext)}</figcaption></figure>')
                    continue
            close_list()
            if captext: out.append(f'<p><em>[Figure: {inline(captext)}]</em></p>')
            continue
        if bs.startswith('\\begin{quote}'):
            q = re.sub(r'\\begin\{quote\}|\\end\{quote\}', '', bs).strip()
            close_list(); out.append(f'<blockquote><p>{inline(q)}</p></blockquote>'); continue
        if bs.startswith('\\begin{center}'):
            q = re.sub(r'\\begin\{center\}|\\end\{center\}', '', bs).strip()
            u = re.search(r'\\url\{([^{}]+)\}', q)
            if u:
                close_list(); out.append(f'<p><a href="{u.group(1)}">{u.group(1)}</a></p>'); continue
            close_list(); out.append(f'<p>{inline(q)}</p>'); continue
        if bs.startswith('\\begin{'):
            txt = re.sub(r'\\begin\{[a-zA-Z*]+\}(\[[^\]]*\])?(\{[^{}]*\})?', '', bs)
            txt = re.sub(r'\\end\{[a-zA-Z*]+\}', '', txt)
            if '\\\\' in txt or '&' in txt:
                rows = [r.strip() for r in txt.split('\\\\')]
                h = ['<table>']
                for r in rows:
                    r = re.sub(r'\\hline', '', r).strip()
                    if not r or r.startswith('\\'): continue
                    cells = [inline(c.strip()) for c in r.split('&')]
                    if cells and any(cells):
                        h.append('<tr>' + ''.join(f'<td>{c}</td>' for c in cells) + '</tr>')
                h.append('</table>')
                close_list(); out.append('\n'.join(h)); continue
            if txt.strip():
                close_list(); out.append(f'<p>{inline(txt.strip())}</p>')
            continue
        idxs = [m.start() for m in re.finditer(r'\\(speaker|speakcont)\{', bs)]
        if idxs:
            close_list()
            for k, st in enumerate(idxs):
                chunk = bs[st:idxs[k + 1] if k + 1 < len(idxs) else len(bs)]
                ms = re.match(r'\\speaker\{([^{}]+)\}\{', chunk)
                mc = re.match(r'\\speakcont\{', chunk)
                if ms:
                    inner, _ = parse_braced(chunk, ms.end() - 1)
                    who = ms.group(1).strip()
                    out.append(f'<p><strong>{html.escape(who)}:</strong> {inline(inner)}</p>')
                elif mc:
                    inner, _ = parse_braced(chunk, mc.end() - 1)
                    out.append(f'<p>{inline(inner)}</p>')
            continue
        if bs.startswith('\\'):
            continue
        close_list()
        out.append(f'<p>{inline(bs)}</p>')
    if in_list: out.append('</ul>' if in_list == 'ul' else '</ol>')
    body = '\n'.join(o for o in out if o.strip())
    return re.sub(r'(<hr/>\s*){2,}', '<hr/>', body)


zin = zipfile.ZipFile(SRC, 'r')
names = zin.namelist()
img_names = [n for n in names if n.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.svg'))]
zin.close()
anchors = nav_anchors(SRC)

new_docs, missing = {}, set()
for key, path in TEX.items():
    with open(path, encoding='utf-8') as f:
        tex_raw = f.read()
    for m in re.finditer(r'\\includegraphics\[[^\]]*\]\{([^}]+)\}', tex_raw):
        base = m.group(1).split('/')[-1]
        if not any(n.endswith(base) for n in img_names):
            missing.add(base)
    body = tex_to_html(tex_raw, img_names, anchors.get(key, ()), PURPOSE.get(key))
    new_docs[TARGETS[key]] = ("<?xml version='1.0' encoding='utf-8'?>\n"
                              '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>' + TITLES[key] +
                              '</title><link rel="stylesheet" type="text/css" href="styles/book.css"/></head><body>'
                              + body + '</body></html>').encode('utf-8')
    nsec = body.count('<h2')
    print(key, 'sections:', nsec, '| nav anchors:', len(anchors.get(key, ()))),

bak = SRC + '.bak2'
shutil.copyfile(SRC, bak)
# Repair pre-existing TOC nesting defects so nav/ncx are well-formed XML.
zin = zipfile.ZipFile(SRC, 'r')
nav = zin.read('EPUB/nav.xhtml').decode('utf-8')
old_nav = """          </ol>
        </li>
          </ol>
        </li>
        <li>
          <a href="ch01.xhtml">"""
new_nav = """          </ol>
        </li>
        <li>
          <a href="ch01.xhtml">"""
if old_nav in nav:
    new_docs['EPUB/nav.xhtml'] = nav.replace(old_nav, new_nav).encode('utf-8')
ncx = zin.read('EPUB/toc.ncx').decode('utf-8')
old_ncx = '    </navPoint>\n    </navPoint><navPoint id="sep_2">'
new_ncx = '    </navPoint>\n    <navPoint id="sep_2">'
if old_ncx in ncx:
    new_docs['EPUB/toc.ncx'] = ncx.replace(old_ncx, new_ncx).encode('utf-8')
zin.close()
# EPUB spec: mimetype first entry, stored uncompressed.
tmp = SRC + '.new'
zin = zipfile.ZipFile(SRC, 'r')
zout = zipfile.ZipFile(tmp, 'w')
zout.writestr('mimetype', zin.read('mimetype'), compress_type=zipfile.ZIP_STORED)
for n in zin.namelist():
    if n == 'mimetype':
        continue
    data = new_docs.get(n, zin.read(n))
    zout.writestr(n, data, compress_type=zipfile.ZIP_DEFLATED)
zin.close(); zout.close()
os.replace(tmp, SRC)
print('updated', SRC)
print('missing images kept as caption placeholders:', sorted(missing) if missing else 'none')
for zname, data in new_docs.items():
    t = data.decode('utf-8')
    print(zname, 'bytes:', len(data),
          '| Interviewer:', t.count('<strong>Interviewer:</strong>'),
          '| Candidate:', t.count('<strong>Candidate:</strong>'))
