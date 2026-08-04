const fs = require('fs');
const path = require('path');

const htmlPath = path.join(__dirname, 'web', 'index.html');
const html = fs.readFileSync(htmlPath, 'utf8');

// Match <script> blocks
const scriptRegex = /<script>([\s\S]*?)<\/script>/gi;
let match;
let scriptIndex = 1;

while ((match = scriptRegex.exec(html)) !== null) {
  const code = match[1];
  console.log(`Checking script block #${scriptIndex}...`);
  try {
    // Compile using Node VM module
    const vm = require('vm');
    new vm.Script(code);
    console.log(`Block #${scriptIndex} is SYNTAX OK!`);
  } catch (err) {
    console.error(`Block #${scriptIndex} has SYNTAX ERROR:`, err.message);
    // Print lines around error
    const lines = code.split('\n');
    console.error(err.stack);
  }
  scriptIndex++;
}
