(() => {
  'use strict';
  // Keep the bearer reference out of subsequent requests and server query logs.
  const params = new URLSearchParams(location.search);
  const sessionId = params.get('session_id') || new URLSearchParams(location.hash.slice(1)).get('session_id');
  if (sessionId) history.replaceState(null, '', `${location.pathname}#session_id=${encodeURIComponent(sessionId)}`);
  const status = document.querySelector('#status');
  const retry = document.querySelector('#retry');
  const downloads = document.querySelector('#downloads');
  let attempts = 0;
  let timer;
  async function claim() {
    clearTimeout(timer);
    retry.hidden = true;
    downloads.hidden = true;
    downloads.replaceChildren();
    if (!sessionId) {
      status.textContent = 'This page needs your purchase reference. Open the download link from Stripe, or contact support. You do not need to pay again.';
      return;
    }
    status.textContent = 'Checking your payment with Stripe…';
    try {
      const response = await fetch('/api/claim', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({session_id: sessionId}), signal: AbortSignal.timeout(20000)
      });
      const result = await response.json();
      if (!response.ok) {
        status.textContent = result.error || 'Your download could not be prepared. Please try again.';
        retry.hidden = false;
        if (response.status === 409 && ++attempts < 12) timer = setTimeout(claim, 5000);
        return;
      }
      const mode = document.querySelector('#mode');
      mode.textContent = result.mode === 'test' ? 'Stripe test purchase confirmed. No real money was charged.' : 'Payment confirmed. Thank you.';
      mode.hidden = false;
      let selected;
      result.downloads.sort((a, b) => Number(b.platform === result.platform) - Number(a.platform === result.platform));
      for (const file of result.downloads) {
        const row = document.createElement('div');
        row.className = 'file';
        const link = document.createElement('a');
        link.className = 'download';
        link.href = file.url;
        link.download = file.filename;
        link.textContent = `Download for ${file.label}`;
        const name = document.createElement('p');
        name.textContent = file.filename;
        const checksum = document.createElement('p');
        checksum.className = 'checksum';
        checksum.textContent = `SHA-256: ${file.sha256}`;
        const materials = document.createElement('a');
        materials.href = file.materials_url;
        materials.textContent = 'Matching source, licenses and release notes';
        row.append(link, name, checksum, materials);
        downloads.append(row);
        if (file.platform === result.platform) selected = link;
      }
      downloads.hidden = false;
      retry.hidden = false;
      status.textContent = selected
        ? 'Your selected download is starting. If it does not start, use its download button below. Links last 15 minutes; Try again refreshes them.'
        : 'Payment confirmed. Choose your computer below to download. This purchase did not include a computer selection.';
      if (selected) selected.click();
    } catch {
      status.textContent = 'We could not connect to the download service. Try again; you do not need to pay again.';
      retry.hidden = false;
    }
  }
  retry.addEventListener('click', () => { attempts = 0; claim(); });
  claim();
})();
