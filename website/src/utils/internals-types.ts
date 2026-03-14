export interface InternalsPlayerRecord {
  key: string;
  modelName: string;
  won: boolean;
  timedOut: boolean;
  costUsd: number;
  promptTokens: number;
  completionTokens: number;
  cachedTokens: number;
  reasoningTokens: number;
  toolCallsOk: number;
  toolCallsFailed: number;
  thinkingTimeSecs: number;
  responses: number;
  timeouts: number;
  otherErrors: number;
  contextResets: number;
  latencyP50: number | null;
}

export interface InternalsGameRecord {
  id: string;
  ts: string;
  epoch: number;
  format: string;
  players: InternalsPlayerRecord[];
}

export interface InternalsTrendData {
  games: InternalsGameRecord[];
}

export interface ModelStatsEpochBucket {
  gamesPlayed: number;
  totalCostUsd: number;
  totalPromptTokens: number;
  totalCompletionTokens: number;
  totalCachedTokens?: number;
  totalReasoningTokens?: number;
  successfulResponses: number;
  contextResets: number;
  latencySamples?: number;
  latencyP50?: number;
  latencyP95?: number;
  errors: Record<string, number>;
}

export interface ModelStatsModel {
  modelName: string;
  provider: string;
  epochs: Record<string, ModelStatsEpochBucket>;
}

export interface ModelStatsData {
  models: Record<string, ModelStatsModel>;
}

export interface BlunderRun {
  gameId: string;
  ts: string;
  version: number;
  model: string;
  decisionsAnalyzed: number;
  promptTokens: number;
  completionTokens: number;
  cachedTokens: number;
  costUsd: number;
}

export interface BlunderInternalsData {
  runs: BlunderRun[];
}

export interface GoldenTestScenario {
  name: string;
  snapshots: number;
  turns: number;
}
