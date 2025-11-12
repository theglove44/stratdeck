import { spawn, type ChildProcess } from 'node:child_process';
import process from 'node:process';
import { resolve } from 'node:path';
import type { ZodTypeAny, infer as ZodInfer } from 'zod';

type PendingResolver = {
  resolve: (value: any) => void;
  reject: (error: Error) => void;
};

function extractStructured(result: any) {
  if (result?.structuredContent !== undefined) {
    return result.structuredContent;
  }
  const textBlock = Array.isArray(result?.content)
    ? result.content.find((block: any) => block?.type === 'text' && typeof block?.text === 'string')
    : undefined;
  if (textBlock) {
    try {
      return JSON.parse(textBlock.text);
    } catch (error) {
      throw new Error(`Failed to parse tool text output as JSON: ${(error as Error).message}`);
    }
  }
  throw new Error('Tool result did not include structuredContent or JSON text block.');
}

export class CliMcpClient {
  private readonly proc: ChildProcess;
  private readonly stdout: NodeJS.ReadableStream;
  private readonly stdin: NodeJS.WritableStream;
  private readonly pending = new Map<number, PendingResolver>();
  private buffer = '';
  private nextId = 1;
  private closed = false;

  private constructor(proc: ChildProcess) {
    this.proc = proc;
    if (!proc.stdout || !proc.stdin) {
      throw new Error('MCP server process did not provide stdio pipes.');
    }
    this.stdout = proc.stdout;
    this.stdin = proc.stdin;
    this.stdout.setEncoding('utf8');
    this.stdout.on('data', chunk => this.onData(chunk));
    this.proc.on('close', () => this.onClose());
    this.proc.on('error', (error) => this.onError(error instanceof Error ? error : new Error(String(error))));
  }

  static async create(options?: { dbPath?: string; cwd?: string }) {
    const dbPath = options?.dbPath ?? process.env.STRATDECK_DB ?? resolve(process.cwd(), 'stratdeck.db');
    const cwd = options?.cwd ?? process.cwd();

    return new Promise<CliMcpClient>((resolveClient, rejectClient) => {
      const proc = spawn('pnpm', ['--dir', 'packages/mcp-server', 'start'], {
        cwd,
        env: {
          ...process.env,
          STRATDECK_DB: dbPath,
        },
        stdio: ['pipe', 'pipe', 'inherit'],
      });

      const handleError = (error: Error) => {
        proc.removeListener('spawn', handleSpawn);
        rejectClient(error);
      };

      const handleSpawn = () => {
        proc.removeListener('error', handleError);
        resolveClient(new CliMcpClient(proc));
      };

      proc.once('error', handleError);
      proc.once('spawn', handleSpawn);
    });
  }

  async listTools() {
    const response = await this.sendRequest('tools/list', {});
    if (response.error) {
      throw new Error(response.error.message ?? 'tools/list failed');
    }
    return response.result;
  }

  async callTool(name: string, args: unknown) {
    const response = await this.sendRequest('tools/call', { name, arguments: args ?? {} });
    if (response.error) {
      throw new Error(response.error.message ?? 'tools/call failed');
    }
    return response.result;
  }

  async callToolParsed<Z extends ZodTypeAny>(name: string, args: unknown, schema: Z): Promise<ZodInfer<Z>> {
    const raw = await this.callTool(name, args);
    const structured = extractStructured(raw);
    return schema.parse(structured);
  }

  private async sendRequest(method: string, params: Record<string, unknown>) {
    if (this.closed) {
      throw new Error('MCP client already closed.');
    }
    const id = this.nextId++;
    const payload = JSON.stringify({ jsonrpc: '2.0', id, method, params });
    return new Promise<any>((resolvePromise, rejectPromise) => {
      this.pending.set(id, { resolve: resolvePromise, reject: rejectPromise });
      this.stdin.write(payload + '\n', (err) => {
        if (err) {
          const resolver = this.pending.get(id);
          if (resolver) {
            this.pending.delete(id);
            resolver.reject(err instanceof Error ? err : new Error(String(err)));
          }
        }
      });
    });
  }

  private onData(chunk: string) {
    this.buffer += chunk;
    let idx: number;
    while ((idx = this.buffer.indexOf('\n')) >= 0) {
      const line = this.buffer.slice(0, idx).trim();
      this.buffer = this.buffer.slice(idx + 1);
      if (!line) continue;
      let message: any;
      try {
        message = JSON.parse(line);
      } catch {
        continue;
      }
      if (message?.id !== undefined) {
        const resolver = this.pending.get(Number(message.id));
        if (resolver) {
          this.pending.delete(Number(message.id));
          resolver.resolve(message);
        }
      }
    }
  }

  private onClose() {
    this.closed = true;
    const error = new Error('MCP server process closed.');
    for (const resolver of this.pending.values()) {
      resolver.reject(error);
    }
    this.pending.clear();
  }

  private onError(error: Error) {
    for (const resolver of this.pending.values()) {
      resolver.reject(error);
    }
    this.pending.clear();
  }

  async dispose() {
    if (this.closed) return;
    this.closed = true;
    this.stdin.end();
    this.proc.kill();
  }
}
