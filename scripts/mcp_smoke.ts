import { spawn } from "node:child_process";
import { resolve } from "node:path";

const defaultDbPath = process.env.STRATDECK_DB ?? resolve(process.cwd(), "stratdeck.db");

const proc = spawn("pnpm", ["--dir", "packages/mcp-server", "start"], {
    stdio: ["pipe", "pipe", "inherit"],
    env: { ...process.env, STRATDECK_DB: defaultDbPath },
});

function send(id: number, method: string, params: any = {}) {
    const msg = JSON.stringify({ jsonrpc: "2.0", id, method, params });
    proc.stdin.write(msg + "\n"); // newline-delimited JSON is common for stdio servers
}

let buffer = "";
proc.stdout.setEncoding("utf8");
proc.stdout.on("data", (chunk) => {
    buffer += chunk;
    let idx;
    while ((idx = buffer.indexOf("\n")) >= 0) {
        const line = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 1);
        if (!line.trim()) continue;
        try {
            const obj = JSON.parse(line);
            console.log("←", JSON.stringify(obj, null, 2));
        } catch (e) {
            console.error("non-JSON line:", line);
        }
    }
});

// 1) list tools
send(1, "tools/list");

// 2) positions.list (no filters; your server adapts)
setTimeout(() => send(2, "tools/call", { name: "positions.list", arguments: {} }), 150);

// 3) spreads.scan
setTimeout(
    () => send(3, "tools/call", { name: "spreads.scan", arguments: { type: "IC" } }),
    300
);

// 4) journal.write
setTimeout(
    () =>
        send(4, "tools/call", {
            name: "journal.write",
            arguments: { strategyId: "IC_SPY_XXXX-XX-XX", note: "smoke test journaling" },
        }),
    450
);

// 5) roll.simulate
setTimeout(
    () => send(5, "tools/call", {
        name: "roll.simulate",
        arguments: { strategyId: "IC_SPY_2025-10-29", targetDTE: 33, width: 5, prefer: "credit" }
    }),
    600
);

// quit after a bit
setTimeout(() => proc.kill(), 1500);
