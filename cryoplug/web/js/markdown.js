// Minimal Markdown renderer for the bundled guide: headings, paragraphs, lists, tables, code, quotes, links.
// Builds DOM nodes (never innerHTML), so the text is always inserted as text.
import { h } from './ui.js';

export function anchorId(text) {
  // Same rule as GitHub: lower case, keep letters/digits/spaces/hyphens, spaces become hyphens.
  return Array.from(text.toLowerCase()).filter((c) => /[\p{L}\p{N} -]/u.test(c)).join('').trim().replace(/ /g, '-');
}

const INLINE = /(`[^`]+`)|(\*\*[^*]+\*\*)|(\*[^*\s][^*]*\*)|(\[[^\]]+\]\([^)\s]+\))/g;

export function inline(text, onAnchor) {
  const out = [];
  let last = 0;
  for (const m of text.matchAll(INLINE)) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const tok = m[0];
    if (m[1]) out.push(h('code', {}, tok.slice(1, -1)));
    else if (m[2]) out.push(h('strong', {}, inline(tok.slice(2, -2), onAnchor)));
    else if (m[3]) out.push(h('em', {}, inline(tok.slice(1, -1), onAnchor)));
    else {
      const [, label, href] = tok.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
      if (href.startsWith('#')) {
        out.push(h('a', { href: '#', onclick: (e) => { e.preventDefault(); if (onAnchor) onAnchor(href.slice(1)); } }, inline(label, onAnchor)));
      } else if (/^https?:\/\//.test(href)) {
        out.push(h('a', { href, target: '_blank', rel: 'noopener' }, inline(label, onAnchor)));
      } else {
        out.push(inline(label, onAnchor));
      }
    }
    last = m.index + tok.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

function splitRow(line) {
  let s = line.trim();
  if (s.startsWith('|')) s = s.slice(1);
  if (s.endsWith('|')) s = s.slice(0, -1);
  return s.split('|').map((c) => c.trim());
}

export function renderMarkdown(text, { onAnchor = null } = {}) {
  const root = h('div', { class: 'doc' });
  const lines = text.replace(/\r/g, '').split('\n');
  const isBlockStart = (l) => /^(#{1,6})\s|^```|^\s*([-*]|\d+\.)\s|^>|^\|/.test(l) || /^---+\s*$/.test(l);
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i++; continue; }
    let m;
    if (line.startsWith('```')) {
      const code = [];
      i++;
      while (i < lines.length && !lines[i].startsWith('```')) code.push(lines[i++]);
      i++;
      root.appendChild(h('pre', {}, h('code', {}, code.join('\n'))));
    } else if ((m = line.match(/^(#{1,6})\s+(.*)$/))) {
      const level = m[1].length;
      root.appendChild(h(`h${level}`, { id: anchorId(m[2]) }, inline(m[2], onAnchor)));
      i++;
    } else if (/^---+\s*$/.test(line)) {
      root.appendChild(h('hr'));
      i++;
    } else if (line.trim().startsWith('|') && i + 1 < lines.length && /^\s*\|?\s*:?-{3,}/.test(lines[i + 1])) {
      const head = splitRow(line);
      i += 2;
      const rows = [];
      while (i < lines.length && lines[i].trim().startsWith('|')) rows.push(splitRow(lines[i++]));
      root.appendChild(h('div', { class: 'doc-table' }, h('table', { class: 'data' },
        h('thead', {}, h('tr', {}, head.map((c) => h('th', {}, inline(c, onAnchor))))),
        h('tbody', {}, rows.map((r) => h('tr', {}, r.map((c) => h('td', {}, inline(c, onAnchor)))))))));
    } else if (line.startsWith('>')) {
      const quote = [];
      while (i < lines.length && lines[i].startsWith('>')) quote.push(lines[i++].replace(/^>\s?/, ''));
      root.appendChild(h('blockquote', {}, inline(quote.join(' '), onAnchor)));
    } else if ((m = line.match(/^\s*([-*]|\d+\.)\s+(.*)$/))) {
      const ordered = /\d+\./.test(m[1]);
      const items = [];
      while (i < lines.length) {
        const lm = lines[i].match(/^\s*([-*]|\d+\.)\s+(.*)$/);
        if (lm && /\d+\./.test(lm[1]) === ordered) { items.push(lm[2]); i++; continue; }
        if (lines[i].trim() && /^\s{2,}/.test(lines[i]) && !isBlockStart(lines[i].trim())) { items[items.length - 1] += ` ${lines[i].trim()}`; i++; continue; }
        break;
      }
      root.appendChild(h(ordered ? 'ol' : 'ul', {}, items.map((it) => h('li', {}, inline(it, onAnchor)))));
    } else {
      const para = [];
      while (i < lines.length && lines[i].trim() && !isBlockStart(lines[i])) para.push(lines[i++].trim());
      root.appendChild(h('p', {}, inline(para.join(' '), onAnchor)));
    }
  }
  return root;
}
