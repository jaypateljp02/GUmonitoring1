const fs = require('fs');
const acorn = require('acorn');

const html = fs.readFileSync('web/index.html', 'utf8');
const scriptRegex = /<script>([\s\S]*?)<\/script>/gi;
let match;
let code = "";
let scriptOffset = 0;

while ((match = scriptRegex.exec(html)) !== null) {
  if (match[1].length > 1000) {
    code = match[1];
    scriptOffset = html.indexOf(match[1]);
    break;
  }
}

const tokens = [];
try {
  for (let token of acorn.tokenizer(code, { ecmaVersion: 2020 })) {
    tokens.push(token);
  }
} catch (err) {
  console.log("Tokenizer error:", err.message);
}

let braceStack = [];
let reported = 0;

for (let token of tokens) {
  const type = token.type.label;
  if (type === '{' || type === '${' || type === '(' || type === '[') {
    braceStack.push({ type, pos: token.start });
  } else if (type === '}' || type === ')' || type === ']') {
    if (braceStack.length === 0) {
      const prefix = html.substring(0, scriptOffset + token.start);
      const lineNum = prefix.split('\n').length;
      console.log(`FIRST ERROR: Extra close brace '${type}' at Line ${lineNum}: ${html.split('\n')[lineNum - 1].trim()}`);
      reported++;
      if (reported >= 5) break;
      continue;
    }
    const top = braceStack.pop();
    const matchType = ((top.type === '{' || top.type === '${') && type === '}') ||
                      (top.type === '(' && type === ')') ||
                      (top.type === '[' && type === ']');
    if (!matchType) {
      const prefixOpen = html.substring(0, scriptOffset + top.pos);
      const lineOpenNum = prefixOpen.split('\n').length;
      const prefixClose = html.substring(0, scriptOffset + token.start);
      const lineCloseNum = prefixClose.split('\n').length;
      
      console.log(`FIRST ERROR: Mismatch: open '${top.type}' at Line ${lineOpenNum} (content: ${html.split('\n')[lineOpenNum - 1].trim()}) closed by '${type}' at Line ${lineCloseNum} (content: ${html.split('\n')[lineCloseNum - 1].trim()})`);
      reported++;
      if (reported >= 5) break;
    }
  }
}

console.log("\nUnclosed tokens count:", braceStack.length);
braceStack.forEach(t => {
  const prefix = html.substring(0, scriptOffset + t.pos);
  const lineNum = prefix.split('\n').length;
  const line = html.split('\n')[lineNum - 1];
  console.log(`Unclosed '${t.type}' at Line ${lineNum}: ${line.trim()}`);
});
