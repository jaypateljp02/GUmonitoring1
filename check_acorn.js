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

try {
  acorn.parse(code, { ecmaVersion: 2020 });
  console.log("Acorn: SYNTAX IS 100% OK!");
} catch (err) {
  console.log("Acorn: SYNTAX ERROR FOUND!");
  console.log("Message:", err.message);
  console.log("Position:", err.pos);
  console.log("Loc (line, col):", err.loc);
  
  // Find line number in original web/index.html
  const prefix = html.substring(0, scriptOffset + err.pos);
  const originalLineNum = prefix.split('\n').length;
  console.log("Original Line Number in web/index.html:", originalLineNum);
  
  const originalLines = html.split('\n');
  console.log("Snippet:");
  for (let i = Math.max(0, originalLineNum - 4); i < Math.min(originalLines.length, originalLineNum + 3); i++) {
    console.log(`${i + 1}: ${originalLines[i]}`);
  }
}
