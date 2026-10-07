// Job help sheets (French): shown in the job builder, the job details panel and the help pages.
import { state } from './state.js';
import { h, icon, modal } from './ui.js';

const SECTIONS = [
  ['when', 'Quand l\'utiliser'],
  ['avoid', 'À éviter / pièges'],
  ['tips', 'Conseils'],
];

export const CATEGORY_FR = {
  Import: 'Import',
  'Map processing': 'Traitement de carte',
  Heterogeneity: 'Hétérogénéité (variabilité 3D)',
  'Model building': 'Construction de modèle',
  Interactive: 'Reconstruction interactive',
  Refinement: 'Affinement',
  Validation: 'Validation',
  Deposition: 'Dépôt',
  Utilities: 'Utilitaires',
};

// Full sheet. `onNext(typeName)` makes the "next steps" clickable.
export function helpSheet(type, { onNext = null, showPurpose = true } = {}) {
  const help = type.help;
  if (!help) return h('div', { class: 'muted small' }, type.description);
  const nextTypes = (help.next || []).map((n) => state.types[n]).filter(Boolean);
  return h('div', { class: 'help-sheet' },
    showPurpose ? h('p', { class: 'help-purpose' }, help.purpose) : null,
    SECTIONS.filter(([key]) => (help[key] || []).length).map(([key, title]) => h('div', { class: `help-sec ${key}` },
      h('h6', {}, title), h('ul', {}, help[key].map((x) => h('li', {}, x))))),
    help.inputs ? h('div', { class: 'help-sec inputs' }, h('h6', {}, 'Entrées conseillées'), h('p', {}, help.inputs)) : null,
    nextTypes.length ? h('div', { class: 'help-sec next' }, h('h6', {}, 'Étapes suivantes'),
      h('div', { class: 'row wrap' }, nextTypes.map((t) => (onNext
        ? h('button', { class: 'btn small', type: 'button', onclick: () => onNext(t.name), title: `Créer : ${t.title}` }, icon('next'), t.title)
        : h('span', { class: 'tag' }, t.title))))) : null,
    h('div', { class: 'muted small', style: { marginTop: '6px' } }, type.tool ? `Logiciel : ${type.tool}` : 'Intégré à CryoPlug (aucun logiciel externe)',
      type.gpu ? ' · GPU' : '', type.interactive ? ' · interactif' : ''));
}

// Collapsible "learn more" block used inside forms and panels.
export function helpDetails(type, { title = 'Quand l\'utiliser, pièges et conseils', open = false, onNext = null } = {}) {
  const det = h('details', { class: 'help-details', open },
    h('summary', {}, icon('alert'), title),
    helpSheet(type, { onNext, showPurpose: false }));
  return det;
}

export function helpModal(type, { onNext = null, onUse = null } = {}) {
  const m = modal({
    title: type.title,
    wide: true,
    body: helpSheet(type, { onNext: onNext ? (n) => { m.close(); onNext(n); } : null }),
    footer: onUse ? [h('button', { class: 'btn primary', type: 'button', onclick: () => { m.close(); onUse(); } }, 'Utiliser ce job')] : [],
  });
  return m;
}
