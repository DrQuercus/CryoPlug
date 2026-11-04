// Decodes the maps the viewer downloads off the main thread (a 400³ map is 64 million values): the page keeps
// responding while a large map arrives. Messages: { id, buffer } in, { id, buffer, offset, length } or { id, error } out.
import { decodeMap } from './decode.js';

self.onmessage = (e) => {
  const { id, buffer } = e.data;
  try {
    const out = decodeMap(buffer);
    self.postMessage({ id, buffer: out.buffer, offset: out.byteOffset, length: out.byteLength }, [out.buffer]);
  } catch (err) {
    self.postMessage({ id, error: String(err?.message || err) });
  }
};
