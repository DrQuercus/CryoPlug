// Density statistics of a loaded map: histogram for the threshold widget, percentiles for the
// initial level (ChimeraX convention: 1% of the grid points above the contour) and the slice window.

const FINE_BINS = 4096;
const SHOWN_BINS = 128;

export function mapStatistics(volume) {
  const { data, space } = volume.grid.cells;
  const { min, max, mean, sigma } = volume.grid.stats;
  const span = max - min || 1;
  const fine = new Float64Array(FINE_BINS);
  const stride = Math.max(1, Math.floor(data.length / 4e6)); // sample at most ~4 M values
  let total = 0;
  for (let i = 0; i < data.length; i += stride) {
    let b = Math.floor(((data[i] - min) / span) * FINE_BINS);
    if (b < 0) b = 0; else if (b >= FINE_BINS) b = FINE_BINS - 1;
    fine[b] += 1;
    total += 1;
  }
  const cumulative = new Float64Array(FINE_BINS);
  let acc = 0;
  for (let b = 0; b < FINE_BINS; b++) { acc += fine[b]; cumulative[b] = acc; }
  const percentile = (p) => {
    const target = p * total;
    for (let b = 0; b < FINE_BINS; b++) if (cumulative[b] >= target) return min + ((b + 0.5) / FINE_BINS) * span;
    return max;
  };
  // fraction of the grid above a value (for the "enclosed" readout)
  const fractionAbove = (value) => {
    const b = Math.floor(((value - min) / span) * FINE_BINS);
    if (b < 0) return 1;
    if (b >= FINE_BINS) return 0;
    return 1 - cumulative[b] / total;
  };
  const bins = new Float64Array(SHOWN_BINS);
  const group = FINE_BINS / SHOWN_BINS;
  for (let b = 0; b < FINE_BINS; b++) bins[Math.floor(b / group)] += fine[b];
  return {
    min, max, mean, sigma, bins, percentile, fractionAbove,
    dims: [...space.dimensions],
    low: percentile(0.01), high: percentile(0.999),
  };
}

export function defaultLevel(item) {
  const { min, max, mean, sigma, percentile } = item.stats;
  if (item.otype === 'mask') return min >= -0.01 && max <= 1.01 ? 0.5 : (min + max) / 2;
  // ChimeraX starts with 1% of the grid points above the level; at least 3σ keeps solvent noise out.
  return Math.min(max - 1e-6 * (max - min), Math.max(percentile(0.99), mean + 3 * sigma));
}

export const toSigma = (stats, value) => (stats.sigma > 0 ? (value - stats.mean) / stats.sigma : 0);
export const fromSigma = (stats, s) => stats.mean + s * stats.sigma;

export function fmtLevel(value) {
  const a = Math.abs(value);
  if (a !== 0 && (a < 1e-3 || a >= 1e5)) return value.toExponential(3);
  const text = value.toPrecision(4);
  return text.includes('.') && !text.includes('e') ? text.replace(/0+$/, '').replace(/\.$/, '') : text;
}
