# PanWatch App privacy policy

[简体中文](#简体中文)

This policy covers PanWatch, the mobile app maintained by the PanWatch project (TNT-Likely).

## Connecting to your PanWatch server

The app checks the service version and mobile compatibility at the HTTPS address you select. If you sign in with a username and password, the app sends those details over HTTPS directly to that server and clears the entered password from the form when you submit it. You use the account that already exists on your server.

If you scan a connection code, the app reads it on your device. It sends the short-lived pairing information to the server named in the code after you confirm the address. The server owner approves the phone through PanWatch web settings. Both connection methods send a generic device label (iPhone or Android) to the selected server. Camera permission is optional for address and password sign-in, and you can change it in your phone’s settings.

On Android, Google ML Kit identifies the QR code on the device. The SDK sends diagnostics and usage data to Google over HTTPS. This can include an installation identifier, device and app information, scanner settings, performance measurements, and errors. [ML Kit Android data disclosure](https://developers.google.com/ml-kit/android-data-disclosure)

## Holdings and device access

The selected server issues a read-only device credential. The app keeps the service address and credential in your operating system’s secure storage. The credential lasts seven days and the server owner can revoke it earlier. The app uses HTTPS to read your holdings, quantities, and available values directly from the selected server. The last successful view stays in app memory until you clear the connection or the app process ends.

Use **Connected service → Forget this connection** to remove the saved device credential and clear the visible holdings. To end access at the server, use **Settings → Connect a phone** on your instance and revoke the device. The app also clears its session and the visible holdings when the server rejects the credential. The operator of your instance controls the account, portfolio, server logs, and server-side retention. Ask that operator about deleting records on the server.

## Distribution and contact

Apple and Google handle app distribution and store testing under their own privacy policies. The app sends sign-in, pairing, and holdings requests only to the instance you choose. Opening this policy or project support in your browser makes a separate request to GitHub.

For privacy questions, use the maintainer’s published contact information and keep account credentials, one-time codes, instance addresses, and real holdings private. [PanWatch maintainer profile](https://github.com/TNT-Likely)

Report suspected security issues through the project’s private channel. [Private reporting instructions](../SECURITY.md)

## 简体中文

本政策适用于 PanWatch 项目（TNT-Likely）维护的 PanWatch 移动 App。

### 连接你的 PanWatch 服务

App 通过你选择的 HTTPS 地址检查服务的版本和移动端兼容性。使用用户名和密码登录时，App 通过 HTTPS 直接将它们发送到该服务，并在提交时清除表单中的密码。登录使用你在该服务上已有的账号。

扫码时，App 在手机上识别连接码。你确认服务地址后，App 将短时有效的配对信息发送到连接码指定的服务。服务所有者需要在 PanWatch 网页设置中批准这台手机。两种连接方式都会将通用设备名称（iPhone 或 Android）发送给所选服务。使用地址和密码登录时可以关闭相机权限，也可以在手机系统设置中修改权限。

Android 使用 Google ML Kit 在手机上识别二维码。该 SDK 通过 HTTPS 向 Google 发送诊断与使用数据，可能包括安装标识、设备与 App 信息、扫码配置、性能指标及错误信息。 [ML Kit Android 数据披露](https://developers.google.com/ml-kit/android-data-disclosure)

### 持仓与设备权限

所选服务签发只读设备凭据。App 将服务地址和凭据存入系统安全存储。凭据有效期为七天，服务所有者可以提前撤销。App 通过 HTTPS 直接从所选服务读取你的持仓、数量和可用估值。最近一次读取的持仓保留在 App 内存中，直至连接被清除或 App 进程结束。

使用「已连接服务 → 忘记此连接」可以删除已保存的设备凭据并清除可见持仓。要在服务端终止访问，请到该实例的「设置 → 连接手机」撤销设备。服务拒绝设备凭据时，App 也会清除会话和可见持仓。账号、投资组合、服务日志及服务端保留期限由实例运营者管理。需要删除服务端记录时，请联系该运营者。

### 分发与联系

Apple 和 Google 根据各自的隐私政策处理 App 分发和商店测试。App 的登录、配对与持仓请求只发往你选择的实例。在浏览器中打开本政策或项目支持页面时，浏览器会向 GitHub 发出请求。

有关隐私问题，请通过维护者公开列出的联系方式联系，并妥善保护账号凭据、一次性连接码、实例地址和真实持仓。 [PanWatch 维护者主页](https://github.com/TNT-Likely)

请通过项目的私下联系渠道报告疑似安全问题。 [安全问题报告方式](../SECURITY.md)
