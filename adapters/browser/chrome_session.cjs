// Connect to the user's running Chrome through its explicit consent flow.
// No profile copying, cookie export, browser restart, or security-check bypass.
const { spawn } = require('child_process');
const readline = require('readline');
const server = spawn('npx', ['--yes', 'chrome-devtools-mcp@latest', '--autoConnect',
  '--no-usage-statistics'], {stdio:['pipe','pipe','pipe']});
const waiting = new Map();
let nextId = 0;
function request(method, params = {}) {
  const id = ++nextId;
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => { waiting.delete(id); reject(new Error('Browser connection timed out; check Chrome permission dialog')); }, 45000);
    waiting.set(id, value => { clearTimeout(timeout); value.error ? reject(new Error(value.error.message)) : resolve(value.result); });
    server.stdin.write(JSON.stringify({jsonrpc:'2.0',id,method,params})+'\n');
  });
}
readline.createInterface({input:server.stdout}).on('line', line => {
  try {const value = JSON.parse(line); if (waiting.has(value.id)) {waiting.get(value.id)(value);waiting.delete(value.id);}} catch {}
});
server.stderr.on('data', data => process.stderr.write(data));
server.on('exit', () => { for (const resolve of waiting.values()) resolve({error:{message:'Chrome connection closed'}}); waiting.clear(); });
(async () => {
  await request('initialize', {protocolVersion:'2024-11-05', capabilities:{}, clientInfo:{name:'job-search-browser-adapter',version:'1.0.0'}});
  server.stdin.write(JSON.stringify({jsonrpc:'2.0',method:'notifications/initialized'})+'\n');
  const available = await request('tools/list');
  process.stdout.write(JSON.stringify({ready:true,tools:available.tools})+'\n');
  for await (const line of readline.createInterface({input:process.stdin})) {
    if (!line.trim()) continue;
    try {
      const command = JSON.parse(line);
      if (command.op === 'close') break;
      const result = await request('tools/call', {name:command.name,arguments:command.arguments || {}});
      process.stdout.write(JSON.stringify(result)+'\n');
    } catch (error) {process.stdout.write(JSON.stringify({error:error.message})+'\n');}
  }
  server.kill(); // Disconnect only; never close the user's Chrome.
})().catch(error => {process.stderr.write(error.message+'\n');server.kill();process.exitCode=1;});
