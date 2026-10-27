// Browser interactions belong to the external adapter, never to stage agents.
const { chromium } = require('playwright');
const fs = require('fs');

function humanVerification(text) {
  return /session verification failed due to a human check error|verify (?:that )?you(?:'re| are) human|human verification|complete (?:the |this )?(?:captcha|security check)|security checkpoint|quick security check/i.test(text);
}

async function submit(input) {
  const { application: app, destination, profile } = input;
  const outcome = { schema_version: 1, status: 'Blocked', employer: app.company, role: app.role,
    original_posting_url: app.posting_url, verified_application_url: destination.application_url,
    recruiting_platform: 'Comeet', confirmation_text: null, application_reference: null,
    submission_time: null, fields: [], attachments: [], blockers: [] };
  for (const url of [destination.application_url, destination.verification_url]) {
    if (new URL(url).hostname !== 'www.comeet.com') throw new Error('Unverified recruiting host');
  }
  const browser = await chromium.launch({ executablePath: input.chrome, headless: true });
  let submitClicked = false;
  let page;
  try {
    page = await browser.newPage();
    await page.goto(destination.verification_url, { waitUntil: 'domcontentloaded' });
    // Never assume the position is senior. Verify the exact position URL,
    // while requiring the visible careers link to name the requested role.
    const link = page.locator('a').filter({ hasText: app.role });
    await link.first().waitFor({ state: 'visible', timeout: 20000 });
    const matches = await link.evaluateAll(nodes => nodes.map(a => a.href));
    if (!matches.includes(destination.application_url)) throw new Error('Role destination is absent from the employer careers board');
    await page.goto(destination.application_url, { waitUntil: 'domcontentloaded' });
    await page.locator('#showApplyForm').click();
    let frame;
    for (let i = 0; i < 20; i++) {
      frame = page.frames().find(f => f.url().startsWith('https://www.comeet.co/') && f.url().includes('/apply'));
      if (frame && await frame.locator('#inputFirstName').count()) break;
      frame = null;
      for (const candidate of page.frames()) {
        if (candidate.url().startsWith('https://www.comeet.co/') && await candidate.locator('#inputFirstName').count()) frame = candidate;
      }
      if (frame) break;
      await page.waitForTimeout(300);
    }
    if (!frame) throw new Error('Application form did not load');
    if (humanVerification(await frame.locator('body').innerText())) {
      outcome.requires_human_verification = true;
      outcome.blockers = ['Human verification blocks form preparation; manual application is required.'];
      return outcome;
    }
    const answers = new Map((input.answers || [])
      .filter(item => item && item.label && item.value)
      .map(item => [String(item.label).trim().toLowerCase(), String(item.value)]));
    const personal = profile.personal || {};
    const fullName = personal.name || personal.full_name || '';
    const parts = fullName.trim().split(/\s+/);
    const values = {
      inputFirstName: personal.first_name || parts[0],
      inputLastName: personal.last_name || parts.slice(1).join(' '),
      inputEmail: personal.email,
      inputTel: personal.phone || personal.phone_number,
      inputLink: personal.website || personal.website_url,
    };
    const requiredMissing = [];
    for (const [id, value] of Object.entries(values)) {
      const control = frame.locator('#' + id);
      if (!await control.count()) continue;
      const label = (await frame.locator('label[for="' + id + '"]').first().innerText()).trim();
      const required = await control.getAttribute('required') !== null || await control.getAttribute('aria-required') === 'true';
      if (value) await control.fill(value);
      else if (required) requiredMissing.push(label);
      outcome.fields.push({ label, required, value: value || null, source: 'applicant_profile.personal', state: value ? 'prepared' : 'blank' });
    }
    // The prepared artifact is one combined cover-letter/resume PDF. Upload it
    // once as the resume; never misrepresent the same file as two attachments.
    // A separate cover-letter upload needs its own explicit artifact.
    for (const [id, label] of [['cv', 'Attach Resume']]) {
      if (!await frame.locator('#' + id).count()) continue;
      const chooserPromise = page.waitForEvent('filechooser', { timeout: 15000 });
      await frame.getByText(label, { exact: true }).click();
      const chooser = await chooserPromise;
      await chooser.setFiles(input.package_path);
      // Readback from the browser file control verifies the exact file chosen.
      const filenames = await frame.locator('#' + id).evaluate(e => Array.from(e.files || [], f => f.name));
      const accepted = filenames.includes(input.package_name);
      if (!accepted) throw new Error('Attachment selection was not accepted');
      await frame.getByText(input.package_name, { exact: true }).first().waitFor({ state: 'visible', timeout: 20000 });
      outcome.attachments.push({ label, filename: input.package_name, accepted });
      outcome.fields.push({ label: id === 'cv' ? 'Resume' : 'Cover Letter', required: id === 'cv', value: input.package_name, source: 'supplied package PDF', state: 'prepared' });
    }
    for (const [id, label] of [['portfolio', 'Portfolio'], ['inputNote', 'Personal note']]) {
      if (!await frame.locator('#' + id).count()) continue;
      const value = answers.get(label.toLowerCase()) || null;
      if (value) await frame.locator('#' + id).fill(value);
      outcome.fields.push({ label, required: false, value, source: value ? 'grok-4.7' : null, state: value ? 'prepared' : 'blank' });
    }
    const unknownRequired = await frame.locator('input[required],select[required],textarea[required],[aria-required="true"]').evaluateAll(elements =>
      elements.filter(e => !['inputFirstName', 'inputLastName', 'inputEmail', 'inputTel', 'cv', 'inputLink', 'portfolio', 'inputNote'].includes(e.id) && !e.disabled).map(e => e.name || e.id || 'Unknown required field'));
    requiredMissing.push(...unknownRequired);
    // This reviewed form has no consent checkboxes. Stop if it changes rather
    // than silently accepting newly introduced commitments.
    if (await frame.locator('input[type="checkbox"],input[type="radio"],select').count())
      requiredMissing.push('New consent or selection controls require review');
    if (requiredMissing.length) {
      outcome.status = 'Prepared but not submitted';
      outcome.blockers = requiredMissing.map(label => 'Missing required answer: ' + label);
      return outcome;
    }
    // CSS renders the text in uppercase, but the accessible button name is
    // "Submit application". Match the actual control without relying on CSS.
    const submit = frame.getByRole('button', { name: /^submit application$/i });
    submitClicked = true; // Mark before awaiting: network failure after click is uncertain.
    await submit.click();
    for (let i = 0; i < 30; i++) {
      const text = await frame.locator('body').innerText();
      if (humanVerification(text)) {
        outcome.status = 'Submission unconfirmed';
        outcome.requires_human_verification = true;
        outcome.submission_rejected = /session verification failed due to a human check error/i.test(text);
        outcome.observed_result_text = text.slice(-6000);
        outcome.blockers = ['Human verification appeared after the final click. Reconcile whether Comeet received the submission before retrying.'];
        return outcome;
      }
      const confirmation = text.match(/(?:application (?:has been |was )?(?:received|submitted successfully|sent successfully)|thank you for (?:applying|your application))[^\n]*/i);
      if (confirmation) {
        outcome.status = 'Submitted successfully';
        outcome.confirmation_text = confirmation[0];
        outcome.submission_time = new Date().toISOString();
        outcome.fields.forEach(field => { if (field.state === 'prepared') field.state = 'submitted'; });
        return outcome;
      }
      await page.waitForTimeout(500);
    }
    outcome.status = 'Submission unconfirmed';
    outcome.observed_result_text = (await frame.locator('body').innerText()).slice(-6000);
    outcome.blockers = ['Submit was clicked once, but no explicit receipt was observed. Check the application outcome before retrying.'];
    return outcome;
  } catch (error) {
    if (page) {
      const visible = await page.locator('body').innerText({timeout:3000}).catch(() => '');
      if (humanVerification(visible)) {
        outcome.status = submitClicked ? 'Submission unconfirmed' : 'Blocked';
        outcome.requires_human_verification = true;
        outcome.blockers = ['Human verification blocks automatic submission.'];
        return outcome;
      }
    }
    outcome.status = submitClicked ? 'Submission unconfirmed' : 'Blocked';
    outcome.blockers = [submitClicked ? 'Browser interrupted after submit; do not retry without reconciling the outcome.' : 'Browser form preparation failed: ' + error.message.split('\n')[0]];
    return outcome;
  } finally {
    await browser.close();
  }
}

(async () => {
  const result = await submit(JSON.parse(fs.readFileSync(0, 'utf8')));
  process.stdout.write(JSON.stringify(result));
})().catch(() => { process.exitCode = 1; });
