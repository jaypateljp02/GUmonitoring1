const fs = require('fs');
const html = fs.readFileSync('web/index.html', 'utf8');
const scriptRegex = /<script>([\s\S]*?)<\/script>/gi;
let match;
while ((match = scriptRegex.exec(html)) !== null) {
  if (match[1].length > 1000) {
    const lines = match[1].split('\n');
    const line = lines[3250];
    console.log("Analyzing line:", line);
    let inString = null;
    for (let i = 0; i < line.length; i++) {
      const char = line[i];
      if (inString) {
        if (char === '\\') {
          i++;
          continue;
        }
        if (char === inString) {
          console.log(`Close string '${inString}' at index ${i}`);
          inString = null;
        }
      } else if (char === '"' || char === "'" || char === '`') {
        inString = char;
        console.log(`Open string '${inString}' at index ${i}`);
      }
    }
    break;
  }
}
