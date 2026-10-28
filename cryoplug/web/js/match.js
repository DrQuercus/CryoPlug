// Matching of the command palette (palette.js): apart from the page, without imports, so that it can be tested alone.
// Every word typed must appear in the item; an item whose label starts with what was typed comes first, then one
// where a word of the label starts with a word typed, then the rest (a match in the description or the keywords).
export function matchScore(query, item) {
  const words = String(query || '').toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return 1;
  const label = String(item.label || '').toLowerCase();
  const hay = `${label} ${item.sub || ''} ${item.keywords || ''}`.toLowerCase();
  let score = 0;
  for (const w of words) {
    if (!hay.includes(w)) return 0;
    if (label.startsWith(w)) score += 3;
    else if (new RegExp(`(^|[\\s\\-_/(.:])${w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}`).test(label)) score += 2;
    else score += label.includes(w) ? 1.5 : 1;
  }
  if (label === words.join(' ')) score += 2;
  return score;
}

// The items in the order shown: by score, then as they came (jobs newest first, projects as listed). A job of the
// open project counts a little more than a job type or a page with the same match: "refine" is first the refinements
// already run, then the refinement jobs to start.
const GROUP_BONUS = { Jobs: 1, Projects: 0.5 };
export function rankItems(query, items) {
  const scored = items.map((it, i) => ({ it, i, s: matchScore(query, it) })).map((x) => ({ ...x, s: x.s > 0 ? x.s + (GROUP_BONUS[x.it.group] || 0) : 0 }))
    .filter((x) => x.s > 0);
  scored.sort((a, b) => b.s - a.s || a.i - b.i);
  return scored.map((x) => x.it);
}
