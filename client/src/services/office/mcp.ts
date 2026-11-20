export type Pair = { id: string; key: string; value: string }
export type Argument = { id: string; value: string }
export type Connection = { type: 'http'; url: string; headers: Pair[] } | { type: 'stdio'; command: string; args: Argument[]; env: Pair[] }
export type McpService = { name: string; enabled: boolean; connection: Connection; transport?: string; revision?: string; status?: string; count?: number; error?: string }

export type McpRow = {
  name: string; enabled: boolean; revision: string; type: string; status: string; count: number; error: string;
  url: string; command: string; args: string[]; env: Record<string, string>; headers: Record<string, string>
}
export type McpChange = { action: 'create' | 'edit' | 'enable' | 'disable' | 'delete' | 'reconnect';
  revision?: string; connection?: { type: 'http'; url: string; headers: Record<string, string> } |
    { type: 'stdio'; command: string; args: string[]; env: Record<string, string> } }

const pairs = (values: Record<string, string>) => Object.entries(values).map(([key, value]) => ({ id: crypto.randomUUID(), key, value }))
const values = (rows: Pair[]) => Object.fromEntries(rows.map(row => [row.key, row.value]))

export function readMcpRow(row: McpRow): McpService {
  return { ...row, transport: row.type, connection: row.type === 'stdio'
    ? { type: 'stdio', command: row.command, args: row.args.map(value => ({ id: crypto.randomUUID(), value })), env: pairs(row.env) }
    : { type: 'http', url: row.url, headers: pairs(row.headers) } }
}

export function writeConnection(connection: Connection): McpChange['connection'] {
  return connection.type === 'http' ? { type: 'http', url: connection.url, headers: values(connection.headers) }
    : { type: 'stdio', command: connection.command, args: connection.args.map(arg => arg.value), env: values(connection.env) }
}
