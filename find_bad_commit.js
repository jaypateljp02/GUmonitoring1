const { execSync } = require('child_process');
const fs = require('fs');
const vm = require('vm');

const commits = execSync('git log --oneline -- web/index.html', { encoding: 'utf8' })
  .trim()
  .split('\n')
  .map(line => line.split(' ')[0]);

console.log(`Checking ${commits.length} commits...`);

for (const commit of commits) {
  try {
    // Checkout the file from that commit
    execSync(`git checkout ${commit} -- web/index.html`);
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
    
    new vm.Script(code);
    console.log(`Commit ${commit}: SYNTAX OK`);
  } catch (err) {
    console.log(`Commit ${commit}: SYNTAX ERROR (${err.message})`);
  }
}

// Restore file to current branch HEAD
execSync('git checkout HEAD -- web/index.html');
console.log("Restored web/index.html to HEAD.");
