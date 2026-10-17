(function () {
    var theme = 'aurora', mode;
    try {
        theme = localStorage.getItem('exp_theme') || theme;
        mode = localStorage.getItem('exp_mode');
    } catch (_) {}
    if (!['aurora', 'classic', 'glass', 'neu', 'mesh', 'mono'].includes(theme)) theme = 'aurora';
    if (!['light', 'dark'].includes(mode)) mode = matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
    document.documentElement.dataset.theme = theme + '-' + mode;
    document.documentElement.style.colorScheme = mode;
    document.querySelector('meta[name="theme-color"]').content = mode === 'dark' ? '#0b1020' : '#f0f3fa';
})();
