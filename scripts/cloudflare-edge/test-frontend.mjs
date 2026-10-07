// SPDX-License-Identifier: Apache-2.0
// Targeted CPU fixtures exercise the actual guard/link-handler source. They do
// not claim browser rendering, platform VPC connectivity or production PASS.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../../assets/welcome/welcome.js', import.meta.url), 'utf8');
const catalogs = vm.runInNewContext('(' + source.slice(source.indexOf('const catalogs = ') + 17, source.indexOf(";\n      const language =")) + ')');
const guardSource = source.slice(source.indexOf('      async function withBackendGuard'), source.indexOf('      function closeLanguages'));
const messages = {
  en: 'LiliuxFlow backend is currently offline.',
  'zh-Hant': 'LiliuxFlow 後端目前離線。',
  'zh-Hans': 'LiliuxFlow 后端当前离线。',
  ja: 'LiliuxFlow バックエンドは現在オフラインです。'
};
let assertions = 0;
function check(actual, expected) { assert.deepEqual(actual, expected); assertions++; }

function harness(locale = 'en', guardMarker = 'true') {
  const requests = [], notices = [], timers = new Map(), navigations = [], tabs = [];
  let timerId = 0, implementation = async () => ({status: 204});
  const links = ['/ui/', '/ui/', '/api-docs'].map(href => ({
    href, listeners: {}, getAttribute(name) { return name === 'href' ? href : null; },
    addEventListener(type, handler) { this.listeners[type] = handler; }
  }));
  const context = {
    catalogs, current: locale, AbortController,
    announce: message => notices.push(message),
    setTimeout: (fn, ms) => { const id = ++timerId; timers.set(id, {fn, ms}); return id; },
    clearTimeout: id => timers.delete(id),
    fetch: async (url, options) => { requests.push({url, options}); return implementation(options); },
    document: {body: {dataset: guardMarker === 'absent' ? {} : {edgeBackendGuard: guardMarker}}, querySelectorAll(selector) {
      check(selector, 'a[href="/ui/"], a[href="/ui"], a[href="/api-docs"]'); return links;
    }},
    window: {location: {assign: href => navigations.push(href)}, open: url => {
      const tab = {url, opener: {}, closed: false,
        location: {replace: href => { tab.url = href; }}, close() { this.closed = true; }};
      tabs.push(tab); return tab;
    }}
  };
  vm.runInNewContext(guardSource, context);
  return {context, requests, notices, timers, navigations, tabs, links,
    response: fn => { implementation = fn; },
    click(index = 0, options = {}) {
      const event = {type: 'click', button: 0, defaultPrevented: false,
        preventDefault() { this.defaultPrevented = true; }, ...options};
      const result = links[index].listeners[event.type]?.(event);
      return {event, result};
    }};
}

// Local source never opts in. Native links remain untouched even when the
// Worker-only readiness route is absent; no hostname assumptions are used.
for (const marker of ['absent', '', 'false', 'TRUE', '1', null]) {
  const fixture = harness('en', marker);
  check(fixture.requests.length, 0);
  check(fixture.links.every(link => Object.keys(link.listeners).length === 0), true);
  for (let index = 0; index < fixture.links.length; index++) {
    const click = fixture.click(index); await click.result;
    check(click.event.defaultPrevented, false);
  }
  check(fixture.requests.length, 0); check(fixture.notices, []); check(fixture.tabs, []);
}
{
  const fixture = harness();
  check(fixture.links.every(link => typeof link.listeners.click === 'function'), true);
  await fixture.click().result; check(fixture.navigations, ['/ui/']);
}

for (const [locale, message] of Object.entries(messages)) {
  check(catalogs[locale].backendOffline, message);
  const fixture = harness(locale);
  check(fixture.requests.length, 0); // setup, page load and hover make no probes
  fixture.response(async () => ({status: 503}));
  await fixture.click().result;
  check(fixture.notices, [message]); check(fixture.navigations, []);
  check(fixture.requests[0].url, '/_liliuxflow/backend/ready');
  check(fixture.requests[0].options.method, 'GET');
  check(fixture.requests[0].options.cache, 'no-store');
  check(fixture.requests[0].options.signal.aborted, false);
  check(fixture.timers.size, 0);
  fixture.response(async () => ({status: 204}));
  await fixture.click().result;
  check(fixture.navigations, ['/ui/']); check(fixture.requests.length, 2);
}
for (const status of [200, 201, 401, 404, 500]) {
  const fixture = harness(); fixture.response(async () => ({status}));
  await fixture.click(2).result; check(fixture.navigations, []); check(fixture.notices, [messages.en]);
}
{
  const fixture = harness('ja');
  fixture.response(async () => { throw new TypeError('network'); });
  await fixture.click().result; check(fixture.notices, [messages.ja]); check(fixture.navigations, []);
}
{
  const fixture = harness();
  fixture.response(options => new Promise((resolve, reject) => options.signal.addEventListener('abort', () => reject(new Error('aborted')))));
  const first = fixture.click();
  const duplicate = fixture.click(); await duplicate.result;
  check(fixture.requests.length, 1); check([...fixture.timers.values()][0].ms, 3000);
  [...fixture.timers.values()][0].fn(); await first.result;
  check(fixture.notices, [messages.en]); check(fixture.navigations, []); check(fixture.timers.size, 0);
}
for (const modifier of [{metaKey: true}, {ctrlKey: true}, {shiftKey: true}, {type: 'auxclick', button: 1}]) {
  const fixture = harness(); fixture.response(async () => ({status: 503}));
  const pending = fixture.click(0, modifier);
  check(fixture.tabs.length, 1); check(fixture.tabs[0].url, 'about:blank'); check(fixture.tabs[0].opener, null);
  await pending.result; check(fixture.tabs[0].closed, true); check(fixture.navigations, []);
  fixture.response(async () => ({status: 204})); await fixture.click(0, modifier).result;
  check(fixture.tabs[1].url, '/ui/'); check(fixture.tabs[1].closed, false);
}
{
  const fixture = harness();
  await fixture.click(2).result; check(fixture.navigations, ['/api-docs']); check(fixture.tabs, []);
  await fixture.click(0, {type: 'auxclick', button: 2}).result; check(fixture.requests.length, 1);
}
// Execute the actual endpoint/sample source for every supported origin shape.
const examplesSource = source.slice(source.indexOf('      function exampleApiBase'), source.indexOf('      const codeTabs'));
for (const [configured, origin, expected] of [
  [undefined, 'http://127.0.0.1:4000', 'http://127.0.0.1:4000/v1'],
  [undefined, 'http://[::1]:4000', 'http://[::1]:4000/v1'],
  [undefined, 'https://self.example:9443', 'https://self.example:9443/v1'],
  ['https://api.example:8443/proxy/v1/', 'https://site.example', 'https://api.example:8443/proxy/v1'],
  ['https://api.example/v1/v1/', 'null', 'https://api.example/v1'],
  [undefined, 'null', 'http://127.0.0.1:4000/v1'],
  [undefined, undefined, 'http://127.0.0.1:4000/v1'],
  ['https://api.example/$(touch)', 'null', 'http://127.0.0.1:4000/v1'],
  ['https:api.example', 'null', 'http://127.0.0.1:4000/v1'],
  ['javascript:alert(1)', 'https://self.example', 'https://self.example/v1'],
  ['https://key@api.example', 'null', 'http://127.0.0.1:4000/v1'],
  ['https://api.example?key=secret', 'null', 'http://127.0.0.1:4000/v1'],
  ['https://api.example/#secret', 'null', 'http://127.0.0.1:4000/v1'],
]) {
  const context = {URL, document: {body: {dataset: {apiBase: configured}}},
    window: {location: {origin}}, welcomeProfile: {model: 'qwen3.8-flash-next-lily-q4-64k'}};
  const actual = vm.runInNewContext(examplesSource + '\n({apiBase, examples})', context);
  check(actual.apiBase, expected);
  check(actual.examples.python.includes('base_url="' + expected + '"'), true);
  check(actual.examples.curl.includes('curl "' + expected + '/chat/completions"'), true);
  check(actual.examples.javascript.includes('baseURL: "' + expected + '"'), true);
  check(Object.values(actual.examples).some(value => value.includes('liliuxflow.diurnoctra.com')), false);
}
// Display uses text nodes; clipboard reads the displayed text, including tabs.
check(source.includes('navigator.clipboard.writeText(example.textContent)'), true);
check(source.includes('span.textContent = token[0]'), true);
check(source.includes('example.appendChild(document.createTextNode(examples[name].slice(last)))'), true);
for (const locale of Object.keys(messages)) check(/private|私有|非公開/.test(catalogs[locale].githubLabel), false);
// Every text and accessible-label consumer has a direct four-locale entry.
const html = fs.readFileSync(new URL('../../assets/welcome/index.html', import.meta.url), 'utf8');
const css = fs.readFileSync(new URL('../../assets/welcome/welcome.css', import.meta.url), 'utf8');
const displayKeys = new Set(Array.from(html.matchAll(/data-i18n(?:-aria)?="([^"]+)"/g), match => match[1]));
for (const locale of Object.keys(messages)) {
  for (const key of displayKeys) {
    check(Object.hasOwn(catalogs[locale], key), true);
    check(typeof catalogs[locale][key] === 'string' && catalogs[locale][key].trim().length > 0, true);
  }
}
// Desktop and narrow layouts keep code within a shrinkable, scrollable panel.
// These source invariants do not substitute for rendered browser inspection.
check(/\.code-wrap\s*\{[^}]*min-width:0/.test(css), true);
check(/pre\s*\{[^}]*overflow-x:auto/.test(css), true);
check(/@media \(max-width:860px\)[^\n]*\.api \{ grid-template-columns:1fr/.test(css), true);
check(/@media \(max-width:600px\)/.test(css), true);
check(html.includes('http://127.0.0.1:4000/v1'), true);
check(html.includes('https://liliuxflow.diurnoctra.com/v1'), false);
process.stdout.write(JSON.stringify({result: 'PASS', assertions, scope: 'CPU guard/link, API samples, locale/source layout fixtures; browser and real VPC acceptance remain required'}) + '\n');
