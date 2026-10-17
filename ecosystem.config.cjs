module.exports = {
  apps: [{
    name: 'linuxplorer',
    cwd: __dirname,
    script: 'run.py',
    interpreter: `${__dirname}/.venv/bin/python`,
    exec_mode: 'fork',
    instances: 1,
    autorestart: true,
    watch: false,
    ignore_watch: ['.git', '.state', '.venv'],
    kill_timeout: 7000,
    max_memory_restart: '384M',
    restart_delay: 1000,
    env: { PYTHONUNBUFFERED: '1' }
  }]
};
