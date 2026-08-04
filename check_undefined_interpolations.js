const fs = require('fs');
const html = fs.readFileSync('web/index.html', 'utf8');

const regex = /\$\{([^}]+)\}/g;
let match;
const suspicious = [];

while ((match = regex.exec(html)) !== null) {
  const expr = match[1].trim();
  // Check if expression accesses properties without fallback or default value
  if (/\b(name|type|description|status|state|apower|voltage|current|kwh|today_energy|month_energy|id|device_id)\b/.test(expr)) {
    if (!expr.includes('||') && !expr.includes('?') && !expr.includes('&&') && !expr.includes('Math') && !expr.includes('parseFloat') && !expr.includes('toFixed') && !expr.includes('Boolean')) {
      const lineNum = html.substring(0, match.index).split('\n').length;
      suspicious.push({ lineNum, expr, line: html.split('\n')[lineNum - 1].trim() });
    }
  }
}

console.log(`Found ${suspicious.length} potentially unsafe template interpolations:`);
suspicious.forEach(s => {
  console.log(`Line ${s.lineNum}: ${s.expr}  ==>  ${s.line}`);
});
