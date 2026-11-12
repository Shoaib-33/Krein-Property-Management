"""Repair help for the tenant chat (app/assistant.py): answers home-repair questions from the knowledge base in
app/knowledge/tenant_repairs.md, so tenants can solve small problems themselves and report the rest.

- excerpts(question): the knowledge-base sections that match the question (simple word matching, no outside service).
  Only these excerpts go to the AI with the question, never tenant records.
- emergency(question): gas smell, carbon monoxide or fire get fixed safety steps straight away, without waiting for the AI.
- log_gap(...): a question the knowledge base could not answer well is kept (masked, without the tenant's name or
  account) so the knowledge base can be extended. The developer sees them under "Chatbot questions"; they are deleted
  after 180 days.
"""
import math
import re
from pathlib import Path

from fastapi import APIRouter, Depends, Request

from .db import connect, now, transaction
from .security import current_user

router = APIRouter()
KB_PATH = Path(__file__).parent / 'knowledge' / 'tenant_repairs.md'
_cache = {'mtime': None, 'rules': '', 'emergency': '', 'chunks': [], 'weight': {}}

# words too common to tell sections apart (English and Dutch)
STOP = set('''the and for are but not you your with this that have has was were what when where which who how does did can
could would should will its into from out off over under then than them they their there here also just very more most some
any all only own same other such too why our ours his her him she he it is be to of in on at by or as an a my me we us do
een het de en van dat die met voor niet zijn ook maar als bij naar wat hoe wie waar wanneer heb hebt heeft kan kun kunt mijn
ik je jij u uw we wij ze zij er dan nog wel geen meer moet moeten mag zou worden wordt is was om op aan in te tot uit over
door zo al dus want omdat of dit deze doet doe gaat ga gaan please help thanks hello hoi hallo graag
no yes ja nee ok am if so up eg'''.split())

EMERGENCY = re.compile(
    r'gaslucht|gas\s*lucht|ruik\w*\s+(?:\w+\s+){0,2}gas\b|gas\s*lek|gaslek|smell\w*\s+(?:of\s+)?gas\b|gas\s+(?:smell|leak)|'
    r'koolmonoxide|carbon\s*monoxide|\bco[\s-]?(?:melder|alarm|detector)\b|'
    r'\bbrand\b|\bin\s+brand\b|\bfire\b|\bvlammen\b|\bflames?\b', re.I)


def _load():
    try:
        mtime = KB_PATH.stat().st_mtime
    except OSError:
        return _cache
    if _cache['mtime'] == mtime:
        return _cache
    text = KB_PATH.read_text(encoding='utf-8')
    parts = re.split(r'\n(?=##+ )', text)
    rules, emergency, chunks = '', '', []
    for part in parts:
        head = part.split('\n', 1)[0]
        if not head.startswith('#'):
            continue
        if head.startswith('## 0.'):
            rules = part.strip()
        elif head.startswith('## 1.'):
            emergency = part.strip()
        elif head.startswith('## Sources') or head.startswith('# '):
            continue
        else:
            body = part.strip()
            if head.startswith('## ') and len(body.split('\n')) < 3:
                continue   # a heading that only introduces its ### subsections
            chunks.append({'head': head.lstrip('# ').strip(), 'text': body,
                           'head_words': _words(head), 'words': _words(body)})
    # a word found in few sections tells more than one found everywhere ("muizen" vs "keuken")
    df = {}
    for c in chunks:
        for w in c['words'] | c['head_words']:
            df[w] = df.get(w, 0) + 1
    weight = {w: math.log(1 + len(chunks) / n) for w, n in df.items()}
    _cache.update(mtime=mtime, rules=rules, emergency=emergency, chunks=chunks, weight=weight)
    return _cache


def _words(text):
    return {w for w in re.findall(r'[a-zà-ÿ]{2,}', str(text).lower()) if w not in STOP}


def _score(chunk, words, weight):
    score = 0
    for w in words:
        if w in chunk['head_words']:
            score += 2.5 * weight.get(w, 1)
        elif len(w) >= 5 and any(w in hw or (len(hw) >= 5 and hw in w) for hw in chunk['head_words']):
            score += 2   # Dutch compounds: "ketel" in "cv-ketel", "afvoer" in "afvoerputje"
        elif w in chunk['words']:
            score += weight.get(w, 0)
    return score


def excerpts(question, history_text='', limit=3):
    """The best-matching knowledge-base sections for a question (the question counts double against earlier turns)."""
    kb = _load()
    words = _words(question)
    if not words:
        return []
    extra = _words(history_text) - words
    ranked = []
    for chunk in kb['chunks']:
        s = _score(chunk, words, kb['weight']) * 2 + _score(chunk, extra, kb['weight'])
        if s:
            ranked.append((s, chunk))
    ranked.sort(key=lambda x: -x[0])
    best = ranked[0][0] if ranked else 0
    return [c for s, c in ranked[:limit] if s >= max(2, best / 3)]


def knowledge_prompt(question, history_text=''):
    """The part of the tenant system prompt with the chatbot rules, the emergency steps and the matching sections."""
    kb = _load()
    found = excerpts(question, history_text)
    body = '\n\n'.join(c['text'] for c in found) or '(no section of the knowledge base matches this question)'
    return ('\n\nREPAIR HELP. You also help tenants solve problems in their rented home in the Netherlands. Use ONLY the knowledge '
            'below for repair advice; do not add steps of your own. If the knowledge does not cover the question, say you are not '
            'sure, suggest reporting it with a photo, and end your reply with the tag [[gap]].\n'
            'For repair answers you may use up to 8 short numbered steps (one per line). Always end a repair answer with a short line '
            'that they can report it if this does not help, and add [[go:repairs|Report a repair]].\n'
            'Never promise who pays: say who usually pays and that the contract decides.\n\n'
            + kb['rules'] + '\n\n' + kb['emergency'] + '\n\nKNOWLEDGE FOR THIS QUESTION:\n' + body), found


EMERGENCY_REPLY = {
    'en': ('This sounds like an emergency. Do this now:\n'
           '1. Gas smell: don\'t switch lights or appliances on or off and don\'t use a lighter. Open windows and doors, close the gas tap at '
           'the meter if it\'s easy, and leave the home with everyone.\n'
           '2. Carbon monoxide (alarm, or headache or dizziness while the heating is on): open windows, switch off the boiler or gas stove '
           'if you can, and go outside.\n'
           '3. Fire or smoke: get everyone out, close the doors behind you, don\'t use the lift.\n'
           'Then call **112** if there is fire or anyone feels unwell. For a gas smell call **0800-9009** (free, 24/7) from outside.\n'
           'When you are safe, report it to us as an emergency.'),
    'nl': ('Dit klinkt als een noodgeval. Doe nu het volgende:\n'
           '1. Gaslucht: doe geen licht of apparaten aan of uit en gebruik geen aansteker. Zet ramen en deuren open, draai de gaskraan bij de '
           'meter dicht als dat makkelijk kan, en ga met iedereen naar buiten.\n'
           '2. Koolmonoxide (melder gaat af, of hoofdpijn of duizeligheid als de verwarming aan staat): zet ramen open, zet de ketel of '
           'het gasfornuis uit als dat kan, en ga naar buiten.\n'
           '3. Brand of rook: ga met iedereen naar buiten, doe de deuren achter je dicht en gebruik de lift niet.\n'
           'Bel daarna **112** bij brand of als iemand zich niet goed voelt. Bij gaslucht bel je buiten **0800-9009** (gratis, 24/7).\n'
           'Meld het daarna bij ons als spoed, als je veilig bent.'),
}


def emergency(question, lang='en'):
    """Fixed safety steps for gas, carbon monoxide or fire; None for anything else."""
    if EMERGENCY.search(question or '') and not re.search(r'rookmelder|smoke\s*(?:detector|alarm)', question or '', re.I):
        return EMERGENCY_REPLY['nl' if lang == 'nl' else 'en']
    return None


def local_reply(question, lang='en'):
    """Without the AI: the steps of the best-matching section, shortened, so the tenant still gets real help."""
    found = excerpts(question)
    if not found:
        return None
    lines, raw = [], []
    for line in found[0]['text'].split('\n')[1:]:   # join the wrapped continuation lines of a step
        if raw and raw[-1].strip() and line.strip() and not re.match(r'\s*(\d+\.\s|[-*•]\s|\||\*\*|#)', line):
            raw[-1] += ' ' + line.strip()
        else:
            raw.append(line)
    for line in raw:
        line = line.strip()
        if not line or line.startswith('|---'):
            continue
        line = re.sub(r'\*([^*]+)\*', r'\1', line.replace('**', '\0')).replace('\0', '**')   # drop italics, keep bold
        lines.append(line.strip('| ').replace(' | ', ': '))
        if sum(len(x) for x in lines) > 900:
            break
    head = found[0]['head']
    intro = 'Dit kan helpen' if lang == 'nl' else 'This may help'
    outro = ('Lukt het niet? Meld het dan met een foto.' if lang == 'nl' else 'If this doesn\'t help, report it with a photo.')
    return f'{intro} ({head}):\n' + '\n'.join(lines) + '\n' + outro


def log_gap(audience, question, reason):
    """Keep a masked question the knowledge base did not answer well; older than 180 days is removed."""
    from .assistant import redact
    text = redact(str(question or '').strip())[:600]
    if not text:
        return
    try:
        with transaction() as conn:
            conn.execute("DELETE FROM assistant_gaps WHERE created_at < ?", (_days_ago(180),))
            conn.execute('INSERT INTO assistant_gaps(created_at,audience,question,reason) VALUES(?,?,?,?)', (now(), audience, text, reason))
    except Exception:   # the chat must never fail because the log could not be written
        pass


def _days_ago(days):
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _developer(request, user):
    from .access import require_developer
    require_developer(user)


@router.get('/api/developer/assistant-gaps')
def list_gaps(request: Request, user=Depends(current_user)):
    _developer(request, user)
    with connect() as conn:
        rows = [dict(r) for r in conn.execute('SELECT id,created_at,audience,question,reason,handled_at FROM assistant_gaps ORDER BY handled_at IS NOT NULL, id DESC LIMIT 300')]
    return {'items': rows, 'sections': [c['head'] for c in _load()['chunks']]}


@router.post('/api/developer/assistant-gaps/{gap_id}/handled')
def handle_gap(gap_id: int, request: Request, user=Depends(current_user)):
    _developer(request, user)
    with transaction() as conn:
        conn.execute('UPDATE assistant_gaps SET handled_at=? WHERE id=?', (now(), gap_id))
    return {'ok': True}


@router.delete('/api/developer/assistant-gaps/{gap_id}')
def delete_gap(gap_id: int, request: Request, user=Depends(current_user)):
    _developer(request, user)
    with transaction() as conn:
        conn.execute('DELETE FROM assistant_gaps WHERE id=?', (gap_id,))
    return {'ok': True}
