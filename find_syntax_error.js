const fs = require('fs');
const vm = require('vm');

const html = fs.readFileSync('web/index.html', 'utf8');
const scriptRegex = /<script>([\s\S]*?)<\/script>/gi;
let match;
let code = "";
while ((match = scriptRegex.exec(html)) !== null) {
  if (match[1].length > 1000) {
    code = match[1];
    break;
  }
}

const lines = code.split('\n');

function checkRange(startLine, endLine) {
  // Try compiling lines from startLine to endLine
  // We wrap it in a function body to check if it's syntactically valid JS
  const chunk = lines.slice(startLine, endLine).join('\n');
  try {
    new vm.Script('function test_compile() {\n' + chunk + '\n}');
    return true;
  } catch (err) {
    return false;
  }
}

console.log("Total script lines:", lines.length);

// Binary search to find the minimal range that fails
let low = 0;
let high = lines.length;

if (checkRange(0, lines.length)) {
  console.log("Entire script compiles cleanly inside function wrap!");
  process.exit(0);
}

// Let's find where the syntax error is
// We slide a window of lines and find the first line that breaks compilation when included.
// Since compilation checks braces/quotes matching, a single unclosed token at line X will cause the range [0, X] or [X, end] to fail.
// Let's find the first line X such that lines [0, X] is valid, but [0, X+1] is invalid!
// Note: [0, X] might not compile because of unclosed functions, but we can do it by checking if VM compile error is specifically "Unexpected end of input" vs syntax error.
// Let's use a simpler method: binary search a window of complete function definitions or lines!
// Actually, let's just find the first character that fails parsing.
// We can use the 'acorn' parser or standard node error line number.
// Node's vm.Script error object has a line number! Let's print the stack trace/line number of the error.
try {
  new vm.Script(code);
} catch (err) {
  console.log("Error line from node stack:", err.stack);
  console.log("Error message:", err.message);
  // Let's parse with acorn if we want, but let's look at the error details
}
