# 微信小程序版本

这个目录把现有 A 股分析逻辑拆成两部分：

- `api_server.py`：小程序调用的后端 JSON API。
- `miniprogram/`：微信开发者工具可导入的小程序源码。
- `generate_miniprogram_code.py`：通过微信服务端接口生成小程序码。

## 1. 本地启动后端

```powershell
python api_server.py
```

本地测试：

```powershell
Invoke-RestMethod -Method Post -Uri http://localhost:8000/api/analyze -ContentType 'application/json' -Body '{"stock_code":"688256"}'
```

正式小程序不能请求 `localhost`，需要把后端部署到 HTTPS 域名，并在微信公众平台把域名加入“request 合法域名”。

## 2. 配置小程序

1. 用微信开发者工具导入 `miniprogram/`。
2. 把 `miniprogram/project.config.json` 里的 `appid` 换成你的小程序 AppID。
3. 把 `miniprogram/pages/index/index.js` 里的 `API_BASE_URL` 换成你的 HTTPS 后端域名。
4. 开发阶段可以在开发者工具里临时关闭“校验合法域名”，正式发布必须配置 HTTPS 合法域名。

## 3. 生成小程序码

先在 `.env` 里配置：

```dotenv
WECHAT_APPID=你的微信小程序AppID
WECHAT_APPSECRET=你的微信小程序AppSecret
```

生成体验版小程序码：

```powershell
python generate_miniprogram_code.py --scene code=688256 --env-version trial --output miniprogram_code.png
```

正式发布后生成正式版：

```powershell
python generate_miniprogram_code.py --scene code=688256 --env-version release --output miniprogram_code.png
```

注意：`scene` 最多 32 个字符，这里只放股票代码参数。`page` 默认是 `pages/index/index`。
