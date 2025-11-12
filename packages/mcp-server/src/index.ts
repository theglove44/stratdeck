import { addJournalNote } from "@stratdeck/data";
import { stdin, stdout } from "node:process";
import * as readline from "node:readline";
import { positionsList } from "./handlers/positionsList";
import { rollSimulate } from "./handlers/rollSimulate";
import { spreadsScan } from "./handlers/spreadsScan";
import { chainsSnapshot, streamSubscribe } from "./handlers/liveData";
import { JournalWriteArgs, JournalWriteResult } from "./schemas";
import { safe } from "./util";

const rl = readline.createInterface({ input: stdin, output: stdout });
let nextId = 0;

const tools = {
  "positions.list": {
    handler: (params: any) => positionsList(params?.arguments),
    inputSchema: "positionsList",
    outputSchema: "PositionsListResult"
  },
  "spreads.scan": {
    handler: (params: any) => spreadsScan(params?.arguments),
    inputSchema: "SpreadsScanArgs",
    outputSchema: "SpreadsScanResult"
  },
  "roll.simulate": {
    handler: (params: any) => rollSimulate(params?.arguments),
    inputSchema: "RollSimArgs",
    outputSchema: "RollSimResult"
  },
  "journal.write": {
    handler: async (params: any) => {
      const a = JournalWriteArgs.parse(params?.arguments ?? {});
      await addJournalNote(a.strategyId, a.note, a.tag ?? 'note');
      return JournalWriteResult.parse({ ok: true });
    },
    inputSchema: "JournalWriteArgs",
    outputSchema: "JournalWriteResult"
  },
  "tasty.stream.subscribe": {
    handler: (params: any) => streamSubscribe(params?.arguments),
    inputSchema: "StreamSubscribeArgs",
    outputSchema: "StreamSubscribeResult"
  },
  "tasty.chains.snapshot": {
    handler: (params: any) => chainsSnapshot(params?.arguments),
    inputSchema: "ChainsSnapshotArgs",
    outputSchema: "ChainsSnapshotResult"
  }
} as const;

function send(obj: any) { stdout.write(JSON.stringify(obj) + "\n"); }

rl.on("line", async (line) => {
  if (!line.trim()) return;
  let msg: any;
  try { msg = JSON.parse(line); } catch { return; }
  const { id, method, params } = msg;

  if (method === "tools/list") {
    const list = Object.keys(tools).map(name => ({
      name,
      title: name.replace(".", " ").replace(/\b\w/g, s => s.toUpperCase()),
      description: "MCP tool",
      inputSchema: {}, outputSchema: {}
    }));
    return send({ jsonrpc: "2.0", id, result: { tools: list } });
  }

  if (method === "tools/call") {
    const name = params?.name;
    const tool = (tools as any)[name];
    if (!tool) return send({ jsonrpc: "2.0", id, error: { code: "NO_SUCH_TOOL", message: name } });
    try {
      const result = await safe(name, async () => tool.handler(params));
      return send({
        jsonrpc: "2.0",
        id,
        result: {
          content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
          structuredContent: result
        }
      });
    } catch (e: any) {
      return send({ jsonrpc: "2.0", id, error: { code: e?.code ?? "ERROR", message: e?.message ?? String(e) } });
    }
  }
});
