// daemon.js — 与 mchose-daemon.py 常驻进程通信
//
// 用 Gio.Subprocess 起一个 Python 进程，按行收发 JSON。
// 比在 GJS 里直接做 ioctl 可靠：GJS 没暴露 open(2)/ioctl。

import Gio from 'gi://Gio';
import GLib from 'gi://GLib';

export class Daemon {
    /**
     * @param {string} scriptPath mchose-daemon.py 的绝对路径
     * @param {string} python     解释器路径
     */
    constructor(scriptPath, python = '/usr/bin/python3') {
        this._script = scriptPath;
        this._python = python;
        this._proc = null;
        this._buffer = '';
        this._pending = new Map();
        this._nextId = 1;
        this._ready = false;
        this._started = false;
        this.onDisconnect = null;
    }

    start() {
        if (this._proc) return;
        const launcher = new Gio.SubprocessLauncher({
            flags: Gio.SubprocessFlags.STDIN_PIPE |
                   Gio.SubprocessFlags.STDOUT_PIPE,
        });
        // 强制无缓冲，避免扩展等不到响应
        launcher.setenv('PYTHONUNBUFFERED', '1', true);
        launcher.setenv('PYTHONDONTWRITEBYTECODE', '1', true);

        try {
            this._proc = launcher.spawnv([this._python, this._script]);
        } catch (e) {
            logError(e, 'mchose: 无法启动辅助进程');
            this._proc = null;
            return false;
        }

        this._stdin = this._proc.get_stdin_pipe();
        this._stdout = new Gio.DataInputStream({
            base_stream: this._proc.get_stdout_pipe(),
            close_base_stream: false,
        });
        this._started = true;
        this._readLoop();
        return true;
    }

    _readLoop() {
        if (!this._stdout) return;
        this._stdout.read_line_async(GLib.PRIORITY_DEFAULT, null, (src, res) => {
            let line = null;
            try {
                const [data] = src.read_line_finish_utf8(res);
                line = data;
            } catch (e) {
                line = null;
            }
            if (line === null) {
                this._onExit();
                return;
            }
            this._handleLine(line);
            this._readLoop();
        });
    }

    _handleLine(line) {
        let msg;
        try {
            msg = JSON.parse(line);
        } catch (e) {
            return;
        }
        const cb = this._pending.get(msg.id);
        if (cb) {
            this._pending.delete(msg.id);
            cb(msg);
        }
    }

    _onExit() {
        this._proc = null;
        this._stdout = null;
        this._stdin = null;
        this._started = false;
        for (const cb of this._pending.values())
            cb({ok: false, error: '辅助进程已退出'});
        this._pending.clear();
        // 通知上层，顺便自动重启一次
        if (this.onDisconnect) this.onDisconnect();
    }

    send(cmd, extra = {}, timeoutMs = 4000) {
        if (!this._proc) {
            if (!this.start()) {
                return Promise.resolve({ok: false, error: '辅助进程未运行'});
            }
        }
        const id = this._nextId++;
        const payload = JSON.stringify({id, cmd, ...extra}) + '\n';

        return new Promise(resolve => {
            let done = false;
            const timer = GLib.timeout_add(GLib.PRIORITY_DEFAULT, timeoutMs, () => {
                if (!done) {
                    done = true;
                    this._pending.delete(id);
                    resolve({ok: false, error: '超时'});
                }
                return GLib.SOURCE_REMOVE;
            });

            this._pending.set(id, msg => {
                if (done) return;
                done = true;
                GLib.source_remove(timer);
                resolve(msg);
            });

            try {
                this._stdin.write_all(payload, null);
                this._stdin.flush(null);
            } catch (e) {
                done = true;
                GLib.source_remove(timer);
                this._pending.delete(id);
                resolve({ok: false, error: `写入失败: ${e.message}`});
            }
        });
    }

    readAll() {
        return this.send('read_all', {}, 6000);
    }

    set(key, value) {
        return this.send('set', {key, value}, 6000);
    }

    destroy() {
        try {
            if (this._proc) {
                this.send('quit', {}, 300).then(() => this._proc?.force_exit());
            }
        } catch (e) { /* 忽略 */ }
        this._proc = null;
    }
}
