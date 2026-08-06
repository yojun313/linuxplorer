// ══════════════════════════════════════════════════════════════
// Linuxplorer — explorer.js
// window.__EXPLORER_DATA__ 는 explorer.html 인라인 스크립트가 채워둠
// ══════════════════════════════════════════════════════════════
(function () {
'use strict';

const DATA = window.__EXPLORER_DATA__ || {};
const ROOT = DATA.root;
const CURRENT = DATA.current;
const PARENT = DATA.parent;
const MODE = DATA.mode;
const SEARCH_ROOT = DATA.searchRoot;
const TOTAL = DATA.total;
const PAGE = DATA.page;
const PAGE_SIZE = DATA.pageSize;
const TOTAL_PAGES = DATA.totalPages;

let items = DATA.items || [];

let currentView = localStorage.getItem('exp_view') || 'details';
if (!['details', 'tiles', 'icons', 'column', 'gallery'].includes(currentView)) currentView = 'details';
let previewVisible = localStorage.getItem('exp_preview') === '1';
let selectedPath = null;
let selectedPaths = new Set();
let sortKey = DATA.sort || 'name';
let sortAsc = (DATA.order || 'asc') !== 'desc';
let ctxTargetPath = null;
let clipboard = null; // {action:'cut'|'copy', paths:[...]}
let dragPaths = [];

// ── UTIL: HTML escape (버그수정: 기존 esc()는 작은따옴표를 리터럴 백슬래시로
// 바꿔서 화면에 그대로 노출되는 문제, >, & 미이스케이프 문제가 있었음) ──
function escapeHtml(s) {
    return String(s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}
const esc = escapeHtml;
const escHtml = escapeHtml;

// 인라인 onclick="...('${path}')" 처럼 JS 문자열 리터럴을 HTML 속성 안에 넣을 때는
// JS용 이스케이프(백슬래시/작은따옴표) 후 HTML 속성용 이스케이프를 다시 해야 안전하다.
function jsAttrPath(path) {
    const jsEscaped = String(path).replace(/\\/g, '\\\\').replace(/'/g, "\\'");
    return escapeHtml(jsEscaped);
}

function formatSize(bytes) {
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let size = bytes, i = 0;
    while (size >= 1024 && i < units.length - 1) { size /= 1024; i++; }
    return i === 0 ? `${size} B` : `${size.toFixed(1)} ${units[i]}`;
}

function joinPath(a, b) { return a.replace(/\/+$/, '') + '/' + b; }

// ── ICONS ──
function fileIcon(item) {
    if (item.is_dir) return '📁';
    const m = {
        image:'🖼️', video:'🎬', audio:'🎵', document:'📕',
        text:'📄', archive:'📦', spreadsheet:'📊',
        presentation:'📽️', word:'📝',
    };
    return m[item.type] || '📃';
}

function typeLabel(item) {
    if (item.is_dir) return '파일 폴더';
    const m = {
        image:'이미지', video:'동영상', audio:'오디오',
        document:'문서', text:'텍스트', archive:'압축',
        spreadsheet:'스프레드시트', presentation:'프레젠테이션', word:'문서',
    };
    if (item.ext) return (m[item.type] || '파일') + ' (' + item.ext + ')';
    return m[item.type] || '파일';
}

function fmtDate(ts) {
    if (!ts) return '';
    const d = new Date(ts * 1000);
    return d.toLocaleDateString('ko-KR') + ' ' + d.toLocaleTimeString('ko-KR', {hour:'2-digit', minute:'2-digit'});
}

function rawUrl(path) { return '/explorer/serve_file?path=' + encodeURIComponent(path); }
function isBrowserPlayable(ext) { return ['.mp4','.webm','.ogg','.mov'].includes(ext); }

function syntaxHL(json) {
    json = escapeHtml(json);
    return json.replace(/("(\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d*)?(?:[eE][+\-]?\d+)?)/g, m => {
        let c = 'j-n';
        if (/^"/.test(m)) c = /:$/.test(m) ? 'j-k' : 'j-s';
        else if (/true|false/.test(m)) c = 'j-b';
        return '<span class="' + c + '">' + m + '</span>';
    });
}

// ── TOAST ──
function showToast(msg, type = 'info', duration = 3000) {
    const stack = document.getElementById('toastStack');
    if (!stack) return;
    const el = document.createElement('div');
    el.className = 'toast' + (type ? ' ' + type : '');
    el.textContent = msg;
    stack.appendChild(el);
    setTimeout(() => el.remove(), duration);
}

function showStatus(msg, duration = 2500) {
    const bar = document.getElementById('statusSelected');
    const prev = bar.textContent;
    bar.textContent = msg;
    setTimeout(() => { bar.textContent = prev; }, duration);
}

// ── 공용 모달 (alert/prompt/confirm 대체) ──
function openConfirm({ title, message, okLabel = '확인', danger = false }) {
    return new Promise(resolve => {
        const overlay = document.getElementById('modalOverlay');
        overlay.innerHTML = `
            <div class="modal-dialog">
                <div class="modal-header">${escapeHtml(title)}</div>
                <div class="modal-body"><p>${escapeHtml(message)}</p></div>
                <div class="modal-footer">
                    <button class="modal-btn" data-act="cancel">취소</button>
                    <button class="modal-btn ${danger ? 'danger' : 'primary'}" data-act="ok">${escapeHtml(okLabel)}</button>
                </div>
            </div>`;
        overlay.classList.add('open');
        const cleanup = (val) => { overlay.classList.remove('open'); overlay.innerHTML = ''; resolve(val); };
        overlay.querySelector('[data-act="cancel"]').onclick = () => cleanup(false);
        overlay.querySelector('[data-act="ok"]').onclick = () => cleanup(true);
        overlay.onclick = (e) => { if (e.target === overlay) cleanup(false); };
    });
}

function openPrompt({ title, label, value = '', okLabel = '확인' }) {
    return new Promise(resolve => {
        const overlay = document.getElementById('modalOverlay');
        overlay.innerHTML = `
            <div class="modal-dialog">
                <div class="modal-header">${escapeHtml(title)}</div>
                <div class="modal-body">
                    <div class="modal-field">
                        <label>${escapeHtml(label)}</label>
                        <input type="text" id="modalPromptInput" value="${escapeHtml(value)}">
                    </div>
                </div>
                <div class="modal-footer">
                    <button class="modal-btn" data-act="cancel">취소</button>
                    <button class="modal-btn primary" data-act="ok">${escapeHtml(okLabel)}</button>
                </div>
            </div>`;
        overlay.classList.add('open');
        const input = document.getElementById('modalPromptInput');
        input.focus(); input.select();
        const cleanup = (val) => { overlay.classList.remove('open'); overlay.innerHTML = ''; resolve(val); };
        overlay.querySelector('[data-act="cancel"]').onclick = () => cleanup(null);
        overlay.querySelector('[data-act="ok"]').onclick = () => cleanup(input.value);
        input.addEventListener('keydown', e => {
            if (e.key === 'Enter') cleanup(input.value);
            if (e.key === 'Escape') cleanup(null);
        });
        overlay.onclick = (e) => { if (e.target === overlay) cleanup(null); };
    });
}

// ── 공용 fetch 헬퍼 ──
async function apiFetch(url, body, method = 'POST', qs = '') {
    const res = await fetch(url + qs, {
        method,
        headers: body ? { 'Content-Type': 'application/json' } : {},
        body: body ? JSON.stringify(body) : undefined,
    });
    if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(err.detail || res.statusText);
    }
    return res.json();
}

// ── SORT (버그수정: 컬럼 전환 시 항상 asc로 리셋, 같은 컬럼 재클릭시에만 토글.
// 기존엔 url.searchParams.set('sort',key)로 먼저 덮어쓴 뒤 같은 파라미터로
// "이전에도 이 컬럼이었는지"를 비교해서 항상 무의미한 비교가 되고 있었음) ──
function sortBy(key) {
    const url = new URL(location.href);
    const prevSort = url.searchParams.get('sort') || 'name';
    const prevOrder = url.searchParams.get('order') || 'asc';
    const nextOrder = (prevSort === key && prevOrder === 'asc') ? 'desc' : 'asc';
    url.searchParams.set('sort', key);
    url.searchParams.set('order', nextOrder);
    url.searchParams.set('page', 1);
    location.href = url.toString();
}

function sortedItems() {
    return [...items].sort((a, b) => {
        if (a.is_dir !== b.is_dir) return a.is_dir ? -1 : 1;
        let va, vb;
        switch (sortKey) {
            case 'name': va = a.name.toLowerCase(); vb = b.name.toLowerCase(); break;
            case 'date': va = a.mtime; vb = b.mtime; break;
            case 'type': va = typeLabel(a); vb = typeLabel(b); break;
            case 'size': va = a.size_raw; vb = b.size_raw; break;
            default: va = a.name; vb = b.name;
        }
        let cmp = va < vb ? -1 : va > vb ? 1 : 0;
        return sortAsc ? cmp : -cmp;
    });
}

function updateSortHeaders() {
    ['name', 'date', 'type', 'size'].forEach(k => {
        const el = document.getElementById('sort-' + k);
        if (!el) return;
        el.textContent = (k === sortKey) ? (sortAsc ? '▲' : '▼') : '';
    });
}

// ── RENDER ──
function renderFiles() {
    const sorted = sortedItems();
    renderDetails(sorted);
    renderTiles(sorted);
    renderIcons(sorted);
    updateSortHeaders();
    updateStatus();
    renderPagination();
    if (currentView === 'gallery') renderGalleryFilmstrip(sorted);
}

function renderDetails(list) {
    const el = document.getElementById('fileListDetails');
    el.innerHTML = '';
    list.forEach(item => {
        const row = document.createElement('div');
        row.className = 'file-row' + (selectedPaths.has(item.path) ? ' selected' : '');
        row.dataset.path = item.path;
        row.innerHTML = `
            <div class="fr-name"><span class="file-icon">${fileIcon(item)}</span><span title="${escapeHtml(item.name)}">${escapeHtml(item.name)}</span></div>
            <div class="fr-date">${fmtDate(item.mtime)}</div>
            <div class="fr-type">${typeLabel(item)}</div>
            <div class="fr-size">${item.is_dir ? '' : item.size}</div>
        `;
        row.addEventListener('click', (e) => selectItem(e, item.path));
        row.addEventListener('dblclick', () => openItem(item.path, item.is_dir));
        addDragHandlers(row, item);
        el.appendChild(row);
    });
}

function renderTiles(list) {
    const el = document.getElementById('fileListTiles');
    el.innerHTML = '';
    list.forEach(item => {
        const div = document.createElement('div');
        div.className = 'grid-item' + (selectedPaths.has(item.path) ? ' selected' : '');
        div.dataset.path = item.path;
        div.innerHTML = `
            <span class="grid-icon">${fileIcon(item)}</span>
            <div class="grid-info">
                <span class="grid-name" title="${escapeHtml(item.name)}">${escapeHtml(item.name)}</span>
                <span class="grid-meta">${item.is_dir ? '폴더' : item.size}</span>
            </div>
        `;
        div.addEventListener('click', (e) => selectItem(e, item.path));
        div.addEventListener('dblclick', () => openItem(item.path, item.is_dir));
        addDragHandlers(div, item);
        el.appendChild(div);
    });
}

function renderIcons(list) {
    const el = document.getElementById('fileListIcons');
    el.innerHTML = '';
    list.forEach(item => {
        const div = document.createElement('div');
        div.className = 'icon-item' + (selectedPaths.has(item.path) ? ' selected' : '');
        div.dataset.path = item.path;
        const isImg = item.type === 'image';
        div.innerHTML = `
            ${isImg
                ? `<img class="icon-thumb" src="${rawUrl(item.path)}" loading="lazy" onerror="this.outerHTML='<span class=icon-big>🖼️</span>'">`
                : `<span class="icon-big">${fileIcon(item)}</span>`
            }
            <span class="icon-label" title="${escapeHtml(item.name)}">${escapeHtml(item.name)}</span>
        `;
        div.addEventListener('click', (e) => selectItem(e, item.path));
        div.addEventListener('dblclick', () => openItem(item.path, item.is_dir));
        addDragHandlers(div, item);
        el.appendChild(div);
    });
}

// ── VIEW MODE ──
function setView(mode) {
    currentView = mode;
    localStorage.setItem('exp_view', mode);
    document.getElementById('colHeaders').style.display = mode === 'details' ? 'flex' : 'none';
    document.getElementById('fileListDetails').style.display = mode === 'details' ? '' : 'none';
    document.getElementById('fileListTiles').style.display = mode === 'tiles' ? '' : 'none';
    document.getElementById('fileListIcons').style.display = mode === 'icons' ? '' : 'none';
    document.getElementById('fileListColumn').style.display = mode === 'column' ? 'flex' : 'none';
    document.getElementById('fileListGallery').style.display = mode === 'gallery' ? 'flex' : 'none';
    const zoomEl = document.getElementById('zoomControl');
    if (zoomEl) zoomEl.style.display = (mode === 'tiles' || mode === 'icons') ? 'flex' : 'none';
    document.querySelectorAll('.tb-btn[data-view]').forEach(b => b.classList.toggle('active', b.dataset.view === mode));
    if (mode === 'column') initColumnView();
    if (mode === 'gallery') { renderGalleryFilmstrip(sortedItems()); renderGalleryMain(); }
}

// ── PREVIEW ──
function togglePreview() {
    previewVisible = !previewVisible;
    localStorage.setItem('exp_preview', previewVisible ? '1' : '0');
    document.getElementById('previewPane').classList.toggle('hidden', !previewVisible);
    document.getElementById('tbPreview').classList.toggle('active', previewVisible);
    document.getElementById('previewResizer').style.display = previewVisible ? '' : 'none';
}

function selectAllItems() {
    selectedPaths = new Set(items.map(i => i.path));
    selectedPath = items.length ? items[items.length - 1].path : null;
    renderFiles();
    updateToolbarForSelection();
    document.getElementById('statusSelected').textContent = selectedPaths.size ? `선택: ${selectedPaths.size}개 ${selectedSizeLabel()}` : '';
}

function selectedSizeLabel() {
    if (!selectedPaths.size) return '';
    let total = 0, hasDir = false;
    selectedPaths.forEach(p => {
        const it = items.find(i => i.path === p);
        if (it && !it.is_dir) total += (it.size_raw || 0);
        else hasDir = true;
    });
    if (!total && !hasDir) return '';
    return `(${formatSize(total)}${hasDir ? '+' : ''})`;
}

function selectItem(e, path) {
    e.stopPropagation();
    if (e.ctrlKey || e.metaKey) {
        selectedPaths.has(path) ? selectedPaths.delete(path) : selectedPaths.add(path);
    } else if (e.shiftKey && selectedPath) {
        const sorted = sortedItems();
        const a = sorted.findIndex(i => i.path === selectedPath);
        const b = sorted.findIndex(i => i.path === path);
        const [lo, hi] = [Math.min(a, b), Math.max(a, b)];
        selectedPaths = new Set(sorted.slice(lo, hi + 1).map(i => i.path));
    } else {
        selectedPaths = new Set([path]);
    }
    selectedPath = path;
    renderFiles();
    loadPreview(path);
    updateToolbarForSelection();
    document.getElementById('statusSelected').textContent = `선택: ${selectedPaths.size}개 ${selectedSizeLabel()}`;
}

function updateToolbarForSelection() {
    const hasFile = [...selectedPaths].some(p => {
        const i = items.find(x => x.path === p);
        return i && !i.is_dir;
    });
    const hasDir = [...selectedPaths].some(p => {
        const i = items.find(x => x.path === p);
        return i && i.is_dir;
    });

    document.getElementById('tbOpen').style.display = selectedPaths.size === 1 ? '' : 'none';
    document.getElementById('tbDownload').style.display = hasFile ? '' : 'none';
    document.getElementById('tbDownloadDir').style.display = hasDir ? '' : 'none';

    if (hasFile && selectedPath) {
        document.getElementById('tbDownload').href = '/explorer/download_file?path=' + encodeURIComponent(selectedPath);
    }
    if (hasDir && selectedPath) {
        document.getElementById('tbDownloadDir').href = '/explorer/download_dir?path=' + encodeURIComponent(selectedPath);
    }
}

function openItem(path, isDir) {
    if (isDir) {
        location.href = '/explorer/view?path=' + encodeURIComponent(path);
    } else {
        openFullscreen();
    }
}

// ── PAGINATION ──
function renderPagination() {
    document.getElementById('paginationBar')?.remove();
    if (TOTAL_PAGES <= 1) return;

    const bar = document.createElement('div');
    bar.id = 'paginationBar';
    bar.style.cssText = `
        display:flex; align-items:center; justify-content:center;
        gap:4px; padding:8px 12px; border-top:1px solid var(--win-border);
        background:var(--win-toolbar); flex-shrink:0; font-size:12px;
    `;

    const makeBtn = (label, targetPage, disabled = false, active = false) => {
        const btn = document.createElement('button');
        btn.textContent = label;
        btn.disabled = disabled;
        btn.style.cssText = `
            padding:3px 10px; border-radius:var(--fx-radius); cursor:pointer; font-size:12px;
            border:1px solid var(--win-border);
            background:${active ? 'var(--win-accent)' : 'var(--win-content)'};
            color:${active ? '#fff' : 'var(--win-text)'};
        `;
        if (!disabled) btn.onclick = () => goToPage(targetPage);
        return btn;
    };

    bar.appendChild(makeBtn('◀', PAGE - 1, PAGE <= 1));

    const pages = new Set([1, TOTAL_PAGES, PAGE - 1, PAGE, PAGE + 1].filter(p => p >= 1 && p <= TOTAL_PAGES));
    let prev = null;
    for (const p of [...pages].sort((a, b) => a - b)) {
        if (prev && p - prev > 1) {
            const dots = document.createElement('span');
            dots.textContent = '…';
            dots.style.padding = '0 4px';
            bar.appendChild(dots);
        }
        bar.appendChild(makeBtn(String(p), p, false, p === PAGE));
        prev = p;
    }

    bar.appendChild(makeBtn('▶', PAGE + 1, PAGE >= TOTAL_PAGES));

    const info = document.createElement('span');
    info.style.cssText = 'margin-left:12px; color:var(--win-text-muted);';
    info.textContent = `전체 ${TOTAL.toLocaleString()}개`;
    bar.appendChild(info);

    document.getElementById('contentPane').appendChild(bar);
}

function goToPage(page) {
    const url = new URL(location.href);
    url.searchParams.set('page', page);
    location.href = url.toString();
}

// ── 미리보기 컴포넌트 (본문만 생성 → previewContent / colPreviewContent / galleryMainContent 어디든 재사용) ──
async function previewHtmlFor(path) {
    const item = items.find(i => i.path === path);
    if (!item) return '<div class="preview-empty">항목을 찾을 수 없습니다</div>';

    if (item.is_dir) {
        return `
            <div class="preview-header"><span class="preview-icon">📁</span>
            <span class="preview-name">${escapeHtml(item.name)}</span></div>
            <div class="preview-meta">
                <div class="preview-meta-row"><span>유형</span><span>파일 폴더</span></div>
                <div class="preview-meta-row"><span>항목</span><span>${item.child_count ?? ''}개</span></div>
            </div>
            <div class="preview-actions">
                <button class="pv-btn" onclick="window.__lx.openItem('${jsAttrPath(item.path)}', true)">열기</button>
                <a class="pv-btn primary" href="/explorer/download_dir?path=${encodeURIComponent(item.path)}">폴더 다운로드</a>
            </div>`;
    }

    const mediaUrl = rawUrl(path);
    const dlUrl = '/explorer/download_file?path=' + encodeURIComponent(path);

    let previewHtml = '';
    if (item.type === 'image') {
        previewHtml = `<div class="preview-content"><img src="${mediaUrl}" onerror="this.outerHTML='미리보기 불가'"></div>`;
    } else if (item.type === 'video') {
        if (isBrowserPlayable(item.ext)) {
            previewHtml = `<div class="preview-content"><video controls preload="metadata" style="max-width:100%;" src="${mediaUrl}"></video></div>`;
        } else {
            previewHtml = `<div class="preview-empty">${item.ext} 형식은 브라우저 재생 불가<br>다운로드 후 재생해주세요</div>`;
        }
    } else if (item.type === 'audio') {
        previewHtml = `<div class="preview-content" style="padding:24px;"><audio controls preload="metadata" style="width:100%;" src="${mediaUrl}"></audio></div>`;
    } else if (item.type === 'text') {
        try {
            const res = await fetch('/explorer/api/file_info?path=' + encodeURIComponent(path));
            const info = await res.json();
            if (info.content != null) {
                let display = info.content;
                if (item.ext === '.json') {
                    try { display = syntaxHL(JSON.stringify(JSON.parse(display), null, 2)); }
                    catch { display = escapeHtml(display); }
                } else {
                    display = escapeHtml(display);
                }
                previewHtml = `<div class="preview-content"><pre>${display}</pre></div>`;
            } else {
                previewHtml = `<div class="preview-empty">미리보기 불가</div>`;
            }
        } catch {
            previewHtml = `<div class="preview-empty">불러오기 실패</div>`;
        }
    } else if (item.type === 'document' && item.ext === '.pdf') {
        previewHtml = `<div class="preview-content"><iframe src="${mediaUrl}" style="width:100%;height:100%;border:none;flex:1;"></iframe></div>`;
    } else {
        previewHtml = `<div class="preview-empty">${item.ext || ''} 파일은 미리보기를 지원하지 않습니다</div>`;
    }

    return `
        <div class="preview-header">
            <span class="preview-icon">${fileIcon(item)}</span>
            <span class="preview-name">${escapeHtml(item.name)}</span>
        </div>
        ${previewHtml}
        <div class="preview-meta">
            <div class="preview-meta-row"><span>유형</span><span>${typeLabel(item)}</span></div>
            <div class="preview-meta-row"><span>크기</span><span>${item.size}</span></div>
            <div class="preview-meta-row"><span>수정</span><span>${fmtDate(item.mtime)}</span></div>
        </div>
        <div class="preview-actions">
            <button class="pv-btn" onclick="window.__lx.openFullscreen()">열기</button>
            <a class="pv-btn primary" href="${dlUrl}">다운로드</a>
        </div>`;
}

async function loadPreview(path) {
    const content = document.getElementById('previewContent');
    content.innerHTML = await previewHtmlFor(path);
}
async function loadPreviewInto(path, targetId) {
    const el = document.getElementById(targetId);
    if (!el) return;
    el.innerHTML = await previewHtmlFor(path);
}

// ── NAV TREE ──
async function buildNavTree() {
    const tree = document.getElementById('navTree');
    tree.innerHTML = '';
    const root = createTreeNode({ name: 'Root', path: ROOT, is_dir: true }, 0);
    tree.appendChild(root);

    const rel = CURRENT.startsWith(ROOT) ? CURRENT.slice(ROOT.length) : '';
    if (rel) {
        const parts = rel.split('/').filter(Boolean);
        let cur = ROOT;
        for (const p of parts) {
            cur += '/' + p;
            await expandTreeNode(cur);
        }
    } else {
        await expandTreeNode(ROOT);
    }
    highlightTreeNode(CURRENT);
}

function createTreeNode(item, depth) {
    const div = document.createElement('div');
    div.dataset.path = item.path;

    const row = document.createElement('div');
    row.className = 'tree-item';
    row.style.paddingLeft = (8 + depth * 16) + 'px';
    row.dataset.path = item.path;

    const toggle = document.createElement('span');
    toggle.className = 'tree-toggle';
    toggle.textContent = '▶';
    toggle.onclick = (e) => { e.stopPropagation(); toggleTreeExpand(item.path, div, depth); };

    const icon = document.createElement('span');
    icon.className = 'tree-icon';
    icon.textContent = '📁';

    const label = document.createElement('span');
    label.className = 'tree-label';
    label.textContent = item.name;

    row.appendChild(toggle);
    row.appendChild(icon);
    row.appendChild(label);
    row.onclick = () => { location.href = '/explorer/view?path=' + encodeURIComponent(item.path); };

    row.addEventListener('dragover', (e) => {
        if (!e.dataTransfer.types.includes('text/plain')) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = 'move';
        row.classList.add('drag-over');
    });
    row.addEventListener('dragleave', (e) => {
        if (!row.contains(e.relatedTarget)) row.classList.remove('drag-over');
    });
    row.addEventListener('drop', async (e) => {
        e.preventDefault();
        row.classList.remove('drag-over');
        const paths = JSON.parse(e.dataTransfer.getData('text/plain') || '[]');
        const filtered = paths.filter(p => p !== item.path && !item.path.startsWith(p + '/'));
        if (!filtered.length) return;
        for (const src of filtered) {
            const isCopy = e.ctrlKey || e.metaKey;
            try { await apiFetch(isCopy ? '/explorer/api/copy' : '/explorer/api/move', { src, dst_dir: item.path }); }
            catch (err) { showToast(`이동 실패: ${err.message}`, 'error'); return; }
        }
        location.reload();
    });

    const children = document.createElement('div');
    children.className = 'tree-children';
    children.dataset.parentPath = item.path;

    div.appendChild(row);
    div.appendChild(children);
    return div;
}

async function toggleTreeExpand(path, container, depth) {
    const children = container.querySelector('.tree-children');
    const toggle = container.querySelector('.tree-toggle');
    if (children.classList.contains('open')) {
        children.classList.remove('open');
        toggle.textContent = '▶';
        return;
    }
    await loadChildren(path, children, depth);
    children.classList.add('open');
    toggle.textContent = '▼';
}

async function expandTreeNode(path) {
    const container = document.querySelector(`[data-path="${CSS.escape(path)}"]`);
    if (!container) return;
    const children = container.querySelector('.tree-children');
    const toggle = container.querySelector('.tree-toggle');
    if (!children || children.classList.contains('open')) return;
    const depth = Math.round((parseInt(container.querySelector('.tree-item')?.style.paddingLeft) - 8) / 16);
    await loadChildren(path, children, depth);
    children.classList.add('open');
    if (toggle) toggle.textContent = '▼';
}

async function loadChildren(path, childrenEl, depth) {
    if (childrenEl.children.length > 0) return;
    try {
        const res = await fetch('/explorer/api/list?path=' + encodeURIComponent(path));
        const data = await res.json();
        const dirs = data.items.filter(i => i.is_dir);
        dirs.forEach(d => childrenEl.appendChild(createTreeNode(d, depth + 1)));
        if (dirs.length === 0) {
            const toggle = childrenEl.parentElement.querySelector('.tree-toggle');
            if (toggle) toggle.style.visibility = 'hidden';
        }
    } catch {}
}

function highlightTreeNode(path) {
    document.querySelectorAll('.tree-item').forEach(el => el.classList.remove('active'));
    const target = document.querySelector(`.tree-item[data-path="${CSS.escape(path)}"]`);
    if (target) { target.classList.add('active'); target.scrollIntoView({ block: 'nearest' }); }
}

// ── 즐겨찾기 ──
const FAV_KEY = 'exp_favorites';
function getFavorites() {
    try { return JSON.parse(localStorage.getItem(FAV_KEY) || '[]'); } catch { return []; }
}
function saveFavorites(list) { localStorage.setItem(FAV_KEY, JSON.stringify(list)); }
function isFavorite(path) { return getFavorites().some(f => f.path === path); }
function toggleFavorite(path, name) {
    let list = getFavorites();
    if (list.some(f => f.path === path)) list = list.filter(f => f.path !== path);
    else list.push({ path, name });
    saveFavorites(list);
    renderFavorites();
}
function ctxToggleFavorite() {
    hideCtx();
    if (!ctxTargetPath) return;
    const item = items.find(i => i.path === ctxTargetPath);
    const name = item ? item.name : ctxTargetPath.split('/').pop();
    toggleFavorite(ctxTargetPath, name);
}
function renderFavorites() {
    const section = document.getElementById('favSection');
    const el = document.getElementById('favList');
    const list = getFavorites();
    el.innerHTML = '';
    list.forEach(f => {
        const row = document.createElement('div');
        row.className = 'fav-item' + (f.path === CURRENT ? ' active' : '');
        row.dataset.path = f.path;
        row.innerHTML = `<span class="tree-icon">⭐</span><span class="fav-label" title="${escapeHtml(f.path)}">${escapeHtml(f.name)}</span><button class="fav-remove-btn" title="즐겨찾기 제거">✕</button>`;
        row.addEventListener('click', (e) => {
            if (e.target.closest('.fav-remove-btn')) return;
            location.href = '/explorer/view?path=' + encodeURIComponent(f.path);
        });
        row.querySelector('.fav-remove-btn').addEventListener('click', (e) => {
            e.stopPropagation();
            toggleFavorite(f.path, f.name);
        });
        row.addEventListener('dragover', (e) => {
            if (!e.dataTransfer.types.includes('text/plain')) return;
            e.preventDefault(); row.classList.add('drag-over');
        });
        row.addEventListener('dragleave', (e) => { if (!row.contains(e.relatedTarget)) row.classList.remove('drag-over'); });
        row.addEventListener('drop', async (e) => {
            e.preventDefault(); row.classList.remove('drag-over');
            const paths = JSON.parse(e.dataTransfer.getData('text/plain') || '[]');
            const filtered = paths.filter(p => p !== f.path && !f.path.startsWith(p + '/'));
            if (!filtered.length) return;
            const isCopy = e.ctrlKey || e.metaKey;
            for (const src of filtered) {
                try { await apiFetch(isCopy ? '/explorer/api/copy' : '/explorer/api/move', { src, dst_dir: f.path }); }
                catch (err) { showToast(`이동 실패: ${err.message}`, 'error'); return; }
            }
            location.reload();
        });
        el.appendChild(row);
    });
    section.style.display = list.length ? '' : 'none';
}

// ── FULLSCREEN ──
let fsItems = [];
let fsIndex = 0;
let fsAutoInterval = null;

function openFullscreen() {
    if (!selectedPath) return;
    const item = items.find(i => i.path === selectedPath);
    if (!item || item.is_dir) return;

    fsItems = items.filter(i => !i.is_dir);
    fsIndex = fsItems.findIndex(i => i.path === selectedPath);
    if (fsIndex < 0) fsIndex = 0;

    renderFullscreen();
    document.getElementById('fsModal').classList.add('open');
    document.body.style.overflow = 'hidden';

    const nav = document.getElementById('fsNav');
    nav.style.display = fsItems.length > 1 ? 'flex' : 'none';
}

function renderFullscreen() {
    const item = fsItems[fsIndex];
    const body = document.getElementById('fsBody');
    const url = rawUrl(item.path);

    if (item.type === 'image') {
        body.innerHTML = `<img src="${url}">`;
    } else if (item.type === 'video') {
        if (isBrowserPlayable(item.ext)) {
            body.innerHTML = `<video controls autoplay preload="metadata" style="max-width:95vw;max-height:85vh;" src="${url}"></video>`;
        } else {
            body.innerHTML = `<div style="color:#fff;text-align:center;padding:40px;">
                <p style="font-size:1.1rem;margin-bottom:8px;">🎬 ${escapeHtml(item.name)}</p>
                <p style="color:#999;margin-bottom:20px;">${item.ext} 형식은 브라우저에서 재생할 수 없습니다.<br>다운로드 후 VLC 등으로 재생해주세요.</p>
                <a href="/explorer/download_file?path=${encodeURIComponent(item.path)}"
                   style="color:#fff;background:var(--win-accent);padding:10px 24px;border-radius:6px;text-decoration:none;">다운로드</a>
            </div>`;
        }
    } else if (item.type === 'audio') {
        body.innerHTML = `<audio controls autoplay preload="metadata" style="min-width:300px;" src="${url}"></audio>`;
    } else if (item.type === 'document' && item.ext === '.pdf') {
        body.innerHTML = `<iframe src="${url}" style="width:90vw;height:85vh;border:none;border-radius:8px;"></iframe>`;
    } else if (item.type === 'text') {
        fetch(url).then(r => r.text()).then(text => {
            let display;
            if (item.ext === '.json') {
                try { display = syntaxHL(JSON.stringify(JSON.parse(text), null, 2)); }
                catch { display = escapeHtml(text); }
            } else { display = escapeHtml(text); }
            body.innerHTML = `<pre>${display}</pre>`;
        }).catch(() => { body.innerHTML = '<pre>불러오기 실패</pre>'; });
    } else {
        body.innerHTML = `<div style="color:#fff;text-align:center;padding:40px;">
            <p style="margin-bottom:16px;">미리보기를 지원하지 않는 파일입니다</p>
            <a href="/explorer/download_file?path=${encodeURIComponent(item.path)}"
               style="color:#fff;background:var(--win-accent);padding:10px 24px;border-radius:6px;text-decoration:none;">다운로드</a>
        </div>`;
    }

    document.getElementById('fsIndex').textContent = `${fsIndex + 1} / ${fsItems.length}`;
    document.getElementById('fsInfo').textContent = item.name;
}

function closeFullscreen() {
    document.getElementById('fsModal').classList.remove('open');
    document.body.style.overflow = '';
    fsStopAuto();
}
function fsNext() { fsIndex = (fsIndex + 1) % fsItems.length; renderFullscreen(); }
function fsPrev() { fsIndex = (fsIndex - 1 + fsItems.length) % fsItems.length; renderFullscreen(); }
function fsToggleAuto() {
    const btn = document.getElementById('fsAutoBtn');
    if (fsAutoInterval) { fsStopAuto(); }
    else { fsAutoInterval = setInterval(fsNext, 300); btn.textContent = '⏸ 정지'; }
}
function fsStopAuto() {
    clearInterval(fsAutoInterval); fsAutoInterval = null;
    const btn = document.getElementById('fsAutoBtn');
    if (btn) btn.textContent = '▶ 자동';
}

// ── NAVIGATE ──
function navigateUp() {
    if (PARENT) location.href = '/explorer/view?path=' + encodeURIComponent(PARENT);
}

// ── STATUS ──
function updateStatus() {
    const dirs = items.filter(i => i.is_dir).length;
    const files = items.filter(i => !i.is_dir).length;
    const parts = [];
    if (dirs) parts.push(dirs + '개 폴더');
    if (files) parts.push(files + '개 파일');
    document.getElementById('statusCount').textContent = parts.join(', ') || '비어 있음';
}

// ── RESIZER ──
function initResizer(resizerId, targetId, side) {
    const resizer = document.getElementById(resizerId);
    const target = document.getElementById(targetId);
    if (!resizer || !target) return;
    let startX, startW;
    resizer.addEventListener('mousedown', (e) => {
        startX = e.clientX;
        startW = target.offsetWidth;
        document.addEventListener('mousemove', onMove);
        document.addEventListener('mouseup', onUp);
        e.preventDefault();
    });
    function onMove(e) {
        const diff = side === 'left' ? e.clientX - startX : startX - e.clientX;
        target.style.width = Math.max(150, startW + diff) + 'px';
    }
    function onUp() {
        document.removeEventListener('mousemove', onMove);
        document.removeEventListener('mouseup', onUp);
    }
}

// ── 컨텍스트 메뉴 (항목 위 / 빈 공간) ──
function positionMenu(menu, e) {
    menu.style.visibility = 'hidden';
    menu.style.display = 'block';
    menu.classList.add('open');
    const mw = menu.offsetWidth, mh = menu.offsetHeight;
    const vw = window.innerWidth, vh = window.innerHeight;
    let x = e.clientX, y = e.clientY;
    if (x + mw > vw) x = vw - mw - 4;
    if (y + mh > vh) y = y - mh;
    if (y < 0) y = 4;
    menu.style.left = x + 'px';
    menu.style.top = y + 'px';
    menu.style.visibility = 'visible';
}

function hideCtx() {
    const menu = document.getElementById('ctxMenu');
    menu.classList.remove('open');
    menu.style.display = '';
    menu.style.visibility = '';
}
function hideCtxEmpty() {
    const menu = document.getElementById('ctxMenuEmpty');
    if (!menu) return;
    menu.classList.remove('open');
    menu.style.display = '';
    menu.style.visibility = '';
}

function showItemContextMenu(e, row) {
    const path = row.dataset?.path;
    if (!path) return;
    ctxTargetPath = path;
    const item = items.find(i => i.path === path);

    const isDirCtx = item ? item.is_dir : row.classList.contains('tree-item') || row.classList.contains('fav-item');
    document.getElementById('ctxDownloadItem').style.display = (!item || isDirCtx) ? 'none' : '';
    document.getElementById('ctxDownloadDirItem').style.display = (item && item.is_dir) ? '' : 'none';

    const fileOnlyIds = ['ctxExtractItem'];
    fileOnlyIds.forEach(id => {
        const el = document.getElementById(id);
        if (el) el.style.display = item ? '' : 'none';
    });

    const favEl = document.getElementById('ctxFavItem');
    if (favEl) favEl.textContent = isFavorite(path) ? '⭐ 즐겨찾기 제거' : '☆ 즐겨찾기 추가';

    const pasteEl = document.getElementById('ctxPasteItem');
    if (pasteEl) pasteEl.classList.toggle('disabled', !clipboard);

    positionMenu(document.getElementById('ctxMenu'), e);
}

function showEmptyContextMenu(e) {
    const pasteEl = document.getElementById('ctxEmptyPasteItem');
    if (pasteEl) pasteEl.classList.toggle('disabled', !clipboard);
    positionMenu(document.getElementById('ctxMenuEmpty'), e);
}

document.addEventListener('contextmenu', (e) => {
    if (document.getElementById('trashView').style.display === 'flex') return;
    const row = e.target.closest('.file-row, .grid-item, .icon-item, .col-row, .tree-item, .fav-item');
    if (row) {
        e.preventDefault();
        hideCtxEmpty();
        showItemContextMenu(e, row);
        return;
    }
    const emptyArea = e.target.closest('#fileListDetails, #fileListTiles, #fileListIcons, #fileListColumn, #fileListGallery, .gallery-main');
    if (emptyArea) {
        e.preventDefault();
        hideCtx();
        showEmptyContextMenu(e);
        return;
    }
    hideCtx();
    hideCtxEmpty();
});

document.addEventListener('mousedown', (e) => {
    if (e.button === 2) return;
    if (!e.target.closest('#ctxMenu')) hideCtx();
    if (!e.target.closest('#ctxMenuEmpty')) hideCtxEmpty();
});

function ctxOpen() {
    hideCtx();
    if (!ctxTargetPath) return;
    const item = items.find(i => i.path === ctxTargetPath);
    if (item) openItem(item.path, item.is_dir);
    else location.href = '/explorer/view?path=' + encodeURIComponent(ctxTargetPath);
}

function ctxDownload() {
    hideCtx();
    if (!ctxTargetPath) return;
    window.location.href = '/explorer/download_file?path=' + encodeURIComponent(ctxTargetPath);
}

function ctxDownloadDir() {
    hideCtx();
    if (!ctxTargetPath) return;
    window.location.href = '/explorer/download_dir?path=' + encodeURIComponent(ctxTargetPath);
}

function ctxProperties() {
    hideCtx();
    if (!ctxTargetPath) return;
    showProperties(ctxTargetPath);
}

function ctxCopyPath() {
    hideCtx();
    if (!ctxTargetPath) return;
    navigator.clipboard.writeText(ctxTargetPath).then(() => {
        showToast('📋 경로 복사됨: ' + ctxTargetPath);
    }).catch(() => {
        showToast(ctxTargetPath, 'info', 6000);
    });
}

// ── PROPERTIES MODAL ──
async function showProperties(path) {
    const item = items.find(i => i.path === path);
    const overlay = document.getElementById('propOverlay');
    const iconEl = document.getElementById('propIcon');
    const nameEl = document.getElementById('propName');
    const body = document.getElementById('propBody');

    const isDir = item ? item.is_dir : true;
    const displayName = item ? item.name : path.split('/').pop();
    const displayIcon = item ? fileIcon(item) : '📁';

    iconEl.textContent = displayIcon;
    nameEl.textContent = displayName;

    let rows = '';
    rows += propRow('위치', escapeHtml(path.substring(0, path.lastIndexOf('/'))));

    if (isDir) {
        rows += propRow('유형', '파일 폴더');
        if (item && item.mtime) rows += propRow('수정 날짜', fmtDate(item.mtime));
        rows += propRow('크기', '<span class="prop-size-loading" id="propSizeVal">⏳ 계산 중...</span>');
        if (item && item.child_count != null) rows += propRow('포함 항목', item.child_count + '개');
    } else {
        rows += propRow('유형', typeLabel(item));
        rows += propRow('크기', item.size + ' (' + item.size_raw.toLocaleString() + ' bytes)');
        rows += propRow('수정 날짜', fmtDate(item.mtime));
        if (item.ext) rows += propRow('확장자', item.ext);
    }

    body.innerHTML = rows;
    overlay.classList.add('open');

    if (isDir) {
        try {
            const res = await fetch('/explorer/api/dir_size?path=' + encodeURIComponent(path));
            const data = await res.json();
            const el = document.getElementById('propSizeVal');
            if (el) el.outerHTML = data.size + ' (' + data.size_raw.toLocaleString() + ' bytes)';
        } catch {
            const el = document.getElementById('propSizeVal');
            if (el) el.outerHTML = '계산 실패';
        }
    }
}

function propRow(label, value) {
    return `<div class="prop-row"><span class="prop-label">${label}</span><span class="prop-value">${value}</span></div>`;
}

function closeProp() {
    document.getElementById('propOverlay').classList.remove('open');
}

// ── 클립보드 ──
function getSelectedPaths() {
    return selectedPaths.size > 0 ? [...selectedPaths] : (ctxTargetPath ? [ctxTargetPath] : []);
}

function ctxCut() {
    hideCtx(); hideCtxEmpty();
    const paths = getSelectedPaths();
    if (!paths.length) return;
    clipboard = { action: 'cut', paths };
    showStatus('✂️ 잘라내기: ' + clipboard.paths.length + '개');
}
function ctxCopyItem() {
    hideCtx(); hideCtxEmpty();
    const paths = getSelectedPaths();
    if (!paths.length) return;
    clipboard = { action: 'copy', paths };
    showStatus('📋 복사: ' + clipboard.paths.length + '개');
}
async function ctxPaste() {
    hideCtx(); hideCtxEmpty();
    if (!clipboard) return;
    try {
        for (const src of clipboard.paths) {
            const endpoint = clipboard.action === 'cut' ? '/explorer/api/move' : '/explorer/api/copy';
            await apiFetch(endpoint, { src, dst_dir: CURRENT });
        }
    } catch (err) {
        showToast('붙여넣기 실패: ' + err.message, 'error');
        return;
    }
    if (clipboard.action === 'cut') clipboard = null;
    showToast('붙여넣기 완료', 'success');
    location.reload();
}

// ── 삭제 (휴지통으로 이동) ──
async function doDeleteSelected(paths) {
    const ok = await openConfirm({
        title: '휴지통으로 이동',
        message: `${paths.length}개 항목을 휴지통으로 이동하시겠습니까?`,
        okLabel: '이동',
    });
    if (!ok) return;
    for (const path of paths) {
        try { await apiFetch('/explorer/api/delete', { path }, 'DELETE'); }
        catch (err) { showToast('삭제 실패: ' + err.message, 'error'); return; }
    }
    showToast(`${paths.length}개 항목을 휴지통으로 이동했습니다`, 'success');
    location.reload();
}
async function ctxDelete() {
    hideCtx(); hideCtxEmpty();
    const paths = getSelectedPaths();
    if (!paths.length) return;
    await doDeleteSelected(paths);
}

// ── 이름 변경 ──
async function ctxRename() {
    hideCtx();
    if (!ctxTargetPath) return;
    const item = items.find(i => i.path === ctxTargetPath);
    if (!item) return;
    const newName = await openPrompt({ title: '이름 바꾸기', label: '새 이름', value: item.name, okLabel: '변경' });
    if (!newName || newName === item.name) return;
    try {
        await apiFetch('/explorer/api/rename', { path: item.path, new_name: newName });
        showToast('이름을 변경했습니다', 'success');
        location.reload();
    } catch (e) { showToast('오류: ' + e.message, 'error'); }
}
async function shortcutRename() {
    const paths = getSelectedPaths();
    if (paths.length !== 1) return;
    ctxTargetPath = paths[0];
    await ctxRename();
}

// ── 복제 ──
async function ctxDuplicate() {
    hideCtx(); hideCtxEmpty();
    const paths = getSelectedPaths();
    if (!paths.length) return;
    try {
        for (const p of paths) await apiFetch('/explorer/api/duplicate', { path: p });
        showToast('복제 완료', 'success');
        location.reload();
    } catch (err) { showToast('복제 실패: ' + err.message, 'error'); }
}

// ── 새 폴더 / 새 파일 ──
async function showMkdirDialog() {
    hideCtxEmpty();
    const name = await openPrompt({ title: '새 폴더', label: '폴더 이름', okLabel: '만들기' });
    if (!name) return;
    try {
        await apiFetch('/explorer/api/mkdir', { parent: CURRENT, name });
        showToast('폴더를 만들었습니다', 'success');
        location.reload();
    } catch (e) { showToast('오류: ' + e.message, 'error'); }
}
async function showNewFileDialog() {
    hideCtxEmpty();
    const name = await openPrompt({ title: '새 파일', label: '파일 이름', okLabel: '만들기' });
    if (!name) return;
    try {
        await apiFetch('/explorer/api/newfile', { parent: CURRENT, name });
        showToast('파일을 만들었습니다', 'success');
        location.reload();
    } catch (e) { showToast('오류: ' + e.message, 'error'); }
}

// ── 업로드 ──
async function handleUpload(input) {
    const files = input.files;
    if (!files.length) return;
    await uploadFiles(files);
    input.value = '';
}
async function uploadFiles(files) {
    const form = new FormData();
    for (const f of files) form.append('files', f);
    showStatus('⬆ 업로드 중...');
    try {
        const res = await fetch(`/explorer/api/upload?path=${encodeURIComponent(CURRENT)}`, { method: 'POST', body: form });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.detail || '업로드 실패');
        }
        const data = await res.json();
        const renamedCount = data.renamed ? Object.keys(data.renamed).length : 0;
        if (renamedCount) {
            const names = Object.entries(data.renamed).map(([a, b]) => `${a} → ${b}`).join(', ');
            showToast('동일한 이름이 있어 자동으로 이름을 바꿔 저장했습니다: ' + names, 'info', 5000);
        } else {
            showToast(`${data.saved.length}개 파일 업로드 완료`, 'success');
        }
        setTimeout(() => location.reload(), renamedCount ? 1200 : 300);
    } catch (err) {
        showToast('업로드 실패: ' + err.message, 'error');
    }
}

// ── 압축 / 해제 ──
async function ctxExtract() {
    hideCtx();
    if (!ctxTargetPath) return;
    try {
        await apiFetch('/explorer/api/extract', null, 'POST', `?path=${encodeURIComponent(ctxTargetPath)}`);
        showToast('압축을 해제했습니다', 'success');
        location.reload();
    } catch (e) { showToast('압축 해제 실패: ' + e.message, 'error'); }
}
async function ctxCompress() {
    hideCtx(); hideCtxEmpty();
    const paths = getSelectedPaths();
    if (!paths.length) return;
    const name = await openPrompt({ title: '압축', label: '압축 파일 이름 (.zip 자동)', okLabel: '압축' });
    if (!name) return;
    try {
        await apiFetch('/explorer/api/compress', { paths, output_name: name, output_dir: CURRENT });
        showToast('압축을 완료했습니다', 'success');
        location.reload();
    } catch (e) { showToast('압축 실패: ' + e.message, 'error'); }
}

// ── 숨김 파일 토글 ──
function toggleHidden() {
    hideCtxEmpty();
    const url = new URL(location.href);
    const cur = url.searchParams.get('show_hidden') === '1';
    url.searchParams.set('show_hidden', cur ? '0' : '1');
    location.href = url.toString();
}

// ── 주소창 직접 입력 ──
function initAddressBarEdit() {
    const bar = document.getElementById('breadcrumbs');
    const input = document.getElementById('bcEditInput');
    if (!bar || !input) return;
    bar.addEventListener('click', (e) => {
        if (e.target.closest('a.bc-item')) return;
        bar.classList.add('editing');
        input.value = CURRENT;
        input.focus(); input.select();
    });
    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            const p = input.value.trim();
            if (p) location.href = '/explorer/view?path=' + encodeURIComponent(p);
        } else if (e.key === 'Escape') {
            bar.classList.remove('editing');
        }
    });
    input.addEventListener('blur', () => bar.classList.remove('editing'));
}

// ── 검색창: 현재 폴더만 검색 토글 ──
function initSearchScope() {
    const form = document.querySelector('.search-form');
    const scopeChk = document.getElementById('searchScopeChk');
    if (!form || !scopeChk) return;
    const pathInput = document.getElementById('searchPathInput');
    scopeChk.checked = !!SEARCH_ROOT && SEARCH_ROOT !== ROOT;
    form.addEventListener('submit', () => {
        pathInput.value = scopeChk.checked ? CURRENT : '';
        if (!pathInput.value) pathInput.disabled = true;
    });
}

// ── 확대/축소 ──
const ZOOM_KEY = 'exp_zoom';
function applyZoom(scale) {
    document.documentElement.style.setProperty('--tile-size', Math.round(180 * scale) + 'px');
    document.documentElement.style.setProperty('--icon-size', Math.round(90 * scale) + 'px');
    localStorage.setItem(ZOOM_KEY, String(scale));
}
function initZoom() {
    const val = parseFloat(localStorage.getItem(ZOOM_KEY) || '1');
    applyZoom(val);
    const slider = document.getElementById('zoomSlider');
    if (slider) {
        slider.value = val;
        slider.addEventListener('input', () => applyZoom(parseFloat(slider.value)));
    }
}

// ── 테마 ──
const THEME_KEY = 'exp_theme';
const MODE_KEY = 'exp_mode';
const THEMES = [
    { id: 'classic', name: '기본', swatchLight: '#0078d4', swatchDark: '#4da3ff' },
    { id: 'glass', name: '글래스모피즘', swatchLight: '#6c63ff', swatchDark: '#8b7bff' },
    { id: 'neu', name: '뉴모피즘', swatchLight: '#5b7fff', swatchDark: '#7c9eff' },
    { id: 'mesh', name: '그라디언트 메시', swatchLight: '#ff6b6b', swatchDark: '#ff7b7b' },
];
function currentTheme() { return localStorage.getItem(THEME_KEY) || 'classic'; }
function currentMode() {
    let m = localStorage.getItem(MODE_KEY);
    if (!m) m = (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) ? 'dark' : 'light';
    return m;
}
function applyTheme(theme, mode) {
    document.documentElement.setAttribute('data-theme', `${theme}-${mode}`);
    localStorage.setItem(THEME_KEY, theme);
    localStorage.setItem(MODE_KEY, mode);
}
function closeThemeMenu() { document.getElementById('themeMenu')?.classList.remove('open'); }
function renderThemeMenu() {
    const menu = document.getElementById('themeMenu');
    if (!menu) return;
    const theme = currentTheme(), mode = currentMode();
    menu.innerHTML = `<div class="theme-menu-label">테마</div>`;
    THEMES.forEach(t => {
        const div = document.createElement('div');
        div.className = 'theme-option' + (t.id === theme ? ' active' : '');
        div.innerHTML = `<span class="theme-swatch" style="background:${mode === 'dark' ? t.swatchDark : t.swatchLight}"></span><span>${escapeHtml(t.name)}</span>`;
        div.addEventListener('click', () => { applyTheme(t.id, mode); renderThemeMenu(); });
        menu.appendChild(div);
    });
    const modeRow = document.createElement('div');
    modeRow.className = 'theme-mode-row';
    modeRow.innerHTML = `
        <button class="theme-mode-btn ${mode === 'light' ? 'active' : ''}" data-mode="light">☀️ 라이트</button>
        <button class="theme-mode-btn ${mode === 'dark' ? 'active' : ''}" data-mode="dark">🌙 다크</button>
    `;
    modeRow.querySelectorAll('button').forEach(b => {
        b.addEventListener('click', () => { applyTheme(theme, b.dataset.mode); renderThemeMenu(); });
    });
    menu.appendChild(modeRow);
}
function initThemeMenu() {
    const btn = document.getElementById('tbTheme');
    const menu = document.getElementById('themeMenu');
    if (!btn || !menu) return;
    renderThemeMenu();
    btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const wasOpen = menu.classList.contains('open');
        closeThemeMenu();
        if (!wasOpen) {
            const r = btn.getBoundingClientRect();
            menu.style.left = Math.min(r.left, window.innerWidth - 240) + 'px';
            menu.style.top = (r.bottom + 4) + 'px';
            menu.classList.add('open');
        }
    });
    document.addEventListener('mousedown', (e) => {
        if (!e.target.closest('#themeMenu') && !e.target.closest('#tbTheme')) closeThemeMenu();
    });
}

// ── 휴지통 ──
async function enterTrashMode() {
    ['colHeaders', 'fileListDetails', 'fileListTiles', 'fileListIcons', 'fileListColumn', 'fileListGallery', 'zoomControl', 'paginationBar'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.style.display = 'none';
    });
    document.getElementById('trashView').style.display = 'flex';
    document.querySelectorAll('.tree-item.active, .fav-item.active').forEach(el => el.classList.remove('active'));
    document.getElementById('trashNavEntry')?.classList.add('active');
    await loadTrashList();
}
function exitTrashMode() {
    document.getElementById('trashView').style.display = 'none';
    document.getElementById('trashNavEntry')?.classList.remove('active');
    setView(currentView);
    highlightTreeNode(CURRENT);
}
async function loadTrashList() {
    const listEl = document.getElementById('trashListBody');
    listEl.innerHTML = '불러오는 중...';
    try {
        const res = await fetch('/explorer/api/trash/list');
        const data = await res.json();
        if (!data.items.length) {
            listEl.innerHTML = '<div class="trash-empty-msg">🗑️ 휴지통이 비어 있습니다</div>';
            return;
        }
        listEl.innerHTML = '';
        data.items.forEach(it => {
            const row = document.createElement('div');
            row.className = 'trash-row';
            row.innerHTML = `
                <span class="file-icon">${it.is_dir ? '📁' : '📃'}</span>
                <div class="trash-info">
                    <div class="trash-name" title="${escapeHtml(it.original_path)}">${escapeHtml(it.name)}</div>
                    <div class="trash-meta">${escapeHtml(it.original_path)} · ${escapeHtml(it.size)} · ${fmtDate(it.deleted_at)} 삭제</div>
                </div>
                <div class="trash-actions">
                    <button data-act="restore">복구</button>
                    <button data-act="delete">영구삭제</button>
                </div>`;
            row.querySelector('[data-act="restore"]').addEventListener('click', () => restoreTrashItem(it.id, it.name));
            row.querySelector('[data-act="delete"]').addEventListener('click', () => deleteTrashItemForever(it.id, it.name));
            listEl.appendChild(row);
        });
    } catch {
        listEl.innerHTML = '<div class="trash-empty-msg">불러오기 실패</div>';
    }
}
async function restoreTrashItem(id, name) {
    try {
        await apiFetch('/explorer/api/trash/restore', { id });
        showToast(`'${name}' 복구 완료`, 'success');
        loadTrashList();
    } catch (e) { showToast('복구 실패: ' + e.message, 'error'); }
}
async function deleteTrashItemForever(id, name) {
    const ok = await openConfirm({ title: '영구 삭제', message: `'${name}'을(를) 영구적으로 삭제하시겠습니까? 되돌릴 수 없습니다.`, okLabel: '영구 삭제', danger: true });
    if (!ok) return;
    try {
        await apiFetch('/explorer/api/trash/' + encodeURIComponent(id), null, 'DELETE');
        showToast('영구 삭제 완료', 'success');
        loadTrashList();
    } catch (e) { showToast('삭제 실패: ' + e.message, 'error'); }
}
async function emptyTrash() {
    const ok = await openConfirm({ title: '휴지통 비우기', message: '휴지통의 모든 항목을 영구적으로 삭제하시겠습니까? 되돌릴 수 없습니다.', okLabel: '비우기', danger: true });
    if (!ok) return;
    try {
        await apiFetch('/explorer/api/trash/empty', null, 'DELETE');
        showToast('휴지통을 비웠습니다', 'success');
        loadTrashList();
    } catch (e) { showToast('실패: ' + e.message, 'error'); }
}

// ── 계층(컬럼) 뷰 ──
let columns = [];

async function fetchDirItems(path) {
    try {
        const res = await fetch('/explorer/api/list?path=' + encodeURIComponent(path) + '&page_size=2000');
        const data = await res.json();
        const showHidden = new URL(location.href).searchParams.get('show_hidden') === '1';
        return showHidden ? data.items : data.items.filter(i => !i.name.startsWith('.'));
    } catch { return []; }
}

async function initColumnView() {
    columns = [];
    const rel = CURRENT.startsWith(ROOT) ? CURRENT.slice(ROOT.length).replace(/^\/+/, '') : '';
    const parts = rel ? rel.split('/').filter(Boolean) : [];

    const rootItems = await fetchDirItems(ROOT);
    columns.push({ path: ROOT, items: rootItems, selected: parts.length ? joinPath(ROOT, parts[0]) : null });

    let curPath = ROOT;
    for (let i = 0; i < parts.length; i++) {
        curPath = joinPath(curPath, parts[i]);
        const isLast = i === parts.length - 1;
        const colItems = isLast ? items : await fetchDirItems(curPath);
        columns.push({ path: curPath, items: colItems, selected: (i + 1 < parts.length) ? joinPath(curPath, parts[i + 1]) : null });
    }
    renderColumnView();
}

function sortColItems(list) {
    return [...list].sort((a, b) => {
        if (a.is_dir !== b.is_dir) return a.is_dir ? -1 : 1;
        return a.name.toLowerCase() < b.name.toLowerCase() ? -1 : 1;
    });
}

function renderColumnView() {
    const el = document.getElementById('fileListColumn');
    el.innerHTML = '';
    columns.forEach((col, colIndex) => {
        const pane = document.createElement('div');
        pane.className = 'col-pane';
        sortColItems(col.items).forEach(item => {
            const row = document.createElement('div');
            row.className = 'col-row' + (col.selected === item.path ? ' selected' : '');
            row.dataset.path = item.path;
            row.innerHTML = `<span class="file-icon">${fileIcon(item)}</span><span class="col-row-name" title="${escapeHtml(item.name)}">${escapeHtml(item.name)}</span>${item.is_dir ? '<span class="col-row-chevron">▶</span>' : ''}`;
            row.addEventListener('click', (e) => { e.stopPropagation(); columnRowClick(colIndex, item); });
            row.addEventListener('dblclick', () => { if (!item.is_dir) openFullscreen(); });
            addDragHandlers(row, item);
            pane.appendChild(row);
        });
        el.appendChild(pane);
    });

    const previewPane = document.createElement('div');
    previewPane.className = 'col-pane-preview';
    previewPane.id = 'colPreviewContent';
    previewPane.innerHTML = '<div class="preview-empty">항목을 선택하면 미리보기가 표시됩니다</div>';
    el.appendChild(previewPane);

    if (selectedPath) loadPreviewInto(selectedPath, 'colPreviewContent');
    requestAnimationFrame(() => { el.scrollLeft = el.scrollWidth; });
}

async function columnRowClick(colIndex, item) {
    columns[colIndex].selected = item.path;
    columns.length = colIndex + 1;
    selectedPath = item.path;
    selectedPaths = new Set([item.path]);
    updateToolbarForSelection();
    if (item.is_dir) {
        const childItems = await fetchDirItems(item.path);
        columns.push({ path: item.path, items: childItems, selected: null });
    }
    renderColumnView();
    document.getElementById('statusSelected').textContent = '선택: ' + item.name;
}

// ── 갤러리 뷰 ──
function renderGalleryFilmstrip(sorted) {
    const filmstrip = document.getElementById('galleryFilmstrip');
    if (!filmstrip) return;
    filmstrip.innerHTML = '';
    sorted.forEach(item => {
        const thumb = document.createElement('div');
        thumb.className = 'gallery-thumb' + (item.path === selectedPath ? ' selected' : '');
        thumb.title = item.name;
        if (item.type === 'image') {
            thumb.innerHTML = `<img src="${rawUrl(item.path)}" loading="lazy" onerror="this.parentElement.textContent='${fileIcon(item)}'">`;
        } else {
            thumb.textContent = fileIcon(item);
        }
        thumb.addEventListener('click', () => {
            selectedPath = item.path;
            selectedPaths = new Set([item.path]);
            updateToolbarForSelection();
            renderGalleryFilmstrip(sorted);
            renderGalleryMain();
        });
        filmstrip.appendChild(thumb);
    });
}
async function renderGalleryMain() {
    const main = document.getElementById('galleryMainContent');
    if (!main) return;
    let path = selectedPath;
    if (!path) {
        const first = sortedItems()[0];
        if (!first) { main.innerHTML = '<div class="gallery-placeholder">📂<div class="gp-name">항목이 없습니다</div></div>'; return; }
        path = first.path;
        selectedPath = path;
        selectedPaths = new Set([path]);
    }
    main.innerHTML = await previewHtmlFor(path);
}

// ── 선택 드래그(러버밴드) ──
let selectBoxState = null;
function initRubberBandSelect() {
    ['fileListDetails', 'fileListTiles', 'fileListIcons'].forEach(id => {
        const container = document.getElementById(id);
        if (!container) return;
        container.addEventListener('mousedown', (e) => {
            if (e.button !== 0) return;
            if (e.target.closest('.file-row,.grid-item,.icon-item')) return;
            selectBoxState = { startX: e.clientX, startY: e.clientY, additive: e.ctrlKey || e.metaKey, container };
            const box = document.createElement('div');
            box.className = 'select-box';
            box.id = 'liveSelectBox';
            document.body.appendChild(box);
        });
    });
    document.addEventListener('mousemove', (e) => {
        if (!selectBoxState) return;
        const box = document.getElementById('liveSelectBox');
        if (!box) return;
        const x1 = Math.min(selectBoxState.startX, e.clientX), x2 = Math.max(selectBoxState.startX, e.clientX);
        const y1 = Math.min(selectBoxState.startY, e.clientY), y2 = Math.max(selectBoxState.startY, e.clientY);
        box.style.left = x1 + 'px'; box.style.top = y1 + 'px';
        box.style.width = (x2 - x1) + 'px'; box.style.height = (y2 - y1) + 'px';
    });
    document.addEventListener('mouseup', () => {
        if (!selectBoxState) return;
        const box = document.getElementById('liveSelectBox');
        if (box) {
            const rect = box.getBoundingClientRect();
            if (rect.width > 3 || rect.height > 3) {
                const els = selectBoxState.container.querySelectorAll('.file-row,.grid-item,.icon-item');
                const hit = new Set(selectBoxState.additive ? selectedPaths : []);
                els.forEach(el => {
                    const r = el.getBoundingClientRect();
                    const overlap = !(r.right < rect.left || r.left > rect.right || r.bottom < rect.top || r.top > rect.bottom);
                    if (overlap) hit.add(el.dataset.path);
                });
                selectedPaths = hit;
                selectedPath = hit.size ? [...hit][hit.size - 1] : null;
                renderFiles();
                updateToolbarForSelection();
                document.getElementById('statusSelected').textContent = selectedPaths.size ? `선택: ${selectedPaths.size}개 ${selectedSizeLabel()}` : '';
            }
            box.remove();
        }
        selectBoxState = null;
    });
}

// ── DRAG & DROP (내부 이동 + OS 파일 업로드) ──
function addDragHandlers(el, item) {
    el.draggable = true;

    el.addEventListener('dragstart', (e) => {
        dragPaths = selectedPaths.size > 0 && selectedPaths.has(item.path) ? [...selectedPaths] : [item.path];
        e.dataTransfer.effectAllowed = 'move';
        e.dataTransfer.setData('text/plain', JSON.stringify(dragPaths));
        setTimeout(() => el.classList.add('dragging'), 0);
    });
    el.addEventListener('dragend', () => {
        el.classList.remove('dragging');
        document.querySelectorAll('.drag-over').forEach(x => x.classList.remove('drag-over'));
    });

    if (!item.is_dir) return;

    el.addEventListener('dragover', (e) => {
        if (!e.dataTransfer.types.includes('text/plain')) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = 'move';
        el.classList.add('drag-over');
    });
    el.addEventListener('dragleave', (e) => {
        if (!el.contains(e.relatedTarget)) el.classList.remove('drag-over');
    });
    el.addEventListener('drop', async (e) => {
        if (!e.dataTransfer.types.includes('text/plain')) return;
        e.preventDefault();
        el.classList.remove('drag-over');
        const paths = JSON.parse(e.dataTransfer.getData('text/plain') || '[]');
        if (!paths.length) return;
        const filtered = paths.filter(p => p !== item.path && !item.path.startsWith(p + '/'));
        if (!filtered.length) return;
        let hasError = false;
        for (const src of filtered) {
            try {
                const isCopy = e.ctrlKey || e.metaKey;
                await apiFetch(isCopy ? '/explorer/api/copy' : '/explorer/api/move', { src, dst_dir: item.path });
            } catch (err) {
                showToast(`이동 실패: ${err.message}`, 'error');
                hasError = true;
                break;
            }
        }
        if (!hasError) location.reload();
    });
}

function initOsFileDrop() {
    const contentPane = document.getElementById('contentPane');
    if (!contentPane) return;
    contentPane.addEventListener('dragover', (e) => {
        if (!e.dataTransfer.types.includes('Files')) return;
        e.preventDefault();
        contentPane.classList.add('drop-target-active');
    });
    contentPane.addEventListener('dragleave', (e) => {
        if (!contentPane.contains(e.relatedTarget)) contentPane.classList.remove('drop-target-active');
    });
    contentPane.addEventListener('drop', async (e) => {
        if (!e.dataTransfer.types.includes('Files')) return;
        e.preventDefault();
        contentPane.classList.remove('drop-target-active');
        const files = e.dataTransfer.files;
        if (!files.length) return;
        await uploadFiles(files);
    });
}

// ── 키보드 단축키 ──
document.addEventListener('keydown', (e) => {
    const modalOpen = document.getElementById('modalOverlay').classList.contains('open');
    const propOpen = document.getElementById('propOverlay').classList.contains('open');
    const fsOpen = document.getElementById('fsModal').classList.contains('open');
    const themeMenuOpen = document.getElementById('themeMenu')?.classList.contains('open');

    if (e.key === 'Escape') {
        if (modalOpen) return;
        if (propOpen) { closeProp(); return; }
        if (fsOpen) { closeFullscreen(); return; }
        if (themeMenuOpen) { closeThemeMenu(); return; }
        hideCtx(); hideCtxEmpty();
        return;
    }
    if (modalOpen || propOpen) return;

    if (fsOpen) {
        if (e.key === 'ArrowRight') fsNext();
        if (e.key === 'ArrowLeft') fsPrev();
        if (e.key === ' ') { e.preventDefault(); fsToggleAuto(); }
        return;
    }
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
    if (document.getElementById('trashView').style.display === 'flex') return;

    const ctrl = e.ctrlKey || e.metaKey;
    const key = e.key.toLowerCase();

    if (ctrl && key === 'a') { e.preventDefault(); selectAllItems(); return; }
    if (ctrl && key === 'f') { e.preventDefault(); document.querySelector('.search-box')?.focus(); return; }
    if (ctrl && key === 'd') { e.preventDefault(); ctxDuplicate(); return; }
    if (ctrl && key === 'x') { e.preventDefault(); ctxCut(); return; }
    if (ctrl && key === 'c') { e.preventDefault(); ctxCopyItem(); return; }
    if (ctrl && key === 'v') { e.preventDefault(); ctxPaste(); return; }
    if (ctrl && e.shiftKey && key === 'n') { e.preventDefault(); showNewFileDialog(); return; }
    if (ctrl && key === 'n') { e.preventDefault(); showMkdirDialog(); return; }
    if (e.key === 'F2') { e.preventDefault(); shortcutRename(); return; }
    if (e.key === 'Delete') { e.preventDefault(); const p = getSelectedPaths(); if (p.length) doDeleteSelected(p); return; }
    if (e.key === 'Backspace') { e.preventDefault(); navigateUp(); return; }
    if (e.key === 'Enter' && selectedPath) {
        const item = items.find(i => i.path === selectedPath);
        if (item) openItem(item.path, item.is_dir);
    }
});

// Click on empty space deselects
document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('contentPane').addEventListener('click', (e) => {
        if (e.target.closest('.file-row,.grid-item,.icon-item,.col-row,.gallery-thumb')) return;
        if (document.getElementById('trashView').style.display === 'flex') return;
        selectedPath = null;
        selectedPaths = new Set();
        renderFiles();
        document.getElementById('tbOpen').style.display = 'none';
        document.getElementById('tbDownload').style.display = 'none';
        document.getElementById('tbDownloadDir').style.display = 'none';
        document.getElementById('statusSelected').textContent = '';
        document.getElementById('previewContent').innerHTML = '<div class="preview-empty">파일을 선택하면 미리보기가 표시됩니다</div>';
    });

    // ── BREADCRUMB DROP ZONE ──
    document.querySelectorAll('a.bc-item').forEach(bc => {
        let decodedPath;
        try {
            const url = new URL(bc.href);
            decodedPath = url.searchParams.get('path');
        } catch { return; }
        if (!decodedPath) return;

        bc.addEventListener('dragover', (e) => {
            if (!e.dataTransfer.types.includes('text/plain')) return;
            e.preventDefault();
            e.dataTransfer.dropEffect = 'move';
            bc.style.background = 'var(--win-accent)';
            bc.style.color = '#fff';
        });
        bc.addEventListener('dragleave', (e) => {
            if (!bc.contains(e.relatedTarget)) { bc.style.background = ''; bc.style.color = ''; }
        });
        bc.addEventListener('drop', async (e) => {
            e.preventDefault();
            bc.style.background = ''; bc.style.color = '';
            let paths;
            try { paths = JSON.parse(e.dataTransfer.getData('text/plain') || '[]'); }
            catch { return; }
            const filtered = paths.filter(p => p !== decodedPath && !decodedPath.startsWith(p + '/'));
            if (!filtered.length) return;
            const isCopy = e.ctrlKey || e.metaKey;
            for (const src of filtered) {
                try { await apiFetch(isCopy ? '/explorer/api/copy' : '/explorer/api/move', { src, dst_dir: decodedPath }); }
                catch (err) { showToast(`실패: ${err.message}`, 'error'); return; }
            }
            location.reload();
        });
    });

    setView(currentView);
    if (previewVisible) {
        document.getElementById('previewPane').classList.remove('hidden');
        document.getElementById('tbPreview').classList.add('active');
        document.getElementById('previewResizer').style.display = '';
    } else {
        document.getElementById('previewResizer').style.display = 'none';
    }
    renderFiles();
    buildNavTree();
    renderFavorites();
    initResizer('navResizer', 'navPane', 'left');
    initResizer('previewResizer', 'previewPane', 'right');
    initRubberBandSelect();
    initOsFileDrop();
    initZoom();
    initThemeMenu();
    initAddressBarEdit();
    initSearchScope();

    document.getElementById('trashNavEntry')?.addEventListener('click', enterTrashMode);
    document.getElementById('trashBackBtn')?.addEventListener('click', exitTrashMode);
    document.getElementById('trashEmptyBtn')?.addEventListener('click', emptyTrash);
});

// ── 외부(인라인 onclick)에서 쓰는 함수들을 전역으로 노출 ──
window.__lx = {
    navigateUp, setView, togglePreview, openFullscreen, closeFullscreen, fsPrev, fsNext, fsToggleAuto,
    sortBy, showMkdirDialog, showNewFileDialog, handleUpload, toggleHidden,
    ctxOpen, ctxDownload, ctxDownloadDir, ctxProperties, ctxCopyPath, ctxRename, ctxCut, ctxCopyItem,
    ctxPaste, ctxDelete, ctxExtract, ctxCompress, ctxDuplicate, ctxToggleFavorite,
    closeProp, openItem, emptyTrash,
};
Object.assign(window, window.__lx);

})();
