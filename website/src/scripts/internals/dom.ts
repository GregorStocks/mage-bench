export function getRequiredElement<T extends Element>(root: Document | Element, selector: string): T {
  const element = root.querySelector(selector);
  if (!(element instanceof Element)) {
    throw new Error(`Missing required element: ${selector}`);
  }
  return element as T;
}
