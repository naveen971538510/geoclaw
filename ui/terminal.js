const STORAGE_KEYS = {
  watchlist: 'geoclaw.operator.watchlist',
  alertsRead: 'geoclaw.operator.alerts.read',
  autoRefresh: 'geoclaw.operator.autorefresh',
  compactMode: 'geoclaw.operator.compact',
  focusMode: 'geoclaw.operator.focus',
  sourceSort: 'geoclaw.operator.sourceSort',
  alertSort: 'geoclaw.operator.alertSort',
  alertUnreadOnly: 'geoclaw.operator.alertUnreadOnly',
  starredArticles: 'geoclaw.operator.starredArticles'
};

const PRESETS = {
  oil: ['oil','brent','wti','crude','opec','tanker'],
  gold: ['gold','bullion','xau','safe haven'],
  forex: ['forex','fx','usd','eur','gbp','yen'],
  rates: ['fed','ecb','boe','treasury','yield','inflation'],
  geopolitics: ['war','sanctions','tariff','missile','strike','china']
};

const LOW_QUALITY_HINTS = ['163.com','epochtimes','jagonews24','dostor','tiflo','yam.com'];

const state = {
  payload: null,
  agentStatus: null,
  agentGoals: [],
  agentJournal: [],
  agentDecisions: [],
  agentTasks: [],
  agentActions: [],
  agentMetrics: null,
  agentOutcomes: null,
  agentQueue: [],
  agentSummary: null,
  terminalDiff: null,
  reasoningItems: [],
  latestBriefing: null,
  calibrationReport: null,
  theses: [],
  schedulerStatus: null,
  sourceHealth: null,
  whatChanged: null,
  cards: [],
  visibleCards: [],
  focusMode: false,
  compactMode: false,
  autoRefresh: true,
  watchlist: [],
  alertReadMap: {},
  starredArticleMap: {},
  currentArticleIndex: -1,
  quickFilter: 'All',
  currentAlertSort: 'newest',
  alertUnreadOnly: false,
  articleDrawerForcedFocus: false,
  operatorStateLoaded: false,
  currentThesisDetail: null,
  currentActionDetail: null,
  currentDrilldown: null,
  currentDebate: null
};

let refreshTimer = null;
let isRunningAgent = false;
let isRunningRealAgent = false;
let liveBarTimer = null;
let gcEventCount = 0;
let gcES = null;

function latestAgentRun(){
  return ((state.agentStatus || {}).runs || [])[0] || {};
}
function latestCompletedRun(){
  return (((state.agentStatus || {}).runs || []).find(run => run && (run.finished_at || run.completed_at || run.started_at))) || {};
}
function syncRunAgentButton(){
  const button = document.getElementById('runAgentBtn');
  const realButton = document.getElementById('runRealAgentBtn');
  if (!button) return;
  button.disabled = !!isRunningAgent;
  button.classList.toggle('busy', !!isRunningAgent);
  button.textContent = isRunningAgent ? 'Running…' : 'Run Agent';
  if (realButton){
    realButton.disabled = !!isRunningRealAgent;
    realButton.classList.toggle('busy', !!isRunningRealAgent);
    realButton.textContent = isRunningRealAgent ? 'Running…' : 'Run Real Agent';
  }
}

function esc(v){
  return String(v ?? '')
    .replace(/&/g,'&amp;')
    .replace(/</g,'&lt;')
    .replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;')
    .replace(/'/g,'&#39;');
}
function parseMs(v){
  const t = Date.parse(String(v || ''));
  return Number.isNaN(t) ? 0 : t;
}
function relTime(v){
  const ms = parseMs(v);
  if (!ms) return String(v || 'time n/a');
  const mins = Math.max(0, Math.floor((Date.now() - ms) / 60000));
  if (mins < 1) return 'just now';
  if (mins < 60) return mins + ' mins ago';
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return hrs + ' hrs ago';
  return Math.floor(hrs / 24) + ' days ago';
}
function gcTimeAgo(isoStr){
  const mins = Math.round((Date.now() - new Date(String(isoStr || ''))) / 60000);
  if (!Number.isFinite(mins)) return 'never';
  if (mins < 1) return 'just now';
  if (mins < 60) return mins + 'm ago';
  return Math.floor(mins / 60) + 'h ago';
}
function gcEscape(value){
  return esc(value);
}
function signalLabel(value){
  const raw = String(value || 'Neutral').toLowerCase();
  if (raw === 'bullish') return 'Positive';
  if (raw === 'bearish') return 'Negative';
  return 'Mixed';
}
function normalizedConfidence(value){
  return Math.max(0, Math.min(1, Number(value || 0)));
}
function confidencePct(value){
  return Math.round(normalizedConfidence(value) * 100);
}
function confidenceText(value){
  return String(confidencePct(value)) + '%';
}
function cardDisplayConfidence(card){
  if (!card) return 0;
  if (card.display_confidence != null) return normalizedConfidence(card.display_confidence);
  if (card.thesis_confidence != null) return normalizedConfidence(card.thesis_confidence);
  if (card.article_confidence != null) return normalizedConfidence(card.article_confidence);
  if (card.confidence_score != null) return normalizedConfidence(card.confidence_score);
  return normalizedConfidence(Number(card.confidence || 0) / 100);
}
function confidenceBorderClass(score){
  const value = Number(score || 0);
  if (value >= 0.75) return 'border-green';
  if (value >= 0.45) return 'border-yellow';
  return 'border-red';
}
function isExpiredTtl(ttl){
  const ms = parseMs(ttl);
  return !!ms && ms < Date.now();
}
function criticalStatusBadge(status){
  return String(status || '').toUpperCase() === 'CRITICAL_CONTRADICTION'
    ? '<span class="badge-critical">Contradiction</span>'
    : '';
}
function staleBadge(ttl){
  return isExpiredTtl(ttl) ? '<span class="badge-stale">Stale</span>' : '';
}
function cardStateClasses(item){
  const classes = [confidenceBorderClass(item.confidence_score)];
  if (String(item.status || '').toUpperCase() === 'CRITICAL_CONTRADICTION') classes.push('contradiction-pulse');
  if (isExpiredTtl(item.ttl)) classes.push('border-stale');
  return classes.join(' ');
}
function confidenceBar(score){
  const width = Math.max(8, Math.min(100, Math.round(Number(score || 0) * 100)));
  return '<div class="confidence-bar" style="width:' + String(width) + '%"></div>';
}
function thesisConfidenceBar(score){
  const value = Math.max(0, Math.min(1, Number(score || 0)));
  const width = Math.max(8, Math.round(value * 100));
  const color = value > 0.7 ? '#1D9E75' : (value >= 0.4 ? '#BA7517' : '#E24B4A');
  return '<div class="confidence-bar" style="width:' + String(width) + '%;background:' + color + '"></div>';
}
function thesisStatusBadge(status){
  const value = String(status || 'active').toLowerCase();
  const cls = value === 'active' || value === 'tracking' ? 'status-good' : (value === 'weakened' ? 'status-warn' : 'status-bad');
  return '<span class="' + cls + '">' + esc(value) + '</span>';
}
function thesisVelocityArrow(value){
  const velocity = Number(value || 0);
  if (velocity > 0.02) return '<span style="color:#3fb950">↑</span>';
  if (velocity < -0.02) return '<span style="color:#f85149">↓</span>';
  return '<span style="color:#8b949e">→</span>';
}
function encodePath(value){
  return encodeURIComponent(String(value || ''));
}
function cardCategory(card){
  const alertTags = (card.alert_tags || []).map(x => String(x || '').toUpperCase());
  const assetTags = (card.asset_tags || []).map(x => String(x || '').toUpperCase());
  const macroTags = (card.macro_tags || []).map(x => String(x || '').toUpperCase());
  const text = ((card.headline || '') + ' ' + (card.summary || '') + ' ' + (card.source || '')).toLowerCase();
  if (alertTags.includes('CONTRADICTION') || alertTags.includes('CRITICAL_CONTRADICTION') || card.has_contradiction) return 'Contradictions';
  if (assetTags.includes('OIL') || alertTags.includes('OPEC') || text.includes('energy') || text.includes('crude')) return 'Energy';
  if (macroTags.includes('GEOPOLITICS') || alertTags.includes('WAR') || alertTags.includes('SANCTIONS') || alertTags.includes('TARIFF')) return 'Politics';
  if (text.includes('tech') || text.includes('ai') || text.includes('chip') || text.includes('software') || text.includes('tesla') || text.includes('anthropic')) return 'Tech';
  return 'Markets';
}
function categoryClass(category){
  return 'cat-' + String(category || 'Markets').toLowerCase();
}
function syncQuickFilterButtons(){
  document.querySelectorAll('[data-quick-filter]').forEach(node => {
    node.classList.toggle('active', node.getAttribute('data-quick-filter') === state.quickFilter);
  });
}
function readLocal(key, fallback){
  try{
    const raw = localStorage.getItem(key);
    if (!raw) return fallback;
    return JSON.parse(raw);
  }catch(err){
    return fallback;
  }
}
function writeLocal(key, value){
  try{ localStorage.setItem(key, JSON.stringify(value)); }catch(err){}
}
function escapeRegex(text){
  return String(text || '').replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}
function highlightText(text, terms){
  let html = esc(text || '');
  (terms || []).filter(Boolean).sort((a,b) => String(b).length - String(a).length).forEach(term => {
    const pattern = new RegExp('(' + escapeRegex(term) + ')', 'ig');
    html = html.replace(pattern, '<span class="highlight">$1</span>');
  });
  return html;
}
function joinTags(items){
  const clean = (items || []).filter(Boolean);
  return clean.length ? clean.join(', ') : 'None';
}
function articleKey(cardOrAlert){
  if (!cardOrAlert) return '';
  return String(cardOrAlert.url || cardOrAlert.headline || '').trim();
}
function isStarred(cardOrAlert){
  return !!state.starredArticleMap[articleKey(cardOrAlert)];
}
function persistLocalState(){
  writeLocal(STORAGE_KEYS.watchlist, state.watchlist);
  writeLocal(STORAGE_KEYS.alertsRead, state.alertReadMap);
  writeLocal(STORAGE_KEYS.starredArticles, state.starredArticleMap);
}
async function saveOperatorState(){
  persistLocalState();
  try{
    await fetch('/operator-state', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        watchlist: state.watchlist,
        read_alerts: state.alertReadMap,
        starred_articles: state.starredArticleMap
      })
    });
  }catch(err){}
}
function applyOperatorState(serverState){
  const localWatchlist = readLocal(STORAGE_KEYS.watchlist, []);
  const localRead = readLocal(STORAGE_KEYS.alertsRead, {});
  const localStarred = readLocal(STORAGE_KEYS.starredArticles, {});
  const mergedWatchlist = [...new Set([...(serverState.watchlist || []), ...localWatchlist].map(x => String(x || '').trim().toLowerCase()).filter(Boolean))];
  state.watchlist = mergedWatchlist;
  state.alertReadMap = Object.assign({}, serverState.read_alerts || {}, localRead || {});
  state.starredArticleMap = Object.assign({}, serverState.starred_articles || {}, localStarred || {});
  state.operatorStateLoaded = true;
  persistLocalState();
}
function priorityWeight(priority){
  const value = String(priority || '').toLowerCase();
  if (value === 'critical') return 4;
  if (value === 'high') return 3;
  if (value === 'medium') return 2;
  return 1;
}
function isTrustedSource(name){
  const s = String(name || '').toLowerCase();
  const trusted = ['bbc','reuters','guardian','financial times','ft','bloomberg','cnbc','wsj','associated press','ap','marketwatch','investing.com','yahoo','forbes','economist'];
  return trusted.some(x => s.includes(x));
}
function isLowQualitySource(card){
  const joined = (String(card.source || '') + ' ' + String(card.url || '')).toLowerCase();
  return LOW_QUALITY_HINTS.some(x => joined.includes(x));
}
function watchlistMatches(card){
  const hay = ((card.headline || '') + ' ' + (card.summary || '') + ' ' + (card.thesis || '') + ' ' + (card.what_to_watch || '') + ' ' + (card.asset_tags || []).join(' ') + ' ' + (card.alert_tags || []).join(' ')).toLowerCase();
  return state.watchlist.filter(x => hay.includes(String(x).toLowerCase()));
}
function setOptions(id, values, firstText){
  const el = document.getElementById(id);
  const keep = el.value;
  el.innerHTML = '<option value="">' + firstText + '</option>' + values.map(v => '<option value="' + esc(v) + '">' + esc(v) + '</option>').join('');
  if (values.includes(keep)) el.value = keep;
}
function currentCards(){
  const q = document.getElementById('q').value.trim().toLowerCase();
  const signal = document.getElementById('signal').value;
  const asset = document.getElementById('asset').value;
  const source = document.getElementById('source').value;
  const sort = document.getElementById('sort').value;
  const alertsOnly = document.getElementById('alertsOnly').checked;
  const trustedOnly = document.getElementById('trustedOnly').checked;
  const watchlistOnly = document.getElementById('watchlistOnly').checked;
  const hideJunk = document.getElementById('hideJunkToggle').checked;

  let cards = state.cards.filter(x => {
    const category = cardCategory(x);
    const search = ((x.headline || '') + ' ' + (x.source || '') + ' ' + (x.summary || '') + ' ' + (x.thesis || '') + ' ' + (x.what_to_watch || '') + ' ' + (x.asset_tags || []).join(' ') + ' ' + (x.alert_tags || []).join(' ') + ' ' + (x.watchlist_hits || []).join(' ') + ' ' + watchlistMatches(x).join(' ')).toLowerCase();
    if (q && !search.includes(q)) return false;
    if (state.quickFilter && state.quickFilter !== 'All' && category !== state.quickFilter) return false;
    if (signal && x.signal !== signal) return false;
    if (asset && !(x.asset_tags || []).includes(asset)) return false;
    if (source && x.source !== source) return false;
    if (alertsOnly && !((x.alert_tags || []).length)) return false;
    if (trustedOnly && !isTrustedSource(x.source || '')) return false;
    if (watchlistOnly && !watchlistMatches(x).length) return false;
    if (hideJunk && isLowQualitySource(x)) return false;
    return true;
  });

  if (sort === 'impact'){
    cards.sort((a,b) => (b.impact_score || 0) - (a.impact_score || 0) || parseMs(b.published_at) - parseMs(a.published_at));
  } else if (sort === 'newest'){
    cards.sort((a,b) => parseMs(b.published_at) - parseMs(a.published_at) || (b.impact_score || 0) - (a.impact_score || 0));
  } else if (sort === 'source'){
    cards.sort((a,b) => String(a.source || '').localeCompare(String(b.source || '')) || (b.impact_score || 0) - (a.impact_score || 0));
  }
  return cards;
}
function unreadAlertCount(){
  const alerts = ((state.payload || {}).top_alerts || []);
  return alerts.filter(a => !state.alertReadMap[String(a.url || a.headline || '')]).length;
}
function setBodyModes(){
  document.body.classList.toggle('focus-mode', !!state.focusMode);
  document.body.classList.toggle('compact-mode', !!state.compactMode);
  document.getElementById('focusModeBtn').textContent = state.focusMode ? 'Exit Focus View' : 'Focus View';
  document.getElementById('compactModeBtn').textContent = state.compactMode ? 'Exit Compact View' : 'Compact View';
}
function renderWatchlist(){
  const box = document.getElementById('watchlistCloud');
  const meta = document.getElementById('watchlistMeta');
  if (!state.watchlist.length){
    meta.textContent = 'No watchlist terms saved yet.';
    box.innerHTML = '<div class="watch-hint">No local watchlist keywords yet. Add a term or apply a preset.</div>';
    return;
  }
  meta.textContent = String(state.watchlist.length) + ' saved watchlist term' + (state.watchlist.length === 1 ? '' : 's') + ' · matches are highlighted in cards and article detail.';
  box.innerHTML = state.watchlist.map(item => '<span class="watch-chip">' + esc(item) + '<button data-remove-watch="' + esc(item) + '">x</button></span>').join('');
  box.querySelectorAll('[data-remove-watch]').forEach(node => {
    node.addEventListener('click', () => {
      const value = node.getAttribute('data-remove-watch');
      state.watchlist = state.watchlist.filter(x => x !== value);
      saveOperatorState();
      renderWatchlist();
      renderCards();
    });
  });
}
function addWatchTerms(items){
  const next = new Set(state.watchlist);
  items.forEach(item => {
    const clean = String(item || '').trim().toLowerCase();
    if (clean) next.add(clean);
  });
  state.watchlist = [...next];
  saveOperatorState();
  renderWatchlist();
  renderCards();
}
function panelCard(title, body){
  return '<div class="overlay-card"><h4>' + esc(title) + '</h4>' + body + '</div>';
}
function articleIndexForUrl(url){
  if (!url) return -1;
  return state.visibleCards.findIndex(card => String(card.url || '') === String(url));
}
function articleIndexForAlert(alert){
  const indexByUrl = articleIndexForUrl(alert.url || '');
  if (indexByUrl >= 0) return indexByUrl;
  return state.visibleCards.findIndex(card => String(card.headline || '') === String(alert.headline || ''));
}
function unreadQueue(){
  const alerts = [ ...(((state.payload || {}).top_alerts) || []) ];
  return alerts.map(alert => {
    const index = articleIndexForAlert(alert);
    const card = index >= 0 ? state.visibleCards[index] : state.cards.find(x => articleKey(x) === articleKey(alert));
    return {
      key: articleKey(alert),
      alert,
      index,
      card,
      starred: isStarred(alert),
      read: !!state.alertReadMap[articleKey(alert)],
      impact: card ? Number(card.impact_score || 0) : 0,
      createdAt: parseMs(alert.created_at || '')
    };
  }).filter(item => item.key).sort((a, b) => {
    return Number(b.starred) - Number(a.starred)
      || Number(a.read) - Number(b.read)
      || priorityWeight(b.alert.priority) - priorityWeight(a.alert.priority)
      || b.impact - a.impact
      || b.createdAt - a.createdAt;
  });
}
function nextUnreadQueueItem(step){
  const queue = unreadQueue().filter(item => !item.read && item.index >= 0);
  if (!queue.length) return null;
  const currentKey = articleKey(state.visibleCards[state.currentArticleIndex] || {});
  if (!currentKey) return step > 0 ? queue[0] : queue[queue.length - 1];
  const currentPos = queue.findIndex(item => item.key === currentKey);
  if (currentPos < 0) return step > 0 ? queue[0] : queue[queue.length - 1];
  const nextPos = currentPos + step;
  if (nextPos < 0 || nextPos >= queue.length) return null;
  return queue[nextPos];
}
function toggleAlertRead(key, nextValue){
  const finalValue = typeof nextValue === 'boolean' ? nextValue : !state.alertReadMap[key];
  state.alertReadMap[key] = finalValue;
  saveOperatorState();
  renderStats();
  renderSidebars();
  renderSummaryStrip();
  renderReadablePanels();
}
function toggleArticleStar(key, nextValue){
  const finalValue = typeof nextValue === 'boolean' ? nextValue : !state.starredArticleMap[key];
  state.starredArticleMap[key] = finalValue;
  saveOperatorState();
  renderSidebars();
  renderCards();
  renderSummaryStrip();
}
function renderMiniChart(nodeId, title, rows){
  const el = document.getElementById(nodeId);
  if (!el) return;
  if (!(rows || []).length){
    el.innerHTML = '<div class="chart"><div class="chart-title">' + esc(title) + '</div><div class="mini-copy">No chart data available yet.</div></div>';
    return;
  }
  const max = Math.max(...rows.map(row => Number(row.value || 0)), 1);
  el.innerHTML = '<div class="chart"><div class="chart-title">' + esc(title) + '</div>' + rows.map(row => '<div class="chart-row"><div class="chart-label">' + esc(row.label) + '</div><div class="chart-bar"><div class="chart-fill" style="width:' + ((Number(row.value || 0) / max) * 100) + '%"></div></div><div class="chart-value">' + esc(String(row.value || 0)) + '</div></div>').join('') + '</div>';
}
function renderAgentPanels(){
  const goals = state.agentGoals || [];
  const journal = state.agentJournal || [];
  const tasks = state.agentTasks || [];
  const theses = state.theses || [];
  const actions = state.agentActions || [];
  const outcomes = (state.agentOutcomes || {}).outcomes || {};
  const queue = state.agentQueue || [];
  document.getElementById('goalsPreview').textContent = goals.length ? goals.slice(0, 2).map(goal => goal.name).join(' | ') : 'No goals stored yet.';
  document.getElementById('thesesPreview').textContent = theses.length ? theses.slice(0, 2).map(item => item.title || item.current_claim || item.thesis_key).join(' | ') : 'No thesis state stored yet.';
  document.getElementById('tasksPreview').textContent = tasks.length ? tasks.slice(0, 2).map(task => task.title).join(' | ') : 'No open tasks yet.';
  document.getElementById('actionsPreview').textContent = actions.length ? actions.slice(0, 2).map(action => action.action_type + ' · ' + (action.status || 'proposed')).join(' | ') : 'No proposed actions yet.';
  const journalPreviewHtml = journal.length
    ? journal.slice(0, 2).map(item => '<div class="mini"><div class="mini-title">' + esc(item.summary || item.journal_type || 'Journal') + '</div><div class="mini-copy">' + esc(relTime(item.created_at || '')) + '</div></div>').join('')
    : '<div class="mini"><div class="mini-copy">No journal entries yet.</div></div>';
  const outcomesPreviewHtml = '<div class="mini"><div class="mini-copy">Confirmed: <strong>' + esc(String(outcomes.confirmed || 0)) + '</strong> · Weakened: <strong>' + esc(String(outcomes.weakened || 0)) + '</strong> · Contradicted: <strong>' + esc(String(outcomes.contradicted || 0)) + '</strong> · Stale: <strong>' + esc(String(outcomes.stale || 0)) + '</strong></div></div>';
  const journalList = document.getElementById('journalList');
  const journalListSecondary = document.getElementById('journalListSecondary');
  const outcomesPanel = document.getElementById('outcomesPanel');
  const outcomesPanelSecondary = document.getElementById('outcomesPanelSecondary');
  if (journalList) journalList.innerHTML = journalPreviewHtml;
  if (journalListSecondary) journalListSecondary.innerHTML = journal.length
    ? journal.slice(0, 4).map(item => '<div class="mini"><div class="mini-title">' + esc(item.summary || item.journal_type || 'Journal') + '</div><div class="mini-copy">' + esc(item.created_at || '') + '</div></div>').join('')
    : '<div class="mini"><div class="mini-copy">No journal entries yet. Run the real agent loop to populate memory.</div></div>';
  if (outcomesPanel) outcomesPanel.innerHTML = outcomesPreviewHtml;
  if (outcomesPanelSecondary) outcomesPanelSecondary.innerHTML = outcomesPreviewHtml;
  renderMiniChart('chartDecisionTrend', 'Decision Trend', Object.entries(((state.agentMetrics || {}).metrics || {}).decision_mix || {}).map(([label, value]) => ({label, value})));
  renderMiniChart('chartThesisOutcome', 'Thesis Outcome', Object.entries(outcomes || {}).map(([label, value]) => ({label, value})));
  const queueBySource = {};
  queue.forEach(item => {
    const key = String(item.source_name || item.source || 'Unknown');
    queueBySource[key] = (queueBySource[key] || 0) + 1;
  });
  renderMiniChart('chartPriorityQueue', 'Priority Queue', Object.entries(queueBySource).map(([label, value]) => ({label, value})).slice(0, 6));
  const runs = (((state.agentMetrics || {}).metrics || {}).runs || []).slice(0, 6);
  renderMiniChart('chartRunMetrics', 'Run Metrics', runs.map(run => ({label: String(run.id || run.run_type || 'run'), value: Number(run.items_kept || 0)})).reverse());
  const watchlistPoints = (((state.agentMetrics || {}).metrics || {}).watchlist_points || []).slice(0, 6);
  renderMiniChart('chartWatchlistHits', 'Watchlist Hit Trend', watchlistPoints.map((point, idx) => ({label: String(idx + 1), value: Number(point.watchlist_hits || 0)})));
  const queueCards = queue.map(item => state.cards.find(card => Number(card.article_id || 0) === Number(item.article_id || 0))).filter(Boolean);
  const sourceMix = {
    trusted: queueCards.filter(card => String(card.trust_label || '') === 'trusted').length,
    low_quality: queueCards.filter(card => !!card.is_low_quality).length,
  };
  renderMiniChart('chartSourceMix', 'Source Quality / Decision Mix', Object.entries(sourceMix).map(([label, value]) => ({label, value})));
}
async function openOverlay(section){
  await ensureOverlayData(section);
  window.gcCurrentPanel = section;
  const overlay = document.getElementById('overlay');
  const body = document.getElementById('overlayBody');
  const title = document.getElementById('overlayTitle');
  const subtitle = document.getElementById('overlaySubtitle');
  const actions = document.getElementById('overlayActions');
  overlay.classList.add('open');
  overlay.setAttribute('aria-hidden', 'false');

  if (section === 'summary'){
    const item = state.agentSummary || {};
    title.textContent = 'Agent Summary';
    subtitle.textContent = 'What the latest real run reviewed, changed, and why.';
    actions.innerHTML = '<a class="textlink" href="/terminal/agent-summary" target="_blank" rel="noopener noreferrer">Open raw agent summary</a>';
    body.innerHTML = panelCard('Latest Run', item && Object.keys(item).length
      ? '<div class="mini"><div class="mini-copy">Stories reviewed: <strong>' + esc(String(item.stories_reviewed || 0)) + '</strong></div><div class="mini-copy">Clusters reviewed: <strong>' + esc(String(item.clusters_reviewed || 0)) + '</strong></div><div class="mini-copy">Theses updated: <strong>' + esc(String(item.theses_updated || 0)) + '</strong></div><div class="mini-copy">Tasks closed: <strong>' + esc(String(item.tasks_closed || 0)) + '</strong></div><div class="mini-copy">Actions proposed: <strong>' + esc(String(item.actions_proposed || 0)) + '</strong></div><div class="mini-copy">LLM path: <strong>' + esc(item.llm_path || 'fallback') + '</strong> · calls ' + esc(String(item.llm_calls_made || 0)) + ' · cache hits ' + esc(String(item.llm_cache_hits || 0)) + '</div><div class="mini-copy">Duration: <strong>' + esc(String(item.duration_seconds || 0)) + 's</strong></div></div>'
      : '<div class="empty"><div class="empty-title">No agent summary yet</div><div class="empty-sub">Run the real agent loop to populate the summary.</div></div>') +
      panelCard('Top Belief Change', (item.top_belief_change && (item.top_belief_change.title || item.top_belief_change.thesis_key))
        ? '<div class="mini"><div class="mini-title">' + esc(item.top_belief_change.title || item.top_belief_change.thesis_key || '') + '</div><div class="mini-copy">Status ' + esc(item.top_belief_change.status || 'unknown') + ' · confidence ' + esc(confidenceText(item.top_belief_change.confidence || 0)) + '</div><div class="mini-copy">' + esc(item.top_reason || 'No reason recorded.') + '</div></div>'
        : '<div class="mini"><div class="mini-copy">No thesis change detail is available yet.</div></div>');
    return;
  }

  if (section === 'source'){
    const health = state.sourceHealth || {};
    const sources = health.sources || [];
    const keys = health.keys || {};
    title.textContent = 'Source Health';
    subtitle.textContent = 'Readable status for feeds, APIs, cached market data, and provider degradation.';
    actions.innerHTML = '<a class="textlink" href="/source-health" target="_blank" rel="noopener noreferrer">Open raw source health</a>';
    body.innerHTML =
      panelCard('Current Source Status', sources.length
        ? sources.map(s => {
            let extra = '';
            if (s.name === 'gdelt' && s.status === 'limited'){
              extra = '<div class="mini-copy">Calm note: GDELT is in cooldown, so RSS can continue carrying the terminal until GDELT retries.</div>';
            } else if (isLowQualitySource({source:s.name,url:s.note || ''})){
              extra = '<div class="mini-copy">Suppression note: low-quality sources can be hidden from the main card feed with the junk toggle.</div>';
            }
            return '<div class="mini"><div class="row"><div class="mini-title">' + esc(String(s.name || '').toUpperCase()) + '</div><div class="' + ((s.status === 'ok' || s.status === 'cached') ? 'status-good' : 'status-warn') + '">' + esc(s.status || 'unknown') + '</div></div><div class="mini-copy">Enabled: ' + esc(String(!!s.enabled)) + ' · Ready: ' + esc(String(!!s.ready)) + '</div><div class="mini-copy">' + esc(s.note || 'No note') + '</div>' + extra + '</div>';
          }).join('')
        : '<div class="empty"><div class="empty-title">No source data yet</div><div class="empty-sub">Source health has not loaded.</div></div>') +
      panelCard('Key Status', ['NEWSAPI_KEY','GUARDIAN_API_KEY','ALPHAVANTAGE_KEY'].map(key => {
        const mapped = key === 'NEWSAPI_KEY' ? keys.newsapi_configured : key === 'GUARDIAN_API_KEY' ? keys.guardian_configured : keys.alphavantage_configured;
        return '<div class="mini"><div class="row"><div class="mini-title">' + esc(key) + '</div><div class="' + (mapped ? 'status-good' : 'status-warn') + '">' + (mapped ? 'configured' : 'missing') + '</div></div></div>';
      }).join(''));
      return;
  }

  if (section === 'agent'){
    const agent = state.agentStatus || {};
    const runs = agent.runs || [];
    title.textContent = 'Agent Status';
    subtitle.textContent = 'Recent runs, output volume, and latest execution results.';
    actions.innerHTML = '<a class="textlink" href="/agent-status" target="_blank" rel="noopener noreferrer">Open raw agent status</a>';
    body.innerHTML =
      panelCard('Current Snapshot',
        '<div class="mini"><div class="mini-copy">Runs visible: <strong>' + esc(String(runs.length)) + '</strong></div><div class="mini-copy">Top alerts count: <strong>' + esc(String(agent.top_alerts_count || 0)) + '</strong></div><div class="mini-copy">Market tiles count: <strong>' + esc(String(agent.market_count || 0)) + '</strong></div><div class="mini-copy">Run in progress: <strong>' + esc(String(isRunningAgent)) + '</strong></div></div>') +
      panelCard('Recent Runs', runs.length
        ? runs.map(r => '<div class="mini"><div class="row"><div class="mini-title">' + esc(r.run_type || 'run') + '</div><div class="' + (r.status === 'ok' ? 'status-good' : (r.status === 'partial' ? 'status-warn' : 'status-bad')) + '">' + esc(r.status || 'unknown') + '</div></div><div class="mini-copy">Fetched ' + esc(String(r.items_fetched || 0)) + ' · Kept ' + esc(String(r.items_kept || 0)) + ' · Alerts ' + esc(String(r.alerts_created || 0)) + '</div><div class="mini-copy">Started ' + esc(r.started_at || 'n/a') + '</div>' + (r.error_text ? '<div class="mini-copy status-warn">' + esc(r.error_text) + '</div>' : '') + '</div>').join('')
        : '<div class="empty"><div class="empty-title">No recent agent runs</div><div class="empty-sub">Run the agent to populate operational history.</div></div>');
      return;
  }

  if (section === 'scheduler'){
    const scheduler = ((state.schedulerStatus || {}).scheduler || {});
    const jobs = scheduler.jobs || [];
    title.textContent = 'Scheduler Status';
    subtitle.textContent = 'Current scheduler health, registered jobs, and next run times.';
    actions.innerHTML = '<a class="textlink" href="/scheduler-status" target="_blank" rel="noopener noreferrer">Open raw scheduler status</a>';
    body.innerHTML =
      panelCard('Scheduler Overview',
        '<div class="mini"><div class="mini-copy">Running: <strong>' + esc(String(!!scheduler.running)) + '</strong></div><div class="mini-copy">Job count: <strong>' + esc(String(scheduler.job_count || 0)) + '</strong></div></div>') +
      panelCard('Jobs', jobs.length
        ? jobs.map(j => '<div class="mini"><div class="mini-title">' + esc(j.id || 'job') + '</div><div class="mini-copy">Next run: ' + esc(j.next_run_time || 'n/a') + '</div><div class="mini-copy">Trigger: ' + esc(j.trigger || 'n/a') + '</div></div>').join('')
        : '<div class="empty"><div class="empty-title">No scheduler jobs visible</div><div class="empty-sub">Scheduler state is present but no jobs were returned.</div></div>');
      return;
  }

  if (section === 'alerts'){
    let alerts = [...(((state.payload || {}).top_alerts) || [])];
    const sortBy = state.currentAlertSort || 'newest';
    if (state.alertUnreadOnly){
      alerts = alerts.filter(alert => !state.alertReadMap[String(alert.url || alert.headline || '')]);
    }
    if (sortBy === 'impact'){
      alerts.sort((a,b) => {
        const aMatch = state.cards.find(c => c.url === a.url) || {};
        const bMatch = state.cards.find(c => c.url === b.url) || {};
        return (bMatch.impact_score || 0) - (aMatch.impact_score || 0);
      });
    }
    const importantNow = alerts.filter(alert => isStarred(alert) || priorityWeight(alert.priority) >= 3).slice(0, 5);
    title.textContent = 'Alerts';
    subtitle.textContent = 'Readable alert workflow with local read / unread state, sorting, and in-terminal reading.';
    actions.innerHTML = '<button class="btn small" id="alertSortNewestBtn">Sort newest</button><button class="btn small" id="alertSortImpactBtn">Sort impact</button><button class="btn small" id="alertUnreadOnlyBtn">' + (state.alertUnreadOnly ? 'Show all alerts' : 'Unread only') + '</button><a class="textlink" href="/alerts" target="_blank" rel="noopener noreferrer">Open raw alerts</a>';
    body.innerHTML =
      panelCard('Most Important Now', importantNow.length
        ? importantNow.map(alert => '<div class="mini queue-card ' + cardStateClasses(alert) + '"><div class="row"><div class="mini-title">' + esc(alert.headline || '') + '</div><div class="status-warn">' + esc((alert.priority || 'watch').toUpperCase()) + '</div></div><div class="mini-copy">' + esc(alert.reason || '') + (isStarred(alert) ? ' · starred' : '') + '</div><div>' + criticalStatusBadge(alert.status) + staleBadge(alert.ttl) + '</div>' + confidenceBar(alert.confidence_score) + '</div>').join('')
        : '<div class="mini"><div class="mini-copy">No starred or high-priority alerts in the current view.</div></div>') +
      panelCard('Alert List', '<div class="mini"><div class="mini-copy">Visible alerts: <strong>' + esc(String(alerts.length)) + '</strong> · unread: <strong>' + esc(String(unreadAlertCount())) + '</strong> · important: <strong>' + esc(String(importantNow.length)) + '</strong></div></div>' + (alerts.length
      ? alerts.map(alert => {
          const key = String(alert.url || alert.headline || '');
          const read = !!state.alertReadMap[key];
          const articleIndex = articleIndexForAlert(alert);
          const starred = isStarred(alert);
          return '<div class="mini ' + (starred ? 'queue-card ' : '') + cardStateClasses(alert) + '"><div class="row"><div class="mini-title">' + esc(alert.headline || '') + '</div><div class="' + (read ? 'status-good' : 'status-warn') + '">' + (read ? 'read' : 'unread') + '</div></div><div class="mini-copy">' + esc((alert.priority || '').toUpperCase()) + ' · ' + esc(alert.reason || '') + (starred ? ' · starred' : '') + '</div><div class="mini-copy">Confidence ' + esc(String(Math.round(Number(alert.confidence_score || 0) * 100))) + '%</div><div>' + criticalStatusBadge(alert.status) + staleBadge(alert.ttl) + '</div>' + confidenceBar(alert.confidence_score) + '<div class="actions"><button class="btn small" data-alert-toggle="' + esc(key) + '">' + (read ? 'Mark unread' : 'Mark read') + '</button><button class="btn small" data-alert-star="' + esc(key) + '">' + (starred ? 'Unstar' : 'Star') + '</button>' + (articleIndex >= 0 ? '<button class="btn small" data-open-alert-article="' + esc(String(articleIndex)) + '">Read here</button>' : '') + '<a class="linkbtn" href="' + esc(alert.url || '#') + '" target="_blank" rel="noopener noreferrer">Open article</a></div></div>';
        }).join('')
      : '<div class="empty"><div class="empty-title">No alerts stored yet</div><div class="empty-sub">Run the agent or wait for the scheduler to generate alert events.</div></div>'));
    setTimeout(() => {
      const newest = document.getElementById('alertSortNewestBtn');
      const impact = document.getElementById('alertSortImpactBtn');
      const unreadOnly = document.getElementById('alertUnreadOnlyBtn');
      if (newest) newest.addEventListener('click', () => { state.currentAlertSort = 'newest'; writeLocal(STORAGE_KEYS.alertSort, state.currentAlertSort); openOverlay('alerts'); });
      if (impact) impact.addEventListener('click', () => { state.currentAlertSort = 'impact'; writeLocal(STORAGE_KEYS.alertSort, state.currentAlertSort); openOverlay('alerts'); });
      if (unreadOnly) unreadOnly.addEventListener('click', () => {
        state.alertUnreadOnly = !state.alertUnreadOnly;
        writeLocal(STORAGE_KEYS.alertUnreadOnly, state.alertUnreadOnly);
        openOverlay('alerts');
      });
      document.querySelectorAll('[data-open-alert-article]').forEach(node => {
        node.addEventListener('click', () => {
          closeOverlay();
          openArticleDrawer(Number(node.getAttribute('data-open-alert-article')));
        });
      });
      document.querySelectorAll('[data-alert-star]').forEach(node => {
        node.addEventListener('click', () => {
          const key = node.getAttribute('data-alert-star');
          toggleArticleStar(key);
          openOverlay('alerts');
        });
      });
      document.querySelectorAll('[data-alert-toggle]').forEach(node => {
        node.addEventListener('click', () => {
          const key = node.getAttribute('data-alert-toggle');
          toggleAlertRead(key);
          openOverlay('alerts');
        });
      });
    }, 0);
    return;
  }

  if (section === 'goals'){
    const goals = state.agentGoals || [];
    title.textContent = 'Agent Goals';
    subtitle.textContent = 'Persistent active goals, priorities, and watch targets driving the agent loop.';
    actions.innerHTML = '<a class="textlink" href="/agent-goals" target="_blank" rel="noopener noreferrer">Open raw goals</a>';
    body.innerHTML = panelCard('Goals', goals.length
      ? goals.map(goal => '<div class="mini"><div class="row"><div class="mini-title">' + esc(goal.name || '') + '</div><div class="' + (goal.is_active ? 'status-good' : 'status-warn') + '">' + (goal.is_active ? 'active' : 'inactive') + '</div></div><div class="mini-copy">Priority ' + esc(String(goal.priority || 0)) + ' · watch ' + esc((goal.watch_targets || []).join(', ') || 'none') + '</div><div class="mini-copy">' + esc(goal.description || '') + '</div></div>').join('')
      : '<div class="empty"><div class="empty-title">No agent goals yet</div><div class="empty-sub">Use the goals endpoint or seed defaults through the backend.</div></div>');
    return;
  }

  if (section === 'theses'){
    const theses = state.theses || [];
    title.textContent = 'Theses';
    subtitle.textContent = 'Durable thesis state with current claim, confidence, evidence count, and contradiction count.';
    actions.innerHTML = '<a class="textlink" href="/terminal-data" target="_blank" rel="noopener noreferrer">Open raw terminal data</a>';
    body.innerHTML = panelCard('Thesis State', theses.length
      ? theses.map(item => {
          const status = String(item.status || 'active');
          return '<div class="mini queue-card" data-open-thesis="' + esc(item.thesis_key || '') + '"><div class="row"><div class="mini-title">' + esc(item.title || item.current_claim || item.thesis_key || '') + '</div><div>' + thesisStatusBadge(status) + '</div></div><div class="mini-copy">' + esc(item.current_claim || item.thesis_key || '') + '</div><div class="mini-copy">Confidence <strong>' + esc(confidenceText(item.confidence || 0.5)) + '</strong> ' + thesisVelocityArrow(item.confidence_velocity || 0) + ' · Evidence ' + esc(String(item.evidence_count || 0)) + ' · Contradictions ' + esc(String(item.contradiction_count || 0)) + '</div><div class="mini-copy"><em>' + esc(item.last_update_reason || 'No update reason recorded yet.') + '</em></div>' + thesisConfidenceBar(item.confidence || 0.5) + '</div>';
        }).join('')
      : '<div class="empty"><div class="empty-title">No thesis records yet</div><div class="empty-sub">Run the real agent loop to build durable thesis state.</div></div>');
    setTimeout(() => {
      document.querySelectorAll('[data-open-thesis]').forEach(node => {
        node.addEventListener('click', async () => {
          const detail = await fetchThesisDetail(node.getAttribute('data-open-thesis'));
          openThesisDrawer(detail, 'thesis');
        });
      });
    }, 0);
    return;
  }

  if (section === 'prices'){
    title.textContent = 'Market Prices';
    subtitle.textContent = 'Live price context for macro, energy, volatility, FX, and risk assets.';
    actions.innerHTML = '<a class="textlink" href="/api/prices" target="_blank" rel="noopener noreferrer">Open raw prices</a>';
    body.innerHTML = panelCard('Price Feed', '<div id="gc-overlay-prices" class="mini"><div class="mini-copy">Loading prices…</div></div>');
    setTimeout(async () => {
      const shell = document.getElementById('gc-overlay-prices');
      if (!shell) return;
      try{
        const data = await fetch('/api/prices').then(r => r.json());
        const prices = data.prices || [];
        shell.innerHTML = prices.length
          ? prices.map(p => {
              const col = p.change_pct > 0 ? '#3fb950' : (p.change_pct < 0 ? '#f85149' : '#8b949e');
              const arr = p.change_pct > 0 ? '▲' : (p.change_pct < 0 ? '▼' : '─');
              return '<div class="mini"><div class="row"><div class="mini-title">' + esc(p.symbol || '') + '</div><div style="color:' + col + '">' + arr + ' ' + esc((p.change_pct > 0 ? '+' : '') + String(Number(p.change_pct || 0).toFixed(2))) + '%</div></div><div class="mini-copy">' + esc(p.name || '') + '</div><div class="mini-copy">Price ' + esc(p.price == null ? '—' : Number(p.price).toLocaleString(undefined, {maximumFractionDigits: 2})) + '</div></div>';
            }).join('')
          : '<div class="mini"><div class="mini-copy">Price feed unavailable.</div></div>';
      }catch(err){
        shell.innerHTML = '<div class="mini"><div class="mini-copy">Price feed unavailable.</div></div>';
      }
    }, 0);
    return;
  }

  if (section === 'decisions'){
    const decisions = state.agentDecisions || [];
    title.textContent = 'Current Decisions';
    subtitle.textContent = 'Latest ignore, queue, alert, follow-up, downgrade, and upgrade decisions.';
    actions.innerHTML = '<a class="textlink" href="/agent-decisions" target="_blank" rel="noopener noreferrer">Open raw decisions</a>';
    body.innerHTML = panelCard('Decision Feed', decisions.length
      ? decisions.slice(0, 30).map(item => '<div class="mini"><div class="row"><div class="mini-title">' + esc(item.headline || 'Article #' + String(item.article_id || 'n/a')) + '</div><div class="status-warn">' + esc(String(item.decision_type || '').toUpperCase()) + '</div></div><div class="mini-copy">' + esc(item.reason || '') + '</div><div class="mini-copy">Priority ' + esc(String(item.priority_score || 0)) + ' · confidence ' + esc(String(item.confidence || 0)) + '% · ' + esc(item.created_at || '') + '</div></div>').join('')
      : '<div class="empty"><div class="empty-title">No decisions yet</div><div class="empty-sub">Run the real agent loop to create decisions.</div></div>');
    return;
  }

  if (section === 'tasks'){
    const tasks = state.agentTasks || [];
    title.textContent = 'Next Actions';
    subtitle.textContent = 'Actionable follow-ups and monitoring tasks created by the real agent loop.';
    actions.innerHTML = '<a class="textlink" href="/agent-tasks" target="_blank" rel="noopener noreferrer">Open raw tasks</a>';
    body.innerHTML = panelCard('Task Feed', tasks.length
      ? tasks.slice(0, 30).map(item => {
          const urgency = String(item.urgency_level || 'medium');
          const urgencyClass = urgency === 'urgent' || urgency === 'high' ? 'status-warn' : 'status-good';
          const contradiction = /contradiction/i.test(String(item.details || ''));
          const stale = String(item.task_type || '') === 'review' && /weakening|dropped/i.test(String(item.details || ''));
          return '<div class="mini ' + cardStateClasses(item) + '"><div class="row"><div class="mini-title">' + esc(item.title || '') + '</div><div class="' + (String(item.status || '') === 'open' ? 'status-good' : 'status-warn') + '">' + esc(String(item.status || '').toUpperCase()) + '</div></div><div>' + criticalStatusBadge(item.status) + staleBadge(item.ttl) + '</div><div class="mini-copy">' + esc(item.task_type || '') + ' · due ' + esc(item.due_hint || 'n/a') + '</div><div class="mini-copy">Sources ' + esc(String(item.source_count || 1)) + ' · Urgency <span class="' + urgencyClass + '">' + esc(urgency) + '</span> · Impact ' + esc(String(item.impact_radius || 'regional')) + '</div><div class="mini-copy">confidence ' + esc(String(Math.round(Number(item.confidence_score || 0) * 100))) + '%' + (item.ttl ? ' · ttl ' + esc(String(item.ttl)) : '') + '</div>' + confidenceBar(item.confidence_score) + '<div class="mini-copy">' + esc(item.details || '') + (contradiction ? ' · contradiction check' : '') + (stale ? ' · stale-risk review' : '') + '</div></div>';
        }).join('')
      : '<div class="empty"><div class="empty-title">No agent tasks yet</div><div class="empty-sub">Open tasks will appear after the real agent loop evaluates current stories.</div></div>');
    return;
  }

  if (section === 'actions'){
    const actionsList = state.agentActions || [];
    title.textContent = 'Actions';
    subtitle.textContent = 'Proposed action adapters with approval gate, preview-only payloads, and audit notes.';
    actions.innerHTML = '<a class="textlink" href="/agent-actions" target="_blank" rel="noopener noreferrer">Open raw actions</a>';
    body.innerHTML = panelCard('Proposed Actions', actionsList.length
      ? actionsList.map(item => {
          const status = String(item.status || 'proposed');
          const badgeClass = status === 'auto_approved' ? 'status-good' : (status === 'approved' ? 'status-good' : (status === 'rejected' ? 'status-bad' : 'status-warn'));
          return '<div class="mini queue-card" data-open-action="' + esc(String(item.id || '')) + '"><div class="row"><div class="mini-title">' + esc(item.action_type || 'action') + ' · ' + esc(item.thesis_claim || item.thesis_key || '') + '</div><div class="' + badgeClass + '">' + esc(status) + '</div></div><div class="mini-copy">Confidence ' + esc(confidenceText(item.confidence || 0)) + ' · evidence ' + esc(String(item.evidence_count || 0)) + '</div><div class="mini-copy">' + esc(item.audit_note || 'No audit note yet.') + '</div><div class="actions"><button class="btn small" data-approve-action="' + esc(String(item.id || '')) + '">Approve</button><button class="btn small" data-reject-action="' + esc(String(item.id || '')) + '">Reject</button><button class="btn small" data-preview-action="' + esc(String(item.id || '')) + '">Preview</button></div></div>';
        }).join('')
      : '<div class="empty"><div class="empty-title">No proposed actions</div><div class="empty-sub">Use the thesis drawer to propose email, Slack, or webhook payloads without executing them.</div></div>');
    setTimeout(() => {
      document.querySelectorAll('[data-preview-action]').forEach(node => {
        node.addEventListener('click', async () => {
          const actionId = node.getAttribute('data-preview-action');
          const action = (state.agentActions || []).find(item => String(item.id || '') === String(actionId || '')) || {};
          const preview = await fetchActionPreview(actionId);
          openThesisDrawer(Object.assign({}, action, preview), 'action');
        });
      });
      document.querySelectorAll('[data-open-action]').forEach(node => {
        node.addEventListener('click', async (event) => {
          if (event.target.closest('button')) return;
          const actionId = node.getAttribute('data-open-action');
          const action = (state.agentActions || []).find(item => String(item.id || '') === String(actionId || '')) || {};
          const preview = await fetchActionPreview(actionId);
          openThesisDrawer(Object.assign({}, action, preview), 'action');
        });
      });
      document.querySelectorAll('[data-approve-action]').forEach(node => {
        node.addEventListener('click', async (event) => {
          event.stopPropagation();
          await approveActionRequest(node.getAttribute('data-approve-action'));
          await reloadAll();
          openOverlay('actions');
        });
      });
      document.querySelectorAll('[data-reject-action]').forEach(node => {
        node.addEventListener('click', async (event) => {
          event.stopPropagation();
          await rejectActionRequest(node.getAttribute('data-reject-action'));
          await reloadAll();
          openOverlay('actions');
        });
      });
    }, 0);
    return;
  }

  if (section === 'reasoning'){
    const items = state.reasoningItems || [];
    title.textContent = 'Reasoning';
    subtitle.textContent = 'Stored implication chains and terminal risks from recent high-relevance stories.';
    actions.innerHTML = '<a class="textlink" href="/agent-reasoning" target="_blank" rel="noopener noreferrer">Open raw reasoning</a>';
    body.innerHTML = panelCard('Reasoning Chains', items.length
      ? items.map(item => '<div class="mini"><div class="mini-title">' + esc(item.terminal_risk || item.watchlist_suggestion || 'Reasoning chain') + '</div><div class="mini-copy">Watchlist suggestion: ' + esc(item.watchlist_suggestion || 'n/a') + ' · thesis ' + esc(item.thesis_key || 'n/a') + '</div><div class="mini-copy">' + esc((item.chain || []).map(step => (step.hop || '?') + '. ' + (step.from || '') + ' -> ' + (step.to || '')).join(' | ')) + '</div></div>').join('')
      : '<div class="empty"><div class="empty-title">No reasoning chains yet</div><div class="empty-sub">These appear after higher-relevance stories are processed.</div></div>');
    return;
  }

  if (section === 'briefing'){
    const item = state.latestBriefing || {};
    title.textContent = 'Latest Briefing';
    subtitle.textContent = 'Most recent stored intelligence briefing generated by the agent.';
    actions.innerHTML = '<a class="textlink" href="/agent-briefing/latest" target="_blank" rel="noopener noreferrer">Open raw briefing</a>';
    body.innerHTML = panelCard('Briefing', item && item.briefing_text
      ? '<div class="mini"><div class="mini-copy">Generated: <strong>' + esc(item.generated_at || 'n/a') + '</strong></div><div class="article-copy" style="font-size:15px">' + esc(item.briefing_text || '') + '</div></div>'
      : '<div class="empty"><div class="empty-title">No briefing yet</div><div class="empty-sub">A daily briefing appears after the real loop stores one.</div></div>');
    return;
  }

  if (section === 'calibration'){
    const report = (state.calibrationReport || {}).items || {};
    const rows = Object.keys(report).sort();
    title.textContent = 'Calibration';
    subtitle.textContent = 'Track record by source and category so future scoring can stay grounded.';
    actions.innerHTML = '<a class="textlink" href="/agent-calibration" target="_blank" rel="noopener noreferrer">Open raw calibration</a>';
    body.innerHTML = panelCard('Calibration Report', rows.length
      ? rows.map(source => '<div class="mini"><div class="mini-title">' + esc(source) + '</div><div class="mini-copy">' + (report[source] || []).map(item => esc((item.category || 'other') + ': ' + (item.calibration_grade || '?') + ' · acc ' + String(item.accuracy || 0))).join(' | ') + '</div></div>').join('')
      : '<div class="empty"><div class="empty-title">No calibration rows yet</div><div class="empty-sub">Reflection needs more history before calibration becomes meaningful.</div></div>');
    return;
  }

  if (section === 'diff'){
    const item = state.terminalDiff || {};
    const deltas = item.metric_deltas || {};
    title.textContent = 'Before / After Diff';
    subtitle.textContent = 'What changed between the latest run and the previous one.';
    actions.innerHTML = '<a class="textlink" href="/terminal/diff" target="_blank" rel="noopener noreferrer">Open raw run diff</a>';
    body.innerHTML =
      panelCard('Metric Delta', Object.keys(deltas).length
        ? '<div class="mini"><div class="mini-copy">' + Object.keys(deltas).map(key => esc(key + ': ' + String(deltas[key]))).join(' · ') + '</div></div>'
        : '<div class="mini"><div class="mini-copy">No diff is available yet.</div></div>') +
      panelCard('Thesis Changes', (item.thesis_changes || []).length
        ? item.thesis_changes.map(change => '<div class="mini"><div class="mini-title">' + esc(change.thesis_key || change.event_type || 'change') + '</div><div class="mini-copy">' + esc(change.event_type || '') + ' · ' + esc(change.note || '') + '</div></div>').join('')
        : '<div class="mini"><div class="mini-copy">No thesis changes were recorded in the latest diff window.</div></div>') +
      panelCard('Task and Action Changes', ((item.tasks_closed || []).length || (item.actions_changed || []).length)
        ? '<div class="mini"><div class="mini-copy">Tasks: ' + esc(String((item.tasks_closed || []).length)) + ' · Actions: ' + esc(String((item.actions_changed || []).length)) + '</div></div>'
        : '<div class="mini"><div class="mini-copy">No task or action changes were captured in the latest diff window.</div></div>');
    return;
  }

  if (section === 'drilldown'){
    const item = state.currentDrilldown || {};
    const thesis = item.thesis || {};
    const debate = state.currentDebate && String((state.currentDebate || {}).thesis_key || '') === String(thesis.thesis_key || '') ? state.currentDebate : null;
    title.textContent = 'Why This Happened';
    subtitle.textContent = 'Trace the path from article to cluster to thesis to action/result.';
    actions.innerHTML = thesis.thesis_key ? '<a class="textlink" href="/terminal/drilldown/' + encodePath(thesis.thesis_key) + '" target="_blank" rel="noopener noreferrer">Open raw drilldown</a><button class="btn small" id="gcDrilldownDebateBtn">Bull vs Bear</button>' : '';
    body.innerHTML =
      panelCard('Thesis', thesis && (thesis.current_claim || thesis.thesis_key)
        ? '<div class="mini"><div class="mini-title">' + esc(thesis.title || thesis.current_claim || thesis.thesis_key || '') + '</div><div class="mini-copy">Status ' + esc(thesis.status || 'unknown') + ' · evidence ' + esc(String(thesis.evidence_count || 0)) + ' · contradictions ' + esc(String(thesis.contradiction_count || 0)) + '</div><div class="mini-copy">' + esc(thesis.last_update_reason || 'No update reason recorded.') + '</div></div>'
        : '<div class="mini"><div class="mini-copy">No thesis drilldown data loaded yet.</div></div>') +
      panelCard('Trace', '<div class="mini"><div class="mini-copy">Articles: ' + esc(String(((item.trace || {}).articles || []).length)) + ' · Clusters: ' + esc(String(((item.trace || {}).clusters || []).length)) + ' · Decisions: ' + esc(String((item.decisions || []).length)) + ' · Reasoning chains: ' + esc(String((item.reasoning || []).length)) + ' · Actions: ' + esc(String((item.actions || []).length)) + '</div></div>') +
      panelCard('Timeline', (item.timeline || []).length
        ? (item.timeline || []).slice(-12).map(event => '<div class="mini"><div class="mini-title">' + esc(event.event_type || 'event') + '</div><div class="mini-copy">' + esc(event.note || '') + '</div><div class="mini-copy">' + esc(event.created_at || '') + '</div></div>').join('')
        : '<div class="mini"><div class="mini-copy">No thesis events recorded yet.</div></div>') +
      panelCard('Bull vs Bear', debate
        ? '<div class="mini"><div class="mini-title" style="color:#8ff0b2">' + esc(((debate.bull || {}).persona || 'Bull')) + '</div><div class="mini-copy">' + esc((debate.bull || {}).argument || '') + '</div><div class="mini-copy">Key point: ' + esc((debate.bull || {}).key_point || '') + '</div></div>' +
          '<div class="mini"><div class="mini-title" style="color:#ffb3b3">' + esc(((debate.bear || {}).persona || 'Bear')) + '</div><div class="mini-copy">' + esc((debate.bear || {}).argument || '') + '</div><div class="mini-copy">Key point: ' + esc((debate.bear || {}).key_point || '') + '</div></div>' +
          '<div class="mini"><div class="mini-title">Verdict</div><div class="mini-copy">' + esc(debate.verdict || '') + '</div><div class="mini-copy">' + esc(debate.mode === 'llm' ? 'Powered by GeoClaw AI' : 'Rule-based fallback') + '</div></div>'
        : '<div class="mini"><div class="mini-copy">Load a bull-vs-bear debate for this thesis.</div></div>');
    setTimeout(() => {
      const debateBtn = document.getElementById('gcDrilldownDebateBtn');
      if (debateBtn && thesis.thesis_key){
        debateBtn.addEventListener('click', async () => {
          state.currentDebate = await fetchDebate(thesis.thesis_key);
          openOverlay('drilldown');
        });
      }
    }, 0);
    return;
  }

  if (section === 'journal'){
    const journal = state.agentJournal || [];
    title.textContent = 'Agent Journal';
    subtitle.textContent = 'Observe, decide, act, verify, remember, and adapt entries recorded for each real run.';
    actions.innerHTML = '<a class="textlink" href="/agent-journal" target="_blank" rel="noopener noreferrer">Open raw journal</a>';
    body.innerHTML = panelCard('Journal Feed', journal.length
      ? journal.slice(0, 25).map(item => '<div class="mini"><div class="mini-title">' + esc(item.summary || item.journal_type || 'Journal') + '</div><div class="mini-copy">' + esc(item.created_at || '') + '</div><div class="mini-copy">' + esc(JSON.stringify(item.metrics || {})) + '</div></div>').join('')
      : '<div class="empty"><div class="empty-title">No journal entries yet</div><div class="empty-sub">The real loop will write run-by-run agent notes here.</div></div>');
    return;
  }

  if (section === 'outcomes'){
    const outcomes = (state.agentOutcomes || {}).outcomes || {};
    title.textContent = 'Confirmed vs Invalidated';
    subtitle.textContent = 'Evaluation loop outcomes for prior thesis items.';
    actions.innerHTML = '<a class="textlink" href="/agent-outcomes" target="_blank" rel="noopener noreferrer">Open raw outcomes</a>';
    body.innerHTML = panelCard('Outcome Summary',
      '<div class="mini"><div class="mini-copy">Confirmed: <strong>' + esc(String(outcomes.confirmed || 0)) + '</strong></div><div class="mini-copy">Weakened: <strong>' + esc(String(outcomes.weakened || 0)) + '</strong></div><div class="mini-copy">Contradicted: <strong>' + esc(String(outcomes.contradicted || 0)) + '</strong></div><div class="mini-copy">Stale: <strong>' + esc(String(outcomes.stale || 0)) + '</strong></div></div>');
    return;
  }

  const data = state.whatChanged || {};
  const summary = data.summary || {};
  const recentArticles = data.recent_articles || [];
  const recentAlerts = data.recent_alerts || [];
  const delta = data.delta || {};
  title.textContent = 'What Changed';
  subtitle.textContent = 'Readable summary of new stories, new alerts, and run deltas.';
  actions.innerHTML = '<a class="textlink" href="/what-changed" target="_blank" rel="noopener noreferrer">Open raw what changed</a>';
  body.innerHTML =
    panelCard('Change Summary',
      '<div class="mini"><div class="mini-copy">New articles (30m): <strong>' + esc(String(summary.new_articles || 0)) + '</strong></div><div class="mini-copy">New alerts (30m): <strong>' + esc(String(summary.new_alerts || 0)) + '</strong></div><div class="mini-copy">Latest run: <strong>' + esc(summary.latest_run_status || 'n/a') + '</strong></div></div>') +
    panelCard('Run Delta', Object.keys(delta).length
      ? '<div class="mini"><div class="mini-copy">Fetched Δ: ' + esc(String(delta.items_fetched_delta || 0)) + '</div><div class="mini-copy">Kept Δ: ' + esc(String(delta.items_kept_delta || 0)) + '</div><div class="mini-copy">Alerts Δ: ' + esc(String(delta.alerts_created_delta || 0)) + '</div></div>'
      : '<div class="mini"><div class="mini-copy">No comparison delta available yet.</div></div>') +
    panelCard('Recent Headlines', recentArticles.length
      ? recentArticles.map(x => '<div class="mini"><div class="mini-title">' + esc(x.headline || '') + '</div><div class="mini-copy">' + esc(x.source || 'Unknown') + ' · impact ' + esc(String(x.impact_score || 0)) + '</div></div>').join('')
      : '<div class="empty"><div class="empty-title">No recent headlines</div><div class="empty-sub">This panel will populate as new material is ingested.</div></div>') +
    panelCard('Recent Alerts', recentAlerts.length
      ? recentAlerts.map(x => '<div class="mini"><div class="mini-title">' + esc(x.headline || '') + '</div><div class="mini-copy">' + esc((x.priority || '').toUpperCase()) + ' · ' + esc(x.reason || '') + '</div></div>').join('')
      : '<div class="mini"><div class="mini-copy">No new alerts in the current window.</div></div>');
}
function closeOverlay(){
  const overlay = document.getElementById('overlay');
  overlay.classList.remove('open');
  overlay.setAttribute('aria-hidden', 'true');
}
async function proposeAction(actionType, thesisKey){
  const thesis = (state.theses || []).find(item => String(item.thesis_key || '') === String(thesisKey || '')) || {};
  const data = await fetchJson('/agent-actions/propose', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      action_type: actionType,
      thesis_key: thesisKey,
      confidence: thesis.confidence,
      evidence_count: thesis.evidence_count,
      triggered_by: 'terminal'
    })
  });
  state.agentActions = [((data || {}).item || {}), ...(state.agentActions || []).filter(item => Number(item.id || 0) !== Number((((data || {}).item || {}).id || 0)))];
  return (data || {}).item || {};
}
async function fetchThesisDetail(thesisKey){
  const data = await fetchJson('/agent-thesis/' + encodePath(thesisKey));
  return (data || {}).item || {};
}
async function fetchDrilldown(thesisKey){
  const data = await fetchJson('/terminal/drilldown/' + encodePath(thesisKey));
  return (data || {}).item || {};
}
async function fetchDebate(thesisKey){
  const data = await fetchJson('/api/debate/' + encodePath(thesisKey));
  return (data || {}).debate || null;
}
async function fetchActionPreview(actionId){
  const data = await fetchJson('/agent-actions/' + String(actionId) + '/preview');
  return (data || {}).item || {};
}
async function approveActionRequest(actionId){
  const data = await fetchJson('/agent-actions/' + String(actionId) + '/approve', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({approved_by: 'terminal'})
  });
  return (data || {}).item || {};
}
async function rejectActionRequest(actionId){
  const data = await fetchJson('/agent-actions/' + String(actionId) + '/reject', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({reason: 'Rejected from terminal'})
  });
  return (data || {}).item || {};
}
function closeThesisDrawer(){
  const drawer = document.getElementById('thesisDrawer');
  drawer.classList.remove('open');
  drawer.setAttribute('aria-hidden', 'true');
}
function openThesisDrawer(detail, mode){
  const drawer = document.getElementById('thesisDrawer');
  const title = document.getElementById('thesisDrawerTitle');
  const meta = document.getElementById('thesisDrawerMeta');
  const body = document.getElementById('thesisDrawerBody');
  const foot = document.getElementById('thesisDrawerActions');
  const position = document.getElementById('thesisDrawerPosition');
  drawer.classList.add('open');
  drawer.setAttribute('aria-hidden', 'false');

  if (mode === 'action'){
    state.currentActionDetail = detail || {};
    title.textContent = 'Action Detail';
    meta.textContent = (detail.action_type || 'action') + ' · ' + (detail.status || 'unknown');
    position.textContent = 'Proposed action';
    body.innerHTML =
      '<div class="article-title">' + esc(detail.thesis_claim || detail.thesis_key || 'Action') + '</div>' +
      '<div class="tag-grid"><span class="badge">' + esc(detail.action_type || 'action') + '</span><span class="badge">' + esc(detail.status || 'unknown') + '</span></div>' +
      '<div class="detail-meta" style="margin-top:18px">' +
        '<div class="meta-box"><div class="label">Confidence</div><div class="value">' + esc(confidenceText(detail.confidence || 0)) + '</div></div>' +
        '<div class="meta-box"><div class="label">Evidence</div><div class="value">' + esc(String(detail.evidence_count || 0)) + '</div></div>' +
        '<div class="meta-box"><div class="label">Triggered By</div><div class="value">' + esc(detail.triggered_by || 'n/a') + '</div></div>' +
      '</div>' +
      '<div class="insight-grid" style="margin-top:18px">' +
        '<div class="insight-box"><strong>Audit Note</strong><div class="article-copy" style="font-size:15px">' + esc(detail.audit_note || 'No audit note yet.') + '</div></div>' +
        '<div class="insight-box"><strong>Preview</strong><div class="article-copy" style="font-size:15px">' + esc(JSON.stringify(detail.preview || detail.payload || {}, null, 2)) + '</div></div>' +
      '</div>';
    foot.innerHTML = detail.thesis_key
      ? '<button class="btn small" data-open-drilldown="' + esc(detail.thesis_key || '') + '">Why this happened</button><button class="btn small" data-open-debate="' + esc(detail.thesis_key || '') + '">Bull vs Bear</button>'
      : '';
    setTimeout(() => {
      document.querySelectorAll('[data-open-drilldown]').forEach(node => {
        node.addEventListener('click', async () => {
          const item = await fetchDrilldown(node.getAttribute('data-open-drilldown'));
          state.currentDrilldown = item;
          state.currentDebate = null;
          closeThesisDrawer();
          openOverlay('drilldown');
        });
      });
      document.querySelectorAll('[data-open-debate]').forEach(node => {
        node.addEventListener('click', async () => {
          const thesisKey = node.getAttribute('data-open-debate');
          state.currentDrilldown = await fetchDrilldown(thesisKey);
          state.currentDebate = await fetchDebate(thesisKey);
          closeThesisDrawer();
          openOverlay('drilldown');
        });
      });
    }, 0);
    return;
  }

  state.currentThesisDetail = detail || {};
  title.textContent = 'Thesis Detail';
  meta.textContent = (detail.status || 'active') + ' · confidence ' + confidenceText(detail.confidence || 0);
  position.textContent = 'Thesis evidence and linked work';
  body.innerHTML =
    '<div class="article-title">' + esc(detail.title || detail.current_claim || detail.thesis_key || 'Thesis') + '</div>' +
    '<div class="article-copy" style="font-size:15px;margin-top:8px">' + esc(detail.current_claim || '') + '</div>' +
    thesisConfidenceBar(detail.confidence || 0.5) +
    '<div class="tag-grid" style="margin-top:12px"><span class="badge">' + esc(detail.status || 'active') + '</span><span class="badge">Evidence ' + esc(String(detail.evidence_count || 0)) + '</span><span class="badge">Contradictions ' + esc(String(detail.contradiction_count || 0)) + '</span></div>' +
    '<div class="mini-copy" style="margin-top:10px"><em>' + esc(detail.last_update_reason || 'No update reason recorded yet.') + '</em></div>' +
    '<div class="detail-meta" style="margin-top:18px">' +
      '<div class="meta-box"><div class="label">Last Updated</div><div class="value">' + esc(detail.last_updated_at || 'n/a') + '</div></div>' +
      '<div class="meta-box"><div class="label">Status</div><div class="value">' + esc(detail.status || 'active') + '</div></div>' +
      '<div class="meta-box"><div class="label">Thesis Key</div><div class="value">' + esc(detail.thesis_key || '') + '</div></div>' +
    '</div>' +
    '<div class="insight-grid" style="margin-top:18px">' +
      '<div class="insight-box"><strong>Linked Articles</strong><div class="article-copy" style="font-size:15px">' + ((detail.linked_articles || []).length ? detail.linked_articles.map(item => '<div><a class="textlink" href="' + esc(item.url || '#') + '" target="_blank" rel="noopener noreferrer">' + esc(item.headline || '') + '</a></div>').join('') : 'No linked articles yet.') + '</div></div>' +
      '<div class="insight-box"><strong>Linked Decisions</strong><div class="article-copy" style="font-size:15px">' + ((detail.linked_decisions || []).length ? detail.linked_decisions.map(item => '<div>' + esc(item.summary || '') + '</div>').join('') : 'No linked decisions yet.') + '</div></div>' +
      '<div class="insight-box"><strong>Linked Tasks</strong><div class="article-copy" style="font-size:15px">' + ((detail.linked_tasks || []).length ? detail.linked_tasks.map(item => '<div>' + esc((item.title || '') + ' · ' + (item.status || 'unknown')) + '</div>').join('') : 'No linked tasks yet.') + '</div></div>' +
    '</div>';
  foot.innerHTML =
    '<button class="btn small" data-open-drilldown="' + esc(detail.thesis_key || '') + '">Why this happened</button>' +
    '<button class="btn small" data-open-debate="' + esc(detail.thesis_key || '') + '">Bull vs Bear</button>' +
    '<button class="btn small" data-propose-action="email_summary" data-thesis-key="' + esc(detail.thesis_key || '') + '">Propose email</button>' +
    '<button class="btn small" data-propose-action="slack_payload" data-thesis-key="' + esc(detail.thesis_key || '') + '">Propose Slack</button>' +
    '<button class="btn small" data-propose-action="webhook" data-thesis-key="' + esc(detail.thesis_key || '') + '">Propose webhook</button>';
  setTimeout(() => {
    document.querySelectorAll('[data-open-drilldown]').forEach(node => {
      node.addEventListener('click', async () => {
        const item = await fetchDrilldown(node.getAttribute('data-open-drilldown'));
        state.currentDrilldown = item;
        state.currentDebate = null;
        closeThesisDrawer();
        openOverlay('drilldown');
      });
    });
    document.querySelectorAll('[data-open-debate]').forEach(node => {
      node.addEventListener('click', async () => {
        const thesisKey = node.getAttribute('data-open-debate');
        state.currentDrilldown = await fetchDrilldown(thesisKey);
        state.currentDebate = await fetchDebate(thesisKey);
        closeThesisDrawer();
        openOverlay('drilldown');
      });
    });
    document.querySelectorAll('[data-propose-action]').forEach(node => {
      node.addEventListener('click', async () => {
        const item = await proposeAction(node.getAttribute('data-propose-action'), node.getAttribute('data-thesis-key'));
        closeThesisDrawer();
        await reloadAll();
        if (item && item.id) openOverlay('actions');
      });
    });
  }, 0);
}
function openArticleDrawer(index){
  if (index < 0 || index >= state.visibleCards.length) return;
  state.currentArticleIndex = index;
  state.articleDrawerForcedFocus = !state.focusMode;
  state.focusMode = true;
  setBodyModes();
  const card = state.visibleCards[index];
  const matches = watchlistMatches(card);
  const headlineHtml = highlightText(card.headline || '', matches);
  const summaryHtml = highlightText(card.summary || 'No summary available yet.', matches);
  const thesisHtml = highlightText(card.thesis || 'No thesis yet', matches);
  const watchHtml = highlightText(card.what_to_watch || 'No watch items yet', matches);
  const bullHtml = highlightText(card.bull_case || 'No bull case yet', matches);
  const bearHtml = highlightText(card.bear_case || 'No bear case yet', matches);
  const watchTags = (card.watchlist_hits || []).map(item => '<span class="badge watch">' + esc(String(item).toUpperCase()) + '</span>').join('');
  const alertTags = (card.alert_tags || []).map(item => '<span class="badge alert">' + esc(String(item).toUpperCase()) + '</span>').join('');
  const assetTags = (card.asset_tags || []).map(item => '<span class="badge asset">' + esc(String(item).toUpperCase()) + '</span>').join('');
  const trustLabel = String(card.trust_label || 'unverified');
  const contradictionBadge = card.has_contradiction ? '<span class="badge contradiction">Contradiction</span>' : '';
  const staleBadge = card.stale_signal ? '<span class="badge stale">Needs refresh</span>' : '';
  document.getElementById('articleDrawer').classList.add('open');
  document.getElementById('articleDrawer').setAttribute('aria-hidden', 'false');
  document.getElementById('articleDrawerMeta').textContent = (card.source || 'Unknown') + ' · ' + relTime(card.published_at || '') + ' · impact ' + String(card.impact_score || 0);
  document.getElementById('articleDrawerPosition').textContent = 'Article ' + String(index + 1) + ' of ' + String(state.visibleCards.length);
  document.getElementById('articleExternalLink').href = card.url || '#';
  document.getElementById('prevArticleBtn').disabled = index <= 0;
  document.getElementById('nextArticleBtn').disabled = index >= (state.visibleCards.length - 1);
  const alertKey = String(card.url || card.headline || '');
  document.getElementById('markArticleReadBtn').textContent = state.alertReadMap[alertKey] ? 'Mark alert unread' : 'Mark alert read';
  document.getElementById('toggleArticleStarBtn').textContent = isStarred(card) ? 'Remove important' : 'Mark important';
  document.getElementById('prevUnreadBtn').disabled = !nextUnreadQueueItem(-1);
  document.getElementById('nextUnreadBtn').disabled = !nextUnreadQueueItem(1);
  document.getElementById('articleDrawerBody').innerHTML =
    '<div class="leftline"><span class="signal ' + esc(String(card.signal || 'Neutral').toLowerCase()) + '">' + esc(card.signal || 'Neutral') + '</span><span class="badge trust-' + esc(trustLabel) + '">Trust: ' + esc(trustLabel) + '</span>' + contradictionBadge + staleBadge + (isStarred(card) ? '<span class="badge star">Important</span>' : '') + (matches.length ? '<span class="badge watchmatch">Watchlist: ' + esc(matches.join(', ')) + '</span>' : '') + '</div>' +
    '<div class="article-title">' + headlineHtml + '</div>' +
    '<div class="article-copy">' + summaryHtml + '</div>' +
    '<div class="tag-grid">' + assetTags + alertTags + watchTags + (matches.length ? matches.map(item => '<span class="badge watchmatch">' + esc(String(item).toUpperCase()) + '</span>').join('') : '<span class="badge">No watchlist match</span>') + '</div>' +
    '<div class="insight-grid" style="margin-top:18px">' +
      '<div class="insight-box"><strong>Thesis</strong><div class="article-copy" style="font-size:15px">' + thesisHtml + '</div></div>' +
      '<div class="insight-box"><strong>What To Watch</strong><div class="article-copy" style="font-size:15px">' + watchHtml + '</div></div>' +
      '<div class="insight-box"><strong>Bull Case</strong><div class="article-copy" style="font-size:15px">' + bullHtml + '</div></div>' +
      '<div class="insight-box"><strong>Bear Case</strong><div class="article-copy" style="font-size:15px">' + bearHtml + '</div></div>' +
    '</div>' +
    '<div class="detail-meta" style="margin-top:18px">' +
      '<div class="meta-box"><div class="label">Published</div><div class="value">' + esc(card.published_at || 'n/a') + '</div></div>' +
      '<div class="meta-box"><div class="label">Source</div><div class="value">' + esc(card.source || 'Unknown') + '</div></div>' +
      '<div class="meta-box"><div class="label">Source Trust</div><div class="value">' + esc(trustLabel + ' · ' + (card.quality_note || 'Use corroboration before acting.')) + '</div></div>' +
      '<div class="meta-box"><div class="label">Confidence</div><div class="value">' + esc(confidenceText(cardDisplayConfidence(card))) + ' · ' + esc(String(card.confidence_source || 'article')) + (card.has_contradiction ? ' · contradiction flagged' : '') + (card.stale_signal ? ' · stale-risk' : '') + '</div></div>' +
    '</div>' +
    '<div class="detail-meta">' +
      '<div class="meta-box"><div class="label">Alert Tags</div><div class="value">' + esc(joinTags(card.alert_tags || [])) + '</div></div>' +
      '<div class="meta-box"><div class="label">Watchlist Hits</div><div class="value">' + esc(joinTags(card.watchlist_hits || matches)) + '</div></div>' +
      '<div class="meta-box"><div class="label">Asset Tags</div><div class="value">' + esc(joinTags(card.asset_tags || [])) + '</div></div>' +
    '</div>';
}
function closeArticleDrawer(){
  document.getElementById('articleDrawer').classList.remove('open');
  document.getElementById('articleDrawer').setAttribute('aria-hidden', 'true');
  if (state.articleDrawerForcedFocus){
    state.focusMode = false;
    state.articleDrawerForcedFocus = false;
    setBodyModes();
  }
}
function renderBanner(){
  const box = document.getElementById('warningBanner');
  const health = state.sourceHealth || {};
  const summary = health.summary || {};
  const notes = (summary.banner_notes || []).slice(0, 2);
  if ((health.sources || []).some(x => x.name === 'gdelt' && x.status === 'limited')){
    notes.length = 0;
    notes.push('GDELT is cooling down after a timeout. RSS remains active and the terminal stays usable.');
  }
  if (!notes.length){
    box.style.display = 'none';
    return;
  }
  box.style.display = '';
  box.innerHTML = notes.join(' · ');
}
function renderProviderBadges(){
  const box = document.getElementById('providerBadgeBar');
  const badges = ((state.payload || {}).provider_badges || []);
  const dots = document.getElementById('sidebarProviderDots');
  if (!badges.length){
    box.innerHTML = '';
    if (dots) dots.innerHTML = '';
    return;
  }
  box.innerHTML = badges.map(x => {
    const cls = String(x.status || 'unknown');
    const label = String(x.label || x.name || '').toUpperCase();
    const reason = x.reason ? ' · ' + esc(x.reason) : '';
    return '<div class="provider-pill ' + esc(cls) + '">' + esc(label) + ' · ' + esc(cls) + reason + '</div>';
  }).join('');
  if (dots){
    dots.innerHTML = badges.map(x => '<span class="provider-dot ' + esc(String(x.status || 'unknown')) + '" title="' + esc(String(x.label || x.name || 'provider') + ' ' + String(x.status || 'unknown')) + '"></span>').join('');
  }
}
function renderSummaryStrip(){
  const payload = state.payload || {};
  const stats = payload.stats || {};
  const health = state.sourceHealth || {};
  const summary = health.summary || {};
  const scheduler = ((state.schedulerStatus || {}).scheduler || {});
  const cardsCount = (payload.cards || []).length;
  const latestRun = latestAgentRun();
  const completedRun = latestCompletedRun();
  const completedAt = completedRun.finished_at || completedRun.completed_at || completedRun.started_at || '';
  const watchMatches = String(stats.watchlist_hits || 0);
  const realLoop = (state.agentStatus || {}).real_loop || {};
  const agentSummary = state.agentSummary || {};
  const importantCount = unreadQueue().filter(item => item.starred || priorityWeight(item.alert.priority) >= 3).length;
  document.getElementById('summaryMain').textContent = cardsCount ? cardsCount + ' stories are ready to read' : 'No stories loaded yet';
  document.getElementById('summaryNote').textContent = 'Watchlist matches: ' + watchMatches + ' · Alerts: ' + String(stats.alerts || 0) + ' · Unread: ' + String(unreadAlertCount()) + ' · Needs attention: ' + String(importantCount) + ' · Scheduled jobs: ' + String(scheduler.job_count || 0);
  document.getElementById('agentSummaryMain').textContent = agentSummary.summary
    ? ('Reviewed ' + String(agentSummary.stories_reviewed || 0) + ' stories · updated ' + String(agentSummary.theses_updated || 0) + ' theses')
    : (latestRun.run_type ? String(latestRun.run_type).replace(/_/g, ' ') + ' · ' + String(latestRun.status || 'unknown') : 'No recent agent run');
  document.getElementById('agentSummaryNote').textContent = isRunningAgent
    ? 'The agent is working now. Extra clicks are paused until it finishes.'
    : (agentSummary.summary
      ? ('Actions proposed ' + String(agentSummary.actions_proposed || 0) + ' · tasks closed ' + String(agentSummary.tasks_closed || 0) + ' · LLM path ' + String(agentSummary.llm_path || 'fallback') + (agentSummary.top_reason ? ' · ' + String(agentSummary.top_reason) : ''))
      : (latestRun.run_type ? 'Fetched ' + String(latestRun.items_fetched || 0) + ', kept ' + String(latestRun.items_kept || 0) + ', alerts ' + String(latestRun.alerts_created || 0) + ' · decisions ' + String(realLoop.decision_count || 0) + ' · open tasks ' + String(realLoop.task_count || 0) + (completedAt ? ' · last finished ' + relTime(completedAt) : '') : 'Use Run Agent to refresh stories and alerts.'));
  const enabled = summary.enabled_sources || [];
  const marketMode = summary.market_data_mode || 'missing';
  document.getElementById('providerSummaryMain').textContent = enabled.length ? enabled.join(' + ').toUpperCase() : 'No active providers';
  document.getElementById('providerSummaryNote').textContent = 'Market data is currently ' + marketMode + '. Missing providers stay quiet unless an active source has a problem.';
  const sidebarAlertCount = document.getElementById('sidebarAlertCount');
  const sidebarAgentStatus = document.getElementById('sidebarAgentStatus');
  if (sidebarAlertCount) sidebarAlertCount.textContent = String(unreadAlertCount()) + ' unread · ' + String(stats.alerts || 0) + ' total';
  if (sidebarAgentStatus) sidebarAgentStatus.textContent = isRunningAgent || isRunningRealAgent ? 'Running' : (latestRun.status ? String(latestRun.status).toUpperCase() : 'Idle');
  syncRunAgentButton();
}
function renderMarket(){
  const box = document.getElementById('marketstrip');
  const preferred = ['SPY', 'BTC-USD', 'GLD', 'EURUSD'];
  let items = ((state.payload || {}).market_snapshot || []).filter(x => x && x.price != null);
  const preferredItems = preferred.map(symbol => items.find(x => String(x.symbol || '').toUpperCase() === symbol)).filter(Boolean);
  if (preferredItems.length) items = preferredItems;
  else items = items.slice(0, 4);
  const health = state.sourceHealth || {};
  const summary = health.summary || {};
  const keys = health.keys || {};
  const sources = health.sources || [];
  const market = sources.find(x => String(x.name || '') === 'market') || {};
  const marketMode = summary.market_data_mode || (keys.alphavantage_configured ? 'live' : 'cached');
  if (!items.length){
    const note = market.note || (keys.alphavantage_configured ? 'Waiting for live snapshot' : 'Cached market values unavailable');
    box.innerHTML = '<div class="ticker tape-item"><div class="n">Market Snapshot</div><div class="p">No data</div><div class="small">' + esc(note) + '</div></div>';
    return;
  }
  box.innerHTML = items.map(x => {
    let cls = 'flat';
    const pct = x.change_pct;
    if (pct > 0) cls = 'up';
    if (pct < 0) cls = 'down';
    const itemMode = String(x.market_mode || marketMode || 'cached');
    const itemSource = String(x.data_source || '');
    const modeLabel = itemMode === 'live' ? 'LIVE' : (itemMode === 'mock' ? 'FALLBACK' : 'CACHED');
    const modeBadge = '<span class="mode-badge ' + esc(itemMode) + '">' + esc(modeLabel) + '</span>';
    const note = itemSource === 'mock_fallback' ? '<div class="small">Fallback baseline</div>' : '';
    return '<div class="ticker tape-item"><div class="n">' + esc(x.symbol || x.label || '') + '</div><div class="p">' + esc(x.price == null ? 'n/a' : String(x.price)) + '</div><div class="c ' + cls + '">' + esc(x.change_pct == null ? 'n/a' : String(x.change_pct) + '%') + '</div>' + modeBadge + note + '</div>';
  }).join('');
}
function renderReadablePanels(){
  const health = state.sourceHealth || {};
  const scheduler = ((state.schedulerStatus || {}).scheduler || {});
  const agent = state.agentStatus || {};
  const whatChanged = state.whatChanged || {};
  const agentSummary = state.agentSummary || {};
  const latestBriefing = state.latestBriefing || {};
  const calibrationItems = ((state.calibrationReport || {}).items || {});
  const sources = health.sources || [];
  const summary = health.summary || {};
  const latestRun = (agent.runs || [])[0] || {};
  document.getElementById('sourceHealthPreview').textContent = sources.length ? sources.map(s => String(s.name || '').toUpperCase() + ': ' + String(s.status || 'unknown')).join(' | ') : 'No source health loaded.';
  document.getElementById('whatChangedPreview').textContent = 'New stories ' + String(((whatChanged.summary || {}).new_articles || 0)) + ' · New alerts ' + String(((whatChanged.summary || {}).new_alerts || 0)) + ' · Latest run ' + String(((whatChanged.summary || {}).latest_run_status || 'n/a'));
  document.getElementById('agentPreview').textContent = latestRun.run_type ? String(latestRun.run_type).replace(/_/g, ' ') + ' is ' + String(latestRun.status || 'unknown') + ' · alerts ' + String(latestRun.alerts_created || 0) : 'No recent agent run.';
  document.getElementById('schedulerPreview').textContent = 'Running ' + String(!!scheduler.running) + ' · jobs ' + String(scheduler.job_count || 0) + ' · next ' + String((((scheduler.jobs || [])[0] || {}).next_run_time || 'n/a'));
  const agentSummaryPreview = document.getElementById('agentSummaryPreview');
  if (agentSummaryPreview){
    agentSummaryPreview.textContent = agentSummary.summary
      ? ('Stories ' + String(agentSummary.stories_reviewed || 0) + ' · theses ' + String(agentSummary.theses_updated || 0) + ' · actions ' + String(agentSummary.actions_proposed || 0))
      : 'No agent summary yet.';
  }
  const briefingPreview = document.getElementById('briefingPreview');
  if (briefingPreview){
    briefingPreview.textContent = latestBriefing.briefing_text
      ? String(latestBriefing.briefing_text || '').slice(0, 96) + (String(latestBriefing.briefing_text || '').length > 96 ? '…' : '')
      : 'No briefing stored yet.';
  }
  const calibrationPreview = document.getElementById('calibrationPreview');
  if (calibrationPreview){
    calibrationPreview.textContent = Object.keys(calibrationItems).length
      ? ('Tracked sources: ' + String(Object.keys(calibrationItems).length))
      : 'No calibration history yet.';
  }
  const completedRun = latestCompletedRun();
  const completedAt = completedRun.finished_at || completedRun.completed_at || completedRun.started_at || '';
  document.getElementById('statusBox').textContent = (isRunningAgent ? 'Agent run in progress. ' : '') + 'Last update: ' + String((state.payload || {}).updated_at || 'n/a') + ' · Last finished run: ' + String(completedAt ? relTime(completedAt) : 'n/a') + ' · Active sources: ' + String((summary.enabled_sources || []).join(', ') || 'none');
  document.getElementById('refreshStatusBox').textContent = (state.autoRefresh ? 'Auto-refresh is on' : 'Auto-refresh is off') + ' · last update ' + String((state.payload || {}).updated_at || 'n/a');
  const firstJob = (((state.schedulerStatus || {}).scheduler || {}).jobs || [])[0] || {};
  document.getElementById('schedulerNextRunBox').textContent = 'Scheduler next run: ' + String(firstJob.next_run_time || 'n/a');
  document.getElementById('filterHint').textContent = 'Use these filters to make the story list smaller. Trusted-only keeps stronger sources, junk-hide removes weaker ones, and watchlist-only shows your saved terms.';
}
function renderSidebars(){
  const alerts = ((state.payload || {}).top_alerts || []);
  const queue = unreadQueue();
  document.getElementById('alertList').innerHTML = alerts.length
    ? alerts.slice(0,4).map(x => {
        const key = String(x.url || x.headline || '');
        const read = !!state.alertReadMap[key];
        return '<div class="mini ' + cardStateClasses(x) + '"><div class="mini-title">' + esc(x.headline || '') + '</div><div class="mini-copy">' + esc((x.priority || '').toUpperCase()) + ' · ' + esc(x.reason || '') + (isStarred(x) ? ' · starred' : '') + '</div><div class="mini-copy">' + (read ? 'read' : 'unread') + ' · confidence ' + esc(String(Math.round(Number(x.confidence_score || 0) * 100))) + '%</div><div>' + criticalStatusBadge(x.status) + staleBadge(x.ttl) + '</div>' + confidenceBar(x.confidence_score) + '<div class="actions"><button class="btn small" data-alert-toggle-inline="' + esc(key) + '">' + (read ? 'Unread' : 'Read') + '</button><button class="btn small" data-alert-star-inline="' + esc(key) + '">' + (isStarred(x) ? 'Unstar' : 'Star') + '</button></div></div>';
      }).join('')
    : '<div class="mini"><div class="mini-copy">No stored alerts yet.</div></div>';
  document.querySelectorAll('[data-alert-toggle-inline]').forEach(node => {
    node.addEventListener('click', () => {
      const key = node.getAttribute('data-alert-toggle-inline');
      toggleAlertRead(key);
      renderSidebars();
    });
  });
  document.querySelectorAll('[data-alert-star-inline]').forEach(node => {
    node.addEventListener('click', () => {
      const key = node.getAttribute('data-alert-star-inline');
      toggleArticleStar(key);
      renderSidebars();
    });
  });
  document.getElementById('queueSummary').textContent = queue.length
    ? ('Unread queue: ' + String(queue.filter(item => !item.read).length) + ' · starred: ' + String(queue.filter(item => item.starred).length))
    : 'No unread or important alerts are currently queued.';
  document.getElementById('queueList').innerHTML = queue.length
    ? queue.slice(0, 4).map(item => '<div class="mini queue-card"><div class="mini-title">' + esc(item.alert.headline || '') + '</div><div class="mini-copy">' + esc((item.alert.priority || '').toUpperCase()) + ' · ' + (item.read ? 'read' : 'unread') + (item.starred ? ' · starred' : '') + '</div><div class="actions">' + (item.index >= 0 ? '<button class="btn small" data-open-queue-article="' + esc(String(item.index)) + '">Open</button>' : '') + '</div></div>').join('')
    : '<div class="mini"><div class="mini-copy">Queue will fill as unread or starred alerts accumulate.</div></div>';
  document.querySelectorAll('[data-open-queue-article]').forEach(node => {
    node.addEventListener('click', () => openArticleDrawer(Number(node.getAttribute('data-open-queue-article'))));
  });

  const sources = ((state.payload || {}).source_distribution || []);
  const maxS = sources.length ? Math.max(...sources.map(x => x.count || 0)) : 1;
  const sourcePrimary = document.getElementById('sourceList');
  const sourceSecondary = document.getElementById('sourceListSecondary');
  const sourceSimpleHtml = sources.length
    ? sources.slice(0, 3).map(x => '<div class="mini-simple-row"><span>' + esc(x.source) + '</span><strong>' + esc(String(x.count)) + '</strong></div>').join('')
    : '<div class="mini-copy">No source mix available.</div>';
  const sourceDetailedHtml = sources.length
    ? sources.map(x => '<div class="mini"><div class="row"><div class="mini-title">' + esc(x.source) + '</div><div class="small">' + esc(String(x.count)) + '</div></div><div class="barwrap"><div class="barfill" style="width:' + (((x.count || 0) / maxS) * 100) + '%"></div></div><div class="mini-copy">' + esc(isTrustedSource(x.source) ? 'Trusted source' : 'Use trusted-only or junk toggle to narrow the feed.') + '</div></div>').join('')
    : '<div class="mini"><div class="mini-copy">No source distribution available.</div></div>';
  if (sourcePrimary) sourcePrimary.innerHTML = sourceSimpleHtml;
  if (sourceSecondary) sourceSecondary.innerHTML = sourceDetailedHtml;

  const assets = ((state.payload || {}).asset_heat || []);
  const maxA = assets.length ? Math.max(...assets.map(x => x.count || 0)) : 1;
  const assetPrimary = document.getElementById('assetHeat');
  const assetSecondary = document.getElementById('assetHeatSecondary');
  const assetSimpleHtml = assets.length
    ? assets.slice(0, 3).map(x => '<div class="mini-simple-row"><span>' + esc(x.asset) + '</span><strong>' + esc(String(x.count)) + '</strong></div>').join('')
    : '<div class="mini-copy">No asset heat available.</div>';
  const assetDetailedHtml = assets.length
    ? assets.map(x => '<div class="mini"><div class="row"><div class="mini-title">' + esc(x.asset) + '</div><div class="small">' + esc(String(x.count)) + '</div></div><div class="barwrap"><div class="barfill" style="width:' + (((x.count || 0) / maxA) * 100) + '%"></div></div></div>').join('')
    : '<div class="mini"><div class="mini-copy">No asset heat available.</div></div>';
  if (assetPrimary) assetPrimary.innerHTML = assetSimpleHtml;
  if (assetSecondary) assetSecondary.innerHTML = assetDetailedHtml;
}
function renderCards(){
  const assetsAll = [...new Set(state.cards.flatMap(x => x.asset_tags || []))].sort();
  const sourcesAll = [...new Set(state.cards.map(x => x.source || 'Unknown'))].sort();
  setOptions('asset', assetsAll, 'Filter by asset');
  setOptions('source', sourcesAll, 'Filter by source');
  state.visibleCards = currentCards();
  const cards = state.visibleCards;
  const feed = document.getElementById('feed');
  const resultsBar = document.getElementById('resultsBar');
  const active = [];
  if (document.getElementById('signal').value) active.push('signal');
  if (document.getElementById('asset').value) active.push('asset');
  if (document.getElementById('source').value) active.push('source');
  if (document.getElementById('alertsOnly').checked) active.push('alerts');
  if (document.getElementById('trustedOnly').checked) active.push('trusted');
  if (document.getElementById('watchlistOnly').checked) active.push('watchlist');
  if (document.getElementById('hideJunkToggle').checked) active.push('junk-hidden');
  if (document.getElementById('q').value.trim()) active.push('search');
  if (state.quickFilter && state.quickFilter !== 'All') active.push(state.quickFilter.toLowerCase());
  const watchHits = cards.filter(card => watchlistMatches(card).length || (card.watchlist_hits || []).length).length;
  resultsBar.innerHTML = 'Showing <strong>' + String(cards.length) + '</strong> of <strong>' + String(state.cards.length) + '</strong> cards · watchlist matches in view: <strong>' + String(watchHits) + '</strong>' + (active.length ? ' · active filters: ' + active.join(', ') : ' · no filters applied');
  syncQuickFilterButtons();

  const gdelt = ((state.sourceHealth || {}).sources || []).find(x => x.name === 'gdelt');
  if (!cards.length && gdelt && gdelt.status === 'limited'){
    feed.innerHTML = '<div class="empty"><div class="empty-title">GDELT is cooling down</div><div class="empty-sub">RSS is still available, but the current filters may be too tight while GDELT is retrying. Try widening filters or wait for the next refresh.</div></div>';
    return;
  }
  if (!cards.length){
    feed.innerHTML = '<div class="empty"><div class="empty-title">No cards match these filters</div><div class="empty-sub">Try clearing filters, widening asset or source selection, or turning off trusted-only, junk-hide, or watchlist-only if the feed is too narrow.</div></div>';
    return;
  }

  feed.innerHTML = cards.map((x, idx) => {
    const sig = String(x.signal || 'Neutral').toLowerCase();
    const category = cardCategory(x);
    const assets = (x.asset_tags || []).slice(0, 3).map(a => '<span class="badge asset">' + esc(a) + '</span>').join('');
    const alerts = (x.alert_tags || []).slice(0, 3).map(a => '<span class="badge alert">' + esc(a) + '</span>').join('');
    const watch = (x.watchlist_hits || []).slice(0, 3).map(a => '<span class="badge watch">' + esc(String(a).toUpperCase()) + '</span>').join('');
    const trust = '<span class="badge trust-' + esc(String(x.trust_label || 'unverified')) + '">' + esc(String(x.trust_label || 'unverified')) + '</span>';
    const star = isStarred(x) ? '<span class="badge star">Important</span>' : '';
    const contradiction = x.has_contradiction ? '<span class="badge contradiction">Contradiction</span>' : '';
    const stale = x.stale_signal ? '<span class="badge stale">Stale-risk</span>' : '';
    const matches = watchlistMatches(x);
    const watchMatches = matches.map(a => '<span class="badge watchmatch">' + esc(String(a).toUpperCase()) + '</span>').join('');
    const headlineHtml = highlightText(x.headline || '', matches);
    const whyItMattersHtml = x.why_it_matters ? '<div class="mini-copy"><em>' + esc(x.why_it_matters) + '</em></div>' : '';
    const summaryHtml = highlightText(x.summary || 'No summary available for this card yet.', matches);
    const detailsId = 'detail_' + idx;
    const categoryBadge = '<span class="category-tag ' + categoryClass(category) + '">' + esc(category) + '</span>';
    const storySignal = '<span class="badge">' + esc(signalLabel(x.signal)) + '</span>';
    const watchSummary = x.what_to_watch || 'No next step recorded yet.';
    const thesisSummary = x.thesis || x.why_it_matters || 'No extra reasoning stored yet.';
    return '<article class="card ' + sig + ' ' + (matches.length ? 'has-watch' : '') + '" data-card-index="' + String(idx) + '">' +
      '<div class="card-meta-row"><div class="leftline">' + categoryBadge + storySignal + star + contradiction + stale + '</div><div class="rightline"><span class="score">Impact ' + esc(String(x.impact_score || 0)) + '</span></div></div>' +
      '<div class="card-topline"><div class="headline bloomberg-headline">' + headlineHtml + '</div><button class="btn small card-open-btn" data-open-article="' + String(idx) + '">Open</button></div>' +
      whyItMattersHtml +
      '<div class="row"><div class="leftline"><span class="source source-line">' + esc(x.source || 'Unknown') + ' · ' + esc(relTime(x.published_at || '')) + '</span>' + trust + watchMatches + '</div></div>' +
      '<div class="summary bloomberg-summary-copy">' + summaryHtml + '</div>' +
      '<div class="story-facts">' +
        '<div class="story-fact"><strong>Why it matters</strong><span>' + esc(thesisSummary) + '</span></div>' +
        '<div class="story-fact"><strong>What to watch next</strong><span>' + esc(watchSummary) + '</span></div>' +
      '</div>' +
      '<div class="tag-grid compact-tags">' + assets + alerts + watch + '</div>' +
      confidenceBar(cardDisplayConfidence(x)) +
      '<details class="detail-drop" id="' + esc(detailsId) + '">' +
        '<summary>More context</summary>' +
        '<div class="detail-body">' +
          '<div class="mini-copy">' + esc(x.quality_note || (isLowQualitySource(x) ? 'This source may be hidden when the junk toggle is on.' : 'Open the full drawer for complete article detail.')) + '</div>' +
          '<div class="insight-grid" style="margin-top:14px">' +
            '<div class="insight-box"><strong>Thesis</strong><div class="insight-copy">' + esc(x.thesis || 'No thesis yet') + '</div></div>' +
            '<div class="insight-box"><strong>What To Watch</strong><div class="insight-copy">' + esc(x.what_to_watch || 'No watch items yet') + '</div></div>' +
            '<div class="insight-box"><strong>Bull Case</strong><div class="insight-copy">' + esc(x.bull_case || 'No bull case yet') + '</div></div>' +
            '<div class="insight-box"><strong>Bear Case</strong><div class="insight-copy">' + esc(x.bear_case || 'No bear case yet') + '</div></div>' +
          '</div>' +
          '<div class="detail-meta">' +
            '<div class="meta-box"><div class="label">Published</div><div class="value">' + esc(x.published_at || 'n/a') + '</div></div>' +
            '<div class="meta-box"><div class="label">Fetched</div><div class="value">' + esc(x.fetched_at || 'n/a') + '</div></div>' +
            '<div class="meta-box"><div class="label">URL</div><div class="value">' + esc(x.url || 'n/a') + '</div></div>' +
          '</div>' +
          '<div class="detail-meta">' +
            '<div class="meta-box"><div class="label">Alert Tags</div><div class="value">' + esc((x.alert_tags || []).join(', ') || 'None') + '</div></div>' +
            '<div class="meta-box"><div class="label">Watchlist Hits</div><div class="value">' + esc((x.watchlist_hits || []).join(', ') || 'None') + '</div></div>' +
            '<div class="meta-box"><div class="label">Asset Tags</div><div class="value">' + esc((x.asset_tags || []).join(', ') || 'None') + '</div></div>' +
          '</div>' +
        '</div>' +
      '</details>' +
      '<div class="actions simple-action-row">' +
        '<button class="btn small" data-toggle-star-card="' + esc(articleKey(x)) + '">' + (isStarred(x) ? 'Unstar' : 'Star') + '</button>' +
        '<a class="linkbtn" href="' + esc(x.url || '#') + '" target="_blank" rel="noopener noreferrer">Open article</a>' +
        '<a class="textlink" href="/terminal-data" target="_blank" rel="noopener noreferrer">Open raw JSON</a>' +
      '</div>' +
    '</article>';
  }).join('');

  document.querySelectorAll('[data-open-article]').forEach(node => {
    node.addEventListener('click', (event) => {
      event.stopPropagation();
      openArticleDrawer(Number(node.getAttribute('data-open-article')));
    });
  });
  document.querySelectorAll('[data-card-index]').forEach(node => {
    node.addEventListener('click', (event) => {
      if (event.target.closest('details') || event.target.closest('a') || event.target.closest('button')) return;
      openArticleDrawer(Number(node.getAttribute('data-card-index')));
    });
  });
  document.querySelectorAll('[data-toggle-star-card]').forEach(node => {
    node.addEventListener('click', (event) => {
      event.stopPropagation();
      toggleArticleStar(node.getAttribute('data-toggle-star-card'));
      renderCards();
    });
  });
}
function renderStats(){
  const s = ((state.payload || {}).stats || {});
  document.getElementById('sArticles').textContent = String(s.articles || 0);
  document.getElementById('sBull').textContent = String(s.bullish || 0);
  document.getElementById('sBear').textContent = String(s.bearish || 0);
  document.getElementById('sNeutral').textContent = String(s.neutral || 0);
  document.getElementById('sAlerts').textContent = String(s.alerts || 0);
  document.getElementById('sUnreadAlerts').textContent = String(unreadAlertCount());
}
function scheduleRefresh(){
  if (refreshTimer){
    clearInterval(refreshTimer);
    refreshTimer = null;
  }
  if (state.autoRefresh){
    refreshTimer = setInterval(reloadAll, 60000);
  }
}
async function fetchJson(url, options){
  const res = await fetch(url, Object.assign({cache:'no-store'}, options || {}));
  if (!res.ok) throw new Error(url + ' HTTP ' + res.status);
  const data = await res.json();
  if (data.status === 'error') throw new Error(url + ' ' + (data.error || 'error'));
  return data;
}
async function gcLoadPrices() {
  const body = document.getElementById('gc-prices-body');
  const upd = document.getElementById('gc-prices-updated');
  if (!body) return;
  try {
    const data = await fetch('/api/prices').then(r => r.json());
    const prices = data.prices || [];
    if (!prices.length) {
      body.innerHTML = '<div style="color:#8b949e;font-size:12px;font-family:monospace;">Price feed unavailable</div>';
      return;
    }
    body.innerHTML = prices.map(p => {
      const col = p.change_pct > 0 ? '#3fb950' : (p.change_pct < 0 ? '#f85149' : '#8b949e');
      const arr = p.change_pct > 0 ? '▲' : (p.change_pct < 0 ? '▼' : '─');
      return `<div style="background:#0d1117;border:1px solid #30363d;border-radius:6px;padding:8px 10px;font-family:monospace;">
        <div style="color:#8b949e;font-size:10px;">${gcEscape(p.symbol)}</div>
        <div style="color:#e6edf3;font-size:13px;font-weight:700;">${p.price != null ? Number(p.price).toLocaleString(undefined,{maximumFractionDigits:2}) : '—'}</div>
        <div style="color:${col};font-size:11px;">${arr} ${p.change_pct != null ? (p.change_pct > 0 ? '+' : '') + Number(p.change_pct).toFixed(2) + '%' : '—'}</div>
        <div style="color:#8b949e;font-size:10px;margin-top:2px;">${gcEscape((p.name || '').slice(0, 18))}</div>
      </div>`;
    }).join('');
    if (upd) upd.textContent = 'updated ' + new Date().toLocaleTimeString();
  } catch (e) {
    body.innerHTML = '<div style="color:#f85149;font-size:12px;font-family:monospace;">⚠ Price fetch failed</div>';
  }
}
async function gcLoadAlerts() {
  const body = document.getElementById('gc-alerts-body');
  const badge = document.getElementById('gc-alerts-badge');
  if (!body) return;
  try {
    const data = await fetch('/api/alerts?limit=10').then(r => r.json());
    const alerts = data.alerts || [];
    const unread = alerts.filter(a => !a.resolved).length;
    if (badge) {
      badge.textContent = unread;
      badge.style.display = unread > 0 ? 'inline' : 'none';
    }
    if (!alerts.length) {
      body.innerHTML = '<div style="color:#3fb950;">✓ No active alerts</div>';
      return;
    }
    body.innerHTML = alerts.map(a => {
      const col = a.resolved ? '#30363d' : '#f85149';
      const mins = Math.round((Date.now() - new Date(a.created_at)) / 60000);
      const ago = mins < 60 ? `${mins}m ago` : `${Math.floor(mins / 60)}h ago`;
      return `<div style="border-left:3px solid ${col};padding:6px 10px;margin-bottom:6px;opacity:${a.resolved ? '0.5' : '1'};">
        <div style="display:flex;justify-content:space-between;align-items:center;">
          <span style="color:#e6edf3;font-weight:600;">${gcEscape(a.title || a.alert_type || 'Alert')}</span>
          <span style="color:#8b949e;font-size:10px;">${ago}</span>
        </div>
        <div style="color:#8b949e;margin-top:3px;font-size:11px;">${gcEscape((a.body || '').slice(0, 120) || a.alert_type || '')}</div>
        ${!a.resolved ? `<button onclick="gcDismissAlert(${a.id})" style="margin-top:4px;background:none;border:1px solid #30363d;color:#8b949e;border-radius:3px;padding:1px 6px;cursor:pointer;font-size:10px;">Dismiss</button>` : ''}
      </div>`;
    }).join('');
  } catch (e) {
    body.innerHTML = '<div style="color:#f85149;">⚠ Alerts unavailable</div>';
  }
}
async function gcDismissAlert(id) {
  try {
    await fetch(`/api/alerts/${id}/dismiss`, {method:'POST'});
    gcLoadAlerts();
  } catch (e) {}
}
async function ensureOverlayData(section){
  const jobs = [];
  if (section === 'summary' && !state.agentSummary) jobs.push(fetchJson('/terminal/agent-summary').then(data => { state.agentSummary = (data || {}).item || null; }));
  if (section === 'source' && !state.sourceHealth) jobs.push(fetchJson('/source-health').then(data => { state.sourceHealth = data; }));
  if (section === 'agent' && !state.agentStatus) jobs.push(fetchJson('/agent-status').then(data => { state.agentStatus = data; }));
  if (section === 'scheduler' && !state.schedulerStatus) jobs.push(fetchJson('/scheduler-status').then(data => { state.schedulerStatus = data; }));
  if (section === 'changes' && !state.whatChanged) jobs.push(fetchJson('/what-changed').then(data => { state.whatChanged = data; }));
  if (section === 'alerts' && !state.payload) jobs.push(fetchJson('/terminal-data').then(data => { state.payload = data; state.cards = data.cards || []; }));
  if (section === 'goals' && !(state.agentGoals || []).length) jobs.push(fetchJson('/agent-goals').then(data => { state.agentGoals = (data || {}).items || []; }));
  if (section === 'theses' && !(state.theses || []).length) jobs.push(fetchJson('/terminal-data').then(data => { state.payload = data; state.cards = data.cards || []; state.theses = data.theses || []; }));
  if (section === 'decisions' && !(state.agentDecisions || []).length) jobs.push(fetchJson('/agent-decisions').then(data => { state.agentDecisions = (data || {}).items || []; }));
  if (section === 'tasks' && !(state.agentTasks || []).length) jobs.push(fetchJson('/agent-tasks').then(data => { state.agentTasks = (data || {}).items || []; }));
  if (section === 'actions' && !(state.agentActions || []).length) jobs.push(fetchJson('/agent-actions').then(data => { state.agentActions = (data || {}).items || []; }));
  if (section === 'reasoning' && !(state.reasoningItems || []).length) jobs.push(fetchJson('/agent-reasoning').then(data => { state.reasoningItems = (data || {}).items || []; }));
  if (section === 'briefing' && !state.latestBriefing) jobs.push(fetchJson('/agent-briefing/latest').then(data => { state.latestBriefing = (data || {}).item || null; }));
  if (section === 'calibration' && !state.calibrationReport) jobs.push(fetchJson('/agent-calibration').then(data => { state.calibrationReport = (data || {}).report || null; }));
  if (section === 'diff' && !state.terminalDiff) jobs.push(fetchJson('/terminal/diff').then(data => { state.terminalDiff = (data || {}).item || null; }));
  if (section === 'journal' && !(state.agentJournal || []).length) jobs.push(fetchJson('/agent-journal').then(data => { state.agentJournal = (data || {}).items || []; }));
  if (section === 'outcomes' && !state.agentOutcomes) jobs.push(fetchJson('/agent-outcomes').then(data => { state.agentOutcomes = data || {}; }));
  if (section === 'drilldown' && !state.currentDrilldown && state.currentThesisDetail && state.currentThesisDetail.thesis_key){
    jobs.push(fetchDrilldown(state.currentThesisDetail.thesis_key).then(item => { state.currentDrilldown = item || null; }));
  }
  if (jobs.length) await Promise.allSettled(jobs);
}
async function reloadAll(){
  try{
    document.getElementById('statusBox').textContent = 'Loading readable dashboard panels...';
    const endpoints = [
      ['payload', '/terminal-data'],
      ['agentStatus', '/agent-status'],
      ['schedulerStatus', '/scheduler-status'],
      ['sourceHealth', '/source-health'],
      ['whatChanged', '/what-changed'],
      ['operatorState', '/operator-state'],
      ['agentGoals', '/agent-goals'],
      ['agentJournal', '/agent-journal'],
      ['agentDecisions', '/agent-decisions'],
      ['agentTasks', '/agent-tasks'],
      ['agentActions', '/agent-actions'],
      ['agentMetrics', '/agent-metrics'],
      ['agentOutcomes', '/agent-outcomes'],
      ['agentQueue', '/agent-queue'],
      ['agentSummary', '/terminal/agent-summary'],
      ['terminalDiff', '/terminal/diff'],
      ['reasoningItems', '/agent-reasoning'],
      ['latestBriefing', '/agent-briefing/latest'],
      ['calibrationReport', '/agent-calibration'],
    ];
    const settled = await Promise.allSettled(endpoints.map(([, url]) => fetchJson(url)));
    const loaded = {};
    settled.forEach((result, index) => {
      loaded[endpoints[index][0]] = result.status === 'fulfilled' ? result.value : null;
    });

    if (loaded.payload){
      state.payload = loaded.payload;
      state.cards = loaded.payload.cards || [];
      state.theses = loaded.payload.theses || [];
    }
    if (loaded.agentStatus) state.agentStatus = loaded.agentStatus;
    if (loaded.agentGoals) state.agentGoals = (loaded.agentGoals || {}).items || [];
    if (loaded.agentJournal) state.agentJournal = (loaded.agentJournal || {}).items || [];
    if (loaded.agentDecisions) state.agentDecisions = (loaded.agentDecisions || {}).items || [];
    if (loaded.agentTasks) state.agentTasks = (loaded.agentTasks || {}).items || [];
    if (loaded.agentActions) state.agentActions = (loaded.agentActions || {}).items || [];
    if (loaded.agentMetrics) state.agentMetrics = loaded.agentMetrics || {};
    if (loaded.agentOutcomes) state.agentOutcomes = loaded.agentOutcomes || {};
    if (loaded.agentQueue) state.agentQueue = (loaded.agentQueue || {}).items || [];
    if (loaded.agentSummary) state.agentSummary = (loaded.agentSummary || {}).item || null;
    if (loaded.terminalDiff) state.terminalDiff = (loaded.terminalDiff || {}).item || null;
    if (loaded.reasoningItems) state.reasoningItems = (loaded.reasoningItems || {}).items || [];
    if (loaded.latestBriefing) state.latestBriefing = (loaded.latestBriefing || {}).item || null;
    if (loaded.calibrationReport) state.calibrationReport = (loaded.calibrationReport || {}).report || null;
    if (loaded.schedulerStatus) state.schedulerStatus = loaded.schedulerStatus;
    if (loaded.sourceHealth) state.sourceHealth = loaded.sourceHealth;
    if (loaded.whatChanged) state.whatChanged = loaded.whatChanged;
    applyOperatorState(((loaded.operatorState || {}).state) || (((loaded.payload || {}).operator_state) || {}));
    renderBanner();
    renderProviderBadges();
    renderSummaryStrip();
    renderWatchlist();
    renderStats();
    renderMarket();
    renderReadablePanels();
    renderSidebars();
    renderAgentPanels();
    renderCards();
    gcUpdateLiveBar();
  }catch(err){
    document.getElementById('feed').innerHTML = '<div class="empty"><div class="empty-title">Dashboard load failed</div><div class="empty-sub">' + esc(err.message) + '</div></div>';
    document.getElementById('statusBox').textContent = 'Error: ' + err.message;
    gcUpdateLiveBar();
  }
}
async function runAgentNow(){
  if (isRunningAgent) return;
  try{
    isRunningAgent = true;
    syncRunAgentButton();
    renderSummaryStrip();
    document.getElementById('statusBox').textContent = 'Running agent cycle now...';
    const data = await fetchJson('/agent-run-now');
    const result = data.result || {};
    document.getElementById('statusBox').textContent = 'Agent run finished · topic runs ' + String(result.topic_runs || 0) + ' · fetched ' + String(result.items_fetched || 0) + ' · kept ' + String(result.items_kept || 0) + ' · alerts ' + String(result.alerts_created || 0);
    await reloadAll();
    openOverlay('agent');
  }catch(err){
    document.getElementById('statusBox').textContent = 'Agent run failed: ' + err.message;
  }finally{
    isRunningAgent = false;
    syncRunAgentButton();
    renderSummaryStrip();
  }
}
async function runRealAgentNow(){
  if (isRunningRealAgent) return;
  try{
    isRunningRealAgent = true;
    syncRunAgentButton();
    document.getElementById('statusBox').textContent = 'Running real agent loop now...';
    const data = await fetchJson('/agent-run-real', {method: 'POST'});
    const result = data.result || {};
    document.getElementById('statusBox').textContent = 'Real agent loop finished · decisions ' + String(result.decisions_created || 0) + ' · tasks ' + String(result.tasks_created || 0) + ' · evaluations ' + String(result.evaluations_created || 0);
    await reloadAll();
    openOverlay('journal');
  }catch(err){
    document.getElementById('statusBox').textContent = 'Real agent loop failed: ' + err.message;
  }finally{
    isRunningRealAgent = false;
    syncRunAgentButton();
  }
}
async function gcUpdateLiveBar(){
  const bar = document.getElementById('gc-live-text');
  const dot = document.getElementById('gc-live-dot');
  if (!bar || !dot) return;
  try{
    const data = await fetchJson('/api/agent/status');
    const lastRun = data.last_run_at ? gcTimeAgo(data.last_run_at) : 'never';
    bar.textContent = 'Last run: ' + lastRun + ' | Theses: ' + String(data.thesis_count || 0) + ' | Articles (24h): ' + String(data.article_count || 0) + ' | Pending actions: ' + String(data.pending_actions || 0);
    dot.style.color = Number(data.thesis_count || 0) > 0 ? '#3fb950' : '#f85149';
  }catch(err){
    bar.textContent = '⚠ Status unavailable';
    dot.style.color = '#f85149';
  }
}
async function gcRunAgentNow(){
  if (isRunningRealAgent) return;
  const btn = document.getElementById('gcLiveRunBtn');
  if (btn){
    btn.textContent = '⟳ Running…';
    btn.disabled = true;
  }
  try{
    await runRealAgentNow();
    await gcUpdateLiveBar();
  }finally{
    if (btn){
      btn.textContent = '▶ Run Agent Now';
      btn.disabled = false;
    }
  }
}
function gcInitEventStream(){
  if (!window.EventSource) return;
  if (gcES) return;
  gcES = new EventSource('/api/events/stream');
  gcES.onmessage = (payload) => {
    try{
      const ev = JSON.parse(payload.data);
      if (ev.type === 'heartbeat') return;
      gcEventCount += 1;
      const countNode = document.getElementById('gc-event-count');
      if (countNode) countNode.textContent = String(gcEventCount);
      if (['thesis_confirmed', 'alert_fired', 'agent_run_complete'].includes(ev.type)){
        const bar = document.getElementById('gc-live-bar');
        if (bar){
          bar.style.borderColor = '#3fb950';
          setTimeout(() => { bar.style.borderColor = '#30363d'; }, 2000);
        }
        if (ev.type === 'agent_run_complete'){
          setTimeout(() => {
            if (typeof gcLoadTheses === 'function') gcLoadTheses();
            if (typeof gcLoadAlerts === 'function') gcLoadAlerts();
            gcUpdateLiveBar();
          }, 1000);
        }
      }
    }catch(err){}
  };
  gcES.onerror = () => {
    const bar = document.getElementById('gc-live-text');
    if (bar && !String(bar.textContent || '').includes('Status unavailable')){
      bar.textContent = '⚠ Live event stream disconnected, retrying…';
    }
  };
}
function gcShowMiniAskAnswer(text, color){
  const box = document.getElementById('gc-mini-ask-answer');
  if (!box) return;
  box.style.display = 'block';
  box.style.borderColor = color || '#263244';
  box.style.color = color || '#c9d5e4';
  box.innerHTML = gcEscape(text || '');
}
async function gcMiniAsk(){
  const input = document.getElementById('gc-mini-ask-input');
  if (!input) return;
  const question = String(input.value || '').trim();
  if (!question) return;
  gcShowMiniAskAnswer('Thinking…', '#8ab4ff');
  try{
    const data = await fetchJson('/api/ask', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({question})
    });
    const answer = String(data.answer || 'No answer returned.');
    gcShowMiniAskAnswer(answer, Number(data.confidence || 0) >= 0.6 ? '#9aebbf' : '#c9d5e4');
  }catch(err){
    gcShowMiniAskAnswer('Query failed: ' + err.message, '#ffb0b0');
  }
}

state.watchlist = readLocal(STORAGE_KEYS.watchlist, ['oil','gold','forex']);
state.alertReadMap = readLocal(STORAGE_KEYS.alertsRead, {});
state.starredArticleMap = readLocal(STORAGE_KEYS.starredArticles, {});
state.autoRefresh = readLocal(STORAGE_KEYS.autoRefresh, true);
state.compactMode = readLocal(STORAGE_KEYS.compactMode, false);
state.focusMode = readLocal(STORAGE_KEYS.focusMode, false);
state.currentAlertSort = readLocal(STORAGE_KEYS.alertSort, 'newest');
state.alertUnreadOnly = readLocal(STORAGE_KEYS.alertUnreadOnly, false);
setBodyModes();
syncRunAgentButton();

document.getElementById('autoRefreshToggle').checked = !!state.autoRefresh;
document.getElementById('refreshBtn').addEventListener('click', reloadAll);
document.getElementById('runAgentBtn').addEventListener('click', runAgentNow);
document.getElementById('runRealAgentBtn').addEventListener('click', runRealAgentNow);
document.getElementById('gcLiveRunBtn').addEventListener('click', gcRunAgentNow);
document.getElementById('gc-mini-ask-btn')?.addEventListener('click', gcMiniAsk);
document.getElementById('gc-mini-ask-input')?.addEventListener('keydown', (event) => {
  if (event.key === 'Enter'){
    event.preventDefault();
    gcMiniAsk();
  }
});
document.getElementById('focusModeBtn').addEventListener('click', () => {
  state.focusMode = !state.focusMode;
  writeLocal(STORAGE_KEYS.focusMode, state.focusMode);
  setBodyModes();
});
document.getElementById('compactModeBtn').addEventListener('click', () => {
  state.compactMode = !state.compactMode;
  writeLocal(STORAGE_KEYS.compactMode, state.compactMode);
  setBodyModes();
});
document.getElementById('closeOverlayBtn').addEventListener('click', closeOverlay);
document.getElementById('overlay').addEventListener('click', (event) => {
  if (event.target.id === 'overlay') closeOverlay();
});
document.querySelectorAll('[data-overlay]').forEach(node => {
  node.addEventListener('click', () => { openOverlay(node.getAttribute('data-overlay')); });
});
document.getElementById('closeArticleBtn').addEventListener('click', closeArticleDrawer);
document.getElementById('articleDrawer').addEventListener('click', (event) => {
  if (event.target.id === 'articleDrawer') closeArticleDrawer();
});
document.getElementById('closeThesisBtn').addEventListener('click', closeThesisDrawer);
document.getElementById('thesisDrawer').addEventListener('click', (event) => {
  if (event.target.id === 'thesisDrawer') closeThesisDrawer();
});
document.addEventListener('keydown', (event) => {
  const articleDrawer = document.getElementById('articleDrawer');
  const thesisDrawer = document.getElementById('thesisDrawer');
  const overlay = document.getElementById('overlay');
  const tagName = String((event.target || {}).tagName || '').toUpperCase();
  if (tagName !== 'INPUT' && tagName !== 'TEXTAREA'){
    if (event.key === '1') document.querySelector('[data-panel="summary"]')?.click();
    if (event.key === '2') document.querySelector('[data-panel="theses"]')?.click();
    if (event.key === '3') document.querySelector('[data-panel="actions"]')?.click();
    if (event.key === '4') document.querySelector('[data-panel="briefing"]')?.click();
    if (event.key === '5') document.querySelector('[data-panel="prices"]')?.click();
    if (event.key === '6') document.querySelector('[data-panel="alerts"]')?.click();
    if (event.key === 's' || event.key === 'S'){
      event.preventDefault();
      document.getElementById('q')?.focus();
      return;
    }
  }
  if (event.key === 'Escape' && thesisDrawer.classList.contains('open')){
    closeThesisDrawer();
    return;
  }
  if (event.key === 'Escape' && articleDrawer.classList.contains('open')){
    closeArticleDrawer();
    return;
  }
  if (event.key === 'Escape' && overlay.classList.contains('open')){
    closeOverlay();
    return;
  }
  if (!articleDrawer.classList.contains('open')) return;
  if (event.key === 'ArrowLeft'){
    event.preventDefault();
    openArticleDrawer(Math.max(0, state.currentArticleIndex - 1));
  }
  if (event.key === 'ArrowRight'){
    event.preventDefault();
    openArticleDrawer(Math.min(state.visibleCards.length - 1, state.currentArticleIndex + 1));
  }
  if (event.key.toLowerCase() === 'r'){
    event.preventDefault();
    document.getElementById('markArticleReadBtn').click();
  }
  if (event.key.toLowerCase() === 'i'){
    event.preventDefault();
    document.getElementById('toggleArticleStarBtn').click();
  }
});
document.getElementById('prevArticleBtn').addEventListener('click', () => openArticleDrawer(Math.max(0, state.currentArticleIndex - 1)));
document.getElementById('nextArticleBtn').addEventListener('click', () => openArticleDrawer(Math.min(state.visibleCards.length - 1, state.currentArticleIndex + 1)));
document.getElementById('prevUnreadBtn').addEventListener('click', () => {
  const item = nextUnreadQueueItem(-1);
  if (item) openArticleDrawer(item.index);
});
document.getElementById('nextUnreadBtn').addEventListener('click', () => {
  const item = nextUnreadQueueItem(1) || unreadQueue().find(entry => !entry.read && entry.index >= 0);
  if (item) openArticleDrawer(item.index);
});
document.getElementById('markArticleReadBtn').addEventListener('click', () => {
  const card = state.visibleCards[state.currentArticleIndex];
  if (!card) return;
  const key = String(card.url || card.headline || '');
  toggleAlertRead(key);
  openArticleDrawer(state.currentArticleIndex);
});
document.getElementById('toggleArticleStarBtn').addEventListener('click', () => {
  const card = state.visibleCards[state.currentArticleIndex];
  if (!card) return;
  toggleArticleStar(articleKey(card));
  openArticleDrawer(state.currentArticleIndex);
});
document.getElementById('addWatchBtn').addEventListener('click', () => {
  const input = document.getElementById('watchlistInput');
  addWatchTerms(String(input.value || '').split(','));
  input.value = '';
});
document.getElementById('watchlistInput').addEventListener('keydown', (event) => {
  if (event.key === 'Enter'){
    event.preventDefault();
    document.getElementById('addWatchBtn').click();
  }
});
document.getElementById('clearWatchBtn').addEventListener('click', () => {
  state.watchlist = [];
  saveOperatorState();
  renderWatchlist();
  renderCards();
});
document.querySelectorAll('.preset-btn').forEach(node => {
  node.addEventListener('click', () => addWatchTerms(PRESETS[node.getAttribute('data-preset')] || []));
});
document.getElementById('openNextUnreadBtn').addEventListener('click', () => {
  const item = unreadQueue().find(entry => !entry.read && entry.index >= 0) || unreadQueue().find(entry => entry.index >= 0);
  if (item) openArticleDrawer(item.index);
});
document.getElementById('openImportantAlertsBtn').addEventListener('click', () => openOverlay('alerts'));
document.querySelectorAll('[data-quick-filter]').forEach(node => {
  node.addEventListener('click', () => {
    state.quickFilter = node.getAttribute('data-quick-filter') || 'All';
    syncQuickFilterButtons();
    renderCards();
  });
});
document.getElementById('autoRefreshToggle').addEventListener('change', (event) => {
  state.autoRefresh = !!event.target.checked;
  writeLocal(STORAGE_KEYS.autoRefresh, state.autoRefresh);
  scheduleRefresh();
  renderReadablePanels();
});
document.getElementById('resetFiltersBtn').addEventListener('click', () => {
  document.getElementById('q').value = '';
  document.getElementById('signal').value = '';
  document.getElementById('asset').value = '';
  document.getElementById('source').value = '';
  document.getElementById('sort').value = 'impact';
  document.getElementById('alertsOnly').checked = false;
  document.getElementById('trustedOnly').checked = false;
  document.getElementById('watchlistOnly').checked = false;
  document.getElementById('hideJunkToggle').checked = false;
  renderCards();
});
['q','signal','asset','source','sort','alertsOnly','trustedOnly','watchlistOnly','hideJunkToggle'].forEach(id => {
  document.getElementById(id).addEventListener('input', renderCards);
  document.getElementById(id).addEventListener('change', renderCards);
});
document.querySelectorAll('.gc-nav-link').forEach(node => {
  const active = node.getAttribute('href') === window.location.pathname;
  node.style.color = active ? '#e6edf3' : '#8b949e';
  node.style.fontWeight = active ? '600' : '400';
});

scheduleRefresh();
renderWatchlist();
reloadAll();
gcLoadPrices();
gcLoadAlerts();
gcUpdateLiveBar();
gcInitEventStream();
if (liveBarTimer) clearInterval(liveBarTimer);
liveBarTimer = setInterval(gcUpdateLiveBar, 30000);
setInterval(gcLoadPrices, 60000);
setInterval(gcLoadAlerts, 60000);
