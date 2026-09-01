(() => {
  let lastToken = '';
  let attempts = 0;

  function candidateText() {
    const chunks = [];
    if (document.body?.innerText) chunks.push(document.body.innerText);
    for (const node of document.querySelectorAll('pre,code,script')) {
      const text = node.textContent || '';
      if (text) chunks.push(text);
    }
    return chunks.join('\n');
  }

  function findToken() {
    const text = candidateText();
    const patterns = [
      /const\s+token\s*=\s*['"`]([^'"`\s]{20,})['"`]/i,
      /authorization\s*:\s*['"`]Bearer\s+([^'"`\s]{20,})['"`]/i,
      /Bearer\s+([A-Za-z0-9._~-]{40,})/i
    ];
    for (const pattern of patterns) {
      const match = text.match(pattern);
      if (match?.[1] && match[1] !== lastToken) {
        lastToken = match[1];
        chrome.runtime.sendMessage({action:'developerToken', token:lastToken});
        return true;
      }
    }
    return false;
  }

  function probe() {
    attempts += 1;
    if (findToken() || attempts >= 30) return;
    setTimeout(probe, 1000);
  }

  probe();
  const observer = new MutationObserver(() => findToken());
  if (document.documentElement) observer.observe(document.documentElement, {subtree:true, childList:true, characterData:true});
  setTimeout(() => observer.disconnect(), 60_000);
})();
