# 参与开发 / 维护

面向想改代码、打包、发版本的人。使用与安装方法见 [README](README.md)。

## 源码布局

- `extension/` 与 `mchose.py` 是源；`dist/` 下的产物全部由 `tools/` 里的脚本生成
- 逆向中间产物（`extract/` 厂商安装包解包、`src/` 反编译出的 C# 源码）体积大且含
  厂商二进制，在 `.gitignore` 里不入库。查协议时看
  `src/pgckyKZq6gyHNM8uTF/m4E88s4JvMgGlnhjWi.cs`，类名是厂商混淆的随机串

## 从源码运行扩展

```bash
tools/install-cli.sh   # 先装 CLI 与 udev 规则
cp -r extension/ ~/.local/share/gnome-shell/extensions/mchose-mouse@local
# 或软链，改完代码即时生效
```

改完代码重载扩展：

```bash
gnome-extensions disable mchose-mouse@local; sleep 4; gnome-extensions enable mchose-mouse@local
```

注意 disable 和 enable 之间至少隔 3~4 秒，连着切会让扩展停在 INACTIVE
且没有任何报错。

## 加新功能

`mchose.py` 里 `Mchose` 类的方法都是一行帧构造：

```python
# 读：命令码 | 0x80，参数从 [7] 开始
r = self.xfer_read(group, code, params, length=len(params))
# 写
self.xfer_write(group, code, params, length=len(params))
```

返回值 `r` 是 65 字节应答，`r[1] == 0xA1` 表示设备确认。
参数区从 `r[7]` 开始，但有些命令的应答会偏移一位（`r[8]`），
具体看 [PROTOCOL.md](PROTOCOL.md) 里每个命令的说明。
排查协议不符时用 `mchose dpiprobe` 打印原始收发帧。

## 重新打包

```bash
python3 tools/build_release.py      # 单文件安装器 + 标准扩展 zip
python3 tools/build_packages.py     # RPM + DEB（需要 rpmbuild；deb 用 ar 组装）
```

`dist/.src` 是每次打包时从仓库自动收集的中间目录，不用手动维护。
版本号统一在根目录 `VERSION` 文件，两个打包脚本都读它。

## 发布新版本

1. 更新根目录 `VERSION`，同步 `app/main.py` 的 `VERSION` 和
   `extension/metadata.json` 的 `version`
2. `python3 tools/build_release.py && python3 tools/build_packages.py`
3. 用新的 `dist/install-gnome-extension.sh` 覆盖仓库根目录的同名文件并提交——
   仓库里的脚本要和 Release 附件保持一致
4. 提交、打 tag 并推送：

   ```bash
   git tag -a v1.2.1 -m "..." && git push origin main v1.2.1
   ```

5. 在 GitHub 上新建 Release（tag 形如 `v1.2.0`），上传这些附件：

| 文件 | 用途 |
|---|---|
| `dist/install-gnome-extension.sh` | 一键安装脚本（安装方式 ①），README 顶部的 `curl` 命令依赖它 |
| `dist/mchose-mouse@local.shell-extension.zip` | 标准扩展包，可 `gnome-extensions install` |
| `dist/mchose-<ver>-1.fc*.noarch.rpm` | Fedora / RHEL 系统包（安装方式 ③） |
| `dist/mchose_<ver>-1_all.deb` | Debian / Ubuntu 系统包（安装方式 ③） |

**文件名不要改**，`releases/latest/download/<文件名>` 是按名字取附件的。
仓库如果改名，同步更新 README 里的下载链接和 `extension/metadata.json` 的 `url`。
