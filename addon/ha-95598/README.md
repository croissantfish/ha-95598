# Home Assistant 应用：95598 for Home Assistant

这个应用用于在 Home Assistant OS / Supervised 中运行 `ha-95598`，通过 MQTT Discovery 把国家电网 `95598` 的电量、电费、余额和历史数据同步到 Home Assistant。

## 使用前准备

- 一个可登录的 `95598` 账号，并且已经绑定户号。
- Home Assistant 已启用 MQTT 集成。
- 一个 MQTT Broker。使用官方 Mosquitto 应用时，`mqtt_host` 通常填 `core-mosquitto`；Broker 开启认证时还需要填写专用 MQTT 账号和密码。

## 安装

1. Home Assistant 进入 `设置` -> `应用` -> `应用商店`。
2. 右上角添加仓库：

```text
https://github.com/croissantfish/ha-95598
```

3. 找到 `95598 for Home Assistant` 并安装。
4. 在 `配置` 页面填写账号、密码和 MQTT 信息。
5. 启动应用，查看日志确认运行状态。

Supervisor 会在设备上现场构建镜像，不需要拉取上游应用镜像。首次安装和每次版本更新需要下载 Debian 软件包及 Python 依赖，在树莓派上会比拉取预构建镜像耗时更长。Debian 软件源默认使用清华大学 TUNA 镜像，失败时才回退到官方源。

## 运行方式

应用使用 headless Chromium，不安装或启动 Xvfb。Docker init 负责进程回收，浏览器和 Python 主程序以非 root 的 `app` 用户运行，MQTT 通过标准容器网络访问 Broker。

## 数据和配置

应用的持久数据保存在应用配置目录中，包括 SQLite 数据库、登录会话、二维码、页面快照和验证码样本。

电费估算依赖镜像内置的 `config/tou_price_config.json`。默认配置是湖南居民阶梯电价示例，不一定适用于你的地区；如果你的地区执行分时电价，需要自行调整谷、平、峰、尖价格。当前计算直接使用 `95598` 返回的分时用量和配置中的对应价格。

`job_start_time` 和定时任务使用容器时区，默认是 `Asia/Shanghai`（UTC+8）。

## 说明

应用目录中的 `rootfs/app` 是现场构建使用的自包含源码副本，测试会检查它与主项目源码保持一致。主项目文档见：

https://github.com/croissantfish/ha-95598
