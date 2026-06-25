/**
 * Behdashtik Visitor Journey Tracker
 * Records page visits as private notes in Chatwoot conversations.
 * Served from: support.behdashtik.ir/js/behdashtik-journey-tracker.js
 *
 * Security:
 *  - auth_token is sent ONLY in POST body (never in URL, header, or console)
 *  - Sensitive query params are stripped server-side; client strips them too as a defense-in-depth measure
 *  - No admin/agent token is used or stored here
 */
(function () {
  'use strict';

  var ENDPOINT = (window.$chatwoot && window.$chatwoot.baseUrl
    ? window.$chatwoot.baseUrl
    : 'https://support.behdashtik.ir') + '/api/v1/behdashtik/journey_events';

  var MIN_INTERVAL_MS = 2000;
  var MAX_QUEUE = 20;
  var QUEUE_TTL_MS = 5 * 60 * 1000; // 5 minutes

  var lastSentURL = '';
  var lastSentTime = 0;
  var pendingQueue = [];
  var drainTimer = null;

  var SENSITIVE_PARAMS = [
    'token', 'password', 'key', 'auth', 'session',
    'email', 'phone', 'api_key', 'secret', 'code'
  ];

  function getAuthToken() {
    var match = document.cookie.match(/(?:^|;\s*)cw_conversation=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : '';
  }

  function getWebsiteToken() {
    return (window.$chatwoot && window.$chatwoot.websiteToken) || '';
  }

  function stripSensitiveParams(url) {
    try {
      var u = new URL(url);
      var kept = [];
      u.searchParams.forEach(function (val, key) {
        var lk = key.toLowerCase();
        var isSensitive = SENSITIVE_PARAMS.some(function (s) {
          return lk === s || lk.indexOf(s) !== -1;
        });
        if (!isSensitive) kept.push([key, val]);
      });
      var newParams = new URLSearchParams(kept);
      u.search = newParams.toString();
      return u.toString();
    } catch (e) {
      return url.slice(0, 2048);
    }
  }

  function sendEvent(url, title, referrerURL, timestamp) {
    var authToken = getAuthToken();
    var websiteToken = getWebsiteToken();

    if (!authToken || !websiteToken) return;

    var cleanURL = stripSensitiveParams(url);
    var cleanReferrer = referrerURL ? stripSensitiveParams(referrerURL) : '';

    var body = new URLSearchParams({
      auth_token: authToken,
      website_token: websiteToken,
      url: cleanURL.slice(0, 2048),
      title: (title || '').slice(0, 500),
      referrer_url: cleanReferrer.slice(0, 2048),
      timestamp: timestamp || new Date().toISOString()
    });

    if (navigator.sendBeacon) {
      navigator.sendBeacon(ENDPOINT, body);
    } else {
      fetch(ENDPOINT, {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: body.toString(),
        credentials: 'omit',
        keepalive: true,
        mode: 'no-cors'
      }).catch(function () {});
    }
  }

  function now() {
    return Date.now();
  }

  function enqueue(url, title, referrerURL) {
    if (pendingQueue.length >= MAX_QUEUE) return;
    pendingQueue.push({
      url: url,
      title: title,
      referrerURL: referrerURL,
      timestamp: new Date().toISOString(),
      expiresAt: now() + QUEUE_TTL_MS
    });
    scheduleDrain();
  }

  function scheduleDrain() {
    if (drainTimer) return;
    drainTimer = setTimeout(drainQueue, MIN_INTERVAL_MS + 50);
  }

  function drainQueue() {
    drainTimer = null;
    var alive = pendingQueue.filter(function (e) { return now() < e.expiresAt; });
    pendingQueue = alive;

    if (pendingQueue.length === 0) return;

    var item = pendingQueue.shift();
    var authToken = getAuthToken();
    var websiteToken = getWebsiteToken();

    if (!authToken || !websiteToken) {
      pendingQueue.unshift(item);
      scheduleDrain();
      return;
    }

    sendEvent(item.url, item.title, item.referrerURL, item.timestamp);

    if (pendingQueue.length > 0) scheduleDrain();
  }

  function trackURL(url, title, referrerURL) {
    var cleanURL = stripSensitiveParams(url);

    if (cleanURL === lastSentURL) return;

    var n = now();
    if (n - lastSentTime < MIN_INTERVAL_MS) {
      enqueue(cleanURL, title, referrerURL);
      return;
    }

    var authToken = getAuthToken();
    var websiteToken = getWebsiteToken();

    if (!authToken || !websiteToken) {
      enqueue(cleanURL, title, referrerURL);
      return;
    }

    lastSentURL = cleanURL;
    lastSentTime = n;
    sendEvent(cleanURL, title, referrerURL, new Date().toISOString());
  }

  function onNavigation(referrerURL) {
    setTimeout(function () {
      trackURL(
        window.location.href,
        document.title,
        referrerURL || ''
      );
    }, 150);
  }

  var lastObservedURL = window.location.href;

  function init() {
    // Track the current (initial) page
    trackURL(window.location.href, document.title, document.referrer || '');

    // popstate (browser back/forward)
    window.addEventListener('popstate', function () {
      onNavigation(lastObservedURL);
      lastObservedURL = window.location.href;
    });

    // Intercept SPA navigation: pushState / replaceState
    var origPush = history.pushState;
    history.pushState = function () {
      var prev = window.location.href;
      origPush.apply(history, arguments);
      onNavigation(prev);
      lastObservedURL = window.location.href;
    };

    var origReplace = history.replaceState;
    history.replaceState = function () {
      var prev = window.location.href;
      origReplace.apply(history, arguments);
      onNavigation(prev);
      lastObservedURL = window.location.href;
    };

    // MutationObserver fallback (Turbo, jQuery PJAX, etc.)
    if (window.MutationObserver) {
      var observer = new MutationObserver(function () {
        if (window.location.href !== lastObservedURL) {
          onNavigation(lastObservedURL);
          lastObservedURL = window.location.href;
        }
      });
      observer.observe(document.documentElement, { childList: true, subtree: true });
    }

    // Poll briefly for cw_conversation cookie (handles case where tracker loads before widget)
    if (!getAuthToken()) {
      var pollCount = 0;
      var pollMax = 15;
      var pollInterval = setInterval(function () {
        pollCount++;
        if (getAuthToken() || pollCount >= pollMax) {
          clearInterval(pollInterval);
          if (pendingQueue.length > 0) drainQueue();
        }
      }, 2000);
    }
  }

  // Wait for Chatwoot to be ready before initialising
  if (window.$chatwoot && window.$chatwoot.hasLoaded) {
    init();
  } else {
    window.addEventListener('chatwoot:ready', function () {
      init();
    });
    // Also start polling queue drain once widget sets cw_conversation cookie
    window.addEventListener('chatwoot:ready', function () {
      if (pendingQueue.length > 0) drainQueue();
    });
  }
}());
