/**
 * Stub RAG: intentionally minimal for v0.1.
 * In future versions, we will embed and recall notes.
 */

export async function retrieve(_q: string) {
  return [];
}
export async function embedAndStore(_text: string) {
  return { ok: true };
}
