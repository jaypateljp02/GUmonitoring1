const fs = require('fs');
const html = fs.readFileSync('web/index.html', 'utf8');
const lines = html.split('\n');

// Extract renderTapoPlugs function: lines 1713 to 2092 (1-indexed)
const blockLines = lines.slice(1712, 2092);

let bracesStack = [];
for (let i = 0; i < blockLines.length; i++) {
  const line = blockLines[i];
  const lineNum = 1713 + i;
  for (let j = 0; j < line.length; j++) {
    const char = line[j];
    if (char === '{') {
      bracesStack.push({ char, line: lineNum, col: j + 1 });
    } else if (char === '}') {
      if (bracesStack.length === 0) {
        console.log(`Extra close brace '}' at line ${lineNum}:${j + 1}`);
      } else {
        bracesStack.pop();
      }
    }
  }
}

console.log("Remaining unclosed braces:", bracesStack.length);
bracesStack.forEach(b => {
  console.log(`Unclosed '{' at line ${b.line}:${b.col} -> ${lines[b.line - 1].trim()}`);
});
