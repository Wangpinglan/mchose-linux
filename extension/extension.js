// extension.js — 稳定外壳
//
// GNOME Shell 45+ 对扩展的 ESM 模块是按 URL 缓存的：disable/enable 只会
// 重跑 enable()，不会重新 import extension.js。所以把实现全部放到 impl.js，
// 每次 enable 用带时间戳的查询串动态 import —— 新 URL 就是新实例，
// 改完界面 disable/enable 一次即可生效，不用注销重登。

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

/** 起主体模块；失败要让人看得见，而不是静默没有图标 */
function boot(ext, mod) {
    if (ext._gone || !mod)
        return;
    ext._mod = mod;
    try {
        mod.start(ext);
    } catch (e) {
        logError(e, 'mchose: impl.start 抛异常');
        Main.notifyError('MCHOSE Mouse', `界面启动失败：${e.message}`);
    }
}

export default class MchoseExtension extends Extension {
    enable() {
        this._gone = false;
        this._mod = null;
        const ver = `${Date.now()}-${Math.floor(Math.random() * 1e6)}`;

        import(`./impl.js?b=${ver}`)
            .then(mod => boot(this, mod))
            .catch(e => {
                logError(e, 'mchose: 带版本号载入 impl.js 失败，改用普通 URL 重试');
                return import('./impl.js').then(mod => boot(this, mod));
            })
            .catch(e => {
                logError(e, 'mchose: impl.js 载入失败');
                Main.notifyError('MCHOSE Mouse', '模块加载失败，详见 journalctl');
            });
    }

    disable() {
        this._gone = true;
        try {
            this._mod?.stop();
        } catch (e) {
            logError(e, 'mchose: stop 失败');
        }
        this._mod = null;
    }
}
