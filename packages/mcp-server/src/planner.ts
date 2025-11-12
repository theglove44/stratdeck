import { z } from 'zod';

export const plannerSystemPrompt = `
You are StratDeck Planner.
Use only the registered MCP tools.

Workflow per request:
1. Determine the minimal tool calls needed to satisfy the user.
2. Execute those tool calls. Do not fabricate results.
3. Respond with:
   - A concise natural-language summary (<= 3 sentences) citing the tool outcomes.
   - An ActionPlan JSON object that lists the tool calls you issued (names + key args + outcome)
     and optional human follow-up actions.

Decision preferences:
- Spreads with higher credit/width ratios outrank others.
- Favor OPEN positions with DTE between 7 and 14 days when possible.
- State explicit reasoning for selections.

Hard rules:
- Never answer from prior knowledge. Always call tools for fresh data.
- If a required tool is missing, return an error in ActionPlan and stop.
- Final reply MUST end with a JSON block labelled ActionPlan that validates against the schema.
- Do not invent tools, keys, or fields. Stick to the schema exactly.
`.trim();

export const ActionPlanSchema = z.object({
  toolCalls: z.array(
    z.object({
      name: z.string(),
      arguments: z.record(z.unknown()),
      status: z.enum(['completed', 'failed']),
      resultRef: z.string().optional(),
      notes: z.string().optional(),
    })
  ),
  humanFollowUp: z.array(z.string()).default([]),
});

export type ActionPlan = z.infer<typeof ActionPlanSchema>;

export const PlannerResponseSchema = z.object({
  summary: z.string().max(480),
  actionPlan: ActionPlanSchema,
});

export type PlannerResponse = z.infer<typeof PlannerResponseSchema>;

export function parsePlannerResponse(raw: string): PlannerResponse {
  try {
    const jsonStart = (() => {
      let depth = 0;
      let inString = false;
      for (let i = raw.length - 1; i >= 0; i -= 1) {
        const char = raw[i];
        if (char === '"' && raw[i - 1] !== '\\') {
          inString = !inString;
        }
        if (inString) continue;
        if (char === '}') {
          depth += 1;
        } else if (char === '{') {
          depth -= 1;
          if (depth === 0) return i;
        }
      }
      return -1;
    })();
    if (jsonStart === -1) {
      throw new Error('Missing ActionPlan JSON block.');
    }
    const jsonText = raw.slice(jsonStart).trim();
    const parsed = JSON.parse(jsonText);
    return PlannerResponseSchema.parse(parsed);
  } catch (error) {
    throw new Error(`Invalid planner response: ${(error as Error).message}`);
  }
}
