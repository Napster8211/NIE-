const fs = require('fs');
const path = require('path');

const AGENTS_DIR = path.resolve(__dirname, '../app/agent/definitions');
const TOOLS_DIR = path.resolve(__dirname, '../app/tools/plugins');
const ENDPOINTS_FILE = path.resolve(__dirname, '../app/api/endpoints.py');
const TOOL_REGISTRY_FILE = path.resolve(__dirname, '../app/tools/tool_registry.py');

// 1. Parse all agents
const agentFiles = fs.readdirSync(AGENTS_DIR).filter(f => f.endsWith('.py') && !f.startsWith('__'));
const agents = [];

for (const file of agentFiles) {
  const content = fs.readFileSync(path.join(AGENTS_DIR, file), 'utf8');
  
  const nameMatch = content.match(/name\s*=\s*["']([^"']+)["']/);
  const deptIdMatch = content.match(/department_id\s*=\s*["']([^"']+)["']/);
  const deptNameMatch = content.match(/department_name\s*=\s*["']([^"']+)["']/);
  const allowedToolsMatch = content.match(/allowed_tools\s*=\s*\{([^}]+)\}/);
  
  // Find invoke_tool calls
  const invokeMatches = [...content.matchAll(/invoke_tool\s*\(\s*["']([^"']+)["']/g)].map(m => m[1]);
  
  let allowedTools = [];
  if (allowedToolsMatch) {
    allowedTools = [...allowedToolsMatch[1].matchAll(/["']([^"']+)["']/g)].map(m => m[1]);
  }
  
  agents.push({
    file,
    name: nameMatch ? nameMatch[1] : file,
    department_id: deptIdMatch ? deptIdMatch[1] : 'unassigned',
    department_name: deptNameMatch ? deptNameMatch[1] : 'Unassigned',
    allowed_tools: allowedTools,
    invoked_tools: [...new Set(invokeMatches)]
  });
}

// 2. Parse all tools defined in app/tools/plugins and app/tools/
const toolDefs = new Map();

function scanToolsDir(dir) {
  const files = fs.readdirSync(dir);
  for (const f of files) {
    const fullPath = path.join(dir, f);
    if (fs.statSync(fullPath).isDirectory()) continue;
    if (!f.endsWith('.py') || f.startsWith('__')) continue;
    
    const content = fs.readFileSync(fullPath, 'utf8');
    // Match class X(BaseTool): ... name = "..."
    const classRegex = /class\s+([A-Za-z0-9_]+)\s*\([^)]*BaseTool[^)]*\):[\s\S]*?(?:name\s*:\s*str\s*=\s*["']([^"']+)["']|name\s*=\s*["']([^"']+)["'])/g;
    let m;
    while ((m = classRegex.exec(content)) !== null) {
      const className = m[1];
      const toolName = m[2] || m[3];
      if (toolName) {
        toolDefs.set(toolName, { className, file: f });
      }
    }
  }
}

scanToolsDir(TOOLS_DIR);
scanToolsDir(path.resolve(__dirname, '../app/tools'));

// 3. Parse tools registered in endpoints.py and tool_registry.py
const registeredTools = new Set(['url_reader', 'web_search', 'web_intelligence']);

function scanRegistrations(file) {
  if (!fs.existsSync(file)) return;
  const content = fs.readFileSync(file, 'utf8');
  // tool_registry.register(ToolClass())
  const regRegex = /tool_registry\.register\s*\(\s*([A-Za-z0-9_]+)\s*\(/g;
  let m;
  while ((m = regRegex.exec(content)) !== null) {
    const className = m[1];
    // Find tool name for this class
    for (const [tName, tInfo] of toolDefs.entries()) {
      if (tInfo.className === className) {
        registeredTools.add(tName);
      }
    }
  }
  
  // Aliases
  const aliasRegex = /tool_registry\.register_alias\s*\(\s*["']([^"']+)["']\s*,\s*["']([^"']+)["']\s*\)/g;
  while ((m = aliasRegex.exec(content)) !== null) {
    registeredTools.add(m[1]);
  }
}

scanRegistrations(ENDPOINTS_FILE);
scanRegistrations(TOOL_REGISTRY_FILE);

console.log('='.repeat(70));
console.log('NAPSTERTEC AI OS - AGENT & TOOL COMPREHENSIVE AUDIT');
console.log('='.repeat(70));
console.log(`Total Agents Found: ${agents.length}`);
console.log(`Total Defined Tool Classes: ${toolDefs.size}`);
console.log(`Total Registered Tools in endpoints/registry: ${registeredTools.size}`);

// Group by Department
const depts = {};
for (const a of agents) {
  if (!depts[a.department_name]) depts[a.department_name] = [];
  depts[a.department_name].push(a);
}

let totalMissing = 0;

for (const [deptName, deptAgents] of Object.entries(depts)) {
  console.log(`\n📂 DEPARTMENT: ${deptName} (${deptAgents.length} Agents)`);
  console.log('-'.repeat(60));
  
  for (const a of deptAgents) {
    const missingAllowed = a.allowed_tools.filter(t => !registeredTools.has(t) && t !== '*');
    const missingInvoked = a.invoked_tools.filter(t => !registeredTools.has(t));
    
    const isOk = missingAllowed.length === 0 && missingInvoked.length === 0;
    const icon = isOk ? '✅' : '❌';
    
    console.log(`  ${icon} Agent: [${a.name}] (${a.file})`);
    console.log(`     Allowed Tools: [${a.allowed_tools.join(', ')}]`);
    console.log(`     Invoked Tools: [${a.invoked_tools.join(', ')}]`);
    
    if (missingAllowed.length > 0) {
      console.log(`     ⚠️ UNREGISTERED in Allowed: ${missingAllowed.join(', ')}`);
      totalMissing += missingAllowed.length;
    }
    if (missingInvoked.length > 0) {
      console.log(`     🚨 UNREGISTERED in Invoked: ${missingInvoked.join(', ')}`);
      totalMissing += missingInvoked.length;
    }
  }
}

console.log('\n' + '='.repeat(70));
if (totalMissing === 0) {
  console.log('🎉 AUDIT PASSED: ALL AGENTS HAVE 100% OF THEIR REQUIRED TOOLS REGISTERED!');
} else {
  console.log(`⚠️ AUDIT WARNING: ${totalMissing} missing tool registrations detected.`);
}
console.log('='.repeat(70));
