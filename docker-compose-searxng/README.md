### 快速部署 search4all + SearXNG

#### 快速开始

**1. 下载配置文件**

确保从本目录下载以下文件:
- `docker-compose.yml`
- `./searxng/settings.yml`

**2 填写 API 密钥**

在`docker-compose.yaml`文件的`search4all`服务配置中，填写`OPENAI_API_KEY`和`OPENAI_BASE_URL`。详细配置请参阅[search4all官方文档](https://github.com/fatwang2/search4all)。

**3. 启动与停止命令**
- 启动服务: `docker-compose up -d`
- 停止服务: `docker-compose down`

**4. 配置说明**
- 若需通过代理连接网络，需在`settings.yml`中取消注释代理配置部分。

**5. 更多信息**
- 详细配置请参阅[searxng官方文档](https://docs.searxng.org/admin/settings/settings.html#settings-use-default-settings)。



#### Quick Start

**1. Download Configuration Files**

Ensure you have downloaded the following files:
- `docker-compose.yml`
- `./searxng/settings.yml`

**2 Fill in API Key**

In the `search4all` service configuration within the `docker-compose.yaml` file, ensure you enter the correct `OPENAI_API_KEY` and `OPENAI_BASE_URL`.

For detailed configurations, please refer to the [search4all official documentation](https://github.com/fatwang2/search4all).

**3. Start and Stop Commands**
- To start the service: `docker-compose up -d`
- To stop the service: `docker-compose down`

**4. Configuration Details**
- If you need to connect through a proxy, uncomment the proxy settings in the `settings.yml`.

**5. Additional Information**
- For detailed configurations, please refer to the [SearXNG official documentation](https://docs.searxng.org/admin/settings/settings.html#settings-use-default-settings).