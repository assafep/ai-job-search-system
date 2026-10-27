#!/usr/bin/env python3
"""Read-only Playwright inspector for JavaScript-rendered application forms.

No personal data is entered; this script never clicks a final submit button.
Output contains form metadata and screenshots; store it privately.
"""
import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout


DOM_INVENTORY = r"""() => Array.from(document.querySelectorAll(
  'input,textarea,select,button,[role="button"],[role="checkbox"],' +
  '[role="radio"],[role="combobox"],[contenteditable]'
)).map((node, index) => {
  const id = node.id || '';
  const linked = id ? document.querySelector('label[for="' +
    CSS.escape(id) + '"]') : null;
  const wrapped = node.closest('label');
  const ariaIds = (node.getAttribute('aria-labelledby') || '').split(/\s+/)
    .filter(Boolean);
  const aria = ariaIds.map(x => document.getElementById(x)?.innerText || '')
    .join(' ').trim();
  const label = (linked?.innerText || wrapped?.innerText || aria ||
      node.getAttribute('aria-label') || node.getAttribute('placeholder') ||
      node.innerText || '').trim().slice(0, 200);
  const rect = node.getBoundingClientRect();
  const style = getComputedStyle(node);
  const visible = rect.width > 0 && rect.height > 0 &&
    style.display !== 'none' && style.visibility !== 'hidden';
  const choices = node.tagName.toLowerCase() === 'select' ?
    Array.from(node.options).map(o => ({label: o.text, value: o.value})) : [];
  return {
    index, tag: node.tagName.toLowerCase(), type: node.getAttribute('type') || '',
    id, name: node.getAttribute('name') || '', label,
    placeholder: node.getAttribute('placeholder') || '',
    required: node.hasAttribute('required') ||
      node.getAttribute('aria-required') === 'true',
    enabled: !node.disabled && node.getAttribute('aria-disabled') !== 'true',
    visible, accept: node.getAttribute('accept'),
    multiple: node.hasAttribute('multiple'), options: choices,
    role: node.getAttribute('role'), autocomplete: node.getAttribute('autocomplete'),
    form_id: node.closest('form')?.id || null
  };
})"""


def safe_url(url):
    """Keep host + path, discard token-bearing query, fragment and credentials."""
    try:
        value = urlsplit(url)
        if not value.hostname:
            return ""
        authority = value.hostname + ((":" + str(value.port)) if value.port else "")
        return urlunsplit((value.scheme, authority, value.path, "", ""))
    except ValueError:
        return "<invalid-url>"


def inspect(url, out, headed=False, entry_selector=None, timeout_ms=30000):
    out.mkdir(parents=True, exist_ok=True)
    events = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not headed)
        context = browser.new_context(viewport={"width": 1365, "height": 900})
        page = context.new_page()

        def register(event, request, **more):
            if len(events) < 500:
                events.append(dict(
                    kind=event, method=request.method, url=safe_url(request.url),
                    resource_type=request.resource_type, **more
                ))

        page.on("request", lambda r: register("request", r))
        page.on("requestfailed", lambda r: register(
            "requestfailed", r, failure=r.failure
        ))
        page.on("response", lambda r: register(
            "response", r.request, status=r.status,
            content_type=(r.headers.get("content-type") or "").split(";")[0]
        ))

        error = None
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.screenshot(path=str(out / "initial.png"), full_page=True)
            if entry_selector:
                opener = page.locator(entry_selector)
                if opener.count() != 1:
                    raise ValueError("Entry selector must uniquely identify one control")
                # Guard against using a final submit as an exploration action.
                meta = opener.evaluate("""el => ({
                    type: (el.getAttribute('type') || '').toLowerCase(),
                    text: (el.innerText || el.value || '').trim(),
                    formAction: el.closest('form')?.getAttribute('action') || null
                })""")
                if meta["type"] == "submit" or re.search(
                    r"\b(submit|send application|finish application)\b",
                    meta["text"], re.I
                ):
                    raise ValueError("Refusing to click a possible final submit control")
                opener.click(timeout=timeout_ms)
            # Observe JS/iframe hydration for a short, bounded period.
            page.wait_for_timeout(1800)
        except (PlaywrightTimeout, ValueError) as exc:
            error = "{}: {}".format(type(exc).__name__, str(exc).splitlines()[0])

        frame_details = []
        for frame in page.frames:
            try:
                controls = frame.evaluate(DOM_INVENTORY)
                title = frame.evaluate("() => document.title")
            except Exception as exc:
                controls, title = [], ""
                frame_error = "{}: {}".format(type(exc).__name__, str(exc).splitlines()[0])
            else:
                frame_error = None
            frame_details.append({
                "url": safe_url(frame.url),
                "title": title,
                "control_count": len(controls),
                "controls": controls,
                "inspection_error": frame_error,
            })
        try:
            page.screenshot(path=str(out / "form.png"), full_page=True)
        except Exception:
            pass
        result = {
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "requested_url": safe_url(url),
            "observed_url": safe_url(page.url),
            "page_title": page.title() if not error else None,
            "entry_selector": entry_selector,
            "status": "partial" if error else "inspected",
            "error": error,
            "frames": frame_details,
            "network": events,
            "submitted": False,
        }
        (out / "inventory.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        browser.close()
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="Exact job posting URL")
    parser.add_argument("--out", default=".local-form-inspection")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--entry-selector", help="Verified OPEN-FORM control only")
    parser.add_argument("--timeout-ms", type=int, default=30000)
    args = parser.parse_args()
    if urlsplit(args.url).scheme != "https":
        parser.error("HTTPS URL required")
    outcome = inspect(args.url, Path(args.out), args.headed,
                      args.entry_selector, args.timeout_ms)
    print(json.dumps({
        "status": outcome["status"], "submitted": outcome["submitted"],
        "observed_url": outcome["observed_url"],
        "frames": len(outcome["frames"]), "error": outcome["error"],
        "report": str(Path(args.out) / "inventory.json")
    }, indent=2))


if __name__ == "__main__":
    main()
