#!/usr/bin/env node
// Drive a real Chromium over the DevTools protocol and screenshot an element
// while the mouse hovers it — the hover state *seen*, not only measured.
//
// usage: node tools/visual_probe.mjs <port> <session_cookie> <path> <selector> <outdir> <label> [--print|--till|--overlay] [button-selectors]
// prints one JSON line on success: {"png": "...", "rect": {...}} — or, with
// --print, {"pdf": "..."}: the page printed to PDF the way the browser's own
// print dialog would, print media applied, for paper-state evidence.
// With `--overlay`, the two floating surfaces are opened in turn and captured:
// the try-on lightbox (its scrim and its elevated stage) and the date-picker
// popup. Both are reported with viewport rects and computed surfaces, so the
// overlay elevation and the scrim can be measured from the painted pixels.
// With `--till`, the driver walks the real checkout flow before photographing:
// the scan step (the till's money screen) exists only behind POSTs — customer
// step → skip-customer → add-to-basket — so it submits the same forms the
// cashier's clicks submit, then falls through to the normal capture.
// With a comma-separated `button-selectors` list (screenshot mode), each
// element is also photographed on its own and returned as `buttons`:
// [{"selector", "png", "width", "height"}] — so a filled control's label-on-fill
// contrast can be measured from the pixels the browser actually painted.
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const [portArg, cookie, urlPath, selector, outdir, label, mode, buttonsArg] = process.argv.slice(2);
if (!portArg || !cookie || !urlPath || !selector || !outdir || !label) {
  console.error('usage: visual_probe.mjs <port> <cookie> <path> <selector> <outdir> <label> [--print|--till]');
  process.exit(64);
}
const PRINT_MODE = mode === '--print';
const TILL_MODE = mode === '--till';
const OVERLAY_MODE = mode === '--overlay';
// One paper definition for both the DOM measurement and Page.printToPDF —
// A4 with 0.4in margins; the printable width in CSS px (96/in) is what a
// table must fit inside for the overflow check to mean anything.
const PAPER = { widthIn: 8.27, heightIn: 11.69, marginIn: 0.4 };
const PRINTABLE_PX = Math.round((PAPER.widthIn - 2 * PAPER.marginIn) * 96);
const BASE = `http://127.0.0.1:${portArg}`;

function findChrome() {
  const candidates = [
    process.env.CHROME_BIN,
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
    '/Applications/Arc.app/Contents/MacOS/Arc',
    '/Applications/Chromium.app/Contents/MacOS/Chromium',
    '/usr/bin/google-chrome', '/usr/bin/chromium', '/usr/bin/chromium-browser',
  ].filter(Boolean);
  for (const candidate of candidates) if (fs.existsSync(candidate)) return candidate;
  console.error('no chromium-family browser found (set CHROME_BIN)');
  process.exit(66);
}

const userDataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'raykids-probe-'));
const chrome = spawn(findChrome(), [
  '--headless=new', '--remote-debugging-port=0', `--user-data-dir=${userDataDir}`,
  '--no-first-run', '--no-default-browser-check', '--disable-extensions',
  '--window-size=1440,900', 'about:blank',
], { stdio: ['ignore', 'ignore', 'pipe'] });

const wsEndpoint = await new Promise((resolve, reject) => {
  let buffer = '';
  const timer = setTimeout(() => reject(new Error('chrome did not open a devtools port')), 15000);
  chrome.stderr.on('data', (chunk) => {
    buffer += chunk.toString();
    const match = buffer.match(/DevTools listening on (ws:\/\/\S+)/);
    if (match) { clearTimeout(timer); resolve(match[1]); }
  });
  chrome.on('exit', (code) => { clearTimeout(timer); reject(new Error(`chrome exited early: ${code}`)); });
});

const pending = new Map();
const listeners = [];
let nextId = 1;
const ws = new WebSocket(wsEndpoint);
await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject; });
ws.onmessage = (event) => {
  const message = JSON.parse(event.data);
  if (message.id && pending.has(message.id)) {
    const { resolve, reject } = pending.get(message.id);
    pending.delete(message.id);
    message.error ? reject(new Error(message.error.message)) : resolve(message.result);
  } else if (message.method) {
    for (const listener of listeners) listener(message);
  }
};
function send(method, params = {}, sessionId) {
  const id = nextId++;
  ws.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
  return new Promise((resolve, reject) => pending.set(id, { resolve, reject }));
}
const once = (filter) => new Promise((resolve) => {
  const listener = (message) => { if (filter(message)) { listeners.splice(listeners.indexOf(listener), 1); resolve(message); } };
  listeners.push(listener);
});

try {
  const { targetId } = await send('Target.createTarget', { url: 'about:blank' });
  const { sessionId } = await send('Target.attachToTarget', { targetId, flatten: true });
  const page = (method, params) => send(method, params, sessionId);

  await page('Page.enable');
  await page('Network.enable');
  await page('Emulation.setDeviceMetricsOverride',
    { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
  await page('Network.setCookie',
    { name: 'session', value: cookie, url: BASE + '/', httpOnly: true, sameSite: 'Lax' });

  const loaded = once((m) => m.sessionId === sessionId && m.method === 'Page.loadEventFired');
  await page('Page.navigate', { url: BASE + urlPath });
  await loaded;
  await page('Runtime.evaluate', {
    expression: 'document.fonts ? document.fonts.ready.then(() => true) : true',
    awaitPromise: true,
  });

  // The till walk: POST the customer step away (anonymous sale), then scan one
  // item, exactly the forms the cashier's clicks submit — the CSRF token from
  // the meta tag, included the way ensureCsrf adds it. Afterwards the page on
  // screen *is* the scan step, basket non-empty, money button rendered.
  let tillState = null;
  const walkLog = [];
  if (TILL_MODE) {
    const readToken = async () => (await page('Runtime.evaluate', {
      expression: "(document.querySelector('meta[name=\"csrf-token\"]')) ? document.querySelector('meta[name=\"csrf-token\"]').content : null",
      returnByValue: true,
    })).result.value;
    let csrf = await readToken();
    walkLog.push({ step: 'meta', gotToken: typeof csrf === 'string' && csrf.length > 10 });
    const submitForm = async (step, action, params) => {
      const walked = once((m) => m.sessionId === sessionId && m.method === 'Page.loadEventFired');
      await page('Runtime.evaluate', {
        expression: `(() => {
          const f = document.createElement('form');
          f.method = 'POST'; f.action = ${JSON.stringify(action)};
          for (const [k, v] of Object.entries(${JSON.stringify(params)})) {
            const i = document.createElement('input'); i.type = 'hidden'; i.name = k; i.value = v;
            f.appendChild(i);
          }
          document.body.appendChild(f); f.submit();
        })()`,
      });
      await walked;
      const after = (await page('Runtime.evaluate', {
        expression: `(() => ({
          url: location.pathname,
          csrfError: (document.body && document.body.innerText || '').includes('CSRF validation failed'),
          confirm: !!document.getElementById('confirm-submit'),
        }))()`,
        returnByValue: true,
      })).result.value;
      walkLog.push({ step, ...after });
      // The way the shell serves a cashier: every page's forms are tokened
      // from that page's own meta tag (ensureCsrf reads it at submit time),
      // so the next POST carries the token the server last handed out.
      const fresh = await readToken();
      if (typeof fresh === 'string' && fresh.length > 10) csrf = fresh;
    };
    // TILL_PHONE turns the walk into a customer sale: the lookup posts the
    // phone (the same form the cashier's search submits), the scan form hands
    // back the customer id, and the نسیه radio is clicked — firing the same
    // change event a cashier's click fires — so the credit confirmation state
    // is what gets photographed, not a simulated one.
    const tillPhone = process.env.TILL_PHONE || '';
    if (tillPhone) {
      await submitForm('lookup-customer', '/sales/lookup-customer', { phone: tillPhone, csrf_token: csrf });
      const attached = (await page('Runtime.evaluate', {
        expression: "(() => { const i = document.querySelector('#scan-form input[name=\"customer_id\"]'); return i ? i.value : null; })()",
        returnByValue: true,
      })).result.value;
      walkLog.push({ step: 'customer-attached', customerId: attached });
      await submitForm('add-to-basket', '/sales/add-to-basket', { customer_id: String(attached), barcode: 'TILL-0001', basket_json: '[]', csrf_token: csrf });
      await page('Runtime.evaluate', {
        expression: "(() => { const r = document.querySelector('input[name=\"payment_method\"][value=\"credit\"]'); if (r) r.click(); return !!r; })()",
        returnByValue: true,
      });
    } else {
      await submitForm('skip-customer', '/sales/skip-customer', { csrf_token: csrf });
      await submitForm('add-to-basket', '/sales/add-to-basket', { customer_id: '0', barcode: 'TILL-0001', basket_json: '[]', csrf_token: csrf });
    }
    tillState = (await page('Runtime.evaluate', {
      expression: `(() => {
        const b = document.getElementById('confirm-submit');
        const t = document.getElementById('terminal-form');
        const body = (document.body && document.body.innerText) || '';
        return {
          confirmPresent: !!b,
          confirmDisabled: b ? b.disabled : null,
          confirmLabel: b ? b.textContent.trim().slice(0, 50) : null,
          basketItems: (document.getElementById('basket-count') || {}).textContent || '',
          creditRadio: !!document.querySelector('input[name="payment_method"][value="credit"]:checked'),
          creditPanel: body.includes('نسیه: مبلغ به حساب بدهی مشتری'),
          creditWarning: body.includes('از سقف اعتبار رد می‌شوید'),
          terminalHidden: t ? t.style.display === 'none' : null,
        };
      })()`,
      returnByValue: true,
    })).result.value;
    tillState.walk = walkLog;
    // The walk arrives through POST navigations, which skip the font wait the
    // first page load does — a screenshot taken mid-font-load shifts the
    // painted glyphs and with them every modal-colour reading downstream.
    await page('Runtime.evaluate', {
      expression: 'document.fonts ? document.fonts.ready.then(() => true) : true',
      awaitPromise: true,
    });
  }

  // Overlay walk: the two floating surfaces the app has, opened for real.
  // Each visit clicks the control a user clicks, waits for the surface to
  // settle, photographs the full viewport (the scrim only exists at that
  // scale), and reports the surface's rect plus its computed paint.
  let overlayState = null;
  if (OVERLAY_MODE) {
    const settle = () => new Promise((r) => setTimeout(r, 350));
    const navigateTo = async (url) => {
      const loaded = once((m) => m.sessionId === sessionId && m.method === 'Page.loadEventFired');
      await page('Page.navigate', { url: BASE + url });
      await loaded;
      await page('Runtime.evaluate', {
        expression: 'document.fonts ? document.fonts.ready.then(() => true) : true',
        awaitPromise: true,
      });
    };
    const shootViewport = async (name) => {
      fs.mkdirSync(outdir, { recursive: true });
      const shot = await page('Page.captureScreenshot', { format: 'png' });
      const p = path.join(outdir, `${label}--${name}.png`);
      fs.writeFileSync(p, Buffer.from(shot.data, 'base64'));
      return p;
    };
    overlayState = { steps: [] };

    // ── the lightbox: scrim + elevated stage, on the saved-gallery page ──
    await navigateTo('/admin/try-on/saved');
    // The page before the overlay: the baseline the scrim must visibly change.
    const pageShot = await shootViewport('page');
    const lb = await page('Runtime.evaluate', {
      expression: `(() => {
        const opener = document.querySelector('.lightbox-open');
        if (!opener) return { present: false };
        opener.click();
        return { present: true };
      })()`,
      returnByValue: true,
    });
    if (!lb.result?.value?.present) {
      console.error('overlay probe: no .lightbox-open on /admin/try-on/saved — seed a GeneratedImage first');
      process.exit(65);
    }
    await settle();
    const lightbox = (await page('Runtime.evaluate', {
      expression: `(() => {
        const scrim = document.getElementById('lightbox');
        const stage = document.getElementById('lightbox-stage');
        if (!scrim || scrim.style.display === 'none' || !stage) return { open: false };
        const r = stage.getBoundingClientRect();
        return { open: true, stage: { vx: r.x, vy: r.y, width: r.width, height: r.height },
                 scrimPaint: getComputedStyle(scrim).backgroundColor,
                 stageShadow: getComputedStyle(stage).boxShadow };
      })()`,
      returnByValue: true,
    })).result.value;
    const lbShot = await shootViewport('lightbox');
    overlayState.lightbox = { ...lightbox, png: lbShot, pngBefore: pageShot };
    overlayState.steps.push({ step: 'lightbox', open: !!lightbox.open });

    // ── the date-picker popup: the floating dropdown surface ──
    await navigateTo('/admin/checks');
    const dp = await page('Runtime.evaluate', {
      expression: `(() => {
        const input = document.querySelector('.persian-date-input');
        if (!input) return { present: false };
        input.click();
        return { present: true };
      })()`,
      returnByValue: true,
    });
    if (!dp.result?.value?.present) {
      console.error('overlay probe: no .persian-date-input on /admin/checks');
      process.exit(65);
    }
    await settle();
    const popup = (await page('Runtime.evaluate', {
      expression: `(() => {
        const pop = document.querySelector('.pdp-popup');
        if (!pop) return { open: false };
        const r = pop.getBoundingClientRect();
        return { open: true, popup: { vx: r.x, vy: r.y, width: r.width, height: r.height },
                 paint: getComputedStyle(pop).backgroundColor,
                 shadow: getComputedStyle(pop).boxShadow };
      })()`,
      returnByValue: true,
    })).result.value;
    const dpShot = await shootViewport('popup');
    overlayState.popup = { ...popup, png: dpShot };
    overlayState.steps.push({ step: 'popup', open: !!popup.open });

    // ── the mobile sidebar: the app's third scrim surface ──
    // It exists only at a pocket width, so this surface gets its own
    // viewport: narrow enough for the ≤900px shell, above the ≤500px
    // compact block. The menu toggle is clicked the way a thumb would,
    // the drawer is given its 0.22s slide, and the scrim's coat, the blur
    // and the drawer's elevation are reported beside the photographs —
    // with the before-shot the dim is proven against.
    await page('Emulation.setDeviceMetricsOverride', {
      width: 720, height: 900, deviceScaleFactor: 1, mobile: true,
    });
    await navigateTo('/admin/customers');
    const sbBefore = await shootViewport('sidebar-page');
    const mt = (await page('Runtime.evaluate', {
      expression: `(() => {
        const t = document.getElementById('menu-toggle');
        if (!t || getComputedStyle(t).display === 'none') return { present: false };
        t.click();
        return { present: true };
      })()`,
      returnByValue: true,
    })).result.value;
    if (!mt?.present) {
      console.error('overlay probe: no visible menu-toggle at 720px on /admin/customers');
      process.exit(65);
    }
    await settle();
    const sidebar = (await page('Runtime.evaluate', {
      expression: `(() => {
        const scrim = document.getElementById('sidebar-overlay');
        const drawer = document.querySelector('.app-sidebar');
        if (!scrim || !drawer) return { open: false };
        const r = drawer.getBoundingClientRect();
        return {
          open: document.body.classList.contains('sidebar-open'),
          scrimPaint: getComputedStyle(scrim).backgroundColor,
          scrimBlur: getComputedStyle(scrim).backdropFilter,
          drawer: { vx: r.x, vy: r.y, width: r.width, height: r.height },
          drawerPaint: getComputedStyle(drawer).backgroundColor,
          drawerShadow: getComputedStyle(drawer).boxShadow,
        };
      })()`,
      returnByValue: true,
    })).result.value;
    const sbShot = await shootViewport('sidebar');
    overlayState.sidebar = { ...sidebar, pngBefore: sbBefore, png: sbShot };
    overlayState.steps.push({ step: 'sidebar', open: !!sidebar.open });
    await page('Emulation.clearDeviceMetricsOverride');
  }

  fs.mkdirSync(outdir, { recursive: true });

  if (PRINT_MODE) {
    // What print media does to the DOM, reported beside the PDF: the page's
    // action cards and danger zone computed display (null when it has none),
    // the record layout's print shape, and how many interactive forms remain
    // visible — evidence that "the screen's reply died with the screen" that
    // pixels alone cannot give.
    await page('Emulation.setEmulatedMedia', { media: 'print' });
    // Print media alone only swaps stylesheets — the viewport stays the
    // screen's 1440px and every "overflow" would measure against the wrong
    // ruler. Lay the viewport out at the paper's printable width first, the
    // geometry Page.printToPDF itself will use.
    await page('Emulation.setDeviceMetricsOverride', {
      width: PRINTABLE_PX, height: Math.round(PAPER.heightIn * 96),
      deviceScaleFactor: 1, mobile: false,
    });
    const printState = (await page('Runtime.evaluate', {
      expression: `(() => {
        const display = (s) => { const el = document.querySelector(s); return el ? getComputedStyle(el).display : null; };
        const forms = [...document.querySelectorAll('form')]
          .filter(f => [...f.elements].some(el => el.type !== 'hidden'));
        // Print-margin overflow: content wider than the printable area is
        // clipped or spills onto a phantom page — visible on screen, invisible
        // in a pixel split that only counts colours. Layout rects, not
        // scrollWidth: an overflow:hidden ancestor (the invoice's card has
        // one) swallows the document-level signal while the element still
        // lays out past the paper's right edge — exactly how a wide table
        // prints as cut-off columns instead of a scroll.
        const doc = document.documentElement;
        const printable = doc.clientWidth;
        const pastEdge = [];
        const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT);
        let el;
        while ((el = walker.nextNode()) && pastEdge.length < 5) {
          const cs = getComputedStyle(el);
          if (cs.display === 'none' || cs.visibility === 'hidden') continue;
          const r = el.getBoundingClientRect();
          // RTL: content wider than the paper extends *leftward* off the
          // printable area, not rightward — both edges are the same defect.
          const overRight = r.right - printable;
          const overLeft = -r.left;
          const over = Math.max(overRight, overLeft);
          if (r.width > 0 && over > 1) {
            pastEdge.push({
              overPx: Math.round(over),
              edge: overLeft > overRight ? 'left' : 'right',
              on: (el.classList && el.classList.length ? el.classList[0] : el.tagName),
            });
          }
        }
        return {
          actionCard: display('.purchase-action-card'),
          dangerZone: display('.purchase-detail-side .danger-zone'),
          layout: display('.purchase-detail-layout'),
          // offsetParent is null when the element *or an ancestor* is
          // display:none — the form's own computed display cannot see its
          // card's hiding, and the card is where the print block bites.
          visibleForms: forms.filter(f => f.offsetParent !== null).length,
          totalForms: forms.length,
          printableWidthPx: printable,
          overflowX: doc.scrollWidth - printable,
          pastEdge,
        };
      })()`,
      returnByValue: true,
    })).result.value;
    await page('Emulation.clearDeviceMetricsOverride');
    // Page.printToPDF applies print media styles itself — the same stylesheet
    // branch the print dialog renders. Backgrounds on, because the paper
    // doctrine paints surfaces deliberately; A4 with sensible margins.
    const pdf = await page('Page.printToPDF', {
      printBackground: true,
      paperWidth: PAPER.widthIn, paperHeight: PAPER.heightIn,
      marginTop: PAPER.marginIn, marginBottom: PAPER.marginIn,
      marginLeft: PAPER.marginIn, marginRight: PAPER.marginIn,
      preferCSSPageSize: false,
    });
    const out = path.join(outdir, `${label}.pdf`);
    const buffer = Buffer.from(pdf.data, 'base64');
    fs.writeFileSync(out, buffer);
    // Page count read from the PDF itself, so "how many sheets did this make"
    // sits beside the overflow numbers in the same JSON line.
    const pdfPages = (buffer.toString('latin1').match(/\/Type\s*\/Page[^s]/g) || []).length;
    console.log(JSON.stringify({ pdf: out, pdfPages, printState }));
    chrome.kill('SIGKILL');
    process.exit(0);
  }

  const rect = await page('Runtime.evaluate', {
    expression: `(() => {
      const el = document.querySelector(${JSON.stringify(selector)});
      if (!el) return null;
      el.scrollIntoView({ block: 'center' });
      const r = el.getBoundingClientRect();
      const n = el.nextElementSibling ? el.nextElementSibling.getBoundingClientRect() : null;
      // Mouse events take viewport coordinates; Page.captureScreenshot's clip
      // takes document coordinates. Return both, or the capture silently
      // photographs a different part of the page than the one under the mouse.
      const vx = r.x, vy = r.y;
      const px = r.x + window.scrollX, py = r.y + window.scrollY;
      return {
        vx, vy, px, py,
        width: Math.max(r.width, n ? n.width : 0),
        height: n ? (n.bottom + window.scrollY - py) : r.height,
        rowHeight: r.height,
      };
    })()`,
    returnByValue: true,
  });
  if (!rect.result?.value) {
    console.error(`selector not found on ${urlPath}: ${selector}`);
    process.exit(65);
  }
  const box = rect.result.value;

  // The mouse hovers in viewport coordinates…
  const cx = box.vx + box.width / 2, cy = box.vy + box.rowHeight / 2;
  const move = (x, y) => page('Input.dispatchMouseEvent',
    { type: 'mouseMoved', x, y, button: 'none', pointerType: 'mouse' });
  await move(cx, cy);
  await new Promise((r) => setTimeout(r, 60));
  await move(cx, cy);
  await new Promise((r) => setTimeout(r, 300));

  // …and the screenshot is clipped in document coordinates.
  const shot = await page('Page.captureScreenshot', {
    format: 'png',
    clip: { x: box.px, y: box.py, width: box.width, height: box.height, scale: 1 },
  });
  fs.mkdirSync(outdir, { recursive: true });
  const png = path.join(outdir, `${label}.png`);
  fs.writeFileSync(png, Buffer.from(shot.data, 'base64'));

  // Filled controls, each photographed on its own, so the pixels a label is
  // read from are exactly the ones the browser painted — gradients included.
  const buttons = [];
  for (const buttonSelector of (buttonsArg ? buttonsArg.split(',').map(s => s.trim()).filter(Boolean) : [])) {
    const b = await page('Runtime.evaluate', {
      expression: `(() => {
        const el = document.querySelector(${JSON.stringify(buttonSelector)});
        if (!el) return null;
        el.scrollIntoView({ block: 'center' });
        const r = el.getBoundingClientRect();
        return { vx: r.x, vy: r.y, width: r.width, height: r.height };
      })()`,
      returnByValue: true,
    });
    if (!b.result?.value) {
      const where = (await page('Runtime.evaluate', {
        expression: `(() => ({
          url: location.href,
          title: document.title.slice(0, 60),
          confirm: !!document.getElementById('confirm-submit'),
          basket: (document.getElementById('basket-count') || {}).textContent || null,
          step: document.querySelector('[aria-label]') ? document.querySelector('[aria-label]').getAttribute('aria-label') : null,
          body: document.body ? document.body.innerText.slice(0, 220) : null,
        }))()`,
        returnByValue: true,
      })).result?.value;
      if (walkLog.length) where.walk = walkLog;
      console.error(`button selector not found on ${urlPath}: ${buttonSelector}` +
        (where ? `\n  page is: ${JSON.stringify(where)}` : ''));
      process.exit(65);
    }
    const bb = b.result.value;
    // The full viewport is captured and the element's *viewport* rect is
    // returned with it: cropping in document coordinates has already proven
    // able to miss the element silently — viewport coordinates cannot.
    const bshot = await page('Page.captureScreenshot', { format: 'png' });
    const bp = path.join(outdir, `${label}--${buttonSelector.replace(/[^#.\\w-]/g, '')}.png`);
    fs.writeFileSync(bp, Buffer.from(bshot.data, 'base64'));
    buttons.push({ selector: buttonSelector, png: bp, rect: bb });
  }
  console.log(JSON.stringify({ png, rect: box, rowHeight: box.rowHeight, buttons, till: tillState, overlay: overlayState }));
} finally {
  try { await send('Browser.close'); } catch { /* the kill below is enough */ }
  setTimeout(() => chrome.kill('SIGKILL'), 500).unref?.();
}
