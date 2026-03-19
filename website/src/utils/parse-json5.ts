/**
 * Fast JSON5 parser for the subset of JSON5 that dumps_json5() produces:
 * trailing commas and backslash-newline line continuations in strings.
 *
 * Strips these features and delegates to the native JSON.parse() for speed.
 * The pure-JS json5 npm package is too slow for bulk game export loading
 * (~375 files, 1 GB).
 */
export function parseJSON5(text: string): unknown {
  // Strip backslash-newline line continuations
  const stripped = text.replaceAll('\\\n', '');
  // Remove trailing commas before } and ]
  const clean = stripped.replace(/,(\s*[\]}])/g, '$1');
  return JSON.parse(clean);
}
