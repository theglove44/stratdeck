import { listPositions, listSpreads } from '@stratdeck/data';
import { z } from 'zod';

/**
 * Minimal local "tools" interface. In v0.3, this will become
 * a proper MCP server. For now, we just re-export functions
 * with Zod schemas to validate inputs/outputs.
 */

export const PositionsListInput = z.object({
  type: z.string().optional(),
  dte_lt: z.number().optional()
});
export type PositionsListInput = z.infer<typeof PositionsListInput>;

export async function positionsList(_input: PositionsListInput) {
  const rows = await listPositions();
  return rows;
}

export const SpreadsListInput = z.object({
  type: z.enum(['PCS','CCS','IC'])
});
export type SpreadsListInput = z.infer<typeof SpreadsListInput>;

export async function spreadsList(input: SpreadsListInput) {
  return listSpreads(input.type);
}
