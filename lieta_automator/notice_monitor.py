"""Remember transient DOM notices between Selenium observations, per document."""


NOTICE_MONITOR_SCRIPT = r"""
(() => {
    const key = '__lietaAutomatorNoticesV1';
    if (window[key]) return window[key];
    const retry = /try\s+again|too\s+many\s+requests|rate\s+limit|temporarily\s+unavailable|server\s+error|稍後再試|稍後重試|請重試|伺服器錯誤|請求過於頻繁/i;
    const unauthorized = /unauthorized\s+request\s*\.\s*please\s+log\s+in\s+again\.?/i;
    const selector = '[role="alert"], [role="status"], [data-sonner-toast]';
    const active = new WeakMap();
    const monitor = {events: [], sequence: 0, sessionExpired: false};
    const inspect = el => {
        if (!el || el.nodeType !== 1 || ['SCRIPT', 'STYLE', 'NOSCRIPT'].includes(el.tagName)) return;
        if (el.childElementCount && !el.matches(selector)) {
            active.delete(el);
            return;
        }
        const text = (el.textContent || '').replace(/\s+/g, ' ').trim();
        const kind = unauthorized.test(text) ? 'unauthorized' : retry.test(text) ? 'retry' : '';
        if (!kind || !el.isConnected || !el.getClientRects().length ||
            getComputedStyle(el).visibility === 'hidden' || getComputedStyle(el).display === 'none') {
            active.delete(el);
            return;
        }
        if (active.get(el) === text) return;
        active.set(el, text);
        monitor.events.push({id: ++monitor.sequence, kind, text});
        if (monitor.events.length > 128) monitor.events.shift();
        if (kind === 'unauthorized') monitor.sessionExpired = true;
    };
    const scan = (root, descendants = true) => {
        const el = root && (root.nodeType === 1 ? root : root.parentElement);
        if (!el) return;
        inspect(el);
        inspect(el.closest(selector));
        if (descendants) el.querySelectorAll('*').forEach(inspect);
    };
    monitor.scan = scan;
    monitor.observer = new MutationObserver(records => {
        for (const record of records) {
            // Inspect only changed subtrees, rather than rescanning every chart.
            scan(record.target, record.type === 'attributes');
            record.addedNodes.forEach(node => scan(node));
            record.removedNodes.forEach(node => scan(node));
        }
    });
    monitor.observer.observe(document.documentElement, {
        subtree: true, childList: true, characterData: true, attributes: true,
        attributeFilter: ['hidden', 'style', 'class', 'aria-hidden', 'data-state']
    });
    window[key] = monitor;
    scan(document.body);
    return monitor;
})()
"""


def install_notice_monitor(driver):
    driver.execute_script(NOTICE_MONITOR_SCRIPT + ";")


def stop_notice_monitor(driver):
    driver.execute_script("""
        const monitor = window.__lietaAutomatorNoticesV1;
        if (monitor) monitor.observer.disconnect();
        delete window.__lietaAutomatorNoticesV1;
    """)
