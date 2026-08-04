const fs = require('fs');
const html = fs.readFileSync('web/index.html', 'utf8');
const scriptRegex = /<script>([\s\S]*?)<\/script>/gi;
let match;
while ((match = scriptRegex.exec(html)) !== null) {
  if (match[1].length > 1000) {
    const lines = match[1].split('\n');
    let inString = null;
    let stringStart = null;
    let inComment = null;
    
    for (let lineIdx = 0; lineIdx < lines.length; lineIdx++) {
      const line = lines[lineIdx];
      let colIdx = 0;
      while (colIdx < line.length) {
        const char = line[colIdx];
        const nextChar = line[colIdx + 1];
        
        if (inComment === 'line') {
          break;
        }
        
        if (inComment === 'block') {
          if (char === '*' && nextChar === '/') {
            inComment = null;
            colIdx += 2;
            continue;
          }
          colIdx++;
          continue;
        }
        
        if (inString) {
          if (char === '\\') {
            colIdx += 2;
            continue;
          }
          if (char === inString) {
            // Close string
            // For backticks, show if they span many lines
            if (inString === '`' && (lineIdx + 1 - stringStart.line) > 50) {
              console.log(`Backtick spanning ${lineIdx + 1 - stringStart.line} lines: open at ${stringStart.line}:${stringStart.col}, close at ${lineIdx + 1}:${colIdx + 1}`);
            }
            inString = null;
            stringStart = null;
          }
          colIdx++;
          continue;
        }
        
        if (char === '/' && nextChar === '/') {
          inComment = 'line';
          break;
        }
        if (char === '/' && nextChar === '*') {
          inComment = 'block';
          colIdx += 2;
          continue;
        }
        
        if (char === '"' || char === "'" || char === '`') {
          inString = char;
          stringStart = { line: lineIdx + 1, col: colIdx + 1 };
        }
        colIdx++;
      }
      if (inComment === 'line') {
        inComment = null;
      }
      
      // If single/double quote is unclosed at end of line, it's a syntax error!
      if (inString && inString !== '`') {
        console.log(`Error: Single/double quote '${inString}' unclosed at end of line ${lineIdx + 1}:${line.length}. Open at ${stringStart.line}:${stringStart.col}`);
        // Reset to prevent cascade errors
        inString = null;
        stringStart = null;
      }
    }
    
    if (inString) {
      console.log(`Unclosed string: ${inString} opened at ${stringStart.line}:${stringStart.col}`);
    }
    break;
  }
}
