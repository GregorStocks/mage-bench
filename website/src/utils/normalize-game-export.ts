import type { GameExportV9 } from '../types/game-export';

const CURRENT_VERSION = 9;

function invariant(condition: unknown, message: string): asserts condition {
  if (!condition) {
    throw new Error(message);
  }
}

function objectValue(value: unknown, context: string): Record<string, unknown> {
  invariant(value != null && typeof value === 'object' && !Array.isArray(value), `${context} must be an object`);
  return value as Record<string, unknown>;
}

function arrayValue(value: unknown, context: string): unknown[] {
  invariant(Array.isArray(value), `${context} must be an array`);
  return value;
}

export function normalizeGameExport(data: unknown): GameExportV9 {
  const raw = objectValue(data, 'game export');
  const version = raw.version;
  invariant(typeof version === 'number', 'game export version must be a number');
  invariant(version === CURRENT_VERSION, `unsupported game export version ${String(version)}`);
  arrayValue(raw.players, 'players');
  arrayValue(raw.snapshots, 'snapshots');
  arrayValue(raw.actions, 'actions');
  arrayValue(raw.llm_events, 'llm_events');
  if (raw.annotations != null) {
    arrayValue(raw.annotations, 'annotations');
  }
  if (raw.decisions != null) {
    arrayValue(raw.decisions, 'decisions');
  }
  if (raw.errors != null) {
    arrayValue(raw.errors, 'errors');
  }
  return raw as unknown as GameExportV9;
}
