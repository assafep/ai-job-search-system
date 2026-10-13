// Interactive local browser used to verify and learn site flows. Ordinary
// Playwright controls only: no CAPTCHA solving or private API calls.
const { chromium } = require('playwright');
const readline = require('readline');
const fs = require('fs');
const path = require('path');

const site = process.argv[2];
if (!['jobify', 'rvc', 'echojobs'].includes(site)) throw new Error('Unknown site');
const recipes = require('./site_recipes.json')[site];
const directory = path.resolve(__dirname, '../../config/browser-sessions', site);
fs.mkdirSync(directory, { recursive: true, mode: 0o700 });
fs.chmodSync(directory, 0o700);
const allowed = new Set(['jobify360.co.il', 'www.jobify360.co.il', 'app.rvc.global',
  'echojobs.io', 'accounts.google.com', 'www.linkedin.com', 'il.linkedin.com',
  'www.comeet.com', 'www.comeet.co']);

function safeUrl(url) {
  const parsed = new URL(url);
  return parsed.origin + parsed.pathname; // never log session/query tokens
}

async function snapshot(page, selector) {
  const scope = selector ? page.locator(selector) : page.locator('body');
  const dialogs = page.locator('[role="dialog"]:visible,.modal.show:visible');
  const text = selector ? await scope.first().innerText()
    : await dialogs.count() ? await dialogs.last().innerText() : await page.locator('body').innerText();
  return {
    url: safeUrl(page.url()), title: await page.title(),
    text: text.slice(-7000),
    controls: await (selector ? scope : page).locator('input,select,textarea,button,a,label').evaluateAll(nodes =>
      nodes.filter(e => e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden')
        .map(e => ({tag: e.tagName, type: e.type, id: e.id, name: e.name,
          label: (e.innerText || e.getAttribute('aria-label') || '').trim().slice(0,180),
          placeholder: e.placeholder, required: e.required, disabled: e.disabled,
          ariaLabel: e.getAttribute('aria-label'), className: e.className,
          value: e.type === 'password' ? undefined : e.value,
          href: e.href ? new URL(e.href).origin + new URL(e.href).pathname : undefined,
          for: e.htmlFor})))
  };
}

(async () => {
  const browser = await chromium.launchPersistentContext(directory, {
    executablePath: process.env.BROWSER_EXECUTABLE || '/home/k/.local/bin/google-chrome',
    headless: process.env.BROWSER_HEADLESS !== 'false',
  });
  let page = browser.pages()[0] || await browser.newPage();
  const statePath = path.join(directory, 'session-state.json');
  if (fs.existsSync(statePath)) {
    const saved = JSON.parse(fs.readFileSync(statePath, 'utf8'));
    await browser.addCookies(saved.cookies || []);
  }
  process.stdout.write(JSON.stringify({ready: true, site}) + '\n');
  const lines = readline.createInterface({input: process.stdin});
  for await (const line of lines) {
    if (!line.trim()) continue;
    let command;
    try {
      command = JSON.parse(line);
      if (command.op === 'recipe') {
        const action = recipes.actions[command.name];
        if (!action) throw new Error('Unknown or unverified recipe action');
        if (action.creates_account && command.allow_account_creation !== true)
          throw new Error('Account creation requires explicit authorization');
        command = {...action, ...(command.path ? {path:command.path} : {})};
      }
      if (command.op === 'navigate') {
        const url = new URL(command.url);
        if (!allowed.has(url.hostname)) throw new Error('Unreviewed destination');
        await page.goto(command.url, {waitUntil:'domcontentloaded', timeout:30000});
      } else if (command.op === 'click') {
        const target = command.selector ? page.locator(command.selector)
          : command.role ? page.getByRole(command.role, {name:command.text,exact:true})
          : page.getByText(command.text, {exact:true});
        await target.nth(command.index || 0).click({timeout:15000});
      } else if (command.op === 'fill') {
        await page.locator(command.selector).fill(command.value);
      } else if (command.op === 'check') {
        await page.locator(command.selector).setChecked(command.checked);
      } else if (command.op === 'upload') {
        const waiting = page.waitForEvent('filechooser', {timeout:15000});
        await page.locator(command.selector).click();
        const chooser = await waiting;
        await chooser.setFiles(command.path);
      } else if (command.op === 'screenshot') {
        await page.screenshot({path:path.join(directory,'current.png'),fullPage:true});
        process.stdout.write(JSON.stringify({screenshot:path.join(directory,'current.png')})+'\n');
        continue;
      } else if (command.op === 'close') {
        await browser.storageState({path:statePath});
        fs.chmodSync(statePath,0o600);
        await browser.close(); return;
      } else if (command.op !== 'inspect') throw new Error('Unknown browser operation');
      await page.waitForTimeout(command.wait_ms || 1500);
      const pages = browser.pages();
      if (pages.length > 1) page = pages[pages.length - 1];
      await browser.storageState({path:statePath});
      fs.chmodSync(statePath,0o600);
      process.stdout.write(JSON.stringify(await snapshot(page, command.selector && command.op === 'inspect' ? command.selector : null)) + '\n');
    } catch (error) {
      // Action errors may contain sensitive URLs; log only the category.
      process.stdout.write(JSON.stringify({error:error.name, operation:command && command.op,
        message:error.message.replace(/https?:\/\/[^\s]+/g,'[url]').slice(0,1800)})+'\n');
    }
  }
  await browser.close();
})().catch(() => {process.stderr.write('Browser session failed\n');process.exitCode=1;});
