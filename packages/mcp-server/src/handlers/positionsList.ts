import { listPositions } from "@stratdeck/data";
import { PositionsListArgs, PositionsListResult } from "../schemas";
import { dteFromISO } from "../util";

export async function positionsList(args: unknown) {
    const a = PositionsListArgs.parse(args ?? {});
    const rows = await listPositions();
    const withDTE = rows.map(r => ({ ...r, dte: dteFromISO(r.expiration) }));
    const filtered = withDTE.filter(r =>
        (!a.type || r.strategy_type === a.type) &&
        (!a.dte_lt || r.dte < a.dte_lt)
    );
    return PositionsListResult.parse({
        meta: { tool: "positions.list", version: "0.1.1" },
        positions: filtered
    });
}
